# Query Command Reference

Full reference for `xaffinity query` — the most powerful data retrieval command in the CLI.

## Input Methods

```bash
# From file (recommended for complex queries)
xaffinity --readonly query --file query.json --json

# Inline JSON
xaffinity --readonly query --query '{"from": "persons", "limit": 10}' --json

# From stdin
echo '{"from": "persons"}' | xaffinity --readonly query --json
```

## Query JSON Structure

```json
{
  "$version": "1.0",
  "from": "entity",
  "select": ["field1", "field2"],
  "where": {"path": "field", "op": "eq", "value": "x"},
  "include": {"relationship": {"limit": 50, "days": 90}},
  "expand": ["interactionDates"],
  "groupBy": "field",
  "aggregate": {"name": {"sum": "field"}},
  "having": {"path": "name", "op": "gt", "value": 100},
  "orderBy": [{"field": "name", "direction": "asc"}],
  "limit": 100
}
```

## Queryable Entities

| Entity | Requires Parent Filter? | Notes |
|--------|------------------------|-------|
| `persons` | No | Global entity; a `where` needs a condition on a person field |
| `companies` | No | Global entity; a `where` needs a condition on a company field |
| `opportunities` | No | Global entity; no `where` |
| `lists` | No | Global entity |
| `listEntries` | **Yes** — must filter by `listId` or `listName` | List-scoped |
| `interactions` | N/A | Only via `include`, not queryable directly |
| `notes` | N/A | Only via `include`, not queryable directly |

On `companies` / `persons`, Affinity's search applies the field conditions and the rest of the
`where` (e.g. `name contains`) runs on what comes back. A `where` with no `fields.<name>` condition
is refused (it would read Affinity's whole company directory, about 12 million records); for
name/email search use `company ls --query TERM` / `person ls --query TERM`.

## Filter Operators

### Comparison
`eq`, `neq`, `gt`, `gte`, `lt`, `lte`

### String Matching
- `contains` — case-insensitive substring match
- `starts_with` — prefix match
- `starts_word` — a word in the value starts with the text, case-insensitive, as Affinity's search
  matches names (`"Jan"` matches "Jane Doe" and "Mary-Jane"; `"ane"` doesn't)

Person and company field values are compared by name ("Jane Doe", "Acme"); `contains`,
`contains_any` and `contains_all` on them are refused, use `starts_word`.

### Collection
- `in` — value in list: `{"op": "in", "value": ["A", "B"]}`
- `between` — inclusive range: `{"op": "between", "value": [10, 100]}`
- `has_any` — array field contains any of specified values
- `has_all` — array field contains all specified values

### Null Checks
`is_null`, `is_not_null`. An empty multi-value field counts as empty: `is_null` matches it.

### Text Search
- `contains_any` — text contains any keyword
- `contains_all` — text contains all keywords

## Where Filtering Happens

Conditions on fields (`fields.<name>` or `fields.<field id>`) that Affinity's search can answer go
to Affinity, so only matching records are downloaded; the full `where` then runs locally on what
comes back, so results are the same as a local filter.

- Sent: dropdown / multi-select / ranked-dropdown values, numbers, dates (incl. `"-30d"`), text
  (as a case-insensitive contains), `is_null` / `is_not_null`, and `starts_word` on one person or
  company field at the top level of the `where`.
- Local: `all` / `none` / `exists` / `_count`, non-field paths (`entityName`, `createdAt`, ...),
  `expr`, `contains_any` / `contains_all`, an OR with a branch Affinity can't answer.
- Enriched fields (`affinity-data-*`) are exact on `listEntries`; on `companies` / `persons` the
  result warns that a few records may be missed.
- If Affinity refuses the search on `listEntries` (key without "Export data from Lists"), the
  query reads the whole list and filters locally, with a warning.
- `--include-meta` shows `meta.serverFilter`: `pushed`, `local` (with reasons), `sort`.
  `XAFFINITY_QUERY_SERVER_FILTER=0` turns server filtering off.
- When two list fields share a name (e.g. Affinity's and Dealroom's "Year Founded"),
  `fields.<name>` is refused with the ids; use `fields.<field id>`. Such fields come back keyed
  by id.
- `--filter` text on `list export` / `company ls` / `person ls` is the same filter language.

## Boolean Logic

Arbitrarily nested AND/OR/NOT:

```json
{
  "where": {
    "or": [
      {
        "and": [
          {"path": "fields.Status", "op": "eq", "value": "Active"},
          {"path": "fields.Region", "op": "eq", "value": "US"}
        ]
      },
      {"path": "fields.Priority", "op": "eq", "value": "High"}
    ]
  }
}
```

## Aggregation & GroupBy

### Basic Aggregation

Aggregation functions: `sum`, `avg`, `min`, `max`, `count`, `percentile`, `first`, `last`

```json
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "aggregate": {
    "totalValue": {"sum": "fields.Deal Value"},
    "avgValue": {"avg": "fields.Deal Value"},
    "count": {"count": true}
  }
}
```

### GroupBy with Aggregation

```json
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "groupBy": "fields.Status",
  "aggregate": {
    "count": {"count": true},
    "totalValue": {"sum": "fields.Deal Size"}
  }
}
```

### Expression Aggregates

Operate on other aggregates: `multiply`, `divide`, `add`, `subtract`

```json
{
  "aggregate": {
    "total": {"sum": "fields.Revenue"},
    "count": {"count": true},
    "avgRevenue": {"divide": ["total", "count"]}
  }
}
```

### HAVING Clause

Filter groups after aggregation (like SQL HAVING):

```json
{
  "groupBy": "fields.Status",
  "aggregate": {"total": {"sum": "fields.Amount"}},
  "having": {"path": "total", "op": "gt", "value": 10000}
}
```

## Cross-Entity Filtering (Quantifiers)

Filter entities based on related entity properties. **WARNING: causes N+1 API calls — always `--dry-run` first.**

### Quantifiers

- `all` — all related items must match
- `none` — no related items can match
- `exists` — at least one related item matches (or just exists)

```json
{
  "from": "persons",
  "where": {
    "and": [
      {"path": "fields.Seniority", "op": "eq", "value": "Executive"},
      {"all": {
        "path": "companies",
        "where": {"path": "name", "op": "contains", "value": "Inc"}
      }}
    ]
  },
  "limit": 50
}
```

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      {"path": "listName", "op": "eq", "value": "Dealflow"},
      {"exists": {
        "from": "interactions",
        "where": {"path": "type", "op": "eq", "value": "email"}
      }}
    ]
  },
  "limit": 50
}
```

### Count Pseudo-Field

Filter by count of related items:

```json
{"path": "companies._count", "op": "gte", "value": 2}
```

### Nested Quantifiers Are NOT Allowed

Would cause exponential API calls. The CLI validates and rejects these at parse time.

## Include Relationships

Fetch related entities alongside results. Returned in `result.included`.

```json
{
  "from": "persons",
  "include": ["companies", "opportunities"],
  "limit": 50
}
```

### Extended Syntax with Parameters

```json
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Portfolio"},
  "include": {
    "interactions": {"limit": 50, "days": 180},
    "notes": {"limit": 20}
  },
  "limit": 50
}
```

Include parameters: `display`, `limit`, `days`, `where`, `list`

## Expand (Computed Data)

Adds computed data directly to each record (unlike `include` which returns separately).

```json
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "expand": ["interactionDates", "unreplied"],
  "limit": 50
}
```

- `interactionDates` — last/next meeting, email activity, team members
- `unreplied` — unreplied incoming messages with date, daysSince, type, subject

## Sorting

When every `orderBy` key is a sortable field (`fields.<name>`, up to 5, no `groupBy` / `aggregate`),
Affinity sorts; with a `limit`, fetching stops once enough records match. Empty values sort last in
both directions, ties break by id ascending, ranked dropdowns sort by rank. A local sort also breaks
ties by id.

## Field Selection

```json
{
  "from": "persons",
  "select": ["id", "firstName", "email", "fields.Department"],
  "limit": 100
}
```

Special paths:
- `fields.*` — all custom fields (slow on lists with 50+ fields)
- `fields.FieldName` — specific custom field
- Nested: `address.city`

## Array Fields (Multi-Select Dropdowns)

```json
{"path": "fields.Team Member", "op": "eq", "value": "LB"}
{"path": "fields.Team Member", "op": "has_any", "value": ["LB", "MA"]}
{"path": "fields.Team Member", "op": "eq", "value": ["LB", "MA"]}
```

## Dry-Run Mode

**Always run `--dry-run` before queries with `include`, `expand`, or quantifiers.**

```bash
xaffinity --readonly query --file query.json --dry-run --json
```

Returns:
```json
{
  "estimatedApiCalls": 52,
  "estimatedTimeout": 120,
  "hasExpensiveOperations": true,
  "steps": [...]
}
```

Use `--dry-run-verbose` for detailed API call breakdown.

## Command Flags

### Query Input
- `--file, -f PATH` — read query from JSON file
- `--query TEXT` — inline JSON query string

### Execution Control
- `--dry-run` — show execution plan without running
- `--dry-run-verbose` — detailed plan with API call breakdown
- `--confirm` — require confirmation before expensive operations
- `--max-records INTEGER` — safety limit (default: 10000)
- `--timeout FLOAT` — overall timeout in seconds (default: 300)

### Output Control
- `--output, -o FORMAT` — format: `table`, `json`, `jsonl`, `markdown`, `toon`, `csv`
- `--json` — alias for `--output json`
- `--csv` — alias for `--output csv`
- `--csv-bom` — add UTF-8 BOM for Excel
- `--include-meta` — include execution metadata
- `--include-style` — included data display: `inline` (default), `separate`, `ids-only`

### Pagination
- `--cursor TEXT` — resume from cursor (from previous truncated response)
- `--max-output-bytes INTEGER` — truncate output to max bytes (exit code 100 if truncated)

## Common Patterns

### Pipeline Summary by Status
```json
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "groupBy": "fields.Status",
  "aggregate": {
    "count": {"count": true},
    "totalValue": {"sum": "fields.Deal Size"}
  }
}
```

### Find Executives at Multiple Companies
```json
{
  "from": "persons",
  "where": {
    "and": [
      {"path": "fields.Seniority", "op": "eq", "value": "Executive"},
      {"path": "companies._count", "op": "gte", "value": 2}
    ]
  },
  "include": ["companies"],
  "limit": 100
}
```

### Activity Report with Unreplied Detection
```json
{
  "from": "listEntries",
  "where": {
    "and": [
      {"path": "listName", "op": "eq", "value": "Portfolio"},
      {"path": "fields.Status", "op": "eq", "value": "Active"}
    ]
  },
  "expand": ["interactionDates", "unreplied"],
  "limit": 50
}
```

### Team Member Filtering (Multi-Select)
```json
{
  "from": "listEntries",
  "where": {
    "and": [
      {"path": "listName", "op": "eq", "value": "Dealflow"},
      {"path": "fields.Team Member", "op": "has_any", "value": ["LB", "MA"]}
    ]
  },
  "limit": 50
}
```

## Performance Guidelines

| Records with expand/include | Estimated Time | Safety |
|-----------------------------|----------------|--------|
| <=100 | ~2 min | Safe |
| ~200 | ~5 min | OK with progress |
| ~400 | ~9 min | Near ceiling |
| 430+ | 10+ min | May timeout |

**Always:**
1. Use `--dry-run` first for expensive queries
2. Set `limit` in the query JSON
3. Use `select` to reduce output size
4. Avoid `fields.*` on lists with many custom fields
