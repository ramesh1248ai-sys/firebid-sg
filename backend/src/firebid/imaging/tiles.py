"""Cutting a rendered sheet into tiles.

This runs inside the sandbox, on pixels a parser produced from a file we did not write, so it
never trusts a declared size: everything is derived from the decoded image.

WebP because a drawing is mostly white with thin dark lines, which WebP's lossless mode
encodes far smaller than PNG, and the lossy mode blurs exactly the hairlines an estimator is
counting. Lossless throughout, therefore, at the smallest effort that still runs fast.
"""

from __future__ import annotations

import io

from PIL import Image

from firebid.imaging.pyramid import (
    THUMBNAIL_MAX_PIXELS,
    Pyramid,
    thumbnail_key,
    tile_key,
)

# Lossless: a lossy tile blurs a 0.3 mm pipe centreline into the paper, and the whole point
# of the viewer is that an estimator can see what was measured.
WEBP_FORMAT = "WEBP"
WEBP_OPTIONS: dict[str, int | bool] = {"lossless": True, "quality": 100, "method": 4}


def encode(image: Image.Image) -> bytes:
    """One PIL image as lossless WebP bytes."""
    buffer = io.BytesIO()
    # A drawing is RGB; an alpha channel would cost a third more for nothing.
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.save(buffer, WEBP_FORMAT, **WEBP_OPTIONS)
    return buffer.getvalue()


def cut_level(
    image: Image.Image, pyramid: Pyramid, level: int, sheet_hash: str
) -> dict[str, bytes]:
    """Every tile of one level, keyed by where it will be stored.

    `image` is the sheet at any resolution at or above this level; it is downsampled here, so
    one render at the pre-rendered maximum serves every level below it.
    """
    width, height = pyramid.size_at(level)
    scaled = (
        image
        if image.size == (width, height)
        else image.resize((width, height), Image.Resampling.LANCZOS)
    )

    tiles: dict[str, bytes] = {}
    for column, row in pyramid.tiles_at(level):
        box = pyramid.region_at(level, column, row)
        tiles[tile_key(sheet_hash, level, column, row)] = encode(scaled.crop(box))
    return tiles


def cut_one(image: Image.Image, pyramid: Pyramid, level: int, column: int, row: int) -> bytes:
    """A single tile, for the on-demand path. `image` must be that level's full image."""
    return encode(image.crop(pyramid.region_at(level, column, row)))


def make_thumbnail(image: Image.Image, sheet_hash: str) -> tuple[str, bytes]:
    """The sheet as it appears in a list: small, and still recognisably a drawing."""
    thumbnail = image.copy()
    thumbnail.thumbnail((THUMBNAIL_MAX_PIXELS, THUMBNAIL_MAX_PIXELS), Image.Resampling.LANCZOS)
    return thumbnail_key(sheet_hash), encode(thumbnail)


def pre_render(image: Image.Image, pyramid: Pyramid, sheet_hash: str) -> dict[str, bytes]:
    """Every tile rendered at ingest, plus the thumbnail, keyed by storage key."""
    output: dict[str, bytes] = {}
    for level in pyramid.pre_rendered_levels:
        output.update(cut_level(image, pyramid, level, sheet_hash))
    key, payload = make_thumbnail(image, sheet_hash)
    output[key] = payload
    return output
