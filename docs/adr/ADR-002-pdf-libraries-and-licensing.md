# ADR-002: PDF libraries and licensing

- **Status:** Proposed. Benchmarked on synthetic sheets (P1-03); licence recommendation below, awaiting the Product Owner, and to be re-measured on real sheets
- **Date:** 2026-09-22
- **Deciders:** Tech Lead; Product Owner (licence spend)
- **Requirements:** FR-DOC-01, FR-VIS-01, FR-VIS-06; NFR-01; risk R1

## Context

Most tender drawings arrive as vector PDFs. Takeoff needs every path segment, its transform, stroke colour and width, and the text with positions, from dense A0/A1 sheets, within the 300-sheet ingest target (NFR-01). The library's licence must allow use in a commercial, hosted product.

## Options

1. **`pypdfium2`** (Apache-2.0 / BSD, Google's PDFium engine). Fast rendering, and its page-object API exposes paths, segments, matrices and text. Lower-level API.
2. **`pdfplumber` / `pdfminer.six`** (MIT). Convenient API, but pure Python: dense sheets can take minutes each.
3. **PyMuPDF** (AGPL or a commercial licence). Very fast and convenient; AGPL is incompatible with a closed hosted product unless a commercial licence is bought.

## Recommendation

Use **`pypdfium2`** for rendering and for vector and text extraction through the PDFium page-object API, with **`pdfplumber` as fallback** for any page PDFium cannot parse (the method is recorded on every extracted item).

Step P1-03 starts with a two-day benchmark on at least five real dense sheets, measuring time, peak memory and completeness for all three libraries (PyMuPDF for evaluation only). It records a table here and recommends whether a PyMuPDF commercial licence is worth buying. The Product Owner decides.

## Benchmark (P1-03, 2026-09-28)

**On synthetic sheets, not real ones.** The golden set (decision D3) had no sheets yet, so the
product owner approved running the benchmark on generated dense sheets for now. These are
five fire protection plans drawn at 1:100: three A1 and two A0. Each has an architectural
background, a sprinkler grid, branches and mains, a structural grid with bubbles, hatches and
annotation: 6,700 to 13,500 CAD entities, 5,000 to 10,000 line runs and 100 to 250 text
strings per sheet. **Real dense sheets are heavier still**, typically several times the entity
count, with xrefs and embedded images. So the recommendation below must be re-measured on
at least five real sheets before it is final:
`python -m firebid.evals.pdf_benchmark run --sheets <dir>` in the sandbox image.

Method (`backend/src/firebid/evals/pdf_benchmark.py`):
- Each engine extracts each sheet in its own process in the sandbox image (Linux, Python
  3.12), so time and peak memory are its own.
- Completeness is measured against what the sheet is known to contain:
  - **line work** is the share of known runs of 20 mm and over recovered within 0.5 mm at
    each end;
  - **text** is the share of known strings recovered whole.
- PyMuPDF was installed into a throwaway directory for the run and is not a project
  dependency.

| Sheet | Engine | Time | Peak memory | Line work | Text |
|---|---|---|---|---|---|
| A1 × 3 | **pypdfium2** (platform) | 0.40–0.81 s | 152–156 MB | 100% | 100% |
| A1 × 3 | pdfplumber (fallback) | 1.35–1.80 s | 176–180 MB | 100% | 99.0–99.2% |
| A1 × 3 | PyMuPDF | 0.25–0.43 s | 111 MB | 100% | 100% |
| A0 × 2 | **pypdfium2** (platform) | 0.73–0.77 s | 186–187 MB | 100% | 100% |
| A0 × 2 | pdfplumber (fallback) | 2.42–2.52 s | 206–211 MB | 100% | 99.6% |
| A0 × 2 | PyMuPDF | 0.34–0.35 s | 125–126 MB | 100% | 100% |

**What the benchmark changed in the code:**
- pypdfium2 first scored 95–98% on text. That was the platform's grouping, not PDFium: spans
  were built from the text page's rectangles, which merge separate text objects sharing a
  line. A head label and the branch label beside it came out as one string
  (`SP0300 DN4`). Spans are now read per text object, and text recall is 100%.
- The first line-work figures (99.2–99.8%) were a fault in the reference: walls drawn partly
  off the page. With them removed, every engine recovers all line work.

**Against the NFR-01 budget:** 300 sheets ingested and classified in an hour is
3,600 s × 4 workers ÷ 300 = **48 worker-seconds per sheet** for everything: parsing, tiles,
title block and OCR, classification and geometry. pypdfium2's slowest sheet took 0.81 s,
about 2% of that. Real sheets would have to be around 60 times heavier before extraction
alone threatened the target.

## Licence recommendation

**Do not buy a PyMuPDF commercial licence now.** PyMuPDF is the faster engine, about half
pypdfium2's time and two-thirds of its memory. But both are complete on every sheet, and the
difference is about 0.4 s a sheet: two minutes over a 300-sheet set, against an hour's budget.
Nothing downstream is waiting on quality pypdfium2 lacks. **Revisit** if real sheets put
pypdfium2 extraction above about 10 s a sheet, or show completeness PyMuPDF has and PDFium
does not. The extraction layer sits behind one function per engine, so a switch would not
touch later stages.

The product owner decides.

## Consequences

- No licence spend unless the benchmark shows a material gain.
- The extraction layer sits behind an interface, so the engine can change without touching later stages.
- All PDF parsing runs in the parser sandbox (project-context guardrail 9).
