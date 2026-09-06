"""Transformation job against the real compose stack. Skipped if it is not running."""

import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone

import boto3
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from wrc.config import get_settings
from wrc.documents import DocumentContract, content_fingerprint, object_key, slugify_identifier
from wrc.partitions import PartitionSize
from wrc.source import SourceSpec
from wrc.transform.job import TransformJob

SOURCE = "fixture-transform"
LANDING, TRANSFORMED = "_test_landing_t", "_test_transformed_t"


@pytest.fixture
def stack():
    base = get_settings()
    try:
        MongoClient(base.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=1500).admin.command("ping")
    except PyMongoError:
        pytest.skip("compose stack not running")
    settings = base.model_copy(update={"mongo_landing_collection": LANDING, "mongo_transformed_collection": TRANSFORMED})
    spec = replace(SourceSpec.load("config/sources/wrc.yml"), name=SOURCE)
    yield settings, spec
    db = MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db]
    db.drop_collection(LANDING); db.drop_collection(TRANSFORMED)
    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key,
                      aws_secret_access_key=settings.s3_secret_key.get_secret_value(), region_name=settings.s3_region)
    for bucket in (settings.s3_landing_bucket, settings.s3_transformed_bucket):
        for o in s3.list_objects_v2(Bucket=bucket, Prefix=f"{SOURCE}/").get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=o["Key"])


def seed(settings, identifier: str, body: bytes, extension="html", content_type="text/html; charset=utf-8", scraped_at=None, doc_url="https://x/d.html"):
    """Write one landing row + object the way the pipelines would."""
    slug = slugify_identifier(identifier)
    digest, basis = content_fingerprint(body, extension, DocumentContract(content="div.content", title="h1.page-title"))
    key = object_key(SOURCE, "3", "2024-01", slug, digest, extension)
    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key,
                      aws_secret_access_key=settings.s3_secret_key.get_secret_value(), region_name=settings.s3_region)
    s3.put_object(Bucket=settings.s3_landing_bucket, Key=key, Body=body, ContentType=content_type)
    row = {
        "identifier": identifier, "identifier_slug": slug, "source": SOURCE, "jurisdiction": "XX", "language": "en",
        "issuing_authority": "Labour Court", "issuing_authority_id": "3", "description": "d",
        "published_date": datetime(2024, 1, 20, tzinfo=timezone.utc), "doc_url": doc_url,
        "partition_key": "2024-01", "partition_date": datetime(2024, 1, 1, tzinfo=timezone.utc), "listing_url": "https://x/l",
        "scraped_at": scraped_at or datetime.now(timezone.utc), "source_metadata": {}, "quality_flags": [],
        "content_type": content_type, "extension": extension, "file_size": len(body),
        "file_hash": digest, "file_hash_basis": basis, "raw_sha256": hashlib.sha256(body).hexdigest(),
        "object_key": key, "file_path": f"s3://{settings.s3_landing_bucket}/{key}",
    }
    MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db][LANDING].insert_one(row)
    return row


def job(settings, spec):
    return TransformJob(settings, spec, date(2024, 1, 1), date(2024, 1, 31), PartitionSize.MONTH)


HTML = b"<html><body><nav>m</nav><h1 class='page-title'>IR - SC \xe2\x80\x93 1</h1><div class='content'><p>decision</p></div><script>x()</script></body></html>"


def test_transforms_renames_and_records_then_is_unchanged_on_rerun(stack):
    settings, spec = stack
    seed(settings, "IR - SC – 1", HTML)
    summary = job(settings, spec).run()
    assert summary["transformed"] == 1 and summary["complete"]

    db = MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db]
    row = db[TRANSFORMED].find_one({"source": SOURCE})
    assert row["file_path"] == f"s3://{settings.s3_transformed_bucket}/{SOURCE}/IR-SC-1.html"
    assert row["title"] == "IR - SC – 1" and row["text_chars"] == len("decision")
    assert row["source_file_hash"] and row["file_hash"] != row["source_file_hash"]

    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key,
                      aws_secret_access_key=settings.s3_secret_key.get_secret_value(), region_name=settings.s3_region)
    out = s3.get_object(Bucket=settings.s3_transformed_bucket, Key=f"{SOURCE}/IR-SC-1.html")["Body"].read()
    assert hashlib.sha256(out).hexdigest() == row["file_hash"]
    assert b"<nav>" not in out and b"x()" not in out and b"<p>decision</p>" in out

    again = job(settings, spec).run()
    assert (again["unchanged"], again["transformed"]) == (1, 0) and again["complete"]
    assert db[LANDING].count_documents({"source": SOURCE}) == 1, "landing untouched"


def test_a_new_landing_version_replaces_the_transformed_document(stack):
    settings, spec = stack
    seed(settings, "A", HTML, scraped_at=datetime(2024, 2, 1, tzinfo=timezone.utc))
    job(settings, spec).run()
    seed(settings, "A", HTML.replace(b"decision", b"amended decision"), scraped_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
    summary = job(settings, spec).run()
    assert summary["transformed"] == 1
    db = MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db]
    assert db[TRANSFORMED].count_documents({"source": SOURCE, "identifier": "A"}) == 1
    assert db[TRANSFORMED].find_one({"identifier": "A"})["text_chars"] == len("amended decision")
    assert db[LANDING].count_documents({"source": SOURCE, "identifier": "A"}) == 2, "both versions kept in landing"


def test_pdf_passes_through_byte_for_byte(stack):
    settings, spec = stack
    pdf = b"%PDF-1.4 fake"
    seed(settings, "P1", pdf, extension="pdf", content_type="application/pdf")
    summary = job(settings, spec).run()
    assert summary["passthrough"] == 1
    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key,
                      aws_secret_access_key=settings.s3_secret_key.get_secret_value(), region_name=settings.s3_region)
    assert s3.get_object(Bucket=settings.s3_transformed_bucket, Key=f"{SOURCE}/P1.pdf")["Body"].read() == pdf
    row = MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db][TRANSFORMED].find_one({"identifier": "P1"})
    assert row["file_hash"] == hashlib.sha256(pdf).hexdigest() == row["source_file_hash"]


def test_reused_identifier_yields_two_documents_with_deterministic_names(stack):
    """Two decisions published as RPD241: two rows, two files, neither named RPD241.html."""
    settings, spec = stack
    seed(settings, "RPD241", HTML.replace(b"decision", b"july decision"), doc_url="https://x/2024/july/rpd241.html")
    seed(settings, "RPD241", HTML.replace(b"decision", b"february decision"), doc_url="https://x/2024/february/rpd241.html")
    seed(settings, "LCR1", HTML, doc_url="https://x/lcr1.html")
    summary = job(settings, spec).run()
    assert summary["transformed"] == 3 and summary["complete"]
    assert summary["quality_flags"] == {"identifier_reused": 2}
    for r in MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db][TRANSFORMED].find({"identifier": "RPD241"}):
        assert r["quality_flags"] == ["identifier_reused"], "flag once, even when landing already carried it"

    db = MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db]
    rows = list(db[TRANSFORMED].find({"source": SOURCE, "identifier": "RPD241"}))
    assert len(rows) == 2
    names = sorted(r["file_path"].rsplit("/", 1)[-1] for r in rows)
    assert all(n.startswith("RPD241~") and n.endswith(".html") and len(n) == len("RPD241~xxxxxxxx.html") for n in names)
    assert names[0] != names[1]
    assert db[TRANSFORMED].find_one({"identifier": "LCR1"})["file_path"].endswith("/LCR1.html"), "unique identifiers keep the spec's name"

    again = job(settings, spec).run()
    assert (again["unchanged"], again["transformed"]) == (3, 0)


def test_stale_index_is_replaced_and_a_late_reuse_renames_the_first_document(stack):
    """The transformed zone reconciles its own schema and its own file names."""
    settings, spec = stack
    db = MongoClient(settings.mongo_uri.get_secret_value())[settings.mongo_db]
    db[TRANSFORMED].create_index([("source", 1), ("identifier", 1)], unique=True, name="document_identity")

    seed(settings, "RPD241", HTML, doc_url="https://x/2024/july/rpd241.html")
    first = job(settings, spec)
    assert [tuple(k) for k in db[TRANSFORMED].index_information()["document_identity"]["key"]] == [("source", 1), ("identifier", 1), ("doc_url", 1)]
    first.run()
    assert db[TRANSFORMED].find_one({"identifier": "RPD241"})["file_path"].endswith("/RPD241.html")

    seed(settings, "RPD241", HTML.replace(b"decision", b"other"), doc_url="https://x/2024/february/rpd241.html")
    summary = job(settings, spec).run()
    assert summary["transformed"] == 2 and summary["unchanged"] == 0, "the first document is re-written under its new name"
    paths = sorted(r["file_path"].rsplit("/", 1)[-1] for r in db[TRANSFORMED].find({"identifier": "RPD241"}))
    assert len(paths) == 2 and all(p.startswith("RPD241~") for p in paths)

    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key,
                      aws_secret_access_key=settings.s3_secret_key.get_secret_value(), region_name=settings.s3_region)
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=settings.s3_transformed_bucket, Prefix=f"{SOURCE}/")["Contents"]}
    assert f"{SOURCE}/RPD241.html" not in keys, "stale bare-named object removed"
    assert len(keys) == 2
