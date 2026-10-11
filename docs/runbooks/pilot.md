# Runbook: the assisted-mode pilot

**What it is for.** Three live tenders taken through FireBid SG by their own team, beside
the way they are done today. It is the only way the three Phase 2 exit criteria can be
measured: estimate turnaround down 30%, no unsourced price, and at least 70% of
clarifications issued with only minor edits.

**Who.** The bid manager runs each tender. The platform engineer is on call for the first
upload of each. The product owner collects the findings.

**Assisted mode means** the estimate the client receives is the team's own. FireBid SG's
quantities and prices are checked against it, never sent in its place, until the gate
review says otherwise.

Related: [the user manual](../user-manual/README.md), `docs/plan/COMPREHENSIVE_PLAN.md`
(stream P), `docs/reports/phase2-gaps.yaml`.

## Before the first tender

Do not start until every line here is true. A pilot run on placeholder figures measures
nothing.

| Check | Who | How to tell |
| --- | --- | --- |
| The turnaround baseline is recorded | Estimating Manager | `baseline_turnaround_working_days` in `backend/config/kpi.yaml` is a number, not `null` |
| The business figures are the company's | Estimating Manager, Commercial Director | No value marked "to be confirmed" in `pricing.yaml`, `labour.yaml`, `clarifications.yaml`, `risk.yaml`, `scope_matrix.yaml`, `kpi.yaml`, `retention.yaml` |
| The rate library is the company's | Senior Estimator | **Rates** lists the company's rates, each with a real source and validity; no synthetic entry (`CS-2026`, `Q-2026-…`) |
| The productivity library is the company's | Senior Estimator | **Rates → Productivity library** likewise; no `PS-2026` |
| Everyone on the pilot has signed in once | Each person | They are offered under **Team → Person** on a bid |
| The gate approvers are named | Sponsor | Who approves G1 and G2 (a Senior Estimator) and G0, G3 and G4 (the Commercial Director) for each pilot tender is written down |
| Submission snapshots are locked | Platform engineer | The snapshot bucket has its retention lock, and a test write to an existing key is refused |
| The team has been trained | Bid Manager | Each has followed [a tender from upload to award](../user-manual/happy-path.md) on the training bid |

## For each tender

### Day the tender arrives

Turnaround is measured from the day the bid is opened on the platform, so open it the day
the documents arrive, not the day work starts.

1. **Register the bid** with its submission deadline, clarification cut-off and tender
   validity, and the design consultant's name exactly as on earlier tenders: confirmed
   symbols are reused by that name.
2. **Build the team** under **Team** on the bid page.
3. **Start qualification**, and have the Commercial Director record the decision to bid.
4. **Upload the whole tender set**, the client's bill included. Use **Or send a whole
   folder** for a set kept in folders, and check what each folder is taken as before
   sending.
5. **Read the list of files that need attention.** A refused drawing is a drawing that is
   not in the takeoff. Note each one as a finding.

### Before anything is counted

1. **Registers.** Every drawing has one current revision; anything marked **To identify**
   or **Conflict** is settled.
2. **Scales.** A plan whose scale nothing proves is not measured. On the MOH set six plans
   were in this state. For each such sheet, open it, and under **Manual takeoff** calibrate
   the view on a length that is known (a gridline spacing, a dimensioned bay). One
   calibration serves every sheet that shares those gridlines.
3. **Symbols.** Work through the legend on the **Symbols** page. Then open the workbench's
   **Symbols & scale** tab and name the large groups nobody has named, once: the mapping is
   remembered for the consultant.
   - A shape on every sheet that is not an object (a title-block box, a north point) is
     named **Not an installed object**.
   - Whether pipe fittings drawn as symbols are counted is the Senior Estimator's decision,
     made once and written here: ____________________.
   - Read the line "Not read as symbols" on the Symbols page. If the tender prints its
     services in grey, they will be among what was set aside: stop and tell the platform
     engineer.
4. **Design intent.** Where the drawings show mains only, use **Design development** and
   have the criterion confirmed before a layout is proposed.

### The estimate

Follow the manual. Three things matter for the pilot's measures:

- **Do not price outside the library.** A line with no rate stays unpriced until a rate is
  in the library with its source. This is what "no unsourced price" measures.
- **Draft clarifications on the platform and edit them there.** The measure compares what
  was drafted with what was issued; a question rewritten in an email is not measured.
- **Record every gate where it is approved**, on the day it is approved.

### Submission and after

1. The Commercial Director approves G4. The frozen files include the client's own bill,
   priced. Download them; the platform sends nothing.
2. Compare the frozen estimate with the team's own before anything is sent, line by line
   for the ten largest lines. Record every difference and its cause as a finding.
3. When the result is known, the bid manager records the outcome. Do not name individuals
   in the reasons or the competitor feedback.

## Recording what is found

A finding is anything where the platform was wrong, slow, unclear, or had to be worked
around. Each goes into `docs/reports/phase2-gaps.yaml` under `pilot_findings`, one line
each, with the bid number and the stage. Findings about counts go to the product owner the
same day: they decide whether the next tender starts.

| Kind | Example | Goes to |
| --- | --- | --- |
| A count or length that differs from the team's | 346 heads counted, 372 on the platform | Product owner, same day |
| A step that needed the platform engineer | A sheet that would not read | Platform engineer, then the list |
| A screen that was not understood | A blocked gate nobody could clear | The manual, then the list |
| Time lost | A re-read that took an hour | The list |

## After the third tender

1. Regenerate the exit report with every bid's measures:

   ```sh
   make exit-report-p2 LIVE=1
   ```

2. Check that it shows each of the three criteria as measured, not "not measurable".
3. Hold the gate review on that report.

## Stopping the pilot

Stop, and tell the sponsor, if any of these happens:

- a count on the platform is wrong in a way the workbench review did not catch;
- a price reached an estimate with no source;
- a frozen submission does not verify;
- someone saw a bid they are not on.

The team's own estimate is unaffected by stopping: that is what assisted mode is for.
