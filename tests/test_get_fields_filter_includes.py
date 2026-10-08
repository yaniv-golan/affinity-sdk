"""get_fields(filter=..., includes=...) on the V2 ``*/fields`` endpoints (P1.3).

The spec gives every fields endpoint a ``filter`` string (``name="X"`` exact, ``name=~X``
substring) and a repeatable ``includes`` (``filterability``, ``sortability``). Each distinct
parameter set must get its own cache entry, and the default call keeps its old cache key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.services.companies import AsyncCompanyService, CompanyService
from affinity.services.lists import AsyncListService, ListService
from affinity.services.persons import AsyncPersonService, PersonService
from affinity.types import ListId


def _config(**extra: object) -> ClientConfig:
    return ClientConfig(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        max_retries=0,
        enable_cache=True,
        **extra,  # type: ignore[arg-type]
    )


def _handler(requests: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        field: dict[str, Any] = {
            "id": "field-1",
            "name": "Location",
            "valueType": "location",
            "type": "global",
        }
        includes = request.url.params.get_list("includes")
        if "filterability" in includes:
            field["filterability"] = {"operators": ["="]}
        if "sortability" in includes:
            field["sortability"] = None
        return httpx.Response(
            200, json={"data": [field], "pagination": {"nextUrl": None}}, request=request
        )

    return handler


SYNC_FETCHERS: list[tuple[str, Callable[..., Any]]] = [
    ("/companies/fields", lambda http, **kw: CompanyService(http).get_fields(**kw)),
    ("/persons/fields", lambda http, **kw: PersonService(http).get_fields(**kw)),
    ("/lists/10/fields", lambda http, **kw: ListService(http).get_fields(ListId(10), **kw)),
]

ASYNC_FETCHERS: list[tuple[str, Callable[..., Any]]] = [
    ("/companies/fields", lambda http, **kw: AsyncCompanyService(http).get_fields(**kw)),
    ("/persons/fields", lambda http, **kw: AsyncPersonService(http).get_fields(**kw)),
    (
        "/lists/10/fields",
        lambda http, **kw: AsyncListService(http).get_fields(ListId(10), **kw),
    ),
]


@pytest.mark.parametrize(("path", "fetch"), SYNC_FETCHERS)
def test_get_fields_sends_filter_and_repeated_includes(path: str, fetch: Any) -> None:
    requests: list[httpx.Request] = []
    http = HTTPClient(_config(transport=httpx.MockTransport(_handler(requests))))
    try:
        fields = fetch(http, filter='name="Location"', includes=["filterability", "sortability"])
    finally:
        http.close()

    assert len(requests) == 1
    url = requests[0].url
    assert url.path.endswith(path)
    assert url.params.get("filter") == 'name="Location"'
    # Repeated, not comma-joined: includes=filterability&includes=sortability
    assert url.params.get_list("includes") == ["filterability", "sortability"]
    assert fields[0].is_filterable is True
    assert fields[0].is_sortable is False


@pytest.mark.parametrize(("path", "fetch"), SYNC_FETCHERS)
def test_get_fields_default_sends_no_filter_or_includes(path: str, fetch: Any) -> None:
    requests: list[httpx.Request] = []
    http = HTTPClient(_config(transport=httpx.MockTransport(_handler(requests))))
    try:
        fields = fetch(http)
    finally:
        http.close()

    assert requests[0].url.path.endswith(path)
    assert "filter" not in requests[0].url.params
    assert "includes" not in requests[0].url.params
    assert fields[0].is_filterable is None


@pytest.mark.parametrize(("path", "fetch"), SYNC_FETCHERS)
def test_get_fields_cache_key_covers_every_param(path: str, fetch: Any) -> None:
    requests: list[httpx.Request] = []
    http = HTTPClient(_config(transport=httpx.MockTransport(_handler(requests))))
    try:
        fetch(http)
        assert requests[0].url.path.endswith(path)
        fetch(http, filter="name=~Loc")
        fetch(http, filter='name="Location"')
        fetch(http, includes=["filterability"])
        fetch(http, includes=["sortability"])
        fetch(http, filter="name=~Loc", includes=["filterability"])
        assert len(requests) == 6  # every distinct parameter set hit the API

        # Same parameters again (includes in another order) -> all served from cache
        fetch(http)
        fetch(http, filter="name=~Loc")
        fetch(http, includes=["filterability"])
        fetch(http, filter="name=~Loc", includes=["filterability"])
        assert len(requests) == 6
    finally:
        http.close()


def test_get_fields_includes_order_shares_cache_entry() -> None:
    requests: list[httpx.Request] = []
    http = HTTPClient(_config(transport=httpx.MockTransport(_handler(requests))))
    try:
        svc = CompanyService(http)
        svc.get_fields(includes=["filterability", "sortability"])
        svc.get_fields(includes=["sortability", "filterability"])
        assert len(requests) == 1
    finally:
        http.close()


def test_get_fields_default_cache_keys_unchanged() -> None:
    """Callers and tests that seed/inspect the cache by key keep working."""
    requests: list[httpx.Request] = []
    http = HTTPClient(_config(transport=httpx.MockTransport(_handler(requests))))
    try:
        CompanyService(http).get_fields()
        PersonService(http).get_fields()
        ListService(http).get_fields(ListId(10))
        assert http.cache is not None
        suffix = http._cache_suffix
        assert http.cache.get(f"company_fields:_all_{suffix}") is not None
        assert http.cache.get(f"person_fields:{suffix}") is not None
        assert http.cache.get(f"list_10_fields:{suffix}") is not None
    finally:
        http.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "fetch"), ASYNC_FETCHERS)
async def test_async_get_fields_sends_filter_and_includes(path: str, fetch: Any) -> None:
    requests: list[httpx.Request] = []
    http = AsyncHTTPClient(_config(async_transport=httpx.MockTransport(_handler(requests))))
    try:
        fields = await fetch(http, filter="name=~Loc", includes=["filterability"])
        await fetch(http, filter="name=~Loc", includes=["filterability"])  # cached
        await fetch(http)  # different params -> new request
    finally:
        await http.close()

    assert len(requests) == 2
    url = requests[0].url
    assert url.path.endswith(path)
    assert url.params.get("filter") == "name=~Loc"
    assert url.params.get_list("includes") == ["filterability"]
    assert fields[0].is_filterable is True
    assert "filter" not in requests[1].url.params


# The committed spec digest lists every operation's declared parameters; the API silently
# drops unknown query parameters, so sending one (as ``fieldTypes`` once was) is a no-op bug.
_SPEC_OPERATIONS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "tools" / "openapi_snapshot.json").read_text()
)["digest"]["operations"]

_SPEC_OPERATION_IDS = {
    "/companies/fields": "GET /v2/companies/fields",
    "/persons/fields": "GET /v2/persons/fields",
    "/lists/10/fields": "GET /v2/lists/{listId}/fields",
}


def _assert_declared_query_params(path: str, request: httpx.Request) -> None:
    declared = {
        name.removeprefix("query:")
        for name in _SPEC_OPERATIONS[_SPEC_OPERATION_IDS[path]]["parameters"]
        if name.startswith("query:")
    }
    sent = set(request.url.params.keys())
    assert sent, "expected the call to send query parameters"
    assert sent <= declared, f"{path} sends undeclared query params {sorted(sent - declared)}"


@pytest.mark.parametrize(("path", "fetch"), SYNC_FETCHERS)
def test_get_fields_sends_only_spec_declared_params(path: str, fetch: Any) -> None:
    requests: list[httpx.Request] = []
    http = HTTPClient(_config(transport=httpx.MockTransport(_handler(requests))))
    try:
        fetch(http, filter='name="Location"', includes=["filterability", "sortability"])
    finally:
        http.close()

    _assert_declared_query_params(path, requests[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "fetch"), ASYNC_FETCHERS)
async def test_async_get_fields_sends_only_spec_declared_params(path: str, fetch: Any) -> None:
    requests: list[httpx.Request] = []
    http = AsyncHTTPClient(_config(async_transport=httpx.MockTransport(_handler(requests))))
    try:
        await fetch(http, filter='name="Location"', includes=["filterability", "sortability"])
    finally:
        await http.close()

    _assert_declared_query_params(path, requests[0])
