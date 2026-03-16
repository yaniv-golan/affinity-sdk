"""
Additional models for interactions, notes, reminders, webhooks, and files.

These entities are primarily accessed through the V1 API for write operations.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .entities import AffinityModel, PersonSummary
from .types import (
    CompanyId,
    FileId,
    InteractionDirection,
    InteractionId,
    InteractionLoggingType,
    InteractionType,
    ISODatetime,
    NoteId,
    NoteType,
    OpportunityId,
    PersonId,
    ReminderIdType,
    ReminderResetType,
    ReminderStatus,
    ReminderType,
    TenantId,
    UserId,
    WebhookEvent,
    WebhookId,
)

# =============================================================================
# Note Models
# =============================================================================


class Note(AffinityModel):
    """
    A note attached to one or more entities.

    Notes can be plain text, HTML, or AI-generated meeting summaries.
    """

    id: NoteId
    creator_id: UserId = Field(alias="creatorId")
    content: str | None = None
    type: NoteType = NoteType.PLAIN_TEXT

    # Associated entities
    person_ids: list[PersonId] = Field(default_factory=list, alias="personIds")
    associated_person_ids: list[PersonId] = Field(default_factory=list, alias="associatedPersonIds")
    interaction_person_ids: list[PersonId] = Field(
        default_factory=list, alias="interactionPersonIds"
    )
    mentioned_person_ids: list[PersonId] = Field(default_factory=list, alias="mentionedPersonIds")
    company_ids: list[CompanyId] = Field(default_factory=list, alias="organizationIds")
    opportunity_ids: list[OpportunityId] = Field(default_factory=list, alias="opportunityIds")

    # Interaction association
    interaction_id: int | None = Field(None, alias="interactionId")
    interaction_type: InteractionType | None = Field(None, alias="interactionType")
    is_meeting: bool = Field(False, alias="isMeeting")

    # Thread support
    parent_id: NoteId | None = Field(None, alias="parentId")

    # Timestamps
    created_at: ISODatetime = Field(alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")


class NoteCreate(AffinityModel):
    """Data for creating a new note (V1 API)."""

    content: str
    type: NoteType = NoteType.PLAIN_TEXT
    person_ids: list[PersonId] = Field(default_factory=list)
    company_ids: list[CompanyId] = Field(default_factory=list, alias="organization_ids")
    opportunity_ids: list[OpportunityId] = Field(default_factory=list)
    parent_id: NoteId | None = None  # For reply notes
    creator_id: UserId | None = None
    created_at: ISODatetime | None = None


class NoteUpdate(AffinityModel):
    """Data for updating a note (V1 API)."""

    content: str


# V2 Note response format
class NoteContent(AffinityModel):
    """Note content in V2 format."""

    html: str | None = None


class NoteV2(AffinityModel):
    """V2 API note format."""

    id: NoteId
    type: str  # "user_root_note", "user_reply_note", etc.
    content: NoteContent
    creator: PersonSummary
    created_at: ISODatetime = Field(alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")

    # Parent for replies
    parent: dict[str, Any] | None = None

    # Mentions
    mentions: list[dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# Interaction Models (V1 only for CRUD)
# =============================================================================


class Interaction(AffinityModel):
    """
    An interaction (email, meeting, call, or chat message).

    Different interaction types have different fields available.
    """

    id: InteractionId
    type: InteractionType
    date: ISODatetime

    # Associated persons
    persons: list[PersonSummary] = Field(default_factory=list)
    attendees: list[str] = Field(default_factory=list)

    # Meeting/Call specific
    title: str | None = None
    start_time: ISODatetime | None = Field(None, alias="startTime")
    end_time: ISODatetime | None = Field(None, alias="endTime")

    # Email specific
    subject: str | None = None

    # Chat/Email direction
    direction: InteractionDirection | None = None

    # Notes attached to this interaction
    notes: list[NoteId] = Field(default_factory=list)

    # Logging info
    logging_type: InteractionLoggingType | None = Field(None, alias="loggingType")
    manual_creator_id: UserId | None = Field(None, alias="manualCreatorId")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")


class InteractionCreate(AffinityModel):
    """Data for creating an interaction (V1 API).

    Note: The API only supports person IDs as participants. The Affinity UI's
    "Also add to... search for an entity" feature (associating an interaction
    with a company or opportunity) has no API equivalent.
    """

    type: InteractionType
    person_ids: list[PersonId]
    content: str
    date: ISODatetime
    direction: InteractionDirection | None = None


class InteractionUpdate(AffinityModel):
    """Data for updating an interaction (V1 API)."""

    person_ids: list[PersonId] | None = None
    content: str | None = None
    date: ISODatetime | None = None
    direction: InteractionDirection | None = None


# =============================================================================
# Reminder Models (V1 only)
# =============================================================================


class Reminder(AffinityModel):
    """A reminder attached to an entity."""

    id: ReminderIdType
    type: ReminderType
    status: ReminderStatus
    content: str | None = None

    # Due date and recurrence
    due_date: ISODatetime = Field(alias="dueDate")
    reset_type: ReminderResetType | None = Field(None, alias="resetType")
    reminder_days: int | None = Field(None, alias="reminderDays")

    # Persons involved
    creator: PersonSummary | None = None
    owner: PersonSummary | None = None
    completer: PersonSummary | None = None

    # Associated entity (one of these)
    person: PersonSummary | None = None
    company: dict[str, Any] | None = Field(None, alias="organization")
    opportunity: dict[str, Any] | None = None

    # Timestamps
    created_at: ISODatetime = Field(alias="createdAt")
    completed_at: ISODatetime | None = Field(None, alias="completedAt")


class ReminderCreate(AffinityModel):
    """Data for creating a reminder (V1 API)."""

    owner_id: UserId
    type: ReminderType
    content: str | None = None
    due_date: ISODatetime | None = None  # Required for one-time
    reset_type: ReminderResetType | None = None  # Required for recurring
    reminder_days: int | None = None  # Required for recurring

    # Associate with one entity
    person_id: PersonId | None = None
    company_id: CompanyId | None = Field(None, alias="organization_id")
    opportunity_id: OpportunityId | None = None


class ReminderUpdate(AffinityModel):
    """Data for updating a reminder (V1 API)."""

    owner_id: UserId | None = None
    type: ReminderType | None = None
    content: str | None = None
    due_date: ISODatetime | None = None
    reset_type: ReminderResetType | None = None
    reminder_days: int | None = None
    is_completed: bool | None = None


# =============================================================================
# Webhook Models (V1 only)
# =============================================================================


class WebhookSubscription(AffinityModel):
    """A webhook subscription for real-time events."""

    id: WebhookId
    webhook_url: str = Field(alias="webhookUrl")
    subscriptions: list[WebhookEvent] = Field(default_factory=list)
    disabled: bool = False
    created_by: UserId = Field(alias="createdBy")


class WebhookCreate(AffinityModel):
    """Data for creating a webhook subscription (V1 API)."""

    webhook_url: str
    subscriptions: list[WebhookEvent] = Field(default_factory=list)


class WebhookUpdate(AffinityModel):
    """Data for updating a webhook subscription (V1 API)."""

    webhook_url: str | None = None
    subscriptions: list[WebhookEvent] | None = None
    disabled: bool | None = None


# =============================================================================
# Entity File Models (V1 only)
# =============================================================================


class EntityFile(AffinityModel):
    """A file attached to an entity."""

    id: FileId
    name: str
    size: int
    # Observed missing in some V1 responses; treat as optional for robustness.
    content_type: str | None = Field(None, alias="contentType")

    # Associated entity
    person_id: PersonId | None = Field(None, alias="personId")
    company_id: CompanyId | None = Field(None, alias="organizationId")
    opportunity_id: OpportunityId | None = Field(None, alias="opportunityId")

    # Uploader
    uploader_id: UserId = Field(alias="uploaderId")

    # Timestamps
    created_at: ISODatetime = Field(alias="createdAt")


# =============================================================================
# Relationship Strength Models (V1 only)
# =============================================================================


class RelationshipStrength(AffinityModel):
    """Relationship strength between internal and external persons."""

    internal_id: UserId = Field(alias="internalId")
    external_id: PersonId = Field(alias="externalId")
    strength: float  # 0.0 to 1.0


# =============================================================================
# Auth/Whoami Models
# =============================================================================


class Tenant(AffinityModel):
    """Affinity tenant (organization/team) information."""

    id: TenantId
    name: str
    subdomain: str


class User(AffinityModel):
    """Current user information."""

    id: UserId
    first_name: str = Field(alias="firstName")
    last_name: str | None = Field(alias="lastName")
    email: str = Field(alias="emailAddress")


class Grant(AffinityModel):
    """API key grant information."""

    type: str
    scopes: list[str] = Field(default_factory=list)
    created_at: ISODatetime = Field(alias="createdAt")

    @model_validator(mode="before")
    @classmethod
    def _coerce_scope_to_scopes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "scopes" in value:
            return value
        scope = value.get("scope")
        if isinstance(scope, str):
            updated = dict(value)
            updated["scopes"] = [scope]
            return updated
        return value

    @property
    def scope(self) -> str | None:
        """
        Backwards-compatible convenience for older response shapes.

        Returns the first scope string when present, otherwise None.
        """
        return self.scopes[0] if self.scopes else None


class WhoAmI(AffinityModel):
    """Response from whoami endpoint."""

    tenant: Tenant
    user: User
    grant: Grant


# =============================================================================
# Rate Limit Models
# =============================================================================


class RateLimitInfo(AffinityModel):
    """Rate limit information for an API key."""

    limit: int
    remaining: int
    reset: int  # Seconds until reset
    used: int


class RateLimits(AffinityModel):
    """Current rate limit status."""

    org_monthly: RateLimitInfo = Field(alias="orgMonthly")
    api_key_per_minute: RateLimitInfo = Field(alias="apiKeyPerMinute")


# =============================================================================
# Merge Task Models (V2 BETA)
# =============================================================================


class MergeResultsSummary(AffinityModel):
    """Summary of merge operation results."""

    total: int
    success: int
    failed: int
    in_progress: int = Field(alias="inProgress")


class MergeTask(AffinityModel):
    """Async merge task status."""

    id: str
    status: str  # pending, in_progress, success, failed
    results_summary: MergeResultsSummary | None = Field(None, alias="resultsSummary")


class MergeResponse(AffinityModel):
    """Response from initiating a merge."""

    task_url: str = Field(alias="taskUrl")
