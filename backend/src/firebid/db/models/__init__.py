"""Entity models. Importing this package registers every table on the metadata."""

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
from firebid.db.models.documents import Addendum, Document, Sheet, SheetRevision
from firebid.db.models.takeoff import DetectedObject, Evidence, MeasurementRule, QtoItem
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
    "BidMember",
    "Boq",
    "BoqLine",
    "BoqLineSource",
    "ClientBoq",
    "ClientBoqLine",
    "ClientBoqMapping",
    "DeadlineAlert",
    "DetectedObject",
    "Document",
    "Evidence",
    "HumanTask",
    "IdCounter",
    "MeasurementRule",
    "Organisation",
    "Project",
    "QtoItem",
    "Rate",
    "Sheet",
    "SheetRevision",
    "SystemHeartbeat",
    "SystemJobResult",
    "TenderPackage",
    "UserRole",
]
