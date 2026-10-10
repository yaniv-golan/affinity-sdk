"""Helpers for V2 ``filter=`` query strings."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models.types import _normalize_to_utc


def v2_filter_datetime(value: datetime, *, round_up: bool) -> str:
    """Whole-second UTC ``Z`` timestamp for a V2 filter (fractions are rejected with 400).

    Rounded outward - down for a lower bound, up for an upper one - so nothing inside the
    requested range is lost. A naive datetime is taken as UTC.
    """
    utc = _normalize_to_utc(value)
    if utc.microsecond:
        utc = utc.replace(microsecond=0) + (timedelta(seconds=1) if round_up else timedelta())
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")
