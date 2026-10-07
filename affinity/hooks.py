"""
Public hook types for request/response instrumentation (DX-008).

Long-term, the preferred API is `on_event(HookEvent)` which can represent retries,
redirects, and streaming lifecycles without ambiguity. The older
`on_request/on_response/on_error` hooks remain available as adapters.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

# =============================================================================
# Legacy 3-hook API (adapters)
# =============================================================================


@dataclass(frozen=True, slots=True)
class RequestInfo:
    """
    Sanitized request metadata for hooks.

    Note: authentication is intentionally excluded.
    """

    method: str
    url: str
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class ResponseInfo:
    """Sanitized response metadata for hooks."""

    status_code: int
    headers: dict[str, str]
    elapsed_ms: float
    request: RequestInfo
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Sanitized error metadata for hooks."""

    error: BaseException
    elapsed_ms: float
    request: RequestInfo


RequestHook: TypeAlias = Callable[[RequestInfo], None]
ResponseHook: TypeAlias = Callable[[ResponseInfo], None]
ErrorHook: TypeAlias = Callable[[ErrorInfo], None]


# =============================================================================
# Event-based hook API
# =============================================================================


@dataclass(frozen=True, slots=True)
class RequestStarted:
    client_request_id: str
    request: RequestInfo
    api_version: Literal["v1", "v2", "external"]
    type: Literal["request_started"] = "request_started"


@dataclass(frozen=True, slots=True)
class RequestRetrying:
    client_request_id: str
    request: RequestInfo
    attempt: int
    wait_seconds: float
    reason: str
    type: Literal["request_retrying"] = "request_retrying"


@dataclass(frozen=True, slots=True)
class RedirectFollowed:
    client_request_id: str
    from_url: str
    to_url: str
    hop: int
    external: bool
    type: Literal["redirect_followed"] = "redirect_followed"


@dataclass(frozen=True, slots=True)
class ResponseHeadersReceived:
    client_request_id: str
    request: RequestInfo
    status_code: int
    headers: list[tuple[str, str]]
    elapsed_ms: float
    external: bool
    cache_hit: bool
    request_id: str | None = None
    type: Literal["response_headers_received"] = "response_headers_received"


@dataclass(frozen=True, slots=True)
class RequestFailed:
    client_request_id: str
    request: RequestInfo
    error: BaseException
    elapsed_ms: float
    external: bool
    type: Literal["request_failed"] = "request_failed"


@dataclass(frozen=True, slots=True)
class RequestSucceeded:
    client_request_id: str
    request: RequestInfo
    status_code: int
    elapsed_ms: float
    external: bool
    type: Literal["request_succeeded"] = "request_succeeded"


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    client_request_id: str
    request: RequestInfo
    bytes_read: int
    bytes_total: int | None
    elapsed_ms: float
    external: bool
    type: Literal["stream_completed"] = "stream_completed"


@dataclass(frozen=True, slots=True)
class StreamAborted:
    client_request_id: str
    request: RequestInfo
    reason: str
    bytes_read: int
    bytes_total: int | None
    elapsed_ms: float
    external: bool
    type: Literal["stream_aborted"] = "stream_aborted"


@dataclass(frozen=True, slots=True)
class StreamFailed:
    client_request_id: str
    request: RequestInfo
    error: BaseException
    bytes_read: int
    bytes_total: int | None
    elapsed_ms: float
    external: bool
    type: Literal["stream_failed"] = "stream_failed"


HookEvent: TypeAlias = (
    RequestStarted
    | RequestRetrying
    | RedirectFollowed
    | ResponseHeadersReceived
    | RequestFailed
    | RequestSucceeded
    | StreamCompleted
    | StreamAborted
    | StreamFailed
)

EventHook: TypeAlias = Callable[[HookEvent], None]
AsyncEventHook: TypeAlias = Callable[[HookEvent], Awaitable[None]]
AnyEventHook: TypeAlias = Callable[[HookEvent], Awaitable[None] | None]


__all__ = [
    # Legacy 3-hook API
    "RequestInfo",
    "ResponseInfo",
    "ErrorInfo",
    "RequestHook",
    "ResponseHook",
    "ErrorHook",
    # Event-based API
    "HookEvent",
    "EventHook",
    "AsyncEventHook",
    "AnyEventHook",
    "RequestStarted",
    "RequestRetrying",
    "RedirectFollowed",
    "ResponseHeadersReceived",
    "RequestFailed",
    "RequestSucceeded",
    "StreamCompleted",
    "StreamAborted",
    "StreamFailed",
]
