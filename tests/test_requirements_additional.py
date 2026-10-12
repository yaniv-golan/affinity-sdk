"""
Additional requirement-driven tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

import affinity.clients.http as http_mod
from affinity import Affinity
from affinity.clients.http import ClientConfig, HTTPClient
from affinity.exceptions import AffinityError, ValidationError
from affinity.models.pagination import PaginatedResponse
from affinity.models.secondary import NoteCreate
from affinity.services.companies import CompanyService
from affinity.services.v1_only import NoteService
from affinity.types import CompanyId, PersonId


@pytest.mark.req("FR-003")
def test_sync_client_exposes_extended_entity_services() -> None:
    client = Affinity(api_key="test")
    try:
        # Extended surfaces currently implemented via V1 services.
        assert hasattr(client, "notes")
        assert hasattr(client, "reminders")
        assert hasattr(client, "webhooks")
        assert hasattr(client, "interactions")
        assert hasattr(client, "fields")
        assert hasattr(client, "field_values")
        assert hasattr(client, "files")
        assert hasattr(client, "relationships")
        assert hasattr(client, "auth")

        # Spot-check a few representative operations exist (no network calls).
        assert callable(client.notes.create)
        assert callable(client.webhooks.list)
        assert callable(client.fields.list)
        assert callable(client.files.download)
    finally:
        client.close()


@pytest.mark.req("FR-004")
def test_v2_first_plus_v1_fallback_routing_and_stable_shapes() -> None:
    called_urls: list[str] = []

    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))

        if request.url.path.endswith("/companies/1"):
            return httpx.Response(
                200,
                json={"id": 1, "name": "Acme"},
                request=request,
            )

        if request.url.path.endswith("/organizations"):
            return httpx.Response(
                200,
                json={
                    "organizations": [{"id": 1, "name": "Acme"}],
                    "next_page_token": None,
                },
                request=request,
            )

        if request.url.path.endswith("/notes") and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "type": "entities",
                    "content": {"html": "<p>hello</p>"},
                    "createdAt": created_at,
                },
                request=request,
            )

        return httpx.Response(200, json={}, request=request)

    config = ClientConfig(
        api_key="test",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    http_client = HTTPClient(config)

    companies = CompanyService(http_client)
    company = companies.get(CompanyId(1))
    assert company.id == 1

    page = companies.search("acme")
    assert isinstance(page, PaginatedResponse)
    assert page.data[0].id == 1

    notes = NoteService(http_client)
    note = notes.create(NoteCreate(content="hello", person_ids=[PersonId(1)]))
    assert note.id == 1

    http_client.close()

    assert called_urls[0].startswith("https://v2.example/v2/")
    assert called_urls[1].startswith("https://v1.example/")
    # Note writes are V2 since SDK 2.0
    assert called_urls[2].startswith("https://v2.example/v2/")


@pytest.mark.req("NFR-001a")
def test_http2_flag_is_wired_to_httpx_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, bool] = {}

    class StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = args
            seen["http2"] = bool(kwargs.get("http2", False))

        def close(self) -> None:
            return None

    monkeypatch.setattr(http_mod.httpx, "Client", StubClient)
    client = http_mod.HTTPClient(http_mod.ClientConfig(api_key="test", http2=True))
    client.close()
    assert seen["http2"] is True


@pytest.mark.asyncio
@pytest.mark.req("NFR-001a")
async def test_http2_flag_is_wired_to_httpx_async_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, bool] = {}

    class StubAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = args
            seen["http2"] = bool(kwargs.get("http2", False))

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(http_mod.httpx, "AsyncClient", StubAsyncClient)
    client = http_mod.AsyncHTTPClient(http_mod.ClientConfig(api_key="test", http2=True))
    _ = await client._get_client()
    await client.close()
    assert seen["http2"] is True


@pytest.mark.req("NFR-001a")
def test_error_diagnostics_include_http_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"errors": [{"message": "not found"}]},
            request=request,
        )

    config = ClientConfig(
        api_key="test",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    client = HTTPClient(config)
    with pytest.raises(AffinityError) as exc_info:
        client.get("/companies/1")
    client.close()

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.http_version is not None


@pytest.mark.req("DX-005")
def test_error_messages_and_context_are_actionable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"errors": [{"message": "bad request", "param": "name"}]},
            request=request,
        )

    config = ClientConfig(
        api_key="test",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    client = HTTPClient(config)

    with pytest.raises(ValidationError) as exc_info:
        client.get("/companies/1")
    client.close()

    err = exc_info.value
    rendered = str(err)

    assert "[400]" in rendered
    assert "param: name" in rendered
    assert "GET" in rendered
    assert "/companies/1" in rendered
    assert err.response_body == {"errors": [{"message": "bad request", "param": "name"}]}
    assert err.diagnostics is not None
    assert err.diagnostics.url is not None
