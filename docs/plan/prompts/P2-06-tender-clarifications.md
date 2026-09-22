# P2-06 · Tender Clarifications Register

**Builds on:** P2-03 (spec issues), P1-09 (BOQ variances), P2-02 (addendum propagation, delta QTO), P0-04 (agent contract). **Needs:** client clarification templates in common use (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 2, 5 and 7, *proposal*, *evidence record*.
- Requirements: §6.10 (FR-RFI-01, 02, 04, 05, 06, 07), §7 (clarification model), §3 (RACI: issue clarifications, engineering options).
- `docs/plan/BUILD_LOG.md`.

## Goal

Turn flagged issues into clear, evidence-backed tender clarifications. Route engineering content to the right approver, track every question against the clarification cut-off, and fold responses back into quantities and prices. Anything unresolved at submission becomes a proposed qualification.

## Scope

FR-RFI-01, 02, 04, 05, 06, 07.

## Build

1. **Types** (FR-RFI-01): tender clarifications only. The data model reserves a type for construction RFIs, which are out of scope, and the UI offers tender clarifications only.
2. **State machine:** add the clarification model from requirements §7 to `domain/`, with guards:
   - "Approved to issue" requires Bid Manager approval.
   - Clarifications flagged with engineering, fire-safety or structural content also require Design Manager approval, with an optional "QP input needed" flag (FR-RFI-06).
3. **Drafting agent** (FR-RFI-02):
   - Inputs are candidates from spec issues, BOQ variances, missing-information flags and scope-matrix "unclear" rows.
   - The draft contains: number, subject, project, level/grid, sheet and revision, problem description, evidence references, options, potential cost and programme impact, required reviewer.
   - A draft without at least one evidence reference is rejected by output validation, never saved.
   - Options are labelled as recommendations.
4. **Grouping and templates** (FR-RFI-07): the agent proposes groups of related candidates for one clarification; the user confirms. Export in the client's template (docx or xlsx) or the company default. Output leaves the platform only as a user-initiated download.
5. **Register** (FR-RFI-04):
   - Due dates computed from the clarification cut-off, with status per the state machine.
   - Response upload linked to its clarification.
   - An impact assessment task on response: re-run delta QTO or pricing where affected, and record the outcome (incorporated or no change).
6. **Conversion to qualifications** (FR-RFI-05): at submission preparation, each unresolved clarification becomes a proposed qualification or assumption for review, linked back to it.

## Done when

- A seeded spec conflict and a BOQ variance each produce a draft with every listed field and at least one evidence reference. A draft attempt without evidence is rejected. Tagged FR-RFI-02.
- State machine tests: an engineering-flagged clarification cannot reach "Approved to issue" without Design Manager approval; a plain one needs only the Bid Manager. Tagged FR-RFI-06.
- Due dates follow the cut-off; an uploaded response links back and triggers an impact task. Tagged FR-RFI-04.
- Grouping merges related candidates after confirmation, and export matches the chosen template. A test confirms the only exit path is a user download. Tagged FR-RFI-07.
- Unresolved clarifications appear as proposed qualifications at submission preparation. Tagged FR-RFI-05.
- The data model and UI support tender clarifications only. Tagged FR-RFI-01.
- A build log entry is appended.
