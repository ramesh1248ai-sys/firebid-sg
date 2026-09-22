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


@lru_cache
def get_settings() -> Settings:
    return Settings()
