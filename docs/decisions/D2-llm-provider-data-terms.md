# D2 · LLM provider data terms and approved data classes

- **Status:** In preparation — not yet decided. Evidence gathered 23 September 2026.
- **Decision owner:** Executive Sponsor, on advice from Legal and IT Security.
- **Requirements:** §0.3 D2, §11.4, §12.2; NFR-05, NFR-06, NFR-08; risk R13; ADR-004.
- **Blocks:** step P1-01 (`ai_gateway`). Until D2 is decided, only development and fake
  adapters may receive confidential or commercial data.

## What D2 has to settle

Requirement §0.3 states D2 as: *approve cloud hosting in a Singapore region, and each LLM
provider's data terms (region, retention, no-training), including which data classes each
provider may receive.*

That is three separate approvals, and they are easier to decide apart than together:

1. **Region** — where our data is processed and where it is stored. These are not the same
   thing, and at least one provider offers the second without the first (see OpenAI below).
2. **Terms** — retention period, whether zero retention is available, and a contractual bar on
   training. Published terms are the starting point; the contract is what binds.
3. **Approved data classes per provider** — the output the gateway actually consumes.

Only the third is machine-readable. It becomes `approved_data_classes` in
`backend/config/llm.yaml`, and the gateway refuses any call, *including fallbacks*, to a
provider not approved for the route's class.

## The data classes, in FireBid terms

| Class | What it means here | Examples from this system |
|---|---|---|
| `internal` | Our own operational data, no third-party confidence | Route names, model metrics, symbol libraries, our own productivity rates |
| `confidential` | Third-party tender material we hold under the tender's terms | Tender drawings, specifications, sheet text, extracted quantities, clarification wording |
| `commercial` | Commercially sensitive pricing | Supplier quotations, unit rates, margins, the priced BOQ |
| `personal` | Personal data under the PDPA | Names, emails and phone numbers in title blocks, transmittals, supplier correspondence, our own user records |

`docs/data-inventory.md` lists the seven columns currently holding personal data.

## Findings on published terms

**Read this table as research, not as advice, and not as the contract.** Published terms change,
default API terms differ from negotiated enterprise terms, and the binding position is whatever
the signed agreement and DPA say. Every "verified" row below is a quote from the vendor's own
page on the date shown.

| Provider / path | Trains on our data? | Retention | Region | Status |
|---|---|---|---|---|
| **Anthropic Claude API** | **No.** Commercial Terms: *"Anthropic may not train models on Customer Content from Services."* Privacy docs: does not train on API submissions unless you join the Development Partner Program. | Not stated in the Commercial Terms; they defer to the DPA. **To confirm.** | No Singapore-region processing option documented for the first-party API. **To confirm.** | Verified 23 Sep 2026 (training); retention and region open |
| **OpenAI API** | **No by default.** *"data sent to the OpenAI API is not used to train or improve OpenAI models (unless you explicitly opt in)"* | Abuse-monitoring logs **up to 30 days**. **Zero Data Retention available on approval** for `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings` and others; **not** available for Assistants, Threads or Vector Stores. | **Singapore residency exists** (`sg.api.openai.com`) but it **requires ZDR approval** and is regional **storage only, not regional processing**. | Verified 23 Sep 2026 |
| Anthropic via Amazon Bedrock | To confirm | To confirm | `ap-southeast-1` (Singapore) exists as a Bedrock region; Claude model availability there **to confirm** | Not yet verified |
| Anthropic via Google Vertex AI | To confirm | To confirm | `asia-southeast1` (Singapore) exists; Claude availability there **to confirm** | Not yet verified |
| Anthropic via Microsoft Foundry | To confirm | To confirm | To confirm | Not yet verified |
| Google Gemini (Gemini API vs Vertex AI) | To confirm — the free Gemini API and paid Vertex AI have **materially different** terms; do not conflate them | To confirm | To confirm | Not yet verified — the docs page fetched returned navigation, not policy |
| Azure OpenAI | To confirm — Microsoft states customer data is not used to train, and offers *modified abuse monitoring* to switch off the 30-day human-review retention, but this needs verifying and the opt-out requires approval | To confirm | To confirm | Not yet verified — documentation URL has moved |
| Self-hosted open-weight models | N/A — our infrastructure | Ours | Ours | Candidate for every class, including `personal` |

### The tension worth deciding first

Requirement D2 asks for **a Singapore region**, and at launch ADR-004 puts **every route on
Anthropic `claude-opus-5`**. Those two may not be simultaneously satisfiable on Anthropic's
first-party API, and the OpenAI finding shows the trap clearly: a vendor can offer Singapore
*storage* while still *processing* elsewhere. If the Sponsor reads "Singapore region" as "our
tender data is processed in Singapore", the options narrow to:

- **(a)** Accept non-Singapore processing for `confidential`/`commercial`, relying on
  contractual no-training and zero/short retention. Simplest; keeps `claude-opus-5` everywhere.
- **(b)** Route Claude through **Bedrock `ap-southeast-1`** or **Vertex `asia-southeast1`** to
  get regional processing. Costs a provider path to build and test, and model availability per
  region must be confirmed before committing.
- **(c)** Reserve the strictest classes for self-hosted models. Highest control, materially
  worse extraction quality, and a hosting bill.

**This is the question to put to the Sponsor first**, because it decides whether P1-01 needs one
provider adapter or two.

## Recommended position, for sign-off

Proposed, pending Legal:

1. **Launch with Anthropic only**, approved for `internal`, `confidential` and `commercial`,
   conditional on the DPA confirming retention and the contractual training bar (the training
   bar is already explicit in the published Commercial Terms).
2. **No provider is approved for `personal` at launch.** Personal data is redacted before any
   model call rather than routed by class. This is the cheapest way to hold the PDPA line, and
   it means a provider's terms never have to be strong enough to carry personal data.
3. **A second provider is enabled per route only when** all three hold: a signed DPA with
   zero or short retention and no training; a region the Sponsor has accepted for that class;
   and `firebid-eval compare-models` evidence that it is at least as good on the golden set.
4. **Self-hosted stays a candidate for every class** but is not built in Phase 1.
5. **Re-check published terms each phase gate.** These change; a decision taken once and never
   revisited is how R13 materialises.

## What only Legal and the Sponsor can answer

Published terms cannot settle these, so they are not research tasks:

1. Does our tender confidentiality position permit sending a client's drawings to a third-party
   processor at all, under any terms? (Links to open question Q8 in §11.4.)
2. Is the signed DPA's retention acceptable for `confidential`, and does it bind sub-processors?
3. Does the Sponsor read "Singapore region" as processing, storage, or both? (See above.)
4. Who accepts the residual risk when a fallback provider is enabled for a route?
5. Does any client contract already forbid AI processing of its documents? If so the gateway
   needs a per-bid override, which is **not** in the current design and would change P1-01.

Item 5 is the one most likely to cause rework, and it is worth asking before P1-01 starts.

## Minimum to unblock P1-01

P1-01 does **not** need the whole table. It needs:

- the four class names fixed (they are, above);
- one provider approved for `internal` so the gateway can be built and tested end to end;
- confirmation that the enforcement point is per-call-and-per-fallback (it is, per ADR-004).

So P1-01 can start against `internal` only, with `confidential` and `commercial` switched on in
configuration once D2 is signed. That keeps the decision off the critical path without
pretending it has been made.

## Sources

Checked 23 September 2026.

- [Anthropic Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms)
- [Anthropic — How do you use personal data in model training?](https://privacy.claude.com/en/articles/7996885-how-do-you-use-personal-data-in-model-training)
- [OpenAI API — Your data](https://developers.openai.com/api/docs/guides/your-data)

Still to check: Amazon Bedrock data protection; Google Vertex AI data governance; Google Gemini
API terms (distinct from Vertex); Azure OpenAI data privacy and modified abuse monitoring;
Microsoft Foundry. A web-search limit stopped the sweep partway; the fetches above were made
directly.
