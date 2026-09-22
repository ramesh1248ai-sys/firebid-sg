"""S3-compatible object storage.

Originals are written once: :meth:`put_once` refuses to overwrite an existing key, so an
uploaded tender document can never be replaced in place (project-context, immutability).
Locally this is SeaweedFS; in production it is the cloud provider's object storage (ADR-001).
"""

from __future__ import annotations

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
    ) -> None:
        self.bucket = bucket
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
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
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
