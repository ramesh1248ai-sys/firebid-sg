# Phase 1 exit report

Generated 28 Sep 2026 21:00 UTC by `firebid-eval exit`. Detection, classification and mapping measured on **synthetic tenders only (no golden set yet)**.

## Exit criteria (requirements §13.2)

| Criterion | Target | Evidence | Status |
|---|---|---|---|
| Sprinkler count on the golden set | ≥98% | 100.0% on synthetic tenders only (no golden set yet) | **pending: no golden set** |
| Pipe length on the golden set | within ±5% | 0.0% error on synthetic tenders only (no golden set yet) | **pending: no golden set** |
| QTO effort in a shadow pilot on ≥3 live tenders | -30% | 0 live tender(s); - mean reduction | **pending: pilot not run** |

## Phase 1 KPIs (requirements §14)

| KPI | Target | Synthetic | Indicative sample | Live bids | Status |
|---|---|---|---|---|---|
| Sprinkler count accuracy | ≥98.0% | 100.0% | 1.2% | - | pending (golden set) |
| Pipe length error | ≤5.0% | 0.0% | - | - | pending (golden set) |
| Missed-item rate | ≤5.0% | 0.0% | 100.0% | - | pending (golden set) |
| False-detection rate | ≤5.0% | 0.0% | 100.0% | - | pending (golden set) |
| Duplicate detection | ≥95.0% | 100.0% | - | - | pending (golden set) |
| QTO effort | -30% | - | - | - min on task recorded | pending (pilot) |
| Human escalation rate | monitor, trending down | - | - | - of 0 runs | monitored |
| Workflow and tool success | ≥98% | - | - | - | not measured |

### Detection, by input class and consultant

| Group | Sprinkler count accuracy | Pipe length error | Missed-item rate | False-detection rate | Duplicate detection |
|---|---|---|---|---|---|
| input class: dwg | 100.0% | 0.0% | 0.0% | 0.0% | 100.0% |
| input class: vector_pdf | 100.0% | 0.0% | 0.0% | 0.0% | - |
| consultant: ALPHA CONSULTANTS PTE LTD | 100.0% | 0.0% | 0.0% | 0.0% | 100.0% |

### Indicative real-drawing sample

> INDICATIVE, NOT VERIFIED: head counts from a machine-generated compliance review (Rev A schedule), not an estimator's takeoff. Exposed heads recorded as pendent. Pipe lengths omitted: the reference measures added branch pipework only.

| Group | Sprinkler count accuracy | Pipe length error | Missed-item rate | False-detection rate | Duplicate detection |
|---|---|---|---|---|---|
| input class: vector_pdf | 1.2% | - | 100.0% | 100.0% | - |
| consultant: 6405 consultant (indicative sample) | 1.2% | - | 100.0% | 100.0% | - |

### Other Phase 1 measures

| Measure | Value | Target | Measured on |
|---|---|---|---|
| Drawing number accuracy (FR-DOC-02) | 50.0% | - | synthetic sheets |
| Revision accuracy (FR-DOC-02) | 50.0% | - | synthetic sheets |
| Client BOQ mapping (FR-BOQ-02) | 100.0% | ≥90% | synthetic bill, rules only |

## Shadow mode

No shadow-mode comparison recorded yet.

## Non-functional evidence

| Evidence | Status | Result |
|---|---|---|
| ingest benchmark | **pending** | not yet run |
| QTO benchmark | **pending** | not yet run |
| load test | **pending** | not yet run |
| restore drill | **pending** | not yet run |
| provider game day | **pending** | not yet run |
| deployment guard | **pending** | not yet run |
| provider data terms | **pending** | not yet run |

## Gap list

| Gap | Cause | Proposed action |
|---|---|---|
| No golden set: the exit accuracy criteria cannot be measured | decision D3's historical tenders with verified takeoffs have not been collected | collect 10-20 tenders (`firebid-eval template` / `import`); rerun `firebid-eval exit` |
| Indicative real sample: sprinkler count accuracy 1.2% | see the gap entries on legend typing and head-type matching | confirm the legend on the Symbols page, then re-measure; fix matching |
| Indicative real sample: missed-item rate 100.0% | see the gap entries on legend typing and head-type matching | confirm the legend on the Symbols page, then re-measure; fix matching |
| Indicative real sample: false-detection rate 100.0% | see the gap entries on legend typing and head-type matching | confirm the legend on the Symbols page, then re-measure; fix matching |
| Ingest benchmark: no evidence yet | not run | run it (see runbooks) |
| Qto benchmark: no evidence yet | not run | run it (see runbooks) |
| Load test: no evidence yet | not run | run it (see runbooks) |
| Restore drill: no evidence yet | not run | run it (see runbooks) |
| Provider game day: no evidence yet | not run | run it (see runbooks) |
| Deployment guard: no evidence yet | not run | run it (see runbooks) |
| Provider data terms: no evidence yet | not run | run it (see runbooks) |
| Real drawings: head types confused by symbol matching; some sheets undercount | Circle-type sprinkler symbols (concealed, exposed quick-response) are close in the shape descriptor. On the 6405 sample, concealed heads matched the exposed quick-response symbol, and F/18 matched 179 heads against a reference of 329. | A dedicated step on head-type matching, using the 6405 sample and the golden set: richer descriptors (fill, concentric rings), and confirmation-driven calibration. |
| Legend rows the keyword rules cannot type stay uncounted until a person confirms them | By design, ambiguous rows ("EXPOSED SPRINKLER": pendent or upright?) go to a person or the model. With no model key and no confirmation, the automatic count is zero. | Pilot runbook: confirm the legend on the Symbols page first. With D2, enable the model route for legend mapping. |
| Pipe runs are not identified from the legend's line styles | Pipework legend rows (a sample of line) are recognised and kept out of symbol matching, but are not yet used to pick out pipe runs on a PDF. | Phase 2 (P2-01): line-style classification from legend samples. |
| No model path has run against a real provider | No API key on any environment; decision D2 (provider data terms and hosting) is open. | After D2, run the live contract tests (`pytest -m live`) and the provider game day in staging. |
| Not deployed in a Singapore region | ADR-004 is proposed and D2 is open; ADR-008 (hosting on Google Cloud, asia-southeast1) is proposed. No cloud account exists. | Sponsor decision D2 and ADR-008; create the project; `terraform apply` for staging, then run the restore drill and game day there; then production. |
| No target AI cost per tender | Phase 0 did not set one (NFR-15). `config/kpi.yaml` holds a placeholder. | Sponsor sets the target; the cost report compares against it. |
| Commercial settings are placeholders | Rate list (synthetic), queue weights and `weight_unit_sgd`, source preference, BOQ variance threshold, measurement convention wording and BOQ template are all marked "to be confirmed". | Commercial team and senior estimator confirm them before the pilot prices a bid. |
| Estimator usability session not run | Needs two or three estimators (P1-08 guide in docs/plan/usability/). | Run it with the pilot's estimator champions; record findings in the build log. |
| BOQ mapping accuracy measured on the synthetic bill only | No real client bills with verified mappings. | Add client bills to the golden set (D3); re-run `firebid-eval run --suite p1_boq`. |
| A bid cannot be deleted in the app | No delete flow; a test bid was removed directly in the database. | An administrator delete with its own audit entry and retention rules. |
| Two order-dependent tests | `test_migrations_leave_application_logging_working` fails when run alone with the migration tests; `Specification.test.tsx` timed out once under load. | Isolate their set-up; neither affects the product. |
