# Persons

## Interaction Dates

The `get()` method supports fetching interaction date summaries for a person using the
`with_interaction_dates` parameter. When enabled, the returned `Person` object will have
its `interaction_dates` and `interactions` fields populated with:

- **Last meeting date**: When the last calendar event with this person occurred
- **Next meeting date**: When the next scheduled calendar event is
- **Last email date**: When the last email exchange happened
- **Last interaction date**: The most recent interaction of any type
- Also the first email, first meeting and last chat message dates

```python
from affinity import Affinity
from affinity.types import PersonId

with Affinity(api_key="YOUR_API_KEY") as client:
    # Fetch person with interaction dates
    person = client.persons.get(
        PersonId(456),
        with_interaction_dates=True,
        with_interaction_persons=True,  # Include person IDs for each interaction
    )

    # Access interaction data
    if person.interaction_dates:
        print(f"Last meeting: {person.interaction_dates.last_event_date}")
        print(f"Next meeting: {person.interaction_dates.next_event_date}")
        print(f"Last email: {person.interaction_dates.last_email_date}")

    # Access team member IDs from interactions
    if person.interactions and person.interactions.last_event:
        team_ids = person.interactions.last_event.person_ids
        print(f"Last meeting attendees: {team_ids}")
```

### Many persons

`get()` with interaction dates makes one V1 call per person, plus one V2 call when `field_ids` or
`field_types` is given. For many persons, use the async client's `batch_get(ids,
with_interaction_dates=True, ...)`: with field filters it reads the fields in one call per 100
ids and the dates in one call per person, so about N + N/100 calls instead of 2N. With
`on_error="skip"` a person that can't be read is left out of the result.

## Finding records with Affinity's search

`client.persons.find(...)`, `iter_find(...)` and `find_body(...)` filter and sort on
Affinity's side (V2 search, beta) and return `Person` records. They work like the
[companies methods](companies.md#finding-records-with-affinitys-search) and need the
"Export All Organizations directory" permission. `search(term)` is the separate
name/email search.

```python
from affinity.criteria import Condition

for person in client.persons.iter_find(Condition("field-123", "is-not-empty")):
    print(person.id, person.first_name, person.last_name)
```

::: affinity.services.persons.PersonService

::: affinity.services.persons.AsyncPersonService
