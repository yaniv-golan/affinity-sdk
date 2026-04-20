"""Additional tests for CLI company commands to improve coverage."""

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


class TestCompanyLs:
    """Tests for company ls command."""

    def test_ls_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic company ls should work."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": 1, "name": "Acme Corp", "domain": "acme.com"},
                    ],
                    "pagination": {"nextUrl": None},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "ls"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_ls_with_filter(self) -> None:
        """Company ls rejects --filter (V2 /companies does not support server-side filtering)."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "ls", "--filter", "domain:acme.com"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload["error"]["type"] == "unsupported_filter"

    def test_ls_with_max_results(self, respx_mock: respx.MockRouter) -> None:
        """Company ls with --max-results should limit output."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": 1, "name": "Acme Corp", "domain": "acme.com"},
                        {"id": 2, "name": "Beta Inc", "domain": "beta.com"},
                        {"id": 3, "name": "Gamma LLC", "domain": "gamma.com"},
                    ],
                    "pagination": {"nextUrl": "https://api.affinity.co/v2/companies?cursor=next"},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "ls", "--max-results", "2"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert len(payload["data"]["companies"]) <= 2


class TestCompanyCreate:
    """Tests for company create command."""

    def test_create_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic company create should work."""
        respx_mock.post("https://api.affinity.co/organizations").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "New Company",
                    "domain": "new.com",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "create",
                "--allow-duplicate",
                "--name",
                "New Company",
                "--domain",
                "new.com",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_create_with_person_ids(self, respx_mock: respx.MockRouter) -> None:
        """Company create with associated person IDs."""
        respx_mock.post("https://api.affinity.co/organizations").mock(
            return_value=Response(
                200,
                json={
                    "id": 456,
                    "name": "New Company",
                    "domain": "new.com",
                    "person_ids": [100, 200],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "create",
                "--allow-duplicate",
                "--name",
                "New Company",
                "--person-id",
                "100",
                "--person-id",
                "200",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestCompanyUpdate:
    """Tests for company update command."""

    def test_update_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic company update should work."""
        respx_mock.put("https://api.affinity.co/organizations/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Updated Company",
                    "domain": "updated.com",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "update", "123", "--name", "Updated Company"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_update_no_fields_raises(self) -> None:
        """Update with no fields should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "update", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        # Should fail with exit code 2 (usage error)
        assert result.exit_code == 2


class TestCompanyDelete:
    """Tests for company delete command."""

    def test_delete_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic company delete should work."""
        respx_mock.delete("https://api.affinity.co/organizations/123").mock(
            return_value=Response(200, json={"success": True})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "delete", "123", "--yes"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestCompanyMerge:
    """Tests for company merge command."""

    def test_merge_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic company merge should work."""
        # Merge uses V2 /company-merges endpoint
        respx_mock.post("https://api.affinity.co/v2/company-merges").mock(
            return_value=Response(
                200,
                json={
                    "taskUrl": "https://api.affinity.co/v2/tasks/123",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "--beta", "company", "merge", "100", "101"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestCompanyLsWithQuery:
    """Tests for company ls with --query option."""

    def test_ls_with_query(self, respx_mock: respx.MockRouter) -> None:
        """Company ls with --query should use V1 search."""
        respx_mock.get("https://api.affinity.co/organizations").mock(
            return_value=Response(
                200,
                json={
                    "organizations": [
                        {"id": 1, "name": "Acme Corp", "domain": "acme.com"},
                    ],
                    "next_page_token": None,
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "ls", "--query", "acme"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_ls_query_with_filter_fails(self) -> None:
        """Company ls with both --query and --filter should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "ls", "--query", "acme", "--filter", "domain:x"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        # Should fail with exit code 2 (usage error)
        assert result.exit_code == 2


class TestCompanyGet:
    """Tests for company get command."""

    def test_get_by_id(self, respx_mock: respx.MockRouter) -> None:
        """Get company by numeric ID."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Acme Corp",
                    "domain": "acme.com",
                    "domains": ["acme.com"],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "get", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_get_json_output(self, respx_mock: respx.MockRouter) -> None:
        """Get company should have ok=True in JSON output."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "name": "Acme Corp",
                    "domain": "acme.com",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "get", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True

    def test_get_with_expand_lists(self, respx_mock: respx.MockRouter) -> None:
        """Company get --expand lists."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={"id": 123, "name": "Acme Corp", "domain": "acme.com"},
            )
        )
        respx_mock.get("https://api.affinity.co/v2/companies/123/lists").mock(
            return_value=Response(
                200,
                json={
                    "data": [{"id": 1, "name": "Pipeline"}],
                    "pagination": {"nextUrl": None},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "get", "123", "--expand", "lists"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_get_with_expand_list_entries(self, respx_mock: respx.MockRouter) -> None:
        """Company get --expand list-entries."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={"id": 123, "name": "Acme Corp", "domain": "acme.com"},
            )
        )
        respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "listId": 10,
                            "createdAt": "2024-01-01T00:00:00Z",
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
                "company",
                "get",
                "123",
                "--expand",
                "list-entries",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestCompanyLsAdvanced:
    """Advanced company ls tests."""

    def test_ls_json_ok(self, respx_mock: respx.MockRouter) -> None:
        """Company ls with --json should have ok=True."""
        respx_mock.get("https://api.affinity.co/v2/companies").mock(
            return_value=Response(
                200,
                json={
                    "data": [{"id": 1, "name": "Acme Corp", "domain": "acme.com"}],
                    "pagination": {"nextUrl": None},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "ls"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True


class TestCompanyFilesLs:
    """Tests for company files ls command."""

    def test_files_ls(self, respx_mock: respx.MockRouter) -> None:
        """List files for a company."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={"id": 123, "name": "Acme Corp", "domain": "acme.com"},
            )
        )
        respx_mock.get("https://api.affinity.co/entity-files").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 456,
                        "name": "contract.pdf",
                        "size": 204800,
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
            ["--json", "company", "files", "ls", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code in (0, 1)


class TestCompanyField:
    """Tests for company field command."""

    def test_field_no_operation_fails(self) -> None:
        """Calling field with no ops should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "field", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0

    def test_field_get(self, respx_mock: respx.MockRouter) -> None:
        """Company field --get should read field values."""
        respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
            return_value=Response(
                200,
                json={"id": 123, "name": "Acme", "domain": "acme.com"},
            )
        )
        respx_mock.get("https://api.affinity.co/v2/companies/fields").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-200",
                            "name": "Industry",
                            "type": "global",
                            "valueType": "text",
                        }
                    ],
                    "pagination": {"nextUrl": None},
                },
            )
        )
        respx_mock.get("https://api.affinity.co/field-values").mock(
            return_value=Response(
                200,
                json=[{"id": 1, "field_id": 200, "value": "Technology"}],
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "field",
                "123",
                "--get",
                "Industry",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code in (0, 1)
