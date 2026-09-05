"""Smoke test: prove Mongo and the object store are reachable and writable."""

import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from pymongo import MongoClient

from wrc.config import get_settings

PROBE_ID = "infra-check"
PROBE_KEY = "_infra_check/probe.txt"


def check_mongo(settings) -> None:
    client = MongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    collection = client[settings.mongo_db]["_infra_check"]
    collection.replace_one(
        {"_id": PROBE_ID},
        {"_id": PROBE_ID, "checked_at": datetime.now(timezone.utc)},
        upsert=True,
    )
    doc = collection.find_one({"_id": PROBE_ID})
    print(f"  mongo   : {settings.mongo_db} reachable, read back {doc['_id']} @ {doc['checked_at']}")
    client.close()


def check_object_store(settings) -> None:
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
    )

    for bucket in (settings.s3_landing_bucket, settings.s3_transformed_bucket):
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError:
            s3.create_bucket(Bucket=bucket)
            print(f"  s3      : created bucket {bucket}")

    body = f"probe {datetime.now(timezone.utc).isoformat()}".encode()
    s3.put_object(Bucket=settings.s3_landing_bucket, Key=PROBE_KEY, Body=body)
    fetched = s3.get_object(Bucket=settings.s3_landing_bucket, Key=PROBE_KEY)["Body"].read()

    if fetched != body:
        raise RuntimeError("object round-trip mismatch")
    print(f"  s3      : {settings.s3_landing_bucket}/{PROBE_KEY} round-tripped {len(fetched)} bytes")


def main() -> int:
    settings = get_settings()
    print("infra check")
    try:
        check_mongo(settings)
        check_object_store(settings)
    except Exception as exc:
        print(f"  FAILED  : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
