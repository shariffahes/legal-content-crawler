from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class DocumentRecord:
    identifier: str
    source: str
    jurisdiction: str
    language: str
    issuing_authority: str | None
    issuing_authority_id: str | None
    description: str
    published_date: date | None
    doc_url: str
    partition_key: str
    partition_date: date
    listing_url: str
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Anything a source publishes that does not generalise across the corpus.
    source_metadata: dict = field(default_factory=dict)

    # Non-fatal problems with this record, e.g. "missing:published_date".
    quality_flags: list[str] = field(default_factory=list)

    # Populated by the download and storage pipelines.
    content_type: str | None = None
    file_size: int | None = None
    file_hash: str | None = None
    file_path: str | None = None
