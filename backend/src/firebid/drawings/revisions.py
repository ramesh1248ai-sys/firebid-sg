"""Ordering revisions of a drawing, and reading a revision out of a filename (FR-DOC-04).

Pure functions over labels and dates; the revision service applies them to the database.

Three rules, each there because the opposite has priced a superseded drawing:

* **A scheme decides the order, not the alphabet.** `C1` is later than `T4` on a project
  that issues tender revisions before construction ones; alphabetically it is earlier.
* **When the scheme cannot order two labels, dates may; when dates cannot either, nobody
  guesses.** `compare` returns None, and the caller sends the revision to Conflict.
* **Every source must agree.** The title block, the filename and the transmittal each say
  what revision a file is. Where two of them say different things, that is a Conflict for a
  person, not a vote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "revisions.yaml"
DEFAULT_SCHEME = "default"

LETTERS = "A-Z"
NUMBERS = "#"

LABEL = re.compile(r"^(?P<prefix>[A-Z]{0,3})(?P<number>\d{0,4})$")


@dataclass(frozen=True)
class Scheme:
    name: str
    series: tuple[str, ...]
    effective_from: date

    def position(self, label: str) -> tuple[int, int] | None:
        """Where a label sits: its series' place in the scheme, then its place in the series."""
        parsed = parse(label)
        if parsed is None:
            return None
        series, number = parsed
        if series not in self.series:
            return None
        return self.series.index(series), number


def parse(label: str) -> tuple[str, int] | None:
    """A label's series and number: `T2` is (`T`, 2), `03` is (`#`, 3), `AB` is (`A-Z`, 28)."""
    text = label.strip().upper()
    match = LABEL.match(text)
    if not match or not text:
        return None
    prefix, digits = match.group("prefix"), match.group("number")
    if digits:
        return (prefix or NUMBERS), int(digits)
    # Letters only: a letter series, counted like spreadsheet columns so Z < AA.
    number = 0
    for letter in prefix:
        number = number * 26 + (ord(letter) - ord("A") + 1)
    return LETTERS, number


def compare(
    a: str,
    b: str,
    scheme: Scheme,
    *,
    a_date: date | None = None,
    b_date: date | None = None,
) -> int | None:
    """-1 if `a` is earlier, 1 if later, 0 if the same revision, None if it cannot be told."""
    if a.strip().upper() == b.strip().upper():
        return 0
    first, second = scheme.position(a), scheme.position(b)
    if first is not None and second is not None and first != second:
        return -1 if first < second else 1
    if a_date is not None and b_date is not None and a_date != b_date:
        return -1 if a_date < b_date else 1
    return None


@dataclass(frozen=True)
class Candidate:
    """A revision competing to be Current: its label, its date, and whatever identifies it."""

    key: str
    label: str
    issued: date | None = None


def latest(candidates: list[Candidate], scheme: Scheme) -> Candidate | None:
    """The one revision later than every other, or None when the order cannot be settled.

    None is an answer, not a failure: it means a person must decide which is current.
    """
    if not candidates:
        return None
    for candidate in candidates:
        beaten = False
        for other in candidates:
            if other is candidate:
                continue
            order = compare(
                candidate.label, other.label, scheme, a_date=candidate.issued, b_date=other.issued
            )
            if order is None or order < 0 or (order == 0 and other.key < candidate.key):
                beaten = True
                break
        if not beaten:
            return candidate
    return None


# --- What the filename says ------------------------------------------------------------------

FILENAME_REVISION = re.compile(
    r"^[\s_\-.]*(?:\(|\[)?\s*(?:REV(?:ISION)?[\s_\-.]*)?"
    r"(?P<label>[A-Z]{0,2}\d{1,3}|[A-Z]{1,2})\s*(?:\)|\])?(?:[\s_\-.].*)?$",
    re.I,
)


def revision_from_filename(filename: str, sheet_number: str) -> str | None:
    """The revision a filename gives after the drawing number, if it gives one.

    `FP-L05-201-R03.pdf`, `FP-L05-201_RevC.pdf`, `FP-L05-201 (C1).pdf` and `FP-L05-201[T2].dwg`
    all say something; `FP-L05-201.pdf` says nothing, which is not a disagreement.
    """
    stem = Path(filename).stem.upper()
    rest = _after_number(stem, sheet_number.upper())
    if rest is None:
        return None
    if not rest.strip(" _-."):
        return None
    match = FILENAME_REVISION.match(rest)
    if not match:
        return None
    label = match.group("label").upper()
    # A bare word that happens to be letters, such as the S in `-S` for "signed", is not
    # evidence of a revision unless the filename said REV or bracketed it.
    said_rev = re.match(r"^[\s_\-.]*[\(\[]|^[\s_\-.]*REV", rest)
    if label.isalpha() and len(label) > 1 and not said_rev:
        return None
    return label


def _after_number(stem: str, number: str) -> str | None:
    """What follows the drawing number in a filename, or None if the number is not in it.

    The number is matched with its separators ignored, so `FP_L05_201` finds `FP-L05-201`,
    and it must end at a separator: `FP-L05-2010` is a different drawing.
    """
    wanted = re.sub(r"[-_.\s]", "", number)
    positions = [
        index for index, character in enumerate(stem) if not re.match(r"[-_.\s]", character)
    ]
    squeezed = "".join(stem[index] for index in positions)
    start = squeezed.find(wanted)
    while start >= 0:
        end = positions[start + len(wanted) - 1] + 1
        rest = stem[end:]
        if not rest or re.match(r"[\s_\-.(\[]", rest):
            return rest
        start = squeezed.find(wanted, start + 1)
    return None


@dataclass(frozen=True)
class Agreement:
    revision: str | None
    conflict: str | None


def reconcile(title_block: str | None, filename: str | None, transmittal: str | None) -> Agreement:
    """Do the sources agree on the revision? A source that says nothing does not disagree."""
    said = {
        name: value.strip().upper()
        for name, value in (
            ("title block", title_block),
            ("filename", filename),
            ("transmittal", transmittal),
        )
        if value and value.strip()
    }
    values = set(said.values())
    if len(values) <= 1:
        return Agreement(next(iter(values), None), None)
    parts = ", ".join(f"the {source} says {value}" for source, value in said.items())
    return Agreement(None, f"The revision sources disagree: {parts}.")


# --- Configuration ---------------------------------------------------------------------------


@lru_cache
def schemes(path: Path = CONFIG) -> dict[str, Scheme]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    loaded = {}
    for name, entry in raw["schemes"].items():
        series = tuple(str(item) for item in entry["series"])
        if len(set(series)) != len(series):
            raise ValueError(f"revision scheme {name!r} lists a series twice")
        loaded[name] = Scheme(
            name=name, series=series, effective_from=_as_date(entry["effective_from"])
        )
    if DEFAULT_SCHEME not in loaded:
        raise ValueError("revisions.yaml needs a 'default' scheme")
    return loaded


def scheme_named(name: str | None) -> Scheme:
    known = schemes()
    return known.get(name or DEFAULT_SCHEME, known[DEFAULT_SCHEME])


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
