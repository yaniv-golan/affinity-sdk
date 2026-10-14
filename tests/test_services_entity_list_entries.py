"""get_list_entries on companies and persons: entries name their entity, cursors stay with it."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.models.types import CompanyId, EntityType, PersonId
from affinity.services.companies import AsyncCompanyService, CompanyService
from affinity.services.persons import AsyncPersonService, PersonService

V2 = "https://v2.example/v2"


def _entry(entry_id: int, **extra: Any) -> dict[str, Any]:
    # What /companies|persons/{id}/list-entries sends: no entityId / entityType / entity
    return {"id": entry_id, "listId": 1, "createdAt": "2024-01-01T00:00:00Z", "fields": [], **extra}


def _handler(kind: str, entity_id: int, seen: list[httpx.URL]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if request.url.params.get("cursor") == "next":
            body = {"data": [_entry(11)], "pagination": {"nextUrl": None}}
        else:
            body = {
                "data": [_entry(10)],
                "pagination": {"nextUrl": f"{V2}/{kind}/{entity_id}/list-entries?cursor=next"},
            }
        return httpx.Response(200, json=body, request=request)

    return handler


def _config(handler: Any) -> ClientConfig:
    return ClientConfig(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url=V2,
        max_retries=0,
        transport=httpx.MockTransport(handler),
        async_transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    ("kind", "service_cls", "id_cls", "entity_type"),
    [
        ("companies", CompanyService, CompanyId, EntityType.ORGANIZATION),
        ("persons", PersonService, PersonId, EntityType.PERSON),
    ],
)
def test_entries_name_their_entity_on_every_page(
    kind: str, service_cls: Any, id_cls: Any, entity_type: EntityType
) -> None:
    seen: list[httpx.URL] = []
    http = HTTPClient(_config(_handler(kind, 7, seen)))
    try:
        service = service_cls(http)
        first = service.get_list_entries(id_cls(7), limit=1)
        second = service.get_list_entries(id_cls(7), cursor=first.next_cursor)
    finally:
        http.close()
    for entry in [*first.data, *second.data]:
        assert entry.entity_id == 7
        assert entry.entity_type == entity_type
        assert entry.entity is None
    assert [int(e.id) for e in [*first.data, *second.data]] == [10, 11]
    assert seen[0].params.get("limit") == "1"


@pytest.mark.parametrize(
    ("kind", "service_cls", "id_cls"),
    [("companies", CompanyService, CompanyId), ("persons", PersonService, PersonId)],
)
def test_a_cursor_from_another_entity_is_refused(kind: str, service_cls: Any, id_cls: Any) -> None:
    seen: list[httpx.URL] = []
    http = HTTPClient(_config(_handler(kind, 7, seen)))
    try:
        service = service_cls(http)
        with pytest.raises(ValueError, match="not for"):
            service.get_list_entries(id_cls(8), cursor=f"{V2}/{kind}/7/list-entries?cursor=next")
        with pytest.raises(ValueError, match="Cannot combine"):
            service.get_list_entries(id_cls(7), limit=1, cursor="x")
    finally:
        http.close()
    assert seen == []


def test_an_entity_the_api_does_name_is_kept() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        item = _entry(10, entityId=99, entityType=1)
        return httpx.Response(200, json={"data": [item], "pagination": {}}, request=request)

    http = HTTPClient(_config(handler))
    try:
        entry = CompanyService(http).get_list_entries(CompanyId(7)).data[0]
    finally:
        http.close()
    assert entry.entity_id == 99


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "service_cls", "id_cls", "entity_type"),
    [
        ("companies", AsyncCompanyService, CompanyId, EntityType.ORGANIZATION),
        ("persons", AsyncPersonService, PersonId, EntityType.PERSON),
    ],
)
async def test_async_entries_name_their_entity_and_check_the_cursor(
    kind: str, service_cls: Any, id_cls: Any, entity_type: EntityType
) -> None:
    seen: list[httpx.URL] = []
    http = AsyncHTTPClient(_config(_handler(kind, 7, seen)))
    try:
        service = service_cls(http)
        first = await service.get_list_entries(id_cls(7), limit=1)
        second = await service.get_list_entries(id_cls(7), cursor=first.next_cursor)
        with pytest.raises(ValueError, match="not for"):
            await service.get_list_entries(id_cls(8), cursor=first.next_cursor)
    finally:
        await http.close()
    assert [(e.entity_id, e.entity_type) for e in [*first.data, *second.data]] == [
        (7, entity_type),
        (7, entity_type),
    ]
