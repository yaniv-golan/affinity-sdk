"""Payloads for V2 field writes (``update-fields`` batches and single-field updates).

Shared by list entries, companies and persons: the three endpoints take the same
``{"id", "value": {"type", "data"}}`` items.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from ..models.types import FieldId, FieldValueType

# Company / person field writes (PATCH /v2/{companies,persons}/{id}/fields) are beta on
# 2024-01-01 and generally available from this version on.
FIELD_WRITES_MIN_API_VERSION = "2026-07-15"

_LOCATION_KEYS = ("streetAddress", "city", "state", "country", "continent")


def _location_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """A location in the V2 write shape: camelCase keys, all five present (the API rejects a
    location missing any of them). ``street_address`` is accepted for ``streetAddress``;
    other keys are passed through for the API to reject."""
    out: dict[str, Any] = dict.fromkeys(_LOCATION_KEYS)
    for key, v in value.items():
        out["streetAddress" if key == "street_address" else key] = v
    return out


def _field_value_payload(
    value: Any, value_type: FieldValueType | str | None = None
) -> dict[str, Any]:
    """Build the V2 ``{"type", "data"}`` value for a field write.

    Without an explicit ``value_type``: str -> text, int/float -> number, datetime/date ->
    datetime (ISO 8601), anything else -> text. Multi-value fields (lists) cannot be inferred
    (a list could be persons, companies or dropdown options); pass ``value_type`` for them.
    Locations are completed to the five keys the API requires.
    """
    if value_type is not None:
        type_str = value_type.value if isinstance(value_type, FieldValueType) else value_type
    elif isinstance(value, (str, bool)):  # bool before int: bool is an int subclass
        type_str = "text"
    elif isinstance(value, (int, float)):
        type_str = "number"
    elif isinstance(value, (datetime, date)):
        type_str = "datetime"
    else:
        type_str = "text"
    data: Any
    if type_str == "location" and isinstance(value, Mapping):
        data = _location_payload(value)
    elif type_str == "location-multi" and isinstance(value, (list, tuple)):
        data = [_location_payload(v) if isinstance(v, Mapping) else v for v in value]
    elif isinstance(value, datetime):
        data = value.isoformat()
    elif isinstance(value, date):
        # The API keeps the UTC calendar date of a date-time (stored as midnight Pacific of
        # that date); noon UTC is the given date in UTC and in Pacific time alike.
        data = f"{value.isoformat()}T12:00:00Z"
    else:
        data = value
    return {"type": type_str, "data": data}


# Affinity's V2 API returns and accepts at most this many values per multi-value field, and at
# most this many updates per batch request.
MAX_MULTI_VALUES = 100
MAX_BATCH_UPDATES = 100

# Multi-value types whose V2 write schema declares ``maxItems: 100`` (verified live for company,
# person and location). dropdown-multi declares none, and the API accepts more than 100.
CAPPED_MULTI_VALUE_TYPES = frozenset(
    {"company-multi", "person-multi", "number-multi", "filterable-text-multi", "location-multi"}
)


def _check_multi_value_size(field_id: Any, payload: dict[str, Any]) -> None:
    data = payload.get("data")
    capped = payload.get("type") in CAPPED_MULTI_VALUE_TYPES
    if capped and isinstance(data, list) and len(data) > MAX_MULTI_VALUES:
        raise ValueError(
            f"Field {field_id}: {len(data)} values given; Affinity accepts at most "
            f"{MAX_MULTI_VALUES} values per multi-value field"
        )


def _normalized_field_key(field_id: Any) -> str:
    try:
        return str(FieldId(field_id))
    except (ValueError, TypeError):
        return str(field_id)


def _batch_update_items(
    updates: Mapping[Any, Any],
    value_types: Mapping[Any, FieldValueType | str] | None = None,
) -> list[dict[str, Any]]:
    """Build PATCH ``update-fields`` items.

    A field with an entry in ``value_types`` is sent with that type, which also allows a list
    (multi-value field) or ``None`` (clears the field). Without a type, the type is inferred from
    the value, and lists and ``None`` are refused (they can't be typed by inference).
    """
    if len(updates) > MAX_BATCH_UPDATES:
        raise ValueError(
            f"{len(updates)} updates given; Affinity accepts at most {MAX_BATCH_UPDATES} "
            "updates per batch request, so split them into several calls"
        )
    types = {_normalized_field_key(k): v for k, v in (value_types or {}).items()}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field_id, value in updates.items():
        key = _normalized_field_key(field_id)
        if key in seen:
            # Affinity applies the last of duplicate ids silently; refuse instead.
            raise ValueError(f"Field {key} appears more than once in the updates")
        seen.add(key)
        value_type = types.get(key)
        if value_type is None:
            if isinstance(value, (list, tuple)):
                raise ValueError(
                    f"Field {field_id}: batch_update_fields() cannot infer the type of a "
                    "multi-value field; pass value_types={field_id: ...} or use "
                    "update_field_value(..., value_type=...)"
                )
            if value is None:
                raise ValueError(
                    f"Field {field_id}: clearing a field needs its type; pass "
                    "value_types={field_id: ...}"
                )
        payload = _field_value_payload(
            list(value) if isinstance(value, tuple) else value, value_type
        )
        _check_multi_value_size(field_id, payload)
        items.append({"id": key, "value": payload})
    return items
