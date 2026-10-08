"""mcp/resources/field-catalogs/field-catalogs.sh resolves list names through the CLI.

Runs the script with bash and a fake `xaffinity` (XAFFINITY_CLI) that logs its argv and answers
like the CLI: JSON on stdout, CLI exit codes. HOME is a temp dir so a developer's debug flag file
doesn't apply.
"""

# ruff: noqa: E501  (the fake CLI prints canned JSON lines)
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "mcp" / "resources" / "field-catalogs" / "field-catalogs.sh"
JQ = shutil.which("jq")

pytestmark = pytest.mark.skipif(JQ is None, reason="jq not installed")

FAKE_CLI = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_LOG"
list_id=""
for ((i = 1; i <= $#; i++)); do
    if [[ "${!i}" == "--list-id" ]]; then j=$((i + 1)); list_id="${!j}"; fi
done
case "$list_id" in
    "Deal Pipeline"|"deal pipeline"|"373446")
        echo '{"ok":true,"command":{"name":"field ls","modifiers":{"listId":373446}},"data":{"fields":[{"id":"field-1","name":"Status","valueType":"ranked-dropdown","enrichmentSource":null}]}}'
        ;;
    "Twins")
        echo '{"ok":false,"command":{"name":"field ls"},"error":{"type":"ambiguous_resolution","message":"Ambiguous list name: \"Twins\" (2 matches)","details":{"matches":[{"listId":1,"name":"Twins","type":0},{"listId":2,"name":"twins","type":8}]}}}'
        exit 2
        ;;
    "Boom")
        echo '{"ok":false,"command":{"name":"field ls"},"error":{"type":"server_error","message":"Affinity is down"}}'
        exit 1
        ;;
    *)
        echo '{"ok":false,"command":{"name":"field ls"},"error":{"type":"not_found","message":"List not found"}}'
        exit 4
        ;;
esac
"""


def _run(
    tmp_path: Path, arg: str, *, bash: str = "bash", json_tool: str | None = JQ
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake = tmp_path / "xaffinity"
    fake.write_text(FAKE_CLI)
    fake.chmod(0o755)
    log = tmp_path / "argv.log"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCPBASH_PROJECT_ROOT": str(REPO / "mcp"),
        "XAFFINITY_CLI": str(fake),
        "FAKE_LOG": str(log),
    }
    if json_tool is not None:
        env["MCPBASH_JSON_TOOL_BIN"] = json_tool
    result = subprocess.run(
        [bash, str(SCRIPT), arg], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def _bashes() -> list[str]:
    found = ["bash"]
    if Path("/bin/bash").exists() and shutil.which("bash") != "/bin/bash":
        found.append("/bin/bash")  # macOS: bash 3.2, the oldest the MCP server supports
    return found


@pytest.mark.parametrize("bash", _bashes())
@pytest.mark.parametrize("arg", ["Deal Pipeline", "deal pipeline", "Deal%20Pipeline", "373446"])
def test_list_name_or_id_resolved_in_one_cli_call(tmp_path: Path, bash: str, arg: str) -> None:
    result, calls = _run(tmp_path, arg, bash=bash)
    assert result.returncode == 0, result.stderr
    catalog = json.loads(result.stdout)
    assert catalog["listId"] == 373446
    assert catalog["fields"][0]["name"] == "Status"
    assert len(calls) == 1
    assert "--readonly" in calls[0] and "field ls --list-id" in calls[0]


def test_unknown_list_name(tmp_path: Path) -> None:
    result, _calls = _run(tmp_path, "Nope")
    assert result.returncode == 4
    assert "Unknown entity type or list name: Nope" in result.stderr


def test_ambiguous_list_name_names_the_candidates(tmp_path: Path) -> None:
    result, _calls = _run(tmp_path, "Twins")
    assert result.returncode == 4
    assert "Twins (1)" in result.stderr and "twins (2)" in result.stderr


def test_other_cli_errors_keep_the_message(tmp_path: Path) -> None:
    result, _calls = _run(tmp_path, "Boom")
    assert result.returncode == 5
    assert "Affinity is down" in result.stderr


@pytest.mark.parametrize("json_tool", [None, "jq"])
def test_jq_without_an_absolute_path_does_not_recurse(
    tmp_path: Path, json_tool: str | None
) -> None:
    """jq_tool must not call the jq() shell function (it used to recurse until bash crashed)."""
    result, calls = _run(tmp_path, "company", json_tool=json_tool)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["entityType"] == "company"
    assert calls == []
