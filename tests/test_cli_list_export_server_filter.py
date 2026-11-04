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
    assert out["meta"]["serverFilter"]["local"] == []
    assert out["meta"]["serverFilter"]["pushed"]
    assert "fallback" not in out["meta"]["serverFilter"]
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
    assert out["meta"]["serverFilter"]["fallback"]


def test_without_a_filter_there_is_no_server_filter_meta(router: respx.MockRouter) -> None:
    router.get(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(200, json=_page([_entry(1, "Won")]))
    )
    code, out = _export("--all")
    assert code == 0, out
    assert "serverFilter" not in out["meta"]


@pytest.mark.usefixtures("router")
def test_a_dry_run_shows_the_split() -> None:
    code, out = _export("--filter", 'Status = "Won" & entityName =~ "Deal"', "--all", "--dry-run")
    assert code == 0, out
    split = out["data"]["serverFilter"]
    assert split["pushed"] and split["local"]


def test_server_filter_off_is_reported(
    router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAFFINITY_QUERY_SERVER_FILTER", "0")
    router.get(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(200, json=_page([_entry(1, "Won")]))
    )
    code, out = _export("--filter", 'Status = "Won"', "--all")
    assert code == 0, out
    assert out["meta"]["serverFilter"] == {"fallback": "XAFFINITY_QUERY_SERVER_FILTER=0"}


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


# --- repeated --filter: the values are ANDed ---------------------------------------------------

REPEATED = ("--filter", 'Status = "Won"', "--filter", 'entityName =~ "Deal"')


@pytest.mark.usefixtures("router")
def test_repeated_filters_are_anded() -> None:
    code, out = _export(*REPEATED, "--all", "--dry-run")
    assert code == 0, out
    assert out["command"]["modifiers"]["filter"] == '(Status = "Won") & (entityName =~ "Deal")'
    split = out["data"]["serverFilter"]
    assert split["pushed"] and split["local"]


@pytest.mark.usefixtures("router")
def test_repeated_partly_local_filter_needs_a_scope_flag() -> None:
    code, out = _export(*REPEATED)
    assert code == 2
    details = out["error"]["details"]
    assert details["rejected_filter"] == '(Status = "Won") & (entityName =~ "Deal")'
    assert details["local"]


def test_repeated_filter_cursor_resumes(router: respx.MockRouter) -> None:
    second = router.post(SEARCH, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([_entry(3, "Won"), _entry(4, "Lead")]))
    )
    first = router.post(SEARCH).mock(
        return_value=Response(201, json=_page([_entry(1, "Won")], f"{SEARCH}?cursor=p2"))
    )
    # Both conditions go to Affinity, so no scope flag is needed
    code, out = _export("--filter", 'Status = "Won"', "--filter", 'Status != "Lead"')
    assert code == 0, out
    assert _ids(out) == [1]
    cursor = out["meta"]["pagination"]["rows"]["nextCursor"]
    code, out = _export("--cursor", cursor)
    assert code == 0, out
    assert _ids(out) == [3]
    assert json.loads(second.calls[0].request.content) == json.loads(first.calls[0].request.content)


@pytest.mark.usefixtures("respx_mock")
def test_repeated_expand_filters_are_anded(monkeypatch: pytest.MonkeyPatch) -> None:
    # The expand filter is parsed while the options are checked, before any request
    from affinity.filters import parse

    seen: list[str] = []

    def spy(text: str) -> Any:
        seen.append(text)
        return parse(text)

    monkeypatch.setattr("affinity.cli.commands.list_cmds.parse_filter", spy)
    _export(
        "--expand", "persons",
        "--expand-filter", 'name =~ "a"',
        "--expand-filter", 'name =~ "b"',
        "--all",
    )  # fmt: skip
    assert seen == ['(name =~ "a") & (name =~ "b")']


# --- person fields match by name, or by id when the value is a number --------------------------

OWNER = {
    **FIELDS[0],
    "id": "field-2",
    "name": "Owner",
    "valueType": "person",
    "filterability": None,  # stays local
}


def _owned(entry_id: int, person_id: int, first: str, last: str) -> dict[str, Any]:
    entry = _entry(entry_id, "Won")
    entry["entity"]["fields"].append(
        {
            "id": "field-2",
            "name": "Owner",
            "type": "list",
            "value": {
                "type": "person",
                "data": {
                    "id": person_id,
                    "firstName": first,
                    "lastName": last,
                    "primaryEmailAddress": None,
                    "type": "internal",
                },
            },
        }
    )
    return entry


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("Owner = 5", [1]),
        ("Owner != 5", [2]),
        ("Owner in [5, 7]", [1]),
        ('Owner = "Dana Levi"', [1]),
    ],
)
def test_a_person_field_matches_its_id(
    router: respx.MockRouter, expression: str, expected: list[int]
) -> None:
    router.get(f"{V2}/lists/9/fields").mock(
        return_value=Response(200, json=_page([*FIELDS, OWNER]))
    )
    router.get(url__regex=r".*/lists/9/list-entries.*").mock(
        return_value=Response(
            200, json=_page([_owned(1, 5, "Dana", "Levi"), _owned(2, 6, "Ron", "Cohen")])
        )
    )
    code, out = _export("--all", "--field", "Owner", "--filter", expression)
    assert code == 0, out
    assert _ids(out) == expected


# --- a search Affinity refuses is caught before any output ---------------------------------------

REFUSAL = {"errors": [{"code": "bad_request", "message": "Cannot filter on that"}]}


def _refuse_search(router: respx.MockRouter, status: int = 400) -> tuple[Any, Any]:
    search = router.post(SEARCH).mock(return_value=Response(status, json=REFUSAL))
    plain = router.get(f"{V2}/lists/9/list-entries").mock(
        return_value=Response(200, json=_page([_entry(1, "Won"), _entry(2, "Lead")]))
    )
    return search, plain


@pytest.mark.parametrize("status", [400, 403])
def test_refused_search_without_scope_flag_exits_2(router: respx.MockRouter, status: int) -> None:
    search, plain = _refuse_search(router, status)
    code, out = _export("--filter", 'Status = "Won"')
    assert code == 2, out
    assert out["error"]["type"] == "usage_error"
    assert out["error"]["message"].startswith("Affinity's search refused this --filter")
    assert out["error"]["details"]["refused"].startswith(f"[{status}]")
    assert "--all" in out["error"]["hint"]
    assert search.call_count == 1
    assert not plain.called


def test_refused_search_csv_writes_nothing(router: respx.MockRouter) -> None:
    _, plain = _refuse_search(router)
    result = CliRunner().invoke(
        cli, ["list", "export", "9", "--csv", "--filter", 'Status = "Won"'], env=ENV
    )
    assert result.exit_code == 2, result.output
    assert "listEntryId" not in result.stdout
    assert not plain.called


def test_refused_search_with_all_falls_back(router: respx.MockRouter) -> None:
    _refuse_search(router)
    code, out = _export("--filter", 'Status = "Won"', "--all")
    assert code == 0, out
    assert _ids(out) == [1]
    assert any(
        "Affinity's search refused the filter, filtering locally" in w for w in out["warnings"]
    )
    assert out["meta"]["serverFilter"]["fallback"]


def test_refused_search_csv_with_all_falls_back(router: respx.MockRouter) -> None:
    _refuse_search(router)
    result = CliRunner().invoke(
        cli, ["list", "export", "9", "--csv", "--filter", 'Status = "Won"', "--all"], env=ENV
    )
    assert result.exit_code == 0, result.output
    assert "Affinity's search refused the filter, filtering locally" in result.output
    assert len(result.stdout.strip().splitlines()) == 2  # header + the one match


@pytest.mark.parametrize("csv", [False, True])
def test_search_first_page_not_fetched_twice(router: respx.MockRouter, csv: bool) -> None:
    search = router.post(SEARCH).mock(return_value=Response(201, json=_page([_entry(1, "Won")])))
    args = ["list", "export", "9", "--filter", 'Status = "Won"']
    if csv:
        args.append("--csv")
    result = CliRunner().invoke(cli, args if csv else ["--json", *args], env=ENV)
    assert result.exit_code == 0, result.output
    assert search.call_count == 1
