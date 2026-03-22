"""Additional tests for affinity.exceptions module to improve coverage."""

from __future__ import annotations

from affinity.exceptions import (
    AffinityError,
    CompanyMergedError,
    CompanyNotFoundError,
    EntityNotFoundError,
    ErrorDiagnostics,
    MergedEntityError,
    NetworkError,
    OpportunityNotFoundError,
    PersonMergedError,
    PersonNotFoundError,
    RateLimitError,
    TimeoutError,
    UnsafeUrlError,
    ValidationError,
    VersionCompatibilityError,
    WebhookMissingKeyError,
    WriteNotAllowedError,
    error_from_response,
)


class TestErrorDiagnostics:
    """Tests for ErrorDiagnostics dataclass."""

    def test_all_fields_none_by_default(self) -> None:
        diag = ErrorDiagnostics()
        assert diag.method is None
        assert diag.url is None
        assert diag.request_params is None
        assert diag.api_version is None
        assert diag.base_url is None
        assert diag.request_id is None
        assert diag.http_version is None
        assert diag.response_headers is None
        assert diag.response_body_snippet is None

    def test_all_fields_populated(self) -> None:
        diag = ErrorDiagnostics(
            method="POST",
            url="https://api.affinity.co/v2/companies",
            request_params={"name": "Acme"},
            api_version="v2",
            base_url="https://api.affinity.co",
            request_id="req-123",
            http_version="HTTP/2",
            response_headers={"Content-Type": "application/json"},
            response_body_snippet='{"error": "bad"}',
        )
        assert diag.method == "POST"
        assert diag.url == "https://api.affinity.co/v2/companies"
        assert diag.request_params == {"name": "Acme"}
        assert diag.api_version == "v2"
        assert diag.base_url == "https://api.affinity.co"
        assert diag.request_id == "req-123"
        assert diag.http_version == "HTTP/2"
        assert diag.response_headers == {"Content-Type": "application/json"}
        assert diag.response_body_snippet == '{"error": "bad"}'


class TestAffinityErrorStr:
    """Tests for AffinityError.__str__ with diagnostics."""

    def test_str_with_status_code_only(self) -> None:
        err = AffinityError("Something went wrong", status_code=500)
        assert str(err) == "[500] Something went wrong"

    def test_str_with_method_and_url(self) -> None:
        diag = ErrorDiagnostics(method="POST", url="https://api.affinity.co/v2/companies")
        err = AffinityError("Failed", status_code=400, diagnostics=diag)
        assert str(err) == "[400] Failed (POST https://api.affinity.co/v2/companies)"

    def test_str_with_url_only(self) -> None:
        diag = ErrorDiagnostics(url="https://api.affinity.co/v2/companies")
        err = AffinityError("Failed", status_code=400, diagnostics=diag)
        assert str(err) == "[400] Failed (url=https://api.affinity.co/v2/companies)"

    def test_str_with_request_id(self) -> None:
        diag = ErrorDiagnostics(
            method="GET",
            url="https://api.affinity.co/v2/persons/123",
            request_id="abc-123",
        )
        err = AffinityError("Not found", status_code=404, diagnostics=diag)
        assert "[request_id=abc-123]" in str(err)
        assert "GET https://api.affinity.co/v2/persons/123" in str(err)

    def test_str_with_all_diagnostics(self) -> None:
        diag = ErrorDiagnostics(
            method="DELETE",
            url="https://api.affinity.co/v2/notes/456",
            request_id="xyz-789",
        )
        err = AffinityError("Forbidden", status_code=403, diagnostics=diag)
        s = str(err)
        assert "[403]" in s
        assert "Forbidden" in s
        assert "DELETE https://api.affinity.co/v2/notes/456" in s
        assert "[request_id=xyz-789]" in s


class TestRateLimitError:
    """Tests for RateLimitError initialization."""

    def test_with_retry_after(self) -> None:
        err = RateLimitError("Rate limited", retry_after=60, status_code=429)
        assert err.retry_after == 60
        assert err.status_code == 429
        assert err.message == "Rate limited"

    def test_without_retry_after(self) -> None:
        err = RateLimitError("Rate limited", status_code=429)
        assert err.retry_after is None

    def test_with_diagnostics(self) -> None:
        diag = ErrorDiagnostics(method="GET", url="https://api.affinity.co/v2/persons")
        err = RateLimitError(
            "Too many requests",
            retry_after=30,
            status_code=429,
            response_body={"errors": [{"message": "Rate limit exceeded"}]},
            diagnostics=diag,
        )
        assert err.retry_after == 30
        assert err.diagnostics is diag


class TestTimeoutError:
    """Tests for TimeoutError initialization."""

    def test_with_diagnostics(self) -> None:
        diag = ErrorDiagnostics(method="POST", url="https://api.affinity.co/v2/companies")
        err = TimeoutError("Request timed out after 30s", diagnostics=diag)
        assert err.message == "Request timed out after 30s"
        assert err.diagnostics is diag
        assert err.status_code is None

    def test_without_diagnostics(self) -> None:
        err = TimeoutError("Timeout")
        assert err.diagnostics is None


class TestNetworkError:
    """Tests for NetworkError initialization."""

    def test_with_diagnostics(self) -> None:
        diag = ErrorDiagnostics(url="https://api.affinity.co")
        err = NetworkError("Connection refused", diagnostics=diag)
        assert err.message == "Connection refused"
        assert err.diagnostics is diag

    def test_without_diagnostics(self) -> None:
        err = NetworkError("DNS resolution failed")
        assert err.diagnostics is None


class TestWriteNotAllowedError:
    """Tests for WriteNotAllowedError initialization."""

    def test_init(self) -> None:
        err = WriteNotAllowedError(
            "Write operations are disabled",
            method="POST",
            url="https://api.affinity.co/v2/companies",
        )
        assert err.method == "POST"
        assert err.url == "https://api.affinity.co/v2/companies"
        assert err.diagnostics is not None
        assert err.diagnostics.method == "POST"
        assert err.diagnostics.url == "https://api.affinity.co/v2/companies"

    def test_str(self) -> None:
        err = WriteNotAllowedError(
            "Writes blocked",
            method="DELETE",
            url="https://api.affinity.co/v2/notes/123",
        )
        s = str(err)
        assert "Writes blocked" in s
        assert "DELETE" in s


class TestUnsafeUrlError:
    """Tests for UnsafeUrlError initialization."""

    def test_with_url(self) -> None:
        err = UnsafeUrlError("Insecure redirect", url="http://evil.com/phish")
        assert err.url == "http://evil.com/phish"
        assert err.diagnostics is not None
        assert err.diagnostics.url == "http://evil.com/phish"

    def test_without_url(self) -> None:
        err = UnsafeUrlError("Generic unsafe URL error")
        assert err.url is None
        assert err.diagnostics is None


class TestEntityNotFoundErrors:
    """Tests for entity-specific NotFoundError subclasses."""

    def test_entity_not_found_error(self) -> None:
        err = EntityNotFoundError("Widget", 999)
        assert err.entity_type == "Widget"
        assert err.entity_id == 999
        assert "Widget with ID 999 not found" in str(err)

    def test_person_not_found_error(self) -> None:
        err = PersonNotFoundError(12345)
        assert err.entity_type == "Person"
        assert err.entity_id == 12345
        assert "Person with ID 12345 not found" in str(err)

    def test_company_not_found_error(self) -> None:
        err = CompanyNotFoundError(67890)
        assert err.entity_type == "Company"
        assert err.entity_id == 67890
        assert "Company with ID 67890 not found" in str(err)

    def test_opportunity_not_found_error(self) -> None:
        err = OpportunityNotFoundError(11111)
        assert err.entity_type == "Opportunity"
        assert err.entity_id == 11111
        assert "Opportunity with ID 11111 not found" in str(err)


class TestVersionCompatibilityError:
    """Tests for VersionCompatibilityError."""

    def test_with_expected_version(self) -> None:
        err = VersionCompatibilityError(
            "Version mismatch",
            expected_version="2024-01-01",
            status_code=200,
        )
        assert err.expected_version == "2024-01-01"
        s = str(err)
        assert "expected_v2_version=2024-01-01" in s

    def test_with_parsing_error(self) -> None:
        err = VersionCompatibilityError(
            "Parse failed",
            parsing_error="Missing 'data' key",
        )
        assert err.parsing_error == "Missing 'data' key"
        s = str(err)
        assert "parsing_error=Missing 'data' key" in s

    def test_with_both(self) -> None:
        err = VersionCompatibilityError(
            "Incompatible response",
            expected_version="2024-02-15",
            parsing_error="Unknown field 'legacy_id'",
        )
        s = str(err)
        assert "expected_v2_version=2024-02-15" in s
        assert "parsing_error=Unknown field 'legacy_id'" in s


class TestWebhookMissingKeyError:
    """Tests for WebhookMissingKeyError."""

    def test_init(self) -> None:
        err = WebhookMissingKeyError("Required key 'entity' missing", key="entity")
        assert err.key == "entity"
        assert "Required key 'entity' missing" in str(err)


class TestErrorFromResponse:
    """Additional tests for error_from_response factory function."""

    def test_rate_limit_with_retry_after(self) -> None:
        err = error_from_response(
            429,
            {"errors": [{"message": "Rate limited"}]},
            retry_after=120,
        )
        assert isinstance(err, RateLimitError)
        assert err.retry_after == 120

    def test_list_response_with_dict(self) -> None:
        # Test response as list of dicts
        err = error_from_response(400, [{"message": "First error"}, {"message": "Second"}])
        assert "First error" in str(err)

    def test_list_response_with_error_key(self) -> None:
        err = error_from_response(400, [{"error": "Something failed"}])
        assert "Something failed" in str(err)

    def test_list_response_with_detail_key(self) -> None:
        err = error_from_response(400, [{"detail": "Invalid input"}])
        assert "Invalid input" in str(err)

    def test_list_response_with_string(self) -> None:
        err = error_from_response(400, ["Error string directly"])
        assert "Error string directly" in str(err)

    def test_error_key_as_string(self) -> None:
        err = error_from_response(500, {"error": "Server exploded"})
        assert "Server exploded" in str(err)

    def test_fallback_to_snippet_in_diagnostics(self) -> None:
        diag = ErrorDiagnostics(response_body_snippet='{"custom": "error body"}')
        err = error_from_response(418, {}, diagnostics=diag)
        assert '{"custom": "error body"}' in str(err)

    def test_snippet_ignored_if_empty(self) -> None:
        diag = ErrorDiagnostics(response_body_snippet="{}")
        err = error_from_response(418, {}, diagnostics=diag)
        assert "Unknown error" in str(err)

    def test_snippet_ignored_if_empty_array(self) -> None:
        diag = ErrorDiagnostics(response_body_snippet="[]")
        err = error_from_response(418, {}, diagnostics=diag)
        assert "Unknown error" in str(err)

    def test_validation_error_with_param(self) -> None:
        err = error_from_response(
            400,
            {"errors": [{"message": "Invalid email", "param": "email"}]},
        )
        assert isinstance(err, ValidationError)
        assert err.param == "email"
        assert "(param: email)" in str(err)

    def test_errors_list_with_empty_items_skipped(self) -> None:
        # Ensure we skip empty/whitespace-only messages
        err = error_from_response(400, {"errors": [{"message": "  "}, {"message": "Real error"}]})
        assert "Real error" in str(err)


class TestValidationErrorStr:
    """Tests for ValidationError.__str__."""

    def test_str_without_param(self) -> None:
        err = ValidationError("Bad request", status_code=400)
        assert str(err) == "[400] Bad request"

    def test_str_with_param(self) -> None:
        err = ValidationError("Invalid value", param="company_id", status_code=422)
        assert "(param: company_id)" in str(err)


class TestMergedEntityError:
    """Tests for MergedEntityError and subclasses."""

    MERGE_MSG = "292479388 no longer exists as it has been merged into 301128758"

    def test_match_positive(self) -> None:
        m = MergedEntityError.match(self.MERGE_MSG)
        assert m is not None
        assert m.group(1) == "292479388"
        assert m.group(2) == "301128758"

    def test_match_negative(self) -> None:
        assert MergedEntityError.match("Invalid email format") is None

    def test_init_base(self) -> None:
        err = MergedEntityError(
            self.MERGE_MSG,
            source_id=292479388,
            target_id=301128758,
            status_code=422,
        )
        assert err.source_id == 292479388
        assert err.target_id == 301128758
        assert err.entity_type is None
        assert err.status_code == 422
        assert isinstance(err, ValidationError)

    def test_company_merged_error(self) -> None:
        err = CompanyMergedError(
            self.MERGE_MSG,
            source_id=292479388,
            target_id=301128758,
        )
        assert err.entity_type == "Company"
        assert err.source_id == 292479388
        assert err.target_id == 301128758
        assert isinstance(err, MergedEntityError)
        assert isinstance(err, ValidationError)

    def test_person_merged_error(self) -> None:
        err = PersonMergedError(
            "100 no longer exists as it has been merged into 200",
            source_id=100,
            target_id=200,
        )
        assert err.entity_type == "Person"
        assert err.source_id == 100
        assert err.target_id == 200
        assert isinstance(err, MergedEntityError)

    def test_catchable_as_validation_error(self) -> None:
        """MergedEntityError can be caught by existing ValidationError handlers."""
        err = CompanyMergedError(self.MERGE_MSG, source_id=292479388, target_id=301128758)
        try:
            raise err
        except ValidationError as caught:
            assert isinstance(caught, CompanyMergedError)
            assert caught.source_id == 292479388


class TestErrorFromResponseMergedEntity:
    """Tests for error_from_response detecting merged entities."""

    MERGE_MSG = "292479388 no longer exists as it has been merged into 301128758"

    def test_422_with_merge_message_returns_merged_entity_error(self) -> None:
        err = error_from_response(
            422,
            {"errors": [{"message": self.MERGE_MSG}]},
        )
        assert isinstance(err, MergedEntityError)
        assert err.source_id == 292479388
        assert err.target_id == 301128758
        assert err.status_code == 422

    def test_422_merge_with_company_url_returns_company_merged(self) -> None:
        diag = ErrorDiagnostics(
            method="GET",
            url="https://api.affinity.co/v2/companies/292479388",
        )
        err = error_from_response(
            422,
            {"errors": [{"message": self.MERGE_MSG}]},
            diagnostics=diag,
        )
        assert isinstance(err, CompanyMergedError)
        assert err.entity_type == "Company"

    def test_422_merge_with_organizations_url_returns_company_merged(self) -> None:
        diag = ErrorDiagnostics(
            url="https://api.affinity.co/organizations/292479388",
        )
        err = error_from_response(
            422,
            {"errors": [{"message": self.MERGE_MSG}]},
            diagnostics=diag,
        )
        assert isinstance(err, CompanyMergedError)

    def test_422_merge_with_person_url_returns_person_merged(self) -> None:
        diag = ErrorDiagnostics(
            url="https://api.affinity.co/v2/persons/100",
        )
        err = error_from_response(
            422,
            {"errors": [{"message": "100 no longer exists as it has been merged into 200"}]},
            diagnostics=diag,
        )
        assert isinstance(err, PersonMergedError)
        assert err.entity_type == "Person"
        assert err.source_id == 100
        assert err.target_id == 200

    def test_422_merge_with_people_url_returns_person_merged(self) -> None:
        diag = ErrorDiagnostics(
            url="https://api.affinity.co/people/100",
        )
        err = error_from_response(
            422,
            {"errors": [{"message": "100 no longer exists as it has been merged into 200"}]},
            diagnostics=diag,
        )
        assert isinstance(err, PersonMergedError)

    def test_422_merge_without_url_returns_generic_merged(self) -> None:
        err = error_from_response(
            422,
            {"errors": [{"message": self.MERGE_MSG}]},
        )
        assert isinstance(err, MergedEntityError)
        assert not isinstance(err, CompanyMergedError)
        assert not isinstance(err, PersonMergedError)
        assert err.entity_type is None

    def test_422_non_merge_still_returns_validation_error(self) -> None:
        err = error_from_response(
            422,
            {"errors": [{"message": "Invalid email format"}]},
        )
        assert isinstance(err, ValidationError)
        assert not isinstance(err, MergedEntityError)
