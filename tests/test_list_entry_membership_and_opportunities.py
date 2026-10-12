from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from affinity.clients.http import ClientConfig, HTTPClient
from affinity.models import OpportunityUpdate
from affinity.services.lists import ListService
from affinity.services.opportunities import OpportunityService
from affinity.types import CompanyId, ListId, OpportunityId, PersonId


def test_list_entry_find_and_ensure_person_uses_entity_centric_v2_list_entries() -> None:
    calls: list[tuple[str, str]] = []
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        if request.method == "GET" and request.url == httpx.URL(
            "https://v2.example/v2/persons/1/list-entries"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 1, "listId": 999, "createdAt": created_at, "fields": {}},
                        {"id": 2, "listId": 10, "createdAt": created_at, "fields": {}},
                    ],
                    "pagination": {
                        "nextUrl": "https://v2.example/v2/persons/1/list-entries?cursor=abc"
                    },
                },
                request=request,
            )

        if request.method == "GET" and request.url == httpx.URL(
            "https://v2.example/v2/persons/1/list-entries?cursor=abc"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 3, "listId": 10, "createdAt": created_at, "fields": {}},
                    ],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )

        if request.method == "POST":
            raise AssertionError("ensure_person should not create when entry already exists")

        return httpx.Response(404, json={}, request=request)

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
        entries = ListService(http).entries(ListId(10))

        first = entries.find_person(PersonId(1))
        assert first is not None
        assert first.id == 2

        all_matches = entries.find_all_person(PersonId(1))
        assert [e.id for e in all_matches] == [2, 3]

        ensured = entries.ensure_person(PersonId(1))
        assert ensured.id == 2

        assert calls[0][0] == "GET"
        assert calls[1][0] == "GET"
    finally:
        http.close()


def test_list_entry_ensure_company_creates_v1_list_entry_when_absent() -> None:
    calls: list[tuple[str, str, dict]] = []
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url == httpx.URL(
            "https://v2.example/v2/companies/2/list-entries"
        ):
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        if request.method == "POST" and request.url == httpx.URL(
            "https://v1.example/lists/10/list-entries"
        ):
            body = json.loads(request.content.decode("utf-8"))
            calls.append((request.method, str(request.url), body))
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "listId": 10,
                    "entityId": 2,
                    "createdAt": created_at,
                    "creatorId": 123,
                },
                request=request,
            )

        return httpx.Response(404, json={}, request=request)

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
        entries = ListService(http).entries(ListId(10))
        created = entries.ensure_company(CompanyId(2), creator_id=123)
        assert created.id == 42
        assert created.entity_id == 2
        assert calls == [
            (
                "POST",
                "https://v1.example/lists/10/list-entries",
                {"entity_id": 2, "creator_id": 123},
            )
        ]
    finally:
        http.close()


def test_opportunity_update_and_get_details_use_v1_endpoints() -> None:
    seen: dict[str, object] = {}
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url == httpx.URL(
            "https://v1.example/opportunities/5"
        ):
            seen["update_payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"id": 5, "name": "Updated", "list_id": 10, "person_ids": [1]},
                request=request,
            )

        if request.method == "GET" and request.url == httpx.URL(
            "https://v1.example/opportunities/5"
        ):
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "name": "Updated",
                    "list_id": 10,
                    "person_ids": [1],
                    "organization_ids": [2],
                    "list_entries": [
                        {"id": 77, "listId": 10, "createdAt": created_at, "fields": {}}
                    ],
                },
                request=request,
            )

        return httpx.Response(404, json={}, request=request)

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
        service = OpportunityService(http)

        updated = service.update(
            OpportunityId(5),
            OpportunityUpdate(
                name="Updated",
                person_ids=[PersonId(1)],
                company_ids=[CompanyId(2)],
            ),
        )
        assert updated.id == 5

        payload = seen["update_payload"]
        assert payload == {"name": "Updated", "person_ids": [1], "organization_ids": [2]}

        details = service.get_details(OpportunityId(5))
        assert details.id == 5
        assert details.list_entries is not None
        assert details.list_entries[0].id == 77
    finally:
        http.close()
