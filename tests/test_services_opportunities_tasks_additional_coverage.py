from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.exceptions import AffinityError
from affinity.exceptions import TimeoutError as AffinityTimeoutError
from affinity.models import OpportunityCreate, OpportunityUpdate
from affinity.models.secondary import MergeTask
from affinity.models.types import CompanyId, ListId, OpportunityId, PersonId
from affinity.services.opportunities import AsyncOpportunityService, OpportunityService
from affinity.services.tasks import AsyncTaskService, TaskService


def test_opportunity_service_v2_reads_v1_writes_and_pagination() -> None:
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        url = request.url

        if request.method == "GET" and url == httpx.URL("https://v2.example/v2/opportunities/5"):
            return httpx.Response(200, json={"id": 5, "name": "O", "listId": 10}, request=request)

        if request.method == "GET" and url == httpx.URL("https://v1.example/opportunities/5"):
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "name": "O",
                    "listId": 10,
                    "personIds": [1],
                    "organizationIds": [2],
                    "listEntries": [{"id": 1, "listId": 10, "createdAt": created_at, "fields": {}}],
                },
                request=request,
            )

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/opportunities?cursor=abc"
        ):
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/opportunities"
        ):
            limit = url.params.get("limit")
            if url.params.get("cursor") is not None:
                raise AssertionError("cursor page should be handled separately")
            if limit is not None:
                assert limit == "1"
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "name": "A", "listId": 10}],
                    "pagination": {"nextUrl": "https://v2.example/v2/opportunities?cursor=abc"},
                },
                request=request,
            )

        if request.method == "POST" and url == httpx.URL("https://v1.example/opportunities"):
            payload = json.loads(request.content.decode("utf-8"))
            if payload.get("name") == "New":
                assert payload == {
                    "name": "New",
                    "list_id": 10,
                    "person_ids": [1],
                    "organization_ids": [2],
                }
                return httpx.Response(
                    200, json={"id": 9, "name": "New", "listId": 10}, request=request
                )
            assert payload == {"name": "Bare", "list_id": 10}
            return httpx.Response(
                200, json={"id": 8, "name": "Bare", "listId": 10}, request=request
            )

        if request.method == "PUT" and url == httpx.URL("https://v1.example/opportunities/9"):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload in (
                {"name": "Updated"},
                {"person_ids": [1], "organization_ids": [2]},
            )
            return httpx.Response(
                200, json={"id": 9, "name": "Updated", "listId": 10}, request=request
            )

        if request.method == "DELETE" and url == httpx.URL("https://v1.example/opportunities/9"):
            return httpx.Response(200, json={"success": True}, request=request)

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
        svc = OpportunityService(http)
        assert svc.get(OpportunityId(5)).id == OpportunityId(5)
        assert svc.get_details(OpportunityId(5)).list_entries is not None

        _ = svc.list()
        _ = svc.list(limit=1)
        assert [o.id for o in list(svc.all())] == [OpportunityId(1)]

        created = svc.create(
            OpportunityCreate(
                name="New",
                list_id=ListId(10),
                person_ids=[PersonId(1)],
                company_ids=[CompanyId(2)],
            )
        )
        assert created.id == OpportunityId(9)
        bare = svc.create(OpportunityCreate(name="Bare", list_id=ListId(10)))
        assert bare.id == OpportunityId(8)

        updated = svc.update(OpportunityId(9), OpportunityUpdate(name="Updated"))
        assert updated.name == "Updated"
        updated2 = svc.update(
            OpportunityId(9),
            OpportunityUpdate(
                person_ids=[PersonId(1)],
                company_ids=[CompanyId(2)],
            ),
        )
        assert updated2.id == OpportunityId(9)
        assert svc.delete(OpportunityId(9)) is True

        assert next(svc.iter()).id == OpportunityId(1)
    finally:
        http.close()


@pytest.mark.asyncio
async def test_async_opportunity_service_cover_writes_and_all() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url

        if request.method == "GET" and url == httpx.URL("https://v2.example/v2/opportunities/5"):
            return httpx.Response(200, json={"id": 5, "name": "O", "listId": 10}, request=request)

        if request.method == "GET" and url == httpx.URL("https://v1.example/opportunities/5"):
            return httpx.Response(200, json={"id": 5, "name": "O", "listId": 10}, request=request)

        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/opportunities?cursor=abc"
        ):
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        if request.method == "GET" and url.copy_with(query=None) == httpx.URL(
            "https://v2.example/v2/opportunities"
        ):
            if url.params.get("cursor") is not None:
                raise AssertionError("cursor page should be handled separately")
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "name": "A", "listId": 10}],
                    "pagination": {"nextUrl": "https://v2.example/v2/opportunities?cursor=abc"},
                },
                request=request,
            )

        if request.method == "POST" and url == httpx.URL("https://v1.example/opportunities"):
            payload = json.loads(request.content.decode("utf-8"))
            if payload.get("person_ids") or payload.get("organization_ids"):
                return httpx.Response(
                    200, json={"id": 9, "name": "New", "listId": 10}, request=request
                )
            return httpx.Response(
                200, json={"id": 8, "name": "Bare", "listId": 10}, request=request
            )

        if request.method == "PUT" and url == httpx.URL("https://v1.example/opportunities/9"):
            return httpx.Response(
                200, json={"id": 9, "name": "Updated", "listId": 10}, request=request
            )

        if request.method == "DELETE" and url == httpx.URL("https://v1.example/opportunities/9"):
            return httpx.Response(200, json={"success": True}, request=request)

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
        svc = AsyncOpportunityService(client)
        assert (await svc.get(OpportunityId(5))).id == OpportunityId(5)
        assert (await svc.get_details(OpportunityId(5))).id == OpportunityId(5)
        _ = await svc.list(limit=1)
        all_items = [o async for o in svc.all()]
        assert [o.id for o in all_items] == [OpportunityId(1)]
        _ = await svc.create(
            OpportunityCreate(
                name="New",
                list_id=ListId(10),
                person_ids=[PersonId(1)],
                company_ids=[CompanyId(2)],
            )
        )
        _ = await svc.create(OpportunityCreate(name="Bare", list_id=ListId(10)))
        _ = await svc.update(
            OpportunityId(9),
            OpportunityUpdate(
                name="Updated",
                person_ids=[PersonId(1)],
                company_ids=[CompanyId(2)],
            ),
        )
        _ = await svc.update(OpportunityId(9), OpportunityUpdate())
        assert await svc.delete(OpportunityId(9)) is True
        assert [o.id async for o in svc.iter()] == [OpportunityId(1)]
    finally:
        await client.close()


def test_task_service_wait_success_failure_and_timeout(monkeypatch: Any) -> None:
    statuses = iter(["pending", "in_progress", "success"])
    monotonic = {"t": 0.0}

    def fake_monotonic() -> float:
        return monotonic["t"]

    def fake_sleep(seconds: float) -> None:
        monotonic["t"] += seconds

    monkeypatch.setattr("affinity.services.tasks.time.monotonic", fake_monotonic)
    monkeypatch.setattr("affinity.services.tasks.time.sleep", fake_sleep)
    monkeypatch.setattr("affinity.services.tasks.random.uniform", lambda _a, _b: 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if request.method == "GET" and url == httpx.URL(
            "https://v2.example/v2/tasks/company-merges/x"
        ):
            return httpx.Response(200, json={"id": "x", "status": next(statuses)}, request=request)
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
        tasks = TaskService(http)
        done = tasks.wait(
            "https://v2.example/v2/tasks/company-merges/x",
            timeout=10.0,
            poll_interval=1.0,
            max_poll_interval=2.0,
        )
        assert isinstance(done, MergeTask)
        assert done.status == "success"

        def failed_handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url == httpx.URL(
                "https://v2.example/v2/tasks/company-merges/y"
            ):
                return httpx.Response(200, json={"id": "y", "status": "failed"}, request=request)
            return httpx.Response(404, json={"message": "not found"}, request=request)

        failed_http = HTTPClient(
            ClientConfig(
                api_key="k",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(failed_handler),
            )
        )
        try:
            with pytest.raises(AffinityError):
                TaskService(failed_http).wait(
                    "https://v2.example/v2/tasks/company-merges/y", timeout=1.0, poll_interval=1.0
                )
        finally:
            failed_http.close()

        monotonic["t"] = 0.0

        def pending_handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url == httpx.URL(
                "https://v2.example/v2/tasks/company-merges/z"
            ):
                return httpx.Response(200, json={"id": "z", "status": "pending"}, request=request)
            return httpx.Response(404, json={"message": "not found"}, request=request)

        timeout_http = HTTPClient(
            ClientConfig(
                api_key="k",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(pending_handler),
            )
        )
        try:
            with pytest.raises(AffinityTimeoutError):
                TaskService(timeout_http).wait(
                    "https://v2.example/v2/tasks/company-merges/z",
                    timeout=0.5,
                    poll_interval=1.0,
                    max_poll_interval=1.0,
                )
        finally:
            timeout_http.close()
    finally:
        http.close()


@pytest.mark.asyncio
async def test_async_task_service_wait_success_and_timeout(monkeypatch: Any) -> None:
    statuses = iter(["pending", "success"])
    monotonic = {"t": 0.0}
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return monotonic["t"]

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        monotonic["t"] += seconds

    monkeypatch.setattr("affinity.services.tasks.time.monotonic", fake_monotonic)
    monkeypatch.setattr("affinity.services.tasks.random.uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr("affinity.services.tasks.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url == httpx.URL(
            "https://v2.example/v2/tasks/company-merges/a"
        ):
            return httpx.Response(200, json={"id": "a", "status": next(statuses)}, request=request)
        if request.method == "GET" and request.url == httpx.URL(
            "https://v2.example/v2/tasks/company-merges/fail"
        ):
            return httpx.Response(200, json={"id": "fail", "status": "failed"}, request=request)
        if request.method == "GET" and request.url == httpx.URL(
            "https://v2.example/v2/tasks/company-merges/timeout"
        ):
            return httpx.Response(200, json={"id": "timeout", "status": "pending"}, request=request)
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
        tasks = AsyncTaskService(client)
        done = await tasks.wait(
            "https://v2.example/v2/tasks/company-merges/a", timeout=10.0, poll_interval=1.0
        )
        assert done.status == "success"

        with pytest.raises(AffinityError):
            await tasks.wait(
                "https://v2.example/v2/tasks/company-merges/fail", timeout=10.0, poll_interval=1.0
            )

        monotonic["t"] = 0.0
        with pytest.raises(AffinityTimeoutError):
            await tasks.wait(
                "https://v2.example/v2/tasks/company-merges/timeout", timeout=0.5, poll_interval=1.0
            )
    finally:
        await client.close()
