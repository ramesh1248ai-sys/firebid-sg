"""Library-level hardening applied inside the sandbox, before any file is opened.

The walls in `limits` stop a parser doing damage. These stop three specific attacks that need
no exploit at all — each is a valid file that any unconfigured parser will happily process
until the machine falls over:

* **An XML entity bomb** (`billion laughs`): ten nested entities, each referencing the last
  ten times, expand to gigabytes of text from a few hundred bytes. `defusedxml` refuses
  entity expansion and external entity resolution outright.
* **A decompression-bomb image**: a 200 kB PNG declaring 60,000 by 60,000 pixels, which Pillow
  will decode into fourteen gigabytes of bitmap if allowed to.
* **A zip bomb**, handled in `ingest.archives` before anything reaches a parser.

Each of these is raised as `UnsafeContent`, which carries a reason an estimator can read.
"""

from __future__ import annotations

# A drawing scanned at 600 dpi on A0 is about 200 megapixels. Twice that is generous for a
# real tender drawing and far below what a bomb declares.
MAX_IMAGE_PIXELS = 400_000_000


class UnsafeContent(Exception):
    """The file is structurally hostile rather than merely broken."""


def harden_xml() -> None:
    """Refuse entity expansion and external entity resolution across the standard library."""
    import defusedxml

    defusedxml.defuse_stdlib()  # type: ignore[attr-defined]


def harden_images() -> None:
    """Cap the pixels Pillow will decode, and make the warning an error.

    Pillow warns at half the limit and raises at the limit. The warning is the useful signal:
    turning it into an exception means a bomb is refused at 200 megapixels rather than
    allocating up to 400 before anything stops it.
    """
    import warnings

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)


def harden_libraries() -> None:
    """Every library-level guard, applied together. Called once per sandboxed job."""
    harden_xml()
    harden_images()


def describe(error: BaseException) -> str:
    """Turn a parser's failure into a sentence an estimator can act on.

    A traceback tells an estimator nothing they can use. Naming the shape of the problem tells
    them whether to re-export the file, ask the consultant for it again, or escalate it.
    """
    from PIL import Image

    if isinstance(error, MemoryError):
        return (
            "the file needed more memory than one parser job is allowed, which usually means "
            "it is far larger than a drawing or is deliberately oversized"
        )
    if isinstance(error, Image.DecompressionBombWarning | Image.DecompressionBombError):
        return (
            "the file declares an image far larger than any real drawing, which is the shape "
            "of a decompression bomb"
        )
    if isinstance(error, UnsafeContent):
        return str(error)

    name = type(error).__name__
    if "Entities" in name or "Entity" in name or "DTDForbidden" in name or "External" in name:
        return (
            "the file contains XML entity definitions, which are refused because they are "
            "used to expand a small file into an enormous one"
        )
    message = str(error).strip().splitlines()[0] if str(error).strip() else name
    return f"the file could not be read: {message[:300]}"
