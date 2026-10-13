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
    assert out["meta"]["pagination"]["companies"]["nextCursor"].startswith("xsearch1:")


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
