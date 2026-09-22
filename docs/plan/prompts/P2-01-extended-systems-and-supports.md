# P2-01 · Extended Fire Protection Systems and Supports

**Builds on:** P1-04 (object library, mappings), P1-05 (detection, network), P1-07 (QTO rules engine), P1-06 (spec attributes). **Needs:** golden set tenders that include pump rooms, hydrant and hose reel systems (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: Singapore terminology, *deterministic-first*.
- Requirements: §4 (Phase 2 systems), §6.3 (FR-VIS-04), §6.4 (FR-QTO-06, 07), §2.5.
- `docs/plan/BUILD_LOG.md`, including Phase 1 gap lists in `docs/reports/phase1-exit.md`.

## Goal

Extend detection and takeoff beyond wet-pipe sprinklers to the rest of the Phase 2 systems: fire pumps, rising mains, hydrants, hose reels, fire water storage, and dry-pipe, pre-action and deluge valve sets. Hangers and supports are derived by rule from the specification.

## Scope

FR-VIS-04, FR-QTO-06, FR-QTO-07.

## Build

1. **Object library:** add the canonical types, with attribute schemas:
   - fire pumps (duty and standby), jockey pumps, pump controllers;
   - fire water tanks;
   - breeching inlets, landing valves, hydrants, hose reels;
   - test headers and flow-test arrangements;
   - dry-pipe, pre-action and deluge valve sets, air compressors.
2. **Detection** (FR-VIS-04): use legend mapping and symbol matching on plans. Parse pump-room layouts and riser or schematic diagrams, where equipment often appears only as tagged symbols with schedules. Link equipment to equipment schedules (tables on drawings, or spec schedules) for attributes such as duty, flow and head where stated.
3. **Takeoff** (FR-QTO-06): equipment items with attributes and evidence. Rising main pipework uses the P1-07 riser rules with the level schedule. Hydrant underground pipework is measured from site plans at verified scale.
4. **Hangers and supports** (FR-QTO-07):
   - Rule-derived from pipe runs, using spacing by DN from verified spec attributes (with a clause citation) or a configured company default when the spec is silent.
   - Seismic restraint is generated only when a verified spec attribute requires it.
5. **Eval:** extend ground truth, metrics and synthetic fixtures (pump-room plan, riser schematic, site hydrant plan) to the new types, and add them to the regression gate.

## Done when

- Synthetic fixtures for each new equipment type are detected and taken off with correct counts and attributes. Tagged FR-VIS-04 and FR-QTO-06.
- Hanger quantities match hand calculations from spec spacing rules, and each hanger item cites the spacing clause or the company default. Tagged FR-QTO-07.
- Seismic restraint items appear only for the fixture whose spec requires them.
- `firebid-eval` reports the new types on the golden set, and the regression gate includes them.
- A build log entry is appended.
