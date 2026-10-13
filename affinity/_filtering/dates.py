"""Relative date parsing for query WHERE clauses.

This module provides parsing for relative date strings like "-30d", "today", etc.
Private module: not part of the public SDK API (the CLI and the SDK share it).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# =============================================================================
# Patterns
# =============================================================================

# Pattern for relative dates: -30d, +7d, -4w, -3m, -1y
RELATIVE_DATE_PATTERN = re.compile(r"^([+-]?\d+)([dwmy])$")

# Pattern for ISO dates
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?")


# =============================================================================
# Parsing Functions
# =============================================================================


def parse_relative_date(
    value: str,
    *,
    now: datetime | None = None,
    use_utc: bool = True,
) -> datetime:
    """Parse relative date strings.

    Supports:
    - Relative: "-30d", "+7d", "-4w", "-3m", "-1y"
    - Keywords: "today", "now", "yesterday", "tomorrow"

    Args:
        value: The date string to parse
        now: Reference time (defaults to current UTC time)
        use_utc: If True and now is None, use UTC; otherwise use local time

    Returns:
        Resolved datetime

    Raises:
        ValueError: If the date string is invalid
    """
    if now is None:
        now = datetime.now(timezone.utc) if use_utc else datetime.now()

    value_lower = value.lower().strip()

    # Keywords
    if value_lower == "now":
        return now
    if value_lower == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if value_lower == "yesterday":
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    if value_lower == "tomorrow":
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    # Relative date pattern
    match = RELATIVE_DATE_PATTERN.match(value_lower)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)

        if unit == "d":
            return now + timedelta(days=amount)
        elif unit == "w":
            return now + timedelta(weeks=amount)
        elif unit == "m":
            # Approximate month as 30 days
            return now + timedelta(days=amount * 30)
        elif unit == "y":
            # Approximate year as 365 days
            return now + timedelta(days=amount * 365)

    raise ValueError(f"Invalid relative date: {value}")


def parse_date_value(value: str) -> datetime | None:
    """Try to parse a value as a date.

    Attempts to parse as:
    1. Relative date (-30d, today, etc.)
    2. ISO date string

    Args:
        value: The value to parse

    Returns:
        datetime if parseable, None otherwise
    """
    # Skip if not a string
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    # Try relative date
    try:
        return parse_relative_date(value)
    except ValueError:
        pass

    # Try ISO date
    if ISO_DATE_PATTERN.match(value):
        try:
            # Handle with and without time component
            if "T" in value:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                return datetime.fromisoformat(value)
        except ValueError:
            pass

    return None


def days_since(date: datetime, *, now: datetime | None = None) -> int:
    """Calculate days since a date.

    Args:
        date: The date to calculate from
        now: Reference time (defaults to current UTC)

    Returns:
        Number of days since the date (positive if in past)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Make both timezone-aware or both naive for comparison
    if date.tzinfo is None and now.tzinfo is not None:
        date = date.replace(tzinfo=timezone.utc)
    elif date.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta = now - date
    return delta.days


def days_until(date: datetime, *, now: datetime | None = None) -> int:
    """Calculate days until a date.

    Args:
        date: The target date
        now: Reference time (defaults to current UTC)

    Returns:
        Number of days until the date (positive if in future)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Make both timezone-aware or both naive for comparison
    if date.tzinfo is None and now.tzinfo is not None:
        date = date.replace(tzinfo=timezone.utc)
    elif date.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta = date - now
    return delta.days


def is_relative_date(value: str) -> bool:
    """Check if a value looks like a relative date.

    Args:
        value: The value to check

    Returns:
        True if it looks like a relative date string
    """
    if not isinstance(value, str):
        return False

    value_lower = value.lower().strip()

    # Keywords
    if value_lower in ("now", "today", "yesterday", "tomorrow"):
        return True

    # Relative pattern
    return bool(RELATIVE_DATE_PATTERN.match(value_lower))
