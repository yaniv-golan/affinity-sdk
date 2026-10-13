# Filters

::: affinity.filters

## Search criteria (`affinity.criteria`)

Conditions and sorts for Affinity's search: `companies.find`, `persons.find` and
`lists.entries(list_id).find`. Affinity applies them on its side, so only matching records
are downloaded. `affinity.filters` (above) matches records after they're downloaded.

- `Condition(field_id, operator, value=..., value_type=None, attribute_id=None)`: one
  test on a field, with Affinity's operator name (`is-any-of`, `has-any-of`, `contains`,
  `is-greater-than`, `is-between`, `is-after`, `is-within-the-last`, `is-empty`, ...).
- `all_of(...)` / `any_of(...)`: AND / OR groups (up to 50 items each), nestable.
- `Sort(field_id, "asc" | "desc")`: up to 5 per search.
- `SearchCriteria`: filters, sorts and a search term together.

See [Companies service](services/companies.md#finding-records-with-affinitys-search) for
the `find` methods, paging and permissions.

::: affinity.criteria
