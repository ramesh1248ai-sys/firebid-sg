"""The viewer's API: sheets, tiles and progress (FR-DOC-01, FR-DOC-07, NFR-06).

The access rule is the one worth guarding. Tiles are stored by content hash so an identical
drawing on two bids is stored once, which means the storage key cannot decide who may see it.
Every route resolves the sheet through the bid first, and the tests below try it the other
way round.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from firebid.api import documents as documents_api
from firebid.api import sheets as sheets_api
from firebid.api.app import create_app
from firebid.api.deps import get_principal, get_session
from firebid.auth.provisioning import Principal
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation, UserRole
from firebid.domain.state_machines import Role
from firebid.evals.synthetic import general_arrangement, write_pdf
from firebid.imaging.pyramid import Pyramid
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services.ingestion import Ingestor
from firebid.services.sheets import process_document
from firebid.settings import Settings
from firebid.storage.object_store import MemoryObjectStore

pytestmark = pytest.mark.req("FR-DOC-01")

SignIn = Callable[[Principal], TestClient]


@pytest.fixture(scope="module")
def vector_pdf(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    document, _ = general_arrangement(columns=6, rows=4)
    return write_pdf(document, tmp_path_factory.mktemp("pdf") / "ga.pdf").read_bytes()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryObjectStore:
    memory = MemoryObjectStore()
    monkeypatch.setattr(sheets_api, "get_object_store", lambda: memory)
    monkeypatch.setattr(documents_api, "get_object_store", lambda: memory)
    monkeypatch.setattr(documents_api, "get_scanner", AlwaysCleanScanner)
    return memory


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Iterator[Session]:
        with factory() as session:
            yield session
            session.commit()

    application = create_app(Settings(env="test"), health_checks={})
    application.dependency_overrides[get_session] = session_override
    return application


@pytest.fixture
def sign_in(app: FastAPI) -> SignIn:
    def _sign_in(principal: Principal) -> TestClient:
        app.dependency_overrides[get_principal] = lambda: principal
        return TestClient(app)

    return _sign_in


def make_member(session: Session, organisation: Organisation, bid: Bid, name: str) -> Principal:
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username=f"{name}@firebid.test",
        display_name=name.title(),
    )
    session.add(person)
    session.flush()
    session.add(UserRole(user_id=person.id, role=str(Role.ESTIMATOR)))
    session.add(BidMember(bid_id=bid.id, user_id=person.id, role=str(Role.ESTIMATOR)))
    session.commit()
    return Principal(
        user_id=person.id,
        organisation_id=organisation.id,
        username=person.username,
        display_name=person.display_name,
        roles=frozenset({str(Role.ESTIMATOR)}),
    )


@pytest.fixture
def estimator(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return make_member(session, organisation, bid, "ethan")


@pytest.fixture
def processed(
    session: Session, bid: Bid, store: MemoryObjectStore, vector_pdf: bytes
) -> dict[str, Any]:
    """A bid with one processed drawing on it."""
    outcome = Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id).ingest(
        "FP-L05-201.pdf", vector_pdf
    )
    document = outcome.stored[0]
    sheets = process_document(session, store, document).sheets
    session.commit()
    return {"document": document, "sheet": sheets[0]}


class TestListingSheets:
    def test_a_processed_drawing_appears_with_its_size(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)

        sheets = client.get(f"/bids/{bid.id}/sheets").json()

        assert len(sheets) == 1
        assert sheets[0]["width_mm"] > 100
        assert sheets[0]["content_class"] == "vector"
        assert sheets[0]["has_thumbnail"] is True

    def test_a_sheet_names_the_file_it_came_from(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)

        listed = client.get(f"/bids/{bid.id}/sheets").json()[0]
        detail = client.get(f"/bids/{bid.id}/sheets/{listed['id']}").json()

        assert listed["filename"] == "FP-L05-201.pdf"
        assert detail["filename"] == "FP-L05-201.pdf"

    def test_a_sheet_carries_its_lineage(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)

        sheets = client.get(f"/bids/{bid.id}/sheets").json()

        assert sheets[0]["source_ref"]["document_id"] == str(processed["document"].id)

    def test_sheets_can_be_filtered_to_one_document(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)
        document_id = processed["document"].id

        assert len(client.get(f"/bids/{bid.id}/sheets?document_id={document_id}").json()) == 1
        assert client.get(f"/bids/{bid.id}/sheets?document_id={uuid.uuid4()}").json() == []


class TestTheDescriptor:
    def test_it_tells_the_viewer_how_to_ask_for_tiles(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)
        sheet = processed["sheet"]

        detail = client.get(f"/bids/{bid.id}/sheets/{sheet.id}").json()

        source = detail["tile_source"]
        assert source["tileSize"] == 256
        assert source["maxLevel"] == sheet.max_level
        assert source["tileUrl"].endswith(f"/sheets/{sheet.id}/tiles")
        assert source["preRenderedLevels"]


class TestTiles:
    def test_a_pre_rendered_tile_is_served(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)
        sheet = processed["sheet"]

        response = client.get(f"/bids/{bid.id}/sheets/{sheet.id}/tiles/0/0_0.webp")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        with Image.open(io.BytesIO(response.content)) as tile:
            assert tile.format == "WEBP"

    def test_a_tile_is_cached_for_a_long_time(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        """A tile's URL contains its content hash, so it can never go stale."""
        client = sign_in(estimator)
        sheet = processed["sheet"]

        response = client.get(f"/bids/{bid.id}/sheets/{sheet.id}/tiles/0/0_0.webp")

        assert "immutable" in response.headers["cache-control"]
        assert "max-age=31536000" in response.headers["cache-control"]
        assert response.headers["etag"]

    def test_a_close_up_tile_is_rendered_on_request_and_then_cached(
        self,
        sign_in: SignIn,
        estimator: Principal,
        bid: Bid,
        processed: dict[str, Any],
        store: MemoryObjectStore,
    ) -> None:
        client = sign_in(estimator)
        sheet = processed["sheet"]
        pyramid = Pyramid(width_px=sheet.base_width_px, height_px=sheet.base_height_px)
        level = pyramid.on_demand_levels[0]

        first = client.get(f"/bids/{bid.id}/sheets/{sheet.id}/tiles/{level}/0_0.webp")
        writes_after_first = store.writes
        second = client.get(f"/bids/{bid.id}/sheets/{sheet.id}/tiles/{level}/0_0.webp")

        assert first.status_code == 200
        assert second.content == first.content
        assert store.writes == writes_after_first, "re-requesting must not re-render"

    def test_a_tile_outside_the_sheet_is_not_found(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)
        sheet = processed["sheet"]

        response = client.get(f"/bids/{bid.id}/sheets/{sheet.id}/tiles/0/9_9.webp")

        assert response.status_code == 404

    def test_the_thumbnail_is_served(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)
        sheet = processed["sheet"]

        response = client.get(f"/bids/{bid.id}/sheets/{sheet.id}/thumbnail.webp")

        assert response.status_code == 200
        with Image.open(io.BytesIO(response.content)) as image:
            assert max(image.size) <= 400


class TestWhoMaySee:
    def test_a_non_member_cannot_list_sheets(
        self,
        sign_in: SignIn,
        session: Session,
        organisation: Organisation,
        bid: Bid,
        processed: dict[str, Any],
    ) -> None:
        stranger = AppUser(
            organisation_id=organisation.id,
            external_id=uuid.uuid4().hex,
            username="stranger@firebid.test",
            display_name="Sam Stranger",
        )
        session.add(stranger)
        session.flush()
        session.add(UserRole(user_id=stranger.id, role=str(Role.ESTIMATOR)))
        session.commit()
        client = sign_in(
            Principal(
                user_id=stranger.id,
                organisation_id=organisation.id,
                username=stranger.username,
                display_name=stranger.display_name,
                roles=frozenset({str(Role.ESTIMATOR)}),
            )
        )

        assert client.get(f"/bids/{bid.id}/sheets").status_code == 404

    def test_a_sheet_cannot_be_read_through_another_bid(
        self,
        sign_in: SignIn,
        session: Session,
        organisation: Organisation,
        bid: Bid,
        second_bid: Bid,
        processed: dict[str, Any],
    ) -> None:
        """The tile key is a content hash, so the sheet's bid is what decides access."""
        other_member = make_member(session, organisation, second_bid, "olivia")
        client = sign_in(other_member)
        sheet = processed["sheet"]

        assert client.get(f"/bids/{second_bid.id}/sheets/{sheet.id}").status_code == 404
        assert (
            client.get(f"/bids/{second_bid.id}/sheets/{sheet.id}/tiles/0/0_0.webp").status_code
            == 404
        )


class TestProgress:
    def test_it_accounts_for_every_document(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, processed: dict[str, Any]
    ) -> None:
        client = sign_in(estimator)

        progress = client.get(f"/bids/{bid.id}/progress").json()

        assert progress["total"] == 1
        assert sum(progress["counts"].values()) == progress["total"]
        assert progress["counts"]["done"] == 1
        assert progress["sheets"] == 1
        assert progress["finished"] is True

    def test_a_refused_file_is_listed_with_its_reason(
        self,
        sign_in: SignIn,
        estimator: Principal,
        bid: Bid,
        session: Session,
        store: MemoryObjectStore,
    ) -> None:
        """A progress bar that reaches 100% while files sit rejected is worse than none."""
        client = sign_in(estimator)
        client.post(
            f"/bids/{bid.id}/documents",
            files=[("files", ("notes.exe", b"MZ\x90\x00" + b"\x00" * 64, "application/x-msdos"))],
        )

        progress = client.get(f"/bids/{bid.id}/progress").json()

        assert progress["counts"]["rejected"] == 1
        assert [failure["filename"] for failure in progress["failures"]] == ["notes.exe"]
        assert progress["failures"][0]["reason"]

    def test_a_bid_with_nothing_on_it_is_not_finished(
        self, sign_in: SignIn, estimator: Principal, bid: Bid
    ) -> None:
        client = sign_in(estimator)

        progress = client.get(f"/bids/{bid.id}/progress").json()

        assert progress["total"] == 0
        assert progress["finished"] is False

    def test_the_stream_sends_the_current_state_immediately(
        self,
        sign_in: SignIn,
        estimator: Principal,
        bid: Bid,
        processed: dict[str, Any],
        as_application_role: None,
    ) -> None:
        """The stream reads on its own session, so it must act as the caller to see anything."""
        import json

        client = sign_in(estimator)

        with client.stream("GET", f"/bids/{bid.id}/progress/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line.removeprefix("data: "))
                    assert payload["total"] == 1
                    assert payload["counts"]["done"] == 1
                    break

    def test_a_document_in_flight_shows_as_unfinished(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, session: Session
    ) -> None:
        from tests.db.factories import make_document

        document = make_document(session, bid)
        document.state = "received"
        session.commit()
        client = sign_in(estimator)

        progress = client.get(f"/bids/{bid.id}/progress").json()

        assert progress["finished"] is False
        assert progress["counts"]["received"] == 1

    def test_a_file_held_by_a_scanner_outage_does_not_read_as_in_flight(
        self, sign_in: SignIn, estimator: Principal, bid: Bid, session: Session
    ) -> None:
        """An outage can hold a file for hours; the page should say so, not spin."""
        from tests.db.factories import make_document

        document = make_document(session, bid)
        document.state = "awaiting_scan"
        session.commit()
        client = sign_in(estimator)

        progress = client.get(f"/bids/{bid.id}/progress").json()

        assert progress["finished"] is True
        assert progress["counts"]["awaiting_scan"] == 1
        assert [failure["state"] for failure in progress["failures"]] == ["awaiting_scan"]
