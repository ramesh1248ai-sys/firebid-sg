"""PostgreSQL's native `box`, for bounding boxes with a GiST index (no PostGIS; see 0017)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.types import UserDefinedType


class Box(UserDefinedType[tuple[float, float, float, float]]):
    """A rectangle as (x0, y0, x1, y1). PostgreSQL stores it corner-first as (x1,y1),(x0,y0)."""

    cache_ok = True

    def get_col_spec(self, **_kwargs: Any) -> str:
        return "box"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: tuple[float, float, float, float] | None) -> str | None:
            if value is None:
                return None
            x0, y0, x1, y1 = value
            return f"(({x0},{y0}),({x1},{y1}))"

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: str | None) -> tuple[float, float, float, float] | None:
            if value is None:
                return None
            numbers = [float(part) for part in value.replace("(", "").replace(")", "").split(",")]
            x1, y1, x0, y0 = numbers
            return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

        return process
