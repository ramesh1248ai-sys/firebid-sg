"""Opening a ZIP that may not be friendly.

A tender set usually arrives as one archive, so this runs on every upload. Three attacks are
cheap to mount and expensive to suffer, and each has a limit here:

* **A zip bomb**: a few kilobytes that expand to terabytes. Bounded by total uncompressed size
  and by the compression ratio of any single entry.
* **Path traversal**: an entry named `../../etc/passwd`. Entries are never written by path;
  the name is sanitised and only ever used as a label.
* **Nesting**: an archive of archives of archives. Bounded by depth.

Limits are checked against the *declared* sizes first, so a bomb is refused before it is
decompressed, and again while reading, because a header can lie.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

import structlog

log = structlog.get_logger("firebid.ingest.archives")

# A 300-sheet tender set is large but not unbounded.
MAX_TOTAL_UNCOMPRESSED = 4 * 1024 * 1024 * 1024  # 4 GiB
MAX_ENTRIES = 5_000
MAX_DEPTH = 3
# A single entry compressing better than this is a bomb, not a drawing.
MAX_RATIO = 200
# Below this, a high ratio is just a small text file compressing well.
RATIO_FLOOR_BYTES = 1024 * 1024


class ArchiveRefused(Exception):
    """The archive breached a limit. Carries a reason a person can act on."""


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    payload: bytes
    depth: int


def _safe_name(raw: str) -> str:
    """A label, never a path. Traversal and absolute paths are stripped, not rejected.

    Rejecting would lose a whole tender set over one oddly-named file; the name is only ever
    shown to a person and stored as text, so neutering it is enough.
    """
    pure = PurePosixPath(raw.replace("\\", "/"))
    parts = [part for part in pure.parts if part not in ("..", "/", ".")]
    return "/".join(parts) or "unnamed"


def _check_declared(archive: zipfile.ZipFile) -> None:
    """Refuse before decompressing anything, using what the headers claim."""
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ArchiveRefused(f"the archive holds {len(infos)} entries; the limit is {MAX_ENTRIES}")

    declared = sum(info.file_size for info in infos)
    if declared > MAX_TOTAL_UNCOMPRESSED:
        raise ArchiveRefused(
            f"the archive expands to {declared / 1e9:.1f} GB; the limit is "
            f"{MAX_TOTAL_UNCOMPRESSED / 1e9:.1f} GB"
        )

    for info in infos:
        if info.file_size < RATIO_FLOOR_BYTES or info.compress_size == 0:
            continue
        ratio = info.file_size / info.compress_size
        if ratio > MAX_RATIO:
            raise ArchiveRefused(
                f"'{_safe_name(info.filename)}' expands {ratio:.0f}x, which is a compression "
                f"bomb rather than a drawing (limit {MAX_RATIO}x)"
            )


def expand(payload: bytes, depth: int = 0) -> Iterator[ArchiveEntry]:
    """Yield every file in an archive, following nested archives up to `MAX_DEPTH`.

    Raises `ArchiveRefused` before decompressing when a declared limit is breached, and while
    reading when the actual bytes exceed what the headers claimed.
    """
    if depth > MAX_DEPTH:
        raise ArchiveRefused(f"archives nested more than {MAX_DEPTH} deep are refused")

    try:
        archive = zipfile.ZipFile(BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise ArchiveRefused(f"not a readable archive: {error}") from error

    with archive:
        _check_declared(archive)

        written = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = _safe_name(info.filename)

            with archive.open(info) as entry:
                # Read one byte past the declared size: a header that under-reports is a lie
                # worth catching.
                data = entry.read(info.file_size + 1)
            if len(data) > info.file_size:
                raise ArchiveRefused(
                    f"'{name}' is larger than its header claims, which means the archive is "
                    "malformed or hostile"
                )

            written += len(data)
            if written > MAX_TOTAL_UNCOMPRESSED:
                raise ArchiveRefused(
                    f"the archive expanded past {MAX_TOTAL_UNCOMPRESSED / 1e9:.1f} GB while "
                    "being read"
                )

            if data[:4] == b"PK\x03\x04" and _is_plain_archive(data):
                log.info("nested_archive", name=name, depth=depth + 1)
                yield from expand(data, depth + 1)
            else:
                yield ArchiveEntry(name=name, payload=data, depth=depth)


def _is_plain_archive(payload: bytes) -> bool:
    """A ZIP that is really an OOXML document is a document, not an archive to expand."""
    from firebid.ingest.detection import FileKind, detect

    return detect(payload).kind is FileKind.ZIP
