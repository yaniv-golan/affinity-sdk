"""CLI tests: company ls / person ls --filter go through Affinity's search."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
NUMBER_OPS = ["is-greater-than", "is-less-than", "is-equal-to", "is-not-empty", "is-empty"]


def _field(fid: str, name: str, kind: str, value_type: str, ops: list[str]) -> dict[str, Any]:
    return {
        "id": fid,
        "name": name,
        "type": kind,
        "valueType": value_type,
        "enrichmentSource": "affinity-data" if kind == "enriched" else None,
        "filterability": {
            "filterableFieldType": "field-only",
            "operators": [{"id": o, "name": o, "numberOfValuesRequired": "one"} for o in ops],
        },
    }


FIELDS = [
    _field("field-1", "Deals", "global", "number", NUMBER_OPS),
    _field("affinity-data-employees", "Employees", "enriched", "number", NUMBER_OPS),
]


def _company(cid: int, deals: int | None, employees: int | None = None) -> dict[str, Any]:
    fields = [
        {
            "id": "field-1",
            "name": "Deals",
            "type": "global",
            "value": {"type": "number", "data": deals},
        }
    ]
    if employees is not None:
        fields.append(
            {
                "id": "affinity-data-employees",
                "name": "Employees",
                "type": "enriched",
                "value": {"type": "number", "data": employees},
            }
        )
    return {
        "id": cid,
        "name": f"Co {cid}",
        "domain": None,
        "domains": [],
        "isGlobal": False,
        "fields": fields,
    }


def _page(items: list[Any], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _run(*args: str) -> tuple[int, Any]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


SEARCH = f"{V2}/companies/search"


def test_company_ls_filter_searches_on_affinity(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/companies/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    route = respx_mock.post(SEARCH).mock(
        return_value=Response(201, json=_page([_company(1, 7), _company(2, 3)], f"{SEARCH}?c=2"))
    )
    code, out = _run("company", "ls", "--filter", "Deals > 5")
    assert code == 0, out
    assert [c["id"] for c in out["data"]["companies"]] == [1]  # the local re-check trims
    body = json.loads(route.calls[0].request.content)
    assert body["filters"]["filters"][0] == {
        "valueType": "number",
        "fieldId": "field-1",
        "operator": "is-greater-than",
        "value": 5,
    }
    assert route.calls[0].request.url.params.get_list("fieldIds") == ["field-1"]
    assert out["meta"]["pagination"]["companies"]["nextCursor"].startswith("xsearch2:")


def test_enriched_filters_carry_a_caveat(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/companies/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    route = respx_mock.post(SEARCH).mock(
        return_value=Response(201, json=_page([_company(1, 0, 500)]))
    )
    code, out = _run("company", "ls", "--filter", "Employees > 100")
    assert code == 0, out
    first = json.loads(route.calls[0].request.content)["filters"]["filters"][0]
    assert first["operator"] == "is-greater-than"  # sent as is: no "or empty" here
    assert any("can miss" in w for w in out["warnings"])


def test_a_filter_with_no_field_condition_is_refused_early(respx_mock: respx.MockRouter) -> None:
    fields = respx_mock.get(f"{V2}/companies/fields")
    code, out = _run("company", "ls", "--filter", 'name =~ "Acme"')
    assert code == 2 and out["error"]["type"] == "unsupported_filter"
    assert not fields.called


def test_person_ls_filter(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/persons/fields").mock(return_value=Response(200, json=_page([FIELDS[0]])))
    person = {
        "id": 5,
        "firstName": "Dana",
        "lastName": "Levi",
        "primaryEmailAddress": "d@a.co",
        "emailAddresses": ["d@a.co"],
        "type": "external",
        "fields": [
            {
                "id": "field-1",
                "name": "Deals",
                "type": "global",
                "value": {"type": "number", "data": 9},
            }
        ],
    }
    respx_mock.post(f"{V2}/persons/search").mock(return_value=Response(201, json=_page([person])))
    code, out = _run("person", "ls", "--filter", 'Deals > 5 & lastName = "Levi"', "--all")
    assert code == 0, out
    assert [p["id"] for p in out["data"]["persons"]] == [5]


WHOAMI = {
    "tenant": {"id": 1, "name": "Test", "subdomain": "test"},
    "user": {"id": 100, "firstName": "T", "lastName": "U", "emailAddress": "t@example.com"},
    "grant": {"type": "api_key", "scopes": ["all"], "createdAt": "2024-01-01T00:00:00Z"},
}


def _query(query: dict[str, Any]) -> tuple[int, Any]:
    args = ["--json", "query", "--include-meta", "--query", json.dumps(query)]
    result = CliRunner().invoke(cli, args, env=ENV)
    line = next(x for x in result.stdout.splitlines() if x.startswith('{"data"'))
    return result.exit_code, json.loads(line)


def test_query_companies_by_field(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))
    respx_mock.get(f"{V2}/companies/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    route = respx_mock.post(SEARCH).mock(
        return_value=Response(201, json=_page([_company(1, 7), _company(2, 3)]))
    )
    code, out = _query(
        {
            "from": "companies",
            "where": {
                "and": [
                    {"path": "fields.Deals", "op": "gt", "value": 5},
                    {"path": "name", "op": "contains", "value": "Co"},
                ]
            },
            "select": ["id", "fields.Deals"],
        }
    )
    assert code == 0, out
    assert out["data"] == [{"id": 1, "fields": {"Deals": 7}}]
    assert route.calls[0].request.url.params.get_list("fieldIds") == ["field-1"]
    assert out["meta"]["serverFilter"]["local"]  # name contains stays local


def test_query_companies_without_a_field_condition_is_refused() -> None:
    query = {"from": "companies", "where": {"path": "name", "op": "contains", "value": "Acme"}}
    result = CliRunner().invoke(cli, ["--json", "query", "--query", json.dumps(query)], env=ENV)
    assert result.exit_code != 0
    message = json.loads(result.stdout)["error"]["message"]
    assert "fields.<name>" in message and "xaffinity company ls" in message


def test_local_sort_on_companies_hides_its_raw_values(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))
    respx_mock.get(f"{V2}/companies/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    respx_mock.post(SEARCH).mock(
        return_value=Response(201, json=_page([_company(1, 3), _company(2, 9), _company(3, 7)]))
    )
    code, out = _query(
        {
            "from": "companies",
            "where": {"path": "fields.Deals", "op": "gt", "value": 0},
            "orderBy": [{"field": "fields.Deals", "direction": "desc"}],
        }
    )
    assert code == 0, out
    assert [c["id"] for c in out["data"]] == [2, 3, 1]
    assert all("_raw_fields" not in c for c in out["data"])


def _json_in(stdout: str) -> Any:
    """The JSON document in stdout (after any [warning] lines), one-line or pretty-printed."""
    lines = stdout.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("{") and not line.startswith('{"type"')
    )
    return json.loads("\n".join(lines[start:]) if lines[start] == "{" else lines[start])


QUANTIFIER = {
    "from": "companies",
    "where": {
        "and": [
            {"path": "fields.Deals", "op": "gt", "value": 0},
            {"exists": {"from": "interactions"}},
        ]
    },
}


def _with_total(items: list[Any], total: int | None) -> dict[str, Any]:
    page = _page(items)
    if total is not None:
        page["pagination"]["totalCount"] = total
    return page


@pytest.mark.parametrize("total", [150, None])
def test_quantifier_query_stops_when_search_matches_too_many(
    respx_mock: respx.MockRouter, total: int | None
) -> None:
    respx_mock.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))
    respx_mock.get(f"{V2}/companies/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    route = respx_mock.post(SEARCH).mock(
        return_value=Response(201, json=_with_total([_company(1, 3)], total))
    )
    result = CliRunner().invoke(
        cli, ["--json", "query", "--query", json.dumps(QUANTIFIER)], env=ENV
    )
    assert result.exit_code == 2, result.stdout
    error = _json_in(result.stdout)["error"]
    assert error["type"] == "validation_error"
    assert "--max-records" in error["message"]
    assert (str(total) in error["message"]) if total else "didn't say" in error["message"]
    assert route.calls[0].request.url.params["totalCount"] == "true"


def test_quantifier_dry_run_counts_instead_of_unbounded() -> None:
    args = ["--json", "query", "--dry-run", "--query", json.dumps(QUANTIFIER)]
    out = _json_in(CliRunner().invoke(cli, args, env=ENV).stdout)
    assert out["execution"]["requiresExplicitMaxRecords"] is False
    assert isinstance(out["execution"]["estimatedApiCalls"], int)
    explicit = CliRunner().invoke(cli, [*args, "--max-records", "50"], env=ENV).stdout
    assert isinstance(_json_in(explicit)["execution"]["estimatedApiCalls"], int)


def test_quantifier_guard_returns_count_check() -> None:
    from affinity.cli.query.executor import validate_quantifier_query
    from affinity.cli.query.parser import parse_query

    query = parse_query(QUANTIFIER).query
    assert validate_quantifier_query(query, 10000, False, count_check_possible=True) is True
    assert validate_quantifier_query(query, 10000, True, count_check_possible=True) is False
