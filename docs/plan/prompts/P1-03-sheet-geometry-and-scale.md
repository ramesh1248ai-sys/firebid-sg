# P1-03 · Sheet Geometry, Text and Scale

**Builds on:** P1-01 (sheets from PDF and DXF, parser sandbox), P0-05 (synthetic fixtures). **Needs:** at least five real dense fire protection sheets (A1/A0, vector) from the golden set for the benchmark.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: *deterministic-first*, units (sheet mm vs model coordinates).
- Requirements: §6.3 (FR-VIS-01, 05, 06, 07, 08).
- `docs/plan/BUILD_LOG.md`.

## Goal

Every vector sheet becomes a normalised geometry model with text, views, grids and a trustworthy scale. Measurement is possible only where the scale is verified. This is the base that symbol matching, pipe networks and takeoff stand on.

## Scope

FR-VIS-01, FR-VIS-05, FR-VIS-06 (text extraction and OCR; association to objects comes in P1-05), FR-VIS-07, FR-VIS-08.

## Build

0. **Benchmark first (2 days).** On the real dense sheets, measure extraction time, peak memory and completeness (path and text counts against a reference) for:
   - `pypdfium2` through the PDFium page-object API;
   - `pdfplumber`;
   - PyMuPDF, for evaluation only.

   Record the results and a recommendation on the PyMuPDF commercial licence in ADR-002, for the product owner's decision. Build item 3 on the recommended engine.
1. **Geometry model:**
   - Primitives: line, polyline, arc, circle, text span, block insert, hatch outline.
   - Each carries sheet-mm coordinates plus layer, colour, linetype and lineweight where the source has them.
   - Each records its extraction method (FR-VIS-01).
   - Store geometry per sheet in a columnar file (Parquet) in object storage, with bbox indexes in PostgreSQL for spatial queries.
   - Keep hot loops vectorised (NumPy, Shapely 2 with STRtree); per-primitive Python loops belong only in cold paths.
   - **Stage cache:** geometry output is keyed by the sheet's content hash plus the extractor version, and an unchanged sheet is never re-extracted. Later drawing stages (P1-04 to P1-07) reuse this cache mechanism.
2. **DXF extraction** (ezdxf): paperspace entities directly; modelspace content through viewports (clip plus transform to paper coordinates, keeping the viewport scale). Explode block inserts to primitives for geometry while keeping the insert reference, because symbols are matched on the insert in P1-04.
3. **Vector PDF extraction** (the benchmark's engine, by default `pypdfium2`), run in the parser sandbox:
   - Walk page objects, recursing into form XObjects and applying each object's transformation matrix.
   - Extract path segments (lines, Béziers, close), stroke colour and width, and text characters with boxes merged into spans with rotation.
   - Keep path grouping, since it carries symbol structure.
   - Use `pdfplumber` as fallback for any page the primary engine cannot parse, and record the method.
4. **Raster text:** Tesseract OCR for raster sheets and embedded images, with per-word confidence.
5. **Views** (FR-VIS-08): detect drawing frames or viewports and view titles (e.g. PLAN, ENLARGED PLAN, SECTION A-A, SCHEMATIC, KEY PLAN, DETAIL). Create View records with type, title, extent and stated scale.
6. **Scale** (FR-VIS-05):
   - Parse stated scales per sheet and per view (e.g. `1:100`, `1:50 @ A1`) and detect NTS.
   - Verify with dimension entities, or with dimension text against measured distance, and cross-check against grid spacing.
   - Scale status is one of: verified, unverified, conflicting, NTS.
   - Add a calibration API (two points plus a known distance) that records who calibrated.
   - The measurement API refuses lengths unless the view's scale is verified or calibrated.
7. **Grids and levels** (FR-VIS-07): detect grid lines and grid bubbles with their labels, and build a grid system per view, so any point converts to a reference such as "Grid B5–C6". Take the level from the title block or view title. The zone comes from P1-02 or from zone boundaries where drawn.
8. **Cross-format consistency:** the same synthetic drawing as DXF and as vector PDF must yield equivalent geometry within tolerance.

## Done when

- On synthetic fixtures, known line lengths measure within 0.5% at a verified scale, for both DXF and PDF. Tagged FR-VIS-01 and FR-VIS-05.
- The NTS fixture and an unverified-scale fixture both return a refusal from the measurement API, and calibration unlocks measurement with the calibrator recorded. Tagged FR-VIS-05.
- The enlarged-plan fixture produces a View of type "enlarged plan" whose extent overlaps the general plan area. Tagged FR-VIS-08.
- A known point returns the correct grid reference and level. Tagged FR-VIS-07.
- The raster fixture yields OCR text with confidences, and vector fixtures yield text spans with positions. Tagged FR-VIS-06.
- ADR-002 contains the benchmark table (time, peak memory, completeness per engine on the real dense sheets) and a licence recommendation.
- The chosen engine extracts each real dense sheet within the per-sheet budget that keeps the 300-sheet ingest-and-classify target (NFR-01) achievable with the planned worker count, and the build log shows that calculation. Tagged NFR-01.
- Re-running extraction on an unchanged sheet is a cache hit with no parsing, and bumping the extractor version forces re-extraction.
- The build log records geometry extraction time per sheet for the 300-sheet benchmark.
- A build log entry is appended.
