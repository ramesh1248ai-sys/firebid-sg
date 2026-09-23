"""The permission matrix, taken from the RACI in requirements §3.

One place decides who may do what. The role marked **Accountable** in §3 signs the gate;
where §3 marks A/R, that role both does the work and signs for it.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from firebid.domain.state_machines import Role


class Action(StrEnum):
    """Decisions and operations that need a role. Gates map to their Accountable role."""

    BID_CREATE = "bid.create"
    BID_EDIT = "bid.edit"
    BID_MEMBER_MANAGE = "bid.member.manage"
    GATE_G0_APPROVE = "gate.G0.approve"  # bid / no-bid
    GATE_G1_APPROVE = "gate.G1.approve"  # QTO verified
    GATE_G2_APPROVE = "gate.G2.approve"  # estimate approved
    GATE_G3_APPROVE = "gate.G3.approve"  # commercial approval
    GATE_G4_APPROVE = "gate.G4.approve"  # submission
    MEASUREMENT_RULES_CHANGE = "measurement_rules.change"
    SUPPLIER_PRICE_SELECT = "supplier_price.select"
    LABOUR_PRODUCTIVITY_ADJUST = "labour_productivity.adjust"
    ENGINEERING_OPTION_APPROVE = "engineering_option.approve"
    REGULATORY_INTERPRETATION_APPROVE = "regulatory_interpretation.approve"
    CLARIFICATION_ISSUE = "clarification.issue"
    QUALIFICATIONS_APPROVE = "qualifications.approve"
    KNOWLEDGE_CORPUS_APPROVE = "knowledge_corpus.approve"
    KNOWLEDGE_CORPUS_APPLY = "knowledge_corpus.apply"
    AUDIT_READ_ORGANISATION = "audit.read.organisation"
    ADMIN_READ_PLATFORM = "admin.read.platform"


# Accountable roles from requirements §3.2. Keep this table and the RACI in step.
_MATRIX: dict[Action, frozenset[str]] = {
    Action.BID_CREATE: frozenset({Role.BID_MANAGER, Role.SENIOR_ESTIMATOR, Role.SYSTEM_ADMIN}),
    Action.BID_EDIT: frozenset({Role.BID_MANAGER, Role.SENIOR_ESTIMATOR, Role.ESTIMATOR}),
    Action.BID_MEMBER_MANAGE: frozenset({Role.BID_MANAGER, Role.SYSTEM_ADMIN}),
    Action.GATE_G0_APPROVE: frozenset({Role.COMMERCIAL_DIRECTOR}),
    Action.GATE_G1_APPROVE: frozenset({Role.SENIOR_ESTIMATOR}),
    Action.GATE_G2_APPROVE: frozenset({Role.SENIOR_ESTIMATOR}),
    Action.GATE_G3_APPROVE: frozenset({Role.COMMERCIAL_DIRECTOR}),
    Action.GATE_G4_APPROVE: frozenset({Role.COMMERCIAL_DIRECTOR}),
    Action.MEASUREMENT_RULES_CHANGE: frozenset({Role.SENIOR_ESTIMATOR}),
    Action.SUPPLIER_PRICE_SELECT: frozenset({Role.SENIOR_ESTIMATOR}),
    Action.LABOUR_PRODUCTIVITY_ADJUST: frozenset({Role.SENIOR_ESTIMATOR}),
    Action.ENGINEERING_OPTION_APPROVE: frozenset({Role.DESIGN_MANAGER}),
    Action.REGULATORY_INTERPRETATION_APPROVE: frozenset({Role.DESIGN_MANAGER}),
    Action.CLARIFICATION_ISSUE: frozenset({Role.BID_MANAGER}),
    Action.QUALIFICATIONS_APPROVE: frozenset({Role.COMMERCIAL_DIRECTOR}),
    Action.KNOWLEDGE_CORPUS_APPROVE: frozenset({Role.DESIGN_MANAGER}),
    Action.KNOWLEDGE_CORPUS_APPLY: frozenset({Role.SYSTEM_ADMIN}),
    Action.AUDIT_READ_ORGANISATION: frozenset({Role.SYSTEM_ADMIN, Role.COMMERCIAL_DIRECTOR}),
    # Routing, provider health and what the platform is spending. The commercial director
    # owns the budget, so they see it too.
    Action.ADMIN_READ_PLATFORM: frozenset({Role.SYSTEM_ADMIN, Role.COMMERCIAL_DIRECTOR}),
}

GATE_ACTIONS: dict[str, Action] = {
    "G0": Action.GATE_G0_APPROVE,
    "G1": Action.GATE_G1_APPROVE,
    "G2": Action.GATE_G2_APPROVE,
    "G3": Action.GATE_G3_APPROVE,
    "G4": Action.GATE_G4_APPROVE,
}


def roles_for(action: Action) -> frozenset[str]:
    return _MATRIX[action]


def may(roles: Iterable[str], action: Action) -> bool:
    return bool({str(role) for role in roles} & _MATRIX[action])
