"""Application configuration, loaded from environment variables or .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from wrc.partitions import PartitionSize


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mongo_uri: SecretStr
    mongo_db: str
    mongo_landing_collection: str
    mongo_transformed_collection: str

    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_region: str
    s3_landing_bucket: str
    s3_transformed_bucket: str

    user_agent: str
    request_delay: float

    partition_size: PartitionSize
    source_spec_path: str

    concurrent_requests: int
    concurrent_requests_per_domain: int
    download_timeout: int
    retry_times: int
    autothrottle_enabled: bool
    autothrottle_target_concurrency: float
    autothrottle_max_delay: float
    robotstxt_obey: bool
    log_level: str
    max_recovery_pages: int


@lru_cache
def get_settings() -> Settings:
    return Settings()
