# P3-02 · BIM Ingestion and Multi-Discipline Coordination

**Builds on:** P1-01 (ingestion), P1-03 (geometry, grids, level frames), P2-06 (clarifications). **Needs:** sample IFC models and multi-discipline drawing sets from consultants, and an ADR on the Revit path (IFC export from consultants vs a model-derivative service) (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. This is an XL step: commit per sub-part (BIM ingestion, 2D alignment, clash engine, issue management, clarification link).

## Read first

- `docs/plan/project-context.md`: *deterministic-first*, *evidence record*.
- Requirements: §6.2 (FR-DOC-01, BIM formats), §6.9 (FR-CRD-01 to 05), §6.10 (FR-RFI-03), NFR-13.
- `docs/plan/BUILD_LOG.md`.

## Goal

Find real coordination problems between fire protection and the other disciplines from geometry. Present each with evidence, severity and an owner, and turn confirmed issues into tender clarifications.

## Scope

FR-DOC-01 (IFC, RVT via the chosen path, NWD/NWC via exported clash reports or federated IFC), FR-CRD-01 to 05, FR-RFI-03, NFR-13 (IFC 4).

## Build

1. **BIM ingestion:**
   - IFC 2x3 and IFC 4 via IfcOpenShell: elements, types, property sets, storeys and geometry.
   - Revit per the new ADR.
   - Navisworks via imported clash-report XML, or federated IFC exports.
   - Map fire protection elements to the canonical library.
2. **Multi-discipline 2D** (FR-CRD-01): align architectural, structural, ACMV, electrical and plumbing sheets to fire protection sheets per level using grid systems, as overlays.
3. **Clash and clearance engine** (FR-CRD-02):
   - Hard clashes and clearance violations from geometry (IfcOpenShell geometry with a spatial index, or IfcClash), using configurable clearance rules per element pair.
   - A clash raised from 2D-only data requires explicit drawing evidence, and is labelled as such.
4. **Issue categories** (FR-CRD-03):
   - beam, duct and cable-tray conflicts;
   - ceiling void congestion (available void depth vs stacked services);
   - pipe crossings;
   - sleeve and opening needs through walls and slabs;
   - valve access and maintenance clearance;
   - pump-room and plant-room layout conflicts.
5. **Issue records** (FR-CRD-04): severity, evidence (snapshot views, coordinates, element IDs), affected QTO items, suggested owner. Group duplicates of the same underlying problem, and add a workbench panel to review them.
6. **Feedback** (FR-CRD-05): users mark false positives with a reason. Rules and tolerances are tuned from the feedback, versioned, and measured for precision in `firebid-eval`.
7. **Clarifications** (FR-RFI-03): a confirmed issue becomes a clarification candidate for the P2-06 drafting agent, with its evidence attached.

## Done when

- Synthetic IFC fixtures with seeded hard clashes and clearance violations are all detected, and a clean fixture raises none. Tagged FR-CRD-02 and NFR-13.
- Each issue category has at least one seeded fixture detected with evidence and severity. Tagged FR-CRD-03 and FR-CRD-04.
- Duplicate clashes of one problem group into one issue. Tagged FR-CRD-04.
- `firebid-eval` reports coordination precision on the golden set when present (target ≥ 70%). A marked false positive affects the next run only through a versioned rule change. Tagged FR-CRD-05.
- A confirmed issue produces a clarification draft with its evidence. Tagged FR-RFI-03.
- IFC and the chosen Revit and Navisworks paths ingest their sample files. Tagged FR-DOC-01 and FR-CRD-01.
- A build log entry is appended.
