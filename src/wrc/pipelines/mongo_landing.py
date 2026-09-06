from __future__ import annotations

import asyncio
import logging

from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from wrc.config import get_settings
from wrc.items import DocumentRecord

logger = logging.getLogger(__name__)

# Identifiers are only unique within a source, and a changed document is a new row.
CONTENT_KEY = [("source", ASCENDING), ("identifier", ASCENDING), ("file_hash", ASCENDING)]
# The transformation reads by source and date range; equality before range.
READ_KEY = [("source", ASCENDING), ("partition_date", ASCENDING)]


class MongoLandingPipeline:
    def open_spider(self, spider):
        settings = get_settings()
        self.client = MongoClient(settings.mongo_uri.get_secret_value())
        self.collection = self.client[settings.mongo_db][settings.mongo_landing_collection]
        self.collection.create_index(CONTENT_KEY, unique=True, name="content_identity")
        self.collection.create_index(READ_KEY, name="source_partition_date")

    def close_spider(self, spider):
        self.client.close()

    async def process_item(self, item: DocumentRecord, spider):
        if item.file_hash is None:
            return item

        stats = spider.run_stats.partition(
            item.issuing_authority_id, item.issuing_authority, item.partition_key
        )
        try:
            await asyncio.to_thread(self.collection.insert_one, item.to_document())
        except DuplicateKeyError:
            stats.unchanged += 1
            logger.info(
                "record_unchanged",
                extra={"identifier": item.identifier, "file_hash": item.file_hash, "partition": item.partition_key},
            )
            return item
        except PyMongoError as exc:
            stats.store_failed += 1
            stats.failures.append(
                {"url": item.doc_url, "identifier": item.identifier, "reason": "metadata_store_failed",
                 "error": type(exc).__name__}
            )
            logger.error("metadata_store_failed", extra={"identifier": item.identifier, "error": str(exc)})
            return item

        stats.stored += 1
        return item
