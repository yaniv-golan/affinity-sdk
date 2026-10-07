from __future__ import annotations

from typing import Any, Literal

ListEntryFieldsScope = Literal["list-only", "all"]


def _is_list_type(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    # "hidden" (API 2026-07-15+) is a masked list field on a restricted opportunity.
    return value.strip().lower() in ("list", "hidden")


def filter_list_entry_fields(
    fields: list[Any],
    *,
    scope: ListEntryFieldsScope,
) -> tuple[list[dict[str, Any]], int, int]:
    field_dicts = [f for f in fields if isinstance(f, dict)]
    total_count = len(field_dicts)
    list_only = [f for f in field_dicts if _is_list_type(f.get("type"))]
    list_only_count = len(list_only)

    if scope == "list-only":
        return list_only, list_only_count, total_count
    return field_dicts, list_only_count, total_count


def build_list_entry_field_rows(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in fields:
        rows.append(
            {
                "id": f.get("id"),
                "type": f.get("type"),
                "enrichmentSource": f.get("enrichmentSource"),
                "name": f.get("name"),
                "value": f.get("value"),
            }
        )
    return rows
