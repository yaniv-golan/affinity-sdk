"""Tests for CSV export functionality in person, company, opportunity, and list commands.

CSV output goes to stdout. Use shell redirection to save to file:
    xaffinity person ls --csv > people.csv
"""

from __future__ import annotations

import csv
import io
import json

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


def _parse_csv_output(output: str) -> list[dict[str, str]]:
    """Parse CSV output from stdout."""
    reader = csv.DictReader(io.StringIO(output))
    return list(reader)


# ==============================================================================
# Mutual Exclusivity Tests
# ==============================================================================


def test_csv_and_json_mutually_exclusive_person_ls() -> None:
    """Test that --csv and --json are mutually exclusive for person ls."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "person", "ls", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--csv and --json are mutually exclusive" in payload["error"]["message"]


def test_csv_and_json_mutually_exclusive_company_ls() -> None:
    """Test that --csv and --json are mutually exclusive for company ls."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "company", "ls", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--csv and --json are mutually exclusive" in payload["error"]["message"]


def test_csv_and_json_mutually_exclusive_opportunity_ls() -> None:
    """Test that --csv and --json are mutually exclusive for opportunity ls."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "ls", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--csv and --json are mutually exclusive" in payload["error"]["message"]


def test_csv_and_json_mutually_exclusive_list_export(respx_mock: respx.MockRouter) -> None:
    """Test that --csv and --json are mutually exclusive for list export."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Pipeline",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 10,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--csv", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--csv and --json are mutually exclusive" in payload["error"]["message"]


# ==============================================================================
# CSV Output Isolation Tests
# ==============================================================================


def test_csv_output_is_clean_csv_format(respx_mock: respx.MockRouter) -> None:
    """Test that CSV output is clean CSV without progress/status messages mixed in."""
    respx_mock.get("https://api.affinity.co/v2/persons").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "primaryEmailAddress": "alice@example.com",
                    },
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["person", "ls", "--all", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0

    # CSV data should be in stdout
    assert "id,name,primaryEmail" in result.output
    assert "Alice Smith" in result.output

    # Output should be valid CSV: first line is header, subsequent lines are data
    lines = result.output.strip().split("\n")
    assert len(lines) >= 2  # header + at least one data row
    assert lines[0] == "id,name,primaryEmail,emails"  # exact header

    # Output should NOT contain progress indicators or non-CSV content
    assert "Fetching" not in result.output
    assert "..." not in result.output  # progress dots
    assert "Error" not in result.output


# ==============================================================================
# Person CSV Export Tests
# ==============================================================================


def test_person_ls_csv_basic(respx_mock: respx.MockRouter) -> None:
    """Test basic CSV export for person ls command outputs to stdout."""
    respx_mock.get("https://api.affinity.co/v2/persons").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "primaryEmailAddress": "alice@example.com",
                    },
                    {
                        "id": 2,
                        "firstName": "Bob",
                        "lastName": "Jones",
                        "primaryEmailAddress": "bob@example.com",
                    },
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["person", "ls", "--all", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    rows = _parse_csv_output(result.output)
    assert len(rows) == 2
    assert rows[0]["id"] == "1"
    assert rows[0]["name"] == "Alice Smith"
    assert rows[0]["primaryEmail"] == "alice@example.com"
    assert rows[1]["id"] == "2"
    assert rows[1]["name"] == "Bob Jones"


def test_person_ls_csv_with_bom(respx_mock: respx.MockRouter) -> None:
    """Test CSV export with BOM for person ls command."""
    respx_mock.get("https://api.affinity.co/v2/persons").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "primaryEmailAddress": "alice@example.com",
                    }
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["person", "ls", "--all", "--csv", "--csv-bom"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    # Check BOM is present at start of output
    assert result.output.startswith("\ufeff")  # UTF-8 BOM as string


def test_person_ls_csv_empty_results(respx_mock: respx.MockRouter) -> None:
    """Test CSV export with empty results for person ls command."""
    respx_mock.get("https://api.affinity.co/v2/persons").mock(
        return_value=Response(
            200,
            json={
                "data": [],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["person", "ls", "--all", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    # Empty output or just header
    assert result.output.strip() == "" or len(result.output.strip().split("\n")) <= 1


# ==============================================================================
# Company CSV Export Tests
# ==============================================================================


def test_company_ls_csv_basic(respx_mock: respx.MockRouter) -> None:
    """Test basic CSV export for company ls command outputs to stdout."""
    respx_mock.get("https://api.affinity.co/v2/companies").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 100,
                        "name": "Acme Corp",
                        "domain": "acme.com",
                    },
                    {
                        "id": 101,
                        "name": "Beta Inc",
                        "domain": "beta.com",
                    },
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["company", "ls", "--all", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    rows = _parse_csv_output(result.output)
    assert len(rows) == 2
    assert rows[0]["id"] == "100"
    assert rows[0]["name"] == "Acme Corp"
    assert rows[0]["domain"] == "acme.com"
    assert rows[1]["id"] == "101"
    assert rows[1]["name"] == "Beta Inc"


def test_company_ls_csv_with_bom(respx_mock: respx.MockRouter) -> None:
    """Test CSV export with BOM for company ls command."""
    respx_mock.get("https://api.affinity.co/v2/companies").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 100,
                        "name": "Acme Corp",
                        "domain": "acme.com",
                    }
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["company", "ls", "--all", "--csv", "--csv-bom"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    assert result.output.startswith("\ufeff")  # UTF-8 BOM as string


def test_company_ls_csv_empty_results(respx_mock: respx.MockRouter) -> None:
    """Test CSV export with empty results for company ls command."""
    respx_mock.get("https://api.affinity.co/v2/companies").mock(
        return_value=Response(
            200,
            json={
                "data": [],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["company", "ls", "--all", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    assert result.output.strip() == "" or len(result.output.strip().split("\n")) <= 1


# ==============================================================================
# Opportunity CSV Export Tests
# ==============================================================================


def test_opportunity_ls_csv_basic(respx_mock: respx.MockRouter) -> None:
    """Test basic CSV export for opportunity ls command outputs to stdout."""
    respx_mock.get("https://api.affinity.co/v2/opportunities").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 10,
                        "name": "Seed Round",
                        "listId": 41780,
                    },
                    {
                        "id": 11,
                        "name": "Series A",
                        "listId": 41780,
                    },
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["opportunity", "ls", "--all", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    rows = _parse_csv_output(result.output)
    assert len(rows) == 2
    assert rows[0]["id"] == "10"
    assert rows[0]["name"] == "Seed Round"
    assert rows[0]["listId"] == "41780"
    assert rows[1]["id"] == "11"
    assert rows[1]["name"] == "Series A"


def test_opportunity_ls_csv_with_bom(respx_mock: respx.MockRouter) -> None:
    """Test CSV export with BOM for opportunity ls command."""
    respx_mock.get("https://api.affinity.co/v2/opportunities").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 10,
                        "name": "Seed Round",
                        "listId": 41780,
                    }
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["opportunity", "ls", "--all", "--csv", "--csv-bom"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    assert result.output.startswith("\ufeff")  # UTF-8 BOM as string


def test_opportunity_ls_csv_empty_results(respx_mock: respx.MockRouter) -> None:
    """Test CSV export with empty results for opportunity ls command."""
    respx_mock.get("https://api.affinity.co/v2/opportunities").mock(
        return_value=Response(
            200,
            json={
                "data": [],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["opportunity", "ls", "--all", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    assert result.output.strip() == "" or len(result.output.strip().split("\n")) <= 1


# ==============================================================================
# List Export with --expand Tests
# ==============================================================================


def test_list_export_expand_invalid_on_person_list(
    respx_mock: respx.MockRouter,
) -> None:
    """Test that --expand people fails on a person list (only companies is valid)."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Contacts",
                "type": 0,  # person
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 10,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "persons", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert payload["ok"] is False
    assert "not valid for person lists" in payload["error"]["message"]
    assert payload["error"]["details"]["validExpand"] == [
        "companies",
        "interactions",
        "opportunities",
    ]


def test_list_export_expand_invalid_on_company_list(
    respx_mock: respx.MockRouter,
) -> None:
    """Test that --expand companies fails on a company list (only people is valid)."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Organizations",
                "type": 1,  # organization
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 10,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "companies", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert payload["ok"] is False
    assert "not valid for company lists" in payload["error"]["message"]
    assert payload["error"]["details"]["validExpand"] == [
        "interactions",
        "opportunities",
        "persons",
    ]


def test_list_export_expand_cursor_combination_fails(
    respx_mock: respx.MockRouter,
) -> None:
    """Test that --cursor cannot be combined with --expand."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Pipeline",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 10,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "export",
            "12345",
            "--expand",
            "persons",
            "--cursor",
            "abc123",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert payload["ok"] is False
    assert "--cursor cannot be combined with" in payload["error"]["message"]


def test_list_export_csv_to_stdout(respx_mock: respx.MockRouter) -> None:
    """Test list export with --csv outputs CSV to stdout."""
    # Mock list metadata
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Pipeline",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 2,
            },
        )
    )

    # Mock fields (V2)
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [{"id": "f1", "name": "Status", "type": "dropdown", "valueType": None}],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get("https://api.affinity.co/fields").mock(return_value=Response(200, json=[]))

    # Mock list entries
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1001,
                        "listId": 12345,
                        "creatorId": 1,
                        "type": "opportunity",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "entity": {"id": 5001, "name": "Deal One"},
                        "fields": {"data": {"f1": "Active"}},
                    },
                    {
                        "id": 1002,
                        "listId": 12345,
                        "creatorId": 1,
                        "type": "opportunity",
                        "createdAt": "2024-01-02T00:00:00Z",
                        "entity": {"id": 5002, "name": "Deal Two"},
                        "fields": {"data": {"f1": "Closed"}},
                    },
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["list", "export", "12345", "--csv", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    rows = _parse_csv_output(result.output)
    assert len(rows) == 2
    assert rows[0]["listEntryId"] == "1001"
    assert rows[0]["entityId"] == "5001"
    assert rows[0]["entityName"] == "Deal One"


def test_list_export_expand_json_output(respx_mock: respx.MockRouter) -> None:
    """Test list export with --expand produces JSON with nested arrays."""
    # Mock list metadata
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Pipeline",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 1,
            },
        )
    )

    # Mock fields (V2)
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get("https://api.affinity.co/fields").mock(return_value=Response(200, json=[]))

    # Mock list entries
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1001,
                        "listId": 12345,
                        "creatorId": 1,
                        "type": "opportunity",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "entity": {"id": 5001, "name": "Deal One"},
                        "fields": {"data": {}},
                    }
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    # Mock V1 opportunity get for associations
    respx_mock.get("https://api.affinity.co/opportunities/5001").mock(
        return_value=Response(
            200,
            json={
                "id": 5001,
                "name": "Deal One",
                "list_id": 12345,
                "person_ids": [101],
                "organization_ids": [201],
            },
        )
    )

    # Mock V2 batch lookup for persons
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/persons\?ids=.*").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 101,
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "emails": ["alice@example.com"],
                        "type": "internal",
                    }
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )

    # Mock V2 batch lookup for companies
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/companies\?ids=.*").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 201,
                        "name": "Acme Corp",
                        "domain": "acme.com",
                        "domains": ["acme.com"],
                    }
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "export",
            "12345",
            "--expand",
            "persons",
            "--expand",
            "companies",
            "--all",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())

    # Check nested arrays
    rows = payload["data"]["rows"]
    assert len(rows) == 1
    assert "persons" in rows[0]
    assert "companies" in rows[0]
    assert len(rows[0]["persons"]) == 1
    assert len(rows[0]["companies"]) == 1
    assert rows[0]["persons"][0]["id"] == 101
    assert rows[0]["persons"][0]["name"] == "Alice Smith"
    assert rows[0]["companies"][0]["id"] == 201
    assert rows[0]["companies"][0]["name"] == "Acme Corp"

    # Check summary data
    assert payload["data"]["entriesProcessed"] == 1
    assert payload["data"]["associationsFetched"]["persons"] == 1
    assert payload["data"]["associationsFetched"]["companies"] == 1


def test_list_export_dry_run_with_expand(respx_mock: respx.MockRouter) -> None:
    """Test --dry-run output includes expand info."""
    # Mock list metadata with entry count
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Pipeline",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 50,
            },
        )
    )

    # Mock fields (V2)
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get("https://api.affinity.co/fields").mock(return_value=Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "export",
            "12345",
            "--expand",
            "persons",
            "--expand",
            "companies",
            "--dry-run",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())

    assert "expand" in payload["data"]
    assert sorted(payload["data"]["expand"]) == ["companies", "persons"]
    assert payload["data"]["expandMaxResults"] == 100
    assert "estimatedApiCalls" in payload["data"]
    assert "get_associations" in payload["data"]["estimatedApiCalls"]["note"]

    # Verify listName and estimatedEntries are included
    assert payload["data"]["listName"] == "Pipeline"
    assert payload["data"]["estimatedEntries"] == 50


def test_list_export_expand_fields_requires_expand() -> None:
    """Test --expand-fields without --expand fails with clear error."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "export",
            "12345",
            "--expand-fields",
            "Status",
            "--all",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--expand-fields and --expand-field-type require --expand" in payload["error"]["message"]
    assert payload["error"]["type"] == "usage_error"


def test_list_export_expand_field_type_requires_expand() -> None:
    """Test --expand-field-type without --expand fails with clear error."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "export",
            "12345",
            "--expand-field-type",
            "global",
            "--all",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--expand-fields and --expand-field-type require --expand" in payload["error"]["message"]
    assert payload["error"]["type"] == "usage_error"


def test_list_export_expand_filter_requires_expand() -> None:
    """Test --expand-filter without --expand fails with clear error."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand-filter", "name=Alice", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--expand-filter requires --expand" in payload["error"]["message"]
    assert payload["error"]["type"] == "usage_error"


def test_list_export_expand_opportunities_list_requires_expand_opportunities(
    respx_mock: respx.MockRouter,
) -> None:
    """Test --expand-opportunities-list requires --expand opportunities."""
    # Mock list lookup
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Contacts",
                "type": 0,  # person
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 10,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "export",
            "12345",
            "--expand",
            "companies",  # Using companies, not opportunities
            "--expand-opportunities-list",
            "Pipeline",
            "--all",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    expected_msg = "--expand-opportunities-list requires --expand opportunities"
    assert expected_msg in payload["error"]["message"]
    assert payload["error"]["type"] == "usage_error"


def test_list_export_expand_opportunities_valid_on_person_list(
    respx_mock: respx.MockRouter,
) -> None:
    """Test --expand opportunities is valid on person lists (no error)."""
    # Mock person list
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Contacts",
                "type": 0,  # person
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 1,
            },
        )
    )

    # Mock fields (V2)
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json={"fields": []})
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get("https://api.affinity.co/fields").mock(return_value=Response(200, json=[]))

    # Mock list entries
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(
            200,
            json={
                "data": [],  # Empty for this test - just checking validation passes
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "opportunities", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    # Should not fail with invalid expand error
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True


def test_list_export_expand_opportunities_invalid_on_opportunity_list(
    respx_mock: respx.MockRouter,
) -> None:
    """Test --expand opportunities is NOT valid on opportunity lists."""
    # Mock opportunity list
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Pipeline",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 10,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "opportunities", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert payload["ok"] is False
    assert "not valid for opportunity lists" in payload["error"]["message"]
    # Valid values should be persons, companies (not opportunities)
    assert "opportunities" not in payload["error"]["details"]["validExpand"]


# ==============================================================================
# Output Flag Conflict Tests (Additional scenarios)
# ==============================================================================


def test_csv_bom_auto_enables_csv(respx_mock: respx.MockRouter) -> None:
    """Test that --csv-bom auto-enables CSV output when no format is specified."""
    respx_mock.get("https://api.affinity.co/v2/persons").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "primaryEmailAddress": "alice@example.com",
                    }
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    # Only --csv-bom, no --csv or --output csv
    result = runner.invoke(
        cli,
        ["person", "ls", "--all", "--csv-bom"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0
    # Should output CSV with BOM
    assert result.output.startswith("\ufeff")
    assert "id,name,primaryEmail" in result.output


def test_csv_bom_conflicts_with_json() -> None:
    """Test that --csv-bom conflicts with --output json."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["person", "ls", "--output", "json", "--csv-bom"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    assert "--csv-bom and --output json are mutually exclusive" in result.output


def test_csv_bom_conflicts_with_json_flag() -> None:
    """Test that --csv-bom conflicts with --json flag."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["person", "ls", "--json", "--csv-bom"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--csv-bom and --json are mutually exclusive" in payload["error"]["message"]


def test_global_output_csv_conflicts_with_command_output_json() -> None:
    """Test that global --output csv conflicts with command --output json."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--output", "json", "person", "ls", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    payload = json.loads(result.output.strip())
    assert "--csv and --output json are mutually exclusive" in payload["error"]["message"]


def test_csv_header_conflicts_with_json() -> None:
    """Test that --csv-header conflicts with --json for list export.

    Note: When --csv-header is processed first, it sets ctx.output="csv",
    so the error is emitted as text (not JSON).
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["list", "export", "12345", "--csv-header", "ids", "--json"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    # Error is text because --csv-header set ctx.output="csv" before --json
    assert "--json and --csv-header are mutually exclusive" in result.output


def test_csv_mode_conflicts_with_json() -> None:
    """Test that --csv-mode conflicts with --json for list export.

    Note: When --csv-mode is processed first, it sets ctx.output="csv",
    so the error is emitted as text (not JSON).
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["list", "export", "12345", "--csv-mode", "nested", "--json"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    # Error is text because --csv-mode set ctx.output="csv" before --json
    assert "--json and --csv-mode are mutually exclusive" in result.output
