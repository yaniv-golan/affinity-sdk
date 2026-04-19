"""
Custom exceptions for the Affinity API client.

All exceptions inherit from AffinityError for easy catching of all library errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ErrorDiagnostics:
    method: str | None = None
    url: str | None = None
    request_params: dict[str, Any] | None = None
    api_version: str | None = None  # "v1" | "v2" (string to avoid circular types)
    base_url: str | None = None
    request_id: str | None = None
    http_version: str | None = None
    response_headers: dict[str, str] | None = None
    response_body_snippet: str | None = None


class AffinityError(Exception):
    """Base exception for all Affinity API errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any | None = None,
        diagnostics: ErrorDiagnostics | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        self.diagnostics = diagnostics

    def __str__(self) -> str:
        base = self.message
        if self.status_code:
            base = f"[{self.status_code}] {base}"
        if self.diagnostics:
            # Include method + url if both present, or just url if only url present
            if self.diagnostics.method and self.diagnostics.url:
                base = f"{base} ({self.diagnostics.method} {self.diagnostics.url})"
            elif self.diagnostics.url:
                base = f"{base} (url={self.diagnostics.url})"
            if self.diagnostics.request_id:
                base = f"{base} [request_id={self.diagnostics.request_id}]"
        return base


# =============================================================================
# HTTP Errors
# =============================================================================


class AuthenticationError(AffinityError):
    """
    401 Unauthorized - Invalid or missing API key.

    Your API key is invalid or was not provided.
    """

    pass


class AuthorizationError(AffinityError):
    """
    403 Forbidden - Insufficient permissions.

    You don't have permission to access this resource or perform this action.
    This can happen with:
    - Private lists you don't have access to
    - Admin-only actions
    - Resource-level permission restrictions
    """

    pass


class NotFoundError(AffinityError):
    """
    404 Not Found - Resource doesn't exist.

    The requested resource (person, company, list, etc.) was not found.
    This could mean:
    - The ID is invalid
    - The resource was deleted
    - You don't have access to view it
    """

    pass


class ValidationError(AffinityError):
    """
    400/422 Bad Request/Unprocessable Entity - Invalid request data.

    The request data is malformed or logically invalid.
    Check the error message for details about what's wrong.
    """

    def __init__(
        self,
        message: str,
        *,
        param: str | None = None,
        status_code: int | None = None,
        response_body: Any | None = None,
        diagnostics: ErrorDiagnostics | None = None,
    ):
        super().__init__(
            message,
            status_code=status_code,
            response_body=response_body,
            diagnostics=diagnostics,
        )
        self.param = param

    def __str__(self) -> str:
        base = super().__str__()
        if self.param:
            return f"{base} (param: {self.param})"
        return base


class RateLimitError(AffinityError):
    """
    429 Too Many Requests - Rate limit exceeded.

    You've exceeded the API rate limit. Wait before retrying.
    Check the response headers for rate limit info:
    - X-Ratelimit-Limit-User-Reset: Seconds until per-minute limit resets
    - X-Ratelimit-Limit-Org-Reset: Seconds until monthly limit resets
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: int | None = None,
        status_code: int | None = None,
        response_body: Any | None = None,
        diagnostics: ErrorDiagnostics | None = None,
    ):
        super().__init__(
            message,
            status_code=status_code,
            response_body=response_body,
            diagnostics=diagnostics,
        )
        self.retry_after = retry_after


class ConflictError(AffinityError):
    """
    409 Conflict - Resource conflict.

    The request conflicts with the current state of the resource.
    For example:
    - Trying to create a person with an email that already exists
    - Concurrent modification conflicts
    """

    pass


class ServerError(AffinityError):
    """
    500/503 Internal Server Error - Server-side problem.

    Something went wrong on Affinity's servers.
    Try again later, and contact support if the problem persists.
    """

    pass


# =============================================================================
# Client-side Errors
# =============================================================================


class ConfigurationError(AffinityError):
    """
    Configuration error - missing or invalid client configuration.

    Check that you've provided:
    - A valid API key
    - Correct base URLs (if customizing)
    """

    pass


class TimeoutError(AffinityError):
    """
    Request timeout.

    The request took too long to complete.
    This could be due to:
    - Network issues
    - Large data sets
    - Server overload
    """

    def __init__(
        self,
        message: str,
        *,
        diagnostics: ErrorDiagnostics | None = None,
    ):
        super().__init__(message, diagnostics=diagnostics)


class NetworkError(AffinityError):
    """
    Network-level error.

    Failed to connect to the Affinity API.
    Check your internet connection and firewall settings.
    """

    def __init__(
        self,
        message: str,
        *,
        diagnostics: ErrorDiagnostics | None = None,
    ):
        super().__init__(message, diagnostics=diagnostics)


class PolicyError(AffinityError):
    """Raised when a client policy blocks an attempted operation."""

    pass


class WriteNotAllowedError(PolicyError):
    """Raised when a write operation is attempted while the write policy denies writes."""

    def __init__(self, message: str, *, method: str, url: str):
        super().__init__(message, diagnostics=ErrorDiagnostics(method=method, url=url))
        self.method = method
        self.url = url


# =============================================================================
# Pagination Errors
# =============================================================================


class TooManyResultsError(AffinityError):
    """
    Raised when ``.all()`` exceeds the limit.

    The default limit is 100,000 items (approximately 100MB for typical Person objects).
    This protects against OOM errors when paginating large datasets.

    To resolve:
    - Use ``.pages()`` for streaming iteration (memory-efficient)
    - Add filters to reduce result size
    - Pass ``limit=None`` to ``.all()`` if you're certain you need all results
    - Pass a custom ``limit=500_000`` if you need more than the default
    """

    pass


# =============================================================================
# URL Safety Errors
# =============================================================================


class UnsafeUrlError(AffinityError):
    """
    SDK blocked following a server-provided URL.

    Raised when SafeFollowUrl policy rejects a URL (scheme/host/userinfo/redirect).
    """

    def __init__(self, message: str, *, url: str | None = None):
        super().__init__(
            message,
            diagnostics=ErrorDiagnostics(url=url) if url else None,
        )
        self.url = url


# =============================================================================
# Business Logic Errors
# =============================================================================


class EntityNotFoundError(NotFoundError):
    """
    Specific entity not found.

    Provides type-safe context about which entity type was not found.
    """

    def __init__(
        self,
        entity_type: str,
        entity_id: int | str,
        **kwargs: Any,
    ):
        message = f"{entity_type} with ID {entity_id} not found"
        super().__init__(message, **kwargs)
        self.entity_type = entity_type
        self.entity_id = entity_id


class FieldNotFoundError(NotFoundError):
    """Field with the specified ID was not found."""

    pass


class ListNotFoundError(NotFoundError):
    """List with the specified ID was not found."""

    pass


class PersonNotFoundError(EntityNotFoundError):
    """Person with the specified ID was not found."""

    def __init__(self, person_id: int, **kwargs: Any):
        super().__init__("Person", person_id, **kwargs)


class CompanyNotFoundError(EntityNotFoundError):
    """Company with the specified ID was not found."""

    def __init__(self, company_id: int, **kwargs: Any):
        super().__init__("Company", company_id, **kwargs)


class OpportunityNotFoundError(EntityNotFoundError):
    """Opportunity with the specified ID was not found."""

    def __init__(self, opportunity_id: int, **kwargs: Any):
        super().__init__("Opportunity", opportunity_id, **kwargs)


# =============================================================================
# Merged Entity Errors
# =============================================================================

# Pattern: "<source_id> no longer exists as it has been merged into <target_id>"
_MERGED_PATTERN = re.compile(r"(\d+)\s+no longer exists as it has been merged into\s+(\d+)")


class MergedEntityError(ValidationError):
    """
    422 error indicating an entity was merged into another.

    When accessing a company or person that has been merged, the Affinity API
    returns a 422 with a message like:
    "292479388 no longer exists as it has been merged into 301128758"

    This exception provides structured access to both IDs.

    Attributes:
        source_id: The ID of the entity that was merged away (no longer exists).
        target_id: The ID of the entity it was merged into (the surviving entity).
        entity_type: The type of entity ("Company", "Person", or None if unknown).
    """

    def __init__(
        self,
        message: str,
        *,
        source_id: int,
        target_id: int,
        entity_type: str | None = None,
        param: str | None = None,
        status_code: int | None = None,
        response_body: Any | None = None,
        diagnostics: ErrorDiagnostics | None = None,
    ):
        super().__init__(
            message,
            param=param,
            status_code=status_code,
            response_body=response_body,
            diagnostics=diagnostics,
        )
        self.source_id = source_id
        self.target_id = target_id
        self.entity_type = entity_type

    @staticmethod
    def match(message: str) -> re.Match[str] | None:
        """Check if an error message matches the merged entity pattern."""
        return _MERGED_PATTERN.search(message)


class CompanyMergedError(MergedEntityError):
    """A company was merged into another company."""

    def __init__(
        self,
        message: str,
        *,
        source_id: int,
        target_id: int,
        **kwargs: Any,
    ):
        super().__init__(
            message,
            source_id=source_id,
            target_id=target_id,
            entity_type="Company",
            **kwargs,
        )


class PersonMergedError(MergedEntityError):
    """A person was merged into another person."""

    def __init__(
        self,
        message: str,
        *,
        source_id: int,
        target_id: int,
        **kwargs: Any,
    ):
        super().__init__(
            message,
            source_id=source_id,
            target_id=target_id,
            entity_type="Person",
            **kwargs,
        )


# =============================================================================
# API Version Errors
# =============================================================================


class UnsupportedOperationError(AffinityError):
    """
    Operation not supported by the current API version.

    Some operations are only available in V1 or V2.
    """

    pass


class BetaEndpointDisabledError(UnsupportedOperationError):
    """Attempted to call a beta endpoint without opt-in."""

    pass


class EnrichedFieldNotWritableError(UnsupportedOperationError):
    """
    Attempted to write to a V2 enriched field that has no V1 numeric twin.

    V1 `/field-values` is the only write endpoint for entity-global field values.
    V2 enriched fields (e.g. ``affinity-data-current-organization``, ``companies``,
    and relationship-intelligence fields like ``last-contact``) are returned by V2
    reads but have no writable V1 counterpart.
    """

    def __init__(self, field_id: str, *, reason: str | None = None) -> None:
        self.field_id = field_id
        self.reason = reason
        msg = f"Enriched field '{field_id}' is not writable via API"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class VersionCompatibilityError(AffinityError):
    """
    Response shape mismatch suggests API version incompatibility.

    TR-015: Raised when the SDK detects response-shape mismatches that
    appear version-related. This typically means the API key's configured
    v2 Default API Version differs from what the SDK expects.

    Guidance:
    1. Check your API key's v2 Default API Version in the Affinity dashboard
    2. Ensure it matches the expected_v2_version configured in the SDK
    3. See: https://developer.affinity.co/#section/Getting-Started/Versioning
    """

    def __init__(
        self,
        message: str,
        *,
        expected_version: str | None = None,
        parsing_error: str | None = None,
        status_code: int | None = None,
        response_body: Any | None = None,
        diagnostics: ErrorDiagnostics | None = None,
    ):
        super().__init__(
            message,
            status_code=status_code,
            response_body=response_body,
            diagnostics=diagnostics,
        )
        self.expected_version = expected_version
        self.parsing_error = parsing_error

    def __str__(self) -> str:
        base = super().__str__()
        hints = []
        if self.expected_version:
            hints.append(f"expected_v2_version={self.expected_version}")
        if self.parsing_error:
            hints.append(f"parsing_error={self.parsing_error}")
        if hints:
            base = f"{base} ({', '.join(hints)})"
        return base


class DeprecationWarning(AffinityError):
    """
    Feature is deprecated and may be removed.
    """

    pass


# =============================================================================
# Error Factory
# =============================================================================


def error_from_response(
    status_code: int,
    response_body: Any,
    *,
    retry_after: int | None = None,
    diagnostics: ErrorDiagnostics | None = None,
) -> AffinityError:
    """
    Create the appropriate exception from an API error response.

    Args:
        status_code: HTTP status code
        response_body: Parsed response body (usually dict with 'errors')
        retry_after: Retry-After header value for rate limits

    Returns:
        Appropriate AffinityError subclass
    """
    # Try to extract message from response
    message = "Unknown error"
    param = None

    extracted = False
    if isinstance(response_body, dict):
        errors = response_body.get("errors")
        if isinstance(errors, list) and errors:
            for item in errors:
                if isinstance(item, dict):
                    msg = item.get("message")
                    if isinstance(msg, str) and msg.strip():
                        message = msg.strip()
                        p = item.get("param")
                        if isinstance(p, str) and p.strip():
                            param = p
                        extracted = True
                        break
                elif isinstance(item, str) and item.strip():
                    message = item.strip()
                    extracted = True
                    break

        if not extracted:
            top_message = response_body.get("message")
            if isinstance(top_message, str) and top_message.strip():
                message = top_message.strip()
                extracted = True
            else:
                detail = response_body.get("detail")
                if isinstance(detail, str) and detail.strip():
                    message = detail.strip()
                    extracted = True
                else:
                    error_obj = response_body.get("error")
                    if isinstance(error_obj, dict):
                        nested_message = error_obj.get("message")
                        if isinstance(nested_message, str) and nested_message.strip():
                            message = nested_message.strip()
                            extracted = True
                    elif isinstance(error_obj, str) and error_obj.strip():
                        message = error_obj.strip()
                        extracted = True

    if not extracted and isinstance(response_body, list) and response_body:
        first = response_body[0]
        if isinstance(first, dict):
            msg = first.get("message") or first.get("error") or first.get("detail")
            if isinstance(msg, str) and msg.strip():
                message = msg.strip()
                extracted = True
        elif isinstance(first, str) and first.strip():
            message = first.strip()
            extracted = True

    if (
        message == "Unknown error"
        and diagnostics is not None
        and isinstance(diagnostics.response_body_snippet, str)
    ):
        snippet = diagnostics.response_body_snippet.strip()
        if snippet and snippet not in {"{}", "[]"}:
            message = snippet

    # Map status codes to exceptions
    error_mapping: dict[int, type[AffinityError]] = {
        400: ValidationError,
        401: AuthenticationError,
        403: AuthorizationError,
        404: NotFoundError,
        409: ConflictError,
        422: ValidationError,
        429: RateLimitError,
        500: ServerError,
        502: ServerError,
        503: ServerError,
        504: ServerError,
    }

    error_class = error_mapping.get(status_code, AffinityError)

    # Special handling for ValidationError with param
    if error_class is ValidationError:
        # Check if this is a merged entity error (422 with merge message)
        merge_match = MergedEntityError.match(message)
        if merge_match is not None:
            source_id = int(merge_match.group(1))
            target_id = int(merge_match.group(2))
            # Detect entity type from URL in diagnostics
            entity_type: str | None = None
            if diagnostics is not None and diagnostics.url is not None:
                url = diagnostics.url
                if "/companies/" in url or "/organizations/" in url:
                    entity_type = "Company"
                elif "/persons/" in url or "/people/" in url:
                    entity_type = "Person"

            if entity_type == "Company":
                cls: type[MergedEntityError] = CompanyMergedError
            elif entity_type == "Person":
                cls = PersonMergedError
            else:
                cls = MergedEntityError

            return cls(
                message,
                source_id=source_id,
                target_id=target_id,
                param=param,
                status_code=status_code,
                response_body=response_body,
                diagnostics=diagnostics,
            )

        return ValidationError(
            message,
            param=param,
            status_code=status_code,
            response_body=response_body,
            diagnostics=diagnostics,
        )

    # Special handling for RateLimitError with retry_after
    if error_class is RateLimitError:
        return RateLimitError(
            message,
            retry_after=retry_after,
            status_code=status_code,
            response_body=response_body,
            diagnostics=diagnostics,
        )

    return error_class(
        message,
        status_code=status_code,
        response_body=response_body,
        diagnostics=diagnostics,
    )


# =============================================================================
# Webhook parsing errors (inbound webhook helpers)
# =============================================================================


class WebhookParseError(AffinityError):
    """Base error for inbound webhook parsing/validation failures."""

    pass


class WebhookInvalidJsonError(WebhookParseError):
    """Raised when a webhook payload cannot be decoded as JSON."""

    pass


class WebhookInvalidPayloadError(WebhookParseError):
    """Raised when a decoded webhook payload is not in the expected envelope shape."""

    pass


class WebhookMissingKeyError(WebhookParseError):
    """Raised when a webhook payload is missing a required key."""

    def __init__(self, message: str, *, key: str):
        super().__init__(message)
        self.key = key


class WebhookInvalidSentAtError(WebhookParseError):
    """Raised when a webhook `sent_at` field is missing or invalid."""

    pass


# =============================================================================
# Filter Parsing Errors
# =============================================================================


class FilterParseError(ValueError):
    """
    Raised when a filter expression cannot be parsed.

    Common causes:
    - Multi-word values not quoted: Status=Intro Meeting
    - Invalid operators
    - Malformed expressions

    Example fix:
        # Wrong: --filter 'Status=Intro Meeting'
        # Right: --filter 'Status="Intro Meeting"'
    """

    pass
