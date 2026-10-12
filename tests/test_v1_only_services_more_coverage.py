from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from affinity.clients.http import ClientConfig, HTTPClient
from affinity.models.entities import FieldCreate, FieldValueCreate
from affinity.models.secondary import (
    InteractionCreate,
    InteractionUpdate,
)
from affinity.models.types import (
    CompanyId,
    EntityType,
    FieldId,
    FieldValueType,
    InteractionType,
    ListId,
    NoteId,
    OpportunityId,
    PersonId,
    ReminderResetType,
    ReminderStatus,
    ReminderType,
    UserId,
)
from affinity.services.v1_only import (
    EntityFileService,
    FieldService,
    FieldValueService,
    InteractionService,
    NoteService,
    RelationshipStrengthService,
)


def test_v1_only_services_cover_optional_params_and_fallback_shapes(tmp_path: Path) -> None:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    created_at = now.isoformat()
    seen: dict[str, Any] = {}
    counts: dict[str, int] = {"notes_list": 0, "reminders_list": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path

        # Notes: exercise params, data-vs-notes fallback, and non-list defensive path.
        if request.method == "GET" and path == "/notes":
            counts["notes_list"] += 1
            if counts["notes_list"] == 1:
                assert url.params.get("person_id") == "1"
                assert url.params.get("organization_id") == "2"
                assert url.params.get("opportunity_id") == "3"
                assert url.params.get("creator_id") == "4"
                assert url.params.get("page_size") == "5"
                assert url.params.get("page_token") == "t"
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": 1, "creatorId": 4, "createdAt": created_at}],
                        "nextPageToken": None,
                    },
                    request=request,
                )
            return httpx.Response(
                200, json={"notes": {"not": "a list"}, "next_page_token": None}, request=request
            )

        if request.method == "POST" and path == "/notes":
            seen.setdefault("note_create", []).append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                json={"id": 2, "creatorId": 4, "createdAt": created_at, "content": "n"},
                request=request,
            )

        # Reminders: exercise many filters + fallback to data + non-list defensive.
        if request.method == "GET" and path == "/reminders":
            counts["reminders_list"] += 1
            if counts["reminders_list"] == 1:
                assert url.params.get("person_id") == "1"
                assert url.params.get("organization_id") == "2"
                assert url.params.get("opportunity_id") == "3"
                assert url.params.get("creator_id") == "4"
                assert url.params.get("owner_id") == "5"
                assert url.params.get("completer_id") == "6"
                assert url.params.get("type") == str(int(ReminderType.ONE_TIME))
                assert url.params.get("reset_type") == str(int(ReminderResetType.EMAIL))
                assert url.params.get("status") == str(int(ReminderStatus.ACTIVE))
                assert url.params.get("due_before") == (now + timedelta(days=1)).isoformat()
                assert url.params.get("due_after") == (now - timedelta(days=1)).isoformat()
                assert url.params.get("page_size") == "5"
                assert url.params.get("page_token") == "t"
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 1,
                                "type": int(ReminderType.ONE_TIME),
                                "status": int(ReminderStatus.ACTIVE),
                                "dueDate": created_at,
                                "createdAt": created_at,
                            }
                        ],
                        "nextPageToken": None,
                    },
                    request=request,
                )
            return httpx.Response(200, json={"reminders": {"bad": "shape"}}, request=request)

        if request.method == "POST" and path == "/reminders":
            seen["reminder_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "type": int(ReminderType.ONE_TIME),
                    "status": int(ReminderStatus.ACTIVE),
                    "dueDate": created_at,
                    "createdAt": created_at,
                },
                request=request,
            )

        if request.method == "PUT" and path == "/reminders/2":
            seen.setdefault("reminder_update", []).append(
                json.loads(request.content.decode("utf-8"))
            )
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "type": int(ReminderType.ONE_TIME),
                    "status": int(ReminderStatus.ACTIVE),
                    "dueDate": created_at,
                    "createdAt": created_at,
                },
                request=request,
            )

        # Interactions: cover chat/call paths + date/person filters + non-list fallback.
        if request.method == "GET" and path == "/interactions":
            if url.params.get("type") == str(int(InteractionType.CHAT_MESSAGE)):
                assert url.params.get("start_time") == (now - timedelta(days=1)).isoformat()
                assert url.params.get("end_time") == (now + timedelta(days=1)).isoformat()
                assert url.params.get("person_id") == "1"
                assert url.params.get("page_size") == "1"
                assert url.params.get("page_token") == "t"
                return httpx.Response(
                    200,
                    json={
                        "chat_messages": [
                            {"id": 1, "type": int(InteractionType.CHAT_MESSAGE), "date": created_at}
                        ]
                    },
                    request=request,
                )
            if url.params.get("type") == str(int(InteractionType.CALL)):
                return httpx.Response(
                    200,
                    json={
                        "events": [{"id": 2, "type": int(InteractionType.CALL), "date": created_at}]
                    },
                    request=request,
                )
            if url.params.get("type") == "999":
                return httpx.Response(200, json={"data": []}, request=request)
            return httpx.Response(200, json={"data": {"not": "a list"}}, request=request)

        if request.method == "POST" and path == "/interactions":
            seen["interaction_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 3, "type": int(InteractionType.EMAIL), "date": created_at},
                request=request,
            )

        if request.method == "PUT" and path == "/interactions/3":
            seen["interaction_update"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 3, "type": int(InteractionType.EMAIL), "date": created_at},
                request=request,
            )

        # Fields: list returns non-list items; create minimal payload; delete path.
        if request.method == "GET" and path == "/fields":
            if url.params:
                assert url.params.get("list_id") == "10"
                assert url.params.get("entity_type") == str(int(EntityType.PERSON))
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "field-1", "name": "F", "valueType": 2, "allowsMultiple": False}
                        ]
                    },
                    request=request,
                )
            return httpx.Response(200, json={"data": {"not": "a list"}}, request=request)

        if request.method == "POST" and path == "/fields":
            seen["field_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": "field-1", "name": "F", "valueType": 2, "allowsMultiple": False},
                request=request,
            )

        if request.method == "DELETE" and path == "/fields/1":
            return httpx.Response(200, json={"success": True}, request=request)

        # Field values: cover multiple selector params and list_entry_id in create.
        if request.method == "GET" and path == "/field-values":
            if url.params.get("organization_id") is not None:
                return httpx.Response(200, json={"data": {"bad": "shape"}}, request=request)
            return httpx.Response(
                200,
                json={"data": [{"id": 1, "fieldId": "field-1", "entityId": 1, "value": "x"}]},
                request=request,
            )

        if request.method == "POST" and path == "/field-values":
            seen["field_value_create"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 2, "fieldId": "field-1", "entityId": 1, "value": "y"},
                request=request,
            )

        # Relationship strengths: cover internal_id omitted and non-list defensive.
        if request.method == "GET" and path == "/relationships-strengths":
            if url.params.get("internal_id") is None:
                return httpx.Response(200, json={"data": {"bad": "shape"}}, request=request)
            return httpx.Response(
                200,
                json={"data": [{"internalId": 2, "externalId": 1, "strength": 0.5}]},
                request=request,
            )

        # Entity files: cover multiple response keys + pagination token loop.
        if request.method == "GET" and path == "/entity-files":
            page_token = url.params.get("page_token")
            if page_token == "p2":
                return httpx.Response(
                    200,
                    json={
                        "files": [],
                        "next_page_token": None,
                    },
                    request=request,
                )
            if url.params.get("organization_id") == "999" and page_token is None:
                return httpx.Response(
                    200,
                    json={
                        "entity_files": [
                            {
                                "id": 2,
                                "name": "missing-content-type",
                                "size": 1,
                                "uploaderId": 1,
                                "createdAt": created_at,
                            }
                        ],
                        "next_page_token": None,
                    },
                    request=request,
                )
            if url.params.get("organization_id") == "2" and page_token is None:
                return httpx.Response(
                    200,
                    json={
                        "entity_files": [
                            {
                                "id": 1,
                                "name": "a",
                                "size": 1,
                                "contentType": "x",
                                "uploaderId": 1,
                                "createdAt": created_at,
                            }
                        ],
                        "next_page_token": "p2",
                    },
                    request=request,
                )
            if page_token == "p1":
                assert url.params.get("organization_id") == "2"
                assert url.params.get("page_size") == "1"
                assert url.params.get("page_token") == "p1"
                return httpx.Response(
                    200,
                    json={
                        "entity_files": [
                            {
                                "id": 1,
                                "name": "a",
                                "size": 1,
                                "contentType": "x",
                                "uploaderId": 1,
                                "createdAt": created_at,
                            }
                        ],
                        "next_page_token": "p2",
                    },
                    request=request,
                )
            return httpx.Response(200, json={"entityFiles": {"bad": "shape"}}, request=request)

        if request.method == "POST" and path == "/entity-files":
            if b"opportunity_id" in request.content:
                return httpx.Response(200, json={"success": False}, request=request)
            if b"organization_id" in request.content:
                return httpx.Response(200, json={"id": 1}, request=request)
            return httpx.Response(200, json={"success": True}, request=request)

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
        notes = NoteService(http)
        assert notes.list(
            person_id=PersonId(1),
            company_id=CompanyId(2),
            opportunity_id=OpportunityId(3),
            creator_id=UserId(4),
            page_size=5,
            page_token="t",
        ).data[0].id == NoteId(1)
        assert notes.list().data == []

        interactions = InteractionService(http)
        assert (
            interactions.list(
                type=InteractionType.CHAT_MESSAGE,
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=1),
                person_id=PersonId(1),
                page_size=1,
                page_token="t",
            )
            .data[0]
            .id
            == 1
        )
        _st = datetime(2024, 1, 1, tzinfo=timezone.utc)
        _et = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert (
            interactions.list(
                type=InteractionType.CALL, person_id=PersonId(1), start_time=_st, end_time=_et
            )
            .data[0]
            .id
            == 2
        )
        assert (
            interactions.list(
                type=InteractionType(999), person_id=PersonId(1), start_time=_st, end_time=_et
            ).data
            == []
        )
        # type is required - verify ValueError is raised without it
        with pytest.raises(ValueError, match="type is required"):
            interactions.list()
        _ = interactions.create(
            InteractionCreate(
                type=InteractionType.EMAIL,
                person_ids=[PersonId(1)],
                content="c",
                date=now,
                direction=None,
            )
        )
        _ = interactions.update(
            3, InteractionType.EMAIL, InteractionUpdate(person_ids=[PersonId(1)], date=now)
        )
        assert "person_ids" in seen["interaction_update"]

        fields = FieldService(http)
        assert fields.list() == []
        assert fields.list(list_id=ListId(10), entity_type=EntityType.PERSON)[0].id == FieldId(
            "field-1"
        )
        _ = fields.create(
            FieldCreate(name="F", entity_type=EntityType.PERSON, value_type=FieldValueType.TEXT)
        )
        assert seen["field_create"] == {
            "name": "F",
            "entity_type": int(EntityType.PERSON),
            "value_type": 6,  # V1 code 6 = Text (long text block)
        }
        assert fields.delete(FieldId("field-1")) is True

        values = FieldValueService(http)
        assert values.list(person_id=PersonId(1))[0].id == 1
        assert values.list(company_id=CompanyId(2)) == []
        assert values.list(opportunity_id=OpportunityId(3))[0].id == 1
        assert values.list(list_entry_id=4)[0].id == 1
        _ = values.create(FieldValueCreate(field_id="1", entity_id=1, value="y", list_entry_id=4))
        assert seen["field_value_create"]["list_entry_id"] == 4

        rel = RelationshipStrengthService(http)
        assert rel.get(PersonId(1), internal_id=UserId(2))[0].strength == 0.5
        assert rel.get(PersonId(1)) == []

        files = EntityFileService(http)
        all_items = list(files.all(company_id=CompanyId(2)))
        assert [f.id for f in all_items] == [1]
        assert list(files.iter(company_id=CompanyId(2))) != []
        page = files.list(company_id=CompanyId(2), page_size=1, page_token="p1")
        assert page.data[0].id == 1
        assert files.list(opportunity_id=OpportunityId(3)).data == []
        page_missing = files.list(company_id=CompanyId(999))
        assert page_missing.data[0].content_type is None

        progress: list[tuple[int, int | None, str]] = []
        p = tmp_path / "x.bin"
        p.write_bytes(b"hello")
        assert files.upload_path(
            p,
            company_id=CompanyId(2),
            filename="override.bin",
            content_type="application/octet-stream",
            on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
        )
        assert progress[0][0] == 0 and progress[-1][2] == "upload"
        assert (
            files.upload_bytes(
                b"hi",
                "a.txt",
                opportunity_id=OpportunityId(3),
                on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
            )
            is False
        )
        assert files.upload_bytes(b"hi", "a.txt", person_id=PersonId(1)) is True
    finally:
        http.close()


def test_field_service_cache_invalidation_branches_when_cache_disabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/fields":
            return httpx.Response(
                200,
                json={"id": "field-1", "name": "F", "valueType": 2, "allowsMultiple": False},
                request=request,
            )
        if request.method == "DELETE" and request.url.path == "/fields/1":
            return httpx.Response(200, json={"success": True}, request=request)
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
        fields = FieldService(http)
        assert fields.create(
            FieldCreate(name="F", entity_type=EntityType.PERSON, value_type=FieldValueType.TEXT)
        ).id == FieldId("field-1")
        assert fields.delete(FieldId("field-1")) is True
    finally:
        http.close()
