# Pagination

Most list endpoints support both:

- `list(...)`: fetch a single page
- `iter(...)` or `all(...)`: iterate across pages automatically

## Services with pagination

| Service | Auto-pagination | Page-by-page | API |
|---------|-----------------|--------------|-----|
| `persons` | `all()`, `iter()` | `pages()` | V2 |
| `companies` | `all()`, `iter()` | `pages()` | V2 |
| `opportunities` | `all()`, `iter()` | `pages()` | V2 |
| `lists` | `all()`, `iter()` | `pages()` | V2 |
| `notes` | `iter()` | use `list()` | V1 |
| `reminders` | `iter()` | `list(cursor=…)` | V2 |
| `interactions` | `iter()` | use `list()` | V1 |
| `files` | `all()`, `iter()` | use `list()` | V1 |

!!! note "V1 vs V2 method availability"
    **V2 services** (persons, companies, opportunities, lists) have `all()` and `iter()` as aliases, plus a dedicated `pages()` method for page-by-page iteration with progress callbacks. Reminders and webhooks have `iter()`, and `list()` returns one page: pass its `pagination.next_cursor` as `list(cursor=…)` for the next.

    **V1 services** (notes, interactions, files) have `iter()` for auto-pagination. For manual page-by-page control, use `list()` with `page_token` parameter directly.

Example:

```python
from affinity import Affinity

with Affinity(api_key="your-api-key") as client:
    # Stream through all companies (memory-efficient)
    for company in client.companies.all():
        print(company.name)

    # For V1 services, use iter()
    for note in client.notes.iter(person_id=person_id):
        print(note.content)
```

## Progress callbacks

Use `on_progress` to track pagination progress for logging, progress bars, or debugging.

The `on_progress` callback is available on `PageIterator.pages()`:

```python
from affinity import Affinity, PaginationProgress

def log_progress(p: PaginationProgress) -> None:
    print(f"Page {p.page_number}: {p.items_so_far} items so far")

with Affinity(api_key="your-api-key") as client:
    # Get a PageIterator, then iterate page-by-page with progress
    iterator = client.companies.all()
    for page in iterator.pages(on_progress=log_progress):
        for company in page.data:
            process(company)
```

!!! note "Service `pages()` vs `PageIterator.pages()`"
    V2 services (persons, companies, etc.) have a direct `pages()` method for convenience.
    The `PageIterator.pages()` method shown above works with any auto-pagination method.

`PaginationProgress` provides:

| Field | Description |
|-------|-------------|
| `page_number` | 1-indexed page number |
| `items_in_page` | Items in current page |
| `items_so_far` | Cumulative items including current page |
| `has_next` | Whether more pages exist |

## Collecting results into a list

Service methods like `client.companies.all()` return a `PageIterator` for streaming iteration. While the return type annotation is `Iterator[T]` for interface compatibility, the actual object returned is a `PageIterator` which provides additional methods like `.all()` and `.pages()`.

If you need all items in a list (instead of streaming), call the `PageIterator.all()` method:

```python
from affinity import Affinity, TooManyResultsError

with Affinity(api_key="your-api-key") as client:
    try:
        # service.all() returns PageIterator
        # PageIterator.all() collects all items into a list
        iterator = client.companies.all()
        companies = iterator.all()  # Returns list[Company]
        # Or as a one-liner: client.companies.all().all()
    except TooManyResultsError as e:
        print(f"Too many results: {e}")
```

!!! warning "The `.all().all()` pattern"
    The double `.all()` can look confusing. Here's what's happening:

    1. `client.companies.all()` → returns a `PageIterator` (streams items lazily)
    2. `PageIterator.all()` → collects all items into a `list` (loads into memory)

    By default, `PageIterator.all()` raises `TooManyResultsError` if results exceed 100,000 items.

Adjust or disable the limit with the `limit` parameter:

```python
# Lower limit for safety
companies = client.companies.all().all(limit=1000)

# Disable limit (use with caution)
companies = client.companies.all().all(limit=None)
```

For very large datasets, prefer streaming directly (no collection into list):

```python
# Memory-efficient: processes one item at a time
for company in client.companies.all():
    process(company)
```

## Manual pagination

When iterating pages manually, use the `next_cursor` property to get the cursor for the next page:

```python
from affinity import Affinity

with Affinity(api_key="your-api-key") as client:
    page = client.companies.list(limit=100)

    while page.has_next:
        process(page.data)
        # Always use next_cursor for the next page cursor
        page = client.companies.list(limit=100, cursor=page.next_cursor)
```

`ids=[]` (an empty id list) returns an empty page without calling the API; `ids=None` means no
id filter. A `limit` below 1 raises `ValueError`.

!!! tip "Use `next_cursor`, not `pagination.next_cursor`"
    Always use the `next_cursor` property on `PaginatedResponse`. This works consistently
    across all services regardless of the underlying API version.

## Next steps

- [Filtering](filtering.md)
- [Field types & values](field-types-and-values.md)
- [Examples](../examples.md)
- [API reference](../reference/services/companies.md)
