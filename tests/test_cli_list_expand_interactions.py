"""Integration tests for list export --expand interactions.

Tests the CLI command for exporting list entries with interaction date summaries.
"""

from __future__ import annotations

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


# ==============================================================================
# Test Data
# ==============================================================================

MOCK_LIST_COMPANY = {
    "id": 12345,
    "name": "Pipeline",
    "type": 1,  # ListType.COMPANY = 1 (not 8!)
    "public": False,
    "owner_id": 1,
    "creator_id": 1,
    "list_size": 2,
}

MOCK_LIST_PERSON = {
    "id": 12346,
    "name": "Contacts",
    "type": 0,  # ListType.PERSON = 0
    "public": False,
    "owner_id": 1,
    "creator_id": 1,
    "list_size": 1,
}

MOCK_LIST_ENTRIES_COMPANY = {
    "data": [
        {
            "id": 1001,
            "listId": 12345,
            "type": "company",
            "createdAt": "2026-01-01T00:00:00Z",
            "entity": {
                "id": 789,
                "name": "Acme Corp",
                "domain": "acme.com",
            },
            "fields": {"data": {}},
        },
    ],
    "pagination": {"nextUrl": None},
}

MOCK_COMPANY_WITH_INTERACTIONS = {
    "id": 789,
    "name": "Acme Corp",
    "domain": "acme.com",
    "interaction_dates": {
        "last_event_date": "2026-01-10T10:00:00Z",
        "next_event_date": "2026-01-25T14:00:00Z",
        "last_email_date": "2026-01-12T09:30:00Z",
        "last_interaction_date": "2026-01-12T09:30:00Z",
    },
    "interactions": {
        "last_event": {"person_ids": [101, 102]},
        "next_event": {"person_ids": [101]},
    },
}

# Team member names are read with persons.get (V2)
MOCK_PERSON_V2 = {
    "id": 101,
    "firstName": "Alice",
    "lastName": "Smith",
    "primaryEmailAddress": "alice@example.com",
    "emailAddresses": ["alice@example.com"],
    "type": "external",
}

MOCK_LIST_FIELDS = {
    "data": [],
    "pagination": {"nextUrl": None},
}


# ==============================================================================
# JSON Output Tests
# ==============================================================================


def test_list_export_expand_interactions_json(respx_mock: respx.MockRouter) -> None:
    """Test list export with --expand interactions in JSON format."""
    # Mock list endpoint
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(200, json=MOCK_LIST_COMPANY)
    )

    # Mock list fields endpoint (V2)
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json=MOCK_LIST_FIELDS)
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(200, json={"data": []})
    )

    # Mock list entries endpoint
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(200, json=MOCK_LIST_ENTRIES_COMPANY)
    )

    # Mock company get with interaction_dates (V1 API)
    # Use URL pattern to match with query params
    respx_mock.get(url__regex=r"https://api\.affinity\.co/organizations/789.*").mock(
        return_value=Response(200, json=MOCK_COMPANY_WITH_INTERACTIONS)
    )

    # Mock person lookups for team member resolution
    respx_mock.get("https://api.affinity.co/v2/persons/101").mock(
        return_value=Response(200, json=MOCK_PERSON_V2)
    )
    respx_mock.get("https://api.affinity.co/v2/persons/102").mock(
        return_value=Response(200, json={**MOCK_PERSON_V2, "id": 102, "firstName": "Bob"})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "interactions"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, f"Failed with: {result.output}"
    output = json.loads(result.output)
    assert output["ok"] is True
    assert "data" in output
    data = output["data"]
    assert "rows" in data
    assert len(data["rows"]) == 1

    row = data["rows"][0]
    # The row should have interaction data attached
    assert "interactions" in row
    interactions = row["interactions"]
    assert interactions is not None
    assert "lastMeeting" in interactions
    assert interactions["lastMeeting"]["date"] == "2026-01-10T10:00:00+00:00"


def test_list_export_expand_interactions_with_persons(respx_mock: respx.MockRouter) -> None:
    """Test combining --expand interactions with --expand persons."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(200, json=MOCK_LIST_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json=MOCK_LIST_FIELDS)
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(200, json={"data": []})
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(200, json=MOCK_LIST_ENTRIES_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/organizations/789").mock(
        return_value=Response(200, json=MOCK_COMPANY_WITH_INTERACTIONS)
    )
    # Mock person lookups
    respx_mock.get("https://api.affinity.co/v2/persons/101").mock(
        return_value=Response(200, json=MOCK_PERSON_V2)
    )
    respx_mock.get("https://api.affinity.co/v2/persons/102").mock(
        return_value=Response(200, json={**MOCK_PERSON_V2, "id": 102, "firstName": "Bob"})
    )
    # Mock company associated persons
    respx_mock.get("https://api.affinity.co/companies/789/persons").mock(
        return_value=Response(200, json={"data": [{"id": 101}], "pagination": {"nextUrl": None}})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "interactions", "--expand", "persons"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, f"Failed with: {result.output}"


# ==============================================================================
# CSV Output Tests
# ==============================================================================


def test_list_export_expand_interactions_csv(respx_mock: respx.MockRouter) -> None:
    """Test list export with --expand interactions in CSV format."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(200, json=MOCK_LIST_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json=MOCK_LIST_FIELDS)
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(200, json={"data": []})
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(200, json=MOCK_LIST_ENTRIES_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/organizations/789").mock(
        return_value=Response(200, json=MOCK_COMPANY_WITH_INTERACTIONS)
    )
    respx_mock.get("https://api.affinity.co/v2/persons/101").mock(
        return_value=Response(200, json=MOCK_PERSON_V2)
    )
    respx_mock.get("https://api.affinity.co/v2/persons/102").mock(
        return_value=Response(200, json={**MOCK_PERSON_V2, "id": 102, "firstName": "Bob"})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["list", "export", "12345", "--expand", "interactions", "--csv"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, f"Failed with: {result.output}"
    lines = result.output.strip().split("\n")
    assert len(lines) >= 1

    header = lines[0]
    # Check for interaction columns
    assert "lastMeetingDate" in header or "_expand_interactions" in header


def test_list_export_csv_prints_expand_skip_warnings(respx_mock: respx.MockRouter) -> None:
    """--csv --expand-on-error skip: a failed lookup is reported on stderr, not left blank."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(200, json=MOCK_LIST_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json=MOCK_LIST_FIELDS)
    )
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(200, json={"data": []})
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(200, json=MOCK_LIST_ENTRIES_COMPANY)
    )
    respx_mock.get(url__regex=r"https://api\.affinity\.co/organizations/789.*").mock(
        return_value=Response(400, json={"errors": [{"message": "bad [request]"}]})
    )

    result = CliRunner().invoke(
        cli,
        [
            "list",
            "export",
            "12345",
            "--expand",
            "interactions",
            "--expand-on-error",
            "skip",
            "--csv",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, f"Failed with: {result.output}"
    # result.output carries stderr too (CliRunner mixes them on click 8.1 and later)
    assert "Warning: Skipped interaction dates for company 789" in result.output
    assert "[request]" in result.output  # printed as plain text, not Rich markup


# ==============================================================================
# Validation Tests
# ==============================================================================


def test_list_export_expand_interactions_is_valid_option() -> None:
    """Test that 'interactions' is a valid expand option."""
    runner = CliRunner()
    # Use --dry-run to avoid actual API calls
    result = runner.invoke(
        cli,
        ["list", "export", "12345", "--expand", "interactions", "--dry-run"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    # Should not fail with "invalid choice" error
    assert "Invalid value for '--expand'" not in result.output


def test_list_export_expand_invalid_option() -> None:
    """Test that invalid expand options are rejected."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["list", "export", "12345", "--expand", "invalid"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2
    # Check for key parts of the error message (handles Rich formatting with ANSI codes)
    assert "Invalid value" in result.output
    assert "--expand" in result.output


# ==============================================================================
# Edge Cases
# ==============================================================================


def test_list_export_expand_interactions_no_interaction_data(respx_mock: respx.MockRouter) -> None:
    """Test export when entity has no interaction dates."""
    company_no_interactions = {
        "id": 789,
        "name": "Acme Corp",
        "domain": "acme.com",
        "interaction_dates": None,
        "interactions": None,
    }

    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(200, json=MOCK_LIST_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json=MOCK_LIST_FIELDS)
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(200, json={"data": []})
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/list-entries").mock(
        return_value=Response(200, json=MOCK_LIST_ENTRIES_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/organizations/789").mock(
        return_value=Response(200, json=company_no_interactions)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "interactions"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    # Should succeed even with no interaction data
    assert result.exit_code == 0, f"Failed with: {result.output}"


def test_list_export_expand_interactions_dry_run(respx_mock: respx.MockRouter) -> None:
    """Test --dry-run with --expand interactions."""
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(200, json=MOCK_LIST_COMPANY)
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json=MOCK_LIST_FIELDS)
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(200, json={"data": []})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["list", "export", "12345", "--expand", "interactions", "--dry-run"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, f"Failed with: {result.output}"
    # Dry run output should mention expand or interactions
    lower_output = result.output.lower()
    assert "expand" in lower_output or "interactions" in lower_output or "entries" in lower_output


def test_list_export_dry_run_uses_list_size_hint(respx_mock: respx.MockRouter) -> None:
    """Test that --dry-run uses _list_size_hint for estimatedEntries.

    Spec test #9: Dry-run uses correct size.

    The list_size from V1 API should be captured as _list_size_hint and
    used in dry-run estimation output.
    """
    # Mock V1 list get - returns accurate list_size
    respx_mock.get(url__regex=r"^https://api\.affinity\.co(/v2)?/lists/12345$").mock(
        return_value=Response(
            200,
            json={
                "id": 12345,
                "name": "Pipeline",
                "type": 1,  # ListType.COMPANY
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 150,  # V1 returns accurate size
            },
        )
    )
    respx_mock.get("https://api.affinity.co/v2/lists/12345/fields").mock(
        return_value=Response(200, json=MOCK_LIST_FIELDS)
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(200, json={"data": []})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "list", "export", "12345", "--expand", "interactions", "--dry-run"],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, f"Failed with: {result.output}"

    # Parse JSON output and verify estimatedEntries matches list_size
    output = json.loads(result.output)
    dry_run_data = output["data"]
    assert dry_run_data["estimatedEntries"] == 150, (
        f"Expected estimatedEntries=150 from _list_size_hint, "
        f"got {dry_run_data.get('estimatedEntries')}"
    )
