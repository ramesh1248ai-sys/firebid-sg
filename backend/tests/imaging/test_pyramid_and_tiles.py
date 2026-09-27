"""The tile pyramid: its arithmetic, and what comes out of it (ADR-005).

The arithmetic matters more than it looks. A viewer that asks for a tile the server thinks
does not exist shows a blank patch of drawing, and an estimator counting sprinklers on a
blank patch counts none. So the grid, the overlap and the level sizes are checked against
what OpenSeadragon will actually ask for.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from firebid.imaging.pyramid import (
    PRE_RENDERED_MAX_PIXELS,
    RENDERER_VERSION,
    TILE_OVERLAP,
    TILE_SIZE,
    Pyramid,
    base_pixels,
    content_hash,
    descriptor,
    thumbnail_key,
    tile_key,
)
from firebid.imaging.tiles import cut_level, cut_one, encode, make_thumbnail, pre_render

pytestmark = pytest.mark.req("FR-DOC-01")

# An A1 sheet at the target resolution: the commonest tender drawing size.
A1_MM = (841.0, 594.0)


def drawing(width: int, height: int) -> Image.Image:
    """An image with something on it, so a blank render cannot pass for a real one."""
    image = Image.new("RGB", (width, height), "white")
    for x in range(0, width, 37):
        for y in range(0, height, 41):
            image.putpixel((x, y), (20, 20, 20))
    return image


class TestTheArithmetic:
    def test_the_top_level_is_the_sheet_at_full_size(self) -> None:
        pyramid = Pyramid(width_px=4967, height_px=3508)

        assert pyramid.size_at(pyramid.max_level) == (4967, 3508)

    def test_level_zero_is_one_tile(self) -> None:
        pyramid = Pyramid(width_px=4967, height_px=3508)

        assert pyramid.grid_at(0) == (1, 1)
        assert max(pyramid.size_at(0)) == 1

    def test_each_level_halves_the_one_above(self) -> None:
        pyramid = Pyramid(width_px=4096, height_px=2048)

        for level in range(1, pyramid.max_level + 1):
            wider, taller = pyramid.size_at(level)
            narrower, shorter = pyramid.size_at(level - 1)
            assert wider in (narrower * 2, narrower * 2 - 1)
            assert taller in (shorter * 2, shorter * 2 - 1)

    def test_a_tile_never_reaches_past_the_image(self) -> None:
        pyramid = Pyramid(width_px=1000, height_px=700)

        for level in range(pyramid.max_level + 1):
            width, height = pyramid.size_at(level)
            for column, row in pyramid.tiles_at(level):
                left, top, right, bottom = pyramid.region_at(level, column, row)
                assert 0 <= left < right <= width
                assert 0 <= top < bottom <= height

    def test_tiles_overlap_their_neighbours(self) -> None:
        """The skirt is what stops a hairline appearing between two tiles."""
        pyramid = Pyramid(width_px=2000, height_px=2000)
        level = pyramid.max_level

        first = pyramid.region_at(level, 0, 0)
        second = pyramid.region_at(level, 1, 0)

        assert first[2] > second[0], "neighbouring tiles must share pixels"
        assert first[2] - second[0] == TILE_OVERLAP * 2

    def test_the_tiles_of_a_level_cover_all_of_it(self) -> None:
        pyramid = Pyramid(width_px=900, height_px=650)
        level = pyramid.max_level
        width, height = pyramid.size_at(level)

        covered: set[tuple[int, int]] = set()
        for column, row in pyramid.tiles_at(level):
            left, top, right, bottom = pyramid.region_at(level, column, row)
            covered.update((x, y) for x in range(left, right) for y in range(top, bottom))

        assert len(covered) == width * height, "every pixel belongs to at least one tile"

    def test_a_tile_outside_the_grid_is_not_held(self) -> None:
        pyramid = Pyramid(width_px=500, height_px=500)
        columns, rows = pyramid.grid_at(pyramid.max_level)

        assert pyramid.holds(pyramid.max_level, columns - 1, rows - 1)
        assert not pyramid.holds(pyramid.max_level, columns, 0)
        assert not pyramid.holds(pyramid.max_level, 0, rows)
        assert not pyramid.holds(pyramid.max_level + 1, 0, 0)
        assert not pyramid.holds(-1, 0, 0)

    def test_every_level_is_either_pre_rendered_or_on_demand(self) -> None:
        pyramid = Pyramid(width_px=4967, height_px=3508)

        both = pyramid.pre_rendered_levels + pyramid.on_demand_levels
        assert sorted(both) == list(range(pyramid.max_level + 1))
        assert not set(pyramid.pre_rendered_levels) & set(pyramid.on_demand_levels)

    def test_the_pre_rendered_levels_stop_at_a_screenful(self) -> None:
        pyramid = Pyramid(width_px=4967, height_px=3508)

        assert max(pyramid.size_at(max(pyramid.pre_rendered_levels))) <= PRE_RENDERED_MAX_PIXELS
        assert pyramid.on_demand_levels, "a big sheet must have close-up levels left to render"

    def test_a_small_sheet_is_entirely_pre_rendered(self) -> None:
        """Nothing is deferred on a sheet that fits on screen; there is nothing to defer."""
        pyramid = Pyramid(width_px=800, height_px=600)

        assert pyramid.on_demand_levels == []


class TestSheetSizes:
    def test_an_a1_sheet_becomes_a_sensible_number_of_pixels(self) -> None:
        width, height = base_pixels(*A1_MM)

        assert 4000 < width < 6000, "A1 at the target resolution"
        assert height < width, "landscape stays landscape"

    def test_a_page_that_declares_an_absurd_size_is_capped(self) -> None:
        """A malformed page claiming kilometres must not become a render that never ends."""
        width, height = base_pixels(100_000.0, 50_000.0)

        assert max(width, height) <= 30_000
        assert width / height == pytest.approx(2.0, abs=0.01), "the proportions survive"

    def test_a_tiny_page_still_has_a_pixel(self) -> None:
        assert base_pixels(0.01, 0.01) == (1, 1)


class TestCacheKeys:
    def test_the_same_sheet_resolves_to_the_same_key(self) -> None:
        """This is what makes a re-uploaded tender set cost nothing to re-render."""
        first = content_hash("a" * 64, 3, 4967, 3508)
        second = content_hash("a" * 64, 3, 4967, 3508)

        assert first == second

    def test_a_different_page_of_the_same_file_differs(self) -> None:
        assert content_hash("a" * 64, 3, 4967, 3508) != content_hash("a" * 64, 4, 4967, 3508)

    def test_a_different_file_differs(self) -> None:
        assert content_hash("a" * 64, 0, 100, 100) != content_hash("b" * 64, 0, 100, 100)

    def test_the_renderer_version_is_in_the_key(self) -> None:
        """Changing the renderer must invalidate every tile without clearing a bucket."""
        assert RENDERER_VERSION in tile_key("abc", 3, 1, 2)
        assert RENDERER_VERSION in thumbnail_key("abc")

    def test_a_tile_key_names_its_place(self) -> None:
        key = tile_key("abcdef", 7, 3, 5)

        assert key.endswith("/7/3_5.webp")
        assert "abcdef" in key


class TestCuttingTiles:
    def test_a_level_produces_its_whole_grid(self) -> None:
        pyramid = Pyramid(width_px=1000, height_px=700)
        level = pyramid.max_level

        tiles = cut_level(drawing(1000, 700), pyramid, level, "hash")

        assert len(tiles) == len(pyramid.tiles_at(level))
        assert all(key.startswith("tiles/") for key in tiles)

    def test_every_tile_is_a_readable_webp_of_the_right_size(self) -> None:
        pyramid = Pyramid(width_px=600, height_px=400)
        level = pyramid.max_level

        tiles = cut_level(drawing(600, 400), pyramid, level, "hash")

        for key, payload in tiles.items():
            with Image.open(io.BytesIO(payload)) as tile:
                assert tile.format == "WEBP"
                assert max(tile.size) <= TILE_SIZE + 2 * TILE_OVERLAP, key

    def test_a_lower_level_is_cut_from_the_same_render(self) -> None:
        """One render serves every pre-rendered level; that is what makes ingest affordable."""
        pyramid = Pyramid(width_px=2000, height_px=1000)
        image = drawing(1024, 512)

        tiles = cut_level(image, pyramid, pyramid.max_level - 1, "hash")

        assert len(tiles) == len(pyramid.tiles_at(pyramid.max_level - 1))

    def test_one_tile_matches_the_same_tile_from_the_whole_level(self) -> None:
        """The on-demand path and the ingest path must produce identical pixels."""
        pyramid = Pyramid(width_px=800, height_px=600)
        level = pyramid.max_level
        image = drawing(*pyramid.size_at(level))

        whole = cut_level(image, pyramid, level, "hash")
        single = cut_one(image, pyramid, level, 1, 1)

        assert single == whole[tile_key("hash", level, 1, 1)]

    def test_a_tile_is_not_blank(self) -> None:
        pyramid = Pyramid(width_px=600, height_px=400)

        payload = cut_one(drawing(600, 400), pyramid, pyramid.max_level, 0, 0)

        with Image.open(io.BytesIO(payload)) as tile:
            assert len(tile.convert("RGB").getcolors(maxcolors=256) or []) > 1

    def test_webp_is_lossless(self) -> None:
        """A lossy tile blurs the hairline an estimator is measuring."""
        image = drawing(100, 100)

        with Image.open(io.BytesIO(encode(image))) as decoded:
            assert decoded.convert("RGB").tobytes() == image.tobytes()


class TestPreRendering:
    def test_it_produces_every_low_level_and_a_thumbnail(self) -> None:
        pyramid = Pyramid(width_px=4967, height_px=3508)
        image = drawing(2048, 1447)

        output = pre_render(image, pyramid, "hash")

        expected = sum(len(pyramid.tiles_at(level)) for level in pyramid.pre_rendered_levels)
        assert len(output) == expected + 1
        assert thumbnail_key("hash") in output

    def test_it_does_not_render_the_close_up_levels(self) -> None:
        pyramid = Pyramid(width_px=4967, height_px=3508)

        output = pre_render(drawing(2048, 1447), pyramid, "hash")

        for level in pyramid.on_demand_levels:
            assert tile_key("hash", level, 0, 0) not in output

    def test_a_thumbnail_is_small_and_still_a_drawing(self) -> None:
        key, payload = make_thumbnail(drawing(2048, 1447), "hash")

        assert key == thumbnail_key("hash")
        with Image.open(io.BytesIO(payload)) as thumbnail:
            assert max(thumbnail.size) <= 400
            assert len(thumbnail.convert("RGB").getcolors(maxcolors=4096) or []) > 1


class TestTheDescriptor:
    def test_it_tells_the_viewer_what_it_needs(self) -> None:
        pyramid = Pyramid(width_px=4967, height_px=3508)

        source = descriptor(pyramid, "hash")

        assert source["width"] == 4967
        assert source["height"] == 3508
        assert source["tileSize"] == TILE_SIZE
        assert source["tileOverlap"] == TILE_OVERLAP
        assert source["maxLevel"] == pyramid.max_level
        assert source["format"] == "webp"
