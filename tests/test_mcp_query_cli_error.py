"""MCP `query` tool: when the CLI fails without a JSON error on stdout, its plain-text stderr is
the message the agent sees (JSON progress lines on stderr are left out). Runs the tool with a fake
CLI."""

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


def _run(tmp_path: Path, stderr_text: str, exit_code: int) -> dict:  # type: ignore[type-arg]
    err_file = tmp_path / "stderr.txt"
    err_file.write_text(stderr_text)
    fake = tmp_path / "xaffinity"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do [[ "$a" == version ]] && '
        f'{{ echo \'{{"data":{{"version":"{_min_version()}"}}}}\'; exit 0; }}; done\n'
        f'cat "{err_file}" >&2\n'
        f"exit {exit_code}\n"
    )
    fake.chmod(0o755)
    args = {"query": {"from": "persons", "limit": 1}}
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCP_SDK": str(MCP / ".mcp-bash" / "sdk"),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "MCPBASH_JSON_TOOL": "jq",
        "MCP_TOOL_ARGS_JSON": json.dumps(args),
        "XAFFINITY_CLI": str(fake),
    }
    out = subprocess.run(
        ["bash", str(MCP / "tools" / "query" / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])  # type: ignore[no-any-return]


def _text(result: dict) -> str:  # type: ignore[type-arg]
    return json.dumps(result)


def test_plain_text_stderr_reaches_the_agent(tmp_path: Path) -> None:
    result = _run(tmp_path, "Error: Unsupported query entity 'persons2'\n", 2)
    assert "Unsupported query entity" in _text(result)


def test_json_progress_lines_are_left_out(tmp_path: Path) -> None:
    stderr = '{"type": "progress", "pct": 50}\nTraceback: boom happened\n'
    result = _run(tmp_path, stderr, 1)
    text = _text(result)
    assert "boom happened" in text
    assert "progress" not in text
