# Filter Operators Reference

Complete reference for all filter operators in the Affinity query language.

## Comparison Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `eq` | Equal | `{"path": "status", "op": "eq", "value": "Active"}` |
| `neq` | Not equal | `{"path": "status", "op": "neq", "value": "Closed"}` |
| `gt` | Greater than | `{"path": "amount", "op": "gt", "value": 10000}` |
| `gte` | Greater than or equal | `{"path": "amount", "op": "gte", "value": 10000}` |
| `lt` | Less than | `{"path": "amount", "op": "lt", "value": 5000}` |
| `lte` | Less than or equal | `{"path": "amount", "op": "lte", "value": 5000}` |

## String Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `contains` | Contains substring (case-insensitive) | `{"path": "email", "op": "contains", "value": "@gmail"}` |
| `starts_with` | Starts with (case-insensitive) | `{"path": "name", "op": "starts_with", "value": "Acme"}` |
| `starts_word` | A word in the value starts with the text (case-insensitive) | `{"path": "fields.Owner", "op": "starts_word", "value": "Jan"}` |

`starts_word` matches the way Affinity's search matches names: `"Jan"` matches "Jane Doe" and "Mary-Jane"; `"ane"` doesn't.

Person and company field values are compared by name ("Jane Doe", "Acme"). `contains`, `contains_any` and `contains_all` on a person or company field are refused: use `starts_word`.

## Collection Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `in` | Value in list | `{"path": "status", "op": "in", "value": ["New", "Active"]}` |
| `between` | Value in range | `{"path": "amount", "op": "between", "value": [1000, 5000]}` |
| `contains_any` | String contains any substring (case-insensitive) | `{"path": "bio", "op": "contains_any", "value": ["python", "java"]}` |
| `contains_all` | String contains all substrings (case-insensitive) | `{"path": "bio", "op": "contains_all", "value": ["senior", "engineer"]}` |
| `has_any` | Array field contains any of the values | `{"path": "fields.Team Member", "op": "has_any", "value": ["LB", "MA"]}` |
| `has_all` | Array field contains all of the values | `{"path": "fields.Team Member", "op": "has_all", "value": ["LB", "MA"]}` |

## Null/Empty Checks

| Operator | Description | Example |
|----------|-------------|---------|
| `is_null` | Field is null, an empty string, or an empty multi-value field | `{"path": "fields.Owner", "op": "is_null"}` |
| `is_not_null` | Field has a value (an empty multi-value field doesn't) | `{"path": "fields.Owner", "op": "is_not_null"}` |

## Multi-Select Field Filtering

Multi-select dropdown fields (like "Team Member") return arrays. Operators handle these automatically:

| Operator | Single-value field | Multi-select field |
|----------|-------------------|-------------------|
| `eq` | Exact match | Scalar: membership check / List: set equality |
| `neq` | Not equal | Scalar: not in array / List: set inequality |
| `in` | Value in list | Any intersection between arrays |
| `has_any` | Returns false | Any specified value present |
| `has_all` | Returns false | All specified values present |

```json
// Find entries where Team Member includes "LB"
{ "path": "fields.Team Member", "op": "eq", "value": "LB" }

// Find entries where Team Member includes any of ["LB", "DW"]
{ "path": "fields.Team Member", "op": "has_any", "value": ["LB", "DW"] }

// Find entries where Team Member includes both "LB" and "MA"
{ "path": "fields.Team Member", "op": "has_all", "value": ["LB", "MA"] }
```

## Where Each Condition Runs

Conditions on fields that Affinity's search can answer are sent to Affinity; the full `where` then runs locally on what comes back, so results are the same either way. Conditions on non-field paths, quantifiers, `expr`, `contains_any` / `contains_all` and an OR with a branch Affinity can't answer run locally. `--include-meta` (CLI) shows the split in `meta.serverFilter`.

## Date Filtering

### Relative Dates

```json
{"path": "created_at", "op": "gte", "value": "-30d"}
```

Supported formats:
- `-30d` — 30 days ago
- `+7d` — 7 days from now
- `today` — start of today
- `now` — current time
- `yesterday` — start of yesterday
- `tomorrow` — start of tomorrow

## Field Paths

Access nested fields using dot notation:

```json
{"path": "fields.Status", "op": "eq", "value": "Active"}
```

Common paths:
- `fields.<FieldName>` — custom list fields on listEntries (preferred); also company/person fields on `companies` / `persons`
- `fields.<field id>` — any field by id (e.g. `fields.affinity-data-year-founded`); required when two list fields share a name
- `fields.*` — all custom fields (avoid for lists with 50+ fields — very slow)
- `emails[0]` — first email in array
- `company.name` — nested object field (on included relationships)
