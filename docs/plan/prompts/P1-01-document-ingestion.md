# P1-01 · Document Ingestion

**Builds on:** P0-02 (Document, Sheet, storage, transactional job queuing), P0-03 (bids, per-bid scoping and row-level security), P0-05 (synthetic fixtures). **Needs:** ADR-003 accepted (DWG converter). Fallback: accept DXF only and record DWG as "conversion unavailable".

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. This is an L step: commit per sub-part (upload and scanning, sandbox, PDF pipeline, DWG pipeline, tiles, progress UI).

## Read first

- `docs/plan/project-context.md`: PDF, DWG and untrusted-file choices, guardrails 8 and 9, conventions *jobs* and *stage caching*.
- Requirements: §6.2 (FR-DOC-01, FR-DOC-07), NFR-01, NFR-06, NFR-08.
- `docs/adr/ADR-002*`, `docs/adr/ADR-003*`, `docs/adr/ADR-005*`, `docs/plan/BUILD_LOG.md`.

## Goal

An estimator uploads a tender set of hundreds of files. Every file is scanned, stored, checksummed and opened only inside a sandbox. Each drawing page or layout becomes a sheet the viewer can open. Every failure is visible. Build it first as a **tracer bullet**: upload → sheet list → open a sheet in a basic tile viewer. Then widen it.

## Scope

FR-DOC-01 (Phase 1 formats: PDF vector and scanned, DWG/DXF, XLSX/CSV, DOCX; legacy `.doc`/`.xls` converted), FR-DOC-07, NFR-01 (ingestion share of the target).

## Build

1. **Upload:** multi-file and zip upload into a bid's tender package, using presigned multipart S3 uploads so large sets resume after interruption. Store a SHA-256 per file; identical re-uploads link to the existing document. Originals are written once.
2. **Malware scan:** scan every uploaded file (ClamAV in its own container, signatures updated automatically) before any parser touches it. An infected file gets state `quarantined` with the scanner's finding, and is never opened. A scan outage holds files in `awaiting_scan` rather than skipping the scan.
3. **Parser sandbox** (`sandbox/`): a separate worker pool, in its own container image, runs every parser of external files. The pool has:
   - no network;
   - a read-only filesystem except a per-job scratch directory;
   - CPU, memory and wall-clock limits per job;
   - a non-root user.

   Inside it, apply `defusedxml`, Pillow's decompression-bomb limit and archive limits (total uncompressed size, file count, nesting depth). A job that breaches a limit or crashes marks its file `rejected` with the reason; the pool itself survives.
4. **Type detection:** detect by content signature (magic bytes and structure), not extension. Unsupported types, and password-protected or corrupt files, get state `rejected` with a human-readable reason, shown in the UI.
5. **Legacy Office formats:** LibreOffice headless, inside the sandbox, converts `.doc` to `.docx` and `.xls` to `.xlsx`. The original is kept, and the converted file is linked as a derived document with its conversion recorded.
6. **PDF pipeline** (queued jobs, enqueued in the upload's transaction), using `pypdfium2`:
   - Create one Sheet per page with page size in mm.
   - Classify each page as vector, raster or mixed from PDFium page-object counts (paths, text, images) and image area coverage, and store the class.
   - Render a thumbnail and the **low zoom levels** of the deep-zoom pyramid (WebP) at ingest.
7. **DWG/DXF pipeline:** DWG → DXF through the converter interface, inside the sandbox. For each paperspace layout, create a Sheet with its extents in mm, and render its thumbnail and low zoom levels by plotting the layout (via ezdxf's drawing add-on or an equivalent).
8. **On-demand tiles** (ADR-005):
   - A tile endpoint renders close-up levels on first request, in the sandbox pool, and caches each tile in object storage.
   - Cache keys include the sheet's content hash and the renderer version, following the stage-caching convention.
   - Tile responses carry long-lived cache headers.
9. **Office files:** register XLSX, CSV and DOCX as documents with their type; classification comes in P1-02.
10. **Lineage** (FR-DOC-07): every Sheet stores its `SourceRef`.
11. **Progress and state:** a per-package progress view that updates live via SSE, with states received, awaiting_scan, quarantined, processing, done and rejected. Failed jobs retry with backoff, then land in `failed` with the error shown. No file disappears without a visible state.
12. **Viewer (tracer bullet):** a basic OpenSeadragon page that opens any sheet's tiles, with close-up tiles filling in as they render. P1-08 builds the full workbench on it.
13. **Throughput:** run pages in parallel across worker processes. Add a benchmark script that ingests a generated 300-sheet set and reports time to "all sheets viewable", peak memory per worker, and storage used per sheet.

## Done when

- Synthetic and sample fixtures ingest with the correct sheet counts and page sizes, and every sheet has complete lineage. Tagged FR-DOC-01 and FR-DOC-07.
- Corrupt, password-protected and unsupported fixtures each show a rejected state with a reason, and no fixture is silently dropped. Tagged FR-DOC-01.
- The EICAR test file is quarantined and never reaches a parser, and a simulated scanner outage leaves files in `awaiting_scan`. Tagged NFR-06.
- Sandbox tests, tagged NFR-06:
  - a zip bomb, an XML entity bomb and an oversized image are each rejected with a reason;
  - a parser job attempting a network connection fails;
  - a job exceeding its memory limit is killed without taking down the pool.
- A legacy `.doc` and `.xls` fixture convert and register as derived documents.
- Re-uploading an identical file creates no second stored object, and re-requesting a cached tile triggers no re-render.
- The Playwright E2E passes: upload a zip of synthetic files → progress completes → open a sheet → zoom in until close-up tiles render.
- The vector/raster classifier labels the synthetic raster fixture as raster and the vector fixtures as vector.
- The 300-sheet benchmark results are recorded in the build log, tagged NFR-01, with the remaining budget for classification noted.
- A build log entry is appended.
