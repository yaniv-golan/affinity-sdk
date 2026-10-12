from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig
from affinity.models.entities import FieldCreate, FieldValueCreate
from affinity.models.secondary import (
    InteractionCreate,
    InteractionUpdate,
)
from affinity.services.rate_limits import AsyncRateLimitService
from affinity.services.v1_only import (
    AsyncAuthService,
    AsyncFieldService,
    AsyncFieldValueService,
    AsyncInteractionService,
    AsyncNoteService,
    AsyncRelationshipStrengthService,
)
from affinity.types import (
    EntityType,
    FieldId,
    FieldValueId,
    FieldValueType,
    InteractionId,
    InteractionType,
    NoteId,
    PersonId,
    UserId,
)


def _async_client(handler: httpx.MockTransport) -> AsyncHTTPClient:
    return AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=handler,
        )
    )


@pytest.mark.asyncio
async def test_async_note_service_crud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if request.method == "GET" and url == httpx.URL("https://v1.example/notes"):
            return httpx.Response(
                200,
                json={
                    "notes": [
                        {
                            "id": 1,
                            "creatorId": 2,
                            "content": "Hello",
                            "type": 0,
                            "personIds": [3],
                            "organizationIds": [],
                            "opportunityIds": [],
                            "createdAt": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "next_page_token": "next",
                },
                request=request,
            )
        if request.method == "GET" and url == httpx.URL("https://v1.example/notes/1"):
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "creatorId": 2,
                    "content": "Hello",
                    "type": 0,
                    "personIds": [3],
                    "organizationIds": [],
                    "opportunityIds": [],
                    "createdAt": "2024-01-01T00:00:00Z",
                },
                request=request,
            )
        if request.method == "POST" and url == httpx.URL("https://v1.example/notes"):
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "creatorId": 2,
                    "content": "Created",
                    "type": 0,
                    "personIds": [],
                    "organizationIds": [],
                    "opportunityIds": [],
                    "createdAt": "2024-01-02T00:00:00Z",
                },
                request=request,
            )
        if request.method == "PUT" and url == httpx.URL("https://v1.example/notes/1"):
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "creatorId": 2,
                    "content": "Updated",
                    "type": 0,
                    "personIds": [],
                    "organizationIds": [],
                    "opportunityIds": [],
                    "createdAt": "2024-01-01T00:00:00Z",
                },
                request=request,
            )
        if request.method == "DELETE" and url == httpx.URL("https://v1.example/notes/1"):
            return httpx.Response(200, json={"success": True}, request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = _async_client(httpx.MockTransport(handler))
    async with client:
        svc = AsyncNoteService(client)
        page = await svc.list()
        assert page.data[0].id == NoteId(1)
        assert page.next_page_token == "next"


@pytest.mark.asyncio
async def test_async_interaction_service_crud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        interaction = {
            "id": 6,
            "type": int(InteractionType.MEETING),
            "date": "2024-01-01T00:00:00Z",
        }
        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v1.example/interactions"
        ):
            return httpx.Response(
                200,
                json={"events": [interaction], "next_page_token": "next"},
                request=request,
            )
        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v1.example/interactions/6"
        ):
            return httpx.Response(200, json=interaction, request=request)
        if request.method == "POST" and url == httpx.URL("https://v1.example/interactions"):
            return httpx.Response(200, json=interaction, request=request)
        if request.method == "PUT" and url.copy_with(query=None) == httpx.URL(
            "https://v1.example/interactions/6"
        ):
            updated = dict(interaction)
            updated["date"] = "2024-01-02T00:00:00Z"
            return httpx.Response(200, json=updated, request=request)
        if request.method == "DELETE" and url.copy_with(query=None) == httpx.URL(
            "https://v1.example/interactions/6"
        ):
            return httpx.Response(200, json={"success": True}, request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = _async_client(httpx.MockTransport(handler))
    async with client:
        svc = AsyncInteractionService(client)
        page = await svc.list(
            type=InteractionType.MEETING,
            person_id=PersonId(1),
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert page.data[0].id == InteractionId(6)
        assert page.next_page_token == "next"
        item = await svc.get(InteractionId(6), InteractionType.MEETING)
        assert item.type == InteractionType.MEETING
        created = await svc.create(
            InteractionCreate(
                type=InteractionType.MEETING,
                person_ids=[PersonId(1)],
                content="Meeting notes",
                date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        )
        assert created.id == InteractionId(6)
        updated = await svc.update(
            InteractionId(6),
            InteractionType.MEETING,
            InteractionUpdate(
                content="Updated",
                date=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
        )
        assert updated.date.isoformat().startswith("2024-01-02")
        assert await svc.delete(InteractionId(6), InteractionType.MEETING) is True


@pytest.mark.asyncio
async def test_async_field_service_crud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        field = {
            "id": "field-123",
            "name": "Stage",
            "valueType": "text",
            "entityType": int(EntityType.OPPORTUNITY),
        }
        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v1.example/fields"
        ):
            return httpx.Response(200, json={"data": [field]}, request=request)
        if request.method == "POST" and url == httpx.URL("https://v1.example/fields"):
            return httpx.Response(200, json=field, request=request)
        if request.method == "DELETE" and url == httpx.URL("https://v1.example/fields/123"):
            return httpx.Response(200, json={"success": True}, request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = _async_client(httpx.MockTransport(handler))
    async with client:
        svc = AsyncFieldService(client)
        items = await svc.list(entity_type=EntityType.OPPORTUNITY)
        assert items[0].id == FieldId("field-123")
        created = await svc.create(
            FieldCreate(
                name="Stage",
                entity_type=EntityType.OPPORTUNITY,
                value_type=FieldValueType.TEXT,
            )
        )
        assert created.name == "Stage"
        assert await svc.delete(FieldId("field-123")) is True


@pytest.mark.asyncio
async def test_async_field_value_service_crud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        value = {
            "id": 11,
            "field_id": "field-123",
            "entity_id": 5,
            "value": "Active",
        }
        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v1.example/field-values"
        ):
            return httpx.Response(200, json={"data": [value]}, request=request)
        if request.method == "POST" and url == httpx.URL("https://v1.example/field-values"):
            return httpx.Response(200, json=value, request=request)
        if request.method == "PUT" and url == httpx.URL("https://v1.example/field-values/11"):
            updated = dict(value)
            updated["value"] = "Updated"
            return httpx.Response(200, json=updated, request=request)
        if request.method == "DELETE" and url == httpx.URL("https://v1.example/field-values/11"):
            return httpx.Response(200, json={"success": True}, request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = _async_client(httpx.MockTransport(handler))
    async with client:
        svc = AsyncFieldValueService(client)
        items = await svc.list(person_id=PersonId(5))
        assert items[0].id == FieldValueId(11)
        created = await svc.create(
            FieldValueCreate(field_id=FieldId("field-123"), entity_id=5, value="Active")
        )
        assert created.value == "Active"
        updated = await svc.update(FieldValueId(11), "Updated")
        assert updated.value == "Updated"
        assert await svc.delete(FieldValueId(11)) is True


@pytest.mark.asyncio
async def test_async_relationship_strength_service_get() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.copy_with(query=None) == httpx.URL(
            "https://v1.example/relationships-strengths"
        ):
            return httpx.Response(
                200,
                json={"data": [{"internalId": 1, "externalId": 2, "strength": 0.5}]},
                request=request,
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = _async_client(httpx.MockTransport(handler))
    async with client:
        svc = AsyncRelationshipStrengthService(client)
        items = await svc.get(PersonId(2), internal_id=UserId(1))
        assert items[0].strength == 0.5


@pytest.mark.asyncio
async def test_async_auth_service_whoami() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url == httpx.URL(
            "https://v2.example/v2/auth/whoami"
        ):
            return httpx.Response(
                200,
                json={
                    "tenant": {"id": 1, "name": "Acme", "subdomain": "acme"},
                    "user": {
                        "id": 2,
                        "firstName": "Ada",
                        "lastName": "Lovelace",
                        "emailAddress": "a@b.com",
                    },
                    "grant": {
                        "type": "personal",
                        "scopes": ["read"],
                        "createdAt": "2024-01-01T00:00:00Z",
                    },
                },
                request=request,
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = _async_client(httpx.MockTransport(handler))
    async with client:
        svc = AsyncAuthService(client)
        whoami = await svc.whoami()
        assert whoami.tenant.name == "Acme"
        assert whoami.user.email == "a@b.com"


@pytest.mark.asyncio
async def test_async_rate_limit_service_refresh() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url == httpx.URL("https://v2.example/v2/rate-limit"):
            return httpx.Response(
                200,
                json={
                    "orgPerMonth": {"limit": 100, "remaining": 50, "reset": 10, "used": 50},
                    "callerPerMinute": {"limit": 10, "remaining": 5, "reset": 5, "used": 5},
                },
                request=request,
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = _async_client(httpx.MockTransport(handler))
    async with client:
        svc = AsyncRateLimitService(client)
        snapshot = await svc.refresh()
        assert snapshot.source == "endpoint"
        assert snapshot.org_monthly.limit == 100
