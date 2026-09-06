from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from urllib.parse import quote, urlencode

import scrapy

from wrc.config import get_settings
from wrc.documents import content_fingerprint, extension_for, object_key, raw_hash, slugify_identifier
from wrc.items import DocumentRecord
from wrc.logging import configure
from wrc.parsing import extract_rows
from wrc.partitions import Partition, PartitionSize, iter_partitions
from wrc.source import FacetValue, SourceSpec
from wrc.stats import CrawlStats

logger = logging.getLogger(__name__)


class ListingSpider(scrapy.Spider):
    name = "listing"

    def __init__(
        self,
        start_date: str,
        end_date: str,
        facets: str | None = None,
        source: str | None = None,
        partition_size: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        config = get_settings()
        configure(config.log_level)

        self.spec = SourceSpec.load(source or config.source_spec_path)
        self.contract = self.spec.contract
        self.strategy = self.spec.pagination
        self.start_date = date.fromisoformat(start_date)
        self.end_date = date.fromisoformat(end_date)
        self.partition_size = PartitionSize(partition_size or config.partition_size)
        self.max_recovery_pages = config.max_recovery_pages
        self.run_stats = CrawlStats()

        selected = {f.strip() for f in facets.split(",")} if facets else None
        self.facet_values = [
            value for value in self.spec.facet.values if selected is None or value.id in selected
        ]
        if not self.facet_values:
            raise ValueError(
                f"no {self.spec.facet.maps_to} matched {facets!r} in source {self.spec.name!r}"
            )

    def listing_url(self, facet_value: FacetValue, partition: Partition, page: int) -> str:
        params = self.spec.listing_params(facet_value, partition, page)
        # Dates carry literal slashes, matching the source's own links.
        return f"{self.spec.search_url}?{urlencode(params, quote_via=quote, safe='/')}"

    def listing_request(
        self,
        facet_value: FacetValue,
        partition: Partition,
        page: int,
        url: str | None = None,
        recovery_depth: int = 0,
    ) -> scrapy.Request:
        return scrapy.Request(
            url or self.listing_url(facet_value, partition, page),
            callback=self.parse_listing,
            errback=self.handle_failure,
            cb_kwargs={
                "facet_value": facet_value,
                "partition": partition,
                "page": page,
                "recovery_depth": recovery_depth,
            },
        )

    def context(self, facet_value: FacetValue, partition: Partition, **extra) -> dict:
        """Stable log keys for every event, whatever the source calls its facet."""
        return {
            "facet_value": facet_value.name,
            "facet_value_id": facet_value.id,
            "partition": partition.key,
            **extra,
        }

    async def start(self):
        """Seed one request per (facet value, partition), page 1."""
        planned = skipped = 0
        for partition in iter_partitions(self.start_date, self.end_date, self.partition_size):
            for facet_value in self.facet_values:
                if not facet_value.covers(partition):
                    skipped += 1
                    logger.debug(
                        "partition_skipped",
                        extra=self.context(
                            facet_value,
                            partition,
                            reason="before_active_from",
                            active_from=facet_value.active_from,
                        ),
                    )
                    continue
                planned += 1
                yield self.listing_request(facet_value, partition, page=1)

        logger.info(
            "run_planned",
            extra={
                "source": self.spec.name,
                "jurisdiction": self.spec.jurisdiction,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "partition_size": str(self.partition_size),
                "pagination": str(self.strategy.kind),
                "facet": self.spec.facet.maps_to,
                "facet_values": [v.name for v in self.facet_values],
                "partitions_planned": planned,
                "partitions_skipped_inactive": skipped,
            },
        )
        if not planned:
            # A completeness check over an empty plan is satisfied trivially, so an
            # empty plan has to be an error rather than a quiet success.
            logger.error(
                "run_plan_empty",
                extra={"start_date": self.start_date, "end_date": self.end_date},
            )

    def parse_listing(
        self,
        response,
        facet_value: FacetValue,
        partition: Partition,
        page: int,
        recovery_depth: int,
    ):
        stats = self.run_stats.partition(facet_value.id, facet_value.name, partition.key)

        info = self.strategy.inspect(response.text, self.contract, page, self.spec.listing.page_size)
        if info.error:
            stats.failures.append({"url": response.url, "reason": info.error})
            logger.error(
                "listing_unreadable",
                extra=self.context(
                    facet_value, partition, page=page, url=response.url,
                    status=response.status, reason=info.error,
                ),
            )
            return

        if page == 1:
            stats.declared = info.declared_total
            stats.pages_expected = info.pages_expected
            logger.info(
                "partition_started",
                extra=self.context(
                    facet_value,
                    partition,
                    window_start=partition.start,
                    window_end=partition.end,
                    truncated=partition.truncated,
                    records_found=info.declared_total,
                    pages_expected=info.pages_expected,
                ),
            )
            for page_number in info.prefetch:
                yield self.listing_request(facet_value, partition, page_number)

        stats.pages_parsed += 1
        new_records = yield from self.emit_records(response, facet_value, partition, page, stats)

        step = self.strategy.advance(
            response.text,
            self.contract,
            page=page,
            pages_expected=stats.pages_expected,
            recovery_depth=recovery_depth,
            max_recovery=self.max_recovery_pages,
            base_url=response.url,
            urls_fetched=stats.urls_fetched,
            new_records=new_records,
        )
        for anomaly in step.anomalies:
            stats.failures.append({"url": response.url, "reason": anomaly})
            logger.error(
                anomaly,
                extra=self.context(
                    facet_value,
                    partition,
                    page=page,
                    pages_expected=stats.pages_expected,
                    records_found=stats.declared,
                    recovery_depth=step.recovery_depth,
                    url=response.url,
                    next_url=step.url,
                ),
            )
        if step.url:
            yield self.listing_request(
                facet_value, partition, page + 1, url=step.url, recovery_depth=step.recovery_depth
            )

    def emit_records(self, response, facet_value, partition, page, stats) -> int:
        """Yield a record per usable row; return how many were new to this partition."""
        new = 0
        for row in extract_rows(response.text, self.contract):
            if row.fatal:
                stats.skipped += 1
                reason = "missing:" + ",".join(row.fatal)
                self.run_stats.record_skip(reason)
                logger.warning(
                    "record_skipped",
                    extra=self.context(
                        facet_value,
                        partition,
                        page=page,
                        identifier=row.identifier or None,
                        reason=reason,
                        listing_url=response.url,
                    ),
                )
                continue

            if row.path_conflict:
                logger.warning(
                    "doc_path_conflict",
                    extra=self.context(
                        facet_value,
                        partition,
                        identifier=row.identifier,
                        candidates=row.path_candidates,
                        resolved=row.doc_path,
                    ),
                )

            doc_url = self.spec.absolute_url(row.doc_path, found_on=response.url)
            if not stats.first_sighting(doc_url):
                logger.warning(
                    "record_duplicate_in_run",
                    extra=self.context(facet_value, partition, page=page, identifier=row.identifier, doc_url=doc_url),
                )
                continue

            flags = [f"missing:{name}" for name in row.degraded]
            other_urls = stats.note_identifier(row.identifier, doc_url)
            if other_urls:
                # The source has published a different document under this identifier.
                flags.append("identifier_reused")
                logger.warning(
                    "identifier_reused",
                    extra=self.context(
                        facet_value, partition, page=page, identifier=row.identifier,
                        doc_url=doc_url, other_urls=sorted(other_urls),
                    ),
                )
            if flags:
                stats.degraded += 1
                logger.warning(
                    "record_degraded",
                    extra=self.context(
                        facet_value, partition, page=page, identifier=row.identifier, flags=flags
                    ),
                )

            new += 1
            stats.scraped += 1
            record = DocumentRecord(
                identifier=row.identifier,
                source=self.spec.name,
                jurisdiction=self.spec.jurisdiction,
                language=self.spec.language,
                issuing_authority=facet_value.name,
                issuing_authority_id=facet_value.id,
                description=row.description,
                published_date=row.published_date,
                doc_url=doc_url,
                partition_key=partition.key,
                partition_date=partition.partition_date,
                listing_url=response.url,
                source_metadata={k: v for k, v in row.extras.items() if v},
                quality_flags=flags,
            )
            yield scrapy.Request(
                record.doc_url,
                callback=self.parse_document,
                errback=self.handle_document_failure,
                cb_kwargs={"record": record},
            )
        return new

    def parse_document(self, response, record: DocumentRecord):
        """Fingerprint a fetched document and pass it on for storage.

        The stored bytes are exactly what arrived. The content hash is computed over the
        response minus HTML comments.
        """
        stats = self.run_stats.partition(
            record.issuing_authority_id, record.issuing_authority, record.partition_key
        )
        body = response.body
        header = response.headers.get("Content-Type", b"").decode("latin-1")
        extension, recognised = extension_for(header, response.url)
        if not recognised:
            record.quality_flags.append("unknown_content_type")
            logger.warning(
                "document_type_unrecognised",
                extra={"identifier": record.identifier, "url": response.url, "content_type": header},
            )

        record.identifier_slug = slugify_identifier(record.identifier)
        record.content_type = header or None
        record.extension = extension
        record.file_size = len(body)
        record.file_hash, record.file_hash_basis = content_fingerprint(
            body, extension, self.spec.document
        )
        if record.file_hash_basis == "document_minus_comments":
            record.quality_flags.append("content_region_missing")
            logger.warning(
                "content_region_missing",
                extra={"identifier": record.identifier, "url": response.url, "selector": self.spec.document.content},
            )
        record.raw_sha256 = raw_hash(body)
        record.object_key = object_key(
            record.source,
            record.issuing_authority_id,
            record.partition_key,
            record.identifier_slug,
            record.file_hash,
            extension,
        )
        record.fetched_at = datetime.now(timezone.utc)
        record.content = body

        stats.downloaded += 1
        yield record

    def handle_document_failure(self, failure):
        request = failure.request
        record: DocumentRecord = request.cb_kwargs["record"]
        response = getattr(failure.value, "response", None)
        stats = self.run_stats.partition(
            record.issuing_authority_id, record.issuing_authority, record.partition_key
        )
        stats.download_failed += 1
        detail = {
            "url": request.url,
            "identifier": record.identifier,
            "status": getattr(response, "status", None),
            "error": failure.type.__name__,
        }
        stats.failures.append({**detail, "reason": "download_failed"})
        logger.error(
            "download_failed",
            extra={"facet_value": record.issuing_authority, "partition": record.partition_key, **detail},
        )

    def handle_failure(self, failure):
        request = failure.request
        context = request.cb_kwargs
        facet_value: FacetValue = context["facet_value"]
        partition: Partition = context["partition"]
        response = getattr(failure.value, "response", None)
        stats = self.run_stats.partition(facet_value.id, facet_value.name, partition.key)
        detail = {
            "url": request.url,
            "page": context.get("page"),
            "status": getattr(response, "status", None),
            "error": failure.type.__name__,
        }
        stats.failures.append(detail)
        logger.error("listing_failed", extra=self.context(facet_value, partition, **detail))

    def closed(self, reason):
        summary = self.run_stats.summary()
        logger.info("run_summary", extra={"reason": reason, **summary})
        for partition in self.run_stats.partitions:
            if not partition.complete:
                logger.error("partition_incomplete", extra=partition.as_dict())
