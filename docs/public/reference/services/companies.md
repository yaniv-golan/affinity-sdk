# Companies

Note: `CompanyService` includes two **v1-only** exceptions for company -> people associations:
`get_associated_person_ids(...)` and `get_associated_people(...)`. V2 does not expose a direct
company -> people relationship endpoint, so these methods use the v1 organizations API under
the hood. They are documented as exceptions and may be superseded when v2 adds parity.

## Interaction Dates

The `get()` method supports fetching interaction date summaries for a company using the
`with_interaction_dates` parameter. When enabled, the returned `Company` object will have
its `interaction_dates` and `interactions` fields populated with:

- **Last meeting date**: When the last calendar event with this company occurred
- **Next meeting date**: When the next scheduled calendar event is
- **Last email date**: When the last email exchange happened
- **Last interaction date**: The most recent interaction of any type

```python
from affinity import Affinity
from affinity.types import CompanyId

with Affinity(api_key="YOUR_API_KEY") as client:
    # Fetch company with interaction dates
    company = client.companies.get(
        CompanyId(123),
        with_interaction_dates=True,
        with_interaction_persons=True,  # Include person IDs for each interaction
    )

    # Access interaction data
    if company.interaction_dates:
        print(f"Last meeting: {company.interaction_dates.last_event_date}")
        print(f"Next meeting: {company.interaction_dates.next_event_date}")
        print(f"Last email: {company.interaction_dates.last_email_date}")

    # Access team member IDs from interactions
    if company.interactions and company.interactions.last_event:
        person_ids = company.interactions.last_event.person_ids
        print(f"Last meeting attendees: {person_ids}")
```

## Finding records with Affinity's search

`find()` filters and sorts on Affinity's side (V2 search, beta), so only matching
records are downloaded. The same methods exist on `client.persons` and on
`client.lists.entries(list_id)`.

```python
find(
    where=None, *, sort=None, term=None, term_fields=None, field_ids=None,
    field_types=None, limit=None, total_count=False, cursor=None,
) -> PaginatedResponse[Company]
iter_find(where=None, *, sort=None, term=None, term_fields=None,
          field_ids=None, field_types=None) -> Iterator[Company]
find_body(body, *, field_ids=None, field_types=None, limit=None,
          total_count=False, cursor=None) -> PaginatedResponse[Company]
```

- `where`: a `Condition` or a group from `all_of(...)` / `any_of(...)`
  ([`affinity.criteria`](../filters.md#search-criteria-affinitycriteria)).
- `sort`: one `Sort` or a list of up to 5.
- `term`: a keyword (3+ characters) matched against names, domains and emails, and
  against the fields in `term_fields`.
- `field_ids` / `field_types`: the field values to return with each record.
- `limit`: 1-100 records per page.
- `iter_find` yields every matching record across pages. `find_body` takes a prebuilt
  search body (the API's `SearchCriteria` JSON).

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

**Next page:** call `find` again with the same criteria and `cursor=page.next_cursor`.
A cursor alone isn't enough: the search needs its body again.

**Values:** each condition's value type is read from the fields metadata when omitted (one
cached request), and the operator is checked against the field. Company, person, list and
dropdown fields take ids (dropdown values are option ids); dates are `datetime.date` or
`"YYYY-MM-DD"` (not datetimes); relative windows are `(6, "month")`.

**Permissions:** search needs the "Export All Organizations directory" permission
(companies, persons) or "Export data from Lists" (list entries). Without it, `find` raises
`AuthorizationError` naming the permission. Search is a read, so it's allowed in read-only
mode.

`search(term)` is the separate name/domain search and needs no extra permission.

::: affinity.services.companies.CompanyService

::: affinity.services.companies.AsyncCompanyService
