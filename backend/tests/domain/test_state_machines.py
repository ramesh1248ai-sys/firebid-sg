"""Every state pair of every Phase 1 state machine (requirements §7).

The audit event written per successful transition is covered in tests/db/test_transitions.py,
where a database is available.
"""

from enum import StrEnum
from itertools import product

import pytest

from firebid.domain.state_machines import (
    BID_LIFECYCLE,
    MACHINES,
    QTO_ITEM,
    SHEET_REVISION,
    BidState,
    QtoItemState,
    Role,
    SheetRevisionState,
    StateMachine,
    Transition,
    TransitionError,
    plan_transition,
)

ALL_ROLES = [str(role) for role in Role]


def declared() -> list[tuple[StateMachine, Transition]]:
    return [(machine, t) for machine in MACHINES.values() for t in machine.transitions]


def undeclared() -> list[tuple[StateMachine, StrEnum, StrEnum]]:
    pairs = []
    for machine in MACHINES.values():
        for source, target in product(machine.states, repeat=2):
            if machine.find(source, target) is None:
                pairs.append((machine, source, target))
    return pairs


@pytest.mark.parametrize(
    ("machine", "transition"),
    declared(),
    ids=[f"{m.name}:{t.source}->{t.target}" for m, t in declared()],
)
def test_declared_transition_is_allowed_for_a_permitted_role(
    machine: StateMachine, transition: Transition
) -> None:
    planned = plan_transition(
        machine,
        source=transition.source,
        target=transition.target,
        actor_roles=[next(iter(transition.roles))],
        context={"has_g4_approval": True},
    )
    assert planned is transition


@pytest.mark.parametrize(
    ("machine", "source", "target"),
    undeclared(),
    ids=[f"{m.name}:{s}->{t}" for m, s, t in undeclared()],
)
def test_undeclared_transition_raises(
    machine: StateMachine, source: StrEnum, target: StrEnum
) -> None:
    with pytest.raises(TransitionError, match="cannot move from"):
        plan_transition(
            machine,
            source=source,
            target=target,
            actor_roles=ALL_ROLES,
            context={"has_g4_approval": True},
        )


@pytest.mark.parametrize(
    ("machine", "transition"),
    declared(),
    ids=[f"{m.name}:{t.source}->{t.target}" for m, t in declared()],
)
def test_transition_refuses_roles_it_does_not_permit(
    machine: StateMachine, transition: Transition
) -> None:
    outsiders = [role for role in ALL_ROLES if role not in transition.roles]
    assert outsiders, "every transition should leave at least one role out"
    with pytest.raises(TransitionError, match="needs one of these roles"):
        plan_transition(
            machine,
            source=transition.source,
            target=transition.target,
            actor_roles=outsiders,
            context={"has_g4_approval": True},
        )


def test_terminal_states_have_no_way_out() -> None:
    for machine in MACHINES.values():
        for state in machine.terminal:
            assert machine.allowed_targets(state) == set()


class TestGuards:
    def test_bid_cannot_be_submitted_without_the_g4_approval(self) -> None:
        with pytest.raises(TransitionError, match="G4 submission approval is missing"):
            plan_transition(
                BID_LIFECYCLE,
                source=BidState.APPROVED_FOR_SUBMISSION,
                target=BidState.SUBMITTED,
                actor_roles=[Role.COMMERCIAL_DIRECTOR],
                context={"has_g4_approval": False},
            )

    def test_a_second_current_revision_is_refused(self) -> None:
        with pytest.raises(TransitionError, match="already Current"):
            plan_transition(
                SHEET_REVISION,
                source=SheetRevisionState.REGISTERED,
                target=SheetRevisionState.CURRENT,
                actor_roles=[Role.ESTIMATOR],
                context={"other_current_revision": "R04"},
            )

    def test_an_item_in_an_unresolved_duplicate_group_cannot_be_baselined(self) -> None:
        with pytest.raises(TransitionError, match="duplicate group"):
            plan_transition(
                QTO_ITEM,
                source=QtoItemState.VERIFIED,
                target=QtoItemState.BASELINED,
                actor_roles=[Role.SENIOR_ESTIMATOR],
                context={"unresolved_duplicates": True},
            )


class TestModelShape:
    def test_only_the_commercial_director_can_submit_a_bid(self) -> None:
        submit = BID_LIFECYCLE.find(BidState.APPROVED_FOR_SUBMISSION, BidState.SUBMITTED)
        assert submit is not None
        assert submit.roles == frozenset({Role.COMMERCIAL_DIRECTOR})

    def test_a_submitted_bid_is_frozen_apart_from_outcome_states(self) -> None:
        assert BID_LIFECYCLE.allowed_targets(BidState.SUBMITTED) == {
            BidState.POST_SUBMISSION_CLARIFICATION,
            BidState.AWARDED,
            BidState.LOST,
            BidState.WITHDRAWN,
        }

    def test_baselined_items_only_move_to_superseded(self) -> None:
        assert QTO_ITEM.allowed_targets(QtoItemState.BASELINED) == {QtoItemState.SUPERSEDED}
