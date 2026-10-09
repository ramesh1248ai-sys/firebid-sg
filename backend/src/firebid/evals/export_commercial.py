"""Stages 8 to 12 of a bid, in the shape of a golden reference package (FR-LRN-01).

The second half of `firebid.evals.export_run`: the specification as read, the company bill
and the client's bill against it, the price and the labour, the clarification candidates,
checklist and risks, and the figures and gates of the review pack. As there, nothing is
worked out again beyond what the platform's own pages work out when they are opened: the
build-up, the labour estimate and the review pack are asked for as those pages ask.

A bill line is named by what it is of, not by its wording or its number: `heads_pendent`,
`pipe_150_main`, `tee_150x50`, `hanger_50`. The name is made from the takeoff items the line
is built from, so two bills that word or order their lines differently still compare.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.commercial import BoqLine, BoqLineSource
from firebid.db.models.core import Bid
from firebid.db.models.specs import SpecAttribute, SpecClause
from firebid.db.models.takeoff import QtoItem

CANDIDATE_FROM = {
    "spec_issue": "specification issue",
    "missing_information": "specification issue",
    "boq_variance": "bill variance",
    "scope_row": "scope",
}


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _amount(money: Any) -> str | None:
    return None if money is None else str(money.amount)


def line_name(item: dict[str, Any]) -> str:
    """What a bill line is of, from one of the takeoff items behind it (`export_run._item`)."""
    kind, size = str(item["item"]), item.get("dn")
    if kind.startswith("sprinkler_"):
        return "heads_" + kind.removeprefix("sprinkler_")
    if kind == "fitting":
        kind = str(item.get("fitting") or "fitting")
    parts = [kind, str(size) if size else "", str(item.get("run") or "")]
    return "_".join(part for part in parts if part)


def _names(session: Session, bid_id: uuid.UUID, lines: list[BoqLine]) -> dict[uuid.UUID, str]:
    from firebid.evals.export_run import _item

    items = {
        row.id: row
        for row in session.execute(select(QtoItem).where(QtoItem.bid_id == bid_id)).scalars()
    }
    first: dict[uuid.UUID, QtoItem] = {}
    for source in session.execute(
        select(BoqLineSource).where(BoqLineSource.bid_id == bid_id)
    ).scalars():
        item = items.get(source.qto_item_id)
        if item is not None:
            first.setdefault(source.boq_line_id, item)
    return {
        line.id: line_name(_item(first[line.id]))
        if line.id in first
        # A line a person added, or a sum: it is of nothing measured.
        else f"line {line.item_no or line.description}"
        for line in lines
    }


def specification(session: Session, bid: Bid) -> dict[str, Any] | None:
    from firebid.services import spec_analysis as analysis

    clauses = list(
        session.execute(
            select(SpecClause).where(SpecClause.bid_id == bid.id).order_by(SpecClause.ordinal)
        ).scalars()
    )
    if not clauses:
        return None
    attributes = session.execute(
        select(SpecAttribute)
        .where(SpecAttribute.bid_id == bid.id, SpecAttribute.state != "superseded")
        .order_by(SpecAttribute.clause_number, SpecAttribute.attribute)
    ).scalars()
    issues = []
    for issue in analysis.list_issues(session, bid.id):
        sheets = issue.drawing_ref.get("sheets")
        first = sheets[0] if isinstance(sheets, list) and sheets else issue.drawing_ref
        issues.append(
            {
                "rule": issue.rule,
                "clause": issue.spec_ref.get("clause"),
                "about": issue.detail.get("what"),
                "sheet": first.get("sheet_number") if isinstance(first, dict) else None,
                "severity": issue.severity,
                "state": issue.state,
            }
        )
    return {
        "clauses": [
            {"number": c.number, "heading": c.heading, "has_text": bool(c.text)} for c in clauses
        ],
        "attributes": [
            {
                "system": a.system,
                "attribute": a.attribute,
                "value": a.value,
                "clause": a.clause_number,
                "dn_min": a.dn_min,
                "dn_max": a.dn_max,
                "condition": a.condition,
                "state": a.state,
            }
            for a in attributes
        ],
        "obligations": [
            {
                "category": o.category,
                "clause": o.clause_number,
                "quantities": dict(o.quantities),
                "state": o.state,
            }
            for o in analysis.list_obligations(session, bid.id)
        ],
        "issues": issues,
        "scope_matrix": {
            "rows": [
                {
                    "system": row.system,
                    "key": row.key,
                    "status": row.status,
                    "clause": row.clause_number,
                }
                for row in analysis.matrix(session, bid.id)
                if row.kind == "interface"
            ]
        },
    }


def bill(session: Session, bid: Bid) -> dict[str, Any] | None:
    from firebid.services import boq

    current = boq.current_boq(session, bid.id)
    if current is None:
        return None
    lines = boq.lines_of(session, current)
    names = _names(session, bid.id, lines)
    by_number = {line.item_no: names[line.id] for line in lines if line.item_no}
    client, ours_only = [], []
    for row in boq.reconciliation(session, bid.id):
        if row.kind == "measured_only":
            ours_only.append(by_number.get(row.line_item or "", row.line_item or ""))
            continue
        one: dict[str, Any] = {
            "item": row.client_item,
            "description": row.client_description,
            "unit": row.client_unit,
            "client_quantity": float(row.client_quantity)
            if row.client_quantity is not None
            else None,
            "maps_to": by_number.get(row.line_item) if row.line_item else None,
            "flagged": row.flagged,
            "state": row.state,
        }
        if row.measured_quantity is not None:
            one["measured_quantity"] = float(row.measured_quantity)
        elif row.client_quantity is not None:
            one["measured_quantity"] = 0.0  # a measured line of the client's, with nothing to it
        if row.variance_percent is not None:
            one["variance_percent"] = float(row.variance_percent)
        client.append(one)
    return {
        "our_bill": {
            "template": f"{current.template_key}, version {current.template_version}",
            "lines": [
                {
                    "line": names[line.id],
                    "number": line.item_no,
                    "group": line.group_heading,
                    "description": line.description,
                    "unit": line.unit,
                    "quantity": float(line.quantity),
                    "allowance_percent": float(line.allowance_percent or 0),
                    "level": line.level,
                }
                for line in lines
            ],
        },
        "client_bill": {"lines": client},
        "in_ours_and_not_in_the_client_s": ours_only,
        "flagged": [row["item"] for row in client if row["flagged"]],
    }


def pricing(session: Session, bid: Bid) -> dict[str, Any] | None:
    from firebid.services import boq, costing, labour
    from firebid.services import pricing as service

    current = boq.current_boq(session, bid.id)
    if current is None:
        return None
    lines = boq.lines_of(session, current)
    names = _names(session, bid.id, lines)
    day = labour.pricing_day(session, bid)
    prices = service.line_prices(session, bid, current, day)
    estimate = labour.estimate(session, bid)
    hours = {one.line.id: one for one in estimate.lines}
    out = []
    for line in lines:
        price, worked = prices[line.id], hours.get(str(line.id))
        one: dict[str, Any] = {
            "line": names[line.id],
            "quantity": float(line.quantity),
            "price_status": price.status,
            "unit_rate": _amount(line.unit_rate) if price.rate else None,
            "amount": _amount(line.amount),
            "labour": None,
        }
        if price.rate is not None:
            one["rate_source"] = f"{price.rate.source_type} {price.rate.source_reference or ''}"
            one["rate_valid_until"] = (
                price.rate.valid_until.isoformat() if price.rate.valid_until else None
            )
            one["warnings"] = sorted(warning.code for warning in price.warnings)
        if worked is not None and worked.hours is not None:
            one["labour"] = {
                "hours_per_unit": _text(worked.entry.hours) if worked.entry else None,
                "productivity_entry": worked.entry.description if worked.entry else None,
                "trade": worked.entry.trade if worked.entry else None,
                "hours": _text(worked.hours),
                "hourly_rate": _text(worked.rate.hourly) if worked.rate else None,
                "cost": _amount(worked.cost),
            }
        out.append(one)
    built = costing.build_up(session, bid)
    build_up: dict[str, Any] = {
        line.component: _amount(line.amount)
        for line in built.lines
        if line.amount is not None and line.component != "margin"
    }
    margin = next((line for line in built.lines if line.component == "margin"), None)
    if margin is not None and margin.amount is not None:
        build_up["margin"] = {"basis": margin.detail, "amount": _amount(margin.amount)}
    build_up |= {
        "direct": _amount(built.direct),
        "cost": _amount(built.cost),
        "not_set": list(built.not_set),
        "total_excluding_gst": _amount(built.total),
        "gst_percent": float(built.gst.rate.percent),
        "gst": _amount(built.gst.gst),
        "total_including_gst": _amount(built.gst.inclusive),
    }
    return {
        "priced_on": built.priced_on.isoformat(),
        "lines": out,
        "labour": {
            "hours": _text(estimate.hours),
            "cost": _amount(estimate.cost),
            "lines_without_hours": [
                names[line.id]
                for line in lines
                if str(line.id) in hours and hours[str(line.id)].hours is None
            ],
        },
        "build_up": build_up,
        "unpriced_lines": [
            names[line.id] for line in lines if prices[line.id].status in ("unpriced", "proposed")
        ],
    }


def risks(session: Session, bid: Bid) -> dict[str, Any] | None:
    from firebid.services import boq, clarifications, risk
    from firebid.services import spec_analysis as analysis

    found = risk.risks(session, bid.id)
    checks = risk.checks(session, bid.id)
    candidates = clarifications.candidates(session, bid)
    if not (found or checks or candidates):
        return None
    issues = {issue.key: issue for issue in analysis.list_issues(session, bid.id)}
    current = boq.current_boq(session, bid.id)
    lines = boq.lines_of(session, current) if current is not None else []
    names = _names(session, bid.id, lines)
    by_number = {line.item_no: names[line.id] for line in lines if line.item_no}
    client_item = {
        row.client_ref: row.client_item
        for row in (boq.reconciliation(session, bid.id) if current is not None else [])
        if row.client_ref
    }
    asked = []
    for candidate in candidates:
        one: dict[str, Any] = {
            "from": CANDIDATE_FROM.get(candidate.kind, candidate.kind),
            "subject": candidate.subject,
        }
        issue = issues.get(candidate.ref)
        kind, _, ref = candidate.ref.partition(":")
        if issue is not None:
            one |= {"rule": issue.rule, "clause": issue.spec_ref.get("clause")}
        elif candidate.kind == "boq_variance" and kind == "client":
            one["client_item"] = client_item.get(ref, ref)
        elif candidate.kind == "boq_variance":
            one["our_line"] = by_number.get(ref, ref)
        else:
            one["ref"] = candidate.ref
        asked.append(one)
    listed = []
    for row in found:
        impact: Any = "not computed"
        if isinstance(row.impact, dict) and row.impact.get("method") == "labour_multiplier":
            impact = {
                "multiplier": row.impact.get("multiplier"),
                "factor": row.impact.get("factor"),
                "hours": row.impact.get("hours"),
                "cost": row.impact.get("cost"),
            }
        listed.append(
            {
                "kind": row.kind,
                "family": row.category.replace("_", " "),
                "clauses": sorted(
                    str(e["clause"]) for e in row.evidence if isinstance(e, dict) and "clause" in e
                ),
                "proposed_treatment": row.proposed_treatment,
                "treatment": row.treatment,
                "impact": impact,
            }
        )
    conventions = [
        line.split(". ", 1)[1]
        for line in boq.qualification_text(session, bid.id).splitlines()
        if line[:1].isdigit() and ". " in line
    ]
    return {
        "clarification_candidates": asked,
        "checklist": {
            "items": [
                {"system": row.system, "key": row.item_key, "status": row.status} for row in checks
            ],
            "decided_by_a_person": sum(1 for row in checks if row.decided_by),
        },
        "risks": listed,
        "qualifications": {
            "measurement_conventions": conventions,
            "proposed": len(risk.qualifications(session, bid.id)),
        },
    }


def review(session: Session, bid: Bid) -> dict[str, Any] | None:
    from firebid.services import boq, review_pack, risk, submission
    from firebid.services import pricing as service

    current = boq.current_boq(session, bid.id)
    if current is None:
        return None
    pack = review_pack.build(session, bid)
    readiness = risk.g3_readiness(session, bid.id)
    prices = service.line_prices(session, bid, current, pack.priced_on)
    gates = submission.gate_status(session, bid)
    g3 = submission.g3_blockers(session, bid)
    return {
        "figures": {
            name: pack.figures[name]
            for name in (
                "priced_bill",
                "direct",
                "total_excluding_gst",
                "gst",
                "total_including_gst",
            )
        },
        "unpriced_lines": int(pack.figures["unpriced_lines"]),
        "open_items": {
            "specification_issues": int(pack.figures["open_issues"]),
            "checklist_items_open": len(readiness.open_checks),
            "risks_untreated": len(readiness.untreated_risks),
            "bill_variances_flagged": sum(
                1 for row in boq.reconciliation(session, bid.id) if row.flagged
            ),
            "rate_warnings": sum(1 for price in prices.values() if price.warnings),
        },
        "gates": {gate.gate: "approved" if gate.approved else "not decided" for gate in gates},
        "ready_for_g3": not g3,
        "why_not_ready_for_g3": g3,
        "submission": "frozen"
        if submission.snapshot_of(session, bid.id) is not None
        else "none: nothing is frozen",
    }
