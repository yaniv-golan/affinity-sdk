"""CLI query engine: resume without lost rows, whole-record CSV truncation, errors that fail
the query instead of passing as empty, max-records cuts, ambiguous list names, aggregates on
objects, formula values and interrupted runs."""

from __future__ import annotations

import asyncio
import base64
import inspect
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


def _runner() -> CliRunner:
    """A runner with stderr kept apart from stdout on click 8.1 too (8.2+ always does)."""
    if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    return CliRunner()


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
    result = _runner().invoke(
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
    # 'include' results are cached with the rows: resume serves the rest from the cache
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
        parsed,
        "json",
        2,
        last_id=6002,
        api_cursor=f"{V2}/persons?cursor=1",
        max_records=10000,  # The default --max-records, part of the query hash
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
    # A first record past the budget is still kept whole (a page with no row never advances)
    out, _ = truncate_csv_output(content, 25)
    assert out == 'id,note\r\n1,"line one\r\nline two"'


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
    records = [{"v": 5}, {"v": 2}, {"v": {"a": 1}}, {"v": None}]
    assert compute_min(records, "v") == 2
    assert compute_max(records, "v") == 5
    # Numbers mixed with text are refused (tests/test_cli_query_review_fixes.py)
    with pytest.raises(QueryValidationError, match="numbers"):
        compute_min([*records, {"v": "text"}], "v")
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
    result = _runner().invoke(
        cli, ["--json", "query", "--query", json.dumps({"from": "persons", "limit": 5})], env=ENV
    )
    assert result.exit_code == 130
    assert json.loads(result.stdout.strip().splitlines()[-1])["data"] == [{"id": 1}]
    assert "partial" in result.stderr


def test_interrupt_before_any_row_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(_self: Any, _plan: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(QueryExecutor, "execute", interrupted)
    result = _runner().invoke(
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
        result = _runner().invoke(
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


# --- include runs on the rows kept, not on every match ----------------------------------------


@pytest.mark.parametrize(
    ("extra", "ops", "max_records", "kept"),
    [
        ({"limit": 3}, ("filter", "sort", "include", "limit"), 10_000, [30, 29, 28]),
        ({}, ("filter", "sort", "include"), 5, [30, 29, 28, 27, 26]),
    ],
)
def test_include_after_sort_fetches_only_kept_rows(
    extra: dict[str, Any], ops: tuple[str, ...], max_records: int, kept: list[int]
) -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = _pages(_many(30))
    client.persons.get_associated_company_ids = AsyncMock(return_value=[])
    query = Query.model_validate(
        {
            "from": "persons",
            "where": {"path": "score", "op": "gt", "value": 0},
            "orderBy": [{"field": "score", "direction": "desc"}],
            "include": ["companies"],
            **extra,
        }
    )
    result = asyncio.run(
        QueryExecutor(client, max_records=max_records).execute(
            _plan(query, *ops, relationship="companies")
        )
    )
    assert [r["id"] for r in result.data] == kept
    fetched = [c.args[0] for c in client.persons.get_associated_company_ids.await_args_list]
    assert sorted(fetched) == sorted(kept)


# --- --max-records with orderBy or groupBy and no where -------------------------------------


@pytest.mark.parametrize(
    ("spec", "op", "kept"),
    [
        ({"orderBy": [{"field": "score", "direction": "desc"}]}, "sort", list(range(30, 20, -1))),
        ({"groupBy": "score", "aggregate": {"n": {"count": True}}}, "aggregate", None),
    ],
)
def test_sort_or_group_without_where_is_cut_at_max_records(
    spec: dict[str, Any], op: str, kept: list[int] | None
) -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = _pages(_many(30))
    query = Query.model_validate({"from": "persons", **spec})
    result = asyncio.run(
        QueryExecutor(client, max_records=10, max_records_explicit=True).execute(_plan(query, op))
    )
    assert len(result.data) == 10
    if kept is not None:
        assert [r["id"] for r in result.data] == kept
    assert result.meta["truncated"] is True
    assert any("10 of 30" in w and "--max-records" in w for w in result.warnings)


def test_cli_order_by_without_where_is_cut_at_max_records(
    respx_mock: respx.MockRouter,
) -> None:
    _mock_person_pages(respx_mock, list(range(1, 31)), 10)
    code, out, _ = _run_query(
        {"from": "persons", "orderBy": [{"field": "id", "direction": "desc"}]},
        "--max-records",
        "10",
    )
    assert code == 0, out
    envelope = json.loads(out.strip().splitlines()[-1])
    assert [r["id"] for r in envelope["data"]] == list(range(30, 20, -1))
    assert any("--max-records" in w for w in envelope.get("warnings", [])), envelope


# --- include options (list, where, limit, days) are used --------------------------------------


def _list_entry_ctx(query: Query, entity_type: str = "company") -> Any:
    from affinity.cli.query.executor import ExecutionContext

    records = [
        {"id": 1, "entityId": 501, "entityType": entity_type},
        {"id": 2, "entityId": 502, "entityType": entity_type},
    ]
    return ExecutionContext(query=query, records=records)


def _include_step(entity: str, relationship: str) -> PlanStep:
    return PlanStep(
        step_id=1,
        operation="include",
        entity=entity,
        relationship=relationship,
        description="include",
    )


def _deals_lists(*names: str) -> list[MagicMock]:
    found = []
    for list_id, name in enumerate(names, start=111):
        item = MagicMock(id=list_id, type="opportunity")
        item.name = name  # MagicMock(name=...) would name the mock
        found.append(item)
    return found


def _opportunity_client(lists: list[MagicMock]) -> AsyncMock:
    client = _client()
    client.lists = MagicMock()
    client.lists.resolve_all = AsyncMock(return_value=lists)
    client.companies = MagicMock()
    client.companies.get_associated_opportunity_ids = AsyncMock(
        side_effect=lambda cid: [cid * 10, cid * 10 + 1]
    )
    opportunities = [
        {"id": 5010, "name": "Acme Corp seed", "listId": 111},
        {"id": 5011, "name": "Acme Corp other", "listId": 999},
        {"id": 5020, "name": "Beta Inc seed", "listId": 111},
        {"id": 5021, "name": "Beta Inc A", "listId": 111},
    ]

    async def iterate(**_kwargs: Any) -> Any:
        for record in opportunities:
            yield MagicMock(model_dump=MagicMock(return_value=dict(record)))

    client.opportunities = MagicMock()
    client.opportunities.iter = MagicMock(side_effect=iterate)
    return client


def test_list_entries_include_uses_list_and_where() -> None:
    client = _opportunity_client(_deals_lists("Deals"))
    query = Query.model_validate(
        {
            "from": "listEntries",
            "where": {"path": "listId", "op": "eq", "value": 9},
            "include": {
                "opportunities": {
                    "list": "Deals",
                    "where": {"path": "name", "op": "contains", "value": "seed"},
                }
            },
        }
    )
    ctx = _list_entry_ctx(query)
    asyncio.run(
        QueryExecutor(client)._execute_include(_include_step("listEntries", "opportunities"), ctx)
    )
    client.lists.resolve_all.assert_awaited_once_with(name="Deals")
    assert {
        k: [r["id"] for r in v] for k, v in ctx.included_by_parent["opportunities"].items()
    } == {
        1: [5010],
        2: [5020],
    }


@pytest.mark.parametrize(
    ("lists", "exit_code", "error_type"),
    [
        (("Deals", "Deals"), 2, "ambiguous_resolution"),
        ((), 4, "not_found"),
    ],
)
def test_list_entries_include_unknown_or_ambiguous_list_fails(
    lists: tuple[str, ...], exit_code: int, error_type: str
) -> None:
    from affinity.cli.errors import CLIError

    client = _opportunity_client(_deals_lists(*lists))
    query = Query.model_validate(
        {
            "from": "listEntries",
            "where": {"path": "listId", "op": "eq", "value": 9},
            "include": {"opportunities": {"list": "Deals"}},
        }
    )
    ctx = _list_entry_ctx(query)
    with pytest.raises(CLIError) as raised:
        asyncio.run(
            QueryExecutor(client)._execute_include(
                _include_step("listEntries", "opportunities"), ctx
            )
        )
    assert raised.value.exit_code == exit_code
    assert raised.value.error_type == error_type
    client.companies.get_associated_opportunity_ids.assert_not_awaited()


def _interactions_client(per_type: int) -> tuple[AsyncMock, list[dict[str, Any]]]:
    client = _client()
    calls: list[dict[str, Any]] = []

    async def iterate(**kwargs: Any) -> Any:
        calls.append(kwargs)
        await asyncio.sleep(0.001)  # other fetches run meanwhile, as with a real request
        for i in range(per_type):
            yield MagicMock(model_dump=MagicMock(return_value={"id": len(calls) * 100 + i}))

    client.interactions = MagicMock()
    client.interactions.iter = MagicMock(side_effect=iterate)
    return client, calls


@pytest.mark.parametrize(
    ("source", "entity"),
    [("listEntries", "listEntries"), ("persons", "persons")],
)
def test_interactions_include_uses_limit_and_days(source: str, entity: str) -> None:
    client, calls = _interactions_client(per_type=5)
    spec: dict[str, Any] = {
        "from": source,
        "include": {"interactions": {"limit": 3, "days": 7}},
    }
    if source == "listEntries":
        spec["where"] = {"path": "listId", "op": "eq", "value": 9}
    query = Query.model_validate(spec)
    if source == "listEntries":
        ctx = _list_entry_ctx(query, entity_type="person")
    else:
        from affinity.cli.query.executor import ExecutionContext

        ctx = ExecutionContext(query=query, records=[{"id": 501}, {"id": 502}])
    asyncio.run(QueryExecutor(client)._execute_include(_include_step(entity, "interactions"), ctx))
    assert calls
    for call in calls:
        assert (call["end_time"] - call["start_time"]).days == 7
    assert all(len(v) == 3 for v in ctx.included_by_parent["interactions"].values())


# --- a relationship filter on interactions doesn't deadlock -----------------------------------


def test_interactions_filter_with_records_at_the_concurrency_finishes() -> None:
    client, _calls = _interactions_client(per_type=0)
    client.persons = MagicMock()
    client.persons.all.return_value = _pages(_many(6))
    query = Query.model_validate(
        {"from": "persons", "where": {"path": "interactions._count", "op": "eq", "value": 0}}
    )
    executor = QueryExecutor(client, max_records_explicit=True, concurrency=3)

    async def run() -> Any:
        return await asyncio.wait_for(executor.execute(_plan(query, "filter")), 5)

    assert len(asyncio.run(run()).data) == 6


# --- a failed related-record fetch stops the other fetches ------------------------------------


@pytest.mark.parametrize(
    ("target", "service", "method"),
    [
        ("persons", "companies", "get_associated_person_ids"),
        ("companies", "persons", "get_associated_company_ids"),
        ("opportunities", "companies", "get_associated_opportunity_ids"),
        ("interactions", "companies", None),
    ],
)
def test_list_entry_fetch_error_cancels_the_other_fetches(
    target: str, service: str, method: str | None
) -> None:
    client = _client()
    finished: list[int] = []
    entity_type = "person" if service == "persons" else "company"

    async def lookup(entity_id: int, **_kwargs: Any) -> list[int]:
        if entity_id == 1:
            raise ServerError("Internal error", status_code=500)
        await asyncio.sleep(0.05)
        finished.append(entity_id)
        return []

    if method is None:

        async def iterate(**kwargs: Any) -> Any:
            await lookup(int(kwargs["company_id"]))
            if False:  # pragma: no cover - an async generator with no items
                yield None

        client.interactions = MagicMock()
        client.interactions.iter = MagicMock(side_effect=iterate)
    else:
        setattr(client, service, MagicMock())
        setattr(getattr(client, service), method, lookup)
    entries = [{"id": i, "entityId": i, "entityType": entity_type} for i in range(1, 6)]

    async def run() -> None:
        with pytest.raises(ServerError):
            await QueryExecutor(client)._fetch_list_entry_indirect(entries, target)
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert finished == []


# --- CLI output: CSV warnings and shape, a JSON row past the budget, TOON cursor in budget ----


def _run_full(query: dict[str, Any], *args: str, output: str) -> Any:
    return _runner().invoke(
        cli,
        ["query", "--query", json.dumps(query), "--output", output, *args],
        env=ENV,
    )


@pytest.mark.parametrize("budget", [[], ["--max-output-bytes", "100000"]])
def test_csv_prints_execution_warnings(respx_mock: respx.MockRouter, budget: list[str]) -> None:
    _mock_person_pages(respx_mock, list(range(1, 13)), 4)
    result = _run_full(
        {"from": "persons", "select": ["id"]}, "--max-records", "6", *budget, output="csv"
    )
    assert result.exit_code == 0, result.output
    assert "--max-records" in result.stderr


def test_csv_is_the_same_with_and_without_a_budget(respx_mock: respx.MockRouter) -> None:
    _mock_person_pages(respx_mock, [1, 2], 4)
    query = {"from": "persons", "limit": 2, "select": ["id", "firstName", "emailAddresses"]}
    plain = _run_full(query, output="csv")
    budget = _run_full(query, "--max-output-bytes", "100000", output="csv")
    assert plain.exit_code == budget.exit_code == 0
    assert plain.stdout_bytes == budget.stdout_bytes
    # Select order, CRLF records, one terminator at the end
    assert plain.stdout_bytes.startswith(b"id,firstName,emailAddresses\r\n")
    assert plain.stdout_bytes.endswith(b"\r\n") and not plain.stdout_bytes.endswith(b"\r\n\n")


def test_csv_cut_page_ends_with_a_record_terminator(respx_mock: respx.MockRouter) -> None:
    _mock_person_pages(respx_mock, list(range(1, 11)), 10)
    query = {"from": "persons", "limit": 10, "select": ["id", "firstName"]}
    result = _run_full(query, "--max-output-bytes", "60", output="csv")
    assert result.exit_code == 100
    assert result.stdout_bytes.endswith(b"\r\n") and b"\n\n" not in result.stdout_bytes


def _mock_small_then_large(router: respx.MockRouter) -> None:
    def person(pid: int, last_name: str) -> dict[str, Any]:
        return {**_person(pid), "lastName": last_name}

    router.get(url__startswith=f"{V2}/persons").mock(
        return_value=Response(200, json=_page([person(1, "B"), person(2, "X" * 400)]))
    )


def test_json_row_larger_than_the_budget_is_shown_whole(respx_mock: respx.MockRouter) -> None:
    _mock_small_then_large(respx_mock)
    query = {"from": "persons", "limit": 2, "select": ["id", "lastName"]}
    outputs, runs = _page_through(query, max_bytes=200)
    assert runs == 2
    assert _json_ids(outputs) == [1, 2]
    assert "X" * 400 in outputs[1]


def test_toon_page_with_cursor_stays_within_the_budget(respx_mock: respx.MockRouter) -> None:
    # The cursor line (~500 bytes: it names the cache file) is part of the page
    _mock_person_pages(respx_mock, list(range(1, 151)), 200)
    query = {"from": "persons", "limit": 150, "select": ["id", "firstName"]}
    code, out, cursor = _run_query(query, "--max-output-bytes", "1000", output="toon")
    assert code == 100 and cursor
    assert f"cursor: {cursor}" in out
    assert len(out.rstrip("\n").encode()) <= 1000, len(out.encode())
    outputs, _runs = _page_through(query, max_bytes=1000, output="toon")
    counts = [re.search(r"^data\[(\d+)\]", o, re.MULTILINE) for o in outputs]
    assert sum(int(m.group(1)) for m in counts if m) == 150
