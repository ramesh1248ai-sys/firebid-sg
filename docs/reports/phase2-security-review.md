# Phase 2 security and privacy review

Reviewed 4 Oct 2026 (P2-09), by reading the code and tests of the surfaces Phase 2 added:
supplier quotations, bid outcomes and submission snapshots. The summary and the open findings
are repeated in the exit report from `phase2-gaps.yaml`.

## Scans

Run in the Dev Container on 4 Oct 2026 (`make security` runs all of them):

| Scan | Result |
|---|---|
| `pip-audit --strict` on the backend's locked dependencies | no known vulnerabilities |
| `npm audit --audit-level=high` on the frontend | 0 vulnerabilities |
| `bandit` on `backend/src`, high severity, medium confidence and above | no findings |
| semgrep, gitleaks, trivy | **not run here**: they run in CI, which is not running (see the gap list) |

## Supplier quotations (FR-CST-02)

| Question | Finding |
|---|---|
| Is an outside file opened before it is scanned? | No. `services/quotations.capture` stores the file, scans it, and reads it only when the scan allows it; an infected file and a scanner outage are both refused. It is read in the parser sandbox (guardrail 9). |
| Who can see a quotation? | Members of its bid: the tables carry `bid_id` and row-level security (`test_no_table_with_a_bid_id_is_left_without_a_policy`). |
| Does a price or a supplier's words reach the logs? | No. The only log line names the bid, the file's hash and the scan verdict. |
| What goes to a model? | Only on the `quotation_extract` route, data class `commercial`, and only when a person asks. No provider key is configured and provider data terms are unconfirmed (decision D2), so nothing has been sent. |
| Personal data (PDPA) | `quotation.extraction` keeps the lines of the file, which can name a sender or a sales contact. It is in the data inventory. New in P2-09: once a bid is lost, withdrawn or no-bid and 365 days have passed, the kept lines are cleared (`retention.yaml`, `quotation_source_lines_after_days`); the fields read from them and the file stay. |

## Outcomes (FR-LRN-02)

| Question | Finding |
|---|---|
| Who can record one? | A named member of the bid whose role the bid's lifecycle allows to make the move (awarded, lost, withdrawn). The outcome itself cannot be changed once recorded. |
| Who can see the report across bids? | It is read under row-level security: a person sees the bids they are on. |
| Personal data | `competitor_feedback` and `reasons` are free text and may name people. `competitor_feedback` is in the data inventory (added in P2-09). |

## Submission snapshots (FR-PKG-03)

| Question | Finding |
|---|---|
| Can a snapshot be altered? | The row is append-only (a trigger refuses update and delete). Every file and the manifest carry a content hash, and `verify_snapshot` checks each one. |
| Can the stored files be altered? | Where a snapshot bucket is configured, each object is written under a compliance-mode lock. **Locally there is none**, and the lock has never been exercised against a real object store. |
| Who can download the files? | Members of the bid; each download is audited. Nothing is sent anywhere by the platform. |
| Personal data | The manifest names the approvers of the gates. That is the record the snapshot exists to keep. |

## KPI instrumentation (P2-09)

| Question | Finding |
|---|---|
| What is stored? | The words of each clarification as drafted, in `clarification.drafting`, beside the clarification itself: the same data, the same bid, the same row-level security. |
| What does `/kpis/phase2` show? | Counts and shares for the bids the caller can see. No price, no text. |
| The exit report with `LIVE=1` | Read on the service role across every bid, it lists each bid's reference with its counts. Run it only for an audience that may see every bid. |

## Findings

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | An outcome's free text (`reasons`, `competitor_feedback`) is copied into the audit event, which is append-only and kept seven years. A name written there cannot be erased. | Medium | Open: tell people on the page not to name individuals; decide with the DPO whether the audit event should hold the text or only that it changed. |
| 2 | Without `FIREBID_S3_SNAPSHOT_BUCKET`, snapshots are written to the ordinary bucket with no object lock. Tampering is still detected by the hashes, not prevented. | Medium | Open: set the locked bucket in every deployed environment and refuse to start production without it; exercise the lock on the real store. |
| 3 | Quotation file lines were kept for ever. | Medium | Closed in P2-09 by the retention rule above. |
| 4 | `competitor_feedback` was missing from the data inventory. | Low | Closed in P2-09. |
| 5 | The retention periods are placeholders (`status: to be confirmed`). | Low | Open: confirm against the company records policy. |
| 6 | semgrep, gitleaks and trivy have not run on the Phase 2 code. | Low | Open: run `make security` where Docker can mount the repository, or restore CI. |

No high or critical finding is open.
