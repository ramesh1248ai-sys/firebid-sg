# P2-05 · Labour Estimation

**Builds on:** P2-04 (cost build-up), P1-07 (QTO items with DN, joining, level, height context). **Needs:** the company productivity standards and labour rate tables (wages, foreign worker levy, accommodation, transport, insurance) (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrail 3, configuration over constants.
- Requirements: §6.12 (FR-LAB-01, 02, 03), §6.13 (FR-RSK-03: execution risks that inform multipliers).
- `docs/plan/BUILD_LOG.md`.

## Goal

Estimate labour transparently. Baseline productivity from a sourced library, visible multipliers for site conditions, and a labour rate built up from company tables produce labour lines where every factor can be traced and challenged.

## Scope

FR-LAB-01, FR-LAB-02, FR-LAB-03.

## Build

1. **Productivity library** (FR-LAB-01), versioned:
   - Units: man-hours per metre by DN and joining method, per sprinkler by type, per valve assembly, per equipment item.
   - Each entry has a source: company standard, historical project (with project reference) or estimator judgement (with name).
   - xlsx import.
2. **Multipliers** (FR-LAB-02), a configurable catalogue:
   - installation height bands;
   - access difficulty;
   - MEP congestion;
   - basement, occupied or live building, night work;
   - high-rise logistics.

   Each has a value, source and rationale. Multipliers apply per zone or level from bid parameters and risk flags, with estimator confirmation. The labour line shows baseline hours and each applied multiplier separately.
3. **Labour rate build-up** (FR-LAB-03): per trade and grade from company tables with effective dates: wages, foreign worker levy, accommodation, transport, insurance (e.g. WICA), overtime policy, supervision ratio. The resulting hourly cost feeds the P2-04 cost build-up labour line. Statutory and commercial values are configuration, and the UI shows their effective date.
4. **Outputs:** labour hours and cost per BOQ line and per system, plus a summary by trade.

## Done when

- Labour hours for a crafted QTO set match a hand calculation of baseline × multipliers, with each factor visible in the API and UI. Tagged FR-LAB-01 and FR-LAB-02.
- Every productivity entry and multiplier shows its source, and a test rejects one without a source.
- The hourly rate build-up matches a hand calculation from table values, and changing an effective-dated value affects only bids priced after it. Tagged FR-LAB-03.
- The labour line appears in the P2-04 cost build-up with its basis.
- A build log entry is appended.
