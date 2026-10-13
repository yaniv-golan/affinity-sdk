# Filtering and searching

Read this before using `--filter`, `--query`, or `--saved-view`.

## Entity commands (`person ls`, `company ls`): `--query` for names, `--filter` for fields

```bash
# Name/email/domain search: --query (fuzzy)
xaffinity --readonly person ls --query "@acme.com" --max-results 20 --json
xaffinity --readonly company ls --query "Acme" --max-results 20 --json

# Company/person fields: --filter, applied by Affinity's search
xaffinity --readonly company ls --filter 'Industry = "Software"' --max-results 20 --json
xaffinity --readonly company ls --filter 'Industry = "Software" & name =~ "Labs"' --max-results 20 --json

# List fields (Status, Stage, Owner): run on the list that defines the field
xaffinity --readonly list export "Pipeline" --filter 'Status = "Active"' --max-results 20 --json
```

On `company ls` / `person ls`, a `--filter` must include a condition on a company or person
field. Attribute conditions (`name`, `domain`, `primaryEmail`, ...) run on what Affinity's search
returns. A filter with only attribute conditions (e.g. `name =~ "Acme"`) is refused with a hint
to use `--query`. Filters on enriched fields (funding, headcount, ...) warn that a few records may
be missed.

`--query` and `--filter` are mutually exclusive.

## `--filter` goes through Affinity's search

`list export`, `company ls` and `person ls` send the conditions on fields that Affinity's search
can answer to Affinity, so only matching records are downloaded. The whole filter then runs
locally on what comes back, so results are exact.

```bash
# Affinity applies the whole filter: no scope flag needed, page one is the first page of matches
xaffinity --readonly list export "Pipeline" --filter 'Status = "Active"' --json

# Part runs locally (entityName is not a field): --all, --max-results or --first-page-only required
xaffinity --readonly list export "Pipeline" --filter 'Status = "Active" & entityName =~ "Labs"' --max-results 50 --json
```

- When part of the filter stays local and no scope flag is given, the command exits 2 and
  `error.details.local` names the conditions Affinity can't apply. On `company ls` /
  `person ls` the scope flags are `--all` or `--max-results`.
- Affinity applies: dropdown and multi-select dropdown values (`=`, `!=`, `in`, `has_any`,
  `has_all`, contains/starts on the option text), numbers and dates (`=`, `!=`, `>`, `>=`, `<`,
  `<=`, `between`, relative dates like `-30d`), text (`=`, `=~`, `=^`, `=$`, `starts_word`),
  `is null` / `is not null`, and `starts_word` on one person or company field.
- Stays local: record attributes (`entityName`, `name`, `createdAt`, ...), `contains_any` /
  `contains_all`, and an OR with a branch Affinity can't answer.
- A filtered run's next-page cursor starts with `xsearch1:`; `--cursor` alone resumes it.
- If Affinity refuses the search on `list export` (key without the "Export data from Lists"
  permission), the export reads the whole list and filters locally, with a warning on stderr.
- `--saved-view` also filters on Affinity's side, by the view's own criteria.
- For one known company/person, use `--company-id` / `--person-id` instead of any filter.

## Filter operators

```
=    exact match           'Status = "Active"'
!=   not equal             'Status != "Closed"'
=~   contains              'Email =~ "@acme"'
=^   starts with           'Name =^ "John"'
=$   ends with             'Domain =$ ".com"'
starts_word                'Owner starts_word "Jan"'
>    greater than          'Revenue > "1000000"'
<    less than
>=   greater or equal
<=   less or equal
&    AND                   'Status = "Active" & Region = "US"'
|    OR                    'Status = "New" | Status = "Pending"'
```

- `starts_word`: a word in the value starts with the text, case-insensitive, the way Affinity's
  search matches names ("Jan" matches "Jane Doe" and "Mary-Jane"; "ane" doesn't).
- Person and company field values are compared by name. `=~` (contains) on a person or company
  field is refused: use `starts_word`.
- An empty multi-value field counts as empty: `Field != *` (is null) matches it.
- The same language is the JSON `where` of `xaffinity query`; every condition means the same.
