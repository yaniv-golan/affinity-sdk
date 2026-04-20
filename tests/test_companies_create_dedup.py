import asyncio

import httpx
import pytest

from affinity import Affinity, AsyncAffinity
from affinity.exceptions import DuplicateEntityError
from affinity.models.entities import CompanyCreate


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


def _search_response(matches):
    return {
        "organizations": matches,
        "next_page_token": None,
    }


def test_create_raises_duplicate_on_exact_name_match():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url and "term=Elssway" in url:
            return httpx.Response(
                200,
                json=_search_response(
                    [{"id": 282327760, "name": "Elssway", "domain": "elssway.com"}]
                ),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        with pytest.raises(DuplicateEntityError) as exc_info:
            client.companies.create(CompanyCreate(name="Elssway"))
        assert exc_info.value.existing_id == 282327760
        assert exc_info.value.existing_name == "Elssway"
        assert exc_info.value.entity_type == "company"
    finally:
        client.close()


def test_create_raises_duplicate_on_domain_match():
    """Domain-first search order."""
    search_terms = []

    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(url).query)
            search_terms.append(qs.get("term", [""])[0])
            return httpx.Response(
                200,
                json=_search_response([{"id": 111, "name": "Acme Inc", "domain": "acme.com"}]),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        with pytest.raises(DuplicateEntityError) as exc_info:
            client.companies.create(CompanyCreate(name="Acme Corporation", domain="acme.com"))
        assert exc_info.value.existing_id == 111
        assert exc_info.value.existing_domain == "acme.com"
        assert search_terms == ["acme.com"]
    finally:
        client.close()


def test_create_matches_on_domains_plural_list():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            return httpx.Response(
                200,
                json=_search_response(
                    [
                        {
                            "id": 222,
                            "name": "Acme Inc",
                            "domain": "primary.com",
                            "domains": ["primary.com", "acme.com"],
                        }
                    ]
                ),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        with pytest.raises(DuplicateEntityError) as exc_info:
            client.companies.create(CompanyCreate(name="Whatever", domain="acme.com"))
        assert exc_info.value.existing_id == 222
    finally:
        client.close()


def test_create_flags_global_match():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            return httpx.Response(
                200,
                json=_search_response(
                    [{"id": 9999, "name": "Stripe", "domain": "stripe.com", "global": True}]
                ),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        with pytest.raises(DuplicateEntityError) as exc_info:
            client.companies.create(CompanyCreate(name="Stripe", domain="stripe.com"))
        assert exc_info.value.existing_is_global is True
        assert "global" in str(exc_info.value).lower()
    finally:
        client.close()


def test_create_succeeds_when_no_match():
    calls = {"search": 0, "post": 0}

    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            calls["search"] += 1
            return httpx.Response(200, json=_search_response([]), request=request)
        if request.method == "POST" and "/organizations" in url:
            calls["post"] += 1
            return httpx.Response(
                200, json={"id": 999, "name": "NewCo", "domain": "newco.com"}, request=request
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        result = client.companies.create(CompanyCreate(name="NewCo", domain="newco.com"))
        assert result.id == 999
        assert calls["search"] == 2
        assert calls["post"] == 1
    finally:
        client.close()


def test_create_skips_dedup_when_flag_false():
    calls = {"search": 0, "post": 0}

    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            calls["search"] += 1
            return httpx.Response(200, json=_search_response([]), request=request)
        if request.method == "POST" and "/organizations" in url:
            calls["post"] += 1
            return httpx.Response(
                200, json={"id": 1234, "name": "Elssway", "domain": None}, request=request
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        result = client.companies.create(CompanyCreate(name="Elssway"), if_not_exists=False)
        assert result.id == 1234
        assert calls["search"] == 0
        assert calls["post"] == 1
    finally:
        client.close()


def test_create_name_match_is_case_insensitive():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            return httpx.Response(
                200,
                json=_search_response([{"id": 77, "name": "ELSSWAY", "domain": None}]),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        with pytest.raises(DuplicateEntityError) as exc_info:
            client.companies.create(CompanyCreate(name="elssway"))
        assert exc_info.value.existing_id == 77
    finally:
        client.close()


def test_create_fuzzy_non_exact_matches_do_not_block():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            return httpx.Response(
                200,
                json=_search_response(
                    [
                        {"id": 1, "name": "Elssway Holdings", "domain": "elsswayholdings.com"},
                        {"id": 2, "name": "Elsswayland", "domain": None},
                    ]
                ),
                request=request,
            )
        if request.method == "POST" and "/organizations" in url:
            return httpx.Response(
                200, json={"id": 999, "name": "Elssway", "domain": None}, request=request
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    client = _make_client(handler)
    try:
        result = client.companies.create(CompanyCreate(name="Elssway"))
        assert result.id == 999
    finally:
        client.close()


# =============================================================================
# Async Tests
# =============================================================================


def test_async_create_raises_duplicate_on_exact_name_match():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            return httpx.Response(
                200,
                json=_search_response([{"id": 282327760, "name": "Elssway", "domain": None}]),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    async def run():
        async with _make_async_client(handler) as client:
            with pytest.raises(DuplicateEntityError) as exc_info:
                await client.companies.create(CompanyCreate(name="Elssway"))
            assert exc_info.value.existing_id == 282327760
            assert exc_info.value.entity_type == "company"

    asyncio.run(run())


def test_async_create_flags_global_match():
    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            return httpx.Response(
                200,
                json=_search_response(
                    [{"id": 9999, "name": "Stripe", "domain": "stripe.com", "global": True}]
                ),
                request=request,
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    async def run():
        async with _make_async_client(handler) as client:
            with pytest.raises(DuplicateEntityError) as exc_info:
                await client.companies.create(CompanyCreate(name="Stripe", domain="stripe.com"))
            assert exc_info.value.existing_is_global is True

    asyncio.run(run())


def test_async_create_skips_dedup_when_flag_false():
    calls = {"search": 0, "post": 0}

    def handler(request):
        url = str(request.url)
        if request.method == "GET" and "/organizations" in url:
            calls["search"] += 1
            return httpx.Response(200, json=_search_response([]), request=request)
        if request.method == "POST" and "/organizations" in url:
            calls["post"] += 1
            return httpx.Response(
                200, json={"id": 1234, "name": "Elssway", "domain": None}, request=request
            )
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    async def run():
        async with _make_async_client(handler) as client:
            result = await client.companies.create(
                CompanyCreate(name="Elssway"), if_not_exists=False
            )
            assert result.id == 1234
            assert calls["search"] == 0
            assert calls["post"] == 1

    asyncio.run(run())
