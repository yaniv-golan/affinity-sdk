from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.models.entities import ListCreate, ListPermission
from affinity.models.types import (
    CompanyId,
    FieldType,
    ListEntryId,
    ListId,
    ListRole,
    ListType,
    OpportunityId,
    PersonId,
    UserId,
)
from affinity.services.lists import AsyncListEntryService, AsyncListService, ListService


def test_list_service_list_all_get_fields_and_create_entry_helpers() -> None:
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    calls: dict[str, int] = {"fields": 0, "lists": 0}
    person_entries_calls = 0
    field_values_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/lists"
        ):
            if url.params.get("cursor") is not None:
                return httpx.Response(
                    200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
                )
            calls["lists"] += 1
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 10,
                            "name": "L",
                            "type": 0,
                            "public": True,
                            "ownerId": 1,
                            "creatorId": 1,
                            "listSize": 1,
                        }
                    ],
                    "pagination": {"nextUrl": "https://v2.example/v2/lists?cursor=abc"},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL("https://v2.example/v2/lists?cursor=abc"):
            return httpx.Response(
                200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
            )

        if request.method == "GET" and url == httpx.URL("https://v1.example/lists/10"):
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "name": "L",
                    "type": 0,
                    "public": True,
                    "owner_id": 1,
                    "creator_id": 1,
                    "list_size": 1,
                    "fields": [],
                },
                request=request,
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/lists/10/fields"
        ):
            calls["fields"] += 1
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "field-1", "name": "F", "valueType": 2, "allowsMultiple": False},
                    ]
                },
                request=request,
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries"
        ):
            if url.params.get("cursor") is not None:
                return httpx.Response(
                    200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
                )
            filter_text = url.params.get("filter")
            assert filter_text is None
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "listId": 10,
                            "createdAt": created_at,
                            "type": "person",
                            "entity": {"id": 1, "type": "external"},
                            "fields": {},
                        }
                    ],
                    "pagination": {
                        "nextUrl": "https://v2.example/v2/lists/10/list-entries?cursor=abc"
                    },
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries?cursor=abc"
        ):
            return httpx.Response(
                200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries/1"
        ):
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "listId": 10,
                    "createdAt": created_at,
                    "type": "person",
                    "entity": {"id": 1, "type": "external"},
                    "fields": {},
                },
                request=request,
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/lists/10/saved-views/1/list-entries"
        ):
            limit = url.params.get("limit")
            if limit is not None:
                assert limit == "1"
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/persons/1/list-entries"
        ):
            nonlocal person_entries_calls
            person_entries_calls += 1
            if person_entries_calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": 5, "listId": 999, "createdAt": created_at, "fields": {}}],
                        "pagination": {"nextUrl": None},
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 5, "listId": 10, "createdAt": created_at, "fields": {}}],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )

        if request.method == "POST" and url == httpx.URL(
            "https://v1.example/lists/10/list-entries"
        ):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload in ({"entity_id": 1}, {"entity_id": 1, "creator_id": 123})
            return httpx.Response(
                200,
                json={"id": 5, "listId": 10, "createdAt": created_at, "entityId": 1, "fields": {}},
                request=request,
            )

        if request.method == "DELETE" and url == httpx.URL(
            "https://v1.example/lists/10/list-entries/5"
        ):
            return httpx.Response(200, json={"success": True}, request=request)

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries/5/fields"
        ):
            nonlocal field_values_calls
            field_values_calls += 1
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-1",
                            "name": "Owner",
                            "type": "list",
                            "value": {
                                "data": {"id": 42, "firstName": "Jane", "lastName": "Doe"},
                                "type": "person",
                            },
                        }
                    ],
                    "pagination": {"prevUrl": None, "nextUrl": None},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries/5/fields/field-1"
        ):
            return httpx.Response(
                200,
                json={
                    "id": "field-1",
                    "name": "Owner",
                    "type": "list",
                    "enrichmentSource": None,
                    "value": {
                        "data": {"id": 42, "firstName": "Jane", "lastName": "Doe"},
                        "type": "person",
                    },
                },
                request=request,
            )

        if request.method == "POST" and url == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries/5/fields/field-1"
        ):
            payload = json.loads(request.content.decode("utf-8"))
            # V2 API expects nested value structure: {"value": {"type": "...", "data": ...}}
            assert payload == {"value": {"type": "text", "data": "y"}}
            return httpx.Response(200, json={"field-1": "y"}, request=request)

        if request.method == "PATCH" and url == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries/5/fields"
        ):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {
                "operation": "update-fields",
                "updates": [{"id": "field-1", "value": {"type": "text", "data": "y"}}],
            }
            return httpx.Response(
                200, json={"results": [{"fieldId": "field-1", "success": True}]}, request=request
            )

        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            enable_cache=True,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        svc = ListService(http)
        page = svc.list()
        assert page.data[0].type == ListType.PERSON
        assert [lst.id for lst in list(svc.all())] == [ListId(10)]
        assert svc.get(ListId(10)).id == ListId(10)

        _ = svc.get_fields(ListId(10))
        _ = svc.get_fields(ListId(10))
        assert calls["fields"] == 1

        entries = svc.entries(ListId(10))
        _ = entries.list(filter="  ")
        assert [e.id for e in list(entries.all())] == [ListEntryId(1)]
        assert entries.get(ListEntryId(1)).id == ListEntryId(1)
        assert entries.from_saved_view(1).data == []
        assert entries.from_saved_view(1, limit=1).data == []

        ensured = entries.ensure_person(PersonId(1))
        assert ensured.id == ListEntryId(5)
        ensured_with_creator = entries.ensure_person(PersonId(1), creator_id=123)
        assert ensured_with_creator.id == ListEntryId(5)
        assert entries.find_person(PersonId(1)) is not None

        assert entries.delete(ListEntryId(5)) is True

        fv = entries.get_field_values(ListEntryId(5))
        assert fv.requested is True
        # Full field object preserved in .data
        field_obj = fv.get("field-1")
        assert field_obj is not None
        assert field_obj["id"] == "field-1"
        assert field_obj["value"]["type"] == "person"
        # get_value() unwraps to raw data
        assert fv.get_value("field-1") == {"id": 42, "firstName": "Jane", "lastName": "Doe"}
        # Second call still works (cache hit path)
        assert entries.get_field_values(ListEntryId(5)).requested is True
        assert entries.get_field_value(ListEntryId(5), "field-1") == {
            "id": 42,
            "firstName": "Jane",
            "lastName": "Doe",
        }
        assert entries.update_field_value(ListEntryId(5), "field-1", "y").requested is True
        assert entries.batch_update_fields(ListEntryId(5), {"field-1": "y"}).all_successful is True
    finally:
        http.close()


def test_list_service_resolve_and_resolve_all_case_insensitive_and_cache() -> None:
    calls: dict[str, int] = {"lists": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url == httpx.URL("https://v2.example/v2/lists"):
            calls["lists"] += 1
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "name": "Pipeline", "type": 8, "public": True, "ownerId": 1},
                        {"id": 2, "name": "Pipeline", "type": 0, "public": True, "ownerId": 1},
                        {"id": 3, "name": "Other", "type": 0, "public": True, "ownerId": 1},
                    ],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        svc = ListService(http)

        resolved = svc.resolve(name="pipeline")
        assert resolved is not None
        assert resolved.id == ListId(1)

        # Cached: no additional GET /lists call.
        resolved_again = svc.resolve(name="pipeline")
        assert resolved_again is not None
        assert resolved_again.id == ListId(1)
        assert calls["lists"] == 1

        matches = svc.resolve_all(name="PIPELINE")
        assert [m.id for m in matches] == [ListId(1), ListId(2)]
        assert calls["lists"] == 2

        filtered = svc.resolve(name="pipeline", list_type=ListType.OPPORTUNITY)
        assert filtered is not None
        assert filtered.id == ListId(1)
        filtered_again = svc.resolve(name="pipeline", list_type=ListType.OPPORTUNITY)
        assert filtered_again is not None
        assert filtered_again.id == ListId(1)
        assert calls["lists"] == 3

        not_found = svc.resolve(name="missing")
        assert not_found is None
        not_found_again = svc.resolve(name="missing")
        assert not_found_again is None
        # Cached negative result should also avoid further calls.
        assert calls["lists"] == 4
    finally:
        http.close()


@pytest.mark.asyncio
async def test_async_list_service_and_list_entry_membership() -> None:
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url

        if request.method == "GET" and url == httpx.URL("https://v2.example/v2/lists"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 10,
                            "name": "L",
                            "type": 0,
                            "public": True,
                            "ownerId": 1,
                            "listSize": 1,
                        }
                    ],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL("https://v1.example/lists/10"):
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "name": "L",
                    "type": 0,
                    "public": True,
                    "owner_id": 1,
                    "list_size": 1,
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/persons/1/list-entries"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "listId": 10, "createdAt": created_at, "fields": {}}],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/companies/2/list-entries"
        ):
            return httpx.Response(
                200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
            )

        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        lists = AsyncListService(client)
        assert (await lists.list()).data[0].id == ListId(10)
        assert (await lists.get(ListId(10))).name == "L"

        entries = AsyncListEntryService(client, ListId(10))
        assert await entries.find_person(PersonId(1)) is not None
        assert await entries.find_company(2) is None
    finally:
        await client.close()


def test_list_service_create_saved_views_and_list_entry_params() -> None:
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    invalidated_prefixes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url

        if request.method == "POST" and url == httpx.URL("https://v1.example/lists"):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {
                "name": "New List",
                "type": 0,
                "is_public": True,
                "owner_id": 1,
                "additional_permissions": [{"internal_person_id": 2, "role_id": 1}],
            }
            return httpx.Response(
                200,
                json={
                    "id": 11,
                    "name": "New List",
                    "type": 0,
                    "public": True,
                    "ownerId": 1,
                    "creatorId": 1,
                    "listSize": 0,
                    "fields": [],
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/11/saved-views"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "name": "SV", "listId": 11, "fieldIds": []}],
                    "pagination": {},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/11/saved-views/1"
        ):
            return httpx.Response(
                200,
                json={"id": 1, "name": "SV", "listId": 11, "fieldIds": []},
                request=request,
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/lists/11/list-entries"
        ):
            assert url.params.get_list("fieldIds") == ["field-1"]
            assert url.params.get_list("fieldTypes") == ["global"]
            # NOTE: filter is NOT sent to API - it's applied client-side
            limit = url.params.get("limit")
            if limit is not None:
                assert limit == "1"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "listId": 11,
                            "createdAt": created_at,
                            "type": "person",
                            "entity": {"id": 1, "type": "external"},
                            "fields": {},
                        }
                    ],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/companies/2/list-entries"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 2, "listId": 11, "createdAt": created_at, "fields": {}}],
                    "pagination": {},
                },
                request=request,
            )

        if request.method == "POST" and url == httpx.URL(
            "https://v1.example/lists/11/list-entries"
        ):
            return httpx.Response(
                200,
                json={"id": 99, "listId": 11, "createdAt": created_at, "entityId": 3, "fields": {}},
                request=request,
            )

        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            enable_cache=True,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert http.cache is not None

        original_invalidate = http.cache.invalidate_prefix

        def recording_invalidate(prefix: str) -> None:
            invalidated_prefixes.append(prefix)
            original_invalidate(prefix)

        http.cache.invalidate_prefix = recording_invalidate  # type: ignore[method-assign]

        svc = ListService(http)
        created = svc.create(
            ListCreate(
                name="New List",
                type=ListType.PERSON,
                is_public=True,
                owner_id=UserId(1),
                additional_permissions=[
                    ListPermission(internal_person_id=UserId(2), role_id=ListRole.BASIC)
                ],
            )
        )
        assert created.id == ListId(11)
        assert invalidated_prefixes == ["list"]

        saved = svc.get_saved_views(ListId(11))
        assert saved.data[0].id == 1
        assert svc.get_saved_view(ListId(11), 1).name == "SV"

        entries = svc.entries(ListId(11))
        # Test list() with field_ids, field_types, and limit
        _ = entries.list(field_ids=["field-1"], field_types=[FieldType.GLOBAL], limit=1)
        assert [
            e.id for e in list(entries.iter(field_ids=["field-1"], field_types=[FieldType.GLOBAL]))
        ] == [ListEntryId(1)]
        existing = entries.ensure_company(2)
        assert existing.id == ListEntryId(2)

        created_entry = entries.add_opportunity(3)
        assert created_entry.id == ListEntryId(99)
    finally:
        http.close()


@pytest.mark.asyncio
async def test_async_list_service_resolve_and_resolve_all_case_insensitive_and_cache() -> None:
    calls: dict[str, int] = {"lists": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url == httpx.URL("https://v2.example/v2/lists"):
            calls["lists"] += 1
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "name": "Pipeline", "type": 8, "public": True, "ownerId": 1},
                        {"id": 2, "name": "Pipeline", "type": 0, "public": True, "ownerId": 1},
                    ],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        svc = AsyncListService(http)

        resolved = await svc.resolve(name="pipeline")
        assert resolved is not None
        assert resolved.id == ListId(1)

        resolved_again = await svc.resolve(name="pipeline")
        assert resolved_again is not None
        assert resolved_again.id == ListId(1)
        assert calls["lists"] == 1

        matches = await svc.resolve_all(name="PIPELINE")
        assert [m.id for m in matches] == [ListId(1), ListId(2)]

        filtered = await svc.resolve(name="pipeline", list_type=ListType.OPPORTUNITY)
        assert filtered is not None
        assert filtered.id == ListId(1)
    finally:
        await http.close()


def test_list_create_does_not_invalidate_when_cache_disabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url == httpx.URL("https://v1.example/lists"):
            return httpx.Response(
                200,
                json={
                    "id": 12,
                    "name": "X",
                    "type": 0,
                    "public": True,
                    "ownerId": 1,
                    "listSize": 0,
                },
                request=request,
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            enable_cache=False,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        svc = ListService(http)
        created = svc.create(ListCreate(name="X", type=ListType.PERSON, is_public=True))
        assert created.id == ListId(12)
    finally:
        http.close()


@pytest.mark.asyncio
async def test_async_list_service_and_entry_list_all_iter_with_pagination() -> None:
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url

        if request.method == "GET" and url == httpx.URL("https://v2.example/v2/lists"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 10,
                            "name": "L",
                            "type": 0,
                            "public": True,
                            "ownerId": 1,
                            "listSize": 1,
                        }
                    ],
                    "pagination": {"nextUrl": "https://v2.example/v2/lists?cursor=abc"},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL("https://v2.example/v2/lists?cursor=abc"):
            return httpx.Response(
                200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/lists/10/list-entries"
        ):
            if url.params.get("cursor") == "xyz":
                return httpx.Response(
                    200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
                )
            if url.params.get("cursor") is not None:
                raise AssertionError("unexpected cursor value")
            field_ids = url.params.get_list("fieldIds")
            if field_ids:
                assert field_ids == ["field-1"]
            field_types = url.params.get_list("fieldTypes")
            if field_types:
                assert field_types == ["global"]
            filter_text = url.params.get("filter")
            if filter_text is not None:
                assert filter_text == "x"
            limit = url.params.get("limit")
            if limit is not None:
                assert limit == "1"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "listId": 10,
                            "createdAt": created_at,
                            "type": "person",
                            "entity": {"id": 1, "type": "external"},
                            "fields": {},
                        }
                    ],
                    "pagination": {
                        "nextUrl": "https://v2.example/v2/lists/10/list-entries?cursor=xyz"
                    },
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/persons/1/list-entries"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 2, "listId": 10, "createdAt": created_at, "fields": {}}],
                    "pagination": {
                        "nextUrl": "https://v2.example/v2/persons/1/list-entries?cursor=abc"
                    },
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/persons/1/list-entries?cursor=abc"
        ):
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        lists = AsyncListService(client)
        assert [lst.id async for lst in lists.all()] == [ListId(10)]

        entries = AsyncListEntryService(client, ListId(10))
        # Test with whitespace-only filter (treated as no filter)
        _ = await entries.list(
            field_ids=["field-1"], field_types=[FieldType.GLOBAL], filter=" ", limit=1
        )
        # Test with no filter
        _ = await entries.list(field_ids=["field-1"], field_types=[FieldType.GLOBAL], limit=1)
        _ = await entries.list()
        assert [
            e.id
            async for e in entries.all(
                field_ids=["field-1"], field_types=[FieldType.GLOBAL], filter=" "
            )
        ] == [ListEntryId(1)]
        assert [
            e.id
            async for e in entries.iter(
                field_ids=["field-1"], field_types=[FieldType.GLOBAL], filter=" "
            )
        ] == [ListEntryId(1)]
        assert (await entries.find_person(PersonId(1))) is not None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_list_service_create_fields_and_entry_write_ops() -> None:
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    invalidated_prefixes: list[str] = []
    calls: dict[str, int] = {"fields": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url

        if request.method == "POST" and url == httpx.URL("https://v1.example/lists"):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {
                "name": "New List",
                "type": 0,
                "is_public": True,
                "owner_id": 1,
                "additional_permissions": [{"internal_person_id": 2, "role_id": 1}],
            }
            return httpx.Response(
                200,
                json={
                    "id": 11,
                    "name": "New List",
                    "type": 0,
                    "public": True,
                    "ownerId": 1,
                    "creatorId": 1,
                    "listSize": 0,
                    "fields": [],
                },
                request=request,
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/lists/11/fields"
        ):
            assert "fieldTypes" not in url.params
            calls["fields"] += 1
            return httpx.Response(
                200,
                json={"data": [{"id": "field-1", "name": "F", "valueType": 2}]},
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/persons/1/list-entries"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 5, "listId": 11, "createdAt": created_at, "fields": {}}],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/companies/2/list-entries"
        ):
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        if request.method == "POST" and url == httpx.URL(
            "https://v1.example/lists/11/list-entries"
        ):
            payload = json.loads(request.content.decode("utf-8"))
            if payload["entity_id"] == 2:
                return httpx.Response(
                    200,
                    json={
                        "id": 99,
                        "listId": 11,
                        "createdAt": created_at,
                        "entityId": 2,
                        "fields": {},
                    },
                    request=request,
                )
            if payload["entity_id"] == 3:
                return httpx.Response(
                    200,
                    json={
                        "id": 100,
                        "listId": 11,
                        "createdAt": created_at,
                        "entityId": 3,
                        "fields": {},
                    },
                    request=request,
                )
            return httpx.Response(400, json={"message": "unexpected"}, request=request)

        if request.method == "DELETE" and url == httpx.URL(
            "https://v1.example/lists/11/list-entries/99"
        ):
            return httpx.Response(200, json={"success": True}, request=request)

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/11/list-entries/99/fields"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-1",
                            "name": "Status",
                            "type": "list",
                            "value": {"data": "x", "type": "text"},
                        }
                    ],
                    "pagination": {"prevUrl": None, "nextUrl": None},
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/lists/11/list-entries/99/fields/field-1"
        ):
            return httpx.Response(
                200,
                json={
                    "id": "field-1",
                    "name": "Status",
                    "type": "list",
                    "enrichmentSource": None,
                    "value": {"data": "x", "type": "text"},
                },
                request=request,
            )

        if request.method == "POST" and url == httpx.URL(
            "https://v2.example/v2/lists/11/list-entries/99/fields/field-1"
        ):
            payload = json.loads(request.content.decode("utf-8"))
            # V2 requires the {"type", "data"} wrapper (async used to send the bare value)
            assert payload == {"value": {"type": "text", "data": "y"}}
            return httpx.Response(200, json={"field-1": "y"}, request=request)

        if request.method == "PATCH" and url == httpx.URL(
            "https://v2.example/v2/lists/11/list-entries/99/fields"
        ):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {
                "operation": "update-fields",
                "updates": [{"id": "field-1", "value": {"type": "text", "data": "y"}}],
            }
            return httpx.Response(
                200,
                json={"results": [{"fieldId": "field-1", "success": True}]},
                request=request,
            )

        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            enable_cache=True,
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert client.cache is not None
        original_invalidate = client.cache.invalidate_prefix

        def recording_invalidate(prefix: str) -> None:
            invalidated_prefixes.append(prefix)
            original_invalidate(prefix)

        client.cache.invalidate_prefix = recording_invalidate  # type: ignore[method-assign]

        svc = AsyncListService(client)
        created = await svc.create(
            ListCreate(
                name="New List",
                type=ListType.PERSON,
                is_public=True,
                owner_id=UserId(1),
                additional_permissions=[
                    ListPermission(internal_person_id=UserId(2), role_id=ListRole.BASIC)
                ],
            )
        )
        assert created.id == ListId(11)
        assert invalidated_prefixes == ["list"]

        _ = await svc.get_fields(ListId(11))
        _ = await svc.get_fields(ListId(11))
        assert calls["fields"] == 1

        entries = svc.entries(ListId(11))
        ensured = await entries.ensure_person(PersonId(1))
        assert ensured.id == ListEntryId(5)

        created_entry = await entries.ensure_company(CompanyId(2))
        assert created_entry.id == ListEntryId(99)

        created_opportunity = await entries.add_opportunity(OpportunityId(3))
        assert created_opportunity.id == ListEntryId(100)

        assert await entries.delete(ListEntryId(99)) is True
        fv = await entries.get_field_values(ListEntryId(99))
        assert fv.requested is True
        assert fv.get_value("field-1") == "x"
        assert await entries.get_field_value(ListEntryId(99), "field-1") == "x"
        assert (await entries.update_field_value(ListEntryId(99), "field-1", "y")).requested is True
        assert (
            await entries.batch_update_fields(ListEntryId(99), {"field-1": "y"})
        ).all_successful is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_list_service_get_size() -> None:
    """Test AsyncListService.get_size() returns correct value and uses cache."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        url = request.url

        # V1 API endpoint returns list with list_size
        if request.method == "GET" and "/lists/100" in str(url):
            call_count += 1
            return httpx.Response(
                200,
                json={
                    "id": 100,
                    "name": "Dealflow",
                    "type": 8,
                    "public": False,
                    "owner_id": 1,
                    "list_size": 9346,
                },
                request=request,
            )

        return httpx.Response(404, request=request)

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        service = AsyncListService(client)

        # First call
        size1 = await service.get_size(ListId(100))
        assert size1 == 9346
        assert call_count == 1

        # Second call should use cache
        size2 = await service.get_size(ListId(100))
        assert size2 == 9346
        assert call_count == 1  # No additional API call
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_list_service_get_size_force_bypasses_cache() -> None:
    """Test AsyncListService.get_size(force=True) bypasses cache."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        url = request.url

        # V1 API endpoint returns list with list_size
        if request.method == "GET" and "/lists/100" in str(url):
            call_count += 1
            return httpx.Response(
                200,
                json={
                    "id": 100,
                    "name": "Dealflow",
                    "type": 8,
                    "public": False,
                    "owner_id": 1,
                    "list_size": 9000 + call_count,  # Different size each call
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        service = AsyncListService(client)

        # First call
        size1 = await service.get_size(ListId(100))
        assert size1 == 9001
        assert call_count == 1

        # Second call without force - uses cache
        size2 = await service.get_size(ListId(100))
        assert size2 == 9001
        assert call_count == 1  # No additional API call

        # Third call with force=True - bypasses cache
        size3 = await service.get_size(ListId(100), force=True)
        assert size3 == 9002  # Gets new value
        assert call_count == 2  # New API call made

        # Fourth call without force - uses newly cached value
        size4 = await service.get_size(ListId(100))
        assert size4 == 9002  # Uses cached value from force call
        assert call_count == 2  # No additional API call
    finally:
        await client.close()


def _capture_field_write_bodies() -> tuple[list[dict], httpx.MockTransport]:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if request.method == "PATCH":
            return httpx.Response(200, json={"results": []}, request=request)
        return httpx.Response(200, json={"data": []}, request=request)

    return bodies, httpx.MockTransport(handler)


def test_field_writes_infer_datetime_type_and_serialize_iso() -> None:
    """Docstrings promise datetime -> 'datetime'; it used to send 'text' with an unserializable
    datetime object (TypeError before the request was even made)."""
    from affinity.services.lists import ListEntryService

    bodies, transport = _capture_field_write_bodies()
    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=transport,
        )
    )
    try:
        entries = ListEntryService(http, ListId(10))
        when = datetime(2024, 4, 1, 15, 30, tzinfo=timezone.utc)
        entries.update_field_value(ListEntryId(5), "field-1", when)
        entries.batch_update_fields(ListEntryId(5), {"field-1": when, "field-2": 3, "field-3": "x"})
    finally:
        http.close()

    assert bodies[0] == {"value": {"type": "datetime", "data": "2024-04-01T15:30:00+00:00"}}
    assert bodies[1]["updates"] == [
        {"id": "field-1", "value": {"type": "datetime", "data": "2024-04-01T15:30:00+00:00"}},
        {"id": "field-2", "value": {"type": "number", "data": 3}},
        {"id": "field-3", "value": {"type": "text", "data": "x"}},
    ]


@pytest.mark.asyncio
async def test_async_field_writes_infer_datetime_type_and_serialize_iso() -> None:
    from affinity.services.lists import AsyncListEntryService

    bodies, transport = _capture_field_write_bodies()
    http = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=transport,
        )
    )
    try:
        entries = AsyncListEntryService(http, ListId(10))
        when = datetime(2024, 4, 1, 15, 30, tzinfo=timezone.utc)
        await entries.update_field_value(ListEntryId(5), "field-1", when)
        await entries.batch_update_fields(ListEntryId(5), {"field-1": when})
    finally:
        await http.close()

    assert bodies[0] == {"value": {"type": "datetime", "data": "2024-04-01T15:30:00+00:00"}}
    assert bodies[1]["updates"] == [
        {"id": "field-1", "value": {"type": "datetime", "data": "2024-04-01T15:30:00+00:00"}}
    ]


def test_field_write_date_object_lands_on_same_pacific_date() -> None:
    """A `date` must be sent as a date-time (spec: DateValue.data is format date-time).

    Midnight UTC would be the previous day in Pacific time, and Affinity stores the Pacific
    calendar date, so the SDK sends noon UTC, which is the same Pacific date year-round.
    """
    from datetime import date

    from affinity.services.lists import _field_value_payload

    assert _field_value_payload(date(2024, 4, 1)) == {
        "type": "datetime",
        "data": "2024-04-01T12:00:00Z",
    }
    assert _field_value_payload(date(2024, 1, 15))["data"] == "2024-01-15T12:00:00Z"
