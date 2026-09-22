# P1-04 · Legend and Symbol Mapping

**Builds on:** P1-03 (geometry, text, views), P0-04 (gateway, proposals). **Needs:** golden set legends from several consultants, to measure reuse. Synthetic legends cover the logic.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: *proposal*, *deterministic-first*, Singapore terminology.
- Requirements: §6.3 (FR-VIS-02), §6.16 (FR-ADM-02), §2.5 (terminology), §4 (Phase 1 system scope), §6.4 (attributes needed by QTO).
- `docs/plan/BUILD_LOG.md`.

## Goal

Every symbol a consultant uses maps to one canonical fire protection object type, confirmed by a person once and reused on later tenders from the same consultant. Unmapped symbols never reach a count.

## Scope

FR-VIS-02, FR-ADM-02 (canonical object library and consultant symbol mappings, versioned with history).

## Build

1. **Canonical object library** (versioned, admin-editable). Seed it with Phase 1 types:
   - sprinklers: pendent, upright, sidewall, concealed;
   - pipe;
   - fittings;
   - valves: installation control valve set, subsidiary / zone control valve, gate, butterfly, check, test and drain, PRV;
   - flow switch, tamper switch.

   Each type declares its attribute schema, e.g. K-factor, temperature rating, response, finish for sprinklers.
2. **Legend detection:** find legend regions on drawing sheets and dedicated legend sheets (headings such as LEGEND, SYMBOLS, ABBREVIATIONS). Split them into rows of symbol graphic plus description text.
3. **Symbol signatures:**
   - DXF: block definition name plus a geometry hash of the block.
   - Vector PDF: a descriptor of each path cluster that is invariant to translation, rotation and uniform scale (e.g. normalised segment and arc features), so instances match their legend entry.
   - Store signatures with tolerances.
4. **Mapping proposals:** for each legend row, ask the model (legend row image plus description text, structured output) for a canonical type and attributes, with confidence. Each result is a proposal. The estimator confirms or corrects in a mapping UI, and the confirmed mapping is stored per consultant, with an optional project override.
5. **Reuse:** on a new tender, match its legend signatures against the consultant's confirmed mappings first. Only new or changed symbols need confirmation.
6. **Unmapped symbols:** recurring symbol instances with no confirmed mapping are grouped and queued for mapping. The QTO engine (P1-07) queries mapping status and excludes unmapped types from counts, surfacing them instead.
7. **Admin UI:** object library and mapping library with version history (who changed what, when), and the ability to deprecate a type while keeping history.

## Done when

- A synthetic legend maps to the correct canonical types after one confirmation pass. On a second synthetic tender from the same consultant, the matches apply with no further confirmation. Tagged FR-VIS-02.
- A symbol instance with no confirmed mapping is never included in a count: a test requests counts and finds the instance listed under "unmapped" instead. Tagged FR-VIS-02.
- A rotated and scaled instance of a legend symbol in a PDF fixture matches its legend entry.
- Library and mapping edits create new versions with history, and a test reads back an earlier version. Tagged FR-ADM-02.
- Mapping proposals carry model, prompt version and confidence.
- A build log entry is appended.
