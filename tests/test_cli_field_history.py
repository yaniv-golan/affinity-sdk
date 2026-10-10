from __future__ import annotations

import json

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)


def test_field_history_by_person_id(respx_mock: respx.MockRouter) -> None:
    """List field value changes for a person."""
    # V1 API returns bare array with camelCase keys
    # HTTPClient normalizes to {"data": [...]} internally
    respx_mock.get("https://api.affinity.co/field-value-changes").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 101,
                    "fieldId": "field-123",  # camelCase, string format
                    "entityId": 456,
                    "listEntryId": None,
                    "actionType": 2,
                    "value": "Closed",
                    "changedAt": "2024-01-15T10:30:00Z",
                    "changer": {"id": 10, "type": 0, "firstName": "Jane", "lastName": "Doe"},
                }
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history", "field-123", "--person-id", "456"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    changes = payload["data"]["fieldValueChanges"]
    assert len(changes) == 1
    assert changes[0]["id"] == 101
    assert changes[0]["fieldId"] == "field-123"
    # Enum displayed as name, not integer
    assert changes[0]["actionType"] == "update"
    # Changer name flattened for table display
    assert changes[0]["changerName"] == "Jane Doe"


def test_field_history_by_company_id(respx_mock: respx.MockRouter) -> None:
    """List field value changes for a company."""
    respx_mock.get("https://api.affinity.co/field-value-changes").mock(
        return_value=Response(200, json=[])
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history", "field-123", "--company-id", "789"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["fieldValueChanges"] == []


def test_field_history_with_action_type_filter(respx_mock: respx.MockRouter) -> None:
    """Filter field value changes by action type."""
    respx_mock.get("https://api.affinity.co/field-value-changes").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 102,
                    "fieldId": "field-123",
                    "entityId": 456,
                    "listEntryId": None,
                    "actionType": 0,
                    "value": "Open",
                    "changedAt": "2024-01-10T09:00:00Z",
                }
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "field",
            "history",
            "field-123",
            "--person-id",
            "456",
            "--action-type",
            "create",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["fieldValueChanges"][0]["actionType"] == "create"


def test_field_history_with_max_results(respx_mock: respx.MockRouter) -> None:
    """Client-side max-results limiting."""
    respx_mock.get("https://api.affinity.co/field-value-changes").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "fieldId": "field-123",
                    "entityId": 456,
                    "listEntryId": None,
                    "actionType": 2,
                    "value": "A",
                    "changedAt": "2024-01-01T00:00:00Z",
                },
                {
                    "id": 2,
                    "fieldId": "field-123",
                    "entityId": 456,
                    "listEntryId": None,
                    "actionType": 2,
                    "value": "B",
                    "changedAt": "2024-01-02T00:00:00Z",
                },
                {
                    "id": 3,
                    "fieldId": "field-123",
                    "entityId": 456,
                    "listEntryId": None,
                    "actionType": 2,
                    "value": "C",
                    "changedAt": "2024-01-03T00:00:00Z",
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history", "field-123", "--person-id", "456", "--max-results", "2"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert len(payload["data"]["fieldValueChanges"]) == 2


def test_field_history_needs_a_bound_without_a_selector_and_at_most_one_selector() -> None:
    """No selector and no bound, or several selectors: usage error before any request."""
    runner = CliRunner()

    # No selector
    result = runner.invoke(
        cli,
        ["--json", "field", "history", "field-123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    assert "--changed-after" in result.output

    # Multiple selectors
    result = runner.invoke(
        cli,
        ["--json", "field", "history", "field-123", "--person-id", "1", "--company-id", "2"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    assert "only one" in result.output.lower()


@pytest.mark.req("CLI-FIELD-LS-LIST-ALIAS")
def test_field_ls_list_alias(respx_mock: respx.MockRouter) -> None:
    """--list alias works identically to --list-id on field ls."""
    respx_mock.get("https://api.affinity.co/fields").mock(return_value=Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "ls", "--list", "Pipeline"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    # Should not fail with "No such option: --list"
    assert "No such option" not in (result.output + str(result.exception or ""))


def test_field_history_missing_field_id() -> None:
    """Error when FIELD_ID argument is missing."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["field", "history", "--person-id", "456"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    # Click reports missing argument
    assert "field_id" in result.output.lower() or "missing argument" in result.output.lower()


V1_URL = "https://api.affinity.co/field-value-changes"
V2_URL = "https://api.affinity.co/v2/field-value-changes"
ENV = {"AFFINITY_API_KEY": "test-key"}


def _v1_row(change_id: int) -> dict[str, object]:
    return {
        "id": change_id,
        "field_id": 123,
        "entity_id": 456,
        "list_entry_id": 7,
        "action_type": 0,
        "value": "x",
        "changed_at": "2024-01-15T03:30:00.123456-07:00",
        "changer": None,
    }


def test_field_history_without_selector_sends_bound_order_and_limit(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(V1_URL).mock(return_value=Response(200, json=[_v1_row(1), _v1_row(2)]))
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "field",
            "history",
            "field-123",
            "--changed-after",
            "2024-01-01T00:00:00Z",
            "--order",
            "asc",
            "--max-results",
            "1",
        ],
        env=ENV,
    )
    assert result.exit_code == 0, result.output
    params = dict(route.calls[0].request.url.params)
    assert params == {
        "field_id": "123",
        "changed_after": "2024-01-01T00:00:00.000000Z",
        "order_by": "asc",
        "limit": "1",
    }
    payload = json.loads(result.output.strip())
    assert [c["id"] for c in payload["data"]["fieldValueChanges"]] == [1]  # client slice too
    assert payload["command"]["inputs"] == {"fieldId": "field-123"}
    assert payload["command"]["modifiers"]["order"] == "asc"


def test_field_history_max_results_alone_is_a_bound(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(V1_URL).mock(return_value=Response(200, json=[]))
    result = CliRunner().invoke(
        cli, ["--json", "field", "history", "field-123", "--max-results", "5"], env=ENV
    )
    assert result.exit_code == 0, result.output
    assert dict(route.calls[0].request.url.params) == {
        "field_id": "123",
        "limit": "5",
        "order_by": "desc",
    }


def _v2_row(change_id: int) -> dict[str, object]:
    return {
        "id": change_id,
        "field": {"id": "field-123", "entityType": "company", "name": "Status", "type": "list"},
        "entity": {"id": 456},
        "listEntry": {"id": 7, "listId": 9},
        "changer": {"id": 5, "firstName": "Ann", "lastName": "Lee", "emailAddress": "a@x.co"},
        "changedAt": "2024-01-15T10:30:00Z",
        "actionType": "update",
        "type": "dropdown",
        "value": {"referenceType": "entity", "id": 3, "text": "Won"},
    }


def test_field_changes_one_page_with_cursor(respx_mock: respx.MockRouter) -> None:
    next_url = f"{V2_URL}?cursor=abc"
    route = respx_mock.get(V2_URL).mock(
        return_value=Response(200, json={"data": [_v2_row(1)], "pagination": {"nextUrl": next_url}})
    )
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "field",
            "changes",
            "--field-id",
            "field-123",
            "--field-id",
            "affinity-data-location",
            "--list-entry-id",
            "7",
            "--action-type",
            "update",
            "--changed-after",
            "2024-01-01",
            "--order",
            "desc",
        ],
        env=ENV,
    )
    assert result.exit_code == 0, result.output
    params = dict(route.calls[0].request.url.params)
    assert params["filter"].startswith(
        "(field.id=field-123 | field.id=affinity-data-location) & listEntry.id=7 & changedAt>="
    )
    assert params["filter"].endswith(" & actionType=update")
    assert params["orderBy"] == "-changedAt"
    payload = json.loads(result.output.strip())
    row = payload["data"]["fieldValueChanges"][0]
    assert row == {
        "id": 1,
        "fieldId": "field-123",
        "fieldName": "Status",
        "fieldEntityType": "company",
        "fieldScope": "list",
        "entityId": 456,
        "listEntryId": 7,
        "listId": 9,
        "actionType": "update",
        "valueType": "dropdown",
        "value": {"referenceType": "entity", "id": 3, "text": "Won"},
        "changedAt": "2024-01-15T10:30:00Z",
        "changerId": 5,
        "changerName": "Ann Lee",
    }
    assert payload["meta"]["pagination"]["nextCursor"] == next_url


def test_field_changes_max_results_pages_and_drops_a_mid_page_cursor(
    respx_mock: respx.MockRouter,
) -> None:
    page1 = {"data": [_v2_row(1), _v2_row(2)], "pagination": {"nextUrl": f"{V2_URL}?cursor=p2"}}
    page2 = {"data": [_v2_row(3), _v2_row(4)], "pagination": {"nextUrl": f"{V2_URL}?cursor=p3"}}
    respx_mock.get(V2_URL, params={"cursor": "p2"}).mock(return_value=Response(200, json=page2))
    first = respx_mock.get(V2_URL).mock(return_value=Response(200, json=page1))
    result = CliRunner().invoke(cli, ["--json", "field", "changes", "--max-results", "3"], env=ENV)
    assert result.exit_code == 0, result.output
    assert dict(first.calls[0].request.url.params) == {"limit": "3"}
    payload = json.loads(result.output.strip())
    assert [r["id"] for r in payload["data"]["fieldValueChanges"]] == [1, 2, 3]
    assert payload["meta"].get("pagination") is None


def test_field_changes_cursor_cannot_be_combined_with_filters() -> None:
    result = CliRunner().invoke(
        cli,
        ["--json", "field", "changes", "--cursor", "https://x", "--changer-id", "5"],
        env=ENV,
    )
    assert result.exit_code == 2
    assert "--cursor" in result.output
