"""A synthetic fire protection specification, with its known answers (P1-06).

Written the way a Singapore M&E consultant writes a particular specification: numbered
sections and clauses, the fire protection systems among other services, pipe material and
joining by size range, sprinkler heads by type, approved makes. Its answers are exact, so
every attribute the extractor reads can be checked.

One clause contradicts the drawings on purpose: the car park sprinkler pipework is to be
galvanised, where the drawing notes say black steel. P1-06 records it as a conditional
attribute; P2-03's drawing-against-specification check uses it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

# (number, heading, body) in reading order. A number with no body is a heading.
CLAUSES: tuple[tuple[str, str, str], ...] = (
    ("1", "GENERAL", ""),
    ("1.1", "Scope", "This section covers the fire protection services to the development."),
    ("1.2", "Standards", "The installation shall comply with SS CP 52 and SS EN 12845."),
    ("2", "AUTOMATIC SPRINKLER SYSTEM", ""),
    ("2.1", "Pipework", ""),
    (
        "2.1.1",
        "",
        "Sprinkler pipework up to and including DN 50 shall be black steel to BS EN 10255 "
        "Heavy grade. Pipework DN 65 and above shall be black steel to ASTM A53 Grade B, "
        "Schedule 40.",
    ),
    (
        "2.1.2",
        "",
        "Pipes up to and including 50 mm shall be joined by screwed fittings. Pipes 65 mm "
        "and above shall be joined by roll-grooved mechanical couplings.",
    ),
    (
        "2.1.3",
        "",
        "All sprinkler pipework within the basement car park shall be hot-dip galvanised.",
    ),
    ("2.2", "Sprinklers", ""),
    (
        "2.2.1",
        "",
        "Sprinkler heads shall be quick response pendent type with a K-factor of K80, rated "
        "at 68°C, chrome finish, unless otherwise indicated.",
    ),
    (
        "2.2.2",
        "",
        "Sidewall sprinklers where shown shall be quick response, K80, 68°C, white finish.",
    ),
    ("2.2.3", "", "Approved makes: Tyco, Viking or Reliable."),
    ("3", "HOSE REEL SYSTEM", ""),
    (
        "3.1",
        "Pipework",
        "Hose reel pipework shall be galvanised steel to BS EN 10255 Medium grade, joined "
        "by screwed fittings throughout.",
    ),
    ("4", "FIRE HYDRANT SYSTEM", ""),
    (
        "4.1",
        "Pipework",
        "Hydrant mains DN 100 and above shall be ductile iron to BS EN 545, with flanged joints.",
    ),
    ("5", "LOW VOLTAGE ELECTRICAL INSTALLATION", ""),
    (
        "5.1",
        "Cables",
        "Cables shall be copper conductors with XLPE insulation, installed in galvanised trunking.",
    ),
)


# Supports (P2-01), as a clause of the sprinkler section a specification may or may not
# have: hanger spacing by size, and, on a project that calls for it, seismic restraint.
SUPPORT_CLAUSES: tuple[tuple[str, str, str], ...] = (
    ("2.3", "Supports", ""),
    (
        "2.3.1",
        "",
        "Hangers for pipes up to and including DN 50 shall be spaced at not more than 3.0 m "
        "centres. Hangers for pipes DN 65 and above shall be fixed at intervals not exceeding "
        "4000 mm.",
    ),
)
SEISMIC_CLAUSES: tuple[tuple[str, str, str], ...] = (
    (
        "2.3.2",
        "",
        "Seismic bracing shall be provided to all sprinkler pipework DN 65 and above.",
    ),
)


def with_supports(seismic: bool = False) -> tuple[tuple[str, str, str], ...]:
    """The specification with its supports clauses, placed at the end of section 2."""
    at = next(i for i, clause in enumerate(CLAUSES) if clause[0] == "3")
    extra = SUPPORT_CLAUSES + (SEISMIC_CLAUSES if seismic else ())
    return (*CLAUSES[:at], *extra, *CLAUSES[at:])


@dataclass(frozen=True)
class Expected:
    system: str
    attribute: str
    value: str
    clause: str
    dn_min: int | None = None
    dn_max: int | None = None
    condition: str | None = None


EXPECTED: tuple[Expected, ...] = (
    Expected("sprinkler", "pipe_material", "black_steel", "2.1.1", None, 50),
    Expected("sprinkler", "pipe_standard", "BS EN 10255", "2.1.1", None, 50),
    Expected("sprinkler", "pipe_class", "Heavy", "2.1.1", None, 50),
    Expected("sprinkler", "pipe_material", "black_steel", "2.1.1", 65, None),
    Expected("sprinkler", "pipe_standard", "ASTM A53 Grade B", "2.1.1", 65, None),
    Expected("sprinkler", "pipe_class", "Schedule 40", "2.1.1", 65, None),
    Expected("sprinkler", "joining_method", "threaded", "2.1.2", None, 50),
    Expected("sprinkler", "joining_method", "grooved", "2.1.2", 65, None),
    Expected(
        "sprinkler", "pipe_material", "galvanised_steel", "2.1.3", None, None, "basement car park"
    ),
    Expected("sprinkler", "sprinkler_type", "pendent", "2.2.1"),
    Expected("sprinkler", "response", "quick", "2.2.1"),
    Expected("sprinkler", "k_factor", "80", "2.2.1"),
    Expected("sprinkler", "temperature_rating_c", "68", "2.2.1"),
    Expected("sprinkler", "finish", "chrome", "2.2.1"),
    Expected("sprinkler", "sprinkler_type", "sidewall", "2.2.2"),
    Expected("sprinkler", "response", "quick", "2.2.2"),
    Expected("sprinkler", "k_factor", "80", "2.2.2"),
    Expected("sprinkler", "temperature_rating_c", "68", "2.2.2"),
    Expected("sprinkler", "finish", "white", "2.2.2"),
    Expected("sprinkler", "approved_make", "Tyco", "2.2.3"),
    Expected("sprinkler", "approved_make", "Viking", "2.2.3"),
    Expected("sprinkler", "approved_make", "Reliable", "2.2.3"),
    Expected("hose_reel", "pipe_material", "galvanised_steel", "3.1"),
    Expected("hose_reel", "pipe_standard", "BS EN 10255", "3.1"),
    Expected("hose_reel", "pipe_class", "Medium", "3.1"),
    Expected("hose_reel", "joining_method", "threaded", "3.1"),
    Expected("hydrant", "pipe_material", "ductile_iron", "4.1", 100, None),
    Expected("hydrant", "pipe_standard", "BS EN 545", "4.1", 100, None),
    Expected("hydrant", "joining_method", "flanged", "4.1", 100, None),
)


EXPECTED_SUPPORTS: tuple[Expected, ...] = (
    Expected("sprinkler", "hanger_spacing_mm", "3000", "2.3.1", None, 50),
    Expected("sprinkler", "hanger_spacing_mm", "4000", "2.3.1", 65, None),
)
EXPECTED_SEISMIC: tuple[Expected, ...] = (
    Expected("sprinkler", "seismic_restraint", "required", "2.3.2", 65, None),
)


def specification_docx(
    title: str = "PARTICULAR SPECIFICATION FOR FIRE PROTECTION SERVICES",
    revision: str = "Rev B",
    clauses: tuple[tuple[str, str, str], ...] = CLAUSES,
) -> bytes:
    """The specification as a Word document: heading styles for sections, numbered text."""
    import docx

    document = docx.Document()
    document.core_properties.title = title
    document.add_paragraph(title, style="Title")
    document.add_paragraph(f"Revision {revision}")
    for number, heading, body in clauses:
        depth = number.count(".")
        if not body:
            document.add_heading(f"{number} {heading}", level=min(depth + 1, 3))
            continue
        if heading:
            document.add_heading(f"{number} {heading}", level=min(depth + 1, 3))
            document.add_paragraph(body)
        else:
            document.add_paragraph(f"{number} {body}")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def specification_pdf(revision: str = "Rev B") -> bytes:
    """The same specification as a PDF, laid out with larger bold headings."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    lines: list[tuple[str, float, bool]] = [
        ("PARTICULAR SPECIFICATION FOR FIRE PROTECTION SERVICES", 14, True),
        (f"Revision {revision}", 10, False),
    ]
    for number, heading, body in CLAUSES:
        if heading:
            lines.append((f"{number} {heading}", 12 if "." not in number else 11, True))
        if body:
            text = f"{number} {body}" if not heading else body
            lines.extend((part, 10, False) for part in _wrap(text, 92))
    buffer = io.BytesIO()
    with PdfPages(buffer) as pages:
        for start in range(0, len(lines), 40):
            figure = plt.figure(figsize=(8.27, 11.69))
            y = 0.95
            for text, size, bold in lines[start : start + 40]:
                figure.text(0.08, y, text, fontsize=size, weight="bold" if bold else "normal")
                y -= 0.022
            pages.savefig(figure)
            plt.close(figure)
    return buffer.getvalue()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines
