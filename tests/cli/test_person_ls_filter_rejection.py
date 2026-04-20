import json

from click.testing import CliRunner

from affinity.cli.main import cli


def test_person_ls_filter_exits_with_usage_error():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--readonly", "person", "ls", "--filter", 'firstName =~ "Alex"', "--json"],
        env={"AFFINITY_API_KEY": "test"},
        catch_exceptions=False,
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["error"]["type"] == "unsupported_filter"
    assert "--query" in payload["error"]["hint"]
