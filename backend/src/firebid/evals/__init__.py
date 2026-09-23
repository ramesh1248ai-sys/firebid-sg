"""Evaluation: how the platform's accuracy is measured, and against what.

`schema.py` is the golden-set format, `template.py` builds the workbook estimators fill,
`importer.py` turns a filled workbook into validated truth. Metrics, the runner and model
comparison follow in the rest of P0-05.
"""

from firebid.evals.importer import ImportFailed, Problem, import_workbook
from firebid.evals.schema import (
    BoqLineTruth,
    GoldenSet,
    InputClass,
    ObjectCount,
    ObjectType,
    PipeLength,
    RevisionStatus,
    SheetTruth,
    SourceFile,
    TenderManifest,
    TenderTruth,
)
from firebid.evals.template import write_template

__all__ = [
    "BoqLineTruth",
    "GoldenSet",
    "ImportFailed",
    "InputClass",
    "ObjectCount",
    "ObjectType",
    "PipeLength",
    "Problem",
    "RevisionStatus",
    "SheetTruth",
    "SourceFile",
    "TenderManifest",
    "TenderTruth",
    "import_workbook",
    "write_template",
]
