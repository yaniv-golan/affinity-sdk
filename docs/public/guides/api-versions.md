# Affinity API versions

Affinity versions its **V2** API by date. Three versions exist today:

| Version | Notes |
|---|---|
| `2024-01-01` | The original V2 API. |
| `2026-07-15` | Adds operations (e.g. `GET /v2/rate-limit`); opportunity list-entry values your API key can't access come back as `{"type": "hidden", "data": null}`; status dropdown options have type `status-dropdown`. |
| `2026-09-17` | Adds operations; field metadata gains `description` / `isRequired`; new value types. |

`current` is also accepted and always means the newest version.

Every API key has a **default version**, chosen in the Affinity dashboard (Settings → API).
A request can override it with the `X-Affinity-Api-Version` header, and every V2 response
reports the version that answered. V1 endpoints are not versioned.

## What the SDK and CLI do by default

Nothing changes unless you ask: the SDK sends **no** version header, so your API key's default
version applies — exactly as before. To find out what that default is:

```bash
xaffinity whoami            # "Key default API version: 2024-01-01" (with -v)
xaffinity --json whoami     # data.keyDefaultApiVersion
xaffinity --json config check-key   # data.keyDefaultApiVersion (best-effort)
```

```python
client.get_key_default_api_version()   # one unversioned V2 request
```

## Pinning a version

When you pin a version, it is sent on **every V2 request** — including pages fetched by
following `nextUrl`. It is never sent to V1 endpoints, and it is stripped when a download
redirects to another host (like credentials).

### Python SDK

```python
from affinity import Affinity, AsyncAffinity

client = Affinity(api_key="...", affinity_api_version="2026-09-17")
async_client = AsyncAffinity(api_key="...", affinity_api_version="current")
```

The parameter is called `affinity_api_version` (not `api_version`) because "V1/V2" is
already called the API version throughout the SDK.

### CLI

```bash
xaffinity --api-version 2026-09-17 company ls
export AFFINITY_API_VERSION=2026-09-17      # environment
```

Or per profile in the config file (`xaffinity config path`):

```toml
[profiles.newest]
api_version = "2026-09-17"
```

Precedence: `--api-version` > `AFFINITY_API_VERSION` > profile `api_version` > the key's
default. An empty value falls through to the next source.

### MCP server

Set `AFFINITY_API_VERSION` (or the **Affinity API Version** field in Claude Desktop's
extension settings). `AFFINITY_PROFILE` is passed through too.

### Accepted values

- `2024-01-01`, `2026-07-15`, `2026-09-17`, `current` (surrounding whitespace is ignored);
- any other `YYYY-MM-DD` date is sent with a warning (Affinity decides whether it exists);
- `auto` / `key-default` (or unset): no header, use the key's default — useful to override an
  environment variable or profile from the command line;
- anything else is rejected before any request is made (`ConfigurationError`; CLI exit code 2).

If Affinity rejects the version (HTTP 400 on `X-Affinity-Api-Version`), the SDK raises
`UnsupportedApiVersionError` (CLI error type `api_version_error`, exit code 2) naming the
value. There is **no silent fallback** to another version.

## Which version answered?

The SDK records the version Affinity reports on every V2 response, including responses served
from its cache:

```python
client.affinity_api_versions_seen   # frozenset({"2026-09-17"})
```

Every CLI JSON result carries it in `meta.affinityApiVersion` — a string, or a sorted list
(plus a warning) in the unusual case that one command saw more than one version, for example
because the key's default was changed mid-run. The `query` command reports it in
`meta.affinityApiVersion` with `--include-meta`.

!!! note "Not `meta.apiVersion`"
    Error details already use `apiVersion` for `v1`/`v2`; the dated version is always called
    `affinityApiVersion`.

## Calls that need a newer version

A few calls only work (or are only out of beta) from a given version on. For those the SDK
sends a minimum version for that one request:

- **Not pinned:** the request carries the minimum version, even if your key's default is
  newer. Every other request still uses your key's default.
- **Pinned to that version or newer** (or `current`): your pin is sent, as for any request.
- **Pinned to an older version:** the call fails with `ApiVersionTooOldError` (CLI:
  `api_version_error`, exit 2) before anything is sent. Pin a newer version, or remove the pin.

Versions that answered such calls are listed in `client.affinity_api_versions_per_operation`
(a subset of `affinity_api_versions_seen`) and in the CLI's `meta.affinityApiVersionPerOperation`.
They don't trigger the "more than one version" warning. Today the only such calls are company
and person field writes; `meta.affinityApiVersionPerOperation` covers the CLI's main client, which
is the one those commands use.

## Caching

Response shapes differ between versions, so both caches are keyed by the configured version:
the SDK's in-memory cache and the CLI session cache (`AFFINITY_SESSION_CACHE`) never serve an
entry written under one version to a client pinned to another (or to the key default).

## Choosing a version

- **Keep the default** if your scripts work today — nothing changes.
- **Pin a version** for reproducible results across API keys (for example, a new key whose
  default is `2026-09-17` next to an old one on `2024-01-01`), or to use features that only
  newer versions have.
- **Avoid `current`** in long-lived automation: it moves to the newest version as soon as
  Affinity releases one, before this SDK has been tested against it.

## Deprecated: `expected_v2_version`

`Affinity(expected_v2_version=...)` never changed which version was used; it only labelled
diagnostics. It still works, now logs once if the echoed version differs, and emits a
`DeprecationWarning`. Use `affinity_api_version` to pin and `affinity_api_versions_seen` to
check.

## See also

- [API versions & routing](api-versions-and-routing.md) — V1 vs V2 endpoints
- [Configuration](configuration.md)
- [Errors & retries](errors-and-retries.md)
- Affinity: [V2 versioning](https://developer.affinity.co/#section/Getting-Started/Versioning)
