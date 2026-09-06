from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field


@dataclass
class PartitionStats:
    facet_value_id: str
    facet_value: str
    partition_key: str
    declared: int | None = None
    pages_expected: int | None = None
    pages_parsed: int = 0
    scraped: int = 0
    skipped: int = 0
    degraded: int = 0
    duplicates: int = 0
    downloaded: int = 0
    download_failed: int = 0
    stored: int = 0
    unchanged: int = 0
    store_failed: int = 0
    failures: list[dict] = field(default_factory=list)
    identifiers: set[str] = field(default_factory=set)
    urls_fetched: set[str] = field(default_factory=set)

    def first_sighting(self, identifier: str) -> bool:
        """Record an identifier; False if this partition already produced it."""
        if identifier in self.identifiers:
            self.duplicates += 1
            return False
        self.identifiers.add(identifier)
        return True

    @property
    def found(self) -> int:
        """The denominator for completeness.

        A source that publishes a count gives an external number to reconcile against.
        One that does not can only be self-reported, and the summary says which.
        """
        return self.declared if self.declared is not None else self.scraped + self.skipped

    @property
    def found_is_declared(self) -> bool:
        return self.declared is not None

    @property
    def listing_complete(self) -> bool:
        if self.failures or self.pages_parsed == 0:
            return False
        return not self.found_is_declared or self.scraped + self.skipped == self.declared

    @property
    def complete(self) -> bool:
        """Every listed record was fetched, and every fetched document is either newly
        stored or confirmed already present with the same content."""
        return (
            self.listing_complete
            and self.download_failed == 0
            and self.downloaded == self.scraped
            and self.store_failed == 0
            and self.stored + self.unchanged == self.downloaded
        )

    @property
    def missing(self) -> int | None:
        if self.declared is None:
            return None
        return self.declared - (self.scraped + self.skipped)

    def as_dict(self) -> dict:
        return {
            "facet_value_id": self.facet_value_id,
            "facet_value": self.facet_value,
            "partition": self.partition_key,
            "records_found": self.found,
            "records_found_source": "declared" if self.found_is_declared else "observed",
            "records_scraped": self.scraped,
            "records_skipped": self.skipped,
            "records_degraded": self.degraded,
            "records_duplicate": self.duplicates,
            "records_unaccounted": self.missing,
            "documents_downloaded": self.downloaded,
            "downloads_failed": self.download_failed,
            "documents_stored": self.stored,
            "documents_unchanged": self.unchanged,
            "stores_failed": self.store_failed,
            "pages_expected": self.pages_expected,
            "pages_parsed": self.pages_parsed,
            "failures": len(self.failures),
            "complete": self.complete,
        }


class CrawlStats:
    def __init__(self) -> None:
        self._partitions: dict[tuple[str, str], PartitionStats] = {}
        self.reasons: dict[str, int] = defaultdict(int)

    def partition(self, facet_value_id: str, facet_value: str, partition_key: str) -> PartitionStats:
        key = (facet_value_id, partition_key)
        if key not in self._partitions:
            self._partitions[key] = PartitionStats(facet_value_id, facet_value, partition_key)
        return self._partitions[key]

    def record_skip(self, reason: str) -> None:
        self.reasons[reason] += 1

    @property
    def partitions(self) -> list[PartitionStats]:
        return list(self._partitions.values())

    def summary(self) -> dict:
        found = sum(p.found for p in self.partitions)
        scraped = sum(p.scraped for p in self.partitions)
        skipped = sum(p.skipped for p in self.partitions)
        duplicates = sum(p.duplicates for p in self.partitions)
        degraded = sum(p.degraded for p in self.partitions)
        downloaded = sum(p.downloaded for p in self.partitions)
        download_failed = sum(p.download_failed for p in self.partitions)
        stored = sum(p.stored for p in self.partitions)
        unchanged = sum(p.unchanged for p in self.partitions)
        store_failed = sum(p.store_failed for p in self.partitions)
        incomplete = [p.as_dict() for p in self.partitions if not p.complete]
        return {
            "partitions_processed": len(self.partitions),
            "records_found": found,
            "records_scraped": scraped,
            "records_skipped": skipped,
            "records_degraded": degraded,
            "records_duplicate": duplicates,
            "documents_downloaded": downloaded,
            "downloads_failed": download_failed,
            "documents_stored": stored,
            "documents_unchanged": unchanged,
            "stores_failed": store_failed,
            "records_unaccounted": found - scraped - skipped,
            "found_is_declared": all(p.found_is_declared for p in self.partitions),
            "skip_reasons": dict(self.reasons),
            "incomplete_partitions": incomplete,
            "complete": not incomplete,
        }


@dataclass
class TransformStats:
    """The transformation's run summary: one flat set of counters per invocation."""
    candidates: int = 0
    transformed: int = 0
    passthrough: int = 0
    unchanged: int = 0
    failed: int = 0
    reasons: Counter = field(default_factory=Counter)
    flags: Counter = field(default_factory=Counter)

    @property
    def complete(self) -> bool:
        return self.failed == 0 and self.transformed + self.passthrough + self.unchanged == self.candidates

    def summary(self) -> dict:
        return {
            "candidates": self.candidates,
            "transformed": self.transformed,
            "passthrough": self.passthrough,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "failure_reasons": dict(self.reasons),
            "quality_flags": dict(self.flags),
            "complete": self.complete,
        }
