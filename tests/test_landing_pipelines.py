"""Landing pipelines against the real compose stack. Skipped if it is not running."""

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from wrc.config import get_settings
from wrc.documents import content_hash, object_key, raw_hash, slugify_identifier
from wrc.items import DocumentRecord
from wrc.stats import RunStats

TEST_COLLECTION = "_test_landing"
TEST_SOURCE = "fixture-test"


def test_to_document_drops_bytes_and_widens_dates():
    record = DocumentRecord(
        identifier="X", source="s", jurisdiction="XX", language="en",
        issuing_authority="A", issuing_authority_id="1", description="d",
        published_date=date(2024, 2, 20), doc_url="https://h/x.html",
        partition_key="2024-02", partition_date=date(2024, 2, 1), listing_url="https://h/l",
        content=b"bytes",
    )
    document = record.to_document()
    assert "content" not in document
    assert document["published_date"] == datetime(2024, 2, 20, tzinfo=timezone.utc)
    assert document["partition_date"] == datetime(2024, 2, 1, tzinfo=timezone.utc)
    assert document["identifier"] == "X"


@pytest.fixture
def stack(monkeypatch):
    base = get_settings()
    try:
        MongoClient(base.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=1500).admin.command("ping")
    except PyMongoError:
        pytest.skip("compose stack not running")
    settings = base.model_copy(update={"mongo_landing_collection": TEST_COLLECTION})
    monkeypatch.setattr("wrc.pipelines.object_store.get_settings", lambda: settings)
    monkeypatch.setattr("wrc.pipelines.mongo_landing.get_settings", lambda: settings)
    yield settings
    client = MongoClient(settings.mongo_uri.get_secret_value())
    client[settings.mongo_db].drop_collection(TEST_COLLECTION)
    import boto3
    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key,
                      aws_secret_access_key=settings.s3_secret_key.get_secret_value(), region_name=settings.s3_region)
    listed = s3.list_objects_v2(Bucket=settings.s3_landing_bucket, Prefix=f"{TEST_SOURCE}/").get("Contents", [])
    for obj in listed:
        s3.delete_object(Bucket=settings.s3_landing_bucket, Key=obj["Key"])


def fetched_record(body: bytes, identifier: str = "IR - SC – 1") -> DocumentRecord:
    slug = slugify_identifier(identifier)
    digest = content_hash(body)
    return DocumentRecord(
        identifier=identifier, source=TEST_SOURCE, jurisdiction="XX", language="en",
        issuing_authority="Fixture Court", issuing_authority_id="fc", description="d",
        published_date=date(2024, 2, 20), doc_url="https://fixture.example/d.html",
        partition_key="2024-02", partition_date=date(2024, 2, 1), listing_url="https://fixture.example/l",
        identifier_slug=slug, content_type="text/html; charset=utf-8", extension="html",
        file_size=len(body), file_hash=digest, raw_sha256=raw_hash(body),
        object_key=object_key(TEST_SOURCE, "fc", "2024-02", slug, digest, "html"),
        fetched_at=datetime.now(timezone.utc), content=body,
    )


def run_pipelines(record):
    from wrc.pipelines.mongo_landing import MongoLandingPipeline
    from wrc.pipelines.object_store import ObjectStorePipeline

    spider = SimpleNamespace(run_stats=RunStats())
    objects, mongo = ObjectStorePipeline(), MongoLandingPipeline()
    objects.open_spider(spider)
    mongo.open_spider(spider)

    async def go():
        out = await objects.process_item(record, spider)
        return await mongo.process_item(out, spider)

    result = asyncio.run(go())
    return result, spider.run_stats.partitions[0], objects, mongo


def test_first_write_stores_and_rerun_is_unchanged(stack):
    body = b"<html><!-- Elapsed time: 0.1 --><body>decision</body></html>"
    record, stats, objects, mongo = run_pipelines(fetched_record(body))
    assert record.file_path == f"s3://{stack.s3_landing_bucket}/{record.object_key}"
    assert record.stored_at is not None
    assert (stats.stored, stats.unchanged) == (1, 0)

    same_content_new_comment = b"<html><!-- Elapsed time: 0.9 --><body>decision</body></html>"
    record2, stats2, _, _ = run_pipelines(fetched_record(same_content_new_comment))
    assert record2.object_key == record.object_key
    assert (stats2.stored, stats2.unchanged) == (0, 1)

    head = objects.client.head_object(Bucket=stack.s3_landing_bucket, Key=record.object_key)
    assert head["Metadata"]["raw-sha256"] == raw_hash(body), "first bytes kept; second fetch must not overwrite"
    assert mongo.collection.count_documents({"source": TEST_SOURCE}) == 1


def test_changed_content_is_a_new_row_and_a_new_object(stack):
    run_pipelines(fetched_record(b"<body>version one</body>"))
    record, stats, objects, mongo = run_pipelines(fetched_record(b"<body>version two</body>"))
    assert stats.stored == 1
    rows = list(mongo.collection.find({"source": TEST_SOURCE}).sort("scraped_at", 1))
    assert len(rows) == 2 and rows[0]["file_hash"] != rows[1]["file_hash"]
    keys = {o["Key"] for o in objects.client.list_objects_v2(Bucket=stack.s3_landing_bucket, Prefix=f"{TEST_SOURCE}/")["Contents"]}
    assert len(keys) == 2


def test_unique_index_is_per_source(stack):
    run_pipelines(fetched_record(b"<body>x</body>"))
    other = fetched_record(b"<body>x</body>")
    other.source = "fixture-other"
    other.object_key = other.object_key.replace(TEST_SOURCE, "fixture-other", 1)
    _, stats, objects, mongo = run_pipelines(other)
    assert stats.stored == 1, "same identifier and hash under another source must not collide"
    mongo.collection.delete_many({"source": "fixture-other"})
    objects.client.delete_object(Bucket=stack.s3_landing_bucket, Key=other.object_key)
