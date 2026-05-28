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
]

# V1 API format uses snake_case and numeric value types (6 = text)
FIELDS_RESPONSE_V1 = [
    {"id": "field-100", "name": "Status", "value_type": 6, "allows_multiple": False},
    {"id": "field-101", "name": "Priority", "value_type": 6, "allows_multiple": False},
    {"id": "field-102", "name": "Tags", "value_type": 6, "allows_multiple": True},
]

FIELD_VALUE_RESPONSE = {
    "id": 999,
    "fieldId": "field-100",
    "entityId": 224925,  # Some entity ID
    "value": "Active",
    "listEntryId": ENTRY_ID,
}


def setup_list_mocks(respx_mock: respx.MockRouter) -> None:
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
    """--append adds to multi-value field."""
    setup_list_mocks(respx_mock)

    # Existing tag value
    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-102", "entityId": 224925, "value": "Existing"}],
        )
    )
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-102"
    ).mock(
        return_value=Response(
            200, json={"id": 501, "fieldId": "field-102", "entityId": 224925, "value": "NewTag"}
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
            "Tags",
            "NewTag",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert "created" in payload["data"]
    # Append doesn't delete existing, so no deleted count
    assert "deleted" not in payload["data"]


def test_entry_field_unset(respx_mock: respx.MockRouter) -> None:
    """--unset removes all values for field."""
    setup_list_mocks(respx_mock)

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
    assert payload["data"]["deleted"] == 1


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
    """Same field in --append and --unset-value is allowed (tag swap pattern)."""
    setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-102", "entityId": 224925, "value": "OldTag"}],
        )
    )
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-102"
    ).mock(
        return_value=Response(
            200, json={"id": 501, "fieldId": "field-102", "entityId": 224925, "value": "NewTag"}
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
            "Tags",
            "NewTag",
            "--unset-value",
            "Tags",
            "OldTag",
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
    assert "deleted" in payload["data"]


def test_entry_field_mixed_operations(respx_mock: respx.MockRouter) -> None:
    """--set and --unset can be combined on different fields."""
    setup_list_mocks(respx_mock)

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
    assert payload["data"]["deleted"] == 1


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
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.delete("https://api.affinity.co/field-values/501").mock(
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
    assert payload["data"]["deleted"] == 2


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
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-200"
    ).mock(
        return_value=Response(
            200, json={"id": 999, "fieldId": "field-200", "entityId": 224925, "value": "Completed"}
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--set", "2024", "Completed"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    # Verify the field was created - the command resolved "2024" as a name, not an ID
    created = payload["data"]["created"][0]
    # The created object wraps the API response in a "data" key
    field_data = created.get("data", created)
    assert field_data.get("fieldId") == "field-200"


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
    """Operations execute in order: set/set-json → append → unset/unset-value.

    This test verifies that when combining operations on DIFFERENT fields,
    the order is correct:
    1. Set operations run first (Status field)
    2. Append operations run second (Tags field)
    3. Unset operations run last (Priority field)

    We verify this by checking the API calls are made in the correct sequence.
    """
    setup_list_mocks(respx_mock)

    # Track call order
    call_order: list[str] = []

    def track_field_values(_request):
        return Response(
            200,
            json=[
                {"id": 500, "fieldId": "field-100", "entityId": 224925, "value": "Old"},
                {"id": 501, "fieldId": "field-101", "entityId": 224925, "value": "Low"},
                {"id": 502, "fieldId": "field-102", "entityId": 224925, "value": "OldTag"},
            ],
        )

    def track_set(_request):
        call_order.append("set")
        return Response(
            200, json={"id": 600, "fieldId": "field-100", "entityId": 224925, "value": "New"}
        )

    def track_append(_request):
        call_order.append("append")
        return Response(
            200, json={"id": 601, "fieldId": "field-102", "entityId": 224925, "value": "NewTag"}
        )

    def track_delete(_request):
        call_order.append("unset")
        return Response(200, json={"success": True})

    respx_mock.get("https://api.affinity.co/field-values").mock(side_effect=track_field_values)
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(side_effect=track_set)
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-102"
    ).mock(side_effect=track_append)
    # Delete for set (existing Status value) and unset (Priority value)
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(side_effect=track_delete)
    respx_mock.delete("https://api.affinity.co/field-values/501").mock(side_effect=track_delete)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            # Specify operations in "wrong" order to verify implementation reorders them
            "--unset",
            "Priority",  # Should run LAST (different field)
            "--append",
            "Tags",
            "NewTag",  # Should run SECOND
            "--set",
            "Status",
            "New",  # Should run FIRST
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    # Verify order: set runs before append, append runs before unset
    # Note: set deletes existing first, so order includes delete calls
    assert call_order.index("set") < call_order.index("append")
    # The unset delete for Priority happens after append
    # Find the last unset (should be the Priority unset after refresh)
    append_index = call_order.index("append")
    # There should be at least one unset after append
    unset_after_append = [i for i, x in enumerate(call_order) if x == "unset" and i > append_index]
    assert len(unset_after_append) > 0, f"Expected unset after append, got: {call_order}"


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


def test_entry_field_partial_failure(respx_mock: respx.MockRouter) -> None:
    """SERVER-SIDE failure may still leave partial state — Affinity has no transactions.

    Client-side-validatable failures (bad person ID, unknown dropdown option,
    invalid datetime, etc.) now abort BEFORE any API write under the strict
    default introduced in v0.7 — see
    ``test_entry_field_invalid_person_aborts_before_any_write`` for that path.

    This test covers the orthogonal scenario: every value passed
    pre-validation client-side, but the server returns HTTP 400 mid-sequence
    (e.g. a constraint we can't check locally). In that case the prior
    --set has already committed and Affinity has no rollback. The CLI exits
    non-zero and surfaces the server error; the user should check the entry
    state after such errors.
    """
    setup_list_mocks(respx_mock)

    # Track what operations were attempted
    operations_attempted: list[str] = []

    def mock_field_values(_request):
        return Response(200, json=[])

    def mock_first_field_success(_request):
        operations_attempted.append("field-100")
        return Response(
            200, json={"id": 600, "fieldId": "field-100", "entityId": 224925, "value": "Active"}
        )

    def mock_second_field_failure(_request):
        operations_attempted.append("field-101")
        # Simulate API error (e.g., invalid value for field type)
        return Response(
            400,
            json={"error": {"message": "Invalid value for dropdown field"}},
        )

    respx_mock.get("https://api.affinity.co/field-values").mock(side_effect=mock_field_values)
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(side_effect=mock_first_field_success)
    respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-101"
    ).mock(side_effect=mock_second_field_failure)

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
            "Active",  # This will succeed
            "--set",
            "Priority",
            "InvalidValue",  # This will fail
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    # Command should fail
    assert result.exit_code != 0

    # Both operations were attempted (first succeeded before second failed)
    assert "field-100" in operations_attempted
    assert "field-101" in operations_attempted

    # Error message should be present
    assert "Invalid value" in result.output or "error" in result.output.lower()


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


def setup_entity_field_mocks(respx_mock: respx.MockRouter) -> None:
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


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestEntryFieldEntityRefSet:
    """Tests for --set on person/company fields."""

    def test_set_person_field_wraps_id(self, respx_mock: respx.MockRouter) -> None:
        """--set Owner 26229794 sends {"id": 26229794} in V2 payload."""
        setup_entity_field_mocks(respx_mock)
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(200, json=[])
        )

        # Capture the POST request to verify payload
        post_route = respx_mock.post(
            f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-200"
        ).mock(
            return_value=Response(
                200,
                json={
                    "id": 700,
                    "fieldId": "field-200",
                    "entityId": 224925,
                    "value": {"id": 26229794},
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
                "Owner",
                "26229794",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        # Verify the V2 API payload wraps the ID
        assert post_route.called
        request_body = json.loads(post_route.calls[0].request.content)
        assert request_body["value"]["type"] == "person"
        assert request_body["value"]["data"] == {"id": 26229794}

    def test_set_company_field_wraps_id(self, respx_mock: respx.MockRouter) -> None:
        """--set 'Parent Company' 789 sends {"id": 789} in V2 payload."""
        setup_entity_field_mocks(respx_mock)
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(200, json=[])
        )

        post_route = respx_mock.post(
            f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-202"
        ).mock(
            return_value=Response(
                200,
                json={
                    "id": 701,
                    "fieldId": "field-202",
                    "entityId": 224925,
                    "value": {"id": 789},
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
                "Parent Company",
                "789",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        assert post_route.called
        request_body = json.loads(post_route.calls[0].request.content)
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
                    "id": 702,
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
                "--set-json",
                '{"Team Members": ["111", "222"]}',
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )

        assert result.exit_code == 0, result.output
        assert post_route.called
        request_body = json.loads(post_route.calls[0].request.content)
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
