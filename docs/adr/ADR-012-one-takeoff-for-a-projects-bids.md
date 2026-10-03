# ADR-012: One verified takeoff for a project's several bids

- **Status:** Proposed. Built in P2-02 (3 Oct 2026); to be accepted by the product owner.
- **Date:** 2026-10-03
- **Deciders:** Tech Lead, Senior Estimator; Commercial Director for what one bid's team may see of another's
- **Requirements:** FR-BID-04; guardrails 2 and 8; NFR-08 (bid isolation); the immutability convention

## Context

A project is often bid to several main contractors. The drawings are the same, so the takeoff is the same, and doing it once is the point of FR-BID-04: "the bids share one verified QTO; each keeps its own documents, commercial terms and pricing".

Three things in the platform as built pull against a literal reading of "share":

- **Everything about a takeoff is bid-scoped.** `qto_item` carries `bid_id`, is partitioned by it, and is under a row-level security policy that admits a bid's team and nobody else (guardrail 8, P0-03). The BOQ, pricing, the review workbench and G1 all read a bid's own rows.
- **Only a person's action makes an item Verified** (guardrail 2).
- **A takeoff's evidence is the drawings of the bid that read them.** Another bid's team is not on that bid, and must not see its client-specific documents.

FR-BID-04's acceptance criterion is about what must *not* cross: "users of one bid cannot see another bid's client-specific documents or prices unless they are members of both".

## Options

1. **Read through.** A second bid reads the first bid's `qto_item` rows, with a wider security policy on that table; an edit in the second bid writes an overriding row there. True copy-on-write at the row level. The policy on the platform's most sensitive partitioned table becomes "a member of this bid, or of any bid that shares its takeoff", and every reader of a takeoff (BOQ, pricing, review, G1, evidence, exports) has to merge two bids' rows. One mistake in any of them leaks or double counts.
2. **Copy once.** The second bid copies the first bid's items when it starts. Simple, and isolation is untouched. But it is a fork, not a shared takeoff: an addendum taken off in the first bid never reaches the second.
3. **Publish and adopt.** The bid that took the drawings off *publishes* its verified items to the project, as an immutable, versioned record that holds quantities and drawing references only. Each other bid *adopts* a published version: its rows are its own, each referring to the published item and version it came from. An edit changes the adopting bid's row only. When a later version is published, adopting it again brings the rows nobody edited up to it and reports the edited ones.

## Recommendation

**Option 3.**

- **Isolation is not widened.** No policy on a bid-scoped table changes. One new project-level table, `shared_takeoff`, is readable by a member of any bid of the project and writable only from the publishing bid. It holds what is common to every bid of the project: what is on the drawings. It never holds a client BOQ, a rate, a price or a commercial term.
- **Guardrail 2 holds.** Publishing needs G1 approved and in force on the publishing bid. Adopting is a named action by the adopting bid's Senior Estimator, which verifies the adopted rows and records who verified each in the publishing bid.
- **It is copy-on-write where it matters.** A bid-specific edit creates that bid's own version and leaves the published takeoff and every other bid alone. Rows that were not edited follow the published takeoff from version to version, so an addendum is taken off once.
- **Everything downstream is unchanged.** The BOQ, pricing, review and G1 of the adopting bid read its own rows, as they always did.

What it costs against option 1: an adopting bid follows a new version when someone adopts it, not the moment it is published. The bid's status says when it is behind.

## Consequences

- **A bid either takes off its own drawings or adopts the project's takeoff, not both.** Adopting is refused on a bid that has takeoff items of its own. Mixing the two (a bid with one extra drawing of its own) is not supported yet.
- **An adopted item's evidence is the published item**, by its reference. Opening the drawing it was counted on needs membership of the publishing bid, which is the isolation FR-BID-04 asks for.
- **Recompute skips adopted items.** They do not come from the adopting bid's drawings.
- **A reopened G1 stops publishing.** After an addendum changes the takeoff, the publishing bid verifies the changes and approves G1 again before it can publish the next version.
- **An edited row whose published item later changes is kept and reported.** A person reconciles it; nothing does so automatically.
- **A new security helper**, `firebid_can_see_project`, exists. It is used by `shared_takeoff` only. Any later project-level table that uses it must hold nothing client-specific.
- **Follow-up:** a workbench screen for publishing, adopting and reconciling (the API is built; the page is not); adopting on a bid with drawings of its own.
