"""QTO items from detections: counted, measured and rule-derived (FR-QTO-01 to 05, 10).

* **Counted:** sprinklers by type and attributes; valves, devices and drawn fittings by type
  (with the size of the pipe they sit on).
* **Measured:** pipe by DN, material, schedule or class, joining method and class (main or
  branch), in integer millimetres from runs on verified-scale views only.
* **Rule-derived:** a drop at every sprinkler, a riser at every riser symbol, and the
  fittings the drawing does not show (`qto.fittings`), each with its rule, version, inputs
  and their sources.
* **Designed:** the heads and range pipes a design-intent tender leaves to the contractor,
  as the design rules proposed them (P1-12). Kept as items of their own, never added to
  what was drawn, each with the design rule, its version, and the criterion it followed.

Each attribute is resolved in order and records which source supplied it: the drawing (the
detection's own attributes), then the verified specification (P1-06), then "not specified".
For sprinklers, a specification value is taken from the clause that states that sprinkler's
type when there is one ("pendent ... chrome", "sidewall ... white").

Items are grouped by level and zone, with the grid range their members span. Duplicates
found by `qto.dedup` are excluded before counting, and a run's duplicated length is taken
off it. The net quantity is never adjusted: the allowance is kept beside it (FR-QTO-10).

Pure: detections, runs, specification, rules and parameters in; item drafts out.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from firebid.qto import fittings, rules
from firebid.qto.model import DESIGNED, Detection, ItemDraft, Run, SpecValue, key_of

# system, DN -> attribute -> verified values
SpecLookup = Callable[[str, int | None], dict[str, list[SpecValue]]]

SPRINKLER_ATTRIBUTES = ("k_factor", "temperature_rating_c", "response", "finish")
PIPE_ATTRIBUTES = ("pipe_material", "pipe_standard", "pipe_class", "joining_method")
NOT_SPECIFIED = "not specified"
ITEM_CLASS = {"sprinkler": "sprinkler", "valve": "valve", "device": "device", "fitting": "fitting"}
LABELS = {
    "sprinkler_pendent": "Sprinkler, pendent",
    "sprinkler_upright": "Sprinkler, upright",
    "sprinkler_sidewall": "Sprinkler, sidewall",
    "sprinkler_concealed": "Sprinkler, concealed",
}


def resolve(
    name: str,
    drawn: dict[str, Any],
    values: list[SpecValue],
    prefer_clauses: set[str] | None = None,
) -> dict[str, Any]:
    """One attribute: the drawing's, else the specification's, else "not specified"."""
    if drawn.get(name) not in (None, "", NOT_SPECIFIED):
        return {"value": str(drawn[name]), "source": "drawing"}
    if values:
        preferred = [v for v in values if prefer_clauses and v.clause in prefer_clauses]
        chosen = preferred or values
        distinct = sorted({v.value for v in chosen})
        resolved: dict[str, Any] = {
            "value": ", ".join(distinct),
            "source": "specification",
            "citations": [v.citation for v in chosen],
        }
        if len(distinct) > 1:
            # No clause names this item's type, and the others disagree: a person chooses.
            resolved["conflict"] = "the specification gives more than one value"
        return resolved
    return {"value": NOT_SPECIFIED, "source": NOT_SPECIFIED}


def _where(members: list[Detection | Run]) -> tuple[str | None, str | None]:
    """The grid references at the low and high corners of what the members span."""
    placed = [
        (
            m.grid_index
            if isinstance(m, Detection)
            else next((p for p in m.grid_points if p), None),
            m.grid_reference,
        )
        for m in members
    ]
    known = [(index, ref) for index, ref in placed if index is not None and ref]
    if not known:
        refs = sorted({m.grid_reference for m in members if m.grid_reference})
        return (refs[0], refs[-1]) if refs else (None, None)
    low = min(known, key=lambda item: (item[0][0], item[0][1]))
    high = max(known, key=lambda item: (item[0][0], item[0][1]))
    return low[1], high[1]


def _sources(members: list[Detection | Run]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for member in members:
        at = member.at
        seen.setdefault(
            at.sheet_id,
            {
                "sheet_id": at.sheet_id,
                "sheet_number": at.sheet_number,
                "revision": at.revision,
                "document_id": at.document_id,
                "view_id": at.view_id,
            },
        )
    return sorted(seen.values(), key=lambda s: s["sheet_number"])


def _member(item: Detection | Run) -> dict[str, Any]:
    if isinstance(item, Detection):
        return {
            "kind": "detection",
            "id": item.id,
            "sheet": item.at.sheet_number,
            "grid": item.grid_reference,
            "x": round(item.x, 3),
            "y": round(item.y, 3),
        }
    return {
        "kind": "run",
        "id": item.id,
        "sheet": item.at.sheet_number,
        "grid": item.grid_reference,
        "length_mm": item.length_mm,
    }


def _confidence(members: list[Detection | Run]) -> float:
    return round(min(m.confidence for m in members), 4) if members else 0.0


def dn_at(detection: Detection, runs: list[Run]) -> int | None:
    """The size of the pipe a valve or fitting sits on: a run ending at it, same sheet."""
    sizes = [
        run.dn
        for run in runs
        if run.at.sheet_id == detection.at.sheet_id
        and run.dn is not None
        and any(
            abs(p[0] - detection.x) < 0.01 and abs(p[1] - detection.y) < 0.01
            for p in (run.points[0], run.points[-1])
        )
    ]
    return max(sizes) if sizes else None


def generate(
    detections: list[Detection],
    runs: list[Run],
    spec: SpecLookup,
    rule_set: dict[str, rules.Rule],
    parameters: list[rules.Parameter],
    *,
    excluded: set[str] | None = None,
    excluded_length: dict[str, int] | None = None,
    carried: dict[str, tuple[int, str]] | None = None,
    system: str = "sprinkler",
) -> list[ItemDraft]:
    excluded = excluded or set()
    excluded_length = excluded_length or {}
    kept = [d for d in detections if d.id not in excluded]
    carried = carried or {}
    runs = [_with_carried(run, carried) for run in runs]
    lengths = [r for r in runs if r.id not in excluded and r.length_mm is not None]
    measured = [r for r in lengths if r.origin != DESIGNED]
    allowance = rule_set.get("allowance")
    drafts: list[ItemDraft] = []

    # --- Counted: sprinklers, valves, devices, drawn fittings ----------------------------
    groups: dict[str, tuple[dict[str, Any], list[Detection]]] = {}
    for detection in kept:
        if detection.kind != "object" or detection.method == DESIGNED:
            continue
        drawn = dict(detection.attributes)
        if detection.category == "sprinkler":
            values = spec(system, None)
            kind = detection.object_type.removeprefix("sprinkler_")
            clauses = {v.clause for v in values.get("sprinkler_type", []) if v.value == kind}
            attributes = {
                name: resolve(name, drawn, values.get(name, []), clauses)
                for name in SPRINKLER_ATTRIBUTES
            }
        else:
            dn = dn_at(detection, runs)
            attributes = {
                "nominal_diameter_mm": (
                    {"value": str(dn), "source": "drawing"}
                    if dn
                    else {"value": NOT_SPECIFIED, "source": NOT_SPECIFIED}
                )
            }
            if detection.attributes.get("fitting"):
                attributes["fitting"] = {
                    "value": str(detection.attributes["fitting"]),
                    "source": "drawing",
                }
        signature = (
            detection.object_type,
            {k: v["value"] for k, v in attributes.items()},
            detection.at.level,
            detection.at.zone,
        )
        key = key_of("count", *signature)
        groups.setdefault(key, (attributes, []))[1].append(detection)
    for key, (attributes, members) in groups.items():
        first = members[0]
        grid_from, grid_to = _where(list(members))
        drafts.append(
            ItemDraft(
                key=key,
                item_type=first.object_type,
                classification=first.category,
                description=_describe(first.object_type, attributes),
                attributes=attributes,
                unit="no",
                net_quantity=Decimal(len(members)),
                length_mm=None,
                level=first.at.level,
                zone=first.at.zone,
                grid_from=grid_from,
                grid_to=grid_to,
                calculation_method="count",
                detection_method=first.method,
                confidence=_confidence(list(members)),
                members=[_member(m) for m in members],
                sources=_sources(list(members)),
                allowance_percent=rules.allowance_percent(
                    allowance, ITEM_CLASS.get(first.category, "")
                ),
                geometry=[
                    {"sheet": m.at.sheet_number, "x": round(m.x, 3), "y": round(m.y, 3)}
                    for m in members
                ],
            )
        )

    # --- Measured: pipe by size, material, class, joining and run class -----------------
    pipe_groups: dict[str, tuple[dict[str, Any], list[Run]]] = {}
    for run in measured:
        values = spec(system, run.dn)
        pipe_attributes: dict[str, Any] = {
            "nominal_diameter_mm": (
                {"value": str(run.dn), "source": _size_source(run, carried)}
                if run.dn
                else {"value": NOT_SPECIFIED, "source": f"drawing ({run.size_status})"}
            ),
            **{name: resolve(name, {}, values.get(name, [])) for name in PIPE_ATTRIBUTES},
        }
        pipe_signature = (
            {k: v["value"] for k, v in pipe_attributes.items()},
            run.run_class,
            run.at.level,
            run.at.zone,
        )
        key = key_of("pipe", *pipe_signature)
        pipe_groups.setdefault(key, (pipe_attributes, []))[1].append(run)
    for key, (pipe_attributes, pipe_members) in pipe_groups.items():
        lead = pipe_members[0]
        length = sum((m.length_mm or 0) - excluded_length.get(m.id, 0) for m in pipe_members)
        grid_from, grid_to = _where(list(pipe_members))
        drafts.append(
            ItemDraft(
                key=key,
                item_type="pipe",
                classification=lead.run_class,
                description=_describe_pipe(pipe_attributes, lead.run_class),
                attributes=pipe_attributes,
                unit="m",
                net_quantity=(Decimal(length) / Decimal(1000)).quantize(Decimal("0.001")),
                length_mm=length,
                level=lead.at.level,
                zone=lead.at.zone,
                grid_from=grid_from,
                grid_to=grid_to,
                calculation_method="centreline_length",
                detection_method="network",
                confidence=_confidence(list(pipe_members)),
                members=[_member(m) for m in pipe_members],
                sources=_sources(list(pipe_members)),
                allowance_percent=rules.allowance_percent(allowance, "pipe"),
                geometry=[
                    {"sheet": m.at.sheet_number, "points": [list(p) for p in m.points]}
                    for m in pipe_members
                ],
                note=_length_note(pipe_members, excluded_length),
            )
        )

    # --- Rule-derived: drops and risers --------------------------------------------------
    drafts.extend(_drops(kept, rule_set, parameters, allowance))
    drafts.extend(_risers(kept, runs, rule_set, parameters, allowance))
    drafts.extend(_designed_heads(kept, spec(system, None), allowance))
    drafts.extend(_designed_pipe(lengths, spec, system, allowance))
    drafts.extend(
        fittings.derive(
            measured,
            kept,
            rule_set,
            lambda dn: spec(system, dn).get("joining_method", []),
            allowance,
            excluded_length,
        )
    )
    return sorted(drafts, key=lambda d: (d.level or "", d.item_type, d.key))


def _drops(
    detections: list[Detection],
    rule_set: dict[str, rules.Rule],
    parameters: list[rules.Parameter],
    allowance: rules.Rule | None,
) -> list[ItemDraft]:
    rule = rule_set.get("drop_length")
    if rule is None:
        return []
    by_level: dict[str | None, list[Detection]] = defaultdict(list)
    for detection in detections:
        if detection.kind == "drop":
            by_level[detection.at.level].append(detection)
    out = []
    for level, members in by_level.items():
        each = rules.drop_length(rule, parameters, level)
        dn = int(rule.definition.get("nominal_diameter_mm", 25))
        total = each.value * len(members)
        grid_from, grid_to = _where(list(members))
        derived = each.as_json()
        derived["count"] = len(members)
        derived["per_drop_mm"] = each.value
        out.append(
            ItemDraft(
                key=key_of("drop", level, members[0].at.zone, dn),
                item_type="pipe",
                classification="drop",
                description=f"Sprinkler drop, DN{dn} (vertical, not drawn)",
                attributes={
                    "nominal_diameter_mm": {"value": str(dn), "source": f"rule {rule.key}"}
                },
                unit="m",
                net_quantity=(Decimal(total) / Decimal(1000)).quantize(Decimal("0.001")),
                length_mm=total,
                level=level,
                zone=members[0].at.zone,
                grid_from=grid_from,
                grid_to=grid_to,
                calculation_method="rule_derived",
                detection_method="rule",
                confidence=_confidence(list(members)),
                members=[_member(m) for m in members],
                sources=_sources(list(members)),
                rule=derived,
                allowance_percent=rules.allowance_percent(allowance, "pipe"),
                geometry=[
                    {"sheet": m.at.sheet_number, "x": round(m.x, 3), "y": round(m.y, 3)}
                    for m in members
                ],
                note=f"{len(members)} drops x {each.value} mm",
            )
        )
    return out


def _risers(
    detections: list[Detection],
    runs: list[Run],
    rule_set: dict[str, rules.Rule],
    parameters: list[rules.Parameter],
    allowance: rules.Rule | None,
) -> list[ItemDraft]:
    rule = rule_set.get("riser_length")
    if rule is None:
        return []
    out = []
    risers = [d for d in detections if d.kind == "riser"]
    # A riser is its own item, so its key must be its own. Where it is on the grid says
    # which riser it is; on a sheet with no grid, or with two risers in one bay, that is
    # shared, and the items took each other's place at every recompute: each new one was
    # created and none superseded (found on a real tender: 34,000 rows for 6,860 risers).
    # Those are told apart by where they are on the sheet, and two drawn at the very same
    # point by their order there. A key that was already its own stays as it was, so an
    # item verified under it is not disturbed.
    shared = Counter(
        (riser.at.level, riser.at.sheet_number, riser.grid_reference) for riser in risers
    )
    taken: Counter[tuple[Any, ...]] = Counter()
    for riser in risers:
        each = rules.riser_length(rule, parameters, riser.at.level)
        dn = dn_at(riser, runs)
        where = (riser.at.level, riser.at.sheet_number, riser.grid_reference)
        place: tuple[Any, ...] = (round(riser.x, 1), round(riser.y, 1)) if shared[where] > 1 else ()
        nth = taken[(*where, *place)]
        taken[(*where, *place)] += 1
        out.append(
            ItemDraft(
                key=key_of("riser", *where, *place, *((nth,) if nth else ())),
                item_type="pipe",
                classification="riser",
                description=f"Riser, DN{dn or '?'} (vertical, not drawn)",
                attributes={
                    "nominal_diameter_mm": (
                        {"value": str(dn), "source": "drawing (main at the riser)"}
                        if dn
                        else {"value": NOT_SPECIFIED, "source": NOT_SPECIFIED}
                    )
                },
                unit="m",
                net_quantity=(Decimal(each.value) / Decimal(1000)).quantize(Decimal("0.001")),
                length_mm=each.value,
                level=riser.at.level,
                zone=riser.at.zone,
                grid_from=riser.grid_reference,
                grid_to=riser.grid_reference,
                calculation_method="rule_derived",
                detection_method="rule",
                confidence=riser.confidence,
                members=[_member(riser)],
                sources=_sources([riser]),
                rule=each.as_json(),
                allowance_percent=rules.allowance_percent(allowance, "pipe"),
                geometry=[
                    {"sheet": riser.at.sheet_number, "x": round(riser.x, 3), "y": round(riser.y, 3)}
                ],
            )
        )
    return out


NOT_DRAWN = "proposed layout, not drawn"


def _design_rule(evidence: dict[str, Any], value: int) -> dict[str, Any]:
    """The design rule a head or pipe was proposed by, as a rule-derived item records it."""
    rule = dict(evidence.get("rule") or {})
    return {
        "rule_key": rule.get("rule_key", "sprinkler_layout"),
        "rule_version": rule.get("rule_version"),
        "rule_status": rule.get("rule_status", "to be confirmed"),
        "inputs": list(rule.get("inputs") or []),
        "value": value,
    }


def _designed_heads(
    detections: list[Detection], values: dict[str, list[SpecValue]], allowance: rules.Rule | None
) -> list[ItemDraft]:
    """Proposed heads, by type and rating, level and zone, and the rule version they follow."""
    groups: dict[str, tuple[dict[str, Any], list[Detection]]] = {}
    for detection in detections:
        if detection.kind != "object" or detection.method != DESIGNED:
            continue
        version = (detection.evidence.get("rule") or {}).get("rule_version")
        source = f"design rule v{version}"
        kind = detection.object_type.removeprefix("sprinkler_")
        clauses = {v.clause for v in values.get("sprinkler_type", []) if v.value == kind}
        attributes = {}
        for name in SPRINKLER_ATTRIBUTES:
            attributes[name] = resolve(name, {}, values.get(name, []), clauses)
            stated = detection.attributes.get(name)
            if attributes[name]["value"] == NOT_SPECIFIED and stated not in (None, ""):
                # The specification is silent: the design rule's value, and it says so.
                attributes[name] = {"value": str(stated), "source": source}
        key = key_of(
            DESIGNED,
            detection.object_type,
            {k: v["value"] for k, v in attributes.items()},
            detection.at.level,
            detection.at.zone,
            version,
        )
        groups.setdefault(key, (attributes, []))[1].append(detection)
    out = []
    for key, (attributes, members) in groups.items():
        first = members[0]
        grid_from, grid_to = _where(list(members))
        spaces = {(m.at.sheet_id, (m.evidence.get("space") or {}).get("index")) for m in members}
        out.append(
            ItemDraft(
                key=key,
                item_type=first.object_type,
                classification=first.category or "sprinkler",
                description=f"{_describe(first.object_type, attributes)} [{NOT_DRAWN}]",
                attributes=attributes,
                unit="no",
                net_quantity=Decimal(len(members)),
                length_mm=None,
                level=first.at.level,
                zone=first.at.zone,
                grid_from=grid_from,
                grid_to=grid_to,
                calculation_method="rule_derived",
                detection_method=DESIGNED,
                confidence=_confidence(list(members)),
                members=[_member(m) for m in members],
                sources=_sources(list(members)),
                rule=_design_rule(first.evidence, len(members)),
                allowance_percent=rules.allowance_percent(allowance, "sprinkler"),
                geometry=[
                    {"sheet": m.at.sheet_number, "x": round(m.x, 3), "y": round(m.y, 3)}
                    for m in members
                ],
                note=f"{len(members)} heads proposed in {len(spaces)} spaces",
            )
        )
    return out


def _designed_pipe(
    runs: list[Run], spec: SpecLookup, system: str, allowance: rules.Rule | None
) -> list[ItemDraft]:
    """Proposed range pipe, by size, level and zone."""
    groups: dict[str, tuple[dict[str, Any], list[Run]]] = {}
    for run in runs:
        if run.origin != DESIGNED:
            continue
        version = (run.evidence.get("rule") or {}).get("rule_version")
        values = spec(system, run.dn)
        attributes: dict[str, Any] = {
            "nominal_diameter_mm": {"value": str(run.dn), "source": f"design rule v{version}"},
            **{name: resolve(name, {}, values.get(name, [])) for name in PIPE_ATTRIBUTES},
        }
        key = key_of(
            DESIGNED,
            "pipe",
            {k: v["value"] for k, v in attributes.items()},
            run.at.level,
            run.at.zone,
            version,
        )
        groups.setdefault(key, (attributes, []))[1].append(run)
    out = []
    for key, (attributes, members) in groups.items():
        lead = members[0]
        length = sum(m.length_mm or 0 for m in members)
        remote = sum(m.length_mm or 0 for m in members if m.evidence.get("remote"))
        grid_from, grid_to = _where(list(members))
        note = f"{len(members)} lengths of range pipe and feed"
        if remote:
            note += f"; {remote} mm of it is an allowance where no drawn main is in reach"
        out.append(
            ItemDraft(
                key=key,
                item_type="pipe",
                classification="range",
                description=f"{_describe_pipe(attributes, 'range')} [{NOT_DRAWN}]",
                attributes=attributes,
                unit="m",
                net_quantity=(Decimal(length) / Decimal(1000)).quantize(Decimal("0.001")),
                length_mm=length,
                level=lead.at.level,
                zone=lead.at.zone,
                grid_from=grid_from,
                grid_to=grid_to,
                calculation_method="rule_derived",
                detection_method=DESIGNED,
                confidence=_confidence(list(members)),
                members=[_member(m) for m in members],
                sources=_sources(list(members)),
                rule=_design_rule(lead.evidence, length),
                allowance_percent=rules.allowance_percent(allowance, "pipe"),
                geometry=[
                    {"sheet": m.at.sheet_number, "points": [list(p) for p in m.points]}
                    for m in members
                ],
                note=note,
            )
        )
    return out


def _describe(object_type: str, attributes: dict[str, dict[str, Any]]) -> str:
    name = LABELS.get(object_type, object_type.replace("_", " ").capitalize())
    if "nominal_diameter_mm" in attributes and attributes["nominal_diameter_mm"]["value"] != (
        NOT_SPECIFIED
    ):
        name += f", DN{attributes['nominal_diameter_mm']['value']}"
    stated = [
        f"{key.replace('_', ' ')} {value['value']}"
        for key, value in attributes.items()
        if key != "nominal_diameter_mm" and value["value"] != NOT_SPECIFIED
    ]
    return f"{name} ({', '.join(stated)})" if stated else name


def _describe_pipe(attributes: dict[str, dict[str, Any]], run_class: str) -> str:
    dn = attributes["nominal_diameter_mm"]["value"]
    parts = [
        attributes[name]["value"].replace("_", " ")
        for name in ("pipe_material", "pipe_class", "joining_method")
        if attributes[name]["value"] != NOT_SPECIFIED
    ]
    size = f"DN{dn}" if dn != NOT_SPECIFIED else "size not determined"
    return f"Pipe, {size}, {run_class}" + (f" ({', '.join(parts)})" if parts else "")


def _length_note(members: list[Run], excluded_length: dict[str, int]) -> str | None:
    taken = sum(excluded_length.get(m.id, 0) for m in members)
    return f"{taken} mm drawn twice excluded (see duplicate groups)" if taken else None


def _with_carried(run: Run, carried: dict[str, tuple[int, str]]) -> Run:
    if run.dn is not None or run.id not in carried:
        return run
    from dataclasses import replace

    return replace(run, dn=carried[run.id][0], size_status="carried")


def _size_source(run: Run, carried: dict[str, tuple[int, str]]) -> str:
    if run.id in carried and run.size_status == "carried":
        return f"drawing ({carried[run.id][1]}, across the match line)"
    return "drawing"
