from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
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
    file_hash_basis: str | None = None
    raw_sha256: str | None = None
    object_key: str | None = None
    fetched_at: datetime | None = None

    # Set once the document has been stored.
    file_path: str | None = None
    stored_at: datetime | None = None

    # The bytes themselves, carried to the storage pipeline and never persisted here.
    content: bytes | None = field(default=None, repr=False)

    @classmethod
    def persisted_fields(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.name != "content"]

    def to_document(self) -> dict:
        """The record as a Mongo document: no bytes, and dates widened to datetimes,
        which is what BSON can encode."""
        document = asdict(self)
        document.pop("content")
        for name in ("published_date", "partition_date"):
            value = document[name]
            if isinstance(value, date):
                document[name] = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        return document


@dataclass
class TransformedRecord:
    """One current document in the transformed zone, derived from a landing version."""

    identifier: str
    identifier_slug: str
    source: str
    jurisdiction: str
    language: str
    issuing_authority: str | None
    issuing_authority_id: str | None
    description: str
    published_date: date | datetime | None
    doc_url: str
    partition_key: str
    partition_date: date | datetime
    listing_url: str
    extension: str

    # Which landing version this came from.
    landing_id: object
    source_file_hash: str
    source_file_path: str

    # The transformed file.
    file_hash: str
    file_path: str
    file_size: int

    # What extraction found. None for passthrough formats.
    title: str | None = None
    text_chars: int | None = None
    extracted_with: str | None = None

    source_metadata: dict = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)
    transformed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def carried_fields(cls) -> tuple[str, ...]:
        """Landing fields copied verbatim; everything else is set by the transformation."""
        return (
            "identifier", "identifier_slug", "source", "jurisdiction", "language",
            "issuing_authority", "issuing_authority_id", "description", "published_date",
            "doc_url", "partition_key", "partition_date", "listing_url", "extension",
            "source_metadata",
        )

    def to_document(self) -> dict:
        document = asdict(self)
        for name in ("published_date", "partition_date"):
            value = document[name]
            if isinstance(value, date) and not isinstance(value, datetime):
                document[name] = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        return document
