# Troubleshooting

## 401 / 403 errors

- Verify your API key is correct.
- Ensure the key has access to the entities you’re querying.

## 404 immediately after create

If you get a 404 `NotFoundError` when calling `get()` right after `create()`, this is due to V1→V2 eventual consistency. The entity exists but hasn't propagated to V2 yet. This typically resolves within 100-500ms, but can take longer under load.

**Solutions:**
- Use the object returned by `create()` directly (recommended)
- Use `get(..., retries=3)` to retry with backoff

See [V1→V2 eventual consistency](guides/errors-and-retries.md#v1v2-eventual-consistency) for details.

## 422 on merged entity

If you get a 422 `MergedEntityError` when calling `get()` on a company or person, the entity was merged into another record. Unlike deleted entities (which return 404), merged entities return 422 with the target ID.

**Solution:** Catch `CompanyMergedError` or `PersonMergedError` and use `e.target_id` to follow the merge:

```python
from affinity.exceptions import CompanyMergedError

try:
    company = client.companies.get(CompanyId(old_id))
except CompanyMergedError as e:
    company = client.companies.get(CompanyId(e.target_id))
```

**CLI:** The error JSON includes `error.details.targetId` — scripts can use `jq '.error.details.targetId'` to extract it.

See [Merged entities](guides/errors-and-retries.md#merged-entities) for details.

## Stale data after update

If `get()` returns old values after calling `update()`, this is also due to V1→V2 eventual consistency. The update succeeded, but V2 hasn't synced yet. Like the 404 case, this typically resolves within 100-500ms.

**Solution:** Use the object returned by `update()` directly - it contains the updated data.

See [Stale data after update](guides/errors-and-retries.md#stale-data-after-update) for details.

## Underscores escaped in note content

When you create or update a note, the Affinity API escapes underscores in the content:

```
Input:  "test_note_content"
Output: "test\_note\_content"
```

This is server-side markdown escaping and cannot be prevented. If you need to search for or compare note content, account for this transformation:

```python
# When checking note content, allow for escaped underscores
original = "my_note"
from_api = note.content
assert from_api in (original, original.replace("_", r"\_"))
```

## Rate limits

The client tracks rate-limit state and retries some requests automatically.
See [Client](reference/client.md) and [Exceptions](reference/exceptions.md).

## Debugging

Enable [hooks](guides/debugging-hooks.md) or set `log_requests=True` on the client.

## CLI: Disable update notifications

The CLI shows update notifications in interactive sessions. To disable:

**For a single command:**
```bash
xaffinity --no-update-check person ls
```

**Via environment variable:**
```bash
export XAFFINITY_NO_UPDATE_CHECK=1
```

**Via config file (`~/.config/xaffinity/config.toml`):**
```toml
[default]
update_check = false
```

**Automatic suppression:** Notifications are automatically hidden when using `--quiet`, `--output json`, in CI environments, or when not attached to a terminal.

See [CLI Update Notifications](cli/index.md#update-notifications) for more details.

## Next steps

- [Getting started](getting-started.md)
- [Examples](examples.md)
- [Debugging hooks](guides/debugging-hooks.md)
- [Errors & retries](guides/errors-and-retries.md)
