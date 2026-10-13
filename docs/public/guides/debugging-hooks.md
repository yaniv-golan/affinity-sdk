# Debugging hooks

You can attach hooks for debugging and observability.

Recommended: use `on_event`, which provides richer lifecycle events (retries, redirects, streaming).

```python
from affinity import Affinity
from affinity.hooks import HookEvent

def on_event(event: HookEvent) -> None:
    # Each event has a `.type` discriminator (e.g., "request_started", "request_failed", ...)
    print(event.type)

with Affinity(api_key="your-api-key", on_event=on_event) as client:
    client.companies.list()
```

The sync `Affinity` client never awaits a hook: an `async def` hook raises
`ConfigurationError` when the client is created. Use `AsyncAffinity` for async hooks.

## Common event types

- Requests: `request_started`, `request_retrying`, `request_succeeded`, `request_failed`
- Redirects (downloads): `redirect_followed`
- Responses: `response_headers_received`
- Streaming downloads: `stream_completed`, `stream_aborted`, `stream_failed`

For streaming downloads, `response_headers_received` fires when headers arrive; one terminal stream event is emitted when iteration ends:

- `stream_completed`: the full body was consumed
- `stream_aborted`: iteration was interrupted (e.g., cancellation, keyboard interrupt, iterator closed)
- `stream_failed`: an error occurred mid-stream

The older `on_request/on_response/on_error` hooks are still supported:

```python
from affinity import Affinity

def on_request(req) -> None:
    print("->", req.method, req.url)

def on_response(res) -> None:
    cache = " (cache hit)" if res.cache_hit else ""
    print("<-", res.status_code, res.request.url, cache)

def on_error(err) -> None:
    print("!!", type(err.error).__name__, err.request.url)

with Affinity(api_key="your-api-key", on_request=on_request, on_response=on_response, on_error=on_error) as client:
    client.companies.list()
```

## External (signed) download URLs

File downloads may redirect to externally-hosted signed URLs. By default, the SDK redacts external URLs in hook events (query/fragment are removed).
You can change this behavior with `Policies(external_hooks=...)`:

- `ExternalHookPolicy.REDACT` (default): emit events, but redact external URLs
- `ExternalHookPolicy.SUPPRESS`: do not emit events for external hops
- `ExternalHookPolicy.EMIT_UNSAFE`: emit full external URLs (unsafe; may leak signed query params)

```python
from affinity import Affinity, ExternalHookPolicy
from affinity.policies import Policies

client = Affinity(
    api_key="your-api-key",
    on_event=lambda e: print(e.type),
    policies=Policies(external_hooks=ExternalHookPolicy.REDACT),
)
```

If you need request interception for tests (without real network calls), use transport injection:

```python
import httpx
from affinity import Affinity

client = Affinity(api_key="your-api-key", transport=httpx.MockTransport(lambda req: httpx.Response(200)))
```

## CLI

The `xaffinity` CLI can also trace requests/responses/errors:

```bash
xaffinity --trace --no-progress whoami
```

## Next steps

- [Configuration](configuration.md)
- [Troubleshooting](../troubleshooting.md)
- [Errors & retries](errors-and-retries.md)
