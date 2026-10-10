"""V2 dropdown-option helpers shared by the list, company and person services."""

from __future__ import annotations

from typing import Any, Literal

from ..models.entities import DropdownOption
from ..models.types import AnyFieldId, ListId

# Option writes are GA from 2026-07-15 (beta before; status options exist only from then).
# Reads stay unversioned: they work on older keys and dropdown writes depend on them.
DROPDOWN_OPTION_WRITES_MIN_API_VERSION = "2026-07-15"

DropdownOptionType = Literal["dropdown", "ranked-dropdown", "status-dropdown"]
DropdownOptionColorName = Literal["white", "gray", "blue", "green", "purple", "orange", "red"]
DropdownStatusCategory = Literal["open", "won", "lost", "on-hold"]

_TYPES = ("dropdown", "ranked-dropdown", "status-dropdown")
_COLORS = ("white", "gray", "blue", "green", "purple", "orange", "red")
_STATUS_CATEGORIES = ("open", "won", "lost", "on-hold")


def options_path(field_id: AnyFieldId, *, list_id: ListId | int | None, entity: str | None) -> str:
    field = str(field_id)
    if list_id is not None:
        return f"/lists/{int(list_id)}/fields/{field}/dropdown-options"
    return f"/{entity}/fields/{field}/dropdown-options"


def option_path(list_id: ListId | int, field_id: AnyFieldId, option_id: int) -> str:
    return f"{options_path(field_id, list_id=list_id, entity=None)}/{int(option_id)}"


def parse_options(data: dict[str, Any]) -> list[DropdownOption]:
    return [DropdownOption.model_validate(item) for item in data.get("data", [])]


def _check_common(
    *,
    text: str | None,
    rank: int | None,
    color: str | None,
    status_category: str | None,
    win_rate: int | None,
) -> None:
    if text is not None and not 1 <= len(text) <= 255:
        raise ValueError("'text' must be 1-255 characters")
    if rank is not None and rank < 0:
        raise ValueError("'rank' must be >= 0")
    if color is not None and color not in _COLORS:
        raise ValueError(f"'color' must be one of: {', '.join(_COLORS)}")
    if status_category is not None and status_category not in _STATUS_CATEGORIES:
        raise ValueError(f"'status_category' must be one of: {', '.join(_STATUS_CATEGORIES)}")
    if win_rate is not None and not 0 <= win_rate <= 100:
        raise ValueError("'win_rate' must be between 0 and 100")


def create_body(
    *,
    option_type: str,
    text: str,
    rank: int | None,
    color: str | None,
    status_category: str | None,
    win_rate: int | None,
) -> dict[str, Any]:
    """Request body for creating an option; only the properties its type allows."""
    if option_type not in _TYPES:
        raise ValueError(f"'option_type' must be one of: {', '.join(_TYPES)}")
    _check_common(
        text=text, rank=rank, color=color, status_category=status_category, win_rate=win_rate
    )
    if option_type != "dropdown" and (rank is None or color is None):
        raise ValueError(f"A '{option_type}' option needs 'rank' and 'color'")
    if option_type == "status-dropdown" and status_category is None:
        raise ValueError("A 'status-dropdown' option needs 'status_category'")
    if option_type == "dropdown" and any(v is not None for v in (rank, color)):
        raise ValueError("A 'dropdown' option takes only text (no rank or color)")
    if option_type != "status-dropdown" and any(v is not None for v in (status_category, win_rate)):
        raise ValueError("'status_category' and 'win_rate' apply to 'status-dropdown' options only")
    body: dict[str, Any] = {"type": option_type, "text": text}
    for key, value in (
        ("rank", rank),
        ("color", color),
        ("statusCategory", status_category),
        ("winRate", win_rate),
    ):
        if value is not None:
            body[key] = value
    return body


def update_body(
    *,
    text: str | None,
    rank: int | None,
    color: str | None,
    status_category: str | None,
    win_rate: int | None,
) -> dict[str, Any]:
    """Request body for updating an option (at least one property)."""
    _check_common(
        text=text, rank=rank, color=color, status_category=status_category, win_rate=win_rate
    )
    body = {
        key: value
        for key, value in (
            ("text", text),
            ("rank", rank),
            ("color", color),
            ("statusCategory", status_category),
            ("winRate", win_rate),
        )
        if value is not None
    }
    if not body:
        raise ValueError("Nothing to update: pass text, rank, color, status_category or win_rate")
    return body
