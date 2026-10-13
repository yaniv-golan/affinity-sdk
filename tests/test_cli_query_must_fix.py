"""CLI query engine: resume without lost rows, whole-record CSV truncation, errors that fail
the query instead of passing as empty, max-records cuts, ambiguous list names, aggregates on
objects, formula values and interrupted runs."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli
from affinity.cli.query.aggregates import compute_max, compute_min, group_and_aggregate
from affinity.cli.query.exceptions import QueryExecutionError, QueryValidationError
from affinity.cli.query.executor import QueryExecutor, _normalize_list_entry_fields
from affinity.cli.query.models import AggregateFunc, ExecutionPlan, PlanStep, Query
from affinity.cli.query.output import truncate_csv_output
from affinity.exceptions import NotFoundError, RateLimitError, ServerError

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
WHOAMI = {
    "tenant": {"id": 1, "name": "Test", "subdomain": "test"},
    "user": {"id": 100, "firstName": "T", "lastName": "U", "emailAddress": "t@example.com"},
    "grant": {"type": "api_key", "scopes": ["all"], "createdAt": "2024-01-01T00:00:00Z"},
}


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Resume caches go to a per-test directory."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture(autouse=True)
def _whoami(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))


def _page(items: list[Any], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _person(pid: int) -> dict[str, Any]:
    return {
        "id": pid,
        "firstName": "Jane",
        "lastName": f"Doe\n{pid}",
        "primaryEmailAddress": f"jane{pid}@example.com",
        "emailAddresses": [f"jane{pid}@example.com"],
        "type": "external",
    }


def _mock_person_pages(router: respx.MockRouter, ids: list[int], size: int) -> respx.Route:
    """Pages of ``size`` persons, chained by cursor; returns the route (for call counts)."""
    url = f"{V2}/persons"
    pages = [ids[i : i + size] for i in range(0, len(ids), size)]

    def respond(request: Any) -> Response:
        cursor = request.url.params.get("cursor")
        index = int(cursor) if cursor else 0
        nxt = f"{url}?cursor={index + 1}" if index + 1 < len(pages) else None
        return Response(200, json=_page([_person(i) for i in pages[index]], nxt))

    return router.get(url__startswith=url).mock(side_effect=respond)


def _run_query(
    query: dict[str, Any], *args: str, output: str = "json"
) -> tuple[int, str, str | None]:
    """Run the query; (exit code, stdout, cursor from stderr or None)."""
    result = CliRunner().invoke(
        cli,
        ["query", "--query", json.dumps(query), "--output", output, *args],
        env=ENV,
    )
    cursor = None
    for line in result.stderr.splitlines():
        if line.startswith("{") and '"cursor"' in line:
            cursor = json.loads(line)["cursor"]
    return result.exit_code, result.stdout, cursor


def _page_through(
    query: dict[str, Any], max_bytes: int, output: str = "json"
) -> tuple[list[str], int]:
    """Resume until the end; (each run's stdout, number of runs)."""
    outputs: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        args = ["--max-output-bytes", str(max_bytes)]
        if cursor:
            args += ["--cursor", cursor]
        code, out, cursor = _run_query(query, *args, output=output)
        outputs.append(out)
        if code == 0:
            return outputs, len(outputs)
        assert code == 100, out
        assert cursor, "a truncated run gives a cursor"
    raise AssertionError("never finished")


def _json_ids(outputs: list[str]) -> list[int]:
    ids: list[int] = []
    for out in outputs:
        ids.extend(r["id"] for r in json.loads(out.strip().splitlines()[-1])["data"])
    return ids


# --- 25: resume loses no rows ---------------------------------------------------------------


def test_resume_across_local_pages_returns_every_row_once(respx_mock: respx.MockRouter) -> None:
    ids = list(range(1001, 1015))
    route = _mock_person_pages(respx_mock, ids, size=4)
    query = {"from": "persons", "limit": 10, "select": ["id", "lastName"]}
    outputs, runs = _page_through(query, max_bytes=180)
    assert runs > 2  # the cut falls inside pages and spans pages
    assert _json_ids(outputs) == ids[:10]
    assert route.call_count == 3  # the limit stops in page 3; resumes read the cache


def test_resume_stopping_mid_page_keeps_the_rest_of_the_page(
    respx_mock: respx.MockRouter,
) -> None:
    # The limit stops the fetch inside the second page; rows cut by --max-output-bytes
    # on the first page used to be skipped (resume jumped to the next API page)
    ids = list(range(2001, 2013))
    _mock_person_pages(respx_mock, ids, size=5)
    query = {"from": "persons", "limit": 7, "select": ["id"]}
    outputs, _runs = _page_through(query, max_bytes=60)
    assert _json_ids(outputs) == ids[:7]


def test_resume_with_include_reruns_and_skips(respx_mock: respx.MockRouter) -> None:
    # 'include' results aren't cached: resume runs the query again and skips the rows shown
    ids = list(range(3001, 3007))
    _mock_person_pages(respx_mock, ids, size=4)
    respx_mock.get(url__regex=r"^https://api\.affinity\.co/persons/\d+").mock(
        side_effect=lambda req: Response(
            200,
            json={"id": int(req.url.path.rsplit("/", 1)[-1]), "organization_ids": []},
        )
    )
    query = {"from": "persons", "limit": 6, "select": ["id"], "include": ["companies"]}
    outputs, runs = _page_through(query, max_bytes=120)
    assert runs > 1
    assert _json_ids(outputs) == ids


NUMBER_OPS = ["is-greater-than", "is-less-than", "is-equal-to", "is-not-empty", "is-empty"]
COMPANY_FIELDS = [
    {
        "id": "field-1",
        "name": "Deals",
        "type": "global",
        "valueType": "number",
        "enrichmentSource": None,
        "filterability": {
            "filterableFieldType": "field-only",
            "operators": [
                {"id": o, "name": o, "numberOfValuesRequired": "one"} for o in NUMBER_OPS
            ],
        },
    }
]


def _company(cid: int, deals: int) -> dict[str, Any]:
    return {
        "id": cid,
        "name": f"Acme Corp {cid}",
        "domain": None,
        "domains": [],
        "isGlobal": False,
        "fields": [
            {
                "id": "field-1",
                "name": "Deals",
                "type": "global",
                "value": {"type": "number", "data": deals},
            }
        ],
    }


def test_resume_across_search_pages_returns_every_row_once(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{V2}/companies/fields").mock(
        return_value=Response(200, json=_page(COMPANY_FIELDS))
    )
    search = f"{V2}/companies/search"
    ids = list(range(4001, 4010))  # pages of 4, 4, 1
    pages = [ids[0:4], ids[4:8], ids[8:]]

    def respond(request: Any) -> Response:
        cursor = request.url.params.get("cursor")
        index = int(cursor) if cursor else 0
        nxt = f"{search}?cursor={index + 1}" if index + 1 < len(pages) else None
        return Response(201, json=_page([_company(i, 9) for i in pages[index]], nxt))

    route = respx_mock.post(url__startswith=search).mock(side_effect=respond)
    query = {
        "from": "companies",
        "where": {"path": "fields.Deals", "op": "gt", "value": 5},
        "limit": 6,
        "select": ["id"],
    }
    outputs, runs = _page_through(query, max_bytes=60)
    assert runs > 2
    assert _json_ids(outputs) == ids[:6]  # the limit stops inside the second search page
    assert route.call_count == 2  # resumes come from the cache


def test_resume_jsonl_and_toon_across_pages(respx_mock: respx.MockRouter) -> None:
    ids = list(range(5001, 5011))
    _mock_person_pages(respx_mock, ids, size=3)
    query = {"from": "persons", "limit": 8, "select": ["id"]}
    outputs, _ = _page_through(query, max_bytes=60, output="jsonl")
    got = [
        json.loads(line)["id"]
        for out in outputs
        for line in out.splitlines()
        if line.strip() and line != '{"truncated":true}'
    ]
    assert got == ids[:8]


STATUS_FIELD = {
    "id": "field-1",
    "name": "Status",
    "type": "list",
    "valueType": "text",
    "enrichmentSource": None,
}


def _entry(entry_id: int) -> dict[str, Any]:
    return {
        "id": entry_id,
        "listId": 9,
        "type": "company",
        "createdAt": "2026-01-01T00:00:00Z",
        "creatorId": 1,
        "entity": {
            "id": 100 + entry_id,
            "name": f"Acme Corp {entry_id}",
            "domain": None,
            "domains": [],
            "isGlobal": False,
            "fields": [
                {
                    "id": "field-1",
                    "name": "Status",
                    "type": "list",
                    "value": {"type": "text", "data": f"Stage {entry_id}"},
                }
            ],
        },
    }


@pytest.mark.parametrize(("output", "max_bytes"), [("toon", 200), ("csv", 120), ("markdown", 200)])
def test_resumed_pages_keep_the_selected_columns(
    respx_mock: respx.MockRouter, output: str, max_bytes: int
) -> None:
    respx_mock.get(f"{V2}/lists/9/fields").mock(
        return_value=Response(200, json=_page([STATUS_FIELD]))
    )
    url = f"{V2}/lists/9/list-entries"
    ids = list(range(1, 21))
    pages = [ids[0:8], ids[8:16], ids[16:]]

    def respond(request: Any) -> Response:
        cursor = request.url.params.get("cursor")
        index = int(cursor) if cursor else 0
        nxt = f"{url}?cursor={index + 1}" if index + 1 < len(pages) else None
        return Response(200, json=_page([_entry(i) for i in pages[index]], nxt))

    respx_mock.get(url__startswith=url).mock(side_effect=respond)
    query = {
        "from": "listEntries",
        "where": {"path": "listId", "op": "eq", "value": 9},
        "select": ["id", "fields.Status"],
    }
    outputs, runs = _page_through(query, max_bytes=max_bytes, output=output)
    assert runs > 2
    text = "\n".join(outputs)
    for i in ids:
        assert len(re.findall(rf"Stage {i}\b", text)) == 1, (i, outputs)
    # Every page has the flattened column, not a nested 'fields' dict
    assert all("fields.Status" in out.splitlines()[0] for out in outputs), outputs
    if output == "csv":
        assert {out.splitlines()[0] for out in outputs} == {"id,fields.Status"}


def test_old_api_cursor_is_not_used_to_skip_ahead(respx_mock: respx.MockRouter) -> None:
    # A cursor from before the fix carries apiCursor (the page after the stop). Resume runs
    # the query again and anchors on lastId instead.
    from affinity.cli.query.cursor import create_streaming_cursor, encode_cursor
    from affinity.cli.query.parser import parse_query

    ids = list(range(6001, 6009))
    _mock_person_pages(respx_mock, ids, size=4)
    query = {"from": "persons", "limit": 8, "select": ["id"]}
    parsed = parse_query(query).query
    old = create_streaming_cursor(
        parsed, "json", 2, last_id=6002, api_cursor=f"{V2}/persons?cursor=1"
    )
    code, out, _ = _run_query(query, "--cursor", encode_cursor(old))
    assert code == 0, out
    assert _json_ids([out]) == ids[2:]


# --- 34 / 35: CSV and JSONL truncation -------------------------------------------------------


def test_csv_truncation_keeps_whole_records() -> None:
    content = 'id,note\r\n1,"line one\r\nline two"\r\n2,plain\r\n3,"a\nb"\r\n'
    out, truncated = truncate_csv_output(content, 45)
    assert truncated
    assert "\r\n" not in out[-2:]
    import csv
    import io

    rows = list(csv.reader(io.StringIO(out, newline="")))
    assert rows[0] == ["id", "note"]
    assert rows[1:] == [["1", "line one\r\nline two"], ["2", "plain"]]
    # A cut that would land inside the quoted field keeps the header only
    out, _ = truncate_csv_output(content, 25)
    assert out == "id,note"


def test_csv_row_count_uses_records() -> None:
    from affinity.cli.commands.query_cmd import _count_rows_in_output

    assert _count_rows_in_output('id,note\r\n1,"a\nb"\r\n2,c', "csv") == 2


def test_jsonl_count_keeps_rows_with_truncated_keys() -> None:
    from affinity.cli.commands.query_cmd import _count_rows_in_output

    output = '{"id":1,"truncated":true}\n{"truncated":true,"id":2}\n{"truncated":true}'
    assert _count_rows_in_output(output, "jsonl") == 2
    assert _count_rows_in_output('{"id":1,"truncated":false}\n', "jsonl") == 1


def test_csv_resume_with_embedded_newlines(respx_mock: respx.MockRouter) -> None:
    ids = list(range(7001, 7007))  # last names hold a newline
    _mock_person_pages(respx_mock, ids, size=4)
    query = {"from": "persons", "limit": 6, "select": ["id", "lastName"]}
    outputs, runs = _page_through(query, max_bytes=40, output="csv")
    assert runs > 1
    import csv
    import io

    got = []
    for out in outputs:
        rows = list(csv.reader(io.StringIO(out.strip("\n"), newline="")))
        got.extend(int(r[0]) for r in rows[1:] if r)
    assert got == ids


# --- executor fixtures ----------------------------------------------------------------------


def _client() -> AsyncMock:
    client = AsyncMock()
    client.whoami = AsyncMock(return_value={})
    return client


def _pages(records: list[dict[str, Any]]) -> MagicMock:
    page = MagicMock()
    page.data = [MagicMock(model_dump=MagicMock(return_value=dict(r))) for r in records]
    page.next_cursor = None

    async def pages(**_kwargs: Any) -> Any:
        yield page

    iterator = MagicMock()
    iterator.pages = pages
    return iterator


def _plan(query: Query, *ops: str, relationship: str | None = None) -> ExecutionPlan:
    steps = [PlanStep(step_id=0, operation="fetch", entity=query.from_, description="Fetch")]
    for i, op in enumerate(ops, start=1):
        steps.append(
            PlanStep(
                step_id=i,
                operation=op,  # type: ignore[arg-type]
                entity=query.from_,
                relationship=relationship if op == "include" else None,
                description=op,
                depends_on=[i - 1],
            )
        )
    return ExecutionPlan(
        query=query,
        steps=steps,
        total_api_calls=1,
        estimated_records_fetched=1,
        estimated_memory_mb=0.01,
        warnings=[],
        recommendations=[],
        has_expensive_operations=False,
        requires_full_scan=False,
    )


# --- 27 / 28: relationship errors fail the query --------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        RateLimitError("Too many requests", status_code=429),
        ServerError("Internal error", status_code=500),
        ValueError("unexpected payload"),  # not an API error: still a clean query failure
    ],
)
def test_include_error_fails_the_query(error: Exception) -> None:
    client = _client()
    persons = MagicMock()
    persons.all.return_value = _pages([{"id": 1}, {"id": 2}])
    persons.get_associated_company_ids = AsyncMock(side_effect=[[100], error])
    client.persons = persons
    query = Query(from_="persons", include=["companies"])
    executor = QueryExecutor(client)
    with pytest.raises(QueryExecutionError):
        asyncio.run(executor.execute(_plan(query, "include", relationship="companies")))


def test_include_not_found_is_no_related_records() -> None:
    client = _client()
    persons = MagicMock()
    persons.all.return_value = _pages([{"id": 1}])
    persons.get_associated_company_ids = AsyncMock(side_effect=NotFoundError("gone"))
    client.persons = persons
    query = Query(from_="persons", include=["companies"])
    result = asyncio.run(
        QueryExecutor(client).execute(_plan(query, "include", relationship="companies"))
    )
    assert result.data == [{"id": 1}]


def test_quantifier_relationship_error_fails_instead_of_matching_none() -> None:
    client = _client()
    persons = MagicMock()
    persons.all.return_value = _pages([{"id": 1}])
    persons.get_associated_company_ids = AsyncMock(
        side_effect=ServerError("Internal error", status_code=500)
    )
    client.persons = persons
    query = Query.model_validate(
        {
            "from": "persons",
            "where": {"path": "companies._count", "op": "eq", "value": 0},
        }
    )
    executor = QueryExecutor(client, max_records_explicit=True)
    with pytest.raises(QueryExecutionError):
        asyncio.run(executor.execute(_plan(query, "filter")))


# --- 31: direct id lookup -------------------------------------------------------------------


def test_direct_lookup_error_fails_instead_of_scanning() -> None:
    client = _client()
    persons = MagicMock()
    persons.get = AsyncMock(side_effect=RateLimitError("Too many requests", status_code=429))
    persons.all = MagicMock(side_effect=AssertionError("no scan"))
    client.persons = persons
    query = Query.model_validate(
        {"from": "persons", "where": {"path": "id", "op": "eq", "value": 12345}}
    )
    with pytest.raises(QueryExecutionError):
        asyncio.run(QueryExecutor(client).execute(_plan(query, "filter")))


def test_direct_lookup_not_found_is_empty() -> None:
    client = _client()
    persons = MagicMock()
    persons.get = AsyncMock(side_effect=NotFoundError("gone"))
    client.persons = persons
    query = Query.model_validate(
        {"from": "persons", "where": {"path": "id", "op": "eq", "value": 12345}}
    )
    result = asyncio.run(QueryExecutor(client).execute(_plan(query, "filter")))
    assert result.data == []


def test_include_error_after_direct_lookup_is_not_hidden() -> None:
    client = _client()
    persons = MagicMock()
    record = MagicMock(model_dump=MagicMock(return_value={"id": 12345}))
    persons.get = AsyncMock(return_value=record)
    persons.get_associated_company_ids = AsyncMock(
        side_effect=ServerError("Internal error", status_code=500)
    )
    persons.all = MagicMock(side_effect=AssertionError("no scan"))
    client.persons = persons
    query = Query.model_validate(
        {
            "from": "persons",
            "where": {"path": "id", "op": "eq", "value": 12345},
            "include": ["companies"],
        }
    )
    plan = _plan(query, "filter", "include", relationship="companies")
    with pytest.raises(QueryExecutionError):
        asyncio.run(QueryExecutor(client).execute(plan))


# --- 32: max_records after aggregate / sort -------------------------------------------------


def _many(n: int) -> list[dict[str, Any]]:
    return [{"id": i, "name": f"Acme Corp {i}", "score": i} for i in range(1, n + 1)]


def test_aggregate_counts_every_matching_row() -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = _pages(_many(30))
    query = Query.model_validate(
        {
            "from": "persons",
            "where": {"path": "score", "op": "gt", "value": 0},
            "aggregate": {"n": {"count": True}},
        }
    )
    result = asyncio.run(
        QueryExecutor(client, max_records=10).execute(_plan(query, "filter", "aggregate"))
    )
    assert result.data == [{"n": 30}]
    assert not result.meta.get("truncated")


def test_sort_then_cut_keeps_the_top_rows_and_warns() -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = _pages(_many(30))
    query = Query.model_validate(
        {
            "from": "persons",
            "where": {"path": "score", "op": "gt", "value": 0},
            "orderBy": [{"field": "score", "direction": "desc"}],
        }
    )
    result = asyncio.run(
        QueryExecutor(client, max_records=10).execute(_plan(query, "filter", "sort"))
    )
    assert [r["id"] for r in result.data] == list(range(30, 20, -1))
    assert result.meta["truncated"] is True
    assert any("--max-records" in w for w in result.warnings)


def test_filtered_cut_without_sort_warns() -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = _pages(_many(30))
    query = Query.model_validate(
        {"from": "persons", "where": {"path": "score", "op": "gt", "value": 0}}
    )
    result = asyncio.run(QueryExecutor(client, max_records=10).execute(_plan(query, "filter")))
    assert len(result.data) == 10
    assert result.meta["truncated"] is True
    assert any("10 of 30" in w for w in result.warnings)


# --- 29: duplicate list names ---------------------------------------------------------------


def test_duplicate_list_name_is_ambiguous() -> None:
    client = _client()
    all_lists = [MagicMock(id=111), MagicMock(id=222), MagicMock(id=333)]
    for item, name in zip(all_lists, ["Deals", "Deals", "Leads"], strict=True):
        item.name = name  # MagicMock(name=...) would name the mock

    async def iterate() -> Any:
        for item in all_lists:
            yield item

    client.lists = MagicMock()
    client.lists.all = MagicMock(return_value=iterate())
    executor = QueryExecutor(client)
    with pytest.raises(QueryValidationError, match=r'Ambiguous list name: "Deals".*111, 222'):
        asyncio.run(
            executor._resolve_list_names_to_ids({"path": "listName", "op": "eq", "value": "Deals"})
        )


# --- 33: aggregates on objects --------------------------------------------------------------


LOCATION = {"city": "Springfield", "country": "United States"}


def test_group_by_object_values() -> None:
    records = [
        {"fields": {"Location": dict(LOCATION)}},
        {"fields": {"Location": dict(LOCATION)}},
        {"fields": {"Location": [{"text": "B"}, {"text": "A"}]}},
        {"fields": {"Location": None}},
    ]
    out = group_and_aggregate(records, "fields.Location", {"n": AggregateFunc(count=True)})
    assert out == [
        {"fields.Location": LOCATION, "n": 2},
        {"fields.Location": [{"text": "B"}, {"text": "A"}], "n": 1},
        {"fields.Location": "(no value)", "n": 1},
    ]


def test_group_by_scalar_lists_unchanged() -> None:
    records = [{"tags": ["b", "a"]}, {"tags": ["a", "b"]}]
    out = group_and_aggregate(records, "tags", {"n": AggregateFunc(count=True)})
    assert out == [{"tags": ["a", "b"], "n": 2}]


def test_min_max_compare_within_one_kind() -> None:
    records = [{"v": 5}, {"v": "text"}, {"v": 2}, {"v": {"a": 1}}, {"v": None}]
    assert compute_min(records, "v") == 2
    assert compute_max(records, "v") == 5
    texts = [{"v": "2026-02-01"}, {"v": "2025-01-01"}]
    assert compute_min(texts, "v") == "2025-01-01"


def test_min_max_on_objects_is_a_validation_error() -> None:
    with pytest.raises(QueryValidationError, match="objects or lists"):
        compute_max([{"v": dict(LOCATION)}, {"v": dict(LOCATION)}], "v")


# --- 16: formula fields ---------------------------------------------------------------------


def test_formula_values_are_unwrapped_for_filters() -> None:
    record = {
        "id": 1,
        "entity": {
            "id": 10,
            "name": "Acme Corp",
            "fields": {
                "requested": True,
                "data": {
                    "field-9": {
                        "name": "Score",
                        "value": {"type": "formula-number", "data": {"calculatedValue": 42.5}},
                    },
                    "field-10": {
                        "name": "Empty",
                        "value": {"type": "formula-number", "data": {"calculatedValue": None}},
                    },
                },
            },
        },
    }
    out = _normalize_list_entry_fields(record)
    assert out["fields"] == {"Score": 42.5, "Empty": None}

    from affinity.cli.query.filters import compile_filter
    from affinity.cli.query.models import WhereClause

    assert compile_filter(WhereClause(path="fields.Score", op="gt", value=40))(out)


# --- 50: interrupted runs exit 130 ----------------------------------------------------------


def test_interrupt_with_partial_rows_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    from affinity.cli.query.models import QueryResult

    async def interrupted(_self: Any, _plan: Any) -> QueryResult:
        return QueryResult(data=[{"id": 1}], meta={"interrupted": True})

    monkeypatch.setattr(QueryExecutor, "execute", interrupted)
    result = CliRunner().invoke(
        cli, ["--json", "query", "--query", json.dumps({"from": "persons", "limit": 5})], env=ENV
    )
    assert result.exit_code == 130
    assert json.loads(result.stdout.strip().splitlines()[-1])["data"] == [{"id": 1}]
    assert "partial" in result.stderr


def test_interrupt_before_any_row_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(_self: Any, _plan: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(QueryExecutor, "execute", interrupted)
    result = CliRunner().invoke(
        cli, ["query", "--query", json.dumps({"from": "persons", "limit": 5})], env=ENV
    )
    assert result.exit_code == 130


def test_cancelled_executor_returns_partial_rows() -> None:
    client = _client()
    persons = MagicMock()
    page = MagicMock()
    page.data = [MagicMock(model_dump=MagicMock(return_value={"id": 1}))]
    page.next_cursor = "next"

    async def pages(**_kwargs: Any) -> Any:
        yield page
        raise asyncio.CancelledError

    persons.all.return_value = MagicMock(pages=pages)
    client.persons = persons
    query = Query(from_="persons")
    result = asyncio.run(QueryExecutor(client, allow_partial=True).execute(_plan(query)))
    assert result.data == [{"id": 1}]
    assert result.meta["interrupted"] is True


def test_cursor_payload_round_trip_unchanged() -> None:
    # The cursor stays base64 JSON with mode streaming/full-fetch (the MCP tool passes it on)
    from affinity.cli.query.cursor import create_streaming_cursor, decode_cursor, encode_cursor

    cursor = create_streaming_cursor(Query(from_="persons"), "json", 3, last_id=12345)
    raw = json.loads(base64.urlsafe_b64decode(encode_cursor(cursor)))
    assert raw["mode"] == "streaming" and "apiCursor" not in raw
    assert decode_cursor(encode_cursor(cursor)).skip == 3


# --- An Affinity error keeps the CLI's documented exit code -----------------------------------


@pytest.mark.parametrize(("status", "exit_code"), [(401, 3), (429, 5), (500, 5)])
def test_query_api_error_keeps_documented_exit_code(status: int, exit_code: int) -> None:
    whoami = {
        "tenant": {"id": 1, "name": "Acme Corp", "subdomain": "acme"},
        "user": {
            "id": 1,
            "firstName": "Jane",
            "lastName": "Doe",
            "emailAddress": "jane@example.com",
        },
        "grant": {"type": "api-key", "scope": "all", "createdAt": "2024-01-01T00:00:00Z"},
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.affinity.co/v2/auth/whoami").mock(
            return_value=Response(200, json=whoami)
        )
        mock.route(host="api.affinity.co").mock(
            return_value=Response(status, json={"errors": [{"code": "x", "message": "boom"}]})
        )
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "--max-retries",
                "0",
                "query",
                "--query",
                json.dumps({"$version": "1.0", "from": "persons", "limit": 5}),
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
    assert result.exit_code == exit_code, result.output
