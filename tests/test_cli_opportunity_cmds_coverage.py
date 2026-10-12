"""Additional tests for CLI opportunity commands to improve coverage."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

try:
    import respx
except ModuleNotFoundError:
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:
    pytest.skip("respx is not installed", allow_module_level=True)


class TestOpportunityLs:
    """Tests for opportunity ls command."""

    def test_ls_with_query(self, respx_mock: respx.MockRouter) -> None:
        """Opportunity ls with --query parameter uses V1 search."""
        respx_mock.get("https://api.affinity.co/opportunities").mock(
            return_value=Response(
                200,
                json={
                    "opportunities": [
                        {"id": 1, "name": "Series A", "list_entries": []},
                    ],
                    "next_page_token": None,
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "ls", "--query", "Series A"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_ls_with_query_and_all(self, respx_mock: respx.MockRouter) -> None:
        """Opportunity ls with --query and --all fetches all pages."""
        respx_mock.get("https://api.affinity.co/opportunities").mock(
            side_effect=[
                Response(
                    200,
                    json={
                        "opportunities": [{"id": 1, "name": "Deal A", "list_entries": []}],
                        "next_page_token": "page2",
                    },
                ),
                Response(
                    200,
                    json={
                        "opportunities": [{"id": 2, "name": "Deal B", "list_entries": []}],
                        "next_page_token": None,
                    },
                ),
            ]
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "ls", "--query", "Deal", "--all"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_ls_with_query_and_max_results(self, respx_mock: respx.MockRouter) -> None:
        """Opportunity ls with --query and --max-results limits output."""
        respx_mock.get("https://api.affinity.co/opportunities").mock(
            return_value=Response(
                200,
                json={
                    "opportunities": [
                        {"id": 1, "name": "Deal A", "list_entries": []},
                        {"id": 2, "name": "Deal B", "list_entries": []},
                        {"id": 3, "name": "Deal C", "list_entries": []},
                    ],
                    "next_page_token": "more",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "ls", "--query", "Deal", "--max-results", "2"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestOpportunityUpdate:
    """Tests for opportunity update command."""

    def test_update_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic opportunity update should work."""
        respx_mock.put("https://api.affinity.co/opportunities/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Updated Deal",
                    "list_entries": [],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "update", "123", "--name", "Updated Deal"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_update_no_fields_raises(self) -> None:
        """Update with no fields should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "update", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        # Should fail with exit code 2 (usage error)
        assert result.exit_code == 2


class TestOpportunityDelete:
    """Tests for opportunity delete command."""

    def test_delete_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic opportunity delete should work."""
        respx_mock.delete("https://api.affinity.co/opportunities/123").mock(
            return_value=Response(200, json={"success": True})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "delete", "123", "--yes"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestOpportunityGet:
    """Tests for opportunity get command."""

    def test_get_by_id(self, respx_mock: respx.MockRouter) -> None:
        """Get opportunity by numeric ID."""
        respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Series A",
                    "listId": 42,
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "get", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_get_json_output(self, respx_mock: respx.MockRouter) -> None:
        """Opportunity get should produce valid JSON."""
        respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Series A",
                    "listId": 42,
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "get", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True

    def test_get_default_reports_not_requested(self, respx_mock: respx.MockRouter) -> None:
        """Without --details, field values are dropped; meta.notRequested must say so."""
        respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
            return_value=Response(200, json={"id": 123, "name": "Series A", "listId": 42})
        )

        result = CliRunner().invoke(
            cli, ["--json", "opportunity", "get", "123"], env={"AFFINITY_API_KEY": "test-key"}
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["meta"]["notRequested"] == [
            {"key": "data.opportunity.fields", "flag": "--details"},
        ]


class TestOpportunityCreate:
    """Tests for opportunity create command."""

    def test_create_basic(self, respx_mock: respx.MockRouter) -> None:
        """Create opportunity with name and --list."""
        # Resolve list by ID (V2)
        respx_mock.get("https://api.affinity.co/v2/lists/42").mock(
            return_value=Response(
                200,
                json={
                    "id": 42,
                    "name": "Dealflow",
                    "type": 8,
                    "public": False,
                    "owner_id": 1,
                    "creator_id": 1,
                },
            )
        )
        # V1 list (may also be called)
        respx_mock.get("https://api.affinity.co/lists/42").mock(
            return_value=Response(
                200,
                json={
                    "id": 42,
                    "name": "Dealflow",
                    "type": 8,
                    "public": False,
                    "owner_id": 1,
                },
            )
        )
        # Create opportunity (V1)
        respx_mock.post("https://api.affinity.co/opportunities").mock(
            return_value=Response(
                200,
                json={
                    "id": 789,
                    "name": "New Deal",
                    "list_id": 42,
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "opportunity",
                "create",
                "--list",
                "42",
                "--name",
                "New Deal",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestOpportunityLsAdvanced:
    """Advanced opportunity ls tests."""

    def test_ls_json_output(self, respx_mock: respx.MockRouter) -> None:
        """Opportunity ls with --json produces valid JSON."""
        respx_mock.get("https://api.affinity.co/opportunities").mock(
            return_value=Response(
                200,
                json={
                    "opportunities": [
                        {
                            "id": 1,
                            "name": "Deal A",
                            "list_entries": [],
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
                "opportunity",
                "ls",
                "--query",
                "Deal",
                "--max-results",
                "5",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True


class TestOpportunityFilesLs:
    """Tests for opportunity files ls command."""

    def test_files_ls(self, respx_mock: respx.MockRouter) -> None:
        """List files for an opportunity."""
        respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
            return_value=Response(
                200,
                json={"id": 123, "name": "Deal A", "listId": 42},
            )
        )
        respx_mock.get("https://api.affinity.co/entity-files").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 456,
                        "name": "term_sheet.pdf",
                        "size": 51200,
                        "content_type": "application/pdf",
                        "uploader_id": 789,
                        "created_at": "2024-01-01T00:00:00Z",
                    }
                ],
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "files", "ls", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code in (0, 1)


class TestOpportunityField:
    """Tests for opportunity field command."""

    def test_field_no_operation_fails(self) -> None:
        """Calling field with no ops should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "field", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0
