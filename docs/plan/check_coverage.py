"""Check that every requirement ID is assigned to a build step.

Compares FR/NFR IDs in the requirements Markdown with the IDs named in the
IMPLEMENTATION_PLAN.md step catalogue and in each prompt's "## Scope" section,
and checks that catalogue links point to existing prompt files.

Run from the repository root:  python docs/plan/check_coverage.py
Exit code 1 when anything is missing, unknown or broken.
"""
import glob
import os
import re
import sys

REQ = "docs/requirements/FireBid_SG_Requirements_v2.md"
PLAN = "docs/plan/IMPLEMENTATION_PLAN.md"
PROMPTS = "docs/plan/prompts/P*.md"

TOKEN = re.compile(r"(FR-[A-Z]+-|NFR-)(\d\d)|(?<=[\s,(–-])(\d\d)\b|(–|\bto\b)")


def expand(text: str) -> set[str]:
    """Expand references such as 'FR-QTO-01–05, 08' or 'NFR-01–07, 09'."""
    ids: set[str] = set()
    prefix, last, in_range = None, None, False
    for m in TOKEN.finditer(text):
        if m.group(1):
            prefix, n = m.group(1), int(m.group(2))
        elif m.group(3) and prefix:
            n = int(m.group(3))
        elif m.group(4):
            in_range = True
            continue
        else:
            continue
        if in_range and last is not None:
            ids.update(f"{prefix}{k:02d}" for k in range(last + 1, n + 1))
        ids.add(f"{prefix}{n:02d}")
        last, in_range = n, False
    return ids


def main() -> int:
    req = open(REQ, encoding="utf-8").read()
    all_ids = set(re.findall(r"\bFR-[A-Z]+-\d\d\b", req)) | set(re.findall(r"\bNFR-\d\d\b", req))

    plan = open(PLAN, encoding="utf-8").read()
    catalogue: set[str] = set()
    for line in plan.splitlines():
        m = re.match(r"\| \*\*(P\d-\d\d)\*\* \| [^|]+ \| ([^|]+) \|", line)
        if m:
            catalogue |= expand(m.group(2))

    scope: set[str] = set()
    for path in sorted(glob.glob(PROMPTS)):
        m = re.search(r"## Scope\n\n(.+?)\n\n## ", open(path, encoding="utf-8").read(), re.S)
        if m:
            scope |= expand(m.group(1))

    links = re.findall(r"\]\((prompts/[^)]+)\)", plan)
    broken = [p for p in links if not os.path.exists(os.path.join(os.path.dirname(PLAN), p))]

    problems = {
        "Missing from plan catalogue": sorted(all_ids - catalogue),
        "Unknown IDs in plan catalogue": sorted(catalogue - all_ids),
        "Missing from prompt Scope sections": sorted(all_ids - scope),
        "Unknown IDs in prompt Scope sections": sorted(scope - all_ids),
        "Broken prompt links": broken,
    }
    print(f"{len(all_ids)} requirement IDs; {len(links)} catalogue links")
    for label, items in problems.items():
        print(f"{label}: {items or 'none'}")
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
