"""Tests for CLI files ls commands (company, person, opportunity)."""

from __future__ import annotations

import json

import click
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


# Sample file data returned by the V1 API
SAMPLE_FILES = {
    "entity_files": [
        {
            "id": 7777777,
            "name": "Pitch Deck 2025.pdf",
            "size": 5826929,
            "content_type": "application/pdf",
            "person_id": None,
            "organization_id": 555555555,
            "opportunity_id": None,
            "uploader_id": 11111114,
            "created_at": "2025-09-16T12:53:13.339Z",
        },
        {
            "id": 7777778,
            "name": "Financial Model.xlsx",
            "size": 1048576,
            "content_type": None,
            "person_id": None,
            "organization_id": 555555555,
            "opportunity_id": None,
            "uploader_id": 11111114,
            "created_at": "2025-09-16T13:00:00.000Z",
        },
    ],
    "next_page_token": None,
}


class TestCompanyFilesLs:
    """Tests for company files ls command."""

    def test_basic_listing(self, respx_mock: respx.MockRouter) -> None:
        """Basic file listing by company ID."""
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "555555555"},
        ).mock(return_value=Response(200, json=SAMPLE_FILES))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "555555555"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is True
        assert payload["command"]["name"] == "company files ls"
        assert payload["command"]["inputs"]["selector"] == "555555555"
        assert len(payload["data"]) == 2
        assert payload["data"][0]["id"] == 7777777
        assert payload["data"][0]["name"] == "Pitch Deck 2025.pdf"
        assert payload["data"][0]["contentType"] == "application/pdf"
        assert payload["data"][1]["contentType"] is None  # null content type preserved

    def test_with_page_size(self, respx_mock: respx.MockRouter) -> None:
        """File listing with --page-size option."""
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "12345", "page_size": "10"},
        ).mock(return_value=Response(200, json=SAMPLE_FILES))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--page-size", "10"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["command"]["modifiers"]["pageSize"] == 10

    def test_page_size_validation(self) -> None:
        """Page size must be 1-100."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--page-size", "101"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0
        assert "101 is not in the range" in result.output or "101" in result.output

    def test_cursor_page_size_exclusivity(self) -> None:
        """--cursor and --page-size cannot be combined."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "ls",
                "12345",
                "--cursor",
                "abc",
                "--page-size",
                "10",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert "--cursor cannot be combined with --page-size" in payload["error"]["message"]

    def test_resolved_metadata(self, respx_mock: respx.MockRouter) -> None:
        """Resolved metadata shows source and companyId."""
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "555555555"},
        ).mock(return_value=Response(200, json=SAMPLE_FILES))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "555555555"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        resolved = payload["meta"]["resolved"]["company"]
        assert resolved["source"] == "id"
        assert resolved["companyId"] == 555555555

    def test_pagination_cursor_returned(self, respx_mock: respx.MockRouter) -> None:
        """Pagination cursor returned when more data exists."""
        files_with_cursor = {
            **SAMPLE_FILES,
            "next_page_token": "cursor123",
        }
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "12345"},
        ).mock(return_value=Response(200, json=files_with_cursor))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["meta"]["pagination"]["nextCursor"] == "cursor123"

    def test_empty_results(self, respx_mock: respx.MockRouter) -> None:
        """Empty file list handled correctly."""
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "99999"},
        ).mock(return_value=Response(200, json={"entity_files": [], "next_page_token": None}))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "99999"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["data"] == []


class TestPersonFilesLs:
    """Tests for person files ls command."""

    def test_basic_listing(self, respx_mock: respx.MockRouter) -> None:
        """Basic file listing by person ID."""
        person_files = {
            "entity_files": [
                {
                    "id": 111,
                    "name": "Resume.pdf",
                    "size": 100000,
                    "content_type": "application/pdf",
                    "person_id": 67890,
                    "organization_id": None,
                    "opportunity_id": None,
                    "uploader_id": 12345,
                    "created_at": "2025-01-01T00:00:00.000Z",
                }
            ],
            "next_page_token": None,
        }
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"person_id": "67890"},
        ).mock(return_value=Response(200, json=person_files))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "files", "ls", "67890"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["command"]["name"] == "person files ls"
        assert len(payload["data"]) == 1
        assert payload["data"][0]["name"] == "Resume.pdf"


class TestOpportunityFilesLs:
    """Tests for opportunity files ls command."""

    def test_basic_listing(self, respx_mock: respx.MockRouter) -> None:
        """Basic file listing by opportunity ID."""
        opp_files = {
            "entity_files": [
                {
                    "id": 222,
                    "name": "Contract.pdf",
                    "size": 200000,
                    "content_type": "application/pdf",
                    "person_id": None,
                    "organization_id": None,
                    "opportunity_id": 98765,
                    "uploader_id": 12345,
                    "created_at": "2025-01-01T00:00:00.000Z",
                }
            ],
            "next_page_token": None,
        }
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"opportunity_id": "98765"},
        ).mock(return_value=Response(200, json=opp_files))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "files", "ls", "98765"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["command"]["name"] == "opportunity files ls"
        assert len(payload["data"]) == 1
        assert payload["data"][0]["name"] == "Contract.pdf"


class TestMaxResultsTruncation:
    """Tests for --max-results truncation behavior."""

    def test_max_results_at_page_boundary_with_more_data(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """When --max-results stops at page boundary with more pages, cursor is returned."""
        files_page = {
            "entity_files": [
                {
                    "id": i,
                    "name": f"file{i}.pdf",
                    "size": 1000,
                    "content_type": "application/pdf",
                    "person_id": None,
                    "organization_id": 12345,
                    "opportunity_id": None,
                    "uploader_id": 111,
                    "created_at": "2025-01-01T00:00:00.000Z",
                }
                for i in range(1, 3)  # 2 files
            ],
            "next_page_token": "more_data",
        }
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "12345"},
        ).mock(return_value=Response(200, json=files_page))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--max-results", "2"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert len(payload["data"]) == 2
        # Cursor available at page boundary
        assert payload["meta"]["pagination"]["nextCursor"] == "more_data"
        # Warning about truncation
        assert any("--max-results" in w for w in payload["warnings"])

    def test_truncation_mid_page(self, respx_mock: respx.MockRouter) -> None:
        """When --max-results truncates mid-page, no cursor is available."""
        files_page = {
            "entity_files": [
                {
                    "id": i,
                    "name": f"file{i}.pdf",
                    "size": 1000,
                    "content_type": "application/pdf",
                    "person_id": None,
                    "organization_id": 12345,
                    "opportunity_id": None,
                    "uploader_id": 111,
                    "created_at": "2025-01-01T00:00:00.000Z",
                }
                for i in range(1, 6)  # 5 files
            ],
            "next_page_token": "more_data",
        }
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "12345"},
        ).mock(return_value=Response(200, json=files_page))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--max-results", "3"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        # Only 3 files returned despite 5 in page
        assert len(payload["data"]) == 3
        # No cursor available (truncated mid-page) - pagination is null
        assert payload["meta"]["pagination"] is None
        # Warning about no resumption cursor
        assert any("no resumption cursor" in w.lower() for w in payload["warnings"])


class TestAllFlagBehavior:
    """Tests for --all flag (fetch all pages)."""

    def test_all_flag_fetches_multiple_pages(self, respx_mock: respx.MockRouter) -> None:
        """--all flag fetches all pages until exhausted."""
        page1 = {
            "entity_files": [
                {
                    "id": 1,
                    "name": "file1.pdf",
                    "size": 1000,
                    "content_type": "application/pdf",
                    "person_id": None,
                    "organization_id": 12345,
                    "opportunity_id": None,
                    "uploader_id": 111,
                    "created_at": "2025-01-01T00:00:00.000Z",
                }
            ],
            "next_page_token": "page2_cursor",
        }
        page2 = {
            "entity_files": [
                {
                    "id": 2,
                    "name": "file2.pdf",
                    "size": 2000,
                    "content_type": "application/pdf",
                    "person_id": None,
                    "organization_id": 12345,
                    "opportunity_id": None,
                    "uploader_id": 111,
                    "created_at": "2025-01-02T00:00:00.000Z",
                }
            ],
            "next_page_token": None,
        }
        # Use side_effect to return different responses for sequential calls
        respx_mock.get(url__startswith="https://api.affinity.co/entity-files").mock(
            side_effect=[Response(200, json=page1), Response(200, json=page2)]
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--all"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        # Both pages fetched
        assert len(payload["data"]) == 2
        assert payload["data"][0]["id"] == 1
        assert payload["data"][1]["id"] == 2
        # No more pages - pagination is null when --all finishes
        assert payload["meta"]["pagination"] is None

    def test_all_flag_incompatible_with_cursor(self, respx_mock: respx.MockRouter) -> None:
        """--all and --cursor cannot be combined."""
        # Mock not needed for validation error, but prevents 401
        respx_mock.get(url__startswith="https://api.affinity.co/entity-files").mock(
            return_value=Response(200, json={"entity_files": [], "next_page_token": None})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--all", "--cursor", "abc"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert "--all cannot be combined with --cursor" in payload["error"]["message"]

    def test_all_flag_incompatible_with_max_results(self, respx_mock: respx.MockRouter) -> None:
        """--all and --max-results cannot be combined."""
        # Mock not needed for validation error, but prevents 401
        respx_mock.get(url__startswith="https://api.affinity.co/entity-files").mock(
            return_value=Response(200, json={"entity_files": [], "next_page_token": None})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--all", "--max-results", "10"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert "--all cannot be combined with --max-results" in payload["error"]["message"]


class TestSelectorResolution:
    """Tests for different selector types (ID, URL, name:, domain:)."""

    def test_resolve_by_url(self, respx_mock: respx.MockRouter) -> None:
        """Selector can be an Affinity URL."""
        # URL resolution extracts company ID from URL
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "555555555"},
        ).mock(return_value=Response(200, json={"entity_files": [], "next_page_token": None}))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "ls",
                "https://app.affinity.co/companies/555555555",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is True
        resolved = payload["meta"]["resolved"]["company"]
        assert resolved["source"] == "url"
        assert resolved["companyId"] == 555555555

    def test_resolve_by_name_prefix(self, respx_mock: respx.MockRouter) -> None:
        """Selector with 'name:' prefix resolves by company name."""
        # First: lookup by name using V1 search endpoint
        respx_mock.get(url__startswith="https://api.affinity.co/organizations").mock(
            return_value=Response(
                200,
                json={
                    "organizations": [{"id": 999, "name": "Acme Corp", "domain": "acme.com"}],
                    "next_page_token": None,
                },
            )
        )
        # Then: fetch files for resolved ID
        respx_mock.get(url__startswith="https://api.affinity.co/entity-files").mock(
            return_value=Response(200, json={"entity_files": [], "next_page_token": None})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "name:Acme Corp"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is True
        resolved = payload["meta"]["resolved"]["company"]
        assert resolved["source"] == "name"
        assert resolved["companyId"] == 999

    def test_resolve_by_domain_prefix(self, respx_mock: respx.MockRouter) -> None:
        """Selector with 'domain:' prefix resolves by company domain."""
        # First: lookup by domain using V1 endpoint
        respx_mock.get(url__startswith="https://api.affinity.co/organizations").mock(
            return_value=Response(
                200,
                json={
                    "organizations": [{"id": 888, "name": "Acme Inc", "domain": "acme.com"}],
                    "next_page_token": None,
                },
            )
        )
        # Then: fetch files for resolved ID
        respx_mock.get(url__startswith="https://api.affinity.co/entity-files").mock(
            return_value=Response(200, json={"entity_files": [], "next_page_token": None})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "domain:acme.com"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is True
        resolved = payload["meta"]["resolved"]["company"]
        assert resolved["source"] == "domain"
        assert resolved["companyId"] == 888

    def test_person_resolve_by_email_prefix(self, respx_mock: respx.MockRouter) -> None:
        """Person selector with 'email:' prefix resolves by email."""
        # First: lookup by email using V1 endpoint
        # Note: Must include email in person data for email matching to work
        respx_mock.get(url__startswith="https://api.affinity.co/persons").mock(
            return_value=Response(
                200,
                json={
                    "persons": [
                        {
                            "id": 777,
                            "first_name": "John",
                            "last_name": "Doe",
                            "primary_email": "john@example.com",
                            "emails": ["john@example.com"],
                        }
                    ],
                    "next_page_token": None,
                },
            )
        )
        # Then: fetch files for resolved ID
        respx_mock.get(url__startswith="https://api.affinity.co/entity-files").mock(
            return_value=Response(200, json={"entity_files": [], "next_page_token": None})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "files", "ls", "email:john@example.com"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is True
        resolved = payload["meta"]["resolved"]["person"]
        assert resolved["source"] == "email"
        assert resolved["personId"] == 777


class TestMcpWarningDifferentiation:
    """Tests for MCP vs user-set limit warning differentiation."""

    def test_user_explicit_max_results_warning(self, respx_mock: respx.MockRouter) -> None:
        """When user sets --max-results, warning mentions --max-results."""
        files_page = {
            "entity_files": [
                {
                    "id": i,
                    "name": f"file{i}.pdf",
                    "size": 1000,
                    "content_type": "application/pdf",
                    "person_id": None,
                    "organization_id": 12345,
                    "opportunity_id": None,
                    "uploader_id": 111,
                    "created_at": "2025-01-01T00:00:00.000Z",
                }
                for i in range(1, 3)
            ],
            "next_page_token": "more_data",
        }
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "12345"},
        ).mock(return_value=Response(200, json=files_page))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--max-results", "2"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        # Warning should mention --max-results (user-explicit)
        assert any("--max-results" in w for w in payload["warnings"])
        # Warning should NOT mention "MCP safety limit"
        assert not any("MCP safety limit" in w for w in payload["warnings"])

    def test_mcp_injected_limit_warning(self, respx_mock: respx.MockRouter) -> None:
        """When MCP injects limit, warning mentions 'MCP safety limit'."""
        files_page = {
            "entity_files": [
                {
                    "id": i,
                    "name": f"file{i}.pdf",
                    "size": 1000,
                    "content_type": "application/pdf",
                    "person_id": None,
                    "organization_id": 12345,
                    "opportunity_id": None,
                    "uploader_id": 111,
                    "created_at": "2025-01-01T00:00:00.000Z",
                }
                for i in range(1, 3)
            ],
            "next_page_token": "more_data",
        }
        respx_mock.get(
            "https://api.affinity.co/entity-files",
            params={"organization_id": "12345"},
        ).mock(return_value=Response(200, json=files_page))

        runner = CliRunner()
        # Set MCP env vars to trigger MCP limit injection
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345"],
            env={
                "AFFINITY_API_KEY": "test-key",
                "AFFINITY_MCP_MAX_LIMIT": "10000",
                "AFFINITY_MCP_DEFAULT_LIMIT": "2",  # Will truncate at 2 files
            },
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        # Warning should mention "MCP safety limit" (injected)
        assert any("MCP safety limit" in w for w in payload["warnings"])
        # Warning should NOT mention --max-results
        assert not any("--max-results" in w for w in payload["warnings"])

    def test_mcp_blocks_all_flag(self, respx_mock: respx.MockRouter) -> None:
        """--all is blocked when running via MCP."""
        # Mock not needed for validation error, but prevents 401
        respx_mock.get(url__startswith="https://api.affinity.co/entity-files").mock(
            return_value=Response(200, json={"entity_files": [], "next_page_token": None})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "ls", "12345", "--all"],
            env={
                "AFFINITY_API_KEY": "test-key",
                "AFFINITY_MCP_MAX_LIMIT": "10000",
                "AFFINITY_MCP_DEFAULT_LIMIT": "1000",
            },
        )
        # MCP limit decorator raises UsageError which bypasses JSON formatter
        assert result.exit_code == 2
        # Strip ANSI codes and normalize whitespace (Rich formatting varies by env)
        clean_output = click.unstyle(result.output)
        normalized_output = " ".join(clean_output.split())
        assert "--all is not allowed via MCP" in normalized_output
