"""MCP gateway bounds `field history-bulk` (one API call per entry): `--all` only with
`--strategy field` (the field-wide read, a few calls), and `--max-results` at most the registry
max. Runs execute-read-command with a fake CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MCP = REPO / "mcp"
JQ = shutil.which("jq")

pytestmark = pytest.mark.skipif(JQ is None, reason="jq not installed")


def _min_version() -> str:
    data = json.loads((MCP / "server.d" / "requirements.json").read_text())
    return next(d["minVersion"] for d in data["dependencies"] if d["name"] == "xaffinity")


def _max() -> int:
    registry = json.loads((MCP / ".registry" / "commands.generated.json").read_text())
    cmd = next(c for c in registry["commands"] if c["name"] == "field history-bulk")
    return int(cmd["limitConfig"]["max"])


def _run(tmp_path: Path, argv: list[str]) -> tuple[dict, bool]:  # type: ignore[type-arg]
    fake = tmp_path / "xaffinity"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do [[ "$a" == version ]] && '
        f'{{ echo \'{{"data":{{"version":"{_min_version()}"}}}}\'; exit 0; }}; done\n'
        'echo "$*" >> "$0.log"\n'
        'echo \'{"ok":true,"data":{"fieldValueChanges":[]}}\'\n'
    )
    fake.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCP_SDK": str(MCP / ".mcp-bash" / "sdk"),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "MCPBASH_JSON_TOOL": "jq",
        "MCP_TOOL_ARGS_JSON": json.dumps({"command": "field history-bulk", "argv": argv}),
        "XAFFINITY_CLI": str(fake),
    }
    out = subprocess.run(
        ["bash", str(MCP / "tools" / "execute-read-command" / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    ).stdout
    ran = (tmp_path / "xaffinity.log").exists()
    return json.loads(out.strip().splitlines()[-1]), ran


BASE = ["field-1", "--list-id", "9"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--all", "--strategy", "field"],
        ["--strategy", "field", "--all"],
        ["--all", "--strategy=field"],
    ],
)
def test_all_with_field_strategy_runs(tmp_path: Path, extra: list[str]) -> None:
    result, ran = _run(tmp_path, BASE + extra)
    assert result.get("isError") is not True, result
    assert ran


@pytest.mark.parametrize(
    "extra",
    [["--all"], ["--all", "--strategy", "auto"], ["--all", "--strategy", "entries"]],
)
def test_all_without_field_strategy_is_refused(tmp_path: Path, extra: list[str]) -> None:
    result, ran = _run(tmp_path, BASE + extra)
    assert result.get("isError") is True
    assert "--strategy field" in json.dumps(result)
    assert not ran


def test_max_results_above_the_max_is_refused(tmp_path: Path) -> None:
    result, ran = _run(tmp_path, [*BASE, "--max-results", str(_max() + 1)])
    assert result.get("isError") is True
    assert str(_max()) in json.dumps(result)
    assert not ran


@pytest.mark.parametrize("flag", ["--max-results", "--limit", "-n"])
def test_max_results_within_the_max_runs(tmp_path: Path, flag: str) -> None:
    result, ran = _run(tmp_path, [*BASE, flag, str(_max())])
    assert result.get("isError") is not True, result
    assert ran


def test_max_results_equals_form_is_checked(tmp_path: Path) -> None:
    result, ran = _run(tmp_path, [*BASE, f"--max-results={_max() + 1}"])
    assert result.get("isError") is True
    assert not ran


def test_other_commands_keep_refusing_all(tmp_path: Path) -> None:
    fake_argv = ["--all", "--strategy", "field"]
    fake = tmp_path / "xaffinity"
    fake.write_text("#!/usr/bin/env bash\necho '{}'\n")
    fake.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCP_SDK": str(MCP / ".mcp-bash" / "sdk"),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "MCPBASH_JSON_TOOL": "jq",
        "MCP_TOOL_ARGS_JSON": json.dumps({"command": "list export", "argv": ["9", *fake_argv]}),
        "XAFFINITY_CLI": str(fake),
    }
    out = subprocess.run(
        ["bash", str(MCP / "tools" / "execute-read-command" / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    ).stdout
    assert json.loads(out.strip().splitlines()[-1]).get("isError") is True
