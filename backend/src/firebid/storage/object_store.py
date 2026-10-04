"""S3-compatible object storage.

Originals are written once: :meth:`put_once` refuses to overwrite an existing key, so an
uploaded tender document can never be replaced in place (project-context, immutability).
Locally this is SeaweedFS; in production it is the cloud provider's object storage (ADR-001).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from firebid.settings import get_settings


class ObjectExists(Exception):
    """Raised when a write-once key is already taken."""


class ObjectStore(Protocol):
    def put_once(self, key: str, data: bytes, *, content_type: str) -> str: ...

    def put(self, key: str, data: bytes, *, content_type: str) -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str: ...


class S3ObjectStore:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str = "ap-southeast-1",
        lock_days: int = 0,
    ) -> None:
        self.bucket = bucket
        # Each object is written under a compliance-mode lock for this many days (the
        # snapshot bucket). 0: none is asked for.
        self.lock_days = lock_days
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_once(self, key: str, data: bytes, *, content_type: str) -> str:
        if self.exists(key):
            raise ObjectExists(key)
        return self.put(key, data, content_type=content_type)

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        extra: dict[str, Any] = {}
        if self.lock_days > 0:
            extra = {
                "ObjectLockMode": "COMPLIANCE",
                "ObjectLockRetainUntilDate": datetime.now(UTC) + timedelta(days=self.lock_days),
            }
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, **extra
        )
        return key

    def get(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        url: str = self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
        )
        return url


@lru_cache
def get_object_store() -> S3ObjectStore:
    settings = get_settings()
    return S3ObjectStore(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url or None,
        access_key_id=settings.s3_access_key_id or None,
        secret_access_key=settings.s3_secret_access_key or None,
        region=settings.s3_region,
    )


@lru_cache
def get_snapshot_store() -> S3ObjectStore:
    """Where submission snapshots are kept: the locked bucket, where one is configured."""
    settings = get_settings()
    return S3ObjectStore(
        bucket=settings.s3_snapshot_bucket or settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url or None,
        access_key_id=settings.s3_access_key_id or None,
        secret_access_key=settings.s3_secret_access_key or None,
        region=settings.s3_region,
        lock_days=settings.s3_snapshot_lock_days if settings.s3_snapshot_bucket else 0,
    )


class MemoryObjectStore:
    """An object store in a dict, for tests.

    Kept beside the real one so it stays honest about the behaviour that matters: `put_once`
    refuses an existing key, exactly as the S3 implementation does, so a test that relies on
    write-once semantics is testing the same rule production enforces.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.writes = 0

    def put_once(self, key: str, data: bytes, *, content_type: str) -> str:
        if self.exists(key):
            raise ObjectExists(key)
        return self.put(key, data, content_type=content_type)

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.objects[key] = data
        self.content_types[key] = content_type
        self.writes += 1
        return key

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def exists(self, key: str) -> bool:
        return key in self.objects

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        return f"memory://{key}?expires_in={expires_in}"
