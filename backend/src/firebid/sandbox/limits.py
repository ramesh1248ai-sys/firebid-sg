"""The walls a parser runs inside, and how each one is put up.

Every parser in this system reads a file a stranger sent us. `pypdfium2`, `ezdxf`, Pillow and
LibreOffice are all large C or C++ bodies of code that were written to be useful, not to be
attacked, so the working assumption here is that any of them can be made to run away with
memory, spin forever, or — in the worst case — do something deliberate.

Four walls, put up in the child process before the file is touched:

* **No network.** A new network namespace when the kernel allows it, and Python's socket
  layer blocked either way. The namespace is the real wall; the socket block is what holds
  when the namespace is refused, and it also catches the ordinary case of a parser deciding
  to fetch a remote entity.
* **Memory.** `RLIMIT_AS`, so an allocation past the limit raises `MemoryError` inside the
  child rather than the kernel's OOM killer choosing a victim elsewhere on the host.
* **CPU and wall-clock.** `RLIMIT_CPU` catches a spin; the parent's timeout catches a sleep,
  a deadlock, or anything else that stops the job without burning CPU.
* **A non-root user and a read-only filesystem**, which come from the container the pool runs
  in (`infra/sandbox/Dockerfile`) rather than from here, because a process cannot take those
  away from itself in any way an attacker would find inconvenient.

Not every wall stands on every platform. `available_walls()` reports which are actually up,
the health check surfaces it, and a test asserts the deployed platform gets all of them — so
a sandbox that has quietly become a plain subprocess is visible rather than assumed.
"""

from __future__ import annotations

import os
import platform
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

IS_LINUX = sys.platform.startswith("linux")
IS_POSIX = os.name == "posix"


@dataclass(frozen=True)
class Limits:
    """What one parser job may use. Generous enough for a 200 MB drawing set, and no more."""

    memory_bytes: int = 2 * 1024 * 1024 * 1024
    cpu_seconds: int = 120
    wall_seconds: float = 180.0

    def replace(self, **changes: object) -> Limits:
        from dataclasses import replace

        return replace(self, **changes)  # type: ignore[arg-type]


DEFAULT_LIMITS = Limits()


class NetworkBlocked(OSError):
    """A parser tried to open a connection. Raised inside the sandbox, never outside it."""


def _blocked(*args: object, **kwargs: object) -> None:
    raise NetworkBlocked(
        "a sandboxed parser tried to use the network, which is never allowed; the file it "
        "was reading is asking for something remote"
    )


def block_python_networking() -> None:
    """Take the network away from Python's standard library.

    This is not a security boundary on its own — a C extension calling `connect(2)` directly
    walks straight past it — which is why the namespace below is attempted first. It is here
    because it turns the common case (an XML file with a remote entity, a PDF with a remote
    resource) into a clear error naming the file, instead of a hang.
    """
    socket.socket = _blocked  # type: ignore[assignment,misc]
    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.socketpair = _blocked  # type: ignore[assignment]
    if hasattr(socket, "create_server"):
        socket.create_server = _blocked  # type: ignore[assignment]
    socket.setdefaulttimeout(1)


class NamespaceUnavailable(Exception):
    """The kernel would not give this process an empty network namespace."""


def unshare_network() -> None:
    """Put this process in an empty network namespace, with its own user still mapped.

    Two steps, and the second is not optional. `CLONE_NEWUSER` is what lets an unprivileged
    process create a network namespace at all, but a new user namespace with no mapping
    leaves the process as the overflow user, which would cost it access to its own scratch
    directory. Writing an identity map puts the same user back, so only the network changed.

    Raises `NamespaceUnavailable` when the kernel refuses. The caller treats that as "this
    host cannot do namespaces" and carries on without one — the process is left in a
    namespace it cannot leave, so it reports and exits rather than trying to recover.
    """
    if not IS_LINUX or not hasattr(os, "unshare") or sys.platform == "win32":
        raise NamespaceUnavailable(f"no namespace support on {sys.platform}")

    uid, gid = os.getuid(), os.getgid()
    try:
        os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)
    except OSError as error:
        # Typically a kernel with unprivileged user namespaces disabled, or a seccomp
        # profile that blocks the call.
        raise NamespaceUnavailable(f"unshare refused: {error}") from error

    try:
        # setgroups must be denied before a gid map may be written.
        Path("/proc/self/setgroups").write_text("deny")
        Path("/proc/self/uid_map").write_text(f"{uid} {uid} 1")
        Path("/proc/self/gid_map").write_text(f"{gid} {gid} 1")
    except OSError as error:
        raise NamespaceUnavailable(f"identity mapping refused: {error}") from error

    if os.geteuid() != uid:  # pragma: no cover - the map was written but did not take
        raise NamespaceUnavailable("the identity mapping did not take effect")


def apply_resource_limits(limits: Limits) -> bool:
    """Cap memory and CPU. True when the caps are real.

    `RLIMIT_AS` caps address space rather than resident memory, so a library that reserves a
    large mapping it never touches counts against it. That is the trade for a limit that
    raises `MemoryError` in the child instead of letting the kernel's OOM killer pick
    something at random on the host.
    """
    if sys.platform == "win32":
        return False
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 5))
    # A parser has no business starting anything, and no business writing a core dump of a
    # file we are treating as hostile.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return True


def drop_environment() -> None:
    """Remove the secrets the parent holds. The child parses a file; it reaches nothing."""
    for name in list(os.environ):
        upper = name.upper()
        if (
            upper.startswith(("FIREBID_", "AWS_", "AZURE_", "GOOGLE_"))
            or "KEY" in upper
            or "SECRET" in upper
            or "TOKEN" in upper
            or "PASSWORD" in upper
        ):
            os.environ.pop(name, None)


def harden(limits: Limits, *, use_namespace: bool = True) -> dict[str, bool]:
    """Put up every wall this platform allows, and report which ones stood.

    Raises `NamespaceUnavailable` when a namespace was asked for and refused, because the
    caller needs to know the difference between "this host cannot" and "this job did not".
    """
    if use_namespace:
        unshare_network()
    walls = {
        "network_namespace": use_namespace,
        "resource_limits": apply_resource_limits(limits),
    }
    block_python_networking()
    drop_environment()
    walls["python_network_block"] = True
    return walls


def available_walls() -> dict[str, object]:
    """Which walls this platform can put up, without putting them up.

    The health check reports this, so a deployment that has lost the network namespace shows
    up as a degraded sandbox rather than as nothing at all.
    """
    return {
        "network_namespace": IS_LINUX and hasattr(os, "unshare"),
        "resource_limits": IS_POSIX,
        "python_network_block": True,
        "platform": platform.system(),
    }
