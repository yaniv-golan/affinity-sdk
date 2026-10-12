"""SDK tests: list get/create on the V2 API, with the documented V1 parts: `get_size` (V2 has
no list size), a list created with an owner or permissions, and adding to a list (V2 documents
it, but Affinity doesn't serve it yet)."""

from __future__ import annotations

import json
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.models import ListCreate
from affinity.types import CompanyId, ListId, ListType, UserId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
V1 = "https://api.affinity.co"
LIST_V2 = {
    "id": 9,
    "name": "Dealflow",
    "type": "company",
    "isPublic": False,
    "ownerId": 4,
    "creatorId": 4,
    "createdAt": "2026-01-01T00:00:00Z",
}
LIST_V1 = {
    "id": 9,
    "name": "Dealflow",
    "type": 1,
    "public": False,
    "owner_id": 4,
    "creator_id": 4,
    "list_size": 42,
}


def _body(route: respx.Route) -> Any:
    return json.loads(route.calls[0].request.content)


@respx.mock
def test_get_uses_v2() -> None:
    route = respx.get(f"{V2}/lists/9").mock(return_value=Response(200, json=LIST_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        lst = client.lists.get(ListId(9))
    assert lst.type == ListType.COMPANY
    assert lst.fields is None
    assert lst.created_at is not None
    assert "x-affinity-api-version" not in route.calls[0].request.headers


@respx.mock
def test_get_size_reads_v1_directly() -> None:
    v1 = respx.get(f"{V1}/lists/9").mock(return_value=Response(200, json=LIST_V1))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.lists.get_size(ListId(9)) == 42
        assert client.lists.get_size(ListId(9)) == 42  # cached
    assert v1.call_count == 1


@respx.mock
def test_create_on_v2() -> None:
    route = respx.post(f"{V2}/lists").mock(return_value=Response(201, json=LIST_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        lst = client.lists.create(
            ListCreate(name="Dealflow", type=ListType.COMPANY, is_public=False)
        )
    assert lst.id == 9
    assert _body(route) == {"name": "Dealflow", "type": "company", "isPublic": False}


@respx.mock
def test_create_with_owner_or_permissions_stays_on_v1() -> None:
    v1 = respx.post(f"{V1}/lists").mock(return_value=Response(200, json=LIST_V1))
    with Affinity(api_key="k", max_retries=0) as client:
        client.lists.create(
            ListCreate(name="Dealflow", type=ListType.COMPANY, is_public=False, owner_id=UserId(5))
        )
    assert _body(v1)["owner_id"] == 5


def test_add_opportunity_is_gone() -> None:
    with Affinity(api_key="k", max_retries=0) as client:
        assert not hasattr(client.lists.entries(ListId(9)), "add_opportunity")


@respx.mock
def test_add_company_uses_v1() -> None:
    route = respx.post(f"{V1}/lists/9/list-entries").mock(
        return_value=Response(
            200,
            json={
                "id": 501,
                "list_id": 9,
                "creator_id": 4,
                "entity_id": 7,
                "entity_type": 1,
                "created_at": "2026-10-12T09:00:00Z",
                "entity": {"id": 7, "name": "Acme", "domain": "acme.com"},
            },
        )
    )
    with Affinity(api_key="k", max_retries=0) as client:
        entry = client.lists.entries(ListId(9)).add_company(CompanyId(7), creator_id=4)
    assert _body(route) == {"entity_id": 7, "creator_id": 4}
    assert entry.id == 501 and entry.entity_id == 7


@respx.mock
async def test_async_lists_and_entries() -> None:
    respx.get(f"{V2}/lists/9").mock(return_value=Response(200, json=LIST_V2))
    respx.get(f"{V1}/lists/9").mock(return_value=Response(200, json=LIST_V1))
    create = respx.post(f"{V2}/lists").mock(return_value=Response(201, json=LIST_V2))
    add = respx.post(f"{V1}/lists/9/list-entries").mock(
        return_value=Response(
            200,
            json={
                "id": 501,
                "list_id": 9,
                "entity_id": 7,
                "entity_type": 1,
                "created_at": "2026-10-12T09:00:00Z",
            },
        )
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        assert (await client.lists.get(ListId(9))).name == "Dealflow"
        assert await client.lists.get_size(ListId(9)) == 42
        await client.lists.create(ListCreate(name="X", type=ListType.PERSON, is_public=True))
        entry = await client.lists.entries(ListId(9)).add_company(CompanyId(7))
        assert not hasattr(client.lists.entries(ListId(9)), "add_opportunity")
    assert _body(create) == {"name": "X", "type": "person", "isPublic": True}
    assert add.called and entry.entity_id == 7
