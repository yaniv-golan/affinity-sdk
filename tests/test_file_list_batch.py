"""Tests for AsyncEntityFileService.list_batch()."""

from __future__ import annotations

import httpx
import pytest

from affinity import AsyncAffinity
from affinity.exceptions import AffinityError
from affinity.types import CompanyId, OpportunityId, PersonId


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_fetches_for_all_companies() -> None:
    """list_batch() should fetch files for all given company IDs."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "organization_id=1" in url:
            return httpx.Response(
                200,
                json={
                    "entity_files": [
                        {
                            "id": 10,
                            "name": "pitch.pdf",
                            "size": 1024,
                            "uploaderId": 10,
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        if "organization_id=2" in url:
            return httpx.Response(
                200,
                json={
                    "entity_files": [
                        {
                            "id": 20,
                            "name": "deck.pdf",
                            "size": 2048,
                            "uploaderId": 10,
                            "createdAt": "2026-01-01T00:00:00Z",
                        },
                        {
                            "id": 21,
                            "name": "logo.png",
                            "size": 512,
                            "uploaderId": 10,
                            "createdAt": "2026-01-01T00:00:00Z",
                        },
                    ]
                },
            )
        return httpx.Response(200, json={"entity_files": []})

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        results = await client.files.list_batch(
            company_ids=[CompanyId(1), CompanyId(2), CompanyId(3)],
            max_concurrent=2,
        )

    assert len(results) == 3
    assert len(results[1]) == 1
    assert results[1][0].name == "pitch.pdf"
    assert len(results[2]) == 2
    assert len(results[3]) == 0


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_empty_input() -> None:
    """list_batch() with empty IDs should return empty dict."""

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Unexpected API call")

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        results = await client.files.list_batch(company_ids=[])
    assert results == {}


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_requires_exactly_one_entity_type() -> None:
    """list_batch() should reject ambiguous or missing entity type."""
    async_transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        with pytest.raises(ValueError, match="Exactly one"):
            await client.files.list_batch()

        with pytest.raises(ValueError, match="Exactly one"):
            await client.files.list_batch(person_ids=[PersonId(1)], company_ids=[CompanyId(2)])


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_on_error_skip() -> None:
    """list_batch(on_error='skip') leaves a failed entity out (its files are unknown)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "person_id=2" in url:
            return httpx.Response(500, json={"error": "Server error"})
        return httpx.Response(200, json={"entity_files": []})

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        results = await client.files.list_batch(
            person_ids=[PersonId(1), PersonId(2), PersonId(3)],
            on_error="skip",
        )

    assert results == {1: [], 3: []}


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_on_error_raise() -> None:
    """list_batch(on_error='raise') should raise and cancel remaining tasks."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "person_id=2" in url:
            return httpx.Response(500, json={"error": "Server error"})
        return httpx.Response(200, json={"entity_files": []})

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        with pytest.raises(AffinityError):
            await client.files.list_batch(
                person_ids=[PersonId(1), PersonId(2)],
                on_error="raise",
            )


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_deduplicates_entity_ids() -> None:
    """list_batch() should deduplicate IDs and make one API call per unique ID."""
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"entity_files": []})

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        results = await client.files.list_batch(
            company_ids=[CompanyId(1), CompanyId(1), CompanyId(2)],
        )

    assert len(results) == 2
    assert 1 in results
    assert 2 in results
    assert call_count == 2


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_rejects_invalid_max_concurrent() -> None:
    """list_batch() should reject max_concurrent < 1."""
    async_transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        with pytest.raises(ValueError, match="max_concurrent"):
            await client.files.list_batch(
                company_ids=[CompanyId(1)],
                max_concurrent=0,
            )


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_handles_pagination() -> None:
    """list_batch() should auto-paginate through multiple pages per entity."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "organization_id=1" in url and "page_token=" not in url:
            return httpx.Response(
                200,
                json={
                    "entity_files": [
                        {
                            "id": 10,
                            "name": "file1.pdf",
                            "size": 100,
                            "uploaderId": 10,
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "next_page_token": "page2",
                },
            )
        if "organization_id=1" in url and "page_token=page2" in url:
            return httpx.Response(
                200,
                json={
                    "entity_files": [
                        {
                            "id": 11,
                            "name": "file2.pdf",
                            "size": 200,
                            "uploaderId": 10,
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"entity_files": []})

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        results = await client.files.list_batch(
            company_ids=[CompanyId(1)],
        )

    assert len(results[1]) == 2
    assert results[1][0].name == "file1.pdf"
    assert results[1][1].name == "file2.pdf"


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_with_opportunity_ids() -> None:
    """list_batch() should work with opportunity_ids parameter."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "opportunity_id=100" in url:
            return httpx.Response(
                200,
                json={
                    "entity_files": [
                        {
                            "id": 50,
                            "name": "proposal.pdf",
                            "size": 4096,
                            "uploaderId": 10,
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"entity_files": []})

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        results = await client.files.list_batch(
            opportunity_ids=[OpportunityId(100), OpportunityId(200)],
        )

    assert len(results) == 2
    assert len(results[100]) == 1
    assert results[100][0].name == "proposal.pdf"
    assert results[200] == []


@pytest.mark.req("SDK-FILE-LIST-BATCH")
@pytest.mark.asyncio
async def test_list_batch_on_error_skip_mid_pagination() -> None:
    """A failure on page 2 leaves the entity out: page 1 alone would pass for all its files."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "organization_id=1" in url and "page_token=" not in url:
            return httpx.Response(
                200,
                json={
                    "entity_files": [
                        {
                            "id": 10,
                            "name": "page1.pdf",
                            "size": 100,
                            "uploaderId": 10,
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "next_page_token": "page2",
                },
            )
        if "organization_id=1" in url and "page_token=page2" in url:
            return httpx.Response(500, json={"error": "Server error"})
        return httpx.Response(200, json={"entity_files": []})

    async_transport = httpx.MockTransport(handler)
    async with AsyncAffinity(
        api_key="test", max_retries=0, async_transport=async_transport
    ) as client:
        results = await client.files.list_batch(
            company_ids=[CompanyId(1), CompanyId(2)],
            on_error="skip",
        )

    assert results == {2: []}
