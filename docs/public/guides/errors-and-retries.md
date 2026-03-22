# Errors and retries

The SDK raises typed exceptions (subclasses of `AffinityError`) and retries some transient failures for safe methods (`GET`/`HEAD`).

## Exception taxonomy (common)

- `AuthenticationError` (401): invalid/missing API key
- `AuthorizationError` (403): insufficient permissions
- `NotFoundError` (404): entity or endpoint not found
- `ValidationError` (400/422): invalid parameters/payload
- `MergedEntityError` (422): entity was merged into another — subclass of `ValidationError` with `source_id` and `target_id`
- `ConflictError` (409): resource conflict (e.g., duplicate email)
- `RateLimitError` (429): you are being rate limited (may include `retry_after`)
- `ServerError` (500/502/503/504): transient server-side errors
- `NetworkError`: connection or network-level failure
- `TimeoutError`: request or download deadline exceeded
- `WriteNotAllowedError`: you attempted a write while writes are disabled by policy
- `BetaEndpointDisabledError`: you called a beta V2 endpoint without `enable_beta_endpoints=True`
- `VersionCompatibilityError`: response parsing failed, often due to V2 API version mismatch

See [Exceptions](../reference/exceptions.md) for the full hierarchy.

## Retry policy (what is retried)

By default, retries apply to:

- `GET`/`HEAD` only (safe/idempotent methods)
- 429 responses (rate limits): respects `Retry-After` when present
- transient network/timeouts for `GET`/`HEAD`
- transient server errors (e.g., 5xx) for `GET`/`HEAD`

Retries are controlled by `max_retries` (default: 3).

## Download deadlines

For large file downloads, `timeout` controls per-request timeouts, and `deadline_seconds` enforces a total time budget for streaming downloads (including retries/backoff). When exceeded, the SDK raises `TimeoutError`.

## Diagnostics

Many errors include diagnostics (method/URL/status and more). When you catch an `AffinityError`, you can log it and inspect attached context.

```python
from affinity import Affinity
from affinity.exceptions import AffinityError, RateLimitError

try:
    with Affinity(api_key="your-api-key") as client:
        client.companies.list()
except RateLimitError as e:
    print("Rate limited:", e)
    print("Retry after:", e.retry_after)
except AffinityError as e:
    print("Affinity error:", e)
    if e.diagnostics:
        print("Request:", e.diagnostics.method, e.diagnostics.url)
        print("Status:", e.status_code)
        print("Request ID:", e.diagnostics.request_id)
```

## Production playbook

The SDK retries some failures for safe reads (`GET`/`HEAD`), but production systems typically need additional policies: alerting, bounded retries, idempotency for writes, and circuit breaking during outages.

### Recommended handling by error type

- **AuthenticationError (401), AuthorizationError (403)**: do not retry; fix credentials/permissions; alert immediately.
- **ValidationError (400/422)**: do not retry; treat as a bug or bad input; log the response body snippet for debugging.
- **MergedEntityError (422)**: a subclass of `ValidationError` raised when you access a company or person that was merged into another. Use `e.target_id` to follow the merge programmatically (see [Merged entities](#merged-entities) below).
- **NotFoundError (404)**: generally do not retry; treat as "missing" and handle at the business layer. **Exception**: see [V1→V2 eventual consistency](#v1v2-eventual-consistency) below for 404s immediately after create.
- **ConflictError (409)**: do not retry; handle the conflict (e.g., duplicate email, concurrent modification). May indicate you need to fetch-then-update instead of blind update.
- **RateLimitError (429)**: retry only after `retry_after` (when present), reduce concurrency, and consider queueing/batching to smooth bursts.
- **Server errors (5xx) / transient network errors / timeouts**:
  - **Reads (`GET`/`HEAD`)**: retry with backoff (the SDK already does).
  - **Writes (`POST`/`PATCH`/`PUT`/`DELETE`)**: only retry if you can make the operation idempotent.
- **NetworkError / TimeoutError**: for reads, retry with backoff; for writes, only retry if idempotent.
- **VersionCompatibilityError**: do not retry; fix API-version configuration (see below).

### Retrying writes safely (idempotency)

By default, the SDK does **not** retry non-`GET`/`HEAD` requests, because a retry can duplicate side effects (e.g., “create note” twice).

If you implement retries around writes, make them idempotent:

- Prefer endpoints that are naturally idempotent (e.g., “set field value to X” rather than “append note”).
- If the API supports an idempotency key header, use it (store the key per logical operation and reuse it on retry).
- If the API does not support idempotency keys, consider application-level deduping (e.g., deterministic external IDs, or checking for an existing resource before creating a new one).

### Circuit breaker (fail fast during outages)

For sustained 5xx/timeout/network failures, a circuit breaker can protect your system (and Affinity) from retry storms.

Minimal pattern:

```python
import time

class SimpleCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 10, open_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._failures = 0
        self._open_until: float | None = None

    def allow(self) -> bool:
        if self._open_until is None:
            return True
        if time.monotonic() >= self._open_until:
            self._open_until = None
            self._failures = 0
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_until = time.monotonic() + self.open_seconds
```

### Alerting guidance

Common triggers:

- Any sustained 401/403 (credentials/permissions regressions).
- Sustained 429s (rate-limit pressure): alert and reduce concurrency / increase backoff.
- Elevated 5xx/timeouts/network errors (provider outage or network problem).

When alerting, include `e.diagnostics.request_id` (when present) to speed up support/debugging.

## Merged entities

When you access a company or person that has been merged into another record, the API returns a 422 instead of a 404. The SDK raises a `MergedEntityError` (or `CompanyMergedError`/`PersonMergedError`) with structured access to the merge target:

```python
from affinity import Affinity
from affinity.exceptions import CompanyMergedError, MergedEntityError

try:
    company = client.companies.get(CompanyId(old_id))
except CompanyMergedError as e:
    # Follow the merge to the surviving record
    company = client.companies.get(CompanyId(e.target_id))
except MergedEntityError as e:
    # Generic handler (works for any entity type)
    print(f"Entity {e.source_id} merged into {e.target_id}")
```

`MergedEntityError` is a subclass of `ValidationError`, so existing `except ValidationError` handlers continue to work. The entity-specific subclasses (`CompanyMergedError`, `PersonMergedError`) are auto-detected from the request URL.

**CLI**: The CLI surfaces this as `error.type: "entity_merged"` with `sourceId`, `targetId`, and `entityType` in `error.details`. Exit code is 4 (same as not-found — the entity is effectively gone from its original ID).

## V1→V2 eventual consistency

The SDK uses V1 API for writes (create/update/delete) and V2 API for reads (get/list). Due to eventual consistency between V1 and V2 backends, a `get()` call **immediately after** `create()` may return a 404 `NotFoundError` even though the entity was successfully created.

This typically resolves within 100-500ms, but under load the delay can be longer.

### Solutions

**Option 1: Use the returned object (recommended)**

The `create()` method returns the created entity - use it directly instead of re-fetching:

```python
person = client.persons.create(PersonCreate(first_name="Jane", last_name="Doe"))
# Use person directly - no need to call get()
print(person.id, person.first_name)
```

**Option 2: Use the `retries` parameter**

The `get()` methods on `persons`, `companies`, and `opportunities` accept a `retries` parameter:

```python
person = client.persons.create(PersonCreate(first_name="Jane", last_name="Doe"))
# Retry up to 3 times on 404 with exponential backoff
fetched = client.persons.get(person.id, retries=3)
```

With `retries=3`, the SDK will retry up to 3 times on 404 with exponential backoff (0.5s, 1s, 1.5s).

**Note**: The default is `retries=0` (fail fast). This is intentional - it keeps the SDK predictable and avoids hidden delays. Only use `retries > 0` when you specifically need to handle eventual consistency after writes.

### Stale data after update

A related issue: after calling `update()`, a subsequent `get()` may return **stale data** (the old values) even though the update succeeded. Unlike the 404 case, the request succeeds with `200 OK` but returns outdated information.

The `retries` parameter doesn't help here because the request succeeds - it just returns stale data.

**Solution: Trust the write response**

The `update()` method returns the updated entity from V1 (strongly consistent). Use it directly:

```python
# Good: Use the response from update()
updated_person = client.persons.update(person.id, PersonUpdate(first_name="Jane"))
print(updated_person.first_name)  # "Jane" - immediate, from V1 response

# Risky: Re-fetching may return stale data
client.persons.update(person.id, PersonUpdate(first_name="Jane"))
person = client.persons.get(person.id)  # May still show old name!
```

This applies to all `update()` methods: persons, companies, opportunities, notes, etc.

## Rate limits

If you are consistently hitting 429s, see [Rate limits](rate-limits.md) for strategies and the rate limit APIs.

## API versions and beta endpoints

If you see `BetaEndpointDisabledError`, enable beta endpoints:

```python
from affinity import Affinity

client = Affinity(api_key="your-api-key", enable_beta_endpoints=True)
```

If you see `VersionCompatibilityError`, this often indicates a V2 API version mismatch between your API key settings and what the SDK expects. Check your API key’s “Default API Version”, and consider setting `expected_v2_version` for clearer diagnostics:

```python
from affinity import Affinity

client = Affinity(api_key="your-api-key", expected_v2_version="2024-01-01")
```

See [API versions & routing](api-versions-and-routing.md) and the [Glossary](../glossary.md).

## Next steps

- [Rate limits](rate-limits.md)
- [Troubleshooting](../troubleshooting.md)
- [Configuration](configuration.md)
- [API versions & routing](api-versions-and-routing.md)
- [Exceptions reference](../reference/exceptions.md)
