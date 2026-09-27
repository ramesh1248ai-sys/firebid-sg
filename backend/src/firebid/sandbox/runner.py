"""Running one parser job in a process that can be killed without regret.

The shape is deliberately plain: a fresh interpreter per job, the walls put up inside it
before the parser is imported, and a parent that waits with a stopwatch. Nothing is shared —
no database handle, no object-store client, no credentials — so a job that goes wrong has
nothing to go wrong *with*.

**The pool survives every job.** A job that exhausts its memory, spins past its CPU limit,
hangs, or segfaults its way out of the interpreter comes back as a `SandboxFailure` carrying
a reason. That is the whole contract: `run_sandboxed` returns a value or raises
`SandboxFailure`, and nothing a hostile file does produces a third outcome.

A `spawn` start method, not `fork`: forking would hand the child a copy of everything the
worker holds, including connection pools and whatever secrets are in memory. Spawn costs
about a fifth of a second per job, which against a per-sheet parse is not worth optimising.
"""

from __future__ import annotations

import multiprocessing
import os
import shutil
import signal
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import structlog

from firebid.sandbox.limits import DEFAULT_LIMITS, IS_LINUX, Limits

log = structlog.get_logger("firebid.sandbox")

# The environment variable a child reads to find its scratch directory.
SCRATCH_ENV = "FIREBID_SANDBOX_SCRATCH"

# Whether this host can give a job its own network namespace. Starts hopeful on Linux; the
# first job that finds the kernel refusing turns it off for the life of the worker, so the
# cost of a host that cannot do namespaces is one retry rather than one per job.
_NAMESPACE_WORKS = IS_LINUX


class SandboxFailure(Exception):
    """A job did not produce a result. `reason` is written for an estimator, not a log.

    `kind` is one of `timeout`, `memory`, `cpu`, `killed`, `unsafe` or `error`, so callers can
    tell "this file is hostile" from "this parser has a bug" without parsing prose.
    """

    def __init__(self, kind: str, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SandboxResult:
    value: Any
    walls: dict[str, bool]


def _child(
    pipe: Connection,
    function: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    limits: Limits,
    scratch: str,
    use_namespace: bool,
) -> None:
    """The whole of a job's life. Runs in a fresh interpreter with nothing inherited."""
    from firebid.sandbox.limits import NamespaceUnavailable, harden
    from firebid.sandbox.safety import describe, harden_libraries

    try:
        walls = harden(limits, use_namespace=use_namespace)
    except NamespaceUnavailable as error:
        # This process is now in a namespace it cannot leave, so it reports and stops; the
        # parent runs the job again without asking for one.
        pipe.send(("no_namespace", str(error), ""))
        pipe.close()
        return

    try:
        harden_libraries()
        os.environ[SCRATCH_ENV] = scratch
        os.chdir(scratch)
    except BaseException as error:  # pragma: no cover - a failure to build the walls
        pipe.send(("setup_failed", f"the sandbox could not be prepared: {error}", ""))
        pipe.close()
        return

    try:
        value = function(*args, **kwargs)
    except MemoryError as error:
        pipe.send(("memory", describe(error), ""))
    except BaseException as error:
        # BaseException, not Exception: a parser that raises SystemExit or trips the CPU
        # limit must still come back as a reason rather than as a silent exit code.
        pipe.send(("error", describe(error), traceback.format_exc(limit=8)))
    else:
        try:
            pipe.send(("ok", value, walls))
        except Exception as error:
            pipe.send(("error", f"the result could not be returned: {error}", ""))
    finally:
        pipe.close()


def run_sandboxed[T](
    function: Callable[..., T],
    *args: Any,
    limits: Limits | None = None,
    **kwargs: Any,
) -> T:
    """Run `function` in a sandboxed child process and return its result.

    `function` must be importable by name (a module-level function) and its arguments and
    result must pickle, because the child is a fresh interpreter rather than a fork. For the
    same reason the process calling this needs a real entry point: a `python -` heredoc has
    no importable `__main__`, and the child dies re-importing it.

    Raises `SandboxFailure` for every way a job can fail to produce a value.
    """
    limits = limits or DEFAULT_LIMITS
    for attempt in (1, 2):
        try:
            return _attempt(function, args, kwargs, limits, use_namespace=_NAMESPACE_WORKS)
        except _NoNamespace as refusal:
            _disable_namespaces(str(refusal))
            if attempt == 2:  # pragma: no cover - the flag is off by the second attempt
                raise SandboxFailure("error", "the sandbox could not be prepared") from refusal
    raise AssertionError("unreachable")  # pragma: no cover


class _NoNamespace(Exception):
    """Internal: the kernel refused a namespace, so the job needs running again without one."""


def _disable_namespaces(reason: str) -> None:
    global _NAMESPACE_WORKS
    if _NAMESPACE_WORKS:
        log.warning("sandbox_namespace_unavailable", reason=reason)
    _NAMESPACE_WORKS = False


def _attempt[T](
    function: Callable[..., T],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    limits: Limits,
    *,
    use_namespace: bool,
) -> T:
    context = multiprocessing.get_context("spawn")
    parent_end, child_end = context.Pipe(duplex=False)
    scratch = tempfile.mkdtemp(prefix="firebid-sandbox-")

    process = context.Process(
        target=_child,
        args=(child_end, function, args, kwargs, limits, scratch, use_namespace),
        daemon=True,
    )
    try:
        process.start()
        child_end.close()  # the parent holds only the reading end
        result: T = _collect(process, parent_end, limits, function)
        return result
    finally:
        _stop(process)
        parent_end.close()
        shutil.rmtree(scratch, ignore_errors=True)


def _collect(
    process: multiprocessing.process.BaseProcess,
    parent_end: Any,
    limits: Limits,
    function: Callable[..., Any],
) -> Any:
    name = getattr(function, "__qualname__", repr(function))

    if not parent_end.poll(limits.wall_seconds):
        # Nothing came back in time. The child may be spinning, hung on a malformed
        # structure, or waiting on something that will never arrive.
        log.warning("sandbox_timeout", job=name, wall_seconds=limits.wall_seconds)
        raise SandboxFailure(
            "timeout",
            f"reading this file took longer than {limits.wall_seconds:.0f} seconds and was "
            "stopped; the file is unusually complex, or damaged in a way that makes the "
            "parser loop",
        )

    try:
        message = parent_end.recv()
    except EOFError:
        # The child died without a word: a segfault, an OOM kill, or a signal.
        raise _died(process, name) from None

    kind = message[0]
    if kind == "ok":
        _, value, walls = message
        log.debug("sandbox_ok", job=name, walls=walls)
        return value
    if kind == "no_namespace":
        raise _NoNamespace(message[1])

    _, reason, detail = message
    log.warning("sandbox_failed", job=name, kind=kind, reason=reason, detail=detail)
    raise SandboxFailure(kind, reason, detail)


def _died(process: multiprocessing.process.BaseProcess, name: str) -> SandboxFailure:
    """Work out why a child vanished, from the only evidence there is: its exit code."""
    process.join(timeout=5)
    code = process.exitcode

    if code is not None and code < 0:
        received = -code
        if received == getattr(signal, "SIGKILL", None):
            # The kernel's OOM killer, or a hard kill. Either way the job asked for too much.
            log.warning("sandbox_killed", job=name, signal="SIGKILL")
            return SandboxFailure(
                "memory",
                "reading this file used more memory than one job is allowed and it was "
                "stopped; the file is far larger than a drawing, or deliberately oversized",
            )
        if received == getattr(signal, "SIGXCPU", None):
            return SandboxFailure(
                "cpu",
                "reading this file used more processor time than one job is allowed; it is "
                "unusually complex, or damaged in a way that makes the parser loop",
            )
        name_of = signal.Signals(received).name if received in set(signal.Signals) else received
        log.warning("sandbox_crashed", job=name, signal=str(name_of))
        return SandboxFailure(
            "killed",
            "the parser stopped unexpectedly while reading this file, which usually means "
            "the file is damaged",
            detail=f"signal {name_of}",
        )

    return SandboxFailure(
        "killed",
        "the parser stopped unexpectedly while reading this file",
        detail=f"exit code {code}",
    )


def _stop(process: multiprocessing.process.BaseProcess) -> None:
    """Make sure nothing is left running. A hostile file does not get to leave a process."""
    if not process.is_alive():
        process.join(timeout=1)
        return
    process.terminate()
    process.join(timeout=5)
    if process.is_alive():  # pragma: no cover - only a child ignoring SIGTERM reaches this
        process.kill()
        process.join(timeout=5)


def scratch_dir() -> Path:
    """The per-job scratch directory, from inside a sandboxed job.

    The sandbox filesystem is read-only apart from this; a parser that needs to write (which
    LibreOffice does) writes here, and it is removed when the job ends.
    """
    return Path(os.environ.get(SCRATCH_ENV, tempfile.gettempdir()))
