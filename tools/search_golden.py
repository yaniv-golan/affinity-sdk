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

Exit codes: 0 every exercised mapping is safe, 1 a mapping dropped records, 2 usage.
Copy the "safe" mappings into ``VERIFIED`` in ``affinity/cli/query/pushdown.py``.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from statistics import median
from typing import Any

from affinity import Affinity
from affinity.cli.query.executor import _normalize_list_entry_fields
from affinity.cli.query.filters import compile_filter
from affinity.cli.query.models import WhereClause
from affinity.cli.query.pushdown import ALL_MAPPINGS, split_where
from affinity.criteria import SearchCriteria, filter_value_type
from affinity.models.entities import DropdownOption, FieldMetadata
from affinity.policies import Policies, WritePolicy
from affinity.types import ListId


def _cases(meta: FieldMetadata, vt: str, values: list[Any]) -> list[WhereClause]:
    path = f"fields.{meta.name}"
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


def run_list(
    client: Affinity, list_id: int, *, enriched_or_empty: bool = False
) -> dict[tuple[str, str], list[tuple[int, int, int]]]:
    lists = client.lists
    fields = lists.get_fields(ListId(list_id), includes=["filterability"])
    filterable = [
        f
        for f in fields
        if f.filterability
        and f.filterability.get("filterableFieldType") == "field-only"
        and filter_value_type(str(getattr(f.value_type, "value", f.value_type)))
    ]
    entries = lists.entries(ListId(list_id))
    records = [
        _normalize_list_entry_fields(e.model_dump(mode="json", by_alias=True))
        for e in entries.iter(field_ids=[f.id for f in filterable])
    ]
    print(f"list {list_id}: {len(records)} entries, {len(filterable)} filterable fields")
    options: dict[str, list[DropdownOption]] = {}

    def options_for(meta: FieldMetadata) -> list[DropdownOption]:
        key = str(meta.id)
        if key not in options:
            options[key] = list(lists.get_field_dropdown_options(ListId(list_id), meta.id))
        return options[key]

    results: dict[tuple[str, str], list[tuple[int, int, int]]] = defaultdict(list)
    for meta in filterable:
        if enriched_or_empty and meta.type != "enriched":
            continue
        vt = filter_value_type(str(getattr(meta.value_type, "value", meta.value_type))) or ""
        values = [r["fields"].get(meta.name) for r in records]
        for where in _cases(meta, vt, values):
            split = split_where(
                where,
                fields,
                options_for,
                verified=ALL_MAPPINGS,
                enriched="or-empty" if enriched_or_empty else "local",
            )
            if not split.server_side or split.local or split.errors:
                continue
            criteria = SearchCriteria(
                where=split.where, term=split.term, term_fields=split.term_fields
            )
            conds = criteria.conditions()
            label = conds[0].value_type or vt if conds else vt
            operator = conds[0].operator if conds else "term"
            mapping = (f"enriched {label}" if enriched_or_empty else label, operator)
            body = criteria.to_body()
            server = {int(e.id) for e in _all(entries, body)}
            keep = compile_filter(where)
            local = {int(r["id"]) for r in records if keep(r)}
            results[mapping].append((len(local), len(server), len(local - server)))
    return results


def _all(entries: Any, body: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    page = entries.find_body(body, limit=100)
    while True:
        out.extend(page.data)
        if not page.next_cursor:
            return out
        page = entries.find_body(body, cursor=page.next_cursor)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    enriched_or_empty = "--enriched-or-empty" in argv
    argv = [a for a in argv if a != "--enriched-or-empty"]
    totals: dict[tuple[str, str], list[tuple[int, int, int]]] = defaultdict(list)
    with Affinity.from_env(load_dotenv=True, policies=Policies(write=WritePolicy.DENY)) as client:
        for raw in argv:
            found = run_list(client, int(raw), enriched_or_empty=enriched_or_empty)
            for mapping, rows in found.items():
                totals[mapping].extend(rows)
    unsafe = False
    print(f"\n{'value type':<18} {'operator':<28} cases  local  server  missing")
    for (vt, op), rows in sorted(totals.items()):
        missing = sum(r[2] for r in rows)
        unsafe |= missing > 0
        print(
            f"{vt:<18} {op:<28} {len(rows):>5}  {sum(r[0] for r in rows):>5}  "
            f"{sum(r[1] for r in rows):>6}  {missing:>7}{'  UNSAFE' if missing else ''}"
        )
    safe = sorted(m for m, rows in totals.items() if not sum(r[2] for r in rows))
    meaningful = sorted(m for m in safe if any(r[0] for r in totals[m]))
    print("\nsafe (and matched at least one record):")
    for m in meaningful:
        print(f"    {m!r},")
    untested = sorted(ALL_MAPPINGS - set(totals))
    print(f"\nnot exercised by these lists: {len(untested)}")
    for m in untested:
        print(f"    {m!r}")
    return 1 if unsafe else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
