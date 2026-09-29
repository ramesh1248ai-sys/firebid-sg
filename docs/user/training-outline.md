# FireBid SG: one-day estimator training

**For:** estimators and senior estimators joining the Phase 1 pilot.
**Length:** one day (09:00–17:00), hands-on.
**Setup:** each person has a staging account and a laptop. The trainer uses the synthetic tender from `firebid-eval`, never a live tender, so mistakes cost nothing.
**Pre-reading:** [estimator-quick-start.md](estimator-quick-start.md).
**By the end, each person can:** take a tender from upload to a priced, approved BOQ; explain where every quantity came from; and recognise when not to trust the AI.

| Time | Session | Content | Hands-on |
|---|---|---|---|
| 09:00 | **Why and how** (30 min) | What FireBid does and does not do. People decide; the AI proposes. Gates G1 and G2. Confidentiality: who sees a bid. | Sign in; find the training bid. |
| 09:30 | **A bid and its documents** (45 min) | Creating a bid; deadline and consultant. Uploading a full set; the malware scan and *awaiting scan*; background reading. Registers: drawing list against upload, revisions, missing sheets. | Create a bid and upload the synthetic set. Find the missing sheet the trainer planted. |
| 10:15 | Break | | |
| 10:30 | **Symbols** (60 min) | Legends and how symbols are recognised. Reused against new mappings. Checking a proposal against the legend. Unnamed symbols: when to name one. Why an unconfirmed symbol never counts. | Confirm the legend. Correct one wrong proposal. Name one unlisted symbol and watch the counts update. |
| 11:30 | **The workbench** (90 min) | The review queue: evidence on the drawing, confidence, grid reference. Accepting, and rejecting with a reason. Manual takeoff for what was missed. Duplicate groups: enlarged plans, sections, match lines. | Verify a full sheet. Resolve the planted duplicates. Add two missed items. |
| 13:00 | Lunch | | |
| 13:45 | **Passing G1** (30 min) | Coverage; what blocks G1; the senior estimator's sign-off. What the audit log records. | Clear everything blocking G1. A senior participant passes it. |
| 14:15 | **BOQ and pricing** (75 min) | The BOQ built from verified quantities; traceability; provisional and lump sums. Reconciliation against the client's BOQ. The rate library: exact against proposed matches, stale and expiring rates, unpriced lines. | Reconcile against the client BOQ. Confirm proposed rates. Find the unpriced line and resolve it. |
| 15:30 | Break | | |
| 15:45 | **When things go wrong** (45 min) | An AI provider outage: fallback and hand-over. A file that will not read. A mapping confirmed wrongly: correcting it, and how detection reruns. Who to call, and saying "due in 48 hours" first. | The trainer switches the model off in staging. Carry on by hand. |
| 16:30 | **Pilot practice and questions** (30 min) | The shadow pilot: run FireBid alongside your usual takeoff on live tenders, and record time on task. The KPI page. How feedback reaches the team. | Each person states one thing they would still check by hand, and why. |

**Assessment (informal):** each participant completes the training bid to G1 without help. The trainer notes where people hesitated; those notes feed the quick-start guide.

**Trainer checklist (the day before):**

- Staging is deployed, `/health` is OK, and the deployment guard shows no conflict for the training day.
- The synthetic tender is loaded with its planted faults: a missing sheet, two duplicates, a wrong proposal, an unlisted symbol and an unpriced line.
- Accounts exist with the right roles (estimator, senior estimator).
- The model can be switched to its fallback for the 15:45 session.
