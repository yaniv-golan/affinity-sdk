"""
Pytest configuration and fixtures for Affinity SDK tests.
"""

from collections.abc import Iterator

import httpx
import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional test dependency
    respx = None  # type: ignore[assignment]

from affinity import Affinity
from affinity.cli.context import CLIContext
from tests import spec_guard

_SPEC_PROBLEMS = pytest.StashKey[list[str]]()


@pytest.fixture(autouse=True)
def _spec_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Check every V2 request the test sends against the OpenAPI specs (tests/spec_guard.py).

    Problems fail the test (see ``pytest_runtest_makereport``). Tests marked
    ``synthetic_http`` may send requests to made-up operations; their parameters on real
    operations are still checked, and a marker that excuses nothing is itself a problem.
    """
    problems: list[str] = []
    request.node.stash[_SPEC_PROBLEMS] = problems
    allow_unknown = request.node.get_closest_marker("synthetic_http") is not None
    sent_unknown = False

    def record(args: tuple[object, ...], kwargs: dict[str, object]) -> None:
        nonlocal sent_unknown
        req = args[0] if args else kwargs.get("request")
        if not isinstance(req, httpx.Request):
            return
        try:
            result = spec_guard.check_request(req)
        except Exception as e:  # never break the request itself
            problems.append(f"spec guard error on {req.method} {req.url}: {e}")
            return
        if result is None:
            return
        unknown, found = result
        sent_unknown = sent_unknown or unknown
        if unknown and allow_unknown:
            return
        problems.extend(p for p in found if p not in problems)

    send, asend = httpx.Client.send, httpx.AsyncClient.send

    def guarded_send(self: httpx.Client, *args: object, **kwargs: object) -> httpx.Response:
        record(args, kwargs)
        return send(self, *args, **kwargs)  # type: ignore[arg-type]

    async def guarded_asend(
        self: httpx.AsyncClient, *args: object, **kwargs: object
    ) -> httpx.Response:
        record(args, kwargs)
        return await asend(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.Client, "send", guarded_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_asend)
    yield
    if allow_unknown and not sent_unknown:
        problems.append(
            "marked synthetic_http but sent no request to a made-up operation: remove the marker"
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()  # type: ignore[attr-defined]
    problems = item.stash.get(_SPEC_PROBLEMS, None)
    if not problems or report.when not in ("call", "teardown") or report.failed:
        return
    report.outcome = "failed"
    report.longrepr = "Requests that don't match the Affinity OpenAPI spec:\n" + "\n".join(
        f"  - {p}" for p in problems
    )
    problems.clear()


@pytest.fixture(autouse=True)
def _no_check_key_api_version_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """`config check-key` probes the key's default API version over the network
    (best-effort). Keep unit tests offline; tests of the probe restore it explicitly."""
    from affinity.cli.commands import config_cmds

    monkeypatch.setattr(config_cmds, "_probe_key_default_api_version", lambda _ctx, _src: None)


@pytest.fixture
def api_key() -> str:
    """Test API key."""
    return "test-api-key-12345"


@pytest.fixture
def mock_api() -> object:
    """
    Create a mock API router.

    Note: respx is an optional dev dependency; when it's missing we skip tests
    that require this fixture.
    """
    if respx is None:
        pytest.skip("respx is not installed")
    with respx.mock(assert_all_called=False) as router:  # type: ignore[attr-defined]
        yield router


@pytest.fixture
def client(api_key: str, mock_api: object) -> Iterator[Affinity]:
    """
    Create a test client with mocked HTTP.

    IMPORTANT: This fixture yields and closes the client to prevent httpx
    connection pools from lingering and causing gc.collect() hangs during
    pytest cleanup.
    """
    _ = mock_api
    c = Affinity(
        api_key=api_key,
        enable_cache=False,
        max_retries=0,
    )
    yield c
    c.close()


# Common mock responses
@pytest.fixture
def whoami_response() -> dict:
    """Mock /auth/whoami response."""
    return {
        "tenant": {
            "id": 1,
            "name": "Test Company",
            "subdomain": "test",
        },
        "user": {
            "id": 100,
            "firstName": "Test",
            "lastName": "User",
            "emailAddress": "test@example.com",
        },
        "grant": {
            "type": "api_key",
            "scopes": ["all"],
            "createdAt": "2024-01-01T00:00:00Z",
        },
    }


@pytest.fixture
def company_response() -> dict:
    """Mock single company response."""
    return {
        "id": 123,
        "name": "Acme Corp",
        "domain": "acme.com",
        "domains": ["acme.com"],
        "personIds": [1, 2, 3],
        "fields": {},
    }


@pytest.fixture
def companies_list_response() -> dict:
    """Mock companies list response."""
    return {
        "data": [
            {
                "id": 123,
                "name": "Acme Corp",
                "domain": "acme.com",
                "domains": ["acme.com"],
                "personIds": [],
                "fields": {},
            },
            {
                "id": 456,
                "name": "Beta Inc",
                "domain": "beta.io",
                "domains": ["beta.io"],
                "personIds": [],
                "fields": {},
            },
        ],
        "pagination": {
            "nextPageUrl": None,
            "prevPageUrl": None,
        },
    }


@pytest.fixture
def person_response() -> dict:
    """Mock single person response."""
    return {
        "id": 100,
        "firstName": "John",
        "lastName": "Doe",
        "primaryEmailAddress": "john@example.com",
        "emails": ["john@example.com"],
        "type": "external",
        "organizationIds": [123],
        "fields": {},
    }


@pytest.fixture
def list_response() -> dict:
    """Mock single list response."""
    return {
        "id": 789,
        "name": "Deal Pipeline",
        "type": 8,  # OPPORTUNITY
        "public": True,
        "ownerId": 100,
        "creatorId": 100,
        "listSize": 50,
        "fields": [],
    }


@pytest.fixture
def make_mock_transport(monkeypatch):
    """Return a factory that registers an httpx.MockTransport and forces the
    CLI's get_client() to return an Affinity bound to that transport.
    """

    def factory(handler):
        transport = httpx.MockTransport(handler)

        def patched_get_client(self, *, warnings):
            _ = warnings  # unused in test transport
            # Rebuild on each mock-transport setup so repeated factory calls
            # within one test don't silently reuse a stale cached Affinity
            # bound to a prior handler.
            self._client = Affinity(
                api_key="test",
                v1_base_url="https://api.affinity.co",
                v2_base_url="https://api.affinity.co/v2",
                max_retries=0,
                transport=transport,
            )
            return self._client

        monkeypatch.setattr(CLIContext, "get_client", patched_get_client)
        return transport

    return factory


@pytest.fixture
def lists_response() -> dict:
    """Mock lists list response."""
    return {
        "data": [
            {
                "id": 789,
                "name": "Deal Pipeline",
                "type": 8,
                "public": True,
                "ownerId": 100,
                "creatorId": 100,
                "listSize": 50,
            },
            {
                "id": 790,
                "name": "Contacts",
                "type": 0,
                "public": True,
                "ownerId": 100,
                "creatorId": 100,
                "listSize": 100,
            },
        ],
        "pagination": {
            "nextPageUrl": None,
            "prevPageUrl": None,
        },
    }
