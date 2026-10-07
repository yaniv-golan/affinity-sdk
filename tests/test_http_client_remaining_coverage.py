from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from affinity.clients.http import (
    AsyncHTTPClient,
    ClientConfig,
    HTTPClient,
    RateLimitState,
    _default_port,
    _safe_follow_url,
)
from affinity.exceptions import (
    AffinityError,
    NetworkError,
    RateLimitError,
    TimeoutError,
    UnsafeUrlError,
    VersionCompatibilityError,
)


def test_default_port_returns_none_for_unknown_scheme() -> None:
    assert _default_port("ftp") is None


def test_safe_follow_url_rejects_wrong_scheme_when_port_matches() -> None:
    with pytest.raises(UnsafeUrlError):
        _safe_follow_url(
            "http://api.affinity.co:443/v2/companies",
            v1_base_url="https://api.affinity.co",
            v2_base_url="https://api.affinity.co/v2",
        )


def test_rate_limit_state_reads_org_reset_header() -> None:
    state = RateLimitState()
    state.update_from_headers(httpx.Headers({"X-Ratelimit-Limit-Org-Reset": "123"}))
    assert state.org_reset_seconds == 123


def test_handle_response_covers_non_json_error_body_empty_and_scalar_payload() -> None:
    http = HTTPClient(ClientConfig(api_key="k", max_retries=0))
    try:
        req = httpx.Request("GET", "https://v2.example/v2/x")

        # status>=400 + invalid JSON body => except path
        with pytest.raises(AffinityError):
            http._handle_response(
                httpx.Response(400, content=b"{not-json", request=req),
                method="GET",
                url=str(req.url),
                v1=False,
            )

        # empty response => {}
        assert (
            http._handle_response(
                httpx.Response(204, content=b"", request=req),
                method="GET",
                url=str(req.url),
                v1=False,
            )
            == {}
        )

        # scalar JSON => error
        with pytest.raises(AffinityError):
            http._handle_response(
                httpx.Response(200, json=True, request=req),
                method="GET",
                url=str(req.url),
                v1=False,
            )
    finally:
        http.close()


def test_download_file_rate_limit_retries_with_retry_after(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("affinity.clients.http.time.sleep", sleeps.append)

    calls: dict[str, int] = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                json={"message": "rate"},
                headers={"Retry-After": "2"},
                request=request,
            )
        return httpx.Response(200, content=b"ok", request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=0.0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert http.download_file("/x") == b"ok"
        assert sleeps == [2.0]
    finally:
        http.close()


def test_download_file_timeout_network_and_no_attempts_paths(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    http_timeout = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(timeout_handler),
        )
    )
    try:
        with pytest.raises(TimeoutError):
            _ = http_timeout.download_file("/x")
    finally:
        http_timeout.close()

    def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    http_network = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(network_handler),
        )
    )
    try:
        with pytest.raises(NetworkError):
            _ = http_network.download_file("/x")
    finally:
        http_network.close()

    http_none = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=-1,
        )
    )
    try:
        with pytest.raises(AffinityError, match="Request failed after retries"):
            _ = http_none.download_file("/x")
    finally:
        http_none.close()


def test_request_with_retry_safe_follow_redirect_block_and_get_v1_page_variants() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v2.example/v2/redirect"):
            return httpx.Response(
                302, headers={"Location": "https://v2.example/v2/ok"}, request=request
            )
        if request.url == httpx.URL("https://v1.example/notes?page_size=1"):
            return httpx.Response(200, json={"data": []}, request=request)
        if request.url == httpx.URL("https://v1.example/notes?page_size=1&page_token=t"):
            return httpx.Response(200, json={"data": []}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

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
            http.get_url("https://v2.example/v2/redirect")

        assert http.get_v1_page("/notes", signature=[("page_size", "1")]) == {"data": []}
        assert http.get_v1_page("/notes", signature=[("page_size", "1")], page_token="t") == {
            "data": []
        }
    finally:
        http.close()


def test_download_file_redirect_location_missing_and_unsafe_schemes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/1"):
            return httpx.Response(302, headers={}, content=b"redirect", request=request)
        if request.url == httpx.URL("https://v1.example/entity-files/download/2"):
            return httpx.Response(
                302, headers={"Location": "ftp://files.example/x"}, request=request
            )
        if request.url == httpx.URL("https://v1.example/entity-files/download/3"):
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
        assert http.download_file("/entity-files/download/1", v1=True) == b"redirect"

        with pytest.raises(UnsafeUrlError):
            _ = http.download_file("/entity-files/download/2", v1=True)

        with pytest.raises(UnsafeUrlError):
            _ = http.download_file("/entity-files/download/3", v1=True)
    finally:
        http.close()


def test_download_file_allows_insecure_redirect_when_opted_in() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/3"):
            return httpx.Response(
                302, headers={"Location": "http://files.example/x"}, request=request
            )
        if request.url == httpx.URL("http://files.example/x"):
            assert request.headers.get("Authorization") is None
            return httpx.Response(200, content=b"x", request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            allow_insecure_download_redirects=True,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert http.download_file("/entity-files/download/3", v1=True) == b"x"
    finally:
        http.close()


def test_stream_download_allows_insecure_redirect_when_opted_in() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/5"):
            return httpx.Response(
                302, headers={"Location": "http://files.example/x"}, request=request
            )
        if request.url == httpx.URL("http://files.example/x"):
            assert request.headers.get("Authorization") is None
            return httpx.Response(
                200, content=b"ab", headers={"Content-Length": "2"}, request=request
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            allow_insecure_download_redirects=True,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert (
            b"".join(http.stream_download("/entity-files/download/5", v1=True, chunk_size=1))
            == b"ab"
        )
    finally:
        http.close()


def test_stream_download_non_redirect_no_progress_non_digit_length_and_progress_digit_length(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/1"):
            return httpx.Response(
                200, content=b"abcd", headers={"Content-Length": "nope"}, request=request
            )
        if request.url == httpx.URL("https://v1.example/entity-files/download/2"):
            return httpx.Response(
                200, content=b"abcd", headers={"Content-Length": "4"}, request=request
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
        assert (
            b"".join(http.stream_download("/entity-files/download/1", v1=True, chunk_size=2))
            == b"abcd"
        )

        progress: list[tuple[int, int | None, str]] = []
        assert (
            b"".join(
                http.stream_download(
                    "/entity-files/download/2",
                    v1=True,
                    chunk_size=2,
                    on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
                )
            )
            == b"abcd"
        )
        assert progress[0] == (0, 4, "download")
        assert progress[-1] == (4, 4, "download")
    finally:
        http.close()


def test_stream_download_redirect_progress_and_error_paths(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("affinity.clients.http.time.sleep", sleeps.append)
    monkeypatch.setattr("affinity.clients.http.time.time_ns", lambda: 500_000)

    calls: dict[str, int] = {"external": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/5"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            calls["external"] += 1
            if calls["external"] == 1:
                return httpx.Response(500, json={"message": "server"}, request=request)
            if calls["external"] == 2:
                raise httpx.ReadTimeout("boom", request=request)
            return httpx.Response(
                200, content=b"x", headers={"Content-Length": "1"}, request=request
            )
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=2,
            retry_delay=1.0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        progress: list[tuple[int, int | None, str]] = []
        data = b"".join(
            http.stream_download(
                "/entity-files/download/5",
                v1=True,
                chunk_size=1,
                on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
            )
        )
        assert data == b"x"
        assert sleeps == [0.5, 1.0]
        assert progress[0] == (0, 1, "download")
        assert progress[-1] == (1, 1, "download")
    finally:
        http.close()


def test_request_with_retry_retries_rate_limit_and_sleeps(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("affinity.clients.http.time.sleep", sleeps.append)

    debug_calls: list[str] = []
    monkeypatch.setattr("affinity.clients.http.logger.debug", debug_calls.append)

    calls: dict[str, int] = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, json={"message": "rate"}, headers={"Retry-After": "1"}, request=request
            )
        return httpx.Response(200, json={"ok": True}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=1.0,
            log_requests=True,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert http.get("/x") == {"ok": True}
        assert sleeps == [1.0]
        assert debug_calls == ["GET https://v2.example/v2/x", "GET https://v2.example/v2/x"]
    finally:
        http.close()


def test_request_with_retry_non_retryable_method_raises_rate_limit(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate"}, request=request)

    http = HTTPClient(
        ClientConfig(api_key="k", max_retries=1, transport=httpx.MockTransport(handler))
    )
    try:
        with pytest.raises(RateLimitError):
            http._request_with_retry("POST", "https://v2.example/v2/x", v1=False)
    finally:
        http.close()


def test_request_with_retry_server_error_breaks_when_exhausted(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "server"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        with pytest.raises(AffinityError) as excinfo:
            _ = http.get("/x")
        assert excinfo.value.status_code == 500
    finally:
        http.close()


@pytest.mark.parametrize(
    ("exc", "_expected"),
    [
        (
            httpx.ReadTimeout("boom", request=httpx.Request("GET", "https://v2.example/v2/x")),
            TimeoutError,
        ),
        (
            httpx.ConnectError("boom", request=httpx.Request("GET", "https://v2.example/v2/x")),
            NetworkError,
        ),
    ],
)
def test_request_with_retry_timeout_and_network_retry_sleep_and_succeed(
    monkeypatch: Any, exc: Exception, _expected: type[Exception]
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("affinity.clients.http.time.sleep", sleeps.append)
    monkeypatch.setattr("affinity.clients.http.time.time_ns", lambda: 500_000)

    calls: dict[str, int] = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise exc
        return httpx.Response(200, json={"ok": True}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=1.0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert http.get("/x") == {"ok": True}
        assert sleeps == [0.5]
    finally:
        http.close()


def test_request_with_retry_timeout_and_network_non_retryable_method_wrap(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    http_timeout = HTTPClient(
        ClientConfig(api_key="k", max_retries=1, transport=httpx.MockTransport(timeout_handler))
    )
    try:
        with pytest.raises(TimeoutError) as excinfo:
            http_timeout._request_with_retry("POST", "https://v2.example/v2/x", v1=False)
        assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)
    finally:
        http_timeout.close()

    def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    http_network = HTTPClient(
        ClientConfig(api_key="k", max_retries=1, transport=httpx.MockTransport(network_handler))
    )
    try:
        with pytest.raises(NetworkError) as excinfo:
            http_network._request_with_retry("POST", "https://v2.example/v2/x", v1=False)
        assert isinstance(excinfo.value.__cause__, httpx.NetworkError)
    finally:
        http_network.close()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            httpx.ReadTimeout("boom", request=httpx.Request("GET", "https://v2.example/v2/x")),
            TimeoutError,
        ),
        (
            httpx.ConnectError("boom", request=httpx.Request("GET", "https://v2.example/v2/x")),
            NetworkError,
        ),
    ],
)
def test_request_with_retry_timeout_and_network_break_when_exhausted(
    monkeypatch: Any, exc: Exception, expected: type[Exception]
) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        with pytest.raises(expected) as excinfo:
            _ = http.get("/x")
        if expected is TimeoutError:
            assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)
        else:
            assert isinstance(excinfo.value.__cause__, httpx.NetworkError)
    finally:
        http.close()


def test_request_with_retry_no_attempts_raises_request_failed() -> None:
    http = HTTPClient(ClientConfig(api_key="k", max_retries=-1))
    try:
        with pytest.raises(AffinityError, match="Request failed after retries"):
            http._request_with_retry("GET", "https://v2.example/v2/x", v1=False)
    finally:
        http.close()


def test_download_file_redirect_to_https_succeeds_without_forwarding_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            assert request.headers.get("Authorization") is None
            return httpx.Response(200, content=b"file", request=request)
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
        assert http.download_file("/entity-files/download/10", v1=True) == b"file"
    finally:
        http.close()


def test_download_file_no_redirect_returns_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/11"):
            return httpx.Response(200, content=b"file", request=request)
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
        assert http.download_file("/entity-files/download/11", v1=True) == b"file"
    finally:
        http.close()


def test_stream_download_progress_reports_none_total_when_content_length_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                200, content=b"abcd", headers={"Content-Length": ""}, request=request
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
        assert (
            b"".join(
                http.stream_download(
                    "/entity-files/download/10",
                    v1=True,
                    chunk_size=2,
                    on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
                )
            )
            == b"abcd"
        )
        assert progress[0] == (0, None, "download")
        assert progress[-1] == (4, None, "download")
    finally:
        http.close()


def test_stream_download_redirect_progress_none_total_when_content_length_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(
                200, content=b"x", headers={"Content-Length": ""}, request=request
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
        assert (
            b"".join(
                http.stream_download(
                    "/entity-files/download/10",
                    v1=True,
                    chunk_size=1,
                    on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
                )
            )
            == b"x"
        )
        assert progress[0] == (0, None, "download")
        assert progress[-1] == (1, None, "download")
    finally:
        http.close()


def test_stream_download_redirect_rate_limit_after_partial_yield_is_not_retried() -> None:
    class ExplodingRateLimitStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"a"
            raise RateLimitError("rate", retry_after=1, status_code=429)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(200, stream=ExplodingRateLimitStream(), request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        it = http.stream_download("/entity-files/download/10", v1=True, chunk_size=1)
        assert next(it) == b"a"
        with pytest.raises(RateLimitError):
            _ = list(it)
    finally:
        http.close()


def test_stream_download_redirect_rate_limit_breaks_when_exhausted(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(429, json={"message": "rate"}, request=request)
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
        with pytest.raises(RateLimitError):
            _ = b"".join(http.stream_download("/entity-files/download/10", v1=True))
    finally:
        http.close()


def test_stream_download_redirect_server_error_breaks_when_exhausted(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(500, json={"message": "server"}, request=request)
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
        with pytest.raises(AffinityError) as excinfo:
            _ = b"".join(http.stream_download("/entity-files/download/10", v1=True))
        assert excinfo.value.status_code == 500
    finally:
        http.close()


def test_stream_download_redirect_timeout_breaks_when_exhausted(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    class TimeoutStream(httpx.SyncByteStream):
        def __init__(self, request: httpx.Request):
            self._request = request

        def __iter__(self) -> Iterator[bytes]:
            raise httpx.ReadTimeout("boom", request=self._request)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(200, stream=TimeoutStream(request), request=request)
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
        with pytest.raises(TimeoutError) as excinfo:
            _ = b"".join(http.stream_download("/entity-files/download/10", v1=True))
        assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)
    finally:
        http.close()


def test_stream_download_redirect_network_errors_retry_then_succeed(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("affinity.clients.http.time.sleep", sleeps.append)
    monkeypatch.setattr("affinity.clients.http.time.time_ns", lambda: 500_000)

    calls: dict[str, int] = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, content=b"x", request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=1.0,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        assert b"".join(http.stream_download("/entity-files/download/10", v1=True)) == b"x"
        assert sleeps == [0.5]
    finally:
        http.close()


def test_stream_download_redirect_network_error_breaks_when_exhausted(monkeypatch: Any) -> None:
    monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/10"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            raise httpx.ConnectError("boom", request=request)
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
        with pytest.raises(NetworkError) as excinfo:
            _ = b"".join(http.stream_download("/entity-files/download/10", v1=True))
        assert isinstance(excinfo.value.__cause__, httpx.NetworkError)
    finally:
        http.close()


def test_stream_download_redirect_network_error_after_partial_yield_is_not_retried() -> None:
    class ExplodingNetworkStream(httpx.SyncByteStream):
        def __init__(self, request: httpx.Request):
            self._request = request

        def __iter__(self) -> Iterator[bytes]:
            yield b"ab"
            raise httpx.ConnectError("boom", request=self._request)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/entity-files/download/12"):
            return httpx.Response(
                302, headers={"Location": "https://files.example/content.bin"}, request=request
            )
        if request.url == httpx.URL("https://files.example/content.bin"):
            return httpx.Response(200, stream=ExplodingNetworkStream(request), request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        it = http.stream_download("/entity-files/download/12", v1=True, chunk_size=2)
        assert next(it) == b"ab"
        with pytest.raises(NetworkError):
            _ = list(it)
    finally:
        http.close()


def test_stream_download_redirect_no_attempts_raises_download_failed() -> None:
    http = HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=-1,
        )
    )
    try:
        with pytest.raises(AffinityError, match="Request failed after retries"):
            _ = b"".join(http.stream_download("/entity-files/download/10", v1=True))
    finally:
        http.close()


@pytest.mark.filterwarnings("ignore:expected_v2_version is deprecated")
def test_expected_v2_version_property_and_async_wrap_validation_error() -> None:
    http = HTTPClient(ClientConfig(api_key="k", expected_v2_version="2024-01-01"))
    try:
        assert http.expected_v2_version == "2024-01-01"
    finally:
        http.close()

    async_http = AsyncHTTPClient(ClientConfig(api_key="k", expected_v2_version="2024-01-01"))
    err = ValueError("bad")
    wrapped = async_http.wrap_validation_error(err)
    assert isinstance(wrapped, VersionCompatibilityError)
    assert wrapped.expected_version == "2024-01-01"
    assert async_http.wrap_validation_error(err, context="ctx").args[0].startswith("[ctx]")
    assert async_http.expected_v2_version == "2024-01-01"


def test_async_handle_response_parses_retry_after_header() -> None:
    client = AsyncHTTPClient(ClientConfig(api_key="k"))
    req = httpx.Request("GET", "https://v2.example/v2/x")
    response = httpx.Response(
        429,
        json={"message": "rate"},
        headers={"Retry-After": "60"},
        request=req,
    )
    with pytest.raises(RateLimitError) as excinfo:
        _ = client._handle_response(response, method="GET", url=str(req.url), v1=False)
    assert excinfo.value.retry_after == 60


@pytest.mark.asyncio
async def test_async_request_with_retry_rate_limit_retry_and_non_retryable_method(
    monkeypatch: Any,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("affinity.clients.http.asyncio.sleep", fake_sleep)

    debug_calls: list[str] = []
    monkeypatch.setattr("affinity.clients.http.logger.debug", debug_calls.append)

    calls: dict[str, int] = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url == httpx.URL("https://v2.example/v2/x") and calls["n"] == 1:
            return httpx.Response(
                429, json={"message": "rate"}, headers={"Retry-After": "1"}, request=request
            )
        if request.url == httpx.URL("https://v2.example/v2/x"):
            return httpx.Response(200, json={"ok": True}, request=request)
        if request.url == httpx.URL("https://v2.example/v2/post"):
            return httpx.Response(429, json={"message": "rate"}, request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    async with AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=1.0,
            log_requests=True,
            async_transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert await client.get("/x") == {"ok": True}
        assert sleeps == [1.0]
        assert debug_calls == ["GET https://v2.example/v2/x", "GET https://v2.example/v2/x"]

        with pytest.raises(RateLimitError):
            await client.post("/post", json={"x": 1})


@pytest.mark.asyncio
async def test_async_request_with_retry_server_error_timeout_network_paths(
    monkeypatch: Any,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("affinity.clients.http.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("affinity.clients.http.time.time_ns", lambda: 500_000)

    calls: dict[str, int] = {"server": 0, "timeout": 0, "network": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v2.example/v2/server"):
            calls["server"] += 1
            if calls["server"] == 1:
                return httpx.Response(500, json={"message": "server"}, request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        if request.url == httpx.URL("https://v2.example/v2/timeout"):
            calls["timeout"] += 1
            if calls["timeout"] == 1:
                raise httpx.ReadTimeout("boom", request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        if request.url == httpx.URL("https://v2.example/v2/network"):
            calls["network"] += 1
            if calls["network"] == 1:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        return httpx.Response(404, json={"message": "not found"}, request=request)

    async with AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=1,
            retry_delay=1.0,
            async_transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert await client.get("/server") == {"ok": True}
        assert await client.get("/timeout") == {"ok": True}
        assert await client.get("/network") == {"ok": True}
        assert sleeps == [0.5, 0.5, 0.5]


@pytest.mark.asyncio
async def test_async_get_v1_page_patch_and_request_failed_after_no_attempts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v1.example/notes?page_size=1"):
            return httpx.Response(200, json={"data": []}, request=request)
        if request.url == httpx.URL("https://v1.example/notes?page_size=1&page_token=t"):
            return httpx.Response(200, json={"data": []}, request=request)
        if request.url == httpx.URL("https://v2.example/v2/x"):
            return httpx.Response(200, json={"ok": True}, request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    async with AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert await client.get_v1_page("/notes", signature=[("page_size", "1")]) == {"data": []}
        assert await client.get_v1_page(
            "/notes", signature=[("page_size", "1")], page_token="t"
        ) == {"data": []}
        assert await client.patch("/x", json={"a": 1}) == {"ok": True}

    async with AsyncHTTPClient(
        ClientConfig(api_key="k", v2_base_url="https://v2.example/v2", max_retries=-1)
    ) as client_no_attempts:
        with pytest.raises(AffinityError, match="Request failed after retries"):
            await client_no_attempts.get("/x")


@pytest.mark.asyncio
async def test_async_request_with_retry_exhausted_and_non_retryable_timeout_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://v2.example/v2/server"):
            return httpx.Response(500, json={"message": "server"}, request=request)
        if request.url == httpx.URL("https://v2.example/v2/timeout"):
            raise httpx.ReadTimeout("boom", request=request)
        if request.url == httpx.URL("https://v2.example/v2/network"):
            raise httpx.ConnectError("boom", request=request)
        if request.url == httpx.URL("https://v2.example/v2/timeout-post"):
            raise httpx.ReadTimeout("boom", request=request)
        if request.url == httpx.URL("https://v2.example/v2/network-post"):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    async with AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    ) as client:
        with pytest.raises(AffinityError) as excinfo:
            await client.get("/server")
        assert excinfo.value.status_code == 500

        with pytest.raises(TimeoutError) as excinfo:
            await client.get("/timeout")
        assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)

        with pytest.raises(NetworkError) as excinfo:
            await client.get("/network")
        assert isinstance(excinfo.value.__cause__, httpx.NetworkError)

        with pytest.raises(TimeoutError) as excinfo:
            await client.post("/timeout-post", json={"x": 1})
        assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)

        with pytest.raises(NetworkError) as excinfo:
            await client.post("/network-post", json={"x": 1})
        assert isinstance(excinfo.value.__cause__, httpx.NetworkError)


@pytest.mark.asyncio
async def test_async_http_client_basic_auth_context_manager_and_error_handling(
    monkeypatch: Any,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("affinity.clients.http.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        # Basic auth should apply to v1 requests.
        if request.url == httpx.URL("https://v1.example/notes"):
            assert request.headers.get("Authorization", "").startswith("Basic ")
            return httpx.Response(429, json={"message": "rate"}, request=request)
        if request.url == httpx.URL("https://v2.example/v2/list"):
            return httpx.Response(200, json=[{"x": 1}], request=request)
        if request.url == httpx.URL("https://v2.example/v2/empty"):
            return httpx.Response(204, content=b"", request=request)
        if request.url == httpx.URL("https://v2.example/v2/scalar"):
            return httpx.Response(200, json=True, request=request)
        if request.url == httpx.URL("https://v2.example/v2/bad"):
            return httpx.Response(400, content=b"{not-json", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async with AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_auth_mode="basic",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    ) as client:
        assert client.cache is None
        _ = client.rate_limit_state
        _ = client.enable_beta_endpoints

        with pytest.raises(RateLimitError):
            await client.get("/notes", v1=True)

        assert await client.get("/empty") == {}
        assert await client.get("/list") == {"data": [{"x": 1}]}
        with pytest.raises(AffinityError):
            await client.get("/scalar")
        with pytest.raises(AffinityError):
            await client.get("/bad")
