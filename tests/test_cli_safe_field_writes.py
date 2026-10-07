"""`list entry field` writes: values are sent in the shape the V2 API accepts, and nothing is
deleted before a write (Affinity's V2 write replaces the value; a rejected write changes nothing).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli
from affinity.services.lists import _field_value_payload

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

LIST_ID = 67890
ENTRY_ID = 123
BASE = "https://api.affinity.co"
V1_FIELDS = [
    {"id": "field-301", "name": "Amount", "value_type": 3, "allows_multiple": False},
    {"id": "field-302", "name": "HQ", "value_type": 5, "allows_multiple": False},
    {"id": "field-303", "name": "Offices", "value_type": 5, "allows_multiple": True},
]


def _setup(respx_mock: respx.MockRouter, existing: list[dict[str, Any]]) -> dict[str, Any]:
    lst = {"id": LIST_ID, "name": "Portfolio", "type": 0, "public": False, "owner_id": 1}
    respx_mock.get(f"{BASE}/v2/lists/{LIST_ID}").mock(
        return_value=Response(200, json={**lst, "isPublic": False, "ownerId": 1})
    )
    respx_mock.get(f"{BASE}/lists/{LIST_ID}").mock(return_value=Response(200, json=lst))
    respx_mock.get(f"{BASE}/fields").mock(return_value=Response(200, json={"data": V1_FIELDS}))
    respx_mock.get(f"{BASE}/field-values").mock(return_value=Response(200, json=existing))
    return {
        "delete": respx_mock.delete(url__regex=rf"{BASE}/field-values/\d+").mock(
            return_value=Response(200, json={"success": True})
        ),
        "post": respx_mock.post(
            url__regex=rf"{BASE}/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-\d+"
        ).mock(return_value=Response(204)),
    }


def _run(*args: str) -> Any:
    return CliRunner().invoke(
        cli,
        ["--json", "list", "entry", "field", str(LIST_ID), str(ENTRY_ID), *args],
        env={"AFFINITY_API_KEY": "k"},
    )


def _sent(route: Any) -> list[dict[str, Any]]:
    return [json.loads(c.request.content)["value"] for c in route.calls]


def _row(fv_id: int, field: int, value: Any) -> dict[str, Any]:
    return {
        "id": fv_id,
        "field_id": field,
        "entity_id": 9,
        "list_entry_id": ENTRY_ID,
        "value": value,
    }


def test_number_set_sends_a_number_and_deletes_nothing(respx_mock: respx.MockRouter) -> None:
    routes = _setup(respx_mock, [_row(1, 301, 3.0)])
    result = _run("--set", "Amount", "5")
    assert result.exit_code == 0, result.output
    assert _sent(routes["post"]) == [{"type": "number", "data": 5}]
    assert routes["delete"].call_count == 0


def test_invalid_number_changes_nothing(respx_mock: respx.MockRouter) -> None:
    routes = _setup(respx_mock, [_row(1, 301, 3.0)])
    result = _run("--set", "Amount", "five")
    assert result.exit_code == 2, result.output
    assert "Invalid number" in result.output
    assert routes["post"].call_count == 0
    assert routes["delete"].call_count == 0


def test_location_set_sends_all_five_keys(respx_mock: respx.MockRouter) -> None:
    routes = _setup(respx_mock, [])
    result = _run("--set", "HQ", '{"city": "Paris", "street_address": "1 Rue X"}')
    assert result.exit_code == 0, result.output
    assert _sent(routes["post"]) == [
        {
            "type": "location",
            "data": {
                "streetAddress": "1 Rue X",
                "city": "Paris",
                "state": None,
                "country": None,
                "continent": None,
            },
        }
    ]


@pytest.mark.parametrize("bad", ["Paris", '{"town": "Paris"}', "{}"])
def test_invalid_location_changes_nothing(respx_mock: respx.MockRouter, bad: str) -> None:
    routes = _setup(respx_mock, [])
    result = _run("--set", "HQ", bad)
    assert result.exit_code == 2, result.output
    assert routes["post"].call_count == 0


def test_location_multi_append_keeps_existing(respx_mock: respx.MockRouter) -> None:
    """--append on a multi-value location field used to replace the list with one value."""
    paris = {
        "street_address": None,
        "city": "Paris",
        "state": None,
        "country": "France",
        "continent": None,
    }
    routes = _setup(respx_mock, [_row(1, 303, paris)])
    result = _run("--append", "Offices", '{"city": "Lyon", "country": "France"}')
    assert result.exit_code == 0, result.output
    (sent,) = _sent(routes["post"])
    assert sent["type"] == "location-multi"
    assert [loc["city"] for loc in sent["data"]] == ["Paris", "Lyon"]
    assert all(
        set(loc) == {"streetAddress", "city", "state", "country", "continent"}
        for loc in sent["data"]
    )
    assert routes["delete"].call_count == 0


def test_location_multi_append_of_present_value_is_noop(respx_mock: respx.MockRouter) -> None:
    paris = {"city": "Paris", "country": "France"}
    routes = _setup(respx_mock, [_row(1, 303, paris)])
    result = _run("--append", "Offices", json.dumps(paris))
    assert result.exit_code == 0, result.output
    assert routes["post"].call_count == 0


def test_location_multi_append_over_cap_refused(respx_mock: respx.MockRouter) -> None:
    rows = [_row(i, 303, {"city": f"c{i}"}) for i in range(1, 101)]
    routes = _setup(respx_mock, rows)
    result = _run("--append", "Offices", '{"city": "new"}')
    assert result.exit_code == 2, result.output
    assert "would hold 101" in result.output
    assert routes["post"].call_count == 0


def test_append_on_single_value_field_refused(respx_mock: respx.MockRouter) -> None:
    routes = _setup(respx_mock, [_row(1, 301, 3.0)])
    result = _run("--append", "Amount", "5")
    assert result.exit_code == 2, result.output
    assert "Use --set" in result.output
    assert routes["post"].call_count == 0


def test_sdk_location_payload_completed() -> None:
    assert _field_value_payload({"city": "Paris"}, "location") == {
        "type": "location",
        "data": {
            "streetAddress": None,
            "city": "Paris",
            "state": None,
            "country": None,
            "continent": None,
        },
    }
    multi = _field_value_payload([{"street_address": "x"}], "location-multi")
    assert multi["data"][0]["streetAddress"] == "x"
    assert set(multi["data"][0]) == {"streetAddress", "city", "state", "country", "continent"}
