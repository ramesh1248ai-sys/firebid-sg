"""`firebid-ops`: operations checks that leave evidence (P1-11).

    firebid-ops deploy-guard --start 2026-10-03T18:00Z --end 2026-10-03T22:00Z
    firebid-ops game-day [--primary anthropic] [--live]
    firebid-ops restore-drill --local
    firebid-ops provider-terms

Each writes JSON under `<root>/results/ops/`, which `firebid-eval exit` reads. `deploy-guard`
exits 1 when the window is refused, so a release pipeline can call it as a gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _write(root: Path, name: str, data: dict[str, Any]) -> Path:
    out = root / "results" / "ops" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8", newline="\n")
    return out


def _when(value: str) -> datetime:
    found = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return found if found.tzinfo else found.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="firebid-ops", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("../eval"))
    commands = parser.add_subparsers(dest="command", required=True)

    guard = commands.add_parser("deploy-guard", help="may this maintenance window run? (NFR-03)")
    guard.add_argument("--start", type=_when, required=True)
    guard.add_argument("--end", type=_when, required=True)
    guard.add_argument("--no-evidence", action="store_true")

    day = commands.add_parser("game-day", help="the primary LLM provider goes down (NFR-03)")
    day.add_argument("--primary", default="anthropic")
    day.add_argument("--live", action="store_true", help="real adapters (staging only)")

    drill = commands.add_parser("restore-drill", help="back up, restore, verify, time (NFR-04)")
    drill.add_argument("--local", action="store_true", required=True)

    commands.add_parser("provider-terms", help="record each enabled provider's data terms")

    arguments = parser.parse_args(argv)

    if arguments.command == "deploy-guard":
        from firebid.db.engine import service_session_scope
        from firebid.ops.deploy_guard import check

        with service_session_scope() as session:
            verdict = check(session, arguments.start, arguments.end)
        print(verdict.summary())
        if not arguments.no_evidence:
            _write(
                arguments.root,
                "deploy-guard",
                {
                    "window_start": verdict.window_start,
                    "window_end": verdict.window_end,
                    "allowed": verdict.allowed,
                    "conflicts": [c.__dict__ for c in verdict.conflicts],
                    "summary": verdict.summary(),
                },
            )
        return 0 if verdict.allowed else 1

    if arguments.command == "game-day":
        from firebid.ai_gateway import build_adapters
        from firebid.ai_gateway.config import load_config
        from firebid.ops import game_day

        config = load_config()
        adapters = (
            build_adapters(config)
            if arguments.live
            else game_day.rehearsal_adapters(config, arguments.primary)
        )
        if arguments.live:
            adapters[arguments.primary] = game_day.rehearsal_adapters(config, arguments.primary)[
                arguments.primary
            ]
        result = game_day.run(
            config, adapters, arguments.primary, "staging" if arguments.live else "local rehearsal"
        )
        out = _write(arguments.root, "game-day", result.to_json())
        print(result.to_json()["summary"], f"-> {out}")
        return 0 if result.passed else 1

    if arguments.command == "restore-drill":
        from firebid.ops import restore_drill

        rehearsal = restore_drill.local()
        out = _write(arguments.root, "restore-drill", rehearsal.to_json())
        print(rehearsal.to_json()["summary"], f"-> {out}")
        return 0 if rehearsal.passed else 1

    from firebid.ai_gateway.config import load_config

    config = load_config()
    providers = {
        name: {
            "enabled": provider.enabled,
            "kind": provider.kind,
            "region": getattr(provider, "region", None),
            "retention": getattr(provider, "retention", None),
            "no_training": getattr(provider, "no_training", None),
            "approved_data_classes": list(provider.approved_data_classes),
        }
        for name, provider in config.providers.items()
    }
    unconfirmed = [
        name
        for name, p in providers.items()
        if p["enabled"]
        and any(
            "confirm" in str(p.get(k) or "confirm") for k in ("region", "retention", "no_training")
        )
    ]
    out = _write(
        arguments.root,
        "provider-terms",
        {
            "providers": providers,
            "confirmed": not unconfirmed,
            "summary": (
                "every enabled provider's region, retention and no-training terms are confirmed"
                if not unconfirmed
                else f"to confirm under decision D2: {', '.join(unconfirmed)}"
            ),
        },
    )
    print(f"provider terms recorded -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
