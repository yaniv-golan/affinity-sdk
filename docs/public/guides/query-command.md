# Query Command

The `xaffinity query` command provides a structured JSON query language for complex data retrieval, filtering, includes, and aggregations.

## When to Use Query vs Individual Commands

**Use `query` when you need:**
- Complex filtering with multiple AND/OR/NOT conditions
- Related entity data (include companies with persons)
- Aggregations (count, sum, avg, groupBy)
- Multi-field sorting
- Analysis across large datasets

**Use individual commands for:**
- Simple lookups (`person get 123`)
- Basic searches (`company ls --query "Acme"`)
- Quick exports (`list export Pipeline`)

## Basic Usage

```bash
# From file
xaffinity query --file query.json

# Inline JSON
xaffinity query --query '{"from": "persons", "limit": 10}'

# From stdin (piped)
echo '{"from": "persons"}' | xaffinity query

# Dry-run to preview execution plan
xaffinity query --file query.json --dry-run
```

## Query Structure

A minimal query requires only the `from` field:

```json
{
  "from": "persons"
}
```

A complete query can include:

```json
{
  "$version": "1.0",
  "from": "persons",
  "where": {
    "and": [
      { "path": "fields.Seniority", "op": "eq", "value": "Executive" },
      { "path": "primaryEmailAddress", "op": "contains", "value": "@acme.com" }
    ]
  },
  "include": ["companies"],
  "select": ["id", "firstName", "lastName", "primaryEmailAddress"],
  "orderBy": [{ "field": "lastName", "direction": "asc" }],
  "limit": 100
}
```

### Supported Entity Types

| Entity | Description | Query Type |
|--------|-------------|------------|
| `persons` | People in your CRM | Direct query; a `where` needs a field condition |
| `companies` | Companies/organizations | Direct query; a `where` needs a field condition |
| `opportunities` | Deals/opportunities | Direct query; no `where` |
| `lists` | Affinity list definitions | Direct query |
| `listEntries` | Entries in Affinity lists | Requires `listId` or `listName` filter |
| `interactions` | Emails, calls, meetings | Include only (cannot query directly) |
| `notes` | Notes on entities | Include only (cannot query directly) |

#### Entity Query Limitations

**`listEntries`** requires a `listId` or `listName` filter to specify which list to query:

```json
{
  "from": "listEntries",
  "where": { "path": "listId", "op": "eq", "value": 12345 },
  "limit": 100
}
```

Or using list name (resolved automatically):

```json
{
  "from": "listEntries",
  "where": { "path": "listName", "op": "eq", "value": "My Pipeline" },
  "limit": 100
}
```

**Field name resolution:** When filtering on `fields.*`, you can use human-readable field names instead of field IDs:

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "My Pipeline" },
      { "path": "fields.Status", "op": "eq", "value": "Active" }
    ]
  }
}
```

Field names are resolved case-insensitively. Any field can also be named by id (`fields.field-260415`, `fields.affinity-data-year-founded`). An unknown field name gives a warning listing the list's fields. If the list's field definitions can't be read, the query fails (rather than reading every `fields.*` value as null).

When two list fields share a name (e.g. Affinity's and Dealroom's "Year Founded"), `fields.<name>` is refused with an error listing the ids: name the one you want by id. Such fields come back keyed by id.

**`companies` and `persons`** are filtered by Affinity's search, so a `where` must include at least one condition on their fields (`fields.<name>`). Affinity applies what it can and the rest (e.g. `name contains`) runs on what comes back:

```json
{
  "from": "companies",
  "where": {
    "and": [
      { "path": "fields.Industry", "op": "eq", "value": "Software" },
      { "path": "name", "op": "contains", "value": "Labs" }
    ]
  },
  "limit": 50
}
```

A `where` with no field condition is refused: it would mean reading Affinity's whole company directory (about 12 million records). For a name or email search use `xaffinity company ls --query TERM` / `xaffinity person ls --query TERM`. **`opportunities`** take no `where`; filter opportunity list entries with `listEntries` instead.

**`interactions` and `notes`** cannot be queried directly. Instead, include them on a parent entity:

```json
{
  "from": "persons",
  "include": ["interactions", "notes"],
  "limit": 50
}
```

Attempting to query `interactions` or `notes` directly will return an error:
```
QueryParseError: 'interactions' cannot be queried directly.
Use it as an 'include' on a parent entity instead.
Example: {"from": "persons", "include": ["interactions"]}
```

## Filtering with WHERE

### Simple Conditions

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "Dealflow" },
      { "path": "fields.Status", "op": "eq", "value": "Active" }
    ]
  }
}
```

### Supported Operators

| Operator | Description | Example Value |
|----------|-------------|---------------|
| `eq` | Equals | `"Active"` |
| `neq` | Not equals | `"Closed"` |
| `gt` | Greater than | `10000` |
| `gte` | Greater than or equal | `10000` |
| `lt` | Less than | `5000` |
| `lte` | Less than or equal | `5000` |
| `contains` | Contains substring | `"@gmail"` |
| `starts_with` | Starts with | `"Acme"` |
| `starts_word` | A word in the value starts with the text (case-insensitive) | `"Jan"` |
| `in` | Value in list | `["New", "Active"]` |
| `between` | Value in range | `[1000, 5000]` |
| `is_null` | Field is null | (no value needed) |
| `is_not_null` | Field is not null | (no value needed) |
| `contains_any` | String contains any substring (case-insensitive) | `["vip", "hot"]` |
| `contains_all` | String contains all substrings (case-insensitive) | `["verified", "active"]` |
| `has_any` | Array contains any of the values | `["LB", "MA"]` |
| `has_all` | Array contains all of the values | `["LB", "MA"]` |

`starts_word` matches the way Affinity's search matches names: `"Jan"` matches "Jane Doe" and "Mary-Jane"; `"ane"` doesn't. Person and company field values are compared by name ("Jane Doe", "Acme"), and `contains` / `contains_any` / `contains_all` on a person or company field is refused: use `starts_word`. An empty multi-value field (no items) counts as empty: `is_null` matches it, `is_not_null` doesn't.

The `--filter` text of `list export`, `company ls` and `person ls` is the same filter language: every condition means the same in both spellings.

### Multi-Select Field Filtering

Multi-select dropdown fields (like "Team Member") return arrays from the API. The query engine handles these automatically:

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "Dealflow" },
      { "path": "fields.Team Member", "op": "eq", "value": "LB" }
    ]
  }
}
```

**Operator behavior with array fields:**

| Operator | Single-value field | Multi-select field |
|----------|-------------------|-------------------|
| `eq` | Exact match | Scalar: membership check / List: set equality |
| `neq` | Not equal | Scalar: not in array / List: set inequality |
| `in` | Value in list | Any intersection between arrays |
| `has_any` | Returns false | Any specified value present |
| `has_all` | Returns false | All specified values present |

**Examples:**

```json
// Find entries where Team Member includes "LB"
{ "path": "fields.Team Member", "op": "eq", "value": "LB" }

// Find entries where Team Member is exactly ["LB", "MA"] (order-insensitive)
{ "path": "fields.Team Member", "op": "eq", "value": ["LB", "MA"] }

// Find entries where Team Member includes any of ["LB", "DW"]
{ "path": "fields.Team Member", "op": "has_any", "value": ["LB", "DW"] }

// Find entries where Team Member includes both "LB" and "MA"
{ "path": "fields.Team Member", "op": "has_all", "value": ["LB", "MA"] }
```

**Note:** Array operators are case-sensitive because dropdown values from the Affinity API are exact matches.

### Compound Conditions

**AND:**

```json
{
  "from": "persons",
  "where": {
    "and": [
      { "path": "fields.Seniority", "op": "is_not_null" },
      { "path": "firstName", "op": "starts_with", "value": "J" }
    ]
  }
}
```

**OR:**

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "Dealflow" },
      {
        "or": [
          { "path": "fields.Status", "op": "eq", "value": "New" },
          { "path": "fields.Status", "op": "eq", "value": "Active" }
        ]
      }
    ]
  }
}
```

**NOT:**

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "Dealflow" },
      { "not": { "path": "fields.Status", "op": "eq", "value": "Closed" } }
    ]
  }
}
```

### Field Paths

Access nested fields with dot notation:

```json
{
  "from": "listEntries",
  "where": { "path": "fields.Status", "op": "eq", "value": "Active" }
}
```

Array access:

```json
{
  "from": "persons",
  "where": {
    "and": [
      { "path": "fields.Seniority", "op": "is_not_null" },
      { "path": "emailAddresses[0]", "op": "contains", "value": "@acme.com" }
    ]
  }
}
```

### Date Filtering

**Relative dates:**

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "Pipeline" },
      { "path": "createdAt", "op": "gte", "value": "-30d" }
    ]
  }
}
```

| Format | Meaning |
|--------|---------|
| `-30d` | 30 days ago |
| `+7d` | 7 days from now |
| `today` | Start of today |
| `now` | Current time |
| `yesterday` | Start of yesterday |
| `tomorrow` | Start of tomorrow |

## Including Related Entities

Fetch related entities in a single query:

```json
{
  "from": "persons",
  "include": ["companies", "opportunities"],
  "limit": 50
}
```

### Available Relationships

| From | Can Include |
|------|-------------|
| `persons` | `companies`, `opportunities`, `interactions`, `notes` |
| `companies` | `persons`, `opportunities`, `interactions`, `notes` |
| `opportunities` | `persons`, `companies`, `interactions`, `notes` |
| `listEntries` | `persons`, `companies`, `opportunities`, `interactions` |

**Note:** For `listEntries`, the include dynamically resolves based on the entry's entity type (person, company, or opportunity). For example, company entries can include `persons` (associated persons) and `interactions` (company interactions).

**Note:** Includes fetch relationship IDs in parallel (N calls), then batch-fetch full records via V2 API. Use `--dry-run` to preview the cost.

### Parameterized Includes

For `listEntries`, you can customize includes with parameters:

```json
{
  "from": "listEntries",
  "where": { "path": "listName", "op": "eq", "value": "Dealflow" },
  "include": [
    { "interactions": { "limit": 50, "days": 180 } },
    { "opportunities": { "list": "Pipeline" } },
    { "persons": { "where": { "path": "firstName", "op": "contains", "value": "John" } } }
  ]
}
```

| Parameter | Description | Applicable To |
|-----------|-------------|---------------|
| `limit` | Maximum records to fetch | `interactions` |
| `days` | Lookback period in days | `interactions` |
| `list` | Scope to specific opportunity list | `opportunities` |
| `where` | Filter included entities | `persons`, `companies`, `opportunities` |

In **table output**, included data appears as separate tables (e.g., "Included: companies").
In **JSON output**, included data appears in a separate `included` section with deduplicated records.
When `--max-output-bytes` splits the result into pages (JSON or TOON), each page's included records are those of its own rows.

A record that is gone (deleted, or merged into another) has nothing related. Any other error while fetching related records fails the query.

## Expanding Computed Data

Use `expand` to add computed data directly to each record (unlike `include` which fetches separate entities).

### Interaction Dates

Add interaction date summaries to records:

```json
{
  "from": "companies",
  "expand": ["interactionDates"],
  "limit": 50
}
```

Output includes interaction summaries on each record:

```json
{
  "id": 123,
  "name": "Acme Corp",
  "interactionDates": {
    "lastMeeting": { "date": "2026-01-08T10:00:00Z", "daysSince": 5 },
    "nextMeeting": { "date": "2026-01-20T14:00:00Z", "daysUntil": 7 },
    "lastEmail": { "date": "2026-01-10T09:30:00Z", "daysSince": 3 }
  }
}
```

### Available Expansions

| Expansion | Description | Supported Entities |
|-----------|-------------|-------------------|
| `interactionDates` | Last/next meeting, email dates | `persons`, `companies`, `opportunities`, `listEntries` |
| `unreplied` | Detect unreplied incoming messages (email/chat) | `persons`, `companies`, `opportunities`, `listEntries` |

#### Unreplied Messages Expansion

Add unreplied message detection to each record:

```json
{
  "from": "listEntries",
  "where": { "path": "listName", "op": "eq", "value": "Pipeline" },
  "expand": ["unreplied"],
  "limit": 50
}
```

Output includes unreplied message info on each record:

```json
{
  "id": 123,
  "entity": { "id": 456, "type": "company", "name": "Acme Corp" },
  "unreplied": {
    "date": "2026-01-15T09:30:00Z",
    "daysSince": 4,
    "type": "email",
    "subject": "Re: Partnership opportunity"
  }
}
```

Records without unreplied messages have `"unreplied": null`, as do records that are gone (deleted, or merged into another); any other error from the check fails the query. For chat messages, `subject` is `null`.

### Expand vs Include

| Feature | `include` | `expand` |
|---------|-----------|----------|
| **Purpose** | Fetch related entities | Add computed data |
| **Output** | Separate records | Merged into each record |
| **Example** | Related company records | Dates, counts, summaries |

**Warning:** Expand also causes N+1 API calls. For large datasets, consider using `list export --expand interactions` for streaming output.

## Aggregations

### Basic Aggregates

```json
{
  "from": "opportunities",
  "aggregate": {
    "total": { "count": true },
    "totalValue": { "sum": "amount" },
    "avgValue": { "avg": "amount" }
  }
}
```

### Group By

```json
{
  "from": "opportunities",
  "groupBy": "status",
  "aggregate": {
    "count": { "count": true },
    "totalValue": { "sum": "amount" }
  }
}
```

### Having (Filter Aggregated Results)

```json
{
  "from": "opportunities",
  "groupBy": "status",
  "aggregate": {
    "count": { "count": true }
  },
  "having": { "path": "count", "op": "gte", "value": 5 }
}
```

## Sorting

```json
{
  "from": "persons",
  "orderBy": [
    { "field": "lastName", "direction": "asc" },
    { "field": "firstName", "direction": "asc" }
  ]
}
```

When every `orderBy` key is a sortable field (`fields.<name>`, up to 5) and the query has no `groupBy` or `aggregate`, Affinity sorts (on `listEntries`, `companies` and `persons`). With a `limit`, fetching stops once enough records pass the filter. Affinity's order puts empty values last in both directions, breaks ties by id ascending, and orders ranked dropdowns by rank. When the sort runs locally it also breaks ties by id.

```json
{
  "from": "listEntries",
  "where": { "path": "listName", "op": "eq", "value": "Dealflow" },
  "orderBy": [{ "field": "fields.Deal Size", "direction": "desc" }],
  "limit": 20
}
```

## Filtering on Affinity's Side

Conditions on fields that Affinity's search can answer are sent to Affinity, so only matching records are downloaded. The **full** `where` still runs locally on what comes back, so results are the same as a local filter.

- **`listEntries`**: conditions on list fields (`fields.<name>` or `fields.<field id>`) go to Affinity when its search can answer them: dropdown and ranked-dropdown fields (`eq`, `in`, `contains`, `starts_with`, `starts_word` on the option text; `neq` and `not` of `eq`/`in`), multi-select dropdowns (membership `eq`, `in`, `has_any`, `has_all`, `contains` / `starts_word` on option text, `neq`), numbers (`eq`, `gt`, `gte`, `lt`, `lte`, `between`, `neq`), dates (`gt`, `gte`, `lt`, `lte`, `eq`, `between`, also relative dates like `"-30d"`), text (`eq`, `contains`, `starts_with`, `starts_word` are sent as Affinity's case-insensitive contains, and the exact check runs locally), `is_null` / `is_not_null` on most field types, and `starts_word` on a person or company field (one per query, at the top level of the `where`, plain text of 3+ letters or digits; sent as Affinity's name search).
- **Stays local**: related-record quantifiers (`all`, `none`, `exists`, `_count`), `entityName` / `entityId` / `entityType` / `createdAt` and other non-field paths, `expr`, `contains_any` / `contains_all`, and an OR with any branch that can't go.
- **Enriched fields** (`affinity-data-*`: funding, headcount, ...): on `listEntries` they're sent as "condition OR field is empty", because Affinity's search reads a few records' enriched values as empty; results stay exact. On `companies` / `persons` they're sent as they are, and the result carries a warning that a few records may be missed.
- **Fallback**: if Affinity refuses the search on `listEntries` (e.g. the API key lacks the "Export data from Lists" permission), the query reads the whole list and filters locally, with a warning.
- `XAFFINITY_QUERY_SERVER_FILTER=0` turns server filtering off for list entries (`listEntries` queries and `list export`), to compare results or debug. Company and person filters always go through Affinity's search.

`--include-meta` shows what happened in `meta.serverFilter`: `pushed` (conditions sent to Affinity), `local` (conditions checked locally, each with the reason), `sort` (when Affinity sorted) and `fallback` (why the search was refused, when it was). `--dry-run` lists the list fields Affinity may filter ("decided when the query runs").

## Limiting Results

```json
{
  "from": "persons",
  "limit": 100
}
```

## Dry-Run Mode

**Always preview expensive queries first:**

```bash
xaffinity query --file query.json --dry-run
```

Output shows:

```
Query Execution Plan

Query:
  $version: 1.0
  from: persons
  include: [companies]
  limit: 100

Steps:
  [1] FETCH persons (1 API call)
  [2] FILTER (client-side)
  [3] INCLUDE companies (up to 100 API calls)
  [4] LIMIT 100

Estimated:
  API Calls: 101
  Records: 100

[warning] Include 'companies' will make N API calls (1 per person).
```

## Output Formats

```bash
# Table (default for interactive)
xaffinity query --file query.json

# JSON output
xaffinity query --file query.json --json

# CSV output
xaffinity query --file query.json --csv

# JSON with metadata
xaffinity query --file query.json --json --include-meta
```

## Command Options

| Option | Description |
|--------|-------------|
| `--file`, `-f` | Read query from JSON file |
| `--query` | Inline JSON query string |
| `--query-version` | Override `$version` in query |
| `--dry-run` | Show execution plan without running |
| `--dry-run-verbose` | Show detailed plan with API call breakdown |
| `--confirm` | Require confirmation before expensive operations |
| `--max-records` | Safety limit on total records (default: 10000). Matches past it are dropped with a warning and `meta.truncated`; with `orderBy` or an aggregate, the cut comes after sorting and aggregating. A `--cursor` resumes only with the same `--max-records` |
| `--timeout` | Overall timeout in seconds (default: 300) |
| `--json` | Output as JSON (alias for `--output json`) |
| `--csv` | Output as CSV (alias for `--output csv`) |
| `--output` | Output format: table, json, csv |
| `--include-meta` | Include execution metadata in output |
| `--quiet`, `-q` | Suppress progress output |
| `--verbose`, `-v` | Show detailed progress |

## Examples

### Find Executive Contacts with Their Companies

```json
{
  "from": "persons",
  "where": {
    "and": [
      { "path": "fields.Seniority", "op": "eq", "value": "Executive" },
      { "path": "primaryEmailAddress", "op": "is_not_null" }
    ]
  },
  "include": ["companies"],
  "orderBy": [{ "field": "lastName", "direction": "asc" }],
  "limit": 100
}
```

### Pipeline Summary by Status

```json
{
  "from": "listEntries",
  "where": { "path": "listId", "op": "eq", "value": 12345 },
  "groupBy": "fields.Status",
  "aggregate": {
    "count": { "count": true },
    "totalValue": { "sum": "fields.Deal Value" }
  }
}
```

### Recent Meetings with Persons

Since interactions cannot be queried directly, fetch persons with their recent interactions included:

```json
{
  "from": "persons",
  "include": [
    { "interactions": { "limit": 50, "days": 7 } }
  ],
  "limit": 100
}
```

Then filter for meetings client-side, or use the CLI with `--expand-filter` for post-fetch filtering.

## Best Practices

1. **Start with dry-run** for complex queries to see API call estimates
2. **Use limit** to avoid fetching too much data
3. **Filter on fields** (`fields.<name>`) so Affinity's search can narrow what's downloaded
4. **Avoid deep includes** which cause N+1 API calls
5. **Include `$version`** in saved query files for forward compatibility

## Limitations

- Affinity's search answers conditions on fields of `listEntries`, `companies` and `persons`; other conditions run locally on what comes back
- `companies` / `persons` need a field condition in any `where`; `opportunities` take no `where`
- Includes cause N+1 API calls (1 per parent record)
- Maximum 10,000 records per query for safety
- No cross-entity joins (use includes instead)
