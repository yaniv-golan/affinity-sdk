"""Calls that need a minimum Affinity API version (``patch(..., min_api_version=...)``)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from affinity.api_versions import AFFINITY_API_VERSION_HEADER
from affinity.cli.context import normalize_exception
from affinity.cli.runner import api_version_meta
from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.exceptions import ApiVersionTooOldError, UnsupportedApiVersionError
from affinity.policies import Policies, WritePolicy

V2 = "https://v2.example/v2"
V1 = "https://v1.example"
KEY_DEFAULT = "2024-01-01"
MINIMUM = "2026-07-15"
FIELDS = "/companies/1/fields"


def _handler(seen: list[httpx.Request]) -> Any:
    """Echo the requested version, or the key default when none is sent."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        requested = request.headers.get(AFFINITY_API_VERSION_HEADER)
        echo = {None: KEY_DEFAULT, "current": "2026-09-17"}.get(requested, requested)
        headers = {AFFINITY_API_VERSION_HEADER: echo}
        if request.url.path == "/v2/companies":
            return httpx.Response(200, json={"data": [], "pagination": {}}, headers=headers)
        if requested == "2026-01-01":
            error = {
                "code": "validation",
                "param": AFFINITY_API_VERSION_HEADER,
                "message": f"The provided version: '{requested}' is not valid.",
            }
            body = {"errors": [error]}
            return httpx.Response(400, json=body, headers=headers)
        return httpx.Response(200, json={"operation": "update-fields"}, headers=headers)

    return handler


def _http(seen: list[httpx.Request], **config: Any) -> HTTPClient:
    return HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            transport=httpx.MockTransport(_handler(seen)),
            **config,
        )
    )


def _async_http(seen: list[httpx.Request], **config: Any) -> AsyncHTTPClient:
    return AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            async_transport=httpx.MockTransport(_handler(seen)),
            **config,
        )
    )


def _sent_version(request: httpx.Request) -> str | None:
    return request.headers.get(AFFINITY_API_VERSION_HEADER)


class TestSelection:
    def test_unpinned_sends_the_minimum_and_records_it_per_operation(self) -> None:
        seen: list[httpx.Request] = []
        with _http(seen) as http:
            http.get("/companies")  # key default answers
            http.patch(FIELDS, json={}, min_api_version=MINIMUM)
            assert [_sent_version(r) for r in seen] == [None, MINIMUM]
            assert http.affinity_api_versions_seen == {KEY_DEFAULT, MINIMUM}
            assert http.affinity_api_versions_per_operation == {MINIMUM}
            # The key default stays the key default.
            assert http.last_affinity_api_version == KEY_DEFAULT

    def test_unpinned_sends_the_minimum_even_when_the_key_default_is_newer(self) -> None:
        seen: list[httpx.Request] = []
        with _http(seen) as http:
            http.patch(FIELDS, json={}, min_api_version="2024-01-01")
            assert _sent_version(seen[0]) == "2024-01-01"

    @pytest.mark.parametrize("pin", ["2026-07-15", "2026-09-17", "2027-01-01", "current"])
    def test_a_pin_that_is_new_enough_is_sent_as_usual(self, pin: str) -> None:
        seen: list[httpx.Request] = []
        with (
            pytest.warns(UserWarning) if pin == "2027-01-01" else _no_warning(),
            _http(seen, affinity_api_version=pin) as http,
        ):
            http.patch(FIELDS, json={}, min_api_version=MINIMUM)
            assert _sent_version(seen[0]) == pin
            assert http.affinity_api_versions_per_operation == frozenset()

    def test_a_pin_that_is_too_old_raises_before_anything_is_sent(self) -> None:
        seen: list[httpx.Request] = []
        with _http(seen, affinity_api_version="2024-01-01") as http:
            with pytest.raises(ApiVersionTooOldError) as exc:
                http.patch(FIELDS, json={}, min_api_version=MINIMUM)
            assert seen == []
            assert exc.value.requested_version == "2024-01-01"
            assert exc.value.required_version == MINIMUM
            assert "PATCH /v2/companies/1/fields" in str(exc.value)
            assert isinstance(exc.value, UnsupportedApiVersionError)

    def test_too_old_wins_over_the_write_policy(self) -> None:
        seen: list[httpx.Request] = []
        policies = Policies(write=WritePolicy.DENY)
        with (
            _http(seen, affinity_api_version="2024-01-01", policies=policies) as http,
            pytest.raises(ApiVersionTooOldError),
        ):
            http.patch(FIELDS, json={}, min_api_version=MINIMUM)

    @pytest.mark.parametrize("bad", ["current", "", "2025-01-01", "auto"])
    def test_minimum_must_be_a_known_version(self, bad: str) -> None:
        with _http([]) as http, pytest.raises(ValueError, match="min_api_version"):
            http.patch(FIELDS, json={}, min_api_version=bad)

    def test_minimum_is_v2_only(self) -> None:
        with _http([]) as http, pytest.raises(ValueError, match="V2"):
            http.patch("/field-values/1", json={}, v1=True, min_api_version=MINIMUM)

    def test_affinity_rejecting_the_minimum_does_not_blame_a_pin(self) -> None:
        seen: list[httpx.Request] = []
        with _http(seen) as http:
            tracker = http._api_versions
            context = tracker.per_operation_context(MINIMUM, "PATCH x")
            assert context == {
                "affinity_api_version": MINIMUM,
                "affinity_api_version_per_operation": True,
            }

    def test_reset_clears_per_operation_versions(self) -> None:
        seen: list[httpx.Request] = []
        with _http(seen) as http:
            http.patch(FIELDS, json={}, min_api_version=MINIMUM)
            http._reset_affinity_api_versions_seen()
            assert http.affinity_api_versions_per_operation == frozenset()
            assert http.affinity_api_versions_seen == frozenset()


class TestRejectedMinimum:
    def test_message_names_the_operation_not_the_pin(self, monkeypatch: Any) -> None:
        from affinity.clients import http as http_module

        monkeypatch.setattr(http_module, "validate_min_api_version", lambda v: v)
        seen: list[httpx.Request] = []
        with _http(seen) as http, pytest.raises(UnsupportedApiVersionError) as exc:
            http.patch(FIELDS, json={}, min_api_version="2026-01-01")
        assert "which this operation needs" in str(exc.value)
        assert "unset" not in str(exc.value)


class TestAsync:
    def test_unpinned_and_too_old(self) -> None:
        async def run() -> None:
            seen: list[httpx.Request] = []
            http = _async_http(seen)
            try:
                await http.patch(FIELDS, json={}, min_api_version=MINIMUM)
                assert _sent_version(seen[0]) == MINIMUM
                assert http.affinity_api_versions_per_operation == {MINIMUM}
                assert http.last_affinity_api_version is None
            finally:
                await http.close()

            pinned = _async_http(seen, affinity_api_version="2024-01-01")
            try:
                with pytest.raises(ApiVersionTooOldError):
                    await pinned.patch(FIELDS, json={}, min_api_version=MINIMUM)
                assert len(seen) == 1
            finally:
                await pinned.close()

        asyncio.run(run())


class TestCli:
    def test_too_old_error_maps_to_api_version_error(self) -> None:
        exc = ApiVersionTooOldError(
            "PATCH /v2/companies/1/fields needs Affinity API version 2026-07-15 or newer, but "
            "the client is pinned to 2024-01-01.",
            requested_version="2024-01-01",
            required_version=MINIMUM,
        )
        error = normalize_exception(exc)
        assert error.error_type == "api_version_error"
        assert error.exit_code == 2
        assert error.details is not None
        assert error.details["requiredApiVersion"] == MINIMUM
        assert error.details["affinityApiVersion"] == "2024-01-01"
        assert error.hint is not None and f"--api-version {MINIMUM}" in error.hint

    def test_per_operation_versions_do_not_trigger_the_mixed_version_warning(self) -> None:
        class Ctx:
            def __init__(self, seen: list[str], per_operation: list[str]) -> None:
                self._seen, self._per_operation = seen, per_operation

            def api_versions_seen(self) -> list[str]:
                return self._seen

            def api_versions_per_operation(self) -> list[str]:
                return self._per_operation

        warnings: list[str] = []
        meta = api_version_meta(Ctx([KEY_DEFAULT, MINIMUM], [MINIMUM]), warnings)  # type: ignore[arg-type]
        assert meta == [KEY_DEFAULT, MINIMUM]
        assert warnings == []
        meta = api_version_meta(Ctx([KEY_DEFAULT, "2026-09-17"], []), warnings)  # type: ignore[arg-type]
        assert len(warnings) == 1


class _no_warning:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
