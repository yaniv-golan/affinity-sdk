"""Tests for CLI date filter parameters.

These tests verify that:
1. --after/--before work for interaction ls (occurrence date range)
2. --due-after/--due-before work for reminder ls (due date range)
3. --type is required for interaction ls
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


def test_interaction_ls_date_params(respx_mock: respx.MockRouter) -> None:
    """Verify --after/--before work for interaction ls."""
    respx_mock.get("https://api.affinity.co/interactions").mock(
        return_value=Response(
            200,
            json={
                "interactions": [
                    {
                        "id": 100,
                        "type": 1,
                        "date": "2024-06-15T10:00:00Z",
                        "subject": "Test meeting",
                        "persons": [{"id": 123, "type": "external"}],
                    }
                ],
                "next_page_token": None,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "--quiet",
            "interaction",
            "ls",
            "--type",
            "meeting",
            "--person-id",
            "123",
            "--after",
            "2024-06-01T00:00:00Z",
            "--before",
            "2024-06-30T00:00:00Z",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, f"Command failed: {result.output}"
    payload = json.loads(result.output.strip())
    assert payload["data"][0]["id"] == 100
    # Verify command metadata uses 'start' and 'end' keys (ISO datetime strings)
    cmd = payload["command"]
    assert "start" in cmd["modifiers"]
    assert "end" in cmd["modifiers"]
    # Verify dates are present (exact format depends on timezone handling)
    assert "2024-06-01" in cmd["modifiers"]["start"]
    assert "2024-06-30" in cmd["modifiers"]["end"]


def test_reminder_ls_date_params(respx_mock: respx.MockRouter) -> None:
    """Verify --due-after/--due-before work for reminder ls."""
    route = respx_mock.get("https://api.affinity.co/v2/reminders").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 200,
                        "type": "one-time",
                        "status": "active",
                        "content": "Follow up",
                        "dueDate": "2024-06-15T00:00:00Z",
                        "recurrence": None,
                        "creator": {"id": 1},
                        "owner": {"id": 1},
                        "completer": None,
                        "person": None,
                        "company": None,
                        "opportunity": None,
                        "createdAt": "2024-06-01T00:00:00Z",
                        "updatedAt": None,
                        "completedAt": None,
                    }
                ],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "reminder",
            "ls",
            "--due-after",
            "2024-06-01",
            "--due-before",
            "2024-06-30",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, f"Command failed: {result.output}"
    payload = json.loads(result.output.strip())
    assert payload["data"]["reminders"][0]["id"] == 200
    # Verify command metadata uses 'dueAfter' and 'dueBefore' keys
    cmd = payload["command"]
    assert cmd["modifiers"]["dueAfter"] == "2024-06-01"
    assert cmd["modifiers"]["dueBefore"] == "2024-06-30"
    assert "dueDate>=" in route.calls[0].request.url.params["filter"]


def test_interaction_ls_type_required() -> None:
    """Verify --type is required for interaction ls."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "interaction",
            "ls",
            "--person-id",
            "123",
            "--days",
            "7",  # Must specify date range now
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    # Command should fail because --type is required
    assert result.exit_code != 0
    # Error message should mention missing required option
    assert "Missing option" in result.output or "--type" in result.output
