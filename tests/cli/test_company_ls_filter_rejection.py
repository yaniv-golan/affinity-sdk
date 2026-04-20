import json

import pytest
from click.testing import CliRunner

from affinity.cli.main import cli


@pytest.mark.req("REQ-FILTER-002")
def test_company_ls_filter_exits_with_usage_error():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--readonly", "company", "ls", "--filter", 'name =~ "Acme"', "--json"],
        env={"AFFINITY_API_KEY": "test"},
        catch_exceptions=False,
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["error"]["type"] == "unsupported_filter"
    assert "--query" in payload["error"]["hint"]


@pytest.mark.req("REQ-FILTER-002")
def test_company_ls_query_still_works():
    """Regression guard — --query path must not be affected by the --filter rejection."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--readonly", "company", "ls", "--query", "Acme", "--json"],
        env={"AFFINITY_API_KEY": "test"},
    )
    assert result.exit_code != 2 or "unsupported_filter" not in result.output
