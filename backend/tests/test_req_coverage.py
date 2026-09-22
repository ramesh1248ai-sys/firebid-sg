"""Tests for scripts/req_coverage.py, the requirement-coverage report."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("req_coverage", ROOT / "scripts/req_coverage.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["req_coverage"] = module
    spec.loader.exec_module(module)
    return module


rc = load_script()

REQS_MD = """
| FR-QTO-08 | Prevent double counting. | M | P1 |
| FR-RSK-07 | Contract terms. | S | P4 |
| NFR-01 | Performance | Fast. |
| NFR-13 | Interoperability | XLSX. |
"""
EN_DASH = chr(0x2013)  # the plan writes ranges with an en dash
PLAN_MD = f"""
| **P0-02** | Domain | NFR-07, NFR-09 | P0-01 | M | x |
| **P1-11** | Hardening | NFR-01{EN_DASH}07, 09, 14, 15; P1 exit | All | L | x |
| **P3-02** | BIM | FR-CRD-01{EN_DASH}05; NFR-13 (IFC) | P2 | XL | x |
"""


def test_parses_fr_priority_and_phase_and_nfr_phase_from_plan() -> None:
    reqs = rc.parse_requirements(REQS_MD, PLAN_MD)
    assert (reqs["FR-QTO-08"].priority, reqs["FR-QTO-08"].phase) == ("M", "P1")
    assert reqs["FR-RSK-07"].phase == "P4"
    assert reqs["NFR-01"].phase == "P1"  # from the NFR-01..07 range in P1-11
    assert reqs["NFR-13"].phase == "P3"


def test_nfr_ranges_expand() -> None:
    assert rc._expand_nfr_refs(f"NFR-01{EN_DASH}03, 09; NFR-14") == {
        "NFR-01",
        "NFR-02",
        "NFR-03",
        "NFR-09",
        "NFR-14",
    }


def test_phase_filter_is_cumulative() -> None:
    reqs = rc.parse_requirements(REQS_MD, PLAN_MD)
    shown = {r.id for r in rc.select(reqs, "P1", None)}
    assert shown == {"FR-QTO-08", "NFR-01"}


def test_python_tags_include_decorators_and_module_pytestmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "import pytest\n"
        "pytestmark = [pytest.mark.req('NFR-06')]\n"
        "@pytest.mark.req('FR-QTO-08', 'FR-QTO-09')\n"
        "def test_one():\n    pass\n"
        "def helper():\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rc, "ROOT", tmp_path)
    tags = rc.python_tags(tests)
    assert tags["FR-QTO-08"] == ["tests/test_sample.py::test_one"]
    assert tags["FR-QTO-09"] == ["tests/test_sample.py::test_one"]
    assert tags["NFR-06"] == ["tests/test_sample.py::test_one"]  # module mark, tests only


def test_typescript_tags_attach_to_the_next_test(tmp_path: Path) -> None:
    src = tmp_path / "frontend/src"
    src.mkdir(parents=True)
    (src / "a.test.tsx").write_text(
        'describe("x", () => {\n'
        "  // req: FR-REV-04, NFR-10\n"
        '  it("blocks the gate", () => {});\n'
        '  it("untagged", () => {});\n'
        "});\n",
        encoding="utf-8",
    )
    tags = rc.typescript_tags(tmp_path)
    assert tags == {
        "FR-REV-04": ["frontend/src/a.test.tsx::blocks the gate"],
        "NFR-10": ["frontend/src/a.test.tsx::blocks the gate"],
    }


def test_real_repository_lists_all_ids_and_the_example_tag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert rc.main([]) == 0
    out = capsys.readouterr().out
    fr = {line.split(" | ")[0][2:] for line in out.splitlines() if line.startswith("| FR-")}
    nfr = {line.split(" | ")[0][2:] for line in out.splitlines() if line.startswith("| NFR-")}
    assert len(fr) == 104
    assert len(nfr) == 15
    nfr14 = next(line for line in out.splitlines() if line.startswith("| NFR-14 |"))
    assert "test_system_endpoints.py::test_every_response_carries_a_request_id" in nfr14


def test_require_fails_for_uncovered_ids() -> None:
    assert rc.main(["--require", "FR-RSK-07"]) == 1
    assert rc.main(["--require", "NFR-14"]) == 0
