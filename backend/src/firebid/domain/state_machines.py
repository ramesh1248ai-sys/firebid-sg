"""State machines from requirements §7.

Services never set a state field. They call :func:`plan_transition`, which checks that the
transition exists, that the actor holds a permitted role, and that any guard passes. The
caller then writes the new state and exactly one audit event in the same transaction.

Each machine here is one of the five models in §7. Phase 1 needs three; the clarification
model arrived with P2-06, and the external-approval model arrives with the step that uses it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    """Organisation roles from requirements §3. `SYSTEM` is the platform acting on its own."""

    ESTIMATOR = "estimator"
    SENIOR_ESTIMATOR = "senior_estimator"
    BID_MANAGER = "bid_manager"
    DESIGN_MANAGER = "design_manager"
    COMMERCIAL_DIRECTOR = "commercial_director"
    PROCUREMENT = "procurement"
    PROJECT_MANAGER = "project_manager"
    SYSTEM_ADMIN = "system_admin"
    EXECUTIVE_SPONSOR = "executive_sponsor"
    SYSTEM = "system"


class BidState(StrEnum):
    REGISTERED = "registered"
    QUALIFYING = "qualifying"
    IN_PREPARATION = "in_preparation"
    UNDER_REVIEW = "under_review"
    APPROVED_FOR_SUBMISSION = "approved_for_submission"
    SUBMITTED = "submitted"
    POST_SUBMISSION_CLARIFICATION = "post_submission_clarification"
    AWARDED = "awarded"
    LOST = "lost"
    WITHDRAWN = "withdrawn"
    NO_BID = "no_bid"


class SheetRevisionState(StrEnum):
    RECEIVED = "received"
    REGISTERED = "registered"
    CURRENT = "current"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"
    CONFLICT = "conflict"


class QtoItemState(StrEnum):
    DETECTED = "detected"
    PROPOSED = "proposed"
    VERIFIED = "verified"
    EDITED = "edited"
    REJECTED = "rejected"
    BASELINED = "baselined"
    SUPERSEDED = "superseded"


class ClarificationState(StrEnum):
    DRAFT = "draft"
    INTERNAL_REVIEW = "internal_review"
    APPROVED_TO_ISSUE = "approved_to_issue"
    ISSUED = "issued"
    RESPONDED = "responded"
    CLOSED_INCORPORATED = "closed_incorporated"
    CLOSED_NO_CHANGE = "closed_no_change"
    CONVERTED_TO_QUALIFICATION = "converted_to_qualification"


class TransitionError(Exception):
    """Raised when a transition is not allowed. Services let this reach the API as a 409."""


GuardResult = str | None
"""A guard returns None to allow the transition, or a reason to refuse it."""

Guard = Callable[[Mapping[str, Any]], GuardResult]


@dataclass(frozen=True)
class Transition:
    source: StrEnum
    target: StrEnum
    action: str
    roles: frozenset[str]
    guard: Guard | None = None
    note: str = ""


@dataclass(frozen=True)
class StateMachine:
    name: str
    states: type[StrEnum]
    initial: StrEnum
    transitions: tuple[Transition, ...]
    terminal: frozenset[StrEnum] = field(default_factory=frozenset)

    def allowed_targets(self, source: StrEnum) -> set[StrEnum]:
        return {t.target for t in self.transitions if t.source == source}

    def find(self, source: StrEnum, target: StrEnum) -> Transition | None:
        return next(
            (t for t in self.transitions if t.source == source and t.target == target), None
        )


def plan_transition(
    machine: StateMachine,
    *,
    source: StrEnum,
    target: StrEnum,
    actor_roles: Iterable[str],
    context: Mapping[str, Any] | None = None,
) -> Transition:
    """Return the transition to apply, or raise :class:`TransitionError`."""
    transition = machine.find(source, target)
    if transition is None:
        allowed = ", ".join(sorted(str(s) for s in machine.allowed_targets(source))) or "nothing"
        raise TransitionError(
            f"{machine.name}: cannot move from '{source}' to '{target}'; allowed: {allowed}"
        )
    roles = {str(role) for role in actor_roles}
    if not roles & transition.roles:
        permitted = ", ".join(sorted(transition.roles))
        raise TransitionError(
            f"{machine.name}: '{transition.action}' needs one of these roles: {permitted}"
        )
    if transition.guard is not None:
        refusal = transition.guard(context or {})
        if refusal:
            raise TransitionError(f"{machine.name}: '{transition.action}' refused: {refusal}")
    return transition


# --- Bid lifecycle -------------------------------------------------------------------------

_BID_MANAGEMENT = frozenset({Role.BID_MANAGER, Role.SENIOR_ESTIMATOR, Role.COMMERCIAL_DIRECTOR})
_COMMERCIAL = frozenset({Role.COMMERCIAL_DIRECTOR})


def _submission_needs_gate_approval(context: Mapping[str, Any]) -> GuardResult:
    """G4: a bid reaches Submitted only with a recorded approval (FR-PKG-02)."""
    return None if context.get("has_g4_approval") else "the G4 submission approval is missing"


def _g3_ready(context: Mapping[str, Any]) -> GuardResult:
    """G3: the commercial approval rests on a resolved scope checklist and treated risks
    (FR-RSK-01, 06)."""
    blockers = context.get("g3_blockers")
    return f"not ready for G3: {blockers}" if blockers else None


def _bid_details_complete(context: Mapping[str, Any]) -> GuardResult:
    """FR-BID-01: work starts only once the tender's details and team are known."""
    missing = context.get("missing_fields") or []
    return f"these details are missing: {', '.join(missing)}" if missing else None


BID_LIFECYCLE = StateMachine(
    name="bid lifecycle",
    states=BidState,
    initial=BidState.REGISTERED,
    terminal=frozenset({BidState.AWARDED, BidState.LOST, BidState.WITHDRAWN, BidState.NO_BID}),
    transitions=(
        Transition(
            BidState.REGISTERED,
            BidState.QUALIFYING,
            "start qualification",
            _BID_MANAGEMENT,
            guard=_bid_details_complete,
            note="FR-BID-01: the mandatory details and the bid team must be complete.",
        ),
        Transition(BidState.REGISTERED, BidState.WITHDRAWN, "withdraw", _BID_MANAGEMENT),
        Transition(BidState.QUALIFYING, BidState.IN_PREPARATION, "bid (G0)", _COMMERCIAL),
        Transition(BidState.QUALIFYING, BidState.NO_BID, "no-bid (G0)", _COMMERCIAL),
        Transition(BidState.QUALIFYING, BidState.WITHDRAWN, "withdraw", _BID_MANAGEMENT),
        Transition(
            BidState.IN_PREPARATION, BidState.UNDER_REVIEW, "submit for review", _BID_MANAGEMENT
        ),
        Transition(BidState.IN_PREPARATION, BidState.WITHDRAWN, "withdraw", _BID_MANAGEMENT),
        Transition(
            BidState.UNDER_REVIEW, BidState.IN_PREPARATION, "send back for rework", _BID_MANAGEMENT
        ),
        Transition(
            BidState.UNDER_REVIEW,
            BidState.APPROVED_FOR_SUBMISSION,
            "approve (G3)",
            _COMMERCIAL,
            guard=_g3_ready,
            note="Every scope checklist item is resolved and every risk has a treatment.",
        ),
        Transition(BidState.UNDER_REVIEW, BidState.WITHDRAWN, "withdraw", _BID_MANAGEMENT),
        Transition(
            BidState.APPROVED_FOR_SUBMISSION, BidState.IN_PREPARATION, "reopen", _BID_MANAGEMENT
        ),
        Transition(
            BidState.APPROVED_FOR_SUBMISSION,
            BidState.SUBMITTED,
            "submit (G4)",
            _COMMERCIAL,
            guard=_submission_needs_gate_approval,
            note="A submitted bid is frozen (FR-PKG-03).",
        ),
        Transition(BidState.APPROVED_FOR_SUBMISSION, BidState.WITHDRAWN, "withdraw", _COMMERCIAL),
        Transition(
            BidState.SUBMITTED,
            BidState.POST_SUBMISSION_CLARIFICATION,
            "client asked for clarification",
            _BID_MANAGEMENT,
        ),
        Transition(BidState.SUBMITTED, BidState.AWARDED, "record award", _BID_MANAGEMENT),
        Transition(BidState.SUBMITTED, BidState.LOST, "record loss", _BID_MANAGEMENT),
        Transition(BidState.SUBMITTED, BidState.WITHDRAWN, "withdraw", _COMMERCIAL),
        Transition(
            BidState.POST_SUBMISSION_CLARIFICATION,
            BidState.AWARDED,
            "record award",
            _BID_MANAGEMENT,
        ),
        Transition(
            BidState.POST_SUBMISSION_CLARIFICATION, BidState.LOST, "record loss", _BID_MANAGEMENT
        ),
        Transition(
            BidState.POST_SUBMISSION_CLARIFICATION, BidState.WITHDRAWN, "withdraw", _COMMERCIAL
        ),
    ),
)


# --- Document / sheet revision -------------------------------------------------------------

_DOC_CONTROL = frozenset({Role.ESTIMATOR, Role.SENIOR_ESTIMATOR, Role.BID_MANAGER, Role.SYSTEM})
_DOC_RESOLVE = frozenset({Role.ESTIMATOR, Role.SENIOR_ESTIMATOR, Role.BID_MANAGER})


def _only_one_current_revision(context: Mapping[str, Any]) -> GuardResult:
    """Guardrail 6: exactly one Current revision per drawing number (FR-DOC-03)."""
    other = context.get("other_current_revision")
    return None if not other else f"revision '{other}' is already Current for this sheet number"


SHEET_REVISION = StateMachine(
    name="sheet revision",
    states=SheetRevisionState,
    initial=SheetRevisionState.RECEIVED,
    terminal=frozenset({SheetRevisionState.WITHDRAWN}),
    transitions=(
        Transition(
            SheetRevisionState.RECEIVED, SheetRevisionState.REGISTERED, "register", _DOC_CONTROL
        ),
        Transition(
            SheetRevisionState.RECEIVED,
            SheetRevisionState.CONFLICT,
            "flag conflicting revision data",
            _DOC_CONTROL,
        ),
        Transition(
            SheetRevisionState.RECEIVED,
            SheetRevisionState.WITHDRAWN,
            "withdraw",
            _DOC_RESOLVE,
            note="A page that is not a drawing, such as a cover sheet, which nobody will identify.",
        ),
        Transition(
            SheetRevisionState.REGISTERED,
            SheetRevisionState.CURRENT,
            "mark current",
            _DOC_CONTROL,
            guard=_only_one_current_revision,
        ),
        Transition(
            SheetRevisionState.REGISTERED, SheetRevisionState.SUPERSEDED, "supersede", _DOC_CONTROL
        ),
        Transition(
            SheetRevisionState.REGISTERED,
            SheetRevisionState.CONFLICT,
            "flag conflict",
            _DOC_CONTROL,
        ),
        Transition(
            SheetRevisionState.REGISTERED, SheetRevisionState.WITHDRAWN, "withdraw", _DOC_RESOLVE
        ),
        Transition(
            SheetRevisionState.CURRENT, SheetRevisionState.SUPERSEDED, "supersede", _DOC_CONTROL
        ),
        Transition(
            SheetRevisionState.CURRENT, SheetRevisionState.CONFLICT, "flag conflict", _DOC_CONTROL
        ),
        Transition(
            SheetRevisionState.CURRENT, SheetRevisionState.WITHDRAWN, "withdraw", _DOC_RESOLVE
        ),
        Transition(
            SheetRevisionState.SUPERSEDED,
            SheetRevisionState.CURRENT,
            "restore as current",
            _DOC_RESOLVE,
            guard=_only_one_current_revision,
            note="Used when a supersession is found to be wrong.",
        ),
        Transition(
            SheetRevisionState.CONFLICT,
            SheetRevisionState.CURRENT,
            "resolve as current",
            _DOC_RESOLVE,
            guard=_only_one_current_revision,
        ),
        Transition(
            SheetRevisionState.CONFLICT,
            SheetRevisionState.SUPERSEDED,
            "resolve as superseded",
            _DOC_RESOLVE,
        ),
        Transition(
            SheetRevisionState.CONFLICT, SheetRevisionState.WITHDRAWN, "withdraw", _DOC_RESOLVE
        ),
    ),
)


# --- QTO item ------------------------------------------------------------------------------

_VERIFIERS = frozenset({Role.ESTIMATOR, Role.SENIOR_ESTIMATOR})
_BASELINE = frozenset({Role.SENIOR_ESTIMATOR})
_AUTOMATED = frozenset({Role.SYSTEM})


def _baseline_needs_verification(context: Mapping[str, Any]) -> GuardResult:
    """G2 baselines only verified quantities, with no unresolved duplicates (FR-QTO-08)."""
    if context.get("unresolved_duplicates"):
        return "the item is in an unresolved duplicate group"
    return None


QTO_ITEM = StateMachine(
    name="QTO item",
    states=QtoItemState,
    initial=QtoItemState.DETECTED,
    terminal=frozenset(),
    transitions=(
        Transition(QtoItemState.DETECTED, QtoItemState.PROPOSED, "propose for review", _AUTOMATED),
        Transition(QtoItemState.PROPOSED, QtoItemState.VERIFIED, "verify", _VERIFIERS),
        Transition(QtoItemState.PROPOSED, QtoItemState.EDITED, "edit", _VERIFIERS),
        Transition(QtoItemState.PROPOSED, QtoItemState.REJECTED, "reject", _VERIFIERS),
        Transition(QtoItemState.EDITED, QtoItemState.VERIFIED, "verify edit", _VERIFIERS),
        Transition(QtoItemState.EDITED, QtoItemState.REJECTED, "reject", _VERIFIERS),
        Transition(QtoItemState.REJECTED, QtoItemState.PROPOSED, "reinstate", _VERIFIERS),
        Transition(QtoItemState.VERIFIED, QtoItemState.PROPOSED, "reopen", _VERIFIERS),
        Transition(
            QtoItemState.VERIFIED,
            QtoItemState.BASELINED,
            "baseline (G2)",
            _BASELINE,
            guard=_baseline_needs_verification,
            note="Baselined items are immutable; changes create new proposals.",
        ),
        Transition(
            QtoItemState.BASELINED,
            QtoItemState.SUPERSEDED,
            "supersede on revision or addendum",
            _AUTOMATED | _BASELINE,
        ),
        Transition(
            QtoItemState.VERIFIED,
            QtoItemState.SUPERSEDED,
            "supersede on revision or addendum",
            _AUTOMATED | _VERIFIERS,
        ),
        Transition(
            QtoItemState.SUPERSEDED,
            QtoItemState.PROPOSED,
            "re-verify after change",
            _AUTOMATED | _VERIFIERS,
        ),
        # P1-07: recomputing takeoff replaces an item whose inputs changed with a new
        # proposal, whatever a person had decided about the old one; the old row stays.
        *(
            Transition(
                source,
                QtoItemState.SUPERSEDED,
                "supersede on recompute",
                _AUTOMATED,
                note="Its inputs changed, or it is no longer found; a new proposal replaces it.",
            )
            for source in (QtoItemState.PROPOSED, QtoItemState.EDITED, QtoItemState.REJECTED)
        ),
    ),
)


# --- Tender clarification ------------------------------------------------------------------

_DRAFTERS = frozenset({Role.ESTIMATOR, Role.SENIOR_ESTIMATOR, Role.BID_MANAGER})
_ISSUERS = frozenset({Role.BID_MANAGER})


def _evidence_and_engineering_approval(context: Mapping[str, Any]) -> GuardResult:
    """Guardrail 5: nothing is issued without evidence, and an option with engineering,
    fire-safety or structural content needs the Design Manager first (FR-RFI-06)."""
    if not context.get("has_evidence"):
        return "the clarification has no evidence reference"
    if context.get("engineering_content") and not context.get("design_manager_approved"):
        return (
            "it has engineering, fire-safety or structural content: "
            "the Design Manager's approval is missing"
        )
    return None


def _impact_assessed(context: Mapping[str, Any]) -> GuardResult:
    """A response is closed only once someone has said what it changed (FR-RFI-04)."""
    return None if context.get("impact_assessed") else "the response's impact is not assessed"


_UNRESOLVED = (
    ClarificationState.DRAFT,
    ClarificationState.INTERNAL_REVIEW,
    ClarificationState.APPROVED_TO_ISSUE,
    ClarificationState.ISSUED,
    ClarificationState.RESPONDED,
)

CLARIFICATION = StateMachine(
    name="clarification",
    states=ClarificationState,
    initial=ClarificationState.DRAFT,
    terminal=frozenset(
        {
            ClarificationState.CLOSED_INCORPORATED,
            ClarificationState.CLOSED_NO_CHANGE,
            ClarificationState.CONVERTED_TO_QUALIFICATION,
        }
    ),
    transitions=(
        Transition(
            ClarificationState.DRAFT,
            ClarificationState.INTERNAL_REVIEW,
            "send for internal review",
            _DRAFTERS,
        ),
        Transition(
            ClarificationState.INTERNAL_REVIEW,
            ClarificationState.DRAFT,
            "send back to draft",
            _DRAFTERS | {Role.DESIGN_MANAGER},
        ),
        Transition(
            ClarificationState.INTERNAL_REVIEW,
            ClarificationState.APPROVED_TO_ISSUE,
            "approve to issue",
            _ISSUERS,
            guard=_evidence_and_engineering_approval,
            note="The Bid Manager approves; engineering content needs the Design Manager too.",
        ),
        Transition(
            ClarificationState.APPROVED_TO_ISSUE,
            ClarificationState.INTERNAL_REVIEW,
            "withdraw the approval",
            _ISSUERS,
        ),
        Transition(
            ClarificationState.APPROVED_TO_ISSUE,
            ClarificationState.ISSUED,
            "record as issued",
            _ISSUERS,
            note="A person sent it outside the platform; the platform sends nothing (L3).",
        ),
        Transition(
            ClarificationState.ISSUED,
            ClarificationState.RESPONDED,
            "record the response",
            _DRAFTERS,
        ),
        Transition(
            ClarificationState.RESPONDED,
            ClarificationState.CLOSED_INCORPORATED,
            "close: incorporated",
            _DRAFTERS,
            guard=_impact_assessed,
        ),
        Transition(
            ClarificationState.RESPONDED,
            ClarificationState.CLOSED_NO_CHANGE,
            "close: no change",
            _DRAFTERS,
            guard=_impact_assessed,
        ),
        *(
            Transition(
                source,
                ClarificationState.CONVERTED_TO_QUALIFICATION,
                "convert to a qualification",
                _ISSUERS,
                note="Unresolved at submission: proposed as a qualification or assumption.",
            )
            for source in _UNRESOLVED
        ),
    ),
)


MACHINES: dict[str, StateMachine] = {
    "bid": BID_LIFECYCLE,
    "sheet_revision": SHEET_REVISION,
    "qto_item": QTO_ITEM,
    "clarification": CLARIFICATION,
}
