# ADR-003: DWG-to-DXF conversion

- **Status:** Proposed; licence confirmation pending (dependency DP3)
- **Date:** 2026-09-22
- **Deciders:** Tech Lead; Procurement (licence)
- **Requirements:** FR-DOC-01, FR-VIS-01; dependency DP3

## Context

Some consultants issue DWG files. DWG is a proprietary binary format; the open-source `ezdxf` reads DXF, not DWG, so DWG must be converted first. Conversion runs on untrusted files.

## Options

1. **ODA File Converter** (Open Design Alliance), invoked as a command-line tool. Mature and handles all DWG versions. Free to download, but its terms for server-side commercial use must be confirmed; ODA membership may be required.
2. **LibreDWG** (GPL). Open source but incomplete for newer DWG versions, and GPL affects distribution.
3. **Cloud conversion** (e.g. Autodesk Platform Services Model Derivative). Reliable, but sends confidential drawings to a third party and needs region and data-terms review (NFR-05, NFR-08).
4. **Ask consultants for DXF or PDF.** No licence, but depends on the consultant.

## Recommendation

**ODA File Converter behind a `DwgConverter` interface**, run inside the parser sandbox with no network and resource limits. Confirm the licence for server-side commercial use before production. Until it is confirmed, or if it is refused, fall back to **option 4**: DWG files get the state "conversion unavailable" with a request for DXF or PDF.

## Consequences

- Conversion is isolated and swappable; the rest of the pipeline sees only DXF.
- Procurement must confirm the ODA terms (dependency DP3) before Phase 1 goes live.
