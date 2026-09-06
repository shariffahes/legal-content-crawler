from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

HTML_COMMENT = re.compile(rb"<!--.*?-->", re.S)

# Extension by media type. Sources lie in URLs more often than in headers, so the
# header is consulted first and the URL only as a fallback.
EXTENSION_BY_MEDIA_TYPE = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/rtf": "rtf",
    "text/plain": "txt",
}
KNOWN_EXTENSIONS = frozenset(EXTENSION_BY_MEDIA_TYPE.values())
UNKNOWN_EXTENSION = "bin"

SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
SLUG_REPEATS = re.compile(r"-{2,}")


def content_hash(body: bytes) -> str:
    """sha256 of the document with HTML comments removed. Stable across fetches."""
    return hashlib.sha256(HTML_COMMENT.sub(b"", body)).hexdigest()


def raw_hash(body: bytes) -> str:
    """sha256 of the bytes exactly as received, for integrity of the stored object."""
    return hashlib.sha256(body).hexdigest()


def slugify_identifier(identifier: str) -> str:
    """Filesystem- and object-key-safe form of a source identifier.

    Deterministic and lossy on purpose: ``IR - SC – 00001494`` and ``IR-SC-00001494``
    are the same reference written two ways and should land on the same name.
    """
    collapsed = " ".join(identifier.split())
    replaced = SLUG_UNSAFE.sub("-", collapsed)
    return SLUG_REPEATS.sub("-", replaced).strip("-").upper()


def media_type(content_type_header: str | bytes | None) -> str | None:
    if not content_type_header:
        return None
    if isinstance(content_type_header, bytes):
        content_type_header = content_type_header.decode("latin-1")
    return content_type_header.split(";", 1)[0].strip().lower() or None


def extension_for(content_type_header: str | bytes | None, url: str) -> tuple[str, bool]:
    """(extension, recognised). Header first, URL suffix second, ``bin`` otherwise."""
    known = EXTENSION_BY_MEDIA_TYPE.get(media_type(content_type_header) or "")
    if known:
        return known, True
    suffix = urlparse(url).path.rsplit(".", 1)
    if len(suffix) == 2 and suffix[1].lower() in KNOWN_EXTENSIONS:
        return suffix[1].lower(), True
    return UNKNOWN_EXTENSION, False


def object_key(
    source: str, facet_value_id: str, partition_key: str, slug: str, file_hash: str, extension: str
) -> str:
    """Landing-zone key: source first so one bucket serves every source, hash last so a
    changed document is a new object rather than an overwrite."""
    return f"{source}/{facet_value_id}/{partition_key}/{slug}/{file_hash}.{extension}"
