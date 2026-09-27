"""Sheet views through the database and the API: measuring, calibrating and locating.

The general arrangement arrives as a DXF, is read like any tender sheet (title block, then
geometry, then views), and is then measured on through the API. Its branches run 21 m each.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from firebid.api.app import create_app
from firebid.api.deps import get_principal, get_session
from firebid.auth.provisioning import Principal
from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation, UserRole
from firebid.db.models.documents import Sheet
from firebid.db.models.drawings import SheetGeometry, SheetView
from firebid.domain.state_machines import Role
from firebid.drawings import geometry
from firebid.drawings import views as view_detection
from firebid.evals import synthetic
from firebid.ingest.scanning import AlwaysCleanScanner
from firebid.services import views as view_service
from firebid.services.geometry import extract_all, load
from firebid.services.ingestion import Ingestor
from firebid.services.sheets import process_document
from firebid.services.title_blocks import read_title_blocks
from firebid.settings import Settings
from firebid.storage.object_store import MemoryObjectStore

SignIn = Callable[[Principal], TestClient]
BRANCH_MM = 7 * synthetic.SPACING_MM


@pytest.fixture(autouse=True)
def no_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    from firebid.services import sheets

    monkeypatch.setattr(sheets, "_render_low_levels", lambda *_args, **_kwargs: 0)


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


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


def member(
    session: Session, organisation: Organisation, bid: Bid, name: str, role: Role
) -> Principal:
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username=f"{name}@firebid.test",
        display_name=name.title(),
    )
    session.add(person)
    session.flush()
    session.add(UserRole(user_id=person.id, role=str(role)))
    session.add(BidMember(bid_id=bid.id, user_id=person.id, role=str(role)))
    session.commit()
    return Principal(
        user_id=person.id,
        organisation_id=organisation.id,
        username=person.username,
        display_name=person.display_name,
        roles=frozenset({str(role)}),
    )


@pytest.fixture
def estimator(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "ethan", Role.ESTIMATOR)


@pytest.fixture
def design_manager(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "dora", Role.DESIGN_MANAGER)


def read_sheet(
    session: Session, bid: Bid, store: MemoryObjectStore, document: object, name: str
) -> Sheet:
    """A drawing read as the parse job reads it: sheets, title block, geometry, views."""
    stored = (
        Ingestor(session, store, AlwaysCleanScanner(), bid_id=bid.id)
        .ingest(f"{name}.dxf", synthetic.dxf_bytes(document))  # type: ignore[arg-type]
        .stored[0]
    )
    sheets = process_document(session, store, stored).sheets
    read_title_blocks(session, store, stored, sheets)
    extract_all(session, store, stored, sheets)
    view_service.detect_all(session, store, sheets)
    session.commit()
    return sheets[0]


def plan(session: Session, bid: Bid, store: MemoryObjectStore, **options: object) -> Sheet:
    document, _ = synthetic.general_arrangement(**options)  # type: ignore[arg-type]
    return read_sheet(session, bid, store, document, "FP-L05-201")


def only_view(session: Session, sheet: Sheet) -> SheetView:
    return session.execute(select(SheetView).where(SheetView.sheet_id == sheet.id)).scalar_one()


def branches(session: Session, store: MemoryObjectStore, sheet: Sheet) -> list[list[list[float]]]:
    record = session.execute(
        select(SheetGeometry).where(SheetGeometry.sheet_id == sheet.id)
    ).scalar_one()
    lines = geometry.segments(load(store, record))
    wanted = np.flatnonzero(np.isclose(lines.lengths, BRANCH_MM / 100, atol=0.05))
    return [
        [[float(lines.x0[i]), float(lines.y0[i])], [float(lines.x1[i]), float(lines.y1[i])]]
        for i in wanted
    ]


def url(bid: Bid, sheet: Sheet, rest: str) -> str:
    return f"/bids/{bid.id}/sheets/{sheet.id}/{rest}"


@pytest.mark.req("FR-VIS-08")
class TestDetectedViews:
    def test_the_parse_pipeline_records_each_view_with_its_scale_and_grid(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        view = only_view(session, plan(session, bid, store))

        assert (view.kind, view.scale_status, view.denominator) == ("plan", "verified", 100.0)
        assert view.level == "L05"
        assert view.grid is not None and view.grid_box is not None
        assert view.scale_evidence["evidence"], "the dimensions it was verified from are kept"

    def test_views_are_listed_through_the_api(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        design_manager: Principal,
    ) -> None:
        sheet = plan(session, bid, store)

        [view] = sign_in(design_manager).get(url(bid, sheet, "views")).json()

        assert view["kind"] == "plan"
        assert view["measurable"] is True
        assert view["title"] == "LEVEL 5 FIRE SPRINKLER LAYOUT PLAN"

    def test_another_bids_sheet_is_not_found(
        self,
        session: Session,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        estimator: Principal,
    ) -> None:
        sheet = plan(session, bid, store)

        response = sign_in(estimator).get(f"/bids/{second_bid.id}/sheets/{sheet.id}/views")

        assert response.status_code in (403, 404)


@pytest.mark.req("FR-VIS-05")
class TestMeasuring:
    def test_a_verified_view_measures_a_branch_within_half_a_percent(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        design_manager: Principal,
    ) -> None:
        sheet = plan(session, bid, store)
        view = only_view(session, sheet)

        for run in branches(session, store, sheet):
            response = sign_in(design_manager).post(
                url(bid, sheet, f"views/{view.id}/measure"), json={"points": run}
            )
            assert response.status_code == 200
            assert response.json()["length_mm"] == pytest.approx(BRANCH_MM, rel=0.005)

    def test_an_unverified_view_is_refused_with_the_reason(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        estimator: Principal,
    ) -> None:
        sheet = plan(session, bid, store, with_grid=False, with_dimensions=False, view_title=None)
        view = only_view(session, sheet)
        run = branches(session, store, sheet)[0]

        response = sign_in(estimator).post(
            url(bid, sheet, f"views/{view.id}/measure"), json={"points": run}
        )

        assert view.scale_status == "unverified"
        assert response.status_code == 409
        assert "unverified" in response.json()["detail"]
        assert "Calibrate" in response.json()["detail"]

    def test_a_not_to_scale_view_is_refused_and_cannot_be_calibrated(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        estimator: Principal,
    ) -> None:
        document, _ = synthetic.not_to_scale_sheet()
        sheet = read_sheet(session, bid, store, document, "FP-SCH-001")
        view = only_view(session, sheet)
        client = sign_in(estimator)

        measured = client.post(
            url(bid, sheet, f"views/{view.id}/measure"), json={"points": [[0, 0], [10, 0]]}
        )
        calibrated = client.post(
            url(bid, sheet, f"views/{view.id}/calibrate"),
            json={"points": [[0, 0], [10, 0]], "distance_mm": 1000},
        )

        assert view.scale_status == "nts"
        assert measured.status_code == 409
        assert calibrated.status_code == 409


@pytest.mark.req("FR-VIS-05")
class TestCalibration:
    def test_calibration_unlocks_measurement_and_records_who(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        estimator: Principal,
    ) -> None:
        sheet = plan(session, bid, store, with_grid=False, with_dimensions=False, view_title=None)
        view = only_view(session, sheet)
        first, second, *_ = branches(session, store, sheet)
        client = sign_in(estimator)

        calibrated = client.post(
            url(bid, sheet, f"views/{view.id}/calibrate"),
            json={"points": first, "distance_mm": BRANCH_MM},
        )
        measured = client.post(url(bid, sheet, f"views/{view.id}/measure"), json={"points": second})

        assert calibrated.status_code == 200
        body = calibrated.json()
        assert (body["scale_status"], body["calibrated_by"]) == ("calibrated", "Ethan")
        assert body["measurable"] is True
        assert measured.status_code == 200
        assert measured.json()["length_mm"] == pytest.approx(BRANCH_MM, rel=0.005)
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "view: calibrated")
        ).scalar_one()
        assert event.actor_label == "Ethan"

    def test_only_a_document_reviewer_may_calibrate(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        design_manager: Principal,
    ) -> None:
        sheet = plan(session, bid, store, with_grid=False, with_dimensions=False, view_title=None)
        view = only_view(session, sheet)

        response = sign_in(design_manager).post(
            url(bid, sheet, f"views/{view.id}/calibrate"),
            json={"points": [[0, 0], [10, 0]], "distance_mm": 1000},
        )

        assert response.status_code == 403

    def test_a_calibration_survives_the_views_being_detected_again(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        estimator: Principal,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sheet = plan(session, bid, store, with_grid=False, with_dimensions=False, view_title=None)
        view = only_view(session, sheet)
        view_service.calibrate(session, view, ((0.0, 0.0), (30.0, 0.0)), 3000.0, estimator.actor())
        session.commit()

        monkeypatch.setattr(view_detection, "DETECTOR_VERSION", "test-bump")
        view_service.detect_all(session, store, [sheet])
        session.commit()

        again = only_view(session, sheet)
        assert again.detector_version == "test-bump"
        assert (again.scale_status, again.denominator) == ("calibrated", 100.0)
        assert again.calibrated_by == "Ethan"


@pytest.mark.req("FR-VIS-07")
class TestLocating:
    def test_a_point_is_given_its_grid_reference_and_level(
        self,
        session: Session,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        design_manager: Principal,
    ) -> None:
        sheet = plan(session, bid, store)
        grid = only_view(session, sheet).grid
        assert grid is not None
        (_, b), (_, two) = grid["across"][1], grid["up"][1]  # type: ignore[index]

        on_line = (
            sign_in(design_manager).get(url(bid, sheet, "locate"), params={"x": b, "y": two}).json()
        )
        in_bay = (
            sign_in(design_manager)
            .get(url(bid, sheet, "locate"), params={"x": b + 15, "y": two + 15})
            .json()
        )

        assert on_line["grid_reference"] == "Grid B2"
        assert in_bay["grid_reference"] == "Grid B1\N{EN DASH}C2"
        assert on_line["level"] == "L05"
        assert on_line["view_kind"] == "plan"
