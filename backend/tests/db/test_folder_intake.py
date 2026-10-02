# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Folder intake through the API: paths kept, and only tender documents read (FR-DOC-09, 10).

The folder is the shape of a real one: the client's drawings, the company's marked-up copies
of the same drawings, a response workbook, and an office lock file.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.auth.provisioning import Principal
from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Bid
from firebid.db.models.documents import Document
from firebid.ingest.scanning import Scanner
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_document_api import (  # noqa: F401
    PDF,
    SignIn,
    app,
    estimator_principal,
    parse_jobs,
    scanner,
    sign_in,
    store,
    zip_of,
)

TENDER = "MOH/SPK Drawing/L10/A03-10-01.pdf"
MARKED_UP = "MOH/Rev B/L10/A03-10-01_SJME-B.pdf"
RESPONSE = "MOH/SJME_RFP_Response_v7.pdf"
LOCK = "MOH/~$RFP Response.xlsx"


def send(
    client: TestClient,
    bid: Bid,
    files: list[tuple[str, bytes]],
    origins: list[str] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"paths": [path for path, _ in files]}
    if origins is not None:
        data["origins"] = origins
    response = client.post(
        f"/bids/{bid.id}/documents",
        files=[
            ("files", (path.rsplit("/", 1)[-1], payload, "application/octet-stream"))
            for path, payload in files
        ],
        data=data,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def by_path(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {document["source_path"]: document for document in report["stored"]}


@pytest.mark.req("FR-DOC-09")
def test_a_folder_s_files_keep_their_paths_and_every_one_is_accounted_for(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)

    report = send(
        client,
        bid,
        [(TENDER, PDF), ("MOH/SPK Drawing/B1/A03-B1-01.pdf", PDF + b"b1"), (LOCK, b"lock")],
    )

    assert report["accounted_for"] == 3
    assert set(by_path(report)) == {TENDER, "MOH/SPK Drawing/B1/A03-B1-01.pdf"}
    assert [item["filename"] for item in report["ignored"]] == ["~$RFP Response.xlsx"]


@pytest.mark.req("FR-DOC-10")
def test_only_tender_documents_are_queued_to_be_read(
    session: Session,
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)

    report = send(
        client,
        bid,
        [(TENDER, PDF), (MARKED_UP, PDF + b"markup"), (RESPONSE, PDF + b"response")],
        origins=["tender", "working", "reference"],
    )

    stored = by_path(report)
    assert {path: doc["origin"] for path, doc in stored.items()} == {
        TENDER: "tender",
        MARKED_UP: "working",
        RESPONSE: "reference",
    }
    assert {doc["origin_status"] for doc in stored.values()} == {"confirmed"}
    assert len(parse_jobs(session, stored[TENDER]["id"])) == 1
    assert parse_jobs(session, stored[MARKED_UP]["id"]) == []
    assert parse_jobs(session, stored[RESPONSE]["id"]) == []
    # Kept and settled: the set does not wait on a document nobody will read.
    assert stored[MARKED_UP]["state"] == "done"
    assert client.get(f"/bids/{bid.id}/progress").json()["counts"]["received"] == 1


@pytest.mark.req("FR-DOC-10")
def test_a_marked_up_copy_sent_without_an_origin_waits_for_a_person(
    session: Session,
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)

    report = send(client, bid, [(TENDER, PDF), (MARKED_UP, PDF + b"markup")])

    marked = by_path(report)[MARKED_UP]
    assert (marked["origin"], marked["origin_status"]) == ("working", "proposed")
    assert "our working document" in marked["origin_reason"]
    assert parse_jobs(session, marked["id"]) == []


@pytest.mark.req("FR-DOC-10")
def test_a_marked_up_copy_inside_a_tender_archive_is_not_read_either(
    session: Session,
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)
    archive = zip_of({"L10/A03-10-01.pdf": PDF, "Rev B/A03-10-01_SJME-B.pdf": PDF + b"markup"})

    report = send(client, bid, [("MOH/tender.zip", archive)], origins=["tender"])

    stored = by_path(report)
    assert stored["MOH/tender.zip/L10/A03-10-01.pdf"]["origin_status"] == "confirmed"
    marked = stored["MOH/tender.zip/Rev B/A03-10-01_SJME-B.pdf"]
    assert (marked["origin"], marked["origin_status"]) == ("working", "proposed")
    assert parse_jobs(session, marked["id"]) == []


@pytest.mark.req("FR-DOC-10")
def test_a_person_says_it_is_a_tender_document_and_it_is_read(
    session: Session,
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)
    marked = by_path(send(client, bid, [(MARKED_UP, PDF + b"markup")]))[MARKED_UP]

    response = client.post(
        f"/bids/{bid.id}/documents/origin",
        json={"document_ids": [marked["id"]], "origin": "tender", "reason": "client reissue"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"changed": [marked["id"]], "refused": {}}
    session.expire_all()
    document = session.get(Document, marked["id"])
    assert document is not None
    assert (document.origin, document.origin_status, document.state) == (
        "tender",
        "confirmed",
        "received",
    )
    assert document.origin_by == estimator_principal.display_name
    assert len(parse_jobs(session, marked["id"])) == 1
    event = session.execute(
        select(AuditEvent).where(AuditEvent.action == "document origin: set")
    ).scalar_one()
    assert event.reason == "client reissue"


@pytest.mark.req("FR-DOC-10")
def test_origins_are_proposed_from_paths_before_anything_is_sent(
    sign_in: SignIn, estimator_principal: Principal, bid: Bid
) -> None:
    client = sign_in(estimator_principal)

    response = client.post(
        f"/bids/{bid.id}/documents/origins", json=[TENDER, MARKED_UP, RESPONSE, LOCK]
    )

    assert response.status_code == 200, response.text
    assert [item["origin"] for item in response.json()] == [
        "tender",
        "working",
        "working",  # the response carries the company's mark, which is tried first
        "ignored",
    ]


@pytest.mark.req("FR-DOC-09")
def test_a_rar_archive_is_refused_with_what_to_do(
    sign_in: SignIn,
    estimator_principal: Principal,
    bid: Bid,
    store: MemoryObjectStore,
    scanner: Scanner,
) -> None:
    client = sign_in(estimator_principal)

    report = send(client, bid, [("MOH/set.rar", b"Rar!\x1a\x07\x01\x00rest")])

    assert report["stored"] == []
    assert "upload the folder itself" in report["rejected"][0]["reason"]


@pytest.mark.req("FR-DOC-09")
def test_paths_must_match_the_files(
    sign_in: SignIn, estimator_principal: Principal, bid: Bid
) -> None:
    client = sign_in(estimator_principal)

    response = client.post(
        f"/bids/{bid.id}/documents",
        files=[("files", ("a.pdf", PDF, "application/pdf"))],
        data={"paths": ["one/a.pdf", "two/b.pdf"]},
    )

    assert response.status_code == 422
