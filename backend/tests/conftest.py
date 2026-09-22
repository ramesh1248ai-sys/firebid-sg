"""Shared test configuration.

Every test that verifies a requirement carries ``@pytest.mark.req("<ID>")``. The ID format is
checked here, so a typo fails collection instead of silently leaving a requirement uncovered.
"""

import re

import pytest

REQ_ID = re.compile(r"^(FR-[A-Z]+-\d{2}|NFR-\d{2})$")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for marker in item.iter_markers(name="req"):
            ids = marker.args
            if not ids or not all(isinstance(i, str) and REQ_ID.match(i) for i in ids):
                raise pytest.UsageError(
                    f"{item.nodeid}: @pytest.mark.req needs IDs like 'FR-QTO-08' or 'NFR-06', "
                    f"got {ids!r}"
                )
