"""What a non-drawing document is and which revision it is at (FR-DOC-03).

Revisions of one document must compete for Current, and different documents must not, so each
document needs an identity that survives re-issue. The document number is the best one, when
the document prints it; otherwise its title, normalised so that "PARTICULAR SPECIFICATION
(REV 2)" and "Particular Specification - Rev 3" are the same document; otherwise the file name
with its revision stripped.

Deterministic and conservative: an identity or revision this cannot find is left None, and the
document waits for a person rather than being filed under a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DOC_NUMBER = re.compile(
    r"\b(?:DOC(?:UMENT)?|SPEC(?:IFICATION)?|REF(?:ERENCE)?)\s*(?:NO|NUMBER|REF)\.?\s*[:#]?\s*"
    r"(?P<number>[A-Z0-9](?:[A-Z0-9]|[-/.](?=[A-Z0-9])){2,40})",
    re.I,
)
REVISION_IN_TEXT = re.compile(
    r"\bREV(?:ISION)?\.?\s*(?:NO\.?)?\s*[:#]?\s*(?P<label>[A-Z]{0,2}\d{1,3}|[A-Z])\b", re.I
)
REVISION_IN_NAME = re.compile(
    r"(?:^|[\s_\-.(\[])REV(?:ISION)?[\s_\-.]*(?P<label>[A-Z]{0,2}\d{1,3}|[A-Z])(?=$|[\s_\-.)\]])",
    re.I,
)
# Words that say which issue a title is, not which document: removed before comparing titles.
ISSUE_WORDS = re.compile(
    r"\(?\b(REV(ISION)?\.?\s*(NO\.?)?\s*[:#]?\s*([A-Z]{0,2}\d{1,3}|[A-Z])|DRAFT|FINAL|"
    r"ISSUED\s+FOR\s+TENDER|FOR\s+TENDER|TENDER\s+ISSUE|AMENDED|REVISED|ADDENDUM\s*(NO\.?)?\s*\d*)"
    r"\b\)?",
    re.I,
)
DATE_WORDS = re.compile(
    r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{1,2}\s+[A-Z]{3,9}\s+\d{4}\b|\b[A-Z]{3,9}\s+\d{4}\b",
    re.I,
)
# Opening lines that are letterhead or boilerplate, never a document's title.
NOT_A_TITLE = re.compile(
    r"^(PAGE\s+\d+|CONFIDENTIAL|PTE\.?\s+LTD|TABLE\s+OF\s+CONTENTS|CONTENTS|\d+)$|PTE\.?\s+LTD",
    re.I,
)


@dataclass(frozen=True)
class Identity:
    key: str | None
    title: str | None
    revision: str | None
    revision_from: str | None  # "text" | "filename" | None


def identify(filename: str, text: str) -> Identity:
    opening = text[:3000]
    title = _title(opening)
    number = DOC_NUMBER.search(opening)
    in_text = REVISION_IN_TEXT.search(opening[:1500])
    in_name = REVISION_IN_NAME.search(Path(filename).stem)

    if number:
        key: str | None = number.group("number").upper().rstrip(".-/")
    elif title:
        key = normalise_title(title)
    else:
        key = normalise_title(REVISION_IN_NAME.sub(" ", Path(filename).stem)) or None

    if in_text:
        revision, source = in_text.group("label").upper(), "text"
    elif in_name:
        revision, source = in_name.group("label").upper(), "filename"
    else:
        revision, source = None, None
    return Identity(key=key or None, title=title, revision=revision, revision_from=source)


def filename_revision(filename: str) -> str | None:
    found = REVISION_IN_NAME.search(Path(filename).stem)
    return found.group("label").upper() if found else None


def text_revision(text: str) -> str | None:
    found = REVISION_IN_TEXT.search(text[:1500])
    return found.group("label").upper() if found else None


def _title(opening: str) -> str | None:
    for line in opening.splitlines():
        cleaned = " ".join(line.split())
        letters = sum(character.isalpha() for character in cleaned)
        if letters < 8 or NOT_A_TITLE.search(cleaned):
            continue
        return cleaned[:300]
    return None


def normalise_title(title: str) -> str:
    text = ISSUE_WORDS.sub(" ", title)
    text = DATE_WORDS.sub(" ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    return " ".join(text.split())[:200]
