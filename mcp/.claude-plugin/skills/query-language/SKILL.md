---
name: query-language
description: >
  Executes structured JSON queries against Affinity CRM data with filtering, aggregation,
  and relationship traversal via the query MCP tool.
when_to_use: >
  Use when user needs complex filtering (AND/OR/NOT), aggregations (count/sum/avg by field),
  cross-entity queries (find persons by company properties), or relationship data
  (include/expand). Triggers on "query", "structured query", "group by", "aggregate",
  "count by status", "sum deal values", or "find all persons where".
---

# Affinity Query Language

Structured query language for the `query` MCP tool. For simple lookups, prefer `execute-read-command` with individual CLI commands.

> **Before running queries**, complete these pre-flight steps:
> 1. Read the data model: `read-xaffinity-resource(uri: "xaffinity://data-model")`
> 2. Discover relevant commands: `discover-commands(query: "your task keywords")`
> 3. State what you learned from each step before proceeding

## When to Use Query vs CLI Commands

| Need | Use |
|------|-----|
| Simple lookup by name/email/ID | `execute-read-command` with `person get` / `company get` |
| Quick search | `execute-read-command` with `person ls --query "..."` |
| List metadata | `execute-read-command` with `list ls` / `field ls` |
| Write operations | `execute-write-command` |
| Complex AND/OR/NOT filtering | **`query`** |
| Aggregation / groupBy | **`query`** |
| Cross-entity filtering (quantifiers) | **`query`** |
| Include related entities | **`query`** |
| Computed data (interaction dates, unreplied) | **`query`** |
| Preview API cost before running | **`query` with `dryRun: true`** |

## Quick Start

```json
// Simplest query - get 10 persons
{"from": "persons", "limit": 10}

// Add a filter (companies/persons: filter on a field)
{"from": "companies", "where": {"path": "fields.Industry", "op": "eq", "value": "Software"}, "limit": 10}

// Include related companies
{"from": "persons", "include": ["companies"], "limit": 10}

// Query list entries (MUST filter by listName or listId)
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "limit": 10}
```

## Query Structure

```json
{
  "$version": "1.0",
  "from": "persons",
  "where": {"and": [
    {"path": "fields.Seniority", "op": "eq", "value": "Executive"},
    {"path": "primaryEmailAddress", "op": "contains", "value": "@acme.com"}
  ]},
  "select": ["id", "firstName", "lastName", "primaryEmailAddress"],
  "include": ["companies", "opportunities"],
  "expand": ["interactionDates"],
  "groupBy": "fields.Status",
  "aggregate": {"count": {"count": true}, "total": {"sum": "fields.Deal Value"}},
  "having": {"path": "count", "op": "gte", "value": 5},
  "orderBy": [{"field": "lastName", "direction": "asc"}],
  "limit": 100
}
```

### Queryable Entities

| Entity | Requires Parent Filter? | Notes |
|--------|------------------------|-------|
| `persons` | No | Global entity; a `where` needs a condition on a person field |
| `companies` | No | Global entity; a `where` needs a condition on a company field |
| `opportunities` | No | Global entity; no `where` |
| `lists` | No | Global entity |
| `listEntries` | **Yes** — must filter by `listId` or `listName` | List-scoped |

**`interactions` and `notes` cannot be queried directly** — access them via `include` on other entities.

## Key Rules

1. **`listEntries` MUST filter by `listId` or `listName`** — use `listName` (not `list.name`)
2. **Always `dryRun: true` first** for queries with `include`, `expand`, or quantifiers
3. **Set `limit`** to avoid fetching too much data
4. **Use `select`** to reduce output size — avoid `fields.*` on lists with 50+ fields
5. **Use `maxRecords`** for quantifier queries on unbounded entities (persons/companies)
6. **`companies` / `persons`: include a `fields.<name>` condition in any `where`.** Affinity's search applies it, and the rest (e.g. `name contains`) runs on what comes back. A `where` with no field condition is refused (it would read Affinity's whole company directory, about 12 million records). For name/email search use `execute-read-command` with `company ls --query TERM` / `person ls --query TERM`.

## Filtering

### Basic Conditions

```json
{"path": "entityName", "op": "contains", "value": "Acme"}
{"path": "fields.Status", "op": "eq", "value": "Active"}
{"path": "fields.Amount", "op": "gte", "value": 10000}
{"path": "fields.Owner", "op": "starts_word", "value": "Jan"}
```

Common operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `contains`, `starts_with`, `starts_word`, `in`, `between`, `is_null`, `is_not_null`, `has_any`, `has_all`

- `starts_word`: a word in the value starts with the text, case-insensitive, the way Affinity's search matches names (`"Jan"` matches "Jane Doe" and "Mary-Jane"; `"ane"` doesn't).
- Person and company field values are compared by name ("Jane Doe", "Acme"). `contains` / `contains_any` / `contains_all` on them is refused: use `starts_word`.
- An empty multi-value field counts as empty: `is_null` matches it, `is_not_null` doesn't.
- Name any field by id with `fields.<field id>` (e.g. `fields.affinity-data-year-founded`). When two list fields share a name (e.g. Affinity's and Dealroom's "Year Founded"), `fields.<name>` is refused with an error listing the ids; use the id. Such fields come back keyed by id.

For the full operator reference (including `has_any`, `has_all`, multi-select handling, date filtering): see `references/filter-operators.md`

### Boolean Logic (AND / OR / NOT)

```json
// AND
{"and": [
  {"path": "fields.Status", "op": "eq", "value": "Active"},
  {"path": "fields.Amount", "op": "gt", "value": 10000}
]}

// OR
{"or": [
  {"path": "email", "op": "contains", "value": "@acme.com"},
  {"path": "email", "op": "contains", "value": "@acme.io"}
]}

// NOT
{"not": {"path": "status", "op": "eq", "value": "Closed"}}
```

These can be nested arbitrarily deep.

### Filtering on Affinity's Side

Conditions on fields (`fields.<name>`) that Affinity's search can answer are sent to Affinity, so only matching records are downloaded. The full `where` still runs locally on what comes back, so results are the same as a local filter.

- **Sent to Affinity:** dropdowns and ranked dropdowns (`eq`, `in`, `contains`, `starts_with`, `starts_word` on option text, `neq`, `not` of `eq`/`in`); multi-select dropdowns (`eq` membership, `in`, `has_any`, `has_all`, `contains`/`starts_word` on option text, `neq`); numbers (`eq`, `gt`, `gte`, `lt`, `lte`, `between`, `neq`); dates (`gt`, `gte`, `lt`, `lte`, `eq`, `between`, relative dates like `"-30d"`); text (`eq`, `contains`, `starts_with`, `starts_word`, sent as Affinity's case-insensitive contains); `is_null` / `is_not_null` on most field types; `starts_word` on a person or company field (one per query, top level of the `where`, plain text of 3+ letters or digits).
- **Stays local:** `all` / `none` / `exists` / `_count`, non-field paths (`entityName`, `entityId`, `entityType`, `createdAt`, ...), `expr`, `contains_any` / `contains_all`, an OR with any branch that can't go.
- **Enriched fields** (`affinity-data-*`: funding, headcount, ...): exact on `listEntries`; on `companies` / `persons` the result warns that a few records may be missed.
- **Sorting:** when every `orderBy` key is a sortable field (`fields.<name>`, up to 5, no `groupBy`/`aggregate`), Affinity sorts; with a `limit`, fetching stops once enough records match. Empty values sort last in both directions, ties break by id, ranked dropdowns sort by rank.
- If Affinity refuses the search on `listEntries` (e.g. the key lacks the "Export data from Lists" permission), the query reads the whole list and filters locally, with a warning.
- `dryRun: true` lists the list fields Affinity may filter ("decided when the query runs").

## Aggregation & GroupBy

```json
// Count + sum by status
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "groupBy": "fields.Status",
  "aggregate": {
    "count": {"count": true},
    "totalValue": {"sum": "fields.Deal Value"}
  }
}

// Filter groups with HAVING
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "groupBy": "fields.Status",
  "aggregate": {"count": {"count": true}},
  "having": {"path": "count", "op": "gte", "value": 5}
}
```

Aggregate functions: `count`, `sum`, `avg`, `min`, `max`, `percentile`, `first`, `last`

Expression aggregates (operate on other aggregates): `multiply`, `divide`, `add`, `subtract`

## Include & Expand (N+1 Warning)

Both cause **one additional API call per record**. Always `dryRun: true` first.

```json
// Include: fetches related entities into separate "included" section
{"from": "persons", "include": ["companies"], "limit": 50}

// Expand: adds computed data directly to each record
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "expand": ["interactionDates", "unreplied"], "limit": 50}
```

**Expand options:** `interactionDates` (last/next meeting, email dates, team members), `unreplied` (unreplied incoming messages)

For detailed include/expand syntax, parameterized includes, and output formats: see `references/include-expand.md`

## Quantifiers (Cross-Entity Filtering)

Filter entities based on related entity properties. **Causes N+1 API calls — always `dryRun: true` first.**

```json
// Dealflow entries whose company has 2+ associated persons
{"from": "listEntries", "where": {"and": [{"path": "listName", "op": "eq", "value": "Dealflow"}, {"path": "persons._count", "op": "gte", "value": 2}]}, "limit": 50}

// Executives where ALL companies have .com domains (persons need a field condition)
{"from": "persons", "where": {"and": [{"path": "fields.Seniority", "op": "eq", "value": "Executive"}, {"all": {"path": "companies", "where": {"path": "domain", "op": "contains", "value": ".com"}}}]}, "limit": 50}

// Executives with at least one meeting interaction
{"from": "persons", "where": {"and": [{"path": "fields.Seniority", "op": "eq", "value": "Executive"}, {"exists": {"from": "interactions", "where": {"path": "type", "op": "eq", "value": "meeting"}}}]}, "limit": 50}
```

Quantifiers: `all`, `none`, `exists`, `._count`

For detailed quantifier reference and performance guidance: see `references/quantifiers.md`

## Dry-Run Mode

**MANDATORY for queries with `include`, `expand`, or quantifiers.**

```json
{
  "query": {"from": "persons", "include": ["companies", "opportunities"], "limit": 100},
  "dryRun": true
}
```

Returns estimated API calls, records, and warnings.

| Estimated API Calls | Action |
|---------------------|--------|
| <100 | Safe to run |
| 100-200 | Will take 2-5 minutes |
| 200-400 | May take 5-10 minutes, near ceiling |
| 400+ | Reduce limit or batch the query |

## List Entries: Custom Fields

```json
// Select specific fields (preferred)
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "select": ["entityName", "fields.Status", "fields.Owner"],
  "limit": 100
}
```

**Performance warning:** `fields.*` fetches ALL custom field values. For lists with 50+ fields, this can take 60+ seconds per API page. Select specific fields instead.

Field values are normalized to display strings (dropdowns show text, person references show names).

### Available Select Fields

| Field | Description |
|-------|-------------|
| `listEntryId` | List entry ID (same as `id`) |
| `entityId` | ID of the company/person/opportunity |
| `entityName` | Name of the entity |
| `entityType` | "company", "person", or "opportunity" |
| `listId` | Parent list ID |
| `createdAt` | Entry creation timestamp |
| `fields.<Name>` | Custom field value by name |
| `fields.*` | All custom fields (slow for 50+ fields) |

## Examples

### Pipeline Summary by Status
```json
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "groupBy": "fields.Status",
  "aggregate": {"count": {"count": true}, "totalValue": {"sum": "fields.Deal Value"}}
}
```

### Find Executive Contacts with Companies
```json
{
  "from": "persons",
  "where": {"and": [
    {"path": "fields.Seniority", "op": "eq", "value": "Executive"},
    {"path": "primaryEmailAddress", "op": "is_not_null"}
  ]},
  "include": ["companies"],
  "orderBy": [{"field": "lastName", "direction": "asc"}],
  "limit": 100
}
```

### Pipeline with Interaction Dates
```json
{
  "from": "listEntries",
  "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
  "expand": ["interactionDates"],
  "select": ["entityId", "entityName", "fields.Status"],
  "limit": 100
}
```

## Output Formats

Default: `toon` (40% fewer tokens). Use `markdown` for LLM analysis, `json` for programmatic use.

For full format reference, truncation handling, and cursor pagination: see `references/output-formats.md`

## Best Practices

1. **Start with dry-run** for complex queries
2. **Use limit** to avoid fetching too much data
3. **Filter on fields** (`fields.<name>`) so Affinity's search narrows what's downloaded
4. **Avoid deep includes** which cause N+1 API calls
5. **Use groupBy + aggregate** for reports instead of fetching all records
6. **For quantifier queries** on unbounded entities, always add `maxRecords`

## Limitations

- Affinity's search answers conditions on fields of `listEntries`, `companies` and `persons`; everything else runs locally on what comes back
- `companies` / `persons` need a field condition in any `where`; `opportunities` take no `where`
- Includes and expands cause N+1 API calls (1 per parent record)
- No cross-entity joins (use includes instead)
- Maximum 10,000 records per query for safety
- Nested quantifiers not supported
- `interactions` and `notes` are only accessible via `include`, not queryable directly
