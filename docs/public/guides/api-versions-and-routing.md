# API versions & SDK routing

??? info "Quick glossary"
    - **V1 API**: legacy endpoints at `https://api.affinity.co`
    - **V2 API**: newer endpoints at `https://api.affinity.co/v2`
    - **V2 API version**: dated versions like `2024-01-01` within V2 (configured in Affinity)
    - **Default API Version**: your Affinity setting that selects the V2 version
    - **Beta endpoints**: newer V2 endpoints that require opt-in

The SDK prefers V2 endpoints where available and falls back to V1 for operations not yet supported in V2.

## What this means in practice

- **Reads** (list/get/search) are typically **V2**.
- **Writes** (create/update/delete) are often **V1** today.

Example: companies

- `client.companies.get(...)` uses V2
- `client.companies.create(...)` uses V1

### Eventual consistency caveat

Because writes go to V1 and reads come from V2, there can be a brief delay (typically 100-500ms) before newly created entities appear in V2. A `get()` immediately after `create()` may return 404. See [V1→V2 eventual consistency](errors-and-retries.md#v1v2-eventual-consistency) for solutions.

## Beta endpoints

Some V2 endpoints are gated behind `enable_beta_endpoints=True`. If you call a beta endpoint without opt-in, the SDK raises `BetaEndpointDisabledError`.

## Version compatibility errors

If Affinity changes V2 response shapes (or your API key is pinned to an unexpected V2 version), parsing can fail with `VersionCompatibilityError`.

Suggested steps:

1. Check which version answered: `client.affinity_api_versions_seen` (CLI: `meta.affinityApiVersion`), and your API key’s “Default API Version” (`xaffinity whoami`).
2. Pin a version with `affinity_api_version=...` (CLI: `--api-version`). See [Affinity API versions](api-versions.md).

`expected_v2_version=...` is deprecated: it only labels diagnostics (and logs once if the echoed version differs).

References:

- V1 docs: https://api-docs.affinity.co/
- V2 portal: https://developer.affinity.co/
- V2 versioning: https://developer.affinity.co/#section/Getting-Started/Versioning
- Glossary: ../glossary.md

## Next steps

- [Configuration](configuration.md)
- [Sync vs async](sync-vs-async.md)
- [Errors & retries](errors-and-retries.md)
