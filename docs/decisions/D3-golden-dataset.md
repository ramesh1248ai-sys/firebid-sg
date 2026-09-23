# D3 · Golden dataset and data owner

- **Status:** Open. Tooling ready 23 September 2026; the data and the owner are outstanding.
- **Decision owner:** Estimating Manager, with the Executive Sponsor naming the data owner.
- **Requirements:** §0.3 D3, §13.2 (exit criteria), §14 (KPIs), assumption A2, FR-LRN-01, NFR-11.
- **Blocks:** the Phase 0 gate, and every accuracy claim from P1-02 onward.

## What D3 asks for

Requirement §0.3 states it as: *provide the golden dataset and nominate a data owner.*

That is two separate things, and only one of them needs a meeting:

1. **Name a data owner.** One person accountable for what enters the set, for its
   confidentiality, and for deciding when a tender is verified. A decision, not a workload.
2. **Collect 10–20 historical tenders with verified takeoffs.** Assumption A2 says the company
   can. This is the workload, and it runs over roughly six weeks.

## Why this is the piece that cannot be worked around

Every accuracy number in §14 — sprinkler count accuracy ≥98%, pipe length ±5%, missed items
≤5% — is measured *against this set*. Without it the platform can be tested for correctness
but not for accuracy, and "the AI found 124 sprinklers" is an assertion with nothing behind it.

The synthetic fixtures built in P0-05 prove the arithmetic and the regression gate work. They
cannot tell you whether the platform reads *your consultants' drawings*. Only real tenders do
that, which is why A2 exists and why this is a Phase 0 exit criterion rather than a Phase 1 one.

## What is ready now

- **The estimator workbook.** `make golden-template` writes `eval/templates/golden_takeoff.xlsx`:
  one tab per concept, dropdown lists for everything with a fixed vocabulary, and an
  instructions tab that says what "verified" means and what to do when the honest answer is
  "I don't know".
- **The importer.** `firebid-eval import <workbook.xlsx>` validates a filled workbook and
  reports every problem at once, by tab, row and column. It writes nothing until the workbook
  is clean, because a partial import looks like it worked.
- **The format.** `firebid/evals/schema.py`. Git holds manifests and checksums; the drawings
  live in the eval bucket and never enter the repository (guardrail 8).

## What to collect

Aim for **10–20 tenders**, chosen for spread rather than convenience. The report slices every
metric by input class and by consultant, so a set drawn from one consultant and one drawing
tool will produce confident numbers that do not survive contact with the next client.

| Dimension | Why it matters | Target |
|---|---|---|
| **Input class** | Accuracy differs sharply between vector and scanned drawings. A 98% average hiding 60% on scans is a dangerous number | At least 3 predominantly scanned sets |
| **Consultant** | Symbol conventions and title-block layouts vary by practice | At least 4 different consultants |
| **Revisions** | Revision handling is itself measured | At least 3 tenders with a superseded revision in the set |
| **Repeated content** | Enlarged plans repeating a general arrangement are a real double-counting risk | At least 3 tenders with enlarged plans |
| **Size** | Small sets are unrepresentative of a 300-sheet tender | A mix, including at least 2 over 100 sheets |
| **Outcome** | Won and lost bids differ in how carefully they were priced | Include both |

For each tender: the drawing set as received, the verified takeoff in the workbook, and the
client BOQ as priced.

## What "verified" has to mean

This is the part worth the data owner's attention, because it decides whether the numbers mean
anything.

A figure is verified if the estimator would defend it in a handover. A figure recalled,
estimated, or carried over from an earlier revision is **not** verified, and the honest move is
to leave the row out. The workbook's instructions say this, and the importer treats an empty
cell as "not recorded" and excludes it from scoring.

The failure mode to guard against is not a missing number. It is a plausible wrong one: it is
scored as truth, the platform is marked down for being right, and nobody can explain the
result six months later.

## Confidentiality

Tender documents belong to the issuer and are held under that tender's own terms (§11.4).

- Drawings go to the **eval bucket**, never into git. Git holds manifests and checksums only.
- If a tender's terms forbid retaining the documents after the bid, **say so on the Tender tab
  and send the workbook alone.** A takeoff without the drawings is still useful for BOQ mapping
  and revision metrics; a breach of a client's terms is not worth a percentage point.
- The workbook itself carries no client drawings, only counts and sheet numbers.

**Open question for Legal:** whether retaining a past client's drawings for model evaluation
is permitted under the confidentiality terms those tenders were issued under. This is the same
question as Q8 in §11.4 and it applies here first, because collection starts before Phase 1
does. If the answer is no for some clients, the set shrinks and the spread targets above
matter more, not less.

## What the data owner is accountable for

1. Deciding which tenders enter the set, against the spread above.
2. Confirming each takeoff is verified in the sense defined here.
3. Holding the drawings in the eval bucket under the right terms.
4. Signing the set off as the baseline, after which changes are tracked rather than silent.

## Suggested sequence

| When | What | Who |
|---|---|---|
| Week 1 | Name the data owner. Ask Legal the retention question above | Sponsor |
| Week 1 | Generate the workbook, walk one estimator through a single tender end to end | Data owner + one estimator |
| Week 2 | Review that first tender together. Fix the template if anything was ambiguous — it is far cheaper now than after fifteen | Data owner |
| Weeks 2–6 | Collect the rest, importing each as it arrives rather than in a batch at the end | Estimators |
| Week 6 | Sign off the set as the baseline | Data owner |

The first tender is the one that matters. If it takes an estimator three hours and produces
arguments about what counts as verified, that is the template's problem to fix, not theirs.

## What this does not decide

The **golden set for Phase 3 and 4 metrics** — citation accuracy, coordination precision,
estimate variance — needs different data (awarded projects with actual costs) and is out of
scope here. D3 covers the Phase 1 KPIs only.
