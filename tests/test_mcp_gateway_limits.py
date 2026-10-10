"""MCP gateway limits per command (registry limitConfig): a --max-results above the command's
max is refused (overflow-safe), nothing is injected when it is absent (commands return one page
and nextCursor), and a trimmed result carries no nextCursor (it would skip the trimmed rows).
Runs execute-read-command with a fake CLI."""

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

REGISTRY = {
    c["name"]: c
    for c in json.loads((MCP / ".registry" / "commands.generated.json").read_text())["commands"]
}


def _min_version() -> str:
    data = json.loads((MCP / "server.d" / "requirements.json").read_text())
    return next(d["minVersion"] for d in data["dependencies"] if d["name"] == "xaffinity")


def _run(
    tmp_path: Path, command: str, argv: list[str], payload: dict | None = None, max_bytes: int = 0
) -> tuple[dict, list[str]]:  # type: ignore[type-arg]
    out_file = tmp_path / "payload.json"
    out_file.write_text(json.dumps(payload or {"ok": True, "data": {}}))
    fake = tmp_path / "xaffinity"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do [[ "$a" == version ]] && '
        f'{{ echo \'{{"data":{{"version":"{_min_version()}"}}}}\'; exit 0; }}; done\n'
        'echo "ARGS $* ENV ${AFFINITY_MCP_MAX_LIMIT:-unset}" >> "$0.log"\n'
        f'cat "{out_file}"\n'
    )
    fake.chmod(0o755)
    args: dict[str, object] = {"command": command, "argv": argv}
    if max_bytes:
        args["maxOutputBytes"] = max_bytes
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
        ["bash", str(MCP / "tools" / "execute-read-command" / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    ).stdout
    log = tmp_path / "xaffinity.log"
    calls = log.read_text().splitlines() if log.exists() else []
    return json.loads(out.strip().splitlines()[-1]), calls


def _max(command: str) -> int:
    return int(REGISTRY[command]["limitConfig"]["max"])


@pytest.mark.parametrize("command", ["note feed", "note search", "list export", "transcript get"])
def test_above_the_max_is_refused_with_the_commands_own_max(tmp_path: Path, command: str) -> None:
    argv = ["1"] if command in ("list export", "transcript get") else []
    argv = ["x", *argv[1:]] if command == "note search" else argv
    result, calls = _run(tmp_path, command, [*argv, "--max-results", str(_max(command) + 1)])
    assert result.get("isError") is True
    text = json.dumps(result)
    assert f"at most {_max(command)}" in text
    assert ("--cursor" in text) == ("--cursor" in REGISTRY[command]["parameters"])
    assert calls == []


@pytest.mark.parametrize("value", ["18446744073709551617", "99999999999999999999999"])
def test_huge_values_do_not_overflow_past_the_check(tmp_path: Path, value: str) -> None:
    result, calls = _run(tmp_path, "note feed", ["--max-results", value])
    assert result.get("isError") is True
    assert calls == []


def test_leading_zeros_are_compared_as_numbers(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "note feed", ["--max-results", "0000020"])
    assert result.get("isError") is not True, result
    assert len(calls) == 1


def test_at_the_max_runs(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "note feed", ["-n", str(_max("note feed"))])
    assert result.get("isError") is not True, result
    assert len(calls) == 1


def test_nothing_is_injected_and_no_limit_env_reaches_the_cli(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "note feed", [])
    assert result.get("isError") is not True, result
    assert "--max-results" not in calls[0]
    assert calls[0].endswith("ENV unset")


def test_transcript_get_can_read_a_long_meeting(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "transcript get", ["1", "--max-results", "5000"])
    assert result.get("isError") is not True, result
    assert len(calls) == 1


def test_all_refusal_names_the_commands_max(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, "note feed", ["--all"])
    assert result.get("isError") is True
    assert f"max: {_max('note feed')}" in json.dumps(result)


def test_trimmed_result_drops_next_cursor(tmp_path: Path) -> None:
    rows = [{"id": i, "name": "x" * 900} for i in range(120)]
    payload = {
        "ok": True,
        "data": {"companies": rows},
        "meta": {"pagination": {"companies": {"nextCursor": "c2", "prevCursor": None}}},
    }
    result, _ = _run(tmp_path, "company ls", [], payload, max_bytes=20000)
    wrapped = result["structuredContent"]["result"]
    assert wrapped["truncated"] is True
    assert "c2" not in json.dumps(wrapped)
    assert "--max-results" in wrapped["paginationNote"]


def test_untrimmed_result_keeps_next_cursor(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "data": {"companies": [{"id": 1}]},
        "meta": {"pagination": {"companies": {"nextCursor": "c2", "prevCursor": None}}},
    }
    result, _ = _run(tmp_path, "company ls", [], payload)
    assert "c2" in json.dumps(result["structuredContent"]["result"])
