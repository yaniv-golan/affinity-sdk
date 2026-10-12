from __future__ import annotations

import httpx
import pytest

from affinity import Affinity, AsyncAffinity, WriteNotAllowedError
from affinity.models.secondary import NoteCreate
from affinity.policies import Policies, WritePolicy
from affinity.types import PersonId


def test_transport_injection_is_used_by_affinity_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v2/lists":
            return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)
        return httpx.Response(404, json={}, request=request)

    client = Affinity(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    try:
        page = client.lists.list(limit=1)
        assert page.data == []
    finally:
        client.close()


def test_write_policy_denies_writes_and_blocks_before_network() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/v2/lists":
            return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)
        pytest.fail(f"Unexpected network call: {request.method} {request.url!s}")

    client = Affinity(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        transport=httpx.MockTransport(handler),
        max_retries=0,
        policies=Policies(write=WritePolicy.DENY),
    )
    try:
        _ = client.lists.list(limit=1)
        assert calls == [("GET", "/v2/lists")]

        with pytest.raises(WriteNotAllowedError):
            _ = client.notes.create(NoteCreate(content="x", person_ids=[PersonId(1)]))

        # No additional network calls should be made for the blocked write.
        assert calls == [("GET", "/v2/lists")]
    finally:
        client.close()


def test_write_policy_denied_write_emits_on_error_hook() -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Unexpected network call: {request.method} {request.url!s}")

    def on_request(_info: object) -> None:
        events.append("request")

    def on_error(_info: object) -> None:
        events.append("error")

    client = Affinity(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        transport=httpx.MockTransport(handler),
        max_retries=0,
        policies=Policies(write=WritePolicy.DENY),
        on_request=on_request,
        on_error=on_error,
    )
    try:
        with pytest.raises(WriteNotAllowedError):
            _ = client.notes.create(NoteCreate(content="x", person_ids=[PersonId(1)]))
        assert events == ["request", "error"]
    finally:
        client.close()


@pytest.mark.asyncio
async def test_transport_injection_is_used_by_async_affinity_client() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v2/lists":
            return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)
        return httpx.Response(404, json={}, request=request)

    client = AsyncAffinity(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        async_transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    try:
        page = await client.lists.list(limit=1)
        assert page.data == []
    finally:
        await client.close()
