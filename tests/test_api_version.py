"""Affinity V2 API version pinning (``X-Affinity-Api-Version``) and echo tracking."""

from __future__ import annotations

import asyncio
import warnings

import httpx
import pytest

from affinity import Affinity, AsyncAffinity
from affinity.api_versions import (
    AFFINITY_API_VERSION_HEADER,
    KNOWN_AFFINITY_API_VERSIONS,
    normalize_affinity_api_version,
)
from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.exceptions import (
    ConfigurationError,
    UnsupportedApiVersionError,
    ValidationError,
    VersionCompatibilityError,
)

V2 = "https://v2.example/v2"
V1 = "https://v1.example"


def _recording_handler(
    seen: list[httpx.Request],
    *,
    echo: str | None = "2024-01-01",
    echo_requested: bool = True,
) -> object:
    """Mock handler: V2 lists page 1 -> nextUrl page 2; V1 whoami; echoes the version."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers: dict[str, str] = {}
        url = str(request.url)
        if url.startswith(V2):
            requested = request.headers.get(AFFINITY_API_VERSION_HEADER)
            value = requested if (echo_requested and requested) else echo
            if value == "current":
                value = "2026-09-17"
            if value is not None:
                headers[AFFINITY_API_VERSION_HEADER] = value
        if url == f"{V2}/companies":
            return httpx.Response(
                200,
                json={"data": [{"id": 1}], "pagination": {"nextUrl": f"{V2}/companies?cursor=p2"}},
                headers=headers,
            )
        if url == f"{V2}/companies?cursor=p2":
            return httpx.Response(
                200, json={"data": [{"id": 2}], "pagination": {"nextUrl": None}}, headers=headers
            )
        if url == f"{V2}/companies/fields":
            return httpx.Response(200, json={"data": []}, headers=headers)
        if url == f"{V2}/auth/whoami":
            return httpx.Response(200, json={"user": {}}, headers=headers)
        if url == f"{V1}/auth/whoami":
            return httpx.Response(200, json={"user": {}})
        return httpx.Response(404, json={"message": "not found"})

    return handler


def _http(handler: object, **kwargs: object) -> HTTPClient:
    return HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )
    )


def _async_http(handler: object, **kwargs: object) -> AsyncHTTPClient:
    return AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            async_transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )
    )


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------


class TestNormalize:
    @pytest.mark.parametrize("value", KNOWN_AFFINITY_API_VERSIONS)
    def test_known_versions(self, value: str) -> None:
        assert normalize_affinity_api_version(value) == (value, None)

    def test_strips_whitespace(self) -> None:
        assert normalize_affinity_api_version("  2026-09-17\n") == ("2026-09-17", None)

    @pytest.mark.parametrize("value", ["current", "CURRENT", " Current "])
    def test_current(self, value: str) -> None:
        assert normalize_affinity_api_version(value) == ("current", None)

    @pytest.mark.parametrize("value", [None, "", "  ", "auto", "AUTO", "key-default", "default"])
    def test_key_default_means_no_header(self, value: str | None) -> None:
        assert normalize_affinity_api_version(value) == (None, None)

    def test_unknown_date_accepted_with_warning(self) -> None:
        version, warning = normalize_affinity_api_version("2027-01-01")
        assert version == "2027-01-01"
        assert warning is not None and "2027-01-01" in warning

    @pytest.mark.parametrize("value", ["garbage", "2026-13-45", "2026-9-17", "v2", "latest"])
    def test_invalid_rejected(self, value: str) -> None:
        with pytest.raises(ConfigurationError, match="Invalid Affinity API version"):
            normalize_affinity_api_version(value)

    def test_client_config_rejects_invalid(self) -> None:
        with pytest.raises(ConfigurationError):
            ClientConfig(api_key="k", affinity_api_version="garbage")

    def test_client_config_warns_on_unknown_date(self) -> None:
        with pytest.warns(UserWarning, match="Unknown Affinity API version"):
            config = ClientConfig(api_key="k", affinity_api_version="2027-01-01")
        assert config.affinity_api_version == "2027-01-01"

    def test_client_config_normalizes(self) -> None:
        assert ClientConfig(api_key="k", affinity_api_version=" auto ").affinity_api_version is None
        assert (
            ClientConfig(api_key="k", affinity_api_version="Current").affinity_api_version
            == "current"
        )


# ---------------------------------------------------------------------------
# Header injection
# ---------------------------------------------------------------------------


class TestHeaderSync:
    def test_header_on_v2_including_next_url_page(self) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen), affinity_api_version="2026-09-17")
        try:
            result = http.get_all_pages("/companies")
        finally:
            http.close()
        assert [item["id"] for item in result["data"]] == [1, 2]
        assert len(seen) == 2
        assert all(r.headers.get(AFFINITY_API_VERSION_HEADER) == "2026-09-17" for r in seen)

    def test_no_header_by_default(self) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen))
        try:
            http.get("/companies")
        finally:
            http.close()
        assert AFFINITY_API_VERSION_HEADER not in seen[0].headers

    @pytest.mark.parametrize("value", ["auto", "key-default", None])
    def test_auto_sends_no_header(self, value: str | None) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen), affinity_api_version=value)
        try:
            http.get("/companies")
        finally:
            http.close()
        assert AFFINITY_API_VERSION_HEADER not in seen[0].headers

    def test_never_on_v1(self) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen), affinity_api_version="2026-09-17")
        try:
            http.get("/auth/whoami", v1=True)
        finally:
            http.close()
        assert str(seen[0].url) == f"{V1}/auth/whoami"
        assert AFFINITY_API_VERSION_HEADER not in seen[0].headers
        assert http.affinity_api_versions_seen == frozenset()

    def test_current_is_sent_verbatim(self) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen), affinity_api_version="current")
        try:
            http.get("/companies")
        finally:
            http.close()
        assert seen[0].headers.get(AFFINITY_API_VERSION_HEADER) == "current"
        assert http.affinity_api_versions_seen == frozenset({"2026-09-17"})

    @pytest.mark.synthetic_http
    def test_stripped_on_cross_host_redirect(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if str(request.url) == f"{V2}/files/5/content":
                return httpx.Response(
                    302, headers={"Location": "https://files.example/blob?sig=secret"}
                )
            if request.url.host == "files.example":
                return httpx.Response(200, content=b"data")
            return httpx.Response(404)

        http = _http(handler, affinity_api_version="2026-09-17")
        try:
            assert http.download_file("/files/5/content") == b"data"
        finally:
            http.close()
        assert seen[0].headers.get(AFFINITY_API_VERSION_HEADER) == "2026-09-17"
        assert seen[1].url.host == "files.example"
        assert AFFINITY_API_VERSION_HEADER not in seen[1].headers
        assert "Authorization" not in seen[1].headers


class TestHeaderAsync:
    def test_header_on_v2_including_next_url_page(self) -> None:
        seen: list[httpx.Request] = []

        async def run() -> dict[str, object]:
            http = _async_http(_recording_handler(seen), affinity_api_version="2026-07-15")
            try:
                page1 = await http.get("/companies")
                page2 = await http.get_url(page1["pagination"]["nextUrl"])
                return page2
            finally:
                await http.close()

        page2 = asyncio.run(run())
        assert page2["data"] == [{"id": 2}]
        assert len(seen) == 2
        assert all(r.headers.get(AFFINITY_API_VERSION_HEADER) == "2026-07-15" for r in seen)

    def test_never_on_v1_and_default_absent(self) -> None:
        seen: list[httpx.Request] = []

        async def run() -> None:
            pinned = _async_http(_recording_handler(seen), affinity_api_version="2026-07-15")
            default = _async_http(_recording_handler(seen))
            try:
                await pinned.get("/auth/whoami", v1=True)
                await default.get("/companies")
            finally:
                await pinned.close()
                await default.close()

        asyncio.run(run())
        assert AFFINITY_API_VERSION_HEADER not in seen[0].headers
        assert AFFINITY_API_VERSION_HEADER not in seen[1].headers

    @pytest.mark.synthetic_http
    def test_stripped_on_cross_host_redirect(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if str(request.url) == f"{V2}/files/5/content":
                return httpx.Response(302, headers={"Location": "https://files.example/blob"})
            if request.url.host == "files.example":
                return httpx.Response(200, content=b"data")
            return httpx.Response(404)

        async def run() -> bytes:
            http = _async_http(handler, affinity_api_version="2026-09-17")
            try:
                return await http.download_file("/files/5/content")
            finally:
                await http.close()

        assert asyncio.run(run()) == b"data"
        assert seen[0].headers.get(AFFINITY_API_VERSION_HEADER) == "2026-09-17"
        assert AFFINITY_API_VERSION_HEADER not in seen[1].headers


# ---------------------------------------------------------------------------
# Rejected version (400 param=X-Affinity-Api-Version)
# ---------------------------------------------------------------------------


def _reject_version_handler(request: httpx.Request) -> httpx.Response:
    value = request.headers.get(AFFINITY_API_VERSION_HEADER)
    return httpx.Response(
        400,
        json={
            "errors": [
                {
                    "code": "validation",
                    "param": "X-Affinity-Api-Version",
                    "message": f"The provided version: '{value}' is not valid.",
                }
            ]
        },
        headers={AFFINITY_API_VERSION_HEADER: "2024-01-01"},
    )


@pytest.mark.filterwarnings("ignore:Unknown Affinity API version")
class TestRejectedVersion:
    def test_sync_raises_clear_error(self) -> None:
        http = _http(_reject_version_handler, affinity_api_version="2027-01-01")
        try:
            with pytest.raises(UnsupportedApiVersionError) as exc_info:
                http.get("/companies")
        finally:
            http.close()
        err = exc_info.value
        assert isinstance(err, VersionCompatibilityError)
        assert isinstance(err, ValidationError)  # existing `except ValidationError` still works
        assert err.requested_version == "2027-01-01"
        assert err.status_code == 400
        assert "2027-01-01" in str(err)
        assert "X-Affinity-Api-Version" in str(err)

    def test_async_raises_clear_error(self) -> None:
        async def run() -> None:
            http = _async_http(_reject_version_handler, affinity_api_version="2027-01-01")
            try:
                await http.get("/companies")
            finally:
                await http.close()

        with pytest.raises(UnsupportedApiVersionError, match="2027-01-01"):
            asyncio.run(run())

    def test_no_retry_and_single_request(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _reject_version_handler(request)

        http = HTTPClient(
            ClientConfig(
                api_key="k",
                v2_base_url=V2,
                max_retries=3,
                retry_delay=0,
                affinity_api_version="2027-01-01",
                transport=httpx.MockTransport(handler),
            )
        )
        try:
            with pytest.raises(UnsupportedApiVersionError):
                http.get("/companies")
        finally:
            http.close()
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Echo recording + cache
# ---------------------------------------------------------------------------


class TestEcho:
    def test_echo_recorded(self) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen, echo="2024-01-01"))
        try:
            assert http.affinity_api_versions_seen == frozenset()
            http.get("/companies")
        finally:
            http.close()
        assert http.affinity_api_versions_seen == frozenset({"2024-01-01"})
        assert http.last_affinity_api_version == "2024-01-01"

    def test_echo_recorded_on_cache_hit(self) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen), enable_cache=True, affinity_api_version="2026-09-17")
        try:
            http.get("/companies/fields", cache_key="fields")
            http._reset_affinity_api_versions_seen()
            http.get("/companies/fields", cache_key="fields")
        finally:
            http.close()
        assert len(seen) == 1  # second call served from cache
        assert http.affinity_api_versions_seen == frozenset({"2026-09-17"})

    def test_async_echo_recorded_on_cache_hit(self) -> None:
        seen: list[httpx.Request] = []

        async def run() -> AsyncHTTPClient:
            http = _async_http(_recording_handler(seen), enable_cache=True)
            try:
                await http.get("/companies/fields", cache_key="fields")
                http._reset_affinity_api_versions_seen()
                await http.get("/companies/fields", cache_key="fields")
            finally:
                await http.close()
            return http

        http = asyncio.run(run())
        assert len(seen) == 1
        assert http.affinity_api_versions_seen == frozenset({"2024-01-01"})

    def test_cache_isolated_per_version(self) -> None:
        seen: list[httpx.Request] = []
        handler = _recording_handler(seen)
        a = _http(handler, enable_cache=True)
        b = _http(handler, enable_cache=True, affinity_api_version="2026-09-17")
        # Share one cache between the two clients to prove the keys differ.
        b._cache = a._cache
        try:
            a.get("/companies/fields", cache_key="fields")
            b.get("/companies/fields", cache_key="fields")
            a.get("/companies/fields", cache_key="fields")
        finally:
            a.close()
            b.close()
        assert len(seen) == 2

    def test_probe_key_default_version_sends_no_header_and_is_not_recorded(self) -> None:
        seen: list[httpx.Request] = []
        http = _http(_recording_handler(seen, echo="2024-01-01"), affinity_api_version="2026-09-17")
        try:
            assert http.probe_key_default_api_version() == "2024-01-01"
        finally:
            http.close()
        assert str(seen[0].url) == f"{V2}/auth/whoami"
        assert AFFINITY_API_VERSION_HEADER not in seen[0].headers
        assert http.affinity_api_versions_seen == frozenset()


# ---------------------------------------------------------------------------
# Public client surface
# ---------------------------------------------------------------------------


class TestClientSurface:
    def test_affinity_kwarg_and_versions_seen(self) -> None:
        seen: list[httpx.Request] = []
        client = Affinity(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            transport=httpx.MockTransport(_recording_handler(seen)),  # type: ignore[arg-type]
            affinity_api_version="2026-09-17",
        )
        try:
            assert client.affinity_api_version == "2026-09-17"
            client._http.get("/companies")
            assert client.affinity_api_versions_seen == frozenset({"2026-09-17"})
            assert client.get_key_default_api_version() == "2024-01-01"
        finally:
            client.close()

    def test_async_affinity_kwarg(self) -> None:
        seen: list[httpx.Request] = []

        async def run() -> tuple[frozenset[str], str | None]:
            async with AsyncAffinity(
                api_key="k",
                v1_base_url=V1,
                v2_base_url=V2,
                max_retries=0,
                async_transport=httpx.MockTransport(_recording_handler(seen)),  # type: ignore[arg-type]
                affinity_api_version="2026-07-15",
            ) as client:
                await client._http.get("/companies")
                default = await client.get_key_default_api_version()
                return client.affinity_api_versions_seen, default

        versions, default = asyncio.run(run())
        assert versions == frozenset({"2026-07-15"})
        assert default == "2024-01-01"
        assert seen[0].headers.get(AFFINITY_API_VERSION_HEADER) == "2026-07-15"

    def test_affinity_rejects_invalid_version(self) -> None:
        with pytest.raises(ConfigurationError):
            Affinity(api_key="k", affinity_api_version="garbage")

    def test_expected_v2_version_deprecated(self) -> None:
        with pytest.warns(DeprecationWarning, match="expected_v2_version"):
            client = Affinity(api_key="k", expected_v2_version="2024-01-01")
        client.close()

    def test_expected_v2_version_mismatch_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        seen: list[httpx.Request] = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            http = _http(
                _recording_handler(seen, echo="2024-01-01"), expected_v2_version="2026-09-17"
            )
        try:
            with caplog.at_level("WARNING", logger="affinity_sdk"):
                http.get("/companies")
                http.get("/companies")
        finally:
            http.close()
        mismatch = [r for r in caplog.records if "expected_v2_version" in r.getMessage()]
        assert len(mismatch) == 1
