"""Shared query/cache-key building for the V2 ``*/fields`` endpoints.

``GET /v2/companies/fields``, ``/v2/persons/fields`` and ``/v2/lists/{listId}/fields`` take the
same parameters:

- ``filter``: a filter string on the field name. ``name="Location"`` matches the name exactly,
  ``name=~Loc`` (or ``name=~"Loc"``) matches a substring; both are case-sensitive. ``|`` combines
  clauses (``name="Location" | name="Industry"``). ``name`` is the only filterable property; any
  other expression is rejected with 400.
- ``includes``: repeatable (``includes=filterability&includes=sortability``); adds the
  ``filterability`` / ``sortability`` objects to each field.

They take no ``fieldTypes`` (the API drops it silently); filter on ``FieldMetadata.type``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def build_fields_query(
    base_cache_key: str,
    *,
    filter: str | None,
    includes: Sequence[str] | str | None,
) -> tuple[dict[str, Any], str]:
    """Return ``(params, cache_key)`` for a fields request.

    ``base_cache_key`` is the key used when neither ``filter`` nor ``includes`` is given (kept
    unchanged so existing cache entries and callers that seed the cache keep working); any
    other parameter set gets its own suffix, with ``includes`` order-insensitive.
    """
    params: dict[str, Any] = {}
    cache_key = base_cache_key
    if filter is not None:
        params["filter"] = filter
        cache_key += f"|filter={filter}"

    if includes is not None:
        values = [includes] if isinstance(includes, str) else list(includes)
        unique = sorted({str(v) for v in values})
        if unique:
            params["includes"] = unique
            cache_key += f"|includes={','.join(unique)}"

    return params, cache_key
