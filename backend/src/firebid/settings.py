"""Application settings, read from environment variables prefixed ``FIREBID_``."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREBID_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    git_sha: str = "unknown"
    database_url: str = "postgresql://firebid:firebid@localhost:55432/firebid"
    # A heartbeat older than this marks the job queue as unhealthy.
    heartbeat_max_age_seconds: int = 180
    # Jobs per worker process. Keep 1 for synchronous tasks; add processes to scale.
    worker_concurrency: int = 1
    worker_name: str = "worker"

    organisation_name: str = "FireBid SG"

    # Identity provider: Keycloak in development, Microsoft Entra ID in staging and production.
    oidc_issuer: str = "http://localhost:8081/realms/firebid"
    oidc_audience: str = "firebid-web"
    oidc_jwks_url: str = ""  # defaults to the issuer's JWKS endpoint
    # Jobs that legitimately span bids (deadline alerts, retention) use this role.
    database_service_url: str = ""

    # Object storage (SeaweedFS locally, cloud object storage in production).
    s3_bucket: str = "firebid-dev"
    s3_endpoint_url: str = "http://localhost:8333"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "ap-southeast-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
