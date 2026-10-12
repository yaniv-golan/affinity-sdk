"""CLI tests: query on list entries lets Affinity's search filter what it can."""

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
OPS = ["is-any-of", "is-none-of", "is-empty", "is-not-empty"]
FIELDS = [
    {
        "id": "field-1",
        "name": "Status",
        "type": "list",
        "valueType": "ranked-dropdown",
        "enrichmentSource": None,
        "filterability": {
            "filterableFieldType": "field-only",
            "operators": [{"id": o, "name": o, "numberOfValuesRequired": "multi"} for o in OPS],
        },
        "sortability": {"sortableFieldType": "field-only"},
    }
]
OPTIONS = [
    {"id": 11, "text": "Lead", "rank": 1, "color": None},
    {"id": 12, "text": "Won", "rank": 2, "color": None},
]


def _entry(entry_id: int, status: str | None) -> dict[str, Any]:
    value = (
        None
        if status is None
        else {"type": "ranked-dropdown", "data": {"dropdownOptionId": 12, "text": status}}
    )
    return {
        "id": entry_id,
        "listId": 9,
        "type": "company",
        "createdAt": "2026-01-01T00:00:00Z",
        "creatorId": 1,
        "entity": {
            "id": 100 + entry_id,
            "name": f"Co {entry_id}",
            "domain": None,
            "domains": [],
            "isGlobal": False,
            "fields": [{"id": "field-1", "name": "Status", "type": "list", "value": value}],
        },
    }


def _page(items: list[Any], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


WHOAMI = {
    "tenant": {"id": 1, "name": "Test", "subdomain": "test"},
    "user": {"id": 100, "firstName": "T", "lastName": "U", "emailAddress": "t@example.com"},
    "grant": {"type": "api_key", "scopes": ["all"], "createdAt": "2024-01-01T00:00:00Z"},
}


@pytest.fixture(autouse=True)
def _whoami(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/auth/whoami").mock(return_value=Response(200, json=WHOAMI))


def _mock_metadata(router: respx.MockRouter) -> None:
    router.get(f"{V2}/lists/9/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    router.get(url__regex=r".*/lists/9/fields/field-1/dropdown-options.*").mock(
        return_value=Response(200, json=_page(OPTIONS))
    )


def _query(where: dict[str, Any], env: dict[str, str] | None = None) -> tuple[int, Any]:
    query = {"from": "listEntries", "where": where, "select": ["id", "fields.Status"]}
    result = CliRunner().invoke(
        cli, ["--json", "query", "--query", json.dumps(query)], env={**ENV, **(env or {})}
    )
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


WON = {
    "and": [
        {"path": "listId", "op": "eq", "value": 9},
        {"path": "fields.Status", "op": "eq", "value": "Won"},
    ]
}


def test_query_sends_the_filter_to_affinity(respx_mock: respx.MockRouter) -> None:
    _mock_metadata(respx_mock)
    url = f"{V2}/lists/9/list-entries/search"
    second = respx_mock.post(url, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([_entry(3, "Won")]))
    )
    first = respx_mock.post(url).mock(
        return_value=Response(201, json=_page([_entry(1, "Won")], f"{url}?cursor=p2"))
    )
    plain = respx_mock.get(f"{V2}/lists/9/list-entries")
    code, out = _query(WON)
    assert code == 0, out
    assert [row["id"] for row in out["data"]] == [1, 3]
    body = json.loads(first.calls[0].request.content)
    assert body["filters"]["filters"][0] == {
        "valueType": "ranked-dropdown",
        "fieldId": "field-1",
        "operator": "is-any-of",
        "value": [{"dropdownOptionId": 12}],
    }
    assert json.loads(second.calls[0].request.content) == body  # next page: same body
    assert first.calls[0].request.url.params.get_list("fieldIds") == ["field-1"]
    assert not plain.called


def test_the_local_filter_still_runs(respx_mock: respx.MockRouter) -> None:
    _mock_metadata(respx_mock)
    # The server answers a superset; the local re-check removes the extra entry.
    respx_mock.post(f"{V2}/lists/9/list-entries/search").mock(
        return_value=Response(201, json=_page([_entry(1, "Won"), _entry(2, "Lead")]))
    )
    code, out = _query(WON)
    assert code == 0, out
    assert [row["id"] for row in out["data"]] == [1]


def test_a_refused_search_falls_back_to_reading_the_list(respx_mock: respx.MockRouter) -> None:
    _mock_metadata(respx_mock)
    respx_mock.post(f"{V2}/lists/9/list-entries/search").mock(
        return_value=Response(403, json={"errors": [{"code": "forbidden", "message": "No"}]})
    )
    respx_mock.get(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(200, json=_page([_entry(1, "Won"), _entry(2, None)]))
    )
    code, out = _query(WON)
    assert code == 0, out
    assert [row["id"] for row in out["data"]] == [1]
    assert any("filtering locally" in w for w in json.dumps(out).split('"'))


def test_conditions_affinity_cant_answer_read_the_list(respx_mock: respx.MockRouter) -> None:
    _mock_metadata(respx_mock)
    search = respx_mock.post(f"{V2}/lists/9/list-entries/search")
    respx_mock.get(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(200, json=_page([_entry(1, "Won"), _entry(2, None)]))
    )
    code, out = _query(
        {
            "and": [
                {"path": "listId", "op": "eq", "value": 9},
                {"path": "fields.Status", "op": "is_null"},
            ]
        }
    )
    assert code == 0, out
    assert [row["id"] for row in out["data"]] == [2]
    assert not search.called


def test_the_env_switch_turns_it_off(respx_mock: respx.MockRouter) -> None:
    search = respx_mock.post(f"{V2}/lists/9/list-entries/search")
    respx_mock.get(f"{V2}/lists/9/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    respx_mock.get(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(200, json=_page([_entry(1, "Won")]))
    )
    code, out = _query(WON, env={"XAFFINITY_QUERY_SERVER_FILTER": "0"})
    assert code == 0, out
    assert [row["id"] for row in out["data"]] == [1]
    assert not search.called
