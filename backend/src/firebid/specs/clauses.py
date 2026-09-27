"""A specification as a tree of numbered clauses (P1-06 build item 1).

Specifications are numbered: `2`, `2.1`, `2.1.3`. The number is what a citation points at
and what a person looks up, so it is the spine of the tree.

* **DOCX:** a paragraph in a heading style starts a clause (its number is at the start of
  the heading text); so does a body paragraph that starts with a clause number. Unnumbered
  paragraphs belong to the clause before them. The anchor is the paragraph index.
* **PDF:** lines are read with their font; a line that starts with a clause number starts a
  clause, and a bold or larger line is its heading. Other lines continue the clause before.
  The anchor is the page and line.

A clause's parent is the nearest earlier clause whose number is a prefix of its own.

Pure: file bytes in, clauses out. Runs in the sandbox pool, as it opens the tender file.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

NUMBER = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+(\S.*)$")


@dataclass
class Clause:
    number: str
    heading: str
    text: str
    ordinal: int
    anchor: dict[str, int] = field(default_factory=dict)

    @property
    def level(self) -> int:
        return self.number.count(".") + 1

    @property
    def parent(self) -> str | None:
        return self.number.rsplit(".", 1)[0] if "." in self.number else None

    def as_json(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "heading": self.heading,
            "text": self.text,
            "ordinal": self.ordinal,
            "level": self.level,
            "parent": self.parent,
            "anchor": self.anchor,
        }


def parse(payload: bytes, kind: str) -> list[Clause]:
    if kind == "docx":
        return parse_docx(payload)
    if kind == "pdf":
        return parse_pdf(payload)
    raise ValueError(f"a {kind} is not a specification format this reads")


def parse_docx(payload: bytes) -> list[Clause]:
    import docx

    document = docx.Document(io.BytesIO(payload))
    clauses: list[Clause] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = " ".join(paragraph.text.split())
        if not text:
            continue
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        heading = style.lower().startswith("heading")
        numbered = NUMBER.match(text)
        if numbered and (heading or _looks_like_clause(numbered.group(1), clauses)):
            number, rest = numbered.group(1), numbered.group(2)
            clauses.append(
                Clause(
                    number=number,
                    heading=rest if heading else "",
                    text="" if heading else rest,
                    ordinal=len(clauses),
                    anchor={"paragraph": index},
                )
            )
        elif clauses:
            last = clauses[-1]
            last.text = f"{last.text} {text}".strip()
    return clauses


def _looks_like_clause(number: str, clauses: list[Clause]) -> bool:
    """A body paragraph starting with a number is a clause if the number fits the tree:
    `2.1.3` after `2.1.2`, or `2.1.1` under `2.1`. A quantity ("50 mm pipes ...") is not."""
    if "." not in number:
        return False
    if not clauses:
        return True
    parent = number.rsplit(".", 1)[0]
    return any(c.number == parent or c.number.rsplit(".", 1)[0] == parent for c in clauses)


def parse_pdf(payload: bytes) -> list[Clause]:
    import pdfplumber

    clauses: list[Clause] = []
    with pdfplumber.open(io.BytesIO(payload)) as document:
        body_size = _body_size(document)
        for page_number, page in enumerate(document.pages, start=1):
            for line_number, line in enumerate(page.extract_text_lines(), start=1):
                text = " ".join(str(line["text"]).split())
                if not text:
                    continue
                chars = line.get("chars") or []
                size = max((float(c.get("size", 0)) for c in chars), default=body_size)
                bold = any("bold" in str(c.get("fontname", "")).lower() for c in chars)
                heading = bold or size > body_size + 0.5
                numbered = NUMBER.match(text)
                if numbered and (heading or _looks_like_clause(numbered.group(1), clauses)):
                    number, rest = numbered.group(1), numbered.group(2)
                    clauses.append(
                        Clause(
                            number=number,
                            heading=rest if heading else "",
                            text="" if heading else rest,
                            ordinal=len(clauses),
                            anchor={"page": page_number, "line": line_number},
                        )
                    )
                elif clauses:
                    last = clauses[-1]
                    last.text = f"{last.text} {text}".strip()
    return clauses


def _body_size(document: Any) -> float:
    """The commonest character size: the body text's, against which headings stand out."""
    sizes: dict[float, int] = {}
    for page in document.pages[:5]:
        for char in page.chars:
            size = round(float(char.get("size", 0)), 1)
            sizes[size] = sizes.get(size, 0) + 1
    return max(sizes, key=lambda size: sizes[size]) if sizes else 10.0


def find(clauses: list[Clause], number: str) -> Clause | None:
    wanted = number.strip().rstrip(".")
    return next((clause for clause in clauses if clause.number == wanted), None)


def full_text(clause: Clause) -> str:
    return f"{clause.heading} {clause.text}".strip()


def parse_json(payload: bytes, kind: str) -> list[dict[str, Any]]:
    """`parse`, as plain data: what the sandbox hands back."""
    return [clause.as_json() for clause in parse(payload, kind)]
