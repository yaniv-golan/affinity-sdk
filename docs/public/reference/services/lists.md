# Lists

## Finding list entries with Affinity's search

`client.lists.entries(list_id).find(...)`, `iter_find(...)` and `find_body(...)` filter
and sort a list's entries on Affinity's side (V2 search, beta) and return the same
entries as `entries(list_id).list()`. They work like the
[companies methods](companies.md#finding-records-with-affinitys-search) and need the
"Export data from Lists" permission.

```python
from affinity.criteria import Condition, Sort
from affinity.types import ListId

page = client.lists.entries(ListId(123)).find(
    Condition("field-456", "is-within-the-last", (30, "day")),
    sort=Sort("field-789", "desc"),
    limit=100,
)
```

::: affinity.services.lists.ListService

::: affinity.services.lists.AsyncListService
