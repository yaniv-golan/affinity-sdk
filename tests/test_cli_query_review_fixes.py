"""CLI query engine, review fixes: the --max-records cut is reported on the streaming path,
include/expand lookups fail the query instead of passing as empty, CSV columns from every
row, Ctrl+C never prints unfiltered rows, cached resume for include queries, truncation
always shows a row, min/max refuse mixed values, cursors carry --max-records, and an
unknown listName is a usage error."""

from __future__ import annotations

import asyncio
import json
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

from affinity.cli.commands.query_cmd import _count_rows_in_output
from affinity.cli.interaction_utils import (
    _resolve_person_names,
    _resolve_person_names_async,
    async_check_unreplied,
    check_unreplied,
)
from affinity.cli.main import cli
from affinity.cli.query.aggregates import compute_max, compute_min
from affinity.cli.query.cursor import hash_query
from affinity.cli.query.exceptions import (
    QueryExecutionError,
    QueryInterruptedError,
    QueryValidationError,
)
from affinity.cli.query.executor import QueryExecutor
from affinity.cli.query.models import ExecutionPlan, PlanStep, Query, QueryResult
from affinity.cli.query.output import (
    format_query_result,
    included_for_rows,
    truncate_csv_output,
    truncate_jsonl_output,
    truncate_markdown_output,
    truncate_toon_output,
)
from affinity.exceptions import (
    AffinityError,
    AuthenticationError,
    CompanyMergedError,
    ConflictError,
    NotFoundError,
    PersonMergedError,
    RateLimitError,
    ServerError,
    ValidationError,
)

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V1 = "https://api.affinity.co"
V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
WHOAMI = {
    "tenant": {"id": 1, "name": "Acme Corp", "subdomain": "acme"},
    "user": {"id": 100, "firstName": "Jane", "lastName": "Doe", "emailAddress": "j@example.com"},
    "grant": {"type": "api_key", "scopes": ["all"], "createdAt": "2024-01-01T00:00:00Z"},
}


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Resume caches go to a per-test directory."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture(autouse=True)
def _whoami(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))


def _person(pid: int, last_name: str = "Doe") -> dict[str, Any]:
    return {
        "id": pid,
        "firstName": "Jane",
        "lastName": f"{last_name} {pid}",
        "primaryEmailAddress": f"jane{pid}@example.com",
        "emailAddresses": [f"jane{pid}@example.com"],
        "type": "external",
    }


def _mock_person_pages(
    router: respx.MockRouter, ids: list[int], size: int, last_name: str = "Doe"
) -> respx.Route:
    """Pages of ``size`` persons, chained by cursor; returns the route (for call counts)."""
    url = f"{V2}/persons"
    pages = [ids[i : i + size] for i in range(0, len(ids), size)]

    def respond(request: Any) -> Response:
        cursor = request.url.params.get("cursor")
        index = int(cursor) if cursor else 0
        nxt = f"{url}?cursor={index + 1}" if index + 1 < len(pages) else None
        data = [_person(i, last_name) for i in pages[index]]
        return Response(200, json={"data": data, "pagination": {"nextUrl": nxt}})

    return router.get(url__regex=rf"^{V2}/persons(\?.*)?$").mock(side_effect=respond)


def _run(query: dict[str, Any], *args: str, output: str = "json") -> tuple[int, str, str | None]:
    """Run the query; (exit code, stdout, cursor from stderr or None)."""
    result = CliRunner().invoke(
        cli,
        ["--max-retries", "0", "query", "--query", json.dumps(query), "--output", output, *args],
        env=ENV,
    )
    cursor = None
    for line in result.stderr.splitlines():
        if line.startswith("{") and '"cursor"' in line:
            cursor = json.loads(line)["cursor"]
    return result.exit_code, result.stdout, cursor


def _page_through(
    query: dict[str, Any], max_bytes: int, *extra: str, output: str = "json"
) -> list[str]:
    """Resume until the end; each run's stdout."""
    outputs: list[str] = []
    cursor: str | None = None
    for _ in range(40):
        args = ["--max-output-bytes", str(max_bytes), *extra]
        if cursor:
            args += ["--cursor", cursor]
        code, out, cursor = _run(query, *args, output=output)
        outputs.append(out)
        if code == 0:
            return outputs
        assert code == 100, out
        assert cursor, "a truncated run gives a cursor"
    raise AssertionError("never finished: a page showed no row")


def _envelope(out: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(out.strip().splitlines()[-1])
    return result


def _client() -> AsyncMock:
    client = AsyncMock()
    client.whoami = AsyncMock(return_value={})
    return client


def _plan(query: Query, *ops: str, relationship: str | None = None) -> ExecutionPlan:
    steps = [PlanStep(step_id=0, operation="fetch", entity=query.from_, description="Fetch")]
    for i, op in enumerate(ops, start=1):
        steps.append(
            PlanStep(
                step_id=i,
                operation=op,  # type: ignore[arg-type]
                entity=query.from_,
                relationship=relationship if op == "include" else None,
                expansion="interactionDates" if op == "expand" else None,
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


def _page(records: list[dict[str, Any]], next_cursor: str | None = None) -> MagicMock:
    page = MagicMock()
    page.data = [MagicMock(model_dump=MagicMock(return_value=dict(r))) for r in records]
    page.next_cursor = next_cursor
    return page


# --- 1: the --max-records cut is reported when streaming ------------------------------------


@pytest.mark.parametrize(
    ("ids", "query_limit", "warned"),
    [
        (list(range(1, 9)), None, True),  # more rows on the page and a next page
        (list(range(1, 6)), None, False),  # exactly --max-records rows exist
        (list(range(1, 9)), 3, False),  # the query's own limit stops it
        (list(range(1, 9)), 20, True),  # a limit above --max-records: the cap stops it
    ],
)
def test_streaming_max_records_cut_is_reported(
    respx_mock: respx.MockRouter, ids: list[int], query_limit: int | None, warned: bool
) -> None:
    _mock_person_pages(respx_mock, ids, size=4)
    query: dict[str, Any] = {"from": "persons", "select": ["id"]}
    if query_limit is not None:
        query["limit"] = query_limit
    code, out, _ = _run(query, "--max-records", "5", "--include-meta")
    assert code == 0, out
    envelope = _envelope(out)
    assert [r["id"] for r in envelope["data"]] == ids[: min(5, query_limit or 5)]
    warnings = envelope.get("warnings", [])
    assert any("(--max-records)" in w and "raise --max-records" in w for w in warnings) is warned
    assert envelope["meta"].get("truncated", False) is warned


def test_streaming_cut_on_a_page_boundary_with_a_next_page_is_reported() -> None:
    client = _client()
    pages = [_page([{"id": i} for i in range(1, 5)], next_cursor="more")]

    async def all_pages(**_kwargs: Any) -> Any:
        for page in pages:
            yield page

    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(pages=all_pages)
    query = Query(from_="persons")
    result = asyncio.run(
        QueryExecutor(client, max_records=4, max_records_explicit=True).execute(_plan(query))
    )
    assert [r["id"] for r in result.data] == [1, 2, 3, 4]
    assert result.meta["truncated"] is True


# --- 2: include and expand lookups fail the query on errors other than not-found ------------


@pytest.mark.parametrize("error", [RateLimitError("slow down"), ServerError("boom")])
def test_batch_include_lookup_errors_propagate(error: Exception) -> None:
    client = _client()

    async def failing_iter(**_kwargs: Any) -> Any:
        raise error
        yield  # pragma: no cover

    client.companies = MagicMock()
    client.companies.iter = failing_iter
    client.companies.get = AsyncMock()
    with pytest.raises(type(error)):
        asyncio.run(QueryExecutor(client)._batch_fetch_by_ids("companies", [1, 2]))
    client.companies.get.assert_not_awaited()


def test_per_id_include_lookup_errors_propagate_and_not_found_is_a_stub() -> None:
    client = _client()

    async def missing_iter(**_kwargs: Any) -> Any:
        raise NotFoundError("gone")
        yield  # pragma: no cover

    async def get(id_: int) -> Any:
        if id_ == 2:
            raise NotFoundError("gone")
        if id_ == 3:
            raise ServerError("boom")
        if id_ == 4:
            raise CompanyMergedError("4 merged into 5", source_id=4, target_id=5)
        return MagicMock(model_dump=MagicMock(return_value={"id": id_, "name": "Acme Corp"}))

    client.companies = MagicMock()
    client.companies.iter = missing_iter
    client.companies.get = get
    executor = QueryExecutor(client)
    found = asyncio.run(executor._batch_fetch_by_ids("companies", [1, 2, 4]))
    # Gone (not found, or merged into another record): an ID-only stub
    assert found == [{"id": 1, "name": "Acme Corp"}, {"id": 2}, {"id": 4}]
    with pytest.raises(ServerError):
        asyncio.run(executor._batch_fetch_by_ids("companies", [1, 3]))


def test_include_server_error_exits_with_affinity_exit_code(respx_mock: respx.MockRouter) -> None:
    _mock_person_pages(respx_mock, [3001], size=4)
    respx_mock.get(url__regex=rf"^{V1}/persons/\d+").mock(
        return_value=Response(200, json={"id": 3001, "organization_ids": [7001]})
    )
    respx_mock.get(url__startswith=f"{V2}/companies").mock(
        return_value=Response(503, json={"errors": [{"code": "x", "message": "unavailable"}]})
    )
    query = {"from": "persons", "limit": 1, "include": ["companies"]}
    code, out, _ = _run(query)
    assert code == 5, out
    assert _envelope(out)["error"]["type"] != "error"


def _expand_client(get: Any) -> AsyncMock:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(pages=_single_page([{"id": 11}]))
    client.persons.get = get
    return client


def _single_page(records: list[dict[str, Any]]) -> Any:
    async def pages(**_kwargs: Any) -> Any:
        yield _page(records)

    return pages


def test_expand_interaction_dates_error_fails_the_query() -> None:
    client = _expand_client(AsyncMock(side_effect=RateLimitError("slow down")))
    query = Query.model_validate({"from": "persons", "expand": ["interactionDates"]})
    with pytest.raises(QueryExecutionError) as exc:
        asyncio.run(QueryExecutor(client).execute(_plan(query, "expand")))
    assert isinstance(exc.value.cause, RateLimitError)


def test_expand_interaction_dates_not_found_is_none() -> None:
    client = _expand_client(AsyncMock(side_effect=NotFoundError("gone")))
    query = Query.model_validate({"from": "persons", "expand": ["interactionDates"]})
    result = asyncio.run(QueryExecutor(client).execute(_plan(query, "expand")))
    assert result.data == [{"id": 11, "interactionDates": None}]


def test_person_name_lookups_reraise_fatal_errors_and_fall_back_on_not_found() -> None:
    sync_client = MagicMock()
    sync_client.persons.get.side_effect = NotFoundError("gone")
    assert _resolve_person_names(sync_client, [5]) == ["Person 5"]
    sync_client.persons.get.side_effect = AuthenticationError("bad key")
    with pytest.raises(AuthenticationError):
        _resolve_person_names(sync_client, [6])

    async_client = MagicMock()
    async_client.persons.get = AsyncMock(side_effect=NotFoundError("gone"))
    assert asyncio.run(_resolve_person_names_async(async_client, [5])) == ["Person 5"]
    async_client.persons.get = AsyncMock(side_effect=ServerError("boom"))
    with pytest.raises(ServerError):
        asyncio.run(_resolve_person_names_async(async_client, [6]))


def test_unreplied_checks_reraise_fatal_errors() -> None:
    sync_client = MagicMock()
    sync_client.interactions.iter.side_effect = RateLimitError("slow down")
    with pytest.raises(RateLimitError):
        check_unreplied(sync_client, "person", 5)
    sync_client.interactions.iter.side_effect = NotFoundError("gone")
    assert check_unreplied(sync_client, "person", 5) is None

    async def failing(**_kwargs: Any) -> Any:
        raise ServerError("boom")
        yield  # pragma: no cover

    async_client = MagicMock()
    async_client.interactions.iter = failing
    with pytest.raises(ServerError):
        asyncio.run(async_check_unreplied(async_client, "person", 5))


# --- 3: CSV columns come from every row; --csv-bom with --max-output-bytes ------------------


def test_csv_header_is_the_union_of_every_rows_keys() -> None:
    result = QueryResult(data=[{"id": 1}, {"id": 2, "name": "Acme Corp"}])
    out = format_query_result(result, "csv")
    assert out.splitlines()[0] == "id,name"
    assert "Acme Corp" in out


def test_csv_bom_is_kept_with_max_output_bytes(respx_mock: respx.MockRouter) -> None:
    _mock_person_pages(respx_mock, [1, 2, 3], size=4)
    result = CliRunner().invoke(
        cli,
        [
            "query",
            "--query",
            json.dumps({"from": "persons", "limit": 3, "select": ["id"]}),
            "--output",
            "csv",
            "--csv-bom",
            "--max-output-bytes",
            "5000",
        ],
        env=ENV,
    )
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes.startswith(b"\xef\xbb\xbf")
    lines = [line for line in result.stdout.lstrip("\ufeff").splitlines() if line]
    assert lines == ["id", "1", "2", "3"]


# --- 4: Ctrl+C returns rows only when they are final-shaped ---------------------------------


def _interrupting_pages(records: list[dict[str, Any]], error: BaseException) -> Any:
    async def pages(**_kwargs: Any) -> Any:
        yield _page(records, next_cursor="more")
        raise error

    return pages


def test_interrupt_before_filter_ran_returns_no_rows() -> None:
    client = _client()
    client.listEntries = None
    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(
        pages=_interrupting_pages(
            [{"id": 1, "score": 0}, {"id": 2, "score": 5}], KeyboardInterrupt()
        )
    )
    query = Query.model_validate(
        {"from": "persons", "where": {"path": "score", "op": "gt", "value": 1}}
    )
    executor = QueryExecutor(client, allow_partial=True)
    with pytest.raises(QueryInterruptedError) as exc:
        asyncio.run(executor.execute(_plan(query, "filter")))
    assert exc.value.partial_results == []

    # Under asyncio.run Ctrl+C arrives as CancelledError: re-raised, no rows returned
    client.persons.all.return_value = MagicMock(
        pages=_interrupting_pages([{"id": 1, "score": 0}], asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(executor.execute(_plan(query, "filter")))


def test_interrupt_during_include_returns_the_filtered_rows() -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(
        pages=_single_page([{"id": 1, "score": 0}, {"id": 2, "score": 5}])
    )
    client.persons.get_associated_company_ids = AsyncMock(side_effect=asyncio.CancelledError())
    query = Query.model_validate(
        {
            "from": "persons",
            "where": {"path": "score", "op": "gt", "value": 1},
            "include": ["companies"],
        }
    )
    result = asyncio.run(
        QueryExecutor(client, allow_partial=True).execute(
            _plan(query, "filter", "include", relationship="companies")
        )
    )
    assert [r["id"] for r in result.data] == [2]
    assert result.meta["interrupted"] is True


def test_interrupt_while_streaming_returns_the_rows_filtered_so_far() -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(
        pages=_interrupting_pages(
            [{"id": 1, "score": 0}, {"id": 2, "score": 5}], asyncio.CancelledError()
        )
    )
    query = Query.model_validate(
        {"from": "persons", "where": {"path": "score", "op": "gt", "value": 1}, "limit": 10}
    )
    result = asyncio.run(QueryExecutor(client, allow_partial=True).execute(_plan(query)))
    assert [r["id"] for r in result.data] == [2]
    assert result.meta["interrupted"] is True


def test_interrupt_without_final_rows_exits_130_with_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def interrupted(_self: Any, _plan: Any) -> Any:
        raise QueryInterruptedError("stopped", partial_results=[])

    monkeypatch.setattr(QueryExecutor, "execute", interrupted)
    code, out, _ = _run({"from": "persons", "limit": 5})
    assert code == 130
    envelope = _envelope(out)
    assert envelope["error"]["type"] == "interrupted"
    assert envelope["data"] is None


# --- 5 / 8: include queries resume from the cache; cursors carry --max-records --------------


def _mock_companies(router: respx.MockRouter) -> respx.Route:
    def respond(request: Any) -> Response:
        ids = [int(i) for i in request.url.params.get_list("ids")]
        data = [
            {"id": i, "name": f"Acme Corp {i}", "domain": None, "domains": [], "isGlobal": False}
            for i in ids
        ]
        return Response(200, json={"data": data, "pagination": {"nextUrl": None}})

    return router.get(url__startswith=f"{V2}/companies").mock(side_effect=respond)


def test_include_query_resumes_from_cache_with_each_pages_included(
    respx_mock: respx.MockRouter,
) -> None:
    ids = list(range(3001, 3007))
    persons = _mock_person_pages(respx_mock, ids, size=4)
    person_details = respx_mock.get(url__regex=rf"^{V1}/persons/\d+").mock(
        side_effect=lambda req: Response(
            200,
            json={
                "id": int(req.url.path.rsplit("/", 1)[-1]),
                "organization_ids": [int(req.url.path.rsplit("/", 1)[-1]) + 4000],
            },
        )
    )
    companies = _mock_companies(respx_mock)
    query = {"from": "persons", "limit": 6, "select": ["id"], "include": ["companies"]}

    code, first, cursor = _run(query, "--max-output-bytes", "700")
    assert code == 100 and cursor
    calls = (persons.call_count, person_details.call_count, companies.call_count)

    outputs = [first]
    while cursor:
        code, out, cursor = _run(query, "--max-output-bytes", "700", "--cursor", cursor)
        assert code in (0, 100), out
        outputs.append(out)
    # Resumed pages come from the cache: no API calls
    assert (persons.call_count, person_details.call_count, companies.call_count) == calls

    assert len(outputs) > 2
    seen: list[int] = []
    for out in outputs:
        envelope = _envelope(out)
        page_ids = [r["id"] for r in envelope["data"]]
        seen.extend(page_ids)
        # Each page carries the companies of its own rows
        assert {c["id"] for c in envelope["included"]["companies"]} == {
            pid + 4000 for pid in page_ids
        }
    assert seen == ids


def test_cached_pages_repeat_the_max_records_warning(respx_mock: respx.MockRouter) -> None:
    ids = list(range(1, 13))
    route = _mock_person_pages(respx_mock, ids, size=4)
    query = {"from": "persons", "select": ["id"]}
    args = ["--max-records", "6", "--include-meta"]
    outputs = _page_through(query, 300, *args)
    assert len(outputs) > 1
    assert route.call_count == 2
    for out in outputs:
        envelope = _envelope(out)
        assert envelope["meta"]["truncated"] is True
        assert any("(--max-records)" in w for w in envelope["warnings"])
    assert sum(len(_envelope(o)["data"]) for o in outputs) == 6


def test_cursor_is_refused_with_a_different_max_records(respx_mock: respx.MockRouter) -> None:
    _mock_person_pages(respx_mock, list(range(1, 13)), size=4)
    query = {"from": "persons", "select": ["id"]}
    code, _out, cursor = _run(query, "--max-records", "6", "--max-output-bytes", "150")
    assert code == 100 and cursor
    code, out, _ = _run(
        query, "--max-records", "8", "--max-output-bytes", "150", "--cursor", cursor
    )
    assert code != 0
    assert "--max-records" in _envelope(out)["error"]["message"]


def test_hash_query_includes_max_records() -> None:
    query = Query(from_="persons")
    assert hash_query(query, "json", max_records=5) != hash_query(query, "json", max_records=6)
    assert hash_query(query, "json") != hash_query(query, "json", max_records=5)


def test_included_for_rows_keeps_the_rows_related_records() -> None:
    included = {"companies": [{"id": 7}, {"id": 8}], "notes": [{"id": 1}]}
    by_parent = {"companies": {1: [{"id": 7}], 2: [{"id": 8}]}}
    assert included_for_rows([{"id": 2}], included, by_parent) == {
        "companies": [{"id": 8}],
        "notes": [{"id": 1}],
    }
    # Rows without an id can't be matched: everything is kept
    assert included_for_rows([{"name": "Acme Corp"}], included, by_parent) == included


# --- 6: a page always shows at least one row ------------------------------------------------


def test_truncation_keeps_a_first_record_larger_than_the_budget() -> None:
    big = "x" * 200
    csv_out, cut = truncate_csv_output(f"id,note\n1,{big}\n2,b\n", 50)
    assert cut and _count_rows_in_output(csv_out, "csv") == 1
    jsonl_out, cut = truncate_jsonl_output(f'{{"id":1,"n":"{big}"}}\n{{"id":2}}\n', 50)
    assert cut and _count_rows_in_output(jsonl_out, "jsonl") == 1
    md = f"| id | n |\n| --- | --- |\n| 1 | {big} |\n| 2 | b |"
    md_out, cut = truncate_markdown_output(md, 50)
    assert cut and _count_rows_in_output(md_out, "markdown") == 1
    toon = f"data[2]{{id,n}}:\n  1,{big}\n  2,b"
    toon_out, cut = truncate_toon_output(toon, 50)
    assert cut and _count_rows_in_output(toon_out, "toon") == 1
    # The envelope alone is past the budget: the first row is still shown
    toon = f"data[2]{{id,n}}:\n  1,a\n  2,b\nincluded_companies[1]{{id,name}}:\n  9,{big}"
    toon_out, cut = truncate_toon_output(toon, 50)
    assert cut and _count_rows_in_output(toon_out, "toon") == 1
    assert "rowsOmitted: 1" in toon_out


@pytest.mark.parametrize("output", ["csv", "jsonl", "toon", "markdown", "json"])
def test_paging_rows_larger_than_the_budget_finishes(
    respx_mock: respx.MockRouter, output: str
) -> None:
    ids = [1, 2, 3]
    _mock_person_pages(respx_mock, ids, size=4, last_name="Doe" * 80)
    query = {"from": "persons", "limit": 3, "select": ["id", "lastName"]}
    outputs = _page_through(query, 150, output=output)
    assert sum(_count_rows_in_output(o.rstrip("\n"), output) for o in outputs) == 3


# --- 7: min/max refuse numbers mixed with text ----------------------------------------------


def test_min_max_refuse_numbers_mixed_with_text() -> None:
    with pytest.raises(QueryValidationError, match=r"max on 'v'.*1 are numbers and 1 are text"):
        compute_max([{"v": 5}, {"v": "n/a"}], "v")
    assert compute_min([{"v": 5}, {"v": 2.5}, {"v": None}], "v") == 2.5
    assert compute_max([{"v": "2026-02-01"}, {"v": "2025-01-01"}], "v") == "2026-02-01"


# --- 9: an unknown listName is a usage error (exit 2) ---------------------------------------


def test_unknown_list_name_exits_2(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{V2}/lists").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 9,
                        "name": "Deals",
                        "type": "company",
                        "isPublic": True,
                        "ownerId": 100,
                        "creatorId": 100,
                    }
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )
    query = {"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Leads"}}
    code, out, _ = _run(query)
    assert code == 2, out
    error = _envelope(out)["error"]
    assert error["type"] == "validation_error"
    assert "List not found: 'Leads'" in error["message"]


# --- the --max-records stop is reported on the non-streaming fetch too ------------------------


@pytest.mark.parametrize(
    ("count", "query_limit", "warned"),
    [
        (8, None, True),  # more rows than --max-records
        (5, None, False),  # exactly --max-records rows exist
        (8, 3, False),  # the query's own limit stops it
    ],
)
def test_plain_fetch_reports_the_max_records_stop(
    count: int, query_limit: int | None, warned: bool
) -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(
        pages=_single_page([{"id": i} for i in range(1, count + 1)])
    )
    spec: dict[str, Any] = {"from": "persons"}
    if query_limit is not None:
        spec["limit"] = query_limit
    query = Query.model_validate(spec)
    result = asyncio.run(QueryExecutor(client, max_records=5).execute(_plan(query)))
    assert len(result.data) == (query_limit or 5)
    assert bool(result.meta.get("truncated")) is warned
    assert any("--max-records" in w for w in result.warnings) is warned


# --- concurrent fetches: the first error stops the rest, none is left unretrieved ------------


def test_gather_or_raise_returns_results_in_order() -> None:
    from affinity.cli.async_utils import gather_or_raise

    async def value(v: int, delay: float) -> int:
        await asyncio.sleep(delay)
        return v

    assert asyncio.run(gather_or_raise(value(1, 0.02), value(2, 0))) == [1, 2]
    assert asyncio.run(gather_or_raise()) == []


def test_gather_or_raise_cancels_the_rest_and_leaves_no_unretrieved_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from affinity.cli.async_utils import gather_or_raise

    finished: list[str] = []

    async def fail_fast() -> None:
        raise RateLimitError("slow down")

    async def fail_later() -> None:
        await asyncio.sleep(0)
        raise ServerError("boom")

    async def slow() -> None:
        await asyncio.sleep(1)
        finished.append("slow")

    async def main() -> None:
        with pytest.raises(RateLimitError):
            await gather_or_raise(fail_fast(), fail_later(), slow())

    with caplog.at_level("ERROR", logger="asyncio"):
        asyncio.run(main())
    assert finished == []
    assert "never retrieved" not in caplog.text


# --- second review: TOON pages trim included to their rows ----------------------------------


def _toon_sections(out: str) -> dict[str, list[list[str]]]:
    """Each TOON tabular section's rows (cells split on commas), checking header counts."""
    sections: dict[str, list[list[str]]] = {}
    name: str | None = None
    declared: dict[str, int] = {}
    for line in out.splitlines():
        if line.startswith("  ") and name is not None:
            sections[name].append(line.strip().split(","))
            continue
        name = None
        header = line.split("{", 1)[0]
        if "[" in header and header.endswith("]") and line.endswith(":"):
            name, count = header[:-1].split("[")
            declared[name] = int(count)
            sections[name] = []
    for section, rows in sections.items():
        assert declared[section] == len(rows), (section, out)
    return sections


def test_toon_pages_carry_their_rows_included_and_fill_the_budget(
    respx_mock: respx.MockRouter,
) -> None:
    ids = list(range(3001, 3007))
    _mock_person_pages(respx_mock, ids, size=4)
    respx_mock.get(url__regex=rf"^{V1}/persons/\d+").mock(
        side_effect=lambda req: Response(
            200,
            json={
                "id": int(req.url.path.rsplit("/", 1)[-1]),
                "organization_ids": [int(req.url.path.rsplit("/", 1)[-1]) + 4000],
            },
        )
    )
    _mock_companies(respx_mock)
    query = {"from": "persons", "limit": 6, "select": ["id"], "include": ["companies"]}

    outputs = _page_through(query, 400, output="toon")
    seen: list[int] = []
    page_sizes: list[int] = []
    for out in outputs:
        sections = _toon_sections(out)
        page_ids = [int(row[0]) for row in sections["data"]]
        seen.extend(page_ids)
        page_sizes.append(len(page_ids))
        # Each page carries the companies of its own rows, whole
        company_ids = {int(row[0]) for row in sections.get("included_companies", [])}
        assert company_ids == {pid + 4000 for pid in page_ids}, out
        # Within the budget, apart from the cursor line added for humans
        body = "\n".join(line for line in out.splitlines() if not line.startswith("  cursor:"))
        assert len(body.encode()) <= 400 or len(page_ids) == 1, out
    assert seen == ids
    # Pages hold as many rows as fit, not one each
    assert max(page_sizes) > 1
    assert len(outputs) < len(ids)


def test_toon_without_included_keeps_text_truncation() -> None:
    from affinity.cli.query.output import truncate_toon_result

    result = QueryResult(data=[{"id": i, "name": f"Acme Corp {i}"} for i in range(1, 30)])
    out, cut = truncate_toon_result(result, 200)
    assert cut
    sections = _toon_sections(out.replace("truncation:", "truncation"))
    assert 1 <= len(sections["data"]) < 29
    assert len(result.data) == 29  # the result is not modified


def test_toon_single_row_with_large_included_is_shown_whole() -> None:
    from affinity.cli.query.output import truncate_toon_result

    result = QueryResult(
        data=[{"id": 1}, {"id": 2}],
        included={"companies": [{"id": 10 + i, "name": "Acme Corp " * 5} for i in range(6)]},
        included_by_parent={
            "companies": {1: [{"id": 10 + i} for i in range(3)], 2: [{"id": 13}]},
        },
    )
    out, cut = truncate_toon_result(result, 100)
    assert cut
    sections = _toon_sections(out)
    assert [row[0] for row in sections["data"]] == ["1"]
    assert [row[0] for row in sections["included_companies"]] == ["10", "11", "12"]
    assert "rowsShown: 1" in out and "rowsOmitted: 1" in out


def test_toon_one_row_with_large_included_is_shown_whole_and_not_cut() -> None:
    from affinity.cli.query.output import truncate_toon_result

    result = QueryResult(
        data=[{"id": 1}],
        included={"companies": [{"id": 10 + i, "name": "Acme Corp " * 5} for i in range(6)]},
        included_by_parent={"companies": {1: [{"id": 10 + i} for i in range(6)]}},
    )
    out, cut = truncate_toon_result(result, 100)
    assert not cut
    sections = _toon_sections(out)
    assert len(sections["included_companies"]) == 6


# --- second review: --max-records is reported only when more rows may exist ------------------


def test_plain_fetch_max_records_at_a_page_end_with_a_next_page_is_a_cut() -> None:
    client = _client()

    async def pages(**_kwargs: Any) -> Any:
        yield _page([{"id": i} for i in range(1, 6)], next_cursor="more")
        yield _page([{"id": 6}])  # pragma: no cover

    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(pages=pages)
    query = Query.model_validate({"from": "persons"})
    result = asyncio.run(QueryExecutor(client, max_records=5).execute(_plan(query)))
    assert len(result.data) == 5
    assert result.meta.get("truncated")
    assert any("--max-records" in w for w in result.warnings)


@pytest.mark.parametrize(("count", "warned"), [(5, False), (6, True)])
def test_record_iterator_fetch_reports_a_cut_only_when_a_record_is_past_it(
    count: int, warned: bool
) -> None:
    from affinity.cli.query.executor import ExecutionContext

    class Records:
        """A service with neither pages() nor all().pages(): records one by one."""

        def all(self, **_kwargs: Any) -> Any:
            async def records() -> Any:
                for i in range(1, count + 1):
                    yield MagicMock(model_dump=MagicMock(return_value={"id": i}))

            return records()

    query = Query.model_validate(
        {"from": "listEntries", "where": {"path": "listId", "op": "eq", "value": 12345}}
    )
    ctx = ExecutionContext(query=query, max_records=5)
    step = PlanStep(step_id=0, operation="fetch", entity="listEntries", description="Fetch")
    asyncio.run(
        QueryExecutor(_client(), max_records=5)._fetch_from_single_parent(
            step, ctx, lambda _pid: Records(), 12345
        )
    )
    assert [r["id"] for r in ctx.records] == [1, 2, 3, 4, 5]
    assert ctx.max_records_cut is warned


# --- second review: unreplied and name lookups fall back only for a gone record --------------


@pytest.mark.parametrize(
    "error",
    [
        ValidationError("bad request"),
        ConflictError("conflict"),
        AffinityError("unmapped status"),
        ValueError("unexpected payload"),
    ],
)
def test_lookups_propagate_every_error_but_a_gone_record(error: Exception) -> None:
    sync_client = MagicMock()
    sync_client.interactions.iter.side_effect = error
    with pytest.raises(type(error)):
        check_unreplied(sync_client, "person", 5)
    sync_client.persons.get.side_effect = error
    with pytest.raises(type(error)):
        _resolve_person_names(sync_client, [5])

    async def failing(**_kwargs: Any) -> Any:
        raise error
        yield  # pragma: no cover

    async_client = MagicMock()
    async_client.interactions.iter = failing
    with pytest.raises(type(error)):
        asyncio.run(async_check_unreplied(async_client, "person", 5))
    async_client.persons.get = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        asyncio.run(_resolve_person_names_async(async_client, [5]))


def test_lookups_read_a_merged_record_as_gone() -> None:
    merged = PersonMergedError("5 merged into 6", source_id=5, target_id=6)
    sync_client = MagicMock()
    sync_client.interactions.iter.side_effect = merged
    assert check_unreplied(sync_client, "person", 5) is None
    sync_client.persons.get.side_effect = merged
    assert _resolve_person_names(sync_client, [5]) == ["Person 5"]


def test_query_unreplied_expansion_error_fails_the_query() -> None:
    client = _client()
    client.listEntries = MagicMock()

    async def failing(**_kwargs: Any) -> Any:
        raise ValidationError("bad request")
        yield  # pragma: no cover

    client.interactions = MagicMock()
    client.interactions.iter = failing
    ctx_records = [{"id": 1, "entityId": 5, "entityType": "person"}]
    executor = QueryExecutor(client)
    query = Query.model_validate(
        {"from": "listEntries", "where": {"path": "listId", "op": "eq", "value": 12345}}
    )
    from affinity.cli.query.executor import ExecutionContext
    from affinity.cli.query.schema import EXPANSION_REGISTRY

    ctx = ExecutionContext(query=query, records=[dict(r) for r in ctx_records])
    step = PlanStep(step_id=1, operation="expand", entity="listEntries", description="expand")
    with pytest.raises(ValidationError):
        asyncio.run(executor._expand_list_entries(step, ctx, EXPANSION_REGISTRY["unreplied"]))


# --- second review: list field metadata failures fail the query ------------------------------


def test_list_field_metadata_failure_fails_the_query(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__regex=rf"^{V2}/lists/12345/fields").mock(
        return_value=Response(503, json={"errors": [{"code": "x", "message": "unavailable"}]})
    )
    query = {
        "from": "listEntries",
        "where": {
            "and": [
                {"path": "listId", "op": "eq", "value": 12345},
                {"path": "fields.Status", "op": "is_null"},
            ]
        },
    }
    code, out, _ = _run(query)
    assert code == 5, out
    assert _envelope(out)["data"] is None


# --- second review: a merged record reads as gone in include and pre-include -----------------


def test_include_reads_a_merged_parent_as_nothing_related() -> None:
    client = _client()
    client.persons = MagicMock()
    client.persons.all.return_value = MagicMock(pages=_single_page([{"id": 11}]))
    client.persons.get_associated_company_ids = AsyncMock(
        side_effect=PersonMergedError("11 merged into 12", source_id=11, target_id=12)
    )
    query = Query.model_validate({"from": "persons", "include": ["companies"]})
    result = asyncio.run(
        QueryExecutor(client).execute(_plan(query, "include", relationship="companies"))
    )
    assert result.data == [{"id": 11}]
    assert not result.included.get("companies")


def test_pre_include_reads_a_merged_record_as_no_relationships() -> None:
    from affinity.cli.query.executor import ExecutionContext
    from affinity.cli.query.schema import SCHEMA_REGISTRY

    client = _client()
    client.persons = MagicMock()
    client.persons.get_associated_company_ids = AsyncMock(
        side_effect=PersonMergedError("11 merged into 12", source_id=11, target_id=12)
    )
    query = Query.model_validate({"from": "persons"})
    ctx = ExecutionContext(query=query, records=[{"id": 11}])
    executor = QueryExecutor(client)
    asyncio.run(executor._execute_pre_include(ctx, {"companies"}, SCHEMA_REGISTRY["persons"]))
    assert ctx.relationship_data["companies"] == {11: []}


# --- third review: a listEntries query reads list fields only when it names one --------------


def test_list_entries_without_fields_does_not_read_the_list_fields(
    respx_mock: respx.MockRouter,
) -> None:
    fields = respx_mock.get(url__regex=rf"^{V2}/lists/12345/fields").mock(
        return_value=Response(503, json={"errors": [{"code": "x", "message": "unavailable"}]})
    )
    respx_mock.get(url__regex=rf"^{V2}/lists/12345/list-entries").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "type": "person",
                        "listId": 12345,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "entity": {"id": 100, "type": "person", "firstName": "Jane"},
                    }
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )
    query = {"from": "listEntries", "where": {"path": "listId", "op": "eq", "value": 12345}}
    code, out, _ = _run(query)
    assert code == 0, out
    assert [r["id"] for r in _envelope(out)["data"]] == [1]
    assert not fields.called


# --- third review: paging with include when select leaves id out ----------------------------


def _id_less_include_setup(router: respx.MockRouter, ids: list[int]) -> None:
    _mock_person_pages(router, ids, size=4)
    router.get(url__regex=rf"^{V1}/persons/\d+").mock(
        side_effect=lambda req: Response(
            200,
            json={
                "id": int(req.url.path.rsplit("/", 1)[-1]),
                "organization_ids": [int(req.url.path.rsplit("/", 1)[-1]) + 4000],
            },
        )
    )
    _mock_companies(router)


@pytest.mark.parametrize("output", ["toon", "json"])
def test_id_less_select_pages_carry_their_rows_included(
    respx_mock: respx.MockRouter, output: str
) -> None:
    ids = list(range(3001, 3009))
    _id_less_include_setup(respx_mock, ids)
    query = {"from": "persons", "limit": 8, "select": ["lastName"], "include": ["companies"]}
    budget = 450 if output == "toon" else 900  # a JSON company record is ~260 bytes

    outputs = _page_through(query, budget, output=output)
    seen: list[int] = []
    page_sizes: list[int] = []
    for out in outputs:
        if output == "toon":
            sections = _toon_sections(out)
            page_ids = [int(row[0].rsplit(" ", 1)[-1]) for row in sections["data"]]
            company_ids = {int(row[0]) for row in sections.get("included_companies", [])}
            body = "\n".join(line for line in out.splitlines() if not line.startswith("  cursor:"))
        else:
            envelope = _envelope(out)
            assert all("id" not in row for row in envelope["data"])
            page_ids = [int(r["lastName"].rsplit(" ", 1)[-1]) for r in envelope["data"]]
            company_ids = {c["id"] for c in envelope.get("included", {}).get("companies", [])}
            body = out.strip()
        seen.extend(page_ids)
        page_sizes.append(len(page_ids))
        # Each page carries the companies of its own rows, and stays within the budget
        assert company_ids == {pid + 4000 for pid in page_ids}, out
        assert len(body.encode()) <= budget, out
    assert seen == ids
    assert max(page_sizes) > 1
    assert len(outputs) < len(ids)


def test_toon_uncorrelated_included_is_trimmed_to_the_budget() -> None:
    from affinity.cli.query.output import insert_cursor_in_toon_truncation, truncate_toon_result

    # No per-parent mapping: the included records can't be matched to rows
    result = QueryResult(
        data=[{"name": f"Acme Corp {i}"} for i in range(1, 4)],
        included={"companies": [{"id": 10 + i, "name": "Acme Corp " * 3} for i in range(20)]},
    )
    out, cut = truncate_toon_result(result, 400)
    assert cut
    assert len(out.encode()) <= 400
    sections = _toon_sections(out)
    assert len(sections["data"]) == 1
    kept = len(sections.get("included_companies", []))
    assert kept < 20
    assert f"includedOmitted: {20 - kept}" in out
    assert _count_rows_in_output(out, "toon") == 1
    with_cursor = insert_cursor_in_toon_truncation(out, "abc")
    assert "rowsOmitted: 2\n  cursor: abc\n  includedOmitted:" in with_cursor


# --- third review: include interactions fails on any error but a gone entity -----------------


def _interaction_service(error: Exception) -> MagicMock:
    def iter_(**_kwargs: Any) -> Any:
        async def records() -> Any:
            raise error
            yield  # pragma: no cover

        return records()

    service = MagicMock()
    service.iter = iter_
    return service


def _pydantic_error() -> Exception:
    import pydantic

    class Model(pydantic.BaseModel):
        id: int

    try:
        Model.model_validate({"id": "not a number"})
    except pydantic.ValidationError as e:
        return e
    raise AssertionError("no validation error")  # pragma: no cover


@pytest.mark.parametrize(
    "error",
    [ServerError("unavailable"), RateLimitError("slow down"), _pydantic_error()],
    ids=["server", "rate-limit", "pydantic"],
)
def test_interactions_include_propagates_every_error_but_a_gone_entity(error: Exception) -> None:
    from affinity.cli.query.executor import _fetch_interactions_for_entity

    with pytest.raises(type(error)):
        asyncio.run(_fetch_interactions_for_entity(_interaction_service(error), {"person_id": 5}))


@pytest.mark.parametrize(
    "error",
    [NotFoundError("gone"), PersonMergedError("5 merged into 6", source_id=5, target_id=6)],
    ids=["not-found", "merged"],
)
def test_interactions_include_reads_a_gone_entity_as_none(error: Exception) -> None:
    from affinity.cli.query.executor import _fetch_interactions_for_entity

    service = _interaction_service(error)
    assert asyncio.run(_fetch_interactions_for_entity(service, {"person_id": 5})) == []
