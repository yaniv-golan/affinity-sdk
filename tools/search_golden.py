"""
Golden test for server-side filtering: does Affinity's search ever drop a record the local
filter keeps?

For each list given, the tool downloads every entry (with its filterable fields), builds test
conditions from the real values (dropdown options, text pieces in other letter case, number and
date midpoints), and runs each one twice: through ``affinity/cli/query/pushdown.py`` and the
V2 search, and through the local query filter. A mapping is safe when, in every case, every
record the local filter keeps is also returned by the server.

Read-only (search is a read). Prints field types, operators and counts only, never values.

Usage:
    AFFINITY_API_KEY=... python tools/search_golden.py LIST_ID [LIST_ID ...]
    ... --enriched-or-empty   # only Affinity's enriched fields, sent as "condition OR empty"
    ... --references-only     # only the person / company id cases (the rest ran in earlier runs)

Person and company id conditions (``Owner = 123``, ``in``, ``has_any``, ``has_all``) are tested
with the most common ids, a rare one, duplicates, 5 ids, an id nobody references, an id of the
other kind, and large ids (2**31 - 1, 2**31 + 7, 2**63 - 1: they decide ``ID_MAX``). Each case
also runs with string values (as ``--filter`` parses them), and the records Affinity's search
returns are normalized and checked against the entries download.

Exit codes: 0 every exercised mapping is safe, 1 a mapping dropped records (or Affinity refused
a search, or a reference has no int id), 2 usage.
Copy the "safe" mappings into ``VERIFIED`` in ``affinity/_filtering/pushdown.py``.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from statistics import median
from typing import Any, NamedTuple

from affinity import Affinity
from affinity._filtering import pushdown
from affinity._filtering.pushdown import ALL_MAPPINGS, split_where
from affinity.cli.query.executor import _normalize_list_entry_fields
from affinity.cli.query.filters import compile_filter
from affinity.cli.query.models import WhereClause
from affinity.compare import EntityName
from affinity.criteria import SearchCriteria, filter_value_type
from affinity.exceptions import (
    AuthorizationError,
    NotFoundError,
    UnsupportedOperationError,
)
from affinity.exceptions import ValidationError as SDKValidationError
from affinity.filters import FieldComparison, FilterExpression, RawToken
from affinity.models.entities import DropdownOption, FieldMetadata
from affinity.policies import Policies, WritePolicy
from affinity.services.lists import _entry_to_filter_dict
from affinity.types import ListId


def _cases(meta: FieldMetadata, vt: str, values: list[Any]) -> list[WhereClause]:
    path = f"fields.{meta.id}"  # by id: two fields can share a name
    present = [v for v in values if v not in (None, "", [])]
    cases = [WhereClause(path=path, op="is_not_null"), WhereClause(path=path, op="is_null")]
    if not present:
        return cases

    def w(op: str, value: Any) -> WhereClause:
        return WhereClause(path=path, op=op, value=value)

    if vt in ("dropdown", "ranked-dropdown"):
        texts = sorted({str(v) for v in present})
        for t in texts[:3]:
            cases += [w("eq", t), w("neq", t), w("contains", t[: max(1, len(t) // 2)].upper())]
        cases.append(w("in", texts[:2]))
    elif vt == "text":
        for t in [str(v) for v in present[:3]]:
            mid = t[len(t) // 3 : len(t) // 3 + 3] or t
            cases += [
                w("eq", t),
                w("contains", mid.swapcase()),
                w("starts_with", t[:3].swapcase()),
                w("ends_with", t[-3:].swapcase()),
            ]
    elif vt == "number":
        nums = sorted(float(v) for v in present if isinstance(v, (int, float)))
        if nums:
            m = median(nums)
            cases += [w(op, m) for op in ("eq", "neq", "gt", "gte", "lt", "lte")]
            cases.append(w("eq", nums[0]))
            cases.append(w("between", [nums[len(nums) // 4], nums[(3 * len(nums)) // 4]]))
    elif vt == "date":
        days = sorted(str(v)[:10] for v in present if isinstance(v, str))
        if days:
            mid_day = days[len(days) // 2]
            cases += [w(op, mid_day) for op in ("gt", "gte", "lt", "lte", "eq")]
            cases.append(w("between", [days[len(days) // 4], days[(3 * len(days)) // 4]]))
    elif vt in ("dropdown-multi", "filterable-text-multi"):
        items = sorted({str(i) for v in present if isinstance(v, list) for i in v})
        for t in items[:3]:
            cases += [w("eq", t), w("neq", t), w("has_all", [t])]
            if vt == "dropdown-multi" and len(t) >= 2:
                cases.append(w("contains", t[: len(t) // 2].upper()))
        if len(items) >= 2:
            cases += [w("in", items[:2]), w("has_any", items[:2])]
    elif vt in ("person", "person-multi", "company", "company-multi"):
        names = sorted({n for v in present for n in (v if isinstance(v, list) else [v]) if n})
        for name in [str(n) for n in names[:4]]:
            words = [p for p in name.split() if len(p) >= 3]
            cases.append(w("starts_word", name))
            if words:
                cases += [w("starts_word", words[0]), w("starts_word", words[-1][:3])]
    elif vt == "filterable-text":
        texts = sorted({str(v) for v in present})
        cases += [w("eq", texts[0]), w("in", texts[:3])]
    return cases


_REFERENCE_TYPES = ("person", "person-multi", "company", "company-multi")
LARGE_IDS = (2**31 - 1, 2**31 + 7, 2**63 - 1)
_REFUSALS = (AuthorizationError, NotFoundError, SDKValidationError, UnsupportedOperationError)


class Row(NamedTuple):
    """One case: how many records the local filter kept / the server returned, how many local
    ones the server missed (``missing``, which includes records whose normalized value differs
    in the search payload), whether Affinity refused the search, whether it returned records
    for an id nothing references (``unexpected``), and how many returned records read
    differently from the entries download (``differing``; expected on some fields)."""

    local: int
    server: int
    missing: int
    refused: bool
    unexpected: bool
    differing: int


def _ids_of(value: Any) -> list[int]:
    items = value if isinstance(value, list) else [value]
    return [i.entity_id for i in items if isinstance(i, EntityName) and i.entity_id is not None]


def _refs_without_int_id(values: list[Any]) -> int:
    """Non-empty references that carry no int id (they could not match an id filter)."""
    count = 0
    for value in values:
        if value in (None, "", []):
            continue
        for item in value if isinstance(value, list) else [value]:
            if not (isinstance(item, EntityName) and item.entity_id is not None):
                count += 1
    return count


def _stringify(value: Any) -> Any:
    """The value as ``--filter`` parses it: numbers become strings."""
    return [str(v) for v in value] if isinstance(value, list) else str(value)


def _reference_cases(
    meta: FieldMetadata, vt: str, values: list[Any], other_kind_ids: set[int]
) -> list[tuple[str, WhereClause]]:
    """Id conditions on a person or company field, built from the ids its records reference.
    The kind is "id" (compared with the local filter), "nonexistent" (an id nothing references:
    must return no rows) or "other-kind" (a company id on a person field, or the reverse)."""
    path = f"fields.{meta.id}"
    per_record = [sorted(set(_ids_of(v))) for v in values]
    counts = Counter(i for ids in per_record for i in ids)
    if not counts:
        return []

    def w(op: str, value: Any) -> WhereClause:
        return WhereClause(path=path, op=op, value=value)

    ranked = [i for i, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    common, rare = ranked[:3], ranked[-1]
    cases: list[tuple[str, WhereClause]] = []
    for i in dict.fromkeys([*common, rare]):
        cases += [("id", w("eq", i)), ("id", w("in", [i]))]
    cases.append(("id", w("in", [ranked[0], ranked[0]])))
    if len(ranked) >= 2:
        cases.append(("id", w("in", ranked[:5])))
    cases.append(("nonexistent", w("eq", max(ranked) + 10**6)))
    foreign = sorted(other_kind_ids - set(counts))
    if foreign:
        cases.append(("other-kind", w("eq", foreign[0])))
    else:
        print(f"    {meta.id}: other-kind id skipped (none on this list)")
    if vt.endswith("-multi"):
        cases.append(("id", w("has_all", [ranked[0]])))
        if len(ranked) >= 2:
            cases.append(("id", w("has_any", ranked[:2])))
        pair = next((ids[:2] for ids in per_record if len(ids) >= 2), None)
        if pair:
            cases.append(("id", w("has_all", pair)))
        sets = [ids for ids in per_record if 1 <= len(ids) <= 100]
        if sets:
            cases.append(("id", w("eq", max(sets, key=len))))
    return cases


def _large_cases(meta: FieldMetadata, vt: str) -> list[WhereClause]:
    path = f"fields.{meta.id}"
    cases = [WhereClause(path=path, op="eq", value=n) for n in LARGE_IDS]
    if vt.endswith("-multi"):
        cases += [WhereClause(path=path, op="has_all", value=[n]) for n in LARGE_IDS]
    return cases


_TEXT_OPS = {
    "eq": "=",
    "neq": "!=",
    "contains": "=~",
    "starts_with": "=^",
    "ends_with": "=$",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def _as_text_filter(where: WhereClause) -> FilterExpression | None:
    """The same condition as an ``affinity.filters`` expression (fields by id), or ``None``."""
    if not where.path or not where.path.startswith("fields."):
        return None
    name = where.path.removeprefix("fields.")
    if where.op == "is_null":
        return FieldComparison(name, "!=", RawToken("*"))
    if where.op == "is_not_null":
        return FieldComparison(name, "=", RawToken("*"))
    if where.op in _TEXT_OPS:
        return FieldComparison(name, _TEXT_OPS[where.op], where.value)
    if where.op in ("in", "between", "has_any", "has_all", "starts_word"):
        return FieldComparison(name, where.op, where.value)
    return None


def _all(entries: Any, body: dict[str, Any], field_ids: list[Any]) -> list[Any] | None:
    """Every entry the search returns (with ``field_ids``), or ``None`` when it is refused."""
    out: list[Any] = []
    try:
        page = entries.find_body(body, field_ids=field_ids, limit=100)
        while True:
            out.extend(page.data)
            if not page.next_cursor:
                return out
            page = entries.find_body(body, cursor=page.next_cursor)
    except _REFUSALS:
        return None


def _field_key(value: Any) -> tuple[Any, ...]:
    items = value if isinstance(value, list) else [value]
    return tuple(sorted((str(i), getattr(i, "entity_id", None)) for i in items if i is not None))


def run_list(
    client: Affinity,
    list_id: int,
    *,
    enriched_or_empty: bool = False,
    references_only: bool = False,
) -> tuple[dict[tuple[str, str], list[Row]], dict[tuple[str, str], list[str]], int]:
    """(results per mapping, large-id outcomes per mapping, references without an int id)."""
    lists = client.lists
    fields = lists.get_fields(ListId(list_id), includes=["filterability"])
    filterable = [
        f
        for f in fields
        if f.filterability
        and f.filterability.get("filterableFieldType") == "field-only"
        and filter_value_type(str(getattr(f.value_type, "value", f.value_type)))
    ]
    if references_only:
        filterable = [
            f
            for f in filterable
            if filter_value_type(str(getattr(f.value_type, "value", f.value_type)))
            in _REFERENCE_TYPES
        ]
    entries = lists.entries(ListId(list_id))
    # Key every field by id (as query does for fields.<id>): names can collide
    by_id = {str(f.id) for f in filterable}
    field_ids = [f.id for f in filterable]
    raw_entries = list(entries.iter(field_ids=field_ids))

    def normalize(entry: Any) -> dict[str, Any]:
        return _normalize_list_entry_fields(
            entry.model_dump(mode="json", by_alias=True), None, by_id
        )

    records = [normalize(e) for e in raw_entries]
    by_entry = {int(r["id"]): r for r in records}
    # What the SDK / --filter text matcher sees (a different code path from query's)
    sdk_dicts = {int(e.id): _entry_to_filter_dict(e) for e in raw_entries}
    print(f"list {list_id}: {len(records)} entries, {len(filterable)} filterable fields")
    options: dict[str, list[DropdownOption]] = {}

    def options_for(meta: FieldMetadata) -> list[DropdownOption]:
        key = str(meta.id)
        if key not in options:
            options[key] = list(lists.get_field_dropdown_options(ListId(list_id), meta.id))
        return options[key]

    def kind_of(vt: str) -> str:
        return "person" if vt.startswith("person") else "company"

    def vt_of(meta: FieldMetadata) -> str:
        return filter_value_type(str(getattr(meta.value_type, "value", meta.value_type))) or ""

    # Ids of each record kind: the list's own entities and every reference field's ids
    ids_by_kind: dict[str, set[int]] = {"person": set(), "company": set()}
    for e in raw_entries:
        if e.type in ids_by_kind and e.entity is not None:
            ids_by_kind[e.type].add(int(e.entity.id))
    refs_without_int_id = 0
    for meta in filterable:
        if vt_of(meta) in _REFERENCE_TYPES and meta.type != "enriched":
            column = [r["fields"].get(str(meta.id)) for r in records]
            refs_without_int_id += _refs_without_int_id(column)
            for value in column:
                ids_by_kind[kind_of(vt_of(meta))].update(_ids_of(value))

    results: dict[tuple[str, str], list[Row]] = defaultdict(list)
    large_ids: dict[tuple[str, str], list[str]] = defaultdict(list)

    def run_case(
        meta: FieldMetadata, where: WhereClause, kind: str, *, large: bool = False
    ) -> None:
        vt = vt_of(meta)
        split = split_where(
            where,
            fields,
            options_for,
            verified=ALL_MAPPINGS,
            enriched="or-empty" if enriched_or_empty else "local",
        )
        if not split.server_side or split.local or split.errors:
            return
        criteria = SearchCriteria(where=split.where, term=split.term, term_fields=split.term_fields)
        conds = criteria.conditions()
        label = conds[0].value_type or vt if conds else vt
        operator = conds[0].operator if conds else "term"
        mapping = (f"enriched {label}" if enriched_or_empty else label, operator)
        body = criteria.to_body()
        # The same condition with string values, as --filter parses it: the same body
        typed = where.model_copy(update={"value": _stringify(where.value)})
        text_split = split_where(
            typed, fields, options_for, verified=ALL_MAPPINGS, enriched="local"
        )
        same_body = (
            SearchCriteria(where=text_split.where).to_body() == body if text_split.where else False
        )
        server_entries = _all(entries, body, field_ids)
        if large:
            outcome = "refused" if server_entries is None else ("rows" if server_entries else "ok")
            n = where.value[0] if isinstance(where.value, list) else where.value
            large_ids[mapping].append(f"{n}:{outcome}")
            return
        keeps = [compile_filter(where), compile_filter(typed)]
        text_filters = [t for t in (_as_text_filter(where), _as_text_filter(typed)) if t]
        local = {int(r["id"]) for r in records if any(k(r) for k in keeps)}
        local |= {i for i, d in sdk_dicts.items() if any(t.matches(d) for t in text_filters)}
        if server_entries is None:
            results[mapping].append(Row(len(local), 0, len(local), True, False, 0))
            return
        server = {int(e.id) for e in server_entries}
        missing = len(local - server)
        # The records the search returned, read through both local paths and compared with
        # the entries download
        payload = {int(e.id): normalize(e) for e in server_entries}
        payload_text = {int(e.id): _entry_to_filter_dict(e) for e in server_entries}
        from_payload = {i for i, r in payload.items() if any(k(r) for k in keeps)}
        from_payload |= {
            i for i, d in payload_text.items() if any(t.matches(d) for t in text_filters)
        }
        missing += len((local & server) - from_payload)
        key = str(meta.id)
        differing = sum(
            _field_key(payload[i]["fields"].get(key)) != _field_key(by_entry[i]["fields"].get(key))
            for i in payload
            if i in by_entry
        )
        unexpected = (kind == "nonexistent" and bool(server)) or not same_body
        results[mapping].append(Row(len(local), len(server), missing, False, unexpected, differing))

    for meta in filterable:
        if enriched_or_empty and meta.type != "enriched":
            continue
        vt = vt_of(meta)
        values = [r["fields"].get(str(meta.id)) for r in records]
        if vt not in _REFERENCE_TYPES and references_only:
            continue
        for where in [] if references_only else _cases(meta, vt, values):
            run_case(meta, where, "plain")
        if vt in _REFERENCE_TYPES and meta.type != "enriched" and not enriched_or_empty:
            other = ids_by_kind["company" if kind_of(vt) == "person" else "person"]
            for kind, where in _reference_cases(meta, vt, values, other):
                run_case(meta, where, kind)
            for where in _large_cases(meta, vt):
                run_case(meta, where, "large", large=True)
    return results, large_ids, refs_without_int_id


def _id_max(large_ids: dict[tuple[str, str], list[str]]) -> str:
    """The ID_MAX the large-id cases support: 2**63 - 1 only if all three cases returned
    200 with no rows for every pair on every list, else 2**31 - 1 if that case did."""
    outcomes = [o for rows in large_ids.values() for o in rows]
    if not outcomes:
        return "not exercised"
    if all(o.endswith(":ok") for o in outcomes):
        return "2**63 - 1"
    if all(o.endswith(":ok") for o in outcomes if o.startswith(f"{2**31 - 1}:")):
        return "2**31 - 1"
    return "re-examine the pair set before shipping"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    enriched_or_empty = "--enriched-or-empty" in argv
    references_only = "--references-only" in argv
    argv = [a for a in argv if a not in ("--enriched-or-empty", "--references-only")]
    # Send the largest ids so the large-id cases are exercised (their outcome decides ID_MAX)
    pushdown.ID_MAX = 2**63 - 1
    totals: dict[tuple[str, str], list[Row]] = defaultdict(list)
    large_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    refs_without_int_id = 0
    with Affinity.from_env(load_dotenv=True, policies=Policies(write=WritePolicy.DENY)) as client:
        for raw in argv:
            found, large, without_id = run_list(
                client,
                int(raw),
                enriched_or_empty=enriched_or_empty,
                references_only=references_only,
            )
            for mapping, rows in found.items():
                totals[mapping].extend(rows)
            for mapping, outcomes in large.items():
                large_ids[mapping].extend(outcomes)
            refs_without_int_id += without_id
    unsafe = refs_without_int_id > 0

    def sound(rows: list[Row]) -> bool:
        return not any(r.missing or r.refused or r.unexpected for r in rows)

    print(
        f"\n{'value type':<18} {'operator':<28} cases  local  server  missing  refused  "
        "unexpected  differing"
    )
    for (vt, op), rows in sorted(totals.items()):
        unsafe |= not sound(rows)
        print(
            f"{vt:<18} {op:<28} {len(rows):>5}  {sum(r.local for r in rows):>5}  "
            f"{sum(r.server for r in rows):>6}  {sum(r.missing for r in rows):>7}  "
            f"{sum(r.refused for r in rows):>7}  {sum(r.unexpected for r in rows):>10}  "
            f"{sum(r.differing for r in rows):>9}{'' if sound(rows) else '  UNSAFE'}"
        )
    safe = sorted(m for m, rows in totals.items() if sound(rows))
    meaningful = sorted(m for m in safe if any(r.local for r in totals[m]))
    print("\nsafe (and matched at least one record):")
    for m in meaningful:
        print(f"    {m!r},")
    if large_ids:
        print("\nlarge ids (200 with no rows is 'ok'):")
        for mapping, outcomes in sorted(large_ids.items()):
            counts = Counter(o.split(":", 1)[1] for o in outcomes)
            print(f"    {mapping!r}: {dict(counts)}")
        print(f"ID_MAX supported: {_id_max(large_ids)}")
    print(f"\nreferences without an int id: {refs_without_int_id}")
    untested = sorted(ALL_MAPPINGS - set(totals))
    print(f"\nnot exercised by these lists: {len(untested)}")
    for m in untested:
        print(f"    {m!r}")
    return 1 if unsafe else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
