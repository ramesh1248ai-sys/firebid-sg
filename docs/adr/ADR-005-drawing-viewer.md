# ADR-005: Drawing viewer

- **Status:** Proposed
- **Date:** 2026-09-22
- **Deciders:** Tech Lead; frontend engineer; estimators consulted
- **Requirements:** FR-REV-01, FR-REV-03; NFR-10, NFR-12; step P1-08

## Context

Estimators review thousands of detected objects on large-format sheets, often on two monitors. The viewer must pan and zoom smoothly, draw a dense overlay of proposals colour-coded by status and confidence, hit-test clicks and lasso selections quickly, and behave the same for vector PDF, DXF and scanned sheets.

## Options

1. **OpenSeadragon deep-zoom tiles plus a Canvas/WebGL overlay.** One path for every source format (tiles are rendered server-side). The overlay scales to tens of thousands of objects with a spatial index. Tiles cost storage and render time.
2. **PDF.js vector rendering in the browser.** Crisp, with no tile storage, but PDF only (DXF and raster need other paths) and heavy on the client for dense A0 sheets.
3. **SVG overlay on tiles.** Simple, but the SVG DOM slows beyond a few thousand elements.

## Recommendation

**Option 1**, with these details:
- Pre-render only the low zoom levels at ingest. Render close-up tiles on first request in the parser sandbox, and cache them in object storage keyed by sheet content hash plus renderer version. Use WebP.
- The overlay is Canvas/WebGL, with a client-side spatial index (e.g. Flatbush) for hit-testing and lasso. Only the viewport's objects are redrawn each frame.
- Only selected and hovered items are drawn as SVG, for crisp outlines and accessibility.

## Consequences

- Ingest stays fast and storage stays bounded, since most close-up tiles are never requested.
- The first view of a close-up area has a short rendering delay; low-resolution tiles show meanwhile.
- Performance targets (step P1-08): 60 fps with 5,000 objects; at least 30 fps with 20,000 objects and hit-testing under 16 ms.
