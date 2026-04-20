"""
Tests for get_first() convenience method across all services.
"""

from __future__ import annotations

import httpx
import pytest

from affinity import Affinity, AsyncAffinity, F
from affinity.types import CompanyId, ListEntryId, ListId

_TS = "2020-01-01T00:00:00Z"


def _entry(
    entry_id: int,
    company_id: int,
    company_name: str,
    field_value: str,
) -> dict:
    """Build a minimal valid ListEntryWithEntity JSON payload."""
    return {
        "id": entry_id,
        "listId": 1,
        "type": "company",
        "createdAt": _TS,
        "entity": {
            "id": company_id,
            "name": company_name,
            # fields as list on entity -> stored as fields_raw for filter matching
            "fields": [{"name": "Status", "value": {"data": field_value}}],
        },
        "fields": [],
    }


@pytest.mark.req("SDK-GET-FIRST")
def test_get_first_returns_first_match() -> None:
    """get_first() should return first matching item."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": 1, "name": "Acme"}], "pagination": {}})

    transport = httpx.MockTransport(handler)
    with Affinity(api_key="test", max_retries=0, transport=transport) as client:
        company = client.companies.get_first()
        assert company is not None
        assert company.id == CompanyId(1)


@pytest.mark.req("SDK-GET-FIRST")
def test_get_first_returns_none_when_empty() -> None:
    """get_first() should return None when no results."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "pagination": {}})

    transport = httpx.MockTransport(handler)
    with Affinity(api_key="test", max_retries=0, transport=transport) as client:
        company = client.companies.get_first()
        assert company is None


def test_get_first_rejects_filter() -> None:
    """get_first() must raise ValueError — V2 /companies ignores filter server-side."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)

    transport = httpx.MockTransport(handler)
    with (
        Affinity(api_key="test", max_retries=0, transport=transport) as client,
        pytest.raises(ValueError, match="does not support server-side filter"),
    ):
        client.companies.get_first(filter=F.field("name").equals("Acme"))


@pytest.mark.req("SDK-GET-FIRST")
def test_list_entry_get_first_paginates_for_client_side_filter() -> None:
    """ListEntryService.get_first() should iterate pages to find client-side match."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "cursor=" not in url:
            # Page 1: no matching entry (Status=No)
            return httpx.Response(
                200,
                json={
                    "data": [_entry(10, 100, "X", "No")],
                    "pagination": {"nextUrl": f"{request.url}?cursor=page2"},
                },
            )
        # Page 2: matching entry (Status=Yes)
        return httpx.Response(
            200,
            json={
                "data": [_entry(20, 200, "Y", "Yes")],
                "pagination": {},
            },
        )

    transport = httpx.MockTransport(handler)
    with Affinity(api_key="test", max_retries=0, transport=transport) as client:
        entry = client.lists.entries(ListId(1)).get_first(
            filter=F.field("Status").equals("Yes"),
        )
        assert entry is not None
        assert entry.id == ListEntryId(20)


@pytest.mark.req("SDK-GET-FIRST")
@pytest.mark.asyncio
async def test_async_list_entry_get_first_paginates() -> None:
    """AsyncListEntryService.get_first() should iterate pages via async for."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "cursor=" not in url:
            return httpx.Response(
                200,
                json={
                    "data": [_entry(10, 100, "X", "No")],
                    "pagination": {"nextUrl": f"{request.url}?cursor=page2"},
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [_entry(20, 200, "Y", "Yes")],
                "pagination": {},
            },
        )

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test",
        max_retries=0,
        async_transport=async_transport,
    ) as client:
        entry = await client.lists.entries(ListId(1)).get_first(
            filter=F.field("Status").equals("Yes"),
        )
        assert entry is not None
        assert entry.id == ListEntryId(20)
