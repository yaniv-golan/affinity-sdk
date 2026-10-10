from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from affinity import AffinityError
from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.models.entities import FieldCreate, FieldValueCreate
from affinity.models.secondary import (
    InteractionCreate,
    InteractionUpdate,
    NoteCreate,
    NoteUpdate,
    ReminderCreate,
    ReminderUpdate,
    WebhookCreate,
    WebhookUpdate,
)
from affinity.models.types import (
    CompanyId,
    EnrichedFieldId,
    EntityType,
    FieldId,
    FieldValueChangeAction,
    FieldValueId,
    FieldValueType,
    FileId,
    InteractionDirection,
    InteractionType,
    ListId,
    NoteId,
    PersonId,
    ReminderIdType,
    ReminderStatus,
    ReminderType,
    UserId,
    WebhookId,
)
from affinity.services.rate_limits import RateLimitService
from affinity.services.v1_only import (
    AsyncFieldService,
    AsyncFieldValueService,
    AuthService,
    EntityFileService,
    FieldService,
    FieldValueChangesService,
    FieldValueService,
    InteractionService,
    NoteService,
    RelationshipStrengthService,
    ReminderService,
    WebhookService,
)


def test_v1_only_services_end_to_end_smoke_and_branch_coverage(tmp_path: Path) -> None:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    iso = now.isoformat()
    created_at = iso
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path

        # Notes
        if request.method == "GET" and path == "/notes":
            return httpx.Response(
                200,
                json={"notes": [{"id": 1, "creatorId": 1, "createdAt": created_at}]},
                request=request,
            )
        if request.method == "GET" and path == "/notes/1":
            return httpx.Response(
                200,
                json={"id": 1, "creatorId": 1, "createdAt": created_at, "content": "x"},
                request=request,
            )
        if request.method == "POST" and path == "/notes":
            seen["note_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "creatorId": 1,
                    "createdAt": created_at,
                    "content": "n",
                    "personIds": [1],
                },
                request=request,
            )
        if request.method == "PUT" and path == "/notes/2":
            seen["note_update"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 2, "creatorId": 1, "createdAt": created_at, "content": "u"},
                request=request,
            )
        if request.method == "DELETE" and path == "/notes/2":
            return httpx.Response(200, json={"success": True}, request=request)

        # Reminders
        if request.method == "GET" and path == "/reminders":
            return httpx.Response(
                200,
                json={
                    "reminders": [
                        {
                            "id": 1,
                            "type": 0,
                            "status": 1,
                            "dueDate": created_at,
                            "createdAt": created_at,
                        }
                    ]
                },
                request=request,
            )
        if request.method == "GET" and path == "/reminders/1":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "type": 0,
                    "status": 1,
                    "dueDate": created_at,
                    "createdAt": created_at,
                },
                request=request,
            )
        if request.method == "POST" and path == "/reminders":
            seen["reminder_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "type": 0,
                    "status": 1,
                    "dueDate": created_at,
                    "createdAt": created_at,
                },
                request=request,
            )
        if request.method == "PUT" and path == "/reminders/2":
            seen["reminder_update"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "type": 0,
                    "status": 1,
                    "dueDate": created_at,
                    "createdAt": created_at,
                },
                request=request,
            )
        if request.method == "DELETE" and path == "/reminders/2":
            return httpx.Response(200, json={"success": True}, request=request)

        # Webhooks
        if request.method == "GET" and path == "/webhook":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "webhookUrl": "https://x", "createdBy": 1, "subscriptions": []}
                    ]
                },
                request=request,
            )
        if request.method == "GET" and path == "/webhook/1":
            return httpx.Response(
                200,
                json={"id": 1, "webhookUrl": "https://x", "createdBy": 1, "subscriptions": []},
                request=request,
            )
        if request.method == "POST" and path == "/webhook/subscribe":
            seen["webhook_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 2, "webhookUrl": "https://y", "createdBy": 1, "subscriptions": []},
                request=request,
            )
        if request.method == "PUT" and path == "/webhook/2":
            seen["webhook_update"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "webhookUrl": "https://y",
                    "createdBy": 1,
                    "subscriptions": [],
                    "disabled": True,
                },
                request=request,
            )
        if request.method == "DELETE" and path == "/webhook/2":
            return httpx.Response(200, json={"success": True}, request=request)

        # Interactions
        if request.method == "GET" and path == "/interactions":
            interaction = {"id": 1, "type": 3, "date": created_at}
            if url.params.get("type") == str(int(InteractionType.EMAIL)):
                return httpx.Response(200, json={"emails": [interaction]}, request=request)
            if url.params.get("type") == str(int(InteractionType.MEETING)):
                return httpx.Response(
                    200, json={"events": interaction}, request=request
                )  # not a list -> []
            return httpx.Response(200, json={"interactions": [interaction]}, request=request)
        if request.method == "GET" and path == "/interactions/1":
            return httpx.Response(
                200,
                json={"id": 1, "type": int(InteractionType.EMAIL), "date": created_at},
                request=request,
            )
        if request.method == "POST" and path == "/interactions":
            seen["interaction_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 2, "type": int(InteractionType.EMAIL), "date": created_at},
                request=request,
            )
        if request.method == "PUT" and path == "/interactions/2":
            seen["interaction_update"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 2, "type": int(InteractionType.EMAIL), "date": created_at},
                request=request,
            )
        if request.method == "DELETE" and path == "/interactions/2":
            return httpx.Response(200, json={"success": True}, request=request)

        # Fields
        if request.method == "GET" and path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "field-1", "name": "F", "valueType": 2, "allowsMultiple": False},
                    ]
                },
                request=request,
            )
        if request.method == "POST" and path == "/fields":
            seen["field_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": "field-1", "name": "F", "valueType": 2, "allowsMultiple": True},
                request=request,
            )
        if request.method == "DELETE" and path == "/fields/1":
            return httpx.Response(200, json={"success": True}, request=request)

        # Field values
        if request.method == "GET" and path == "/field-values":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "fieldId": "field-1", "entityId": 1, "value": "x"},
                    ]
                },
                request=request,
            )
        if request.method == "POST" and path == "/field-values":
            seen["field_value_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 2, "fieldId": "field-1", "entityId": 1, "value": "y"},
                request=request,
            )
        if request.method == "PUT" and path == "/field-values/2":
            seen["field_value_update"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 2, "fieldId": "field-1", "entityId": 1, "value": "z"},
                request=request,
            )
        if request.method == "DELETE" and path == "/field-values/2":
            return httpx.Response(200, json={"success": True}, request=request)

        # Relationship strengths
        if request.method == "GET" and path == "/relationships-strengths":
            return httpx.Response(
                200,
                json={"data": [{"internalId": 2, "externalId": 1, "strength": 0.5}]},
                request=request,
            )

        # Entity files (list/get/upload helpers)
        if request.method == "GET" and path == "/entity-files":
            return httpx.Response(
                200,
                json={
                    "entityFiles": [
                        {
                            "id": 1,
                            "name": "a",
                            "size": 1,
                            "contentType": "x",
                            "uploaderId": 1,
                            "createdAt": created_at,
                        }
                    ]
                },
                request=request,
            )
        if request.method == "GET" and path == "/entity-files/1":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "a",
                    "size": 1,
                    "contentType": "x",
                    "uploaderId": 1,
                    "createdAt": created_at,
                },
                request=request,
            )
        if request.method == "POST" and path == "/entity-files":
            return httpx.Response(200, json={"success": True}, request=request)

        # Auth
        if request.method == "GET" and path == "/v2/auth/whoami":
            return httpx.Response(
                200,
                json={
                    "tenant": {"id": 1, "name": "T", "subdomain": "s"},
                    "user": {"id": 1, "firstName": "A", "lastName": "B", "email": "a@b"},
                    "grant": {"type": "api_key", "scope": "all", "createdAt": created_at},
                },
                request=request,
            )
        if request.method == "GET" and path == "/rate-limit":
            return httpx.Response(
                200,
                json={
                    "rate": {
                        "orgMonthly": {"limit": 1, "remaining": 1, "reset": 1, "used": 0},
                        "apiKeyPerMinute": {"limit": 1, "remaining": 1, "reset": 1, "used": 0},
                    }
                },
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
        http.cache.set("field_meta", {"x": 1})
        http.cache.set("list_10_fields", {"x": 1})
        http.cache.set("person_fields:", {"x": 1})
        http.cache.set("company_fields:_all_", {"x": 1})

        notes = NoteService(http)
        assert notes.list(person_id=PersonId(1)).data[0].id == NoteId(1)
        assert notes.get(NoteId(1)).content == "x"
        created = notes.create(
            NoteCreate(
                content="n",
                person_ids=[PersonId(1)],
                creator_id=UserId(1),
                created_at=now,
            )
        )
        assert created.id == NoteId(2)
        updated = notes.update(NoteId(2), NoteUpdate(content="u"))
        assert updated.content == "u"
        assert notes.delete(NoteId(2)) is True
        assert seen["note_create"]["created_at"] == iso
        assert seen["note_update"] == {"content": "u"}

        reminders = ReminderService(http)
        assert reminders.list(person_id=PersonId(1), status=ReminderStatus.ACTIVE).data[
            0
        ].id == ReminderIdType(1)
        assert reminders.get(ReminderIdType(1)).status == ReminderStatus.ACTIVE
        created_r = reminders.create(
            ReminderCreate(
                owner_id=UserId(1),
                type=ReminderType.ONE_TIME,
                content="c",
                due_date=now,
                person_id=PersonId(1),
            )
        )
        assert created_r.id == ReminderIdType(2)
        _ = reminders.update(
            ReminderIdType(2), ReminderUpdate(owner_id=UserId(2), is_completed=True)
        )
        assert reminders.delete(ReminderIdType(2)) is True
        assert seen["reminder_create"]["owner_id"] == 1
        assert seen["reminder_update"]["is_completed"] is True

        webhooks = WebhookService(http)
        assert webhooks.list()[0].id == WebhookId(1)
        assert webhooks.get(WebhookId(1)).webhook_url == "https://x"
        created_w = webhooks.create(WebhookCreate(webhook_url="https://y"))
        assert created_w.id == WebhookId(2)
        _ = webhooks.update(WebhookId(2), WebhookUpdate(disabled=True))
        assert webhooks.delete(WebhookId(2)) is True
        assert seen["webhook_create"] == {"webhook_url": "https://y"}
        assert seen["webhook_update"]["disabled"] is True

        interactions = InteractionService(http)
        _st = datetime(2024, 1, 1, tzinfo=timezone.utc)
        _et = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert (
            interactions.list(
                type=InteractionType.EMAIL, person_id=PersonId(1), start_time=_st, end_time=_et
            )
            .data[0]
            .id
            == 1
        )
        assert (
            interactions.list(
                type=InteractionType.MEETING, person_id=PersonId(1), start_time=_st, end_time=_et
            ).data
            == []
        )
        # type is required - verify ValueError is raised without it
        with pytest.raises(ValueError, match="type is required"):
            interactions.list()
        assert interactions.get(1, InteractionType.EMAIL).id == 1
        created_i = interactions.create(
            InteractionCreate(
                type=InteractionType.EMAIL,
                person_ids=[PersonId(1)],
                content="c",
                date=now,
                direction=InteractionDirection.OUTGOING,
            )
        )
        assert created_i.id == 2
        updated_i = interactions.update(
            2,
            InteractionType.EMAIL,
            InteractionUpdate(content="u", direction=InteractionDirection.INCOMING),
        )
        assert updated_i.id == 2
        assert interactions.delete(2, InteractionType.EMAIL) is True
        assert seen["interaction_create"]["direction"] == int(InteractionDirection.OUTGOING)
        assert seen["interaction_update"]["direction"] == int(InteractionDirection.INCOMING)

        fields = FieldService(http)
        assert fields.list(list_id=ListId(10), entity_type=EntityType.PERSON)[0].id == FieldId(
            "field-1"
        )
        created_f = fields.create(
            FieldCreate(
                name="F",
                entity_type=EntityType.PERSON,
                value_type=FieldValueType.TEXT,
                list_id=ListId(10),
                allows_multiple=True,
                is_list_specific=True,
                is_required=True,
            )
        )
        assert created_f.id == FieldId("field-1")
        assert http.cache.get("field_meta") is None
        assert http.cache.get("list_10_fields") is None
        assert http.cache.get("person_fields:") is None
        assert http.cache.get("company_fields:_all_") is None
        assert fields.delete(FieldId("field-1")) is True
        assert seen["field_create"]["is_list_specific"] is True

        field_values = FieldValueService(http)
        with pytest.raises(ValueError):
            field_values.list()
        with pytest.raises(ValueError):
            field_values.list(person_id=PersonId(1), company_id=CompanyId(2))
        assert field_values.list(person_id=PersonId(1))[0].id == 1
        created_v = field_values.create(FieldValueCreate(field_id="1", entity_id=1, value="y"))
        assert created_v.id == 2
        _ = field_values.update(FieldValueId(2), "z")
        assert field_values.delete(FieldValueId(2)) is True
        assert seen["field_value_create"]["field_id"] == 1

        rel = RelationshipStrengthService(http)
        strengths = rel.get(PersonId(1), internal_id=UserId(2))
        assert strengths[0].strength == 0.5

        files = EntityFileService(http)
        with pytest.raises(ValueError):
            files.list()
        with pytest.raises(ValueError):
            files.list(person_id=PersonId(1), company_id=CompanyId(2))
        assert files.list(person_id=PersonId(1)).data[0].id == FileId(1)
        assert files.get(FileId(1)).name == "a"
        assert files.upload_bytes(b"x", "a.txt", person_id=PersonId(1)) is True
        p = tmp_path / "a.txt"
        p.write_text("x", encoding="utf-8")
        assert files.upload_path(p, person_id=PersonId(1)) is True

        auth = AuthService(http)
        assert auth.whoami().tenant.name == "T"
        rate_limits = RateLimitService(http)
        refreshed = rate_limits.refresh()
        assert refreshed.source == "endpoint"
        assert refreshed.org_monthly.limit == 1
    finally:
        http.close()


def test_field_value_changes_service_validation_and_request_building() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-value-changes":
            seen["params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "fieldId": "field-100",
                        "entityId": 123,
                        "actionType": 0,
                        "value": "new",
                        "changedAt": "2025-01-01T00:00:00Z",
                    }
                ],
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport))
    try:
        svc = FieldValueChangesService(http)

        with pytest.raises(ValueError, match="at most one of"):
            svc.list(
                FieldId("field-100"),
                person_id=PersonId(1),
                company_id=CompanyId(2),
            )

        with pytest.raises(ValueError, match="Field IDs must be 'field-<digits>'"):
            svc.list(
                EnrichedFieldId("affinity-data-location"),
                person_id=PersonId(1),
            )

        result = svc.list(FieldId("field-100"), company_id=CompanyId(123))
        assert seen["params"]["field_id"] == "100"
        assert seen["params"]["organization_id"] == "123"
        assert len(result) == 1
        assert result[0].id == 1
        assert result[0].action_type == int(FieldValueChangeAction.CREATE)

        items = list(svc.iter(FieldId("field-100"), person_id=PersonId(1)))
        assert len(items) == 1
    finally:
        http.close()


# =============================================================================
# Enhancement 1: get_for_entity() tests (DX-001)
# =============================================================================


@pytest.mark.req("DX-001")
def test_field_value_service_get_for_entity_found() -> None:
    """Test get_for_entity returns matching FieldValue when found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-values":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "fieldId": "field-100", "entityId": 1, "value": "active"},
                        {"id": 2, "fieldId": "field-200", "entityId": 1, "value": "other"},
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldValueService(http)
        result = svc.get_for_entity(FieldId("field-100"), person_id=PersonId(1))
        assert result is not None
        assert result.id == 1
        assert result.value == "active"
    finally:
        http.close()


@pytest.mark.req("DX-001")
def test_field_value_service_get_for_entity_not_found_returns_none() -> None:
    """Test get_for_entity returns None when field not found (no default)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-values":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "fieldId": "field-999", "entityId": 1, "value": "x"},
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldValueService(http)
        result = svc.get_for_entity(FieldId("field-100"), person_id=PersonId(1))
        assert result is None
    finally:
        http.close()


@pytest.mark.req("DX-001")
def test_field_value_service_get_for_entity_with_default() -> None:
    """Test get_for_entity returns default when field not found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-values":
            return httpx.Response(200, json={"data": []}, request=request)
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldValueService(http)
        result = svc.get_for_entity(FieldId("field-100"), person_id=PersonId(1), default="N/A")
        assert result == "N/A"
    finally:
        http.close()


@pytest.mark.req("DX-001")
def test_field_value_service_get_for_entity_accepts_string_field_id() -> None:
    """Test get_for_entity accepts string field_id and normalizes it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-values":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "fieldId": "field-100", "entityId": 1, "value": "found"},
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldValueService(http)
        # Test with plain string "field-100"
        result = svc.get_for_entity("field-100", person_id=PersonId(1))
        assert result is not None
        assert result.value == "found"
    finally:
        http.close()


# =============================================================================
# Enhancement 2: list_batch() tests (DX-002)
# =============================================================================


@pytest.mark.req("DX-002")
def test_field_value_service_list_batch_success() -> None:
    """Test list_batch returns dict mapping entity_id -> field values."""
    call_count = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["value"] += 1
        if request.method == "GET" and request.url.path == "/field-values":
            person_id = request.url.params.get("person_id")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": call_count["value"],
                            "fieldId": "field-1",
                            "entityId": int(person_id),
                            "value": f"value-{person_id}",
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldValueService(http)
        result = svc.list_batch(person_ids=[PersonId(1), PersonId(2), PersonId(3)])

        # Should have 3 entries
        assert len(result) == 3
        assert PersonId(1) in result
        assert PersonId(2) in result
        assert PersonId(3) in result

        # Should have made 3 API calls (one per entity)
        assert call_count["value"] == 3
    finally:
        http.close()


@pytest.mark.req("DX-002")
def test_field_value_service_list_batch_on_error_raise() -> None:
    """Test list_batch raises on error with on_error='raise'."""
    call_count = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["value"] += 1
        if request.method == "GET" and request.url.path == "/field-values":
            person_id = request.url.params.get("person_id")
            if person_id == "2":
                return httpx.Response(404, json={"message": "not found"}, request=request)
            return httpx.Response(
                200,
                json={"data": [{"id": 1, "fieldId": "field-1", "entityId": 1, "value": "x"}]},
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldValueService(http)
        with pytest.raises(AffinityError):
            svc.list_batch(person_ids=[PersonId(1), PersonId(2), PersonId(3)], on_error="raise")
    finally:
        http.close()


@pytest.mark.req("DX-002")
def test_field_value_service_list_batch_on_error_skip() -> None:
    """Test list_batch skips failed entities with on_error='skip'."""
    call_count = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["value"] += 1
        if request.method == "GET" and request.url.path == "/field-values":
            person_id = request.url.params.get("person_id")
            if person_id == "2":
                return httpx.Response(404, json={"message": "not found"}, request=request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": call_count["value"],
                            "fieldId": "field-1",
                            "entityId": int(person_id),
                            "value": "x",
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldValueService(http)
        result = svc.list_batch(person_ids=[PersonId(1), PersonId(2), PersonId(3)], on_error="skip")

        # Should only have 2 entries (entity 2 was skipped)
        assert len(result) == 2
        assert PersonId(1) in result
        assert PersonId(2) not in result
        assert PersonId(3) in result
    finally:
        http.close()


@pytest.mark.req("DX-002")
def test_field_value_service_list_batch_requires_exactly_one_sequence() -> None:
    """Test list_batch raises when zero or multiple sequences provided."""
    http = HTTPClient(ClientConfig(api_key="test", max_retries=0))
    try:
        svc = FieldValueService(http)

        # No sequences provided
        with pytest.raises(ValueError, match="Exactly one"):
            svc.list_batch()

        # Multiple sequences provided
        with pytest.raises(ValueError, match="Exactly one"):
            svc.list_batch(person_ids=[PersonId(1)], company_ids=[CompanyId(2)])
    finally:
        http.close()


# =============================================================================
# Enhancement 8: fields.exists() and get_by_name() tests (DX-008)
# =============================================================================


@pytest.mark.req("DX-008")
def test_field_service_exists_returns_true_when_found() -> None:
    """Test exists() returns True when field exists."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                        {
                            "id": "field-200",
                            "name": "Owner",
                            "valueType": 0,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldService(http)
        assert svc.exists(FieldId("field-100")) is True
        assert svc.exists(FieldId("field-200")) is True
    finally:
        http.close()


@pytest.mark.req("DX-008")
def test_field_service_exists_returns_false_when_not_found() -> None:
    """Test exists() returns False when field does not exist."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldService(http)
        assert svc.exists(FieldId("field-999")) is False
    finally:
        http.close()


@pytest.mark.req("DX-008")
def test_field_service_get_by_name_found() -> None:
    """Test get_by_name() returns FieldMetadata when found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                        {
                            "id": "field-200",
                            "name": "Primary Owner",
                            "valueType": 0,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldService(http)
        result = svc.get_by_name("Status")
        assert result is not None
        assert result.id == FieldId("field-100")
        assert result.name == "Status"
    finally:
        http.close()


@pytest.mark.req("DX-008")
def test_field_service_get_by_name_case_insensitive() -> None:
    """Test get_by_name() is case-insensitive (uses casefold)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Primary Email Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldService(http)
        # Test various case variations
        assert svc.get_by_name("PRIMARY EMAIL STATUS") is not None
        assert svc.get_by_name("primary email status") is not None
        assert svc.get_by_name("Primary Email Status") is not None
        assert svc.get_by_name("  Primary Email Status  ") is not None  # Whitespace stripped
    finally:
        http.close()


@pytest.mark.req("DX-008")
def test_field_service_get_by_name_not_found() -> None:
    """Test get_by_name() returns None when field not found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldService(http)
        assert svc.get_by_name("NonExistent Field") is None
    finally:
        http.close()


@pytest.mark.req("DX-008")
def test_field_service_get_by_name_returns_first_match() -> None:
    """Test get_by_name() returns first match when multiple fields have same name."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                        {
                            "id": "field-200",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = HTTPClient(ClientConfig(api_key="test", transport=transport, max_retries=0))
    try:
        svc = FieldService(http)
        # When multiple fields have the same name, returns first match
        field = svc.get_by_name("Status")

        assert field is not None
        # First matching field is returned
        assert field.id == FieldId("field-100")
    finally:
        http.close()


# =============================================================================
# Async Tests for Enhancement 1: get_for_entity() (DX-001)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.req("DX-001")
async def test_async_field_value_service_get_for_entity_found() -> None:
    """Test async get_for_entity returns matching FieldValue when found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-values":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "fieldId": "field-100", "entityId": 1, "value": "active"},
                        {"id": 2, "fieldId": "field-200", "entityId": 1, "value": "other"},
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldValueService(http)
        result = await svc.get_for_entity(FieldId("field-100"), person_id=PersonId(1))
        assert result is not None
        assert result.id == 1
        assert result.value == "active"
    finally:
        await http.close()


@pytest.mark.asyncio
@pytest.mark.req("DX-001")
async def test_async_field_value_service_get_for_entity_with_default() -> None:
    """Test async get_for_entity returns default when field not found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-values":
            return httpx.Response(200, json={"data": []}, request=request)
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldValueService(http)
        result = await svc.get_for_entity(
            FieldId("field-100"), person_id=PersonId(1), default="N/A"
        )
        assert result == "N/A"
    finally:
        await http.close()


# =============================================================================
# Async Tests for Enhancement 2: list_batch() (DX-002)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.req("DX-002")
async def test_async_field_value_service_list_batch_success() -> None:
    """Test async list_batch returns dict mapping entity_id -> field values."""
    call_count = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["value"] += 1
        if request.method == "GET" and request.url.path == "/field-values":
            person_id = request.url.params.get("person_id")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": call_count["value"],
                            "fieldId": "field-1",
                            "entityId": int(person_id),
                            "value": f"value-{person_id}",
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldValueService(http)
        result = await svc.list_batch(person_ids=[PersonId(1), PersonId(2), PersonId(3)])

        # Should have 3 entries
        assert len(result) == 3
        assert PersonId(1) in result
        assert PersonId(2) in result
        assert PersonId(3) in result
    finally:
        await http.close()


@pytest.mark.asyncio
@pytest.mark.req("DX-002")
async def test_async_field_value_service_list_batch_on_error_skip() -> None:
    """Test async list_batch skips failed entities with on_error='skip'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/field-values":
            person_id = request.url.params.get("person_id")
            if person_id == "2":
                return httpx.Response(404, json={"message": "not found"}, request=request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "fieldId": "field-1",
                            "entityId": int(person_id),
                            "value": "x",
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldValueService(http)
        result = await svc.list_batch(
            person_ids=[PersonId(1), PersonId(2), PersonId(3)], on_error="skip"
        )

        # Should only have 2 entries (entity 2 was skipped)
        assert len(result) == 2
        assert PersonId(1) in result
        assert PersonId(2) not in result
        assert PersonId(3) in result
    finally:
        await http.close()


# =============================================================================
# Async Tests for Enhancement 8: exists() and get_by_name() (DX-008)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.req("DX-008")
async def test_async_field_service_exists_returns_true_when_found() -> None:
    """Test async exists() returns True when field exists."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldService(http)
        assert await svc.exists(FieldId("field-100")) is True
    finally:
        await http.close()


@pytest.mark.asyncio
@pytest.mark.req("DX-008")
async def test_async_field_service_exists_returns_false_when_not_found() -> None:
    """Test async exists() returns False when field does not exist."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldService(http)
        assert await svc.exists(FieldId("field-999")) is False
    finally:
        await http.close()


@pytest.mark.asyncio
@pytest.mark.req("DX-008")
async def test_async_field_service_get_by_name_found() -> None:
    """Test async get_by_name() returns FieldMetadata when found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldService(http)
        result = await svc.get_by_name("Status")
        assert result is not None
        assert result.id == FieldId("field-100")
        assert result.name == "Status"
    finally:
        await http.close()


@pytest.mark.asyncio
@pytest.mark.req("DX-008")
async def test_async_field_service_get_by_name_not_found() -> None:
    """Test async get_by_name() returns None when field not found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Status",
                            "valueType": 2,
                            "allowsMultiple": False,
                        },
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    http = AsyncHTTPClient(ClientConfig(api_key="test", async_transport=transport, max_retries=0))
    try:
        svc = AsyncFieldService(http)
        assert await svc.get_by_name("NonExistent Field") is None
    finally:
        await http.close()
