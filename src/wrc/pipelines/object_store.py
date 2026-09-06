from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from wrc.config import get_settings
from wrc.items import DocumentRecord

logger = logging.getLogger(__name__)


class ObjectStorePipeline:
    def open_spider(self, spider):
        settings = get_settings()
        self.bucket = settings.s3_landing_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name=settings.s3_region,
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    async def process_item(self, item: DocumentRecord, spider):
        if item.content is None or item.object_key is None:
            return item

        stats = spider.run_stats.partition(
            item.issuing_authority_id, item.issuing_authority, item.partition_key
        )
        try:
            written = await asyncio.to_thread(self._put_if_absent, item)
        except ClientError as exc:
            stats.store_failed += 1
            stats.failures.append(
                {"url": item.doc_url, "identifier": item.identifier, "reason": "object_store_failed",
                 "error": exc.response.get("Error", {}).get("Code")}
            )
            logger.error(
                "object_store_failed",
                extra={"identifier": item.identifier, "key": item.object_key, "error": str(exc)},
            )
            return item

        item.file_path = f"s3://{self.bucket}/{item.object_key}"
        item.stored_at = datetime.now(timezone.utc)
        if not written:
            logger.debug("object_already_present", extra={"key": item.object_key})
        return item

    def _put_if_absent(self, item: DocumentRecord) -> bool:
        """The key already encodes the content hash, and the stored bytes may differ from
        this fetch only by the source's timing comment. Overwriting would change a
        landing-zone object, so an existing key is left exactly as it is."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=item.object_key)
            return False
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                raise
        self.client.put_object(
            Bucket=self.bucket,
            Key=item.object_key,
            Body=item.content,
            ContentType=item.content_type or "application/octet-stream",
            Metadata={"raw-sha256": item.raw_sha256 or "", "source": item.source},
        )
        return True
