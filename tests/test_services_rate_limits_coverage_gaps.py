"""Additional tests for affinity.services.rate_limits to improve coverage."""

from __future__ import annotations

import time

import httpx

from affinity.clients.http import ClientConfig, HTTPClient
from affinity.services.rate_limits import (
    RateLimitService,
    _bucket_from_headers,
    _snapshot_from_state,
)


class TestBucketFromHeaders:
    """Tests for _bucket_from_headers helper function."""

    def test_with_age_seconds(self) -> None:
        """When age_seconds is provided, reset should be adjusted."""
        bucket = _bucket_from_headers(
            limit=100,
            remaining=80,
            reset_seconds=60,
            age_seconds=10.0,
        )
        assert bucket.limit == 100
        assert bucket.remaining == 80
        assert bucket.reset_seconds == 50  # 60 - 10
        assert bucket.used == 20  # 100 - 80

    def test_with_age_exceeding_reset(self) -> None:
        """When age_seconds exceeds reset_seconds, reset should be 0."""
        bucket = _bucket_from_headers(
            limit=100,
            remaining=80,
            reset_seconds=30,
            age_seconds=50.0,
        )
        assert bucket.reset_seconds == 0  # max(0, 30 - 50)

    def test_without_age_seconds(self) -> None:
        """When age_seconds is None, reset should be unchanged."""
        bucket = _bucket_from_headers(
            limit=100,
            remaining=80,
            reset_seconds=60,
            age_seconds=None,
        )
        assert bucket.reset_seconds == 60

    def test_used_calculation(self) -> None:
        """Used should be limit - remaining when both are present."""
        bucket = _bucket_from_headers(
            limit=100,
            remaining=30,
            reset_seconds=None,
            age_seconds=None,
        )
        assert bucket.used == 70

    def test_used_when_negative(self) -> None:
        """Used should be None if calculation would be negative."""
        bucket = _bucket_from_headers(
            limit=50,
            remaining=100,  # remaining > limit (unusual but possible)
            reset_seconds=None,
            age_seconds=None,
        )
        assert bucket.used is None

    def test_used_when_limit_none(self) -> None:
        """Used should be None if limit is None."""
        bucket = _bucket_from_headers(
            limit=None,
            remaining=80,
            reset_seconds=None,
            age_seconds=None,
        )
        assert bucket.used is None

    def test_used_when_remaining_none(self) -> None:
        """Used should be None if remaining is None."""
        bucket = _bucket_from_headers(
            limit=100,
            remaining=None,
            reset_seconds=None,
            age_seconds=None,
        )
        assert bucket.used is None


class TestSnapshotFromState:
    """Tests for _snapshot_from_state helper function."""

    def test_with_last_updated(self) -> None:
        """When last_updated is present, age_seconds and observed_at should be calculated."""

        class MockState:
            def snapshot(self) -> dict:
                return {
                    "last_updated": time.time() - 5.0,  # 5 seconds ago
                    "user_limit": 100,
                    "user_remaining": 80,
                    "user_reset_seconds": 60,
                    "org_limit": 1000,
                    "org_remaining": 800,
                    "org_reset_seconds": 600,
                    "last_request_id": "req-123",
                }

        snapshot = _snapshot_from_state(MockState())
        assert snapshot.age_seconds is not None
        assert snapshot.age_seconds >= 4.0  # approximately 5 seconds
        assert snapshot.age_seconds <= 6.0
        assert snapshot.observed_at is not None
        assert snapshot.source == "headers"
        assert snapshot.request_id == "req-123"

    def test_without_last_updated(self) -> None:
        """When last_updated is None, age_seconds and observed_at should be None."""

        class MockState:
            def snapshot(self) -> dict:
                return {
                    "last_updated": None,
                    "user_limit": None,
                    "user_remaining": None,
                    "user_reset_seconds": None,
                    "org_limit": None,
                    "org_remaining": None,
                    "org_reset_seconds": None,
                }

        snapshot = _snapshot_from_state(MockState())
        assert snapshot.age_seconds is None
        assert snapshot.observed_at is None
        assert snapshot.source == "unknown"

    def test_source_unknown_when_no_limits(self) -> None:
        """Source should be 'unknown' when no limit data is available."""

        class MockState:
            def snapshot(self) -> dict:
                return {
                    "last_updated": time.time(),
                    "user_limit": None,
                    "user_remaining": None,
                    "user_reset_seconds": None,
                    "org_limit": None,
                    "org_remaining": None,
                    "org_reset_seconds": None,
                }

        snapshot = _snapshot_from_state(MockState())
        assert snapshot.source == "unknown"


class TestRateLimitServiceRefreshFallback:
    """Tests for the fallback path in RateLimitService.refresh."""

    def test_refresh_fallback_on_403(self) -> None:
        """refresh should fallback to whoami when rate-limit returns 403."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            url = str(request.url)

            if "/rate-limit" in url:
                return httpx.Response(
                    403,
                    json={"error": "Forbidden"},
                    request=request,
                    headers={
                        "X-Ratelimit-Limit-User-Minute": "100",
                        "X-Ratelimit-Remaining-User-Minute": "80",
                    },
                )
            if "/auth/whoami" in url:
                return httpx.Response(
                    200,
                    json={"type": 0, "tenant": {"id": 1, "name": "Test"}},
                    request=request,
                    headers={
                        "X-Ratelimit-Limit-User-Minute": "100",
                        "X-Ratelimit-Remaining-User-Minute": "80",
                    },
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = RateLimitService(http)

        snapshot = service.refresh()
        assert call_count == 2  # rate-limit + whoami fallback
        assert snapshot is not None

    def test_refresh_fallback_on_404(self) -> None:
        """refresh should fallback to whoami when rate-limit returns 404."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            url = str(request.url)

            if "/rate-limit" in url:
                return httpx.Response(404, json={"error": "Not found"}, request=request)
            if "/auth/whoami" in url:
                return httpx.Response(
                    200,
                    json={"type": 0, "tenant": {"id": 1, "name": "Test"}},
                    request=request,
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = RateLimitService(http)

        snapshot = service.refresh()
        assert call_count == 2  # rate-limit + whoami fallback
        assert snapshot is not None

    def test_refresh_success_uses_endpoint_data(self) -> None:
        """refresh should use endpoint data when successful."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)

            if "/rate-limit" in url:
                return httpx.Response(
                    200,
                    json={
                        "rate": {
                            "api_key_per_minute": {
                                "limit": 500,
                                "remaining": 400,
                                "reset": 60,
                                "used": 100,
                            },
                            "org_monthly": {
                                "limit": 50000,
                                "remaining": 49000,
                                "reset": 86400,
                                "used": 1000,
                            },
                        }
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        service = RateLimitService(http)

        snapshot = service.refresh()
        assert snapshot.source == "endpoint"
        assert snapshot.api_key_per_minute.limit == 500
        assert snapshot.api_key_per_minute.remaining == 400
        assert snapshot.org_monthly.limit == 50000

    def test_refresh_without_org_monthly_bucket(self) -> None:
        """Plans with no monthly cap omit the org bucket; refresh must not raise.

        v2 `/v2/rate-limit` documents `orgPerMonth` as absent when no monthly quota applies, and
        v1 and v2 share one request pool, so v1's `org_monthly` is treated as optional too.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if "/rate-limit" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "rate": {
                            "api_key_per_minute": {
                                "limit": 900,
                                "remaining": 899,
                                "reset": 42,
                                "used": 1,
                            }
                        }
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        snapshot = RateLimitService(http).refresh()
        assert snapshot.source == "endpoint"
        assert snapshot.api_key_per_minute.remaining == 899
        assert snapshot.org_monthly.limit is None
        assert snapshot.org_monthly.remaining is None
