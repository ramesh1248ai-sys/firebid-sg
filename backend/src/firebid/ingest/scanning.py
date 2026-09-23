"""Malware scanning, and what to do when the scanner is down.

Tender sets come from outside the company and are opened by parsers that were never written
with hostile input in mind. Everything is scanned before any parser touches it (guardrail 9).

**The scanner being unavailable is not permission to skip the scan.** An outage holds files in
`awaiting_scan` and they are retried; it never lets a file through unscanned. Failing open on
malware scanning is precisely how the one infected file gets in, and it happens on the day the
scanner is down, which is the day nobody is watching.

Speaks clamd's INSTREAM protocol directly. The protocol is a length-prefixed stream and a
one-line answer, which is less to own than an unmaintained client library.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import StrEnum

import structlog

log = structlog.get_logger("firebid.ingest.scanning")

# clamd's default stream chunk limit is 1 MiB; stay under it.
CHUNK_BYTES = 256 * 1024


class Verdict(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    # The scan did not happen. Not a pass.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ScanResult:
    verdict: Verdict
    signature: str = ""
    detail: str = ""

    @property
    def may_be_opened(self) -> bool:
        """Only a clean file may reach a parser."""
        return self.verdict is Verdict.CLEAN


class Scanner:
    """What the ingestion pipeline needs from a scanner."""

    def scan(self, payload: bytes) -> ScanResult:  # pragma: no cover - interface
        raise NotImplementedError


class ClamAvScanner(Scanner):
    def __init__(self, host: str, port: int, timeout_seconds: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_seconds

    def scan(self, payload: bytes) -> ScanResult:
        try:
            answer = self._instream(payload)
        except (OSError, TimeoutError) as error:
            # Deliberately not a pass: the caller holds the file in awaiting_scan.
            log.warning("scanner_unavailable", error=str(error))
            return ScanResult(Verdict.UNAVAILABLE, detail=f"scanner unreachable: {error}")

        if answer.endswith("OK") and "FOUND" not in answer:
            return ScanResult(Verdict.CLEAN)
        if answer.endswith("FOUND"):
            # "stream: Eicar-Test-Signature FOUND"
            signature = answer.rsplit(":", 1)[-1].removesuffix("FOUND").strip()
            log.warning("malware_found", signature=signature)
            return ScanResult(Verdict.INFECTED, signature=signature, detail=answer)
        if "ERROR" in answer:
            # A size limit or an internal error: the file was not cleared, so it is not clean.
            log.warning("scanner_error", answer=answer)
            return ScanResult(Verdict.UNAVAILABLE, detail=answer)
        return ScanResult(Verdict.UNAVAILABLE, detail=f"unexpected answer: {answer}")

    def _instream(self, payload: bytes) -> str:
        with socket.create_connection((self._host, self._port), timeout=self._timeout) as stream:
            stream.settimeout(self._timeout)
            stream.sendall(b"zINSTREAM\x00")
            for start in range(0, len(payload), CHUNK_BYTES):
                chunk = payload[start : start + CHUNK_BYTES]
                stream.sendall(struct.pack("!L", len(chunk)) + chunk)
            stream.sendall(struct.pack("!L", 0))  # end of stream

            received = bytearray()
            while b"\x00" not in received:
                block = stream.recv(4096)
                if not block:
                    break
                received.extend(block)
        return received.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()

    def available(self) -> bool:
        """Whether clamd answers PING, for the health check."""
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=self._timeout
            ) as stream:
                stream.sendall(b"zPING\x00")
                return b"PONG" in stream.recv(64)
        except OSError:
            return False


class AlwaysCleanScanner(Scanner):
    """For tests that are not about scanning. Never wired into a running system."""

    def scan(self, payload: bytes) -> ScanResult:
        return ScanResult(Verdict.CLEAN)


class UnavailableScanner(Scanner):
    """Simulates an outage, so the holding behaviour can be tested."""

    def scan(self, payload: bytes) -> ScanResult:
        return ScanResult(Verdict.UNAVAILABLE, detail="simulated outage")


def get_scanner() -> Scanner:
    from firebid.settings import get_settings

    settings = get_settings()
    return ClamAvScanner(settings.clamav_host, settings.clamav_port)
