"""Application settings, read from environment variables prefixed ``FIREBID_``."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from firebid.env import env_file


class Settings(BaseSettings):
    # The env file is found from the package, not the working directory: a .env that works
    # from backend/ but silently does nothing from the repository root wastes an afternoon.
    model_config = SettingsConfigDict(env_prefix="FIREBID_", env_file=env_file(), extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    git_sha: str = "unknown"
    database_url: str = "postgresql://firebid:firebid@localhost:55432/firebid"
    # A heartbeat older than this marks the job queue as unhealthy.
    heartbeat_max_age_seconds: int = 180
    # Jobs per worker process. Keep 1 for synchronous tasks; add processes to scale.
    worker_concurrency: int = 1
    worker_name: str = "worker"
    # Sheets one parse job reads at once, each in its own sandboxed process (NFR-01). Each
    # may use up to its sandbox memory limit (2 GiB), so this times 2 GiB must fit the
    # parser pool's memory: 2 for the 2 CPU, 4 GiB pods of ADR-008.
    parse_concurrency: int = 2

    organisation_name: str = "FireBid SG"

    # Internal notifications. With no SMTP host, alerts are logged instead of sent.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_sender: str = "firebid@example.com"
    smtp_use_tls: bool = True

    # Debugging payloads are encrypted with this key, held outside the database. Unset means
    # payloads cannot be stored or read at all, which is the right default.
    payload_encryption_key: str = ""

    # Identity provider: Keycloak in development, Microsoft Entra ID in staging and production.
    oidc_issuer: str = "http://localhost:8081/realms/firebid"
    oidc_audience: str = "firebid-web"
    oidc_jwks_url: str = ""  # defaults to the issuer's JWKS endpoint
    # Jobs that legitimately span bids (deadline alerts, retention) use this role.
    database_service_url: str = ""

    # Security baseline (docs/security-baseline.md). The app and API share an origin, so
    # cross-origin requests are refused unless an origin is named here.
    cors_allowed_origins: tuple[str, ...] = ()
    max_body_bytes: int = 2 * 1024 * 1024
    max_upload_bytes: int = 200 * 1024 * 1024
    rate_limit_per_minute: int = 300

    # Malware scanning. Every uploaded file is scanned before any parser opens it; an
    # outage holds files rather than letting them through (guardrail 9).
    clamav_host: str = "clamav"
    clamav_port: int = 3310

    # DWG conversion. Empty until ADR-003 records a converter licence, which is why DWG
    # files are recorded as awaiting conversion rather than rejected as unreadable.
    dwg_converter_command: str = ""

    # Object storage (SeaweedFS locally, cloud object storage in production).
    s3_bucket: str = "firebid-dev"
    s3_endpoint_url: str = "http://localhost:8333"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "ap-southeast-1"
    # Submission snapshots (FR-PKG-03). In production this is a bucket under a retention
    # lock (ADR-008), so an object cannot be changed or removed even by an administrator.
    # Empty: the snapshots go in the main bucket, written once.
    s3_snapshot_bucket: str = ""
    # Days an object written to the snapshot bucket is held under object lock. 0: the
    # bucket's own retention policy governs, and no per-object lock is asked for.
    s3_snapshot_lock_days: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
