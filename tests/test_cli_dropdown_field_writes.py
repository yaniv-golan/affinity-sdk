"""Dropdown writes on `company field`, `person field`, `opportunity field` and `list entry field`.

V2 field metadata has no dropdown options, so the options are read fresh (uncached) from the V2
dropdown-options endpoints for the fields being written. Plain dropdowns on companies/persons go
through the V2 write (option ids; V1 creates a new option for unknown text); ranked/status
dropdowns are written through V1 by option id.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

pytest.importorskip("rich_click")
respx = pytest.importorskip("respx")

from click.testing import CliRunner

from affinity.cli.main import cli

Handler = Callable[[httpx.Request], httpx.Response | None]


def _run(
    args: list[str], routes: dict[tuple[str, str], Any]
) -> tuple[Any, list[tuple[str, str, Any]]]:
    """Invoke the CLI with a path-keyed fake API. Returns (result, [(method, path, body)])."""
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, path, body))
        key = (request.method, path)
        if key in routes:
            r = routes[key]
            r = r(request) if callable(r) else r
            if isinstance(r, httpx.Response):
                return r
            return httpx.Response(200, json=r)
        if request.method in ("PUT", "POST") and path.startswith("/field-values"):
            return httpx.Response(200, json={"id": 1, "field_id": 1, "entity_id": 1, "value": body})
        return httpx.Response(404, json={"errors": [{"message": f"unmocked {key}"}]})

    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.affinity.co").mock(side_effect=handler)
        result = CliRunner().invoke(cli, ["--json", *args], env={"AFFINITY_API_KEY": "test"})
    return result, seen


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


COMPANY_FIELDS = _page(
    [
        {"id": "field-10", "name": "Stage", "type": "global", "valueType": "dropdown"},
        {"id": "field-11", "name": "Tags", "type": "global", "valueType": "dropdown-multi"},
        {"id": "field-12", "name": "Notes", "type": "global", "valueType": "text"},
        {"id": "dealroom-x", "name": "Kind", "type": "enriched", "valueType": "dropdown"},
    ]
)
STAGE_OPTIONS = _page(
    [
        {"type": "dropdown", "id": 100, "text": "Seed"},
        {"type": "dropdown", "id": 101, "text": "Series A"},
    ]
)


def _company_routes(**extra: Any) -> dict[tuple[str, str], Any]:
    routes: dict[tuple[str, str], Any] = {
        ("GET", "/v2/companies/fields"): COMPANY_FIELDS,
        ("GET", "/v2/companies/fields/field-10/dropdown-options"): STAGE_OPTIONS,
        ("GET", "/v2/companies/fields/field-11/dropdown-options"): STAGE_OPTIONS,
        ("GET", "/field-values"): [{"id": 7, "field_id": 10, "entity_id": 5, "value": "Seed"}],
        ("POST", "/v2/companies/5/fields/field-10"): httpx.Response(204),
        ("POST", "/v2/companies/5/fields/field-11"): httpx.Response(204),
    }
    routes.update(extra)
    return routes


def test_company_dropdown_set_by_text_writes_option_id_via_v2() -> None:
    result, seen = _run(["company", "field", "5", "--set", "Stage", "series a"], _company_routes())
    assert result.exit_code == 0, result.output
    assert ("GET", "/v2/companies/fields/field-10/dropdown-options", None) in seen
    assert (
        "POST",
        "/v2/companies/5/fields/field-10",
        {"value": {"type": "dropdown", "data": {"dropdownOptionId": 101}}},
    ) in seen
    assert not any(
        m in ("PUT", "DELETE") or (p.startswith("/field-values") and m == "POST")
        for m, p, _ in seen
    )


def test_company_dropdown_same_option_is_noop() -> None:
    result, seen = _run(["company", "field", "5", "--set", "Stage", "seed"], _company_routes())
    assert result.exit_code == 0, result.output
    assert not any(m in ("POST", "PUT", "DELETE") for m, _, _ in seen)


def test_company_dropdown_multi_set_json() -> None:
    result, seen = _run(
        ["company", "field", "5", "--set-json", json.dumps({"Tags": ["Seed", "Series A"]})],
        _company_routes(),
    )
    assert result.exit_code == 0, result.output
    posts = [b for m, p, b in seen if m == "POST" and p == "/v2/companies/5/fields/field-11"]
    assert posts == [
        {
            "value": {
                "type": "dropdown-multi",
                "data": [{"dropdownOptionId": 100}, {"dropdownOptionId": 101}],
            }
        }
    ]


def test_unknown_or_renamed_option_writes_nothing() -> None:
    result, seen = _run(["company", "field", "5", "--set", "Stage", "Series B"], _company_routes())
    assert result.exit_code == 2, result.output
    assert "Series B" in result.output
    assert not any(m in ("POST", "PUT", "DELETE") for m, _, _ in seen)


def test_options_read_for_dropdown_fields_only() -> None:
    result, seen = _run(["company", "field", "5", "--set", "Notes", "hi"], _company_routes())
    assert result.exit_code == 0, result.output
    assert not any("dropdown-options" in p for _, p, _ in seen)


def test_enriched_dropdown_not_fetched_and_refused() -> None:
    result, seen = _run(["company", "field", "5", "--set", "dealroom-x", "A"], _company_routes())
    assert result.exit_code == 2, result.output
    assert not any("dropdown-options" in p for _, p, _ in seen)
    assert not any(m in ("POST", "PUT", "DELETE") for m, _, _ in seen)


def test_options_fetch_failure_writes_nothing() -> None:
    routes = _company_routes()
    routes[("GET", "/v2/companies/fields/field-10/dropdown-options")] = httpx.Response(
        500, json={"errors": [{"message": "boom"}]}
    )
    result, seen = _run(
        ["company", "field", "5", "--set", "Notes", "hi", "--set", "Stage", "Seed"], routes
    )
    assert result.exit_code != 0, result.output
    assert not any(m in ("POST", "PUT", "DELETE") for m, _, _ in seen)


def test_paged_options_are_all_read() -> None:
    page2 = "https://api.affinity.co/v2/companies/fields/field-10/dropdown-options?cursor=p2"

    def options(request: httpx.Request) -> Any:
        if "cursor=p2" in str(request.url):
            return _page([{"type": "dropdown", "id": 150, "text": "Growth"}])
        return _page([{"type": "dropdown", "id": i, "text": f"o{i}"} for i in range(100)], page2)

    routes = _company_routes()
    routes[("GET", "/v2/companies/fields/field-10/dropdown-options")] = options
    result, seen = _run(["company", "field", "5", "--set", "Stage", "Growth"], routes)
    assert result.exit_code == 0, result.output
    assert any(
        b == {"value": {"type": "dropdown", "data": {"dropdownOptionId": 150}}}
        for m, _, b in seen
        if m == "POST"
    )


def test_person_dropdown_uses_person_endpoints() -> None:
    routes = {
        ("GET", "/v2/persons/fields"): _page(
            [{"id": "field-20", "name": "Role", "type": "global", "valueType": "dropdown"}]
        ),
        ("GET", "/v2/persons/fields/field-20/dropdown-options"): _page(
            [{"type": "dropdown", "id": 300, "text": "Founder"}]
        ),
        ("GET", "/field-values"): [],
        ("POST", "/v2/persons/8/fields/field-20"): httpx.Response(204),
    }
    result, seen = _run(["person", "field", "8", "--set", "Role", "founder"], routes)
    assert result.exit_code == 0, result.output
    assert (
        "POST",
        "/v2/persons/8/fields/field-20",
        {"value": {"type": "dropdown", "data": {"dropdownOptionId": 300}}},
    ) in seen


@pytest.mark.parametrize("option_type", ["ranked-dropdown", "status-dropdown"])
def test_opportunity_status_written_by_option_id(option_type: str) -> None:
    """`opportunity field` writes through its list entry: one update-fields PATCH with the
    option id read fresh from the list's dropdown options."""
    result, seen = _run(
        ["opportunity", "field", "42", "--set", "Status", "won", "--set", "Amount", "5"],
        _opportunity_routes(option_type),
    )
    assert result.exit_code == 0, result.output
    patches = [b for m, p, b in seen if m == "PATCH"]
    assert patches == [
        {
            "operation": "update-fields",
            "updates": [
                {
                    "id": "field-30",
                    "value": {"type": option_type, "data": {"dropdownOptionId": 401}},
                },
                {"id": "field-31", "value": {"type": "number", "data": 5}},
            ],
        }
    ]
    assert not any(
        m in ("PUT", "DELETE") or (p == "/field-values" and m == "POST") for m, p, _ in seen
    )


def _opportunity_routes(option_type: str = "ranked-dropdown") -> dict[tuple[str, str], Any]:
    lst = {"id": 9, "name": "Deals", "type": 8, "public": False, "owner_id": 1}
    return {
        ("GET", "/v2/opportunities/42"): {"id": 42, "name": "Deal", "listId": 9},
        ("GET", "/opportunities/42"): {"id": 42, "list_entries": [{"id": 555, "list_id": 9}]},
        ("GET", "/v2/lists/9"): {**lst, "isPublic": False, "ownerId": 1},
        ("GET", "/lists/9"): lst,
        ("GET", "/v2/lists/9/fields"): _page(
            [
                {"id": "field-30", "name": "Status", "type": "list", "valueType": option_type},
                {"id": "field-31", "name": "Amount", "type": "list", "valueType": "number"},
            ]
        ),
        ("GET", "/fields"): {
            "data": [
                {
                    "id": 30,
                    "name": "Status",
                    "value_type": 7,
                    "allows_multiple": False,
                    "list_id": 9,
                    "dropdown_options": [{"id": 400, "text": "New"}],
                },
                {
                    "id": 31,
                    "name": "Amount",
                    "value_type": 3,
                    "allows_multiple": False,
                    "list_id": 9,
                },
            ]
        },
        ("GET", "/v2/lists/9/fields/field-30/dropdown-options"): _page(
            [
                {"type": option_type, "id": 400, "text": "New", "rank": 1, "color": "blue"},
                {"type": option_type, "id": 401, "text": "Won", "rank": 2, "color": "green"},
            ]
        ),
        ("GET", "/field-values"): [],
        ("PATCH", "/v2/lists/9/list-entries/555/fields"): {"operation": "update-fields"},
    }


def test_unset_typo_aborts_before_any_write() -> None:
    result, seen = _run(
        ["company", "field", "5", "--set", "Notes", "hi", "--unset", "No Such Field"],
        _company_routes(),
    )
    assert result.exit_code == 2, result.output
    assert not any(m in ("POST", "PUT", "DELETE") for m, _, _ in seen)


def test_list_entry_option_missing_from_cached_metadata_is_read_fresh() -> None:
    lst = {"id": 9, "name": "Pipeline", "type": 0, "public": False, "owner_id": 1}
    routes = {
        ("GET", "/v2/lists/9"): {**lst, "isPublic": False, "ownerId": 1},
        ("GET", "/lists/9"): lst,
        # V1 list fields lag: the option "Won" was added moments ago and isn't listed yet.
        ("GET", "/fields"): {
            "data": [
                {
                    "id": 30,
                    "name": "Status",
                    "value_type": 7,
                    "allows_multiple": False,
                    "list_id": 9,
                    "dropdown_options": [{"id": 400, "text": "New"}],
                }
            ]
        },
        ("GET", "/v2/lists/9/fields/field-30/dropdown-options"): _page(
            [
                {"type": "ranked-dropdown", "id": 400, "text": "New"},
                {"type": "ranked-dropdown", "id": 401, "text": "Won"},
            ]
        ),
        ("GET", "/v2/lists/9/fields"): _page(
            [{"id": "field-30", "name": "Status", "type": "list", "valueType": "ranked-dropdown"}]
        ),
        ("GET", "/field-values"): [],
        ("PATCH", "/v2/lists/9/list-entries/77/fields"): {"operation": "update-fields"},
    }
    result, seen = _run(["list", "entry", "field", "9", "77", "--set", "Status", "Won"], routes)
    assert result.exit_code == 0, result.output
    assert (
        "PATCH",
        "/v2/lists/9/list-entries/77/fields",
        {
            "operation": "update-fields",
            "updates": [
                {
                    "id": "field-30",
                    "value": {"type": "ranked-dropdown", "data": {"dropdownOptionId": 401}},
                }
            ],
        },
    ) in seen


def test_opportunity_unset_goes_through_its_list_entry() -> None:
    result, seen = _run(["opportunity", "field", "42", "--unset", "Amount"], _opportunity_routes())
    assert result.exit_code == 0, result.output
    assert [b for m, p, b in seen if m == "PATCH"] == [
        {
            "operation": "update-fields",
            "updates": [{"id": "field-31", "value": {"type": "number", "data": None}}],
        }
    ]
