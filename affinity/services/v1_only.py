"""
V1-only services: Notes, Reminders, Webhooks, Interactions, Fields, and more.

These services wrap V1 API endpoints that don't have V2 equivalents.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import functools
import logging
import mimetypes
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import AliasChoices, TypeAdapter, ValidationError

from ..downloads import AsyncDownloadedFile, DownloadedFile
from ..exceptions import AffinityError
from ..models.entities import (
    FieldCreate,
    FieldMetadata,
    FieldValue,
    FieldValueChange,
    FieldValueCreate,
)
from ..models.interactions_v2 import CallV2, ChatMessageV2, EmailV2, MeetingV2
from ..models.pagination import AsyncPageIterator, PageIterator, PaginatedResponse, PaginationInfo
from ..models.secondary import (
    EntityFile,
    FieldValueChangeV2,
    Interaction,
    InteractionCreate,
    InteractionUpdate,
    Note,
    NoteCreate,
    NoteUpdate,
    NoteV2,
    RelationshipStrength,
    Reminder,
    ReminderCreate,
    ReminderUpdate,
    WebhookCreate,
    WebhookSubscription,
    WebhookUpdate,
    WhoAmI,
)
from ..models.types import (
    AnyFieldId,
    CompanyId,
    EntityType,
    FieldId,
    FieldValueChangeAction,
    FieldValueId,
    FileId,
    InteractionId,
    InteractionType,
    ListEntryId,
    ListId,
    NoteId,
    OpportunityId,
    PersonId,
    ReminderIdType,
    ReminderResetType,
    ReminderStatus,
    ReminderType,
    UserId,
    WebhookId,
    _normalize_to_utc,
    field_id_to_v1_numeric,
    to_v1_value_type_code,
)
from ..progress import ProgressCallback
from ._org_reads import (
    check_cursor_alone,
    interaction_params,
    note_includes,
    note_params,
    page_of,
)
from ._v2_filters import v2_filter_datetime
from .search import (
    AsyncFileSearchMixin,
    AsyncNoteSearchMixin,
    FileSearchMixin,
    NoteSearchMixin,
)

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient

# TypeVar for default parameter in get_for_entity()
T = TypeVar("T")


# Presigned URL response for file downloads
@dataclass
class PresignedUrl:
    """Presigned URL for downloading a file without authentication.

    Attributes:
        url: The presigned download URL (valid for expires_in seconds)
        file_id: ID of the file
        name: Original filename
        size: File size in bytes
        content_type: MIME type of the file (e.g., "application/pdf")
        expires_in: Seconds until the URL expires (typically 60)
        expires_at: Datetime when the URL expires
    """

    url: str
    file_id: int
    name: str
    size: int
    content_type: str | None
    expires_in: int
    expires_at: datetime


_MAX_INTERACTION_RANGE_DAYS = 365


def _chunk_date_range(
    start: datetime, end: datetime, max_days: int = _MAX_INTERACTION_RANGE_DAYS
) -> list[tuple[datetime, datetime]]:
    """Split a date range into <=max_days chunks.

    Uses adjacent boundaries (next_start == previous_end).
    The Affinity V1 API treats end_time as exclusive, so records at exact
    boundary timestamps appear in the later chunk, not duplicated.
    """
    chunks: list[tuple[datetime, datetime]] = []
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=max_days), end)
        chunks.append((current, chunk_end))
        current = chunk_end
    return chunks


# Sentinel for distinguishing None from "not provided" in get_for_entity()
_UNSET: Any = object()


def _coerce_isoformat(payload: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, datetime):
            payload[key] = value.isoformat()


logger = logging.getLogger("affinity_sdk")


@functools.cache
def _entity_file_adapters() -> dict[str, tuple[tuple[str, ...], TypeAdapter[Any]]]:
    """Per-field (input keys, validator) for EntityFile, built once."""
    adapters: dict[str, tuple[tuple[str, ...], TypeAdapter[Any]]] = {}
    for name, info in EntityFile.model_fields.items():
        keys = [name]
        if info.alias:
            keys.append(info.alias)
        va = info.validation_alias
        if isinstance(va, str):
            keys.append(va)
        elif isinstance(va, AliasChoices):
            keys.extend(c for c in va.choices if isinstance(c, str))
        adapters[name] = (tuple(dict.fromkeys(keys)), TypeAdapter(info.rebuild_annotation()))
    return adapters


def _partial_entity_file(item: dict[str, Any]) -> EntityFile | None:
    """
    Best-effort EntityFile for an item that failed full validation.

    Each field is validated on its own; a field that is missing or invalid is
    set to None. Returns None when the item has no usable id.
    """
    values: dict[str, Any] = {}
    for name, (keys, adapter) in _entity_file_adapters().items():
        raw = next((item[k] for k in keys if k in item), None)
        value: Any = None
        if raw is not None:
            try:
                value = adapter.validate_python(raw)
            except ValidationError:
                value = None
        values[name] = value
    if values.get("id") is None:
        return None
    return EntityFile.model_construct(**values)


def _parse_uploaded_files(result: Any) -> builtins.list[EntityFile]:
    """
    Parse the `entity_files` array of a V1 `POST /entity-files` response.

    Never raises: the upload already succeeded (2xx), so a response shape the
    SDK doesn't expect must not turn it into an error. A missing or malformed
    `entity_files` gives `[]` (older API responses were just `{"success": true}`).
    Items that fail validation are kept as partial records (unvalidated, missing
    fields None) when they carry an id, and skipped otherwise.
    """
    if not isinstance(result, dict):
        return []
    items = result.get("entity_files")
    if not isinstance(items, builtins.list):
        return []
    parsed: builtins.list[EntityFile] = []
    for item in items:
        if not isinstance(item, dict):
            logger.warning("Ignoring non-object entry in upload response: %r", item)
            continue
        try:
            parsed.append(EntityFile.model_validate(item))
            continue
        except ValidationError as exc:
            partial = _partial_entity_file(item)
            if partial is None:
                logger.warning("Ignoring uploaded file entry without a valid id: %s", exc)
                continue
            logger.warning(
                "Uploaded file %s returned an unexpected shape; fields that failed "
                "validation are None: %s",
                partial.id,
                exc,
            )
            parsed.append(partial)
    return parsed


def _upload_target_data(
    *,
    person_id: PersonId | None,
    company_id: CompanyId | None,
    opportunity_id: OpportunityId | None,
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if person_id:
        data["person_id"] = int(person_id)
    if company_id:
        data["organization_id"] = int(company_id)
    if opportunity_id:
        data["opportunity_id"] = int(opportunity_id)
    return data


def _upload_succeeded(result: dict[str, Any]) -> bool:
    if "success" in result:
        return bool(result.get("success"))
    # Any other 2xx JSON response is a success (4xx/5xx raise earlier).
    return True


# =============================================================================
# Notes Service (V1 API)
# =============================================================================


class NoteService(NoteSearchMixin):
    """
    Service for managing notes.

    V2 provides read-only access; use V1 for create/update/delete.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Note]:
        """
        Get notes filtered by entity or creator.

        Args:
            person_id: Filter notes associated with this person
            company_id: Filter notes associated with this company
            opportunity_id: Filter notes associated with this opportunity
            creator_id: Filter notes created by this user
            page_size: Number of results per page
            page_token: Pagination token from previous response

        Returns:
            PaginatedResponse with notes and next_page_token
        """
        params: dict[str, Any] = {}
        if person_id:
            params["person_id"] = int(person_id)
        if company_id:
            params["organization_id"] = int(company_id)
        if opportunity_id:
            params["opportunity_id"] = int(opportunity_id)
        if creator_id:
            params["creator_id"] = int(creator_id)
        if page_size:
            params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token

        data = self._client.get("/notes", params=params or None, v1=True)
        items = data.get("notes", data.get("data", []))
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[Note](
            data=[Note.model_validate(n) for n in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    def get(self, note_id: NoteId) -> Note:
        """Get a single note by ID."""
        data = self._client.get(f"/notes/{note_id}", v1=True)
        return Note.model_validate(data)

    def create(self, data: NoteCreate) -> Note:
        """
        Create a new note.

        Must be associated with at least one person, organization,
        opportunity, or parent note (for replies).
        """
        payload = data.model_dump(by_alias=True, mode="python", exclude_none=True)
        _coerce_isoformat(payload, ("created_at",))
        if not data.person_ids:
            payload.pop("person_ids", None)
        if not data.company_ids:
            payload.pop("organization_ids", None)
        if not data.opportunity_ids:
            payload.pop("opportunity_ids", None)

        result = self._client.post("/notes", json=payload, v1=True)
        return Note.model_validate(result)

    def update(self, note_id: NoteId, data: NoteUpdate) -> Note:
        """Update a note's content."""
        payload = data.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        result = self._client.put(
            f"/notes/{note_id}",
            json=payload,
            v1=True,
        )
        return Note.model_validate(result)

    def delete(self, note_id: NoteId) -> bool:
        """Delete a note."""
        result = self._client.delete(f"/notes/{note_id}", v1=True)
        return bool(result.get("success", False))

    def iter(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        page_size: int | None = None,
    ) -> PageIterator[Note]:
        """
        Iterate through all notes with automatic pagination.

        Args:
            person_id: Filter notes associated with this person
            company_id: Filter notes associated with this company
            opportunity_id: Filter notes associated with this opportunity
            creator_id: Filter notes created by this user
            page_size: Number of results per page

        Returns:
            PageIterator that yields Note objects
        """

        def fetch_page(cursor: str | None) -> PaginatedResponse[Note]:
            return self.list(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                creator_id=creator_id,
                page_size=page_size,
                page_token=cursor,
            )

        return PageIterator(fetch_page)

    # =========================================================================
    # V2 notes (read): NoteV2 with creator, HTML content, interaction, transcript
    # =========================================================================

    def list_v2(
        self,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[NoteV2]:
        """One page of notes across the organization as ``NoteV2`` (replies not included).

        Filters: ``creator_id``, ``created_after`` (inclusive) / ``created_before`` (exclusive),
        ``updated_after``, rounded outward to whole seconds. ``includes=True`` (or a list of
        ``"companiesPreview"``, ``"personsPreview"``, ``"opportunitiesPreview"``,
        ``"repliesCount"``) fills ``companies`` / ``persons`` / ``opportunities`` (the first
        ones, with totals) and ``replies_count``. ``limit`` is 1-100. :meth:`list` (V1) and its
        ``Note`` model are unchanged.
        """
        check_cursor_alone(
            cursor,
            creator_id,
            created_after,
            created_before,
            updated_after,
            includes,
            limit,
            total_count,
        )
        if cursor is not None:
            return page_of(NoteV2, self._client.get_url(cursor))
        params = note_params(
            creator_id=creator_id,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            limit=limit,
            total_count=total_count,
            includes=includes,
        )
        return page_of(NoteV2, self._client.get("/notes", params=params or None))

    def iter_v2(
        self,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
    ) -> Iterator[NoteV2]:
        """All notes matching the filters (every page); see :meth:`list_v2`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[NoteV2]:
            if next_url:
                return self.list_v2(cursor=next_url)
            return self.list_v2(
                creator_id=creator_id,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                includes=includes,
                limit=limit,
            )

        return PageIterator(fetch_page)

    def get_v2(self, note_id: NoteId, *, includes: bool | Sequence[str] | None = None) -> NoteV2:
        """One note as ``NoteV2`` (``includes`` as in :meth:`list_v2`)."""
        params = [("includes", value) for value in note_includes(includes)]
        return NoteV2.model_validate(
            self._client.get(f"/notes/{int(note_id)}", params=params or None)
        )

    def list_replies(
        self,
        note_id: NoteId,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[NoteV2]:
        """One page of replies to a note (``NoteV2`` with ``parent``)."""
        check_cursor_alone(cursor, creator_id, created_after, limit, total_count)
        if cursor is not None:
            return page_of(NoteV2, self._client.get_url(cursor))
        params = note_params(
            creator_id=creator_id,
            created_after=created_after,
            created_before=None,
            updated_after=None,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            NoteV2,
            self._client.get(f"/notes/{int(note_id)}/replies", params=params or None),
        )

    def iter_replies(self, note_id: NoteId, *, limit: int | None = None) -> Iterator[NoteV2]:
        """All replies to a note (every page)."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[NoteV2]:
            if next_url:
                return self.list_replies(note_id, cursor=next_url)
            return self.list_replies(note_id, limit=limit)

        return PageIterator(fetch_page)


# =============================================================================
# Reminder Service (V1 API)
# =============================================================================


class ReminderService:
    """
    Service for managing reminders.

    Reminders are V1-only in this SDK (create/update/delete via V1).
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        owner_id: UserId | None = None,
        completer_id: UserId | None = None,
        type: ReminderType | None = None,
        reset_type: ReminderResetType | None = None,
        status: ReminderStatus | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Reminder]:
        """
        Get reminders with optional filtering.

        Args:
            person_id: Filter reminders for this person
            company_id: Filter reminders for this company
            opportunity_id: Filter reminders for this opportunity
            creator_id: Filter by reminder creator
            owner_id: Filter by reminder owner (assignee)
            completer_id: Filter by who completed the reminder
            type: Filter by reminder type (ONE_TIME or RECURRING)
            reset_type: Filter by reset type (FIXED_DATE, DATE_ADDED, or INTERACTION)
            status: Filter by status (COMPLETED, ACTIVE, or OVERDUE)
            due_before: Filter reminders due before this datetime
            due_after: Filter reminders due after this datetime
            page_size: Number of results per page
            page_token: Pagination token from previous response

        Returns:
            PaginatedResponse with reminders and next_page_token
        """
        params: dict[str, Any] = {}
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if creator_id is not None:
            params["creator_id"] = int(creator_id)
        if owner_id is not None:
            params["owner_id"] = int(owner_id)
        if completer_id is not None:
            params["completer_id"] = int(completer_id)
        if type is not None:
            params["type"] = int(type)
        if reset_type is not None:
            params["reset_type"] = int(reset_type)
        if status is not None:
            params["status"] = int(status)
        if due_before is not None:
            params["due_before"] = due_before.isoformat()
        if due_after is not None:
            params["due_after"] = due_after.isoformat()
        if page_size is not None:
            params["page_size"] = page_size
        if page_token is not None:
            params["page_token"] = page_token

        data = self._client.get("/reminders", params=params or None, v1=True)
        items = data.get("reminders", data.get("data", []))
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[Reminder](
            data=[Reminder.model_validate(r) for r in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    def get(self, reminder_id: ReminderIdType) -> Reminder:
        """Get a single reminder."""
        data = self._client.get(f"/reminders/{reminder_id}", v1=True)
        return Reminder.model_validate(data)

    def create(self, data: ReminderCreate) -> Reminder:
        """Create a new reminder."""
        payload = data.model_dump(by_alias=True, mode="python", exclude_none=True)
        _coerce_isoformat(payload, ("due_date",))

        result = self._client.post("/reminders", json=payload, v1=True)
        return Reminder.model_validate(result)

    def update(self, reminder_id: ReminderIdType, data: ReminderUpdate) -> Reminder:
        """Update a reminder."""
        payload = data.model_dump(
            by_alias=True,
            mode="python",
            exclude_unset=True,
            exclude_none=True,
        )
        _coerce_isoformat(payload, ("due_date",))

        result = self._client.put(f"/reminders/{reminder_id}", json=payload, v1=True)
        return Reminder.model_validate(result)

    def delete(self, reminder_id: ReminderIdType) -> bool:
        """Delete a reminder."""
        result = self._client.delete(f"/reminders/{reminder_id}", v1=True)
        return bool(result.get("success", False))

    def iter(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        owner_id: UserId | None = None,
        completer_id: UserId | None = None,
        type: ReminderType | None = None,
        reset_type: ReminderResetType | None = None,
        status: ReminderStatus | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        page_size: int | None = None,
    ) -> PageIterator[Reminder]:
        """
        Iterate through all reminders with automatic pagination.

        Args:
            person_id: Filter reminders for this person
            company_id: Filter reminders for this company
            opportunity_id: Filter reminders for this opportunity
            creator_id: Filter by reminder creator
            owner_id: Filter by reminder owner (assignee)
            completer_id: Filter by who completed the reminder
            type: Filter by reminder type (ONE_TIME or RECURRING)
            reset_type: Filter by reset type (FIXED_DATE, DATE_ADDED, or INTERACTION)
            status: Filter by status (COMPLETED, ACTIVE, or OVERDUE)
            due_before: Filter reminders due before this datetime
            due_after: Filter reminders due after this datetime
            page_size: Number of results per page

        Returns:
            PageIterator that yields Reminder objects
        """

        def fetch_page(cursor: str | None) -> PaginatedResponse[Reminder]:
            return self.list(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                creator_id=creator_id,
                owner_id=owner_id,
                completer_id=completer_id,
                type=type,
                reset_type=reset_type,
                status=status,
                due_before=due_before,
                due_after=due_after,
                page_size=page_size,
                page_token=cursor,
            )

        return PageIterator(fetch_page)


# =============================================================================
# Webhook Service (V1 API)
# =============================================================================


class WebhookService:
    """
    Service for managing webhook subscriptions.

    Note: Limited to 3 subscriptions per Affinity instance.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(self) -> builtins.list[WebhookSubscription]:
        """Get all webhook subscriptions."""
        data = self._client.get("/webhook", v1=True)
        return [WebhookSubscription.model_validate(w) for w in data.get("data", [])]

    def get(self, webhook_id: WebhookId) -> WebhookSubscription:
        """Get a single webhook subscription."""
        data = self._client.get(f"/webhook/{webhook_id}", v1=True)
        return WebhookSubscription.model_validate(data)

    def create(self, data: WebhookCreate) -> WebhookSubscription:
        """
        Create a webhook subscription.

        The webhook URL will receive a validation request.
        """
        payload = data.model_dump(by_alias=True, mode="python", exclude_none=True)
        _coerce_isoformat(payload, ("date",))
        if not data.subscriptions:
            payload.pop("subscriptions", None)

        result = self._client.post("/webhook/subscribe", json=payload, v1=True)
        return WebhookSubscription.model_validate(result)

    def update(self, webhook_id: WebhookId, data: WebhookUpdate) -> WebhookSubscription:
        """Update a webhook subscription."""
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )

        result = self._client.put(f"/webhook/{webhook_id}", json=payload, v1=True)
        return WebhookSubscription.model_validate(result)

    def delete(self, webhook_id: WebhookId) -> bool:
        """Delete a webhook subscription."""
        result = self._client.delete(f"/webhook/{webhook_id}", v1=True)
        return bool(result.get("success", False))


# =============================================================================
# Interaction Service (V1 API)
# =============================================================================


class InteractionService:
    """
    Service for managing interactions (meetings, calls, emails, chats).

    V2 provides read-only metadata; V1 supports full CRUD.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        type: InteractionType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Interaction]:
        """
        Get interactions with filtering.

        All parameters are validated before calling the API:
        - type, start_time, end_time are required
        - At least one entity ID (person_id, company_id, or opportunity_id)
        - Date range must be <= 365 days
        - start_time must be before end_time

        For ranges exceeding 365 days, use iter() which automatically chunks.

        Returns V1 paginated response with `data` and `next_page_token`.

        Raises:
            ValueError: If required parameters are missing or invalid.
        """
        if type is None:
            raise ValueError(
                "type is required for interactions API. "
                "Use InteractionType.EMAIL, MEETING, CALL, or CHAT_MESSAGE."
            )
        if start_time is None:
            raise ValueError(
                "start_time is required for interactions API. "
                "Use iter() for automatic date range handling."
            )
        if end_time is None:
            raise ValueError(
                "end_time is required for interactions API. "
                "Use iter() for automatic date range handling."
            )
        if (start_time.tzinfo is None) != (end_time.tzinfo is None):
            raise ValueError(
                "start_time and end_time must both be timezone-aware or both naive. "
                "Recommended: use timezone-aware datetimes (e.g., datetime.now(timezone.utc))."
            )
        if end_time <= start_time:
            raise ValueError("start_time must be before end_time.")
        if (end_time - start_time) > timedelta(days=_MAX_INTERACTION_RANGE_DAYS):
            raise ValueError(
                f"Date range exceeds {_MAX_INTERACTION_RANGE_DAYS} days. "
                f"Use iter() which automatically chunks large ranges."
            )
        if not any(x is not None for x in (person_id, company_id, opportunity_id)):
            raise ValueError(
                "At least one entity filter is required: person_id, company_id, or opportunity_id."
            )
        params: dict[str, Any] = {"type": int(type)}
        params["start_time"] = start_time.isoformat()
        params["end_time"] = end_time.isoformat()
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if page_size is not None:
            params["page_size"] = page_size
        if page_token is not None:
            params["page_token"] = page_token

        data = self._client.get("/interactions", params=params or None, v1=True)
        items: Any = None
        if int(type) in (int(InteractionType.MEETING), int(InteractionType.CALL)):
            items = data.get("events")
        elif int(type) == int(InteractionType.CHAT_MESSAGE):
            items = data.get("chat_messages")
        elif int(type) == int(InteractionType.EMAIL):
            items = data.get("emails")

        if items is None:
            items = (
                data.get("interactions")
                or data.get("events")
                or data.get("emails")
                or data.get("chat_messages")
                or data.get("data", [])
            )
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[Interaction](
            data=[Interaction.model_validate(i) for i in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    def get(self, interaction_id: InteractionId, type: InteractionType) -> Interaction:
        """Get a single interaction by ID and type."""
        data = self._client.get(
            f"/interactions/{int(interaction_id)}",
            params={"type": int(type)},
            v1=True,
        )
        return Interaction.model_validate(data)

    def create(self, data: InteractionCreate) -> Interaction:
        """Create a new interaction (manually logged)."""
        payload = data.model_dump(by_alias=True, mode="python", exclude_none=True)
        _coerce_isoformat(payload, ("date",))

        result = self._client.post("/interactions", json=payload, v1=True)
        return Interaction.model_validate(result)

    def update(
        self,
        interaction_id: InteractionId,
        type: InteractionType,
        data: InteractionUpdate,
    ) -> Interaction:
        """Update an interaction."""
        payload = data.model_dump(
            by_alias=True,
            mode="python",
            exclude_unset=True,
            exclude_none=True,
        )
        payload["type"] = int(type)
        _coerce_isoformat(payload, ("date",))

        result = self._client.put(
            f"/interactions/{int(interaction_id)}",
            json=payload,
            v1=True,
        )
        return Interaction.model_validate(result)

    def delete(self, interaction_id: InteractionId, type: InteractionType) -> bool:
        """Delete an interaction."""
        result = self._client.delete(
            f"/interactions/{int(interaction_id)}",
            params={"type": int(type)},
            v1=True,
        )
        return bool(result.get("success", False))

    def iter(
        self,
        *,
        type: InteractionType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        page_size: int | None = None,
    ) -> PageIterator[Interaction]:
        """
        Iterate through all interactions with automatic pagination and date chunking.

        Automatically splits date ranges exceeding 365 days into chunks
        and bridges them with synthetic cursors for seamless iteration.

        Args:
            type: Interaction type (required).
            start_time: Start of date range (required).
            end_time: End of date range (defaults to now if not provided).
            person_id: Filter by person.
            company_id: Filter by company.
            opportunity_id: Filter by opportunity.
            page_size: Page size for API calls.

        Returns:
            PageIterator that yields Interaction objects
        """
        if type is None:
            raise ValueError(
                "type is required for interactions API. "
                "Use InteractionType.EMAIL, MEETING, CALL, or CHAT_MESSAGE."
            )
        if start_time is None:
            raise ValueError("start_time is required for interactions API.")
        if not any(x is not None for x in (person_id, company_id, opportunity_id)):
            raise ValueError(
                "At least one entity filter is required: person_id, company_id, or opportunity_id."
            )
        resolved_end = end_time if end_time is not None else datetime.now(timezone.utc)
        if (start_time.tzinfo is None) != (resolved_end.tzinfo is None):
            raise ValueError(
                "start_time and end_time must both be timezone-aware or both naive. "
                "Recommended: use timezone-aware datetimes (e.g., datetime.now(timezone.utc))."
            )
        if resolved_end <= start_time:
            raise ValueError("start_time must be before end_time.")
        chunks = _chunk_date_range(start_time, resolved_end)
        chunk_index = 0
        chunk_sentinel = f"__chunk_{uuid.uuid4().hex}__"

        def fetch_page(cursor: str | None) -> PaginatedResponse[Interaction]:
            nonlocal chunk_index
            if cursor == chunk_sentinel:
                chunk_index += 1
                cursor = None
            if chunk_index >= len(chunks):
                return PaginatedResponse[Interaction](data=[])
            c_start, c_end = chunks[chunk_index]
            response = self.list(
                type=type,
                start_time=c_start,
                end_time=c_end,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                page_size=page_size,
                page_token=cursor,
            )
            if response.next_cursor is None and chunk_index < len(chunks) - 1:
                response.next_page_token = chunk_sentinel
            return response

        return PageIterator(fetch_page)

    def list_emails(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[EmailV2]:
        """One page of emails across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the sent time (``sentAt``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(EmailV2, self._client.get_url(cursor))
        params = interaction_params(
            "sentAt",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(EmailV2, self._client.get("/emails", params=params or None))

    def iter_emails(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[EmailV2]:
        """All emails matching the filters (every page); see :meth:`list_emails`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[EmailV2]:
            if next_url:
                return self.list_emails(cursor=next_url)
            return self.list_emails(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return PageIterator(fetch_page)

    def list_meetings(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[MeetingV2]:
        """One page of meetings across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the start time (``startTime``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(MeetingV2, self._client.get_url(cursor))
        params = interaction_params(
            "startTime",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(MeetingV2, self._client.get("/meetings", params=params or None))

    def iter_meetings(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[MeetingV2]:
        """All meetings matching the filters (every page); see :meth:`list_meetings`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[MeetingV2]:
            if next_url:
                return self.list_meetings(cursor=next_url)
            return self.list_meetings(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return PageIterator(fetch_page)

    def list_calls(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[CallV2]:
        """One page of calls across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the start time (``startTime``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(CallV2, self._client.get_url(cursor))
        params = interaction_params(
            "startTime",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(CallV2, self._client.get("/calls", params=params or None))

    def iter_calls(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[CallV2]:
        """All calls matching the filters (every page); see :meth:`list_calls`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[CallV2]:
            if next_url:
                return self.list_calls(cursor=next_url)
            return self.list_calls(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return PageIterator(fetch_page)

    def list_chat_messages(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ChatMessageV2]:
        """One page of chat messages across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the sent time (``sentAt``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(ChatMessageV2, self._client.get_url(cursor))
        params = interaction_params(
            "sentAt",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(ChatMessageV2, self._client.get("/chat-messages", params=params or None))

    def iter_chat_messages(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[ChatMessageV2]:
        """All chat messages matching the filters (every page); see :meth:`list_chat_messages`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[ChatMessageV2]:
            if next_url:
                return self.list_chat_messages(cursor=next_url)
            return self.list_chat_messages(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return PageIterator(fetch_page)


# =============================================================================
# Field Service (V1 API)
# =============================================================================


class FieldService:
    """
    Service for managing custom fields.

    Use V2 /fields endpoints for reading field metadata.
    Use V1 for creating/deleting fields.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        list_id: ListId | None = None,
        entity_type: EntityType | None = None,
        skip_cache: bool = False,
    ) -> list[FieldMetadata]:
        """
        Get field metadata.

        Results are cached for 5 minutes when caching is enabled on the client.

        Args:
            list_id: Filter to fields for a specific list
            entity_type: Filter to fields for a specific entity type
            skip_cache: When True, bypass the 5-minute cache (useful when
                resolving a field that may have been added recently).

        Returns:
            List of field metadata
        """
        params: dict[str, Any] = {}
        if list_id is not None:
            params["list_id"] = int(list_id)
        if entity_type is not None:
            params["entity_type"] = int(entity_type)

        get_kwargs: dict[str, Any] = {"v1": True}
        if not skip_cache:
            list_key = "all" if list_id is None else int(list_id)
            type_key = "all" if entity_type is None else int(entity_type)
            get_kwargs["cache_key"] = f"field:v1_list_{list_key}:type_{type_key}"
            get_kwargs["cache_ttl"] = 300

        data = self._client.get(
            "/fields",
            params=params or None,
            **get_kwargs,
        )
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return [FieldMetadata.model_validate(f) for f in items]

    def create(self, data: FieldCreate) -> FieldMetadata:
        """Create a custom field."""
        value_type_code = to_v1_value_type_code(value_type=data.value_type, raw=None)
        if value_type_code is None:
            raise ValueError(f"Field value_type has no V1 numeric mapping: {data.value_type!s}")
        payload = data.model_dump(by_alias=True, mode="json", exclude_unset=True, exclude_none=True)
        payload["entity_type"] = int(data.entity_type)
        payload["value_type"] = value_type_code
        for key in ("allows_multiple", "is_list_specific", "is_required"):
            if not payload.get(key):
                payload.pop(key, None)

        result = self._client.post("/fields", json=payload, v1=True)

        # Invalidate field caches
        if self._client.cache:
            self._client.cache.invalidate_prefix("field")
            self._client.cache.invalidate_prefix("list_")
            self._client.cache.invalidate_prefix("person_fields")
            self._client.cache.invalidate_prefix("company_fields")

        return FieldMetadata.model_validate(result)

    def delete(self, field_id: FieldId) -> bool:
        """
        Delete a custom field (V1 API).

        Note: V1 deletes require numeric field IDs. The SDK accepts V2-style
        `field-<digits>` IDs and converts them; enriched/relationship-intelligence
        IDs are not supported.
        """
        numeric_id = field_id_to_v1_numeric(field_id)
        result = self._client.delete(f"/fields/{numeric_id}", v1=True)

        # Invalidate field caches
        if self._client.cache:
            self._client.cache.invalidate_prefix("field")
            self._client.cache.invalidate_prefix("list_")
            self._client.cache.invalidate_prefix("person_fields")
            self._client.cache.invalidate_prefix("company_fields")

        return bool(result.get("success", False))

    def exists(self, field_id: AnyFieldId) -> bool:
        """
        Check if a field exists.

        Useful for validation before setting field values.

        Note: This fetches all fields and checks locally. If your code calls
        exists() frequently in a loop, consider caching the result of fields.list()
        yourself.

        Args:
            field_id: The field ID to check

        Returns:
            True if the field exists, False otherwise

        Example:
            if client.fields.exists(FieldId("field-123")):
                client.field_values.create(...)
        """
        target_id = FieldId(field_id) if not isinstance(field_id, FieldId) else field_id
        fields = self.list()
        return any(f.id == target_id for f in fields)

    def get_by_name(self, name: str) -> FieldMetadata | None:
        """
        Find a field by its display name.

        Uses case-insensitive matching (casefold for i18n support).

        Note: This fetches all fields and searches locally. If your code calls
        get_by_name() frequently in a loop, consider caching the result of
        fields.list() yourself.

        Args:
            name: The field display name to search for

        Returns:
            FieldMetadata if found, None otherwise

        Example:
            field = client.fields.get_by_name("Primary Email Status")
            if field:
                fv = client.field_values.get_for_entity(field.id, person_id=pid)
        """
        fields = self.list()
        name_folded = name.strip().casefold()  # Strip whitespace, then casefold for i18n
        for field in fields:
            if field.name.casefold() == name_folded:
                return field
        return None


# =============================================================================
# Field Value Service (V1 API)
# =============================================================================


class FieldValueService:
    """
    Service for managing field values.

    For list entry field values, prefer ListEntryService.update_field_value().
    Use this for global field values not tied to list entries.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
    ) -> list[FieldValue]:
        """
        Get field values for an entity.

        Exactly one of person_id, company_id, opportunity_id, or list_entry_id
        must be provided.

        Raises:
            ValueError: If zero or multiple IDs are provided.
        """
        provided = {
            name: value
            for name, value in (
                ("person_id", person_id),
                ("company_id", company_id),
                ("opportunity_id", opportunity_id),
                ("list_entry_id", list_entry_id),
            )
            if value is not None
        }
        if len(provided) == 0:
            raise ValueError(
                "field_values.list() requires exactly one entity ID. "
                "Example: client.field_values.list(person_id=PersonId(123))"
            )
        if len(provided) > 1:
            raise ValueError(
                f"field_values.list() accepts only one entity ID, "
                f"but received {len(provided)}: {', '.join(provided.keys())}. "
                "Call list() separately for each entity."
            )

        params: dict[str, Any] = {}
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if list_entry_id is not None:
            params["list_entry_id"] = int(list_entry_id)

        data = self._client.get("/field-values", params=params or None, v1=True)
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return [FieldValue.model_validate(v) for v in items]

    def create(self, data: FieldValueCreate) -> FieldValue:
        """
        Create a field value (V1 API).

        Note: V1 writes require numeric field IDs. The SDK accepts V2-style
        `field-<digits>` IDs and converts them; enriched/relationship-intelligence
        IDs are not supported.
        """
        payload = data.model_dump(by_alias=True, mode="json", exclude_unset=True, exclude_none=True)
        payload["field_id"] = field_id_to_v1_numeric(data.field_id)

        result = self._client.post("/field-values", json=payload, v1=True)
        return FieldValue.model_validate(result)

    def update(self, field_value_id: FieldValueId, value: Any) -> FieldValue:
        """Update a field value."""
        result = self._client.put(
            f"/field-values/{field_value_id}",
            json={"value": value},
            v1=True,
        )
        return FieldValue.model_validate(result)

    def delete(self, field_value_id: FieldValueId) -> bool:
        """Delete a field value."""
        result = self._client.delete(f"/field-values/{field_value_id}", v1=True)
        return bool(result.get("success", False))

    def get_for_entity(
        self,
        field_id: str | FieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        default: T = _UNSET,
    ) -> FieldValue | T | None:
        """
        Get a specific field value for an entity.

        Convenience method that fetches all field values and returns the one
        matching field_id. Like dict.get(), returns None (or default) if not found.

        Note: This still makes one API call to fetch all field values for the entity.
        For entities with hundreds of field values, prefer using ``list()`` directly
        if you need to inspect multiple fields.

        Args:
            field_id: The field to look up (accepts str or FieldId for convenience)
            person_id: Person entity (exactly one entity ID required)
            company_id: Company entity
            opportunity_id: Opportunity entity
            list_entry_id: List entry entity
            default: Value to return if field not found (default: None)

        Returns:
            FieldValue if the field has a value, default otherwise.
            Note: A FieldValue with ``.value is None`` still counts as "present" (explicit empty).

        Example:
            # Check if a person has a specific field value
            status = client.field_values.get_for_entity(
                "field-123",  # or FieldId("field-123")
                person_id=PersonId(456),
            )
            if status is None:
                print("Field is empty")
            else:
                print(f"Value: {status.value}")

            # With default value
            status = client.field_values.get_for_entity(
                "field-123",
                person_id=PersonId(456),
                default="N/A",
            )
        """
        all_values = self.list(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
            list_entry_id=list_entry_id,
        )
        # Normalize field_id for comparison (handles both str and FieldId)
        target_id = FieldId(field_id) if not isinstance(field_id, FieldId) else field_id
        for fv in all_values:
            if fv.field_id == target_id:
                return fv
        return None if default is _UNSET else default

    def list_batch(
        self,
        person_ids: Sequence[PersonId] | None = None,
        company_ids: Sequence[CompanyId] | None = None,
        opportunity_ids: Sequence[OpportunityId] | None = None,
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[PersonId | CompanyId | OpportunityId, builtins.list[FieldValue]]:
        """
        Get field values for multiple entities.

        **Performance note:** This makes one API call per entity (O(n) calls).
        There is no server-side batch endpoint. Use this for convenience and
        consistent error handling, not for performance optimization.
        For parallelism, use the async client.

        Args:
            person_ids: Sequence of person IDs (mutually exclusive with others)
            company_ids: Sequence of company IDs
            opportunity_ids: Sequence of opportunity IDs
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping entity_id -> list of field values.
            Note: Dict ordering is not guaranteed; do not rely on insertion order.

        Example:
            # Check which persons have a specific field set
            fv_map = client.field_values.list_batch(person_ids=person_ids)
            for person_id, field_values in fv_map.items():
                has_status = any(fv.field_id == target_field for fv in field_values)
        """
        # Validate exactly one sequence provided
        provided = [
            ("person_ids", person_ids),
            ("company_ids", company_ids),
            ("opportunity_ids", opportunity_ids),
        ]
        non_none = [(name, seq) for name, seq in provided if seq is not None]
        if len(non_none) != 1:
            raise ValueError("Exactly one of person_ids, company_ids, or opportunity_ids required")

        name, ids = non_none[0]
        result: dict[PersonId | CompanyId | OpportunityId, list[FieldValue]] = {}

        for entity_id in ids:
            try:
                if name == "person_ids":
                    result[entity_id] = self.list(person_id=cast(PersonId, entity_id))
                elif name == "company_ids":
                    result[entity_id] = self.list(company_id=cast(CompanyId, entity_id))
                else:
                    result[entity_id] = self.list(opportunity_id=cast(OpportunityId, entity_id))
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this entity
            except Exception as e:
                if on_error == "raise":
                    # Preserve status_code if available
                    status_code = getattr(e, "status_code", None)
                    raise AffinityError(
                        f"Failed to get field values for {name[:-1]} {entity_id}: {e}",
                        status_code=status_code,
                    ) from e

        return result


# =============================================================================
# Field Value Changes Service (V1 API)
# =============================================================================


_FVC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FVC_V2_ACTIONS = ("add", "update", "delete")


def _fvc_utc(value: datetime) -> str:
    """UTC timestamp with microseconds and a ``Z`` suffix (a naive datetime is taken as UTC)."""
    return _normalize_to_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fvc_v1_params(
    field_id: AnyFieldId,
    *,
    person_id: PersonId | None,
    company_id: CompanyId | None,
    opportunity_id: OpportunityId | None,
    list_entry_id: ListEntryId | None,
    action_type: FieldValueChangeAction | None,
    changed_after: datetime | None,
    limit: int | None,
    order_by: Literal["asc", "desc"] | None,
    after_id: int | None,
) -> dict[str, Any]:
    """Query params for V1 ``GET /field-value-changes``, checked like the server checks them."""
    provided = [
        name
        for name, value in (
            ("person_id", person_id),
            ("company_id", company_id),
            ("opportunity_id", opportunity_id),
            ("list_entry_id", list_entry_id),
        )
        if value is not None
    ]
    if len(provided) > 1:
        raise ValueError(
            "FieldValueChangesService accepts at most one of: person_id, company_id, "
            f"opportunity_id, or list_entry_id; got {len(provided)}: {', '.join(provided)}"
        )
    if limit is not None and limit < 1:
        raise ValueError("'limit' must be >= 1")
    if order_by is not None and order_by not in ("asc", "desc"):
        raise ValueError("'order_by' must be 'asc' or 'desc'")
    if after_id is not None and (changed_after is None or order_by != "asc"):
        raise ValueError(
            "'after_id' requires changed_after and order_by='asc' (keyset paging: pass the "
            "changed_at and id of the last change of the previous page)"
        )

    params: dict[str, Any] = {"field_id": field_id_to_v1_numeric(field_id)}
    if person_id is not None:
        params["person_id"] = int(person_id)
    if company_id is not None:
        params["organization_id"] = int(company_id)
    if opportunity_id is not None:
        params["opportunity_id"] = int(opportunity_id)
    if list_entry_id is not None:
        params["list_entry_id"] = int(list_entry_id)
    if action_type is not None:
        params["action_type"] = int(action_type)
    if changed_after is not None:
        params["changed_after"] = _fvc_utc(changed_after)
    if limit is not None:
        params["limit"] = limit
    if order_by is not None:
        params["order_by"] = order_by
    if after_id is not None:
        params["after_id"] = int(after_id)
    return params


def _fvc_v1_items(data: Any) -> list[FieldValueChange]:
    items = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    return [FieldValueChange.model_validate(item) for item in items]


def _fvc_keyset_done(page: Sequence[FieldValueChange], cursor: tuple[datetime, int | None]) -> bool:
    """True if the page is empty or doesn't move past the cursor (guards against a loop)."""
    if not page:
        return True
    last = page[-1]
    after_time, after_id = cursor
    return after_id is not None and (last.changed_at, int(last.id)) <= (after_time, after_id)


def _fvc_v2_params(
    *,
    field_id: AnyFieldId | Sequence[AnyFieldId] | None,
    list_entry_id: ListEntryId | int | Sequence[ListEntryId | int] | None,
    changer_id: int | None,
    changed_after: datetime | None,
    changed_before: datetime | None,
    action_type: Literal["add", "update", "delete"] | None,
    order: Literal["asc", "desc"],
    limit: int | None,
) -> dict[str, Any]:
    """Query params for V2 ``GET /v2/field-value-changes``.

    Filter grammar: ``a=1``, ``(a=1 | a=2)``, ``&`` between properties; values unquoted,
    datetimes whole-second UTC ``Z``.
    """
    if limit is not None and not 1 <= limit <= 100:
        raise ValueError("'limit' must be between 1 and 100")
    if order not in ("asc", "desc"):
        raise ValueError("'order' must be 'asc' or 'desc'")
    if action_type is not None and action_type not in _FVC_V2_ACTIONS:
        raise ValueError("'action_type' must be 'add', 'update' or 'delete'")

    clauses: list[str] = []

    def one_of(prop: str, values: builtins.list[str]) -> None:
        if not values:
            return
        parts = [f"{prop}={v}" for v in values]
        clauses.append(parts[0] if len(parts) == 1 else "(" + " | ".join(parts) + ")")

    if field_id is not None:
        ids = [field_id] if isinstance(field_id, (str, int)) else builtins.list(field_id)
        one_of("field.id", [f"field-{f}" if isinstance(f, int) else str(f) for f in ids])
    if list_entry_id is not None:
        entries = (
            [list_entry_id] if isinstance(list_entry_id, int) else builtins.list(list_entry_id)
        )
        one_of("listEntry.id", [str(int(e)) for e in entries])
    if changer_id is not None:
        one_of("changer.id", [str(int(changer_id))])
    if changed_after is not None:
        clauses.append(f"changedAt>={v2_filter_datetime(changed_after, round_up=False)}")
    if changed_before is not None:
        clauses.append(f"changedAt<{v2_filter_datetime(changed_before, round_up=True)}")
    if action_type is not None:
        one_of("actionType", [action_type])

    params: dict[str, Any] = {}
    if clauses:
        params["filter"] = " & ".join(clauses)
    if order == "desc":
        params["orderBy"] = "-changedAt"
    if limit is not None:
        params["limit"] = limit
    return params


def _fvc_v2_page(data: dict[str, Any]) -> PaginatedResponse[FieldValueChangeV2]:
    return PaginatedResponse[FieldValueChangeV2](
        data=[FieldValueChangeV2.model_validate(item) for item in data.get("data", [])],
        pagination=PaginationInfo.model_validate(data.get("pagination", {})),
    )


class FieldValueChangesService:
    """Service for querying field value change history (V1 API, plus the V2 org-wide list)."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        field_id: AnyFieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        action_type: FieldValueChangeAction | None = None,
        changed_after: datetime | None = None,
        limit: int | None = None,
        order_by: Literal["asc", "desc"] | None = None,
        after_id: int | None = None,
    ) -> builtins.list[FieldValueChange]:
        """Get field value changes for a field (V1 API, one response).

        Pass at most one entity selector (``person_id``, ``company_id``, ``opportunity_id``,
        ``list_entry_id``). Without one, changes for every entity are returned; bound such calls
        with ``changed_after`` and/or ``limit``, since a field's whole history can time out.

        Results are sorted by ``(changed_at, id)``, newest first unless ``order_by="asc"``.
        ``changed_after`` is inclusive. ``after_id`` continues after a change (keyset paging) and
        needs ``changed_after`` and ``order_by="asc"``; :meth:`iter_all` does this for you.
        V1 requires numeric field IDs; only ``field-<digits>`` values are convertible.

        """
        params = _fvc_v1_params(
            field_id,
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
            list_entry_id=list_entry_id,
            action_type=action_type,
            changed_after=changed_after,
            limit=limit,
            order_by=order_by,
            after_id=after_id,
        )
        return _fvc_v1_items(self._client.get("/field-value-changes", params=params, v1=True))

    def iter(
        self,
        field_id: AnyFieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        action_type: FieldValueChangeAction | None = None,
        changed_after: datetime | None = None,
        limit: int | None = None,
        order_by: Literal["asc", "desc"] | None = None,
    ) -> Iterator[FieldValueChange]:
        """Iterate the result of one :meth:`list` call. For keyset paging use :meth:`iter_all`."""
        yield from self.list(
            field_id,
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
            list_entry_id=list_entry_id,
            action_type=action_type,
            changed_after=changed_after,
            limit=limit,
            order_by=order_by,
        )

    def iter_all(
        self,
        field_id: AnyFieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        action_type: FieldValueChangeAction | None = None,
        changed_after: datetime | None = None,
        page_size: int = 100,
    ) -> Iterator[FieldValueChange]:
        """Iterate a field's changes oldest first, one page at a time (keyset paging).

        Pages of ``page_size`` are fetched with ``order_by="asc"``, each continuing after the
        last change of the previous page, until a page comes back empty. Optional selector and
        ``changed_after`` (inclusive) narrow it; without a selector every entity's changes for
        the field are returned.

        """
        if page_size < 1:
            raise ValueError("'page_size' must be >= 1")
        cursor: tuple[datetime, int | None] = (changed_after or _FVC_EPOCH, None)
        while True:
            page = self.list(
                field_id,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                list_entry_id=list_entry_id,
                action_type=action_type,
                changed_after=cursor[0],
                limit=page_size,
                order_by="asc",
                after_id=cursor[1],
            )
            if _fvc_keyset_done(page, cursor):
                return
            yield from page
            cursor = (page[-1].changed_at, int(page[-1].id))

    def list_global(
        self,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        list_entry_id: ListEntryId | int | Sequence[ListEntryId | int] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
        order: Literal["asc", "desc"] = "asc",
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[FieldValueChangeV2]:
        """Get one page of field value changes across all entities and fields (V2 API).

        Filters (all optional, combined with AND): ``field_id`` and ``list_entry_id`` take one
        value or several (OR); ``changer_id``; ``changed_after`` (inclusive) and
        ``changed_before`` (exclusive), rounded outward to whole seconds; ``action_type``
        ``"add"``, ``"update"`` or ``"delete"`` (V2 names). There is no person/company/
        opportunity filter; use :meth:`list` for that.
        ``order`` is ``"asc"`` (oldest first, the default) or ``"desc"``. ``limit`` is 1-100.
        ``cursor`` (``next_cursor`` of a previous page) can't be combined with other arguments.

        For delta sync, page to the end, store the latest ``changed_at`` you processed, and
        next time pass it as ``changed_after``; it is inclusive, so skip ids you already have.

        """
        if cursor is not None:
            if (
                any(
                    v is not None
                    for v in (
                        field_id,
                        list_entry_id,
                        changer_id,
                        changed_after,
                        changed_before,
                        action_type,
                        limit,
                    )
                )
                or order != "asc"
            ):
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context."
                )
            return _fvc_v2_page(self._client.get_url(cursor))
        params = _fvc_v2_params(
            field_id=field_id,
            list_entry_id=list_entry_id,
            changer_id=changer_id,
            changed_after=changed_after,
            changed_before=changed_before,
            action_type=action_type,
            order=order,
            limit=limit,
        )
        return _fvc_v2_page(self._client.get("/field-value-changes", params=params or None))

    def iter_global(
        self,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        list_entry_id: ListEntryId | int | Sequence[ListEntryId | int] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
        order: Literal["asc", "desc"] = "asc",
        limit: int | None = None,
    ) -> Iterator[FieldValueChangeV2]:
        """Iterate all pages of :meth:`list_global` (same filters; ``limit`` is the page size)."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[FieldValueChangeV2]:
            if next_url:
                return self.list_global(cursor=next_url)
            return self.list_global(
                field_id=field_id,
                list_entry_id=list_entry_id,
                changer_id=changer_id,
                changed_after=changed_after,
                changed_before=changed_before,
                action_type=action_type,
                order=order,
                limit=limit,
            )

        return PageIterator(fetch_page)


# =============================================================================
# Relationship Strength Service (V1 API)
# =============================================================================


class RelationshipStrengthService:
    """Service for querying relationship strengths."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def get(
        self,
        external_id: PersonId,
        internal_id: UserId | None = None,
    ) -> list[RelationshipStrength]:
        """
        Get relationship strength(s) for an external person.

        Args:
            external_id: External person to query
            internal_id: Optional internal person for specific relationship

        Returns:
            List of relationship strengths (may be empty)
        """
        params: dict[str, Any] = {"external_id": int(external_id)}
        if internal_id:
            params["internal_id"] = int(internal_id)

        data = self._client.get("/relationships-strengths", params=params, v1=True)
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return [RelationshipStrength.model_validate(r) for r in items]


# =============================================================================
# Entity File Service (V1 API)
# =============================================================================


class EntityFileService(FileSearchMixin):
    """Service for managing files attached to entities."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def _validate_exactly_one_target(
        self,
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
    ) -> None:
        targets = [person_id, company_id, opportunity_id]
        count = sum(1 for t in targets if t is not None)
        if count == 1:
            return
        if count == 0:
            raise ValueError("Exactly one of person_id, company_id, or opportunity_id is required")
        raise ValueError("Only one of person_id, company_id, or opportunity_id may be provided")

    def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[EntityFile]:
        """Get files attached to an entity."""
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )
        params: dict[str, Any] = {}
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if page_size is not None:
            params["page_size"] = page_size
        if page_token is not None:
            params["page_token"] = page_token

        data = self._client.get("/entity-files", params=params or None, v1=True)
        items = (
            data.get("entity_files")
            or data.get("entityFiles")
            or data.get("files")
            or data.get("data", [])
        )
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[EntityFile](
            data=[EntityFile.model_validate(f) for f in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    def get(self, file_id: FileId) -> EntityFile:
        """Get file metadata."""
        data = self._client.get(f"/entity-files/{file_id}", v1=True)
        return EntityFile.model_validate(data)

    def download(
        self,
        file_id: FileId,
        *,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> bytes:
        """Download file content."""
        return self._client.download_file(
            f"/entity-files/download/{file_id}",
            v1=True,
            timeout=timeout,
            deadline_seconds=deadline_seconds,
        )

    def get_download_url(
        self,
        file_id: FileId,
        *,
        timeout: httpx.Timeout | float | None = None,
    ) -> PresignedUrl:
        """
        Get a presigned download URL for a file without downloading its content.

        The returned URL is valid for approximately 60 seconds and can be
        fetched without authentication (it's self-authenticating via signature).

        Args:
            file_id: The entity file ID
            timeout: Optional request timeout

        Returns:
            PresignedUrl with the URL, file metadata, and expiration info

        Raises:
            AffinityError: If the API doesn't return a redirect URL
        """
        # Fetch file metadata first
        file_meta = self.get(file_id)

        url = self._client.get_redirect_url(
            f"/entity-files/download/{file_id}",
            v1=True,
            timeout=timeout,
        )
        if not url:
            raise AffinityError(
                f"Failed to get presigned URL for file {file_id}: no redirect returned"
            )

        # Parse X-Amz-Expires from the presigned URL to determine TTL
        # Default to 60 seconds if not found (Affinity's typical TTL)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        expires_in = 60  # default
        if "X-Amz-Expires" in qs:
            with contextlib.suppress(ValueError, IndexError):
                expires_in = int(qs["X-Amz-Expires"][0])

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in)

        return PresignedUrl(
            url=url,
            file_id=int(file_id),
            name=file_meta.name,
            size=file_meta.size,
            content_type=file_meta.content_type,
            expires_in=expires_in,
            expires_at=expires_at,
        )

    def download_stream(
        self,
        file_id: FileId,
        *,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> Iterator[bytes]:
        """Stream-download file content in chunks."""
        return self._client.stream_download(
            f"/entity-files/download/{file_id}",
            v1=True,
            chunk_size=chunk_size,
            on_progress=on_progress,
            timeout=timeout,
            deadline_seconds=deadline_seconds,
        )

    def download_stream_with_info(
        self,
        file_id: FileId,
        *,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> DownloadedFile:
        """
        Stream-download a file and return response metadata (headers/filename/size).

        Notes:
        - `filename` is derived from `Content-Disposition` when present.
        - If the server does not provide a filename, callers can fall back to
          `files.get(file_id).name`.
        """
        return self._client.stream_download_with_info(
            f"/entity-files/download/{file_id}",
            v1=True,
            chunk_size=chunk_size,
            on_progress=on_progress,
            timeout=timeout,
            deadline_seconds=deadline_seconds,
        )

    def download_to(
        self,
        file_id: FileId,
        path: str | Path,
        *,
        overwrite: bool = False,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> Path:
        """
        Download a file to disk.

        Args:
            file_id: The entity file id
            path: Destination path
            overwrite: If False, raises FileExistsError when path exists
            chunk_size: Bytes per chunk

        Returns:
            The destination path
        """
        target = Path(path)
        if target.exists() and not overwrite:
            raise FileExistsError(str(target))

        try:
            with target.open("wb") as f:
                for chunk in self.download_stream(
                    file_id,
                    chunk_size=chunk_size,
                    on_progress=on_progress,
                    timeout=timeout,
                    deadline_seconds=deadline_seconds,
                ):
                    f.write(chunk)
        except Exception:
            # Clean up partial file on error
            if target.exists():
                target.unlink()
            raise

        return target

    def _post_upload(
        self,
        files: dict[str, Any],
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
    ) -> dict[str, Any]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )
        return self._client.upload_file(
            "/entity-files",
            files=files,
            data=_upload_target_data(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            ),
            v1=True,
        )

    def _post_upload_path(
        self,
        path: str | Path,
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
        filename: str | None,
        content_type: str | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        p = Path(path)
        upload_filename = filename or p.name
        guessed, _ = mimetypes.guess_type(upload_filename)
        final_content_type = content_type or guessed or "application/octet-stream"
        total = p.stat().st_size

        if on_progress:
            on_progress(0, total, phase="upload")

        with p.open("rb") as f:
            result = self._post_upload(
                files={"file": (upload_filename, f, final_content_type)},
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            )

        if on_progress:
            on_progress(total, total, phase="upload")

        return result

    def _post_upload_bytes(
        self,
        data: bytes,
        filename: str,
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
        content_type: str | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        guessed, _ = mimetypes.guess_type(filename)
        final_content_type = content_type or guessed or "application/octet-stream"
        total = len(data)

        if on_progress:
            on_progress(0, total, phase="upload")

        result = self._post_upload(
            files={"file": (filename, data, final_content_type)},
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        if on_progress:
            on_progress(total, total, phase="upload")

        return result

    def upload(
        self,
        files: dict[str, Any],
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> bool:
        """
        Upload files to an entity.

        Args:
            files: Multipart files mapping, e.g. `{"file": (filename, content, content_type)}`
            person_id: Person to attach to
            company_id: Company to attach to
            opportunity_id: Opportunity to attach to

        Returns:
            True when the upload succeeded (the response's `success` flag when
            present). Use `upload_returning_files()` to get the created file records.
        """
        return _upload_succeeded(
            self._post_upload(
                files,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            )
        )

    def upload_returning_files(
        self,
        files: dict[str, Any],
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> builtins.list[EntityFile]:
        """
        Upload files to an entity and return the created file records.

        Same request as `upload()`; returns the `entity_files` from the response,
        so the new file ids can be used with `get()`/`download()` straight away.

        Returns:
            The created files. Empty if the API returned no `entity_files` (older
            responses were just `{"success": true}`). Never raises after a successful
            (2xx) upload: an entry that fails validation is returned as a partial
            record (unvalidated; fields that failed are None) when it has an id,
            and skipped otherwise.
        """
        return _parse_uploaded_files(
            self._post_upload(
                files,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            )
        )

    def upload_path(
        self,
        path: str | Path,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> bool:
        """
        Upload a file from disk.

        Notes:
        - Returns True when the upload succeeded. Use `upload_path_returning_files()`
          to get the created file record (with its id).
        - Progress reporting is best-effort for uploads (start/end only).
        """
        return _upload_succeeded(
            self._post_upload_path(
                path,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                filename=filename,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    def upload_path_returning_files(
        self,
        path: str | Path,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> builtins.list[EntityFile]:
        """
        Upload a file from disk and return the created file records.

        Same as `upload_path()`, but returns the response's `entity_files`
        (see `upload_returning_files()` for the parsing rules).
        """
        return _parse_uploaded_files(
            self._post_upload_path(
                path,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                filename=filename,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    def upload_bytes(
        self,
        data: bytes,
        filename: str,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> bool:
        """
        Upload in-memory bytes as a file.

        Notes:
        - Returns True when the upload succeeded. Use `upload_bytes_returning_files()`
          to get the created file record (with its id).
        - Progress reporting is best-effort for uploads (start/end only).
        """
        return _upload_succeeded(
            self._post_upload_bytes(
                data,
                filename,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    def upload_bytes_returning_files(
        self,
        data: bytes,
        filename: str,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> builtins.list[EntityFile]:
        """
        Upload in-memory bytes as a file and return the created file records.

        Same as `upload_bytes()`, but returns the response's `entity_files`
        (see `upload_returning_files()` for the parsing rules).
        """
        return _parse_uploaded_files(
            self._post_upload_bytes(
                data,
                filename,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    def all(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> Iterator[EntityFile]:
        """Iterate through all files for an entity with automatic pagination."""
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        page_token: str | None = None
        while True:
            page = self.list(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                page_token=page_token,
            )
            yield from page.data
            if not page.has_next:
                break
            page_token = page.next_page_token

    def iter(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> Iterator[EntityFile]:
        """Auto-paginate all files (alias for `all()`)."""
        return self.all(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )


# =============================================================================
# Auth Service
# =============================================================================


class AuthService:
    """Service for authentication info."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def whoami(self) -> WhoAmI:
        """Get info about current user and API key."""
        # V2 also has this endpoint
        data = self._client.get("/auth/whoami")
        return WhoAmI.model_validate(data)

    # Note: rate limit handling is exposed via `client.rate_limits` (version-agnostic).


# =============================================================================
# Async V1-only services
# =============================================================================


class AsyncNoteService(AsyncNoteSearchMixin):
    """
    Async service for managing notes (V1 API).

    V2 provides read-only access; use V1 for create/update/delete.
    """

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Note]:
        params: dict[str, Any] = {}
        if person_id:
            params["person_id"] = int(person_id)
        if company_id:
            params["organization_id"] = int(company_id)
        if opportunity_id:
            params["opportunity_id"] = int(opportunity_id)
        if creator_id:
            params["creator_id"] = int(creator_id)
        if page_size:
            params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token

        data = await self._client.get("/notes", params=params or None, v1=True)
        items = data.get("notes", data.get("data", []))
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[Note](
            data=[Note.model_validate(n) for n in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    async def get(self, note_id: NoteId) -> Note:
        data = await self._client.get(f"/notes/{note_id}", v1=True)
        return Note.model_validate(data)

    async def create(self, data: NoteCreate) -> Note:
        payload = data.model_dump(by_alias=True, mode="python", exclude_none=True)
        _coerce_isoformat(payload, ("created_at",))
        if not data.person_ids:
            payload.pop("person_ids", None)
        if not data.company_ids:
            payload.pop("organization_ids", None)
        if not data.opportunity_ids:
            payload.pop("opportunity_ids", None)

        result = await self._client.post("/notes", json=payload, v1=True)
        return Note.model_validate(result)

    async def update(self, note_id: NoteId, data: NoteUpdate) -> Note:
        payload = data.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        result = await self._client.put(
            f"/notes/{note_id}",
            json=payload,
            v1=True,
        )
        return Note.model_validate(result)

    async def delete(self, note_id: NoteId) -> bool:
        result = await self._client.delete(f"/notes/{note_id}", v1=True)
        return bool(result.get("success", False))

    def iter(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        page_size: int | None = None,
    ) -> AsyncPageIterator[Note]:
        """
        Iterate through all notes with automatic pagination.

        Args:
            person_id: Filter notes associated with this person
            company_id: Filter notes associated with this company
            opportunity_id: Filter notes associated with this opportunity
            creator_id: Filter notes created by this user
            page_size: Number of results per page

        Returns:
            AsyncPageIterator that yields Note objects
        """

        async def fetch_page(cursor: str | None) -> PaginatedResponse[Note]:
            return await self.list(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                creator_id=creator_id,
                page_size=page_size,
                page_token=cursor,
            )

        return AsyncPageIterator(fetch_page)

    # =========================================================================
    # V2 notes (read): NoteV2 with creator, HTML content, interaction, transcript
    # =========================================================================

    async def list_v2(
        self,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[NoteV2]:
        """One page of notes across the organization as ``NoteV2`` (replies not included).

        Filters: ``creator_id``, ``created_after`` (inclusive) / ``created_before`` (exclusive),
        ``updated_after``, rounded outward to whole seconds. ``includes=True`` (or a list of
        ``"companiesPreview"``, ``"personsPreview"``, ``"opportunitiesPreview"``,
        ``"repliesCount"``) fills ``companies`` / ``persons`` / ``opportunities`` (the first
        ones, with totals) and ``replies_count``. ``limit`` is 1-100. :meth:`list` (V1) and its
        ``Note`` model are unchanged.
        """
        check_cursor_alone(
            cursor,
            creator_id,
            created_after,
            created_before,
            updated_after,
            includes,
            limit,
            total_count,
        )
        if cursor is not None:
            return page_of(NoteV2, await self._client.get_url(cursor))
        params = note_params(
            creator_id=creator_id,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            limit=limit,
            total_count=total_count,
            includes=includes,
        )
        return page_of(NoteV2, await self._client.get("/notes", params=params or None))

    def iter_v2(
        self,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[NoteV2]:
        """All notes matching the filters (every page); see :meth:`list_v2`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[NoteV2]:
            if next_url:
                return await self.list_v2(cursor=next_url)
            return await self.list_v2(
                creator_id=creator_id,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                includes=includes,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)

    async def get_v2(
        self, note_id: NoteId, *, includes: bool | Sequence[str] | None = None
    ) -> NoteV2:
        """One note as ``NoteV2`` (``includes`` as in :meth:`list_v2`)."""
        params = [("includes", value) for value in note_includes(includes)]
        return NoteV2.model_validate(
            await self._client.get(f"/notes/{int(note_id)}", params=params or None)
        )

    async def list_replies(
        self,
        note_id: NoteId,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[NoteV2]:
        """One page of replies to a note (``NoteV2`` with ``parent``)."""
        check_cursor_alone(cursor, creator_id, created_after, limit, total_count)
        if cursor is not None:
            return page_of(NoteV2, await self._client.get_url(cursor))
        params = note_params(
            creator_id=creator_id,
            created_after=created_after,
            created_before=None,
            updated_after=None,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            NoteV2,
            await self._client.get(f"/notes/{int(note_id)}/replies", params=params or None),
        )

    def iter_replies(self, note_id: NoteId, *, limit: int | None = None) -> AsyncIterator[NoteV2]:
        """All replies to a note (every page)."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[NoteV2]:
            if next_url:
                return await self.list_replies(note_id, cursor=next_url)
            return await self.list_replies(note_id, limit=limit)

        return AsyncPageIterator(fetch_page)


class AsyncReminderService:
    """Async service for managing reminders (V1 API)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        owner_id: UserId | None = None,
        completer_id: UserId | None = None,
        type: ReminderType | None = None,
        reset_type: ReminderResetType | None = None,
        status: ReminderStatus | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Reminder]:
        params: dict[str, Any] = {}
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if creator_id is not None:
            params["creator_id"] = int(creator_id)
        if owner_id is not None:
            params["owner_id"] = int(owner_id)
        if completer_id is not None:
            params["completer_id"] = int(completer_id)
        if type is not None:
            params["type"] = int(type)
        if reset_type is not None:
            params["reset_type"] = int(reset_type)
        if status is not None:
            params["status"] = int(status)
        if due_before is not None:
            params["due_before"] = due_before.isoformat()
        if due_after is not None:
            params["due_after"] = due_after.isoformat()
        if page_size is not None:
            params["page_size"] = page_size
        if page_token is not None:
            params["page_token"] = page_token

        data = await self._client.get("/reminders", params=params or None, v1=True)
        items = data.get("reminders", data.get("data", []))
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[Reminder](
            data=[Reminder.model_validate(r) for r in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    async def get(self, reminder_id: ReminderIdType) -> Reminder:
        data = await self._client.get(f"/reminders/{reminder_id}", v1=True)
        return Reminder.model_validate(data)

    async def create(self, data: ReminderCreate) -> Reminder:
        payload = data.model_dump(by_alias=True, mode="python", exclude_none=True)
        _coerce_isoformat(payload, ("due_date",))

        result = await self._client.post("/reminders", json=payload, v1=True)
        return Reminder.model_validate(result)

    async def update(self, reminder_id: ReminderIdType, data: ReminderUpdate) -> Reminder:
        payload = data.model_dump(
            by_alias=True,
            mode="python",
            exclude_unset=True,
            exclude_none=True,
        )
        _coerce_isoformat(payload, ("due_date",))

        result = await self._client.put(f"/reminders/{reminder_id}", json=payload, v1=True)
        return Reminder.model_validate(result)

    async def delete(self, reminder_id: ReminderIdType) -> bool:
        result = await self._client.delete(f"/reminders/{reminder_id}", v1=True)
        return bool(result.get("success", False))

    def iter(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        creator_id: UserId | None = None,
        owner_id: UserId | None = None,
        completer_id: UserId | None = None,
        type: ReminderType | None = None,
        reset_type: ReminderResetType | None = None,
        status: ReminderStatus | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        page_size: int | None = None,
    ) -> AsyncPageIterator[Reminder]:
        """
        Iterate through all reminders with automatic pagination.

        Args:
            person_id: Filter reminders for this person
            company_id: Filter reminders for this company
            opportunity_id: Filter reminders for this opportunity
            creator_id: Filter by reminder creator
            owner_id: Filter by reminder owner (assignee)
            completer_id: Filter by who completed the reminder
            type: Filter by reminder type (ONE_TIME or RECURRING)
            reset_type: Filter by reset type (FIXED_DATE, DATE_ADDED, or INTERACTION)
            status: Filter by status (COMPLETED, ACTIVE, or OVERDUE)
            due_before: Filter reminders due before this datetime
            due_after: Filter reminders due after this datetime
            page_size: Number of results per page

        Returns:
            AsyncPageIterator that yields Reminder objects
        """

        async def fetch_page(cursor: str | None) -> PaginatedResponse[Reminder]:
            return await self.list(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                creator_id=creator_id,
                owner_id=owner_id,
                completer_id=completer_id,
                type=type,
                reset_type=reset_type,
                status=status,
                due_before=due_before,
                due_after=due_after,
                page_size=page_size,
                page_token=cursor,
            )

        return AsyncPageIterator(fetch_page)

    async def list_batch(
        self,
        *,
        person_ids: Sequence[PersonId] | None = None,
        company_ids: Sequence[CompanyId] | None = None,
        opportunity_ids: Sequence[OpportunityId] | None = None,
        status: ReminderStatus | None = None,
        type: ReminderType | None = None,
        reset_type: ReminderResetType | None = None,
        creator_id: UserId | None = None,
        owner_id: UserId | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        max_concurrent: int = 10,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[int, builtins.list[Reminder]]:
        """
        List reminders for multiple entities with controlled concurrency.

        Fetches all reminders (auto-paginates) for each entity ID concurrently.
        Exactly one of person_ids, company_ids, or opportunity_ids must be provided.

        Args:
            person_ids: Person IDs to fetch reminders for
            company_ids: Company IDs to fetch reminders for
            opportunity_ids: Opportunity IDs to fetch reminders for
            status: Filter by reminder status
            type: Filter by reminder type
            reset_type: Filter by reset type
            creator_id: Filter by reminder creator
            owner_id: Filter by reminder owner
            due_before: Filter reminders due before this datetime
            due_after: Filter reminders due after this datetime
            max_concurrent: Maximum concurrent entity queries (default: 10)
            on_error: How to handle errors:
                - "raise": Raise on first error (default)
                - "skip": Skip failed entities, return partial results

        Returns:
            Dict mapping entity ID (int) -> list of Reminder objects.
            Entities with no matching reminders map to an empty list.

        Raises:
            ValueError: If not exactly one of person_ids/company_ids/opportunity_ids is provided.
            AffinityError: If on_error="raise" and any fetch fails.

        Example:
            >>> results = await client.reminders.list_batch(
            ...     company_ids=[CompanyId(1), CompanyId(2)],
            ...     status=ReminderStatus.ACTIVE,
            ...     max_concurrent=5,
            ... )
            >>> for company_id, reminders in results.items():
            ...     print(f"Company {company_id}: {len(reminders)} active reminders")
        """
        # Validate exactly one entity type
        provided = [
            ("person_ids", person_ids),
            ("company_ids", company_ids),
            ("opportunity_ids", opportunity_ids),
        ]
        non_none = [(name, ids) for name, ids in provided if ids is not None]
        if len(non_none) != 1:
            raise ValueError(
                "Exactly one of person_ids, company_ids, or opportunity_ids must be provided"
            )

        param_name, entity_ids = non_none[0]
        if not entity_ids:
            return {}
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        unique_ids = builtins.list(dict.fromkeys(entity_ids))
        results: dict[int, builtins.list[Reminder]] = {}

        # Build the entity-specific kwarg for iter()
        id_kwarg_map = {
            "person_ids": "person_id",
            "company_ids": "company_id",
            "opportunity_ids": "opportunity_id",
        }
        iter_kwarg_name = id_kwarg_map[param_name]

        async def fetch_all_for_entity(eid: int) -> tuple[int, builtins.list[Reminder]]:
            iter_kwargs: dict[str, Any] = {iter_kwarg_name: eid}
            for key, val in [
                ("status", status),
                ("type", type),
                ("reset_type", reset_type),
                ("creator_id", creator_id),
                ("owner_id", owner_id),
                ("due_before", due_before),
                ("due_after", due_after),
            ]:
                if val is not None:
                    iter_kwargs[key] = val
            reminders: builtins.list[Reminder] = []
            try:
                async for reminder in self.iter(**iter_kwargs):
                    reminders.append(reminder)
            except AffinityError:
                if on_error == "raise":
                    raise
                # on_error="skip": return whatever was collected before the error.
            return (int(eid), reminders)

        for i in range(0, len(unique_ids), max_concurrent):
            chunk = unique_ids[i : i + max_concurrent]
            tasks = [asyncio.create_task(fetch_all_for_entity(eid)) for eid in chunk]
            try:
                for coro in asyncio.as_completed(tasks):
                    eid_int, reminders = await coro
                    results[eid_int] = reminders
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        return results


class AsyncWebhookService:
    """Async service for managing webhook subscriptions (V1 API)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(self) -> builtins.list[WebhookSubscription]:
        data = await self._client.get("/webhook", v1=True)
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return [WebhookSubscription.model_validate(w) for w in items]

    async def get(self, webhook_id: WebhookId) -> WebhookSubscription:
        data = await self._client.get(f"/webhook/{webhook_id}", v1=True)
        return WebhookSubscription.model_validate(data)

    async def create(self, data: WebhookCreate) -> WebhookSubscription:
        payload = data.model_dump(by_alias=True, mode="json", exclude_none=True)
        if not data.subscriptions:
            payload.pop("subscriptions", None)
        result = await self._client.post("/webhook/subscribe", json=payload, v1=True)
        return WebhookSubscription.model_validate(result)

    async def update(self, webhook_id: WebhookId, data: WebhookUpdate) -> WebhookSubscription:
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )
        result = await self._client.put(f"/webhook/{webhook_id}", json=payload, v1=True)
        return WebhookSubscription.model_validate(result)

    async def delete(self, webhook_id: WebhookId) -> bool:
        result = await self._client.delete(f"/webhook/{webhook_id}", v1=True)
        return bool(result.get("success", False))


class AsyncInteractionService:
    """Async service for managing interactions (V1 API)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        type: InteractionType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Interaction]:
        if type is None:
            raise ValueError(
                "type is required for interactions API. "
                "Use InteractionType.EMAIL, MEETING, CALL, or CHAT_MESSAGE."
            )
        if start_time is None:
            raise ValueError(
                "start_time is required for interactions API. "
                "Use iter() for automatic date range handling."
            )
        if end_time is None:
            raise ValueError(
                "end_time is required for interactions API. "
                "Use iter() for automatic date range handling."
            )
        if (start_time.tzinfo is None) != (end_time.tzinfo is None):
            raise ValueError(
                "start_time and end_time must both be timezone-aware or both naive. "
                "Recommended: use timezone-aware datetimes (e.g., datetime.now(timezone.utc))."
            )
        if end_time <= start_time:
            raise ValueError("start_time must be before end_time.")
        if (end_time - start_time) > timedelta(days=_MAX_INTERACTION_RANGE_DAYS):
            raise ValueError(
                f"Date range exceeds {_MAX_INTERACTION_RANGE_DAYS} days. "
                f"Use iter() which automatically chunks large ranges."
            )
        if not any(x is not None for x in (person_id, company_id, opportunity_id)):
            raise ValueError(
                "At least one entity filter is required: person_id, company_id, or opportunity_id."
            )
        params: dict[str, Any] = {"type": int(type)}
        params["start_time"] = start_time.isoformat()
        params["end_time"] = end_time.isoformat()
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if page_size is not None:
            params["page_size"] = page_size
        if page_token is not None:
            params["page_token"] = page_token

        data = await self._client.get("/interactions", params=params or None, v1=True)
        items: Any = None
        if int(type) in (int(InteractionType.MEETING), int(InteractionType.CALL)):
            items = data.get("events")
        elif int(type) == int(InteractionType.CHAT_MESSAGE):
            items = data.get("chat_messages")
        elif int(type) == int(InteractionType.EMAIL):
            items = data.get("emails")

        if items is None:
            items = (
                data.get("interactions")
                or data.get("events")
                or data.get("emails")
                or data.get("chat_messages")
                or data.get("data", [])
            )
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[Interaction](
            data=[Interaction.model_validate(i) for i in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    async def get(self, interaction_id: InteractionId, type: InteractionType) -> Interaction:
        data = await self._client.get(
            f"/interactions/{interaction_id}",
            params={"type": int(type)},
            v1=True,
        )
        return Interaction.model_validate(data)

    async def create(self, data: InteractionCreate) -> Interaction:
        payload = data.model_dump(by_alias=True, mode="python", exclude_none=True)
        _coerce_isoformat(payload, ("date",))

        result = await self._client.post("/interactions", json=payload, v1=True)
        return Interaction.model_validate(result)

    async def update(
        self,
        interaction_id: InteractionId,
        type: InteractionType,
        data: InteractionUpdate,
    ) -> Interaction:
        payload = data.model_dump(
            by_alias=True,
            mode="python",
            exclude_unset=True,
            exclude_none=True,
        )
        payload["type"] = int(type)
        _coerce_isoformat(payload, ("date",))

        result = await self._client.put(f"/interactions/{interaction_id}", json=payload, v1=True)
        return Interaction.model_validate(result)

    async def delete(self, interaction_id: InteractionId, type: InteractionType) -> bool:
        result = await self._client.delete(
            f"/interactions/{interaction_id}",
            params={"type": int(type)},
            v1=True,
        )
        return bool(result.get("success", False))

    def iter(
        self,
        *,
        type: InteractionType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        page_size: int | None = None,
    ) -> AsyncPageIterator[Interaction]:
        """
        Iterate through all interactions with automatic pagination and date chunking.

        Automatically splits date ranges exceeding 365 days into chunks
        and bridges them with synthetic cursors for seamless iteration.

        Args:
            type: Interaction type (required).
            start_time: Start of date range (required).
            end_time: End of date range (defaults to now if not provided).
            person_id: Filter by person.
            company_id: Filter by company.
            opportunity_id: Filter by opportunity.
            page_size: Page size for API calls.

        Returns:
            AsyncPageIterator that yields Interaction objects
        """
        if type is None:
            raise ValueError(
                "type is required for interactions API. "
                "Use InteractionType.EMAIL, MEETING, CALL, or CHAT_MESSAGE."
            )
        if start_time is None:
            raise ValueError("start_time is required for interactions API.")
        if not any(x is not None for x in (person_id, company_id, opportunity_id)):
            raise ValueError(
                "At least one entity filter is required: person_id, company_id, or opportunity_id."
            )
        resolved_end = end_time if end_time is not None else datetime.now(timezone.utc)
        if (start_time.tzinfo is None) != (resolved_end.tzinfo is None):
            raise ValueError(
                "start_time and end_time must both be timezone-aware or both naive. "
                "Recommended: use timezone-aware datetimes (e.g., datetime.now(timezone.utc))."
            )
        if resolved_end <= start_time:
            raise ValueError("start_time must be before end_time.")
        chunks = _chunk_date_range(start_time, resolved_end)
        chunk_index = 0
        chunk_sentinel = f"__chunk_{uuid.uuid4().hex}__"

        async def fetch_page(cursor: str | None) -> PaginatedResponse[Interaction]:
            nonlocal chunk_index
            if cursor == chunk_sentinel:
                chunk_index += 1
                cursor = None
            if chunk_index >= len(chunks):
                return PaginatedResponse[Interaction](data=[])
            c_start, c_end = chunks[chunk_index]
            response = await self.list(
                type=type,
                start_time=c_start,
                end_time=c_end,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                page_size=page_size,
                page_token=cursor,
            )
            if response.next_cursor is None and chunk_index < len(chunks) - 1:
                response.next_page_token = chunk_sentinel
            return response

        return AsyncPageIterator(fetch_page)

    async def list_emails(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[EmailV2]:
        """One page of emails across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the sent time (``sentAt``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(EmailV2, await self._client.get_url(cursor))
        params = interaction_params(
            "sentAt",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(EmailV2, await self._client.get("/emails", params=params or None))

    def iter_emails(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[EmailV2]:
        """All emails matching the filters (every page); see :meth:`list_emails`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[EmailV2]:
            if next_url:
                return await self.list_emails(cursor=next_url)
            return await self.list_emails(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)

    async def list_meetings(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[MeetingV2]:
        """One page of meetings across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the start time (``startTime``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(MeetingV2, await self._client.get_url(cursor))
        params = interaction_params(
            "startTime",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(MeetingV2, await self._client.get("/meetings", params=params or None))

    def iter_meetings(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[MeetingV2]:
        """All meetings matching the filters (every page); see :meth:`list_meetings`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[MeetingV2]:
            if next_url:
                return await self.list_meetings(cursor=next_url)
            return await self.list_meetings(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)

    async def list_calls(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[CallV2]:
        """One page of calls across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the start time (``startTime``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(CallV2, await self._client.get_url(cursor))
        params = interaction_params(
            "startTime",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(CallV2, await self._client.get("/calls", params=params or None))

    def iter_calls(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CallV2]:
        """All calls matching the filters (every page); see :meth:`list_calls`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[CallV2]:
            if next_url:
                return await self.list_calls(cursor=next_url)
            return await self.list_calls(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)

    async def list_chat_messages(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ChatMessageV2]:
        """One page of chat messages across the organization (V2), the ones the key's user may see.

        ``after`` (inclusive) / ``before`` (exclusive) filter the sent time (``sentAt``);
        ``created_after`` / ``updated_after`` filter ``createdAt`` / ``updatedAt`` (inclusive),
        all rounded outward to whole seconds. There is no sort option, and ``updatedAt`` is
        empty for items never changed: for delta sync, page to the end and track the latest
        times yourself. ``limit`` is 1-100; ``cursor`` (a previous ``next_cursor``) can't be
        combined with other arguments.
        """
        check_cursor_alone(cursor, after, before, created_after, updated_after, limit)
        if cursor is not None:
            return page_of(ChatMessageV2, await self._client.get_url(cursor))
        params = interaction_params(
            "sentAt",
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
            limit=limit,
        )
        return page_of(
            ChatMessageV2, await self._client.get("/chat-messages", params=params or None)
        )

    def iter_chat_messages(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[ChatMessageV2]:
        """All chat messages matching the filters (every page); see :meth:`list_chat_messages`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[ChatMessageV2]:
            if next_url:
                return await self.list_chat_messages(cursor=next_url)
            return await self.list_chat_messages(
                after=after,
                before=before,
                created_after=created_after,
                updated_after=updated_after,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)


class AsyncFieldService:
    """Async service for managing custom fields (V1 API)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        list_id: ListId | None = None,
        entity_type: EntityType | None = None,
        skip_cache: bool = False,
    ) -> builtins.list[FieldMetadata]:
        """
        Get field metadata.

        Results are cached for 5 minutes when caching is enabled on the client.

        Args:
            list_id: Filter to fields for a specific list
            entity_type: Filter to fields for a specific entity type
            skip_cache: When True, bypass the 5-minute cache.

        Returns:
            List of field metadata
        """
        params: dict[str, Any] = {}
        if list_id is not None:
            params["list_id"] = int(list_id)
        if entity_type is not None:
            params["entity_type"] = int(entity_type)

        get_kwargs: dict[str, Any] = {"v1": True}
        if not skip_cache:
            list_key = "all" if list_id is None else int(list_id)
            type_key = "all" if entity_type is None else int(entity_type)
            get_kwargs["cache_key"] = f"field:v1_list_{list_key}:type_{type_key}"
            get_kwargs["cache_ttl"] = 300

        data = await self._client.get(
            "/fields",
            params=params or None,
            **get_kwargs,
        )
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return [FieldMetadata.model_validate(f) for f in items]

    async def create(self, data: FieldCreate) -> FieldMetadata:
        value_type_code = to_v1_value_type_code(value_type=data.value_type, raw=None)
        if value_type_code is None:
            raise ValueError(f"Field value_type has no V1 numeric mapping: {data.value_type!s}")
        payload = data.model_dump(by_alias=True, mode="json", exclude_unset=True, exclude_none=True)
        payload["entity_type"] = int(data.entity_type)
        payload["value_type"] = value_type_code
        for key in ("allows_multiple", "is_list_specific", "is_required"):
            if not payload.get(key):
                payload.pop(key, None)

        result = await self._client.post("/fields", json=payload, v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("field")
            self._client.cache.invalidate_prefix("list_")
            self._client.cache.invalidate_prefix("person_fields")
            self._client.cache.invalidate_prefix("company_fields")

        return FieldMetadata.model_validate(result)

    async def delete(self, field_id: FieldId) -> bool:
        """
        Delete a custom field (V1 API).

        Note: V1 deletes require numeric field IDs. The SDK accepts V2-style
        `field-<digits>` IDs and converts them; enriched/relationship-intelligence
        IDs are not supported.
        """
        numeric_id = field_id_to_v1_numeric(field_id)
        result = await self._client.delete(f"/fields/{numeric_id}", v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("field")
            self._client.cache.invalidate_prefix("list_")
            self._client.cache.invalidate_prefix("person_fields")
            self._client.cache.invalidate_prefix("company_fields")

        return bool(result.get("success", False))

    async def exists(self, field_id: AnyFieldId) -> bool:
        """
        Check if a field exists.

        Useful for validation before setting field values.

        Note: This fetches all fields and checks locally. If your code calls
        exists() frequently in a loop, consider caching the result of fields.list()
        yourself.

        Args:
            field_id: The field ID to check

        Returns:
            True if the field exists, False otherwise

        Example:
            if await client.fields.exists(FieldId("field-123")):
                await client.field_values.create(...)
        """
        target_id = FieldId(field_id) if not isinstance(field_id, FieldId) else field_id
        fields = await self.list()
        return any(f.id == target_id for f in fields)

    async def get_by_name(self, name: str) -> FieldMetadata | None:
        """
        Find a field by its display name.

        Uses case-insensitive matching (casefold for i18n support).

        Note: This fetches all fields and searches locally. If your code calls
        get_by_name() frequently in a loop, consider caching the result of
        fields.list() yourself.

        Args:
            name: The field display name to search for

        Returns:
            FieldMetadata if found, None otherwise

        Example:
            field = await client.fields.get_by_name("Primary Email Status")
            if field:
                fv = await client.field_values.get_for_entity(field.id, person_id=pid)
        """
        fields = await self.list()
        name_folded = name.strip().casefold()  # Strip whitespace, then casefold for i18n
        for field in fields:
            if field.name.casefold() == name_folded:
                return field
        return None


class AsyncFieldValueService:
    """Async service for managing field values (V1 API)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
    ) -> builtins.list[FieldValue]:
        provided = {
            name: value
            for name, value in (
                ("person_id", person_id),
                ("company_id", company_id),
                ("opportunity_id", opportunity_id),
                ("list_entry_id", list_entry_id),
            )
            if value is not None
        }
        if len(provided) == 0:
            raise ValueError(
                "field_values.list() requires exactly one entity ID. "
                "Example: client.field_values.list(person_id=PersonId(123))"
            )
        if len(provided) > 1:
            raise ValueError(
                f"field_values.list() accepts only one entity ID, "
                f"but received {len(provided)}: {', '.join(provided.keys())}. "
                "Call list() separately for each entity."
            )

        params: dict[str, Any] = {}
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if list_entry_id is not None:
            params["list_entry_id"] = int(list_entry_id)

        data = await self._client.get("/field-values", params=params or None, v1=True)
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return [FieldValue.model_validate(v) for v in items]

    async def create(self, data: FieldValueCreate) -> FieldValue:
        """
        Create a field value (V1 API).

        Note: V1 writes require numeric field IDs. The SDK accepts V2-style
        `field-<digits>` IDs and converts them; enriched/relationship-intelligence
        IDs are not supported.
        """
        payload = data.model_dump(by_alias=True, mode="json", exclude_unset=True, exclude_none=True)
        payload["field_id"] = field_id_to_v1_numeric(data.field_id)

        result = await self._client.post("/field-values", json=payload, v1=True)
        return FieldValue.model_validate(result)

    async def update(self, field_value_id: FieldValueId, value: Any) -> FieldValue:
        result = await self._client.put(
            f"/field-values/{field_value_id}",
            json={"value": value},
            v1=True,
        )
        return FieldValue.model_validate(result)

    async def delete(self, field_value_id: FieldValueId) -> bool:
        result = await self._client.delete(f"/field-values/{field_value_id}", v1=True)
        return bool(result.get("success", False))

    async def get_for_entity(
        self,
        field_id: str | FieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        default: T = _UNSET,
    ) -> FieldValue | T | None:
        """
        Get a specific field value for an entity.

        Convenience method that fetches all field values and returns the one
        matching field_id. Like dict.get(), returns None (or default) if not found.

        Note: This still makes one API call to fetch all field values for the entity.
        For entities with hundreds of field values, prefer using ``list()`` directly
        if you need to inspect multiple fields.

        Args:
            field_id: The field to look up (accepts str or FieldId for convenience)
            person_id: Person entity (exactly one entity ID required)
            company_id: Company entity
            opportunity_id: Opportunity entity
            list_entry_id: List entry entity
            default: Value to return if field not found (default: None)

        Returns:
            FieldValue if the field has a value, default otherwise.
            Note: A FieldValue with ``.value is None`` still counts as "present" (explicit empty).

        Example:
            # Check if a person has a specific field value
            status = await client.field_values.get_for_entity(
                "field-123",  # or FieldId("field-123")
                person_id=PersonId(456),
            )
            if status is None:
                print("Field is empty")
            else:
                print(f"Value: {status.value}")

            # With default value
            status = await client.field_values.get_for_entity(
                "field-123",
                person_id=PersonId(456),
                default="N/A",
            )
        """
        all_values = await self.list(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
            list_entry_id=list_entry_id,
        )
        # Normalize field_id for comparison (handles both str and FieldId)
        target_id = FieldId(field_id) if not isinstance(field_id, FieldId) else field_id
        for fv in all_values:
            if fv.field_id == target_id:
                return fv
        return None if default is _UNSET else default

    async def list_batch(
        self,
        person_ids: Sequence[PersonId] | None = None,
        company_ids: Sequence[CompanyId] | None = None,
        opportunity_ids: Sequence[OpportunityId] | None = None,
        *,
        on_error: Literal["raise", "skip"] = "raise",
        concurrency: int | None = 10,
    ) -> dict[PersonId | CompanyId | OpportunityId, builtins.list[FieldValue]]:
        """
        Get field values for multiple entities concurrently.

        Uses asyncio.gather() for concurrent API calls, bounded by semaphore.
        Significant speedup compared to sequential sync version.

        Args:
            person_ids: Sequence of person IDs (mutually exclusive with others)
            company_ids: Sequence of company IDs
            opportunity_ids: Sequence of opportunity IDs
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs
            concurrency: Maximum concurrent requests. Default 10. Set to None for unlimited.

        Returns:
            Dict mapping entity_id -> list of field values.
            Note: Dict ordering is not guaranteed; do not rely on insertion order.

        Example:
            # Check which persons have a specific field set
            fv_map = await client.field_values.list_batch(person_ids=person_ids)
            for person_id, field_values in fv_map.items():
                has_status = any(fv.field_id == target_field for fv in field_values)
        """
        # Validate exactly one sequence provided
        provided = [
            ("person_ids", person_ids),
            ("company_ids", company_ids),
            ("opportunity_ids", opportunity_ids),
        ]
        non_none = [(name, seq) for name, seq in provided if seq is not None]
        if len(non_none) != 1:
            raise ValueError("Exactly one of person_ids, company_ids, or opportunity_ids required")

        name, ids = non_none[0]
        semaphore = asyncio.Semaphore(concurrency) if concurrency else None

        async def fetch_one(
            entity_id: PersonId | CompanyId | OpportunityId,
        ) -> tuple[PersonId | CompanyId | OpportunityId, builtins.list[FieldValue] | None]:
            async def do_fetch() -> builtins.list[FieldValue]:
                if name == "person_ids":
                    return await self.list(person_id=cast(PersonId, entity_id))
                elif name == "company_ids":
                    return await self.list(company_id=cast(CompanyId, entity_id))
                else:
                    return await self.list(opportunity_id=cast(OpportunityId, entity_id))

            try:
                if semaphore:
                    async with semaphore:
                        values = await do_fetch()
                else:
                    values = await do_fetch()
                return (entity_id, values)
            except AffinityError:
                if on_error == "raise":
                    raise
                return (entity_id, None)
            except Exception as e:
                if on_error == "raise":
                    status_code = getattr(e, "status_code", None)
                    raise AffinityError(
                        f"Failed to get field values for {name[:-1]} {entity_id}: {e}",
                        status_code=status_code,
                    ) from e
                return (entity_id, None)

        results = await asyncio.gather(*[fetch_one(eid) for eid in ids])
        return {eid: values for eid, values in results if values is not None}


class AsyncFieldValueChangesService:
    """Async service for querying field value change history (V1 API, plus the V2 org-wide list)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        field_id: AnyFieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        action_type: FieldValueChangeAction | None = None,
        changed_after: datetime | None = None,
        limit: int | None = None,
        order_by: Literal["asc", "desc"] | None = None,
        after_id: int | None = None,
    ) -> builtins.list[FieldValueChange]:
        """Get field value changes for a field (V1 API, one response).

        Pass at most one entity selector (``person_id``, ``company_id``, ``opportunity_id``,
        ``list_entry_id``). Without one, changes for every entity are returned; bound such calls
        with ``changed_after`` and/or ``limit``, since a field's whole history can time out.

        Results are sorted by ``(changed_at, id)``, newest first unless ``order_by="asc"``.
        ``changed_after`` is inclusive. ``after_id`` continues after a change (keyset paging) and
        needs ``changed_after`` and ``order_by="asc"``; :meth:`iter_all` does this for you.
        V1 requires numeric field IDs; only ``field-<digits>`` values are convertible.

        """
        params = _fvc_v1_params(
            field_id,
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
            list_entry_id=list_entry_id,
            action_type=action_type,
            changed_after=changed_after,
            limit=limit,
            order_by=order_by,
            after_id=after_id,
        )
        return _fvc_v1_items(await self._client.get("/field-value-changes", params=params, v1=True))

    async def iter(
        self,
        field_id: AnyFieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        action_type: FieldValueChangeAction | None = None,
        changed_after: datetime | None = None,
        limit: int | None = None,
        order_by: Literal["asc", "desc"] | None = None,
    ) -> AsyncIterator[FieldValueChange]:
        """Iterate the result of one :meth:`list` call. For keyset paging use :meth:`iter_all`."""
        for item in await self.list(
            field_id,
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
            list_entry_id=list_entry_id,
            action_type=action_type,
            changed_after=changed_after,
            limit=limit,
            order_by=order_by,
        ):
            yield item

    async def iter_all(
        self,
        field_id: AnyFieldId,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        list_entry_id: ListEntryId | None = None,
        action_type: FieldValueChangeAction | None = None,
        changed_after: datetime | None = None,
        page_size: int = 100,
    ) -> AsyncIterator[FieldValueChange]:
        """Iterate a field's changes oldest first, one page at a time (keyset paging).

        Pages of ``page_size`` are fetched with ``order_by="asc"``, each continuing after the
        last change of the previous page, until a page comes back empty. Optional selector and
        ``changed_after`` (inclusive) narrow it; without a selector every entity's changes for
        the field are returned.

        """
        if page_size < 1:
            raise ValueError("'page_size' must be >= 1")
        cursor: tuple[datetime, int | None] = (changed_after or _FVC_EPOCH, None)
        while True:
            page = await self.list(
                field_id,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                list_entry_id=list_entry_id,
                action_type=action_type,
                changed_after=cursor[0],
                limit=page_size,
                order_by="asc",
                after_id=cursor[1],
            )
            if _fvc_keyset_done(page, cursor):
                return
            for item in page:
                yield item
            cursor = (page[-1].changed_at, int(page[-1].id))

    async def list_global(
        self,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        list_entry_id: ListEntryId | int | Sequence[ListEntryId | int] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
        order: Literal["asc", "desc"] = "asc",
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[FieldValueChangeV2]:
        """Get one page of field value changes across all entities and fields (V2 API).

        Filters (all optional, combined with AND): ``field_id`` and ``list_entry_id`` take one
        value or several (OR); ``changer_id``; ``changed_after`` (inclusive) and
        ``changed_before`` (exclusive), rounded outward to whole seconds; ``action_type``
        ``"add"``, ``"update"`` or ``"delete"`` (V2 names). There is no person/company/
        opportunity filter; use :meth:`list` for that.
        ``order`` is ``"asc"`` (oldest first, the default) or ``"desc"``. ``limit`` is 1-100.
        ``cursor`` (``next_cursor`` of a previous page) can't be combined with other arguments.

        For delta sync, page to the end, store the latest ``changed_at`` you processed, and
        next time pass it as ``changed_after``; it is inclusive, so skip ids you already have.

        """
        if cursor is not None:
            if (
                any(
                    v is not None
                    for v in (
                        field_id,
                        list_entry_id,
                        changer_id,
                        changed_after,
                        changed_before,
                        action_type,
                        limit,
                    )
                )
                or order != "asc"
            ):
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context."
                )
            return _fvc_v2_page(await self._client.get_url(cursor))
        params = _fvc_v2_params(
            field_id=field_id,
            list_entry_id=list_entry_id,
            changer_id=changer_id,
            changed_after=changed_after,
            changed_before=changed_before,
            action_type=action_type,
            order=order,
            limit=limit,
        )
        return _fvc_v2_page(await self._client.get("/field-value-changes", params=params or None))

    def iter_global(
        self,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        list_entry_id: ListEntryId | int | Sequence[ListEntryId | int] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
        order: Literal["asc", "desc"] = "asc",
        limit: int | None = None,
    ) -> AsyncIterator[FieldValueChangeV2]:
        """Iterate all pages of :meth:`list_global` (same filters; ``limit`` is the page size)."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[FieldValueChangeV2]:
            if next_url:
                return await self.list_global(cursor=next_url)
            return await self.list_global(
                field_id=field_id,
                list_entry_id=list_entry_id,
                changer_id=changer_id,
                changed_after=changed_after,
                changed_before=changed_before,
                action_type=action_type,
                order=order,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)


class AsyncRelationshipStrengthService:
    """Async service for querying relationship strengths (V1 API)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def get(
        self,
        external_id: PersonId,
        internal_id: UserId | None = None,
    ) -> builtins.list[RelationshipStrength]:
        params: dict[str, Any] = {"external_id": int(external_id)}
        if internal_id:
            params["internal_id"] = int(internal_id)

        data = await self._client.get("/relationships-strengths", params=params, v1=True)
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return [RelationshipStrength.model_validate(r) for r in items]


class AsyncEntityFileService(AsyncFileSearchMixin):
    """Async service for managing files attached to entities (V1 API)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    def _validate_exactly_one_target(
        self,
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
    ) -> None:
        targets = [person_id, company_id, opportunity_id]
        count = sum(1 for t in targets if t is not None)
        if count == 1:
            return
        if count == 0:
            raise ValueError("Exactly one of person_id, company_id, or opportunity_id is required")
        raise ValueError("Only one of person_id, company_id, or opportunity_id may be provided")

    async def list(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[EntityFile]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )
        params: dict[str, Any] = {}
        if person_id is not None:
            params["person_id"] = int(person_id)
        if company_id is not None:
            params["organization_id"] = int(company_id)
        if opportunity_id is not None:
            params["opportunity_id"] = int(opportunity_id)
        if page_size is not None:
            params["page_size"] = page_size
        if page_token is not None:
            params["page_token"] = page_token

        data = await self._client.get("/entity-files", params=params or None, v1=True)
        items = (
            data.get("entity_files")
            or data.get("entityFiles")
            or data.get("files")
            or data.get("data", [])
        )
        if not isinstance(items, list):
            items = []
        return PaginatedResponse[EntityFile](
            data=[EntityFile.model_validate(f) for f in items],
            next_page_token=data.get("next_page_token") or data.get("nextPageToken"),
        )

    async def get(self, file_id: FileId) -> EntityFile:
        data = await self._client.get(f"/entity-files/{file_id}", v1=True)
        return EntityFile.model_validate(data)

    async def batch_get(
        self,
        file_ids: Sequence[FileId],
        *,
        max_concurrent: int = 10,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[FileId, EntityFile]:
        """
        Fetch metadata for multiple files with controlled concurrency.

        Makes individual get() calls with bounded concurrency.

        Args:
            file_ids: File IDs to fetch metadata for
            max_concurrent: Maximum concurrent API calls (default: 10)
            on_error: How to handle AffinityError exceptions:
                - "raise": Raise on first AffinityError (default)
                - "skip": Skip failed IDs, return partial results

        Returns:
            Dict mapping file_id -> EntityFile for successfully fetched files.

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        if not file_ids:
            return {}
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        unique_ids = list(dict.fromkeys(file_ids))
        results: dict[FileId, EntityFile] = {}

        async def fetch_one(fid: FileId) -> tuple[FileId, EntityFile | None]:
            try:
                entity_file = await self.get(fid)
                return (fid, entity_file)
            except AffinityError:
                if on_error == "raise":
                    raise
                return (fid, None)

        for i in range(0, len(unique_ids), max_concurrent):
            chunk = unique_ids[i : i + max_concurrent]
            tasks = [asyncio.create_task(fetch_one(fid)) for fid in chunk]
            try:
                for coro in asyncio.as_completed(tasks):
                    fid, entity_file = await coro
                    if entity_file is not None:
                        results[fid] = entity_file
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        return results

    async def list_batch(
        self,
        *,
        person_ids: Sequence[PersonId] | None = None,
        company_ids: Sequence[CompanyId] | None = None,
        opportunity_ids: Sequence[OpportunityId] | None = None,
        max_concurrent: int = 10,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[int, builtins.list[EntityFile]]:
        """
        List files for multiple entities with controlled concurrency.

        Fetches all files (auto-paginates) for each entity ID concurrently.
        Exactly one of person_ids, company_ids, or opportunity_ids must be provided.

        Args:
            person_ids: Person IDs to fetch files for
            company_ids: Company IDs to fetch files for
            opportunity_ids: Opportunity IDs to fetch files for
            max_concurrent: Maximum concurrent entity queries (default: 10)
            on_error: How to handle errors:
                - "raise": Raise on first error (default)
                - "skip": Skip failed entities, return partial results

        Returns:
            Dict mapping entity ID (int) -> list of EntityFile objects.
            Entities with no files map to an empty list.

        Raises:
            ValueError: If not exactly one of person_ids/company_ids/opportunity_ids is provided.
            AffinityError: If on_error="raise" and any fetch fails.

        Example:
            >>> files_map = await client.files.list_batch(
            ...     company_ids=[CompanyId(1), CompanyId(2)],
            ...     max_concurrent=20,
            ...     on_error="skip",
            ... )
            >>> for company_id, files in files_map.items():
            ...     print(f"Company {company_id}: {len(files)} files")
        """
        provided = [
            ("person_ids", person_ids),
            ("company_ids", company_ids),
            ("opportunity_ids", opportunity_ids),
        ]
        non_none = [(name, ids) for name, ids in provided if ids is not None]
        if len(non_none) != 1:
            raise ValueError(
                "Exactly one of person_ids, company_ids, or opportunity_ids must be provided"
            )

        param_name, entity_ids = non_none[0]
        if not entity_ids:
            return {}
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        unique_ids = builtins.list(dict.fromkeys(entity_ids))
        results: dict[int, builtins.list[EntityFile]] = {}

        id_kwarg_map = {
            "person_ids": "person_id",
            "company_ids": "company_id",
            "opportunity_ids": "opportunity_id",
        }
        iter_kwarg_name = id_kwarg_map[param_name]

        async def fetch_all_for_entity(eid: int) -> tuple[int, builtins.list[EntityFile]]:
            iter_kwargs: dict[str, Any] = {iter_kwarg_name: eid}
            files: builtins.list[EntityFile] = []
            try:
                async for entity_file in self.iter(**iter_kwargs):
                    files.append(entity_file)
            except AffinityError:
                if on_error == "raise":
                    raise
            return (int(eid), files)

        for i in range(0, len(unique_ids), max_concurrent):
            chunk = unique_ids[i : i + max_concurrent]
            tasks = [asyncio.create_task(fetch_all_for_entity(eid)) for eid in chunk]
            try:
                for coro in asyncio.as_completed(tasks):
                    eid_int, files = await coro
                    results[eid_int] = files
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        return results

    async def download(
        self,
        file_id: FileId,
        *,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> bytes:
        return await self._client.download_file(
            f"/entity-files/download/{file_id}",
            v1=True,
            timeout=timeout,
            deadline_seconds=deadline_seconds,
        )

    async def get_download_url(
        self,
        file_id: FileId,
        *,
        timeout: httpx.Timeout | float | None = None,
    ) -> PresignedUrl:
        """
        Get a presigned download URL for a file without downloading its content.

        The returned URL is valid for approximately 60 seconds and can be
        fetched without authentication (it's self-authenticating via signature).

        Args:
            file_id: The entity file ID
            timeout: Optional request timeout

        Returns:
            PresignedUrl with the URL, file metadata, and expiration info

        Raises:
            AffinityError: If the API doesn't return a redirect URL
        """
        # Fetch file metadata first
        file_meta = await self.get(file_id)

        url = await self._client.get_redirect_url(
            f"/entity-files/download/{file_id}",
            v1=True,
            timeout=timeout,
        )
        if not url:
            raise AffinityError(
                f"Failed to get presigned URL for file {file_id}: no redirect returned"
            )

        # Parse X-Amz-Expires from the presigned URL to determine TTL
        # Default to 60 seconds if not found (Affinity's typical TTL)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        expires_in = 60  # default
        if "X-Amz-Expires" in qs:
            with contextlib.suppress(ValueError, IndexError):
                expires_in = int(qs["X-Amz-Expires"][0])

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in)

        return PresignedUrl(
            url=url,
            file_id=int(file_id),
            name=file_meta.name,
            size=file_meta.size,
            content_type=file_meta.content_type,
            expires_in=expires_in,
            expires_at=expires_at,
        )

    def download_stream(
        self,
        file_id: FileId,
        *,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> AsyncIterator[bytes]:
        return self._client.stream_download(
            f"/entity-files/download/{file_id}",
            v1=True,
            chunk_size=chunk_size,
            on_progress=on_progress,
            timeout=timeout,
            deadline_seconds=deadline_seconds,
        )

    async def download_stream_with_info(
        self,
        file_id: FileId,
        *,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> AsyncDownloadedFile:
        """
        Stream-download a file and return response metadata (headers/filename/size).

        Notes:
        - `filename` is derived from `Content-Disposition` when present.
        - If the server does not provide a filename, callers can fall back to
          `await files.get(file_id)` and use `.name`.
        """
        return await self._client.stream_download_with_info(
            f"/entity-files/download/{file_id}",
            v1=True,
            chunk_size=chunk_size,
            on_progress=on_progress,
            timeout=timeout,
            deadline_seconds=deadline_seconds,
        )

    async def download_to(
        self,
        file_id: FileId,
        path: str | Path,
        *,
        overwrite: bool = False,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> Path:
        target = Path(path)
        if target.exists() and not overwrite:
            raise FileExistsError(str(target))

        with target.open("wb") as f:
            async for chunk in self.download_stream(
                file_id,
                chunk_size=chunk_size,
                on_progress=on_progress,
                timeout=timeout,
                deadline_seconds=deadline_seconds,
            ):
                f.write(chunk)

        return target

    async def _post_upload(
        self,
        files: dict[str, Any],
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
    ) -> dict[str, Any]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )
        return await self._client.upload_file(
            "/entity-files",
            files=files,
            data=_upload_target_data(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            ),
            v1=True,
        )

    async def _post_upload_path(
        self,
        path: str | Path,
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
        filename: str | None,
        content_type: str | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        p = Path(path)
        upload_filename = filename or p.name
        guessed, _ = mimetypes.guess_type(upload_filename)
        final_content_type = content_type or guessed or "application/octet-stream"
        total = p.stat().st_size

        if on_progress:
            on_progress(0, total, phase="upload")

        with p.open("rb") as f:
            result = await self._post_upload(
                files={"file": (upload_filename, f, final_content_type)},
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            )

        if on_progress:
            on_progress(total, total, phase="upload")

        return result

    async def _post_upload_bytes(
        self,
        data: bytes,
        filename: str,
        *,
        person_id: PersonId | None,
        company_id: CompanyId | None,
        opportunity_id: OpportunityId | None,
        content_type: str | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        guessed, _ = mimetypes.guess_type(filename)
        final_content_type = content_type or guessed or "application/octet-stream"
        total = len(data)

        if on_progress:
            on_progress(0, total, phase="upload")

        result = await self._post_upload(
            files={"file": (filename, data, final_content_type)},
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        if on_progress:
            on_progress(total, total, phase="upload")

        return result

    async def upload(
        self,
        files: dict[str, Any],
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> bool:
        """
        Upload files to an entity.

        Args:
            files: Multipart files mapping, e.g. `{"file": (filename, content, content_type)}`
            person_id: Person to attach to
            company_id: Company to attach to
            opportunity_id: Opportunity to attach to

        Returns:
            True when the upload succeeded (the response's `success` flag when
            present). Use `upload_returning_files()` to get the created file records.
        """
        return _upload_succeeded(
            await self._post_upload(
                files,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            )
        )

    async def upload_returning_files(
        self,
        files: dict[str, Any],
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> builtins.list[EntityFile]:
        """
        Upload files to an entity and return the created file records.

        Same request as `upload()`; returns the `entity_files` from the response,
        so the new file ids can be used with `get()`/`download()` straight away.

        Returns:
            The created files. Empty if the API returned no `entity_files` (older
            responses were just `{"success": true}`). Never raises after a successful
            (2xx) upload: an entry that fails validation is returned as a partial
            record (unvalidated; fields that failed are None) when it has an id,
            and skipped otherwise.
        """
        return _parse_uploaded_files(
            await self._post_upload(
                files,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            )
        )

    async def upload_path(
        self,
        path: str | Path,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> bool:
        """
        Upload a file from disk.

        Notes:
        - Returns True when the upload succeeded. Use `upload_path_returning_files()`
          to get the created file record (with its id).
        - Progress reporting is best-effort for uploads (start/end only).
        """
        return _upload_succeeded(
            await self._post_upload_path(
                path,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                filename=filename,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    async def upload_path_returning_files(
        self,
        path: str | Path,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> builtins.list[EntityFile]:
        """
        Upload a file from disk and return the created file records.

        Same as `upload_path()`, but returns the response's `entity_files`
        (see `upload_returning_files()` for the parsing rules).
        """
        return _parse_uploaded_files(
            await self._post_upload_path(
                path,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                filename=filename,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    async def upload_bytes(
        self,
        data: bytes,
        filename: str,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> bool:
        """
        Upload in-memory bytes as a file.

        Notes:
        - Returns True when the upload succeeded. Use `upload_bytes_returning_files()`
          to get the created file record (with its id).
        - Progress reporting is best-effort for uploads (start/end only).
        """
        return _upload_succeeded(
            await self._post_upload_bytes(
                data,
                filename,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    async def upload_bytes_returning_files(
        self,
        data: bytes,
        filename: str,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
        content_type: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> builtins.list[EntityFile]:
        """
        Upload in-memory bytes as a file and return the created file records.

        Same as `upload_bytes()`, but returns the response's `entity_files`
        (see `upload_returning_files()` for the parsing rules).
        """
        return _parse_uploaded_files(
            await self._post_upload_bytes(
                data,
                filename,
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                content_type=content_type,
                on_progress=on_progress,
            )
        )

    async def all(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> AsyncIterator[EntityFile]:
        self._validate_exactly_one_target(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )

        page_token: str | None = None
        while True:
            page = await self.list(
                person_id=person_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
                page_token=page_token,
            )
            for item in page.data:
                yield item
            if not page.has_next:
                break
            page_token = page.next_page_token

    def iter(
        self,
        *,
        person_id: PersonId | None = None,
        company_id: CompanyId | None = None,
        opportunity_id: OpportunityId | None = None,
    ) -> AsyncIterator[EntityFile]:
        return self.all(
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
        )


class AsyncAuthService:
    """Async service for authentication info."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def whoami(self) -> WhoAmI:
        data = await self._client.get("/auth/whoami")
        return WhoAmI.model_validate(data)

    # Note: rate limit handling is exposed via `client.rate_limits` (version-agnostic).
