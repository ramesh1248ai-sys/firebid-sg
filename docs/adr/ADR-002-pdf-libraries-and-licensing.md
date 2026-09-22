# ADR-002: PDF libraries and licensing

- **Status:** Proposed; the licence question is decided after the P1-03 benchmark
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

## Consequences

- No licence spend unless the benchmark shows a material gain.
- The extraction layer sits behind an interface, so the engine can change without touching later stages.
- All PDF parsing runs in the parser sandbox (project-context guardrail 9).
