"""Filtering list entries, companies and persons through Affinity's search, shared by the SDK
(``lists.entries(id).list/pages/all/iter(filter=...)``) and the CLI (``list export --filter``,
``company ls`` / ``person ls --filter``).

- :func:`plan_filter` splits a text filter (or :class:`~affinity.filters.FilterExpression`)
  into the search body Affinity can answer and the rest, which the caller re-checks locally.
- Next-page cursors carry the filter, so a cursor alone resumes a filtered read:
  ``xsearch2:`` + base64 JSON ``{"k": "search"|"local", "u": next page URL, "b": search body,
  "f": filter tree}``. ``k="search"`` re-sends ``b`` to ``u`` (a search's next page needs its
  body); ``k="local"`` reads ``u`` (a plain page) and filters it locally. ``xsearch1:`` cursors
  (filter as text, always a search) from 2.0.2 still decode.
- :class:`SearchCache` keeps fields, dropdown options and refused searches per list for a while.

Private module: not part of the public SDK API.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from ..criteria import SearchCriteria
from ..filters import (
    AndExpression,
    FieldComparison,
    FilterExpression,
    NotExpression,
    OrExpression,
    RawFilter,
    RawToken,
    parse,
)
from ..models.entities import DropdownOption, FieldMetadata
from .filter_text import ROW_KEYS, where_from_filter
from .pushdown import fields_index, split_where
from .where import WhereClause

CURSOR_V2 = "xsearch2:"
CURSOR_V1 = "xsearch1:"
CURSOR_PREFIXES = (CURSOR_V2, CURSOR_V1)
CACHE_TTL_SECONDS = 300.0

CursorKind = Literal["search", "local"]


# --- plan ---------------------------------------------------------------------------------------


@dataclass
class FilterPlan:
    """What Affinity's search applies of a filter (``body``; ``None``: nothing) and the notes."""

    body: dict[str, Any] | None
    field_ids: list[str] = field(default_factory=list)  # fields the filter tests
    pushed: list[str] = field(default_factory=list)
    local: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every condition went to the server."""
        return self.body is not None and not self.local


def reject_raw_filters(expr: FilterExpression) -> None:
    """``RawFilter`` can't be evaluated locally; refuse it anywhere in the tree."""
    if isinstance(expr, RawFilter):
        raise ValueError(
            "A raw filter string can't filter list entries, companies or persons: "
            "use a parsed filter (affinity.filters.parse or the F builder)"
        )
    for child in _children(expr):
        reject_raw_filters(child)


def _children(expr: FilterExpression) -> list[FilterExpression]:
    if isinstance(expr, (AndExpression, OrExpression)):
        return [expr.left, expr.right]
    if isinstance(expr, NotExpression):
        return [expr.expr]
    return []


def _field_paths(where: WhereClause) -> set[str]:
    paths: set[str] = set()
    if where.path and where.path.startswith("fields."):
        paths.add(where.path.removeprefix("fields."))
    for child in [*(where.and_ or []), *(where.or_ or [])]:
        paths |= _field_paths(child)
    if where.not_ is not None:
        paths |= _field_paths(where.not_)
    return paths


def referenced_field_names(expr: FilterExpression, row_keys: frozenset[str] = ROW_KEYS) -> set[str]:
    """The field names (or ids) a filter tests; record attributes (``row_keys``) excluded."""
    return _field_paths(where_from_filter(expr, row_keys))


def plan_filter(
    expr: FilterExpression,
    fields: Sequence[FieldMetadata],
    options_for: Callable[[FieldMetadata], Sequence[DropdownOption]],
    *,
    row_keys: frozenset[str] = ROW_KEYS,
    enriched: str = "or-empty",
) -> FilterPlan:
    """Split ``expr`` for these fields (pure: ``options_for`` supplies dropdown options)."""
    where = where_from_filter(expr, row_keys)
    split = split_where(where, fields, options_for, enriched=enriched)
    index = fields_index(fields)
    ids: list[str] = []
    for name in sorted(_field_paths(where)):
        meta = index.get(name) or index.get(name.lower())
        if meta is not None and str(meta.id) not in ids:
            ids.append(str(meta.id))
    body = (
        SearchCriteria(where=split.where, term=split.term, term_fields=split.term_fields).to_body()
        if split.server_side
        else None
    )
    return FilterPlan(
        body=body,
        field_ids=ids,
        pushed=split.pushed,
        local=split.local,
        caveats=split.caveats,
        errors=split.errors,
    )


def dropdown_fields(expr: FilterExpression, fields: Sequence[FieldMetadata]) -> list[FieldMetadata]:
    """The dropdown fields a filter tests (their options are needed to plan it)."""
    index = fields_index(fields)
    out: list[FieldMetadata] = []
    for name in sorted(referenced_field_names(expr)):
        meta = index.get(name) or index.get(name.lower())
        value_type = str(getattr(meta.value_type, "value", meta.value_type)) if meta else ""
        if meta is not None and value_type in ("dropdown", "ranked-dropdown", "dropdown-multi"):
            out.append(meta)
    return out


# --- filter tree (cursor payload) ---------------------------------------------------------------


def _value_to_json(value: Any) -> Any:
    if isinstance(value, RawToken):
        return {"t": "raw", "v": value.token}
    if isinstance(value, datetime):
        return {"t": "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {"t": "date", "v": value.isoformat()}
    if isinstance(value, (list, tuple)):
        return {"t": "list", "v": [_value_to_json(v) for v in value]}
    if value is None or isinstance(value, (str, bool, int, float)):
        return {"t": "value", "v": value}
    raise ValueError(f"Can't put a {type(value).__name__} filter value in a cursor")


def _value_from_json(obj: Any) -> Any:
    if not isinstance(obj, dict) or set(obj) != {"t", "v"}:
        raise ValueError("bad value")
    tag, v = obj["t"], obj["v"]
    if tag == "raw":
        if v != "*":
            raise ValueError("bad raw token")
        return RawToken("*")
    if tag == "datetime" and isinstance(v, str):
        return datetime.fromisoformat(v)
    if tag == "date" and isinstance(v, str):
        return date.fromisoformat(v)
    if tag == "list" and isinstance(v, list):
        return [_value_from_json(x) for x in v]
    if tag == "value" and (v is None or isinstance(v, (str, bool, int, float))):
        return v
    raise ValueError("bad value")


def filter_to_json(expr: FilterExpression) -> Any:
    """A filter expression as JSON data (round-trips, unlike ``to_string``)."""
    if isinstance(expr, FieldComparison):
        return {
            "cmp": [expr.field_name, expr.operator, _value_to_json(expr.value)],
        }
    if isinstance(expr, AndExpression):
        return {"and": [filter_to_json(expr.left), filter_to_json(expr.right)]}
    if isinstance(expr, OrExpression):
        return {"or": [filter_to_json(expr.left), filter_to_json(expr.right)]}
    if isinstance(expr, NotExpression):
        return {"not": filter_to_json(expr.expr)}
    raise ValueError(f"Can't put a {type(expr).__name__} in a cursor")


def filter_from_json(obj: Any, depth: int = 0) -> FilterExpression:
    """The filter expression :func:`filter_to_json` made; ``ValueError`` for anything else."""
    if depth > 64 or not isinstance(obj, dict) or len(obj) != 1:
        raise ValueError("bad filter")
    ((key, value),) = obj.items()
    if key == "cmp" and isinstance(value, list) and len(value) == 3:
        name, operator, raw = value
        if not isinstance(name, str) or not isinstance(operator, str):
            raise ValueError("bad comparison")
        return FieldComparison(name, operator, _value_from_json(raw))
    if key in ("and", "or") and isinstance(value, list) and len(value) == 2:
        left, right = (filter_from_json(v, depth + 1) for v in value)
        return AndExpression(left, right) if key == "and" else OrExpression(left, right)
    if key == "not":
        return NotExpression(filter_from_json(value, depth + 1))
    raise ValueError("bad filter")


# --- cursors ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchCursor:
    """A decoded filtered-read cursor."""

    kind: CursorKind
    url: str
    body: dict[str, Any] | None
    filter: FilterExpression


def encode_cursor(
    kind: CursorKind, url: str, body: dict[str, Any] | None, expr: FilterExpression
) -> str:
    payload = {"k": kind, "u": url, "b": body, "f": filter_to_json(expr)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return CURSOR_V2 + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def is_filter_cursor(cursor: str | None) -> bool:
    return cursor is not None and cursor.startswith(CURSOR_PREFIXES)


def decode_cursor(cursor: str, *, path_suffixes: Iterable[str]) -> SearchCursor | None:
    """The filtered-read cursor in ``cursor`` (``None`` when it's a plain page URL). The
    cursor is user input: anything malformed, or a cursor for another kind of record (its URL
    path must end with one of ``path_suffixes``), raises ``ValueError``."""
    if not is_filter_cursor(cursor):
        return None
    prefix = CURSOR_V2 if cursor.startswith(CURSOR_V2) else CURSOR_V1
    encoded = cursor.removeprefix(prefix)
    try:
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, TypeError) as e:
        raise ValueError("This cursor is damaged: start the read again without it") from e
    if not isinstance(data, dict) or not isinstance(data.get("u"), str):
        raise ValueError("This cursor is damaged: start the read again without it")
    url = data["u"]
    if not any(urlparse(url).path.endswith(s) for s in path_suffixes):
        raise ValueError("This cursor belongs to a different list or kind of record")
    if prefix == CURSOR_V1:
        body, text = data.get("b"), data.get("f")
        if not isinstance(body, dict) or not isinstance(text, str):
            raise ValueError("This cursor is damaged: start the read again without it")
        return SearchCursor("search", url, body, parse(text))
    kind, body = data.get("k"), data.get("b")
    if kind not in ("search", "local") or (kind == "search" and not isinstance(body, dict)):
        raise ValueError("This cursor is damaged: start the read again without it")
    return SearchCursor(
        kind, url, body if kind == "search" else None, filter_from_json(data.get("f"))
    )


# --- cache --------------------------------------------------------------------------------------


class SearchCache:
    """Per-client cache for planning searches: a list's fields (with filterability), dropdown
    options, and lists whose search was refused (403 / 404 / unsupported). Entries expire
    after ``ttl`` seconds; races only cost a duplicate fetch."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._data: dict[tuple[str, ...], tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[str, ...]) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None or hit[0] < time.monotonic():
                self._data.pop(key, None)
                return None
            return hit[1]

    def put(self, key: tuple[str, ...], value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self._ttl, value)

    def drop_scope(self, scope: str) -> None:
        """Forget everything cached for one list (or entity type), e.g. after a 400."""
        with self._lock:
            for key in [k for k in self._data if k[1:2] == (scope,)]:
                del self._data[key]
