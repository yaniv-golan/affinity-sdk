"""
Rate limit services (version-agnostic).

These services provide a unified public surface for inspecting and refreshing
rate limit information without exposing API versioning details.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..clients.http import AsyncHTTPClient, HTTPClient, RateLimitState
from ..exceptions import AuthorizationError, NotFoundError
from ..models.rate_limit_snapshot import RateLimitBucket, RateLimitSnapshot
from ..models.secondary import RateLimits


def _bucket_from_headers(
    *,
    limit: int | None,
    remaining: int | None,
    reset_seconds: int | None,
    age_seconds: float | None,
) -> RateLimitBucket:
    effective_reset: int | None = None
    if reset_seconds is not None:
        if age_seconds is None:
            effective_reset = reset_seconds
        else:
            effective_reset = max(0, int(reset_seconds - age_seconds))

    used: int | None = None
    if limit is not None and remaining is not None:
        used_candidate = limit - remaining
        if used_candidate >= 0:
            used = used_candidate

    return RateLimitBucket(
        limit=limit, remaining=remaining, reset_seconds=effective_reset, used=used
    )


def _snapshot_from_state(state: RateLimitState) -> RateLimitSnapshot:
    raw = state.snapshot()
    last_updated = raw.get("last_updated")
    now = time.time()

    age_seconds: float | None
    observed_at: datetime | None
    if isinstance(last_updated, (int, float)):
        age_seconds = max(0.0, now - float(last_updated))
        observed_at = datetime.fromtimestamp(float(last_updated), tz=timezone.utc)
    else:
        age_seconds = None
        observed_at = None

    api_key_bucket = _bucket_from_headers(
        limit=raw.get("user_limit"),
        remaining=raw.get("user_remaining"),
        reset_seconds=raw.get("user_reset_seconds"),
        age_seconds=age_seconds,
    )
    org_bucket = _bucket_from_headers(
        limit=raw.get("org_limit"),
        remaining=raw.get("org_remaining"),
        reset_seconds=raw.get("org_reset_seconds"),
        age_seconds=age_seconds,
    )

    known = any(
        v is not None
        for v in (
            api_key_bucket.limit,
            api_key_bucket.remaining,
            api_key_bucket.reset_seconds,
            org_bucket.limit,
            org_bucket.remaining,
            org_bucket.reset_seconds,
        )
    )

    return RateLimitSnapshot(
        api_key_per_minute=api_key_bucket,
        org_monthly=org_bucket,
        observed_at=observed_at,
        age_seconds=age_seconds,
        source="headers" if known else "unknown",
        request_id=raw.get("last_request_id"),
    )


def _snapshot_from_endpoint(
    limits: RateLimits,
    *,
    observed_at: datetime,
    request_id: str | None,
) -> RateLimitSnapshot:
    return RateLimitSnapshot(
        api_key_per_minute=RateLimitBucket(
            limit=limits.api_key_per_minute.limit,
            remaining=limits.api_key_per_minute.remaining,
            reset_seconds=limits.api_key_per_minute.reset,
            used=limits.api_key_per_minute.used,
        ),
        org_monthly=(
            RateLimitBucket(
                limit=limits.org_monthly.limit,
                remaining=limits.org_monthly.remaining,
                reset_seconds=limits.org_monthly.reset,
                used=limits.org_monthly.used,
            )
            if limits.org_monthly is not None
            else RateLimitBucket()  # no monthly quota applies
        ),
        observed_at=observed_at,
        age_seconds=0.0,
        source="endpoint",
        request_id=request_id,
    )


class RateLimitService:
    """Unified rate limit service (sync)."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def snapshot(self) -> RateLimitSnapshot:
        """Return a best-effort snapshot derived from tracked response headers."""
        return _snapshot_from_state(self._client.rate_limit_state)

    def refresh(self) -> RateLimitSnapshot:
        """
        Fetch/observe the best available rate limit snapshot now.

        Strategy:
        1) Try the dedicated endpoint (`GET /rate-limit`, internal v1 today).
        2) If unavailable (403/404), fall back to `GET /auth/whoami` to observe headers.
        """
        observed_at = datetime.now(tz=timezone.utc)
        try:
            data = self._client.get("/rate-limit", v1=True)
            limits = RateLimits.model_validate(data.get("rate", {}))
            request_id = self._client.rate_limit_state.snapshot().get("last_request_id")
            return _snapshot_from_endpoint(limits, observed_at=observed_at, request_id=request_id)
        except (AuthorizationError, NotFoundError):
            # Fallback: make a lightweight request and return header-derived snapshot.
            _ = self._client.get("/auth/whoami")
            return self.snapshot()


class AsyncRateLimitService:
    """Unified rate limit service (async)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    def snapshot(self) -> RateLimitSnapshot:
        """Return a best-effort snapshot derived from tracked response headers."""
        return _snapshot_from_state(self._client.rate_limit_state)

    async def refresh(self) -> RateLimitSnapshot:
        """
        Fetch/observe the best available rate limit snapshot now.

        Strategy mirrors the sync client.
        """
        observed_at = datetime.now(tz=timezone.utc)
        try:
            data = await self._client.get("/rate-limit", v1=True)
            limits = RateLimits.model_validate(data.get("rate", {}))
            request_id = self._client.rate_limit_state.snapshot().get("last_request_id")
            return _snapshot_from_endpoint(limits, observed_at=observed_at, request_id=request_id)
        except (AuthorizationError, NotFoundError):
            _ = await self._client.get("/auth/whoami")
            return self.snapshot()
