# Phase 1 exit report

Generated 01 Oct 2026 08:27 UTC by `firebid-eval exit`. Detection, classification and mapping measured on **synthetic tenders only (no golden set yet)**.

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

| Tender | Kind | Lines | Manual h | AI-assisted h | Effort reduction |
|---|---|---|---|---|---|
| SYNTH-SHADOW-001 | synthetic | 11 | - | 0.05 | - |

## Non-functional evidence

| Evidence | Status | Result |
|---|---|---|
| ingest benchmark | recorded | synthetic-50: 50 sheets in 3.9 min (4.6 s a sheet); 300 sheets projected at 23.2 min (meets the 60 min of NFR-01) |
| QTO benchmark | recorded | synthetic-50: first-pass takeoff of 50 sheets (14 items) in 7.0 min of machine time (legend confirmed by script); 50 sheets projected at 7.0 min (meets the 240 min of NFR-01) |
| load test | recorded | 10 bids and 20 users: 10 of 10 bids taken off in 2.3 min; 1831 requests, 0 failed; workbench p95 0.094 s (meets NFR-02 and the 2 s p95 of NFR-01) |
| restore drill | recorded | local rehearsal (docker compose): 120 MB restored and verified in 0.2 min (RTO target 8 h); data lost 0.0 min back (RPO target 24 h); 58 tables and 83 audit chains match |
| provider game day | recorded | local rehearsal: primary 'anthropic' down; 9 route(s) served by an approved fallback, 1 escalated cleanly, 0 failed |
| deployment guard | recorded | window refused: 45 bid(s) due within 48 h (BID-2026-034 due 11 Oct 18:39 UTC, BID-2026-035 due 11 Oct 18:52 UTC, BID-2026-036 due 11 Oct 21:48 UTC, BID-2026-037 due 11 Oct 23:09 UTC, BID-2026-038 due 12 Oct 07:53 UTC and 40 more) |
| provider data terms | **misses** | to confirm under decision D2: anthropic, openai, google |

## Gap list

| Gap | Cause | Proposed action |
|---|---|---|
| No golden set: the exit accuracy criteria cannot be measured | decision D3's historical tenders with verified takeoffs have not been collected | collect 10-20 tenders (`firebid-eval template` / `import`); rerun `firebid-eval exit` |
| Indicative real sample: sprinkler count accuracy 1.2% | see the gap entries on legend typing and head-type matching | confirm the legend on the Symbols page, then re-measure; fix matching |
| Indicative real sample: missed-item rate 100.0% | see the gap entries on legend typing and head-type matching | confirm the legend on the Symbols page, then re-measure; fix matching |
| Indicative real sample: false-detection rate 100.0% | see the gap entries on legend typing and head-type matching | confirm the legend on the Symbols page, then re-measure; fix matching |
| Provider data terms: misses its target | to confirm under decision D2: anthropic, openai, google | sponsor decision D2: confirm each provider's region, retention and no-training terms, then record them in llm.yaml |
| Real drawings: head types confused by symbol matching; some sheets undercount | Circle-type sprinkler symbols (concealed, exposed quick-response) are close in the shape descriptor. On the 6405 sample, concealed heads matched the exposed quick-response symbol, and F/18 matched 179 heads against a reference of 329. | A dedicated step on head-type matching, using the 6405 sample and the golden set: richer descriptors (fill, concentric rings), and confirmation-driven calibration. |
| Legend rows the keyword rules cannot type stay uncounted until a person confirms them | By design, ambiguous rows ("EXPOSED SPRINKLER": pendent or upright?) go to a person or the model. With no model key and no confirmation, the automatic count is zero. | Pilot runbook: confirm the legend on the Symbols page first. With D2, enable the model route for legend mapping. |
| Pipe runs are not identified from the legend's line styles | Pipework legend rows (a sample of line) are recognised and kept out of symbol matching, but are not yet used to pick out pipe runs on a PDF. | Phase 2 (P2-01): line-style classification from legend samples. |
| No model path has run against a real provider | No API key on any environment; decision D2 (provider data terms and hosting) is open. | After D2, run the live contract tests (`pytest -m live`) and the provider game day in staging. |
| Not deployed in a Singapore region | ADR-008 (hosting on Google Cloud, asia-southeast1) was accepted on 2 Oct 2026. ADR-004 is proposed: the provider data terms, the other half of D2, are open. No cloud account exists. | Create the project; `terraform apply` for staging, then run the restore drill and game day there; then production. Decide the provider data terms (ADR-004). |
| No target AI cost per tender | Phase 0 did not set one (NFR-15). `config/kpi.yaml` holds a placeholder. | Sponsor sets the target; the cost report compares against it. |
| Commercial settings are placeholders | Rate list (synthetic), queue weights and `weight_unit_sgd`, source preference, BOQ variance threshold, measurement convention wording and BOQ template are all marked "to be confirmed". | Commercial team and senior estimator confirm them before the pilot prices a bid. |
| Rule-derived pipe (sprinkler drops, riser main) rests on default inputs | The synthetic shadow comparison matched every drawn quantity, and differed only where measurement rules add what no drawing shows: 24 drops x 500 mm of DN25 (branch elevation 3300, ceiling 2750) and 4 m of DN150 riser (floor to floor 4000, one level). Those inputs are rule defaults marked "to be confirmed", and an estimator's takeoff may or may not include such items. | Senior estimator confirms the drop and riser rules and their inputs per project (the measurement rules page); the shadow pilot's live tenders show whether estimators take these off. |
| Estimator usability session not run | Needs two or three estimators (P1-08 guide in docs/plan/usability/). | Run it with the pilot's estimator champions; record findings in the build log. |
| BOQ mapping accuracy measured on the synthetic bill only | No real client bills with verified mappings. | Add client bills to the golden set (D3); re-run `firebid-eval run --suite p1_boq`. |
| A bid cannot be deleted in the app | No delete flow; a test bid was removed directly in the database. | An administrator delete with its own audit entry and retention rules. |
| A job on the ordinary worker queue whose worker died stays running for ever | system.retry_stalled_parse re-queues only the parser pool's jobs (ADR-010). Found in the P1-11 benchmarks: a symbol.propose job left `doing` with no worker after a container restart, 7 hours later, so its legend row never got the model's proposal. | Extend the stall check to the default queue: re-queue the idempotent tasks (detection, takeoff, the model proposals) and fail the rest with a reason a person sees; alert on any job `doing` for over an hour. |
| Two order-dependent tests | `test_migrations_leave_application_logging_working` fails when run alone with the migration tests; `Specification.test.tsx` timed out once under load. | Isolate their set-up; neither affects the product. |
