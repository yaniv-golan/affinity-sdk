"""Tests for the unified 'entry field' command.

Tests cover:
- --set, --append, --unset, --unset-value, --set-json, --get operations
- Operation exclusivity and conflict validations
- Field ID auto-detection
- JSON output format
- Error handling
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli
from tests.field_write_mocks import mock_list_field_writes, patched

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)


# Common test fixtures
LIST_ID = 67890
ENTRY_ID = 123

LIST_RESPONSE = {
    "id": LIST_ID,
    "name": "Portfolio",
    "type": 0,  # Regular list
    "isPublic": False,
    "ownerId": 100,
    "creatorId": 100,
}

FIELDS_RESPONSE = [
    {"id": "field-100", "name": "Status", "valueType": "text", "allowsMultiple": False},
    {"id": "field-101", "name": "Priority", "valueType": "text", "allowsMultiple": False},
    {"id": "field-102", "name": "Tags", "valueType": "text", "allowsMultiple": True},
    {"id": "field-103", "name": "Investors", "valueType": "company-multi", "allowsMultiple": True},
]

# V1 API format uses snake_case and numeric value types (6 = text)
FIELDS_RESPONSE_V1 = [
    {"id": "field-100", "name": "Status", "value_type": 6, "allows_multiple": False},
    {"id": "field-101", "name": "Priority", "value_type": 6, "allows_multiple": False},
    {"id": "field-102", "name": "Tags", "value_type": 6, "allows_multiple": True},
    # 1 = organization (company); allows_multiple -> company-multi
    {"id": "field-103", "name": "Investors", "value_type": 1, "allows_multiple": True},
]

FIELD_VALUE_RESPONSE = {
    "id": 999,
    "fieldId": "field-100",
    "entityId": 224925,  # Some entity ID
    "value": "Active",
    "listEntryId": ENTRY_ID,
}


def setup_list_mocks(respx_mock: respx.MockRouter) -> Any:
    """Set up common list resolution and field metadata mocks."""
    # List resolution by name (V2 API)
    respx_mock.get("https://api.affinity.co/v2/lists").mock(
        return_value=Response(200, json={"data": [LIST_RESPONSE], "pagination": {}})
    )
    # V1 API for accurate listSize (called by resolve_list_selector after V2 resolution)
    respx_mock.get(f"https://api.affinity.co/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={
                "id": LIST_ID,
                "name": "Portfolio",
                "type": 0,
                "public": False,
                "owner_id": 100,
                "creator_id": 100,
                "list_size": 100,
            },
        )
    )
    # Field metadata (V2)
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(200, json={"data": FIELDS_RESPONSE, "pagination": {}})
    )
    # Field metadata (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(200, json={"data": FIELDS_RESPONSE_V1})
    )
    return mock_list_field_writes(respx_mock, LIST_ID, FIELDS_RESPONSE_V1)


def mock_v2_entry_fields(
    respx_mock: respx.MockRouter,
    fields: list[dict[str, Any]],
) -> None:
    """Mock the V2 GET /lists/{listId}/list-entries/{entryId}/fields endpoint."""
    respx_mock.get(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields"
    ).mock(
        return_value=Response(
            200,
            json={
                "data": fields,
                "pagination": {"prevUrl": None, "nextUrl": None},
            },
        )
    )


# ============================================================================
# Basic Operation Tests
# ============================================================================


def test_entry_field_set_single(respx_mock: respx.MockRouter) -> None:
    """--set replaces existing field value."""
    setup_list_mocks(respx_mock)

    # No existing values
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    # V2 API update
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(return_value=Response(200, json=FIELD_VALUE_RESPONSE))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--set", "Status", "Active"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert "created" in payload["data"]
    assert len(payload["data"]["created"]) == 1


def test_entry_field_set_multiple(respx_mock: respx.MockRouter) -> None:
    """Multiple --set options work."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(return_value=Response(200, json=FIELD_VALUE_RESPONSE))
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-101"
    ).mock(
        return_value=Response(
            200, json={"id": 1000, "fieldId": "field-101", "entityId": 224925, "value": "High"}
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--set",
            "Priority",
            "High",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert len(payload["data"]["created"]) == 2


def test_entry_field_append(respx_mock: respx.MockRouter) -> None:
    """--append adds to a multi-value field, keeping the existing values."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-103", "entityId": 224925, "value": 7}],
        )
    )
    post = respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-103"
    ).mock(return_value=Response(204))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--append", "Investors", "8"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert "created" in payload["data"]
    # Append doesn't delete existing, so no deleted count
    assert "deleted" not in payload["data"]
    assert json.loads(post.calls[0].request.content)["value"] == {
        "type": "company-multi",
        "data": [{"id": 7}, {"id": 8}],
    }


def test_entry_field_append_to_single_value_field_refused(respx_mock: respx.MockRouter) -> None:
    """--append on a field that holds one value would replace it; refuse before any write."""
    setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200, json=[{"id": 500, "fieldId": "field-102", "entityId": 224925, "value": "Old"}]
        )
    )
    post = respx_mock.post(url__regex=r".*/v2/lists/.*/fields/.*").mock(return_value=Response(204))
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "x",
            "--append",
            "Tags",
            "NewTag",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2, result.output
    assert "Use --set" in result.output
    assert post.call_count == 0


def test_entry_field_unset(respx_mock: respx.MockRouter) -> None:
    """--unset clears the field with a typed null in the update-fields PATCH (no V1 delete)."""
    patch = setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-100", "entityId": 224925, "value": "Active"}],
        )
    )
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--unset", "Status"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["data"]["cleared"] == [{"fieldId": "field-100", "name": "Status"}]
    assert "deleted" not in payload["data"]
    assert patched(patch) == [{"id": "field-100", "value": {"type": "text", "data": None}}]


def test_entry_field_unset_value(respx_mock: respx.MockRouter) -> None:
    """--unset-value removes specific value."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[
                {"id": 500, "fieldId": "field-102", "entityId": 224925, "value": "Tag1"},
                {"id": 501, "fieldId": "field-102", "entityId": 224925, "value": "Tag2"},
            ],
        )
    )
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--unset-value",
            "Tags",
            "Tag1",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["data"]["deleted"] == 1


def test_entry_field_set_json(respx_mock: respx.MockRouter) -> None:
    """--set-json batch updates fields."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(return_value=Response(200, json=FIELD_VALUE_RESPONSE))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set-json",
            '{"Status": "Active"}',
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert len(payload["data"]["created"]) == 1


def test_entry_field_get(respx_mock: respx.MockRouter) -> None:
    """--get retrieves field values via V2 API."""
    setup_list_mocks(respx_mock)
    mock_v2_entry_fields(
        respx_mock,
        [
            {
                "id": "field-100",
                "name": "Status",
                "type": "list",
                "enrichmentSource": None,
                "value": {"data": "Active", "type": "text"},
            },
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "Status"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["data"]["fields"]["Status"] == "Active"


def test_entry_field_get_output_format(respx_mock: respx.MockRouter) -> None:
    """--get returns {fields: {name: value}} format."""
    setup_list_mocks(respx_mock)
    mock_v2_entry_fields(
        respx_mock,
        [
            {
                "id": "field-100",
                "name": "Status",
                "type": "list",
                "enrichmentSource": None,
                "value": {"data": "Active", "type": "text"},
            },
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "Status"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert "fields" in payload["data"]
    assert "Status" in payload["data"]["fields"]


def test_entry_field_get_resolves_field_names(respx_mock: respx.MockRouter) -> None:
    """--get with field ID outputs resolved field name as key."""
    setup_list_mocks(respx_mock)
    mock_v2_entry_fields(
        respx_mock,
        [
            {
                "id": "field-100",
                "name": "Status",
                "type": "list",
                "enrichmentSource": None,
                "value": {"data": "Active", "type": "text"},
            },
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "field-100"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert "Status" in payload["data"]["fields"]


def test_entry_field_get_resolves_person_fields(respx_mock: respx.MockRouter) -> None:
    """--get returns resolved person object, not raw ID."""
    setup_list_mocks(respx_mock)

    # Add a person-type field to the fields metadata (V2 + V1)
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    *FIELDS_RESPONSE,
                    {
                        "id": "field-200",
                        "name": "Owner",
                        "valueType": "person",
                        "allowsMultiple": False,
                    },
                ],
                "pagination": {},
            },
        )
    )
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    *FIELDS_RESPONSE_V1,
                    {"id": "field-200", "name": "Owner", "value_type": 6, "allows_multiple": False},
                ]
            },
        )
    )

    mock_v2_entry_fields(
        respx_mock,
        [
            {
                "id": "field-200",
                "name": "Owner",
                "type": "list",
                "enrichmentSource": None,
                "value": {
                    "data": {
                        "id": 26323038,
                        "firstName": "Avichay",
                        "lastName": "Nissenbaum",
                        "primaryEmailAddress": "avichay@lool.vc",
                        "type": "internal",
                    },
                    "type": "person",
                },
            },
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "Owner"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    owner = payload["data"]["fields"]["Owner"]
    assert owner["id"] == 26323038
    assert owner["firstName"] == "Avichay"
    assert owner["lastName"] == "Nissenbaum"


def test_entry_field_get_resolves_dropdown_fields(respx_mock: respx.MockRouter) -> None:
    """--get returns dropdown data matching list export format."""
    setup_list_mocks(respx_mock)

    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    *FIELDS_RESPONSE,
                    {
                        "id": "field-300",
                        "name": "Stage",
                        "valueType": "ranked-dropdown",
                        "allowsMultiple": False,
                        "dropdownOptions": [
                            {"id": 7, "text": "Passed", "rank": 8, "color": "green"},
                        ],
                    },
                ],
                "pagination": {},
            },
        )
    )
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    *FIELDS_RESPONSE_V1,
                    {"id": "field-300", "name": "Stage", "value_type": 6, "allows_multiple": False},
                ]
            },
        )
    )

    mock_v2_entry_fields(
        respx_mock,
        [
            {
                "id": "field-300",
                "name": "Stage",
                "type": "list",
                "enrichmentSource": None,
                "value": {
                    "data": {"dropdownOptionId": 7, "text": "Passed", "rank": 8, "color": "green"},
                    "type": "ranked-dropdown",
                },
            },
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "Stage"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    stage = payload["data"]["fields"]["Stage"]
    assert stage["dropdownOptionId"] == 7
    assert stage["text"] == "Passed"


def test_entry_field_get_null_for_missing_field(respx_mock: respx.MockRouter) -> None:
    """--get returns None for fields not set on the entry."""
    setup_list_mocks(respx_mock)
    mock_v2_entry_fields(respx_mock, [])

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "Status"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["data"]["fields"]["Status"] is None


def test_entry_field_get_multi_value_field(respx_mock: respx.MockRouter) -> None:
    """--get returns list for multi-value fields."""
    setup_list_mocks(respx_mock)

    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    *FIELDS_RESPONSE,
                    {
                        "id": "field-400",
                        "name": "Team",
                        "valueType": "person",
                        "allowsMultiple": True,
                    },
                ],
                "pagination": {},
            },
        )
    )
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    *FIELDS_RESPONSE_V1,
                    {"id": "field-400", "name": "Team", "value_type": 6, "allows_multiple": True},
                ]
            },
        )
    )

    mock_v2_entry_fields(
        respx_mock,
        [
            {
                "id": "field-400",
                "name": "Team",
                "type": "list",
                "enrichmentSource": None,
                "value": {
                    "data": [
                        {"id": 1, "firstName": "Alice", "lastName": "Smith", "type": "internal"},
                        {"id": 2, "firstName": "Bob", "lastName": "Jones", "type": "internal"},
                    ],
                    "type": "person-multi",
                },
            },
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "Team"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    team = payload["data"]["fields"]["Team"]
    assert isinstance(team, list)
    assert len(team) == 2
    assert team[0]["firstName"] == "Alice"
    assert team[1]["firstName"] == "Bob"


# ============================================================================
# Validation Tests
# ============================================================================


def test_entry_field_no_operation_error() -> None:
    """Must specify at least one operation."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["entry", "field", "Portfolio", "123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "No operation specified" in result.output


def test_entry_field_get_exclusive_with_set(respx_mock: respx.MockRouter) -> None:
    """--get cannot be combined with --set."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--get",
            "Status",
            "--set",
            "Status",
            "Active",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "--get cannot be combined" in result.output


def test_entry_field_get_exclusive_with_append(respx_mock: respx.MockRouter) -> None:
    """--get cannot be combined with --append."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--get",
            "Status",
            "--append",
            "Tags",
            "NewTag",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "--get cannot be combined" in result.output


def test_entry_field_get_exclusive_with_unset(respx_mock: respx.MockRouter) -> None:
    """--get cannot be combined with --unset."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["entry", "field", "Portfolio", "123", "--get", "Status", "--unset", "Status"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "--get cannot be combined" in result.output


def test_entry_field_get_exclusive_with_unset_value(
    respx_mock: respx.MockRouter,
) -> None:
    """--get cannot be combined with --unset-value."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--get",
            "Status",
            "--unset-value",
            "Tags",
            "Tag1",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "--get cannot be combined" in result.output


def test_entry_field_get_exclusive_with_set_json(respx_mock: respx.MockRouter) -> None:
    """--get cannot be combined with --set-json."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--get",
            "Status",
            "--set-json",
            '{"Status": "Active"}',
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "--get cannot be combined" in result.output


def test_entry_field_same_field_set_append_conflict(
    respx_mock: respx.MockRouter,
) -> None:
    """Same field in --set and --append raises error."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--set",
            "Tags",
            "New",
            "--append",
            "Tags",
            "Another",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field(s) in both --set and --append" in result.output


def test_entry_field_same_field_set_unset_conflict(
    respx_mock: respx.MockRouter,
) -> None:
    """Same field in --set and --unset raises error."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--set",
            "Status",
            "Active",
            "--unset",
            "Status",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field(s) in both --set and --unset" in result.output


def test_entry_field_same_field_append_unset_conflict(
    respx_mock: respx.MockRouter,
) -> None:
    """Same field in --append and --unset raises error."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--append",
            "Tags",
            "New",
            "--unset",
            "Tags",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field(s) in both --append and --unset" in result.output


def test_entry_field_same_field_set_unset_value_conflict(
    respx_mock: respx.MockRouter,
) -> None:
    """Same field in --set and --unset-value raises error."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--set",
            "Tags",
            "New",
            "--unset-value",
            "Tags",
            "Old",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field(s) in both --set and --unset-value" in result.output


def test_entry_field_same_field_unset_unset_value_conflict(
    respx_mock: respx.MockRouter,
) -> None:
    """Same field in --unset and --unset-value raises error (redundant)."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--unset",
            "Tags",
            "--unset-value",
            "Tags",
            "Old",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field(s) in both --unset and --unset-value" in result.output


def test_entry_field_append_unset_value_same_field_allowed(
    respx_mock: respx.MockRouter,
) -> None:
    """Same field in --append and --unset-value is allowed (swap pattern)."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-103", "entityId": 224925, "value": 7}],
        )
    )
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-103"
    ).mock(
        return_value=Response(
            200, json={"id": 501, "fieldId": "field-103", "entityId": 224925, "value": 8}
        )
    )
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--append",
            "Investors",
            "8",
            "--unset-value",
            "Investors",
            "7",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert len(payload["data"]["created"]) == 1
    assert payload["data"]["deleted"] == 1


# ============================================================================
# Error Handling Tests
# ============================================================================


def test_entry_field_malformed_set_json(respx_mock: respx.MockRouter) -> None:
    """Malformed --set-json gives clear error message."""
    setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--set-json",
            "{invalid json}",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Invalid JSON" in result.output


def test_entry_field_set_json_non_object(respx_mock: respx.MockRouter) -> None:
    """--set-json with array or primitive raises error (must be object)."""
    setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            "123",
            "--set-json",
            '["Status", "Active"]',
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "must be a JSON object" in result.output


def test_entry_field_empty_set_json(respx_mock: respx.MockRouter) -> None:
    """Empty --set-json '{}' is valid but no-op."""
    setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", "123", "--set-json", "{}"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    # No created or deleted since empty
    assert "created" not in payload["data"]
    assert "deleted" not in payload["data"]


def test_entry_field_unset_value_idempotent(respx_mock: respx.MockRouter) -> None:
    """--unset-value with nonexistent value succeeds silently."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            "123",
            "--unset-value",
            "Tags",
            "NonexistentTag",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    # Should succeed but with warning (visible in stderr)


# ============================================================================
# Field ID Auto-Detection Tests
# ============================================================================


def test_entry_field_id_auto_detection(respx_mock: respx.MockRouter) -> None:
    """Field IDs (field-123) detected and used directly."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(return_value=Response(200, json=FIELD_VALUE_RESPONSE))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "field-100",
            "Active",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output


def test_entry_field_enriched_id_auto_detection(respx_mock: respx.MockRouter) -> None:
    """Enriched field IDs (affinity-data-*) detected and used directly."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/affinity-data-location"
    ).mock(
        return_value=Response(
            200,
            json={
                "id": 999,
                "fieldId": "affinity-data-location",
                "entityId": 224925,
                "value": "NYC",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "affinity-data-location",
            "NYC",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output


# ============================================================================
# JSON Output Format Tests
# ============================================================================


def test_entry_field_json_output_format(respx_mock: respx.MockRouter) -> None:
    """JSON output includes command context and created/deleted counts."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-100", "entityId": 224925, "value": "Old"}],
        )
    )
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(return_value=Response(200, json=FIELD_VALUE_RESPONSE))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())

    # Check structure
    assert "data" in payload
    assert "command" in payload
    assert payload["command"]["name"] == "entry field"
    assert payload["command"]["inputs"]["entryId"] == ENTRY_ID
    assert "created" in payload["data"]
    # --set replaces through the V2 write; nothing is deleted first
    assert "deleted" not in payload["data"]


def test_entry_field_mixed_operations(respx_mock: respx.MockRouter) -> None:
    """--set and --unset can be combined on different fields."""
    patch = setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-101", "entityId": 224925, "value": "Low"}],
        )
    )
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(return_value=Response(200, json=FIELD_VALUE_RESPONSE))
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--unset",
            "Priority",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert len(payload["data"]["created"]) == 1
    assert len(payload["data"]["cleared"]) == 1
    assert patch.call_count == 1  # one all-or-nothing request
    assert [u["id"] for u in patched(patch)] == ["field-100", "field-101"]


def test_entry_field_multivalue_set_replaces(respx_mock: respx.MockRouter) -> None:
    """--set on multi-value field replaces all values (with warning)."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[
                {"id": 500, "fieldId": "field-102", "entityId": 224925, "value": "Tag1"},
                {"id": 501, "fieldId": "field-102", "entityId": 224925, "value": "Tag2"},
            ],
        )
    )
    delete = respx_mock.delete(url__regex=r".*/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-102"
    ).mock(
        return_value=Response(
            200, json={"id": 502, "fieldId": "field-102", "entityId": 224925, "value": "NewOnly"}
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Tags",
            "NewOnly",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    # Check warning was printed (goes to output in test environment)
    assert "Warning: Replaced 2 existing values" in result.output
    # Extract JSON from output (skip warning line)
    json_line = next(line for line in result.output.split("\n") if line.startswith("{"))
    payload = json.loads(json_line)
    # The V2 write replaces both values itself; no row is deleted first.
    assert "deleted" not in payload["data"]
    assert delete.call_count == 0


def test_entry_field_duplicate_field_in_set_operations(
    respx_mock: respx.MockRouter,
) -> None:
    """Same field in --set and --set-json raises error."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--set-json",
            '{"Status": "Inactive"}',
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field(s) in both --set and --set-json" in result.output


# ============================================================================
# Operation Order and Field Resolution Tests
# ============================================================================


def test_entry_field_numeric_field_name(respx_mock: respx.MockRouter) -> None:
    """Numeric-only field name (e.g., '2024') treated as name, not ID."""
    # Add a field named "2024" to the field metadata
    fields_with_numeric = [
        *FIELDS_RESPONSE,
        {"id": "field-200", "name": "2024", "valueType": "text", "allowsMultiple": False},
    ]
    respx_mock.get("https://api.affinity.co/v2/lists").mock(
        return_value=Response(200, json={"data": [LIST_RESPONSE], "pagination": {}})
    )
    # V1 API for accurate listSize
    respx_mock.get(f"https://api.affinity.co/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={
                "id": LIST_ID,
                "name": "Portfolio",
                "type": 0,
                "public": False,
                "owner_id": 100,
                "list_size": 100,
            },
        )
    )
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(200, json={"data": fields_with_numeric, "pagination": {}})
    )
    # V1 fields format for list_fields_for_list (with snake_case keys)
    fields_with_numeric_v1 = [
        *FIELDS_RESPONSE_V1,
        {"id": "field-200", "name": "2024", "value_type": 6, "allows_multiple": False},
    ]
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(200, json={"data": fields_with_numeric_v1})
    )
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    patch = mock_list_field_writes(respx_mock, LIST_ID, [])

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--set", "2024", "Completed"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    # The command resolved "2024" as a field name, not an ID
    assert payload["data"]["created"][0]["fieldId"] == "field-200"
    assert patched(patch)[0]["id"] == "field-200"


def test_entry_field_field_not_found(respx_mock: respx.MockRouter) -> None:
    """Field ID not on list returns error."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["entry", "field", "Portfolio", str(ENTRY_ID), "--get", "field-99999"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field 'field-99999' not found on list" in result.output


def test_entry_field_field_name_not_found(respx_mock: respx.MockRouter) -> None:
    """Field name not on list returns error."""
    setup_list_mocks(respx_mock)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["entry", "field", "Portfolio", str(ENTRY_ID), "--get", "NonExistentField"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "NonExistentField" in result.output


def test_entry_field_resolution_upfront(respx_mock: respx.MockRouter) -> None:
    """All field names resolved before any API calls (fail-fast).

    If one field in a batch is invalid, the command should error before any
    API calls are made (no partial updates).
    """
    setup_list_mocks(respx_mock)
    # Note: no field_values mock - if we get that far, the test should fail

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--set",
            "InvalidField",  # This doesn't exist
            "Value",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    # Should fail on field resolution, not during execution
    assert result.exit_code == 2
    assert "InvalidField" in result.output


def test_entry_field_operation_order(respx_mock: respx.MockRouter) -> None:
    """Order: --set/--set-json/--unset in one update-fields PATCH, then --append, then
    --unset-value (operations on different fields)."""
    patch = setup_list_mocks(respx_mock)
    call_order: list[str] = []
    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[
                {"id": 500, "fieldId": "field-100", "entityId": 224925, "value": "Old"},
                {"id": 501, "fieldId": "field-101", "entityId": 224925, "value": "Low"},
                {"id": 502, "fieldId": "field-103", "entityId": 224925, "value": 7},
            ],
        )
    )

    def track(name: str, response: Response):
        def handler(_request):
            call_order.append(name)
            return response

        return handler

    patch.mock(side_effect=track("patch", Response(200, json={"operation": "update-fields"})))
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-103"
    ).mock(side_effect=track("append", Response(204)))

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            # Given in "wrong" order on purpose
            "--unset",
            "Priority",
            "--append",
            "Investors",
            "8",
            "--set",
            "Status",
            "New",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert call_order == ["patch", "append"]
    assert [u["id"] for u in patched(patch)] == ["field-100", "field-101"]


def test_entry_field_field_name_like_id(respx_mock: respx.MockRouter) -> None:
    """Field literally named 'field-123' requires using actual field ID.

    If a field is named 'field-123' (unlikely but possible), the user must
    use the actual field ID to reference it, since 'field-123' pattern
    is interpreted as a field ID.
    """
    # Create field metadata with a field named "field-123"
    fields_with_weird_name = [
        *FIELDS_RESPONSE,
        {"id": "field-999", "name": "field-123", "valueType": "text", "allowsMultiple": False},
    ]
    respx_mock.get("https://api.affinity.co/v2/lists").mock(
        return_value=Response(200, json={"data": [LIST_RESPONSE], "pagination": {}})
    )
    # V1 API for accurate listSize
    respx_mock.get(f"https://api.affinity.co/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={
                "id": LIST_ID,
                "name": "Portfolio",
                "type": 0,
                "public": False,
                "owner_id": 100,
                "list_size": 100,
            },
        )
    )
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(200, json={"data": fields_with_weird_name, "pagination": {}})
    )
    # V1 fields format for list_fields_for_list (with snake_case keys)
    fields_with_weird_name_v1 = [
        *FIELDS_RESPONSE_V1,
        {"id": "field-999", "name": "field-123", "value_type": 6, "allows_multiple": False},
    ]
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(200, json={"data": fields_with_weird_name_v1})
    )

    runner = CliRunner()
    # Using "field-123" will be interpreted as a field ID, not a name
    # Since field-123 doesn't exist as an ID, it should error
    result = runner.invoke(
        cli,
        ["entry", "field", "Portfolio", str(ENTRY_ID), "--get", "field-123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "Field 'field-123' not found on list" in result.output

    # To access a field named "field-123", user must use the actual ID (field-999)
    mock_v2_entry_fields(
        respx_mock,
        [
            {
                "id": "field-999",
                "name": "field-123",
                "type": "list",
                "enrichmentSource": None,
                "value": {"data": "test", "type": "text"},
            },
        ],
    )

    result2 = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--get", "field-999"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result2.exit_code == 0, result2.output
    payload = json.loads(result2.output.strip())
    # The output key should be the field name, not the ID
    assert "field-123" in payload["data"]["fields"]


def test_entry_field_rejected_batch_changes_nothing(respx_mock: respx.MockRouter) -> None:
    """All --set values go out in one request, which Affinity applies all-or-nothing: a value
    the server rejects (one we can't check locally) leaves every field unchanged."""
    patch = setup_list_mocks(respx_mock)
    patch.mock(
        return_value=Response(
            400, json={"errors": [{"code": "validation", "message": "Invalid value for field"}]}
        )
    )
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    post = respx_mock.post(url__regex=r".*/list-entries/\d+/fields/.*").mock(
        return_value=Response(204)
    )

    result = CliRunner().invoke(
        cli,
        [
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--set",
            "Priority",
            "Whatever",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code != 0
    assert "nothing was changed" in " ".join(result.output.split())
    assert patch.call_count == 1
    assert post.call_count == 0


# ============================================================================
# Entity-Reference Field Tests (person, company, person-multi, company-multi)
# ============================================================================

# Extended field definitions with entity-reference types
# V1 numeric value types: 0=person, 1=company, 6=text
ENTITY_FIELDS_V1 = [
    *FIELDS_RESPONSE_V1,
    {"id": "field-200", "name": "Owner", "value_type": 0, "allows_multiple": False},
    {"id": "field-201", "name": "Team Members", "value_type": 0, "allows_multiple": True},
    {"id": "field-202", "name": "Parent Company", "value_type": 1, "allows_multiple": False},
    {"id": "field-203", "name": "Related Companies", "value_type": 1, "allows_multiple": True},
]

# V2 string value types
ENTITY_FIELDS_V2 = [
    *FIELDS_RESPONSE,
    {"id": "field-200", "name": "Owner", "valueType": "person", "allowsMultiple": False},
    {
        "id": "field-201",
        "name": "Team Members",
        "valueType": "person-multi",
        "allowsMultiple": True,
    },
    {"id": "field-202", "name": "Parent Company", "valueType": "company", "allowsMultiple": False},
    {
        "id": "field-203",
        "name": "Related Companies",
        "valueType": "company-multi",
        "allowsMultiple": True,
    },
]


def setup_entity_field_mocks(respx_mock: respx.MockRouter) -> Any:
    """Set up mocks with entity-reference field types."""
    respx_mock.get("https://api.affinity.co/v2/lists").mock(
        return_value=Response(200, json={"data": [LIST_RESPONSE], "pagination": {}})
    )
    respx_mock.get(f"https://api.affinity.co/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={
                "id": LIST_ID,
                "name": "Portfolio",
                "type": 0,
                "public": False,
                "owner_id": 100,
                "creator_id": 100,
                "list_size": 100,
            },
        )
    )
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(200, json={"data": ENTITY_FIELDS_V2, "pagination": {}})
    )
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(200, json={"data": ENTITY_FIELDS_V1})
    )
    return mock_list_field_writes(respx_mock, LIST_ID, ENTITY_FIELDS_V1)


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestEntryFieldEntityRefSet:
    """Tests for --set on person/company fields."""

    def test_set_person_field_wraps_id(self, respx_mock: respx.MockRouter) -> None:
        """--set Owner 26229794 sends {"id": 26229794} in V2 payload."""
        patch_route = setup_entity_field_mocks(respx_mock)
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(200, json=[])
        )

        # Capture the POST request to verify payload
        post_route = patch_route

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--set",
                "Owner",
                "26229794",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        # Verify the V2 API payload wraps the ID
        assert post_route.called
        request_body = patched(post_route)[0]
        assert request_body["value"]["type"] == "person"
        assert request_body["value"]["data"] == {"id": 26229794}

    def test_set_company_field_wraps_id(self, respx_mock: respx.MockRouter) -> None:
        """--set 'Parent Company' 789 sends {"id": 789} in V2 payload."""
        patch_route = setup_entity_field_mocks(respx_mock)
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(200, json=[])
        )

        post_route = patch_route

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--set",
                "Parent Company",
                "789",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        assert post_route.called
        request_body = patched(post_route)[0]
        assert request_body["value"]["type"] == "company"
        assert request_body["value"]["data"] == {"id": 789}

    def test_set_person_non_numeric_fails(self, respx_mock: respx.MockRouter) -> None:
        """--set Owner 'not-a-number' fails with validation error."""
        setup_entity_field_mocks(respx_mock)
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(200, json=[])
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--set",
                "Owner",
                "not-a-number",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 2
        assert "Invalid entity ID" in result.output


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestEntryFieldEntityRefSetJson:
    """Tests for --set-json on person-multi/company-multi fields."""

    def test_set_json_person_multi_list(self, respx_mock: respx.MockRouter) -> None:
        """--set-json with person-multi list wraps each ID."""
        patch_route = setup_entity_field_mocks(respx_mock)
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(200, json=[])
        )

        post_route = patch_route

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--set-json",
                '{"Team Members": ["111", "222"]}',
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        assert post_route.called
        request_body = patched(post_route)[0]
        assert request_body["value"]["type"] == "person-multi"
        assert request_body["value"]["data"] == [{"id": 111}, {"id": 222}]


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestEntryFieldEntityRefAppend:
    """Tests for --append on person-multi/company-multi fields."""

    def test_append_person_multi_merges(self, respx_mock: respx.MockRouter) -> None:
        """--append Team Members merges with existing, deduplicates."""
        setup_entity_field_mocks(respx_mock)

        # Existing team member (entity ID 111)
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 600,
                        "fieldId": "field-201",
                        "entityId": 224925,
                        "value": 111,
                    }
                ],
            )
        )

        post_route = respx_mock.post(
            f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-201"
        ).mock(
            return_value=Response(
                200,
                json={
                    "id": 703,
                    "fieldId": "field-201",
                    "entityId": 224925,
                    "value": [{"id": 111}, {"id": 222}],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--append",
                "Team Members",
                "222",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        assert post_route.called
        request_body = json.loads(post_route.calls[0].request.content)
        # Should contain both existing (111) and new (222)
        assert request_body["value"]["type"] == "person-multi"
        data = request_body["value"]["data"]
        ids = [item["id"] for item in data]
        assert 111 in ids
        assert 222 in ids

    def test_append_person_multi_deduplicates(self, respx_mock: respx.MockRouter) -> None:
        """--append with existing ID doesn't duplicate."""
        setup_entity_field_mocks(respx_mock)

        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 600,
                        "fieldId": "field-201",
                        "entityId": 224925,
                        "value": 111,
                    }
                ],
            )
        )

        post_route = respx_mock.post(
            f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-201"
        ).mock(
            return_value=Response(
                200,
                json={
                    "id": 703,
                    "fieldId": "field-201",
                    "entityId": 224925,
                    "value": [{"id": 111}],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--append",
                "Team Members",
                "111",  # Same as existing
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        # No-op short-circuit: appending an ID that already exists in
        # person-multi is now a no-op. No POST should be issued — preserves
        # a clean audit log on retries (CLI-SET-PHASE-ATOMICITY).
        assert not post_route.called

    def test_append_person_multi_empty_field(self, respx_mock: respx.MockRouter) -> None:
        """--append on empty person-multi field works."""
        setup_entity_field_mocks(respx_mock)

        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(200, json=[])
        )

        post_route = respx_mock.post(
            f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-201"
        ).mock(
            return_value=Response(
                200,
                json={
                    "id": 703,
                    "fieldId": "field-201",
                    "entityId": 224925,
                    "value": [{"id": 999}],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--append",
                "Team Members",
                "999",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        assert post_route.called
        request_body = json.loads(post_route.calls[0].request.content)
        assert request_body["value"]["data"] == [{"id": 999}]

    def test_append_company_multi_merges(self, respx_mock: respx.MockRouter) -> None:
        """--append on company-multi field merges existing + new."""
        setup_entity_field_mocks(respx_mock)

        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 610,
                        "fieldId": "field-203",
                        "entityId": 224925,
                        "value": 500,
                    }
                ],
            )
        )

        post_route = respx_mock.post(
            f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-203"
        ).mock(
            return_value=Response(
                200,
                json={
                    "id": 704,
                    "fieldId": "field-203",
                    "entityId": 224925,
                    "value": [{"id": 500}, {"id": 600}],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "entry",
                "field",
                "Portfolio",
                str(ENTRY_ID),
                "--append",
                "Related Companies",
                "600",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        assert post_route.called
        request_body = json.loads(post_route.calls[0].request.content)
        data = request_body["value"]["data"]
        ids = [item["id"] for item in data]
        assert 500 in ids
        assert 600 in ids


# ============================================================================
# Stale V1 field metadata (field just created; v1 /fields?list_id lags, v2 is current)
# ============================================================================

NEW_V2_LIST_FIELD = {
    "id": "field-200",
    "name": "Close Date",
    "valueType": "datetime",
    "type": "list",
}


def _setup_stale_v1_mocks(respx_mock: respx.MockRouter) -> respx.Route:
    setup_list_mocks(respx_mock)  # v1 /fields lists only field-100..102 (stale)
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(
            200, json={"data": [*FIELDS_RESPONSE, NEW_V2_LIST_FIELD], "pagination": {}}
        )
    )
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    return mock_list_field_writes(respx_mock, LIST_ID, FIELDS_RESPONSE_V1)


@pytest.mark.parametrize("field_spec", ["field-200", "Close Date"])
def test_entry_field_set_field_missing_from_stale_v1_uses_v2(
    respx_mock: respx.MockRouter, field_spec: str
) -> None:
    """A field Affinity's v1 listing doesn't show yet (observed stale for 3+ minutes after
    creation) is found via v2 instead of failing with 'not found on list'."""
    write = _setup_stale_v1_mocks(respx_mock)

    result = CliRunner().invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--set", field_spec, "2024-04-01"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert write.called
    assert patched(write) == [
        {"id": "field-200", "value": {"type": "datetime", "data": "2024-04-01T12:00:00Z"}}
    ]


def test_entry_field_set_unknown_field_still_not_found(respx_mock: respx.MockRouter) -> None:
    _setup_stale_v1_mocks(respx_mock)

    result = CliRunner().invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--set", "field-999", "x"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert json.loads(result.output.strip().splitlines()[-1])["error"]["type"] == "not_found"
