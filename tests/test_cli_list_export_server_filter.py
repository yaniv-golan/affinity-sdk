"""CLI tests: list export --filter lets Affinity's search apply what it can."""

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

from affinity.cli.list_search import decode_cursor
from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
OPS = ["is-any-of", "is-none-of", "is-empty", "is-not-empty"]
LIST = {
    "id": 9,
    "name": "Pipeline",
    "type": 8,
    "isPublic": False,
    "ownerId": 1,
    "creatorId": 1,
    "listSize": 3,
}
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
        "sortability": None,
    }
]
OPTIONS = [{"id": 11, "text": "Lead", "rank": 1}, {"id": 12, "text": "Won", "rank": 2}]


def _entry(entry_id: int, status: str) -> dict[str, Any]:
    option = 12 if status == "Won" else 11
    return {
        "id": entry_id,
        "listId": 9,
        "type": "opportunity",
        "createdAt": "2026-01-01T00:00:00Z",
        "creatorId": 1,
        "entity": {
            "id": 100 + entry_id,
            "name": f"Deal {entry_id}",
            "listId": 9,
            "fields": [
                {
                    "id": "field-1",
                    "name": "Status",
                    "type": "list",
                    "value": {
                        "type": "ranked-dropdown",
                        "data": {"dropdownOptionId": option, "text": status},
                    },
                }
            ],
        },
    }


def _page(items: list[Any], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


@pytest.fixture
def router(respx_mock: respx.MockRouter) -> respx.MockRouter:
    respx_mock.get(f"{V2}/lists/9").mock(return_value=Response(200, json=LIST))
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Status",
                    "list_id": 9,
                    "value_type": 7,
                    "allows_multiple": False,
                    "track_changes": False,
                    "enrichment_source": "none",
                    "dropdown_options": [],
                }
            ],
        )
    )
    respx_mock.get(f"{V2}/lists/9/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    respx_mock.get(url__regex=r".*/lists/9/fields/field-1/dropdown-options.*").mock(
        return_value=Response(200, json=_page(OPTIONS))
    )
    return respx_mock


def _export(*args: str) -> tuple[int, Any]:
    result = CliRunner().invoke(cli, ["--json", "list", "export", "9", *args], env=ENV)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def _ids(out: Any) -> list[int]:
    rows = out["data"]["rows"]
    return [row["listEntryId"] for row in rows]


SEARCH = f"{V2}/lists/9/list-entries/search"


def test_a_filter_affinity_answers_needs_no_scope_flag(router: respx.MockRouter) -> None:
    second = router.post(SEARCH, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([_entry(3, "Won"), _entry(4, "Lead")]))
    )
    first = router.post(SEARCH).mock(
        return_value=Response(201, json=_page([_entry(1, "Won")], f"{SEARCH}?cursor=p2"))
    )
    plain = router.get(f"{V2}/lists/9/list-entries")
    code, out = _export("--filter", 'Status = "Won"')
    assert code == 0, out
    assert _ids(out) == [1]
    assert json.loads(first.calls[0].request.content)["filters"]["filters"][0]["value"] == [
        {"dropdownOptionId": 12}
    ]
    assert not plain.called
    # The next-page cursor carries the search: it resumes on its own
    cursor = out["meta"]["pagination"]["rows"]["nextCursor"]
    decoded = decode_cursor(cursor, path_suffixes=["/lists/9/list-entries/search"])
    assert decoded is not None and decoded.url == f"{SEARCH}?cursor=p2"
    code, out = _export("--cursor", cursor)
    assert code == 0, out
    assert _ids(out) == [3]  # the whole filter still runs locally
    assert json.loads(second.calls[0].request.content) == json.loads(first.calls[0].request.content)


@pytest.mark.usefixtures("router")
def test_a_partly_local_filter_still_needs_a_scope_flag() -> None:
    code, out = _export("--filter", 'Status = "Won" & entityName =~ "Deal"')
    assert code == 2
    assert "--all" in out["error"]["message"]
    assert out["error"]["details"]["local"]


def test_a_refused_search_falls_back(router: respx.MockRouter) -> None:
    router.post(SEARCH).mock(
        return_value=Response(403, json={"errors": [{"code": "forbidden", "message": "No"}]})
    )
    router.get(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(200, json=_page([_entry(1, "Won"), _entry(2, "Lead")]))
    )
    code, out = _export("--filter", 'Status = "Won"', "--all")
    assert code == 0, out
    assert _ids(out) == [1]
    assert any("filtering locally" in w for w in out["warnings"])


def test_the_filters_fields_are_fetched_even_when_not_exported(
    router: respx.MockRouter,
) -> None:
    notes = {**FIELDS[0], "id": "field-2", "name": "Notes", "valueType": "text"}
    router.get(f"{V2}/lists/9/fields").mock(
        return_value=Response(200, json=_page([*FIELDS, notes]))
    )
    route = router.post(SEARCH).mock(return_value=Response(201, json=_page([_entry(1, "Won")])))
    code, out = _export("--field", "Notes", "--filter", 'Status = "Won"')
    assert code == 0, out
    assert _ids(out) == [1]  # the local re-check saw Status (it was fetched)
    assert sorted(route.calls[0].request.url.params.get_list("fieldIds")) == ["field-1", "field-2"]
