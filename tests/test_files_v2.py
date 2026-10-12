"""SDK tests: V2 file reads (`files.get`, `files.feed`, `files.iter_feed`; API 2026-09-17+).
`files.list/iter` (V1, one entity's files) are unchanged."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.models import FileV2, PersonRef
from affinity.types import FileId, PersonId, UserId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
SUMMARY = {
    "id": 61,
    "name": "deck.pdf",
    "size": 245760,
    "type": "application/pdf",
    "creator": {"id": 4},
    "createdAt": "2026-10-01T09:00:00Z",
    "updatedAt": None,
}
DETAIL = {**SUMMARY, "downloadUrl": "https://user-files.affinity.co/f/61?sig=x"}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def test_model() -> None:
    f = FileV2.model_validate(DETAIL)
    assert f.content_type == "application/pdf"
    assert f.creator == PersonRef(id=PersonId(4))
    assert f.download_url is not None
    assert FileV2.model_validate({**SUMMARY, "creator": None, "type": None}).download_url is None


@respx.mock
def test_get() -> None:
    route = respx.get(f"{V2}/files/61").mock(return_value=Response(200, json=DETAIL))
    with Affinity(api_key="k", max_retries=0) as client:
        f = client.files.get(FileId(61))
    assert f.name == "deck.pdf" and f.download_url
    assert route.calls[0].request.headers["x-affinity-api-version"] == "2026-09-17"


@respx.mock
def test_feed_filters() -> None:
    route = respx.get(f"{V2}/files").mock(return_value=Response(200, json=_page([SUMMARY])))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.files.feed(
            created_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
            created_before=datetime(2026, 2, 1, tzinfo=timezone.utc),
            creator_id=UserId(4),
            limit=20,
            total_count=True,
        )
    assert [f.id for f in page.data] == [61]
    params = route.calls[0].request.url.params
    assert params["filter"] == (
        "createdAt>=2026-01-01T00:00:00Z & createdAt<2026-02-01T00:00:00Z & creator.id=4"
    )
    assert params["limit"] == "20" and params["totalCount"] == "true"
    assert route.calls[0].request.headers["x-affinity-api-version"] == "2026-09-17"


def test_feed_rejects_bad_limit_and_cursor_with_filters() -> None:
    with Affinity(api_key="k", max_retries=0) as client:
        with pytest.raises(ValueError, match="limit"):
            client.files.feed(limit=0)
        with pytest.raises(ValueError, match="cursor"):
            client.files.feed(cursor=f"{V2}/files?cursor=a", creator_id=UserId(4))


@respx.mock
def test_iter_feed_follows_pages() -> None:
    url = f"{V2}/files"
    second = respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{**SUMMARY, "id": 62}]))
    )
    respx.get(url).mock(return_value=Response(200, json=_page([SUMMARY], f"{url}?cursor=p2")))
    with Affinity(api_key="k", max_retries=0) as client:
        assert [f.id for f in client.files.iter_feed()] == [61, 62]
    assert second.calls[0].request.headers["x-affinity-api-version"] == "2026-09-17"


@respx.mock
async def test_async_v2_reads() -> None:
    url = f"{V2}/files"
    respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{**SUMMARY, "id": 62}]))
    )
    respx.get(url).mock(return_value=Response(200, json=_page([SUMMARY], f"{url}?cursor=p2")))
    respx.get(f"{V2}/files/61").mock(return_value=Response(200, json=DETAIL))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        page = await client.files.feed(limit=1)
        ids = [f.id async for f in client.files.iter_feed()]
        f = await client.files.get(FileId(61))
    assert page.data[0].id == 61 and ids == [61, 62] and f.download_url
