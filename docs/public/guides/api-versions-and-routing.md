# API versions & SDK routing

??? info "Quick glossary"
    - **V1 API**: legacy endpoints at `https://api.affinity.co`
    - **V2 API**: newer endpoints at `https://api.affinity.co/v2`
    - **V2 API version**: dated versions like `2024-01-01` within V2 (configured in Affinity)
    - **Default API Version**: your Affinity setting that selects the V2 version
    - **Beta endpoints**: newer V2 endpoints that require opt-in

The SDK uses V2 endpoints wherever V2 can do the job, and V1 where it can't.

## What this means in practice

- **Reads** (list/get/search) use **V2**, except notes `list`/`get`, interactions per entity,
  files per entity, field values on companies and persons, and list sizes.
- **Writes** use **V2** for webhooks, reminders, notes and list create. **V1** remains for
  writes V2 doesn't offer: company create/update, person create/update/delete, opportunity
  create/update/delete, adding to a list, interactions, entity files, and field values on
  companies and persons. (Affinity's V2 spec also documents person create/delete, opportunity
  rename/delete and adding to a list, but the API didn't serve them when tested, 2026-10-12.)

### Documented V1 exceptions

Where V2 has the operation but can't do one part of it, that case goes through V1:

| Operation | Goes through V1 when… |
|---|---|
| `lists.create` | `owner_id` or `additional_permissions` is set |
| `reminders.update` | `type` is set (V2 can't change a reminder's type) |
| `reminders.update` / `delete` | V2 refuses (403: only the creator or owner may change it on V2); retried once on V1 |
| `files.get` / `list` / `iter` | always (only V1 has the file's person, company or opportunity and works under any version pin; `files.feed` lists through V2) |
| `files.get_download_url` | when V2 can't answer: pinned older than 2026-09-17, or V2 refuses (V2 otherwise, in one request) |
| `lists.get_size` | always (V2 has no list size) |

The [API coverage](../reference/api-coverage.md) page lists every operation, the SDK call that
sends it, and which V1 operations are kept and why.

### Eventual consistency caveat

When a write goes to V1 and the next read comes from V2, there can be a brief delay (typically
100-500ms) before the change shows up. A `get()` immediately after a V1 `create()` may return
404. See [V1→V2 eventual consistency](errors-and-retries.md#v1v2-eventual-consistency).

## API versions per operation

Operations Affinity added after 2024-01-01 send the version they need (for example
`2026-07-15` for webhooks, `2026-09-17` for reminder updates); other V2 calls use your API key's
default version. A client pinned to an older version (`affinity_api_version=`, CLI
`--api-version`) gets `ApiVersionTooOldError` for operations newer than the pin, before
anything is sent. See [Affinity API versions](api-versions.md).

## Beta endpoints

Affinity marks some V2 endpoints as beta (webhooks, reminder get/update/delete, file reads,
the rate-limit endpoint, and others). The SDK calls them like any other endpoint; the
[API coverage](../reference/api-coverage.md) page marks each one. Beta endpoints can change
shape before Affinity makes them generally available.

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
