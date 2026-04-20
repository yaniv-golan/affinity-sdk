"""Additional tests for affinity.services.companies to improve coverage."""

from __future__ import annotations

import httpx
import pytest

from affinity.clients.http import ClientConfig, HTTPClient
from affinity.models.types import CompanyId, FieldType
from affinity.services.companies import CompanyService


class TestCompanyServiceValidationErrors:
    """Tests for validation error branches in CompanyService."""

    @pytest.fixture
    def service(self) -> CompanyService:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        return CompanyService(http)

    def test_list_cursor_with_field_ids_raises(self, service: CompanyService) -> None:
        with pytest.raises(ValueError, match="Cannot combine 'cursor' with other parameters"):
            service.list(cursor="some-cursor", field_ids=["field-1"])

    def test_list_cursor_with_field_types_raises(self, service: CompanyService) -> None:
        with pytest.raises(ValueError, match="Cannot combine 'cursor' with other parameters"):
            service.list(cursor="some-cursor", field_types=[FieldType.ENRICHED])

    def test_list_cursor_with_limit_raises(self, service: CompanyService) -> None:
        with pytest.raises(ValueError, match="Cannot combine 'cursor' with other parameters"):
            service.list(cursor="some-cursor", limit=10)

    def test_pages_cursor_with_field_ids_raises(self, service: CompanyService) -> None:
        with pytest.raises(ValueError, match="Cannot combine 'cursor' with other parameters"):
            list(service.pages(cursor="some-cursor", field_ids=["field-1"]))

    def test_pages_cursor_with_limit_raises(self, service: CompanyService) -> None:
        with pytest.raises(ValueError, match="Cannot combine 'cursor' with other parameters"):
            list(service.pages(cursor="some-cursor", limit=10))


class TestCompanyServicePagination:
    """Tests for company pagination."""

    def test_pages_iterates_multiple_pages(self) -> None:
        page_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal page_count
            page_count += 1

            if page_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": 1, "name": "Company 1", "domain": "c1.com"}],
                        "pagination": {"nextUrl": "https://v2.example/v2/companies?cursor=p2"},
                    },
                    request=request,
                )
            elif page_count == 2:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": 2, "name": "Company 2", "domain": "c2.com"}],
                        "pagination": {"nextUrl": None},
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        pages = list(service.pages())
        assert len(pages) == 2
        assert len(pages[0].data) == 1
        assert pages[0].data[0].name == "Company 1"

    def test_pages_stops_on_same_cursor(self) -> None:
        """Pages should stop if cursor doesn't advance (prevents infinite loop)."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Always return the same next cursor
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "name": "Company", "domain": "c.com"}],
                    "pagination": {"nextUrl": "https://v2.example/v2/companies?cursor=same"},
                },
                request=request,
            )

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        # Start with cursor="same" - should return one page then stop
        pages = list(service.pages(cursor="https://v2.example/v2/companies?cursor=same"))
        assert len(pages) == 1
        assert call_count == 1

    def test_all_iterates_all_companies(self) -> None:
        page_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal page_count
            page_count += 1

            if page_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": 1, "name": "Company 1", "domain": "c1.com"},
                            {"id": 2, "name": "Company 2", "domain": "c2.com"},
                        ],
                        "pagination": {"nextUrl": "https://v2.example/v2/companies?cursor=p2"},
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 3, "name": "Company 3", "domain": "c3.com"}],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        companies = list(service.all())
        assert len(companies) == 3
        assert [c.id for c in companies] == [1, 2, 3]


class TestCompanyServiceGet:
    """Tests for getting individual companies."""

    def test_get_by_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/companies/123" in str(request.url):
                return httpx.Response(
                    200,
                    json={"id": 123, "name": "Test Company", "domain": "test.com"},
                    request=request,
                )
            return httpx.Response(404, json={"error": "Not found"}, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        company = service.get(CompanyId(123))
        assert company.id == 123
        assert company.name == "Test Company"
        assert company.domain == "test.com"


class TestCompanyServiceSearch:
    """Tests for company search (V1 API)."""

    def test_search_with_term(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "v1.example/organizations" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "organizations": [{"id": 1, "name": "Acme Corp", "domain": "acme.com"}],
                        "next_page_token": None,
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        result = service.search(term="acme")
        assert len(result.data) == 1
        assert result.data[0].name == "Acme Corp"

    def test_search_with_page_size(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "v1.example/organizations" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "organizations": [{"id": 1, "name": "Company", "domain": "co.com"}],
                        "next_page_token": "token123",
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        result = service.search(term="company", page_size=10)
        assert len(result.data) == 1
        assert result.next_page_token == "token123"

    def test_search_pages_iterates(self) -> None:
        page_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal page_count
            page_count += 1

            if page_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "organizations": [{"id": 1, "name": "Company 1", "domain": "c1.com"}],
                        "next_page_token": "page2",
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "organizations": [{"id": 2, "name": "Company 2", "domain": "c2.com"}],
                    "next_page_token": None,
                },
                request=request,
            )

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        pages = list(service.search_pages(term="company"))
        assert len(pages) == 2
        assert pages[0].data[0].name == "Company 1"
        assert pages[1].data[0].name == "Company 2"

    def test_search_all_iterates(self) -> None:
        page_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal page_count
            page_count += 1

            if page_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "organizations": [{"id": 1, "name": "Company 1", "domain": "c1.com"}],
                        "next_page_token": "page2",
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "organizations": [{"id": 2, "name": "Company 2", "domain": "c2.com"}],
                    "next_page_token": None,
                },
                request=request,
            )

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        companies = list(service.search_all(term="company"))
        assert len(companies) == 2
        assert [c.name for c in companies] == ["Company 1", "Company 2"]


class TestCompanyServiceResolve:
    """Tests for company resolution by domain."""

    def test_resolve_by_domain(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # resolve uses V1 search API with /organizations
            if "v1.example/organizations" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "organizations": [{"id": 100, "name": "Acme Corp", "domain": "acme.com"}],
                        "next_page_token": None,
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        result = service.resolve(domain="acme.com")
        assert result is not None
        assert result.id == 100
        assert result.domain == "acme.com"

    def test_resolve_not_found_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # V1 API response format
            return httpx.Response(
                200,
                json={"organizations": [], "next_page_token": None},
                request=request,
            )

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        result = service.resolve(domain="nonexistent.com")
        assert result is None

    def test_resolve_requires_domain_or_name(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={}, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        with pytest.raises(ValueError, match="Must provide either domain or name"):
            service.resolve()

    def test_resolve_by_name(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "v1.example/organizations" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "organizations": [{"id": 200, "name": "Acme Corp", "domain": "acme.com"}],
                        "next_page_token": None,
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = CompanyService(http)

        result = service.resolve(name="Acme Corp")
        assert result is not None
        assert result.id == 200
        assert result.name == "Acme Corp"
