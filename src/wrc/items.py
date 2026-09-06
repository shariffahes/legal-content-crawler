from __future__ import annotations

from dataclasses import dataclass, field, fields
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

    # Set once the document has been fetched.
    identifier_slug: str | None = None
    content_type: str | None = None
    extension: str | None = None
    file_size: int | None = None
    file_hash: str | None = None
    raw_sha256: str | None = None
    object_key: str | None = None
    fetched_at: datetime | None = None

    # Set once the document has been stored.
    file_path: str | None = None

    # The bytes themselves, carried to the storage pipeline and never persisted here.
    content: bytes | None = field(default=None, repr=False)

    @classmethod
    def persisted_fields(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.name != "content"]
