# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Specification attributes through the database: read, cited, checked, verified, queried
(FR-SPEC-01, FR-SPEC-05).

The synthetic specification is uploaded like any tender document. It is classified as a
specification, which queues `spec.read`; reading it stores its clause tree and proposes its
attributes by rule. Nothing reaches takeoff until a person verifies it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.metering import PostgresMeter
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, DocumentRevision
from firebid.db.models.specs import SpecAttribute, SpecClause
from firebid.db.models.workflow import AgentRun
from firebid.domain.actors import Actor
from firebid.evals.synthetic_spec import EXPECTED, specification_docx
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services import specs
from firebid.services.classification import classify_in_sandbox
from firebid.services.ingestion import Ingestor
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_sheet_views import app, sign_in  # noqa: F401

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
    model_id: reader-1
    capabilities: [structured_output]
    prices:
      - {effective_from: 2026-01-01, input_per_mtok: 5.0, output_per_mtok: 25.0}
routes:
  spec_attribute_extract:
    requires: [structured_output]
    data_class: confidential
    models: [first]
    reasoning: low
    prompt: spec_attribute_extract
"""


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


def uploaded(session: Session, bid: Bid, store: MemoryObjectStore) -> Document:
    payload = specification_docx()
    document = (
        Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id)
        .ingest("Particular Specification Fire Protection Rev B.docx", payload)
        .stored[0]
    )
    classify_in_sandbox(session, document, payload)
    session.commit()
    return document


def read(session: Session, bid: Bid, store: MemoryObjectStore) -> DocumentRevision:
    document = uploaded(session, bid, store)
    revision = specs.read_specification(session, store, document)
    assert revision is not None
    revision.state = "current"  # settled as the specification register settles it
    session.commit()
    return revision


def verify_all(session: Session, bid: Bid) -> None:
    for row in specs.current_attributes(session, bid.id):
        specs.decide(session, row.lineage_id, ESTIMATOR, verdict="confirm")
    session.commit()


@pytest.mark.req("FR-SPEC-01")
class TestReading:
    def test_classifying_a_specification_queues_its_reading(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        document = uploaded(session, bid, store)

        queued = session.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'spec.read' "
                "AND args->>'document_id' = :document"
            ),
            {"document": str(document.id)},
        ).scalar_one()

        assert document.doc_type == "specification"
        assert queued == 1

    def test_the_clause_tree_and_every_attribute_are_stored_as_proposals(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        revision = read(session, bid, store)

        clauses = session.execute(
            select(SpecClause).where(SpecClause.document_revision_id == revision.id)
        ).scalars()
        attributes = specs.current_attributes(session, bid.id)

        assert len(list(clauses)) == 18
        assert len(attributes) == len(EXPECTED)
        assert {row.state for row in attributes} == {"proposed"}
        assert all(row.citation_ok for row in attributes)
        assert {row.method for row in attributes} == {"rule"}

    def test_reading_twice_changes_nothing(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        revision = read(session, bid, store)
        document = session.get(Document, revision.document_id)
        assert document is not None

        specs.read_specification(session, store, document)

        assert len(specs.current_attributes(session, bid.id)) == len(EXPECTED)


@pytest.mark.req("FR-SPEC-01")
class TestWhatTakeoffReads:
    def test_nothing_is_specified_until_a_person_verifies_it(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        read(session, bid, store)

        found = specs.attributes_for(session, bid.id, "sprinkler", 50)

        assert {answer.value for answer in found.values()} == {specs.NOT_SPECIFIED}

    def test_verified_attributes_follow_the_size_ranges(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        read(session, bid, store)
        verify_all(session, bid)

        small = specs.attributes_for(session, bid.id, "sprinkler", 50)
        large = specs.attributes_for(session, bid.id, "sprinkler", 80)
        hydrant = specs.attributes_for(session, bid.id, "hydrant", 150)

        assert (small["joining_method"].value, small["pipe_class"].value) == ("threaded", "Heavy")
        assert (large["joining_method"].value, large["pipe_class"].value) == (
            "grooved",
            "Schedule 40",
        )
        assert small["pipe_material"].value == "black_steel", "the car park clause is conditional"
        assert hydrant["joining_method"].value == "flanged"
        assert specs.attributes_for(session, bid.id, "fire_pump", 100)["pipe_material"].value == (
            specs.NOT_SPECIFIED
        )

    def test_a_place_limited_attribute_applies_only_there(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        read(session, bid, store)
        verify_all(session, bid)

        car_park = specs.attributes_for(
            session, bid.id, "sprinkler", 50, condition="basement car park"
        )

        assert set(car_park["pipe_material"].values) == {"black_steel", "galvanised_steel"}


@pytest.mark.req("FR-SPEC-05")
class TestCitations:
    def test_every_attribute_cites_document_clause_anchor_and_revision(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        revision = read(session, bid, store)
        verify_all(session, bid)

        found = specs.attributes_for(session, bid.id, "sprinkler", 80)
        [citation] = found["joining_method"].citations

        assert citation.document_revision_id == revision.id
        assert citation.document_id == revision.document_id
        assert citation.clause == "2.1.2"
        assert citation.anchor == {"paragraph": 10}
        assert citation.revision_label == revision.revision_label
        assert "roll-grooved" in citation.quote

    def test_a_model_answer_citing_the_wrong_clause_is_downgraded_and_flagged(
        self, session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
    ) -> None:
        revision = read(session, bid, store)
        wrong = {
            "attributes": [
                {
                    "attribute": "joining_method",
                    "value": "grooved",
                    "clause": "3.1",  # the hose reel clause, which says screwed
                    "quote": "joined by grooved couplings",
                    "confidence": 0.95,
                }
            ]
        }

        [stored] = specs.read_attributes_with_model(
            session, revision.id, "hose_reel", ["3.1"], router(session, tmp_path, wrong)
        )

        assert stored.method == "model"
        assert stored.citation_ok is False
        assert stored.confidence == 0.2
        assert "does not state" in stored.citation_reason


@pytest.mark.req("FR-SPEC-01")
def test_a_model_call_records_its_route_provider_model_prompt_and_cost(
    session: Session, bid: Bid, store: MemoryObjectStore, tmp_path: Path
) -> None:
    revision = read(session, bid, store)
    answer = {
        "attributes": [
            {
                "attribute": "joining_method",
                "value": "threaded",
                "clause": "3.1",
                "quote": "joined by screwed fittings throughout",
                "confidence": 0.9,
            }
        ]
    }

    [stored] = specs.read_attributes_with_model(
        session, revision.id, "hose_reel", ["3.1"], router(session, tmp_path, answer)
    )
    session.commit()

    run = session.get(AgentRun, uuid.UUID(str(stored.provenance["agent_run_id"])))
    assert run is not None
    assert (run.route, run.provider, run.model) == ("spec_attribute_extract", "primary", "reader-1")
    assert run.prompt_version and run.cost_sgd is not None
    assert run.bid_id == bid.id
    # Metered onto the agent's own run, not a second record with no bid (the gateway takes
    # the run's call context from the agent runtime).
    runs = session.execute(
        select(AgentRun).where(AgentRun.route == "spec_attribute_extract")
    ).scalars()
    assert [r.id for r in runs] == [run.id]
    assert stored.citation_ok is True


def router(session: Session, tmp_path: Path, reply: dict[str, Any]) -> Router:
    path = tmp_path / "llm.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return Router(
        config=load_config(path),
        adapters={"primary": FakeAdapter("primary").reply(json.dumps(reply))},
        backoff_base_seconds=0,
        sleep=lambda _s: None,
        meter=PostgresMeter(session),
    )


@pytest.mark.req("FR-SPEC-01")
def test_a_decision_is_a_new_version_and_an_edit_is_checked_too(
    session: Session, bid: Bid, store: MemoryObjectStore
) -> None:
    read(session, bid, store)
    [joining] = [
        row
        for row in specs.current_attributes(session, bid.id)
        if row.system == "hose_reel" and row.attribute == "joining_method"
    ]

    edited = specs.decide(
        session, joining.lineage_id, ESTIMATOR, verdict="edit", value="grooved", note="per RFI"
    )

    history = specs.history(session, joining.lineage_id)
    assert [(v.version, v.state) for v in history] == [(1, "proposed"), (2, "verified")]
    assert (edited.method, edited.verified_by) == ("person", "Esther Tan")
    assert edited.citation_ok is False, "the clause still says screwed, and the record says so"
    _ = session.execute(select(SpecAttribute)).scalars()


@pytest.mark.req("FR-SPEC-05")
def test_the_api_resolves_every_citation_to_its_clause_text(
    session: Session, bid: Bid, store: MemoryObjectStore, sign_in: Any, organisation: Any
) -> None:
    from firebid.domain.state_machines import Role
    from tests.db.test_sheet_views import member

    read(session, bid, store)
    client = sign_in(member(session, organisation, bid, "ethan", Role.ESTIMATOR))

    rows = client.get(f"/bids/{bid.id}/spec/attributes").json()

    assert len(rows) == len(EXPECTED)
    for row in rows:
        clause = client.get(f"/bids/{bid.id}/spec/clauses/{row['clause_id']}").json()
        assert clause["number"] == row["clause_number"]
        assert row["quote"] in f"{clause['heading']} {clause['text']}"
        assert row["document_title"] and row["revision_label"] is not None


@pytest.mark.req("FR-SPEC-01")
def test_the_takeoff_view_shows_only_what_a_person_verified(
    session: Session, bid: Bid, store: MemoryObjectStore, sign_in: Any, organisation: Any
) -> None:
    from firebid.domain.state_machines import Role
    from tests.db.test_sheet_views import member

    read(session, bid, store)
    client = sign_in(member(session, organisation, bid, "ethan", Role.ESTIMATOR))
    before = client.get(f"/bids/{bid.id}/spec/for", params={"system": "sprinkler", "dn": 80})

    [joining] = [
        row
        for row in client.get(f"/bids/{bid.id}/spec/attributes").json()
        if row["attribute"] == "joining_method" and row["dn_min"] == 65
    ]
    decided = client.post(
        f"/bids/{bid.id}/spec/attributes/{joining['lineage_id']}/decide",
        json={"verdict": "confirm"},
    )
    after = client.get(f"/bids/{bid.id}/spec/for", params={"system": "sprinkler", "dn": 80})

    assert {a["value"] for a in before.json()} == {"not specified"}
    assert decided.status_code == 200 and decided.json()["verified_by"] == "Ethan"
    by_name = {a["attribute"]: a for a in after.json()}
    assert by_name["joining_method"]["value"] == "grooved"
    assert by_name["joining_method"]["citations"][0]["clause"] == "2.1.2"
    assert by_name["pipe_material"]["value"] == "not specified"
