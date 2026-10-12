"""SDK tests: one field on one company/person/list entry (V2), its value items, and one list
entry's field change history."""

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

from affinity import Affinity, AsyncAffinity
from affinity.exceptions import ApiVersionTooOldError
from affinity.types import CompanyId, ListEntryId, ListId, PersonId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
FIELD = {
    "id": "field-1",
    "name": "Stage",
    "type": "global",
    "enrichmentSource": None,
    "value": {"type": "text", "data": "Seed"},
}
CHANGE = {
    "id": 1,
    "field": {"id": "field-1", "entityType": "company", "name": "Stage", "type": "list"},
    "entity": {"id": 7},
    "listEntry": {"id": 3, "listId": 9},
    "actionType": "update",
    "changedAt": "2026-10-01T09:00:00Z",
    "changer": None,
    "type": "text",
    "value": "Seed",
}


def _page(items: list[Any], next_url: str | None = None, kind: str | None = None) -> Any:
    page: dict[str, Any] = {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}
    if kind:
        page["type"] = kind
    return page


@respx.mock
def test_company_get_and_update_field_value() -> None:
    get = respx.get(f"{V2}/companies/7/fields/field-1").mock(return_value=Response(200, json=FIELD))
    post = respx.post(f"{V2}/companies/7/fields/field-1").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.companies.get_field_value(CompanyId(7), "field-1") == "Seed"
        assert client.companies.update_field_value(CompanyId(7), "field-1", "Series A") is None
    assert get.called
    assert json.loads(post.calls[0].request.content) == {
        "value": {"type": "text", "data": "Series A"}
    }


@respx.mock
def test_person_field_value_items_pages() -> None:
    url = f"{V2}/persons/5/fields/field-2/values"
    second = respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page(["c"], kind="filterable-text-multi"))
    )
    respx.get(url).mock(
        return_value=Response(
            200, json=_page(["a", "b"], f"{url}?cursor=p2", kind="filterable-text-multi")
        )
    )
    with Affinity(api_key="k", max_retries=0) as client:
        items = list(client.persons.iter_field_value_items(PersonId(5), "field-2"))
    assert items == ["a", "b", "c"]
    assert second.calls[0].request.headers["x-affinity-api-version"] == "2026-09-17"


def test_value_items_need_2026_09_17() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2026-07-15") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.companies.list_field_value_items(CompanyId(7), "field-1")


@respx.mock
def test_list_entry_value_items_and_changes() -> None:
    items = respx.get(f"{V2}/lists/9/list-entries/3/fields/field-1/values").mock(
        return_value=Response(200, json=_page([{"id": 1, "text": "A"}], kind="dropdown-multi"))
    )
    changes = respx.get(f"{V2}/lists/9/list-entries/3/field-value-changes").mock(
        return_value=Response(200, json=_page([CHANGE]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        entries = client.lists.entries(ListId(9))
        page = entries.list_field_value_items(ListEntryId(3), "field-1", limit=10)
        changed = entries.list_field_changes(
            ListEntryId(3),
            field_id="field-1",
            changed_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
            action_type="update",
            limit=50,
        )
        all_changes = list(entries.iter_field_changes(ListEntryId(3)))
    assert page.data == [{"id": 1, "text": "A"}]
    assert items.calls[0].request.url.params["limit"] == "10"
    assert changed.data[0].action_type == "update" and len(all_changes) == 1
    params = changes.calls[0].request.url.params
    assert params["filter"] == (
        "field.id=field-1 & changedAt>=2026-01-01T00:00:00Z & actionType=update"
    )
    assert params["limit"] == "50"


def test_list_field_changes_refuses_cursor_with_filters() -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match="cursor"):
        client.lists.entries(ListId(9)).list_field_changes(
            ListEntryId(3), cursor=f"{V2}/x?cursor=a", limit=5
        )


@respx.mock
async def test_async() -> None:
    respx.get(f"{V2}/companies/7/fields/field-1").mock(return_value=Response(200, json=FIELD))
    post = respx.post(f"{V2}/persons/5/fields/field-1").mock(return_value=Response(204))
    respx.get(f"{V2}/companies/7/fields/field-1/values").mock(
        return_value=Response(200, json=_page(["x"], kind="filterable-text-multi"))
    )
    respx.get(f"{V2}/lists/9/list-entries/3/field-value-changes").mock(
        return_value=Response(200, json=_page([CHANGE]))
    )
    respx.get(f"{V2}/lists/9/list-entries/3/fields/field-1/values").mock(
        return_value=Response(200, json=_page([], kind="dropdown-multi"))
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        assert await client.companies.get_field_value(CompanyId(7), "field-1") == "Seed"
        await client.persons.update_field_value(PersonId(5), "field-1", 3)
        assert [
            i async for i in client.companies.iter_field_value_items(CompanyId(7), "field-1")
        ] == ["x"]
        entries = client.lists.entries(ListId(9))
        assert [c.id async for c in entries.iter_field_changes(ListEntryId(3))] == [1]
        assert (await entries.list_field_value_items(ListEntryId(3), "field-1")).data == []
    assert json.loads(post.calls[0].request.content) == {"value": {"type": "number", "data": 3}}
