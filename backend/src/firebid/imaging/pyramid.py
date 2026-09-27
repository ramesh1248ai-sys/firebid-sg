"""The deep-zoom pyramid: how a sheet becomes tiles, and where each tile lives.

An A0 fire-protection layout at a useful resolution is roughly 5,000 by 7,000 pixels. No
browser will hold that as one image, so the viewer gets a pyramid: level 0 is the whole sheet
in a single tile, and each level doubles the resolution until the top level is the sheet at
full size. OpenSeadragon asks for the tiles covering what is on screen and nothing else
(ADR-005).

**Only the low levels are rendered at ingest.** Most close-up tiles are never looked at — an
estimator zooms into the riser shafts and the valve room, not into every square metre of a
car park — so rendering them all would spend most of its time and storage on tiles nobody
requests. The low levels are cheap (one render, then downsampling) and cover panning and
overview, which is what the first minute with a sheet is. Close-up tiles are rendered on
first request and cached.

**Cache keys carry the content hash and the renderer version** (the stage-caching
convention). A re-uploaded sheet with identical content reuses its tiles; a change to the
renderer invalidates every tile without anyone having to remember to clear a bucket.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

# Bumped whenever a change would make a tile look different. It is part of every tile's key,
# so bumping it invalidates the cache rather than leaving a mix of old and new tiles.
RENDERER_VERSION = "r1"

# 254 px of image plus a 1 px skirt on each side, which is how OpenSeadragon avoids seams
# between neighbouring tiles.
TILE_SIZE = 256
TILE_OVERLAP = 1

# What a sheet is rendered at for the top of the pyramid. 150 dpi resolves a 2 mm text height
# on an A0 sheet to about 12 pixels, which is readable and is where scanned tender drawings
# stop carrying more detail anyway.
TARGET_DPI = 150
MM_PER_INCH = 25.4

# A guard against a page that declares absurd dimensions: 30,000 px is an A0 sheet at 900 dpi.
MAX_BASE_PIXELS = 30_000

# Every level whose longest side fits in this is rendered at ingest. 2,048 px is a sheet
# filling a large monitor, so panning and overview never wait for a render.
PRE_RENDERED_MAX_PIXELS = 2048

# What the sheet list shows.
THUMBNAIL_MAX_PIXELS = 400


@dataclass(frozen=True)
class Pyramid:
    """The shape of one sheet's tile pyramid."""

    width_px: int
    height_px: int

    @property
    def max_level(self) -> int:
        """The level at which the sheet is at full resolution."""
        return math.ceil(math.log2(max(self.width_px, self.height_px, 1)))

    def size_at(self, level: int) -> tuple[int, int]:
        """The sheet's pixel size at `level`, never smaller than one pixel."""
        scale = 2 ** (self.max_level - level)
        return (
            max(1, math.ceil(self.width_px / scale)),
            max(1, math.ceil(self.height_px / scale)),
        )

    def grid_at(self, level: int) -> tuple[int, int]:
        """How many tile columns and rows `level` has."""
        width, height = self.size_at(level)
        return (math.ceil(width / TILE_SIZE), math.ceil(height / TILE_SIZE))

    def tiles_at(self, level: int) -> list[tuple[int, int]]:
        columns, rows = self.grid_at(level)
        return [(column, row) for row in range(rows) for column in range(columns)]

    @property
    def pre_rendered_levels(self) -> list[int]:
        """The levels rendered at ingest: everything up to a screen-sized view."""
        return [
            level
            for level in range(self.max_level + 1)
            if max(self.size_at(level)) <= PRE_RENDERED_MAX_PIXELS
        ]

    @property
    def on_demand_levels(self) -> list[int]:
        pre_rendered = set(self.pre_rendered_levels)
        return [level for level in range(self.max_level + 1) if level not in pre_rendered]

    def region_at(self, level: int, column: int, row: int) -> tuple[int, int, int, int]:
        """The box of `level`'s image that one tile covers, including its overlap skirt.

        Returned as (left, top, right, bottom) in that level's pixels, clipped to the image.
        """
        width, height = self.size_at(level)
        left = max(0, column * TILE_SIZE - TILE_OVERLAP)
        top = max(0, row * TILE_SIZE - TILE_OVERLAP)
        right = min(width, (column + 1) * TILE_SIZE + TILE_OVERLAP)
        bottom = min(height, (row + 1) * TILE_SIZE + TILE_OVERLAP)
        return (left, top, right, bottom)

    def holds(self, level: int, column: int, row: int) -> bool:
        """Whether this tile exists. A request for one that does not is a 404, not a render."""
        if not 0 <= level <= self.max_level:
            return False
        columns, rows = self.grid_at(level)
        return 0 <= column < columns and 0 <= row < rows


def base_pixels(width_mm: float, height_mm: float, dpi: int = TARGET_DPI) -> tuple[int, int]:
    """The full-resolution pixel size of a sheet of this paper size.

    Capped, because a malformed page can declare a size in kilometres and the cap is the
    difference between a slow render and one that never finishes.
    """
    scale = dpi / MM_PER_INCH
    width = max(1, round(width_mm * scale))
    height = max(1, round(height_mm * scale))
    if max(width, height) > MAX_BASE_PIXELS:
        shrink = MAX_BASE_PIXELS / max(width, height)
        width = max(1, round(width * shrink))
        height = max(1, round(height * shrink))
    return width, height


def content_hash(document_sha256: str, page_index: int, width_px: int, height_px: int) -> str:
    """What identifies a sheet's pixels, for the tile cache.

    The document's digest plus the page and the size it is rendered at: the same sheet
    uploaded to two bids, or re-uploaded after an addendum, resolves to the same tiles.
    """
    material = f"{document_sha256}:{page_index}:{width_px}x{height_px}:{RENDERER_VERSION}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def tile_key(sheet_hash: str, level: int, column: int, row: int) -> str:
    """Where one tile is stored. Shared across bids, because the hash is the content."""
    return f"tiles/{RENDERER_VERSION}/{sheet_hash}/{level}/{column}_{row}.webp"


def thumbnail_key(sheet_hash: str) -> str:
    return f"tiles/{RENDERER_VERSION}/{sheet_hash}/thumbnail.webp"


def descriptor(pyramid: Pyramid, sheet_hash: str) -> dict[str, object]:
    """What the viewer needs to ask for tiles, in OpenSeadragon's custom-tile-source shape."""
    return {
        "width": pyramid.width_px,
        "height": pyramid.height_px,
        "tileSize": TILE_SIZE,
        "tileOverlap": TILE_OVERLAP,
        "minLevel": 0,
        "maxLevel": pyramid.max_level,
        "format": "webp",
        "contentHash": sheet_hash,
        "rendererVersion": RENDERER_VERSION,
        "preRenderedLevels": pyramid.pre_rendered_levels,
    }
