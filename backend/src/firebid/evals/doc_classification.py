"""The `doc_classification` suite: how well title blocks are read (FR-DOC-02).

Two halves:

* **Fixtures.** One synthetic tender per way a drawing arrives, each holding the same varied
  set of sheets: CAD (DXF), a vector PDF with a text layer, a vector PDF whose text is drawn
  as outlines (so it must be read by OCR), and a low-resolution scan. Numbers, levels and
  revisions vary with the seed, and each tender holds superseded revisions beside current
  ones, so the suite also measures whether the right one is chosen as Current.
* **The predictor.** The platform's own reading, with nothing mocked: every file is opened
  through the sandbox (a real golden set holds client files, guardrail 9), its title blocks
  read deterministically with OCR where needed, and Current decided by the revision scheme.
  The model fallback is not called: this suite measures what the deterministic reader
  achieves on its own, which is the number that decides how often a model or a person is
  needed.

A real golden set is used instead when `eval/truth/doc_classification/` exists, with its
files under `eval/files/doc_classification/<tender id>/`.
"""

from __future__ import annotations

import random
import shutil
from dataclasses import dataclass
from pathlib import Path

from firebid.drawings.revisions import Candidate, latest, scheme_named
from firebid.drawings.title_block import CANDIDATES, DEFAULT_THRESHOLD, Box, Field, Span, read
from firebid.evals import synthetic
from firebid.evals.prediction import SheetPrediction, TenderPrediction
from firebid.evals.schema import GoldenSet, InputClass, RevisionStatus, SheetTruth, TenderTruth

SUITE = "doc_classification"

# How each form of the same set is written, and the input class it is reported under.
FORMS: dict[str, InputClass] = {
    "cad": InputClass.DWG,
    "pdf-text": InputClass.VECTOR_PDF,
    "pdf-outlined": InputClass.VECTOR_PDF,
    "scan-150dpi": InputClass.RASTER,
}
LEVELS = ("B1", "L01", "L02", "L03", "L05", "L08", "RF")
TITLES = (
    "FIRE SPRINKLER LAYOUT",
    "HOSE REEL AND RISING MAIN LAYOUT",
    "WET RISER SCHEMATIC",
    "SPRINKLER PIPING DETAILS",
)


@dataclass(frozen=True)
class Suite:
    golden_set: GoldenSet
    files: dict[str, list[Path]]


def generate(out_dir: Path, seed: int = 1, sheets: int = 8) -> Suite:
    """The synthetic suite: every form of one seeded set of drawings."""
    rng = random.Random(seed)  # noqa: S311  # reproducible fixtures, not cryptography
    drawings = _plan(rng, sheets)
    tenders: list[TenderTruth] = []
    files: dict[str, list[Path]] = {}
    for form, input_class in FORMS.items():
        tender_id = f"SYNTH-DOC-{form.upper()}"
        folder = out_dir / tender_id
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        truths: list[SheetTruth] = []
        written: list[Path] = []
        for number, revision, title, status in drawings:
            document, truth = synthetic.general_arrangement(sheet_number=number, revision=revision)
            _retitle(document, title)
            path = _write(document, folder / f"{number}-{revision}", form)
            written.append(path)
            truths.append(truth.model_copy(update={"status": status, "input_class": input_class}))
        tenders.append(
            TenderTruth(
                tender_id=tender_id,
                consultant="Synthetic Consultants",
                input_class=input_class,
                sheets=tuple(truths),
            )
        )
        files[tender_id] = written
    return Suite(GoldenSet(name=SUITE, tenders=tuple(tenders)), files)


def _plan(rng: random.Random, count: int) -> list[tuple[str, str, str, RevisionStatus]]:
    """Distinct drawings, some issued twice so a superseded revision sits beside the current."""
    planned: list[tuple[str, str, str, RevisionStatus]] = []
    used: set[str] = set()
    while len(used) < count:
        level = rng.choice(LEVELS)
        number = f"FP-{level}-{rng.randint(100, 399)}"
        if number in used:
            continue
        used.add(number)
        style = rng.choice(("R", "T", "C", "letter"))
        latest_issue = rng.randint(2, 6)
        label = (
            chr(ord("A") + latest_issue - 1)
            if style == "letter"
            else f"{style}{latest_issue:02d}"
            if style == "R"
            else f"{style}{latest_issue}"
        )
        title = rng.choice(TITLES)
        planned.append((number, label, title, RevisionStatus.CURRENT))
        if rng.random() < 0.4:
            earlier = (
                chr(ord("A") + latest_issue - 2)
                if style == "letter"
                else f"{style}{latest_issue - 1:02d}"
                if style == "R"
                else f"{style}{latest_issue - 1}"
            )
            planned.append((number, earlier, title, RevisionStatus.SUPERSEDED))
    return planned


def _retitle(document: object, title: str) -> None:
    for entity in document.modelspace():  # type: ignore[attr-defined]
        if entity.dxftype() == "TEXT" and entity.dxf.text == "FIRE SPRINKLER LAYOUT":
            entity.dxf.text = title


def _write(document: object, stem: Path, form: str) -> Path:
    from PIL import Image

    if form == "cad":
        return synthetic.write_dxf(document, stem.with_suffix(".dxf"))  # type: ignore[arg-type]
    if form == "pdf-text":
        return synthetic.write_pdf(document, stem.with_suffix(".pdf"), live_text=True)  # type: ignore[arg-type]
    if form == "pdf-outlined":
        return synthetic.write_pdf(document, stem.with_suffix(".pdf"))  # type: ignore[arg-type]
    png = synthetic.write_raster(document, stem.with_suffix(".png"), dpi=150)  # type: ignore[arg-type]
    pdf = stem.with_suffix(".pdf")
    with Image.open(png) as image:
        image.convert("RGB").save(pdf, "PDF", resolution=150.0)
    png.unlink()
    return pdf


def load_golden(root: Path) -> Suite | None:
    """A real golden set, if one has been imported for this suite."""
    truth_dir = root / "truth" / SUITE
    if not truth_dir.exists():
        return None
    tenders = tuple(
        TenderTruth.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(truth_dir.glob("*.json"))
    )
    files = {
        tender.tender_id: sorted((root / "files" / SUITE / tender.tender_id).glob("*"))
        for tender in tenders
    }
    return Suite(GoldenSet(name=SUITE, tenders=tenders), files)


# --- The predictor ---------------------------------------------------------------------------


class TitleBlockPredictor:
    """The platform's deterministic title block reading, scored as a predictor."""

    name = "title-block-reader"
    version = "p1-02"

    def __init__(self, files: dict[str, list[Path]], *, use_ocr: bool = True) -> None:
        self._files = files
        self._use_ocr = use_ocr

    def predict(self, truth: TenderTruth) -> TenderPrediction:
        read_sheets: list[tuple[str, str]] = []
        for path in self._files.get(truth.tender_id, []):
            read_sheets.extend(self._read_file(path))

        scheme = scheme_named(None)
        by_number: dict[str, list[str]] = {}
        for number, revision in read_sheets:
            by_number.setdefault(number, []).append(revision)
        predictions = []
        for number, revisions in by_number.items():
            winner = latest(
                [Candidate(str(index), label) for index, label in enumerate(set(revisions))],
                scheme,
            )
            for revision in sorted(set(revisions)):
                current = winner is not None and winner.label == revision
                predictions.append(
                    SheetPrediction(
                        sheet_number=number,
                        revision=revision,
                        status=RevisionStatus.CURRENT if current else RevisionStatus.SUPERSEDED,
                    )
                )
        return TenderPrediction(tender_id=truth.tender_id, sheets=tuple(predictions))

    def _read_file(self, path: Path) -> list[tuple[str, str]]:
        from firebid.parsing import pdf as pdf_parsing
        from firebid.parsing import text as text_parsing
        from firebid.sandbox.runner import SandboxFailure, run_sandboxed

        payload = path.read_bytes()
        found: list[tuple[str, str]] = []
        try:
            if path.suffix.lower() == ".dxf":
                pages = [run_sandboxed(text_parsing.dxf_text, payload, None)]
                indices = [0]
            else:
                facts = run_sandboxed(pdf_parsing.inspect_pdf, payload)
                indices = [page["index"] for page in facts]
                pages = [run_sandboxed(text_parsing.pdf_text, payload, index) for index in indices]
        except SandboxFailure:
            return found

        for index, data in zip(indices, pages, strict=True):
            reading = read([Span(*span) for span in data["spans"]], Box(*data["page"]))
            if reading.needs_help(DEFAULT_THRESHOLD) and self._use_ocr and path.suffix == ".pdf":
                for region in CANDIDATES:
                    try:
                        ocr = run_sandboxed(
                            text_parsing.ocr_text,
                            payload,
                            "pdf",
                            index,
                            [region.x0, region.y0, region.x1, region.y1],
                        )
                    except SandboxFailure:
                        break
                    candidate = read([Span(*span) for span in ocr["spans"]], Box(*ocr["page"]))
                    if candidate.confidence >= reading.confidence:
                        reading = candidate
                    if not reading.needs_help(DEFAULT_THRESHOLD):
                        break
            number, revision = reading.value(Field.SHEET_NUMBER), reading.value(Field.REVISION)
            if number and revision:
                found.append((number, revision))
        return found
