"""
Search criteria for the V2 search endpoints (beta): ``companies.find``, ``persons.find`` and
``lists.entries(list_id).find``.

Affinity applies these filters and sorts on its side, so only matching records are downloaded.

Example:
    from datetime import date
    from affinity.criteria import Condition, Sort, all_of, any_of

    page = client.companies.find(
        all_of(
            Condition("field-498220", "is-any-of", [3, 4]),  # dropdown option ids
            any_of(
                Condition("affinity-data-last-funding-date", "is-after", date(2025, 1, 1)),
                Condition("dealroom-twitter-followers-last-month", "is-greater-than", 1000),
            ),
        ),
        sort=[Sort("dealroom-twitter-followers-last-month", "desc")],
    )

Each condition needs the field's filter value type. When it isn't given, it's read from the
fields metadata (one cached request), which also checks the operator against the field.

This is separate from :mod:`affinity.filters` (the text filter language that filters records
after they're downloaded).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .models.entities import FieldMetadata

__all__ = [
    "Condition",
    "FilterGroup",
    "SearchCriteria",
    "Sort",
    "all_of",
    "any_of",
    "filter_value_type",
]

MAX_GROUP_ITEMS = 50
MAX_VALUES = 100
MAX_SORTS = 5
MIN_TERM_LENGTH = 3

# Filter value types the search endpoints accept, and the operators each takes.
_REF_OPS = ("is-any-of", "is-none-of")
_MULTI_OPS = ("has-any-of", "has-all-of", "has-none-of")
_EMPTY_OPS = ("is-empty", "is-not-empty")
_DATE_ONE = ("is-on", "is-not-on", "is-before", "is-after", "is-on-or-before", "is-on-or-after")
_DATE_WINDOW = (
    "is-within-the-last",
    "is-not-within-the-last",
    "is-within-the-next",
    "is-not-within-the-next",
)
_DATE_DURATION = (
    "is-exactly-relative",
    "is-not-exactly-relative",
    "is-less-than-relative",
    "is-less-than-or-equal-to-relative",
    "is-more-than-relative",
    "is-more-than-or-equal-to-relative",
)
_NUMBER_ONE = (
    "is-equal-to",
    "is-not-equal-to",
    "is-greater-than",
    "is-greater-than-or-equal-to",
    "is-less-than",
    "is-less-than-or-equal-to",
)
OPERATORS: dict[str, tuple[str, ...]] = {
    "company": _REF_OPS + _EMPTY_OPS,
    "company-multi": _MULTI_OPS + _EMPTY_OPS,
    "person": _REF_OPS + _EMPTY_OPS,
    "person-multi": _MULTI_OPS + _EMPTY_OPS,
    "list-multi": _MULTI_OPS + _EMPTY_OPS,
    "dropdown": _REF_OPS + _EMPTY_OPS,
    "dropdown-multi": _MULTI_OPS + _EMPTY_OPS,
    "ranked-dropdown": _REF_OPS + _EMPTY_OPS,
    "location": _REF_OPS + _EMPTY_OPS,
    "location-multi": _MULTI_OPS + _EMPTY_OPS,
    "filterable-text": _REF_OPS + _EMPTY_OPS,
    "filterable-text-multi": _MULTI_OPS + _EMPTY_OPS,
    "text": ("contains", "does-not-contain", "starts-with", "ends-with", *_EMPTY_OPS),
    "number": (*_NUMBER_ONE, "is-between", *_EMPTY_OPS),
    "date": (
        *_DATE_ONE,
        "is-between",
        "is-between-relative",
        *_DATE_WINDOW,
        *_DATE_DURATION,
        *_EMPTY_OPS,
    ),
}
# Field metadata value type -> filter value type (where they differ).
_FIELD_TO_FILTER_TYPE = {"datetime": "date"}
_ID_REF_TYPES = {"company", "company-multi", "person", "person-multi", "list-multi"}
_DROPDOWN_TYPES = {"dropdown", "dropdown-multi", "ranked-dropdown"}
_LOCATION_TYPES = {"location", "location-multi"}
_RELATIVE_UNITS = ("minute", "hour", "day", "week", "month", "quarter", "year")
_UNSET: Any = object()


def filter_value_type(field_value_type: str) -> str | None:
    """The search filter value type for a field's metadata value type, or ``None`` when that
    type has no value filter (e.g. ``interaction``: filter it by an attribute instead)."""
    vt = _FIELD_TO_FILTER_TYPE.get(field_value_type, field_value_type)
    return vt if vt in OPERATORS else None


@dataclass(frozen=True)
class Condition:
    """One filter: ``field_id``, an ``operator`` and (except for is-empty / is-not-empty) a
    ``value``.

    Values are written plainly and wrapped for the API:

    - company / person / list fields: ids (``7`` or ``[7, 8]``)
    - dropdown / ranked-dropdown fields: option ids
    - location fields: dicts with ``country``, ``state``, ``city``, ``continent``,
      ``streetAddress``
    - filterable-text fields: strings; text fields: one string
    - numbers: a number, or ``(low, high)`` for ``is-between``
    - dates: :class:`~datetime.date` (or ``YYYY-MM-DD``), ``(start, end)`` for ``is-between``;
      relative windows as ``(amount, unit)``, e.g. ``(6, "month")``; ``is-between-relative`` as
      ``(start, end, unit)``

    ``value_type`` is the filter type (``date`` for datetime fields); omitted, it's read from
    the fields metadata. ``attribute_id`` is required for fields filtered by an attribute
    (relationship-intelligence fields such as ``date-of-activity``).
    """

    field_id: str
    operator: str
    value: Any = _UNSET
    value_type: str | None = None
    attribute_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "field_id", str(self.field_id))


@dataclass(frozen=True)
class FilterGroup:
    """Conditions or groups combined with ``and`` / ``or`` (at most 50 items)."""

    operator: Literal["and", "or"]
    items: tuple[Condition | FilterGroup, ...]

    def __post_init__(self) -> None:
        if self.operator not in ("and", "or"):
            raise ValueError("FilterGroup operator must be 'and' or 'or'")
        if len(self.items) > MAX_GROUP_ITEMS:
            raise ValueError(f"A filter group takes at most {MAX_GROUP_ITEMS} items")
        for item in self.items:
            if not isinstance(item, (Condition, FilterGroup)):
                raise TypeError(f"Expected a Condition or a group, got {type(item).__name__}")


def all_of(*items: Condition | FilterGroup) -> FilterGroup:
    """Every item must match."""
    return FilterGroup("and", tuple(items))


def any_of(*items: Condition | FilterGroup) -> FilterGroup:
    """At least one item must match."""
    return FilterGroup("or", tuple(items))


@dataclass(frozen=True)
class Sort:
    """Sort by a field (at most 5 sorts, applied in order). Empty values come last in both
    directions; ties are ordered by id."""

    field_id: str
    direction: Literal["asc", "desc"] = "asc"
    attribute_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "field_id", str(self.field_id))
        if self.direction not in ("asc", "desc"):
            raise ValueError("Sort direction must be 'asc' or 'desc'")

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"fieldId": self.field_id, "direction": self.direction}
        if self.attribute_id is not None:
            body["attributeId"] = self.attribute_id
        return body


@dataclass(frozen=True)
class SearchCriteria:
    """What to search for: filters, sorts and a keyword (``term``, 3+ characters, matched
    against names, domains and emails, plus ``term_fields``)."""

    where: Condition | FilterGroup | None = None
    sort: tuple[Sort, ...] = field(default_factory=tuple)
    term: str | None = None
    term_fields: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sort", tuple(self.sort))
        object.__setattr__(self, "term_fields", tuple(str(f) for f in self.term_fields))
        if len(self.sort) > MAX_SORTS:
            raise ValueError(f"At most {MAX_SORTS} sorts")
        if self.term is not None and len(self.term.strip()) < MIN_TERM_LENGTH:
            raise ValueError(f"'term' must be at least {MIN_TERM_LENGTH} characters")
        if self.term_fields and self.term is None:
            raise ValueError("'term_fields' needs a 'term'")
        if len(self.term_fields) > MAX_VALUES:
            raise ValueError(f"At most {MAX_VALUES} 'term_fields'")

    def conditions(self) -> list[Condition]:
        """Every condition in ``where``."""
        return list(_walk(self.where)) if self.where is not None else []

    def needs_fields(self) -> bool:
        """True when a condition has no ``value_type`` (the fields metadata is needed)."""
        return any(c.value_type is None for c in self.conditions())

    def to_body(self, fields: Mapping[str, FieldMetadata] | None = None) -> dict[str, Any]:
        """The request body. ``fields`` (by field id, with ``filterability``) supplies missing
        value types and checks each operator against the field."""
        body: dict[str, Any] = {}
        if self.where is not None:
            group = self.where if isinstance(self.where, FilterGroup) else all_of(self.where)
            body["filters"] = _group_body(group, fields)
        if self.sort:
            body["sorts"] = [s.to_body() for s in self.sort]
        if self.term is not None:
            term: dict[str, Any] = {"term": self.term}
            if self.term_fields:
                term["fieldIds"] = list(self.term_fields)
            body["search"] = term
        return body


def _walk(item: Condition | FilterGroup) -> Iterable[Condition]:
    if isinstance(item, Condition):
        yield item
        return
    for child in item.items:
        yield from _walk(child)


def _group_body(group: FilterGroup, fields: Mapping[str, FieldMetadata] | None) -> dict[str, Any]:
    return {
        "operator": group.operator,
        "filters": [
            _group_body(i, fields) if isinstance(i, FilterGroup) else _condition_body(i, fields)
            for i in group.items
        ],
    }


def _field_operators(meta: FieldMetadata, attribute_id: str | None) -> list[str] | None:
    """The operators the field (or its attribute) allows, from ``filterability``; ``None``
    when filterability wasn't requested."""
    if "filterability" not in meta.model_fields_set:
        return None
    filterability = meta.filterability
    if filterability is None:
        raise ValueError(f"Field {meta.id} ({meta.name}) can't be filtered in a search")
    if filterability.get("filterableFieldType") == "attribute-on-field":
        attributes = {a.get("id"): a for a in filterability.get("attributes") or []}
        if attribute_id is None:
            raise ValueError(
                f"Field {meta.id} ({meta.name}) is filtered by an attribute; pass attribute_id "
                f"(one of: {', '.join(sorted(str(a) for a in attributes))})"
            )
        if attribute_id not in attributes:
            raise ValueError(f"Field {meta.id} ({meta.name}) has no attribute {attribute_id!r}")
        return [str(o.get("id")) for o in attributes[attribute_id].get("operators") or []]
    if attribute_id is not None:
        raise ValueError(f"Field {meta.id} ({meta.name}) takes no attribute_id")
    return [str(o.get("id")) for o in filterability.get("operators") or []]


def _condition_body(c: Condition, fields: Mapping[str, FieldMetadata] | None) -> dict[str, Any]:
    meta = fields.get(c.field_id) if fields is not None else None
    if fields is not None and meta is None and c.value_type is None:
        raise ValueError(f"Unknown field {c.field_id!r}")
    if meta is not None:
        allowed = _field_operators(meta, c.attribute_id)
        if allowed is not None and c.operator not in allowed:
            raise ValueError(
                f"Operator {c.operator!r} isn't allowed on field {c.field_id} ({meta.name}); "
                f"allowed: {', '.join(allowed)}"
            )
    value_type = c.value_type
    if value_type is None:
        if meta is None:
            raise ValueError(
                f"Condition on {c.field_id!r} needs value_type (or the fields metadata)"
            )
        raw = getattr(meta.value_type, "value", meta.value_type)
        value_type = filter_value_type(str(raw))
        if value_type is None:
            raise ValueError(f"Field {c.field_id} ({meta.name}) has no value filter ({raw})")
    if value_type not in OPERATORS:
        raise ValueError(f"Unknown filter value type {value_type!r}")
    if c.operator not in OPERATORS[value_type]:
        raise ValueError(
            f"Operator {c.operator!r} isn't valid for {value_type} filters; "
            f"valid: {', '.join(OPERATORS[value_type])}"
        )
    body: dict[str, Any] = {"valueType": value_type, "fieldId": c.field_id, "operator": c.operator}
    if c.attribute_id is not None:
        body["attributeId"] = c.attribute_id
    if c.operator in _EMPTY_OPS:
        if c.value is not _UNSET and c.value is not None:
            raise ValueError(f"Operator {c.operator!r} takes no value")
        return body
    if c.value is _UNSET or c.value is None:
        raise ValueError(f"Operator {c.operator!r} on {c.field_id!r} needs a value")
    body["value"] = _value(value_type, c.operator, c.value)
    return body


def _as_list(value: Any) -> list[Any]:
    items = list(value) if isinstance(value, (list, tuple, set, frozenset)) else [value]
    if not items:
        raise ValueError("Give at least one value")
    if len(items) > MAX_VALUES:
        raise ValueError(f"At most {MAX_VALUES} values per condition")
    return items


def _ref_id(item: Any, key: str) -> dict[str, int]:
    raw = item.get(key, item.get("id")) if isinstance(item, Mapping) else getattr(item, "id", item)
    if isinstance(raw, bool) or raw is None:
        raise ValueError(f"Expected an id, got {item!r}")
    return {key: int(raw)}


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        raise ValueError(
            "Date filters compare whole days: pass a date (datetime.date or 'YYYY-MM-DD'), "
            "not a datetime"
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return date.fromisoformat(value).isoformat()
    raise ValueError(f"Expected a date, got {value!r}")


def _unit(unit: Any) -> str:
    if unit not in _RELATIVE_UNITS:
        raise ValueError(f"Relative date unit must be one of: {', '.join(_RELATIVE_UNITS)}")
    return str(unit)


def _pair(value: Any, what: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{what} needs two values (start, end)")
    return value


def _value(value_type: str, operator: str, value: Any) -> Any:
    if value_type in _ID_REF_TYPES:
        return [_ref_id(i, "id") for i in _as_list(value)]
    if value_type in _DROPDOWN_TYPES:
        return [_ref_id(i, "dropdownOptionId") for i in _as_list(value)]
    if value_type in _LOCATION_TYPES:
        items = _as_list(value)
        for i in items:
            if not isinstance(i, Mapping):
                raise ValueError(f"Location values are dicts (country, city, ...), got {i!r}")
        return [dict(i) for i in items]
    if value_type in ("filterable-text", "filterable-text-multi"):
        return [str(i) for i in _as_list(value)]
    if value_type == "text":
        if not isinstance(value, str):
            raise ValueError(f"Text filters take one string, got {value!r}")
        return value
    if value_type == "number":
        if operator == "is-between":
            low, high = _pair(value, "is-between")
            if float(low) > float(high):
                raise ValueError("is-between needs low <= high")
            return [low, high]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Number filters take a number, got {value!r}")
        return value
    # date
    if operator == "is-between":
        start, end = (_iso_date(v) for v in _pair(value, "is-between"))
        if start > end:
            raise ValueError("is-between needs start <= end")
        return [start, end]
    if operator in _DATE_ONE:
        return _iso_date(value)
    if operator == "is-between-relative":
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError("is-between-relative takes (start, end, unit), e.g. (7, 30, 'day')")
        a, b, unit = value
        if not 1 <= int(a) <= int(b):
            raise ValueError("is-between-relative needs 1 <= start <= end")
        return {"amount": [int(a), int(b)], "unit": _unit(unit)}
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{operator} takes (amount, unit), e.g. (6, 'month')")
    amount, unit = value
    if operator in _DATE_WINDOW:
        if int(amount) < 1:
            raise ValueError(f"{operator} needs an amount of 1 or more")
        return {"amount": [int(amount)], "unit": _unit(unit)}
    if int(amount) < 0:
        raise ValueError(f"{operator} needs an amount of 0 or more")
    return {"amount": int(amount), "unit": _unit(unit)}
