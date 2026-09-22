# ADR-004: LLM provider strategy and data residency

- **Status:** Proposed; pending sponsor decision D2 and legal review
- **Date:** 2026-09-22
- **Deciders:** Executive Sponsor (D2), Tech Lead, Legal, IT Security
- **Requirements:** §12.2, §11.4; NFR-05, NFR-08, NFR-11, NFR-15; risk R13

## Context

The platform uses multiple LLM providers, chosen per task by configuration (requirements §12.2). Tender documents, quantities and prices are confidential, and personal data falls under the PDPA. A provider may receive a class of data only if its region, retention and no-training terms are acceptable for that class.

## Options

1. **In-house thin adapters over each provider's official SDK**, behind a provider-neutral gateway. Full access to each provider's features (structured outputs, documents, caching, batches, citations), and data-class enforcement stays under our control. We maintain the adapters.
2. **A third-party multi-provider library.** Faster to start and broad, but features lag behind providers, it adds a dependency on the hot path, and enforcement depends on the library's behaviour.
3. **A single provider.** Simplest, but creates lock-in (R13) and no failover.

## Recommendation

**Option 1.** Callers name a route; `backend/config/llm.yaml` maps each route to a main model and ordered fallbacks. At startup the gateway rejects configurations that break capability or data-class rules, and at runtime it refuses any attempt, including fallbacks, to send data to a provider not approved for the route's class (built in step P0-04).

At launch every route uses **Anthropic `claude-opus-5`**. Other providers are enabled per route only after legal approval and `firebid-eval compare-models` evidence.

### Candidate providers and access paths

| Provider / access path | Region for our data | Retention | No-training terms | Approved data classes |
|---|---|---|---|---|
| Anthropic: Claude API | To confirm | To confirm | To confirm | To be decided (D2) |
| Anthropic: via Amazon Bedrock | To confirm | To confirm | To confirm | To be decided (D2) |
| Anthropic: via Google Vertex AI | To confirm | To confirm | To confirm | To be decided (D2) |
| Anthropic: via Microsoft Foundry | To confirm | To confirm | To confirm | To be decided (D2) |
| OpenAI API / Azure OpenAI | To confirm | To confirm | To confirm | To be decided (D2) |
| Google Gemini (Gemini API / Vertex AI) | To confirm | To confirm | To confirm | To be decided (D2) |
| Self-hosted open-weight models | Our own hosting | Ours | N/A | Candidate for all classes |

Data classes: `internal`, `confidential` (tender documents, drawings, quantities), `commercial` (prices, quotations, margins), `personal` (PDPA). Legal and IT Security complete this table. The Executive Sponsor approves it as decision D2. Providers hosted in Singapore or the region, with zero-retention terms where available, are preferred for confidential and commercial data.

## Consequences

- Changing a route's provider is a configuration change plus evidence, not a code change.
- We maintain one adapter per provider, covered by a shared contract test suite.
- Until D2 is decided, only development and fake adapters receive confidential or commercial data.
