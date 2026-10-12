"""rate_limits.refresh() reads GET /v2/rate-limit (Affinity API 2026-07-15+), falling back to
response headers (via whoami) when the endpoint is refused, missing, or the client is pinned
to an older API version."""

from __future__ import annotations

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.models import RateLimits

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
WINDOW = {"limit": 900, "remaining": 899, "reset": 30, "used": 1}
WHOAMI = {
    "tenant": {"id": 1, "name": "Acme", "subdomain": "acme"},
    "user": {"id": 2, "firstName": "A", "lastName": "B", "emailAddress": "a@b.co"},
    "grant": {"type": "api-key", "scope": "external-api", "createdAt": "2024-01-01T00:00:00Z"},
}


def test_model_reads_v2_and_v1_names() -> None:
    v2 = RateLimits.model_validate({"callerPerMinute": WINDOW, "orgPerMonth": WINDOW})
    v1 = RateLimits.model_validate({"api_key_per_minute": WINDOW, "org_monthly": WINDOW})
    assert v2.api_key_per_minute.limit == v1.api_key_per_minute.limit == 900
    assert v2.org_monthly is not None and v2.org_monthly.used == 1
    assert RateLimits.model_validate({"callerPerMinute": WINDOW}).org_monthly is None


@respx.mock
def test_refresh_uses_v2() -> None:
    route = respx.get(f"{V2}/rate-limit").mock(
        return_value=Response(200, json={"callerPerMinute": WINDOW})
    )
    with Affinity(api_key="k", max_retries=0) as client:
        snap = client.rate_limits.refresh()
    assert snap.source == "endpoint"
    assert snap.api_key_per_minute.limit == 900
    assert snap.org_monthly.limit is None
    assert route.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"


@respx.mock
def test_refresh_falls_back_to_headers_when_refused() -> None:
    respx.get(f"{V2}/rate-limit").mock(return_value=Response(403, json={"errors": []}))
    whoami = respx.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))
    with Affinity(api_key="k", max_retries=0) as client:
        snap = client.rate_limits.refresh()
    assert whoami.called
    assert snap.source != "endpoint"


@respx.mock
def test_refresh_falls_back_when_pinned_old() -> None:
    whoami = respx.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))
    with Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client:
        snap = client.rate_limits.refresh()
    assert whoami.called
    assert snap.source != "endpoint"


@respx.mock
async def test_async_refresh_uses_v2() -> None:
    respx.get(f"{V2}/rate-limit").mock(
        return_value=Response(200, json={"callerPerMinute": WINDOW, "orgPerMonth": WINDOW})
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        snap = await client.rate_limits.refresh()
    assert snap.org_monthly.used == 1
