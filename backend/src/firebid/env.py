"""Where `backend/.env` is, and what is in it.

One module knows this, so settings and the AI gateway cannot disagree about which file holds
local configuration.

Two decisions worth stating:

* **The path is absolute, derived from this package**, not relative to the working directory.
  A `.env` that works from `backend/` but silently does nothing from the repository root is
  the kind of thing that costs an afternoon.
* **A real environment variable always wins.** The file is for local development. In a
  container the value comes from the environment, and a stale `.env` left in a working copy
  must never override what the deployment set.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

# backend/src/firebid/env.py -> backend/.env
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def env_file() -> Path:
    """The file to read. `FIREBID_ENV_FILE` overrides it, which tests rely on."""
    override = os.environ.get("FIREBID_ENV_FILE")
    return Path(override) if override else DEFAULT_ENV_FILE


@lru_cache
def _file_values(path: str, mtime: float) -> dict[str, str]:
    """Parsed once per file version. `mtime` is in the key so an edit is picked up."""
    del mtime  # only here to key the cache
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def file_values() -> dict[str, str]:
    path = env_file()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    return _file_values(str(path), stamp)


def lookup(name: str) -> str | None:
    """A variable's value: the real environment first, then `backend/.env`."""
    from_environment = os.environ.get(name)
    if from_environment:
        return from_environment
    return file_values().get(name) or None
