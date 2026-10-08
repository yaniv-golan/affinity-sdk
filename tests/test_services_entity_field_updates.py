"""CompanyService / PersonService field reads and one-request field writes (V2)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from affinity.api_versions import AFFINITY_API_VERSION_HEADER
from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.exceptions import ApiVersionTooOldError
from affinity.services.companies import AsyncCompanyService, CompanyService
from affinity.services.persons import AsyncPersonService, PersonService
from affinity.types import CompanyId, PersonId

V2 = "https://v2.example/v2"


def _handler(seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            body = {
                "data": [{"id": "field-1", "value": {"type": "text", "data": "x"}}],
                "pagination": {"nextUrl": None},
            }
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"operation": "update-fields"})

    return handler


def _config(seen: list[httpx.Request], **extra: Any) -> ClientConfig:
    return ClientConfig(
        api_key="k",
        v1_base_url="https://v1.example",
        v2_base_url=V2,
        max_retries=0,
        transport=httpx.MockTransport(_handler(seen)),
        async_transport=httpx.MockTransport(_handler(seen)),
        **extra,
    )


@pytest.mark.parametrize(
    ("service_cls", "entity_id", "path"),
    [(CompanyService, CompanyId(5), "companies"), (PersonService, PersonId(8), "persons")],
)
def test_batch_update_is_one_patch_at_the_minimum_version(
    service_cls: Any, entity_id: int, path: str
) -> None:
    seen: list[httpx.Request] = []
    with HTTPClient(_config(seen)) as http:
        result = service_cls(http).batch_update_fields(
            entity_id,
            {"field-1": "a", "field-2": None, "field-3": ["Go", "Rust"]},
            value_types={"field-2": "dropdown", "field-3": "filterable-text-multi"},
        )
    assert result.operation == "update-fields"
    (request,) = seen
    assert request.method == "PATCH"
    assert request.url.path == f"/v2/{path}/{entity_id}/fields"
    assert request.headers[AFFINITY_API_VERSION_HEADER] == "2026-07-15"
    assert json.loads(request.content) == {
        "operation": "update-fields",
        "updates": [
            {"id": "field-1", "value": {"type": "text", "data": "a"}},
            {"id": "field-2", "value": {"type": "dropdown", "data": None}},
            {"id": "field-3", "value": {"type": "filterable-text-multi", "data": ["Go", "Rust"]}},
        ],
    }


def test_batch_update_refuses_duplicates_untyped_lists_and_too_many() -> None:
    with HTTPClient(_config([])) as http:
        svc = CompanyService(http)
        with pytest.raises(ValueError, match="more than once"):
            svc.batch_update_fields(CompanyId(5), {"field-1": "a", 1: "b"})
        with pytest.raises(ValueError, match="infer the type"):
            svc.batch_update_fields(CompanyId(5), {"field-1": ["a"]})
        with pytest.raises(ValueError, match="at most 100"):
            svc.batch_update_fields(CompanyId(5), {f"field-{i}": "x" for i in range(101)})


def test_pinned_too_old_raises_without_sending() -> None:
    seen: list[httpx.Request] = []
    with HTTPClient(_config(seen, affinity_api_version="2024-01-01")) as http:
        with pytest.raises(ApiVersionTooOldError):
            PersonService(http).batch_update_fields(PersonId(8), {"field-1": "a"})
        with pytest.raises(ApiVersionTooOldError):
            http.require_api_version("2026-07-15", operation="person field")
    assert seen == []


def test_get_field_values_by_id() -> None:
    seen: list[httpx.Request] = []
    with HTTPClient(_config(seen)) as http:
        fields = CompanyService(http).get_field_values(CompanyId(5), ids=["field-1", "field-2"])
    assert fields.data["field-1"]["value"]["data"] == "x"
    assert seen[0].url.path == "/v2/companies/5/fields"
    assert seen[0].url.params.get_list("ids") == ["field-1", "field-2"]


def test_async_parity() -> None:
    async def run() -> None:
        seen: list[httpx.Request] = []
        http = AsyncHTTPClient(_config(seen))
        try:
            await AsyncCompanyService(http).batch_update_fields(CompanyId(5), {"field-1": "a"})
            fields = await AsyncPersonService(http).get_field_values(PersonId(8))
        finally:
            await http.close()
        assert seen[0].headers[AFFINITY_API_VERSION_HEADER] == "2026-07-15"
        assert fields.data["field-1"]["value"]["data"] == "x"

    asyncio.run(run())
