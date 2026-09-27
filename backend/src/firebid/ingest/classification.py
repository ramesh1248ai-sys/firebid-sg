"""What kind of tender document a file is (FR-DOC-02). Rules first; a model only for the rest.

Every tender set is the same handful of things: drawings, the specification, the client's BOQ,
schedules, addenda, responses to tenderers' queries, and the conditions of contract. Most of
them announce themselves: a BOQ has ITEM, DESCRIPTION, QTY, UNIT, RATE and AMOUNT across its
top; a specification numbers its clauses; an addendum says "Addendum No. 2" on its first page.
These rules read a short digest of the file, extracted in the sandbox, and say what they found
and why. Whatever they cannot place confidently goes to the `doc_classify` model route, and
what that cannot place goes to a person. Every answer is a proposal.

The result is a type for the whole file. A PDF of drawings is a drawing set even when one page
is a schedule; per-sheet identity is the title block reader's job, not this module's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class DocType(StrEnum):
    DRAWING = "drawing"
    SPECIFICATION = "specification"
    BOQ = "boq"
    SCHEDULE = "schedule"
    ADDENDUM = "addendum"
    CLARIFICATION_RESPONSE = "clarification_response"
    CONTRACT_CONDITIONS = "contract_conditions"
    OTHER = "other"


# Below this the rules' answer is not trusted on its own, and the model is asked.
RULE_THRESHOLD = 0.8
# A second type this close to the best, and strong in its own right, makes the answer a doubt.
RIVAL_MARGIN = 0.1


@dataclass(frozen=True)
class Digest:
    """What the sandbox extracted from a file to classify it by. Plain data, no file handle."""

    kind: str
    filename: str
    # The opening text: first pages of a PDF, first paragraphs of a document, top rows of a
    # workbook's sheets joined by line.
    text: str = ""
    # Workbook sheet names, and each sheet's likely header row (its first row of text cells).
    sheet_names: tuple[str, ...] = ()
    header_rows: tuple[tuple[str, ...], ...] = ()
    pages: int = 0
    # Of the sheets this file became, how many had a title block read with confidence.
    identified_sheets: int = 0
    sheets: int = 0


@dataclass(frozen=True)
class Classification:
    doc_type: DocType
    confidence: float
    method: str  # rules | model | person
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def settled(self) -> bool:
        return self.confidence >= RULE_THRESHOLD


BOQ_COLUMNS = {
    "item": re.compile(r"^(ITEM|ITEM\s*NO\.?|REF\.?|S/?N)$", re.I),
    "description": re.compile(r"^(DESCRIPTION|PARTICULARS|DESCRIPTION OF WORKS?)$", re.I),
    "quantity": re.compile(r"^(QTY\.?|QUANTITY|QUANT\.?)$", re.I),
    "unit": re.compile(r"^(UNIT|UOM|UNITS)$", re.I),
    "rate": re.compile(r"^(RATE|UNIT\s*RATE|RATE\s*\(?S?\$\)?|UNIT\s*PRICE)$", re.I),
    "amount": re.compile(r"^(AMOUNT|TOTAL|AMOUNT\s*\(?S?\$\)?|SUM)$", re.I),
}
SCHEDULE_COLUMNS = re.compile(
    r"^(TAG|REF(ERENCE)?|MODEL|MAKE|CAPACITY|FLOW(\s*RATE)?|PRESSURE|LOCATION|SERVES|"
    r"DUTY|SIZE|TYPE|POWER|KW|QTY\.?)$",
    re.I,
)

ADDENDUM = re.compile(r"\bADDEND(UM|A)\s*(NO\.?|NUMBER)?\s*[:#]?\s*\d+", re.I)
CLARIFICATION = re.compile(
    r"TENDER(ER'?S'?)?\s+(CLARIFICATIONS?|QUER(Y|IES))|"
    r"RESPONSES?\s+TO\s+(TENDERERS'?\s+)?(QUER(Y|IES)|CLARIFICATIONS?)|"
    r"\bT\.?Q\.?\s*(NO\.?)?\s*\d+\b|CLARIFICATION\s+RESPONSES?",
    re.I,
)
CONDITIONS = re.compile(
    r"CONDITIONS\s+OF\s+(SUB-?)?CONTRACT|PREAMBLES?\b|PRELIMINARIES|\bPSSCOC\b|"
    r"SIA\s+(BUILDING\s+)?CONTRACT|LETTER\s+OF\s+AWARD|FORM\s+OF\s+TENDER",
    re.I,
)
SPECIFICATION_TITLE = re.compile(
    r"\bSPECIFICATIONS?\b|PARTICULAR\s+SPECIFICATION|TECHNICAL\s+SPECIFICATION", re.I
)
CLAUSE = re.compile(r"^\s*\d{1,2}(\.\d{1,3}){1,3}\s+\S", re.M)
BILL_TITLE = re.compile(r"BILLS?\s+OF\s+QUANTITIES|\bB\.?O\.?Q\.?\b|PRICING\s+SCHEDULE", re.I)
SCHEDULE_TITLE = re.compile(r"\bSCHEDULE\s+OF\b|EQUIPMENT\s+SCHEDULE|\bSCHEDULES?\b", re.I)


def classify(digest: Digest) -> Classification:
    """The rules' best answer, with its reasons. Low confidence means "ask the model"."""
    candidates: list[tuple[float, DocType, str]] = []
    opening = digest.text[:6000]
    name = digest.filename

    if digest.kind == "dxf":
        return Classification(DocType.DRAWING, 0.97, "rules", ("a CAD drawing file",))
    if digest.kind == "pdf" and digest.sheets:
        share = digest.identified_sheets / digest.sheets
        if share >= 0.5:
            candidates.append(
                (
                    0.9 + 0.08 * share,
                    DocType.DRAWING,
                    f"{digest.identified_sheets} of {digest.sheets} pages carry a title block",
                )
            )

    boq = _best_boq_header(digest.header_rows)
    if boq >= 4:
        candidates.append((0.8 + 0.03 * boq, DocType.BOQ, f"a header row with {boq} BOQ columns"))
    elif BILL_TITLE.search(opening) or BILL_TITLE.search(name):
        candidates.append((0.82, DocType.BOQ, "titled a bill of quantities"))

    if ADDENDUM.search(opening[:1500]) or ADDENDUM.search(name):
        candidates.append((0.9, DocType.ADDENDUM, "announces itself as an addendum"))
    if CLARIFICATION.search(opening[:2000]) or CLARIFICATION.search(name):
        candidates.append((0.88, DocType.CLARIFICATION_RESPONSE, "responds to tender queries"))
    if CONDITIONS.search(opening[:3000]) or CONDITIONS.search(name):
        candidates.append((0.86, DocType.CONTRACT_CONDITIONS, "contract conditions wording"))

    clauses = len(CLAUSE.findall(opening))
    if SPECIFICATION_TITLE.search(opening[:2000]) or SPECIFICATION_TITLE.search(name):
        confidence = 0.9 if clauses >= 5 else 0.78
        candidates.append(
            (confidence, DocType.SPECIFICATION, f"titled a specification, {clauses} clauses")
        )
    elif clauses >= 15 and digest.kind in ("docx", "pdf"):
        candidates.append((0.75, DocType.SPECIFICATION, f"{clauses} numbered clauses"))

    schedule_columns = _best_schedule_header(digest.header_rows)
    if schedule_columns >= 3 and boq < 4:
        candidates.append(
            (0.84, DocType.SCHEDULE, f"a header row with {schedule_columns} schedule columns")
        )
    elif SCHEDULE_TITLE.search(opening[:1000]) or SCHEDULE_TITLE.search(name):
        candidates.append((0.72, DocType.SCHEDULE, "titled a schedule"))

    if not candidates:
        return Classification(DocType.OTHER, 0.3, "rules", ("no rule recognised it",))

    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    best_confidence, best_type, why = candidates[0]
    reasons = [why]
    # Two different strong answers is a reason for doubt, not a tie to break silently: an
    # addendum that reissues the BOQ is both, and a person or the model should say which.
    rivals = [
        candidate
        for candidate in candidates[1:]
        if candidate[1] is not best_type
        and candidate[0] >= RULE_THRESHOLD
        and candidate[0] >= best_confidence - RIVAL_MARGIN
    ]
    if rivals:
        reasons.append(f"but also looks like {rivals[0][1]}: {rivals[0][2]}")
        best_confidence = min(best_confidence, RULE_THRESHOLD - 0.05)
    return Classification(best_type, round(best_confidence, 3), "rules", tuple(reasons))


def _best_boq_header(rows: Sequence[Sequence[str]]) -> int:
    best = 0
    for row in rows:
        cells = [cell.strip() for cell in row if cell and cell.strip()]
        found = {
            column
            for column, pattern in BOQ_COLUMNS.items()
            if any(pattern.match(cell) for cell in cells)
        }
        best = max(best, len(found))
    return best


def _best_schedule_header(rows: Sequence[Sequence[str]]) -> int:
    best = 0
    for row in rows:
        cells = [cell.strip() for cell in row if cell and cell.strip()]
        best = max(best, sum(1 for cell in cells if SCHEDULE_COLUMNS.match(cell)))
    return best
