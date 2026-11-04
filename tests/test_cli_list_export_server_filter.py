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


# --- person fields match by name; a number is the record's id ----------------------------------

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


# A 403 here becomes a usage error (exit 2) like a 400: list export reports the refusal with the
# scope flags that make it read the list instead. company ls / person ls pass the raw
# AuthorizationError through (accepted).
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
    assert not [line for line in result.stdout.splitlines() if line[:1].isdigit()]  # no CSV rows
    assert not plain.called


@pytest.mark.parametrize(
    "scope", [["--all"], ["--max-results", "5"], ["--first-page-only"]], ids=lambda s: s[0]
)
def test_refused_search_with_all_falls_back(router: respx.MockRouter, scope: list[str]) -> None:
    _refuse_search(router)
    code, out = _export("--filter", 'Status = "Won"', *scope)
    assert code == 0, out
    assert _ids(out) == [1]
    assert any(
        "Affinity's search refused the filter, filtering locally" in w for w in out["warnings"]
    )
    assert out["meta"]["serverFilter"]["fallback"]


def test_unsupported_search_is_a_refusal(
    router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from affinity.cli.commands import list_cmds
    from affinity.exceptions import UnsupportedOperationError

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise UnsupportedOperationError("search is not available for this list")

    monkeypatch.setattr(list_cmds, "_open_search", refuse)
    _, plain = _refuse_search(router)
    code, out = _export("--filter", 'Status = "Won"')
    assert code == 2, out
    assert out["error"]["details"]["refused"] == "search is not available for this list"
    assert not plain.called
    code, out = _export("--filter", 'Status = "Won"', "--all")
    assert code == 0, out
    assert _ids(out) == [1]
    assert out["meta"]["serverFilter"]["fallback"] == "search is not available for this list"


def test_refused_search_csv_with_all_falls_back(router: respx.MockRouter) -> None:
    _refuse_search(router)
    result = CliRunner().invoke(
        cli, ["list", "export", "9", "--csv", "--filter", 'Status = "Won"', "--all"], env=ENV
    )
    assert result.exit_code == 0, result.output
    assert "Affinity's search refused the filter, filtering locally" in result.output
    # header + the one match (older click mixes stderr into stdout, so count CSV rows only)
    rows = [line for line in result.stdout.splitlines() if line.startswith(("listEntryId,", "1,"))]
    assert len(rows) == 2, result.stdout


@pytest.mark.parametrize("csv", [False, True])
def test_search_first_page_not_fetched_twice(router: respx.MockRouter, csv: bool) -> None:
    search = router.post(SEARCH).mock(return_value=Response(201, json=_page([_entry(1, "Won")])))
    args = ["list", "export", "9", "--filter", 'Status = "Won"']
    if csv:
        args.append("--csv")
    result = CliRunner().invoke(cli, args if csv else ["--json", *args], env=ENV)
    assert result.exit_code == 0, result.output
    assert search.call_count == 1


# --- person and company ids go to Affinity's search ----------------------------------------

PERSON_OPS = ["is-any-of", "is-none-of", "is-empty", "is-not-empty"]
OWNER_FILTERABLE = {
    **OWNER,
    "filterability": {
        "filterableFieldType": "field-only",
        "operators": [{"id": o, "name": o, "numberOfValuesRequired": "multi"} for o in PERSON_OPS],
    },
}
ACCOUNT = {**OWNER_FILTERABLE, "id": "field-3", "name": "Account", "valueType": "company"}
TEAM = {
    **OWNER_FILTERABLE,
    "id": "field-4",
    "name": "Team",
    "valueType": "person-multi",
    "filterability": {
        "filterableFieldType": "field-only",
        "operators": [
            {"id": o, "name": o, "numberOfValuesRequired": "multi"}
            for o in ("has-any-of", "has-all-of", "has-none-of", "is-empty")
        ],
    },
}


def _with_fields(entry: dict[str, Any], *values: dict[str, Any]) -> dict[str, Any]:
    entry["entity"]["fields"].extend(values)
    return entry


def _person_value(fid: str, name: str, data: Any) -> dict[str, Any]:
    kind = "person-multi" if isinstance(data, list) else "person"
    return {"id": fid, "name": name, "type": "list", "value": {"type": kind, "data": data}}


def _dana(person_id: int) -> dict[str, Any]:
    return {
        "id": person_id,
        "firstName": "Dana",
        "lastName": "Levi",
        "primaryEmailAddress": None,
        "type": "internal",
    }


def _company_value(company_id: int, name: str) -> dict[str, Any]:
    data = {"id": company_id, "name": name, "domain": None}
    return {
        "id": "field-3",
        "name": "Account",
        "type": "list",
        "value": {"type": "company", "data": data},
    }


def _fields(router: respx.MockRouter, *extra: dict[str, Any]) -> None:
    router.get(f"{V2}/lists/9/fields").mock(
        return_value=Response(200, json=_page([*FIELDS, *extra]))
    )


def test_a_person_id_filter_needs_no_scope_flag(router: respx.MockRouter) -> None:
    _fields(router, OWNER_FILTERABLE)
    plain = router.get(f"{V2}/lists/9/list-entries")
    search = router.post(SEARCH).mock(
        return_value=Response(
            201,
            json=_page(
                [
                    _with_fields(_entry(1, "Won"), _person_value("field-2", "Owner", _dana(5))),
                    _with_fields(_entry(2, "Won"), _person_value("field-2", "Owner", _dana(6))),
                ]
            ),
        )
    )
    code, out = _export("--filter", 'Status = "Won" & Owner = 5')
    assert code == 0, out
    assert _ids(out) == [1]  # the local re-check trims what Affinity returned extra
    assert not plain.called
    conditions = json.loads(search.calls[0].request.content)["filters"]["filters"]
    assert {c["operator"] for c in conditions} == {"is-any-of"}
    assert {"valueType": "person", "fieldId": "field-2", "operator": "is-any-of",
            "value": [{"id": 5}]} in conditions  # fmt: skip
    assert out["meta"]["serverFilter"]["local"] == []


def test_a_person_name_filter_still_needs_a_scope_flag(router: respx.MockRouter) -> None:
    _fields(router, OWNER_FILTERABLE)
    code, out = _export("--filter", 'Owner = "Dana Levi"')
    assert code == 2, out
    assert any("names are matched locally" in r for r in out["error"]["details"]["local"])


def test_a_number_never_matches_a_company_named_with_digits(router: respx.MockRouter) -> None:
    _fields(router, ACCOUNT)
    # The id goes to Affinity's search; the local filter (run on what comes back) applies the
    # id rule too, so a company named "360" is not a match for 360
    entry = _with_fields(_entry(1, "Won"), _company_value(77, "360"))
    plain = router.get(f"{V2}/lists/9/list-entries")
    search = router.post(SEARCH).mock(return_value=Response(201, json=_page([entry])))
    code, out = _export("--filter", "Account = 360")
    assert code == 0, out
    assert _ids(out) == []
    code, out = _export("--filter", "Account = 77")
    assert code == 0, out
    assert _ids(out) == [1]
    sent = json.loads(search.calls[-1].request.content)["filters"]["filters"][0]
    assert (sent["operator"], sent["value"]) == ("is-any-of", [{"id": 77}])
    assert not plain.called


def test_a_digit_starts_word_needs_a_scope_flag(router: respx.MockRouter) -> None:
    _fields(router, ACCOUNT)
    search = router.post(SEARCH)
    code, out = _export("--filter", 'Account starts_word "360"')
    assert code == 2, out
    assert any(
        "a number isn't sent as a name search" in r for r in out["error"]["details"]["local"]
    )
    assert not search.called


def test_a_person_id_search_cursor_carries_the_condition(router: respx.MockRouter) -> None:
    _fields(router, OWNER_FILTERABLE)
    owned = _with_fields(_entry(1, "Won"), _person_value("field-2", "Owner", _dana(5)))
    second = router.post(SEARCH, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([owned]))
    )
    first = router.post(SEARCH).mock(
        return_value=Response(201, json=_page([owned], f"{SEARCH}?cursor=p2"))
    )
    code, out = _export("--filter", "Owner = 5")
    assert code == 0, out
    cursor = out["meta"]["pagination"]["rows"]["nextCursor"]
    assert cursor.startswith("xsearch2:")
    code, out = _export("--cursor", cursor)
    assert code == 0, out
    assert json.loads(second.calls[0].request.content) == json.loads(first.calls[0].request.content)


def test_a_multi_field_with_a_nameless_person_in_the_filter_dict(router: respx.MockRouter) -> None:
    # Team = [5]: pushed as has-all-of; the local filter decides. A nameless member (id 7)
    # breaks set equality here, while query skips nameless members (see the query tests).
    _fields(router, TEAM)
    nameless = {"id": 7, "firstName": None, "lastName": None, "type": "internal"}
    router.post(SEARCH).mock(
        return_value=Response(
            201,
            json=_page(
                [
                    _with_fields(
                        _entry(1, "Won"), _person_value("field-4", "Team", [_dana(5), nameless])
                    ),
                    _with_fields(_entry(2, "Won"), _person_value("field-4", "Team", [_dana(5)])),
                ]
            ),
        )
    )
    code, out = _export("--filter", "Team = [5]")
    assert code == 0, out
    assert _ids(out) == [2]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("Owner = 5", [1]),
        ("Owner in [5, 7]", [1]),
        ("Owner in [8, 9]", []),
    ],
)
def test_a_person_field_id_matches_without_a_scope_flag(
    router: respx.MockRouter, expression: str, expected: list[int]
) -> None:
    _fields(router, OWNER_FILTERABLE)
    router.post(SEARCH).mock(
        return_value=Response(
            201,
            json=_page(
                [
                    _with_fields(_entry(1, "Won"), _person_value("field-2", "Owner", _dana(5))),
                    _with_fields(_entry(2, "Won"), _person_value("field-2", "Owner", _dana(6))),
                ]
            ),
        )
    )
    code, out = _export("--field", "Owner", "--filter", expression)
    assert code == 0, out
    assert _ids(out) == expected


def test_a_person_id_negation_stays_local(router: respx.MockRouter) -> None:
    _fields(router, OWNER_FILTERABLE)
    code, out = _export("--filter", "Owner != 5")
    assert code == 2, out
    assert any("stays local" in r for r in out["error"]["details"]["local"])


def test_expand_filter_matches_a_person_field_by_id_only() -> None:
    from types import SimpleNamespace

    from affinity.cli.commands.list_cmds import _person_to_expand_dict
    from affinity.filters import parse

    def expanded(person_id: int, mentor: dict[str, Any]) -> dict[str, Any]:
        person = SimpleNamespace(
            id=person_id,
            full_name=f"Person {person_id}",
            primary_email=None,
            emails=[],
            fields=SimpleNamespace(requested=True),
            fields_raw=[{"id": "field-9", "value": {"type": "person", "data": mentor}}],
        )
        return _person_to_expand_dict(
            person,
            field_ids=["field-9"],  # type: ignore[list-item]
            field_id_to_display={"field-9": "Mentor"},
            prefix_fields=False,
        )

    by_id = expanded(1, {"id": 9, "firstName": "Ann", "lastName": "Lee"})
    digit_name = expanded(2, {"id": 4, "firstName": "9", "lastName": None})
    flt = parse("Mentor = 9")
    assert [d["id"] for d in (by_id, digit_name) if flt.matches(d)] == [1]
