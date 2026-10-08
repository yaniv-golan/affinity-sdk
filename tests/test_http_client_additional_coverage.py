from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from enum import Enum
from typing import Any

import httpx
import pytest

from affinity.clients.http import (
    AsyncHTTPClient,
    ClientConfig,
    HTTPClient,
    RateLimitState,
    SimpleCache,
    _encode_query_params,
    _parse_retry_after,
    _redact_url,
    _safe_follow_url,
)
from affinity.exceptions import (
    TimeoutError,
    UnsafeUrlError,
)


def test_encode_query_params_supports_mapping_and_sequence_and_skips_none() -> None:
    class Color(Enum):
        RED = "red"

    assert _encode_query_params(
        {
            "fieldIds": "123",
            "term": "x",
            "color": Color.RED,
            "ignore": None,
        }
    ) == [
        ("color", "red"),
        ("fieldIds", "123"),
        ("term", "x"),
    ]

    assert _encode_query_params([("a", 1), ("b", "x")]) == [("a", "1"), ("b", "x")]


def test_parse_retry_after_handles_edge_cases_and_naive_http_date() -> None:
    assert _parse_retry_after("") is None
    assert _parse_retry_after("   ") is None
    assert _parse_retry_after("nope") is None
    assert _parse_retry_after("60") == 60

    naive_http_date = "Wed, 21 Oct 2015 07:28:00"
    parsed = _parse_retry_after(naive_http_date)
    assert parsed is not None
    assert parsed >= 0


def test_safe_follow_url_rejects_wrong_scheme() -> None:
    with pytest.raises(UnsafeUrlError):
        _safe_follow_url(
            "http://api.affinity.co/v2/companies",
            v1_base_url="https://api.affinity.co",
            v2_base_url="https://api.affinity.co/v2",
        )


def test_redact_url_redacts_sensitive_query_keys_and_bare_pairs() -> None:
    secret = "super-secret"
    url = f"https://api.affinity.co/v2/x?token=abc&x=1&flag&api_key={secret}&q={secret}"
    redacted = _redact_url(url, secret)
    assert "token=[REDACTED]" in redacted
    assert "api_key=[REDACTED]" in redacted
    assert "x=1" in redacted
    assert "flag" in redacted
    assert secret not in redacted


def test_rate_limit_state_seconds_until_user_reset_counts_down(monkeypatch: Any) -> None:
    state = RateLimitState()
    state.user_reset_seconds = 60
    state.last_updated = 1000.0
    monkeypatch.setattr("affinity.clients.http.time.time", lambda: 1020.0)
    assert state.seconds_until_user_reset == 40.0


def test_simple_cache_delete_and_expiry_removes_key(monkeypatch: Any) -> None:
    cache = SimpleCache(default_ttl=10.0)

    t = {"now": 1000.0}
    monkeypatch.setattr("affinity.clients.http.time.time", lambda: t["now"])

    cache.set("k", {"x": 1}, ttl=1.0)
    assert cache.get("k") == {"x": 1}

    t["now"] = 1002.0
    assert cache.get("k") is None

    cache.set("k2", {"y": 2})
    cache.delete("k2")
    assert cache.get("k2") is None


def test_http_client_context_manager_calls_close() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, request=request)

    with HTTPClient(
        ClientConfig(api_key="k", max_retries=0, transport=httpx.MockTransport(handler))
    ) as client:
        assert client.get("/auth/whoami")["ok"] is True


def test_stream_download_without_redirect_reports_progress() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/5"):
            return httpx.Response(
                200, content=b"hello-world", headers={"Content-Length": "11"}, request=request
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        progress: list[tuple[int, int | None, str]] = []
        chunks = list(
            http.stream_download(
                "/entity-files/download/5",
                v1=True,
                chunk_size=4,
                on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
            )
        )
        assert b"".join(chunks) == b"hello-world"
        assert progress[0] == (0, 11, "download")
        assert progress[-1] == (11, 11, "download")
    finally:
        http.close()


def test_stream_download_redirect_missing_location_yields_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/5"):
            return httpx.Response(302, headers={}, request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert list(http.stream_download("/entity-files/download/5", v1=True)) == []
    finally:
        http.close()


def test_stream_download_redirect_retries_then_succeeds(monkeypatch: Any) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("affinity.clients.http.time.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url == httpx.URL("https://v1.example/entity-files/download/5"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if (
            request.url == httpx.URL("https://files.example/content.bin")
            and calls.count("https://files.example/content.bin") == 1
        ):
            retry_after = format_datetime(
                datetime.now(timezone.utc) + timedelta(seconds=1), usegmt=True
            )
            return httpx.Response(
                429,
                json={"message": "rate limit"},
                headers={"Retry-After": retry_after},
                request=request,
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(
                200, content=b"ok", headers={"Content-Length": "2"}, request=request
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=0.0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        data = b"".join(http.stream_download("/entity-files/download/5", v1=True))
        assert data == b"ok"
        assert len(sleeps) == 1
        assert sleeps[0] >= 0
    finally:
        http.close()


def test_stream_download_redirect_rejects_non_https() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/5"):
            return httpx.Response(
                302, headers={"Location": "http://files.example/x"}, request=request
            )
        if request.url == httpx.URL("http://files.example/x"):
            return httpx.Response(200, content=b"x", request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        with pytest.raises(UnsafeUrlError):
            _ = b"".join(http.stream_download("/entity-files/download/5", v1=True))
    finally:
        http.close()


def test_stream_download_redirect_timeout_after_partial_yield_is_not_retried() -> None:
    class ExplodingStream(httpx.SyncByteStream):
        def __init__(self, request: httpx.Request):
            self._request = request

        def __iter__(self) -> Iterator[bytes]:
            yield b"ab"
            raise httpx.ReadTimeout("boom", request=self._request)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/5"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(
                200,
                stream=ExplodingStream(request),
                headers={"Content-Length": "4"},
                request=request,
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=0.0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        it = http.stream_download("/entity-files/download/5", v1=True, chunk_size=2)
        assert next(it) == b"ab"
        with pytest.raises(TimeoutError):
            _ = list(it)
    finally:
        http.close()


def test_download_file_calls_hooks_and_redacts_auth(monkeypatch: Any) -> None:
    events: dict[str, Any] = {"on_request": None, "on_response": None}

    def on_request(info: Any) -> None:
        events["on_request"] = info

    def on_response(info: Any) -> None:
        events["on_response"] = info

    debug_calls: list[str] = []

    def fake_debug(msg: str) -> None:
        debug_calls.append(msg)

    monkeypatch.setattr("affinity.clients.http.logger.debug", fake_debug)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization", "").startswith("Basic ")
        return httpx.Response(200, content=b"ok", request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_auth_mode="basic",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            log_requests=True,
            on_request=on_request,
            on_response=on_response,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        data = http.download_file("/auth/whoami", v1=True)
        assert data == b"ok"
        assert debug_calls == ["GET https://v1.example/auth/whoami"]

        req_info = events["on_request"]
        assert req_info is not None
        assert "Authorization" not in req_info.headers

        resp_info = events["on_response"]
        assert resp_info is not None
        assert resp_info.status_code == 200
    finally:
        http.close()


@pytest.mark.synthetic_http
@pytest.mark.asyncio
async def test_async_http_client_hooks_cache_and_safe_follow_redirect_block(
    monkeypatch: Any,
) -> None:
    events: dict[str, Any] = {"on_request": 0, "on_response": 0}
    response_cache_hits: list[bool] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("affinity.clients.http.asyncio.sleep", fake_sleep)

    def on_request(_info: Any) -> None:
        events["on_request"] += 1

    def on_response(_info: Any) -> None:
        events["on_response"] += 1
        response_cache_hits.append(bool(getattr(_info, "cache_hit", False)))

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url == httpx.URL("https://v2.example/v2/companies"):
            return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)
        if request.url == httpx.URL("https://v2.example/v2/paged"):
            return httpx.Response(
                302, headers={"Location": "https://v2.example/v2/paged?page=2"}, request=request
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            enable_cache=True,
            on_request=on_request,
            on_response=on_response,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        first = await client.get("/companies", cache_key="k")
        second = await client.get("/companies", cache_key="k")
        assert first == second
        assert calls.count("https://v2.example/v2/companies") == 1
        assert events["on_request"] == 2
        assert events["on_response"] == 2
        assert response_cache_hits.count(True) == 1

        with pytest.raises(UnsafeUrlError):
            await client.get_url("https://v2.example/v2/paged")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_http_client_on_error_hook_is_called_on_cancellation() -> None:
    events: list[str] = []

    def on_error(_info: Any) -> None:
        events.append("error")

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError()

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            max_retries=0,
            on_error=on_error,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await client.get("/companies")
        assert events == ["error"]
    finally:
        await client.close()
