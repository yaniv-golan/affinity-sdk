# Datetime Handling

This guide explains how the SDK and CLI handle datetime values, including timezone behavior.

!!! warning "SDK and CLI interpret naive datetimes differently"
    **SDK (Python)**: Naive datetime strings like `"2024-01-01"` are assumed to be **UTC**.

    **CLI**: Naive datetime strings like `--after 2024-01-01` are assumed to be **local time**.

    This difference can cause subtle bugs if you're not aware of it. For portable, reproducible behavior, always use explicit timezone suffixes (`Z` for UTC or `+HH:MM` offset).

## SDK (Python API)

All datetime fields in SDK models are **UTC-aware**:

- **API responses**: Parsed as UTC-aware datetime objects
- **Naive datetimes**: Assumed UTC when passed to models
- **Serialization**: Output uses ISO-8601 with `+00:00` offset

```python
from affinity.models.secondary import Note

# API response with Z suffix - parsed as UTC
note = Note.model_validate({"id": 1, "createdAt": "2024-01-01T12:00:00Z", ...})
assert note.created_at.tzinfo is not None  # UTC-aware

# You can safely compare datetimes
from datetime import datetime, timezone
cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
if note.created_at > cutoff:
    print("Recent note")
```

### Passing datetimes to models

When constructing models directly, naive datetimes are assumed UTC:

```python
from datetime import datetime, timezone, timedelta

# These are all equivalent:
dt1 = datetime(2024, 1, 1, 12, 0, 0)  # Naive - assumed UTC
dt2 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # Explicit UTC
dt3 = datetime(2024, 1, 1, 7, 0, 0, tzinfo=timezone(timedelta(hours=-5)))  # EST → converted to UTC
```

## CLI (Command Line)

The CLI uses **local time** for user convenience:

- **Input (naive strings)**: Interpreted as local time
- **Input (with timezone)**: Respected exactly as specified
- **Output (tables)**: Displayed in local time
- **Output (CSV)**: The API value as returned (UTC)
- **Output (JSON)**: Always UTC for machine consumption

```bash
# Date only = midnight in YOUR timezone
xaffinity interaction ls --type email --person-id 123 --after 2024-01-01

# Explicit UTC (Z suffix)
xaffinity interaction ls --type email --person-id 123 --after 2024-01-01T00:00:00Z

# Explicit timezone offset
xaffinity interaction ls --type email --person-id 123 --after 2024-01-01T00:00:00-05:00
```

### Example

If you're in EST (UTC-5) and run `--after 2024-01-01`:

| Stage | Value |
|-------|-------|
| CLI interprets as | midnight EST on Jan 1 |
| API receives | `2024-01-01T05:00:00Z` (converted to UTC) |
| Table output | times in EST |
| CSV output | times in UTC (as returned by the API) |
| JSON output | times in UTC (for scripting) |

### Why local time for CLI?

This matches how other CLI tools work (`git log`, `docker logs`, `ls -la` all display local time). When users type `--after 2024-01-01`, they think "midnight on January 1st in my timezone", not "midnight UTC".

## SDK vs CLI Summary

| Input | SDK (Pydantic model) | CLI (command flag) |
|-------|---------------------|-------------------|
| `"2024-01-01"` | midnight **UTC** | midnight **local time** → UTC |
| `"2024-01-01T12:00:00"` | 12:00 **UTC** | 12:00 **local time** → UTC |
| `"2024-01-01T12:00:00Z"` | 12:00 UTC | 12:00 UTC |
| `"2024-01-01T12:00:00-05:00"` | 17:00 UTC | 17:00 UTC |

**Key difference**: SDK assumes naive strings are UTC (matches API behavior). CLI assumes naive strings are local time (matches user expectations).

## Scripting Best Practices

For reproducible scripts that work regardless of timezone:

```bash
# Always use explicit UTC for scripting
xaffinity interaction ls --type email --person-id 123 --after 2024-01-01T00:00:00Z --json > interactions.json

# Or use explicit timezone offset
xaffinity interaction ls --type email --person-id 123 --after 2024-01-01T00:00:00-05:00 --json > interactions.json
```

For machine consumption, use `--json` output which always provides UTC timestamps.
