# Filtering and searching

Read this before using `--filter`, `--query`, or `--saved-view`.

## Entity commands (`person ls`, `company ls`): use `--query`, NOT `--filter`

```bash
# Global-entity search — use --query for fuzzy name/email/domain search
xaffinity --readonly person ls --query "@acme.com" --max-results 20 --json
xaffinity --readonly company ls --query "Acme" --max-results 20 --json

# Department-style filters are list-specific; run on the list that defines the field
xaffinity --readonly list export "All Contacts" --filter 'Department = "Sales"' --max-results 20 --json
```

`--filter` is **not supported** on `company ls` / `person ls` / `query companies|persons|opportunities`.
Global-entity list endpoints don't filter server-side, so these raise `unsupported_filter` (exit 2).
Use `--query TERM` for fuzzy search on global entities, and `list export <LIST> --filter ...` for
list-specific field filters.

`--query` and `--filter` are mutually exclusive.

## List export: `--filter` is CLIENT-SIDE (fetches everything first)

```bash
# SLOW on large lists — downloads ALL entries, then filters locally:
xaffinity --readonly list export "Pipeline" --filter 'Status = "Active"' --all --json

# FAST — use saved views for server-side filtering:
xaffinity --readonly list export "Pipeline" --saved-view "Active Deals" --max-results 50 --json
```

`--filter` on `list export` requires `--all`, `--max-results`, or `--first-page-only` (exit 2
otherwise). **For large lists (1000+ entries), prefer `--saved-view` over `--filter`.**
For one known company/person, use `--company-id` / `--person-id` instead of any filter.

## Filter operators

```
=    exact match           'Status = "Active"'
!=   not equal             'Status != "Closed"'
=~   contains              'Email =~ "@acme"'
=^   starts with           'Name =^ "John"'
=$   ends with             'Domain =$ ".com"'
>    greater than          'Revenue > "1000000"'
<    less than
>=   greater or equal
<=   less or equal
&    AND                   'Status = "Active" & Region = "US"'
|    OR                    'Status = "New" | Status = "Pending"'
```
