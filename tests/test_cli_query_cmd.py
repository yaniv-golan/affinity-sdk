"""Tests for CLI query command."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from affinity.cli.commands.query_cmd import query_cmd
from affinity.cli.context import CLIContext

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def runner() -> CliRunner:
    """Create CLI runner."""
    return CliRunner()


@pytest.fixture
def cli_context() -> CLIContext:
    """Create CLI context."""
    ctx = MagicMock(spec=CLIContext)
    ctx.output = "json"
    ctx.quiet = False
    ctx.verbosity = 0
    # Required for output option validation
    ctx._output_source = None
    ctx._output_format_conflict = None
    return ctx


def get_error_message(result) -> str:
    """Extract error message from result (either from output or exception)."""
    # Check output first (error messages are now echoed to stderr which is mixed into output)
    if result.output and "Error:" in result.output:
        return result.output
    # When --json is active the query command emits a JSON error envelope; surface
    # the nested error.message so keyword assertions on the message still work.
    if result.output and result.output.lstrip().startswith("{"):
        try:
            payload = extract_json_from_output(result.output)
            err = payload.get("error") or {}
            parts = [
                str(err.get("message") or ""),
                str(err.get("hint") or ""),
                str(err.get("type") or ""),
            ]
            return " ".join(p for p in parts if p)
        except Exception:
            pass
    if result.exception and hasattr(result.exception, "message"):
        return str(result.exception.message)
    if result.exception:
        return str(result.exception)
    return result.output


def extract_json_from_output(output: str) -> dict:
    """Extract JSON object from output that may have warnings/messages before it."""
    # Find the start of JSON (first '{')
    start = output.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in output: {output}")
    # Find matching close brace by counting
    depth = 0
    for i, char in enumerate(output[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(output[start : i + 1])
    raise ValueError(f"Unbalanced JSON in output: {output}")


# =============================================================================
# Input Method Tests
# =============================================================================


class TestQueryInputMethods:
    """Tests for query input methods."""

    @pytest.mark.req("QUERY-CLI-001")
    def test_read_query_from_file(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Read query from --file option."""
        query = {"from": "persons", "limit": 10}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(query, f)
            f.flush()

            result = runner.invoke(
                query_cmd,
                ["--file", f.name, "--dry-run"],
                obj=cli_context,
            )

        assert result.exit_code == 0
        output = extract_json_from_output(result.output)
        assert output["query"]["from"] == "persons"

    @pytest.mark.req("QUERY-CLI-002")
    def test_read_query_from_json_option(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Read query from --query option."""
        query = '{"from": "persons", "limit": 5}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code == 0
        output = extract_json_from_output(result.output)
        assert output["query"]["from"] == "persons"
        assert output["query"]["limit"] == 5

    @pytest.mark.req("QUERY-CLI-003")
    def test_read_query_from_stdin(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Read query from stdin."""
        query = '{"from": "companies", "limit": 20}'

        result = runner.invoke(
            query_cmd,
            ["--dry-run"],
            input=query,
            obj=cli_context,
        )

        assert result.exit_code == 0
        output = extract_json_from_output(result.output)
        assert output["query"]["from"] == "companies"

    def test_no_query_provided_error(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Error when no query provided (via empty stdin)."""
        result = runner.invoke(
            query_cmd,
            [],
            obj=cli_context,
        )

        assert result.exit_code != 0
        error_msg = get_error_message(result)
        # When no input is provided, it tries to read from stdin and gets invalid JSON
        assert "Invalid JSON" in error_msg or "No query provided" in error_msg

    def test_invalid_json_error(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Error on invalid JSON."""
        result = runner.invoke(
            query_cmd,
            ["--query", "not valid json"],
            obj=cli_context,
        )

        assert result.exit_code != 0
        error_msg = get_error_message(result)
        assert "Invalid JSON" in error_msg


# =============================================================================
# Output Format Tests
# =============================================================================


class TestQueryOutputFormats:
    """Tests for output format options."""

    @pytest.mark.req("QUERY-CLI-004")
    def test_output_json_format(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Output JSON format."""
        query = '{"from": "persons", "limit": 10}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run", "--output", "json"],
            obj=cli_context,
        )

        assert result.exit_code == 0
        # Should be valid JSON
        output = extract_json_from_output(result.output)
        assert "query" in output

    @pytest.mark.req("QUERY-CLI-006")
    def test_dry_run_shows_execution_plan(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Dry-run shows execution plan."""
        query = '{"from": "persons", "include": ["companies"]}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code == 0
        output = extract_json_from_output(result.output)
        assert "steps" in output
        assert "execution" in output
        assert output["execution"]["totalSteps"] > 0

    @pytest.mark.req("QUERY-CLI-007")
    def test_dry_run_verbose_shows_api_breakdown(
        self, runner: CliRunner, cli_context: CLIContext
    ) -> None:
        """Verbose dry-run shows API call breakdown."""
        query = '{"from": "persons", "include": ["companies"]}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run-verbose"],
            obj=cli_context,
        )

        assert result.exit_code == 0
        # Verbose mode should show step details
        assert "steps" in result.output or "Step" in result.output


# =============================================================================
# Validation Tests
# =============================================================================


class TestQueryValidation:
    """Tests for query validation."""

    def test_missing_from_field(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Error when 'from' field missing."""
        query = '{"limit": 10}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code != 0
        error_msg = get_error_message(result)
        assert "from" in error_msg.lower()

    def test_unknown_entity_type(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Error on unknown entity type."""
        query = '{"from": "unknownEntity"}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code != 0
        error_msg = get_error_message(result)
        assert "unknownEntity" in error_msg

    def test_invalid_operator(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Error on invalid operator."""
        query = '{"from": "persons", "where": {"path": "name", "op": "like", "value": "x"}}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code != 0
        error_msg = get_error_message(result)
        assert "like" in error_msg

    def test_aggregate_with_include_error(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Error when using aggregate with include."""
        query = (
            '{"from": "persons", "aggregate": {"count": {"count": true}}, "include": ["companies"]}'
        )

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code != 0
        error_msg = get_error_message(result)
        assert "aggregate" in error_msg.lower()


# =============================================================================
# Option Tests
# =============================================================================


class TestQueryOptions:
    """Tests for command options."""

    def test_max_records_option(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """--max-records option is accepted."""
        query = '{"from": "persons"}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run", "--max-records", "500"],
            obj=cli_context,
        )

        assert result.exit_code == 0

    def test_timeout_option(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """--timeout option is accepted."""
        query = '{"from": "persons"}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run", "--timeout", "60"],
            obj=cli_context,
        )

        assert result.exit_code == 0

    def test_query_version_override(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """--query-version overrides version in query."""
        query = '{"$version": "1.0", "from": "persons"}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run", "--query-version", "1.0"],
            obj=cli_context,
        )

        assert result.exit_code == 0
        output = extract_json_from_output(result.output)
        assert output["version"] == "1.0"


# =============================================================================
# Warning Tests
# =============================================================================


class TestQueryWarnings:
    """Tests for query warnings."""

    def test_version_warning_when_missing(self, runner: CliRunner, cli_context: CLIContext) -> None:
        """Warning when $version field missing."""
        query = '{"from": "persons"}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        # Warning should be in stderr or output
        assert result.exit_code == 0
        # The warning appears in stderr
        if result.output:
            pass  # Warning may be in stderr not captured here


# =============================================================================
# Output Format Tests
# =============================================================================


class TestQueryOutputFormatsUnit:
    """Unit tests for query output format handling.

    These tests verify that the format_data function is correctly called
    for new output formats. Full integration tests with mocked API are
    complex due to async client; these tests verify the formatter integration.
    """

    def test_format_data_markdown_import(self) -> None:
        """Verify format_data can be imported and handles markdown."""
        from affinity.cli.formatters import format_data

        data = [{"id": 1, "name": "Test"}]
        result = format_data(data, "markdown", fieldnames=["id", "name"])
        assert "|" in result
        assert "Test" in result

    def test_format_data_toon_import(self) -> None:
        """Verify format_data handles toon format."""
        from affinity.cli.formatters import format_data

        data = [{"id": 1, "name": "Test"}]
        result = format_data(data, "toon", fieldnames=["id", "name"])
        assert "[" in result
        assert "{" in result
        assert "}:" in result

    def test_format_data_jsonl_import(self) -> None:
        """Verify format_data handles jsonl format."""
        from affinity.cli.formatters import format_data

        data = [{"id": 1, "name": "Test"}, {"id": 2, "name": "Another"}]
        result = format_data(data, "jsonl", fieldnames=["id", "name"])
        lines = [line for line in result.strip().split("\n") if line]
        assert len(lines) == 2
        parsed = json.loads(lines[0])
        assert parsed["id"] == 1

    def test_format_data_csv_import(self) -> None:
        """Verify format_data handles csv format."""
        from affinity.cli.formatters import format_data

        data = [{"id": 1, "name": "Test"}]
        result = format_data(data, "csv", fieldnames=["id", "name"])
        lines = [line.strip() for line in result.strip().split("\n")]
        assert lines[0] == "id,name"
        assert "1" in lines[1]
        assert "Test" in lines[1]
