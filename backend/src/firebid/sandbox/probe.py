"""Checking that the sandbox is still a sandbox.

A sandbox degrades quietly. A kernel upgrade, a changed seccomp profile or a move to a
different runtime can each take a wall away without anything failing, and the first sign
would be an incident. So the pool runs one trivial job at startup and reports which walls
actually stood — not which ones this platform could put up in principle.

The result is cached for the life of the process: the walls are decided by the kernel and the
container, neither of which changes while the process runs, and a health check should not
spawn a process every time somebody loads a dashboard.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Any

import structlog

from firebid.sandbox.limits import Limits
from firebid.sandbox.runner import SandboxFailure, run_sandboxed

log = structlog.get_logger("firebid.sandbox.probe")

# Every wall that must stand for the pool to be reported as fully sandboxed.
REQUIRED_WALLS = ("resource_limits", "python_network_block")


def _inspect() -> dict[str, Any]:
    """Runs inside the sandbox and reports what it can see from in there."""
    import socket

    from firebid.sandbox.limits import NetworkBlocked

    try:
        socket.create_connection(("192.0.2.1", 80), timeout=1)
        network_blocked = False
    except NetworkBlocked:
        network_blocked = True
    except OSError:
        # The connection failed for some other reason. Not proof of a wall, so it is not
        # reported as one.
        network_blocked = False

    limits: dict[str, Any] = {}
    user: int | None = None
    if sys.platform != "win32":
        import resource

        soft, _ = resource.getrlimit(resource.RLIMIT_AS)
        limits["memory_bytes"] = soft
        limits["cpu_seconds"] = resource.getrlimit(resource.RLIMIT_CPU)[0]
        user = os.geteuid()

    return {
        "python_network_block": network_blocked,
        "resource_limits": bool(limits) and limits.get("memory_bytes", -1) > 0,
        "user": user,
        "platform": sys.platform,
        **limits,
    }


@lru_cache(maxsize=1)
def sandbox_report() -> dict[str, Any]:
    """Run a probe job and report the walls that stood, or why the pool could not run one."""
    try:
        report = run_sandboxed(_inspect, limits=Limits(wall_seconds=60))
    except SandboxFailure as failure:
        log.error("sandbox_probe_failed", kind=failure.kind, reason=failure.reason)
        return {"ok": False, "error": failure.reason}

    from firebid.sandbox.runner import _NAMESPACE_WORKS

    report["network_namespace"] = _NAMESPACE_WORKS
    missing = [wall for wall in REQUIRED_WALLS if not report.get(wall)]
    report["ok"] = not missing
    if missing:
        report["walls_missing"] = missing
        log.error("sandbox_degraded", missing=missing)
    elif not _NAMESPACE_WORKS:
        # Not a failure: the pool's container has no route off its network either way. It is
        # reported so a deployment that loses the stronger wall is visible.
        log.info("sandbox_without_namespace")
    return report
