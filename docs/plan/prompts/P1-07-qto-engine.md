# P1-07 · QTO Engine, Measurement Rules and De-duplication

**Builds on:** P1-05 (detections and pipe network), P1-06 (verified spec attributes), P1-03 (scale, grids, views), P0-02 (QTO state machine, evidence types). **Needs:** estimator input on default measurement rules and allowances (business track). Seed defaults and mark them "to be confirmed".

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. This is an XL step: commit per sub-part (item generation, rules engine, fittings, de-duplication, evidence completeness, manual items).

## Read first

- `docs/plan/project-context.md`: guardrails 1–3 and 6, units, *evidence record*.
- Requirements: §6.4 (FR-QTO-01 to 05, 08 to 11), §6.16 (FR-ADM-02 measurement rules), Appendix B (evidence fields), §7 (QTO item model).
- `docs/plan/BUILD_LOG.md`.

## Goal

Turn detections into QTO items: counted, measured and rule-derived quantities with complete evidence records, and no double counting across sheets and views. Every number is reproducible from its inputs and rules.

## Scope

FR-QTO-01, 02, 03, 04, 05, 08, 09, 10, FR-QTO-11 (manual items API; the UI is in P1-08), FR-ADM-02 (measurement rules and allowance factors, versioned).

## Build

1. **Item generation:**
   - Sprinklers by type and attributes.
   - Pipes by DN, material, schedule/class, joining method and classification, with length in integer mm from network geometry at verified scale.
   - Valves and assemblies by type.
   - Group each by level, zone and grid range, from current sheets only.
2. **Attribute resolution:** drawing annotation, then verified spec attribute (P1-06), then "not specified". Record which source supplied each attribute (FR-QTO-01).
3. **Rules engine** (versioned rules as data, FR-ADM-02):
   - **Drops:** drop length per sprinkler = ceiling or soffit height − main elevation (or a configured default) − sprinkler setting. Heights come from section notes, reflected ceiling plan notes or bid parameters the estimator enters; each input records its source.
   - **Risers:** floor-to-floor height × levels served, from the riser schematic and the level schedule.
   - **Fittings where not drawn:**
     - a tee per branch connection;
     - an elbow per direction change;
     - a reducer per size change;
     - grooved couplings per joint, based on pipe length ÷ configured random length plus fittings.

     Drawn fittings take precedence over derived ones.
   - **Allowances:** percentage factors by item class.
   - Each rule-derived item is labelled "rule-derived", with rule ID, rule version, inputs and input sources (FR-QTO-03, 04).
4. **Net vs allowance** (FR-QTO-10): store the net quantity; apply wastage and allowance factors as separate visible adjustments, never folded into the net.
5. **De-duplication** (FR-QTO-08):
   - Align views to a building frame per level using grid systems.
   - Detect overlap between enlarged plans and general plans, between match-lined adjacent sheets, and between schematics or sections and plans. By default, schematic and section items do not count against plan items.
   - Put suspected duplicates in a DuplicateGroup showing each evidence location.
   - An unresolved group blocks G1.
6. **Evidence completeness** (FR-QTO-09): every QTO item carries every Appendix B field. A completeness check runs on generation and at G1, and lists offending items.
7. **Manual items API** (FR-QTO-11): create, edit and delete manual count and length items with measurement on a verified-scale view, tagged `manual` with user and timestamp. Changes go through the QTO item state machine.
8. **Determinism:** recomputation is idempotent. The same inputs, mappings and rule versions give identical items and quantities, and a test compares hashes. Recompute preserves human verification on items whose inputs are unchanged.

## Done when

- On synthetic fixtures, the counts and net lengths per DN match ground truth, and drop and riser quantities match hand calculations from fixture parameters. Tagged FR-QTO-01 to FR-QTO-05.
- Rule-derived items show rule ID, version, inputs and their sources in API output. Tagged FR-QTO-03 and FR-QTO-04.
- Seeded duplicates in the synthetic set (enlarged plan, match-line overlap, schematic vs plan) are all detected. On the golden set, the duplicate detection rate is reported against the ≥ 95% target. G1 is refused while any group is unresolved. Tagged FR-QTO-08.
- The completeness check reports 100% on generated items, and flags a deliberately incomplete item. Tagged FR-QTO-09.
- Net and allowance quantities appear separately in the API and on export. Tagged FR-QTO-10.
- The determinism test passes, and recompute keeps verification on unchanged items.
- Manual item API tests pass, tagged FR-QTO-11, and measurement refuses an unverified scale.
- Rule edits create new versions, and old QTO results still reference the rule version they used. Tagged FR-ADM-02.
- A build log entry is appended.
