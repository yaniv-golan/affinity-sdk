"""Server-side filtering: which part of a WHERE clause Affinity's search can answer.

The local filter always runs afterwards on every record, so the server part only has to be a
**superset**: it may return extra records (the local filter drops them), never fewer. Each
mapping below is chosen with the local semantics in mind (``affinity/compare.py`` and the
normalized record values: dropdowns as option text, dates as ISO strings).

A mapping is used only when it is in :data:`VERIFIED` — confirmed against the live API by
``tools/search_golden.py`` (server count == local count on real data). Unverified mappings stay
local.

This module is CLI-only and NOT part of the public SDK API.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ...compare import compare_values
from ...criteria import Condition, FilterGroup, filter_value_type
from ...models.entities import DropdownOption, FieldMetadata
from .dates import parse_date_value
from .models import WhereClause

# (filter value type, server operator) pairs confirmed by tools/search_golden.py: no record the
# local filter keeps was missing from the server's answer (5 lists, about 3,750 entries,
# 2026-10-13). Enriched fields and shared field names were excluded (they stay local).
VERIFIED: frozenset[tuple[str, str]] = frozenset(
    {
        ("date", "is-after"),
        ("date", "is-before"),
        ("date", "is-between"),
        ("date", "is-not-empty"),
        ("dropdown", "is-any-of"),
        ("dropdown", "is-none-of"),
        ("dropdown", "is-not-empty"),
        ("number", "is-between"),
        ("number", "is-equal-to"),
        ("number", "is-greater-than"),
        ("number", "is-greater-than-or-equal-to"),
        ("number", "is-less-than"),
        ("number", "is-less-than-or-equal-to"),
        ("number", "is-not-empty"),
        ("person", "is-not-empty"),
        ("ranked-dropdown", "is-any-of"),
        ("ranked-dropdown", "is-none-of"),
        ("ranked-dropdown", "is-not-empty"),
        ("text", "contains"),
        ("text", "is-not-empty"),
    }
)

_NUMBER_OPS = {
    "eq": "is-equal-to",
    "gt": "is-greater-than",
    "gte": "is-greater-than-or-equal-to",
    "lt": "is-less-than",
    "lte": "is-less-than-or-equal-to",
}
_VALUE_TYPES = (
    "company",
    "company-multi",
    "person",
    "person-multi",
    "list-multi",
    "dropdown",
    "dropdown-multi",
    "ranked-dropdown",
    "location",
    "location-multi",
    "filterable-text",
    "filterable-text-multi",
    "text",
    "number",
    "date",
)
# Every (value type, operator) this module can emit; tools/search_golden.py checks each one.
ALL_MAPPINGS: frozenset[tuple[str, str]] = frozenset(
    {(vt, op) for vt in ("dropdown", "ranked-dropdown") for op in ("is-any-of", "is-none-of")}
    | {(vt, op) for vt in _VALUE_TYPES for op in ("is-empty", "is-not-empty")}
    | {("text", "contains"), ("filterable-text", "is-any-of")}
    | {("number", op) for op in (*_NUMBER_OPS.values(), "is-between")}
    | {("date", op) for op in ("is-after", "is-before", "is-between")}
    | {(vt, "term") for vt in ("person", "person-multi", "company", "company-multi")}
)

_DROPDOWN = {"dropdown", "ranked-dropdown"}
_NAMED = {"person", "person-multi", "company", "company-multi"}
# How enriched-field conditions go to the server (see split_where). "or-empty" verified by
# tools/search_golden.py --enriched-or-empty (5 lists, 604 cases, none missing; 2026-10-13).
ENRICHED = "or-empty"
MAX_GROUP = 50
MAX_VALUES = 100

OptionsFor = Callable[[FieldMetadata], Sequence[DropdownOption]]


@dataclass
class Pushdown:
    """The server part of a WHERE clause and why the rest stays local."""

    where: Condition | FilterGroup | None = None
    # A name search (``starts_word`` on a person or company field): the search term and the
    # field it's matched against (it also matches the record's own name: a superset)
    term: str | None = None
    term_fields: tuple[str, ...] = ()
    pushed: list[str] = field(default_factory=list)
    local: list[str] = field(default_factory=list)
    # Conditions the query must not run with (e.g. contains on a person field)
    errors: list[str] = field(default_factory=list)

    @property
    def server_side(self) -> bool:
        """True when some condition goes to the server."""
        return self.where is not None or self.term is not None

    @property
    def complete(self) -> bool:
        """True when every condition went to the server (the local re-check is a formality)."""
        return self.server_side and not self.local


@dataclass
class _Ctx:
    fields: Mapping[str, FieldMetadata | None]  # by lowercased name and by id
    options_for: OptionsFor
    verified: frozenset[tuple[str, str]]
    pushed: list[str]
    local: list[str]
    enriched: str  # "local" or "or-empty"
    errors: list[str] = field(default_factory=list)
    term_claim: WhereClause | None = None  # the starts_word condition sent as the search term


def fields_index(fields: Sequence[FieldMetadata]) -> dict[str, FieldMetadata | None]:
    """Index fields by id and by lowercased name. A name two fields share maps to ``None``:
    records key field values by name, so which field a shared name reads is not defined."""
    index: dict[str, FieldMetadata | None] = {}
    for f in fields:
        if f.name:
            key = f.name.lower()
            index[key] = None if key in index else f
    for f in fields:
        index[str(f.id)] = f
    return index


def split_where(
    where: WhereClause | None,
    fields: Sequence[FieldMetadata],
    options_for: OptionsFor,
    *,
    verified: frozenset[tuple[str, str]] = VERIFIED,
    enriched: str = ENRICHED,
) -> Pushdown:
    """Split ``where`` into the server part (a superset) and the reasons the rest stays local.

    ``fields`` need ``filterability`` (fields requested with ``includes``); ``options_for``
    returns a dropdown field's options (called only for dropdown conditions).

    ``enriched``: how conditions on Affinity's enriched fields go: ``"local"`` (not sent) or
    ``"or-empty"`` (sent as "condition OR the field is empty": search reads some records'
    enriched values as empty, so those come back too and the local filter decides).
    """
    result = Pushdown()
    if where is None:
        return result
    ctx = _Ctx(
        fields_index(fields),
        options_for,
        verified,
        result.pushed,
        result.local,
        enriched,
        result.errors,
    )
    _claim_term(where, ctx, result)
    node = _node(where, ctx)
    if node is not None:
        result.where = node if isinstance(node, FilterGroup) else FilterGroup("and", (node,))
    return result


def _claim_term(where: WhereClause, ctx: _Ctx, result: Pushdown) -> None:
    """Send the first top-level ``starts_word`` on a person or company field as the search
    term (a search takes one term; it ANDs with the filters)."""
    for w in [where] if where.and_ is None else where.and_:
        if w.op != "starts_word" or not isinstance(w.value, str) or len(w.value.strip()) < 3:
            continue
        trial = _Ctx(ctx.fields, ctx.options_for, ctx.verified, [], [], ctx.enriched)
        found = _field(w, trial)
        if found is None or found[1] not in _NAMED:
            continue
        meta, vt = found
        if (vt, "term") not in ctx.verified:
            continue
        result.term, result.term_fields = w.value.strip(), (str(meta.id),)
        ctx.term_claim = w
        ctx.pushed.append(_describe(w))
        return


def _node(w: WhereClause, ctx: _Ctx) -> Condition | FilterGroup | None:
    if w.and_ is not None:
        parts = [p for p in (_node(c, ctx) for c in w.and_) if p is not None]
        if not parts:
            return None
        if len(parts) > MAX_GROUP:
            ctx.local.append(f"more than {MAX_GROUP} conditions in one AND: the rest stay local")
            parts = parts[:MAX_GROUP]
        return parts[0] if len(parts) == 1 else FilterGroup("and", tuple(parts))
    if w.or_ is not None:
        # Every branch must go to the server, or none: a partial OR would drop records.
        trial = _Ctx(ctx.fields, ctx.options_for, ctx.verified, [], [], ctx.enriched, ctx.errors)
        branches = [_node(c, trial) for c in w.or_]
        if any(b is None for b in branches) or trial.local or len(branches) > MAX_GROUP:
            ctx.local.append("an OR with a branch the server can't answer")
            return None
        ctx.pushed.extend(trial.pushed)
        parts = [b for b in branches if b is not None]
        return parts[0] if len(parts) == 1 else FilterGroup("or", tuple(parts))
    if w.not_ is not None:
        return _negated(w.not_, ctx)
    if w.all_ is not None or w.none_ is not None or w.exists_ is not None:
        ctx.local.append("all/none/exists (needs related records)")
        return None
    if w.op is None:
        return None
    cond = _condition(w, ctx)
    meta = ctx.fields.get(cond.field_id) if cond is not None else None
    if cond is not None and meta is not None and meta.type == "enriched":
        empty = Condition(cond.field_id, "is-empty", value_type=cond.value_type)
        return FilterGroup("or", (cond, empty))
    return cond


def _describe(w: WhereClause) -> str:
    return f"{w.path} {w.op} {w.value!r}" if w.value is not None else f"{w.path} {w.op}"


def _field(w: WhereClause, ctx: _Ctx) -> tuple[FieldMetadata, str] | None:
    """The field a ``fields.<name or id>`` path names, and its filter value type."""
    if w.expr is not None or not w.path or not w.path.startswith("fields."):
        ctx.local.append(f"{_describe(w)}: not a field the search filters")
        return None
    key = w.path.removeprefix("fields.")
    found_key = key if key in ctx.fields else key.lower()
    if found_key not in ctx.fields:
        ctx.local.append(f"{_describe(w)}: unknown field")
        return None
    meta = ctx.fields[found_key]
    if meta is None:
        ctx.local.append(f"{_describe(w)}: two fields share this name")
        return None
    if meta.type == "enriched" and (ctx.enriched != "or-empty" or w.op == "is_not_null"):
        # Search reads some records' enriched values as empty (live golden test, 2026-10-13);
        # "or empty" makes "is not null" match everything, so it stays local too.
        ctx.local.append(f"{_describe(w)}: Affinity enriched fields stay local")
        return None
    if "filterability" in meta.model_fields_set and meta.filterability is None:
        ctx.local.append(f"{_describe(w)}: the field can't be filtered on the server")
        return None
    if (meta.filterability or {}).get("filterableFieldType") == "attribute-on-field":
        ctx.local.append(f"{_describe(w)}: the field is filtered by attribute")
        return None
    raw = getattr(meta.value_type, "value", meta.value_type)
    vt = filter_value_type(str(raw))
    if vt is None:
        ctx.local.append(f"{_describe(w)}: no server filter for {raw} fields")
        return None
    return meta, vt


def _emit(
    w: WhereClause, meta: FieldMetadata, vt: str, op: str, value: Any, ctx: _Ctx
) -> Condition | None:
    if (vt, op) not in ctx.verified:
        ctx.local.append(f"{_describe(w)}: {vt} {op} not verified against the API yet")
        return None
    allowed = [
        str(o.get("id")) for o in (meta.filterability or {}).get("operators") or [] if o.get("id")
    ]
    if "filterability" in meta.model_fields_set and allowed and op not in allowed:
        ctx.local.append(f"{_describe(w)}: the field doesn't take {op}")
        return None
    if isinstance(value, list) and not 1 <= len(value) <= MAX_VALUES:
        ctx.local.append(f"{_describe(w)}: needs 1 to {MAX_VALUES} values on the server")
        return None
    ctx.pushed.append(_describe(w))
    if value is None:
        return Condition(str(meta.id), op, value_type=vt)
    return Condition(str(meta.id), op, value, value_type=vt)


def _option_ids(meta: FieldMetadata, ctx: _Ctx, test: Callable[[str], bool]) -> list[int]:
    return [int(o.id) for o in ctx.options_for(meta) if test(o.text)]


def _texts(value: Any) -> list[str] | None:
    if isinstance(value, list):
        return [str(v) for v in value] if value else None
    return None if value is None else [str(value)]


def _condition(w: WhereClause, ctx: _Ctx) -> Condition | None:
    op = w.op
    if op == "is_not_null":
        found = _field(w, ctx)
        if found is None:
            return None
        meta, vt = found
        return _emit(w, meta, vt, "is-not-empty", None, ctx)
    if op in ("is_null", "is_empty"):
        found = _field(w, ctx)
        if found is None:
            return None
        meta, vt = found
        return _emit(w, meta, vt, "is-empty", None, ctx)
    if op == "neq":
        return _negated(WhereClause(path=w.path, op="eq", value=w.value), ctx)
    if w is ctx.term_claim:
        return None  # sent as the search term
    found = _field(w, ctx)
    if found is None:
        return None
    meta, vt = found
    value = w.value
    if vt in _NAMED and op in ("contains", "contains_any", "contains_all"):
        ctx.errors.append(
            f"{_describe(w)}: on person and company fields use starts_word (a word in the "
            "name starts with the text, as in Affinity's search)"
        )
        return None
    if vt in _NAMED and op == "starts_word":
        ctx.local.append(f"{_describe(w)}: one name search per query (the rest stay local)")
        return None
    if vt in _DROPDOWN:
        return _dropdown(w, meta, vt, ctx)
    if vt == "text":
        text_ops = ("eq", "contains", "starts_with", "ends_with", "starts_word")
        if op in text_ops and isinstance(value, str) and value:
            # The server's contains is case-insensitive: a superset of all four (and the only
            # text operator many fields take).
            return _emit(w, meta, vt, "contains", value, ctx)
    elif vt == "number":
        return _number(w, meta, ctx)
    elif vt == "date":
        return _date(w, meta, ctx)
    elif vt == "filterable-text" and op in ("eq", "in"):
        texts = _texts(value)
        if texts:
            return _emit(w, meta, vt, "is-any-of", texts, ctx)
    ctx.local.append(f"{_describe(w)}: no server mapping for {vt} {op}")
    return None


def _dropdown(w: WhereClause, meta: FieldMetadata, vt: str, ctx: _Ctx) -> Condition | None:
    """Locally a dropdown value is the option's text, so map the text test onto option ids."""
    op, value = w.op, w.value
    tests: dict[str, Callable[[str], bool]] = {}
    if op == "eq" and isinstance(value, str):
        tests[op] = lambda t: t == value
    elif op == "in" and isinstance(value, list):
        wanted = {str(v) for v in value}
        tests[op] = lambda t: t in wanted
    elif op == "contains" and isinstance(value, str):
        needle = value.lower()
        tests[op] = lambda t: needle in t.lower()
    elif op == "starts_with" and isinstance(value, str):
        prefix = value.lower()
        tests[op] = lambda t: t.lower().startswith(prefix)
    elif op == "ends_with" and isinstance(value, str):
        suffix = value.lower()
        tests[op] = lambda t: t.lower().endswith(suffix)
    elif op == "starts_word" and isinstance(value, str):
        tests[op] = lambda t: compare_values(t, value, "starts_word")
    if op not in tests:
        ctx.local.append(f"{_describe(w)}: no server mapping for {vt} {op}")
        return None
    ids = _option_ids(meta, ctx, tests[op])
    if not ids:
        ctx.local.append(f"{_describe(w)}: no dropdown option matches")
        return None
    return _emit(w, meta, vt, "is-any-of", ids, ctx)


def _negated(inner: WhereClause, ctx: _Ctx) -> Condition | None:
    """NOT: only "not (dropdown is one of these options)" — is-none-of keeps empty values,
    like the local filter."""
    if inner.op not in ("eq", "in") or inner.path is None:
        ctx.local.append("a NOT the server can't answer")
        return None
    trial = _Ctx(ctx.fields, ctx.options_for, ctx.verified, [], [], ctx.enriched, ctx.errors)
    found = _field(inner, trial)
    if found is None or found[1] not in _DROPDOWN:
        ctx.local.append(f"not ({_describe(inner)}): stays local")
        return None
    meta, vt = found
    texts = _texts(inner.value) or []
    wanted = set(texts)
    ids = _option_ids(meta, ctx, lambda t: t in wanted)
    # Every option with that text must be excluded, and an unknown text excludes nothing.
    if not ids:
        ctx.local.append(f"not ({_describe(inner)}): no dropdown option matches")
        return None
    w = WhereClause(not_=inner)
    cond = _emit(w, meta, vt, "is-none-of", ids, ctx)
    if cond is not None:
        ctx.pushed[-1] = f"not ({_describe(inner)})"
    return cond


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _number(w: WhereClause, meta: FieldMetadata, ctx: _Ctx) -> Condition | None:
    op = w.op or ""
    if op == "between" and isinstance(w.value, list) and len(w.value) == 2:
        low, high = (_as_number(v) for v in w.value)
        if low is not None and high is not None and low <= high:
            return _emit(w, meta, "number", "is-between", [_num(low), _num(high)], ctx)
    elif op in _NUMBER_OPS:
        n = _as_number(w.value)
        if n is not None:
            return _emit(w, meta, "number", _NUMBER_OPS[op], _num(n), ctx)
    ctx.local.append(f"{_describe(w)}: no server mapping for number {op}")
    return None


def _num(n: float) -> int | float:
    return int(n) if n.is_integer() else n


def _as_day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        parsed = parse_date_value(value)
        return parsed.date() if parsed is not None else None
    return None


def _date(w: WhereClause, meta: FieldMetadata, ctx: _Ctx) -> Condition | None:
    """The server compares whole days, the local filter compares times (and the org's time zone
    may differ from UTC), so every window is widened by a day on each side."""
    op = w.op or ""
    one = timedelta(days=1)
    if op == "between" and isinstance(w.value, list) and len(w.value) == 2:
        start, end = (_as_day(v) for v in w.value)
        if start is not None and end is not None and start <= end:
            return _emit(w, meta, "date", "is-between", (start - one, end + one), ctx)
    else:
        day = _as_day(w.value)
        if day is not None:
            # is-after / is-before are strict (datetime fields take no "on or after")
            if op in ("gt", "gte"):
                return _emit(w, meta, "date", "is-after", day - 2 * one, ctx)
            if op in ("lt", "lte"):
                return _emit(w, meta, "date", "is-before", day + 2 * one, ctx)
            if op == "eq":
                return _emit(w, meta, "date", "is-between", (day - one, day + one), ctx)
    ctx.local.append(f"{_describe(w)}: no server mapping for date {op}")
    return None
