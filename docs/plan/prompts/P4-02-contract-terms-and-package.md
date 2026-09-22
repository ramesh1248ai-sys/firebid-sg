# P4-02 · Contract Terms Review and Full Tender Package

**Builds on:** P2-07 (risk register), P2-08 (gates, snapshot), P1-06 (clause trees). **Needs:** company risk-appetite thresholds and standard submission forms (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 3 and 7, *evidence record*.
- Requirements: §6.13 (FR-RSK-07), §6.14 (FR-PKG-04), §5 (S7, S8).
- `docs/plan/BUILD_LOG.md`.

## Goal

Surface the commercial exposure buried in contract conditions, measured against the company's risk appetite. Assemble the complete submission package, ready to download after G4 and frozen with the snapshot.

## Scope

FR-RSK-07, FR-PKG-04.

## Build

1. **Contract terms extraction** (FR-RSK-07):
   - Terms: liquidated damages (rate, cap), retention (percentage, release), performance bond, payment terms, defects liability and maintenance period, design liability, back-to-back terms with the main contractor, price fluctuation, insurance requirements.
   - Each term has a clause citation, using a structured output with citation fields verified against the clause tree.
   - Compare each term with configurable risk-appetite thresholds. Breaches create risks in the P2-07 register with the citation.
2. **Package assembly** (FR-PKG-04):
   - Priced BOQ in the client format.
   - Cost summary.
   - Qualifications, assumptions and exclusions.
   - Clarification log.
   - Deviations list.
   - Technical submittal schedule (products, data sheets, certification references).
   - Company forms from templates.
   - A package manifest.

   The package is generated after G4, included in the snapshot, and delivered only as a user-initiated download.

## Done when

- A contract fixture with seeded terms yields each term with a verified citation, and threshold breaches create cited risks. Tagged FR-RSK-07.
- The package for a synthetic bid contains every listed component, its manifest matches the snapshot hashes, and it is available only after G4 through a download action. Tagged FR-PKG-04.
- A build log entry is appended.
