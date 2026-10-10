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

## Dropdown options

```python
from affinity import Affinity
from affinity.types import ListId

with Affinity.from_env() as client:
    # with_status_types=True reports status fields as "status-dropdown" (API version 2026-07-15)
    for option in client.lists.get_field_dropdown_options(
        ListId(123), "field-456", with_status_types=True
    ):
        print(option.id, option.text, option.type, option.status_category)

    option = client.lists.create_field_dropdown_option(
        ListId(123), "field-456", option_type="ranked-dropdown", text="Due diligence",
        rank=5, color="blue",
    )
    client.lists.update_field_dropdown_option(ListId(123), "field-456", option.id, text="DD")
    # Cannot be undone: also clears the field on every entry set to this option
    client.lists.delete_field_dropdown_option(ListId(123), "field-456", option.id)
```

Global company and person fields' options can be read
(`client.companies.get_field_dropdown_options("field-789")`) but not changed.

## Meeting transcripts

```python
from datetime import datetime, timezone

with Affinity.from_env() as client:
    for transcript in client.transcripts.iter(
        created_after=datetime(2025, 6, 1, tzinfo=timezone.utc)
    ):
        print(transcript.id, transcript.created_at, transcript.note.type if transcript.note else None)

    full = client.transcripts.get(123)
    for fragment in client.transcripts.iter_fragments(full.id):
        print(fragment.start_timestamp, fragment.speaker, fragment.content)
```

Only transcripts the API key's user may see are returned.

## Notes as V2 (creator, HTML content, attachments)

```python
from datetime import datetime, timezone

from affinity.types import CompanyId, NoteId

since = datetime(2025, 6, 1, tzinfo=timezone.utc)
with Affinity.from_env() as client:
    for note in client.notes.iter_v2(created_after=since, includes=True):
        print(note.id, note.type, note.creator, note.replies_count, note.companies_total)

    for reply in client.notes.iter_replies(NoteId(123)):
        print(reply.content.html)

    for note in client.companies.iter_notes(CompanyId(456)):  # also persons / opportunities
        print(note.created_at, note.type)
```

`client.notes.list()` / `get()` (V1 `Note`) are unchanged. Company notes need Affinity API
version 2026-07-15 or newer; Affinity may refuse opportunity notes (`AuthorizationError`).

## Org-wide activity, merge history and relationships

```python
from datetime import datetime, timezone

from affinity.types import CompanyId

week_ago = datetime(2025, 6, 1, tzinfo=timezone.utc)
with Affinity.from_env() as client:
    for email in client.interactions.iter_emails(after=week_ago):
        print(email.sent_at, email.subject, email.from_, email.to_total)
    for meeting in client.interactions.iter_meetings(after=week_ago):
        print(meeting.start_time, meeting.title, meeting.attendees_total)

    for merge in client.companies.iter_merges(status="failed"):
        print(merge.id, merge.primary_company_id, merge.error_message)

    for rel in client.companies.iter_relationships(CompanyId(123), min_score=0.3):
        print(rel.person1.first_name, rel.person2.first_name, rel.interaction_score)
```

Interaction lists cover only what the API key's user may see. Merge history needs the "Manage
duplicates" permission and the organization admin role. Relationships need Affinity API version
2026-07-15 or newer.

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
