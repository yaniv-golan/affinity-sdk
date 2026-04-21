# Filtering

> **For list entries:** prefer `--saved-view` (server-side, fast) or
> `--company-id` / `--person-id` (entity-scoped, cheap). `--filter` is
> client-side and requires a scope flag (`--all`, `--max-results`, or
> `--first-page-only`) on `list export` since v1.13. Without a scope flag,
> `list export --filter` exits 2.

V2 list endpoints accept `filter` expressions to query custom fields.

**Important:** V2 filters only work with **custom fields**, not built-in entity properties. Built-in properties like `type`, `firstName`, `domain`, etc. cannot be filtered.

## Recommended: Use the Filter Builder

Use `affinity.F` to build type-safe filter expressions:

```python
from affinity import Affinity, F

with Affinity(api_key="your-api-key") as client:
    # Recommended: Type-safe filter builder
    companies = client.companies.list(
        filter=F.field("Industry").equals("Software")
    )
```

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

# Equals
persons = client.persons.list(filter=F.field("Department").equals("Sales"))

# Contains (case-insensitive substring match)
companies = client.companies.list(filter=F.field("Industry").contains("Tech"))

# Starts with
persons = client.persons.list(filter=F.field("Title").starts_with("VP"))

# Ends with
persons = client.persons.list(filter=F.field("Email").ends_with("@acme.com"))

# Greater than (for numbers/dates)
opportunities = client.opportunities.list(filter=F.field("Amount").greater_than(100000))

# Is null / is not null
persons = client.persons.list(filter=F.field("Manager").is_null())
```

**Complex logic (AND/OR/NOT):**

```python
# AND: Both conditions must be true
active_sales = client.persons.list(
    filter=F.field("Department").equals("Sales") & F.field("Status").equals("Active")
)

# OR: Either condition can be true
tech_or_finance = client.companies.list(
    filter=F.field("Industry").equals("Technology") | F.field("Industry").equals("Finance")
)

# NOT: Negate a condition
non_archived = client.persons.list(
    filter=~F.field("Archived").equals(True)
)

# Complex: (A AND B) OR (C AND D)
result = client.companies.list(
    filter=(
        (F.field("Industry").equals("Software") & F.field("Region").equals("US"))
        | (F.field("Industry").equals("Hardware") & F.field("Region").equals("EU"))
    )
)
```

**In list (multiple values):**

```python
# Match any value in the list
multi_region = client.companies.list(
    filter=F.field("Region").in_list(["US", "Canada", "Mexico"])
)
```

## Raw Filter Strings

For CLI or advanced SDK use, you can use raw filter strings. The SDK supports both official Affinity V2 API syntax and SDK-specific extensions.

### Standard Operators (Affinity V2 API compatible)

These operators work both with the Affinity API and SDK client-side filtering:

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

### SDK Extensions (client-side filtering only)

These operators are SDK-specific and only work for client-side filtering with `--expand-filter` or `matches()`. They do NOT work with the Affinity API.

#### Word-Based Aliases `[SDK Extension]`

Human/LLM-friendly aliases for official operators:

| Alias | Equivalent | Example |
|---|---|---|
| `contains` | `=~` | `name contains "Corp"` |
| `starts_with` | `=^` | `name starts_with "Acme"` |
| `ends_with` | `=$` | `email ends_with "@acme.com"` |
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

**Example equivalents (prefer official syntax for API portability):**

```bash
# These are equivalent:
xaffinity list export 123 --expand-filter '"Team Member" =~ LB'       # Official V2 API
xaffinity list export 123 --expand-filter '"Team Member" contains LB' # SDK alias

xaffinity list export 123 --expand-filter 'email = *'           # Official V2 API
xaffinity list export 123 --expand-filter 'email is not null'   # SDK alias

xaffinity list export 123 --expand-filter 'count > 5'   # Official V2 API
xaffinity list export 123 --expand-filter 'count gt 5'  # SDK alias
```

## CLI Examples

```bash
# Simple filter
xaffinity person ls --filter 'Department = "Sales"'

# Contains (case-insensitive)
xaffinity company ls --filter 'Industry =~ "tech"'

# Numeric comparison
xaffinity opportunity ls --filter 'Amount > 100000'

# Multiple conditions with AND
xaffinity person ls --filter 'Status = "Active" & Department = "Sales"'

# Multiple conditions with OR
xaffinity company ls --filter 'Region = "US" | Region = "Canada"'

# Null check
xaffinity person ls --filter 'Manager != *'

# Collection operators (SDK extension)
xaffinity list export 123 --expand-filter 'Status in [Active, Pending]'
xaffinity list export 123 --expand-filter 'Tags has_any [priority, urgent]'
```

## What Can Be Filtered?

**Custom fields** (added to entities in Affinity):

Python SDK:
- `F.field("Department").equals("Sales")`
- `F.field("Status").contains("Active")`

CLI (raw filter syntax):
- `Department = "Sales"`
- `Status =~ "Active"`

**Built-in properties** (cannot be filtered with V2 filter expressions):
- Person: `type`, `firstName`, `lastName`, `primaryEmail`, `emailAddresses`
- Company: `name`, `domain`, `domains`
- Opportunity: `name`, `listId`

For built-in properties, retrieve all data and filter client-side (see [CSV Export Guide](./csv-export.md) for examples).

## Filtering in List Exports (CLI)

The `list export` command supports two filter options with **identical syntax** but different behavior:

| Option | What It Filters | Where Filtering Happens |
|--------|----------------|------------------------|
| `--filter` | List entries | Server-side (API) |
| `--expand-filter` | Expanded entities (people, companies) | Client-side (after fetch) |

### Why the difference?

The Affinity API supports filtering for list entries, but **does not support filtering associations**.

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
# Server-side: only fetch Active opportunities
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

## Next steps

- [Pagination](pagination.md)
- [Field types & values](field-types-and-values.md)
- [Examples](../examples.md)
- [Filters reference](../reference/filters.md)
