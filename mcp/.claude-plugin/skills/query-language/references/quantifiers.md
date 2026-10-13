# Quantifiers Reference

Filter entities based on related entity properties. **All quantifiers cause N+1 API calls — always `dryRun: true` first.**

Quantifiers always run locally. On `persons` and `companies` the `where` must also include a condition on one of their fields (`fields.<name>`), which Affinity's search applies first; a `where` with only quantifiers is refused. `opportunities` take no `where`.

## ALL Quantifier

All related items must match the condition:

```json
{
  "from": "persons",
  "where": {
    "and": [
      { "path": "fields.Seniority", "op": "eq", "value": "Executive" },
      { "all": { "path": "companies", "where": { "path": "domain", "op": "contains", "value": ".com" }}}
    ]
  }
}
```

**Note:** Returns `true` for records with no related items (vacuous truth). To require at least one, combine with `_count`:

```json
{
  "where": {
    "and": [
      { "path": "companies._count", "op": "gte", "value": 1 },
      { "all": { "path": "companies", "where": { "path": "domain", "op": "contains", "value": ".com" }}}
    ]
  }
}
```

## NONE Quantifier

No related items may match the condition:

```json
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "Dealflow" },
      { "none": { "path": "interactions", "where": { "path": "type", "op": "eq", "value": "spam" }}}
    ]
  }
}
```

## EXISTS Clause

At least one related item exists (optionally matching a filter):

```json
// Simple existence check
{
  "from": "listEntries",
  "where": {
    "and": [
      { "path": "listName", "op": "eq", "value": "Dealflow" },
      { "exists": { "from": "interactions" }}
    ]
  }
}

// With filter (persons need a field condition too)
{
  "from": "persons",
  "where": {
    "and": [
      { "path": "fields.Seniority", "op": "eq", "value": "Executive" },
      { "exists": { "from": "interactions", "where": { "path": "type", "op": "eq", "value": "meeting" }}}
    ]
  }
}
```

## Count Pseudo-Field

Count related items and compare:

```json
// Persons with 2 or more companies
{ "path": "companies._count", "op": "gte", "value": 2 }

// Persons with no interactions
{ "path": "interactions._count", "op": "eq", "value": 0 }
```

## Available Relationships for Quantifiers

| From Entity | Available Relationship Paths |
|-------------|------------------------------|
| `persons` | `companies`, `opportunities`, `interactions`, `notes`, `listEntries` |
| `companies` | `persons`, `opportunities`, `interactions`, `notes`, `listEntries` |
| `opportunities` | `persons`, `companies`, `interactions` |

## Performance

**Quick decision:**
- `listEntries` with quantifiers: Safe (bounded by list size)
- `persons`/`companies` with quantifiers: Requires a field condition and `maxRecords`
- `opportunities`: no `where`, so no quantifiers

**Why?** Quantifier operations make N+1 API calls (one per record). On a database with 50,000 persons, this could take 26+ minutes.

**Recommended approach:**
1. Start from `listEntries` (bounded by list size) instead of unbounded entities
2. Add cheap pre-filters before quantifier conditions to reduce N+1 calls
3. Use `maxRecords` to explicitly limit scope: `maxRecords: 100`
4. Use `dryRun: true` to preview estimated API calls before running

**Example — safe quantifier query:**
```json
{
  "query": {
    "from": "listEntries",
    "where": {
      "and": [
        {"path": "listName", "op": "eq", "value": "Target Companies"},
        {"path": "persons._count", "op": "gte", "value": 3}
      ]
    }
  },
  "maxRecords": 1000
}
```

## Limitations

- Nested quantifiers not supported (would cause exponential API calls)
- OR clauses containing quantifiers cannot benefit from lazy loading optimization
