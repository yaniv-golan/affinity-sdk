"""
Reminder service (V2 API: ``/v2/reminders``).

Listing and creating are GA; ``get`` and ``delete`` need Affinity API 2026-07-15 and ``update``
2026-09-17 (both beta). Changing a reminder's type, which V2 can't do, goes through V1 and
returns the same V2-shaped :class:`~affinity.models.Reminder`. Every reminder is attached to
exactly one person, company or opportunity (both APIs require it).

On V2 only a reminder's creator or owner may update or delete it. When V2 refuses (403), the
update or delete is retried once through V1, whose docs state no such rule (an update first
reads the reminder, since V1 needs its full type-specific fields). If the retry fails too, V2's
refusal is raised, with the V1 error as its cause. Clearing content can't be retried (V1 can't
clear it).
"""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from ..exceptions import AffinityError, AuthorizationError
from ..models.pagination import AsyncPageIterator, PageIterator, PaginatedResponse
from ..models.secondary import Reminder, ReminderCreate, ReminderUpdate
from ..models.types import (
    CompanyId,
    OpportunityId,
    PersonId,
    ReminderIdType,
    ReminderResetType,
    ReminderStatus,
    ReminderType,
    UserId,
)
from ._org_reads import check_cursor_alone, check_limit, page_of
from ._v2_filters import v2_body_datetime, v2_filter_datetime

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient

GET_DELETE_MIN_API_VERSION = "2026-07-15"
UPDATE_MIN_API_VERSION = "2026-09-17"
MAX_PERIOD_DAYS = 3650
# Entity ids per OR-filter request in list_batch (keeps the URL short)
BATCH_CHUNK = 50

_V1_TYPE = {ReminderType.ONE_TIME: 0, ReminderType.RECURRING: 1}
_V1_RESET = {
    ReminderResetType.INTERACTION: 0,
    ReminderResetType.EMAIL: 1,
    ReminderResetType.EVENT: 2,
}


# --- request bodies -----------------------------------------------------------------------------


def _entity(data: ReminderCreate) -> dict[str, Any] | None:
    for kind, value in (
        ("person", data.person_id),
        ("company", data.company_id),
        ("opportunity", data.opportunity_id),
    ):
        if value is not None:
            return {"type": kind, "id": int(value)}
    return None


def _check_create(data: ReminderCreate) -> None:
    if _entity(data) is None:
        raise ValueError(
            "A reminder must be attached to one person, company or opportunity "
            "(person_id, company_id or opportunity_id)"
        )
    if data.type == ReminderType.ONE_TIME and data.due_date is None:
        raise ValueError("A one-time reminder needs due_date")
    if data.type == ReminderType.RECURRING:
        if data.reset_type is None:
            raise ValueError("A recurring reminder needs reset_type")
        if data.reminder_days is None:
            raise ValueError("A recurring reminder needs reminder_days")
    if data.reminder_days is not None and not 1 <= data.reminder_days <= MAX_PERIOD_DAYS:
        raise ValueError(f"reminder_days must be between 1 and {MAX_PERIOD_DAYS}")


def create_body(data: ReminderCreate) -> dict[str, Any]:
    """``POST /v2/reminders`` body (needs an entity)."""
    body: dict[str, Any] = {
        "type": str(ReminderType(data.type)),
        "owner": {"id": int(data.owner_id)},
        "entity": _entity(data),
    }
    if data.content is not None:
        body["content"] = data.content
    if data.type == ReminderType.RECURRING:
        body["recurrence"] = {
            "periodDays": data.reminder_days,
            "resetTrigger": str(ReminderResetType(data.reset_type)),
        }
    if data.due_date is not None:
        body["dueDate"] = v2_body_datetime(data.due_date)
    return body


def update_body(data: ReminderUpdate) -> dict[str, Any]:
    """``POST /v2/reminders/{id}`` body: only what was set."""
    body: dict[str, Any] = {}
    if data.owner_id is not None:
        body["owner"] = {"id": int(data.owner_id)}
    if data.content is not None:
        body["content"] = data.content
    elif data.clear_content:
        body["content"] = None
    if data.due_date is not None:
        body["dueDate"] = v2_body_datetime(data.due_date)
    recurrence: dict[str, Any] = {}
    if data.reminder_days is not None:
        if not 1 <= data.reminder_days <= MAX_PERIOD_DAYS:
            raise ValueError(f"reminder_days must be between 1 and {MAX_PERIOD_DAYS}")
        recurrence["periodDays"] = data.reminder_days
    if data.reset_type is not None:
        recurrence["resetTrigger"] = str(ReminderResetType(data.reset_type))
    if recurrence:
        body["recurrence"] = recurrence
    if data.is_completed is True:
        body["completedAt"] = v2_body_datetime(datetime.now(timezone.utc))
    elif data.is_completed is False:
        body["completedAt"] = None
    if not body:
        raise ValueError("ReminderUpdate has nothing to update")
    return body


def v1_update_body(data: ReminderUpdate) -> dict[str, Any]:
    """V1 ``PUT /reminders/{id}`` body: a type change, or the retry after V2 refused."""
    if data.clear_content:
        raise ValueError(
            "Change the type and clear the content in separate updates (a type change goes "
            "through the V1 API, which can't clear content)"
        )
    body: dict[str, Any] = {}
    if data.type is not None:
        body["type"] = _V1_TYPE[ReminderType(data.type)]
    if data.owner_id is not None:
        body["owner_id"] = int(data.owner_id)
    if data.content is not None:
        body["content"] = data.content
    if data.due_date is not None:
        body["due_date"] = v2_body_datetime(data.due_date)
    if data.reset_type is not None:
        body["reset_type"] = _V1_RESET[ReminderResetType(data.reset_type)]
    if data.reminder_days is not None:
        body["reminder_days"] = data.reminder_days
    if data.is_completed is not None:
        body["is_completed"] = data.is_completed
    return body


def v1_retry_body(data: ReminderUpdate, current: Reminder) -> dict[str, Any]:
    """V1 ``PUT`` body for retrying an update V2 refused: V1 needs the type, and the due date
    (one-time) or reset type and period (recurring), so the current values fill the gaps."""
    body = v1_update_body(data.model_copy(update={"type": data.type or current.type}))
    if ReminderType(current.type) == ReminderType.ONE_TIME:
        body.setdefault("due_date", v2_body_datetime(current.due_date))
    else:
        if "reset_type" not in body and current.reset_type is not None:
            body["reset_type"] = _V1_RESET[ReminderResetType(current.reset_type)]
        if "reminder_days" not in body and current.reminder_days is not None:
            body["reminder_days"] = current.reminder_days
    return body


def _ref(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value.get("id") is not None:
        return {"id": int(value["id"])}
    return None


def reminder_from_v1(data: dict[str, Any]) -> Reminder:
    """A V1 reminder response as the V2-shaped :class:`Reminder`."""
    kind = ReminderType(data.get("type"))
    recurrence = None
    if kind == ReminderType.RECURRING and data.get("reminder_days") is not None:
        recurrence = {
            "periodDays": data["reminder_days"],
            "resetTrigger": ReminderResetType(data.get("reset_type")),
        }
    return Reminder.model_validate(
        {
            "id": data["id"],
            "type": kind,
            "status": ReminderStatus(data.get("status")),
            "content": data.get("content"),
            "dueDate": data.get("due_date"),
            "recurrence": recurrence,
            "creator": _ref(data.get("creator")),
            "owner": _ref(data.get("owner")),
            "completer": _ref(data.get("completer")),
            "person": _ref(data.get("person")),
            "company": _ref(data.get("organization")),
            "opportunity": _ref(data.get("opportunity")),
            "createdAt": data.get("created_at"),
            "updatedAt": None,
            "completedAt": data.get("completed_at"),
        }
    )


# --- list filters -------------------------------------------------------------------------------


def _either(field: str, ids: Sequence[int]) -> str:
    clause = " | ".join(f"{field}={int(i)}" for i in ids)
    return f"({clause})" if len(ids) > 1 else clause


def reminder_params(
    *,
    ids: Sequence[int] | None = None,
    person_id: int | None = None,
    company_id: int | None = None,
    opportunity_id: int | None = None,
    creator_id: int | None = None,
    owner_id: int | None = None,
    completer_id: int | None = None,
    type: ReminderType | None = None,
    reset_type: ReminderResetType | None = None,
    status: ReminderStatus | None = None,
    due_after: datetime | None = None,
    due_before: datetime | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    limit: int | None = None,
    total_count: bool = False,
    either: tuple[str, Sequence[int]] | None = None,
) -> list[tuple[str, Any]]:
    """Query for ``GET /v2/reminders``. Datetimes in whole seconds, rounded outward."""
    check_limit(limit)
    clauses = []
    if either is not None:
        clauses.append(_either(*either))
    if ids:
        clauses.append(_either("id", ids))
    for field, value in (
        ("person.id", person_id),
        ("company.id", company_id),
        ("opportunity.id", opportunity_id),
        ("creator.id", creator_id),
        ("owner.id", owner_id),
        ("completer.id", completer_id),
    ):
        if value is not None:
            clauses.append(f"{field}={int(value)}")
    if type is not None:
        clauses.append(f"type={ReminderType(type)}")
    if reset_type is not None:
        clauses.append(f"resetTrigger={ReminderResetType(reset_type)}")
    if status is not None:
        clauses.append(f"status={ReminderStatus(status)}")
    for field, after, before in (
        ("dueDate", due_after, due_before),
        ("createdAt", created_after, created_before),
        ("updatedAt", updated_after, updated_before),
    ):
        if after is not None:
            clauses.append(f"{field}>={v2_filter_datetime(after, round_up=False)}")
        if before is not None:
            clauses.append(f"{field}<{v2_filter_datetime(before, round_up=True)}")
    params: list[tuple[str, Any]] = []
    if clauses:
        params.append(("filter", " & ".join(clauses)))
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    return params


_BATCH_FIELDS = {
    "person_ids": ("person.id", "person"),
    "company_ids": ("company.id", "company"),
    "opportunity_ids": ("opportunity.id", "opportunity"),
}


def _batch_target(
    person_ids: Sequence[PersonId] | None,
    company_ids: Sequence[CompanyId] | None,
    opportunity_ids: Sequence[OpportunityId] | None,
) -> tuple[str, str, builtins.list[int]]:
    given = [
        (name, ids)
        for name, ids in (
            ("person_ids", person_ids),
            ("company_ids", company_ids),
            ("opportunity_ids", opportunity_ids),
        )
        if ids is not None
    ]
    if len(given) != 1:
        raise ValueError(
            "Exactly one of person_ids, company_ids, or opportunity_ids must be provided"
        )
    name, ids = given[0]
    field, attr = _BATCH_FIELDS[name]
    return field, attr, builtins.list(dict.fromkeys(int(i) for i in ids))


def _owner_of(reminder: Reminder, attr: str) -> int | None:
    ref = getattr(reminder, attr)
    return int(ref.id) if ref is not None else None


# --- services -----------------------------------------------------------------------------------


class ReminderService:
    """Reminders (V2, with two V1 exceptions; see the module docstring)."""

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
        due_after: datetime | None = None,
        due_before: datetime | None = None,
        ids: Sequence[ReminderIdType] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Reminder]:
        """One page of the reminders visible to the caller, soonest due first.

        ``*_after`` bounds are inclusive and ``*_before`` exclusive (whole seconds). ``limit``
        is 1-100. Pass ``cursor`` (``pagination.next_cursor``) alone for the next page;
        :meth:`iter` follows every page.
        """
        filters: dict[str, Any] = {
            "ids": ids,
            "person_id": person_id,
            "company_id": company_id,
            "opportunity_id": opportunity_id,
            "creator_id": creator_id,
            "owner_id": owner_id,
            "completer_id": completer_id,
            "type": type,
            "reset_type": reset_type,
            "status": status,
            "due_after": due_after,
            "due_before": due_before,
            "created_after": created_after,
            "created_before": created_before,
            "updated_after": updated_after,
            "updated_before": updated_before,
        }
        check_cursor_alone(cursor, *filters.values(), limit, total_count)
        if cursor is not None:
            return page_of(Reminder, self._client.get_url(cursor))
        params = reminder_params(**filters, limit=limit, total_count=total_count)
        return page_of(Reminder, self._client.get("/reminders", params=params or None))

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
        due_after: datetime | None = None,
        due_before: datetime | None = None,
        ids: Sequence[ReminderIdType] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[Reminder]:
        """All reminders matching the filters (every page); see :meth:`list`."""
        kwargs = locals().copy()
        kwargs.pop("self")

        def fetch_page(next_url: str | None) -> PaginatedResponse[Reminder]:
            if next_url:
                return self.list(cursor=next_url)
            return self.list(**kwargs)

        return PageIterator(fetch_page)

    def list_batch(
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
    ) -> dict[int, builtins.list[Reminder]]:
        """Reminders for many entities of one kind: ``{entity_id: [reminders]}``.

        One request per 50 ids (an OR filter), plus further pages. Every requested id is a key;
        entities without matching reminders map to an empty list.
        """
        field, attr, unique = _batch_target(person_ids, company_ids, opportunity_ids)
        results: dict[int, builtins.list[Reminder]] = {i: [] for i in unique}
        filters: dict[str, Any] = {
            "status": status,
            "type": type,
            "reset_type": reset_type,
            "creator_id": creator_id,
            "owner_id": owner_id,
            "due_before": due_before,
            "due_after": due_after,
        }
        for start in range(0, len(unique), BATCH_CHUNK):
            chunk = unique[start : start + BATCH_CHUNK]
            params = reminder_params(**filters, either=(field, chunk), limit=100)
            page = page_of(Reminder, self._client.get("/reminders", params=params))
            while True:
                for reminder in page.data:
                    owner = _owner_of(reminder, attr)
                    if owner in results:
                        results[owner].append(reminder)
                if not page.next_cursor:
                    break
                page = page_of(Reminder, self._client.get_url(page.next_cursor))
        return results

    def get(self, reminder_id: ReminderIdType) -> Reminder:
        """One reminder (``NotFoundError`` when it doesn't exist or isn't visible)."""
        return Reminder.model_validate(
            self._client.get(
                f"/reminders/{int(reminder_id)}", min_api_version=GET_DELETE_MIN_API_VERSION
            )
        )

    def create(self, data: ReminderCreate) -> Reminder:
        """Create a reminder attached to one person, company or opportunity; the caller becomes
        its creator."""
        _check_create(data)
        return Reminder.model_validate(self._client.post("/reminders", json=create_body(data)))

    def update(self, reminder_id: ReminderIdType, data: ReminderUpdate) -> Reminder:
        """Change a reminder (only what is set in ``data``) and return it as read back.

        Two requests on V2 (the update has no response body). With ``type`` set, the whole update
        is one V1 request. If V2 refuses (only the creator or owner may update there), the update
        is retried once through V1.
        """
        if data.type is not None:
            return reminder_from_v1(
                self._client.put(
                    f"/reminders/{int(reminder_id)}", json=v1_update_body(data), v1=True
                )
            )
        body = update_body(data)
        try:
            self._client.post(
                f"/reminders/{int(reminder_id)}", json=body, min_api_version=UPDATE_MIN_API_VERSION
            )
        except AuthorizationError as refused:
            # V2 lets only the creator or owner change a reminder; V1 states no such rule
            if data.clear_content:
                raise
            try:
                body = v1_retry_body(data, self.get(reminder_id))
                result = self._client.put(f"/reminders/{int(reminder_id)}", json=body, v1=True)
            except AffinityError as retry_error:
                raise refused from retry_error
            return reminder_from_v1(result)
        return self.get(reminder_id)

    def delete(self, reminder_id: ReminderIdType) -> bool:
        """Delete a reminder. Returns True.

        If V2 refuses (only the creator or owner may delete there), retried once through V1.
        """
        try:
            self._client.delete(
                f"/reminders/{int(reminder_id)}", min_api_version=GET_DELETE_MIN_API_VERSION
            )
        except AuthorizationError as refused:
            try:
                self._client.delete(f"/reminders/{int(reminder_id)}", v1=True)
            except AffinityError as retry_error:
                raise refused from retry_error
        return True


class AsyncReminderService:
    """Async reminders; same behavior as :class:`ReminderService`."""

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
        due_after: datetime | None = None,
        due_before: datetime | None = None,
        ids: Sequence[ReminderIdType] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Reminder]:
        """One page of reminders; see :meth:`ReminderService.list`."""
        filters: dict[str, Any] = {
            "ids": ids,
            "person_id": person_id,
            "company_id": company_id,
            "opportunity_id": opportunity_id,
            "creator_id": creator_id,
            "owner_id": owner_id,
            "completer_id": completer_id,
            "type": type,
            "reset_type": reset_type,
            "status": status,
            "due_after": due_after,
            "due_before": due_before,
            "created_after": created_after,
            "created_before": created_before,
            "updated_after": updated_after,
            "updated_before": updated_before,
        }
        check_cursor_alone(cursor, *filters.values(), limit, total_count)
        if cursor is not None:
            return page_of(Reminder, await self._client.get_url(cursor))
        params = reminder_params(**filters, limit=limit, total_count=total_count)
        return page_of(Reminder, await self._client.get("/reminders", params=params or None))

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
        due_after: datetime | None = None,
        due_before: datetime | None = None,
        ids: Sequence[ReminderIdType] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Reminder]:
        """All reminders matching the filters (every page); see :meth:`list`."""
        kwargs = locals().copy()
        kwargs.pop("self")

        async def fetch_page(next_url: str | None) -> PaginatedResponse[Reminder]:
            if next_url:
                return await self.list(cursor=next_url)
            return await self.list(**kwargs)

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
        """Reminders for many entities of one kind; see :meth:`ReminderService.list_batch`.

        Chunks of 50 ids run up to ``max_concurrent`` at a time. ``on_error="skip"`` keeps
        what a failed chunk collected before the error instead of raising.
        """
        field, attr, unique = _batch_target(person_ids, company_ids, opportunity_ids)
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        results: dict[int, builtins.list[Reminder]] = {i: [] for i in unique}
        filters: dict[str, Any] = {
            "status": status,
            "type": type,
            "reset_type": reset_type,
            "creator_id": creator_id,
            "owner_id": owner_id,
            "due_before": due_before,
            "due_after": due_after,
        }
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_chunk(chunk: builtins.list[int]) -> builtins.list[Reminder]:
            found: builtins.list[Reminder] = []
            async with semaphore:
                try:
                    params = reminder_params(**filters, either=(field, chunk), limit=100)
                    page = page_of(Reminder, await self._client.get("/reminders", params=params))
                    while True:
                        found.extend(page.data)
                        if not page.next_cursor:
                            break
                        page = page_of(Reminder, await self._client.get_url(page.next_cursor))
                except AffinityError:
                    if on_error == "raise":
                        raise
            return found

        chunks = [unique[i : i + BATCH_CHUNK] for i in range(0, len(unique), BATCH_CHUNK)]
        for found in await asyncio.gather(*(fetch_chunk(c) for c in chunks)):
            for reminder in found:
                owner = _owner_of(reminder, attr)
                if owner in results:
                    results[owner].append(reminder)
        return results

    async def get(self, reminder_id: ReminderIdType) -> Reminder:
        """One reminder; see :meth:`ReminderService.get`."""
        return Reminder.model_validate(
            await self._client.get(
                f"/reminders/{int(reminder_id)}", min_api_version=GET_DELETE_MIN_API_VERSION
            )
        )

    async def create(self, data: ReminderCreate) -> Reminder:
        """Create a reminder; see :meth:`ReminderService.create`."""
        _check_create(data)
        return Reminder.model_validate(
            await self._client.post("/reminders", json=create_body(data))
        )

    async def update(self, reminder_id: ReminderIdType, data: ReminderUpdate) -> Reminder:
        """Change a reminder and return it as read back; see :meth:`ReminderService.update`."""
        if data.type is not None:
            return reminder_from_v1(
                await self._client.put(
                    f"/reminders/{int(reminder_id)}", json=v1_update_body(data), v1=True
                )
            )
        body = update_body(data)
        try:
            await self._client.post(
                f"/reminders/{int(reminder_id)}", json=body, min_api_version=UPDATE_MIN_API_VERSION
            )
        except AuthorizationError as refused:
            if data.clear_content:
                raise
            try:
                body = v1_retry_body(data, await self.get(reminder_id))
                result = await self._client.put(
                    f"/reminders/{int(reminder_id)}", json=body, v1=True
                )
            except AffinityError as retry_error:
                raise refused from retry_error
            return reminder_from_v1(result)
        return await self.get(reminder_id)

    async def delete(self, reminder_id: ReminderIdType) -> bool:
        """Delete a reminder; see :meth:`ReminderService.delete`. Returns True."""
        try:
            await self._client.delete(
                f"/reminders/{int(reminder_id)}", min_api_version=GET_DELETE_MIN_API_VERSION
            )
        except AuthorizationError as refused:
            try:
                await self._client.delete(f"/reminders/{int(reminder_id)}", v1=True)
            except AffinityError as retry_error:
                raise refused from retry_error
        return True
