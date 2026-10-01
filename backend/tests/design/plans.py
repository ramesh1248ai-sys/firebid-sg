"""Synthetic floor plans for the design tests: a base plan in grey, drawn at 1:100.

Sheet millimetres, so 10 mm on the sheet is 1 m of building.
"""

from __future__ import annotations

import pyarrow as pa

from firebid.drawings import geometry

GREY = 0xBBBBBB
RED = 0xFF0000
REGION = (0.0, 0.0, 400.0, 300.0)
DENOMINATOR = 100.0


class Plan:
    """A base plan under construction: walls, doors, names, and the extras a test needs."""

    def __init__(self) -> None:
        self.builder = geometry.Builder(geometry.Method.PDF_VECTOR)

    def wall(self, x0: float, y0: float, x1: float, y1: float, colour: int = GREY) -> None:
        self.builder.line(x0, y0, x1, y1, self.builder.group(), color=colour)

    def room(
        self,
        box: tuple[float, float, float, float],
        name: str | None = None,
        *,
        door_mm: float = 9.0,
    ) -> None:
        """A rectangular room with a doorway in the middle of its bottom wall."""
        x0, y0, x1, y1 = box
        self.wall(x0, y0, x1, y0)
        self.wall(x0, y0, x0, y1)
        self.wall(x1, y0, x1, y1)
        middle = (x0 + x1) / 2
        if door_mm:
            self.wall(x0, y1, middle - door_mm / 2, y1)
            self.wall(middle + door_mm / 2, y1, x1, y1)
        else:
            self.wall(x0, y1, x1, y1)
        if name:
            self.name(name, (x0 + x1) / 2, (y0 + y1) / 2)

    def name(self, text: str, x: float, y: float) -> None:
        box = (x - 2.0 * len(text) / 2, y - 1.5, x + 2.0 * len(text) / 2, y + 1.5)
        self.builder.text(text, box, self.builder.group(), height=3.0, color=0x000000)

    def cross(self, box: tuple[float, float, float, float]) -> None:
        """A shaft's X: corner to corner."""
        x0, y0, x1, y1 = box
        self.wall(x0, y0, x1, y1)
        self.wall(x0, y1, x1, y0)

    def grid_line(self, x: float, y0: float, y1: float) -> None:
        """A structural grid line: a dash-dot line down the sheet."""
        y = y0
        while y < y1:
            self.wall(x, y, x, min(y + 12.0, y1))
            self.wall(x, y + 15.0, x, min(y + 15.5, y1))
            y += 18.5

    def pipe(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.builder.line(x0, y0, x1, y1, self.builder.group(), color=RED)

    def table(self) -> pa.Table:
        return self.builder.table()


def building(*, door_mm: float = 9.0) -> Plan:
    """A 30 m x 20 m building: three named rooms along the top, an open floor below.

    * WARD A: 10 m x 8 m (80 m2).
    * STORE: 4 m x 3 m (12 m2).
    * LIFT: 3 m x 3 m, crossed.
    * The rest is open floor, reached through the rooms' doors.
    """
    plan = Plan()
    plan.room((50.0, 50.0, 350.0, 250.0), door_mm=0)
    plan.room((50.0, 50.0, 150.0, 130.0), "WARD A", door_mm=door_mm)
    plan.room((150.0, 50.0, 190.0, 80.0), "STORE", door_mm=door_mm)
    plan.room((320.0, 50.0, 350.0, 80.0), None, door_mm=0)
    plan.cross((320.0, 50.0, 350.0, 80.0))
    return plan
