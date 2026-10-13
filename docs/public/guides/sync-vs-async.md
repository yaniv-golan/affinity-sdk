# Sync vs async

## Sync

Use `Affinity`:

```python
from affinity import Affinity

with Affinity(api_key="your-api-key") as client:
    for person in client.persons.all():
        print(person.first_name)
```

## Async

Use `AsyncAffinity`:

```python
from affinity import AsyncAffinity

async def main() -> None:
    async with AsyncAffinity(api_key="your-api-key") as client:
        async for company in client.companies.all():
            print(company.name)
```

## Parity

`AsyncAffinity` mirrors the `Affinity` service surface area.

| Service | Sync (`Affinity`) | Async (`AsyncAffinity`) |
|---|:---:|:---:|
| companies | ✅ | ✅ |
| persons | ✅ | ✅ |
| lists | ✅ | ✅ |
| lists.entries | ✅ | ✅ |
| opportunities | ✅ | ✅ |
| tasks | ✅ | ✅ |
| notes | ✅ | ✅ |
| reminders | ✅ | ✅ |
| webhooks | ✅ | ✅ |
| interactions | ✅ | ✅ |
| transcripts | ✅ | ✅ |
| teams | ✅ | ✅ |
| users | ✅ | ✅ |
| feedback | ✅ | ✅ |
| fields | ✅ | ✅ |
| field_values | ✅ | ✅ |
| field_value_changes | ✅ | ✅ |
| files | ✅ | ✅ |
| relationships | ✅ | ✅ |
| auth | ✅ | ✅ |
| rate_limits | ✅ | ✅ |

Nested services (like `lists.entries`) have the same sync/async parity as their parent services.

## Next steps

- [Getting started](../getting-started.md)
- [Examples](../examples.md)
- [API versions & routing](api-versions-and-routing.md)
