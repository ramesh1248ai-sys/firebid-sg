"""Entity models. Importing this package registers every table on the metadata."""

from firebid.db.models.ai import (
    BidBudget,
    LlmPayload,
    LlmRateBucket,
    LlmResponseCache,
    PayloadCapture,
)
from firebid.db.models.audit import (
    AuditChainLink,
    AuditEvent,
    AuditRetention,
    IdCounter,
)
from firebid.db.models.commercial import (
    Boq,
    BoqLine,
    BoqLineSource,
    ClientBoq,
    ClientBoqLine,
    ClientBoqMapping,
    Rate,
)
from firebid.db.models.core import (
    AppUser,
    Bid,
    BidMember,
    Organisation,
    Project,
    TenderPackage,
    UserRole,
)
from firebid.db.models.documents import (
    Addendum,
    Document,
    DocumentRevision,
    RegisterConfirmation,
    Sheet,
    SheetRevision,
    TitleBlockLayout,
    TransmittalEntry,
)
from firebid.db.models.drawings import GeometryFeature, SheetGeometry, SheetView
from firebid.db.models.symbols import (
    ConsultantProfile,
    LegendEntry,
    ObjectType,
    SymbolInstance,
    SymbolMapping,
)
from firebid.db.models.takeoff import (
    DetectedObject,
    Evidence,
    MeasurementRule,
    PipeRun,
    QtoItem,
)
from firebid.db.models.workflow import AgentRun, Approval, DeadlineAlert, HumanTask
from firebid.db.system import SystemHeartbeat, SystemJobResult

__all__ = [
    "Addendum",
    "AgentRun",
    "AppUser",
    "Approval",
    "AuditChainLink",
    "AuditEvent",
    "AuditRetention",
    "Bid",
    "BidBudget",
    "BidMember",
    "Boq",
    "BoqLine",
    "BoqLineSource",
    "ClientBoq",
    "ClientBoqLine",
    "ClientBoqMapping",
    "ConsultantProfile",
    "DeadlineAlert",
    "DetectedObject",
    "Document",
    "DocumentRevision",
    "Evidence",
    "GeometryFeature",
    "HumanTask",
    "IdCounter",
    "LegendEntry",
    "LlmPayload",
    "LlmRateBucket",
    "LlmResponseCache",
    "MeasurementRule",
    "ObjectType",
    "Organisation",
    "PayloadCapture",
    "PipeRun",
    "Project",
    "QtoItem",
    "Rate",
    "RegisterConfirmation",
    "Sheet",
    "SheetGeometry",
    "SheetRevision",
    "SheetView",
    "SymbolInstance",
    "SymbolMapping",
    "SystemHeartbeat",
    "SystemJobResult",
    "TenderPackage",
    "TitleBlockLayout",
    "TransmittalEntry",
    "UserRole",
]
