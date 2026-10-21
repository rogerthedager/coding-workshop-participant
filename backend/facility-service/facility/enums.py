"""Shared domain and query enums; their values are stable API/database contracts."""

from enum import StrEnum


class IncidentStatus(StrEnum):
    """Current lifecycle state of an incident."""

    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    RESOLVED = "Resolved"
    CLOSED = "Closed"
    CANCELLED = "Cancelled"


class IncidentPriority(StrEnum):
    """Incident urgency, ordered through rank rather than alphabetical labels."""

    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"

    @property
    def rank(self) -> int:
        """Return increasing urgency, independent of API spelling or declaration order."""
        return PRIORITY_RANK[self]


class UserRole(StrEnum):
    """Stored additive roles; Employee remains implicit for every user."""

    ENGINEER = "engineer"
    LEAD = "lead"
    ADMIN = "admin"


class EngineerAvailability(StrEnum):
    """An engineer's self-reported availability, independent of activation."""

    AVAILABLE = "Available"
    BUSY = "Busy"
    OFF_DUTY = "Off-duty"


class EngineerSkill(StrEnum):
    """Engineer specialties, one per top-level incident category except "Other"."""

    IT_AV_EQUIPMENT = "IT and AV equipment"
    NETWORK_WIFI = "Network and Wi-Fi"
    FURNITURE = "Furniture"
    HVAC_TEMPERATURE = "HVAC and temperature"
    ELECTRICAL_LIGHTING = "Electrical and lighting"
    PLUMBING_RESTROOMS = "Plumbing and restrooms"
    CLEANING = "Cleaning"
    ACCESS_SECURITY = "Access and security"


class NoteVisibility(StrEnum):
    """Audience of a note and its associated audit history."""

    PUBLIC = "public"
    INTERNAL = "internal"


class EscalationState(StrEnum):
    """Persisted lifecycle of an escalation request."""

    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SUPERSEDED = "Superseded"


class BlockedReason(StrEnum):
    """Fixed reason codes; free-text explanation belongs in a note."""

    WAITING_ON_PARTS = "Waiting on parts"
    WAITING_ON_EMPLOYEE = "Waiting on employee"
    VENDOR_NEEDED = "Vendor needed"
    ACCESS_NEEDED = "Access needed"
    OTHER = "Other"


class IncidentEventType(StrEnum):
    """Stable event categories for append-only incident auditing."""

    CREATED = "created"
    DETAILS_CHANGED = "details_changed"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    PRIORITY_CHANGED = "priority_changed"
    ESCALATION_REQUESTED = "escalation_requested"
    ESCALATION_APPROVED = "escalation_approved"
    ESCALATION_REJECTED = "escalation_rejected"
    ESCALATION_SUPERSEDED = "escalation_superseded"
    NOTE_ADDED = "note_added"
    NOTE_EDITED = "note_edited"
    NOTE_HIDDEN = "note_hidden"
    DUPLICATE_MARKED = "duplicate_marked"
    AUTO_CLOSED = "auto_closed"
    ATTACHMENT_ADDED = "attachment_added"


class IncidentScope(StrEnum):
    """Optional narrowing of the caller's authorized incident list."""

    ALL = "all"
    MINE = "mine"
    ASSIGNED = "assigned"


class IncidentSort(StrEnum):
    """Supported incident ordering fields, independent of SQL column names."""

    CREATED = "created"
    UPDATED = "updated"
    PRIORITY = "priority"
    SLA_DUE = "sla_due"


class SortDirection(StrEnum):
    """Ordering direction accepted by list APIs."""

    ASC = "asc"
    DESC = "desc"


class AssigneeFilter(StrEnum):
    """A query sentinel; actual assignee identities are UUIDs."""

    UNASSIGNED = "unassigned"


class EscalationFilter(StrEnum):
    """A query sentinel for absence of an escalation, never a persisted state."""

    NONE = "none"


class TriageFilter(StrEnum):
    """A query sentinel for tickets in triage with no recorded reason."""

    NONE = "none"


class TriageReason(StrEnum):
    """Why an unassigned ticket waits in the admin triage queue."""

    UNROUTED_GROUP = "unrouted_group"
    NO_ENGINEER = "no_engineer"
    RETURNED = "returned"


class CloseKind(StrEnum):
    """How a ticket reached Closed."""

    CONFIRMED = "confirmed"
    ADMIN = "admin"
    AUTO = "auto"
    DUPLICATE = "duplicate"


class SlaState(StrEnum):
    """Computed SLA health; never stored."""

    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    BREACHED = "breached"
    MET = "met"
    MISSED = "missed"


class NotificationType(StrEnum):
    """In-app notification categories."""

    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    RETURNED_TO_TRIAGE = "returned_to_triage"
    NOTE_ADDED = "note_added"
    ESCALATION_DECIDED = "escalation_decided"
    DUPLICATE_MARKED = "duplicate_marked"
    SLA_AT_RISK = "sla_at_risk"
    SLA_BREACHED = "sla_breached"


class AttachmentStatus(StrEnum):
    """Upload lifecycle: presigned, verified, or refused."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AttachmentType(StrEnum):
    """Accepted image media types."""

    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"
    HEIC = "image/heic"


class TeamPeriod(StrEnum):
    """Team lead summary windows."""

    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"


class APIErrorCode(StrEnum):
    """Stable machine-readable error codes returned to API clients."""

    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VALIDATION_ERROR = "validation_error"
    PRECONDITION_REQUIRED = "precondition_required"
    LOCKED = "locked"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"
    REQUEST_ERROR = "request_error"


MAX_ENGINEER_SKILLS = 2
REQUIRED_ENGINEER_SKILLS = 2
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
AUTO_CLOSE_HOURS = 72
SLA_AT_RISK_FRACTION = 0.75
# (acknowledge, resolve) windows in hours, PRD section 7.
SLA_HOURS = {
    IncidentPriority.CRITICAL: (1, 4),
    IncidentPriority.HIGH: (4, 24),
    IncidentPriority.MEDIUM: (24, 72),
    IncidentPriority.LOW: (48, 120),
}
TEAM_PERIOD_DAYS = {TeamPeriod.WEEK: 7, TeamPeriod.MONTH: 30, TeamPeriod.QUARTER: 90}
FINAL_STATUSES = frozenset({IncidentStatus.CLOSED, IncidentStatus.CANCELLED})
NON_FINAL_STATUSES = (
    IncidentStatus.OPEN,
    IncidentStatus.IN_PROGRESS,
    IncidentStatus.BLOCKED,
    IncidentStatus.RESOLVED,
)
RESOLVED_STATUSES = (IncidentStatus.RESOLVED, IncidentStatus.CLOSED)
PRIORITY_RANK = {
    IncidentPriority.LOW: 1,
    IncidentPriority.MEDIUM: 2,
    IncidentPriority.HIGH: 3,
    IncidentPriority.CRITICAL: 4,
}
BLOCKED_REASONS = frozenset(BlockedReason)
VALID_TRANSITIONS = frozenset(
    {
        (IncidentStatus.OPEN, IncidentStatus.IN_PROGRESS),
        (IncidentStatus.OPEN, IncidentStatus.CANCELLED),
        (IncidentStatus.IN_PROGRESS, IncidentStatus.BLOCKED),
        (IncidentStatus.BLOCKED, IncidentStatus.IN_PROGRESS),
        (IncidentStatus.IN_PROGRESS, IncidentStatus.RESOLVED),
        (IncidentStatus.RESOLVED, IncidentStatus.CLOSED),
        (IncidentStatus.RESOLVED, IncidentStatus.IN_PROGRESS),
    }
)
