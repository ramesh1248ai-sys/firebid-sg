"""Converting legacy Office files, inside the sandbox (FR-DOC-01, item 5).

Singapore fire-protection tenders still arrive with `.xls` rate schedules and `.doc`
specifications, often produced by a system nobody has replaced since 2003. LibreOffice
headless reads them; nothing else in this stack does.

LibreOffice is a very large program reading a format designed for a different era, so it runs
where every other parser of external files runs: in the sandbox, as a child process with a
wall-clock limit, writing only to its scratch directory.

**The original is kept.** The conversion is registered as a derived document pointing back at
it, so an estimator can always go to what the consultant actually sent. A converter is a
lossy step, and the audit trail has to lead back past it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

from firebid.sandbox.runner import run_sandboxed, scratch_dir

log = structlog.get_logger("firebid.sandbox.office")

# What each legacy format converts to, and the filter LibreOffice needs to be told.
CONVERSIONS: dict[str, tuple[str, str]] = {
    "doc": ("docx", "docx:MS Word 2007 XML"),
    "xls": ("xlsx", "xlsx:Calc MS Excel 2007 XML"),
}

CONVERTER = "soffice"
CONVERT_TIMEOUT_SECONDS = 120


class ConversionFailed(Exception):
    """The file could not be converted. Carries a reason an estimator can act on."""


@dataclass(frozen=True)
class Converted:
    payload: bytes
    filename: str
    media_type: str
    converter: str


def _media_type(extension: str) -> str:
    return {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[extension]


def _convert_in_sandbox(payload: bytes, filename: str, source_extension: str) -> dict[str, object]:
    """Runs inside the sandbox. Returns a plain dict because it crosses a process boundary."""
    target_extension, filter_name = CONVERSIONS[source_extension]
    scratch = scratch_dir()
    source = scratch / f"input.{source_extension}"
    source.write_bytes(payload)
    out_dir = scratch / "out"
    out_dir.mkdir(exist_ok=True)

    result = subprocess.run(  # noqa: S603 - a fixed command, inside the sandbox
        [
            CONVERTER,
            "--headless",
            "--norestore",
            # Its own profile in scratch: the sandbox filesystem is read-only everywhere else,
            # and a shared profile is how two concurrent conversions corrupt each other.
            f"-env:UserInstallation=file://{scratch / 'lo-profile'}",
            "--convert-to",
            filter_name,
            "--outdir",
            str(out_dir),
            str(source),
        ],
        capture_output=True,
        timeout=CONVERT_TIMEOUT_SECONDS,
        check=False,
    )

    produced = list(out_dir.glob(f"*.{target_extension}"))
    if not produced:
        message = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise ConversionFailed(
            "this legacy Office file could not be converted, which usually means it is "
            "damaged or password-protected"
            + (f" ({message.splitlines()[-1][:200]})" if message else "")
        )

    return {
        "payload": produced[0].read_bytes(),
        "filename": produced[0].name,
        "extension": target_extension,
    }


def convert_legacy(payload: bytes, filename: str, source_extension: str) -> Converted:
    """Convert a `.doc` or `.xls` to its modern equivalent.

    Raises `ConversionFailed` or `SandboxFailure`; never returns a partial result.
    """
    if source_extension not in CONVERSIONS:
        raise ConversionFailed(f"there is no conversion for '{source_extension}' files")

    stem = Path(filename).stem or "document"
    result = _run(payload, filename, source_extension)
    extension = str(result["extension"])
    converted = result["payload"]
    if not isinstance(converted, bytes):  # pragma: no cover - the sandbox always sends bytes
        raise ConversionFailed("the converter returned something that was not a file")
    log.info("legacy_converted", filename=filename, to=extension)
    return Converted(
        payload=converted,
        filename=f"{stem}.{extension}",
        media_type=_media_type(extension),
        converter=converter_version(),
    )


def _run(payload: bytes, filename: str, source_extension: str) -> dict[str, object]:
    from firebid.sandbox.limits import Limits

    return run_sandboxed(
        _convert_in_sandbox,
        payload,
        filename,
        source_extension,
        limits=Limits(wall_seconds=CONVERT_TIMEOUT_SECONDS + 30),
    )


def converter_available() -> bool:
    """Whether LibreOffice is installed. False outside the sandbox image, which is expected."""
    import shutil

    return shutil.which(CONVERTER) is not None


def converter_version() -> str:
    """Which LibreOffice did the conversion, recorded against the derived document.

    A converted file that looks wrong six months from now is answerable only if the version
    that produced it is written down.
    """
    import shutil

    if shutil.which(CONVERTER) is None:
        return "libreoffice:unavailable"
    try:
        result = subprocess.run(  # noqa: S603 - a fixed command
            [CONVERTER, "--version"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - a broken install
        return "libreoffice:unknown"
    first = result.stdout.decode("utf-8", "replace").strip().splitlines()
    return f"libreoffice:{first[0].strip()}" if first else "libreoffice:unknown"
