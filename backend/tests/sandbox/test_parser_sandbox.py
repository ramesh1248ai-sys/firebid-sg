"""The walls around a parser, tested by trying to walk through each one (NFR-06).

Every test here runs a real child process. That is slower than mocking the sandbox, and it is
the only version worth having: a mocked limit proves the mock works. Each test asserts the
job failed *and* that the pool is still able to run the next one, because a sandbox that
takes the worker down with the job is not a sandbox.

The functions the tests run must be module-level: the child is a fresh interpreter, so it
imports them by name rather than inheriting a closure.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from firebid.sandbox.limits import IS_LINUX, IS_POSIX, Limits, available_walls
from firebid.sandbox.runner import SCRATCH_ENV, SandboxFailure, run_sandboxed, scratch_dir
from firebid.sandbox.safety import MAX_IMAGE_PIXELS, UnsafeContent, describe

pytestmark = pytest.mark.req("NFR-06")

needs_limits = pytest.mark.skipif(
    not IS_POSIX, reason="resource limits are POSIX-only; CI runs Linux"
)


# --- jobs the tests run inside the sandbox -------------------------------------------------


def add(a: int, b: int) -> int:
    return a + b


def reach_the_network() -> str:
    import socket

    with socket.create_connection(("192.0.2.1", 80), timeout=5) as connection:
        return str(connection.getsockname())


def fetch_a_url() -> str:
    from urllib.request import urlopen

    with urlopen("http://192.0.2.1/tender.pdf", timeout=5) as response:
        return str(response.status)


def eat_memory() -> int:
    """Allocate well past the limit, in pieces, the way a decoding parser would."""
    held = []
    for _ in range(4096):
        held.append(bytearray(8 * 1024 * 1024))
    return len(held)


def spin_forever() -> int:
    total = 0
    while True:
        total += 1


def sleep_forever() -> int:
    time.sleep(600)
    return 1


def die_hard() -> int:
    """Leave the interpreter without unwinding, the way a segfaulting C library does."""
    os._exit(3)


def raise_unsafe() -> int:
    raise UnsafeContent("this file declares a structure far larger than any real drawing")


def read_an_entity_bomb() -> int:
    """Parse the classic billion-laughs document. `defusedxml` must refuse it."""
    from xml.etree import ElementTree

    bomb = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY lol "lol">
      <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
      <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
      <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
      <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
      <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
    ]>
    <lolz>&lol5;</lolz>"""
    return len(ElementTree.fromstring(bomb).text or "")  # noqa: S314 - defused in the sandbox


def read_an_external_entity() -> str:
    """An XML file asking the parser to fetch something off the filesystem or the network."""
    from xml.etree import ElementTree

    document = b"""<?xml version="1.0"?>
    <!DOCTYPE data [ <!ENTITY secret SYSTEM "file:///etc/passwd"> ]>
    <data>&secret;</data>"""
    return ElementTree.fromstring(document).text or ""  # noqa: S314 - defused in the sandbox


def decode_an_oversized_image() -> tuple[int, int]:
    """A PNG header declaring far more pixels than any drawing. Pillow must refuse it."""
    import io
    import struct
    import zlib

    from PIL import Image

    width = height = 60_000

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(4))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        return image.size


def report_scratch() -> dict[str, object]:
    directory = scratch_dir()
    written = directory / "page.tmp"
    written.write_bytes(b"a rendered tile")
    return {
        "scratch": str(directory),
        "env": os.environ.get(SCRATCH_ENV, ""),
        "cwd": os.getcwd(),
        "readable": written.read_bytes().decode(),
    }


def report_environment() -> dict[str, str]:
    """Everything the platform passes down, except the one variable the sandbox itself sets."""
    from firebid.sandbox.runner import SCRATCH_ENV

    return {
        name: value
        for name, value in os.environ.items()
        if "FIREBID" in name.upper() and name != SCRATCH_ENV
    }


def report_walls() -> dict[str, object]:
    euid = os.geteuid() if sys.platform != "win32" else -1  # type: ignore[attr-defined,unused-ignore]
    return {"euid": euid, "platform": sys.platform}


# --- the tests -----------------------------------------------------------------------------


class TestItRunsJobs:
    def test_a_job_returns_its_result(self) -> None:
        assert run_sandboxed(add, 2, 3) == 5

    def test_a_job_gets_a_scratch_directory_it_can_write_to(self) -> None:
        report = run_sandboxed(report_scratch)

        assert report["readable"] == "a rendered tile"
        assert report["env"] == report["scratch"]
        assert report["cwd"] == report["scratch"], "a job starts in its own scratch directory"

    def test_the_scratch_directory_is_removed_afterwards(self) -> None:
        from pathlib import Path

        report = run_sandboxed(report_scratch)

        assert not Path(str(report["scratch"])).exists()

    def test_a_job_does_not_inherit_the_platform_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A parser reads a file. It has no business holding a database URL or an API key."""
        monkeypatch.setenv("FIREBID_DATABASE_URL", "postgresql://firebid:secret@db/firebid")
        monkeypatch.setenv("FIREBID_PAYLOAD_ENCRYPTION_KEY", "a-key")

        assert run_sandboxed(report_environment) == {}


class TestTheNetworkIsClosed:
    def test_a_job_cannot_open_a_connection(self) -> None:
        with pytest.raises(SandboxFailure) as refused:
            run_sandboxed(reach_the_network, limits=Limits(wall_seconds=30))

        assert refused.value.kind in {"error", "timeout"}

    def test_a_job_cannot_fetch_a_url(self) -> None:
        """A PDF or XML asking for a remote resource fails rather than hanging."""
        with pytest.raises(SandboxFailure) as refused:
            run_sandboxed(fetch_a_url, limits=Limits(wall_seconds=30))

        assert refused.value.kind in {"error", "timeout"}

    def test_the_pool_still_works_afterwards(self) -> None:
        with pytest.raises(SandboxFailure):
            run_sandboxed(reach_the_network, limits=Limits(wall_seconds=30))

        assert run_sandboxed(add, 1, 1) == 2


class TestLimits:
    @needs_limits
    def test_a_job_over_its_memory_limit_is_stopped(self) -> None:
        with pytest.raises(SandboxFailure) as stopped:
            run_sandboxed(eat_memory, limits=Limits(memory_bytes=256 * 1024 * 1024))

        assert stopped.value.kind == "memory"
        assert "memory" in stopped.value.reason

    @needs_limits
    def test_the_pool_survives_a_job_that_exhausts_memory(self) -> None:
        with pytest.raises(SandboxFailure):
            run_sandboxed(eat_memory, limits=Limits(memory_bytes=256 * 1024 * 1024))

        assert run_sandboxed(add, 40, 2) == 42, "the next job must still run"

    @needs_limits
    def test_a_job_over_its_cpu_limit_is_stopped(self) -> None:
        with pytest.raises(SandboxFailure) as stopped:
            run_sandboxed(spin_forever, limits=Limits(cpu_seconds=2, wall_seconds=60))

        assert stopped.value.kind in {"cpu", "killed", "error"}
        assert run_sandboxed(add, 1, 1) == 2

    def test_a_job_that_hangs_is_stopped_by_the_clock(self) -> None:
        """A parser waiting on something that never arrives burns no CPU, so only the wall
        clock catches it."""
        started = time.monotonic()

        with pytest.raises(SandboxFailure) as stopped:
            run_sandboxed(sleep_forever, limits=Limits(wall_seconds=3))

        assert stopped.value.kind == "timeout"
        assert time.monotonic() - started < 30, "it must not wait for the job to finish"
        assert run_sandboxed(add, 1, 1) == 2

    def test_a_job_that_crashes_out_of_the_interpreter_is_reported(self) -> None:
        with pytest.raises(SandboxFailure) as stopped:
            run_sandboxed(die_hard)

        assert stopped.value.kind == "killed"
        assert "damaged" in stopped.value.reason or "unexpectedly" in stopped.value.reason
        assert run_sandboxed(add, 1, 1) == 2


class TestHostileContent:
    def test_an_xml_entity_bomb_is_refused(self) -> None:
        with pytest.raises(SandboxFailure) as refused:
            run_sandboxed(read_an_entity_bomb, limits=Limits(wall_seconds=30))

        assert refused.value.kind in {"error", "memory"}
        assert "entity" in refused.value.reason.lower() or "read" in refused.value.reason

    def test_an_external_entity_is_refused(self) -> None:
        """Otherwise an XML file in a tender set reads files off the parser's filesystem."""
        with pytest.raises(SandboxFailure):
            run_sandboxed(read_an_external_entity, limits=Limits(wall_seconds=30))

    def test_an_oversized_image_is_refused(self) -> None:
        with pytest.raises(SandboxFailure) as refused:
            run_sandboxed(decode_an_oversized_image, limits=Limits(wall_seconds=60))

        assert refused.value.kind in {"error", "memory"}

    def test_unsafe_content_keeps_its_own_reason(self) -> None:
        with pytest.raises(SandboxFailure) as refused:
            run_sandboxed(raise_unsafe)

        assert "far larger than any real drawing" in refused.value.reason

    def test_the_pool_survives_all_of_them(self) -> None:
        for job in (read_an_entity_bomb, raise_unsafe):
            with pytest.raises(SandboxFailure):
                run_sandboxed(job, limits=Limits(wall_seconds=30))

        assert run_sandboxed(add, 7, 7) == 14


class TestTheReasonsAreReadable:
    """A refusal an estimator cannot act on is barely better than a silent drop."""

    def test_a_memory_failure_explains_itself(self) -> None:
        assert "memory" in describe(MemoryError())
        assert "traceback" not in describe(MemoryError()).lower()

    def test_an_unknown_failure_still_says_something(self) -> None:
        reason = describe(ValueError("startxref not found"))

        assert "could not be read" in reason
        assert "startxref not found" in reason

    def test_a_reason_is_a_sentence_not_a_traceback(self) -> None:
        for error in (MemoryError(), UnsafeContent("a bomb"), ValueError("broken")):
            assert "\n" not in describe(error)
            assert 'File "' not in describe(error)


class TestTheWallsAreTheOnesWeThink:
    def test_the_pixel_limit_is_set_where_we_said(self) -> None:
        assert MAX_IMAGE_PIXELS == 400_000_000

    def test_the_platform_reports_which_walls_it_can_put_up(self) -> None:
        walls = available_walls()

        assert walls["python_network_block"] is True
        assert walls["resource_limits"] is IS_POSIX
        assert walls["network_namespace"] is (IS_LINUX and hasattr(os, "unshare"))

    @pytest.mark.skipif(not IS_LINUX, reason="the deployed platform is Linux")
    def test_linux_gets_every_wall(self) -> None:
        """The sandbox is only a sandbox where all of it is available. CI runs Linux."""
        walls = available_walls()

        assert walls["network_namespace"] is True
        assert walls["resource_limits"] is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
    def test_a_job_keeps_its_own_user_inside_the_namespace(self) -> None:
        """A namespace without an identity map would cost the job access to its scratch."""
        report = run_sandboxed(report_walls)

        expected = os.geteuid() if sys.platform != "win32" else -1  # type: ignore[attr-defined,unused-ignore]
        assert report["euid"] == expected
