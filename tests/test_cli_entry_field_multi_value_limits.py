"""`list entry field` refuses to exceed Affinity's 100-value cap BEFORE touching anything,
so a multi-field command never stops half-way on a request the server would reject."""

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

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

LIST_ID = 67890
ENTRY_ID = 123
BASE = "https://api.affinity.co"


def _setup(respx_mock: respx.MockRouter, existing_company_ids: list[int]) -> dict[str, Any]:
    respx_mock.get(f"{BASE}/v2/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={"id": LIST_ID, "name": "Portfolio", "type": 0, "isPublic": False, "ownerId": 1},
        )
    )
    respx_mock.get(f"{BASE}/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={"id": LIST_ID, "name": "Portfolio", "type": 0, "public": False, "owner_id": 1},
        )
    )
    # value_type 1 = organization (company); allows_multiple -> company-multi
    respx_mock.get(f"{BASE}/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "field-200",
                        "name": "Investors",
                        "value_type": 1,
                        "allows_multiple": True,
                    }
                ]
            },
        )
    )
    respx_mock.get(f"{BASE}/field-values").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 5000 + cid,
                    "field_id": 200,
                    "entity_id": 999,
                    "list_entry_id": ENTRY_ID,
                    "value": cid,
                }
                for cid in existing_company_ids
            ],
        )
    )
    return {
        "delete": respx_mock.delete(url__regex=rf"{BASE}/field-values/\d+").mock(
            return_value=Response(200, json={"success": True})
        ),
        "post": respx_mock.post(
            f"{BASE}/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-200"
        ).mock(return_value=Response(200, json={"data": []})),
    }


def _run(*args: str) -> Any:
    return CliRunner().invoke(
        cli,
        ["--json", "list", "entry", "field", str(LIST_ID), str(ENTRY_ID), *args],
        env={"AFFINITY_API_KEY": "k"},
    )


def test_set_json_over_cap_makes_no_delete_or_write(respx_mock: respx.MockRouter) -> None:
    routes = _setup(respx_mock, existing_company_ids=[1, 2, 3])
    result = _run("--set-json", json.dumps({"field-200": list(range(1000, 1101))}))
    assert result.exit_code == 2, result.output
    assert "at most 100" in result.output
    assert routes["delete"].call_count == 0
    assert routes["post"].call_count == 0


def test_append_over_cap_makes_no_write(respx_mock: respx.MockRouter) -> None:
    routes = _setup(respx_mock, existing_company_ids=list(range(1, 101)))
    result = _run("--append", "field-200", "5000")
    assert result.exit_code == 2, result.output
    assert "would hold 101" in result.output
    assert routes["delete"].call_count == 0
    assert routes["post"].call_count == 0


def test_append_of_existing_value_at_cap_is_allowed(respx_mock: respx.MockRouter) -> None:
    """Appending an ID the field already holds does not grow it, so it must not be refused."""
    _setup(respx_mock, existing_company_ids=list(range(1, 101)))
    result = _run("--append", "field-200", "7")
    assert "at most 100" not in result.output, result.output


def test_set_json_at_cap_is_written(respx_mock: respx.MockRouter) -> None:
    routes = _setup(respx_mock, existing_company_ids=[])
    result = _run("--set-json", json.dumps({"field-200": list(range(1000, 1100))}))
    assert result.exit_code == 0, result.output
    assert routes["post"].call_count == 1
    sent = json.loads(routes["post"].calls[0].request.content)
    assert sent["value"]["type"] == "company-multi"
    assert len(sent["value"]["data"]) == 100


def test_rejected_write_leaves_existing_values(respx_mock: respx.MockRouter) -> None:
    """A rejected V2 write changes nothing server-side, and nothing was deleted before it."""
    routes = _setup(respx_mock, existing_company_ids=[1, 2])
    routes["post"].mock(return_value=Response(400, json={"errors": [{"message": "bad value"}]}))
    result = _run("--set-json", json.dumps({"field-200": [3]}))
    assert result.exit_code != 0
    assert routes["delete"].call_count == 0
