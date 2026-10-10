"""execute-read-command trims large list results instead of failing, for every list command whose
rows aren't at a path the generic rules derive (MCP 1.28.1). Runs tool.sh with a fake CLI."""

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

# command -> (argv, JSON path of the rows, as a list of keys under the top level)
CASES = {
    "interaction feed": (["--type", "email"], ["data", "interactions"]),
    "transcript ls": ([], ["data", "transcripts"]),
    "transcript get": (["1"], ["data", "transcript", "fragments"]),
    "field changes": ([], ["data", "fieldValueChanges"]),
    "field history": (["field-1", "--person-id", "2"], ["data", "fieldValueChanges"]),
    "field options ls": (["field-1", "--list-id", "9"], ["data", "options"]),
    "company relationships": (["10"], ["data", "relationships"]),
    "person relationships": (["10"], ["data", "relationships"]),
    "company merge-history ls": ([], ["data", "merges"]),
    "person merge-history ls": ([], ["data", "merges"]),
    "task ls": (["--kind", "company-merge"], ["data", "tasks"]),
}


def _payload(path: list[str]) -> dict:  # type: ignore[type-arg]
    rows = [{"id": i, "text": "x" * 900} for i in range(120)]
    node: dict = {"ok": True}  # type: ignore[type-arg]
    cur = node
    for key in path[:-1]:
        cur[key] = {"id": 1} if key == "transcript" else {}
        cur = cur[key]
    cur[path[-1]] = rows
    return node


def _min_version() -> str:
    data = json.loads((MCP / "server.d" / "requirements.json").read_text())
    return next(d["minVersion"] for d in data["dependencies"] if d["name"] == "xaffinity")


@pytest.mark.parametrize("command", sorted(CASES))
def test_large_result_is_trimmed_not_refused(tmp_path: Path, command: str) -> None:
    argv, path = CASES[command]
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(_payload(path)))
    fake = tmp_path / "xaffinity"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do [[ "$a" == version ]] && '
        f'{{ echo \'{{"data":{{"version":"{_min_version()}"}}}}\'; exit 0; }}; done\n'
        f'cat "{payload}"\n'
    )
    fake.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCP_SDK": str(MCP / ".mcp-bash" / "sdk"),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "MCPBASH_JSON_TOOL": "jq",
        "MCP_TOOL_ARGS_JSON": json.dumps(
            {"command": command, "argv": argv, "maxOutputBytes": 20000}
        ),
        "XAFFINITY_CLI": str(fake),
    }
    out = subprocess.run(
        ["bash", str(MCP / "tools" / "execute-read-command" / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    ).stdout
    result = json.loads(out.strip().splitlines()[-1])
    assert result.get("isError") is not True, result
    wrapped = result["structuredContent"]["result"]
    assert wrapped["truncated"] is True
    assert 0 < wrapped["kept"] < wrapped["total"] == 120
    rows = wrapped["result"]
    for key in path:
        rows = rows[key]
    assert len(rows) == wrapped["kept"]
