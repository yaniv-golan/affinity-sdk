"""
HTTP client implementation for the Affinity API.

Handles:
- Authentication
- Rate limiting with automatic retries
- Request/response logging
- V1/V2 API routing
- Optional response caching
- Request/response hooks (DX-008)
"""

from __future__ import annotations

import asyncio
import base64
import email.utils
import hashlib
import inspect
import json
import logging
import math
import re
import sys
import threading
import time
import uuid
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, TypeAlias, TypeVar, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..api_versions import (
    AFFINITY_API_VERSION_HEADER,
    KNOWN_AFFINITY_API_VERSIONS,
    normalize_affinity_api_version,
)
from ..downloads import (
    AsyncDownloadedFile,
    DownloadedFile,
    _download_info_from_headers,
)
from ..exceptions import (
    AffinityError,
    ConfigurationError,
    ErrorDiagnostics,
    NetworkError,
    RateLimitError,
    TimeoutError,
    UnsafeUrlError,
    UnsupportedApiVersionError,
    ValidationError,
    VersionCompatibilityError,
    WriteNotAllowedError,
    error_from_response,
)
from ..hooks import (
    AnyEventHook,
    ErrorHook,
    ErrorInfo,
    HookEvent,
    RedirectFollowed,
    RequestFailed,
    RequestHook,
    RequestInfo,
    RequestRetrying,
    RequestStarted,
    RequestSucceeded,
    ResponseHeadersReceived,
    ResponseHook,
    ResponseInfo,
    StreamAborted,
    StreamCompleted,
    StreamFailed,
)
from ..models.types import V1_BASE_URL, V2_BASE_URL
from ..policies import ExternalHookPolicy, Policies, WritePolicy
from ..progress import ProgressCallback
from .pipeline import (
    AsyncMiddleware,
    Middleware,
    RequestContext,
    SDKBaseResponse,
    SDKRawResponse,
    SDKRawStreamResponse,
    SDKRequest,
    SDKResponse,
    compose,
    compose_async,
)

logger = logging.getLogger("affinity_sdk")

RepeatableQueryParam: TypeAlias = Literal["fieldIds", "fieldTypes", "ids", "types"]
REPEATABLE_QUERY_PARAMS: frozenset[str] = frozenset({"fieldIds", "fieldTypes", "ids", "types"})

_RETRYABLE_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})
_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MAX_RETRY_DELAY_SECONDS: float = 60.0
_MAX_DOWNLOAD_REDIRECTS: int = 10

_DEFAULT_HEADERS: dict[str, str] = {"Accept": "application/json"}

T = TypeVar("T")
R_resp = TypeVar("R_resp", bound=SDKBaseResponse)


def _to_wire_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _encode_query_params(
    params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
) -> list[tuple[str, str]] | None:
    """
    Convert params into deterministic ordered key/value pairs for `httpx`.

    - Repeatable params are encoded as repeated keys (e.g., fieldIds=a&fieldIds=b).
    - Repeatable values are de-duplicated while preserving caller order.
    - Non-repeatable params are emitted in sorted-key order for determinism.
    """
    if params is None:
        return None

    if isinstance(params, Mapping):
        ordered: list[tuple[str, str]] = []
        for key in sorted(params.keys()):
            value = params[key]
            if value is None:
                continue

            if key in REPEATABLE_QUERY_PARAMS:
                raw_values: Sequence[Any]
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                    raw_values = value
                else:
                    raw_values = [value]

                seen: set[str] = set()
                for item in raw_values:
                    wire = _to_wire_value(item)
                    if wire in seen:
                        continue
                    ordered.append((key, wire))
                    seen.add(wire)
            else:
                ordered.append((key, _to_wire_value(value)))
        return ordered

    return [(key, _to_wire_value(value)) for key, value in params]


def _freeze_v1_query_signature(
    params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
) -> list[tuple[str, str]]:
    """
    Freeze the canonical v1 query signature for token pagination.

    The returned sequence MUST NOT include the v1 `page_token` param so that the
    signature can be reused verbatim across pages (TR-017/TR-017a).
    """
    encoded = _encode_query_params(params) or []
    return [(key, value) for (key, value) in encoded if key != "page_token"]


def _compute_backoff_seconds(attempt: int, *, base: float) -> float:
    # "Full jitter": random(0, min(cap, base * 2^attempt))
    max_delay = float(min(_MAX_RETRY_DELAY_SECONDS, base * (2**attempt)))
    jitter = float((time.time_ns() % 1_000_000) / 1_000_000.0)
    return jitter * max_delay


def _throttle_jitter(delay: float) -> float:
    if delay <= 0:
        return 0.0
    cap = float(min(1.0, delay * 0.1))
    jitter = float((time.time_ns() % 1_000_000) / 1_000_000.0)
    return jitter * cap


@dataclass(frozen=True, slots=True)
class _RetryOutcome:
    action: Literal["sleep", "break", "raise", "raise_wrapped"]
    wait_time: float | None = None
    last_error: Exception | None = None
    log_message: str | None = None
    wrapped_error: Exception | None = None


def _retry_outcome(
    *,
    method: str,
    attempt: int,
    max_retries: int,
    retry_delay: float,
    error: Exception,
) -> _RetryOutcome:
    """
    Decide whether to retry after an exception.

    The caller is responsible for raising via `raise` (to preserve tracebacks) when
    outcome.action == "raise", and for chaining `from error` when
    outcome.action == "raise_wrapped".
    """
    if isinstance(error, RateLimitError):
        if method not in _RETRYABLE_METHODS:
            return _RetryOutcome(action="raise")
        if attempt >= max_retries:
            return _RetryOutcome(action="break", last_error=error)
        wait_time = (
            float(error.retry_after)
            if error.retry_after is not None
            else _compute_backoff_seconds(attempt, base=retry_delay)
        )
        return _RetryOutcome(
            action="sleep",
            wait_time=wait_time,
            last_error=error,
            log_message=f"Rate limited, waiting {wait_time}s (attempt {attempt + 1})",
        )

    if isinstance(error, AffinityError):
        status = error.status_code
        if method not in _RETRYABLE_METHODS or status is None or status < 500 or status >= 600:
            return _RetryOutcome(action="raise")
        if attempt >= max_retries:
            return _RetryOutcome(action="break", last_error=error)
        wait_time = _compute_backoff_seconds(attempt, base=retry_delay)
        return _RetryOutcome(
            action="sleep",
            wait_time=wait_time,
            last_error=error,
            log_message=f"Server error {status}, waiting {wait_time}s (attempt {attempt + 1})",
        )

    if isinstance(error, httpx.TimeoutException):
        if method not in _RETRYABLE_METHODS:
            return _RetryOutcome(
                action="raise_wrapped",
                wrapped_error=TimeoutError(f"Request timed out: {error}"),
            )
        if attempt >= max_retries:
            timeout_error = TimeoutError(f"Request timed out: {error}")
            timeout_error.__cause__ = error
            return _RetryOutcome(action="break", last_error=timeout_error)
        wait_time = _compute_backoff_seconds(attempt, base=retry_delay)
        return _RetryOutcome(action="sleep", wait_time=wait_time, last_error=error)

    if isinstance(error, httpx.NetworkError):
        if method not in _RETRYABLE_METHODS:
            return _RetryOutcome(
                action="raise_wrapped",
                wrapped_error=NetworkError(f"Network error: {error}"),
            )
        if attempt >= max_retries:
            network_error = NetworkError(f"Network error: {error}")
            network_error.__cause__ = error
            return _RetryOutcome(action="break", last_error=network_error)
        wait_time = _compute_backoff_seconds(attempt, base=retry_delay)
        return _RetryOutcome(action="sleep", wait_time=wait_time, last_error=error)

    return _RetryOutcome(action="raise")


def _parse_retry_after(value: str) -> int | None:
    """
    Parse `Retry-After` header per RFC7231.

    Supports:
    - delta-seconds (e.g. "60")
    - HTTP-date (e.g. "Wed, 21 Oct 2015 07:28:00 GMT")
    """
    candidate = value.strip()
    if not candidate:
        return None

    if candidate.isdigit():
        return int(candidate)

    try:
        parsed = email.utils.parsedate_to_datetime(candidate)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = (parsed.astimezone(timezone.utc) - now).total_seconds()
    return max(0, math.ceil(delta))


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _host_port(url: str) -> tuple[str, int | None]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port
    if port is None:
        port = _default_port(parts.scheme)
    return (host, port)


def _safe_follow_url(
    url: str,
    *,
    v1_base_url: str,
    v2_base_url: str,
) -> tuple[str, bool]:
    """
    Validate and normalize a server-provided URL under SafeFollowUrl policy.

    Returns: (absolute_url_without_fragment, is_v1)
    """
    v1_base = v1_base_url.rstrip("/") + "/"
    v2_base = v2_base_url.rstrip("/") + "/"

    parsed = urlsplit(url)
    # Relative URL: resolve against v2 base by default (v2 pagination/task URLs)
    absolute = urljoin(v2_base, url) if not parsed.scheme and not parsed.netloc else url

    parts = urlsplit(absolute)
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("Refusing URL with userinfo", url=absolute)

    # Strip fragments (never sent, never used for routing decisions)
    absolute = urlunsplit(parts._replace(fragment=""))
    parts = urlsplit(absolute)

    v2_parts = urlsplit(v2_base)
    v1_parts = urlsplit(v1_base)

    v2_host, v2_port = _host_port(v2_base)
    v1_host, v1_port = _host_port(v1_base)
    host, port = _host_port(absolute)

    if host not in {v1_host, v2_host} or port not in {v1_port, v2_port}:
        raise UnsafeUrlError("Refusing URL with unexpected host", url=absolute)

    v2_prefix = (v2_parts.path or "").rstrip("/") + "/"
    is_v1 = not (parts.path or "").startswith(v2_prefix)
    expected_scheme = v1_parts.scheme if is_v1 else v2_parts.scheme
    if parts.scheme != expected_scheme:
        raise UnsafeUrlError("Refusing URL with unexpected scheme", url=absolute)

    return absolute, is_v1


def _redact_url(url: str, api_key: str) -> str:
    parts = urlsplit(url)
    query_params = []
    if parts.query:
        for pair in parts.query.split("&"):
            if "=" in pair:
                key, _value = pair.split("=", 1)
                lowered = key.lower()
                if any(token in lowered for token in ("key", "token", "authorization")):
                    query_params.append(f"{key}=[REDACTED]")
                else:
                    query_params.append(pair)
            else:
                query_params.append(pair)
    redacted = urlunsplit(
        parts._replace(
            netloc=parts.hostname or parts.netloc,
            query="&".join(query_params),
        )
    )
    return redacted.replace(api_key, "[REDACTED]")


def _select_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    allow = [
        "Retry-After",
        "Date",
        "X-Ratelimit-Limit-User",
        "X-Ratelimit-Limit-User-Remaining",
        "X-Ratelimit-Limit-User-Reset",
        "X-Ratelimit-Limit-Org",
        "X-Ratelimit-Limit-Org-Remaining",
        "X-Ratelimit-Limit-Org-Reset",
        "X-Request-Id",
        "Request-Id",
    ]
    selected: dict[str, str] = {}
    for name in allow:
        value = headers.get(name) or headers.get(name.lower())
        if value is not None:
            selected[name] = value
    return selected


def _extract_request_id(headers: dict[str, str]) -> str | None:
    for key in ("X-Request-Id", "Request-Id"):
        if key in headers:
            return headers[key]
    return None


def _diagnostic_request_params(
    params: Sequence[tuple[str, str]] | None,
) -> dict[str, Any] | None:
    if not params:
        return None
    out: dict[str, Any] = {}
    for k, v in params:
        if k in out:
            existing = out[k]
            if isinstance(existing, list):
                existing.append(v)
            else:
                out[k] = [existing, v]
        else:
            out[k] = v
    return out or None


def _redact_external_url(url: str) -> str:
    """
    Redact external URLs for logs/diagnostics.

    External download URLs are often signed; stripping the query avoids leaking tokens.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"']+")


def _safe_body_preview(content: bytes, *, api_key: str, external: bool) -> str:
    """
    Produce a small, safe body snippet for diagnostics/logging.

    - Internal: redact known-sensitive query parameters + API key.
    - External: scrub any URLs to remove query/fragment (signed URLs often embed secrets there).
    """
    text = content[:512].decode("utf-8", errors="replace")
    if not external:
        return _redact_url(text, api_key)[:512]

    def _scrub(match: re.Match[str]) -> str:
        try:
            return _redact_external_url(match.group(0))
        except Exception:
            return "[REDACTED_URL]"

    return _URL_IN_TEXT_RE.sub(_scrub, text)[:512]


def _sanitize_hook_url(
    url: str,
    *,
    api_key: str,
    external: bool,
    external_hook_policy: ExternalHookPolicy,
) -> str | None:
    if not external:
        return _redact_url(url, api_key)
    if external_hook_policy is ExternalHookPolicy.SUPPRESS:
        return None
    if external_hook_policy is ExternalHookPolicy.EMIT_UNSAFE:
        return url
    return _redact_external_url(url)


def _sanitize_hook_headers(headers: Sequence[tuple[str, str]]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers:
        if key.lower() == "authorization":
            continue
        sanitized[key] = value
    return sanitized


_CREDENTIAL_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        # Not a credential, but Affinity-specific: never forward it to another host.
        "x-affinity-api-version",
    }
)


def _strip_credential_headers(headers: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(k, v) for (k, v) in headers if k.lower() not in _CREDENTIAL_HEADER_NAMES]


def _extract_bytes_total(headers: Sequence[tuple[str, str]]) -> int | None:
    """
    Extract a safe `bytes_total` from headers.

    Rules:
    - If Transfer-Encoding: chunked is present => unknown
    - If Content-Encoding is present => unknown (httpx may decode bytes)
    - If Content-Length is missing/invalid/multiple conflicting => unknown
    """
    transfer_encodings: list[str] = []
    content_encodings: list[str] = []
    content_lengths: list[str] = []

    for key, value in headers:
        lowered = key.lower()
        if lowered == "transfer-encoding":
            transfer_encodings.append(value)
        elif lowered == "content-encoding":
            content_encodings.append(value)
        elif lowered == "content-length":
            content_lengths.append(value)

    if any("chunked" in v.lower() for v in transfer_encodings):
        return None
    if content_encodings:
        return None
    if not content_lengths:
        return None

    parsed: set[int] = set()
    for raw in content_lengths:
        raw = raw.strip()
        if not raw.isdigit():
            return None
        parsed.add(int(raw))
    if len(parsed) != 1:
        return None
    return next(iter(parsed))


class _HTTPXSyncStream:
    def __init__(
        self,
        *,
        context_manager: Any,
        response: httpx.Response,
        headers: list[tuple[str, str]],
        request_info: RequestInfo | None,
        client_request_id: str,
        external: bool,
        started_at: float,
        deadline_seconds: float | None,
        on_progress: ProgressCallback | None,
        emit_event: Callable[[HookEvent], Any] | None,
    ):
        self._cm = context_manager
        self._resp = response
        self._headers = headers
        self._bytes_total = _extract_bytes_total(headers)
        self._request_info = request_info
        self._client_request_id = client_request_id
        self._external = external
        self._started_at = started_at
        self._deadline_at = started_at + deadline_seconds if deadline_seconds is not None else None
        self._on_progress = on_progress
        self._emit_event = emit_event
        self._closed = False
        self._iterated = False

    def __enter__(self) -> _HTTPXSyncStream:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            self._resp.close()

    def iter_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        self._iterated = True
        bytes_read = 0
        completed = False
        aborted_reason: str | None = None
        raised: BaseException | None = None

        def check_deadline() -> None:
            if self._deadline_at is None:
                return
            if time.monotonic() >= self._deadline_at:
                raise TimeoutError("Download deadline exceeded")

        if self._on_progress:
            self._on_progress(0, self._bytes_total, phase="download")

        try:
            for chunk in self._resp.iter_bytes(chunk_size=chunk_size):
                check_deadline()
                bytes_read += len(chunk)
                if self._on_progress:
                    self._on_progress(bytes_read, self._bytes_total, phase="download")
                yield chunk
            completed = True
        except GeneratorExit:
            aborted_reason = "closed"
            raise
        except KeyboardInterrupt:
            aborted_reason = "keyboard_interrupt"
            raise
        except BaseException as exc:
            if isinstance(exc, httpx.TimeoutException):
                raised = TimeoutError(f"Request timed out: {exc}")
            elif isinstance(exc, httpx.NetworkError):
                raised = NetworkError(f"Network error: {exc}")
            else:
                raised = exc
            raise raised from exc
        finally:
            elapsed_ms = (time.monotonic() - self._started_at) * 1000
            if self._emit_event is not None and self._request_info is not None:
                if completed:
                    self._emit_event(
                        StreamCompleted(
                            client_request_id=self._client_request_id,
                            request=self._request_info,
                            bytes_read=bytes_read,
                            bytes_total=self._bytes_total,
                            elapsed_ms=elapsed_ms,
                            external=self._external,
                        )
                    )
                elif aborted_reason is not None:
                    self._emit_event(
                        StreamAborted(
                            client_request_id=self._client_request_id,
                            request=self._request_info,
                            reason=aborted_reason,
                            bytes_read=bytes_read,
                            bytes_total=self._bytes_total,
                            elapsed_ms=elapsed_ms,
                            external=self._external,
                        )
                    )
                elif raised is not None:
                    self._emit_event(
                        StreamFailed(
                            client_request_id=self._client_request_id,
                            request=self._request_info,
                            error=raised,
                            bytes_read=bytes_read,
                            bytes_total=self._bytes_total,
                            elapsed_ms=elapsed_ms,
                            external=self._external,
                        )
                    )
            self.close()


class _HTTPXAsyncStream:
    def __init__(
        self,
        *,
        context_manager: Any,
        response: httpx.Response,
        headers: list[tuple[str, str]],
        request_info: RequestInfo | None,
        client_request_id: str,
        external: bool,
        started_at: float,
        deadline_seconds: float | None,
        on_progress: ProgressCallback | None,
        emit_event: Callable[[HookEvent], Any] | None,
    ):
        self._cm = context_manager
        self._resp = response
        self._headers = headers
        self._bytes_total = _extract_bytes_total(headers)
        self._request_info = request_info
        self._client_request_id = client_request_id
        self._external = external
        self._started_at = started_at
        self._deadline_at = started_at + deadline_seconds if deadline_seconds is not None else None
        self._on_progress = on_progress
        self._emit_event = emit_event
        self._closed = False
        self._iterated = False

    async def __aenter__(self) -> _HTTPXAsyncStream:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cm.__aexit__(None, None, None)

    def aiter_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            self._iterated = True
            bytes_read = 0
            completed = False
            aborted_reason: str | None = None
            raised: BaseException | None = None

            def check_deadline() -> None:
                if self._deadline_at is None:
                    return
                if time.monotonic() >= self._deadline_at:
                    raise TimeoutError("Download deadline exceeded")

            if self._on_progress:
                self._on_progress(0, self._bytes_total, phase="download")

            try:
                async for chunk in self._resp.aiter_bytes(chunk_size=chunk_size):
                    check_deadline()
                    bytes_read += len(chunk)
                    if self._on_progress:
                        self._on_progress(bytes_read, self._bytes_total, phase="download")
                    yield chunk
                completed = True
            except asyncio.CancelledError:
                aborted_reason = "cancelled"
                raise
            except KeyboardInterrupt:
                aborted_reason = "keyboard_interrupt"
                raise
            except BaseException as exc:
                if isinstance(exc, httpx.TimeoutException):
                    raised = TimeoutError(f"Request timed out: {exc}")
                elif isinstance(exc, httpx.NetworkError):
                    raised = NetworkError(f"Network error: {exc}")
                else:
                    raised = exc
                raise raised from exc
            finally:
                elapsed_ms = (time.monotonic() - self._started_at) * 1000
                if self._emit_event is not None and self._request_info is not None:
                    if completed:
                        maybe = self._emit_event(
                            StreamCompleted(
                                client_request_id=self._client_request_id,
                                request=self._request_info,
                                bytes_read=bytes_read,
                                bytes_total=self._bytes_total,
                                elapsed_ms=elapsed_ms,
                                external=self._external,
                            )
                        )
                        if inspect.isawaitable(maybe):
                            await cast(Awaitable[None], maybe)
                    elif aborted_reason is not None:
                        maybe = self._emit_event(
                            StreamAborted(
                                client_request_id=self._client_request_id,
                                request=self._request_info,
                                reason=aborted_reason,
                                bytes_read=bytes_read,
                                bytes_total=self._bytes_total,
                                elapsed_ms=elapsed_ms,
                                external=self._external,
                            )
                        )
                        if inspect.isawaitable(maybe):
                            await cast(Awaitable[None], maybe)
                    elif raised is not None:
                        maybe = self._emit_event(
                            StreamFailed(
                                client_request_id=self._client_request_id,
                                request=self._request_info,
                                error=raised,
                                bytes_read=bytes_read,
                                bytes_total=self._bytes_total,
                                elapsed_ms=elapsed_ms,
                                external=self._external,
                            )
                        )
                        if inspect.isawaitable(maybe):
                            await cast(Awaitable[None], maybe)
                await asyncio.shield(self.aclose())

        return _gen()


# =============================================================================
# Rate Limit Tracking
# =============================================================================


@dataclass
class RateLimitState:
    """Tracks rate limit status from response headers."""

    user_limit: int | None = None
    user_remaining: int | None = None
    user_reset_seconds: int | None = None
    org_limit: int | None = None
    org_remaining: int | None = None
    org_reset_seconds: int | None = None
    last_updated: float | None = None
    last_request_id: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def update_from_headers(self, headers: Mapping[str, str]) -> None:
        """Update state from response headers."""

        # Handle both uppercase (current) and lowercase (future) headers.
        def get_int(name: str) -> int | None:
            value = headers.get(name) or headers.get(name.lower())
            return int(value) if value else None

        request_id = _extract_request_id(_select_response_headers(headers))
        observed_any = False

        user_limit = get_int("X-Ratelimit-Limit-User")
        user_remaining = get_int("X-Ratelimit-Limit-User-Remaining")
        user_reset = get_int("X-Ratelimit-Limit-User-Reset")
        org_limit = get_int("X-Ratelimit-Limit-Org")
        org_remaining = get_int("X-Ratelimit-Limit-Org-Remaining")
        org_reset = get_int("X-Ratelimit-Limit-Org-Reset")

        with self._lock:
            if request_id is not None:
                self.last_request_id = request_id

            # Only update timestamps when we actually observed rate limit headers.
            for v in (user_limit, user_remaining, user_reset, org_limit, org_remaining, org_reset):
                if v is not None:
                    observed_any = True
                    break

            if not observed_any:
                return

            self.last_updated = time.time()

            if user_limit is not None:
                self.user_limit = user_limit
            if user_remaining is not None:
                self.user_remaining = user_remaining
            if user_reset is not None:
                self.user_reset_seconds = user_reset
            if org_limit is not None:
                self.org_limit = org_limit
            if org_remaining is not None:
                self.org_remaining = org_remaining
            if org_reset is not None:
                self.org_reset_seconds = org_reset

    @property
    def should_throttle(self) -> bool:
        """Whether we should slow down requests."""
        return (self.user_remaining is not None and self.user_remaining < 50) or (
            self.org_remaining is not None and self.org_remaining < 1000
        )

    @property
    def seconds_until_user_reset(self) -> float:
        """Seconds until per-minute limit resets."""
        if self.user_reset_seconds is None or self.last_updated is None:
            return 0.0
        elapsed = time.time() - self.last_updated
        return max(0.0, float(self.user_reset_seconds) - elapsed)

    def snapshot(self) -> dict[str, Any]:
        """Return a coherent snapshot of the tracked state."""
        with self._lock:
            return {
                "user_limit": self.user_limit,
                "user_remaining": self.user_remaining,
                "user_reset_seconds": self.user_reset_seconds,
                "org_limit": self.org_limit,
                "org_remaining": self.org_remaining,
                "org_reset_seconds": self.org_reset_seconds,
                "last_updated": self.last_updated,
                "last_request_id": self.last_request_id,
            }


class _RateLimitGateSync:
    def __init__(self) -> None:
        self._blocked_until: float = 0.0
        self._lock = threading.Lock()

    def note(self, wait_seconds: float) -> None:
        if wait_seconds <= 0:
            return
        now = time.monotonic()
        with self._lock:
            self._blocked_until = max(self._blocked_until, now + wait_seconds)

    def delay(self) -> float:
        now = time.monotonic()
        with self._lock:
            return max(0.0, self._blocked_until - now)


class _RateLimitGateAsync:
    def __init__(self) -> None:
        self._blocked_until: float = 0.0
        self._lock = asyncio.Lock()

    async def note(self, wait_seconds: float) -> None:
        if wait_seconds <= 0:
            return
        now = time.monotonic()
        async with self._lock:
            self._blocked_until = max(self._blocked_until, now + wait_seconds)

    async def delay(self) -> float:
        now = time.monotonic()
        async with self._lock:
            return max(0.0, self._blocked_until - now)


# =============================================================================
# Simple TTL Cache
# =============================================================================


@dataclass
class CacheEntry:
    """Single cache entry with TTL."""

    value: dict[str, Any]
    expires_at: float
    # V2 API version Affinity echoed for the cached response (X-Affinity-Api-Version).
    api_version: str | None = None


class SimpleCache:
    """
    Simple in-memory cache with TTL.

    Used for caching field metadata and other rarely-changing data.
    Thread-safe via an internal lock.
    """

    def __init__(self, default_ttl: float = 300.0):
        self._cache: dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        """Get value if not expired."""
        entry = self.get_entry(key)
        return entry.value if entry is not None else None

    def get_entry(self, key: str) -> CacheEntry | None:
        """Get the full entry (value + echoed API version) if not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() > entry.expires_at:
                del self._cache[key]
                return None
            return entry

    def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl: float | None = None,
        *,
        api_version: str | None = None,
    ) -> None:
        """Set value with TTL (and the API version Affinity echoed for it, if known)."""
        expires_at = time.time() + (ttl or self._default_ttl)
        with self._lock:
            self._cache[key] = CacheEntry(
                value=value, expires_at=expires_at, api_version=api_version
            )

    def delete(self, key: str) -> None:
        """Delete a cache entry."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def invalidate_prefix(self, prefix: str) -> None:
        """Invalidate all entries with the given prefix."""
        with self._lock:
            keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
            for key in keys_to_delete:
                del self._cache[key]


# =============================================================================
# HTTP Client Configuration
# =============================================================================


@dataclass
class ClientConfig:
    """Configuration for the HTTP client."""

    api_key: str
    v1_base_url: str = V1_BASE_URL
    v2_base_url: str = V2_BASE_URL
    http2: bool = False
    v1_auth_mode: Literal["bearer", "basic"] = "bearer"
    timeout: httpx.Timeout | float = field(
        default_factory=lambda: httpx.Timeout(
            30.0,
            connect=10.0,
            read=30.0,
            write=30.0,
            pool=10.0,
        )
    )
    limits: httpx.Limits = field(
        default_factory=lambda: httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
            keepalive_expiry=30.0,
        )
    )
    transport: httpx.BaseTransport | None = None
    async_transport: httpx.AsyncBaseTransport | None = None
    max_retries: int = 3
    retry_delay: float = 1.0
    enable_cache: bool = False
    cache_ttl: float = 300.0
    log_requests: bool = False
    enable_beta_endpoints: bool = False
    # If True, allows following `http://` redirects when downloading files (not recommended).
    allow_insecure_download_redirects: bool = False
    # Request/response hooks (DX-008)
    on_request: RequestHook | None = None
    on_response: ResponseHook | None = None
    on_error: ErrorHook | None = None
    on_event: AnyEventHook | None = None
    hook_error_policy: Literal["swallow", "raise"] = "swallow"
    # TR-015: Expected v2 API version for diagnostics only (deprecated; use
    # `affinity_api_version` to pin, and `affinity_api_versions_seen` to inspect).
    expected_v2_version: str | None = None
    policies: Policies = field(default_factory=Policies)
    # Affinity V2 API version sent as `X-Affinity-Api-Version` on every V2 request
    # (e.g. "2026-09-17" or "current"). None/"auto"/"key-default" -> no header, so the
    # API key's default version applies. Never sent to V1 or to non-Affinity hosts.
    affinity_api_version: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.timeout, (int, float)):
            self.timeout = httpx.Timeout(float(self.timeout))
        version, warning = normalize_affinity_api_version(self.affinity_api_version)
        self.affinity_api_version = version
        if warning:
            warnings.warn(warning, UserWarning, stacklevel=3)
        if self.expected_v2_version is not None:
            warnings.warn(
                "expected_v2_version is deprecated: it only labels diagnostics. Use "
                "affinity_api_version=... to pin the V2 API version, and "
                "affinity_api_versions_seen to see which version answered.",
                DeprecationWarning,
                stacklevel=3,
            )


def _cache_key_suffix(
    v1_base_url: str,
    v2_base_url: str,
    api_key: str,
    affinity_api_version: str | None = None,
) -> str:
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    version = affinity_api_version or "default"
    return f"|v1={v1_base_url}|v2={v2_base_url}|tenant={digest}|apiver={version}"


def _header_value(headers: Sequence[tuple[str, str]], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return None


_UNSET: Any = object()


class _ApiVersionTracker:
    """Per-client V2 API version handling: header injection and echo tracking (thread-safe)."""

    def __init__(self, config: ClientConfig):
        self._config = config
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._last: str | None = None
        self._mismatch_logged = False

    def requested(self, req: SDKRequest) -> str | None:
        """The header value to send for this request (None -> no header)."""
        override = req.context.get("affinity_api_version", _UNSET)
        if override is not _UNSET:
            return cast(str | None, override)
        return self._config.affinity_api_version

    def inject(self, req: SDKRequest, headers: list[tuple[str, str]]) -> str | None:
        """Add the version header to a V2 request's headers; returns the value sent."""
        if req.api_version != "v2" or req.context.get("external", False):
            return None
        version = self.requested(req)
        if version is None:
            return None
        headers[:] = [(k, v) for (k, v) in headers if k.lower() != "x-affinity-api-version"]
        headers.append((AFFINITY_API_VERSION_HEADER, version))
        return version

    def observe(self, req: SDKRequest, resp: SDKBaseResponse) -> None:
        """Record the version echoed by a V2 response (cache hits replay the stored echo)."""
        if req.api_version != "v2" or resp.context.get("external", False):
            return
        echoed = _header_value(resp.headers, AFFINITY_API_VERSION_HEADER)
        if not echoed:
            return
        resp.context["affinity_api_version"] = echoed
        if "affinity_api_version" in req.context:
            return  # per-request override (e.g. key-default probe): not the client's version
        expected = self._config.expected_v2_version
        with self._lock:
            self._seen.add(echoed)
            self._last = echoed
            log_mismatch = bool(expected) and echoed != expected and not self._mismatch_logged
            if log_mismatch:
                self._mismatch_logged = True
        if log_mismatch:
            logger.warning(
                "Affinity answered with V2 API version %s but expected_v2_version=%s",
                echoed,
                expected,
            )

    def map_error(self, exc: ValidationError, version: str | None) -> AffinityError:
        """Turn a 400 on the version header into an UnsupportedApiVersionError."""
        param = (exc.param or "").strip().lower()
        if param != "x-affinity-api-version":
            return exc
        shown = version if version is not None else "(none)"
        return UnsupportedApiVersionError(
            f"Affinity rejected API version {shown!r} (X-Affinity-Api-Version): "
            f"{exc.message} Use {', '.join(KNOWN_AFFINITY_API_VERSIONS)} or 'current', or unset "
            "affinity_api_version (CLI: --api-version / AFFINITY_API_VERSION / profile "
            "api_version) to use the API key's default version.",
            requested_version=version,
            status_code=exc.status_code,
            response_body=exc.response_body,
            diagnostics=exc.diagnostics,
        )

    def seen(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._seen)

    def last(self) -> str | None:
        with self._lock:
            return self._last

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()
            self._last = None


# =============================================================================
# Synchronous HTTP Client
# =============================================================================


class HTTPClient:
    """
    Synchronous HTTP client for Affinity API.

    Handles authentication, rate limiting, retries, and caching.
    """

    def __init__(self, config: ClientConfig):
        self._config = config
        self._rate_limit = RateLimitState()
        self._rate_limit_gate = _RateLimitGateSync()
        self._cache = SimpleCache(config.cache_ttl) if config.enable_cache else None
        self._cache_suffix = _cache_key_suffix(
            self._config.v1_base_url,
            self._config.v2_base_url,
            self._config.api_key,
            self._config.affinity_api_version,
        )
        self._api_versions = _ApiVersionTracker(config)

        # Configure httpx client (auth is applied per-request)
        self._client = httpx.Client(
            http2=config.http2,
            timeout=config.timeout,
            limits=config.limits,
            transport=config.transport,
            headers=dict(_DEFAULT_HEADERS),
        )
        self._pipeline = self._build_pipeline()
        self._raw_buffered_pipeline = self._build_raw_buffered_pipeline()
        self._raw_stream_pipeline = self._build_raw_stream_pipeline()

    def _request_id_middleware(
        self,
    ) -> Middleware[SDKBaseResponse]:
        def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], SDKBaseResponse]
        ) -> SDKBaseResponse:
            context: RequestContext = cast(RequestContext, dict(req.context))
            client_request_id = context.get("client_request_id")
            if not isinstance(client_request_id, str) or not client_request_id:
                try:
                    client_request_id = uuid.uuid4().hex
                except Exception:
                    client_request_id = "unknown"
                context["client_request_id"] = client_request_id

            headers = list(req.headers)
            if not any(k.lower() == "x-client-request-id" for (k, _v) in headers):
                headers.append(("X-Client-Request-Id", client_request_id))

            return next(replace(req, headers=headers, context=context))

        return middleware

    def _hooks_middleware(
        self,
    ) -> Middleware[SDKBaseResponse]:
        config = self._config

        def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], SDKBaseResponse]
        ) -> SDKBaseResponse:
            context: RequestContext = cast(RequestContext, dict(req.context))
            started_at = context.get("started_at")
            if started_at is None:
                started_at = time.monotonic()
                context["started_at"] = started_at

            client_request_id = context.get("client_request_id") or "unknown"

            def emit_event(event: HookEvent) -> None:
                if config.on_event is None:
                    return
                if (
                    getattr(event, "external", False)
                    and config.policies.external_hooks is ExternalHookPolicy.SUPPRESS
                ):
                    return
                try:
                    result = config.on_event(event)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise ConfigurationError(
                            "Sync clients require a synchronous `on_event` handler"
                        )
                except Exception:
                    if config.hook_error_policy == "raise":
                        raise
                    logger.warning(
                        "Hook error suppressed (hook_error_policy=swallow)", exc_info=True
                    )

            context["emit_event"] = emit_event

            external = bool(context.get("external", False))
            sanitized_url = _sanitize_hook_url(
                req.url,
                api_key=config.api_key,
                external=external,
                external_hook_policy=config.policies.external_hooks,
            )
            request_info = (
                RequestInfo(
                    method=req.method.upper(),
                    url=sanitized_url,
                    headers=_sanitize_hook_headers(req.headers),
                )
                if sanitized_url is not None
                else None
            )
            context["hook_request_info"] = request_info

            if request_info is not None:
                if config.on_request:
                    config.on_request(request_info)
                emit_event(
                    RequestStarted(
                        client_request_id=client_request_id,
                        request=request_info,
                        api_version=req.api_version if not external else "external",
                    )
                )

            try:
                resp = next(replace(req, context=context))
            except Exception as exc:
                elapsed_ms = (time.monotonic() - started_at) * 1000
                if request_info is not None:
                    emit_event(
                        RequestFailed(
                            client_request_id=client_request_id,
                            request=request_info,
                            error=exc,
                            elapsed_ms=elapsed_ms,
                            external=external,
                        )
                    )
                if config.on_error and request_info is not None:
                    config.on_error(
                        ErrorInfo(error=exc, elapsed_ms=elapsed_ms, request=request_info)
                    )
                raise

            elapsed_ms = (time.monotonic() - started_at) * 1000
            resp.context.setdefault("client_request_id", client_request_id)
            resp.context.setdefault("external", external)
            resp.context.setdefault("elapsed_seconds", elapsed_ms / 1000.0)

            if config.on_response and request_info is not None:
                config.on_response(
                    ResponseInfo(
                        status_code=resp.status_code,
                        headers=dict(resp.headers),
                        elapsed_ms=elapsed_ms,
                        cache_hit=bool(resp.context.get("cache_hit", False)),
                        request=request_info,
                    )
                )

            if request_info is not None:
                emit_event(
                    ResponseHeadersReceived(
                        client_request_id=client_request_id,
                        request=request_info,
                        status_code=resp.status_code,
                        headers=list(resp.headers),
                        elapsed_ms=elapsed_ms,
                        external=bool(resp.context.get("external", False)),
                        cache_hit=bool(resp.context.get("cache_hit", False)),
                        request_id=resp.context.get("request_id"),
                    )
                )

                if not isinstance(resp, SDKRawStreamResponse):
                    emit_event(
                        RequestSucceeded(
                            client_request_id=client_request_id,
                            request=request_info,
                            status_code=resp.status_code,
                            elapsed_ms=elapsed_ms,
                            external=bool(resp.context.get("external", False)),
                        )
                    )

            return resp

        return middleware

    def _retry_middleware(
        self,
    ) -> Middleware[SDKBaseResponse]:
        config = self._config

        def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], SDKBaseResponse]
        ) -> SDKBaseResponse:
            last_error: Exception | None = None
            for attempt in range(config.max_retries + 1):
                if attempt == 0:
                    throttle_delay = self._rate_limit_gate.delay()
                    if throttle_delay > 0:
                        time.sleep(throttle_delay + _throttle_jitter(throttle_delay))
                try:
                    resp = next(req)
                    resp.context["retry_count"] = attempt
                    return resp
                except (
                    RateLimitError,
                    AffinityError,
                    httpx.TimeoutException,
                    httpx.NetworkError,
                ) as e:
                    outcome = _retry_outcome(
                        method=req.method.upper(),
                        attempt=attempt,
                        max_retries=config.max_retries,
                        retry_delay=config.retry_delay,
                        error=e,
                    )
                    if isinstance(e, RateLimitError):
                        rate_limit_wait = (
                            float(e.retry_after) if e.retry_after is not None else outcome.wait_time
                        )
                        if rate_limit_wait is not None:
                            self._rate_limit_gate.note(rate_limit_wait)
                    if outcome.action == "raise":
                        raise
                    if outcome.action == "raise_wrapped":
                        assert outcome.wrapped_error is not None
                        raise outcome.wrapped_error from e

                    assert outcome.last_error is not None
                    last_error = outcome.last_error
                    if outcome.action == "break":
                        break

                    assert outcome.wait_time is not None
                    emit = req.context.get("emit_event")
                    request_info = cast(RequestInfo | None, req.context.get("hook_request_info"))
                    if emit is not None and request_info is not None:
                        emit(
                            RequestRetrying(
                                client_request_id=req.context.get("client_request_id") or "unknown",
                                request=request_info,
                                attempt=attempt + 1,
                                wait_seconds=outcome.wait_time,
                                reason=outcome.log_message or type(e).__name__,
                            )
                        )

                    if outcome.log_message:
                        logger.warning(outcome.log_message)
                    time.sleep(outcome.wait_time)

            if last_error:
                raise last_error
            raise AffinityError("Request failed after retries")

        return middleware

    def _auth_middleware(
        self,
    ) -> Middleware[SDKBaseResponse]:
        config = self._config

        def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], SDKBaseResponse]
        ) -> SDKBaseResponse:
            headers = [(k, v) for (k, v) in req.headers if k.lower() != "authorization"]
            if req.api_version == "v1" and config.v1_auth_mode == "basic":
                token = base64.b64encode(f":{config.api_key}".encode()).decode("ascii")
                headers.append(("Authorization", f"Basic {token}"))
            else:
                headers.append(("Authorization", f"Bearer {config.api_key}"))
            version = self._api_versions.inject(req, headers)
            try:
                resp = next(replace(req, headers=headers))
            except ValidationError as exc:
                mapped = self._api_versions.map_error(exc, version)
                if mapped is exc:
                    raise
                raise mapped from exc
            self._api_versions.observe(req, resp)
            return resp

        return middleware

    def _write_guard_middleware(
        self,
    ) -> Middleware[SDKBaseResponse]:
        config = self._config

        def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], SDKBaseResponse]
        ) -> SDKBaseResponse:
            if config.policies.write is WritePolicy.DENY and req.write_intent:
                method_upper = req.method.upper()
                raise WriteNotAllowedError(
                    f"Cannot {method_upper} while writes are disabled by policy",
                    method=method_upper,
                    url=_redact_url(req.url, config.api_key),
                )
            return next(req)

        return middleware

    def _build_pipeline(self) -> Callable[[SDKRequest], SDKResponse]:
        config = self._config

        def terminal(req: SDKRequest) -> SDKResponse:
            external = bool(req.context.get("external", False))
            if config.log_requests and not external:
                logger.debug(f"{req.method} {req.url}")

            request_kwargs: dict[str, Any] = {}
            if req.headers:
                request_kwargs["headers"] = req.headers
            if req.params is not None:
                request_kwargs["params"] = req.params
            if req.json is not None:
                request_kwargs["json"] = req.json
            if req.files is not None:
                request_kwargs["files"] = req.files
            if req.data is not None:
                request_kwargs["data"] = req.data

            timeout_seconds = req.context.get("timeout_seconds")
            if timeout_seconds is not None:
                request_kwargs["timeout"] = float(timeout_seconds)

            response = self._client.request(req.method, req.url, **request_kwargs)
            return SDKResponse(
                status_code=response.status_code,
                headers=list(response.headers.multi_items()),
                content=response.content,
                context={"external": external, "http_version": response.http_version},
            )

        def response_mapping(
            req: SDKRequest, next: Callable[[SDKRequest], SDKResponse]
        ) -> SDKResponse:
            resp = next(req)
            headers_map = dict(resp.headers)
            external = bool(resp.context.get("external", False))
            if not external:
                self._rate_limit.update_from_headers(headers_map)

            if req.context.get("safe_follow", False):
                location = headers_map.get("Location") or headers_map.get("location")
                if 300 <= resp.status_code < 400 and location:
                    raise UnsafeUrlError(
                        "Refusing to follow redirect for server-provided URL",
                        url=req.url,
                    )

            if resp.status_code >= 400:
                try:
                    body = json.loads(resp.content) if resp.content else {}
                except Exception:
                    body = {"message": resp.content.decode("utf-8", errors="replace")}

                retry_after = None
                if resp.status_code == 429:
                    header_value = headers_map.get("Retry-After") or headers_map.get("retry-after")
                    if header_value is not None:
                        retry_after = _parse_retry_after(header_value)

                selected_headers = _select_response_headers(headers_map)
                request_id = _extract_request_id(selected_headers)
                if request_id is not None:
                    resp.context["request_id"] = request_id

                diagnostics = ErrorDiagnostics(
                    method=req.method.upper(),
                    url=_redact_url(req.url, config.api_key),
                    request_params=_diagnostic_request_params(req.params),
                    api_version=req.api_version,
                    base_url=config.v1_base_url if req.api_version == "v1" else config.v2_base_url,
                    request_id=request_id,
                    http_version=resp.context.get("http_version"),
                    response_headers=selected_headers,
                    response_body_snippet=str(body)[:512].replace(config.api_key, "[REDACTED]"),
                )
                raise error_from_response(
                    resp.status_code,
                    body,
                    retry_after=retry_after,
                    diagnostics=diagnostics,
                )

            # Check for empty or whitespace-only content
            content_stripped = resp.content.strip() if resp.content else b""
            if resp.status_code == 204 or not content_stripped:
                resp.json = {}
                return resp

            try:
                payload = json.loads(content_stripped)
            except Exception as e:
                raise AffinityError("Expected JSON object/array response") from e

            if isinstance(payload, dict):
                resp.json = payload
                return resp
            if isinstance(payload, list):
                resp.json = {"data": payload}
                return resp
            raise AffinityError("Expected JSON object/array response")

        def cache_middleware(
            req: SDKRequest, next: Callable[[SDKRequest], SDKResponse]
        ) -> SDKResponse:
            cache_key = req.context.get("cache_key")
            if cache_key and self._cache:
                cached_entry = self._cache.get_entry(f"{cache_key}{self._cache_suffix}")
                if cached_entry is not None:
                    return SDKResponse(
                        status_code=200,
                        headers=(
                            [(AFFINITY_API_VERSION_HEADER, cached_entry.api_version)]
                            if cached_entry.api_version
                            else []
                        ),
                        content=b"",
                        json=cached_entry.value,
                        context={
                            "cache_hit": True,
                            "external": bool(req.context.get("external", False)),
                        },
                    )

            resp = next(req)
            if cache_key and self._cache and isinstance(resp.json, dict):
                self._cache.set(
                    f"{cache_key}{self._cache_suffix}",
                    cast(dict[str, Any], resp.json),
                    req.context.get("cache_ttl"),
                    api_version=_header_value(resp.headers, AFFINITY_API_VERSION_HEADER),
                )
            return resp

        middlewares: list[Middleware[SDKResponse]] = [
            cast(Middleware[SDKResponse], self._request_id_middleware()),
            cast(Middleware[SDKResponse], self._hooks_middleware()),
            cast(Middleware[SDKResponse], self._write_guard_middleware()),
            cast(Middleware[SDKResponse], self._retry_middleware()),
            cast(Middleware[SDKResponse], self._auth_middleware()),
            cache_middleware,
            response_mapping,
        ]
        return compose(middlewares, terminal)

    def _build_raw_buffered_pipeline(self) -> Callable[[SDKRequest], SDKRawResponse]:
        config = self._config
        internal_hosts = {
            urlsplit(config.v1_base_url).netloc,
            urlsplit(config.v2_base_url).netloc,
        }

        def terminal(req: SDKRequest) -> SDKRawResponse:
            external = bool(req.context.get("external", False))
            if config.log_requests and not external:
                logger.debug(f"{req.method} {req.url}")

            request_kwargs: dict[str, Any] = {"follow_redirects": False}
            if req.headers:
                request_kwargs["headers"] = req.headers
            if req.params is not None:
                request_kwargs["params"] = req.params
            if req.files is not None:
                request_kwargs["files"] = req.files
            if req.data is not None:
                request_kwargs["data"] = req.data
            if req.json is not None:
                request_kwargs["json"] = req.json

            timeout = req.context.get("timeout")
            if timeout is not None:
                request_kwargs["timeout"] = timeout
            timeout_seconds = req.context.get("timeout_seconds")
            if timeout_seconds is not None and timeout is None:
                request_kwargs["timeout"] = float(timeout_seconds)

            response = self._client.request(req.method, req.url, **request_kwargs)
            return SDKRawResponse(
                status_code=response.status_code,
                headers=list(response.headers.multi_items()),
                content=response.content,
                context={"external": external, "http_version": response.http_version},
            )

        def raw_response_mapping(
            req: SDKRequest, next: Callable[[SDKRequest], SDKRawResponse]
        ) -> SDKRawResponse:
            resp = next(req)
            headers_map = dict(resp.headers)
            external = bool(resp.context.get("external", False))
            if not external:
                self._rate_limit.update_from_headers(headers_map)

            if resp.status_code >= 400:
                try:
                    body: Any = json.loads(resp.content) if resp.content else {}
                except Exception:
                    body = {
                        "message": _safe_body_preview(
                            resp.content, api_key=config.api_key, external=external
                        )
                    }

                retry_after = None
                if resp.status_code == 429:
                    header_value = headers_map.get("Retry-After") or headers_map.get("retry-after")
                    if header_value is not None:
                        retry_after = _parse_retry_after(header_value)

                selected_headers = _select_response_headers(headers_map)
                request_id = _extract_request_id(selected_headers)
                if request_id is not None:
                    resp.context["request_id"] = request_id

                if external:
                    api_version: Literal["v1", "v2", "external"] = "external"
                    base_url = f"{urlsplit(req.url).scheme}://{urlsplit(req.url).netloc}"
                    redacted_url = _redact_external_url(req.url)
                else:
                    api_version = req.api_version
                    base_url = config.v1_base_url if req.api_version == "v1" else config.v2_base_url
                    redacted_url = _redact_url(req.url, config.api_key)

                diagnostics = ErrorDiagnostics(
                    method=req.method.upper(),
                    url=redacted_url,
                    request_params=_diagnostic_request_params(req.params),
                    api_version=api_version,
                    base_url=base_url,
                    request_id=request_id,
                    http_version=resp.context.get("http_version"),
                    response_headers=selected_headers,
                    response_body_snippet=str(body)[:512].replace(config.api_key, "[REDACTED]"),
                )
                raise error_from_response(
                    resp.status_code,
                    body,
                    retry_after=retry_after,
                    diagnostics=diagnostics,
                )

            return resp

        def redirect_policy(
            req: SDKRequest, next: Callable[[SDKRequest], SDKRawResponse]
        ) -> SDKRawResponse:
            current_req = req
            redirects_followed = 0
            ever_external = bool(current_req.context.get("ever_external", False))

            while True:
                deadline_seconds = current_req.context.get("deadline_seconds")
                if deadline_seconds is not None:
                    started_at = current_req.context.get("started_at") or time.monotonic()
                    if (time.monotonic() - started_at) >= deadline_seconds:
                        raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

                resp = next(current_req)
                if not (300 <= resp.status_code < 400):
                    resp.context["ever_external"] = ever_external
                    return resp

                location = dict(resp.headers).get("Location") or dict(resp.headers).get("location")
                if not location:
                    resp.context["ever_external"] = ever_external
                    return resp

                # If stop_at_redirect is set, return the response with redirect location
                if current_req.context.get("stop_at_redirect"):
                    resp.context["redirect_location"] = location
                    resp.context["ever_external"] = ever_external
                    return resp

                if redirects_followed >= _MAX_DOWNLOAD_REDIRECTS:
                    raise UnsafeUrlError(
                        "Refusing to follow too many redirects for download",
                        url=_redact_external_url(current_req.url),
                    )

                absolute = str(urljoin(current_req.url, location))
                scheme = urlsplit(absolute).scheme.lower()
                if scheme and scheme not in ("https", "http"):
                    raise UnsafeUrlError("Refusing to follow non-http(s) redirect", url=absolute)
                if scheme == "http" and not config.allow_insecure_download_redirects:
                    raise UnsafeUrlError(
                        "Refusing to follow non-https redirect for download",
                        url=_redact_external_url(absolute),
                    )

                to_host = urlsplit(absolute).netloc
                to_external = to_host not in internal_hosts
                ever_external = ever_external or to_external

                emit = current_req.context.get("emit_event")
                if emit is not None:
                    from_url = _sanitize_hook_url(
                        current_req.url,
                        api_key=config.api_key,
                        external=bool(current_req.context.get("external", False)),
                        external_hook_policy=config.policies.external_hooks,
                    )
                    to_url = _sanitize_hook_url(
                        absolute,
                        api_key=config.api_key,
                        external=to_external,
                        external_hook_policy=config.policies.external_hooks,
                    )
                    if from_url is not None and to_url is not None:
                        emit(
                            RedirectFollowed(
                                client_request_id=current_req.context.get("client_request_id")
                                or "unknown",
                                from_url=from_url,
                                to_url=to_url,
                                hop=redirects_followed + 1,
                                external=to_external,
                            )
                        )

                next_headers = (
                    _strip_credential_headers(current_req.headers)
                    if to_external
                    else list(current_req.headers)
                )
                next_context: RequestContext = cast(RequestContext, dict(current_req.context))
                next_context["external"] = to_external
                next_context["ever_external"] = ever_external
                current_req = replace(
                    current_req, url=absolute, headers=next_headers, context=next_context
                )
                redirects_followed += 1

        middlewares: list[Middleware[SDKRawResponse]] = [
            cast(Middleware[SDKRawResponse], self._request_id_middleware()),
            cast(Middleware[SDKRawResponse], self._hooks_middleware()),
            cast(Middleware[SDKRawResponse], self._write_guard_middleware()),
            cast(Middleware[SDKRawResponse], self._retry_middleware()),
            cast(Middleware[SDKRawResponse], self._auth_middleware()),
            redirect_policy,
            raw_response_mapping,
        ]
        return compose(middlewares, terminal)

    def _build_raw_stream_pipeline(self) -> Callable[[SDKRequest], SDKBaseResponse]:
        config = self._config
        internal_hosts = {
            urlsplit(config.v1_base_url).netloc,
            urlsplit(config.v2_base_url).netloc,
        }

        def terminal(req: SDKRequest) -> SDKBaseResponse:
            external = bool(req.context.get("external", False))
            if config.log_requests and not external:
                logger.debug(f"{req.method} {req.url}")

            request_kwargs: dict[str, Any] = {"follow_redirects": False}
            if req.headers:
                request_kwargs["headers"] = req.headers
            if req.params is not None:
                request_kwargs["params"] = req.params

            timeout = req.context.get("timeout")
            if timeout is not None:
                request_kwargs["timeout"] = timeout

            cm = self._client.stream(req.method, req.url, **request_kwargs)
            try:
                response = cm.__enter__()
                headers = list(response.headers.multi_items())
            except Exception:
                # Ensure context manager is closed on any error during setup
                cm.__exit__(*sys.exc_info())
                raise

            if response.status_code >= 400:
                try:
                    content = response.read()
                finally:
                    cm.__exit__(None, None, None)
                return SDKRawResponse(
                    status_code=response.status_code,
                    headers=headers,
                    content=content,
                    context={"external": external, "http_version": response.http_version},
                )

            request_info = cast(RequestInfo | None, req.context.get("hook_request_info"))
            client_request_id = req.context.get("client_request_id") or "unknown"
            started_at = req.context.get("started_at") or time.monotonic()
            deadline_seconds = req.context.get("deadline_seconds")
            on_progress = cast(ProgressCallback | None, req.context.get("on_progress"))
            emit = cast(Callable[[HookEvent], Any] | None, req.context.get("emit_event"))

            stream = _HTTPXSyncStream(
                context_manager=cm,
                response=response,
                headers=headers,
                request_info=request_info,
                client_request_id=client_request_id,
                external=external,
                started_at=started_at,
                deadline_seconds=deadline_seconds,
                on_progress=on_progress,
                emit_event=emit,
            )
            return SDKRawStreamResponse(
                status_code=response.status_code,
                headers=headers,
                stream=stream,
                context={"external": external, "http_version": response.http_version},
            )

        def raw_response_mapping(
            req: SDKRequest, next: Callable[[SDKRequest], SDKBaseResponse]
        ) -> SDKBaseResponse:
            resp = next(req)
            headers_map = dict(resp.headers)
            external = bool(resp.context.get("external", False))
            if not external:
                self._rate_limit.update_from_headers(headers_map)

            if resp.status_code >= 400:
                assert isinstance(resp, SDKRawResponse)
                try:
                    body: Any = json.loads(resp.content) if resp.content else {}
                except Exception:
                    body = {
                        "message": _safe_body_preview(
                            resp.content, api_key=config.api_key, external=external
                        )
                    }

                retry_after = None
                if resp.status_code == 429:
                    header_value = headers_map.get("Retry-After") or headers_map.get("retry-after")
                    if header_value is not None:
                        retry_after = _parse_retry_after(header_value)

                selected_headers = _select_response_headers(headers_map)
                request_id = _extract_request_id(selected_headers)
                if request_id is not None:
                    resp.context["request_id"] = request_id

                if external:
                    api_version: Literal["v1", "v2", "external"] = "external"
                    base_url = f"{urlsplit(req.url).scheme}://{urlsplit(req.url).netloc}"
                    redacted_url = _redact_external_url(req.url)
                else:
                    api_version = req.api_version
                    base_url = config.v1_base_url if req.api_version == "v1" else config.v2_base_url
                    redacted_url = _redact_url(req.url, config.api_key)

                diagnostics = ErrorDiagnostics(
                    method=req.method.upper(),
                    url=redacted_url,
                    request_params=_diagnostic_request_params(req.params),
                    api_version=api_version,
                    base_url=base_url,
                    request_id=request_id,
                    http_version=resp.context.get("http_version"),
                    response_headers=selected_headers,
                    response_body_snippet=str(body)[:512].replace(config.api_key, "[REDACTED]"),
                )
                raise error_from_response(
                    resp.status_code,
                    body,
                    retry_after=retry_after,
                    diagnostics=diagnostics,
                )

            return resp

        def redirect_policy(
            req: SDKRequest, next: Callable[[SDKRequest], SDKBaseResponse]
        ) -> SDKBaseResponse:
            current_req = req
            redirects_followed = 0
            ever_external = bool(current_req.context.get("ever_external", False))

            while True:
                deadline_seconds = current_req.context.get("deadline_seconds")
                if deadline_seconds is not None:
                    started_at = current_req.context.get("started_at") or time.monotonic()
                    if (time.monotonic() - started_at) >= deadline_seconds:
                        raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

                resp = next(current_req)
                if not (300 <= resp.status_code < 400):
                    resp.context["ever_external"] = ever_external
                    return resp

                location = dict(resp.headers).get("Location") or dict(resp.headers).get("location")
                if not location:
                    resp.context["ever_external"] = ever_external
                    return resp

                # If stop_at_redirect is set, return the response with redirect location
                if current_req.context.get("stop_at_redirect"):
                    resp.context["redirect_location"] = location
                    resp.context["ever_external"] = ever_external
                    return resp

                if redirects_followed >= _MAX_DOWNLOAD_REDIRECTS:
                    raise UnsafeUrlError(
                        "Refusing to follow too many redirects for download",
                        url=_redact_external_url(current_req.url),
                    )

                absolute = str(urljoin(current_req.url, location))
                scheme = urlsplit(absolute).scheme.lower()
                if scheme and scheme not in ("https", "http"):
                    raise UnsafeUrlError("Refusing to follow non-http(s) redirect", url=absolute)
                if scheme == "http" and not config.allow_insecure_download_redirects:
                    raise UnsafeUrlError(
                        "Refusing to follow non-https redirect for download",
                        url=_redact_external_url(absolute),
                    )

                to_host = urlsplit(absolute).netloc
                to_external = to_host not in internal_hosts
                ever_external = ever_external or to_external

                if isinstance(resp, SDKRawStreamResponse):
                    resp.stream.close()

                emit = current_req.context.get("emit_event")
                if emit is not None:
                    from_url = _sanitize_hook_url(
                        current_req.url,
                        api_key=config.api_key,
                        external=bool(current_req.context.get("external", False)),
                        external_hook_policy=config.policies.external_hooks,
                    )
                    to_url = _sanitize_hook_url(
                        absolute,
                        api_key=config.api_key,
                        external=to_external,
                        external_hook_policy=config.policies.external_hooks,
                    )
                    if from_url is not None and to_url is not None:
                        emit(
                            RedirectFollowed(
                                client_request_id=current_req.context.get("client_request_id")
                                or "unknown",
                                from_url=from_url,
                                to_url=to_url,
                                hop=redirects_followed + 1,
                                external=to_external,
                            )
                        )

                next_headers = (
                    _strip_credential_headers(current_req.headers)
                    if to_external
                    else list(current_req.headers)
                )
                next_context: RequestContext = cast(RequestContext, dict(current_req.context))
                next_context["external"] = to_external
                next_context["ever_external"] = ever_external
                current_req = replace(
                    current_req, url=absolute, headers=next_headers, context=next_context
                )
                redirects_followed += 1

        middlewares: list[Middleware[SDKBaseResponse]] = [
            self._request_id_middleware(),
            self._hooks_middleware(),
            self._write_guard_middleware(),
            self._retry_middleware(),
            self._auth_middleware(),
            redirect_policy,
            raw_response_mapping,
        ]
        return compose(middlewares, terminal)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> HTTPClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @property
    def cache(self) -> SimpleCache | None:
        """Access to the cache for invalidation."""
        return self._cache

    @property
    def rate_limit_state(self) -> RateLimitState:
        """Current rate limit state."""
        return self._rate_limit

    @property
    def enable_beta_endpoints(self) -> bool:
        """Whether beta endpoints are enabled for this client."""
        return self._config.enable_beta_endpoints

    @property
    def affinity_api_version(self) -> str | None:
        """Configured V2 API version sent as ``X-Affinity-Api-Version`` (None = key default)."""
        return self._config.affinity_api_version

    @property
    def affinity_api_versions_seen(self) -> frozenset[str]:
        """V2 API versions Affinity echoed on responses so far (incl. cache hits)."""
        return self._api_versions.seen()

    @property
    def last_affinity_api_version(self) -> str | None:
        """V2 API version echoed by the most recent V2 response (incl. cache hits)."""
        return self._api_versions.last()

    def _reset_affinity_api_versions_seen(self) -> None:
        self._api_versions.reset()

    def _key_default_probe_request(self) -> SDKRequest:
        # Unversioned V2 call: the echo is the API key's default version.
        return SDKRequest(
            method="GET",
            url=self._build_url("/auth/whoami"),
            api_version="v2",
            context={"affinity_api_version": None},
        )

    def probe_key_default_api_version(self) -> str | None:
        """
        Return the API key's default V2 API version.

        Makes one V2 ``GET /auth/whoami`` without ``X-Affinity-Api-Version`` and reads the
        echoed version. The probe does not count towards ``affinity_api_versions_seen``.
        """
        resp = self._pipeline(self._key_default_probe_request())
        return resp.context.get("affinity_api_version")

    def _build_url(self, path: str, *, v1: bool = False) -> str:
        """Build full URL from path."""
        base = self._config.v1_base_url if v1 else self._config.v2_base_url
        # V1 paths don't have /v1 prefix in the base URL
        if v1:
            return f"{base}/{path.lstrip('/')}"
        return f"{base}/{path.lstrip('/')}"

    def _handle_response(
        self,
        response: httpx.Response,
        *,
        method: str,
        url: str,
        v1: bool,
    ) -> dict[str, Any]:
        """Process response and handle errors."""
        # Update rate limit state
        self._rate_limit.update_from_headers(response.headers)

        # Check for errors
        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = {"message": response.text}

            retry_after = None
            if response.status_code == 429:
                header_value = response.headers.get("Retry-After")
                if header_value is not None:
                    retry_after = _parse_retry_after(header_value)

            selected_headers = _select_response_headers(response.headers)
            request_id = _extract_request_id(selected_headers)
            diagnostics = ErrorDiagnostics(
                method=method,
                url=_redact_url(url, self._config.api_key),
                api_version="v1" if v1 else "v2",
                base_url=self._config.v1_base_url if v1 else self._config.v2_base_url,
                request_id=request_id,
                http_version=response.http_version,
                response_headers=selected_headers,
                response_body_snippet=str(body)[:512].replace(self._config.api_key, "[REDACTED]"),
            )

            raise error_from_response(
                response.status_code,
                body,
                retry_after=retry_after,
                diagnostics=diagnostics,
            )

        # Empty response (204 No Content, etc.)
        if response.status_code == 204 or not response.content:
            return {}

        payload = response.json()
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
        if isinstance(payload, list):
            # Some V1 endpoints return top-level arrays. Normalize into an object
            # wrapper so call sites can consistently access `data`.
            return {"data": payload}
        raise AffinityError("Expected JSON object/array response")

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        v1: bool,
        safe_follow: bool = False,
        write_intent: bool = False,
        cache_key: str | None = None,
        cache_ttl: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = kwargs.pop("headers", None) or {}
        params = kwargs.pop("params", None)
        json_payload = kwargs.pop("json", None)
        files = kwargs.pop("files", None)
        data = kwargs.pop("data", None)
        timeout = kwargs.pop("timeout", None)
        if kwargs:
            raise TypeError(f"Unsupported request kwargs: {sorted(kwargs.keys())}")

        context: RequestContext = {}
        if safe_follow:
            context["safe_follow"] = True
        if cache_key is not None:
            context["cache_key"] = cache_key
        if cache_ttl is not None:
            context["cache_ttl"] = float(cache_ttl)
        if timeout is not None:
            if isinstance(timeout, (int, float)):
                context["timeout_seconds"] = float(timeout)
            else:
                raise TypeError("timeout must be float seconds for JSON requests")

        req = SDKRequest(
            method=method.upper(),
            url=url,
            headers=list(headers.items()),
            params=params,
            json=json_payload,
            files=files,
            data=data,
            api_version="v1" if v1 else "v2",
            write_intent=write_intent,
            context=context,
        )
        resp = self._pipeline(req)
        payload = resp.json
        if not isinstance(payload, dict):
            raise AffinityError("Expected JSON object response")
        return cast(dict[str, Any], payload)

    # =========================================================================
    # Public Request Methods
    # =========================================================================

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        v1: bool = False,
        cache_key: str | None = None,
        cache_ttl: float | None = None,
    ) -> dict[str, Any]:
        """
        Make a GET request.

        Args:
            path: API path (e.g., "/companies")
            params: Query parameters
            v1: Use V1 API endpoint
            cache_key: If provided, cache the response with this key
            cache_ttl: Cache TTL override

        Returns:
            Parsed JSON response
        """
        url = self._build_url(path, v1=v1)
        encoded_params = _encode_query_params(params)
        return self._request_with_retry(
            "GET",
            url,
            v1=v1,
            params=encoded_params,
            cache_key=cache_key,
            cache_ttl=cache_ttl,
        )

    def get_v1_page(
        self,
        path: str,
        *,
        signature: Sequence[tuple[str, str]],
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch a v1 paginated page using a frozen canonical query signature.

        This enforces TR-017a by reusing the exact same query signature across
        pages, varying only the `page_token`.
        """
        params = list(signature)
        if page_token is not None:
            params.append(("page_token", page_token))
        url = self._build_url(path, v1=True)
        return self._request_with_retry("GET", url, v1=True, params=params)

    def get_all_pages(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        cache_key: str | None = None,
        cache_ttl: float | None = None,
        max_pages: int = 100,
    ) -> dict[str, Any]:
        """
        GET a V2 paged collection and follow ``pagination.nextUrl`` to the end.

        Returns ``{"data": [...all items...], "pagination": {"nextUrl": None, ...}}``.
        Page 1 goes through the normal (cache-aware) ``get``; when more pages follow, the
        merged result replaces the cached first page so later calls do not refetch.
        Stops after ``max_pages`` pages as a guard against a server that never ends paging.
        """
        data = self.get(path, params=params, cache_key=cache_key, cache_ttl=cache_ttl)
        next_url = (data.get("pagination") or {}).get("nextUrl")
        if not next_url:
            return data
        items = list(data.get("data", []))
        pages = 1
        while next_url and pages < max_pages:
            page = self.get_url(next_url)
            items.extend(page.get("data", []))
            next_url = (page.get("pagination") or {}).get("nextUrl")
            pages += 1
        merged: dict[str, Any] = {"data": items, "pagination": {"nextUrl": None, "prevUrl": None}}
        if cache_key and self._cache:
            self._cache.set(
                f"{cache_key}{self._cache_suffix}",
                merged,
                cache_ttl,
                api_version=self._api_versions.last(),
            )
        return merged

    def get_url(self, url: str) -> dict[str, Any]:
        """
        Make a GET request to a full URL.

        Used for following pagination URLs.
        """
        absolute, is_v1 = _safe_follow_url(
            url,
            v1_base_url=self._config.v1_base_url,
            v2_base_url=self._config.v2_base_url,
        )
        return self._request_with_retry("GET", absolute, v1=is_v1, safe_follow=True)

    def post(
        self,
        path: str,
        *,
        json: Any = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        """Make a POST request."""
        url = self._build_url(path, v1=v1)
        return self._request_with_retry("POST", url, v1=v1, json=json, write_intent=True)

    def put(
        self,
        path: str,
        *,
        json: Any = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        """Make a PUT request."""
        url = self._build_url(path, v1=v1)
        return self._request_with_retry("PUT", url, v1=v1, json=json, write_intent=True)

    def patch(
        self,
        path: str,
        *,
        json: Any = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        """Make a PATCH request."""
        url = self._build_url(path, v1=v1)
        return self._request_with_retry("PATCH", url, v1=v1, json=json, write_intent=True)

    def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        """Make a DELETE request."""
        url = self._build_url(path, v1=v1)
        return self._request_with_retry(
            "DELETE",
            url,
            v1=v1,
            params=_encode_query_params(params),
            write_intent=True,
        )

    def upload_file(
        self,
        path: str,
        *,
        files: dict[str, Any],
        data: dict[str, Any] | None = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        """Upload files with multipart form data."""
        url = self._build_url(path, v1=v1)

        # Ensure we don't force a Content-Type; httpx must generate multipart boundaries.
        headers = dict(self._client.headers)
        headers.pop("Content-Type", None)
        return self._request_with_retry(
            "POST",
            url,
            v1=v1,
            files=files,
            data=data,
            headers=headers,
            write_intent=True,
        )

    def download_file(
        self,
        path: str,
        *,
        v1: bool = False,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> bytes:
        """
        Download file content.

        Notes:
        - The initial Affinity API response may redirect to an external signed URL.
          Redirects are followed without forwarding credentials.
        - Uses the standard retry/diagnostics policy for GET requests.
        """
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

        url = self._build_url(path, v1=v1)
        context: RequestContext = {}
        if timeout is not None:
            context["timeout"] = timeout
        if deadline_seconds is not None:
            context["deadline_seconds"] = float(deadline_seconds)

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )
        resp = self._raw_buffered_pipeline(req)
        return resp.content

    def stream_download(
        self,
        path: str,
        *,
        v1: bool = False,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> Iterator[bytes]:
        """
        Stream-download file content in chunks.

        Notes:
        - The initial Affinity API response may redirect to an external signed URL.
          Redirects are followed without forwarding credentials.
        - External signed URLs are protected via ExternalHookPolicy (redaction by default).
        """
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

        url = self._build_url(path, v1=v1)
        context: RequestContext = {"streaming": True}
        if timeout is not None:
            context["timeout"] = timeout
        if deadline_seconds is not None:
            context["deadline_seconds"] = float(deadline_seconds)
        if on_progress is not None:
            context["on_progress"] = on_progress

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )
        resp = self._raw_stream_pipeline(req)
        if not isinstance(resp, SDKRawStreamResponse):
            return iter(())

        def _iter() -> Iterator[bytes]:
            try:
                yield from resp.stream.iter_bytes(chunk_size=chunk_size)
            finally:
                # Ensure stream is closed even if iterator collected without full consumption
                resp.stream.close()

        return _iter()

    def stream_download_with_info(
        self,
        path: str,
        *,
        v1: bool = False,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> DownloadedFile:
        """
        Stream-download file content and return response metadata (headers/filename/size).

        Notes:
        - The initial Affinity API response may redirect to an external signed URL.
          Redirects are followed without forwarding credentials.
        - External signed URLs are protected via ExternalHookPolicy (redaction by default).
        """
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

        url = self._build_url(path, v1=v1)
        context: RequestContext = {"streaming": True}
        if timeout is not None:
            context["timeout"] = timeout
        if deadline_seconds is not None:
            context["deadline_seconds"] = float(deadline_seconds)
        if on_progress is not None:
            context["on_progress"] = on_progress

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )
        resp = self._raw_stream_pipeline(req)
        if not isinstance(resp, SDKRawStreamResponse):
            info = _download_info_from_headers([])
            return DownloadedFile(
                headers=info["headers"],
                raw_headers=[],
                content_type=info["content_type"],
                filename=info["filename"],
                size=info["size"],
                iter_bytes=iter(()),
            )

        info = _download_info_from_headers(resp.headers)

        def _iter() -> Iterator[bytes]:
            yield from resp.stream.iter_bytes(chunk_size=chunk_size)

        return DownloadedFile(
            headers=info["headers"],
            raw_headers=list(resp.headers),
            content_type=info["content_type"],
            filename=info["filename"],
            size=info["size"],
            iter_bytes=_iter(),
        )

    def get_redirect_url(
        self,
        path: str,
        *,
        v1: bool = False,
        timeout: httpx.Timeout | float | None = None,
    ) -> str | None:
        """
        Get the redirect URL for a download endpoint without following it.

        Makes a GET request and returns the Location header if the response
        is a redirect (3xx). Returns None if not a redirect.

        This is useful for getting presigned URLs from file download endpoints.
        """
        url = self._build_url(path, v1=v1)
        context: RequestContext = {"stop_at_redirect": True}
        if timeout is not None:
            context["timeout"] = timeout

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )
        resp = self._raw_buffered_pipeline(req)
        return resp.context.get("redirect_location")

    def wrap_validation_error(
        self,
        error: Exception,
        *,
        context: str | None = None,
    ) -> VersionCompatibilityError:
        """
        Wrap a validation error with version compatibility context.

        TR-015: If expected_v2_version is configured, validation failures
        are wrapped with actionable guidance about checking API version.
        """
        expected = (
            self._config.expected_v2_version
            or self._config.affinity_api_version
            or self._api_versions.last()
        )
        message = (
            f"Response parsing failed: {error}. "
            "This may indicate a v2 API version mismatch. "
            "Check your API key's Default API Version in the Affinity dashboard."
        )
        if context:
            message = f"[{context}] {message}"
        return VersionCompatibilityError(
            message,
            expected_version=expected,
            parsing_error=str(error),
        )

    @property
    def expected_v2_version(self) -> str | None:
        """Expected V2 API version for diagnostics."""
        return self._config.expected_v2_version


# =============================================================================
# Asynchronous HTTP Client
# =============================================================================


class AsyncHTTPClient:
    """
    Asynchronous HTTP client for Affinity API.

    Same functionality as HTTPClient but with async/await support.
    """

    def __init__(self, config: ClientConfig):
        self._config = config
        self._rate_limit = RateLimitState()
        self._rate_limit_gate = _RateLimitGateAsync()
        self._cache = SimpleCache(config.cache_ttl) if config.enable_cache else None
        self._cache_suffix = _cache_key_suffix(
            self._config.v1_base_url,
            self._config.v2_base_url,
            self._config.api_key,
            self._config.affinity_api_version,
        )
        self._api_versions = _ApiVersionTracker(config)
        self._client: httpx.AsyncClient | None = None
        self._client_lock: asyncio.Lock | None = None  # Lazy init to avoid event loop issues
        self._pipeline = self._build_pipeline()
        self._raw_buffered_pipeline = self._build_raw_buffered_pipeline()
        self._raw_stream_pipeline = self._build_raw_stream_pipeline()

    def _request_id_middleware(
        self,
    ) -> AsyncMiddleware[SDKBaseResponse]:
        async def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKBaseResponse]]
        ) -> SDKBaseResponse:
            context: RequestContext = cast(RequestContext, dict(req.context))
            client_request_id = context.get("client_request_id")
            if not isinstance(client_request_id, str) or not client_request_id:
                try:
                    client_request_id = uuid.uuid4().hex
                except Exception:
                    client_request_id = "unknown"
                context["client_request_id"] = client_request_id

            headers = list(req.headers)
            if not any(k.lower() == "x-client-request-id" for (k, _v) in headers):
                headers.append(("X-Client-Request-Id", client_request_id))

            return await next(replace(req, headers=headers, context=context))

        return middleware

    def _hooks_middleware(
        self,
    ) -> AsyncMiddleware[SDKBaseResponse]:
        config = self._config

        async def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKBaseResponse]]
        ) -> SDKBaseResponse:
            context: RequestContext = cast(RequestContext, dict(req.context))
            started_at = context.get("started_at")
            if started_at is None:
                started_at = time.monotonic()
                context["started_at"] = started_at

            client_request_id = context.get("client_request_id") or "unknown"

            async def emit_event(event: HookEvent) -> None:
                if config.on_event is None:
                    return
                if (
                    getattr(event, "external", False)
                    and config.policies.external_hooks is ExternalHookPolicy.SUPPRESS
                ):
                    return
                try:
                    result = config.on_event(event)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    if config.hook_error_policy == "raise":
                        raise
                    logger.warning(
                        "Hook error suppressed (hook_error_policy=swallow)", exc_info=True
                    )

            context["emit_event"] = emit_event

            external = bool(context.get("external", False))
            sanitized_url = _sanitize_hook_url(
                req.url,
                api_key=config.api_key,
                external=external,
                external_hook_policy=config.policies.external_hooks,
            )
            request_info = (
                RequestInfo(
                    method=req.method.upper(),
                    url=sanitized_url,
                    headers=_sanitize_hook_headers(req.headers),
                )
                if sanitized_url is not None
                else None
            )
            context["hook_request_info"] = request_info

            if request_info is not None:
                if config.on_request:
                    config.on_request(request_info)
                await emit_event(
                    RequestStarted(
                        client_request_id=client_request_id,
                        request=request_info,
                        api_version=req.api_version if not external else "external",
                    )
                )

            try:
                resp = await next(replace(req, context=context))
            except asyncio.CancelledError as exc:
                elapsed_ms = (time.monotonic() - started_at) * 1000
                if request_info is not None:
                    await emit_event(
                        RequestFailed(
                            client_request_id=client_request_id,
                            request=request_info,
                            error=exc,
                            elapsed_ms=elapsed_ms,
                            external=external,
                        )
                    )
                if config.on_error and request_info is not None:
                    config.on_error(
                        ErrorInfo(error=exc, elapsed_ms=elapsed_ms, request=request_info)
                    )
                raise
            except Exception as exc:
                elapsed_ms = (time.monotonic() - started_at) * 1000
                if request_info is not None:
                    await emit_event(
                        RequestFailed(
                            client_request_id=client_request_id,
                            request=request_info,
                            error=exc,
                            elapsed_ms=elapsed_ms,
                            external=external,
                        )
                    )
                if config.on_error and request_info is not None:
                    config.on_error(
                        ErrorInfo(error=exc, elapsed_ms=elapsed_ms, request=request_info)
                    )
                raise

            elapsed_ms = (time.monotonic() - started_at) * 1000
            resp.context.setdefault("client_request_id", client_request_id)
            resp.context.setdefault("external", external)
            resp.context.setdefault("elapsed_seconds", elapsed_ms / 1000.0)

            if config.on_response and request_info is not None:
                config.on_response(
                    ResponseInfo(
                        status_code=resp.status_code,
                        headers=dict(resp.headers),
                        elapsed_ms=elapsed_ms,
                        cache_hit=bool(resp.context.get("cache_hit", False)),
                        request=request_info,
                    )
                )

            if request_info is not None:
                await emit_event(
                    ResponseHeadersReceived(
                        client_request_id=client_request_id,
                        request=request_info,
                        status_code=resp.status_code,
                        headers=list(resp.headers),
                        elapsed_ms=elapsed_ms,
                        external=bool(resp.context.get("external", False)),
                        cache_hit=bool(resp.context.get("cache_hit", False)),
                        request_id=resp.context.get("request_id"),
                    )
                )
                if not isinstance(resp, SDKRawStreamResponse):
                    await emit_event(
                        RequestSucceeded(
                            client_request_id=client_request_id,
                            request=request_info,
                            status_code=resp.status_code,
                            elapsed_ms=elapsed_ms,
                            external=bool(resp.context.get("external", False)),
                        )
                    )

            return resp

        return middleware

    def _retry_middleware(
        self,
    ) -> AsyncMiddleware[SDKBaseResponse]:
        config = self._config

        async def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKBaseResponse]]
        ) -> SDKBaseResponse:
            last_error: Exception | None = None
            for attempt in range(config.max_retries + 1):
                if attempt == 0:
                    throttle_delay = await self._rate_limit_gate.delay()
                    if throttle_delay > 0:
                        await asyncio.sleep(throttle_delay + _throttle_jitter(throttle_delay))
                try:
                    resp = await next(req)
                    resp.context["retry_count"] = attempt
                    return resp
                except (
                    RateLimitError,
                    AffinityError,
                    httpx.TimeoutException,
                    httpx.NetworkError,
                ) as e:
                    outcome = _retry_outcome(
                        method=req.method.upper(),
                        attempt=attempt,
                        max_retries=config.max_retries,
                        retry_delay=config.retry_delay,
                        error=e,
                    )
                    if isinstance(e, RateLimitError):
                        rate_limit_wait = (
                            float(e.retry_after) if e.retry_after is not None else outcome.wait_time
                        )
                        if rate_limit_wait is not None:
                            await self._rate_limit_gate.note(rate_limit_wait)
                    if outcome.action == "raise":
                        raise
                    if outcome.action == "raise_wrapped":
                        assert outcome.wrapped_error is not None
                        raise outcome.wrapped_error from e

                    assert outcome.last_error is not None
                    last_error = outcome.last_error
                    if outcome.action == "break":
                        break

                    assert outcome.wait_time is not None
                    emit = req.context.get("emit_event")
                    request_info = cast(RequestInfo | None, req.context.get("hook_request_info"))
                    if emit is not None and request_info is not None:
                        await cast(Callable[[HookEvent], Awaitable[None]], emit)(
                            RequestRetrying(
                                client_request_id=req.context.get("client_request_id") or "unknown",
                                request=request_info,
                                attempt=attempt + 1,
                                wait_seconds=outcome.wait_time,
                                reason=outcome.log_message or type(e).__name__,
                            )
                        )

                    if outcome.log_message:
                        logger.warning(outcome.log_message)
                    await asyncio.sleep(outcome.wait_time)

            if last_error:
                raise last_error
            raise AffinityError("Request failed after retries")

        return middleware

    def _auth_middleware(
        self,
    ) -> AsyncMiddleware[SDKBaseResponse]:
        config = self._config

        async def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKBaseResponse]]
        ) -> SDKBaseResponse:
            headers = [(k, v) for (k, v) in req.headers if k.lower() != "authorization"]
            if req.api_version == "v1" and config.v1_auth_mode == "basic":
                token = base64.b64encode(f":{config.api_key}".encode()).decode("ascii")
                headers.append(("Authorization", f"Basic {token}"))
            else:
                headers.append(("Authorization", f"Bearer {config.api_key}"))
            version = self._api_versions.inject(req, headers)
            try:
                resp = await next(replace(req, headers=headers))
            except ValidationError as exc:
                mapped = self._api_versions.map_error(exc, version)
                if mapped is exc:
                    raise
                raise mapped from exc
            self._api_versions.observe(req, resp)
            return resp

        return middleware

    def _write_guard_middleware(
        self,
    ) -> AsyncMiddleware[SDKBaseResponse]:
        config = self._config

        async def middleware(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKBaseResponse]]
        ) -> SDKBaseResponse:
            if config.policies.write is WritePolicy.DENY and req.write_intent:
                method_upper = req.method.upper()
                raise WriteNotAllowedError(
                    f"Cannot {method_upper} while writes are disabled by policy",
                    method=method_upper,
                    url=_redact_url(req.url, config.api_key),
                )
            return await next(req)

        return middleware

    def _build_pipeline(self) -> Callable[[SDKRequest], Awaitable[SDKResponse]]:
        config = self._config

        async def terminal(req: SDKRequest) -> SDKResponse:
            external = bool(req.context.get("external", False))
            if config.log_requests and not external:
                logger.debug(f"{req.method} {req.url}")

            request_kwargs: dict[str, Any] = {}
            if req.headers:
                request_kwargs["headers"] = req.headers
            if req.params is not None:
                request_kwargs["params"] = req.params
            if req.json is not None:
                request_kwargs["json"] = req.json
            if req.files is not None:
                request_kwargs["files"] = req.files
            if req.data is not None:
                request_kwargs["data"] = req.data

            timeout_seconds = req.context.get("timeout_seconds")
            if timeout_seconds is not None:
                request_kwargs["timeout"] = float(timeout_seconds)

            client = await self._get_client()
            response = await client.request(req.method, req.url, **request_kwargs)
            return SDKResponse(
                status_code=response.status_code,
                headers=list(response.headers.multi_items()),
                content=response.content,
                context={"external": external, "http_version": response.http_version},
            )

        async def response_mapping(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKResponse]]
        ) -> SDKResponse:
            resp = await next(req)
            headers_map = dict(resp.headers)
            external = bool(resp.context.get("external", False))
            if not external:
                self._rate_limit.update_from_headers(headers_map)

            if req.context.get("safe_follow", False):
                location = headers_map.get("Location") or headers_map.get("location")
                if 300 <= resp.status_code < 400 and location:
                    raise UnsafeUrlError(
                        "Refusing to follow redirect for server-provided URL",
                        url=req.url,
                    )

            if resp.status_code >= 400:
                try:
                    body = json.loads(resp.content) if resp.content else {}
                except Exception:
                    body = {"message": resp.content.decode("utf-8", errors="replace")}

                retry_after = None
                if resp.status_code == 429:
                    header_value = headers_map.get("Retry-After") or headers_map.get("retry-after")
                    if header_value is not None:
                        retry_after = _parse_retry_after(header_value)

                selected_headers = _select_response_headers(headers_map)
                request_id = _extract_request_id(selected_headers)
                if request_id is not None:
                    resp.context["request_id"] = request_id

                diagnostics = ErrorDiagnostics(
                    method=req.method.upper(),
                    url=_redact_url(req.url, config.api_key),
                    request_params=_diagnostic_request_params(req.params),
                    api_version=req.api_version,
                    base_url=config.v1_base_url if req.api_version == "v1" else config.v2_base_url,
                    request_id=request_id,
                    http_version=resp.context.get("http_version"),
                    response_headers=selected_headers,
                    response_body_snippet=str(body)[:512].replace(config.api_key, "[REDACTED]"),
                )
                raise error_from_response(
                    resp.status_code,
                    body,
                    retry_after=retry_after,
                    diagnostics=diagnostics,
                )

            # Check for empty or whitespace-only content
            content_stripped = resp.content.strip() if resp.content else b""
            if resp.status_code == 204 or not content_stripped:
                resp.json = {}
                return resp

            try:
                payload = json.loads(content_stripped)
            except Exception as e:
                raise AffinityError("Expected JSON object/array response") from e

            if isinstance(payload, dict):
                resp.json = payload
                return resp
            if isinstance(payload, list):
                resp.json = {"data": payload}
                return resp
            raise AffinityError("Expected JSON object/array response")

        async def cache_middleware(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKResponse]]
        ) -> SDKResponse:
            cache_key = req.context.get("cache_key")
            if cache_key and self._cache:
                cached_entry = self._cache.get_entry(f"{cache_key}{self._cache_suffix}")
                if cached_entry is not None:
                    return SDKResponse(
                        status_code=200,
                        headers=(
                            [(AFFINITY_API_VERSION_HEADER, cached_entry.api_version)]
                            if cached_entry.api_version
                            else []
                        ),
                        content=b"",
                        json=cached_entry.value,
                        context={
                            "cache_hit": True,
                            "external": bool(req.context.get("external", False)),
                        },
                    )

            resp = await next(req)
            if cache_key and self._cache and isinstance(resp.json, dict):
                self._cache.set(
                    f"{cache_key}{self._cache_suffix}",
                    cast(dict[str, Any], resp.json),
                    req.context.get("cache_ttl"),
                    api_version=_header_value(resp.headers, AFFINITY_API_VERSION_HEADER),
                )
            return resp

        middlewares: list[AsyncMiddleware[SDKResponse]] = [
            cast(AsyncMiddleware[SDKResponse], self._request_id_middleware()),
            cast(AsyncMiddleware[SDKResponse], self._hooks_middleware()),
            cast(AsyncMiddleware[SDKResponse], self._write_guard_middleware()),
            cast(AsyncMiddleware[SDKResponse], self._retry_middleware()),
            cast(AsyncMiddleware[SDKResponse], self._auth_middleware()),
            cache_middleware,
            response_mapping,
        ]
        return compose_async(middlewares, terminal)

    def _build_raw_buffered_pipeline(self) -> Callable[[SDKRequest], Awaitable[SDKRawResponse]]:
        config = self._config
        internal_hosts = {
            urlsplit(config.v1_base_url).netloc,
            urlsplit(config.v2_base_url).netloc,
        }

        async def terminal(req: SDKRequest) -> SDKRawResponse:
            external = bool(req.context.get("external", False))
            if config.log_requests and not external:
                logger.debug(f"{req.method} {req.url}")

            request_kwargs: dict[str, Any] = {"follow_redirects": False}
            if req.headers:
                request_kwargs["headers"] = req.headers
            if req.params is not None:
                request_kwargs["params"] = req.params
            if req.files is not None:
                request_kwargs["files"] = req.files
            if req.data is not None:
                request_kwargs["data"] = req.data
            if req.json is not None:
                request_kwargs["json"] = req.json

            timeout = req.context.get("timeout")
            if timeout is not None:
                request_kwargs["timeout"] = timeout
            timeout_seconds = req.context.get("timeout_seconds")
            if timeout_seconds is not None and timeout is None:
                request_kwargs["timeout"] = float(timeout_seconds)

            client = await self._get_client()
            response = await client.request(req.method, req.url, **request_kwargs)
            return SDKRawResponse(
                status_code=response.status_code,
                headers=list(response.headers.multi_items()),
                content=response.content,
                context={"external": external, "http_version": response.http_version},
            )

        async def raw_response_mapping(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKRawResponse]]
        ) -> SDKRawResponse:
            resp = await next(req)
            headers_map = dict(resp.headers)
            external = bool(resp.context.get("external", False))
            if not external:
                self._rate_limit.update_from_headers(headers_map)

            if resp.status_code >= 400:
                try:
                    body: Any = json.loads(resp.content) if resp.content else {}
                except Exception:
                    body = {
                        "message": _safe_body_preview(
                            resp.content, api_key=config.api_key, external=external
                        )
                    }

                retry_after = None
                if resp.status_code == 429:
                    header_value = headers_map.get("Retry-After") or headers_map.get("retry-after")
                    if header_value is not None:
                        retry_after = _parse_retry_after(header_value)

                selected_headers = _select_response_headers(headers_map)
                request_id = _extract_request_id(selected_headers)
                if request_id is not None:
                    resp.context["request_id"] = request_id

                if external:
                    api_version: Literal["v1", "v2", "external"] = "external"
                    base_url = f"{urlsplit(req.url).scheme}://{urlsplit(req.url).netloc}"
                    redacted_url = _redact_external_url(req.url)
                else:
                    api_version = req.api_version
                    base_url = config.v1_base_url if req.api_version == "v1" else config.v2_base_url
                    redacted_url = _redact_url(req.url, config.api_key)

                diagnostics = ErrorDiagnostics(
                    method=req.method.upper(),
                    url=redacted_url,
                    request_params=_diagnostic_request_params(req.params),
                    api_version=api_version,
                    base_url=base_url,
                    request_id=request_id,
                    http_version=resp.context.get("http_version"),
                    response_headers=selected_headers,
                    response_body_snippet=str(body)[:512].replace(config.api_key, "[REDACTED]"),
                )
                raise error_from_response(
                    resp.status_code,
                    body,
                    retry_after=retry_after,
                    diagnostics=diagnostics,
                )

            return resp

        async def redirect_policy(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKRawResponse]]
        ) -> SDKRawResponse:
            current_req = req
            redirects_followed = 0
            ever_external = bool(current_req.context.get("ever_external", False))

            while True:
                deadline_seconds = current_req.context.get("deadline_seconds")
                if deadline_seconds is not None:
                    started_at = current_req.context.get("started_at") or time.monotonic()
                    if (time.monotonic() - started_at) >= deadline_seconds:
                        raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

                resp = await next(current_req)
                if not (300 <= resp.status_code < 400):
                    resp.context["ever_external"] = ever_external
                    return resp

                headers_dict = dict(resp.headers)
                location = headers_dict.get("Location") or headers_dict.get("location")
                if not location:
                    resp.context["ever_external"] = ever_external
                    return resp

                # If stop_at_redirect is set, return the response with redirect location
                if current_req.context.get("stop_at_redirect"):
                    resp.context["redirect_location"] = location
                    resp.context["ever_external"] = ever_external
                    return resp

                if redirects_followed >= _MAX_DOWNLOAD_REDIRECTS:
                    raise UnsafeUrlError(
                        "Refusing to follow too many redirects for download",
                        url=_redact_external_url(current_req.url),
                    )

                absolute = str(urljoin(current_req.url, location))
                scheme = urlsplit(absolute).scheme.lower()
                if scheme and scheme not in ("https", "http"):
                    raise UnsafeUrlError("Refusing to follow non-http(s) redirect", url=absolute)
                if scheme == "http" and not config.allow_insecure_download_redirects:
                    raise UnsafeUrlError(
                        "Refusing to follow non-https redirect for download",
                        url=_redact_external_url(absolute),
                    )

                to_host = urlsplit(absolute).netloc
                to_external = to_host not in internal_hosts
                ever_external = ever_external or to_external

                emit = current_req.context.get("emit_event")
                if emit is not None:
                    from_url = _sanitize_hook_url(
                        current_req.url,
                        api_key=config.api_key,
                        external=bool(current_req.context.get("external", False)),
                        external_hook_policy=config.policies.external_hooks,
                    )
                    to_url = _sanitize_hook_url(
                        absolute,
                        api_key=config.api_key,
                        external=to_external,
                        external_hook_policy=config.policies.external_hooks,
                    )
                    if from_url is not None and to_url is not None:
                        await cast(Callable[[HookEvent], Awaitable[None]], emit)(
                            RedirectFollowed(
                                client_request_id=current_req.context.get("client_request_id")
                                or "unknown",
                                from_url=from_url,
                                to_url=to_url,
                                hop=redirects_followed + 1,
                                external=to_external,
                            )
                        )

                next_headers = (
                    _strip_credential_headers(current_req.headers)
                    if to_external
                    else list(current_req.headers)
                )
                next_context: RequestContext = cast(RequestContext, dict(current_req.context))
                next_context["external"] = to_external
                next_context["ever_external"] = ever_external
                current_req = replace(
                    current_req, url=absolute, headers=next_headers, context=next_context
                )
                redirects_followed += 1

        middlewares: list[AsyncMiddleware[SDKRawResponse]] = [
            cast(AsyncMiddleware[SDKRawResponse], self._request_id_middleware()),
            cast(AsyncMiddleware[SDKRawResponse], self._hooks_middleware()),
            cast(AsyncMiddleware[SDKRawResponse], self._write_guard_middleware()),
            cast(AsyncMiddleware[SDKRawResponse], self._retry_middleware()),
            cast(AsyncMiddleware[SDKRawResponse], self._auth_middleware()),
            redirect_policy,
            raw_response_mapping,
        ]
        return compose_async(middlewares, terminal)

    def _build_raw_stream_pipeline(self) -> Callable[[SDKRequest], Awaitable[SDKBaseResponse]]:
        config = self._config
        internal_hosts = {
            urlsplit(config.v1_base_url).netloc,
            urlsplit(config.v2_base_url).netloc,
        }

        async def terminal(req: SDKRequest) -> SDKBaseResponse:
            external = bool(req.context.get("external", False))
            if config.log_requests and not external:
                logger.debug(f"{req.method} {req.url}")

            request_kwargs: dict[str, Any] = {"follow_redirects": False}
            if req.headers:
                request_kwargs["headers"] = req.headers
            if req.params is not None:
                request_kwargs["params"] = req.params

            timeout = req.context.get("timeout")
            if timeout is not None:
                request_kwargs["timeout"] = timeout

            client = await self._get_client()
            cm = client.stream(req.method, req.url, **request_kwargs)
            try:
                response = await cm.__aenter__()
                headers = list(response.headers.multi_items())
            except Exception:
                # Ensure context manager is closed on any error during setup
                await cm.__aexit__(*sys.exc_info())
                raise

            if response.status_code >= 400:
                try:
                    content = await response.aread()
                finally:
                    await cm.__aexit__(None, None, None)
                return SDKRawResponse(
                    status_code=response.status_code,
                    headers=headers,
                    content=content,
                    context={"external": external, "http_version": response.http_version},
                )

            request_info = cast(RequestInfo | None, req.context.get("hook_request_info"))
            client_request_id = req.context.get("client_request_id") or "unknown"
            started_at = req.context.get("started_at") or time.monotonic()
            deadline_seconds = req.context.get("deadline_seconds")
            on_progress = cast(ProgressCallback | None, req.context.get("on_progress"))
            emit = cast(Callable[[HookEvent], Any] | None, req.context.get("emit_event"))

            stream = _HTTPXAsyncStream(
                context_manager=cm,
                response=response,
                headers=headers,
                request_info=request_info,
                client_request_id=client_request_id,
                external=external,
                started_at=started_at,
                deadline_seconds=deadline_seconds,
                on_progress=on_progress,
                emit_event=emit,
            )
            return SDKRawStreamResponse(
                status_code=response.status_code,
                headers=headers,
                stream=stream,
                context={"external": external, "http_version": response.http_version},
            )

        async def raw_response_mapping(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKBaseResponse]]
        ) -> SDKBaseResponse:
            resp = await next(req)
            headers_map = dict(resp.headers)
            external = bool(resp.context.get("external", False))
            if not external:
                self._rate_limit.update_from_headers(headers_map)

            if resp.status_code >= 400:
                assert isinstance(resp, SDKRawResponse)
                try:
                    body: Any = json.loads(resp.content) if resp.content else {}
                except Exception:
                    body = {
                        "message": _safe_body_preview(
                            resp.content, api_key=config.api_key, external=external
                        )
                    }

                retry_after = None
                if resp.status_code == 429:
                    header_value = headers_map.get("Retry-After") or headers_map.get("retry-after")
                    if header_value is not None:
                        retry_after = _parse_retry_after(header_value)

                selected_headers = _select_response_headers(headers_map)
                request_id = _extract_request_id(selected_headers)
                if request_id is not None:
                    resp.context["request_id"] = request_id

                if external:
                    api_version: Literal["v1", "v2", "external"] = "external"
                    base_url = f"{urlsplit(req.url).scheme}://{urlsplit(req.url).netloc}"
                    redacted_url = _redact_external_url(req.url)
                else:
                    api_version = req.api_version
                    base_url = config.v1_base_url if req.api_version == "v1" else config.v2_base_url
                    redacted_url = _redact_url(req.url, config.api_key)

                diagnostics = ErrorDiagnostics(
                    method=req.method.upper(),
                    url=redacted_url,
                    request_params=_diagnostic_request_params(req.params),
                    api_version=api_version,
                    base_url=base_url,
                    request_id=request_id,
                    http_version=resp.context.get("http_version"),
                    response_headers=selected_headers,
                    response_body_snippet=str(body)[:512].replace(config.api_key, "[REDACTED]"),
                )
                raise error_from_response(
                    resp.status_code,
                    body,
                    retry_after=retry_after,
                    diagnostics=diagnostics,
                )

            return resp

        async def redirect_policy(
            req: SDKRequest, next: Callable[[SDKRequest], Awaitable[SDKBaseResponse]]
        ) -> SDKBaseResponse:
            current_req = req
            redirects_followed = 0
            ever_external = bool(current_req.context.get("ever_external", False))

            while True:
                deadline_seconds = current_req.context.get("deadline_seconds")
                if deadline_seconds is not None:
                    started_at = current_req.context.get("started_at") or time.monotonic()
                    if (time.monotonic() - started_at) >= deadline_seconds:
                        raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

                resp = await next(current_req)
                if not (300 <= resp.status_code < 400):
                    resp.context["ever_external"] = ever_external
                    return resp

                headers_dict = dict(resp.headers)
                location = headers_dict.get("Location") or headers_dict.get("location")
                if not location:
                    resp.context["ever_external"] = ever_external
                    return resp

                # If stop_at_redirect is set, return the response with redirect location
                if current_req.context.get("stop_at_redirect"):
                    resp.context["redirect_location"] = location
                    resp.context["ever_external"] = ever_external
                    return resp

                if redirects_followed >= _MAX_DOWNLOAD_REDIRECTS:
                    raise UnsafeUrlError(
                        "Refusing to follow too many redirects for download",
                        url=_redact_external_url(current_req.url),
                    )

                absolute = str(urljoin(current_req.url, location))
                scheme = urlsplit(absolute).scheme.lower()
                if scheme and scheme not in ("https", "http"):
                    raise UnsafeUrlError("Refusing to follow non-http(s) redirect", url=absolute)
                if scheme == "http" and not config.allow_insecure_download_redirects:
                    raise UnsafeUrlError(
                        "Refusing to follow non-https redirect for download",
                        url=_redact_external_url(absolute),
                    )

                to_host = urlsplit(absolute).netloc
                to_external = to_host not in internal_hosts
                ever_external = ever_external or to_external

                if isinstance(resp, SDKRawStreamResponse):
                    await cast(_HTTPXAsyncStream, resp.stream).aclose()

                emit = current_req.context.get("emit_event")
                if emit is not None:
                    from_url = _sanitize_hook_url(
                        current_req.url,
                        api_key=config.api_key,
                        external=bool(current_req.context.get("external", False)),
                        external_hook_policy=config.policies.external_hooks,
                    )
                    to_url = _sanitize_hook_url(
                        absolute,
                        api_key=config.api_key,
                        external=to_external,
                        external_hook_policy=config.policies.external_hooks,
                    )
                    if from_url is not None and to_url is not None:
                        await cast(Callable[[HookEvent], Awaitable[None]], emit)(
                            RedirectFollowed(
                                client_request_id=current_req.context.get("client_request_id")
                                or "unknown",
                                from_url=from_url,
                                to_url=to_url,
                                hop=redirects_followed + 1,
                                external=to_external,
                            )
                        )

                next_headers = (
                    _strip_credential_headers(current_req.headers)
                    if to_external
                    else list(current_req.headers)
                )
                next_context: RequestContext = cast(RequestContext, dict(current_req.context))
                next_context["external"] = to_external
                next_context["ever_external"] = ever_external
                current_req = replace(
                    current_req, url=absolute, headers=next_headers, context=next_context
                )
                redirects_followed += 1

        middlewares: list[AsyncMiddleware[SDKBaseResponse]] = [
            self._request_id_middleware(),
            self._hooks_middleware(),
            self._write_guard_middleware(),
            self._retry_middleware(),
            self._auth_middleware(),
            redirect_policy,
            raw_response_mapping,
        ]
        return compose_async(middlewares, terminal)

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy initialization of async client with lock to prevent race conditions."""
        # Quick check without lock - if already initialized, return immediately
        if self._client is not None:
            return self._client

        # Lazy-init lock to avoid event loop issues during __init__
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()

        async with self._client_lock:
            # Double-check after acquiring lock
            if self._client is None:
                self._client = httpx.AsyncClient(
                    http2=self._config.http2,
                    timeout=self._config.timeout,
                    limits=self._config.limits,
                    transport=self._config.async_transport,
                    headers=dict(_DEFAULT_HEADERS),
                )
            return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> AsyncHTTPClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def cache(self) -> SimpleCache | None:
        return self._cache

    @property
    def rate_limit_state(self) -> RateLimitState:
        return self._rate_limit

    @property
    def enable_beta_endpoints(self) -> bool:
        return self._config.enable_beta_endpoints

    @property
    def affinity_api_version(self) -> str | None:
        """Configured V2 API version sent as ``X-Affinity-Api-Version`` (None = key default)."""
        return self._config.affinity_api_version

    @property
    def affinity_api_versions_seen(self) -> frozenset[str]:
        """V2 API versions Affinity echoed on responses so far (incl. cache hits)."""
        return self._api_versions.seen()

    @property
    def last_affinity_api_version(self) -> str | None:
        """V2 API version echoed by the most recent V2 response (incl. cache hits)."""
        return self._api_versions.last()

    def _reset_affinity_api_versions_seen(self) -> None:
        self._api_versions.reset()

    def _key_default_probe_request(self) -> SDKRequest:
        # Unversioned V2 call: the echo is the API key's default version.
        return SDKRequest(
            method="GET",
            url=self._build_url("/auth/whoami"),
            api_version="v2",
            context={"affinity_api_version": None},
        )

    async def probe_key_default_api_version(self) -> str | None:
        """
        Return the API key's default V2 API version.

        Makes one V2 ``GET /auth/whoami`` without ``X-Affinity-Api-Version`` and reads the
        echoed version. The probe does not count towards ``affinity_api_versions_seen``.
        """
        resp = await self._pipeline(self._key_default_probe_request())
        return resp.context.get("affinity_api_version")

    def _build_url(self, path: str, *, v1: bool = False) -> str:
        base = self._config.v1_base_url if v1 else self._config.v2_base_url
        if v1:
            return f"{base}/{path.lstrip('/')}"
        return f"{base}/{path.lstrip('/')}"

    def _handle_response(
        self,
        response: httpx.Response,
        *,
        method: str,
        url: str,
        v1: bool,
    ) -> dict[str, Any]:
        self._rate_limit.update_from_headers(response.headers)

        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = {"message": response.text}

            retry_after = None
            if response.status_code == 429:
                header_value = response.headers.get("Retry-After")
                if header_value is not None:
                    retry_after = _parse_retry_after(header_value)

            selected_headers = _select_response_headers(response.headers)
            request_id = _extract_request_id(selected_headers)
            diagnostics = ErrorDiagnostics(
                method=method,
                url=_redact_url(url, self._config.api_key),
                api_version="v1" if v1 else "v2",
                base_url=self._config.v1_base_url if v1 else self._config.v2_base_url,
                request_id=request_id,
                http_version=response.http_version,
                response_headers=selected_headers,
                response_body_snippet=str(body)[:512].replace(self._config.api_key, "[REDACTED]"),
            )

            raise error_from_response(
                response.status_code,
                body,
                retry_after=retry_after,
                diagnostics=diagnostics,
            )

        if response.status_code == 204 or not response.content:
            return {}

        payload = response.json()
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
        if isinstance(payload, list):
            return {"data": payload}
        raise AffinityError("Expected JSON object/array response")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        v1: bool,
        safe_follow: bool = False,
        write_intent: bool = False,
        cache_key: str | None = None,
        cache_ttl: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = kwargs.pop("headers", None) or {}
        params = kwargs.pop("params", None)
        json_payload = kwargs.pop("json", None)
        files = kwargs.pop("files", None)
        data = kwargs.pop("data", None)
        timeout = kwargs.pop("timeout", None)
        if kwargs:
            raise TypeError(f"Unsupported request kwargs: {sorted(kwargs.keys())}")

        context: RequestContext = {}
        if safe_follow:
            context["safe_follow"] = True
        if cache_key is not None:
            context["cache_key"] = cache_key
        if cache_ttl is not None:
            context["cache_ttl"] = float(cache_ttl)
        if timeout is not None:
            if isinstance(timeout, (int, float)):
                context["timeout_seconds"] = float(timeout)
            else:
                raise TypeError("timeout must be float seconds for JSON requests")

        req = SDKRequest(
            method=method.upper(),
            url=url,
            headers=list(headers.items()),
            params=params,
            json=json_payload,
            files=files,
            data=data,
            api_version="v1" if v1 else "v2",
            write_intent=write_intent,
            context=context,
        )
        resp = await self._pipeline(req)
        payload = resp.json
        if not isinstance(payload, dict):
            raise AffinityError("Expected JSON object response")
        return cast(dict[str, Any], payload)

    # =========================================================================
    # Public Request Methods
    # =========================================================================

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        v1: bool = False,
        cache_key: str | None = None,
        cache_ttl: float | None = None,
    ) -> dict[str, Any]:
        url = self._build_url(path, v1=v1)
        encoded_params = _encode_query_params(params)
        return await self._request_with_retry(
            "GET",
            url,
            v1=v1,
            params=encoded_params,
            cache_key=cache_key,
            cache_ttl=cache_ttl,
        )

    async def get_v1_page(
        self,
        path: str,
        *,
        signature: Sequence[tuple[str, str]],
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Async version of `get_v1_page()`."""
        params = list(signature)
        if page_token is not None:
            params.append(("page_token", page_token))
        url = self._build_url(path, v1=True)
        return await self._request_with_retry("GET", url, v1=True, params=params)

    async def get_all_pages(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        cache_key: str | None = None,
        cache_ttl: float | None = None,
        max_pages: int = 100,
    ) -> dict[str, Any]:
        """
        GET a V2 paged collection and follow ``pagination.nextUrl`` to the end.

        Returns ``{"data": [...all items...], "pagination": {"nextUrl": None, ...}}``.
        Page 1 goes through the normal (cache-aware) ``get``; when more pages follow, the
        merged result replaces the cached first page so later calls do not refetch.
        Stops after ``max_pages`` pages as a guard against a server that never ends paging.
        """
        data = await self.get(path, params=params, cache_key=cache_key, cache_ttl=cache_ttl)
        next_url = (data.get("pagination") or {}).get("nextUrl")
        if not next_url:
            return data
        items = list(data.get("data", []))
        pages = 1
        while next_url and pages < max_pages:
            page = await self.get_url(next_url)
            items.extend(page.get("data", []))
            next_url = (page.get("pagination") or {}).get("nextUrl")
            pages += 1
        merged: dict[str, Any] = {"data": items, "pagination": {"nextUrl": None, "prevUrl": None}}
        if cache_key and self._cache:
            self._cache.set(
                f"{cache_key}{self._cache_suffix}",
                merged,
                cache_ttl,
                api_version=self._api_versions.last(),
            )
        return merged

    async def get_url(self, url: str) -> dict[str, Any]:
        absolute, is_v1 = _safe_follow_url(
            url,
            v1_base_url=self._config.v1_base_url,
            v2_base_url=self._config.v2_base_url,
        )
        return await self._request_with_retry(
            "GET",
            absolute,
            v1=is_v1,
            safe_follow=True,
        )

    async def post(
        self,
        path: str,
        *,
        json: Any = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        url = self._build_url(path, v1=v1)
        return await self._request_with_retry("POST", url, v1=v1, json=json, write_intent=True)

    async def put(
        self,
        path: str,
        *,
        json: Any = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        url = self._build_url(path, v1=v1)
        return await self._request_with_retry("PUT", url, v1=v1, json=json, write_intent=True)

    async def patch(
        self,
        path: str,
        *,
        json: Any = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        url = self._build_url(path, v1=v1)
        return await self._request_with_retry("PATCH", url, v1=v1, json=json, write_intent=True)

    async def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        url = self._build_url(path, v1=v1)
        return await self._request_with_retry(
            "DELETE",
            url,
            v1=v1,
            params=_encode_query_params(params),
            write_intent=True,
        )

    async def upload_file(
        self,
        path: str,
        *,
        files: dict[str, Any],
        data: dict[str, Any] | None = None,
        v1: bool = False,
    ) -> dict[str, Any]:
        """Upload files with multipart form data."""
        url = self._build_url(path, v1=v1)
        client = await self._get_client()

        headers = dict(client.headers)
        headers.pop("Content-Type", None)
        return await self._request_with_retry(
            "POST",
            url,
            v1=v1,
            files=files,
            data=data,
            headers=headers,
            write_intent=True,
        )

    async def download_file(
        self,
        path: str,
        *,
        v1: bool = False,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> bytes:
        """
        Download file content.

        Notes:
        - The initial Affinity API response may redirect to an external signed URL.
          Redirects are followed without forwarding credentials.
        - External signed URLs are protected via ExternalHookPolicy (redaction by default).
        """
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

        url = self._build_url(path, v1=v1)
        context: RequestContext = {}
        if timeout is not None:
            context["timeout"] = timeout
        if deadline_seconds is not None:
            context["deadline_seconds"] = float(deadline_seconds)

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )
        resp = await self._raw_buffered_pipeline(req)
        return resp.content

    async def stream_download(
        self,
        path: str,
        *,
        v1: bool = False,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> AsyncIterator[bytes]:
        """
        Stream-download file content in chunks.

        Notes:
        - The initial Affinity API response may redirect to an external signed URL.
          Redirects are followed without forwarding credentials.
        - External signed URLs are protected via ExternalHookPolicy (redaction by default).
        """
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

        url = self._build_url(path, v1=v1)
        context: RequestContext = {"streaming": True}
        if timeout is not None:
            context["timeout"] = timeout
        if deadline_seconds is not None:
            context["deadline_seconds"] = float(deadline_seconds)
        if on_progress is not None:
            context["on_progress"] = on_progress

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )

        resp = await self._raw_stream_pipeline(req)
        if not isinstance(resp, SDKRawStreamResponse):
            return

        async for chunk in resp.stream.aiter_bytes(chunk_size=chunk_size):
            yield chunk

    async def stream_download_with_info(
        self,
        path: str,
        *,
        v1: bool = False,
        chunk_size: int = 65_536,
        on_progress: ProgressCallback | None = None,
        timeout: httpx.Timeout | float | None = None,
        deadline_seconds: float | None = None,
    ) -> AsyncDownloadedFile:
        """
        Stream-download file content and return response metadata (headers/filename/size).

        Notes:
        - The initial Affinity API response may redirect to an external signed URL.
          Redirects are followed without forwarding credentials.
        - External signed URLs are protected via ExternalHookPolicy (redaction by default).
        """
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise TimeoutError(f"Download deadline exceeded: {deadline_seconds}s")

        url = self._build_url(path, v1=v1)
        context: RequestContext = {"streaming": True}
        if timeout is not None:
            context["timeout"] = timeout
        if deadline_seconds is not None:
            context["deadline_seconds"] = float(deadline_seconds)
        if on_progress is not None:
            context["on_progress"] = on_progress

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )
        resp = await self._raw_stream_pipeline(req)
        if not isinstance(resp, SDKRawStreamResponse):
            info = _download_info_from_headers([])

            async def _empty_iter_bytes() -> AsyncIterator[bytes]:
                if False:
                    yield b""

            return AsyncDownloadedFile(
                headers=info["headers"],
                raw_headers=[],
                content_type=info["content_type"],
                filename=info["filename"],
                size=info["size"],
                iter_bytes=_empty_iter_bytes(),
            )

        info = _download_info_from_headers(resp.headers)

        async def _iter_bytes() -> AsyncIterator[bytes]:
            async for chunk in resp.stream.aiter_bytes(chunk_size=chunk_size):
                yield chunk

        return AsyncDownloadedFile(
            headers=info["headers"],
            raw_headers=list(resp.headers),
            content_type=info["content_type"],
            filename=info["filename"],
            size=info["size"],
            iter_bytes=_iter_bytes(),
        )

    async def get_redirect_url(
        self,
        path: str,
        *,
        v1: bool = False,
        timeout: httpx.Timeout | float | None = None,
    ) -> str | None:
        """
        Get the redirect URL for a download endpoint without following it.

        Makes a GET request and returns the Location header if the response
        is a redirect (3xx). Returns None if not a redirect.

        This is useful for getting presigned URLs from file download endpoints.
        """
        url = self._build_url(path, v1=v1)
        context: RequestContext = {"stop_at_redirect": True}
        if timeout is not None:
            context["timeout"] = timeout

        req = SDKRequest(
            method="GET",
            url=url,
            headers=[("Accept", "*/*")],
            api_version="v1" if v1 else "v2",
            write_intent=False,
            context=context,
        )
        resp = await self._raw_buffered_pipeline(req)
        return resp.context.get("redirect_location")

    def wrap_validation_error(
        self,
        error: Exception,
        *,
        context: str | None = None,
    ) -> VersionCompatibilityError:
        """
        Wrap a validation error with version compatibility context.

        TR-015: If expected_v2_version is configured, validation failures
        are wrapped with actionable guidance about checking API version.
        """
        expected = (
            self._config.expected_v2_version
            or self._config.affinity_api_version
            or self._api_versions.last()
        )
        message = (
            f"Response parsing failed: {error}. "
            "This may indicate a v2 API version mismatch. "
            "Check your API key's Default API Version in the Affinity dashboard."
        )
        if context:
            message = f"[{context}] {message}"
        return VersionCompatibilityError(
            message,
            expected_version=expected,
            parsing_error=str(error),
        )

    @property
    def expected_v2_version(self) -> str | None:
        """Expected V2 API version for diagnostics."""
        return self._config.expected_v2_version
