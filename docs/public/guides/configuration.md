# Configuration

This guide documents the knobs exposed on `Affinity` / `AsyncAffinity`.

## Load from environment

```python
from affinity import Affinity

client = Affinity.from_env()
```

By default, `from_env()` reads the `AFFINITY_API_KEY` environment variable. You can customize this:

```python
from affinity import Affinity

# Use a different environment variable
client = Affinity.from_env(env_var="MY_AFFINITY_KEY")
```

### Using .env files

To load a local `.env` file, install the optional extra and set `load_dotenv=True`:

```bash
pip install "affinity-sdk[dotenv]"
```

```python
from affinity import Affinity

# Load from .env in current directory
client = Affinity.from_env(load_dotenv=True)

# Load from a specific .env file
client = Affinity.from_env(load_dotenv=True, dotenv_path="/path/to/.env.local")

# Override existing environment variables with .env values
client = Affinity.from_env(load_dotenv=True, dotenv_override=True)
```

## Timeouts

```python
from affinity import Affinity

client = Affinity(api_key="your-api-key", timeout=60.0)
```

For file downloads, you can override timeouts per call, and (for streaming downloads) set a total time budget:

```python
from affinity import Affinity
from affinity.types import FileId

with Affinity(api_key="your-api-key") as client:
    for chunk in client.files.download_stream(FileId(123), timeout=60.0, deadline_seconds=300):
        ...
```

### Streaming download parameters

| Parameter | Description |
|-----------|-------------|
| `timeout` | Per-request timeout in seconds (default: client timeout) |
| `deadline_seconds` | Total time budget for the entire download including retries |

If `deadline_seconds` is exceeded, the SDK raises `TimeoutError`.

### Preserving file metadata

To preserve server-provided file metadata (like filename and size), use `download_stream_with_info(...)`:

```python
from affinity import Affinity
from affinity.types import FileId

with Affinity(api_key="your-api-key") as client:
    downloaded = client.files.download_stream_with_info(FileId(123), timeout=60.0, deadline_seconds=300)
    filename = downloaded.filename or client.files.get(FileId(123)).name
    for chunk in downloaded.iter_bytes:
        ...
```

The `DownloadedFile` object provides:

- `filename`: Original filename from Content-Disposition header (may be `None`)
- `content_type`: MIME type from Content-Type header
- `size`: File size in bytes (may be `None` if server doesn't provide it)
- `iter_bytes`: Iterator yielding file content chunks

### Error handling for downloads

```python
from affinity import Affinity
from affinity.types import FileId
from affinity.exceptions import AffinityError

with Affinity(api_key="your-api-key") as client:
    try:
        with open("output.pdf", "wb") as f:
            for chunk in client.files.download_stream(FileId(123), deadline_seconds=120):
                f.write(chunk)
    except TimeoutError:
        print("Download timed out - file may be too large or connection too slow")
    except AffinityError as e:
        print(f"Download failed: {e}")
```

!!! note "No resume support"
    The SDK does not currently support resuming partial downloads. If a download fails, you must restart from the beginning.

## Retries

- Retries apply to safe/idempotent methods (by default `GET`/`HEAD`).
- Tune with `max_retries`.

```python
from affinity import Affinity

client = Affinity(api_key="your-api-key", max_retries=5)
```

## Download redirects (files)

Affinity file downloads may redirect to externally-hosted signed URLs. By default, the SDK refuses `http://` redirects.

If you must allow insecure redirects (not recommended), opt in explicitly:

```python
from affinity import Affinity

client = Affinity(api_key="your-api-key", allow_insecure_download_redirects=True)
```

## Caching

Caching is optional and currently targets metadata-style responses (e.g., field metadata). Default TTL is 300 seconds (5 minutes).

```python
from affinity import Affinity

# Enable with default 5-minute TTL
client = Affinity(api_key="your-api-key", enable_cache=True)

# Custom TTL (in seconds)
client = Affinity(api_key="your-api-key", enable_cache=True, cache_ttl=600.0)
```

## Logging and hooks

```python
from affinity import Affinity
from affinity.hooks import HookEvent

def on_event(event: HookEvent) -> None:
    print(event.type)

client = Affinity(
    api_key="your-api-key",
    log_requests=True,
    on_event=on_event,
    hook_error_policy="swallow",  # or "raise"
)
```

Notes:

- For the synchronous client (`Affinity`), `on_event` must be a synchronous function. If it returns an awaitable, the SDK raises `ConfigurationError`.
- For the async client (`AsyncAffinity`), `on_event` can be sync or async.

Affinity downloads may redirect to externally-hosted signed URLs; external URLs are redacted in events by default.
If you want to change that behavior, configure `Policies(external_hooks=...)`:

```python
from affinity import Affinity, ExternalHookPolicy
from affinity.policies import Policies

client = Affinity(
    api_key="your-api-key",
    on_event=lambda e: print(e.type),
    policies=Policies(external_hooks=ExternalHookPolicy.REDACT),  # or SUPPRESS / EMIT_UNSAFE
)
```

## Disable writes (policy)

To guarantee the SDK does not perform write operations (POST/PUT/PATCH/DELETE, including uploads),
disable writes via policy:

```python
from affinity import Affinity
from affinity.policies import Policies, WritePolicy

client = Affinity(api_key="your-api-key", policies=Policies(write=WritePolicy.DENY))
```

## HTTP transport injection (advanced)

For testing/mocking without real network calls, inject an `httpx` transport:

```python
import httpx
from affinity import Affinity

def handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path.endswith("/lists"):
        return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)
    return httpx.Response(404, json={}, request=request)

client = Affinity(api_key="your-api-key", transport=httpx.MockTransport(handler))
```

## V1/V2 URLs and auth mode

```python
from affinity import Affinity

client = Affinity(
    api_key="your-api-key",
    v1_base_url="https://api.affinity.co",
    v2_base_url="https://api.affinity.co/v2",
    v1_auth_mode="bearer",  # or "basic"
)
```

## Beta endpoints and the Affinity API version

Opt into beta endpoints, and pin the Affinity V2 API version (sent as `X-Affinity-Api-Version`
on every V2 request; default: your API key's default version):

```python
from affinity import Affinity

client = Affinity(
    api_key="your-api-key",
    enable_beta_endpoints=True,
    affinity_api_version="2026-09-17",
)
```

See also:

- [Affinity API versions](api-versions.md)
- [API versions & routing](api-versions-and-routing.md)
- [Errors & retries](errors-and-retries.md)

## Next steps

- [Getting started](../getting-started.md)
- [Debugging hooks](debugging-hooks.md)
- [Rate limits](rate-limits.md)
- [API reference](../reference/client.md)
