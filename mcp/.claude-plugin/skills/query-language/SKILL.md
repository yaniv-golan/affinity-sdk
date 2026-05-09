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

// Add a filter
{"from": "persons", "where": {"path": "email", "op": "contains", "value": "@acme.com"}, "limit": 10}

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
  "where": {"path": "email", "op": "contains", "value": "@acme.com"},
  "select": ["id", "firstName", "lastName", "email"],
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
| `persons` | No | Global entity |
| `companies` | No | Global entity |
| `opportunities` | No | Global entity |
| `lists` | No | Global entity |
| `listEntries` | **Yes** — must filter by `listId` or `listName` | List-scoped |

**`interactions` and `notes` cannot be queried directly** — access them via `include` on other entities.

## Key Rules

1. **`listEntries` MUST filter by `listId` or `listName`** — use `listName` (not `list.name`)
2. **Always `dryRun: true` first** for queries with `include`, `expand`, or quantifiers
3. **Set `limit`** to avoid fetching too much data
4. **Use `select`** to reduce output size — avoid `fields.*` on lists with 50+ fields
5. **Use `maxRecords`** for quantifier queries on unbounded entities (persons/companies)

## Filtering

### Basic Conditions

```json
{"path": "email", "op": "contains", "value": "@acme.com"}
{"path": "fields.Status", "op": "eq", "value": "Active"}
{"path": "amount", "op": "gte", "value": 10000}
```

Common operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `contains`, `starts_with`, `in`, `is_null`, `is_not_null`

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
// Persons at 2+ companies
{"from": "persons", "where": {"path": "companies._count", "op": "gte", "value": 2}, "limit": 50}

// Persons where ALL companies have .com domains
{"from": "persons", "where": {"all": {"path": "companies", "where": {"path": "domain", "op": "contains", "value": ".com"}}}, "limit": 50}

// Persons with at least one meeting interaction
{"from": "persons", "where": {"exists": {"from": "interactions", "where": {"path": "type", "op": "eq", "value": "meeting"}}}, "limit": 50}
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

### Find VIP Contacts with Companies
```json
{
  "from": "persons",
  "where": {"and": [
    {"path": "email", "op": "is_not_null"},
    {"path": "fields.VIP", "op": "eq", "value": true}
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
3. **Be specific with where** to reduce client-side filtering
4. **Avoid deep includes** which cause N+1 API calls
5. **Use groupBy + aggregate** for reports instead of fetching all records
6. **For quantifier queries** on unbounded entities, always add `maxRecords`

## Limitations

- All filtering except `listEntries` field filters happens client-side
- Includes and expands cause N+1 API calls (1 per parent record)
- No cross-entity joins (use includes instead)
- Maximum 10,000 records per query for safety
- Nested quantifiers not supported
- `interactions` and `notes` are only accessible via `include`, not queryable directly
