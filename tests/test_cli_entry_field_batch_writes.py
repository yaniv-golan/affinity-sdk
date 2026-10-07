"""`list entry field`: every --set/--set-json/--unset goes out in ONE update-fields PATCH, which
Affinity applies all-or-nothing; conflicts are checked on resolved fields; field types come from
V2 metadata."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

pytest.importorskip("rich_click")
respx = pytest.importorskip("respx")

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli
from affinity.clients.http import ClientConfig, HTTPClient
from affinity.models.types import ListEntryId, ListId
from affinity.services.lists import ListEntryService
from tests.field_write_mocks import mock_list_field_writes, patched

BASE = "https://api.affinity.co"
LIST_ID = 55
ENTRY_ID = 9
V1_FIELDS = [
    {
        "id": "field-1",
        "name": "Status",
        "value_type": 2,
        "allows_multiple": False,
        "dropdown_options": [{"id": 10, "text": "Open"}, {"id": 11, "text": "Won"}],
    },
    # V1 says "2" (text or dropdown); V2 says text.
    {
        "id": "field-2",
        "name": "Notes",
        "value_type": 2,
        "allows_multiple": False,
        "dropdown_options": [],
    },
    {"id": "field-3", "name": "Amount", "value_type": 3, "allows_multiple": False},
    {"id": "field-4", "name": "Last Touch", "value_type": 4, "allows_multiple": False},
]
V2_FIELDS = [
    {"id": "field-1", "name": "Status", "type": "list", "valueType": "ranked-dropdown"},
    {"id": "field-2", "name": "Notes", "type": "list", "valueType": "text"},
    {"id": "field-3", "name": "Amount", "type": "list", "valueType": "number"},
    {"id": "field-4", "name": "Last Touch", "type": "list", "valueType": "interaction"},
]


def _setup(respx_mock: Any, existing: list[dict[str, Any]] | None = None) -> Any:
    lst = {"id": LIST_ID, "name": "Deals", "type": 0, "public": False, "owner_id": 1}
    respx_mock.get(f"{BASE}/v2/lists/{LIST_ID}").mock(
        return_value=Response(200, json={**lst, "isPublic": False, "ownerId": 1})
    )
    respx_mock.get(f"{BASE}/lists/{LIST_ID}").mock(return_value=Response(200, json=lst))
    respx_mock.get(f"{BASE}/fields").mock(return_value=Response(200, json={"data": V1_FIELDS}))
    respx_mock.get(f"{BASE}/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(200, json={"data": V2_FIELDS, "pagination": {"nextUrl": None}})
    )
    respx_mock.get(f"{BASE}/field-values").mock(return_value=Response(200, json=existing or []))
    return mock_list_field_writes(respx_mock, LIST_ID, V1_FIELDS)


def _run(*args: str) -> Any:
    return CliRunner().invoke(
        cli,
        ["--json", "list", "entry", "field", str(LIST_ID), str(ENTRY_ID), *args],
        env={"AFFINITY_API_KEY": "k"},
    )


def test_set_and_unset_in_one_typed_request(respx_mock: respx.MockRouter) -> None:
    patch = _setup(respx_mock)
    result = _run("--set", "Status", "won", "--set", "Amount", "12", "--unset", "Notes")
    assert result.exit_code == 0, result.output
    assert patch.call_count == 1
    assert patched(patch) == [
        {"id": "field-1", "value": {"type": "ranked-dropdown", "data": {"dropdownOptionId": 11}}},
        {"id": "field-3", "value": {"type": "number", "data": 12}},
        {"id": "field-2", "value": {"type": "text", "data": None}},
    ]
    data = json.loads(result.output)["data"]
    assert [c["name"] for c in data["created"]] == ["Status", "Amount"]
    assert data["cleared"] == [{"fieldId": "field-2", "name": "Notes"}]


def test_v2_type_wins_over_ambiguous_v1_type(respx_mock: respx.MockRouter) -> None:
    """V1 type 2 means "text or dropdown"; a text field must not be validated as a dropdown."""
    patch = _setup(respx_mock)
    result = _run("--set", "Notes", "free text")
    assert result.exit_code == 0, result.output
    assert patched(patch) == [{"id": "field-2", "value": {"type": "text", "data": "free text"}}]


@pytest.mark.parametrize(
    "args",
    [
        ("--set", "Status", "Open", "--unset", "status"),
        ("--set", "Status", "Open", "--unset", "field-1"),
        ("--set", "Status", "Open", "--set", "STATUS", "Won"),
    ],
)
def test_conflicts_detected_on_resolved_fields(respx_mock: respx.MockRouter, args: tuple) -> None:
    patch = _setup(respx_mock)
    result = _run(*args)
    assert result.exit_code == 2, result.output
    assert patch.call_count == 0


def test_unwritable_type_refused_before_request(respx_mock: respx.MockRouter) -> None:
    patch = _setup(respx_mock)
    result = _run("--unset", "Last Touch")
    assert result.exit_code == 2, result.output
    assert "interaction" in result.output
    assert patch.call_count == 0


def test_unset_always_sent_even_if_reads_show_empty(respx_mock: respx.MockRouter) -> None:
    """V1 reads can lag a write; clearing is idempotent, so the unset is never skipped."""
    patch = _setup(respx_mock, existing=[])
    result = _run("--unset", "Amount")
    assert result.exit_code == 0, result.output
    assert patched(patch) == [{"id": "field-3", "value": {"type": "number", "data": None}}]


def test_all_noop_sends_nothing(respx_mock: respx.MockRouter) -> None:
    patch = _setup(
        respx_mock,
        existing=[{"id": 1, "field_id": 3, "entity_id": 7, "list_entry_id": ENTRY_ID, "value": 12}],
    )
    result = _run("--set", "Amount", "12")
    assert result.exit_code == 0, result.output
    assert patch.call_count == 0


def test_rejected_batch_reports_nothing_changed(respx_mock: respx.MockRouter) -> None:
    patch = _setup(respx_mock)
    patch.mock(
        return_value=Response(400, json={"errors": [{"code": "validation", "message": "bad"}]})
    )
    result = _run("--set", "Amount", "1", "--set", "Status", "Open")
    assert result.exit_code == 1
    assert "nothing was changed" in json.loads(result.output)["error"]["message"]


def test_forbidden_batch_names_restricted_access(respx_mock: respx.MockRouter) -> None:
    patch = _setup(respx_mock)
    patch.mock(
        return_value=Response(
            403, json={"errors": [{"code": "permission-denied", "message": "Forbidden"}]}
        )
    )
    result = _run("--set", "Amount", "1")
    assert result.exit_code == 1
    assert "restricted" in json.loads(result.output)["error"]["message"]


def test_readonly_refuses_the_patch(respx_mock: respx.MockRouter) -> None:
    patch = _setup(respx_mock)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "--readonly",
            "list",
            "entry",
            "field",
            str(LIST_ID),
            str(ENTRY_ID),
            "--set",
            "Amount",
            "1",
        ],
        env={"AFFINITY_API_KEY": "k"},
    )
    assert result.exit_code != 0
    assert patch.call_count == 0


def _sdk(handler: Any) -> HTTPClient:
    return HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )


def test_sdk_batch_value_types_lists_and_nulls() -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"operation": "update-fields"})

    http = _sdk(handler)
    try:
        resp = ListEntryService(http, ListId(1)).batch_update_fields(
            ListEntryId(2),
            {"field-1": [{"id": 5}], "field-2": None, "field-3": "x"},
            value_types={"field-1": "person-multi", "field-2": "number"},
        )
    finally:
        http.close()
    assert resp.operation == "update-fields"
    assert seen[0]["updates"] == [
        {"id": "field-1", "value": {"type": "person-multi", "data": [{"id": 5}]}},
        {"id": "field-2", "value": {"type": "number", "data": None}},
        {"id": "field-3", "value": {"type": "text", "data": "x"}},
    ]


@pytest.mark.parametrize(
    ("updates", "types", "match"),
    [
        ({"field-1": None}, None, "needs its type"),
        ({"field-1": "a", 1: "b"}, None, "more than once"),
        ({"field-1": list(range(101))}, {"field-1": "company-multi"}, "at most 100"),
    ],
)
def test_sdk_batch_refusals(updates: dict, types: dict | None, match: str) -> None:
    http = _sdk(lambda _r: httpx.Response(200, json={}))
    try:
        with pytest.raises(ValueError, match=match):
            ListEntryService(http, ListId(1)).batch_update_fields(
                ListEntryId(2), updates, value_types=types
            )
    finally:
        http.close()
