# P1-06 · Specification Attribute Extraction

**Builds on:** P1-02 (specifications classified and registered), P0-04 (gateway, structured outputs, proposals). **Needs:** a real fire protection specification from the golden set to check against. The synthetic spec this step creates covers the tests.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: *proposal*, *evidence record*, AI usage.
- Requirements: §6.7 (FR-SPEC-01, FR-SPEC-05), §6.4 (FR-QTO-01, 02: attributes the QTO needs).
- `docs/plan/BUILD_LOG.md`.

## Goal

Extract from the fire protection specification the attributes the takeoff needs: pipe material, schedule or class, joining method by size range, and sprinkler types. Each attribute carries a citation that resolves to the clause text, so the QTO engine can fill item attributes with traceable defaults.

## Scope

FR-SPEC-01, FR-SPEC-05.

## Build

1. **Clause tree:**
   - Parse DOCX using heading styles and list numbering, and layout-aware PDF using numbering patterns and font hierarchy.
   - Produce sections, clause numbers, headings, text, and page/paragraph anchors.
   - Store it per specification revision.
2. **Section finder:** identify fire protection sections (sprinkler, fire protection pipework, hydrant, hose reel, fire pumps) with rules, and fall back to the model for unusual headings.
3. **Attribute extraction:** use a structured output schema with the clause reference required on every field. Extract:
   - pipe material and schedule or class per system and DN range;
   - joining method per DN range (e.g. threaded ≤ 50 mm, grooved ≥ 65 mm);
   - sprinkler type, temperature rating, K-factor, response and finish;
   - approved makes, stored for later phases.

   Mark the specification prefix with the gateway's cache hint, which applies where the route's provider supports caching.
4. **Citation check:** a deterministic check confirms that each cited clause exists in the clause tree and contains supporting text (normalised match of key tokens such as "Schedule 40" or "grooved"). A failed check downgrades confidence and flags the attribute for review.
5. **Attribute rules table per bid:** (system, DN range) → attributes with citations. The estimator confirms or edits entries (proposal → verified) in a small UI. The QTO engine will read only verified entries, and fall back to "not specified".
6. **Synthetic specification fixture:** a DOCX spec with known clauses, including size-range rules and one clause that contradicts a drawing note. P2-03 uses the contradiction later.

## Done when

- Every attribute in the synthetic spec fixture is extracted correctly, including DN-range joining rules. Tagged FR-SPEC-01.
- Every extracted attribute has a citation (document, clause, page/paragraph, revision) that resolves to clause text in the UI. Tagged FR-SPEC-05.
- A test with a deliberately wrong citation shows the confidence downgraded and the attribute flagged.
- The QTO-facing query returns only verified attributes, and returns "not specified" where none exist.
- Model calls go through named gateway routes (e.g. `spec_attribute_extract`) declared in `llm.yaml` with their required capabilities and data class. They use structured outputs and the cache hint. AgentRun records show provider, model, prompt version and cost.
- A build log entry is appended.
