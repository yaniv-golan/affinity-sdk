"""``--page-size`` must be at least 1 on every command that pages: a zero or negative size
is a usage error (exit 2) found before any API call, not an internal error from the SDK."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("rich_click")
respx = pytest.importorskip("respx")

from click.testing import CliRunner

from affinity.cli.main import cli

COMMANDS = [
    ["company", "ls"],
    ["person", "ls"],
    ["opportunity", "ls"],
    ["list", "ls"],
    ["list", "export", "12345"],
    ["note", "ls"],
    ["interaction", "ls", "--type", "email", "--person-id", "1"],
    ["reminder", "ls"],
    ["company", "files", "download", "1", "--out", "unused"],
    ["person", "files", "download", "1", "--out", "unused"],
    ["opportunity", "files", "download", "1", "--out", "unused"],
]


@pytest.mark.parametrize("size", ["0", "-1"])
@pytest.mark.parametrize("command", COMMANDS, ids=[" ".join(c[:2]) for c in COMMANDS])
def test_page_size_below_one_is_a_usage_error(
    command: list[str], size: str, respx_mock: respx.MockRouter
) -> None:
    # No routes: any API call fails the test instead of reaching Affinity.
    result = CliRunner().invoke(
        cli, ["--json", *command, "--page-size", size], env={"AFFINITY_API_KEY": "test-key"}
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["error"]["type"] == "usage_error"
    assert "--page-size" in payload["error"]["message"]
    assert not respx_mock.calls
