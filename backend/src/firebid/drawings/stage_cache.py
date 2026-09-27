"""The stage cache: a drawing stage's output, kept by what went in and what made it.

Each stage of drawing understanding (geometry now; symbols, networks and takeoff later)
stores its output under the content hash of its input plus the stage's own version. Re-run
on an unchanged sheet, a stage finds its output and does no work; change the stage's code
and bump its version, and every sheet is done again once (project-context "Stage caching").

Keys hold no bid: identical sheets on two bids share one output, as tiles do. That is why a
cache key never decides who may see anything. Access always goes through the sheet, which
belongs to a bid.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from firebid.storage.object_store import ObjectExists, ObjectStore


@dataclass(frozen=True)
class StageCache:
    store: ObjectStore
    stage: str  # e.g. "geometry"
    version: str  # the stage's extractor or model version
    suffix: str = "parquet"

    def key(self, content_hash: str) -> str:
        return f"stages/{self.stage}/v{self.version}/{content_hash}.{self.suffix}"

    def get(self, content_hash: str) -> bytes | None:
        key = self.key(content_hash)
        if not self.store.exists(key):
            return None
        return self.store.get(key)

    def put(self, content_hash: str, payload: bytes, content_type: str) -> str:
        key = self.key(content_hash)
        # The same input and version always give the same output; a second writer is fine.
        with contextlib.suppress(ObjectExists):
            self.store.put_once(key, payload, content_type=content_type)
        return key
