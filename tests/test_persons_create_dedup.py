import asyncio

import httpx
import pytest

from affinity import Affinity, AsyncAffinity
from affinity.exceptions import DuplicateEntityError
from affinity.models.entities import PersonCreate


def _make_client(handler):
    return Affinity(
        api_key="test",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )


def _make_async_client(handler):
    """Return an AsyncAffinity context manager backed by a sync MockTransport."""
    return AsyncAffinity(
        api_key="test",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        max_retries=0,
        # httpx.MockTransport works for both sync and async because AsyncAffinity
        # wraps it in an async adapter internally (or uses it for mock purposes).
        async_transport=httpx.MockTransport(handler),
    )


def _person_search(matches):
    return {"persons": matches, "next_page_token": None}


@pytest.mark.req("REQ-DEDUP-002")
def test_person_create_raises_on_exact_email_match():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/persons" in url:
            return httpx.Response(
                200,
                json=_person_search(
                    [
                        {
                            "id": 500,
                            "firstName": "Alex",
                            "lastName": "Rivera",
                            "emails": ["alex@acme.com"],
                            "primaryEmailAddress": "alex@acme.com",
                        }
                    ]
                ),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        with pytest.raises(DuplicateEntityError) as exc_info:
            client.persons.create(
                PersonCreate(first_name="Alex", last_name="Rivera", emails=["alex@acme.com"])
            )
        assert exc_info.value.existing_id == 500
        assert exc_info.value.entity_type == "person"
    finally:
        client.close()


@pytest.mark.req("REQ-DEDUP-002")
def test_person_create_raises_on_exact_name_match_when_no_email():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/persons" in url:
            return httpx.Response(
                200,
                json=_person_search(
                    [
                        {
                            "id": 600,
                            "firstName": "Alex",
                            "lastName": "Rivera",
                            "emails": [],
                            "primaryEmailAddress": None,
                        }
                    ]
                ),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        with pytest.raises(DuplicateEntityError) as exc_info:
            client.persons.create(PersonCreate(first_name="Alex", last_name="Rivera", emails=[]))
        assert exc_info.value.existing_id == 600
    finally:
        client.close()


@pytest.mark.req("REQ-DEDUP-002")
def test_person_create_same_name_different_email_does_not_block():
    calls = {"search": 0, "post": 0}

    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/persons" in url:
            calls["search"] += 1
            return httpx.Response(
                200,
                json=_person_search(
                    [
                        {
                            "id": 700,
                            "firstName": "Alex",
                            "lastName": "Rivera",
                            "emails": ["alex@other.com"],
                            "primaryEmailAddress": "alex@other.com",
                        }
                    ]
                ),
                request=request,
            )
        if request.method == "POST" and "/persons" in url:
            calls["post"] += 1
            return httpx.Response(
                200,
                json={
                    "id": 800,
                    "firstName": "Alex",
                    "lastName": "Rivera",
                    "emails": ["alex@acme.com"],
                    "primaryEmailAddress": "alex@acme.com",
                },
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        result = client.persons.create(
            PersonCreate(first_name="Alex", last_name="Rivera", emails=["alex@acme.com"])
        )
        assert result.id == 800
        assert calls["post"] == 1
    finally:
        client.close()


@pytest.mark.req("REQ-DEDUP-002")
def test_person_create_skips_when_flag_false():
    calls = {"search": 0, "post": 0}

    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/persons" in url:
            calls["search"] += 1
            return httpx.Response(200, json=_person_search([]), request=request)
        if request.method == "POST" and "/persons" in url:
            calls["post"] += 1
            return httpx.Response(
                200,
                json={
                    "id": 900,
                    "firstName": "Alex",
                    "lastName": "Rivera",
                    "emails": [],
                    "primaryEmailAddress": None,
                },
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        result = client.persons.create(
            PersonCreate(first_name="Alex", last_name="Rivera", emails=[]),
            if_not_exists=False,
        )
        assert result.id == 900
        assert calls["search"] == 0
    finally:
        client.close()


@pytest.mark.req("REQ-DEDUP-002")
def test_async_person_create_raises_on_email_match():
    async def run():
        def handler(request):
            url = str(request.url)
            if request.method == "GET" and "/persons" in url:
                return httpx.Response(
                    200,
                    json=_person_search(
                        [
                            {
                                "id": 42,
                                "firstName": "A",
                                "lastName": "B",
                                "emails": ["a@b.com"],
                                "primaryEmailAddress": "a@b.com",
                            }
                        ]
                    ),
                    request=request,
                )
            raise AssertionError(f"Unexpected call: {request.method} {url}")

        async with _make_async_client(handler) as client:
            with pytest.raises(DuplicateEntityError) as exc_info:
                await client.persons.create(
                    PersonCreate(first_name="A", last_name="B", emails=["a@b.com"])
                )
            assert exc_info.value.existing_id == 42

    asyncio.run(run())
