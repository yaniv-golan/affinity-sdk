---
name: affinity-python-sdk
description: "Writes Python code using the Affinity SDK for CRM data access and automation."
when_to_use: "Use when user asks to write Python scripts for Affinity, mentions affinity-sdk, typed IDs, async client, pagination, or Affinity Python code."
---

# Affinity Python SDK

## IMPORTANT: Write Operations Require Explicit User Request

**Always use read-only mode by default.** Only allow writes when the user explicitly requests data modification.

```python
from affinity.policies import Policies, WritePolicy

# DEFAULT: Read-only mode (prevents accidental data modification)
with Affinity.from_env(policies=Policies(write=WritePolicy.DENY)) as client:
    ...  # Write operations will raise WriteNotAllowedError

# ONLY when user explicitly approves writes:
with Affinity.from_env() as client:
    ...
```

## Installation

```bash
# SDK only (Python API wrapper)
pip install affinity-sdk

# SDK with .env file support
pip install "affinity-sdk[dotenv]"
```

## Client Initialization

```python
from affinity import Affinity, AsyncAffinity
from affinity.policies import Policies, WritePolicy

# RECOMMENDED: Read-only with .env file
with Affinity.from_env(load_dotenv=True, policies=Policies(write=WritePolicy.DENY)) as client:
    me = client.whoami()
    companies = client.companies.all()

# Async client
async with AsyncAffinity.from_env(policies=Policies(write=WritePolicy.DENY)) as client:
    companies = await client.companies.all()
```

## Multi-Source Tasks: Output Only the Summary

When a task combines data from **multiple Affinity sources** (e.g., person + interactions + list entries), fetch everything in one script and **print only the relevant summary**. Never dump raw `model_dump_json()` — it floods the conversation with hundreds of lines the agent must parse just to extract a few facts.

**Do this when:** combining entity details with interactions, cross-referencing list entries with entities, generating reports from multiple queries.

**A single SDK call is fine when:** fetching one entity, listing one page of results, or performing a single write.

### Bad: dumping raw models

```python
person = client.persons.get(PersonId(123))
print(person.model_dump_json(indent=2))  # 200+ lines of raw JSON
```

### Good: extract and print only what's needed

```python
person = client.persons.get(PersonId(123))
print(f"Name: {person.first_name} {person.last_name}")
print(f"Email: {person.primary_email}")
```

### Full example: deals with no recent contact

```python
"""Find pipeline deals with no contact in 30 days."""
from datetime import datetime, timedelta, timezone
from affinity import Affinity
from affinity.policies import Policies, WritePolicy
from affinity.types import InteractionType, FieldType

with Affinity.from_env(load_dotenv=True, policies=Policies(write=WritePolicy.DENY)) as client:
    pipeline = client.lists.resolve(name="Dealflow")
    entries = client.lists.entries(pipeline.id).all(
        field_types=[FieldType.LIST],
        expand=["interactionDates"],
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    stale = []
    for entry in entries:
        last = getattr(entry, "last_interaction_date", None)
        if last is None or last < cutoff:
            stale.append(entry)

    print(f"Deals with no contact in 30 days: {len(stale)}/{len(entries)}")
    for e in stale:
        days = (datetime.now(timezone.utc) - e.last_interaction_date).days if e.last_interaction_date else "never"
        print(f"  {e.entity_name}: last contact {days} days ago")
```

### When to use bash instead

For simple 2-command pipelines (fetch an ID, then use it in a second command), a bash script with `xaffinity session start` + `jq` is lighter weight. See the CLI skill's "Multi-Source Tasks" section.

## Typed IDs (ALWAYS USE)

Prevent mixing up entity types by using typed IDs:

```python
from affinity.types import (
    PersonId, CompanyId, ListId, ListEntryId,
    OpportunityId, FieldId, NoteId, UserId
)

# CORRECT:
person = client.persons.get(PersonId(123))
company = client.companies.get(CompanyId(456))
entries = client.lists.entries(ListId(789))

# UserId is a subtype of PersonId — pass it anywhere PersonId is accepted:
creator = client.persons.get(note.creator_id)  # Works directly, no cast needed

# WRONG - will cause type errors:
person = client.persons.get(123)  # Don't do this!
```

## Pagination Patterns

```python
# Single page (default 100 items)
page = client.companies.list(limit=50)
for company in page.data:
    process(company)
# For next page, use pages() iterator instead

# All items as list (default max 100,000)
all_companies = client.companies.all()

# Adjust limit
companies = client.companies.all(limit=1000)

# Disable limit (use with caution!)
companies = client.companies.all(limit=None)

# Memory-efficient iterator (large datasets)
for person in client.persons.iter():
    process(person)

# Page-by-page iteration
for page in client.companies.pages():
    for company in page.data:
        process(company)

# Progress callback
from affinity import PaginationProgress

def log_progress(p: PaginationProgress) -> None:
    print(f"Page {p.page_number}: {p.items_so_far} items")

for company in client.companies.all(on_progress=log_progress):
    ...
```

## Filtering (Custom Fields Only)

**Note:** Global-entity lists (`companies`, `persons`, `opportunities`) do NOT accept `filter=` — server-side filtering is not supported on these endpoints, and the SDK raises `ValueError` to prevent silently-unfiltered results. Use `search_pages()` for name/domain/email fuzzy search, or filter **list entries** (which support client-side filtering).

```python
from affinity import F

# Global-entity fuzzy search
for page in client.companies.search_pages("Acme"):
    for company in page.data:
        ...

for page in client.persons.search_pages("alex@acme.com"):
    for person in page.data:
        ...

# For list-specific field filters, use list entries (client-side, warned)
entries = client.lists.entries(ListId(123)).list(
    filter=F.field("Department").equals("Sales")
)

# For filters on large lists, prefer saved views (server-side efficient)
entries = client.lists.entries(ListId(123)).list(saved_view_id=SavedViewId(456))
```

**Do NOT pass `filter=` to `client.companies.list()` / `client.persons.list()` / `client.opportunities.list()`** — it raises `ValueError` with a hint to use `search_pages()`.

### Duplicate prevention on `create`

`companies.create()` and `persons.create()` default to `if_not_exists=True`. On conflict they raise `DuplicateEntityError` carrying the existing entity ID so callers can recover without creating a duplicate:

```python
from affinity.exceptions import DuplicateEntityError
from affinity.models import CompanyCreate, CompanyId

try:
    company = client.companies.create(
        CompanyCreate(name="Elssway", domain="elssway.com")
    )
except DuplicateEntityError as e:
    # e.existing_id is the pre-existing company's ID
    # e.existing_is_global is True for global Affinity directory records
    company = client.companies.get(CompanyId(e.existing_id))
```

Pass `if_not_exists=False` only when you deliberately want to create a distinct record that collides on name/domain.

## Services Reference

```python
with Affinity.from_env() as client:
    # Core entities
    client.persons.list() / .get() / .all() / .search()
    client.companies.list() / .get() / .all() / .search()
    client.opportunities.list() / .get() / .all()

    # Lists
    client.lists.list() / .get() / .all()
    client.lists.resolve(name="Pipeline Name")
    client.lists.get_fields(ListId(123))

    # List entries
    entries_service = client.lists.entries(ListId(123))
    entries_service.list() / .get() / .all()
    entries_service.add_person() / .add_company() / .add_opportunity()
    entries_service.update_field_value() / .batch_update_fields()

    # Notes, reminders, interactions
    client.notes.list() / .create()
    client.reminders.list() / .create()
    client.interactions.list(type=..., start_time=..., end_time=..., person_id=...)
    client.interactions.iter(type=..., start_time=..., person_id=...)  # auto-chunks, defaults end_time to now

    # Rate limits
    snapshot = client.rate_limits.snapshot()

    # Identity
    me = client.whoami()
```

## Error Handling

```python
from affinity.exceptions import (
    AffinityError,           # Base class
    AuthenticationError,     # 401 - invalid/missing API key
    AuthorizationError,      # 403 - insufficient permissions
    NotFoundError,           # 404 - entity not found
    ValidationError,         # 400/422 - invalid parameters
    MergedEntityError,       # 422 - entity merged into another (subclass of ValidationError)
    CompanyMergedError,      # 422 - company merged (has source_id, target_id)
    PersonMergedError,       # 422 - person merged (has source_id, target_id)
    RateLimitError,          # 429 - rate limited
    ServerError,             # 500/503 - server errors
    WriteNotAllowedError,    # Write attempted in read-only mode
    TooManyResultsError,     # .all() exceeded limit
)

try:
    company = client.companies.get(CompanyId(123))
except CompanyMergedError as e:
    # Entity was merged — follow to the surviving record
    company = client.companies.get(CompanyId(e.target_id))
except NotFoundError:
    print("Company not found")
except RateLimitError as e:
    print(f"Rate limited. Retry after: {e.retry_after}")
except AffinityError as e:
    print(f"Error: {e}")
    if e.diagnostics:
        print(f"Request ID: {e.diagnostics.request_id}")
```

## Creating Records (requires explicit user approval)

```python
from affinity.models import NoteCreate, ReminderCreate
from affinity.types import NoteType, ReminderType
from datetime import datetime, timedelta

# Add entity to list
entries_service = client.lists.entries(ListId(123))
entry = entries_service.add_person(PersonId(456))
entry = entries_service.add_company(CompanyId(789))

# Create note
note = client.notes.create(NoteCreate(
    content="<p>Meeting notes</p>",
    type=NoteType.HTML,
    person_ids=[PersonId(123)],
))

# Create reminder
reminder = client.reminders.create(ReminderCreate(
    owner_id=UserId(me.user.id),
    type=ReminderType.ONE_TIME,
    content="Follow up",
    due_date=datetime.now() + timedelta(days=7),
    person_id=PersonId(123),
))

# Update field value on list entry
entries_service.update_field_value(
    ListEntryId(456),
    FieldId(789),
    "New Value"
)

# Batch update multiple fields
entries_service.batch_update_fields(
    ListEntryId(456),
    {FieldId(789): "Value1", FieldId(790): "Value2"}
)
```

## Field Selection

```python
from affinity.types import FieldType

# Request specific field types
client.companies.list(field_types=[FieldType.ENRICHED])
client.persons.get(PersonId(123), field_types=[FieldType.GLOBAL, FieldType.RELATIONSHIP_INTELLIGENCE])

# Check if fields were requested and access data
if company.fields.requested:
    for field_name, value in company.fields.data.items():
        print(f"{field_name}: {value}")

# Available: GLOBAL, LIST, ENRICHED, RELATIONSHIP_INTELLIGENCE
```

## Resolving Fields by Name

**Pattern A: Reading field values by name** — use `FieldResolver.get()`:
```python
from affinity.field_resolver import FieldResolver
from affinity.types import ListId, FieldType, ResolveMode

fields = client.lists.get_fields(ListId(123))
resolver = FieldResolver(fields)

# Fields must be requested when fetching entries — they're NOT populated by default
entries = client.lists.entries(ListId(123)).all(field_types=[FieldType.LIST])

for entry in entries:
    status = resolver.get(entry, "Status")
    owner = resolver.get(entry, "Owner", resolve=ResolveMode.TEXT)
```
`FieldResolver.get()` maps names to values. `FieldResolver.find_field()` maps names to metadata (including FieldId).

**Pattern B: Getting FieldId for write operations** — use `FieldResolver.find_field()`:
```python
# For list-entry fields: use client.lists.get_fields(list_id)
fields = client.lists.get_fields(ListId(123))
resolver = FieldResolver(fields)

status_meta = resolver.find_field("Status")  # -> FieldMetadata | None
if status_meta:
    entries_service = client.lists.entries(ListId(123))
    # For text fields, pass the string directly:
    entries_service.update_field_value(ListEntryId(456), status_meta.id, "Active")
    # For dropdown fields, pass the dropdown option ID (int), not the text:
    #   option = next(o for o in status_meta.dropdown_options if o.text == "Active")
    #   entries_service.update_field_value(entry_id, status_meta.id, option.id,
    #                                      value_type=FieldValueType.DROPDOWN)

# For entity-level fields: use the entity-specific method
# FieldResolver(client.companies.get_fields()) / FieldResolver(client.persons.get_fields())
```

## Enriched Fields

Enriched fields (Phone Number, Source of Introduction, Industry, Location, Description, etc.) are returned on `entity.fields.data` like any other field when you request them via `field_types=[FieldType.ENRICHED]`.

Most enriched fields are writable via the normal `update_field_value()` path using their `FieldMetadata.id`. A small number are purely derived (notably "Current Organization" on persons, which is computed from email domain) and cannot be written — the SDK raises `EnrichedFieldNotWritableError` (subclass of `UnsupportedOperationError`) for these.

```python
from affinity import EnrichedFieldNotWritableError
```

For fresh post-write reads where you want to skip the in-memory field-metadata cache, pass `skip_cache=True` to `client.fields.list(...)`.

## Rate Limits

```python
# Check current status (from cached headers)
snapshot = client.rate_limits.snapshot()
print(f"Per-minute: {snapshot.api_key_per_minute.remaining}/{snapshot.api_key_per_minute.limit}")
print(f"Monthly: {snapshot.org_monthly.remaining}/{snapshot.org_monthly.limit}")

# Refresh from API
refreshed = client.rate_limits.refresh()
```

## Retry Behavior

- **GET/HEAD**: Automatic retries (3 by default) for rate limits and transient errors
- **POST/PUT/PATCH/DELETE**: No automatic retries (to avoid duplicates)

```python
# Configure retries
client = Affinity(api_key="key", max_retries=5)
```

## Documentation

- Full SDK docs: https://yaniv-golan.github.io/affinity-sdk/latest/
