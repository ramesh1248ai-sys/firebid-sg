# ADR-011: Design development as an estimating aid, and document origin at intake

- **Status:** Proposed; built in P1-12 (1 Oct 2026)
- **Date:** 2026-10-01
- **Deciders:** Tech Lead, Design Manager; Executive Sponsor for the scope change (it adds requirements that are not in the approved specification)
- **Requirements:** FR-DSN-01 to 06 and FR-DOC-09, 10 (proposed, requirements §6.17); guardrails 1, 2, 3 and 6; requirements §1.3 and §2.1 (the platform is not a design or approval system)

## Context

The MOH (TTSH) tender is drawn as design intent. Its 148 fire protection plans show the sprinkler mains and branch stubs, and their notes leave the rest to the contractor: *"the contractor shall be responsible for the further development and detailed design"*. No sprinkler head is drawn. A takeoff of what is drawn counts zero heads and none of the range pipe, which is most of the sprinkler cost. SJ M&E's estimators answered this tender by developing a layout themselves (their response workbook, sheets 14, 15 and 17) and counting from that.

Two things in the requirements pull against building this:

- **§1.3 and §2.1:** the platform is AI-assisted estimating, not a fire-safety design or approval system. The Qualified Person owns the design.
- **Guardrail 1:** every quantity comes from geometry, extracted text or a versioned rule, with an evidence record.

The same tender's folder shows a second problem. It mixes the client's tender set with the company's own marked-up copies of the same drawings (same drawing numbers, an `_SJME-B` suffix) and its earlier responses. Read together, a marked-up copy can become the Current revision of the client's drawing, and a response workbook can be read as a client BOQ.

## Options

1. **Do nothing; estimators allow for heads by hand.** No new scope. The platform then reports a takeoff it knows is missing most of the cost on this kind of tender, and the largest quantity on the bid has no evidence record.
2. **Count heads by area only** (floor area ÷ area per head, metres of pipe per head). Simple and auditable. It needs the floor area per sheet anyway, gives nothing to look at on the drawing, and cannot respect rooms, omissions or the drawn mains.
3. **Propose a layout from versioned rules, as an estimating aid.** Find the spaces of each plan, place heads and range pipes by a rule set a senior estimator owns, and take off from the proposal. More to build; the result can be seen, checked and corrected on the drawing.
4. **Have a model draw the layout.** Rejected: it breaks guardrail 3 (model output never supplies final numbers) and is not reproducible.

## Recommendation

**Option 3, bounded so that it stays an estimating aid:**

- **Deterministic.** Spaces come from the sheet's own linework; heads and pipes come from a versioned rule (`sprinkler_layout`, FR-ADM-02) and the criterion the tender's notes state. No model is involved. The same sheet, criterion and rule version give the same proposal (guardrail 3).
- **Nothing without a person.** The criteria read from a sheet's notes are proposals. No layout is made until a Senior Estimator or Design Manager confirms the criterion for the sheet (new permission `design_basis.confirm`). The proposed heads and pipes are then proposals in their turn, reviewed on the workbench like any detection (guardrail 2).
- **Kept apart from what is drawn.** Proposed quantities are QTO items of their own, marked "proposed layout, not drawn", each a rule-derived quantity with the rule, its version, the criterion and its source in the evidence record (guardrail 1). They are never added into a drawn quantity.
- **Refused where it cannot be right.** A sheet with no plan view at a verified or calibrated scale is blocked (FR-VIS-05). A sheet with heads already drawn is flagged, so a layout is not counted on top of them.
- **Not a design.** The output is not submitted to anyone, carries no compliance statement, and does no hydraulic calculation. The seeded rule values are marked "to be confirmed". Any export of the layout (FR-DSN-05, not built) must be stamped "For estimation only: not for construction".

**For the folder:** every document carries an **origin**: tender, working or reference. It is proposed from the file's path by rules in `config/intake.yaml` and set by the person sending the folder. Only a tender document whose origin is confirmed is read; the others are stored with the bid, unread. A file proposed as other than tender, where nobody was asked (inside an archive, or sent without an origin), waits for a person.

## Consequences

- **Easier:** a design-intent tender gets a takeoff with evidence for its largest quantities. A whole folder can be sent without the company's own drawings entering the registers.
- **Harder:**
  - The layout is only as good as the space finding, which reads a base plan drawn in greys under services in colour. A plan drawn otherwise finds no spaces, and says so.
  - The seeded design rules follow one tender response as SJ M&E read SS CP 52. They must be confirmed by a senior estimator before the quantities are relied on, and the Design page says so until they are.
  - Sheets that share a floor across match lines are each laid out whole. Until FR-DSN-06 is built, heads in the shared part are counted on both sheets unless the existing duplicate detection catches them. On the MOH L10 sheet this is about 30% of the heads.
- **Follow-up:**
  - Carry §6.17 into the approved `.docx`, or strike it, at the next requirements review.
  - FR-DSN-05 (export) and FR-DSN-06 (match-line scope) in Phase 2.
  - Fittings on proposed range pipe (tees and elbows at each head) are not derived yet.
  - A RAR archive is refused with a reason. Opening one needs a decoder added to the sandbox image.
