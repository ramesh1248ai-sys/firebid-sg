# P3-01 · Compliance Knowledge Corpus and Cited Retrieval

**Builds on:** P1-06 (clause trees), P2-03 (spec analysis), P0-04 (gateway). **Needs:** licensed copies of the SCDF Fire Code and relevant Singapore Standards with a licence that allows AI use (DP4), a named knowledge owner, and Design Manager time to build the evaluation question set (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. Model calls go through `ai_gateway` routes. Declare the new answering, labelling and embedding routes in `llm.yaml` with data class `confidential`.

## Read first

- `docs/plan/project-context.md`: guardrail 5, *source-type label*, "LLM providers and AI usage" (provider-neutral citations, embeddings).
- Requirements: §2 (regulatory context, source-type labels, rules), §6.8 (FR-CMP-01 to 05), §3 (RACI: regulatory interpretation, corpus updates).
- `docs/plan/BUILD_LOG.md`.

## Goal

Answer compliance questions only from a governed, edition-pinned corpus, with clause-level citations and a source-type label on every statement. The platform never presents an inference as a requirement, and every material interpretation goes to the Design Manager.

## Scope

FR-CMP-01, 02, 03, 04, 05.

## Build

1. **Corpus administration** (FR-CMP-01):
   - A KnowledgeDocument has type (SCDF Fire Code, SCDF circular, SS/CP, project specification, approved drawing or waiver, project-specific standard), edition, effective date, source, owner and licence status.
   - Ingestion is refused unless licence status permits AI use.
   - A superseded edition stays available.
2. **Clause-structured indexing:**
   - Parse each document into clauses (number, heading, text, page), each with a stable clause ID.
   - Index clauses in pgvector with embeddings from a configurable embedding route, plus a keyword index for clause numbers and defined terms, using hybrid retrieval.
   - Each vector stores its embedding model and version. Changing the embedding route's model triggers a background re-index, and retrieval uses only vectors from the active model.
3. **Edition selection** (FR-CMP-03): each project selects the applicable edition per code. Retrieval filters to those editions.
4. **Cited answering** (FR-CMP-02):
   - **Answer:** send the retrieved clauses with their clause IDs, and require a structured answer. It is a list of statements, each with its supporting clause IDs and the exact quoted span from each clause. When the route's model supports native citations, the adapter may use them and convert them to the same structure.
   - **Verify:** a deterministic verifier confirms each quoted span appears in the cited clause text of the selected edition, after whitespace and case normalisation. A statement with no verified span loses its citation.
   - **Label:** a structured-output call assigns each statement a source-type label. Deterministic post-processing then labels every statement without a verified citation "AI recommendation", and removes requirement wording from it.
   - Persist the answer as a proposal with verified citations, labels, provider and model.
5. **Advisory workflow** (FR-CMP-04): findings are advisory. A finding marked "material consequence" (by rule or by user) needs Design Manager approval, with an optional "QP review required" flag, before other modules use it.
6. **Product compliance** (FR-CMP-05):
   - Product records hold certification evidence (e.g. Certificate of Conformity reference, listing, expiry) and AVL status.
   - Flag proposed products that need regulated certification or AVL approval when evidence is missing or expired.
7. **Evaluation:** a question set built with the Design Manager: question, expected clauses and edition, expected label. Metrics are citation accuracy (cited span supports the statement), label accuracy, and "uncited-as-requirement" count, all added to `firebid-eval`.

## Done when

- `firebid-eval run --suite compliance` reports citation accuracy (target ≥ 98%) and zero uncited statements labelled as requirements. `compare-models` shows the same metrics for each configured model approved for `confidential` data. Tagged FR-CMP-02.
- A test with a fabricated quoted span shows the verifier stripping the citation and the statement relabelled "AI recommendation". Tagged FR-CMP-02.
- A test shows that changing the embedding model triggers a re-index, and that retrieval never mixes vectors from two embedding models.
- A test shows ingestion refused for a document whose licence status forbids AI use. Tagged FR-CMP-01.
- Edition pinning: a project on an earlier edition retrieves only that edition's clauses. Tagged FR-CMP-03.
- A material finding is unusable by other modules until the Design Manager approves it. Tagged FR-CMP-04.
- A product with missing or expired certification evidence is flagged. Tagged FR-CMP-05.
- A build log entry is appended.
