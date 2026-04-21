"""
Tests for the HTTP client and service layer.
"""

import gc
import warnings
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]
from httpx import Headers, Response

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

from affinity import Affinity, AffinityError, AsyncAffinity, NotFoundError, RateLimitError
from affinity.clients.http import (
    REPEATABLE_QUERY_PARAMS,
    AsyncHTTPClient,
    ClientConfig,
    HTTPClient,
    RateLimitState,
    SimpleCache,
    _encode_query_params,
    _freeze_v1_query_signature,
)
from affinity.exceptions import BetaEndpointDisabledError, UnsafeUrlError
from affinity.models import (
    CompanyCreate,
    ListCreate,
)
from affinity.services.companies import AsyncCompanyService
from affinity.services.lists import AsyncListService, ListService
from affinity.types import CompanyId, ListId, ListType, OpportunityId, PersonId, PersonType

# =============================================================================
# Rate Limit State Tests
# =============================================================================


@pytest.mark.req("NFR-003")
class TestRateLimitState:
    """Test rate limit tracking."""

    def test_default_values(self) -> None:
        """Test default rate limit values (unknown until observed)."""
        state = RateLimitState()
        assert state.user_limit is None
        assert state.user_remaining is None
        assert state.org_limit is None

    @pytest.mark.req("NFR-003b")
    def test_update_from_headers(self) -> None:
        """Test rate limit state updates from response headers."""
        state = RateLimitState()
        headers = Headers(
            {
                "X-Ratelimit-Limit-User": "900",
                "X-Ratelimit-Limit-User-Remaining": "850",
                "X-Ratelimit-Limit-User-Reset": "45",
                "X-Ratelimit-Limit-Org": "100000",
                "X-Ratelimit-Limit-Org-Remaining": "99500",
            }
        )

        state.update_from_headers(headers)

        assert state.user_remaining == 850
        assert state.user_reset_seconds == 45
        assert state.org_remaining == 99500
        assert state.last_updated is not None

    def test_should_throttle(self) -> None:
        """Test throttle detection."""
        state = RateLimitState()
        assert not state.should_throttle

        state.user_remaining = 40
        assert state.should_throttle

        state.user_remaining = 100
        state.org_remaining = 500
        assert state.should_throttle


# =============================================================================
# Simple Cache Tests
# =============================================================================


@pytest.mark.req("NFR-002")
class TestSimpleCache:
    """Test the simple TTL cache."""

    def test_get_set(self) -> None:
        """Test basic get/set operations."""
        cache = SimpleCache(default_ttl=300)

        cache.set("key1", {"data": "value"})
        result = cache.get("key1")

        assert result == {"data": "value"}

    def test_get_missing_key(self) -> None:
        """Test getting non-existent key."""
        cache = SimpleCache()
        assert cache.get("nonexistent") is None

    def test_clear(self) -> None:
        """Test clearing cache."""
        cache = SimpleCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_invalidate_prefix(self) -> None:
        """Test prefix invalidation."""
        cache = SimpleCache()
        cache.set("companies:fields", "data1")
        cache.set("companies:metadata", "data2")
        cache.set("persons:fields", "data3")

        cache.invalidate_prefix("companies:")

        assert cache.get("companies:fields") is None
        assert cache.get("companies:metadata") is None
        assert cache.get("persons:fields") == "data3"


# =============================================================================
# HTTP Client Tests
# =============================================================================


class TestHTTPClient:
    """Test HTTP client functionality."""

    @pytest.mark.req("TR-010")
    @pytest.mark.req("TR-010a")
    def test_default_timeout_and_limits_are_finite_and_stable(self) -> None:
        config = ClientConfig(api_key="test-key")
        assert isinstance(config.timeout, httpx.Timeout)
        assert config.timeout.connect == 10.0
        assert config.timeout.read == 30.0
        assert config.timeout.write == 30.0
        assert config.timeout.pool == 10.0

        assert isinstance(config.limits, httpx.Limits)
        assert config.limits.max_connections == 20
        assert config.limits.max_keepalive_connections == 10
        assert config.limits.keepalive_expiry == 30.0

    @pytest.mark.req("TR-010")
    def test_timeout_can_be_overridden_with_seconds(self) -> None:
        config = ClientConfig(api_key="test-key", timeout=5.0)
        assert isinstance(config.timeout, httpx.Timeout)
        assert config.timeout.connect == 5.0
        assert config.timeout.read == 5.0
        assert config.timeout.write == 5.0
        assert config.timeout.pool == 5.0

    @pytest.mark.req("TR-006")
    def test_transport_injection_with_mock_transport(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL("https://api.affinity.co/v2/companies/123"):
                return httpx.Response(200, json={"id": 123, "name": "Acme"})
            return httpx.Response(404, json={"message": "not found"})

        transport = httpx.MockTransport(handler)
        http_client = HTTPClient(ClientConfig(api_key="k", max_retries=0, transport=transport))
        try:
            data = http_client.get("/companies/123")
        finally:
            http_client.close()

        assert data["id"] == 123

    @pytest.mark.req("FR-001")
    @pytest.mark.req("TR-007")
    @pytest.mark.req("TR-007a")
    def test_get_request(self, respx_mock: respx.MockRouter) -> None:
        """Test basic GET request."""
        config = ClientConfig(api_key="test-key")
        client = HTTPClient(config)

        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(200, json={"id": 123, "name": "Acme Corp"})
        )

        result = client.get("/companies/123")

        assert result == {"id": 123, "name": "Acme Corp"}
        client.close()

    @pytest.mark.req("FR-001")
    @pytest.mark.req("TR-007")
    def test_v1_url_routing(self, respx_mock: respx.MockRouter) -> None:
        """Test V1 API URL routing."""
        config = ClientConfig(api_key="test-key")
        client = HTTPClient(config)

        respx_mock.get("https://api.affinity.co/organizations/456").mock(
            return_value=Response(200, json={"id": 456})
        )

        result = client.get("/organizations/456", v1=True)

        assert result == {"id": 456}
        client.close()

    @pytest.mark.req("FR-009")
    def test_list_response_normalization_wraps_top_level_list(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """SDK must not return sometimes-list, sometimes-dict responses."""
        config = ClientConfig(api_key="test-key", max_retries=0)
        client = HTTPClient(config)
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(200, json=[{"id": 1, "name": "Acme"}])
        )
        try:
            result = client.get("/companies")
        finally:
            client.close()

        assert result == {"data": [{"id": 1, "name": "Acme"}]}

    def test_authentication(self, respx_mock: respx.MockRouter) -> None:
        """Test API key authentication."""
        config = ClientConfig(api_key="my-secret-key")
        client = HTTPClient(config)

        route = respx_mock.get("https://api.affinity.co/v2/auth/whoami").mock(
            return_value=Response(200, json={"user": {"id": 1}})
        )

        client.get("/auth/whoami")

        # Check that Bearer auth is being used by default (v2)
        request = route.calls.last.request
        assert request.headers.get("Authorization") == "Bearer my-secret-key"
        client.close()

    @pytest.mark.req("FR-005")
    def test_v1_basic_auth_mode_uses_basic_auth(self, respx_mock: respx.MockRouter) -> None:
        """V1 Basic auth is an explicit escape hatch."""
        config = ClientConfig(api_key="secret-key", max_retries=0, v1_auth_mode="basic")
        client = HTTPClient(config)
        route = respx_mock.get("https://api.affinity.co/organizations/456").mock(
            return_value=Response(200, json={"id": 456})
        )

        try:
            _ = client.get("/organizations/456", v1=True)
        finally:
            client.close()

        request = route.calls.last.request
        expected = "Basic " + b64encode(b":secret-key").decode("ascii")
        assert request.headers.get("Authorization") == expected

    @pytest.mark.req("FR-005")
    def test_v2_still_uses_bearer_when_v1_basic_auth_enabled(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """V1 Basic auth config must not leak into v2 requests."""
        config = ClientConfig(api_key="secret-key", max_retries=0, v1_auth_mode="basic")
        client = HTTPClient(config)
        route = respx_mock.get("https://api.affinity.co/v2/auth/whoami").mock(
            return_value=Response(200, json={"user": {"id": 1}})
        )

        try:
            _ = client.get("/auth/whoami", v1=False)
        finally:
            client.close()

        request = route.calls.last.request
        assert request.headers.get("Authorization") == "Bearer secret-key"

    @pytest.mark.req("NFR-002")
    def test_caching(self, respx_mock: respx.MockRouter) -> None:
        """Test response caching."""
        config = ClientConfig(api_key="test-key", enable_cache=True)
        client = HTTPClient(config)

        route = respx_mock.get("https://api.affinity.co/v2/fields").mock(
            return_value=Response(200, json={"fields": []})
        )

        # First call - should hit API
        result1 = client.get("/fields", cache_key="fields")
        assert route.call_count == 1

        # Second call - should use cache
        result2 = client.get("/fields", cache_key="fields")
        assert route.call_count == 1  # No additional call

        assert result1 == result2
        client.close()

    @pytest.mark.req("NFR-002a")
    def test_cache_isolation_by_tenant_hash(self, respx_mock: respx.MockRouter) -> None:
        """Cache keys must not collide across different API keys."""
        route = respx_mock.get("https://api.affinity.co/v2/fields").mock(
            return_value=Response(200, json={"fields": []})
        )

        client_a = HTTPClient(ClientConfig(api_key="key-a", enable_cache=True, max_retries=0))
        client_b = HTTPClient(ClientConfig(api_key="key-b", enable_cache=True, max_retries=0))
        try:
            _ = client_a.get("/fields", cache_key="fields")
            _ = client_b.get("/fields", cache_key="fields")
        finally:
            client_a.close()
            client_b.close()

        assert route.call_count == 2

    @pytest.mark.req("TR-017a")
    def test_v1_query_signature_freezing_excludes_page_token(self) -> None:
        """V1 pagination signature must remain stable across page tokens."""
        params_page_1 = {
            "term": "acme",
            "page_size": 50,
            "page_token": "token-1",
        }
        params_page_2 = {
            # intentionally different dict order
            "page_token": "token-2",
            "page_size": 50,
            "term": "acme",
        }

        sig_1 = _freeze_v1_query_signature(params_page_1)
        sig_2 = _freeze_v1_query_signature(params_page_2)

        assert sig_1 == sig_2
        assert ("page_token", "token-1") not in sig_1
        assert ("page_token", "token-2") not in sig_2

    @pytest.mark.req("TR-017")
    @pytest.mark.req("FR-008")
    @pytest.mark.req("TR-017b")
    def test_encode_query_params_repeatables_are_repeated_and_deduped(self) -> None:
        params = {
            "fieldIds": ["b", "a", "b"],
            "fieldTypes": ["global", "global", "enriched"],
            "term": "acme",
            "page_size": 50,
        }

        assert _encode_query_params(params) == [
            ("fieldIds", "b"),
            ("fieldIds", "a"),
            ("fieldTypes", "global"),
            ("fieldTypes", "enriched"),
            ("page_size", "50"),
            ("term", "acme"),
        ]

    @pytest.mark.req("TR-017b")
    def test_repeatable_params_registry_includes_field_ids_and_types(self) -> None:
        assert "fieldIds" in REPEATABLE_QUERY_PARAMS
        assert "fieldTypes" in REPEATABLE_QUERY_PARAMS


@pytest.mark.req("NFR-003")
@pytest.mark.req("TR-006a")
class TestRetryPolicy:
    """Retry policy tests (status-aware + jitter)."""

    def test_get_retries_on_500_with_jitter(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("affinity.clients.http.time.sleep", fake_sleep)
        monkeypatch.setattr("affinity.clients.http.time.time_ns", lambda: 999_999)

        route = respx_mock.get("https://api.affinity.co/v2/companies").mock(
            side_effect=[
                Response(500, json={"message": "server error"}),
                Response(200, json={"data": [], "pagination": {}}),
            ]
        )

        client = HTTPClient(ClientConfig(api_key="test-key", max_retries=1, retry_delay=1.0))
        try:
            data = client.get("/companies")
        finally:
            client.close()

        assert route.call_count == 2
        assert sleeps == [pytest.approx(1.0, abs=1e-3)]
        assert data["data"] == []

    @pytest.mark.req("NFR-003a")
    def test_post_does_not_retry_on_500(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("affinity.clients.http.time.sleep", fake_sleep)
        route = respx_mock.post("https://api.affinity.co/v2/companies").mock(
            return_value=Response(500, json={"message": "server error"})
        )

        client = HTTPClient(ClientConfig(api_key="test-key", max_retries=3, retry_delay=1.0))
        try:
            with pytest.raises(AffinityError):
                client.post("/companies", json={"name": "x"})
        finally:
            client.close()

        assert route.call_count == 1
        assert sleeps == []

    def test_get_honors_retry_after_over_backoff(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("affinity.clients.http.time.sleep", fake_sleep)
        monkeypatch.setattr("affinity.clients.http.time.time_ns", lambda: 999_999)

        route = respx_mock.get("https://api.affinity.co/v2/companies").mock(
            side_effect=[
                Response(429, json={"message": "rate limit"}, headers={"Retry-After": "3"}),
                Response(200, json={"data": [], "pagination": {}}),
            ]
        )

        client = HTTPClient(ClientConfig(api_key="test-key", max_retries=1, retry_delay=100.0))
        try:
            _ = client.get("/companies")
        finally:
            client.close()

        assert route.call_count == 2
        assert sleeps == [3.0]

    def test_backoff_is_capped(self, respx_mock: respx.MockRouter, monkeypatch: Any) -> None:
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("affinity.clients.http.time.sleep", fake_sleep)
        monkeypatch.setattr("affinity.clients.http.time.time_ns", lambda: 999_999)

        route = respx_mock.get("https://api.affinity.co/v2/companies").mock(
            side_effect=[
                Response(429, json={"message": "rate limit"}),
                Response(200, json={"data": [], "pagination": {}}),
            ]
        )

        client = HTTPClient(ClientConfig(api_key="test-key", max_retries=1, retry_delay=100.0))
        try:
            _ = client.get("/companies")
        finally:
            client.close()

        assert route.call_count == 2
        assert sleeps == [pytest.approx(60.0, abs=1e-3)]


@pytest.mark.req("DX-010")
class TestBetaEndpoints:
    """Beta endpoint gating tests."""

    def test_person_merge_requires_opt_in(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post("https://api.affinity.co/v2/person-merges").mock(
            return_value=Response(
                200, json={"taskUrl": "https://api.affinity.co/v2/tasks/person-merges/1"}
            )
        )

        with Affinity(api_key="test-key") as client, pytest.raises(BetaEndpointDisabledError):
            _ = client.persons.merge(PersonId(1), PersonId(2))

        assert route.call_count == 0

    def test_person_merge_works_with_opt_in(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post("https://api.affinity.co/v2/person-merges").mock(
            return_value=Response(
                200, json={"taskUrl": "https://api.affinity.co/v2/tasks/person-merges/1"}
            )
        )

        with Affinity(api_key="test-key", enable_beta_endpoints=True) as client:
            task_url = client.persons.merge(PersonId(1), PersonId(2))

        assert route.call_count == 1
        assert task_url.endswith("/tasks/person-merges/1")

    def test_company_merge_requires_opt_in(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post("https://api.affinity.co/v2/company-merges").mock(
            return_value=Response(
                200, json={"taskUrl": "https://api.affinity.co/v2/tasks/company-merges/1"}
            )
        )

        with Affinity(api_key="test-key") as client, pytest.raises(BetaEndpointDisabledError):
            _ = client.companies.merge(CompanyId(1), CompanyId(2))

        assert route.call_count == 0

    def test_company_merge_works_with_opt_in(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post("https://api.affinity.co/v2/company-merges").mock(
            return_value=Response(
                200, json={"taskUrl": "https://api.affinity.co/v2/tasks/company-merges/1"}
            )
        )

        with Affinity(api_key="test-key", enable_beta_endpoints=True) as client:
            task_url = client.companies.merge(CompanyId(1), CompanyId(2))

        assert route.call_count == 1
        assert task_url.endswith("/tasks/company-merges/1")


# =============================================================================
# Service Layer Tests
# =============================================================================


@pytest.mark.req("FR-008a")
class TestCompanyService:
    """Test company service operations."""

    def test_list_companies(self, respx_mock: respx.MockRouter) -> None:
        """Test listing companies."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": 1, "name": "Acme Corp"},
                        {"id": 2, "name": "Globex Inc"},
                    ],
                    "pagination": {"nextPageUrl": None},
                },
            )
        )

        with Affinity(api_key="test-key") as client:
            result = client.companies.list()

            assert len(result.data) == 2
            assert result.data[0].name == "Acme Corp"

    def test_get_company(self, respx_mock: respx.MockRouter) -> None:
        """Test getting a single company."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Acme Corp",
                    "domain": "acme.com",
                    "domains": ["acme.com"],
                },
            )
        )

        with Affinity(api_key="test-key") as client:
            company = client.companies.get(CompanyId(123))

            assert company.id == 123
            assert company.name == "Acme Corp"
            assert company.domain == "acme.com"
            assert company.fields.requested is False

    def test_fields_requested_semantics(self, respx_mock: respx.MockRouter) -> None:
        """Omitted vs empty fields must be distinguishable."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Acme Corp",
                    "domain": "acme.com",
                    "domains": ["acme.com"],
                    "fields": {},
                },
            )
        )

        with Affinity(api_key="test-key") as client:
            company = client.companies.get(CompanyId(123))

        assert company.fields.requested is True
        assert company.fields.data == {}

    def test_create_company(self, respx_mock: respx.MockRouter) -> None:
        """Test creating a company (uses V1 API)."""
        respx_mock.post("https://api.affinity.co/organizations").mock(
            return_value=Response(
                200,
                json={
                    "id": 999,
                    "name": "New Corp",
                    "domain": "newcorp.com",
                },
            )
        )

        with Affinity(api_key="test-key") as client:
            company = client.companies.create(
                CompanyCreate(name="New Corp", domain="newcorp.com"),
                if_not_exists=False,
            )

            assert company.id == 999
            assert company.name == "New Corp"


@pytest.mark.req("FR-008a")
class TestPersonService:
    """Test person service operations."""

    def test_get_person(self, respx_mock: respx.MockRouter) -> None:
        """Test getting a person."""
        respx_mock.get("https://api.affinity.co/v2/persons/456").mock(
            return_value=Response(
                200,
                json={
                    "id": 456,
                    "firstName": "John",
                    "lastName": "Doe",
                    "primaryEmailAddress": "john@example.com",
                    "type": "external",
                },
            )
        )

        with Affinity(api_key="test-key") as client:
            person = client.persons.get(PersonId(456))

            assert person.id == 456
            assert person.first_name == "John"
            assert person.last_name == "Doe"
            assert person.type == PersonType.EXTERNAL


@pytest.mark.req("NFR-002a")
class TestListService:
    """Test list service operations."""

    def test_get_list(self, respx_mock: respx.MockRouter) -> None:
        """Test getting a list."""
        respx_mock.get("https://api.affinity.co/lists/789").mock(
            return_value=Response(
                200,
                json={
                    "id": 789,
                    "name": "Deal Pipeline",
                    "type": 8,
                    "public": True,
                    "owner_id": 1,
                    "list_size": 50,
                },
            )
        )

        with Affinity(api_key="test-key") as client:
            lst = client.lists.get(ListId(789))

            assert lst.id == 789
            assert lst.name == "Deal Pipeline"
            assert lst.type == ListType.OPPORTUNITY

    def test_list_create_invalidates_metadata_cache(self, respx_mock: respx.MockRouter) -> None:
        """Metadata-changing operations must invalidate metadata caches."""
        fields_route = respx_mock.get("https://api.affinity.co/v2/lists/789/fields").mock(
            return_value=Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None, "prevUrl": None}},
            )
        )
        create_route = respx_mock.post("https://api.affinity.co/lists").mock(
            return_value=Response(
                200,
                json={
                    "id": 999,
                    "name": "New List",
                    "type": 0,
                    "public": True,
                    "ownerId": 1,
                    "listSize": 0,
                },
            )
        )

        config = ClientConfig(api_key="test-key", enable_cache=True, max_retries=0)
        http = HTTPClient(config)
        try:
            lists = ListService(http)

            # First call caches list fields.
            _ = lists.get_fields(ListId(789))
            assert fields_route.call_count == 1

            # Second call is cached.
            _ = lists.get_fields(ListId(789))
            assert fields_route.call_count == 1

            # Creating a list is metadata-changing and should invalidate list caches.
            _ = lists.create(ListCreate(name="New List", type=ListType.PERSON, is_public=True))
            assert create_route.call_count == 1

            # Cache should be invalidated; fetch hits API again.
            _ = lists.get_fields(ListId(789))
            assert fields_route.call_count == 2
        finally:
            http.close()

    @pytest.mark.req("FR-006")
    def test_lists_iter_auto_paginates_next_cursor(self) -> None:
        """Test that iter() auto-paginates through nextUrl cursor."""
        call_count = {"value": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["value"] += 1
            url = str(request.url)
            if "page=2" in url:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": 2, "name": "B", "type": 0, "public": True, "ownerId": 1},
                        ],
                        "pagination": {"nextUrl": None},
                    },
                    request=request,
                )
            elif url.endswith("/lists") or url.endswith("/lists/"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": 1, "name": "A", "type": 0, "public": True, "ownerId": 1},
                        ],
                        "pagination": {"nextUrl": "https://api.affinity.co/v2/lists?page=2"},
                    },
                    request=request,
                )
            return httpx.Response(404, json={"error": "not found"}, request=request)

        transport = httpx.MockTransport(handler)
        config = ClientConfig(api_key="test-key", max_retries=0, transport=transport)
        http_client = HTTPClient(config)
        try:
            lists_service = ListService(http_client)
            lists = list(lists_service.iter())
            assert [lst.id for lst in lists] == [1, 2]
            assert call_count["value"] == 2  # Two pages fetched
        finally:
            http_client.close()


# =============================================================================
# Opportunity Service Tests
# =============================================================================


@pytest.mark.req("FR-009a")
class TestOpportunityService:
    """Test opportunity service operations."""

    def test_get_opportunity_v2(self, respx_mock: respx.MockRouter) -> None:
        """Get uses v2 and may return partial data."""
        respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Deal A",
                    "listId": 789,
                },
            )
        )

        with Affinity(api_key="test-key", max_retries=0) as client:
            opp = client.opportunities.get(OpportunityId(123))

        assert opp.id == 123
        assert opp.name == "Deal A"
        assert opp.list_id == 789

    def test_list_opportunities_v2(self, respx_mock: respx.MockRouter) -> None:
        """List uses v2 and returns a stable paginated response shape."""
        respx_mock.get("https://api.affinity.co/v2/opportunities").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": 1, "name": "Deal 1", "listId": 789},
                        {"id": 2, "name": "Deal 2", "listId": 789},
                    ],
                    "pagination": {"nextUrl": None, "prevUrl": None},
                },
            )
        )

        with Affinity(api_key="test-key", max_retries=0) as client:
            page = client.opportunities.list()

        assert [o.id for o in page.data] == [1, 2]


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling."""

    @pytest.mark.req("NFR-004")
    def test_not_found_error(self, respx_mock: respx.MockRouter) -> None:
        """Test 404 error handling."""
        # V2 returns 404
        respx_mock.get("https://api.affinity.co/v2/companies/99999").mock(
            return_value=Response(404, json={"message": "Company not found"})
        )
        # V1 fallback also returns 404
        respx_mock.get("https://api.affinity.co/organizations/99999").mock(
            return_value=Response(404, json={"message": "Organization not found"})
        )

        with pytest.raises(NotFoundError), Affinity(api_key="test-key", max_retries=0) as client:
            client.companies.get(CompanyId(99999))

    @pytest.mark.req("NFR-003")
    @pytest.mark.req("NFR-004")
    @pytest.mark.req("TR-014")
    def test_rate_limit_error(self, respx_mock: respx.MockRouter) -> None:
        """Test 429 rate limit error."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                429, json={"message": "Rate limit exceeded"}, headers={"Retry-After": "60"}
            )
        )

        with pytest.raises(RateLimitError) as exc_info:
            config = ClientConfig(api_key="test-key", max_retries=0)
            client = HTTPClient(config)
            try:
                client.get("/companies")
            finally:
                client.close()

        assert exc_info.value.retry_after == 60
        assert exc_info.value.diagnostics is not None
        assert exc_info.value.diagnostics.method == "GET"
        assert exc_info.value.diagnostics.api_version == "v2"
        assert exc_info.value.diagnostics.url is not None
        assert "test-key" not in exc_info.value.diagnostics.url

    @pytest.mark.req("DX-007")
    @pytest.mark.req("TR-014")
    @pytest.mark.req("NFR-003b")
    def test_diagnostics_redact_api_key_and_capture_headers(
        self,
        respx_mock: respx.MockRouter,
    ) -> None:
        """Diagnostics must be safe (no secrets) and include header subset."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                429,
                json={"message": "Rate limit exceeded"},
                headers={
                    "retry-after": "60",
                    "x-ratelimit-limit-user": "900",
                    "x-ratelimit-limit-user-remaining": "0",
                    "x-ratelimit-limit-user-reset": "45",
                    "x-request-id": "req-123",
                },
            )
        )

        secret = "super-secret-api-key"
        with pytest.raises(RateLimitError) as exc_info:
            client = HTTPClient(ClientConfig(api_key=secret, max_retries=0))
            try:
                client.get("/companies")
            finally:
                client.close()

        exc = exc_info.value
        assert secret not in str(exc)
        assert exc.diagnostics is not None
        assert exc.diagnostics.response_headers is not None
        assert exc.diagnostics.response_headers["Retry-After"] == "60"
        assert exc.diagnostics.response_headers["X-Ratelimit-Limit-User"] == "900"
        assert exc.diagnostics.request_id == "req-123"

    @pytest.mark.req("NFR-003")
    def test_retry_after_http_date(self, respx_mock: respx.MockRouter) -> None:
        """Test Retry-After parsing for HTTP-date format."""
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        retry_after = format_datetime(retry_at, usegmt=True)
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                429,
                json={"message": "Rate limit exceeded"},
                headers={"Retry-After": retry_after},
            )
        )

        with pytest.raises(RateLimitError) as exc_info:
            config = ClientConfig(api_key="test-key", max_retries=0)
            client = HTTPClient(config)
            try:
                client.get("/companies")
            finally:
                client.close()

        assert exc_info.value.retry_after is not None
        assert 1 <= exc_info.value.retry_after <= 10

    @pytest.mark.req("TR-016")
    def test_safe_follow_url_rejects_wrong_host(self) -> None:
        """SafeFollowUrl blocks unexpected hosts."""
        config = ClientConfig(api_key="test-key", max_retries=0)
        client = HTTPClient(config)
        try:
            with pytest.raises(UnsafeUrlError):
                client.get_url("https://evil.example/v2/companies")
        finally:
            client.close()

    @pytest.mark.req("TR-016")
    def test_safe_follow_url_rejects_userinfo(self) -> None:
        """SafeFollowUrl blocks URLs with userinfo."""
        config = ClientConfig(api_key="test-key", max_retries=0)
        client = HTTPClient(config)
        try:
            with pytest.raises(UnsafeUrlError):
                client.get_url("https://user:pass@api.affinity.co/v2/companies")
        finally:
            client.close()

    @pytest.mark.req("TR-016")
    def test_safe_follow_url_allows_relative(self, respx_mock: respx.MockRouter) -> None:
        """SafeFollowUrl resolves relative URLs against configured base."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(200, json={"data": [], "pagination": {}})
        )
        config = ClientConfig(api_key="test-key", max_retries=0)
        client = HTTPClient(config)
        try:
            data = client.get_url("/v2/companies")
        finally:
            client.close()
        assert data["data"] == []

    @pytest.mark.req("TR-016")
    @pytest.mark.req("TR-016a")
    def test_safe_follow_url_blocks_redirect(self, respx_mock: respx.MockRouter) -> None:
        """SafeFollowUrl must not follow redirects for server-provided URLs."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                302,
                headers={"Location": "https://api.affinity.co/v2/companies?page=2"},
            )
        )
        config = ClientConfig(api_key="test-key", max_retries=0)
        client = HTTPClient(config)
        try:
            with pytest.raises(UnsafeUrlError):
                client.get_url("/v2/companies")
        finally:
            client.close()

    @pytest.mark.req("TR-016")
    def test_safe_follow_url_rejects_http_when_base_is_https(self) -> None:
        """SafeFollowUrl rejects scheme mismatches (https base, http nextUrl)."""
        config = ClientConfig(api_key="test-key", max_retries=0)
        client = HTTPClient(config)
        try:
            with pytest.raises(UnsafeUrlError):
                client.get_url("http://api.affinity.co/v2/companies")
        finally:
            client.close()

    @pytest.mark.req("TR-016")
    def test_safe_follow_url_allows_http_when_base_is_http(
        self,
        respx_mock: respx.MockRouter,
    ) -> None:
        """SafeFollowUrl allows http only if configured base is http (test override)."""
        respx_mock.get("http://localhost:8000/v2/companies").mock(
            return_value=Response(200, json={"data": [], "pagination": {}})
        )
        config = ClientConfig(
            api_key="test-key",
            max_retries=0,
            v1_base_url="http://localhost:8000",
            v2_base_url="http://localhost:8000/v2",
        )
        client = HTTPClient(config)
        try:
            data = client.get_url("http://localhost:8000/v2/companies")
        finally:
            client.close()
        assert data["data"] == []


# =============================================================================
# Client Lifecycle Tests
# =============================================================================


@pytest.mark.req("DX-009")
class TestClientLifecycle:
    """Test client lifecycle management."""

    def test_context_manager(self) -> None:
        """Test client context manager."""
        with Affinity(api_key="test-key") as client:
            assert client is not None
        # Should be closed after context

    def test_lazy_service_initialization(self) -> None:
        """Test that services are lazily initialized."""
        with Affinity(api_key="test-key") as client:
            assert client._companies is None

            # Access service
            _ = client.companies

            assert client._companies is not None

    def test_rate_limits_snapshot_access(self) -> None:
        """Test rate limit snapshot access."""
        with Affinity(api_key="test-key") as client:
            snapshot = client.rate_limits.snapshot()
            assert snapshot.source in {"unknown", "headers"}

    def test_resource_warning_on_unclosed_sync_client(self) -> None:
        """Test that ResourceWarning is raised when sync client not closed."""
        # Create client without context manager and don't close
        client = Affinity(api_key="test-key")
        # Mark that we didn't enter context (simulating direct construction)
        assert not client._closed

        # Force garbage collection to trigger __del__
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", ResourceWarning)
            del client
            gc.collect()

            # Check that a ResourceWarning was raised
            resource_warnings = [
                warning for warning in w if issubclass(warning.category, ResourceWarning)
            ]
            assert len(resource_warnings) >= 1
            assert "not closed" in str(resource_warnings[0].message)

    def test_no_warning_when_context_manager_used(self) -> None:
        """Test that no ResourceWarning is raised when context manager used."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", ResourceWarning)

            with Affinity(api_key="test-key") as client:
                _ = client  # Use the client

            gc.collect()

            # Check that no ResourceWarning was raised
            resource_warnings = [
                warning for warning in w if issubclass(warning.category, ResourceWarning)
            ]
            assert len(resource_warnings) == 0

    def test_no_warning_when_close_called_explicitly(self) -> None:
        """Test that no ResourceWarning when close() is called explicitly."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", ResourceWarning)

            client = Affinity(api_key="test-key")
            client.close()

            del client
            gc.collect()

            # Check that no ResourceWarning was raised
            resource_warnings = [
                warning for warning in w if issubclass(warning.category, ResourceWarning)
            ]
            assert len(resource_warnings) == 0


@pytest.mark.asyncio
@pytest.mark.req("TR-006")
async def test_async_transport_injection_with_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://api.affinity.co/v2/companies"):
            return httpx.Response(200, json={"data": [], "pagination": {"nextUrl": None}})
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    http_client = AsyncHTTPClient(
        ClientConfig(api_key="k", max_retries=0, async_transport=transport)
    )
    try:
        data = await http_client.get("/companies")
    finally:
        await http_client.close()

    assert data["data"] == []


@pytest.mark.asyncio
@pytest.mark.req("TR-009")
async def test_async_affinity_companies_get() -> None:
    """Test async company get with mock transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/companies/123" in str(request.url):
            return httpx.Response(200, json={"id": 123, "name": "Acme"}, request=request)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    transport = httpx.MockTransport(handler)
    config = ClientConfig(api_key="test-key", max_retries=0, async_transport=transport)
    http_client = AsyncHTTPClient(config)
    try:
        companies = AsyncCompanyService(http_client)
        company = await companies.get(CompanyId(123))
        assert company.id == 123
        assert company.name == "Acme"
    finally:
        await http_client.close()


@pytest.mark.asyncio
@pytest.mark.req("TR-009")
async def test_async_affinity_companies_iter_auto_paginates() -> None:
    """Test async company iteration with pagination using mock transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "page=2" in url:
            return httpx.Response(
                200,
                json={"data": [{"id": 2, "name": "B"}], "pagination": {"nextUrl": None}},
                request=request,
            )
        elif "/companies" in url:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "name": "A"}],
                    "pagination": {"nextUrl": "https://api.affinity.co/v2/companies?page=2"},
                },
                request=request,
            )
        return httpx.Response(404, json={"error": "not found"}, request=request)

    transport = httpx.MockTransport(handler)
    config = ClientConfig(api_key="test-key", max_retries=0, async_transport=transport)
    http_client = AsyncHTTPClient(config)
    try:
        companies = AsyncCompanyService(http_client)
        items: list[int] = []
        async for company in companies.iter():
            items.append(int(company.id))
        assert items == [1, 2]
    finally:
        await http_client.close()


@pytest.mark.asyncio
@pytest.mark.req("TR-009")
async def test_async_affinity_lists_iter_auto_paginates() -> None:
    """Test async list iteration with mock transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/lists" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "name": "L1", "type": 0, "public": True, "ownerId": 1}],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )
        return httpx.Response(404, json={"error": "not found"}, request=request)

    transport = httpx.MockTransport(handler)
    config = ClientConfig(api_key="test-key", max_retries=0, async_transport=transport)
    http_client = AsyncHTTPClient(config)
    try:
        lists_service = AsyncListService(http_client)
        lists = []
        async for lst in lists_service.iter():
            lists.append(int(lst.id))
        assert lists == [1]
    finally:
        await http_client.close()


# =============================================================================
# Async Client Lifecycle Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.req("DX-009")
async def test_async_resource_warning_on_unclosed_client() -> None:
    """Test that ResourceWarning is raised when async client not closed."""
    # Create client without context manager and don't close
    client = AsyncAffinity(api_key="test-key")
    assert not client._closed

    # Force garbage collection to trigger __del__
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", ResourceWarning)
        del client
        gc.collect()

        # Check that a ResourceWarning was raised
        resource_warnings = [
            warning for warning in w if issubclass(warning.category, ResourceWarning)
        ]
        assert len(resource_warnings) >= 1
        assert "not closed" in str(resource_warnings[0].message)


@pytest.mark.asyncio
@pytest.mark.req("DX-009")
async def test_async_no_warning_when_context_manager_used() -> None:
    """Test that no ResourceWarning is raised when async context manager used."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", ResourceWarning)

        async with AsyncAffinity(api_key="test-key") as client:
            _ = client  # Use the client

        gc.collect()

        # Check that no ResourceWarning was raised
        resource_warnings = [
            warning for warning in w if issubclass(warning.category, ResourceWarning)
        ]
        assert len(resource_warnings) == 0


@pytest.mark.asyncio
@pytest.mark.req("DX-009")
async def test_async_no_warning_when_close_called_explicitly() -> None:
    """Test that no ResourceWarning when close() is called explicitly."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", ResourceWarning)

        client = AsyncAffinity(api_key="test-key")
        await client.close()

        del client
        gc.collect()

        # Check that no ResourceWarning was raised
        resource_warnings = [
            warning for warning in w if issubclass(warning.category, ResourceWarning)
        ]
        assert len(resource_warnings) == 0
