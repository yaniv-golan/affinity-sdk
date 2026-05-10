# Getting started

Requires Python 3.10+.

## Provide your API key

Set `AFFINITY_API_KEY`:

```bash
export AFFINITY_API_KEY="your-api-key"
```

Or use one of the alternative resolution paths — file-based for container deployments
(`AFFINITY_API_KEY_FILE=/run/secrets/affinity_api_key`) or command-based for credential
managers (`AFFINITY_API_KEY_COMMAND="op read op://Personal/Affinity/credential"`). See
[Authentication](guides/authentication.md) for the full resolution chain.

Then create a client from the environment:

```python
from affinity import Affinity

client = Affinity.from_env()
```

To load a local `.env` file, install the optional extra and set `load_dotenv=True`:

```bash
pip install "affinity-sdk[dotenv]"
```

```python
from affinity import Affinity

client = Affinity.from_env(load_dotenv=True)
```

## Disable writes (policy)

If you want the SDK to guarantee it does not perform write operations (POST/PUT/PATCH/DELETE),
disable writes via policy:

```python
from affinity import Affinity
from affinity.policies import Policies, WritePolicy

client = Affinity.from_env(policies=Policies(write=WritePolicy.DENY))
```

## Create a client

```python
from affinity import Affinity

client = Affinity(api_key="your-api-key")
```

Prefer the context manager to ensure resources are closed:

```python
from affinity import Affinity

with Affinity(api_key="your-api-key") as client:
    ...
```

## Make your first request

This snippet covers authentication, a first request, and common failures:

```python
from affinity import Affinity
from affinity.exceptions import AuthenticationError, RateLimitError

try:
    with Affinity.from_env() as client:
        me = client.whoami()
        print(f"Authenticated as: {me.user.email}")
except AuthenticationError:
    print("Check AFFINITY_API_KEY is set correctly")
except RateLimitError as e:
    print(f"Rate limited. Retry after: {e.retry_after}")
```

## Resolve a list by name

If you have a list name from configuration (and not a `ListId`), you can resolve it:

```python
from affinity import Affinity
from affinity.types import ListType

with Affinity.from_env() as client:
    pipeline = client.lists.resolve(name="Deal Pipeline", list_type=ListType.OPPORTUNITY)
    if pipeline is None:
        raise ValueError("List not found")
    for entry in client.lists.entries(pipeline.id).all():
        ...
```

!!! warning "SDK-specific gotchas"
    - Use typed IDs (e.g., `CompanyId(123)`) instead of raw integers.
    - Entity `fields` are only present when requested via `field_ids` or `field_types` parameters. The `entity.fields.requested` boolean indicates whether field data was actually fetched (`True`) or omitted (`False`). When `True`, `entity.fields.data` contains the field values (which may be empty if the entity has no field values). Use `FieldResolver` to look up values by field name instead of raw IDs — see [Field Lookup Patterns](guides/performance.md#field-lookup-patterns).
    - Some write operations still route to V1; see the V1 vs V2 routing guide.

## Sync vs async

- Use `Affinity` for synchronous code.
- Use `AsyncAffinity` for async/await code.

See [Sync vs async](guides/sync-vs-async.md).

## Next steps

- [Authentication](guides/authentication.md)
- [Examples](examples.md)
- [Pagination](guides/pagination.md)
- [Filtering](guides/filtering.md)
- [CSV Export](guides/csv-export.md) (CLI)
- [Errors & retries](guides/errors-and-retries.md)
- [Configuration](guides/configuration.md)
- [Field types & values](guides/field-types-and-values.md)
- [API versions & routing](guides/api-versions-and-routing.md)
- [AI Integrations](ai-integrations/index.md) - MCP Server & Claude Code plugins
- [API reference](reference/client.md)
