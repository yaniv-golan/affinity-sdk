# Filtering

> **Where filtering happens:** `list export --filter`, `company ls --filter`,
> `person ls --filter` and `query` send the conditions on fields that Affinity's
> search can answer to Affinity, so only matching records are downloaded. The
> whole filter then runs locally on what comes back, so results are the same as a
> local filter. See [Filtering on Affinity's side](#filtering-on-affinitys-side).
> For one known company or person on a list, `--company-id` / `--person-id` is
> still the cheapest lookup.

One filter language is used everywhere: the `--filter` text of the CLI, the JSON
`where` of `query` (same conditions, JSON spelling), and the SDK's `affinity.filters`
/ `F` builder. Every condition means the same in all of them.

**Fields and attributes:** conditions on **fields** (custom, global and enriched
fields) can go to Affinity. Conditions on a record's own attributes (`name`,
`domain`, `firstName`, `primaryEmail`, ...) are checked locally on the records
Affinity returns.

## Recommended: Use the Filter Builder

Use `affinity.F` to build type-safe filter expressions. In the SDK, `filter=` on
list entries is matched locally after the entries are fetched; to filter on
Affinity's side from Python, use [`find`](#sdk-filtering-on-affinitys-side-with-find).

```python
from affinity import Affinity, F
from affinity.types import ListId

with Affinity(api_key="your-api-key") as client:
    # Recommended: Type-safe filter builder
    entries = client.lists.entries(ListId(123)).list(
        filter=F.field("Industry").equals("Software")
    )
```

`client.companies.list()`, `client.persons.list()` and `client.opportunities.list()`
don't take `filter=` (they raise `ValueError`).

Benefits of the filter builder:
- Prevents syntax errors with type checking
- Handles escaping automatically
- Makes it clear you're filtering custom fields (via `field()` method)
- Provides IDE autocomplete for filter operations

**CLI users:** The CLI uses raw filter string syntax. See examples below.

## Filter Builder Examples

**Simple comparisons:**

```python
from affinity import Affinity, F
from affinity.types import ListId

entries = client.lists.entries(ListId(123))

# Equals
rows = entries.list(filter=F.field("Department").equals("Sales"))

# Contains (case-insensitive substring match)
rows = entries.list(filter=F.field("Industry").contains("Tech"))

# Starts with
rows = entries.list(filter=F.field("Title").starts_with("VP"))

# Ends with
rows = entries.list(filter=F.field("Email").ends_with("@acme.com"))

# Greater than (for numbers/dates)
rows = entries.list(filter=F.field("Amount").greater_than(100000))

# Is null / is not null
rows = entries.list(filter=F.field("Manager").is_null())
```

**Complex logic (AND/OR/NOT):**

```python
# AND: Both conditions must be true
active_sales = entries.list(
    filter=F.field("Department").equals("Sales") & F.field("Status").equals("Active")
)

# OR: Either condition can be true
tech_or_finance = entries.list(
    filter=F.field("Industry").equals("Technology") | F.field("Industry").equals("Finance")
)

# NOT: Negate a condition
non_archived = entries.list(
    filter=~F.field("Archived").equals(True)
)

# Complex: (A AND B) OR (C AND D)
result = entries.list(
    filter=(
        (F.field("Industry").equals("Software") & F.field("Region").equals("US"))
        | (F.field("Industry").equals("Hardware") & F.field("Region").equals("EU"))
    )
)
```

**In list (multiple values):**

```python
# Match any value in the list
multi_region = entries.list(
    filter=F.field("Region").in_list(["US", "Canada", "Mexico"])
)
```

## Raw Filter Strings

For CLI or advanced SDK use, you can use raw filter strings: the `--filter` text of
`list export`, `company ls` and `person ls`, and `affinity.filters.parse()` in the SDK.

### Standard Operators

These operators work in every `--filter` and in `affinity.filters`:

| Meaning | Operator | Example |
|---|---|---|
| and | `&` | `field("A") = 1 & field("B") = 2` |
| or | `\|` | `field("A") = 1 \| field("B") = 2` |
| not | `!` | `!(field("A") = 1)` |
| equals | `=` | `field("Industry") = "Software"` |
| not equals | `!=` | `field("Status") != "inactive"` |
| starts with | `=^` | `field("Name") =^ "Ac"` |
| ends with | `=$` | `field("Name") =$ "Inc"` |
| contains | `=~` | `field("Title") =~ "Manager"` |
| greater than | `>` | `field("Count") > 5` |
| greater than or equal | `>=` | `field("Count") >= 5` |
| less than | `<` | `field("Count") < 10` |
| less than or equal | `<=` | `field("Count") <= 10` |
| is NULL | `!= *` | `field("Email") != *` |
| is not NULL | `= *` | `field("Email") = *` |
| is empty string | `= ""` | `field("Notes") = ""` |
| collection exact match | `= [A, B]` | `field("Tags") = [tech, startup]` |
| collection contains all | `=~ [A, B]` | `field("Tags") =~ [tech, startup]` |
| collection empty | `= []` | `field("Tags") = []` |

### SDK Extensions

These operators work in every `--filter` (including `--expand-filter`) and in
`affinity.filters` / `matches()`. Each one has a JSON spelling in `query`.

#### Word-Based Aliases `[SDK Extension]`

Human/LLM-friendly aliases for official operators:

| Alias | Equivalent | Example |
|---|---|---|
| `contains` | `=~` | `name contains "Corp"` |
| `starts_with` | `=^` | `name starts_with "Acme"` |
| `ends_with` | `=$` | `email ends_with "@acme.com"` |
| `starts_word` | (none) | `Owner starts_word "Jan"` |
| `gt` | `>` | `count gt 5` |
| `gte` | `>=` | `count gte 5` |
| `lt` | `<` | `count lt 10` |
| `lte` | `<=` | `count lte 10` |
| `is null` | `!= *` | `email is null` |
| `is not null` | `= *` | `email is not null` |
| `is empty` | `= ""` or `= []` | `tags is empty` |

#### Additional Collection Operators `[SDK Extension]`

| Operator | Syntax | Description |
|---|---|---|
| in list | `in [A, B, C]` | Value equals any in list |
| between | `between [1, 10]` | Value in range (inclusive) |
| has any | `has_any [A, B]` | Array contains any of values (exact match) |
| has all | `has_all [A, B]` | Array contains all values (exact match) |
| contains any | `contains_any [A, B]` | Any element contains any substring |
| contains all | `contains_all [A, B]` | Any element contains all substrings |

#### Matching rules

- **`starts_word`**: a word in the value starts with the text, case-insensitive, the way
  Affinity's search matches names. `"Jan"` matches "Jane Doe" and "Mary-Jane"; `"ane"`
  doesn't. JSON spelling in `query`: `{"path": "fields.Owner", "op": "starts_word", "value": "Jan"}`.
- **Person and company fields** are compared by name ("Jane Doe", "Acme"). In `--filter`
  and `query`, `contains` (and `contains_any` / `contains_all`) on a person or company
  field is refused: use `starts_word`.
- **Empty multi-value fields**: a multi-value field with no items counts as empty.
  `is null` (`!= *`) matches it and `is not null` (`= *`) doesn't.

**Example equivalents:**

```bash
# These are equivalent:
xaffinity list export 123 --expand-filter '"Team Member" =~ LB'       # Symbol
xaffinity list export 123 --expand-filter '"Team Member" contains LB' # Word alias

xaffinity list export 123 --expand-filter 'email = *'           # Symbol
xaffinity list export 123 --expand-filter 'email is not null'   # Word alias

xaffinity list export 123 --expand-filter 'count > 5'   # Symbol
xaffinity list export 123 --expand-filter 'count gt 5'  # Word alias
```

## CLI Examples

```bash
# Simple filter on a list field
xaffinity list export Pipeline --filter 'Status = "Active"'

# Companies by a company field (Affinity filters on its side)
xaffinity company ls --filter 'Industry = "Software"' --max-results 50

# Contains (case-insensitive)
xaffinity company ls --filter 'Industry =~ "tech"' --max-results 50

# Numeric comparison
xaffinity list export Pipeline --filter 'Amount > 100000'

# Multiple conditions with AND
xaffinity person ls --filter 'Status = "Active" & Department = "Sales"' --max-results 50

# Multiple conditions with OR
xaffinity list export Pipeline --filter 'Region = "US" | Region = "Canada"'

# A person field by name
xaffinity list export Pipeline --filter 'Owner starts_word "Jan"'

# Null check
xaffinity list export Pipeline --filter 'Owner != *'

# Collection operators (SDK extension)
xaffinity list export 123 --expand-filter 'Status in [Active, Pending]'
xaffinity list export 123 --expand-filter 'Tags has_any [priority, urgent]'
```

## What Can Be Filtered?

**Fields** (custom, global and enriched fields):

Python SDK:
- `F.field("Department").equals("Sales")`
- `F.field("Status").contains("Active")`

CLI (raw filter syntax):
- `Department = "Sales"`
- `Status =~ "Active"`

**Record attributes** (checked locally on the records that come back):
- Person: `type`, `firstName`, `lastName`, `primaryEmail`, `emailAddresses`
- Company: `name`, `domain`, `domains`
- List entry: `entityName`, `entityId`, `entityType`, `createdAt`

On `company ls` and `person ls`, a `--filter` must include at least one condition on a
company or person field: Affinity's search applies that part, and attribute conditions
(e.g. `name =~ "Acme"`) run on what it returns. A filter with only attribute conditions is
refused, since it would mean reading Affinity's whole company directory (about 12 million
records). To search by name, domain or email, use `--query TERM`.

## Filtering on Affinity's side

`list export --filter`, `company ls --filter`, `person ls --filter` and `query` split a
filter in two: the conditions Affinity's search can answer are sent to Affinity, and only
matching records are downloaded. The **whole** filter still runs locally on what comes
back, so the result is the same as a local filter.

What Affinity's search answers:

| Field type | Conditions |
|---|---|
| Dropdown, ranked dropdown | `=`, `in`, `contains`, `starts_with`, `ends_with`, `starts_word` on the option text; `!=` and `!(... = / in ...)` |
| Multi-select dropdown | membership `=`, `in`, `has_any`, `has_all`, `contains` / `starts_word` on the option text, `!=` |
| Number | `=`, `>`, `>=`, `<`, `<=`, `between`, `!=` |
| Date | `>`, `>=`, `<`, `<=`, `=`, `between`, also relative dates like `-30d` |
| Text | `=`, `contains`, `starts_with`, `ends_with`, `starts_word` (sent as Affinity's case-insensitive contains; the exact check runs locally) |
| Most field types | `is null`, `is not null` |
| Person or company field | `starts_word` with plain text of 3+ letters or digits, one per query, at the top level (sent as Affinity's name search) |

What stays local: conditions on record attributes (`name`, `entityName`, `createdAt`, ...),
related-record conditions in `query` (`all`, `none`, `exists`, `_count`), `expr`,
`contains_any` / `contains_all`, and an OR with any branch Affinity can't answer.

**Enriched fields** (`affinity-data-*`, e.g. funding and headcount): on list entries they're
sent as "condition OR field is empty", because Affinity's search reads a few records' enriched
values as empty; results stay exact. On `company ls`, `person ls` and `query` on companies or
persons they're sent as they are, and the result carries a warning that a few records may be
missed.

**Scope flags and paging:**

- `list export --filter`: when Affinity applies the whole filter, `--all`, `--max-results`
  and `--first-page-only` aren't required (page one is the first page of matches). When part
  of the filter runs locally, one of them is required; the error's `details.local` names the
  conditions that stay local.
- `company ls --filter` / `person ls --filter`: when part of the filter runs locally, `--all`
  or `--max-results` is required.
- The next-page cursor of a filtered run starts with `xsearch1:` and carries the search, so
  `--cursor` alone resumes it.
- If Affinity refuses the search on `list export` or a `listEntries` query (e.g. the API key
  lacks the "Export data from Lists" permission), the command reads the whole list and filters
  locally, with a warning.
- `XAFFINITY_QUERY_SERVER_FILTER=0` turns this off for `query` and `list export` (to compare
  results or debug).

## SDK: filtering on Affinity's side with `find`

`client.companies.find(...)`, `client.persons.find(...)` and
`client.lists.entries(list_id).find(...)` call Affinity's search (V2, beta): Affinity
filters and sorts, and only matching records are downloaded. Conditions are built with
`affinity.criteria` and use Affinity's operator names (`is-any-of`, `has-any-of`,
`contains`, `is-greater-than`, `is-between`, `is-after`, `is-within-the-last`,
`is-empty`, ...).

```python
from datetime import date
from affinity.criteria import Condition, Sort, all_of

page = client.companies.find(
    all_of(
        Condition("field-498220", "is-any-of", [3, 4]),
        Condition("affinity-data-last-funding-date", "is-after", date(2025, 1, 1)),
    ),
    sort=[Sort("affinity-data-total-funding-amount", "desc")],
    limit=50,
)
```

Values are ids for company, person, list and dropdown fields (dropdown values are option
ids), dates are `datetime.date` or `"YYYY-MM-DD"`, and relative windows are `(6, "month")`.
Search needs the "Export All Organizations directory" permission (companies, persons) or
"Export data from Lists" (list entries). See [Companies service](../reference/services/companies.md#finding-records-with-affinitys-search)
for paging and the full signature.

## Filtering in List Exports (CLI)

The `list export` command supports two filter options with **identical syntax** but different behavior:

| Option | What It Filters | Where Filtering Happens |
|--------|----------------|------------------------|
| `--filter` | List entries | Affinity's search where it can, the rest locally |
| `--expand-filter` | Expanded entities (people, companies) | Client-side (after fetch) |

### Why the difference?

Affinity's search filters list entries, but **it doesn't filter associations**.

When you use `--expand persons`, the CLI:

1. Fetches the list entries (can be filtered with `--filter`)
2. For each entry, fetches ALL associated people (API returns all, no filter option)
3. Filters the people locally based on `--expand-filter`

This means `--expand-filter`:

- Uses the same syntax as `--filter` for consistency
- Is applied after fetching data (doesn't reduce API calls)
- Supports SDK extension operators (word aliases, collection operators)
- Still useful for reducing output size and focusing on relevant associations

### Example

```bash
# Affinity's search: only fetch Active opportunities
# Client-side: only include people with valid email status
xaffinity list export 275454 \
  --filter "Status=Active" \
  --expand persons \
  --expand-filter '"Primary Email Status"=Valid | "Primary Email Status"=Unknown | "Primary Email Status" is null' \
  --all --csv > output.csv
```

### Performance consideration

Since `--expand-filter` is client-side, all associations are still fetched from the API.
For large lists with many associations, the export may take time even if the filter
reduces the final output significantly. Use `--dry-run` to estimate API calls.

## Searching notes, files and companies

Filters match field values. To find records by **content or description**, use the V2
search endpoints instead:

| Need | CLI | SDK |
|---|---|---|
| Notes whose text mentions something | `note search "pricing"` | `client.notes.search("pricing")` |
| Files whose contents mention something | `file search "pitch deck"` | `client.files.search("pitch deck")` |
| Companies matching a description | `company search "AI infrastructure startups"` | `client.companies.semantic_search("AI infrastructure startups")` |
| A company by name or domain | `company ls --query acme` | `client.companies.search("acme")` |

- Each call returns at most 100 results, ranked by relevance, with no pagination
  (`--max-results`/`limit` 1–100; defaults 20 for notes and files, 100 for companies).
- Note and file search take a 3–500 character prompt and can be narrowed to one company
  (`--company-id` / `company_id`) or to given ids (`--note-id`, `--file-id` /
  `note_ids`, `file_ids`), not both.
- Company search can be narrowed to company lists (`--list` / `list_ids`) and returns an
  `explanation` of how the prompt was read (`meta.explanation` in CLI JSON).
- Results carry ids: follow up with `note get`, `file-url` / `company files read`, or
  `company get`.
- The searches are `POST` requests that only read: they work under `--readonly` /
  `WritePolicy.DENY` and are retried on rate limits like other reads.

```python
from affinity import Affinity

with Affinity(api_key="your-api-key") as client:
    for hit in client.notes.search("Series B terms", limit=5):
        print(hit.note.id, hit.note.kind, hit.preview[:80])

    result = client.companies.semantic_search("seed-stage fintech in Europe", limit=10)
    print(result.explanation)
    for company in result.data:
        print(company.id, company.name, company.score_float)
```

## Next steps

- [Pagination](pagination.md)
- [Field types & values](field-types-and-values.md)
- [Examples](../examples.md)
- [Filters reference](../reference/filters.md)
