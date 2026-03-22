"""Coverage tests for affinity.cli.context.

Targets normalize_exception() for ~20 exception types, build_result()
parameter combinations, and helper functions.
"""

from __future__ import annotations

import errno
import time
from unittest.mock import MagicMock

from affinity.cli.context import (
    _hint_for_validation_message,
    build_result,
    normalize_exception,
)
from affinity.cli.errors import CLIError
from affinity.cli.results import CommandContext, ErrorInfo
from affinity.exceptions import (
    AffinityError,
    AuthenticationError,
    AuthorizationError,
    CompanyMergedError,
    ConfigurationError,
    ConflictError,
    MergedEntityError,
    NetworkError,
    NotFoundError,
    PersonMergedError,
    RateLimitError,
    ServerError,
    UnsafeUrlError,
    UnsupportedOperationError,
    ValidationError,
    WriteNotAllowedError,
)
from affinity.exceptions import TimeoutError as AffinityTimeoutError

# ---------------------------------------------------------------------------
# normalize_exception — file-system errors
# ---------------------------------------------------------------------------


class TestNormalizeFileErrors:
    def test_file_exists_error(self) -> None:
        exc = FileExistsError(17, "File exists", "/tmp/out")
        result = normalize_exception(exc)
        assert result.error_type == "file_exists"
        assert result.exit_code == 2
        assert "/tmp/out" in result.message

    def test_permission_error(self) -> None:
        exc = PermissionError(13, "Permission denied", "/tmp/out")
        result = normalize_exception(exc)
        assert result.error_type == "permission_denied"
        assert result.exit_code == 2

    def test_is_a_directory_error(self) -> None:
        exc = IsADirectoryError(21, "Is a directory", "/tmp")
        result = normalize_exception(exc)
        assert result.error_type == "io_error"
        assert "directory" in result.message

    def test_oserror_enospc(self) -> None:
        exc = OSError(errno.ENOSPC, "No space left on device")
        result = normalize_exception(exc)
        assert result.error_type == "disk_full"
        assert "space" in result.message.lower()


# ---------------------------------------------------------------------------
# normalize_exception — Affinity errors
# ---------------------------------------------------------------------------


class TestNormalizeAffinityErrors:
    def test_write_not_allowed(self) -> None:
        exc = WriteNotAllowedError(
            "Write blocked", method="POST", url="https://api.example.com/v2/persons"
        )
        result = normalize_exception(exc)
        assert result.error_type == "write_not_allowed"
        assert result.exit_code == 2
        assert "readonly" in result.hint.lower()

    def test_rate_limit_error_without_retry_after(self) -> None:
        exc = RateLimitError("Too many requests", status_code=429)
        result = normalize_exception(exc)
        assert result.error_type == "rate_limited"
        assert result.exit_code == 5
        assert "retry" in result.hint.lower()

    def test_rate_limit_error_with_retry_after(self) -> None:
        exc = RateLimitError("Too many requests", status_code=429, retry_after=30)
        result = normalize_exception(exc)
        assert result.error_type == "rate_limited"
        assert "30" in result.hint

    def test_authentication_error(self) -> None:
        exc = AuthenticationError("Invalid API key", status_code=401)
        result = normalize_exception(exc)
        assert result.error_type == "auth_error"
        assert result.exit_code == 3

    def test_authorization_error(self) -> None:
        exc = AuthorizationError("Forbidden", status_code=403)
        result = normalize_exception(exc)
        assert result.error_type == "forbidden"
        assert result.exit_code == 3

    def test_not_found_error(self) -> None:
        exc = NotFoundError("Person not found", status_code=404)
        result = normalize_exception(exc)
        assert result.error_type == "not_found"
        assert result.exit_code == 4

    def test_conflict_error(self) -> None:
        exc = ConflictError("Duplicate email", status_code=409)
        result = normalize_exception(exc)
        assert result.error_type == "conflict"
        assert result.exit_code == 1

    def test_unsafe_url_error(self) -> None:
        exc = UnsafeUrlError("Bad redirect", url="http://evil.com")
        result = normalize_exception(exc)
        assert result.error_type == "unsafe_url"
        assert result.exit_code == 1

    def test_unsupported_operation_error(self) -> None:
        exc = UnsupportedOperationError("Not in V2")
        result = normalize_exception(exc)
        assert result.error_type == "unsupported_operation"
        assert result.exit_code == 1

    def test_server_error(self) -> None:
        exc = ServerError("Internal error", status_code=500)
        result = normalize_exception(exc)
        assert result.error_type == "server_error"
        assert result.exit_code == 5

    def test_network_error(self) -> None:
        exc = NetworkError("Connection refused")
        result = normalize_exception(exc)
        assert result.error_type == "network_error"
        assert result.exit_code == 1

    def test_timeout_error(self) -> None:
        exc = AffinityTimeoutError("Request timed out")
        result = normalize_exception(exc)
        assert result.error_type == "timeout"
        assert result.exit_code == 1

    def test_configuration_error(self) -> None:
        exc = ConfigurationError("Missing API key")
        result = normalize_exception(exc)
        assert result.error_type == "config_error"
        assert result.exit_code == 2

    def test_generic_affinity_error(self) -> None:
        exc = AffinityError("Something else")
        result = normalize_exception(exc)
        assert result.error_type == "api_error"
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# normalize_exception — ValidationError paths
# ---------------------------------------------------------------------------


class TestNormalizeMergedEntityError:
    def test_company_merged_error(self) -> None:
        exc = CompanyMergedError(
            "292479388 no longer exists as it has been merged into 301128758",
            source_id=292479388,
            target_id=301128758,
            status_code=422,
        )
        result = normalize_exception(exc)
        assert result.error_type == "entity_merged"
        assert result.exit_code == 4
        assert result.details["sourceId"] == 292479388
        assert result.details["targetId"] == 301128758
        assert result.details["entityType"] == "Company"
        assert "301128758" in result.hint
        assert "Use the target ID" in result.hint

    def test_person_merged_error(self) -> None:
        exc = PersonMergedError(
            "100 no longer exists as it has been merged into 200",
            source_id=100,
            target_id=200,
        )
        result = normalize_exception(exc)
        assert result.error_type == "entity_merged"
        assert result.details["entityType"] == "Person"

    def test_generic_merged_error(self) -> None:
        exc = MergedEntityError(
            "123 no longer exists as it has been merged into 456",
            source_id=123,
            target_id=456,
        )
        result = normalize_exception(exc)
        assert result.error_type == "entity_merged"
        assert result.exit_code == 4
        assert "entityType" not in result.details

    def test_not_caught_as_validation_error(self) -> None:
        """MergedEntityError must be handled before ValidationError in normalize_exception."""
        exc = CompanyMergedError(
            "1 no longer exists as it has been merged into 2",
            source_id=1,
            target_id=2,
        )
        result = normalize_exception(exc)
        # Must NOT fall through to the ValidationError handler
        assert result.error_type != "validation_error"


class TestNormalizeValidationError:
    def test_basic_validation_error(self) -> None:
        exc = ValidationError("Invalid email format")
        result = normalize_exception(exc)
        assert result.error_type == "validation_error"
        assert result.exit_code == 2

    def test_date_range_hint(self) -> None:
        exc = ValidationError("Date range must be within 1 year")
        result = normalize_exception(exc)
        assert "1 year" in result.hint
        assert "Split" in result.hint

    def test_with_person_id_hint(self) -> None:
        exc = ValidationError("Invalid person", status_code=422)
        diag = MagicMock()
        diag.method = "POST"
        diag.url = "https://api.affinity.co/v2/persons?person_id=123"
        diag.api_version = None
        diag.request_id = None
        diag.response_headers = None
        diag.response_body_snippet = None
        exc.diagnostics = diag
        result = normalize_exception(exc)
        assert result.error_type == "validation_error"

    def test_field_name_extraction(self) -> None:
        exc = ValidationError("Field email: must be valid")
        result = normalize_exception(exc)
        assert result.error_type == "validation_error"


# ---------------------------------------------------------------------------
# normalize_exception — Click & generic errors
# ---------------------------------------------------------------------------


class TestNormalizeClickAndGenericErrors:
    def test_cli_error_passthrough(self) -> None:
        exc = CLIError("already a CLIError")
        result = normalize_exception(exc)
        assert result is exc

    def test_click_usage_error(self) -> None:
        from affinity.cli.click_compat import click

        exc = click.UsageError("No such option: --foo")
        result = normalize_exception(exc)
        assert result.error_type == "usage_error"
        assert result.exit_code == 2

    def test_click_exception(self) -> None:
        from affinity.cli.click_compat import click

        exc = click.ClickException("Bad thing")
        result = normalize_exception(exc)
        assert result.error_type == "error"
        assert result.exit_code == 1

    def test_generic_exception_fallback(self) -> None:
        exc = RuntimeError("unexpected")
        result = normalize_exception(exc)
        assert result.error_type == "internal_error"
        assert result.exit_code == 1
        assert "unexpected" in result.message


# ---------------------------------------------------------------------------
# _hint_for_validation_message
# ---------------------------------------------------------------------------


class TestHintForValidationMessage:
    def test_date_range_pattern(self) -> None:
        hint = _hint_for_validation_message("Date range must be within 1 year")
        assert hint is not None
        assert "1 year" in hint

    def test_no_match(self) -> None:
        assert _hint_for_validation_message("some other error") is None


# ---------------------------------------------------------------------------
# build_result
# ---------------------------------------------------------------------------


class TestBuildResult:
    def test_success(self) -> None:
        result = build_result(
            ok=True,
            command=CommandContext(name="test"),
            started_at=time.time(),
            data={"id": 1},
            warnings=[],
            profile=None,
            rate_limit=None,
        )
        assert result.ok is True
        assert result.data == {"id": 1}

    def test_with_error(self) -> None:
        result = build_result(
            ok=False,
            command=CommandContext(name="test"),
            started_at=time.time(),
            data=None,
            warnings=[],
            profile=None,
            rate_limit=None,
            error=ErrorInfo(type="not_found", message="Not found"),
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.type == "not_found"

    def test_with_pagination(self) -> None:
        result = build_result(
            ok=True,
            command=CommandContext(name="test"),
            started_at=time.time(),
            data=[1, 2, 3],
            warnings=[],
            profile=None,
            rate_limit=None,
            pagination={"nextUrl": "https://example.com?page=2"},
        )
        assert result.meta.pagination is not None

    def test_with_summary(self) -> None:
        from affinity.cli.results import ResultSummary

        result = build_result(
            ok=True,
            command=CommandContext(name="test"),
            started_at=time.time(),
            data=[],
            warnings=["warning1"],
            profile="default",
            rate_limit=None,
            summary=ResultSummary(total_rows=10),
        )
        assert result.meta.summary is not None
        assert result.meta.summary.total_rows == 10

    def test_with_columns(self) -> None:
        result = build_result(
            ok=True,
            command=CommandContext(name="test"),
            started_at=time.time(),
            data=[],
            warnings=[],
            profile=None,
            rate_limit=None,
            columns=[{"name": "id", "type": "int"}],
        )
        assert result.meta.columns is not None

    def test_with_resolved(self) -> None:
        result = build_result(
            ok=True,
            command=CommandContext(name="test"),
            started_at=time.time(),
            data=[],
            warnings=[],
            profile=None,
            rate_limit=None,
            resolved={"entity": {"id": 1, "type": "person"}},
        )
        assert result.meta.resolved is not None

    def test_with_artifacts(self) -> None:
        from affinity.cli.results import Artifact

        result = build_result(
            ok=True,
            command=CommandContext(name="test"),
            started_at=time.time(),
            data=None,
            warnings=[],
            profile=None,
            rate_limit=None,
            artifacts=[
                Artifact(
                    path="/tmp/file.csv",
                    type="csv",
                    pathIsRelative=False,
                )
            ],
        )
        assert len(result.artifacts) == 1

    def test_negative_duration_clamped(self) -> None:
        result = build_result(
            ok=True,
            command=CommandContext(name="test"),
            started_at=time.time() + 9999,
            data=None,
            warnings=[],
            profile=None,
            rate_limit=None,
        )
        assert result.meta.duration_ms == 0
