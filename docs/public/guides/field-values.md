# Field Values

Field values store custom data on entities (persons, companies, opportunities, list entries). This guide covers common patterns for working with field values.

## Reading and changing a company's or person's fields

The `companies` and `persons` services read and write field values through V2. This is the usual
way to change a field such as a company's Stage:

```python
from affinity import Affinity
from affinity.types import CompanyId

with Affinity(api_key="your-api-key") as client:
    values = client.companies.get_field_values(CompanyId(123))
    print(values.get_value("field-456"))

    print(client.companies.get_field_value(CompanyId(123), "field-456"))

    # Replace one field's value
    client.companies.update_field_value(CompanyId(123), "field-456", "Series A")

    # Several fields in one request (all or nothing)
    client.companies.batch_update_fields(
        CompanyId(123), {"field-456": "Series A", "field-789": 5}
    )
```

`persons` has the same methods. For fields on a list (including an opportunity's fields), use
the list entry: `client.lists.entries(list_id).update_field_value(entry_id, field_id, value)`
and `batch_update_fields`. Field names are resolved to ids with
[`FieldResolver`](../reference/models.md#fieldresolver) or `fields.get_by_name()`; see
[Field types and values](field-types-and-values.md) for value types.

## The V1 `field_values` service

The rest of this guide covers `client.field_values`, which calls V1's field-value endpoints
directly. It returns one `FieldValue` per value (with its id), across persons, companies,
opportunities and list entries.

## Getting field values for an entity

Use `field_values.list()` to get all field values for a specific entity:

```python
from affinity import Affinity
from affinity.types import PersonId

with Affinity(api_key="your-api-key") as client:
    field_values = client.field_values.list(person_id=PersonId(123))
    for fv in field_values:
        print(f"{fv.field_id}: {fv.value}")
```

Exactly one entity ID must be provided (`person_id`, `company_id`, `opportunity_id`, or `list_entry_id`).

## Looking up a specific field value

Use `get_for_entity()` to get a single field value without iterating:

```python
from affinity import Affinity
from affinity.types import FieldId, PersonId

with Affinity(api_key="your-api-key") as client:
    # Returns FieldValue or None if not found
    status = client.field_values.get_for_entity(
        FieldId("field-123"),
        person_id=PersonId(456),
    )

    if status is None:
        print("Field is empty")
    else:
        print(f"Value: {status.value}")

    # With default value
    status = client.field_values.get_for_entity(
        FieldId("field-123"),
        person_id=PersonId(456),
        default="N/A",
    )
```

## Batch field value queries

Use `list_batch()` to get field values for multiple entities:

```python
from affinity import Affinity
from affinity.types import PersonId

with Affinity(api_key="your-api-key") as client:
    person_ids = [PersonId(1), PersonId(2), PersonId(3)]

    # Returns dict mapping entity_id -> list of field values
    fv_map = client.field_values.list_batch(person_ids=person_ids)

    for person_id, field_values in fv_map.items():
        print(f"Person {person_id}: {len(field_values)} field values")
```

Handle errors gracefully with `on_error`:

```python
# Skip entities that fail (e.g., deleted or inaccessible)
fv_map = client.field_values.list_batch(
    person_ids=person_ids,
    on_error="skip",  # or "raise" (default)
)
```

**Performance note:** This makes one API call per entity (O(n) calls). For parallel execution, use the async client.

## Field validation

Check if a field exists before using it:

```python
from affinity import Affinity
from affinity.types import FieldId

with Affinity(api_key="your-api-key") as client:
    if client.fields.exists(FieldId("field-123")):
        # Field exists, safe to use
        pass
```

Look up a field by name:

```python
# Case-insensitive name lookup
field = client.fields.get_by_name("Primary Email Status")

if field:
    # Use field.id for subsequent operations
    fv = client.field_values.get_for_entity(field.id, person_id=pid)
```

## Getting person with field values

When you need both person data and field values, use `include_field_values` to save an API call:

```python
from affinity import Affinity
from affinity.types import PersonId

with Affinity(api_key="your-api-key") as client:
    # Single API call returns person + field values
    person = client.persons.get(
        PersonId(123),
        include_field_values=True,
    )

    # Field values are attached to the person object (may be None if not returned)
    if person.field_values:
        for fv in person.field_values:
            print(f"{fv.field_id}: {fv.value}")
```

## Resource management

Always use the client as a context manager to ensure proper cleanup:

```python
# Recommended: context manager ensures cleanup
with Affinity(api_key="your-api-key") as client:
    field_values = client.field_values.list(person_id=PersonId(123))

# Or close explicitly
client = Affinity(api_key="your-api-key")
try:
    field_values = client.field_values.list(person_id=PersonId(123))
finally:
    client.close()
```

If a client is not properly closed, a `ResourceWarning` will be raised during garbage collection. For async code, use `async with` or `await client.close()`.

## Next steps

- [Field types & values](field-types-and-values.md) - Field type mapping and FieldId semantics
- [Filtering](filtering.md) - Filter entities by field values
- [Pagination](pagination.md) - Iterate through large result sets
