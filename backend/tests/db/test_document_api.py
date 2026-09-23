"""The upload API: what an estimator sends, and what they are told came back (FR-DOC-01).

The report is the contract. Every file sent appears in exactly one bucket, and a refusal
carries a reason, because a tender set that silently loses four drawings is priced short.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from firebid.api import documents as documents_api
from firebid.api.app import create_app
from firebid.api.deps import get_principal, get_session
from firebid.auth.provisioning import Principal
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation, UserRole
from firebid.domain.state_machines import Role
from firebid.ingest.scanning import (
    AlwaysCleanScanner,
    Scanner,
    ScanResult,
    UnavailableScanner,
    Verdict,
)
from firebid.services.ingestion import checksum
from firebid.settings import Settings
from firebid.storage.object_store import MemoryObjectStore

SignIn = Callable[[Principal], TestClient]

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\ncontent\n"
EICAR = (r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE" + "!$H+H*").encode()


class EicarScanner(Scanner):
    def scan(self, payload: bytes) -> ScanResult:
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in payload:
            return ScanResult(Verdict.INFECTED, signature="Eicar-Test-Signature")
        return ScanResult(Verdict.CLEAN)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryObjectStore:
    """The API reaches for the real store; give it one that lives in a dict."""
    memory = MemoryObjectStore()
    monkeypatch.setattr(documents_api, "get_object_store", lambda: memory)
    return memory


@pytest.fixture
def scanner(monkeypatch: pytest.MonkeyPatch) -> Scanner:
    clean = AlwaysCleanScanner()
    monkeypatch.setattr(documents_api, "get_scanner", lambda: clean)
    return clean


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


@pytest.fixture
def estimator_principal(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username="ethan@firebid.test",
        display_name="Ethan Ng",
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
def director_principal(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    """A member of the bid whose role does not include uploading."""
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username="clara@firebid.test",
        display_name="Clara Wong",
    )
    session.add(person)
    session.flush()
    session.add(UserRole(user_id=person.id, role=str(Role.COMMERCIAL_DIRECTOR)))
    session.add(BidMember(bid_id=bid.id, user_id=person.id, role=str(Role.COMMERCIAL_DIRECTOR)))
    session.commit()
    return Principal(
        user_id=person.id,
        organisation_id=organisation.id,
        username=person.username,
        display_name=person.display_name,
        roles=frozenset({str(Role.COMMERCIAL_DIRECTOR)}),
    )


def zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def upload(client: TestClient, bid: Bid, files: list[tuple[str, bytes]]) -> dict[str, Any]:
    response = client.post(
        f"/bids/{bid.id}/documents",
        files=[("files", (name, payload, "application/octet-stream")) for name, payload in files],
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.req("FR-DOC-01")
def test_an_upload_reports_every_file_it_was_given(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)

    report = upload(client, bid, [("A-01.pdf", PDF), ("A-02.pdf", PDF + b"two")])

    assert report["accounted_for"] == 2
    assert len(report["stored"]) == 2
    assert {document["filename"] for document in report["stored"]} == {"A-01.pdf", "A-02.pdf"}


@pytest.mark.req("FR-DOC-01")
def test_a_whole_set_arrives_as_one_archive(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)
    payload = zip_of({f"A-{index:02d}.pdf": PDF + str(index).encode() for index in range(12)})

    report = upload(client, bid, [("tender-set.zip", payload)])

    assert len(report["stored"]) == 12
    listed = client.get(f"/bids/{bid.id}/documents").json()
    assert len(listed) == 12


@pytest.mark.req("FR-DOC-01")
def test_re_uploading_the_same_file_reports_it_as_a_duplicate(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)
    upload(client, bid, [("A-01.pdf", PDF)])

    report = upload(client, bid, [("A-01-copy.pdf", PDF)])

    assert report["stored"] == []
    assert len(report["duplicates"]) == 1
    assert store.writes == 1


@pytest.mark.req("NFR-06")
def test_a_quarantined_file_cannot_be_downloaded(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(documents_api, "get_scanner", EicarScanner)
    client = sign_in(estimator_principal)

    report = upload(client, bid, [("nasty.pdf", PDF + EICAR)])
    assert len(report["quarantined"]) == 1

    document = client.get(f"/bids/{bid.id}/documents").json()[0]
    assert document["state"] == "quarantined"

    refused = client.get(f"/bids/{bid.id}/documents/{document['id']}/content")
    assert refused.status_code == 403
    assert "quarantined" in refused.json()["detail"]


@pytest.mark.req("NFR-06")
def test_a_file_held_by_an_outage_is_not_downloadable_until_it_is_scanned(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(documents_api, "get_scanner", UnavailableScanner)
    client = sign_in(estimator_principal)

    report = upload(client, bid, [("A-01.pdf", PDF)])
    assert len(report["awaiting_scan"]) == 1

    document = client.get(f"/bids/{bid.id}/documents").json()[0]
    blocked = client.get(f"/bids/{bid.id}/documents/{document['id']}/content")
    assert blocked.status_code == 409

    # The scanner comes back, and a rescan releases the file.
    monkeypatch.setattr(documents_api, "get_scanner", AlwaysCleanScanner)
    assert client.post(f"/bids/{bid.id}/documents/rescan").json() == {"moved": 1}
    assert client.get(f"/bids/{bid.id}/documents/{document['id']}/content").status_code == 200


@pytest.mark.req("FR-DOC-01")
def test_a_presigned_upload_round_trips(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    """The browser puts the bytes in storage itself, then asks the API to register them."""
    client = sign_in(estimator_principal)
    digest = checksum(PDF)

    tickets = client.post(
        f"/bids/{bid.id}/documents/uploads",
        json=[{"filename": "A-01.pdf", "byte_size": len(PDF), "sha256": digest}],
    ).json()
    assert tickets[0]["upload_url"] is not None
    assert tickets[0]["already_uploaded"] is False

    # Stand in for the browser's PUT to the presigned URL.
    store.put(tickets[0]["storage_key"], PDF, content_type="application/pdf")

    report = client.post(
        f"/bids/{bid.id}/documents/uploads/complete",
        json=[
            {
                "filename": "A-01.pdf",
                "storage_key": tickets[0]["storage_key"],
                "sha256": digest,
            }
        ],
    ).json()
    assert len(report["stored"]) == 1
    assert report["stored"][0]["state"] == "received"


@pytest.mark.req("FR-DOC-01")
def test_a_file_already_on_the_bid_is_not_asked_for_again(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)
    upload(client, bid, [("A-01.pdf", PDF)])

    tickets = client.post(
        f"/bids/{bid.id}/documents/uploads",
        json=[{"filename": "A-01.pdf", "byte_size": len(PDF), "sha256": checksum(PDF)}],
    ).json()

    assert tickets[0]["already_uploaded"] is True
    assert tickets[0]["upload_url"] is None


@pytest.mark.req("NFR-06")
def test_completing_with_a_digest_that_does_not_match_the_bytes_is_refused(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    """The digest is recomputed, so a truncated transfer cannot register as a whole file."""
    client = sign_in(estimator_principal)
    digest = checksum(PDF)
    tickets = client.post(
        f"/bids/{bid.id}/documents/uploads",
        json=[{"filename": "A-01.pdf", "byte_size": len(PDF), "sha256": digest}],
    ).json()
    store.put(tickets[0]["storage_key"], PDF[:10], content_type="application/pdf")

    response = client.post(
        f"/bids/{bid.id}/documents/uploads/complete",
        json=[{"filename": "A-01.pdf", "storage_key": tickets[0]["storage_key"], "sha256": digest}],
    )

    assert response.status_code == 400
    assert "corrupted or truncated" in response.json()["detail"]


@pytest.mark.req("NFR-06")
def test_completing_against_a_key_from_somewhere_else_is_refused(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    """The key is derived from the bid and the digest; a chosen key would cross bids."""
    client = sign_in(estimator_principal)
    other = uuid.uuid4()
    store.put(f"bids/{other}/documents/{checksum(PDF)}.pdf", PDF, content_type="application/pdf")

    response = client.post(
        f"/bids/{bid.id}/documents/uploads/complete",
        json=[
            {
                "filename": "A-01.pdf",
                "storage_key": f"bids/{other}/documents/{checksum(PDF)}.pdf",
                "sha256": checksum(PDF),
            }
        ],
    )

    assert response.status_code == 400


@pytest.mark.req("NFR-06")
def test_a_non_member_cannot_upload_to_a_bid(
    sign_in: SignIn,
    session: Session,
    organisation: Organisation,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
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

    response = client.post(
        f"/bids/{bid.id}/documents",
        files=[("files", ("A-01.pdf", PDF, "application/pdf"))],
    )

    assert response.status_code == 404, "a non-member is not told the bid exists"


@pytest.mark.req("NFR-06")
def test_a_role_without_the_upload_permission_is_refused(
    sign_in: SignIn,
    director_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(director_principal)

    response = client.post(
        f"/bids/{bid.id}/documents",
        files=[("files", ("A-01.pdf", PDF, "application/pdf"))],
    )

    assert response.status_code == 403
    # Reading the bid's documents is still fine.
    assert client.get(f"/bids/{bid.id}/documents").status_code == 200
