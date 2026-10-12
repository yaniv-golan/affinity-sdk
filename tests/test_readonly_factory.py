"""
Tests for read_only() and read_only_from_env() convenience factories.
"""

from __future__ import annotations

import httpx
import pytest

from affinity import Affinity, AsyncAffinity, Policies, WriteNotAllowedError
from affinity.models import NoteCreate
from affinity.types import PersonId


@pytest.mark.req("SDK-READONLY-FACTORY")
def test_read_only_factory_blocks_writes() -> None:
    """read_only() should create client that blocks write operations."""

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Unexpected network call: {request.method} {request.url!s}")

    transport = httpx.MockTransport(handler)
    with (
        Affinity.read_only(api_key="test-key", max_retries=0, transport=transport) as client,
        pytest.raises(WriteNotAllowedError),
    ):
        client.notes.create(NoteCreate(content="x", person_ids=[PersonId(1)]))


@pytest.mark.req("SDK-READONLY-FACTORY")
def test_read_only_factory_allows_reads() -> None:
    """read_only() client should still allow read operations."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/lists":
            return httpx.Response(200, json={"data": [], "pagination": {}})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    with Affinity.read_only(api_key="test-key", max_retries=0, transport=transport) as client:
        page = client.lists.list(limit=1)
        assert page.data == []


@pytest.mark.req("SDK-READONLY-FACTORY")
def test_read_only_factory_rejects_policies_kwarg() -> None:
    """read_only() should reject explicit policies argument."""
    with pytest.raises(ValueError, match="Cannot specify 'policies'"):
        Affinity.read_only(api_key="test", policies=Policies())


@pytest.mark.req("SDK-READONLY-FACTORY")
def test_read_only_factory_passes_kwargs() -> None:
    """read_only() should forward kwargs to constructor."""
    client = Affinity.read_only(api_key="test", timeout=60.0, max_retries=0)
    client.close()


@pytest.mark.req("SDK-READONLY-FACTORY")
@pytest.mark.asyncio
async def test_async_read_only_factory() -> None:
    """AsyncAffinity.read_only() should block writes identically."""

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Unexpected network call: {request.method} {request.url!s}")

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity.read_only(
        api_key="test-key",
        max_retries=0,
        async_transport=async_transport,
    ) as client:
        with pytest.raises(WriteNotAllowedError):
            await client.notes.create(NoteCreate(content="x", person_ids=[PersonId(1)]))


@pytest.mark.req("SDK-READONLY-FACTORY")
def test_read_only_from_env_rejects_policies_kwarg() -> None:
    """read_only_from_env() should reject explicit policies argument."""
    with pytest.raises(ValueError, match="Cannot specify 'policies'"):
        Affinity.read_only_from_env(policies=Policies())


@pytest.mark.req("SDK-READONLY-FACTORY")
def test_read_only_from_env_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_only_from_env() should read API key from environment."""
    monkeypatch.setenv("AFFINITY_API_KEY", "test-from-env")
    client = Affinity.read_only_from_env(max_retries=0)
    try:
        assert client is not None
    finally:
        client.close()
