import asyncio

import httpx
import pytest

from affinity import Affinity, AsyncAffinity
from affinity.filters import F


def _ok(request):
    return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)


def _make_sync_client(handler=None):
    return Affinity(
        api_key="test",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        max_retries=0,
        transport=httpx.MockTransport(handler or _ok),
    )


# ---- Sync ----


def test_persons_list_rejects_string_filter():
    client = _make_sync_client()
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            client.persons.list(filter='firstName =~ "Alex"')
    finally:
        client.close()


def test_persons_list_rejects_filter_expression():
    client = _make_sync_client()
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            client.persons.list(filter=F.field("Department").equals("Sales"))
    finally:
        client.close()


def test_persons_list_error_hints_at_alternative():
    client = _make_sync_client()
    try:
        with pytest.raises(ValueError) as exc_info:
            client.persons.list(filter='firstName =~ "Alex"')
        msg = str(exc_info.value)
        assert "search_pages" in msg or "lists.entries" in msg
    finally:
        client.close()


def test_persons_list_still_works_without_filter():
    def handler(request):
        assert "filter" not in str(request.url)
        return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)

    client = _make_sync_client(handler)
    try:
        result = client.persons.list(limit=10)
        assert result.data == []
    finally:
        client.close()


def test_persons_get_first_rejects_filter():
    client = _make_sync_client()
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            client.persons.get_first(filter='firstName =~ "Alex"')
    finally:
        client.close()


def test_persons_all_rejects_filter():
    client = _make_sync_client()
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            list(client.persons.all(filter='firstName =~ "Alex"'))
    finally:
        client.close()


def test_persons_iter_rejects_filter():
    client = _make_sync_client()
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            list(client.persons.iter(filter='firstName =~ "Alex"'))
    finally:
        client.close()


def test_persons_pages_rejects_filter():
    client = _make_sync_client()
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            list(client.persons.pages(filter='firstName =~ "Alex"'))
    finally:
        client.close()


# ---- Async ----


def test_async_persons_list_rejects_filter():
    async def run():
        async with AsyncAffinity(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(_ok),
        ) as client:
            with pytest.raises(ValueError, match="does not support server-side filter"):
                await client.persons.list(filter='firstName =~ "Alex"')

    asyncio.run(run())


def test_async_persons_get_first_rejects_filter():
    async def run():
        async with AsyncAffinity(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(_ok),
        ) as client:
            with pytest.raises(ValueError, match="does not support server-side filter"):
                await client.persons.get_first(filter='firstName =~ "Alex"')

    asyncio.run(run())


def test_async_persons_all_rejects_filter_on_call():
    async def run():
        async with AsyncAffinity(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(_ok),
        ) as client:
            with pytest.raises(ValueError, match="does not support server-side filter"):
                client.persons.all(filter='firstName =~ "Alex"')

    asyncio.run(run())


def test_async_persons_iter_rejects_filter_on_call():
    async def run():
        async with AsyncAffinity(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(_ok),
        ) as client:
            with pytest.raises(ValueError, match="does not support server-side filter"):
                client.persons.iter(filter='firstName =~ "Alex"')

    asyncio.run(run())


def test_async_persons_pages_rejects_filter_on_call():
    async def run():
        async with AsyncAffinity(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(_ok),
        ) as client:
            with pytest.raises(ValueError, match="does not support server-side filter"):
                client.persons.pages(filter='firstName =~ "Alex"')

    asyncio.run(run())
