"""Symbol mapping end to end: one confirmation pass, then reuse; unmapped never counted.

Tender 1 from Alpha Consultants is a legend sheet and a plan (with its own small legend).
The keyword rules propose five rows; "SPRINKLER - UP TYPE" goes to the model. A person
confirms each proposal once. Tender 2, a different project from the same consultant, then
maps with no proposals and no model calls at all. The plan's mystery symbol, which no
legend explains, is listed as unmapped on both and never counted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.prompts import prompt_for
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Bid, Project
from firebid.db.models.symbols import LegendEntry, SymbolMapping
from firebid.domain.actors import Actor
from firebid.evals import synthetic
from firebid.evals import synthetic_symbols as fixtures
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services import symbols as service
from firebid.services.geometry import extract_all
from firebid.services.ingestion import Ingestor
from firebid.services.sheets import process_document
from firebid.services.title_blocks import read_title_blocks
from firebid.services.views import detect_all
from firebid.storage.object_store import MemoryObjectStore

ESTIMATOR = Actor(label="Esther Tan", roles=frozenset({"estimator"}))

CONFIG = """
version: 1
providers:
  primary:
    kind: fake
    approved_data_classes: [internal, confidential]
models:
  first:
    provider: primary
    model_id: vision-1
    capabilities: [structured_output, vision]
routes:
  symbol_map:
    requires: [vision, structured_output]
    data_class: confidential
    models: [first]
    reasoning: low
    prompt: symbol_map
"""


@pytest.fixture(autouse=True)
def no_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    from firebid.services import sheets

    monkeypatch.setattr(sheets, "_render_low_levels", lambda *_args, **_kwargs: 0)


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def router(tmp_path: Path) -> Callable[..., tuple[Router, FakeAdapter]]:
    path = tmp_path / "llm.yaml"
    path.write_text(CONFIG, encoding="utf-8")

    def build(reply: dict[str, Any]) -> tuple[Router, FakeAdapter]:
        adapter = FakeAdapter("primary").reply(json.dumps(reply))
        routed = Router(
            config=load_config(path),
            adapters={"primary": adapter},
            backoff_base_seconds=0,
            sleep=lambda _s: None,
        )
        return routed, adapter

    return build


UPRIGHT = {
    "object_type": "sprinkler_upright",
    "attributes": {},
    "confidence": 0.82,
    "reason": "a circle with a dot is an upright head; 'UP TYPE' agrees",
}


def from_consultant(session: Session, bid: Bid, name: str) -> Bid:
    project = session.get(Project, bid.project_id)
    assert project is not None
    project.consultant = name
    session.commit()
    return bid


def read(session: Session, bid: Bid, store: MemoryObjectStore, name: str, document: Any) -> None:
    """A drawing through the parse pipeline: sheets, title block, geometry, views, symbols."""
    stored = (
        Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id)
        .ingest(f"{name}.dxf", synthetic.dxf_bytes(document))
        .stored[0]
    )
    sheets = process_document(session, store, stored).sheets
    read_title_blocks(session, store, stored, sheets)
    extract_all(session, store, stored, sheets)
    detect_all(session, store, sheets)
    service.read_all(session, store, sheets)
    session.commit()


def tender(session: Session, bid: Bid, store: MemoryObjectStore, consultant: Any) -> Any:
    read(session, bid, store, "FP-LEG-001", fixtures.legend_sheet(consultant))
    plan, truth = fixtures.plan_sheet(consultant, with_legend=True)
    read(session, bid, store, "FP-L05-201", plan)
    return truth


def entries(session: Session, bid: Bid) -> list[LegendEntry]:
    return list(
        session.execute(
            select(LegendEntry).where(LegendEntry.bid_id == bid.id).order_by(LegendEntry.ordinal)
        ).scalars()
    )


def ask_the_model(session: Session, store: MemoryObjectStore, bid: Bid, routed: Router) -> None:
    for entry in entries(session, bid):
        service.propose_with_model(session, store, entry, routed)
    session.commit()


def confirm_everything(session: Session, bid: Bid) -> None:
    """One confirmation pass: each proposal, as proposed."""
    lineages = {entry.mapping_lineage_id for entry in entries(session, bid)}
    for lineage in lineages:
        assert lineage is not None
        mapping = service.current(session, lineage)
        assert mapping is not None
        if mapping.state == "proposed":
            service.confirm(session, lineage, ESTIMATOR)
    session.commit()


def mapping_rows(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(SymbolMapping)).scalar_one())


def model_jobs(session: Session, bid: Bid) -> int:
    ids = [str(entry.id) for entry in entries(session, bid)]
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'symbol.propose' "
                "AND args->>'entry_id' = ANY(:ids)"
            ),
            {"ids": ids},
        ).scalar_one()
    )


@pytest.mark.req("FR-VIS-02")
class TestOneConfirmationThenReuse:
    def test_a_legend_maps_after_one_pass_and_the_next_tender_needs_none(
        self,
        session: Session,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants Pte. Ltd.")
        truth = tender(session, bid, store, fixtures.ALPHA)

        # The rules proposed five rows; the upright went to the model, on the worker.
        statuses = [(e.description, e.status) for e in entries(session, bid)]
        assert ("SPRINKLER - UP TYPE", "awaiting_model") in statuses
        assert model_jobs(session, bid) >= 1
        routed, _ = router(UPRIGHT)
        ask_the_model(session, store, bid, routed)
        assert {e.status for e in entries(session, bid)} == {"proposed"}

        before = service.counts(session, bid.id)
        assert before.counted == {}, "nothing is counted while it is only proposed"

        confirm_everything(session, bid)

        found = service.counts(session, bid.id)
        assert {key: item.count for key, item in found.counted.items()} == truth.counts
        [mystery] = found.unmapped
        assert (mystery.block, mystery.instances) == (fixtures.ALPHA.mystery_block, 3)

        # Tender 2: another project, the same consultant spelt another way.
        rows, calls = mapping_rows(session), 0
        from_consultant(session, second_bid, "ALPHA CONSULTANTS")
        second_truth = tender(session, second_bid, store, fixtures.ALPHA)

        assert {e.status for e in entries(session, second_bid)} == {"reused"}
        assert mapping_rows(session) == rows, "no new proposals"
        assert model_jobs(session, second_bid) == calls, "no model calls"
        second = service.counts(session, second_bid.id)
        assert {key: item.count for key, item in second.counted.items()} == second_truth.counts
        assert [group.block for group in second.unmapped] == [fixtures.ALPHA.mystery_block]

    def test_another_consultants_symbols_are_not_reused(
        self,
        session: Session,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)
        ask_the_model(session, store, bid, router(UPRIGHT)[0])
        confirm_everything(session, bid)

        from_consultant(session, second_bid, "Beta Engineering")
        tender(session, second_bid, store, fixtures.BETA)

        assert {e.status for e in entries(session, second_bid)} == {"proposed"}
        assert service.counts(session, second_bid.id).counted == {}


@pytest.mark.req("FR-VIS-02")
class TestUnmappedIsNeverCounted:
    def test_an_instance_with_no_confirmed_mapping_is_listed_not_counted(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        plan, truth = fixtures.plan_sheet(fixtures.ALPHA, with_legend=False)
        read(session, bid, store, "FP-L05-201", plan)  # a plan with no legend anywhere

        found = service.counts(session, bid.id)

        assert found.counted == {}
        assert sum(group.instances for group in found.unmapped) == sum(truth.counts.values()) + 3
        assert {group.status for group in found.unmapped} == {"no legend"}

    def test_a_legend_read_after_its_plan_still_maps_the_plan(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
    ) -> None:
        """Files arrive in any order: the plan first, its legend sheet after."""
        from_consultant(session, bid, "Alpha Consultants")
        plan, truth = fixtures.plan_sheet(fixtures.ALPHA, with_legend=False)
        read(session, bid, store, "FP-L05-201", plan)
        read(session, bid, store, "FP-LEG-001", fixtures.legend_sheet(fixtures.ALPHA))
        ask_the_model(session, store, bid, router(UPRIGHT)[0])
        confirm_everything(session, bid)

        found = service.counts(session, bid.id)

        assert {key: item.count for key, item in found.counted.items()} == truth.counts

    def test_a_rejected_mapping_stays_unmapped(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)
        ask_the_model(session, store, bid, router(UPRIGHT)[0])
        upright = next(e for e in entries(session, bid) if "UP TYPE" in e.description)
        assert upright.mapping_lineage_id is not None
        service.reject(session, upright.mapping_lineage_id, ESTIMATOR, note="not sure")
        confirm_everything(session, bid)

        found = service.counts(session, bid.id)

        assert "sprinkler_upright" not in found.counted
        rejected = [group for group in found.unmapped if group.status == "rejected"]
        assert rejected and rejected[0].instances == len(fixtures.PLACEMENTS)


@pytest.mark.req("FR-VIS-02")
class TestProposals:
    def test_a_model_proposal_carries_model_prompt_version_and_confidence(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        read(session, bid, store, "FP-LEG-001", fixtures.legend_sheet(fixtures.ALPHA))
        routed, adapter = router(UPRIGHT)
        ask_the_model(session, store, bid, routed)

        upright = next(e for e in entries(session, bid) if "UP TYPE" in e.description)
        assert upright.mapping_lineage_id is not None
        mapping = service.current(session, upright.mapping_lineage_id)
        assert mapping is not None
        assert (mapping.source, mapping.state, mapping.object_type_key) == (
            "model",
            "proposed",
            "sprinkler_upright",
        )
        assert mapping.provenance["model"] == "vision-1"
        prompt = prompt_for("symbol_map")
        assert prompt is not None
        assert mapping.provenance["prompt_version"] == prompt.version, "the prompt's content hash"
        assert mapping.provenance["confidence"] == 0.82
        assert mapping.provenance["agent_run_id"]
        assert len(adapter.calls) == 1, "one model call for the one undecided row"

    @pytest.mark.req("NFR-01")
    def test_a_proposal_reaches_its_row_s_instances_without_matching_the_bid_again(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from firebid.db.models.symbols import SymbolInstance

        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)

        def state() -> dict[int, tuple[Any, Any, str]]:
            session.expire_all()
            return {
                i.id: (i.legend_entry_id, i.mapping_lineage_id, i.symbol_key)
                for i in session.execute(
                    select(SymbolInstance).where(SymbolInstance.bid_id == bid.id)
                ).scalars()
            }

        # The rows the rules could not decide: the legend sheet's and the plan's own.
        waiting = {e.id for e in entries(session, bid) if e.status == "awaiting_model"}
        drawn = {key for key, (row, _, _) in state().items() if row in waiting}
        assert waiting and drawn, "the plan draws a symbol whose row waits for the model"
        assert {state()[key][1] for key in drawn} == {None}

        # Matching every instance of the bid again is what this must not do: on a real
        # tender that is 150,000 instances for each proposal.
        def refuse(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("a proposal matched the whole bid again")

        monkeypatch.setattr(service, "match_instances", refuse)
        routed, _ = router(UPRIGHT)
        ask_the_model(session, store, bid, routed)
        monkeypatch.undo()

        linked = state()
        lineage_of = {e.id: e.mapping_lineage_id for e in entries(session, bid)}
        for key in drawn:
            row, lineage, _ = linked[key]
            assert lineage is not None and lineage == lineage_of[row]
        # And it is what matching the whole bid again gives.
        service.match_instances(session, bid.id, service.consultant_of(session, bid.id))
        session.commit()
        assert state() == linked

    def test_a_rule_proposal_carries_the_rule_version(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        read(session, bid, store, "FP-LEG-001", fixtures.legend_sheet(fixtures.ALPHA))

        gate = next(e for e in entries(session, bid) if e.description == "GATE VALVE")
        assert gate.mapping_lineage_id is not None
        mapping = service.current(session, gate.mapping_lineage_id)
        assert mapping is not None
        assert (mapping.source, mapping.object_type_key) == ("rule", "gate_valve")
        assert str(mapping.provenance["rule_version"]).startswith("rules-")

    def test_a_model_answer_outside_the_library_is_no_answer(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        read(session, bid, store, "FP-LEG-001", fixtures.legend_sheet(fixtures.ALPHA))
        routed, _ = router({"object_type": "fire_hydrant_pillar", "confidence": 0.9})
        ask_the_model(session, store, bid, routed)

        upright = next(e for e in entries(session, bid) if "UP TYPE" in e.description)
        mapping = service.current(session, upright.mapping_lineage_id)  # type: ignore[arg-type]
        assert mapping is not None and mapping.object_type_key is None
        with pytest.raises(service.MappingError, match="say which object type"):
            service.confirm(session, mapping.lineage_id, ESTIMATOR)


@pytest.mark.req("FR-ADM-02")
class TestMappingHistory:
    def test_a_correction_is_a_new_version_and_the_earlier_one_reads_back(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        read(session, bid, store, "FP-LEG-001", fixtures.legend_sheet(fixtures.ALPHA))
        gate = next(e for e in entries(session, bid) if e.description == "GATE VALVE")
        lineage = gate.mapping_lineage_id
        assert lineage is not None

        service.confirm(session, lineage, ESTIMATOR)
        service.confirm(
            session,
            lineage,
            ESTIMATOR,
            object_type="butterfly_valve",
            note="the consultant's gate valve symbol is used for butterfly valves",
        )

        versions = service.history(session, lineage)
        assert [(v.version, v.state, v.object_type_key, v.source) for v in versions] == [
            (1, "proposed", "gate_valve", "rule"),
            (2, "confirmed", "gate_valve", "rule"),
            (3, "confirmed", "butterfly_valve", "person"),
        ]
        assert versions[1].confirmed_by == "Esther Tan"
        assert versions[2].supersedes_id == versions[1].id
        actions = session.execute(
            select(AuditEvent.action).where(AuditEvent.entity_id == str(versions[2].id))
        ).scalars()
        assert list(actions) == ["symbol mapping: corrected"]

    def test_a_changed_description_is_proposed_for_that_project_only(
        self,
        session: Session,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        router: Callable[..., tuple[Router, FakeAdapter]],
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)
        ask_the_model(session, store, bid, router(UPRIGHT)[0])
        confirm_everything(session, bid)

        changed = fixtures.Consultant(
            fixtures.ALPHA.name,
            tuple(
                fixtures.Symbol(s.block, "GATE VALVE (LOCKED OPEN)", s.object_type, s.draw)
                if s.block == "VLV-GATE"
                else s
                for s in fixtures.ALPHA.symbols
            ),
        )
        from_consultant(session, second_bid, "Alpha Consultants")
        read(session, second_bid, store, "FP-LEG-001", fixtures.legend_sheet(changed))

        second = {e.description: e for e in entries(session, second_bid)}
        assert second["GATE VALVE (LOCKED OPEN)"].status == "proposed"
        assert {e.status for d, e in second.items() if d != "GATE VALVE (LOCKED OPEN)"} == {
            "reused"
        }
        proposal = service.current(session, second["GATE VALVE (LOCKED OPEN)"].mapping_lineage_id)  # type: ignore[arg-type]
        assert proposal is not None
        assert proposal.project_id == second_bid.project_id
        assert proposal.object_type_key == "gate_valve", "offered the consultant's own answer"
        first = {e.description: e for e in entries(session, bid)}
        original = service.current(session, first["GATE VALVE"].mapping_lineage_id)  # type: ignore[arg-type]
        assert original is not None and original.state == "confirmed", "left alone"


def test_consultant_names_are_normalised() -> None:
    assert service.consultant_key("Alpha Consultants Pte. Ltd.") == "ALPHA CONSULTANTS"
    assert service.consultant_key("ALPHA CONSULTANTS PTE LTD") == "ALPHA CONSULTANTS"
    assert service.consultant_key("Beta Engineering (S) Private Limited") == "BETA ENGINEERING"


class _Broken:
    """A gateway whose provider cannot even start, as with no credentials configured."""

    def generate(self, *_args: Any, **_kwargs: Any) -> Any:
        raise TypeError("Could not resolve authentication method")


@pytest.mark.req("FR-VIS-02")
def test_a_failing_model_leaves_the_row_with_a_person_not_waiting_for_ever(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    from firebid.db.models.workflow import AgentRun, HumanTask

    from_consultant(session, bid, "Alpha Consultants")
    read(session, bid, store, "FP-LEG-001", fixtures.legend_sheet(fixtures.ALPHA))
    upright = next(e for e in entries(session, bid) if "UP TYPE" in e.description)

    service.propose_with_model(session, store, upright, _Broken())
    session.commit()

    assert upright.status == "proposed"
    mapping = service.current(session, upright.mapping_lineage_id)  # type: ignore[arg-type]
    assert mapping is not None and mapping.object_type_key is None
    assert "authentication" in str(mapping.provenance["error"])
    run = session.execute(select(AgentRun).where(AgentRun.agent == "symbol_mapper")).scalar_one()
    assert (run.state, run.error_type) == ("escalated", "TypeError")
    assert session.execute(select(HumanTask)).scalars().first() is not None


@pytest.mark.req("NFR-01")
class TestMatchingATender:
    """Matching every instance of a bid, as each document's finish does: 150,000 instances
    on a real tender. It reads plain columns, works each shape out once, and writes only
    what changed."""

    @staticmethod
    def state(session: Session, bid: Bid) -> dict[int, tuple[Any, ...]]:
        return {
            row[0]: tuple(row[1:])
            for row in session.execute(
                text(
                    "SELECT id, legend_entry_id, mapping_lineage_id, symbol_key, match_distance "
                    "FROM symbol_instance WHERE bid_id = :bid"
                ),
                {"bid": bid.id},
            )
        }

    def test_matching_again_writes_nothing(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from sqlalchemy import event

        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)
        consultant = service.consultant_of(session, bid.id)
        service.match_instances(session, bid.id, consultant)
        session.commit()
        before = self.state(session, bid)
        statements: list[str] = []

        def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            service.match_instances(session, bid.id, consultant)
            session.flush()
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert not [s for s in statements if s.lstrip().upper().startswith("UPDATE")]
        # Every instance is read once, as a digest of its shape; a shape's own text is read
        # only for the first instance of each shape, a few statements for the whole bid.
        reads = [s for s in statements if "FROM symbol_instance" in s]
        every = [s for s in reads if "md5(" in s]
        assert len(every) == 1 and "WHERE symbol_instance.bid_id" in every[0]
        assert all("symbol_instance.id IN" in s for s in reads if s not in every)
        assert len(reads) - 1 <= 2
        assert self.state(session, bid) == before

    @pytest.mark.req("NFR-01")
    def test_a_shape_s_text_is_read_once_a_shape_however_the_shapes_are_batched(
        self, session: Session, bid: Bid, store: MemoryObjectStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Found on a real tender of 450,000 instances: every instance's shape was read as
        # text, three gigabytes of it, once for every document of the tender.
        from sqlalchemy import event

        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)
        consultant = service.consultant_of(session, bid.id)
        service.match_instances(session, bid.id, consultant)
        session.commit()
        before = self.state(session, bid)
        instances, shapes = session.execute(
            text(
                "SELECT count(*), count(DISTINCT signature::text) FROM symbol_instance "
                "WHERE bid_id = :bid"
            ),
            {"bid": bid.id},
        ).one()
        assert instances > shapes > 3, "the plan draws each of several shapes many times"

        session.execute(
            text(
                "UPDATE symbol_instance SET legend_entry_id = NULL, mapping_lineage_id = NULL,"
                " match_distance = NULL, symbol_key = 'unset' WHERE bid_id = :bid"
            ),
            {"bid": bid.id},
        )
        monkeypatch.setattr(service, "READ_CHUNK", 3)
        fetched: list[int] = []

        def record(_conn: Any, _cursor: Any, statement: str, parameters: Any, *_rest: Any) -> None:
            if "FROM symbol_instance" in statement and "symbol_instance.id IN" in statement:
                fetched.append(len(parameters))

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            service.match_instances(session, bid.id, consultant)
            session.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)

        # Three shapes a statement, every shape once, and the same matches as before.
        assert sum(fetched) == shapes and max(fetched) <= 3
        assert self.state(session, bid) == before

    def test_matching_from_nothing_gives_what_was_matched_and_the_same_every_time(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)
        consultant = service.consultant_of(session, bid.id)
        service.match_instances(session, bid.id, consultant)
        session.commit()
        before = self.state(session, bid)
        assert {v[0] for v in before.values()} - {None}, "the legend claims some symbols"

        for _ in range(2):
            session.execute(
                text(
                    "UPDATE symbol_instance SET legend_entry_id = NULL, mapping_lineage_id = NULL,"
                    " match_distance = NULL, symbol_key = 'unset' WHERE bid_id = :bid"
                ),
                {"bid": bid.id},
            )
            service.match_instances(session, bid.id, consultant)
            session.commit()
            assert self.state(session, bid) == before

    def test_copies_of_one_unexplained_shape_are_raised_as_one(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        from_consultant(session, bid, "Alpha Consultants")
        tender(session, bid, store, fixtures.ALPHA)
        service.match_instances(session, bid.id, service.consultant_of(session, bid.id))
        session.commit()

        keys_of_shape: dict[str, set[str]] = {}
        for shape, key in session.execute(
            text(
                "SELECT signature::text, symbol_key FROM symbol_instance "
                "WHERE bid_id = :bid AND legend_entry_id IS NULL AND mapping_lineage_id IS NULL"
            ),
            {"bid": bid.id},
        ):
            keys_of_shape.setdefault(shape, set()).add(key)

        assert keys_of_shape, "the plan draws symbols no legend explains"
        assert all(len(keys) == 1 for keys in keys_of_shape.values())
