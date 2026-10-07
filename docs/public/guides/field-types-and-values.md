# Field types and values

Many endpoints can return “field values” in addition to the core entity shape.

## Field types

Use `FieldType` to request which field scopes you want:

```python
from affinity import Affinity
from affinity.types import FieldType, PersonId

with Affinity(api_key="your-api-key") as client:
    person = client.persons.get(PersonId(123), field_types=[FieldType.ENRICHED, FieldType.GLOBAL])
    if person.fields.requested:
        print(person.fields.data)
```

Common values include:

- `FieldType.ENRICHED`
- `FieldType.GLOBAL`
- `FieldType.LIST` (only valid for list entry endpoints)
- `FieldType.RELATIONSHIP_INTELLIGENCE`

Notes:

- `FieldType.LIST` is only accepted by list entry endpoints. Company/person endpoints accept
  `ENRICHED`, `GLOBAL`, and `RELATIONSHIP_INTELLIGENCE` only — the SDK raises `ValueError` if
  you pass `LIST` to a company or person method.

## Field IDs

If you know specific field IDs, you can request only those:

```python
from affinity import Affinity
from affinity.types import FieldId, FieldType

with Affinity(api_key="your-api-key") as client:
    page = client.companies.list(field_ids=[FieldId(101)], field_types=[FieldType.GLOBAL])
    for company in page.data:
        if company.fields.requested:
            print(company.fields.data.get("101"))
```

### Accepted FieldId formats

`FieldId` accepts several input formats and normalizes them to `field-<digits>`:

| Input | Normalized to | Valid |
|-------|---------------|-------|
| `FieldId(123)` | `"field-123"` | Yes |
| `FieldId("456")` | `"field-456"` | Yes |
| `FieldId("field-789")` | `"field-789"` | Yes |
| `FieldId("invalid")` | — | No, raises `ValueError` |

Invalid formats raise `ValueError` immediately at construction time.

### FieldId comparison semantics

`FieldId` normalizes values at construction time, enabling direct equality comparisons without manual string conversion:

```python
from affinity.types import FieldId

# All these are equal - normalized to "field-123"
assert FieldId(123) == FieldId("123") == FieldId("field-123")

# Works in sets and dicts
seen = {FieldId(123)}
assert FieldId("field-123") in seen  # True

# Compare API response IDs directly
field_id = FieldId(123)
if field_id == api_response_field_id:  # No str() conversion needed
    process(field_id)
```

This normalization eliminates common comparison bugs where `FieldId(123) != FieldId("field-123")` due to type differences.

### V1-only writes and numeric field IDs

The SDK uses V2 field metadata endpoints for reads. Some write operations still use V1
endpoints under the hood (for example, field value writes and field deletes). Those V1
endpoints require numeric field IDs.

For everyday use, **the SDK and CLI hide the V1/V2 split**: call the normal field-write
methods (`update_field_value`, `person field --set`, etc.) and the right endpoint is
chosen for you. The details below only matter if you are bypassing the SDK's resolver
and constructing write calls by hand.

- IDs of the form `field-<digits>` are convertible to V1 numeric IDs directly.
- Enriched IDs (for example, `affinity-data-*`, `dealroom-*`, or `source-of-introduction`)
  do not contain a numeric ID, but most of them have a V1 "twin" row that can be found
  by matching `(name, list_id IS NULL, enrichment_source)` against
  `client.fields.list(entity_type=...)`. Name alone is not sufficient — company
  `Industry`, `Location`, and `Description` have distinct `affinity-data` and
  `dealroom` twins.
- A small number of enriched fields are purely derived (notably
  `affinity-data-current-organization`) and have no V1 twin. The SDK raises
  `EnrichedFieldNotWritableError` in that case.

## Requested vs not requested

Entities expose a `fields` container that preserves whether the API returned field data:

- `entity.fields.requested == False`: you didn’t request fields (or the API omitted them)
- `entity.fields.requested == True`: field data was requested and returned (possibly empty)

## Field value type mapping

When you read `entity.fields.data`, values are typed as `Any`. The expected shape depends on the field’s `valueType`
(`FieldValueType`) and whether the field allows multiple values.

`FieldValueType` is **V2-first** and string-based (for example: `dropdown-multi`, `ranked-dropdown`).
Unknown future values are treated as open enums and preserved as strings.

| Affinity `FieldValueType` | Typical Python value | Notes |
|---|---|---|
| `text` | `str` | Plain text |
| `filterable-text` / `filterable-text-multi` | `str` / `list[str]` | Reserved for Affinity-populated fields |
| `number` / `number-multi` | `int \| float` / `list[int \| float]` | JSON numbers |
| `datetime` | `str` / `datetime.datetime` | Typically ISO-8601 datetime strings on read. Affinity stores date fields at midnight Pacific Time; see [Writing dates](#writing-dates) |
| `person` / `person-multi` | `PersonId` / `list[PersonId]` | Under the hood: `int` or `list[int]` |
| `company` / `company-multi` | `CompanyId` / `list[CompanyId]` | Under the hood: `int` or `list[int]` |
| `dropdown` / `dropdown-multi` | `DropdownOption` / `list[DropdownOption]` | Has `.id`, `.text`, `.rank`, `.color` |
| `ranked-dropdown` | `DropdownOption` | Has `.id`, `.text`, `.rank`, `.color` |
| `location` / `location-multi` | `dict[str, Any]` / `list[dict[str, Any]]` | Structured location object(s); shape varies by API |
| `interaction` | `Any` | Relationship-intelligence fields; shape varies by API |

## Multi-value limits

Affinity's V2 API returns and accepts at most **100 values per multi-value field**:

- **Reads:** when a field holds more, the response includes the first 100 values plus a
  `totalCount`. Check with `entity.fields.is_truncated(field_id)`,
  `entity.fields.total_count(field_id)` or `entity.fields.truncated_fields()`. The CLI adds a
  warning (`Field 'Tags' on … shows 100 of 250 values`) to `company get`, `person get`,
  `opportunity get`, `list entry get`, `list entry field --get`, `list export` and `query`.
  Filters evaluated on a truncated field only see the returned values.
- **Writes:** `update_field_value()` raises `ValueError` before sending more than 100 values for a
  company, person, number, text or location multi-value field (Affinity rejects them; the
  dropdown-multi write schema declares no cap). `batch_update_fields()` accepts at most 100
  updates per call and no list values (use `update_field_value(..., value_type=...)` for those).
  The CLI's `list entry field` checks the final count before changing anything.
  dropdown-multi has no such cap (Affinity accepted 101 values in testing).
- **Replacing values:** a V2 write replaces the field's whole value; if Affinity rejects it,
  the field keeps its old values. Nothing needs to be deleted first.

## Writing numbers and locations

- `number` data must be a JSON number: Affinity rejects `"5"`. The CLI converts numeric
  input and refuses anything else before writing.
- `location` data must contain all five keys `streetAddress`, `city`, `state`, `country`,
  `continent` (`null` allowed). `update_field_value(..., value_type="location")` fills in the
  missing ones (and accepts `street_address`); the CLI takes a JSON object such as
  `{"city": "Paris", "country": "France"}`.

## Writing dates

Since 2026-01-01 Affinity stores date fields at midnight Pacific Time, so a stored value reads
back as e.g. `2024-04-01T07:00:00Z` (PDT) or `2024-01-15T08:00:00Z` (PST).

- **SDK:** pass a `datetime.date` or `datetime.datetime` to `update_field_value()` /
  `batch_update_fields()`. A `date` is sent as noon UTC, which falls on the same calendar day in
  Pacific Time, so it is stored on the date you passed.
- **CLI:** `--set <field> 2024-04-01` works the same way; a full date-time is converted to UTC,
  and anything unparseable is rejected before any write.
- Setting the date a field already holds is a no-op: the CLI compares calendar dates, not exact
  timestamps.

## Next steps

- [Filtering](filtering.md)
- [Models](models.md)
- [Types reference](../reference/types.md)
