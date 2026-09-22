"""Object storage against the running stack (make up, then make test-integration)."""

from __future__ import annotations

import os
import uuid

import pytest

from firebid.storage import ObjectExists, S3ObjectStore

pytestmark = pytest.mark.integration

HOST = os.environ.get("FIREBID_STACK_HOST", "localhost")
PORT = os.environ.get("FIREBID_S3_PORT", "8333")


@pytest.fixture
def store() -> S3ObjectStore:
    return S3ObjectStore(
        bucket="firebid-dev",
        endpoint_url=f"http://{HOST}:{PORT}",
        access_key_id="firebid-dev",
        secret_access_key="firebid-dev-secret",  # noqa: S106  (local development credential)
    )


def test_stores_and_reads_an_object(store: S3ObjectStore) -> None:
    key = f"tests/{uuid.uuid4()}.txt"
    store.put_once(key, b"FP-L05-201 Rev R04", content_type="text/plain")

    assert store.exists(key) is True
    assert store.get(key) == b"FP-L05-201 Rev R04"


def test_originals_are_written_once(store: S3ObjectStore) -> None:
    key = f"tests/{uuid.uuid4()}.txt"
    store.put_once(key, b"original", content_type="text/plain")

    with pytest.raises(ObjectExists):
        store.put_once(key, b"replacement", content_type="text/plain")
    assert store.get(key) == b"original"


def test_missing_keys_report_absent(store: S3ObjectStore) -> None:
    assert store.exists(f"tests/{uuid.uuid4()}.txt") is False


def test_presigned_url_points_at_the_object(store: S3ObjectStore) -> None:
    key = f"tests/{uuid.uuid4()}.txt"
    store.put_once(key, b"hello", content_type="text/plain")

    url = store.presigned_url(key, expires_in=60)
    assert key in url
    assert "X-Amz-Signature" in url
