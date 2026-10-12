"""
Additional models for interactions, notes, reminders, webhooks, and files.

These entities are primarily accessed through the V1 API for write operations.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, Field, model_validator

from .entities import AffinityModel, CompanySummary, OpportunitySummary, PersonSummary
from .types import (
    CompanyId,
    FileId,
    InteractionDirection,
    InteractionId,
    InteractionLoggingType,
    InteractionType,
    ISODatetime,
    ListEntryId,
    ListId,
    MergeStatus,
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
    WebhookStatus,
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
    """A note to create (V2 API).

    Attach it to at least one person, company or opportunity, or make it a reply with
    ``parent_id`` (a reply has no attachments of its own). ``type`` says how ``content`` is
    written: ``PLAIN_TEXT`` (default; escaped and wrapped in paragraphs) or ``HTML``.
    """

    content: str
    type: NoteType = NoteType.PLAIN_TEXT
    person_ids: list[PersonId] = Field(default_factory=list)
    company_ids: list[CompanyId] = Field(
        default_factory=list, validation_alias=AliasChoices("company_ids", "organization_ids")
    )
    opportunity_ids: list[OpportunityId] = Field(default_factory=list)
    parent_id: NoteId | None = None  # For reply notes
    creator_id: UserId | None = None
    created_at: ISODatetime | None = None

    @model_validator(mode="after")
    def _reply_has_no_entities(self) -> NoteCreate:
        if self.parent_id is not None and (
            self.person_ids or self.company_ids or self.opportunity_ids
        ):
            raise ValueError(
                "A reply (parent_id) can't have its own persons, companies or opportunities"
            )
        return self


class NoteUpdate(AffinityModel):
    """Changes to a note: only what is set is sent.

    ``type`` says how ``content`` is written (``PLAIN_TEXT`` by default, or ``HTML``), whatever
    the note was before; V2 stores both as HTML. ``person_ids``/``company_ids``/
    ``opportunity_ids`` replace the note's attachments of that kind (``[]`` clears them; root
    notes only).
    """

    content: str | None = None
    type: NoteType = NoteType.PLAIN_TEXT
    person_ids: list[PersonId] | None = None
    company_ids: list[CompanyId] | None = None
    opportunity_ids: list[OpportunityId] | None = None


# V2 Note response format
class NoteContent(AffinityModel):
    """Note content in V2 format."""

    html: str | None = None


class NoteV2(AffinityModel):
    """V2 API note format.

    ``replies_count`` and the attached ``companies`` / ``persons`` / ``opportunities`` (first ones,
    with ``*_total``) are filled only when requested with ``includes`` (``notes.list_v2`` /
    ``notes.get_v2``); otherwise they are ``None``.
    """

    id: NoteId
    # Discriminator: "entities", "interaction", "ai-notetaker", "ai-notetaker-reply", "user-reply"
    type: str
    content: NoteContent
    creator: PersonSummary | None = None
    created_at: ISODatetime = Field(alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")

    # Parent for replies
    parent: dict[str, Any] | None = None

    # Mentions
    mentions: list[dict[str, Any]] = Field(default_factory=list)

    # Interaction / AI-notetaker notes: the meeting, and its transcript (None when the org
    # doesn't keep transcripts or it was deleted)
    interaction: dict[str, Any] | None = None
    transcript_id: int | None = Field(None, alias="transcriptId")

    # Only with includes=
    replies_count: int | None = Field(None, alias="repliesCount")
    companies: list[CompanySummary] | None = None
    companies_total: int | None = None
    persons: list[PersonSummary] | None = None
    persons_total: int | None = None
    opportunities: list[OpportunitySummary] | None = None
    opportunities_total: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_previews(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for preview, key in (
            ("companiesPreview", "companies"),
            ("personsPreview", "persons"),
            ("opportunitiesPreview", "opportunities"),
        ):
            if isinstance(data.get(preview), dict):
                block = data[preview]
                data = {
                    **data,
                    key: block.get("data") or [],
                    f"{key}_total": block.get("totalCount"),
                }
                data.pop(preview, None)
        return data


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
# Bare references (V2: webhooks, reminders, files)
# =============================================================================


class PersonRef(AffinityModel):
    """A bare person reference (``{"id": …}``)."""

    id: PersonId


class CompanyRef(AffinityModel):
    """A bare company reference (``{"id": …}``)."""

    id: CompanyId


class OpportunityRef(AffinityModel):
    """A bare opportunity reference (``{"id": …}``)."""

    id: OpportunityId


# =============================================================================
# Reminder Models (V2 API)
# =============================================================================


class ReminderRecurrence(AffinityModel):
    """How a recurring reminder repeats: every ``period_days`` days, reset by ``reset_type``."""

    period_days: int = Field(alias="periodDays")
    reset_type: ReminderResetType = Field(alias="resetTrigger")


class Reminder(AffinityModel):
    """A reminder (V2 shape). At most one of ``person``, ``company``, ``opportunity`` is set."""

    id: ReminderIdType
    type: ReminderType
    # active / overdue / completed (a recurring reminder is never completed: completing it
    # moves due_date forward)
    status: ReminderStatus
    content: str | None = None
    due_date: ISODatetime = Field(alias="dueDate")
    # None for one-time reminders
    recurrence: ReminderRecurrence | None = None

    creator: PersonRef | None = None
    owner: PersonRef | None = None
    completer: PersonRef | None = None

    person: PersonRef | None = None
    company: CompanyRef | None = None
    opportunity: OpportunityRef | None = None

    created_at: ISODatetime = Field(alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")
    completed_at: ISODatetime | None = Field(None, alias="completedAt")

    @property
    def reset_type(self) -> ReminderResetType | None:
        """``recurrence.reset_type`` (None for one-time reminders)."""
        return self.recurrence.reset_type if self.recurrence else None

    @property
    def reminder_days(self) -> int | None:
        """``recurrence.period_days`` (None for one-time reminders)."""
        return self.recurrence.period_days if self.recurrence else None


class ReminderCreate(AffinityModel):
    """A reminder to create.

    One-time reminders need ``due_date``; recurring ones need ``reset_type`` and
    ``reminder_days`` (1-3650; ``due_date`` optional). Attach at most one of ``person_id``,
    ``company_id``, ``opportunity_id``; a reminder with none is created through the V1 API.
    """

    owner_id: UserId
    type: ReminderType
    content: str | None = None
    due_date: ISODatetime | None = None
    reset_type: ReminderResetType | None = None
    reminder_days: int | None = None

    person_id: PersonId | None = None
    company_id: CompanyId | None = Field(
        None, validation_alias=AliasChoices("company_id", "organization_id")
    )
    opportunity_id: OpportunityId | None = None

    @model_validator(mode="after")
    def _one_entity(self) -> ReminderCreate:
        given = [x for x in (self.person_id, self.company_id, self.opportunity_id) if x is not None]
        if len(given) > 1:
            raise ValueError("A reminder can be attached to one entity only")
        return self


class ReminderUpdate(AffinityModel):
    """Changes to a reminder: fields left as ``None`` are not changed.

    ``clear_content=True`` removes the content. ``is_completed=True`` completes the reminder
    (a recurring one moves to its next due date); ``False`` reopens a one-time reminder.
    Setting ``type`` sends the whole update through the V1 API (V2 can't change the type).
    """

    owner_id: UserId | None = None
    type: ReminderType | None = None
    content: str | None = None
    clear_content: bool = False
    due_date: ISODatetime | None = None
    reset_type: ReminderResetType | None = None
    reminder_days: int | None = None
    is_completed: bool | None = None

    @model_validator(mode="after")
    def _content_or_clear(self) -> ReminderUpdate:
        if self.clear_content and self.content is not None:
            raise ValueError("Pass content or clear_content=True, not both")
        return self


# =============================================================================
# Webhook Models (V2 API)
# =============================================================================


def _reject_v1_fields(data: Any, renamed: dict[str, str]) -> Any:
    """Fail on 1.x field names instead of silently ignoring them (models ignore extra keys)."""
    if isinstance(data, dict):
        for old, new in renamed.items():
            if old in data:
                raise ValueError(f"'{old}' was removed in SDK 2.0 (V2 webhooks): use {new}")
    return data


class Webhook(AffinityModel):
    """A webhook: event notifications delivered to an external URL (V2 ``webhooks.Webhook``)."""

    id: WebhookId
    url: str
    # An empty list means the webhook receives every event type.
    subscriptions: list[WebhookEvent] = Field(default_factory=list)
    status: WebhookStatus
    # None when Affinity has no record of the creator.
    creator: PersonRef | None = None
    created_at: ISODatetime = Field(alias="createdAt")
    # None until the webhook is first updated.
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")


class WebhookCreate(AffinityModel):
    """A webhook to create.

    ``subscriptions`` is required. **An empty list subscribes the webhook to every event type**,
    so every change in the organization is sent to ``url``; name the events you need.
    """

    url: str
    subscriptions: list[WebhookEvent]

    @model_validator(mode="before")
    @classmethod
    def _no_v1_fields(cls, data: Any) -> Any:
        return _reject_v1_fields(data, {"webhook_url": "url="})


class WebhookUpdate(AffinityModel):
    """Changes to a webhook: only the fields you set are sent.

    ``subscriptions=[]`` subscribes the webhook to every event type.
    """

    url: str | None = None
    subscriptions: list[WebhookEvent] | None = None
    status: WebhookStatus | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_v1_fields(cls, data: Any) -> Any:
        return _reject_v1_fields(
            data, {"webhook_url": "url=", "disabled": "status=WebhookStatus.DISABLED (or ACTIVE)"}
        )


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
    # V1 sends `organization_id`; accept the camelCase form too. Serialization keeps
    # `organizationId` so `model_dump(by_alias=True)` output is unchanged.
    company_id: CompanyId | None = Field(
        None,
        validation_alias=AliasChoices("organization_id", "organizationId"),
        serialization_alias="organizationId",
    )
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


class FieldValueChangeField(AffinityModel):
    """The field a V2 field value change belongs to."""

    id: str
    entity_type: str = Field(alias="entityType")
    name: str
    type: str  # "global" or "list"


class FieldValueChangeEntity(AffinityModel):
    """The entity (person, company or opportunity) whose field value changed."""

    id: int


class FieldValueChangeListEntry(AffinityModel):
    """The list entry a V2 field value change occurred in."""

    id: ListEntryId
    list_id: ListId = Field(alias="listId")


class FieldValueChangeV2(AffinityModel):
    """A field value change from ``GET /v2/field-value-changes`` (all entities and fields).

    ``action_type`` is ``"add"``, ``"update"`` or ``"delete"`` (V2 names; V1 uses create/update/
    delete). ``value_type`` is the field's value type (e.g. ``"ranked-dropdown"``) and ``value``
    is kept as the API sends it (its shape depends on ``value_type``; a deleted dropdown option
    or entity comes back as a reference with a ``displayValue`` snapshot). ``changer`` is
    ``None`` for system changes such as enrichment.
    """

    id: int
    field: FieldValueChangeField
    entity: FieldValueChangeEntity
    list_entry: FieldValueChangeListEntry | None = Field(None, alias="listEntry")
    changer: User | None = None
    changed_at: ISODatetime = Field(alias="changedAt")
    action_type: str = Field(alias="actionType")
    value_type: str | None = Field(None, alias="type")
    value: Any = None


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

    # Absent when no monthly quota applies (documented for v2 `orgPerMonth`; v1 and v2 share
    # one request pool, so v1's `org_monthly` is treated the same way).
    org_monthly: RateLimitInfo | None = Field(None, alias="orgMonthly")
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
    status: str  # pending, in-progress, success, failed
    results_summary: MergeResultsSummary | None = Field(None, alias="resultsSummary")


class CompanyMergeState(AffinityModel):
    """One company merge (V2 ``GET /v2/company-merges``)."""

    id: int
    status: MergeStatus
    task_id: str = Field(alias="taskId")
    started_at: ISODatetime = Field(alias="startedAt")
    primary_company_id: CompanyId = Field(alias="primaryCompanyId")
    duplicate_company_id: CompanyId = Field(alias="duplicateCompanyId")
    completed_at: ISODatetime | None = Field(None, alias="completedAt")
    error_message: str | None = Field(None, alias="errorMessage")


class PersonMergeState(AffinityModel):
    """One person merge (V2 ``GET /v2/person-merges``)."""

    id: int
    status: MergeStatus
    task_id: str = Field(alias="taskId")
    started_at: ISODatetime = Field(alias="startedAt")
    primary_person_id: PersonId = Field(alias="primaryPersonId")
    duplicate_person_id: PersonId = Field(alias="duplicatePersonId")
    completed_at: ISODatetime | None = Field(None, alias="completedAt")
    error_message: str | None = Field(None, alias="errorMessage")


class MergeResponse(AffinityModel):
    """Response from initiating a merge."""

    task_url: str = Field(alias="taskUrl")
