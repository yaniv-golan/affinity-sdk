"""SDK tests for field value changes: V1 params and keyset paging, V2 org-wide list."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.models import FieldValueChangeV2
from affinity.types import CompanyId, FieldId, ListEntryId, PersonId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V1 = "https://api.affinity.co/field-value-changes"
V2 = "https://api.affinity.co/v2/field-value-changes"


def _v1(change_id: int, changed_at: str, entry: int | None = 7) -> dict[str, Any]:
    """A realistic V1 row: snake_case, offset timestamp with microseconds."""
    return {
        "id": change_id,
        "field_id": 520352,
        "entity_id": 99,
        "list_entry_id": entry,
        "action_type": 0,
        "value": {"id": 1, "text": "Lead"},
        "changed_at": changed_at,
        "changer": {"id": 5, "type": 1, "first_name": "Ann", "last_name": "Lee"},
    }


def _v2(change_id: int, *, value: Any = None, changer: Any = None, entry: Any = None) -> dict:  # type: ignore[type-arg]
    return {
        "id": change_id,
        "field": {"id": "field-520352", "entityType": "company", "name": "Status", "type": "list"},
        "entity": {"id": 99},
        "listEntry": entry,
        "changer": changer,
        "changedAt": "2020-04-27T22:35:43Z",
        "actionType": "add",
        "type": "ranked-dropdown",
        "value": value,
    }


def _params(route: Any, call: int = -1) -> dict[str, str]:
    return dict(route.calls[call].request.url.params)


# --- V1 list --------------------------------------------------------------------------------------


@respx.mock
def test_list_without_selector_sends_the_new_params() -> None:
    route = respx.get(V1).mock(
        return_value=Response(200, json=[_v1(1, "2020-04-27T15:35:43.383953-07:00")])
    )
    with Affinity(api_key="k", max_retries=0) as client:
        rows = client.field_value_changes.list(
            FieldId("field-520352"),
            changed_after=datetime(2020, 1, 1, 9, 30, tzinfo=timezone.utc),
            limit=3,
            order_by="asc",
        )
    assert _params(route) == {
        "field_id": "520352",
        "changed_after": "2020-01-01T09:30:00.000000Z",
        "limit": "3",
        "order_by": "asc",
    }
    assert rows[0].changed_at == datetime(2020, 4, 27, 22, 35, 43, 383953, tzinfo=timezone.utc)
    assert rows[0].list_entry_id == 7


@respx.mock
def test_naive_and_offset_changed_after_are_sent_as_utc() -> None:
    route = respx.get(V1).mock(return_value=Response(200, json=[]))
    with Affinity(api_key="k", max_retries=0) as client:
        client.field_value_changes.list(FieldId("field-1"), changed_after=datetime(2020, 1, 1))
        offset = datetime.fromisoformat("2020-01-01T02:00:00+02:00")
        client.field_value_changes.list(FieldId("field-1"), changed_after=offset)
    assert _params(route, 0)["changed_after"] == "2020-01-01T00:00:00.000000Z"
    assert _params(route, 1)["changed_after"] == "2020-01-01T00:00:00.000000Z"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"person_id": PersonId(1), "company_id": CompanyId(2)}, "at most one of"),
        ({"limit": 0}, "'limit' must be >= 1"),
        ({"order_by": "up"}, "'order_by'"),
        ({"after_id": 5}, "'after_id' requires"),
        ({"after_id": 5, "order_by": "asc"}, "'after_id' requires"),
        ({"after_id": 5, "changed_after": datetime(2020, 1, 1)}, "'after_id' requires"),
    ],
)
def test_list_rejects_what_the_server_rejects(kwargs: dict[str, Any], message: str) -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match=message):
        client.field_value_changes.list(FieldId("field-1"), **kwargs)


@respx.mock
def test_selector_still_works() -> None:
    route = respx.get(V1).mock(return_value=Response(200, json=[]))
    with Affinity(api_key="k", max_retries=0) as client:
        client.field_value_changes.list(FieldId("field-1"), list_entry_id=ListEntryId(7))
    assert _params(route) == {"field_id": "1", "list_entry_id": "7"}


# --- V1 keyset paging -----------------------------------------------------------------------------


@respx.mock
def test_iter_all_pages_by_keyset_until_an_empty_page() -> None:
    pages = [
        [_v1(1, "2020-04-27T15:35:43.383953-07:00"), _v1(2, "2020-04-28T03:41:50.978539-07:00")],
        # Same timestamp as the last row of page 1: after_id keeps it from being skipped
        [_v1(3, "2020-04-28T03:41:50.978539-07:00")],
        [],
    ]
    route = respx.get(V1).mock(side_effect=[Response(200, json=p) for p in pages])
    with Affinity(api_key="k", max_retries=0) as client:
        ids = [
            int(c.id) for c in client.field_value_changes.iter_all(FieldId("field-1"), page_size=2)
        ]
    assert ids == [1, 2, 3]
    assert len(route.calls) == 3
    first, second, third = (_params(route, i) for i in range(3))
    assert first == {
        "field_id": "1",
        "changed_after": "1970-01-01T00:00:00.000000Z",
        "limit": "2",
        "order_by": "asc",
    }
    assert second["changed_after"] == "2020-04-28T10:41:50.978539Z"
    assert second["after_id"] == "2"
    assert third["after_id"] == "3"
    assert third["changed_after"] == "2020-04-28T10:41:50.978539Z"


@respx.mock
def test_iter_all_stops_if_the_server_ignores_the_cursor() -> None:
    same = [_v1(1, "2020-04-27T15:35:43.383953-07:00")]
    route = respx.get(V1).mock(return_value=Response(200, json=same))
    with Affinity(api_key="k", max_retries=0) as client:
        ids = [
            int(c.id) for c in client.field_value_changes.iter_all(FieldId("field-1"), page_size=1)
        ]
    assert ids == [1]
    assert len(route.calls) == 2


@respx.mock
async def test_async_iter_all_pages_by_keyset() -> None:
    pages = [[_v1(1, "2020-04-27T15:35:43.383953-07:00")], []]
    route = respx.get(V1).mock(side_effect=[Response(200, json=p) for p in pages])
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        ids = [int(c.id) async for c in client.field_value_changes.iter_all(FieldId("field-1"))]
    assert ids == [1]
    assert _params(route, 1)["after_id"] == "1"


# --- V2 org-wide ---------------------------------------------------------------------------------


@respx.mock
def test_list_global_builds_the_filter() -> None:
    route = respx.get(V2).mock(
        return_value=Response(200, json={"data": [], "pagination": {"nextUrl": None}})
    )
    with Affinity(api_key="k", max_retries=0) as client:
        client.field_value_changes.list_global(
            field_id=["field-1", 2],
            list_entry_id=7,
            changer_id=5,
            changed_after=datetime(2025, 1, 1, tzinfo=timezone.utc),
            changed_before=datetime(2025, 2, 1, tzinfo=timezone.utc),
            action_type="update",
            order="desc",
            limit=50,
        )
    assert _params(route) == {
        "filter": (
            "(field.id=field-1 | field.id=field-2) & listEntry.id=7 & changer.id=5"
            " & changedAt>=2025-01-01T00:00:00Z & changedAt<2025-02-01T00:00:00Z"
            " & actionType=update"
        ),
        "orderBy": "-changedAt",
        "limit": "50",
    }


@respx.mock
def test_list_global_rounds_bounds_outward_to_whole_seconds() -> None:
    route = respx.get(V2).mock(
        return_value=Response(200, json={"data": [], "pagination": {"nextUrl": None}})
    )
    with Affinity(api_key="k", max_retries=0) as client:
        client.field_value_changes.list_global(
            changed_after=datetime(2025, 1, 1, 0, 0, 5, 900, tzinfo=timezone.utc),
            changed_before=datetime(2025, 1, 1, 0, 0, 9, 1, tzinfo=timezone.utc),
        )
    assert _params(route)["filter"] == (
        "changedAt>=2025-01-01T00:00:05Z & changedAt<2025-01-01T00:00:10Z"
    )


@respx.mock
def test_list_global_without_filters_sends_nothing() -> None:
    route = respx.get(V2).mock(
        return_value=Response(200, json={"data": [], "pagination": {"nextUrl": None}})
    )
    with Affinity(api_key="k", max_retries=0) as client:
        client.field_value_changes.list_global()
    assert _params(route) == {}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "between 1 and 100"),
        ({"limit": 101}, "between 1 and 100"),
        ({"action_type": "create"}, "'action_type'"),
        ({"order": "newest"}, "'order'"),
        ({"cursor": "https://x", "limit": 5}, "Cannot combine 'cursor'"),
    ],
)
def test_list_global_rejects_bad_arguments(kwargs: dict[str, Any], message: str) -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match=message):
        client.field_value_changes.list_global(**kwargs)


@respx.mock
def test_list_global_parses_variants_and_iter_global_follows_next_url() -> None:
    next_url = "https://api.affinity.co/v2/field-value-changes?cursor=abc"
    first = {
        "data": [
            _v2(
                1,
                value={"referenceType": "entity", "id": 3, "text": "Lead"},
                entry={"id": 7, "listId": 9},
            ),
            _v2(2, value={"referenceType": "deleted-entity", "displayValue": "Old"}),
        ],
        "pagination": {"nextUrl": next_url, "prevUrl": None},
    }
    second = {
        "data": [
            _v2(
                3, changer={"id": 5, "firstName": "Ann", "lastName": None, "emailAddress": "a@x.co"}
            )
        ],
        "pagination": {"nextUrl": None},
    }
    respx.get(V2, params={"cursor": "abc"}).mock(return_value=Response(200, json=second))
    respx.get(V2).mock(return_value=Response(200, json=first))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.field_value_changes.list_global(field_id="field-520352")
        rows = list(client.field_value_changes.iter_global(field_id="field-520352"))
    assert page.next_cursor == next_url
    assert [r.id for r in rows] == [1, 2, 3]
    one, two, three = rows
    assert isinstance(one, FieldValueChangeV2)
    assert one.list_entry is not None and one.list_entry.list_id == 9
    assert one.field.type == "list" and one.value_type == "ranked-dropdown"
    assert two.list_entry is None and two.changer is None
    assert two.value == {"referenceType": "deleted-entity", "displayValue": "Old"}
    assert three.changer is not None and three.changer.first_name == "Ann"


@respx.mock
async def test_async_iter_global() -> None:
    respx.get(V2).mock(
        return_value=Response(200, json={"data": [_v2(1)], "pagination": {"nextUrl": None}})
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        rows = [r async for r in client.field_value_changes.iter_global(action_type="add")]
    assert [r.id for r in rows] == [1]
