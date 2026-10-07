"""get_fields() must follow pagination.nextUrl (V2 */fields endpoints are paged).

It used to read only the first page, so orgs with more fields than one page silently got
truncated field metadata. The merged result is what gets cached.
"""

from __future__ import annotations

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.services.companies import AsyncCompanyService, CompanyService
from affinity.services.lists import ListService
from affinity.services.persons import PersonService
from affinity.types import ListId


def _field(i: int) -> dict[str, object]:
    return {"id": f"field-{i}", "name": f"F{i}", "valueType": "text", "type": "global"}


def _two_page_handler(path: str, calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith(path) and "cursor" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "data": [_field(1), _field(2)],
                    "pagination": {"nextUrl": f"https://v2.example/v2{path}?cursor=p2"},
                },
                request=request,
            )
        if request.url.params.get("cursor") == "p2":
            return httpx.Response(
                200,
                json={"data": [_field(3)], "pagination": {"nextUrl": None}},
                request=request,
            )
        return httpx.Response(404, json={}, request=request)

    return handler


def _config(**extra: object) -> ClientConfig:
    return ClientConfig(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        max_retries=0,
        enable_cache=True,
        **extra,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("path", "fetch"),
    [
        ("/companies/fields", lambda http: CompanyService(http).get_fields()),
        ("/persons/fields", lambda http: PersonService(http).get_fields()),
        ("/lists/10/fields", lambda http: ListService(http).get_fields(ListId(10))),
    ],
)
def test_get_fields_follows_next_url_and_caches_merged(path: str, fetch) -> None:
    calls: list[str] = []
    http = HTTPClient(_config(transport=httpx.MockTransport(_two_page_handler(path, calls))))
    try:
        first = fetch(http)
        assert [str(f.id) for f in first] == ["field-1", "field-2", "field-3"]
        assert len(calls) == 2

        second = fetch(http)  # served from cache: the merged list, no refetch of page 2
        assert [str(f.id) for f in second] == ["field-1", "field-2", "field-3"]
        assert len(calls) == 2
    finally:
        http.close()


@pytest.mark.asyncio
async def test_async_get_fields_follows_next_url() -> None:
    calls: list[str] = []
    handler = _two_page_handler("/companies/fields", calls)
    http = AsyncHTTPClient(_config(async_transport=httpx.MockTransport(handler)))
    try:
        fields = await AsyncCompanyService(http).get_fields()
        assert [str(f.id) for f in fields] == ["field-1", "field-2", "field-3"]
        fields_again = await AsyncCompanyService(http).get_fields()
        assert len(fields_again) == 3
        assert len(calls) == 2
    finally:
        await http.close()
