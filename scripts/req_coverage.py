"""Requirement coverage: which FR/NFR IDs have at least one test tagged with them.

Sources
- Requirements: docs/requirements/FireBid_SG_Requirements_v2.md (IDs, priority, phase).
- Python tests: ``@pytest.mark.req("FR-QTO-08", ...)`` on tests or ``pytestmark`` (via ``ast``).
- Frontend tests: a ``// req: FR-REV-04, NFR-10`` comment above an ``it(``/``test(`` call.

NFRs have no phase column in the requirements; their phase is the earliest phase of a build
step that names them in docs/plan/IMPLEMENTATION_PLAN.md, with P0 counted as P1 (release).
``--phase P2`` is cumulative: everything due by the end of Phase 2.

Usage: python scripts/req_coverage.py [--phase P1] [--ids FR-QTO-08,NFR-06]
                                      [--require IDS | --strict] [--output FILE]
Exit codes: 0 ok; 1 required IDs uncovered; 2 tests tag unknown IDs.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "docs/requirements/FireBid_SG_Requirements_v2.md"
PLAN = ROOT / "docs/plan/IMPLEMENTATION_PLAN.md"
PY_TESTS = ROOT / "backend/tests"
TS_TEST_GLOBS = ("frontend/src/**/*.test.ts", "frontend/src/**/*.test.tsx", "frontend/e2e/**/*.ts")

EN_DASH = chr(0x2013)  # the plan writes ID ranges with an en dash
ID_PATTERN = r"(?:FR-[A-Z]+-\d{2}|NFR-\d{2})"
FR_ROW = re.compile(r"^\| (FR-[A-Z]+-\d{2}) \| .* \| ([MSCW]) \| (P\d) \|\s*$")
NFR_ROW = re.compile(r"^\| (NFR-\d{2}) \| ([^|]+) \|")
TS_TAG = re.compile(r"//\s*req:\s*(.+)$")
TS_TEST = re.compile(r"""\b(?:it|test)\s*\(\s*(["'`])(.+?)\1""")


@dataclass
class Requirement:
    id: str
    priority: str
    phase: str
    tests: list[str] = field(default_factory=list)


def phase_number(phase: str) -> int:
    return int(phase.removeprefix("P"))


def parse_requirements(requirements_md: str, plan_md: str) -> dict[str, Requirement]:
    reqs: dict[str, Requirement] = {}
    nfr_ids: list[str] = []
    for line in requirements_md.splitlines():
        if m := FR_ROW.match(line):
            reqs[m.group(1)] = Requirement(m.group(1), m.group(2), m.group(3))
        elif m := NFR_ROW.match(line):
            nfr_ids.append(m.group(1))
    nfr_phase = _nfr_phases_from_plan(plan_md)
    for nfr in nfr_ids:
        reqs[nfr] = Requirement(nfr, "-", nfr_phase.get(nfr, "P1"))
    return reqs


def _nfr_phases_from_plan(plan_md: str) -> dict[str, str]:
    """Earliest plan phase naming each NFR; ranges (NFR-01 to 07) expand to every ID."""
    phases: dict[str, str] = {}
    for line in plan_md.splitlines():
        m = re.match(r"^\| \*\*P(\d)-\d{2}\*\* \| [^|]+ \| ([^|]+) \|", line)
        if not m:
            continue
        step_phase = max(1, int(m.group(1)))  # P0 foundations release with P1
        for nfr in _expand_nfr_refs(m.group(2)):
            current = phases.get(nfr)
            if current is None or step_phase < phase_number(current):
                phases[nfr] = f"P{step_phase}"
    return phases


def _expand_nfr_refs(text: str) -> set[str]:
    ids: set[str] = set()
    for segment in re.findall(rf"NFR-[\d,\s{EN_DASH}-]+", text):
        numbers = segment.removeprefix("NFR-")
        for part in numbers.split(","):
            part = part.strip()
            if not part:
                continue
            bounds = re.split(rf"[{EN_DASH}-]", part)
            if len(bounds) == 2 and all(b.strip().isdigit() for b in bounds):
                lo, hi = (int(b) for b in bounds)
                ids.update(f"NFR-{n:02d}" for n in range(lo, hi + 1))
            elif part.isdigit():
                ids.add(f"NFR-{int(part):02d}")
    return ids


def _req_ids_in_marker(node: ast.expr) -> list[str]:
    """IDs from a `pytest.mark.req(...)` call expression, else []."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return []
    if node.func.attr != "req":
        return []
    return [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def python_tags(tests_dir: Path) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    for path in sorted(tests_dir.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT).as_posix()
        module_ids: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
            ):
                values = node.value.elts if isinstance(node.value, ast.List) else [node.value]
                for value in values:
                    module_ids += _req_ids_in_marker(value)
        for item in ast.walk(tree):
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                ids = [i for d in item.decorator_list for i in _req_ids_in_marker(d)]
                is_test = item.name.startswith(("test", "Test"))
                for req_id in ids + (module_ids if is_test else []):
                    tags.setdefault(req_id, []).append(f"{rel}::{item.name}")
    return tags


def typescript_tags(root: Path) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    for pattern in TS_TEST_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            pending: list[str] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if m := TS_TAG.search(line):
                    pending += re.findall(ID_PATTERN, m.group(1))
                elif pending and (t := TS_TEST.search(line)):
                    for req_id in pending:
                        tags.setdefault(req_id, []).append(f"{rel}::{t.group(2)}")
                    pending = []
    return tags


def select(
    reqs: dict[str, Requirement], phase: str | None, ids: list[str] | None
) -> list[Requirement]:
    chosen = list(reqs.values())
    if phase:
        limit = phase_number(phase)
        chosen = [r for r in chosen if phase_number(r.phase) <= limit]
    if ids:
        wanted = set(ids)
        chosen = [r for r in chosen if r.id in wanted]
    return chosen


def render_markdown(rows: list[Requirement]) -> str:
    covered = sum(1 for r in rows if r.tests)
    lines = [
        f"Requirement coverage: {covered}/{len(rows)} covered",
        "",
        "| ID | Priority | Phase | Tests |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r.id} | {r.priority} | {r.phase} | {'<br>'.join(r.tests) or '-'} |")
    return "\n".join(lines) + "\n"


def split_ids(value: str | None) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()] if value else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--phase", help="cumulative phase filter, e.g. P1")
    parser.add_argument("--ids", help="comma-separated requirement IDs to show")
    parser.add_argument("--require", help="comma-separated IDs that must be covered")
    parser.add_argument("--strict", action="store_true", help="every shown ID must be covered")
    parser.add_argument("--output", type=Path, help="also write the table to this file")
    args = parser.parse_args(argv)

    reqs = parse_requirements(
        REQUIREMENTS.read_text(encoding="utf-8"), PLAN.read_text(encoding="utf-8")
    )
    all_tags: dict[str, list[str]] = {}
    for source in (python_tags(PY_TESTS), typescript_tags(ROOT)):
        for req_id, tests in source.items():
            all_tags.setdefault(req_id, []).extend(tests)

    unknown = sorted(set(all_tags) - set(reqs))
    for req_id, tests in all_tags.items():
        if req_id in reqs:
            reqs[req_id].tests = sorted(tests)

    rows = select(reqs, args.phase, split_ids(args.ids) or None)
    report = render_markdown(rows)
    sys.stdout.write(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8", newline="\n")

    if unknown:
        sys.stderr.write(f"Tests tag IDs not in the requirements: {', '.join(unknown)}\n")
        return 2
    required = split_ids(args.require)
    if args.strict:
        required += [r.id for r in rows]
    missing = sorted({i for i in required if not (i in reqs and reqs[i].tests)})
    if missing:
        sys.stderr.write(f"Required IDs without tests: {', '.join(missing)}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
