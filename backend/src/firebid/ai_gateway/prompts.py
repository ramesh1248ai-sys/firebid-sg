"""The prompt registry.

Prompts are files, not strings in code, so a change to one is reviewable in a diff and
identifiable afterwards. A prompt's version is the hash of its content, which means a prompt
cannot be edited without the version changing, and every call records the version it used
(NFR-11).

    ai_gateway/prompts/<route>/v1.md              the prompt for a route
    ai_gateway/prompts/<route>/v1.anthropic.md    a variant for one model family

Front matter names the route, its purpose and its owner, so a prompt is never anonymous.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from firebid.ai_gateway.errors import ConfigError

PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"
FRONT_MATTER = re.compile(r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)$", re.DOTALL)
VERSION_FILE = re.compile(r"^v(?P<number>\d+)(?:\.(?P<family>[a-z0-9_-]+))?\.md$")


@dataclass(frozen=True)
class Prompt:
    route: str
    number: int
    family: str | None
    text: str
    version: str
    purpose: str
    owner: str

    @property
    def name(self) -> str:
        suffix = f".{self.family}" if self.family else ""
        return f"{self.route}/v{self.number}{suffix}"


def _parse(path: Path, route: str, number: int, family: str | None) -> Prompt:
    raw = path.read_text(encoding="utf-8")
    match = FRONT_MATTER.match(raw)
    if match is None:
        raise ConfigError(
            f"{path} has no front matter; a prompt must declare route, purpose and owner"
        )
    try:
        meta = yaml.safe_load(match.group("meta")) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} has unreadable front matter: {error}") from error

    missing = [key for key in ("route", "purpose", "owner") if not meta.get(key)]
    if missing:
        raise ConfigError(f"{path} front matter is missing: {', '.join(missing)}")
    if meta["route"] != route:
        raise ConfigError(
            f"{path} declares route '{meta['route']}' but sits in the '{route}' directory"
        )

    body = match.group("body").strip()
    # The version is the hash of the whole file: change a word and the version changes.
    version = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return Prompt(
        route=route,
        number=number,
        family=family,
        text=body,
        version=version,
        purpose=str(meta["purpose"]),
        owner=str(meta["owner"]),
    )


@lru_cache
def load_prompts(root: Path | None = None) -> dict[str, tuple[Prompt, ...]]:
    """Every prompt on disk, newest version first within each route."""
    root = root or PROMPT_ROOT
    found: dict[str, list[Prompt]] = {}
    if not root.exists():
        return {}

    for route_directory in sorted(root.iterdir()):
        if not route_directory.is_dir():
            continue
        route = route_directory.name
        for path in sorted(route_directory.iterdir()):
            match = VERSION_FILE.match(path.name)
            if match is None:
                raise ConfigError(
                    f"{path} is not a prompt file; expected v<N>.md or v<N>.<family>.md"
                )
            prompt = _parse(path, route, int(match.group("number")), match.group("family"))
            found.setdefault(route, []).append(prompt)

    return {
        route: tuple(sorted(prompts, key=lambda p: p.number, reverse=True))
        for route, prompts in found.items()
    }


def prompt_for(route: str, family: str | None = None, root: Path | None = None) -> Prompt | None:
    """The newest prompt for a route, preferring a variant for this model family.

    A route with no prompt file is not an error: plenty of routes are driven entirely by the
    caller's messages.
    """
    prompts = load_prompts(root).get(route)
    if not prompts:
        return None
    newest = prompts[0].number
    candidates = [prompt for prompt in prompts if prompt.number == newest]
    if family:
        for prompt in candidates:
            if prompt.family == family:
                return prompt
    for prompt in candidates:
        if prompt.family is None:
            return prompt
    return candidates[0]


def family_of(provider_kind: str) -> str:
    """Which prompt variant a provider should prefer."""
    return {"openai_compatible": "openai"}.get(provider_kind, provider_kind)
