"""
Tests for newly implemented features:
- FR-007: Filter Builder
- FR-010: Search/Resolve Helpers
- FR-011: Task Service
- DX-008: Request/Response Hooks
- TR-013: OpenAPI Schema Alignment
- TR-015: V2 Version Compatibility
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from affinity import Affinity, F, Filter, VersionCompatibilityError
from affinity.clients.http import (
    ClientConfig,
    HTTPClient,
    RequestInfo,
    ResponseInfo,
)
from affinity.filters import (
    _escape_string,
    _format_value,
)
from affinity.services.companies import CompanyService
from affinity.services.lists import ListService
from affinity.services.persons import PersonService
from affinity.services.tasks import TaskService, TaskStatus

# =============================================================================
# FR-007: Filter Builder Tests
# =============================================================================


class TestFilterBuilder:
    """Test the filter builder (FR-007)."""

    @pytest.mark.req("FR-007")
    def test_simple_equals(self) -> None:
        """Test simple equality filter."""
        f = Filter.field("name").equals("Acme")
        assert f.to_string() == 'name = "Acme"'

    @pytest.mark.req("FR-007")
    def test_contains(self) -> None:
        """Test contains filter."""
        f = Filter.field("name").contains("Corp")
        assert f.to_string() == 'name =~ "Corp"'

    @pytest.mark.req("FR-007")
    def test_starts_with(self) -> None:
        f = Filter.field("name").starts_with("A")
        assert f.to_string() == 'name =^ "A"'

    @pytest.mark.req("FR-007")
    def test_ends_with(self) -> None:
        f = Filter.field("name").ends_with("Inc")
        assert f.to_string() == 'name =$ "Inc"'

    @pytest.mark.req("FR-007")
    def test_not_equals(self) -> None:
        f = Filter.field("status").not_equals("inactive")
        assert f.to_string() == 'status != "inactive"'

    @pytest.mark.req("FR-007")
    def test_numeric_comparison(self) -> None:
        f = Filter.field("count").greater_than(10)
        assert f.to_string() == "count > 10"

        f = Filter.field("count").less_than_or_equal(100)
        assert f.to_string() == "count <= 100"

    @pytest.mark.req("FR-007")
    def test_null_check(self) -> None:
        f = Filter.field("email").is_null()
        assert f.to_string() == "email != *"

        f = Filter.field("email").is_not_null()
        assert f.to_string() == "email = *"

    @pytest.mark.req("FR-007")
    def test_boolean_value(self) -> None:
        f = Filter.field("archived").equals(True)
        assert f.to_string() == "archived = true"

        f = Filter.field("archived").equals(False)
        assert f.to_string() == "archived = false"

    @pytest.mark.req("FR-007")
    def test_date_comparison(self) -> None:
        d = date(2025, 1, 15)
        f = Filter.field("created_at").greater_than(d)
        assert f.to_string() == 'created_at > "2025-01-15"'

    @pytest.mark.req("FR-007")
    def test_datetime_comparison(self) -> None:
        dt = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        f = Filter.field("updated_at").less_than(dt)
        assert "2025-01-15T12:30:00" in f.to_string()

    @pytest.mark.req("FR-007")
    def test_and_combination(self) -> None:
        f = Filter.field("name").contains("Corp") & Filter.field("status").equals("active")
        result = f.to_string()
        assert "&" in result
        assert 'name =~ "Corp"' in result
        assert 'status = "active"' in result

    @pytest.mark.req("FR-007")
    def test_or_combination(self) -> None:
        f = Filter.field("type").equals("customer") | Filter.field("type").equals("prospect")
        result = f.to_string()
        assert "|" in result

    @pytest.mark.req("FR-007")
    def test_not_negation(self) -> None:
        f = ~Filter.field("archived").equals(True)
        result = f.to_string()
        assert result.startswith("!(")
        assert "archived = true" in result

    @pytest.mark.req("FR-007")
    def test_complex_expression(self) -> None:
        """Test complex boolean expression."""
        f = (F.field("name").contains("Corp") | F.field("name").contains("Inc")) & ~F.field(
            "archived"
        ).equals(True)
        result = f.to_string()
        assert "&" in result
        assert "|" in result
        assert "!(" in result

    @pytest.mark.req("FR-007")
    def test_in_list(self) -> None:
        f = Filter.field("status").in_list(["active", "pending", "review"])
        result = f.to_string()
        # Should be OR combination of equals
        assert "|" in result
        assert '"active"' in result
        assert '"pending"' in result
        assert '"review"' in result

    @pytest.mark.req("FR-007")
    def test_in_list_empty(self) -> None:
        with pytest.raises(ValueError):
            _ = Filter.field("status").in_list([])

    @pytest.mark.req("FR-007")
    def test_raw_filter_escape_hatch(self) -> None:
        """Test raw filter string for power users."""
        f = Filter.raw('name =~ "Acme" & status = "active"')
        assert f.to_string() == 'name =~ "Acme" & status = "active"'

    @pytest.mark.req("FR-007")
    def test_and_multiple(self) -> None:
        f = Filter.and_(
            F.field("name").contains("Corp"),
            F.field("status").equals("active"),
            F.field("type").equals("customer"),
        )
        result = f.to_string()
        assert result.count("&") == 2

    @pytest.mark.req("FR-007")
    def test_or_multiple(self) -> None:
        f = Filter.or_(
            F.field("type").equals("customer"),
            F.field("type").equals("prospect"),
            F.field("type").equals("partner"),
        )
        result = f.to_string()
        assert result.count("|") == 2

    @pytest.mark.req("FR-007")
    def test_shorthand_f_alias(self) -> None:
        """F is a shorthand for Filter."""
        f = F.field("name").equals("Test")
        assert f.to_string() == 'name = "Test"'


class TestFilterEscaping:
    """Test string escaping in filters (golden fixtures for FR-007)."""

    @pytest.mark.req("FR-007")
    def test_escape_quotes(self) -> None:
        """Quotes in strings must be escaped."""
        assert _escape_string('Say "hello"') == 'Say \\"hello\\"'

    @pytest.mark.req("FR-007")
    def test_escape_backslashes(self) -> None:
        """Backslashes must be doubled."""
        assert _escape_string("path\\to\\file") == "path\\\\to\\\\file"

    @pytest.mark.req("FR-007")
    def test_escape_newlines(self) -> None:
        """Newlines must be escaped."""
        assert _escape_string("line1\nline2") == "line1\\nline2"

    @pytest.mark.req("FR-007")
    def test_escape_tabs(self) -> None:
        """Tabs must be escaped."""
        assert _escape_string("col1\tcol2") == "col1\\tcol2"

    @pytest.mark.req("FR-007")
    def test_escape_combined(self) -> None:
        """Test combined escaping."""
        result = _escape_string('He said "hi\\there\nbuddy"')
        assert result == 'He said \\"hi\\\\there\\nbuddy\\"'

    @pytest.mark.req("FR-007")
    def test_strip_nul_bytes(self) -> None:
        """NUL bytes are stripped for safety."""
        assert _escape_string("a\x00b") == "ab"

    @pytest.mark.req("FR-007")
    def test_filter_with_special_chars(self) -> None:
        """Filter with special characters in value."""
        f = Filter.field("description").contains('Say "hello" to O\'Brien')
        result = f.to_string()
        assert '\\"hello\\"' in result

    @pytest.mark.req("FR-007")
    def test_format_value_types(self) -> None:
        """Test value formatting for different types."""
        with pytest.raises(ValueError):
            _format_value(None)
        assert _format_value(True) == "true"
        assert _format_value(False) == "false"
        assert _format_value(42) == "42"
        assert _format_value(3.14) == "3.14"
        assert _format_value("hello") == '"hello"'


# =============================================================================
# FR-010: Resolve Helpers Tests
# =============================================================================


class TestResolveHelpers:
    """Test resolve helpers (FR-010)."""

    @pytest.mark.req("FR-010")
    def test_resolve_company_by_domain(self) -> None:
        """Test resolving a company by domain."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/organizations" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "organizations": [
                            {"id": 1, "name": "Acme Corp", "domain": "acme.com"},
                            {"id": 2, "name": "Acme Inc", "domain": "acmeinc.com"},
                        ],
                        "next_page_token": None,
                    },
                    request=request,
                )
            return httpx.Response(404, json={}, request=request)

        transport = httpx.MockTransport(handler)
        config = ClientConfig(api_key="test", max_retries=0, transport=transport)
        http_client = HTTPClient(config)
        try:
            companies = CompanyService(http_client)
            result = companies.resolve(domain="acme.com")
            assert result is not None
            assert result.domain == "acme.com"
            assert result.id == 1
        finally:
            http_client.close()

    @pytest.mark.req("FR-010")
    def test_resolve_company_not_found(self) -> None:
        """Test resolving a company that doesn't exist."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"organizations": [], "next_page_token": None},
                request=request,
            )

        transport = httpx.MockTransport(handler)
        config = ClientConfig(api_key="test", max_retries=0, transport=transport)
        http_client = HTTPClient(config)
        try:
            companies = CompanyService(http_client)
            result = companies.resolve(domain="notfound.com")
            assert result is None
        finally:
            http_client.close()

    @pytest.mark.req("FR-010")
    def test_resolve_company_requires_arg(self) -> None:
        """Test that resolve requires at least one argument."""
        config = ClientConfig(api_key="test", max_retries=0)
        http_client = HTTPClient(config)
        try:
            companies = CompanyService(http_client)
            with pytest.raises(ValueError, match="Must provide either"):
                companies.resolve()
        finally:
            http_client.close()

    @pytest.mark.req("FR-010")
    def test_resolve_person_by_email(self) -> None:
        """Test resolving a person by email."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/persons" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "persons": [
                            {
                                "id": 100,
                                "firstName": "John",
                                "lastName": "Doe",
                                "primaryEmailAddress": "john@example.com",
                                "emails": ["john@example.com", "jdoe@work.com"],
                            },
                        ],
                        "next_page_token": None,
                    },
                    request=request,
                )
            return httpx.Response(404, json={}, request=request)

        transport = httpx.MockTransport(handler)
        config = ClientConfig(api_key="test", max_retries=0, transport=transport)
        http_client = HTTPClient(config)
        try:
            persons = PersonService(http_client)
            result = persons.resolve(email="john@example.com")
            assert result is not None
            assert result.id == 100
        finally:
            http_client.close()

    @pytest.mark.req("FR-010")
    def test_resolve_person_by_secondary_email(self) -> None:
        """Test resolving a person by secondary email."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "persons": [
                        {
                            "id": 100,
                            "firstName": "John",
                            "lastName": "Doe",
                            "primaryEmailAddress": "john@example.com",
                            "emails": ["john@example.com", "jdoe@work.com"],
                        },
                    ],
                    "next_page_token": None,
                },
                request=request,
            )

        transport = httpx.MockTransport(handler)
        config = ClientConfig(api_key="test", max_retries=0, transport=transport)
        http_client = HTTPClient(config)
        try:
            persons = PersonService(http_client)
            result = persons.resolve(email="jdoe@work.com")
            assert result is not None
            assert result.id == 100
        finally:
            http_client.close()


# =============================================================================
# FR-011: Task Service Tests
# =============================================================================


class TestTaskService:
    """Test the task service (FR-011)."""

    @pytest.mark.req("FR-011")
    def test_task_get(self) -> None:
        """Test getting task status."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/tasks/company-merges/" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "id": "task-123",
                        "status": "success",
                        "resultsSummary": None,
                    },
                    request=request,
                )
            return httpx.Response(404, json={}, request=request)

        transport = httpx.MockTransport(handler)
        config = ClientConfig(
            api_key="test",
            max_retries=0,
            transport=transport,
            v2_base_url="https://api.affinity.co/v2",
        )
        http_client = HTTPClient(config)
        try:
            tasks = TaskService(http_client)
            task = tasks.get("https://api.affinity.co/v2/tasks/company-merges/123")
            assert task.id == "task-123"
            assert task.status == "success"
        finally:
            http_client.close()

    @pytest.mark.req("FR-011")
    def test_task_wait_immediate_success(self) -> None:
        """Test waiting for a task that's already complete."""
        call_count = {"value": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["value"] += 1
            return httpx.Response(
                200,
                json={"id": "task-123", "status": "success"},
                request=request,
            )

        transport = httpx.MockTransport(handler)
        config = ClientConfig(
            api_key="test",
            max_retries=0,
            transport=transport,
            v2_base_url="https://api.affinity.co/v2",
        )
        http_client = HTTPClient(config)
        try:
            tasks = TaskService(http_client)
            task = tasks.wait(
                "https://api.affinity.co/v2/tasks/company-merges/123",
                timeout=5.0,
            )
            assert task.status == "success"
            assert call_count["value"] == 1  # Only one poll needed
        finally:
            http_client.close()

    @pytest.mark.req("FR-011")
    def test_task_status_constants(self) -> None:
        """Test task status constants."""
        assert TaskStatus.PENDING == "pending"
        # The V2 spec (merge tasks) uses "in-progress"; "in_progress" never appears.
        assert TaskStatus.IN_PROGRESS == "in-progress"
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.FAILED == "failed"

    @pytest.mark.req("FR-011")
    def test_merge_status_matches_spec_and_accepts_old_spelling(self) -> None:
        from affinity.models.types import MergeStatus

        assert MergeStatus("in-progress") is MergeStatus.IN_PROGRESS
        assert MergeStatus.IN_PROGRESS.value == "in-progress"
        # Backward compatibility for callers using the SDK's earlier spelling
        assert MergeStatus("in_progress") is MergeStatus.IN_PROGRESS


# =============================================================================
# DX-008: Request/Response Hooks Tests
# =============================================================================


class TestRequestHooks:
    """Test request/response hooks (DX-008)."""

    @pytest.mark.req("DX-008")
    def test_on_request_hook_called(self) -> None:
        """Test that on_request hook is called."""
        hook_calls: list[RequestInfo] = []

        def on_request(info: RequestInfo) -> None:
            hook_calls.append(info)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []}, request=request)

        transport = httpx.MockTransport(handler)
        config = ClientConfig(
            api_key="test-key",
            max_retries=0,
            transport=transport,
            on_request=on_request,
        )
        http_client = HTTPClient(config)
        try:
            http_client.get("/companies")
            assert len(hook_calls) == 1
            assert hook_calls[0].method == "GET"
            assert "/companies" in hook_calls[0].url
        finally:
            http_client.close()

    @pytest.mark.req("DX-008")
    def test_on_response_hook_called(self) -> None:
        """Test that on_response hook is called."""
        hook_calls: list[ResponseInfo] = []

        def on_response(info: ResponseInfo) -> None:
            hook_calls.append(info)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []}, request=request)

        transport = httpx.MockTransport(handler)
        config = ClientConfig(
            api_key="test-key",
            max_retries=0,
            transport=transport,
            on_response=on_response,
        )
        http_client = HTTPClient(config)
        try:
            http_client.get("/companies")
            assert len(hook_calls) == 1
            assert hook_calls[0].status_code == 200
            assert hook_calls[0].elapsed_ms >= 0
        finally:
            http_client.close()

    @pytest.mark.req("DX-008")
    def test_hooks_do_not_expose_api_key(self) -> None:
        """Test that hooks don't expose the API key."""
        request_infos: list[RequestInfo] = []
        response_infos: list[ResponseInfo] = []

        def on_request(info: RequestInfo) -> None:
            request_infos.append(info)

        def on_response(info: ResponseInfo) -> None:
            response_infos.append(info)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []}, request=request)

        transport = httpx.MockTransport(handler)
        config = ClientConfig(
            api_key="super-secret-key",
            max_retries=0,
            transport=transport,
            on_request=on_request,
            on_response=on_response,
        )
        http_client = HTTPClient(config)
        try:
            http_client.get("/companies")

            # Check request info doesn't have auth
            assert len(request_infos) == 1
            for key, value in request_infos[0].headers.items():
                assert "super-secret-key" not in value
                assert key.lower() != "authorization"

            # URL should be redacted if it contains key
            assert "super-secret-key" not in request_infos[0].url
        finally:
            http_client.close()

    @pytest.mark.req("DX-008")
    def test_hooks_with_affinity_client(self) -> None:
        """Test hooks work when passed to Affinity client."""
        hook_calls: list[str] = []

        def on_request(info: RequestInfo) -> None:
            hook_calls.append(f"REQ:{info.method}")

        def on_response(info: ResponseInfo) -> None:
            hook_calls.append(f"RES:{info.status_code}")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextUrl": None}},
                request=request,
            )

        transport = httpx.MockTransport(handler)
        config = ClientConfig(
            api_key="test",
            max_retries=0,
            transport=transport,
            on_request=on_request,
            on_response=on_response,
        )
        http_client = HTTPClient(config)
        try:
            lists = ListService(http_client)
            _ = lists.list()
            assert "REQ:GET" in hook_calls
            assert "RES:200" in hook_calls
        finally:
            http_client.close()


# =============================================================================
# TR-015: Version Compatibility Tests
# =============================================================================


class TestVersionCompatibility:
    """Tests for v2 version compatibility features (TR-015)."""

    @pytest.mark.req("TR-015")
    def test_expected_v2_version_config(self) -> None:
        """Test that expected_v2_version can be configured."""
        config = ClientConfig(
            api_key="test",
            expected_v2_version="2024-01-01",
        )
        assert config.expected_v2_version == "2024-01-01"

    @pytest.mark.req("TR-015")
    def test_client_accepts_expected_v2_version(self) -> None:
        """Test that Affinity client accepts expected_v2_version."""
        # Should not raise
        client = Affinity(
            api_key="test",
            expected_v2_version="2024-01-01",
        )
        client.close()

    @pytest.mark.req("TR-015")
    def test_version_compatibility_error_creation(self) -> None:
        """Test VersionCompatibilityError can be created with context."""
        err = VersionCompatibilityError(
            "Response parsing failed",
            expected_version="2024-01-01",
            parsing_error="Missing field 'foo'",
        )
        assert err.expected_version == "2024-01-01"
        assert err.parsing_error == "Missing field 'foo'"
        assert "2024-01-01" in str(err)

    @pytest.mark.req("TR-015")
    def test_wrap_validation_error(self) -> None:
        """Test HTTPClient.wrap_validation_error provides guidance."""
        config = ClientConfig(
            api_key="test",
            expected_v2_version="2024-01-01",
        )
        http_client = HTTPClient(config)
        try:
            err = http_client.wrap_validation_error(
                ValueError("field 'foo' is required"),
                context="Person.get",
            )
            assert err.expected_version == "2024-01-01"
            assert "Person.get" in str(err)
            assert "v2 API version mismatch" in str(err)
        finally:
            http_client.close()

    @pytest.mark.req("TR-015")
    def test_version_error_without_expected_version(self) -> None:
        """Test wrap_validation_error works without expected_v2_version."""
        config = ClientConfig(api_key="test")
        http_client = HTTPClient(config)
        try:
            err = http_client.wrap_validation_error(ValueError("parse error"))
            assert err.expected_version is None
            assert "parse error" in str(err)
        finally:
            http_client.close()


# =============================================================================
# TR-013: OpenAPI Schema Alignment Tests
# =============================================================================


class TestOpenAPIAlignment:
    """Tests for OpenAPI schema alignment features (TR-013)."""

    @pytest.mark.req("TR-013")
    def test_openapi_validation_script_exists(self) -> None:
        """Test that the OpenAPI validation script exists."""
        script_path = Path(__file__).parent.parent / "tools" / "validate_openapi_models.py"
        assert script_path.exists(), "OpenAPI validation script should exist"

    @pytest.mark.req("TR-013")
    def test_openapi_script_syntax_valid(self) -> None:
        """Test that the OpenAPI validation script has valid Python syntax."""
        script_path = Path(__file__).parent.parent / "tools" / "validate_openapi_models.py"
        source = script_path.read_text()
        # Should not raise SyntaxError
        ast.parse(source)

    @pytest.mark.req("TR-013")
    def test_openapi_script_has_required_functions(self) -> None:
        """Test that the OpenAPI script defines expected functions."""
        script_path = Path(__file__).parent.parent / "tools" / "validate_openapi_models.py"
        source = script_path.read_text()
        tree = ast.parse(source)

        function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert "validate_model" in function_names
        assert "get_sdk_models" in function_names
        assert "main" in function_names

    @pytest.mark.req("TR-013")
    def test_openapi_schema_url_in_script(self) -> None:
        """Test that the OpenAPI schema URL is correctly configured."""
        script_path = Path(__file__).parent.parent / "tools" / "validate_openapi_models.py"
        source = script_path.read_text()

        # Check for the expected URL
        assert "openapi.json" in source
        assert "yaniv-golan/affinity-api-docs" in source
