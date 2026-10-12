"""
Tests for ReminderService.list_batch() / AsyncReminderService.list_batch() (V2: one OR-filter
request per chunk of ids).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from affinity import AsyncAffinity
from affinity.exceptions import AffinityError
from affinity.services.reminders import BATCH_CHUNK
from affinity.types import CompanyId, PersonId, ReminderStatus


def _reminder(rid: int, **entity: Any) -> dict[str, Any]:
    return {
        "id": rid,
        "type": "one-time",
        "status": "active",
        "content": None,
        "dueDate": "2026-11-01T09:00:00Z",
        "recurrence": None,
        "creator": {"id": 1},
        "owner": {"id": 1},
        "completer": None,
        "person": None,
        "company": None,
        "opportunity": None,
        "createdAt": "2026-10-01T09:00:00Z",
        "updatedAt": None,
        "completedAt": None,
        **{k: {"id": v} for k, v in entity.items()},
    }


def _page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": None, "prevUrl": None}}


@pytest.mark.req("SDK-REMINDER-LIST-BATCH")
async def test_list_batch_fetches_for_all_companies() -> None:
    """Every company id is a key; reminders are grouped by their company."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["filter"])
        return httpx.Response(200, json=_page([_reminder(10, company=1), _reminder(11, company=1)]))

    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=httpx.MockTransport(handler)
    ) as client:
        results = await client.reminders.list_batch(company_ids=[CompanyId(1), CompanyId(2)])

    assert {k: [r.id for r in v] for k, v in results.items()} == {1: [10, 11], 2: []}
    assert seen == ["(company.id=1 | company.id=2)"]


@pytest.mark.req("SDK-REMINDER-LIST-BATCH")
async def test_list_batch_empty_input() -> None:
    async with AsyncAffinity(api_key="test", max_retries=0) as client:
        assert await client.reminders.list_batch(person_ids=[]) == {}


@pytest.mark.req("SDK-REMINDER-LIST-BATCH")
async def test_list_batch_requires_exactly_one_entity_type() -> None:
    async with AsyncAffinity(api_key="test", max_retries=0) as client:
        with pytest.raises(ValueError, match="Exactly one"):
            await client.reminders.list_batch()
        with pytest.raises(ValueError, match="Exactly one"):
            await client.reminders.list_batch(person_ids=[PersonId(1)], company_ids=[CompanyId(1)])


@pytest.mark.req("SDK-REMINDER-LIST-BATCH")
async def test_list_batch_passes_filters() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["filter"])
        return httpx.Response(200, json=_page([]))

    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=httpx.MockTransport(handler)
    ) as client:
        await client.reminders.list_batch(person_ids=[PersonId(1)], status=ReminderStatus.ACTIVE)
    assert seen == ["person.id=1 & status=active"]


@pytest.mark.req("SDK-REMINDER-LIST-BATCH")
async def test_list_batch_chunks_ids() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["filter"])
        return httpx.Response(200, json=_page([]))

    ids = [PersonId(i) for i in range(1, BATCH_CHUNK + 2)]
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=httpx.MockTransport(handler)
    ) as client:
        results = await client.reminders.list_batch(person_ids=ids)
    assert len(seen) == 2
    assert len(results) == BATCH_CHUNK + 1


@pytest.mark.req("SDK-REMINDER-LIST-BATCH")
async def test_list_batch_on_error_skip() -> None:
    """A failed chunk is skipped: its ids keep empty lists."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "person.id=1" in request.url.params["filter"]:
            return httpx.Response(500, json={"error": "Server error"})
        return httpx.Response(200, json=_page([_reminder(30, person=BATCH_CHUNK + 1)]))

    ids = [PersonId(i) for i in range(1, BATCH_CHUNK + 2)]
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=httpx.MockTransport(handler)
    ) as client:
        results = await client.reminders.list_batch(person_ids=ids, on_error="skip")
    assert results[1] == []
    assert [r.id for r in results[BATCH_CHUNK + 1]] == [30]


@pytest.mark.req("SDK-REMINDER-LIST-BATCH")
async def test_list_batch_on_error_raise() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Server error"})

    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AffinityError):
            await client.reminders.list_batch(person_ids=[PersonId(1), PersonId(2)])
