"""batch_get with interaction dates and field filters: V2 fields per 100 ids, V1 dates per id."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from affinity import AsyncAffinity
from affinity.exceptions import NotFoundError
from affinity.models.types import CompanyId, FieldType, PersonId

DATES = {"last_event_date": "2026-01-02T00:00:00Z", "next_event_date": "2026-03-04T00:00:00Z"}


def _handler(kind: str, v1_kind: str, calls: list[httpx.URL], *, fail_v2: bool = False) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        calls.append(url)
        if url.path == f"/v2/{kind}":
            if fail_v2:
                return httpx.Response(500, json={"error": "boom"})
            ids = [int(i) for i in url.params.get_list("ids")]
            data = [{"id": i, "name": f"R{i}", "fields": []} for i in ids if i != 404]
            return httpx.Response(200, json={"data": data, "pagination": {"nextUrl": None}})
        if url.path.startswith(f"/v2/{kind}/"):  # per-id V2 (the get() fallback)
            record_id = int(url.path.rsplit("/", 1)[1])
            if record_id == 404:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(
                200, json={"id": record_id, "name": f"R{record_id}", "fields": []}
            )
        if url.path.startswith(f"/{v1_kind}/"):
            record_id = int(url.path.rsplit("/", 1)[1])
            if record_id == 404:
                return httpx.Response(404, json={"message": "not found"})
            body: dict[str, Any] = {"id": record_id, "interaction_dates": DATES}
            if v1_kind == "persons":
                body.update({"first_name": "A", "last_name": "B", "type": 0})
            else:
                body["name"] = f"R{record_id}"
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": f"unexpected {url}"})

    return handler


def _client(handler: Any) -> AsyncAffinity:
    return AsyncAffinity(
        api_key="k",
        max_retries=0,
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        async_transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "kind", "v1_kind", "id_cls"),
    [
        ("companies", "companies", "organizations", CompanyId),
        ("persons", "persons", "persons", PersonId),
    ],
)
async def test_fields_come_from_one_call_per_100_ids(
    service: str, kind: str, v1_kind: str, id_cls: Any
) -> None:
    calls: list[httpx.URL] = []
    ids = [id_cls(i) for i in range(1, 151)]
    async with _client(_handler(kind, v1_kind, calls)) as client:
        result = await getattr(client, service).batch_get(
            ids,
            field_types=[FieldType.RELATIONSHIP_INTELLIGENCE],
            with_interaction_dates=True,
        )
    assert sorted(result) == ids
    assert result[ids[0]].interaction_dates is not None
    assert result[ids[0]].interaction_dates.last_event_date is not None
    v2_calls = [c for c in calls if c.path == f"/v2/{kind}"]
    assert [len(c.params.get_list("ids")) for c in v2_calls] == [100, 50]
    assert v2_calls[0].params.get_list("fieldTypes") == ["relationship-intelligence"]
    assert len([c for c in calls if c.path.startswith(f"/{v1_kind}/")]) == 150
    assert len(calls) == 152  # was 300: two calls per id


@pytest.mark.asyncio
async def test_a_record_the_fields_call_did_not_return_is_not_found() -> None:
    calls: list[httpx.URL] = []
    async with _client(_handler("companies", "organizations", calls)) as client:
        ids = [CompanyId(1), CompanyId(404)]
        skipped = await client.companies.batch_get(
            ids, field_types=[FieldType.GLOBAL], with_interaction_dates=True, on_error="skip"
        )
        assert list(skipped) == [CompanyId(1)]
        with pytest.raises(NotFoundError):
            await client.companies.batch_get(
                ids, field_types=[FieldType.GLOBAL], with_interaction_dates=True
            )


@pytest.mark.asyncio
async def test_a_failed_fields_call_falls_back_to_one_get_per_id_under_skip() -> None:
    calls: list[httpx.URL] = []
    handler = _handler("companies", "organizations", calls, fail_v2=True)
    async with _client(handler) as client:
        result = await client.companies.batch_get(
            [CompanyId(1), CompanyId(404), CompanyId(2)],
            field_ids=["field-1"],
            with_interaction_dates=True,
            on_error="skip",
        )
    assert sorted(result) == [CompanyId(1), CompanyId(2)]
    assert result[CompanyId(1)].interaction_dates is not None


@pytest.mark.asyncio
async def test_without_field_filters_it_is_one_v1_call_per_id() -> None:
    calls: list[httpx.URL] = []
    async with _client(_handler("companies", "organizations", calls)) as client:
        result = await client.companies.batch_get(
            [CompanyId(1), CompanyId(2)], with_interaction_dates=True
        )
    assert sorted(result) == [CompanyId(1), CompanyId(2)]
    assert all(c.path.startswith("/organizations/") for c in calls)
