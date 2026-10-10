# Examples

All examples assume `AFFINITY_API_KEY` is set:

```bash
export AFFINITY_API_KEY="your-api-key"
```

Run an example with:

```bash
python examples/basic_usage.py
```

## Basic

- [`examples/basic_usage.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/basic_usage.py) — small end-to-end tour of core services
- [`examples/advanced_usage.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/advanced_usage.py) — deeper patterns and best practices

## Async

- [`examples/async_lifecycle.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/async_lifecycle.py) — async client lifecycle and usage

## Filtering and hooks

- [`examples/filter_builder.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/filter_builder.py) — build V2 filter expressions with `affinity.F`
- [`examples/hooks_debugging.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/hooks_debugging.py) — request/response hooks for debugging

## Lists, resolve helpers, tasks

- [`examples/list_management.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/list_management.py) — list CRUD and entry operations
- [`examples/resolve_helpers.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/resolve_helpers.py) — resolve helpers (IDs from external identifiers)
- [`examples/task_polling.py`](https://github.com/yaniv-golan/affinity-sdk/blob/main/examples/task_polling.py) — polling long-running tasks

## Field Value Changes (audit history)

Query the change history for a specific field on an entity:

```python
from affinity import Affinity
from affinity.types import CompanyId, FieldId, FieldValueChangeAction

with Affinity.from_env() as client:
    # Get all changes to field "field-123" for company 456
    changes = client.field_value_changes.list(
        FieldId("field-123"),
        company_id=CompanyId(456),
    )

    for change in changes:
        print(f"{change.changed_at}: {change.value} (action={change.action_type})")

    # Filter by action type (e.g., only updates)
    updates = client.field_value_changes.list(
        FieldId("field-123"),
        company_id=CompanyId(456),
        action_type=FieldValueChangeAction.UPDATE,
    )
```

Without an entity selector, `list()` returns a field's changes for every entity; bound it
with `changed_after` and/or `limit`. To page through a whole history oldest first, use
`iter_all()`:

```python
from datetime import datetime, timezone

with Affinity.from_env() as client:
    for change in client.field_value_changes.iter_all(
        FieldId("field-123"), changed_after=datetime(2025, 1, 1, tzinfo=timezone.utc)
    ):
        print(change.id, change.changed_at, change.list_entry_id)
```

For changes across all fields and entities (delta sync, audits by changer), use the V2
endpoint:

```python
with Affinity.from_env() as client:
    for change in client.field_value_changes.iter_global(
        changed_after=datetime(2025, 6, 1, tzinfo=timezone.utc), action_type="update"
    ):
        print(change.field.name, change.entity.id, change.value, change.changer)
```

`changed_after` is inclusive: when syncing, store the latest `changed_at` you processed and
skip ids you already have.

## V1-only exception: company -> people associations

V2 does not expose a company -> people association endpoint yet. These helpers use the v1
organizations API and are documented as exceptions:

```python
from affinity import Affinity
from affinity.types import CompanyId

with Affinity.from_env() as client:
    person_ids = client.companies.get_associated_person_ids(CompanyId(224925494))
    people = client.companies.get_associated_people(CompanyId(224925494), max_results=5)
```
