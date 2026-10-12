"""SDK tests: list get/create and list-entry add on the V2 API, with the documented V1 parts:
`get_size` (V2 has no list size) and a list created with an owner or permissions."""

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
from affinity.exceptions import ValidationError
from affinity.models import ListCreate
from affinity.types import CompanyId, EntityType, ListId, ListType, PersonId, UserId

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
ENTRY_V2 = {
    "id": 501,
    "type": "company",
    "listId": 9,
    "creatorId": 4,
    "createdAt": "2026-10-12T09:00:00Z",
    "entity": {"id": 7, "name": "Acme", "domain": "acme.com"},
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


@respx.mock
def test_add_company_on_v2_returns_a_list_entry() -> None:
    route = respx.post(f"{V2}/lists/9/list-entries").mock(return_value=Response(201, json=ENTRY_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        entry = client.lists.entries(ListId(9)).add_company(CompanyId(7), creator_id=4)
    assert _body(route) == {"entity": {"id": 7}, "creatorId": 4}
    assert route.calls[0].request.headers["x-affinity-api-version"] == "2026-09-17"
    assert entry.id == 501
    assert entry.list_id == 9
    assert entry.entity_id == 7
    assert entry.entity_type == EntityType.ORGANIZATION
    assert entry.entity is not None and entry.entity.name == "Acme"  # type: ignore[union-attr]


@respx.mock
def test_add_person_on_v2() -> None:
    route = respx.post(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(
            201,
            json={
                **ENTRY_V2,
                "type": "person",
                "entity": {
                    "id": 3,
                    "firstName": "Dana",
                    "lastName": "Levi",
                    "primaryEmailAddress": "d@a.co",
                    "type": "external",
                },
            },
        )
    )
    with Affinity(api_key="k", max_retries=0) as client:
        entry = client.lists.entries(ListId(9)).add_person(PersonId(3))
    assert _body(route) == {"entity": {"id": 3}}
    assert entry.entity_type == EntityType.PERSON
    assert entry.entity_id == 3


@respx.mock
def test_add_rejected_explains() -> None:
    respx.post(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(
            400, json={"errors": [{"code": "bad-request", "message": "entity not found"}]}
        )
    )
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValidationError, match="list 9") as exc,
    ):
        client.lists.entries(ListId(9)).add_company(CompanyId(7))
    assert "required field" in str(exc.value)
    assert "entity not found" in str(exc.value)


def test_add_opportunity_is_gone() -> None:
    with Affinity(api_key="k", max_retries=0) as client:
        assert not hasattr(client.lists.entries(ListId(9)), "add_opportunity")


@respx.mock
async def test_async_lists_and_entries() -> None:
    respx.get(f"{V2}/lists/9").mock(return_value=Response(200, json=LIST_V2))
    respx.get(f"{V1}/lists/9").mock(return_value=Response(200, json=LIST_V1))
    create = respx.post(f"{V2}/lists").mock(return_value=Response(201, json=LIST_V2))
    add = respx.post(f"{V2}/lists/9/list-entries").mock(return_value=Response(201, json=ENTRY_V2))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        assert (await client.lists.get(ListId(9))).name == "Dealflow"
        assert await client.lists.get_size(ListId(9)) == 42
        await client.lists.create(ListCreate(name="X", type=ListType.PERSON, is_public=True))
        entry = await client.lists.entries(ListId(9)).add_company(CompanyId(7))
        assert not hasattr(client.lists.entries(ListId(9)), "add_opportunity")
    assert _body(create) == {"name": "X", "type": "person", "isPublic": True}
    assert add.called and entry.entity_id == 7


def test_add_with_an_old_pin_raises_the_version_error_unchanged() -> None:
    from affinity.exceptions import ApiVersionTooOldError

    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.lists.entries(ListId(9)).add_person(PersonId(2))
