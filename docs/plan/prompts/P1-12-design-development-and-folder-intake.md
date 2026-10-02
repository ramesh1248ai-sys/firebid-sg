# P1-12 · Design Development and Folder Intake

**Builds on:** P1-01 (ingestion), P1-03 (geometry, views, verified scale), P1-05 (detections and pipe runs), P1-07 (QTO engine and measurement rules), P1-08 (workbench). **Needs:** a design-intent tender set to check against. The MOH (TTSH) set and SJ M&E's response to it were used; a synthetic sheet covers the tests.

This step was added after the Phase 1 exit report, from a real tender the pipeline could not take off. Its requirements (§6.17) are proposed and not yet in the approved specification: see ADR-011.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 1, 2, 3, 6 and 9; units; "Configuration over constants".
- Requirements: §6.17 (FR-DSN-01 to 06, FR-DOC-09, 10); §1.3 and §2.1 (what the platform is not); FR-VIS-05; FR-QTO-03 and 09; FR-ADM-02.
- `docs/adr/ADR-011-design-development-as-an-estimating-aid.md`.
- `docs/plan/BUILD_LOG.md`.

## Goal

A design-intent tender, sent as the folder it sits in, gets a takeoff that includes the heads and range pipes its drawings leave to the contractor. Each is a proposal a person confirmed the basis for, kept apart from what was drawn, with the rule behind it. The company's own documents in the folder never enter the registers.

## Scope

FR-DSN-01 to 04; FR-DOC-09 and 10. Not in this step: FR-DSN-05 (export), FR-DSN-06 (match-line scope), hydraulic checks.

## Build

1. **Document origin (FR-DOC-10):**
   - Each document has an origin (tender, working, reference), a status (proposed or confirmed) and the reason.
   - Rules in `config/intake.yaml` propose the origin from the file's path. A lock or system file is ignored: reported, never stored.
   - Only a tender document whose origin is confirmed is queued to be read. A person can set a document's origin later; making it tender queues it.
2. **Folder upload (FR-DOC-09):**
   - The upload route takes each file's path in the folder and the origin the person gave it.
   - The Documents page picks a folder, shows each folder's proposed origin for the person to confirm, and sends the files in batches. Every file is accounted for.
3. **Design basis (FR-DSN-01):**
   - Read the criteria a plan sheet's notes state (spacing, area per head, K-factor), each citing its words, and whether a note leaves the design to the contractor.
   - Store them per sheet as a proposal. A sheet with no plan view at a verified or calibrated scale is blocked, with the reason.
   - A person with `design_basis.confirm` chooses the criterion. Nothing is laid out before that.
4. **Spaces (FR-DSN-02):** find rooms, open floors and the building outline from the base plan's linework; name them from the plan's text; find crossed shafts.
5. **Layout (FR-DSN-03):**
   - Design rules are a versioned measurement rule (`sprinkler_layout`), seeded from `config/design_rules.yaml`, every value "to be confirmed".
   - Heads on a grid square to each space's walls, never beyond the criterion. Omissions by rule, each listed. Head type and rating by space and level.
   - Range pipes sized by the heads each length feeds, fed from the nearest drawn pipe, or by a stated allowance where none is in reach.
6. **Takeoff (FR-DSN-04):**
   - Store proposed heads, drops and pipes as detections and pipe runs marked `designed`. Detecting a sheet again leaves them alone; a person's rejection survives a new layout.
   - The QTO engine makes them rule-derived items of their own, marked "proposed layout, not drawn", with the design rule in the evidence record.
7. **Design page:** list each plan sheet with its criteria, state, drawn and proposed heads; confirm in bulk; show what was omitted and why; withdraw a sheet's layout.

## Done when

- A synthetic design-intent sheet goes from DXF to a proposed layout in a test: scale verified from its dimensions, criteria read from its notes, each room's area within 5%, and no head covering more than the criterion allows. Tagged FR-DSN-01 to 03.
- A test shows no layout is made before a person confirms, that the confirmation names the person and is audited, and that a role without `design_basis.confirm` is refused.
- A test shows what was drawn is counted exactly as before when proposed heads are added, and that proposed items carry the rule key, version and criterion. Tagged FR-DSN-04.
- A test shows a marked-up copy of a tender drawing, sent in the same folder or inside a tender archive, is not queued to be read. Tagged FR-DOC-10.
- A test shows a folder's files keep their paths and every file is stored, a duplicate, refused or ignored. Tagged FR-DOC-09.
- On the MOH L10 sheet, the proposed head count is reported against the estimators' own count in the build log.
- `make req-coverage PHASE=P1` covers FR-DSN-01 to 04 and FR-DOC-09, 10.
- The build log has this step's entry.
