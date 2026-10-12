"""SDK tests for reminders on the V2 API (`/v2/reminders`), with the documented V1 exceptions:
changing a reminder's type, and retrying an update/delete V2 refuses (creator or owner only).
Both return the same V2-shaped `Reminder`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response
from pydantic import ValidationError

from affinity import Affinity, AsyncAffinity
from affinity.exceptions import ApiVersionTooOldError, AuthorizationError
from affinity.models import CompanyRef, PersonRef, Reminder, ReminderCreate, ReminderUpdate
from affinity.types import (
    CompanyId,
    OpportunityId,
    PersonId,
    ReminderIdType,
    ReminderResetType,
    ReminderStatus,
    ReminderType,
    UserId,
)

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
V1 = "https://api.affinity.co"
ONE_TIME = {
    "id": 11,
    "type": "one-time",
    "status": "active",
    "content": "Call back",
    "dueDate": "2026-11-01T09:00:00Z",
    "recurrence": None,
    "creator": {"id": 1},
    "owner": {"id": 2},
    "completer": None,
    "person": {"id": 5},
    "company": None,
    "opportunity": None,
    "createdAt": "2026-10-01T09:00:00Z",
    "updatedAt": None,
    "completedAt": None,
}
RECURRING = {
    **ONE_TIME,
    "id": 12,
    "type": "recurring",
    "status": "overdue",
    "recurrence": {"periodDays": 30, "resetTrigger": "email"},
    "person": None,
    "company": {"id": 7},
}
V1_REMINDER = {
    "id": 13,
    "type": 1,
    "status": 1,
    "content": "Quarterly check-in",
    "due_date": "2026-12-01T09:00:00Z",
    "reset_type": 2,
    "reminder_days": 90,
    "creator": {"id": 1, "first_name": "A", "last_name": "B", "type": 1},
    "owner": {"id": 2, "first_name": "C", "last_name": "D", "type": 1},
    "completer": None,
    "person": None,
    "organization": None,
    "opportunity": None,
    "created_at": "2026-10-01T09:00:00Z",
    "completed_at": None,
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _version(route: respx.Route, i: int = 0) -> str | None:
    return route.calls[i].request.headers.get("x-affinity-api-version")


def _body(route: respx.Route, i: int = 0) -> Any:
    return json.loads(route.calls[i].request.content)


# --- models and enums ---------------------------------------------------------------------------


def test_model_reads_both_v2_variants() -> None:
    one = Reminder.model_validate(ONE_TIME)
    assert one.type == ReminderType.ONE_TIME
    assert one.status == ReminderStatus.ACTIVE
    assert one.person == PersonRef(id=PersonId(5))
    assert one.recurrence is None
    assert one.reset_type is None and one.reminder_days is None
    rec = Reminder.model_validate(RECURRING)
    assert rec.company == CompanyRef(id=CompanyId(7))
    assert rec.reset_type == ReminderResetType.EMAIL
    assert rec.reminder_days == 30


def test_enums_take_v2_strings_and_1x_ints() -> None:
    assert ReminderType(0) is ReminderType.ONE_TIME
    assert ReminderType(1) is ReminderType.RECURRING
    assert ReminderResetType(2) is ReminderResetType.EVENT
    assert ReminderStatus(0) is ReminderStatus.COMPLETED
    assert ReminderStatus(2) is ReminderStatus.OVERDUE
    assert ReminderType("recurring") is ReminderType.RECURRING
    with pytest.raises(ValueError):
        ReminderType(7)


def test_meeting_is_an_alias_of_event() -> None:
    assert ReminderResetType.MEETING is ReminderResetType.EVENT
    assert ReminderResetType("meeting") is ReminderResetType.EVENT
    assert ReminderResetType.EVENT.value == "event"


def test_unknown_strings_are_kept() -> None:
    assert ReminderStatus("snoozed") == "snoozed"


def test_create_allows_at_most_one_entity() -> None:
    with pytest.raises(ValidationError, match="one entity"):
        ReminderCreate(
            owner_id=UserId(2),
            type=ReminderType.ONE_TIME,
            due_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
            person_id=PersonId(5),
            company_id=CompanyId(7),
        )


def test_update_rejects_content_and_clear_content_together() -> None:
    with pytest.raises(ValidationError, match="clear_content"):
        ReminderUpdate(content="x", clear_content=True)


# --- create -------------------------------------------------------------------------------------


@respx.mock
def test_create_one_time_on_v2() -> None:
    route = respx.post(f"{V2}/reminders").mock(return_value=Response(201, json=ONE_TIME))
    with Affinity(api_key="k", max_retries=0) as client:
        created = client.reminders.create(
            ReminderCreate(
                owner_id=UserId(2),
                type=ReminderType.ONE_TIME,
                content="Call back",
                due_date=datetime(2026, 11, 1, 9, 0, 0, 750000, tzinfo=timezone.utc),
                person_id=PersonId(5),
            )
        )
    assert created.id == 11
    assert _body(route) == {
        "type": "one-time",
        "owner": {"id": 2},
        "entity": {"type": "person", "id": 5},
        "content": "Call back",
        "dueDate": "2026-11-01T09:00:00Z",
    }
    assert _version(route) is None  # GA since 2024-01-01: the key's default version


@respx.mock
def test_create_recurring_on_v2_naive_due_date_is_utc() -> None:
    route = respx.post(f"{V2}/reminders").mock(return_value=Response(201, json=RECURRING))
    with Affinity(api_key="k", max_retries=0) as client:
        client.reminders.create(
            ReminderCreate(
                owner_id=UserId(2),
                type=ReminderType.RECURRING,
                reset_type=ReminderResetType.MEETING,
                reminder_days=30,
                due_date=datetime(2026, 11, 1, 9),
                company_id=CompanyId(7),
            )
        )
    assert _body(route) == {
        "type": "recurring",
        "owner": {"id": 2},
        "entity": {"type": "company", "id": 7},
        "recurrence": {"periodDays": 30, "resetTrigger": "event"},
        "dueDate": "2026-11-01T09:00:00Z",
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"type": ReminderType.ONE_TIME}, "due_date"),
        ({"type": ReminderType.RECURRING, "reminder_days": 30}, "reset_type"),
        (
            {"type": ReminderType.RECURRING, "reset_type": ReminderResetType.EMAIL},
            "reminder_days",
        ),
        (
            {
                "type": ReminderType.RECURRING,
                "reset_type": ReminderResetType.EMAIL,
                "reminder_days": 4000,
            },
            "3650",
        ),
    ],
)
def test_create_checks_required_fields(kwargs: dict[str, Any], message: str) -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match=message),
    ):
        client.reminders.create(
            ReminderCreate(owner_id=UserId(2), opportunity_id=OpportunityId(3), **kwargs)
        )


def test_create_without_entity_is_refused_before_sending() -> None:
    # Both APIs require exactly one person, company or opportunity (V1 answers 422)
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="person, company or opportunity"),
    ):
        client.reminders.create(
            ReminderCreate(
                owner_id=UserId(2),
                type=ReminderType.RECURRING,
                reset_type=ReminderResetType.EVENT,
                reminder_days=90,
            )
        )


# --- get / update / delete ------------------------------------------------------------------------


@respx.mock
def test_get_and_delete_send_2026_07_15() -> None:
    get = respx.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    delete = respx.delete(f"{V2}/reminders/11").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.reminders.get(ReminderIdType(11)).content == "Call back"
        assert client.reminders.delete(ReminderIdType(11)) is True
    assert _version(get) == "2026-07-15"
    assert _version(delete) == "2026-07-15"


@respx.mock
def test_update_sends_only_given_fields_then_reads_back() -> None:
    update = respx.post(f"{V2}/reminders/11").mock(return_value=Response(204))
    get = respx.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    with Affinity(api_key="k", max_retries=0) as client:
        result = client.reminders.update(
            ReminderIdType(11),
            ReminderUpdate(
                owner_id=UserId(3),
                due_date=datetime(2026, 12, 1, 10, 30, tzinfo=timezone.utc),
            ),
        )
    assert _body(update) == {"owner": {"id": 3}, "dueDate": "2026-12-01T10:30:00Z"}
    assert result.id == 11
    assert _version(update) == "2026-09-17"
    assert get.called


@respx.mock
def test_none_means_unchanged_and_clear_content_sends_null() -> None:
    update = respx.post(f"{V2}/reminders/11").mock(return_value=Response(204))
    respx.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    with Affinity(api_key="k", max_retries=0) as client:
        client.reminders.update(
            ReminderIdType(11), ReminderUpdate(content=None, is_completed=None, clear_content=True)
        )
    assert _body(update) == {"content": None}


@respx.mock
def test_complete_and_uncomplete() -> None:
    update = respx.post(f"{V2}/reminders/11").mock(return_value=Response(204))
    respx.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    with Affinity(api_key="k", max_retries=0) as client:
        client.reminders.update(ReminderIdType(11), ReminderUpdate(is_completed=True))
        client.reminders.update(ReminderIdType(11), ReminderUpdate(is_completed=False))
    completed_at = _body(update, 0)["completedAt"]
    assert completed_at.endswith("Z") and "." not in completed_at
    assert _body(update, 1) == {"completedAt": None}


@respx.mock
def test_recurrence_update_sends_only_given_keys() -> None:
    update = respx.post(f"{V2}/reminders/12").mock(return_value=Response(204))
    respx.get(f"{V2}/reminders/12").mock(return_value=Response(200, json=RECURRING))
    with Affinity(api_key="k", max_retries=0) as client:
        client.reminders.update(ReminderIdType(12), ReminderUpdate(reminder_days=14))
    assert _body(update) == {"recurrence": {"periodDays": 14}}


def test_empty_update_is_refused() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="nothing to update"),
    ):
        client.reminders.update(ReminderIdType(11), ReminderUpdate())


@respx.mock
def test_type_change_goes_through_v1_in_one_request() -> None:
    v1 = respx.put(f"{V1}/reminders/13").mock(return_value=Response(200, json=V1_REMINDER))
    with Affinity(api_key="k", max_retries=0) as client:
        result = client.reminders.update(
            ReminderIdType(13),
            ReminderUpdate(
                type=ReminderType.RECURRING,
                reset_type=ReminderResetType.EVENT,
                reminder_days=90,
                is_completed=False,
            ),
        )
    assert _body(v1) == {"type": 1, "reset_type": 2, "reminder_days": 90, "is_completed": False}
    assert result.type == ReminderType.RECURRING


def test_type_change_cannot_clear_content() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="separate"),
    ):
        client.reminders.update(
            ReminderIdType(13), ReminderUpdate(type=ReminderType.ONE_TIME, clear_content=True)
        )


def test_pinned_2024_01_01_cannot_get() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.reminders.get(ReminderIdType(11))


# --- list / iter / list_batch ---------------------------------------------------------------------


@respx.mock
def test_list_maps_1x_filters_to_v2() -> None:
    route = respx.get(f"{V2}/reminders").mock(return_value=Response(200, json=_page([ONE_TIME])))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.reminders.list(
            person_id=PersonId(5),
            owner_id=UserId(2),
            type=ReminderType.ONE_TIME,
            reset_type=ReminderResetType.MEETING,
            status=ReminderStatus.OVERDUE,
            due_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
            due_before=datetime(2026, 2, 1, tzinfo=timezone.utc),
            limit=25,
            total_count=True,
        )
    assert page.data[0].id == 11
    params = route.calls[0].request.url.params
    assert params["filter"] == (
        "person.id=5 & owner.id=2 & type=one-time & resetTrigger=event & status=overdue"
        " & dueDate>=2026-01-01T00:00:00Z & dueDate<2026-02-01T00:00:00Z"
    )
    assert params["limit"] == "25"
    assert params["totalCount"] == "true"
    assert _version(route) is None


def test_list_rejects_1x_paging_and_bad_limit() -> None:
    with Affinity(api_key="k", max_retries=0) as client:
        with pytest.raises(TypeError):
            client.reminders.list(page_size=10)  # type: ignore[call-arg]
        with pytest.raises(ValueError, match="limit"):
            client.reminders.list(limit=500)


@respx.mock
def test_iter_follows_cursors() -> None:
    url = f"{V2}/reminders"
    respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([RECURRING]))
    )
    respx.get(url).mock(return_value=Response(200, json=_page([ONE_TIME], f"{url}?cursor=p2")))
    with Affinity(api_key="k", max_retries=0) as client:
        assert [r.id for r in client.reminders.iter()] == [11, 12]


@respx.mock
def test_list_batch_uses_one_or_filter_per_chunk() -> None:
    route = respx.get(f"{V2}/reminders").mock(
        return_value=Response(
            200,
            json=_page([ONE_TIME, {**ONE_TIME, "id": 14, "person": {"id": 6}}]),
        )
    )
    with Affinity(api_key="k", max_retries=0) as client:
        result = client.reminders.list_batch(
            person_ids=[PersonId(5), PersonId(6), PersonId(9), PersonId(5)],
            status=ReminderStatus.ACTIVE,
        )
    assert {k: [r.id for r in v] for k, v in result.items()} == {5: [11], 6: [14], 9: []}
    assert route.call_count == 1
    assert route.calls[0].request.url.params["filter"] == (
        "(person.id=5 | person.id=6 | person.id=9) & status=active"
    )


def test_list_batch_needs_exactly_one_id_kind() -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError):
        client.reminders.list_batch(person_ids=[PersonId(1)], company_ids=[CompanyId(2)])


@respx.mock
async def test_async_service() -> None:
    listing = respx.get(f"{V2}/reminders").mock(
        return_value=Response(200, json=_page([ONE_TIME, RECURRING]))
    )
    respx.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    create = respx.post(f"{V2}/reminders").mock(return_value=Response(201, json=ONE_TIME))
    update = respx.post(f"{V2}/reminders/11").mock(return_value=Response(204))
    respx.delete(f"{V2}/reminders/11").mock(return_value=Response(204))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        page = await client.reminders.list(company_id=CompanyId(7))
        ids = [r.id async for r in client.reminders.iter()]
        await client.reminders.create(
            ReminderCreate(
                owner_id=UserId(2),
                type=ReminderType.ONE_TIME,
                due_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
                person_id=PersonId(5),
            )
        )
        await client.reminders.update(ReminderIdType(11), ReminderUpdate(content="new"))
        assert await client.reminders.delete(ReminderIdType(11)) is True
        batch = await client.reminders.list_batch(company_ids=[CompanyId(7)])
    assert [r.id for r in page.data] == [11, 12]
    assert ids == [11, 12]
    assert create.called
    assert _body(update) == {"content": "new"}
    assert [r.id for r in batch[7]] == [12]
    assert listing.calls[0].request.url.params["filter"] == "company.id=7"


# --- V1 fallback when V2 refuses (only the creator or owner may change a reminder on V2) ---------

FORBIDDEN = Response(403, json={"errors": [{"code": "permission-denied", "message": "Forbidden"}]})


@respx.mock
def test_update_forbidden_on_v2_retries_on_v1_with_a_complete_body() -> None:
    # V1 needs the type, and due_date (one-time) or reset_type + reminder_days (recurring)
    v2 = respx.post(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    respx.get(f"{V2}/reminders/13").mock(return_value=Response(200, json={**RECURRING, "id": 13}))
    v1 = respx.put(f"{V1}/reminders/13").mock(return_value=Response(200, json=V1_REMINDER))
    with Affinity(api_key="k", max_retries=0) as client:
        result = client.reminders.update(
            ReminderIdType(13), ReminderUpdate(is_completed=True, reminder_days=60)
        )
    assert v2.called
    assert _body(v1) == {
        "type": 1,
        "reset_type": 1,
        "reminder_days": 60,
        "is_completed": True,
    }
    assert result.id == 13


@respx.mock
def test_update_retry_of_a_one_time_reminder_keeps_its_due_date() -> None:
    respx.post(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    respx.get(f"{V2}/reminders/13").mock(return_value=Response(200, json={**ONE_TIME, "id": 13}))
    v1 = respx.put(f"{V1}/reminders/13").mock(return_value=Response(200, json=V1_REMINDER))
    with Affinity(api_key="k", max_retries=0) as client:
        client.reminders.update(ReminderIdType(13), ReminderUpdate(content="done"))
    assert _body(v1) == {"type": 0, "content": "done", "due_date": "2026-11-01T09:00:00Z"}


@respx.mock
def test_update_retry_refused_by_v1_raises_the_v2_refusal() -> None:
    respx.post(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    respx.get(f"{V2}/reminders/13").mock(return_value=Response(200, json={**ONE_TIME, "id": 13}))
    respx.put(f"{V1}/reminders/13").mock(
        return_value=Response(422, json={"message": "Due date is required"})
    )
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(AuthorizationError):
        client.reminders.update(ReminderIdType(13), ReminderUpdate(content="done"))


@respx.mock
def test_delete_forbidden_on_v2_retries_on_v1() -> None:
    respx.delete(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    v1 = respx.delete(f"{V1}/reminders/13").mock(return_value=Response(200, json={"success": True}))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.reminders.delete(ReminderIdType(13)) is True
    assert v1.called


@respx.mock
def test_forbidden_on_both_apis_raises() -> None:
    respx.delete(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    respx.delete(f"{V1}/reminders/13").mock(return_value=Response(403, json={"message": "no"}))
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(AuthorizationError):
        client.reminders.delete(ReminderIdType(13))


@respx.mock
def test_forbidden_clear_content_is_not_retried() -> None:
    respx.post(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    v1 = respx.put(f"{V1}/reminders/13").mock(return_value=Response(200, json=V1_REMINDER))
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(AuthorizationError):
        client.reminders.update(ReminderIdType(13), ReminderUpdate(clear_content=True))
    assert not v1.called


@respx.mock
async def test_async_forbidden_falls_back_to_v1() -> None:
    respx.post(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    respx.get(f"{V2}/reminders/13").mock(return_value=Response(200, json={**ONE_TIME, "id": 13}))
    respx.delete(f"{V2}/reminders/13").mock(return_value=FORBIDDEN)
    put = respx.put(f"{V1}/reminders/13").mock(return_value=Response(200, json=V1_REMINDER))
    delete = respx.delete(f"{V1}/reminders/13").mock(
        return_value=Response(200, json={"success": True})
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        await client.reminders.update(ReminderIdType(13), ReminderUpdate(owner_id=UserId(4)))
        assert await client.reminders.delete(ReminderIdType(13)) is True
    assert _body(put) == {"type": 0, "owner_id": 4, "due_date": "2026-11-01T09:00:00Z"}
    assert delete.called
