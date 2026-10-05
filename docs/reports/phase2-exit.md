# Phase 2 exit report

Generated 05 Oct 2026 00:22 UTC by `firebid-eval exit-p2`. **No pilot data:** the KPIs below are instrumented and pending measurement.

## Exit criteria (requirements §13.2)

| Criterion | Target | Evidence | Status |
|---|---|---|---|
| Estimate turnaround | -30% against the baseline | not measured | **pending: pilot not run** |
| Zero unsourced prices | 100% of priced lines sourced | not measured | **pending: pilot not run** |
| Clarifications issued with only minor edits | ≥70% | not measured | **pending: pilot not run** |

## Phase 2 KPIs (requirements §14)

| KPI | How it is measured | Target | Measured | Status |
|---|---|---|---|---|
| Tender turnaround | working days from the day the bid was opened to its first G3 approval (weekends and configured holidays left out), averaged over bids, against `baseline_turnaround_working_days` | -30% | not measured | **pending: pilot not run** |
| Price provenance | priced lines of the current bill with a rate from the library, a quotation or a named allowance, over priced lines | 100% | not measured | **pending: pilot not run** |
| Clarification acceptance | drafts issued with a word-level edit distance from the draft at or under 20.0% of the longer text, over drafts issued | ≥70% | not measured | **pending: pilot not run** |
| Business outcomes | tenders awarded and lost, from recorded outcomes | monitor | not measured | monitored |

## Pilot findings

The assisted-mode pilot has not run.

## Regression (Phase 1 suites, with Phase 2 in place)

| Suite | Compared with | Result | Measured now |
|---|---|---|---|
| p1_detection | the result recorded 2026-09-28 | no regression | sprinkler_count_accuracy 1, pipe_length_error 2.037e-05, missed_item_rate 0, false_detection_rate 0, duplicate_detection_rate 1, sheet_classification_accuracy 1, drawing_number_accuracy 1, revision_accuracy 1, calibration_error 0.000192 |
| doc_classification | the result recorded 2026-10-01 | no regression | sheet_classification_accuracy 1, drawing_number_accuracy 1, revision_accuracy 1 |
| p2_systems | the baseline accepted 2026-10-03 by P2-01 build (to be confirmed by the product owner) | no regression | pipe_length_error 0, equipment_count_accuracy 1, missed_item_rate 0, false_detection_rate 0, duplicate_detection_rate 1, sheet_classification_accuracy 1, drawing_number_accuracy 1, revision_accuracy 1, calibration_error 0.3212 |
| p1_boq | its target of 90% | no regression | boq_mapping_accuracy 1 |

## Non-functional evidence

| Evidence | Recorded | With Phase 2 in place | Status | Result |
|---|---|---|---|---|
| Phase 2 workload benchmark | 2026-10-04 | yes | recorded | 5 workloads; slowest labour estimate at 0.526 s on 5000 bill lines, 200 library entries, against 2.0 s |
| load test | 2026-10-04 | yes | recorded | 10 bids and 20 users: 10 of 10 bids taken off in 2.1 min; 1689 requests, 0 failed; workbench p95 0.106 s (meets NFR-02 and the 2 s p95 of NFR-01) |
| ingest benchmark | 2026-10-04 | yes | recorded | synthetic-50: 50 sheets in 4.7 min (5.7 s a sheet); 300 sheets projected at 28.4 min (meets the 60 min of NFR-01) |
| QTO benchmark | 2026-10-04 | yes | recorded | synthetic-50: first-pass takeoff of 50 sheets (17 items) in 4.9 min of machine time (legend confirmed by script); 50 sheets projected at 4.9 min (meets the 240 min of NFR-01) |
| restore drill | 2026-10-04 | yes | recorded | local rehearsal (docker compose): 3455 MB restored and verified in 1.2 min (RTO target 8 h); data lost 0.0 min back (RPO target 24 h); 83 tables and 151 audit chains match |
| provider game day | 2026-10-04 | yes | recorded | local rehearsal: primary 'anthropic' down; 12 route(s) served by an approved fallback, 1 escalated cleanly, 0 failed |
| deployment guard | 2026-10-04 | yes | recorded | window refused: 13 bid(s) due within 48 h (BID-2026-138 due 01 Nov 10:46 UTC, BID-2026-139 due 01 Nov 12:01 UTC, BID-2026-140 due 03 Nov 03:25 UTC, BID-2026-141 due 03 Nov 03:25 UTC, BID-2026-142 due 03 Nov 03:25 UTC and 8 more) |
| provider data terms | 2026-09-29 | **no** | **misses** | to confirm under decision D2: anthropic, openai, google |

## Requirement coverage

`make req-coverage PHASE=P2`: **108 of 108** requirements up to Phase 2 have at least one test.

## Security and privacy review

Quotations, outcomes, submission snapshots and the KPI instrumentation were reviewed on 4 Oct 2026 (`docs/reports/phase2-security-review.md`). Dependency and code scans (pip-audit, npm audit, bandit) found nothing; semgrep, gitleaks and trivy were not run. The data inventory and the retention job now cover the Phase 2 entities: quotation file lines are cleared a year after a bid ends, and an outcome's competitor feedback is listed as personal data. No high or critical finding is open.

| Finding | Severity | Status |
|---|---|---|
| An outcome's free text (reasons, competitor feedback) is copied into the audit log, which is append-only and kept seven years | medium | open: say on the page not to name individuals; decide with the DPO whether the audit event holds the text |
| Without a snapshot bucket configured, submission snapshots are written with no object lock: tampering is detected by the hashes, not prevented | medium | open: configure the locked bucket in every deployed environment and exercise the lock on the real store |
| Quotation file lines, which can name a supplier's contact, were kept for ever | medium | closed in P2-09 (retention rule) |
| Retention periods are placeholders, to be confirmed against the records policy | low | open |
| semgrep, gitleaks and trivy have not run on the Phase 2 code | low | open: `make security`, or restore CI |

## AI cost and provider review

Not measurable yet. No provider key has been configured on any environment (decision D2 is open), so every Phase 2 model route (specification reading, quotation extraction, clarification wording) has run on its rules or been emulated, and the AI cost per tender is zero by absence, not by economy. `firebid-eval compare-models` still scores a stand-in predictor rather than the route's models, so it produces no evidence about quality. No change to any route's chain or reasoning level is proposed. The provider game day was rerun with the Phase 2 routes: with the primary provider down, 12 routes were served by an approved fallback and 1 escalated cleanly.

## Gap list

| Gap | Cause | Proposed action |
|---|---|---|
| No pilot: no Phase 2 exit criterion is measured | the assisted-mode pilot on live tenders has not run (business track) | run the pilot; then `make exit-report-p2 LIVE=1` |
| Provider data terms: misses its target | to confirm under decision D2: anthropic, openai, google | sponsor decision D2: confirm each provider's region, retention and no-training terms, then record them in llm.yaml |
| Match lines (FR-DSN-06) have been tried on one real floor: four sheets of one consultant | On the four tenth-storey sheets of the MOH set, run on their extracted geometry, each match line and the sheet it continues on are found, and sheet 1's layout falls from 514 heads to 372 on its own side (the estimators counted 346 there). The other 144 sheets and other consultants' ways of marking a cut are untried. A match line that steps is followed as one straight line; a DXF whose pipes are coloured by layer gives no colour to judge by; nothing compares two sheets to check they took opposite sides. | Run the rest of the MOH set and a second consultant's drawings; a person confirms each sheet's side on the Design page meanwhile. |
| The real design-intent sheets do not get from upload to a layout on the stack | Found on the MOH tenth-storey sheets. Three of four got no register entry at all and the fourth was read as "T/B", so none is Current and the design step finds no sheet; a sheet with no entry cannot be identified by a person. Two of the four are classed as schematic and detail, not plan, likely from words in their notes. None carries a dimension, so every plan view needs a person to calibrate its scale. | Before the pilot: read this consultant's title block (or let a person identify a sheet with no entry), and correct the plan classification; then run the set again. |
| The layout export is slow and large on a real sheet | A real A0 sheet has 1.5 million line segments. Its PDF is 11 MB and takes about a minute; its DXF is 273 MB and takes about a minute and a half. Both are made inside the request for the download. | Make the export in a background job and keep the file; write the DXF's tender drawing as polylines or a block, or offer the layout alone. |
| The layout export (FR-DSN-05) redraws the tender drawing rather than overlaying the original file | The PDF and DXF are drawn from the sheet's extracted geometry, in grey, so they show what the platform read: hatches, images and fonts of the original are not reproduced. | Product owner: accept, or ask for an overlay on the original PDF page. |
| CI has not run since P2-06: not on P2-07 to P2-09, the dependency refresh, the move to Python 3.14 or FR-DSN-05 and 06 | GitHub Actions refuses to start jobs: the account's payments have failed or its spending limit is reached. | Fix the billing; re-run CI on main. Until then the evidence is local: `make check`, `make test-integration` and `make e2e` in the Dev Container. |
| The end-to-end tests do not open the Phase 2 pages | The eleven end-to-end tests cover sign-in, the bid workspace, document intake and the workbench, and pass on the current stack. None opens Costing, Labour, Clarifications, Risk, Review or Design, and those pages were not walked through in a browser. | Add an end-to-end test that takes a bid from pricing to G4; walk the pages before the pilot. |
| Title block reading measures 100% here and measured 50% in the Phase 1 report | The suite's scanned tenders need Tesseract. The Phase 1 report was generated where it was not installed; this one in the Dev Container, where it is. The reader is unchanged. | None: generate the reports in the Dev Container. |
| No model route has run against a real provider | No API key on any environment; decision D2 (provider data terms and hosting) is open. | Sponsor: decide D2. Then run the routes on the golden set, measure cost per tender by route, and only then compare models. |
| `firebid-eval compare-models` does not call the route's models | It scores a stand-in predictor: it proves the harness, not a model. | Give it a route-backed predictor once a provider key exists. |
| No golden set for Phase 2: equipment, pricing, labour and clarifications are measured on synthetic tenders | Decision D3's historical tenders have not been collected. | Collect them; import under `eval/truth`; re-run the suites and this report. |
| The clarification acceptance measure starts with clarifications drafted from P2-09 on | The drafted wording is recorded from this step. A clarification drafted earlier has no draft on record and is counted as issued, not measured. | None needed: the pilot's clarifications will all be measured. |
| Turnaround is measured from the day the bid is opened on the platform | The bid has no separate date for when the tender documents were received. Where a bid is opened late, the measured turnaround is shorter than the true one. | Pilot runbook: open the bid on the day the tender arrives. If that proves unreliable, add a received date to the bid. |
| The baseline turnaround is not recorded | Business track: working days per tender before the platform have not been measured. | Estimating Manager: record it in `config/kpi.yaml`. |
| The load test opens the Phase 2 pages on bids that are taken off but not yet priced | It measures the pages under ten bids being parsed, not a full estimate on each. The scale of a full estimate is measured in process (`make p2-benchmark`: 5,000 bill lines). | Extend the load test to price and draft on each bid once the pilot shows typical sizes. |
| Accepted risk allowances are shown beside the estimate, not in it | P2-07 and P2-08 left the cost build-up without a risk component. | Product owner: decide whether an accepted allowance becomes a build-up component. |
| The priced client workbook is not among the frozen submission's files | P2-08 freezes the company bill, the review pack, the qualifications and the clarification register. | Add the priced client workbook to the snapshot before the pilot's first submission. |
| Decision D5 (named approvers for the gates) is open | The gates are approved by role, from the permission matrix. | Sponsor: name the approvers, or confirm that the role is enough. |
| Business figures are placeholders | `pricing.yaml`, `labour.yaml`, `clarifications.yaml`, `risk.yaml`, `scope_matrix.yaml`, `kpi.yaml` and `retention.yaml` carry values marked to be confirmed. | Estimating Manager and Commercial Director: confirm each before the pilot. |
| The accepted baseline for the Phase 2 systems suite was approved by the build step | `eval/baselines/p2_systems.json` names P2-01 as its approver. | Product owner: review and accept it under their own name. |

## Recommendation

**Not ready to pass the gate; ready for the assisted-mode pilot.** The platform does what Phase 2 asked of it, and every Phase 2 requirement has a test: the estimate is priced with a source on every price (the database refuses one without), labour is estimated, clarifications, risks and qualifications are drafted for people to decide, the review pack and gates G3 and G4 work, and the submission is frozen and verifiable. The Phase 1 suites show no regression and the non-functional targets hold with the Phase 2 workload. But none of the three exit criteria can be judged: they are measures of live tenders, and no pilot has run and no turnaround baseline exists. Recommended: (1) fix CI billing and run the full pipeline on main; (2) decide D2 so the model routes can be measured and costed; (3) record the turnaround baseline and confirm the placeholder figures; (4) get a real design-intent tender from upload to a layout on the stack (its title blocks and plan views are not yet read); (5) run the assisted-mode pilot on at least three live tenders and regenerate this report with `make exit-report-p2 LIVE=1`. Hold the gate review until that report shows the three criteria measured.
