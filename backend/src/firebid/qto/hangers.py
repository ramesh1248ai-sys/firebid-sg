"""Hangers, supports and seismic restraint, derived by rule from measured pipe (FR-QTO-07).

Nobody draws a hanger. Their number follows from how much pipe there is and how far apart
the specification lets its supports be:

* **Hangers:** one per spacing, or part of one, of the pipe measured at a size on a level.
  The spacing for a size is the verified specification's, cited by its clause. Where the
  specification is silent it is the company default in the `hanger_spacing` rule, and the
  item says so. Where the specification gives two spacings for one size, the closer one is
  used and the conflict is recorded for a person.
* **Seismic restraint:** lateral and longitudinal braces, at the `seismic_restraint` rule's
  spacings, on pipe of the rule's minimum size and above. Only where a verified
  specification attribute requires restraint for that system and size: with no such
  attribute nothing is taken off, whatever the rule says.

Pipe of a system the rule excludes is not supported at all: by default the hydrant system,
whose mains are buried.

Pipe of a size the drawing does not give is supported at the closest default spacing, which
gives the most hangers, and is left out of seismic restraint: its item says both.

Every item records the rule and version, the spacing and where it came from, and the pipe
length it was counted from, so the count can be reproduced by hand: length over spacing,
rounded up.

Pure: measured runs, the specification and rules in; item drafts out.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from firebid.qto import rules
from firebid.qto.model import ItemDraft, Run, SpecValue, key_of

SPACING = "hanger_spacing_mm"
SEISMIC = "seismic_restraint"
REQUIRED = "required"
NOT_SPECIFIED = "not specified"

# run -> attribute -> verified values, for the run's own system and size
SpecFor = Callable[[Run], dict[str, list[SpecValue]]]


def default_spacing(rule: rules.Rule, dn: int | None) -> int:
    """The company default for a size: the first band that reaches it; the closest spacing
    of all for a size that is not known."""
    bands = sorted(
        (
            (int(band["up_to_dn"]), int(band["spacing_mm"]))
            for band in rule.definition.get("default_spacing_mm", [])
        ),
    )
    if not bands:
        raise KeyError(f"rule {rule.key} v{rule.version} has no default spacing")
    if dn is None:
        return min(spacing for _, spacing in bands)
    return next((spacing for up_to, spacing in bands if dn <= up_to), bands[-1][1])


def spacing_for(
    rule: rules.Rule, dn: int | None, values: list[SpecValue]
) -> tuple[int, dict[str, Any]]:
    """The spacing for a size and the attribute that records where it came from."""
    stated = sorted({int(Decimal(v.value)) for v in values}) if dn is not None else []
    if stated:
        attribute: dict[str, Any] = {
            "value": str(stated[0]),
            "source": "specification",
            "citations": [v.citation for v in values],
        }
        if len(stated) > 1:
            attribute["conflict"] = (
                "the specification gives more than one spacing; the closest is used"
            )
        return stated[0], attribute
    spacing = default_spacing(rule, dn)
    return spacing, {
        "value": str(spacing),
        "source": f"company default (rule {rule.key} v{rule.version}, {rule.status})",
    }


def count(length_mm: int, spacing_mm: int) -> int:
    """One per spacing or part of one."""
    return math.ceil(length_mm / spacing_mm) if length_mm > 0 and spacing_mm > 0 else 0


def _sources(members: list[Run]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for run in members:
        at = run.at
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


def _draft(
    *,
    key: str,
    item_type: str,
    description: str,
    attributes: dict[str, dict[str, Any]],
    members: list[Run],
    lengths: dict[str, int],
    quantity: int,
    rule: rules.Rule,
    inputs: list[dict[str, Any]],
    allowance: rules.Rule | None,
    note: str,
) -> ItemDraft:
    lead = members[0]
    return ItemDraft(
        key=key,
        item_type=item_type,
        classification="support",
        description=description,
        attributes=attributes,
        unit="no",
        net_quantity=Decimal(quantity),
        length_mm=None,
        level=lead.at.level,
        zone=lead.at.zone,
        grid_from=None,
        grid_to=None,
        calculation_method="rule_derived",
        detection_method="rule",
        confidence=round(min(m.confidence for m in members), 4),
        members=[
            {
                "kind": "run",
                "id": m.id,
                "sheet": m.at.sheet_number,
                "grid": m.grid_reference,
                "length_mm": lengths[m.id],
            }
            for m in members
        ],
        sources=_sources(members),
        rule={
            "rule_key": rule.key,
            "rule_version": rule.version,
            "rule_status": rule.status,
            "inputs": inputs,
            "value": quantity,
        },
        allowance_percent=rules.allowance_percent(allowance, "support"),
        geometry=[
            {"sheet": m.at.sheet_number, "points": [list(p) for p in m.points]} for m in members
        ],
        note=note,
    )


def derive(
    runs: list[Run],
    spec_for: SpecFor,
    rule_set: dict[str, rules.Rule],
    allowance: rules.Rule | None,
    excluded_length: dict[str, int],
    default_system: str,
) -> list[ItemDraft]:
    """Hanger items, and seismic brace items where the specification requires them."""
    hanger_rule = rule_set.get("hanger_spacing")
    seismic_rule = rule_set.get("seismic_restraint")
    if hanger_rule is None and seismic_rule is None:
        return []
    definition = hanger_rule.definition if hanger_rule else {}
    classes = set(definition.get("run_classes") or [])
    # Systems whose pipe is not hung: a hydrant main is buried.
    unhung = set(definition.get("exclude_systems") or [])
    lengths = {run.id: (run.length_mm or 0) - excluded_length.get(run.id, 0) for run in runs}
    supported = [run for run in runs if lengths[run.id] > 0]

    hangers: dict[str, tuple[int, dict[str, Any], list[Run]]] = {}
    braced: dict[str, tuple[list[SpecValue], list[Run]]] = {}
    for run in supported:
        values = spec_for(run)
        # A system other than the bid's own is part of what the item is; the bid's own is
        # left out of the key, as it is left out of the pipe's.
        system = run.system if run.system and run.system != default_system else None
        where = (run.dn, run.at.level, run.at.zone, system)
        if run.system in unhung:
            continue
        if hanger_rule is not None and (not classes or run.run_class in classes):
            spacing, attribute = spacing_for(hanger_rule, run.dn, values.get(SPACING, []))
            key = key_of("hanger", *where, spacing, attribute["source"])
            hangers.setdefault(key, (spacing, attribute, []))[2].append(run)
        required = [v for v in values.get(SEISMIC, []) if v.value == REQUIRED]
        if (
            seismic_rule is not None
            and required
            and run.dn is not None
            and run.dn >= int(seismic_rule.definition.get("min_dn", 0))
        ):
            braced.setdefault(key_of("seismic", *where), (required, []))[1].append(run)

    drafts = []
    for key, (spacing, attribute, members) in hangers.items():
        if hanger_rule is None:  # for the type checker: nothing is grouped without it
            continue
        lead = members[0]
        total = sum(lengths[m.id] for m in members)
        size = f"DN{lead.dn}" if lead.dn else "size not determined"
        drafts.append(
            _draft(
                key=key,
                item_type="pipe_hanger",
                description=f"Pipe hanger, {size} (rule-derived: not drawn)",
                attributes={
                    "nominal_diameter_mm": (
                        {"value": str(lead.dn), "source": "drawing"}
                        if lead.dn
                        else {"value": NOT_SPECIFIED, "source": NOT_SPECIFIED}
                    ),
                    "spacing_mm": attribute,
                    **_system_attribute(lead, default_system),
                },
                members=members,
                lengths=lengths,
                quantity=count(total, spacing),
                rule=hanger_rule,
                inputs=[
                    {"name": "length_mm", "value": total, "source": "measured runs"},
                    {
                        "name": "spacing_mm",
                        "value": spacing,
                        "source": _spacing_source(attribute),
                    },
                ],
                allowance=allowance,
                note=f"{total} mm of pipe at {spacing} mm centres, rounded up"
                + (
                    ""
                    if lead.dn
                    else "; the size is not known, so the closest default spacing is used"
                ),
            )
        )
    for key, (required, members) in braced.items():
        if seismic_rule is None:
            continue
        lead = members[0]
        total = sum(lengths[m.id] for m in members)
        clauses = sorted({v.clause for v in required})
        for way in ("lateral", "longitudinal"):
            spacing = int(seismic_rule.definition.get(f"{way}_spacing_mm", 0))
            quantity = count(total, spacing)
            if not quantity:
                continue
            drafts.append(
                _draft(
                    key=key_of(key, way),
                    item_type=f"seismic_brace_{way}",
                    description=f"Seismic brace, {way}, DN{lead.dn} (rule-derived: not drawn)",
                    attributes={
                        "nominal_diameter_mm": {"value": str(lead.dn), "source": "drawing"},
                        SEISMIC: {
                            "value": REQUIRED,
                            "source": "specification",
                            "citations": [v.citation for v in required],
                        },
                        "spacing_mm": {
                            "value": str(spacing),
                            "source": f"rule {seismic_rule.key} v{seismic_rule.version} "
                            f"({seismic_rule.status})",
                        },
                        **_system_attribute(lead, default_system),
                    },
                    members=members,
                    lengths=lengths,
                    quantity=quantity,
                    rule=seismic_rule,
                    inputs=[
                        {"name": "length_mm", "value": total, "source": "measured runs"},
                        {
                            "name": f"{way}_spacing_mm",
                            "value": spacing,
                            "source": f"rule ({seismic_rule.status})",
                        },
                        {
                            "name": "required_by",
                            "value": ", ".join(clauses),
                            "source": "specification",
                        },
                    ],
                    allowance=allowance,
                    note=f"required by specification clause {', '.join(clauses)}: "
                    f"{total} mm of pipe, one {way} brace per {spacing} mm, rounded up",
                )
            )
    return drafts


def _system_attribute(run: Run, default_system: str) -> dict[str, dict[str, Any]]:
    if run.system and run.system != default_system:
        return {"system": {"value": run.system, "source": "drawing"}}
    return {}


def _spacing_source(attribute: dict[str, Any]) -> str:
    cited = sorted({str(c.get("clause")) for c in attribute.get("citations", []) if c})
    if cited:
        return f"specification clause {', '.join(cited)}"
    return str(attribute["source"])
