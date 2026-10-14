"""Service ``pages()`` generators stop when paging can't progress, as the page iterators do:
a cursor already followed (A -> B -> A) ends paging, and a long run of empty pages that
keep returning a next cursor ends it with a ``RuntimeWarning``."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest

from affinity import Affinity, AsyncAffinity
from affinity.models import pagination as pagination_module
from affinity.types import ListId

BASE = "https://api.affinity.co"
# Far more pages than any guarded loop reads: past it the handlers raise, so an unguarded
# loop fails instead of hanging.
CAP = 25


def _cycle_handler(target: str, calls: list[str | None]) -> Callable[[httpx.Request], Any]:
    """Pages of ``target`` cycle A -> B -> A; every other path is one empty page."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != target:
            return httpx.Response(200, json={"data": [], "pagination": {"nextUrl": None}})
        cursor = request.url.params.get("cursor")
        calls.append(cursor)
        if len(calls) > CAP:
            raise RuntimeError("paging did not stop")
        nxt = {None: "A", "A": "B", "B": "A"}[cursor]
        next_url = f"{BASE}{target}?cursor={nxt}"
        return httpx.Response(200, json={"data": [], "pagination": {"nextUrl": next_url}})

    return handler


def _endless_handler(target: str, calls: list[str | None]) -> Callable[[httpx.Request], Any]:
    """Empty pages of ``target``, each with a new next cursor."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != target:
            return httpx.Response(200, json={"data": [], "pagination": {"nextUrl": None}})
        calls.append(request.url.params.get("cursor"))
        if len(calls) > CAP:
            raise RuntimeError("paging did not stop")
        next_url = f"{BASE}{target}?cursor=c{len(calls)}"
        return httpx.Response(200, json={"data": [], "pagination": {"nextUrl": next_url}})

    return handler


SYNC_PAGERS: list[tuple[str, str, Callable[[Affinity], Iterator[Any]]]] = [
    ("companies", "/v2/companies", lambda c: c.companies.pages()),
    ("persons", "/v2/persons", lambda c: c.persons.pages()),
    ("opportunities", "/v2/opportunities", lambda c: c.opportunities.pages()),
    ("lists", "/v2/lists", lambda c: c.lists.pages()),
    (
        "saved_views",
        "/v2/lists/1/saved-views",
        lambda c: c.lists.saved_views_pages(ListId(1)),
    ),
    ("entries", "/v2/lists/1/list-entries", lambda c: c.lists.entries(ListId(1)).pages()),
]

ASYNC_PAGERS: list[tuple[str, str, Callable[[AsyncAffinity], AsyncIterator[Any]]]] = [
    ("companies", "/v2/companies", lambda c: c.companies.pages()),
    ("persons", "/v2/persons", lambda c: c.persons.pages()),
    ("opportunities", "/v2/opportunities", lambda c: c.opportunities.pages()),
    ("lists", "/v2/lists", lambda c: c.lists.pages()),
    (
        "saved_views",
        "/v2/lists/1/saved-views",
        lambda c: c.lists.saved_views_pages(ListId(1)),
    ),
    ("entries", "/v2/lists/1/list-entries", lambda c: c.lists.entries(ListId(1)).pages()),
]


def _sync_client(handler: Callable[[httpx.Request], Any]) -> Affinity:
    return Affinity(api_key="k", transport=httpx.MockTransport(handler), enable_cache=False)


def _async_client(handler: Callable[[httpx.Request], Any]) -> AsyncAffinity:
    return AsyncAffinity(
        api_key="k", async_transport=httpx.MockTransport(handler), enable_cache=False
    )


async def _take(pages: AsyncIterator[Any], n: int) -> int:
    count = 0
    async for _ in pages:
        count += 1
        if count >= n:
            break
    return count


@pytest.mark.parametrize(
    ("path", "pager"), [p[1:] for p in SYNC_PAGERS], ids=[p[0] for p in SYNC_PAGERS]
)
def test_sync_pages_stop_on_a_cursor_cycle(
    path: str, pager: Callable[[Affinity], Iterator[Any]]
) -> None:
    calls: list[str | None] = []
    with _sync_client(_cycle_handler(path, calls)) as client:
        pages = list(itertools.islice(pager(client), CAP))
    assert calls == [None, "A", "B"]
    assert len(pages) == 3


@pytest.mark.parametrize(
    ("path", "pager"), [p[1:] for p in ASYNC_PAGERS], ids=[p[0] for p in ASYNC_PAGERS]
)
async def test_async_pages_stop_on_a_cursor_cycle(
    path: str, pager: Callable[[AsyncAffinity], AsyncIterator[Any]]
) -> None:
    calls: list[str | None] = []
    async with _async_client(_cycle_handler(path, calls)) as client:
        count = await _take(pager(client), CAP)
    assert calls == [None, "A", "B"]
    assert count == 3


def test_sync_pages_cap_a_run_of_empty_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination_module, "_MAX_EMPTY_PAGES", 3)
    calls: list[str | None] = []
    with (
        _sync_client(_endless_handler("/v2/companies", calls)) as client,
        pytest.warns(RuntimeWarning, match="empty pages"),
    ):
        pages = list(itertools.islice(client.companies.pages(), CAP))
    assert len(calls) == 3
    assert len(pages) == 3


async def test_async_pages_cap_a_run_of_empty_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination_module, "_MAX_EMPTY_PAGES", 3)
    calls: list[str | None] = []
    async with _async_client(_endless_handler("/v2/lists/1/list-entries", calls)) as client:
        with pytest.warns(RuntimeWarning, match="empty pages"):
            count = await _take(client.lists.entries(ListId(1)).pages(), CAP)
    assert len(calls) == 3
    assert count == 3


def test_filtered_entry_pages_stop_on_a_cursor_cycle() -> None:
    """The physical reads behind a filtered ``pages()`` stop on a cycle too."""
    calls: list[str | None] = []
    with (
        _sync_client(_cycle_handler("/v2/lists/1/list-entries", calls)) as client,
        pytest.warns(UserWarning),
    ):
        pages = list(
            itertools.islice(
                client.lists.entries(ListId(1)).pages(
                    filter="name = 'x'", server_filter=False, limit=10
                ),
                CAP,
            )
        )
    assert calls == [None, "A", "B"]
    assert pages == []


async def test_async_filtered_entry_pages_stop_on_a_cursor_cycle() -> None:
    calls: list[str | None] = []
    async with _async_client(_cycle_handler("/v2/lists/1/list-entries", calls)) as client:
        with pytest.warns(UserWarning):
            count = await _take(
                client.lists.entries(ListId(1)).pages(
                    filter="name = 'x'", server_filter=False, limit=10
                ),
                CAP,
            )
    assert calls == [None, "A", "B"]
    assert count == 0
