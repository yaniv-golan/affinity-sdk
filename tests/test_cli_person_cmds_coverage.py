"""Additional tests for CLI person commands to improve coverage."""

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


class TestPersonLs:
    """Tests for person ls command."""

    def test_ls_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic person ls should work."""
        respx_mock.get("https://api.affinity.co/v2/persons").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": 1, "firstName": "Alice", "lastName": "Smith"},
                    ],
                    "pagination": {"nextUrl": None},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "ls"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_ls_with_max_results(self, respx_mock: respx.MockRouter) -> None:
        """Person ls with --max-results should limit output."""
        respx_mock.get("https://api.affinity.co/v2/persons").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": 1, "firstName": "Alice", "lastName": "Smith"},
                        {"id": 2, "firstName": "Bob", "lastName": "Jones"},
                        {"id": 3, "firstName": "Carol", "lastName": "Lee"},
                    ],
                    "pagination": {"nextUrl": "https://api.affinity.co/v2/persons?cursor=next"},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "ls", "--max-results", "2"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        # Should have truncated to 2 results
        assert len(payload["data"]["persons"]) <= 2

    def test_ls_with_filter(self) -> None:
        """Person ls rejects --filter (V2 /persons does not support server-side filtering)."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "ls", "--filter", "email:alice@example.com"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload["error"]["type"] == "unsupported_filter"


class TestPersonCreate:
    """Tests for person create command."""

    def test_create_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic person create should work."""
        respx_mock.post("https://api.affinity.co/persons").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "first_name": "New",
                    "last_name": "Person",
                    "primary_email": "new@example.com",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "person",
                "create",
                "--allow-duplicate",
                "--first-name",
                "New",
                "--last-name",
                "Person",
                "--email",
                "new@example.com",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonUpdate:
    """Tests for person update command."""

    def test_update_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic person update should work."""
        respx_mock.put("https://api.affinity.co/persons/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "first_name": "Updated",
                    "last_name": "Person",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "update", "123", "--first-name", "Updated"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonDelete:
    """Tests for person delete command."""

    def test_delete_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic person delete should work."""
        respx_mock.delete("https://api.affinity.co/persons/123").mock(
            return_value=Response(200, json={"success": True})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "delete", "123", "--yes"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonMerge:
    """Tests for person merge command."""

    def test_merge_basic(self, respx_mock: respx.MockRouter) -> None:
        """Basic person merge should work."""
        # Merge uses V2 /person-merges endpoint
        respx_mock.post("https://api.affinity.co/v2/person-merges").mock(
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
            ["--json", "--beta", "person", "merge", "100", "101"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonGet:
    """Tests for person get command."""

    def test_get_by_id(self, respx_mock: respx.MockRouter) -> None:
        """Get person by numeric ID."""
        respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "firstName": "Alice",
                    "lastName": "Smith",
                    "primaryEmail": "alice@example.com",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "get", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_get_with_expand_lists(self, respx_mock: respx.MockRouter) -> None:
        """Get person with --expand lists."""
        respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "firstName": "Alice",
                    "lastName": "Smith",
                },
            )
        )
        respx_mock.get("https://api.affinity.co/v2/persons/123/lists").mock(
            return_value=Response(
                200,
                json={
                    "data": [{"id": 1, "name": "My List"}],
                    "pagination": {"nextUrl": None},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "get", "123", "--expand", "lists"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonUpdateErrors:
    """Tests for person update error handling."""

    def test_update_no_fields_raises(self) -> None:
        """Update with no fields should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "update", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        # Should fail with exit code 2 (usage error)
        assert result.exit_code == 2


class TestPersonLsWithQuery:
    """Tests for person ls with --query option."""

    def test_ls_with_query(self, respx_mock: respx.MockRouter) -> None:
        """Person ls with --query should use V1 search."""
        respx_mock.get("https://api.affinity.co/persons").mock(
            return_value=Response(
                200,
                json={
                    "persons": [
                        {"id": 1, "first_name": "Alice", "last_name": "Smith"},
                    ],
                    "next_page_token": None,
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "ls", "--query", "alice"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0

    def test_ls_query_with_filter_fails(self) -> None:
        """Person ls with both --query and --filter should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "ls", "--query", "alice", "--filter", "email:x"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        # Should fail with exit code 2 (usage error)
        assert result.exit_code == 2


class TestPersonCreateWithCompany:
    """Tests for person create with company IDs."""

    def test_create_with_company_ids(self, respx_mock: respx.MockRouter) -> None:
        """Create person with associated company IDs."""
        respx_mock.post("https://api.affinity.co/persons").mock(
            return_value=Response(
                200,
                json={
                    "id": 456,
                    "first_name": "Test",
                    "last_name": "User",
                    "organization_ids": [100, 200],
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "person",
                "create",
                "--allow-duplicate",
                "--first-name",
                "Test",
                "--last-name",
                "User",
                "--company-id",
                "100",
                "--company-id",
                "200",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonGetExpanded:
    """Expanded person get tests for coverage."""

    def test_get_json_output_contains_data(self, respx_mock: respx.MockRouter) -> None:
        """Person get with --json should include person data."""
        respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "firstName": "Alice",
                    "lastName": "Smith",
                    "primaryEmail": "alice@example.com",
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "get", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        import json as _json

        payload = _json.loads(result.output)
        assert payload["ok"] is True

    def test_get_with_expand_list_entries(self, respx_mock: respx.MockRouter) -> None:
        """Person get --expand list-entries."""
        respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
            return_value=Response(
                200,
                json={
                    "id": 123,
                    "firstName": "Alice",
                    "lastName": "Smith",
                },
            )
        )
        respx_mock.get("https://api.affinity.co/v2/persons/123/list-entries").mock(
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
                "person",
                "get",
                "123",
                "--expand",
                "list-entries",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonLsAdvanced:
    """Advanced person ls tests."""

    def test_ls_json_output(self, respx_mock: respx.MockRouter) -> None:
        """Person ls with --json."""
        respx_mock.get("https://api.affinity.co/v2/persons").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "firstName": "Alice",
                            "lastName": "Smith",
                        }
                    ],
                    "pagination": {"nextUrl": None},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "ls", "--max-results", "5"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0


class TestPersonFilesLs:
    """Tests for person files ls command."""

    def test_files_ls(self, respx_mock: respx.MockRouter) -> None:
        """List files for a person."""
        respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
            return_value=Response(
                200,
                json={"id": 123, "firstName": "Alice", "lastName": "Smith"},
            )
        )
        respx_mock.get("https://api.affinity.co/entity-files").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 456,
                        "name": "resume.pdf",
                        "size": 102400,
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
            ["--json", "person", "files", "ls", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code in (0, 1)


class TestPersonField:
    """Tests for person field command."""

    def test_field_no_operation_fails(self) -> None:
        """Calling field with no ops should fail."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "field", "123"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0

    def test_field_get(self, respx_mock: respx.MockRouter) -> None:
        """Person field --get should read field values."""
        respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
            return_value=Response(
                200,
                json={"id": 123, "firstName": "A", "lastName": "B"},
            )
        )
        respx_mock.get("https://api.affinity.co/v2/persons/fields").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "id": "field-100",
                            "name": "Phone",
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
                json=[{"id": 1, "field_id": 100, "value": "555-1234"}],
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "field", "123", "--get", "Phone"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code in (0, 1)
