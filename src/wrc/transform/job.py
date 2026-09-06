from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone

import boto3
from botocore.exceptions import ClientError
from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError

from wrc.config import Settings
from wrc.documents import HTML_EXTENSIONS
from wrc.items import TransformedRecord
from wrc.partitions import PartitionSize, iter_partitions
from wrc.source import SourceSpec
from wrc.stats import TransformStats
from wrc.transform.extract import extract_document

logger = logging.getLogger(__name__)

TRANSFORMED_KEY = [("source", ASCENDING), ("identifier", ASCENDING), ("doc_url", ASCENDING)]
READ_KEY = [("source", ASCENDING), ("partition_date", ASCENDING)]


def ensure_index(collection, keys, name: str, unique: bool = False) -> None:
    """Create an index, replacing a same-named one whose key differs.

    Legitimate here and only here: the transformed zone is a derived, rebuildable view,
    so its job may reconcile its own schema. The landing pipeline deliberately does not
    do this; a changed index on immutable data should fail loudly, not self-heal.
    """
    existing = collection.index_information().get(name)
    if existing is not None and [tuple(k) for k in existing["key"]] != [tuple(k) for k in keys]:
        logger.warning("index_replaced", extra={"collection": collection.name, "index": name,
                                                "old_key": existing["key"], "new_key": keys})
        collection.drop_index(name)
    collection.create_index(keys, unique=unique, name=name)


class TransformJob:
    def __init__(self, settings: Settings, spec: SourceSpec, start: date, end: date, partition_size: PartitionSize):
        self.settings = settings
        self.spec = spec
        self.start, self.end, self.partition_size = start, end, partition_size
        self.stats = TransformStats()

        client = MongoClient(settings.mongo_uri.get_secret_value())
        db = client[settings.mongo_db]
        self.landing = db[settings.mongo_landing_collection]
        self.transformed = db[settings.mongo_transformed_collection]
        ensure_index(self.transformed, TRANSFORMED_KEY, "document_identity", unique=True)
        ensure_index(self.transformed, READ_KEY, "source_partition_date")

        self.s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name=settings.s3_region,
        )
        try:
            self.s3.head_bucket(Bucket=settings.s3_transformed_bucket)
        except ClientError:
            self.s3.create_bucket(Bucket=settings.s3_transformed_bucket)

    def partition_dates(self) -> list[datetime]:
        """The same partitions the scraper would produce for this range, as BSON datetimes."""
        return [
            datetime(p.partition_date.year, p.partition_date.month, p.partition_date.day, tzinfo=timezone.utc)
            for p in iter_partitions(self.start, self.end, self.partition_size)
        ]

    def latest_landing_records(self) -> list[dict]:
        """One record per document: the most recently scraped version in the range.

        A document is identified by its URL. The source has published different
        decisions under one identifier, so identifier alone would merge them.
        """
        cursor = self.landing.find(
            {"source": self.spec.name, "partition_date": {"$in": self.partition_dates()}, "file_hash": {"$ne": None}},
        ).sort("scraped_at", -1)
        latest: dict[tuple[str, str], dict] = {}
        for row in cursor:
            latest.setdefault((row["identifier"], row["doc_url"]), row)
        return list(latest.values())

    @staticmethod
    def file_names(records: list[dict]) -> dict[tuple[str, str], str]:
        """<slug>.<ext> per the spec, unless an identifier names more than one document.

        Then every document sharing it gets a suffix derived from its URL, so the names
        are deterministic whatever order the documents are processed in.
        """
        urls_by_identifier: dict[str, set[str]] = {}
        for row in records:
            urls_by_identifier.setdefault(row["identifier"], set()).add(row["doc_url"])
        names = {}
        for row in records:
            stem = row["identifier_slug"]
            if len(urls_by_identifier[row["identifier"]]) > 1:
                stem += "~" + hashlib.sha256(row["doc_url"].encode()).hexdigest()[:8]
            names[(row["identifier"], row["doc_url"])] = f"{stem}.{row['extension']}"
        return names

    def run(self) -> dict:
        records = self.latest_landing_records()
        self.names = self.file_names(records)
        self.stats.candidates = len(records)
        logger.info(
            "transform_planned",
            extra={
                "source": self.spec.name, "start_date": self.start, "end_date": self.end,
                "partition_size": str(self.partition_size), "candidates": len(records),
            },
        )
        for row in records:
            try:
                self.process(row)
            except (ClientError, PyMongoError, OSError) as exc:
                self.stats.failed += 1
                self.stats.reasons[type(exc).__name__] += 1
                logger.error(
                    "document_failed",
                    extra={"identifier": row.get("identifier"), "landing_path": row.get("file_path"),
                           "error": type(exc).__name__, "detail": str(exc)[:200]},
                )
        summary = self.stats.summary()
        logger.info("transform_summary", extra={"source": self.spec.name, **summary})
        return summary

    def process(self, row: dict) -> None:
        identifier = row["identifier"]
        identity = {"source": row["source"], "identifier": identifier, "doc_url": row["doc_url"]}
        name = self.names[(identifier, row["doc_url"])]
        key = f"{row['source']}/{name}"
        file_path = f"s3://{self.settings.s3_transformed_bucket}/{key}"

        existing = self.transformed.find_one(identity, {"source_file_hash": 1, "file_path": 1})
        if existing and existing.get("source_file_hash") == row["file_hash"] and existing.get("file_path") == file_path:
            self.stats.unchanged += 1
            logger.info("document_unchanged", extra={"identifier": identifier, "file_hash": row["file_hash"]})
            return

        body = self.s3.get_object(Bucket=self.settings.s3_landing_bucket, Key=row["object_key"])["Body"].read()
        extension = row["extension"]
        flags = list(row.get("quality_flags") or [])
        details: dict = {}

        if extension in HTML_EXTENSIONS:
            extraction = extract_document(body, self.spec.document, row.get("language") or "en")
            output = extraction.html
            flags += extraction.quality_flags
            details = {"title": extraction.title, "text_chars": extraction.text_chars,
                       "extracted_with": extraction.extracted_with}
            content_type = "text/html; charset=utf-8"
            outcome = "document_transformed"
            self.stats.transformed += 1
        else:
            output = body
            content_type = row.get("content_type") or "application/octet-stream"
            outcome = "document_passthrough"
            self.stats.passthrough += 1

        if "~" in name:
            flags.append("identifier_reused")
        flags = list(dict.fromkeys(flags))
        self.s3.put_object(
            Bucket=self.settings.s3_transformed_bucket, Key=key, Body=output, ContentType=content_type,
            Metadata={"source-file-hash": row["file_hash"], "landing-key": row["object_key"]},
        )

        record = TransformedRecord(
            **{name: row.get(name) for name in TransformedRecord.carried_fields()},
            landing_id=row["_id"],
            source_file_hash=row["file_hash"],
            source_file_path=row["file_path"],
            file_hash=hashlib.sha256(output).hexdigest(),
            file_path=file_path,
            file_size=len(output),
            quality_flags=flags,
            **details,
        )
        self.transformed.replace_one(identity, record.to_document(), upsert=True)
        stale = existing.get("file_path") if existing else None
        if stale and stale != file_path:
            # The document was renamed (an identifier turned out to be shared). The
            # transformed zone is mutable, so the old object goes.
            self.s3.delete_object(Bucket=self.settings.s3_transformed_bucket, Key=stale.split("/", 3)[3])
            logger.info("document_renamed", extra={"identifier": identifier, "from": stale, "to": file_path})
        for flag in flags:
            self.stats.flags[flag] += 1
        logger.info(outcome, extra={"identifier": identifier, "file_path": record.file_path, "flags": flags, **details})
