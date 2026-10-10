"""Shared helpers for the org-wide V2 reads: interactions, merge history, relationships."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from ..models.pagination import PaginatedResponse, PaginationInfoWithTotal
from ._v2_filters import v2_filter_datetime

T = TypeVar("T", bound=BaseModel)

# Relationships are beta on 2024-01-01 and GA from 2026-07-15 (same schema).
RELATIONSHIPS_MIN_API_VERSION = "2026-07-15"
MERGE_STATUSES = ("in-progress", "success", "failed")


def page_of(model: type[T], data: dict[str, Any]) -> PaginatedResponse[T]:
    return PaginatedResponse[model](  # type: ignore[valid-type]
        data=[model.model_validate(item) for item in data.get("data", [])],
        pagination=PaginationInfoWithTotal.model_validate(data.get("pagination", {})),
    )


def check_cursor_alone(cursor: str | None, *others: Any) -> None:
    if cursor is not None and any(o not in (None, False) for o in others):
        raise ValueError(
            "Cannot combine 'cursor' with other parameters; cursor encodes all query context."
        )


def check_limit(limit: int | None) -> None:
    if limit is not None and not 1 <= limit <= 100:
        raise ValueError("'limit' must be between 1 and 100")


def interaction_params(
    time_field: str,
    *,
    after: datetime | None,
    before: datetime | None,
    created_after: datetime | None,
    updated_after: datetime | None,
    limit: int | None,
) -> dict[str, Any]:
    """Filters for the org-wide interaction lists (whole seconds, rounded outward)."""
    check_limit(limit)
    clauses = []
    if after is not None:
        clauses.append(f"{time_field}>={v2_filter_datetime(after, round_up=False)}")
    if before is not None:
        clauses.append(f"{time_field}<{v2_filter_datetime(before, round_up=True)}")
    if created_after is not None:
        clauses.append(f"createdAt>={v2_filter_datetime(created_after, round_up=False)}")
    if updated_after is not None:
        clauses.append(f"updatedAt>={v2_filter_datetime(updated_after, round_up=False)}")
    params: dict[str, Any] = {}
    if clauses:
        params["filter"] = " & ".join(clauses)
    if limit is not None:
        params["limit"] = limit
    return params


def merge_params(*, status: str | None, task_id: str | None, limit: int | None) -> dict[str, Any]:
    check_limit(limit)
    if status is not None and status not in MERGE_STATUSES:
        raise ValueError(f"'status' must be one of: {', '.join(MERGE_STATUSES)}")
    clauses = []
    if status is not None:
        clauses.append(f"status={status}")
    if task_id is not None:
        # Accept a task URL as well as its id
        clauses.append(f"taskId={task_id.rstrip('/').rsplit('/', 1)[-1]}")
    params: dict[str, Any] = {}
    if clauses:
        params["filter"] = " & ".join(clauses)
    if limit is not None:
        params["limit"] = limit
    return params


def relationship_params(
    *, min_score: float | None, order: str, limit: int | None, total_count: bool
) -> dict[str, Any]:
    check_limit(limit)
    if order not in ("asc", "desc"):
        raise ValueError("'order' must be 'asc' or 'desc'")
    params: dict[str, Any] = {}
    if min_score is not None:
        if not 0.0 <= min_score <= 1.0:
            raise ValueError("'min_score' must be between 0.0 and 1.0")
        params["filter"] = (
            f"interactionScore>={format(min_score, 'f').rstrip('0').rstrip('.') or '0'}"
        )
    if order == "asc":  # the API's default is descending
        params["orderBy"] = "interactionScore"
    if limit is not None:
        params["limit"] = limit
    if total_count:
        params["totalCount"] = "true"
    return params


NOTE_INCLUDES = ("companiesPreview", "personsPreview", "opportunitiesPreview", "repliesCount")
# Company notes are beta on 2024-01-01 and GA from 2026-07-15 (same schema).
COMPANY_NOTES_MIN_API_VERSION = "2026-07-15"


def note_params(
    *,
    creator_id: int | None,
    created_after: datetime | None,
    created_before: datetime | None,
    updated_after: datetime | None,
    limit: int | None,
    total_count: bool,
    includes: Any = None,
) -> list[tuple[str, Any]]:
    """Query for the V2 note lists (filters shared by /notes, replies and entity notes)."""
    check_limit(limit)
    clauses = []
    if creator_id is not None:
        clauses.append(f"creator.id={int(creator_id)}")
    if created_after is not None:
        clauses.append(f"createdAt>={v2_filter_datetime(created_after, round_up=False)}")
    if created_before is not None:
        clauses.append(f"createdAt<{v2_filter_datetime(created_before, round_up=True)}")
    if updated_after is not None:
        clauses.append(f"updatedAt>={v2_filter_datetime(updated_after, round_up=False)}")
    params: list[tuple[str, Any]] = []
    if clauses:
        params.append(("filter", " & ".join(clauses)))
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    params.extend(("includes", value) for value in note_includes(includes))
    return params


def note_includes(includes: Any) -> list[str]:
    """``True`` for all four extras, or a list of them; checked against the API's names."""
    if not includes:
        return []
    values = list(NOTE_INCLUDES) if includes is True else list(includes)
    unknown = [v for v in values if v not in NOTE_INCLUDES]
    if unknown:
        raise ValueError(f"Unknown includes {unknown}; allowed: {', '.join(NOTE_INCLUDES)}")
    return values
