# ruff: noqa: E501  (the fake CLI prints canned JSON lines)
"""MCP resource scripts, called through providers/xaffinity.sh like the server does.

Runs bash with a fake `xaffinity` (XAFFINITY_CLI) that logs its argv and answers like the CLI:
JSON on stdout, CLI exit codes. HOME is a temp dir (no developer debug flag file), and
AFFINITY_API_KEY holds a marker so a leak would show up in the output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MCP = REPO / "mcp"
PROVIDER = MCP / "providers" / "xaffinity.sh"
JQ = shutil.which("jq")
SECRET = "SECRET-FAKE-KEY"

pytestmark = pytest.mark.skipif(JQ is None, reason="jq not installed")

FAKE_CLI = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_LOG"
cmd="" ref=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[$i]}" in
        get) cmd="list get"; ref="${args[$((i + 1))]}" ;;
        --list-id) cmd="field ls"; ref="${args[$((i + 1))]}" ;;
    esac
done
case "$ref" in
    "Deal Pipeline"|"deal pipeline"|"Deal%20Pipeline"|"373446"|"100% Club")
        if [[ "$cmd" == "list get" ]]; then
            echo '{"ok":true,"command":{"name":"list get"},"data":{"list":{"id":373446,"name":"Deal Pipeline","type":8},"savedViews":[{"id":9,"name":"Active","type":"sheet"}]}}'
        else
            echo '{"ok":true,"command":{"name":"field ls","modifiers":{"listId":373446}},"data":{"fields":[{"id":"field-1","name":"Status","valueType":"ranked-dropdown","enrichmentSource":null,"dropdownOptions":[{"id":1,"text":"Won"}]}]}}'
        fi
        ;;
    "Twins")
        echo '{"ok":false,"error":{"type":"ambiguous_resolution","message":"Ambiguous list name: \"Twins\" (2 matches)","details":{"matches":[{"listId":1,"name":"Twins","type":0},{"listId":2,"name":"twins","type":8}]}}}'
        exit 2
        ;;
    "Boom")
        echo '{"ok":false,"error":{"type":"server_error","message":"Affinity is down"}}'
        exit 1
        ;;
    *)
        echo '{"ok":false,"error":{"type":"not_found","message":"List not found"}}'
        exit 4
        ;;
esac
"""


def _env(tmp_path: Path, json_tool: str | None = JQ) -> dict[str, str]:
    fake = tmp_path / "xaffinity"
    fake.write_text(FAKE_CLI)
    fake.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "XAFFINITY_CLI": str(fake),
        "FAKE_LOG": str(tmp_path / "argv.log"),
        "AFFINITY_API_KEY": SECRET,
    }
    if json_tool is not None:
        env["MCPBASH_JSON_TOOL_BIN"] = json_tool
    return env


def _read(
    tmp_path: Path, uri: str, *, bash: str = "bash", json_tool: str | None = JQ
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    result = subprocess.run(
        [bash, str(PROVIDER), uri],
        capture_output=True,
        text=True,
        env=_env(tmp_path, json_tool),
        timeout=60,
        check=False,
    )
    log = tmp_path / "argv.log"
    return result, (log.read_text().splitlines() if log.exists() else [])


def _bashes() -> list[str]:
    found = ["bash"]
    if Path("/bin/bash").exists() and shutil.which("bash") != "/bin/bash":
        found.append("/bin/bash")  # macOS: bash 3.2, the oldest the MCP server supports
    return found


# --- field catalogs ---------------------------------------------------------------------------


@pytest.mark.parametrize("bash", _bashes())
@pytest.mark.parametrize("ref", ["Deal%20Pipeline", "deal%20pipeline", "373446", "100%25%20Club"])
def test_field_catalog_resolves_a_list_in_one_cli_call(tmp_path: Path, bash: str, ref: str) -> None:
    result, calls = _read(tmp_path, f"xaffinity://field-catalogs/{ref}", bash=bash)
    assert result.returncode == 0, result.stderr
    catalog = json.loads(result.stdout)
    assert catalog["listId"] == 373446
    assert catalog["fields"][0]["name"] == "Status"
    assert len(calls) == 1 and "--readonly" in calls[0] and "field ls --list-id" in calls[0]


def test_decoded_once(tmp_path: Path) -> None:
    """`Deal%2520Pipeline` reaches the CLI as `Deal%20Pipeline` (the provider decodes once)."""
    result, calls = _read(tmp_path, "xaffinity://field-catalogs/Deal%2520Pipeline")
    assert result.returncode == 0, result.stderr
    assert "--list-id Deal%20Pipeline " in calls[0] + " "


def test_unknown_and_ambiguous_lists(tmp_path: Path) -> None:
    result, _ = _read(tmp_path, "xaffinity://field-catalogs/Nope")
    assert result.returncode == 4 and "Unknown list: Nope" in result.stderr
    result, _ = _read(tmp_path, "xaffinity://field-catalogs/Twins")
    assert result.returncode == 4
    assert "Twins (1)" in result.stderr and "twins (2)" in result.stderr


def test_other_cli_errors_keep_the_message(tmp_path: Path) -> None:
    result, _ = _read(tmp_path, "xaffinity://field-catalogs/Boom")
    assert result.returncode == 5 and "Affinity is down" in result.stderr


@pytest.mark.parametrize("json_tool", [None, "jq"])
def test_jq_without_an_absolute_path_does_not_recurse(
    tmp_path: Path, json_tool: str | None
) -> None:
    result, calls = _read(tmp_path, "xaffinity://field-catalogs/company", json_tool=json_tool)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["entityType"] == "company"
    assert calls == []


# --- saved views / workflow config ------------------------------------------------------------


@pytest.mark.parametrize("bash", _bashes())
def test_saved_views_by_name(tmp_path: Path, bash: str) -> None:
    result, calls = _read(tmp_path, "xaffinity://saved-views/Deal%20Pipeline", bash=bash)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["listId"] == 373446
    assert out["savedViews"] == [{"id": 9, "name": "Active", "type": "sheet"}]
    assert "--readonly" in calls[0]


def test_saved_views_argument_is_data_not_jq(tmp_path: Path) -> None:
    """The argument used to be pasted into the jq program: `1, leak: $ENV.AFFINITY_API_KEY`
    returned the API key."""
    uri = "xaffinity://saved-views/1%2C%20leak%3A%20%24ENV.AFFINITY_API_KEY"
    result, _ = _read(tmp_path, uri)
    assert SECRET not in result.stdout + result.stderr
    assert result.returncode == 4  # resolved as a (missing) list name


def test_workflow_config_lists_ranked_status_fields(tmp_path: Path) -> None:
    result, calls = _read(tmp_path, "xaffinity://workflow-config/Deal%20Pipeline")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["listName"] == "Deal Pipeline"
    assert [f["name"] for f in out["statusFields"]] == ["Status"]
    assert "field ls --list-id 373446" in calls[1]


# --- provider path checks ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "xaffinity://../../x",
        "xaffinity://..%2F..%2Fx",
        "xaffinity:///etc/passwd",
        "xaffinity://me/../me",
        "xaffinity://me.json",
        "xaffinity://me?x=1",
    ],
)
def test_static_paths_outside_plain_segments_are_not_found(tmp_path: Path, uri: str) -> None:
    result, calls = _read(tmp_path, uri)
    assert result.returncode == 3, (uri, result.stdout, result.stderr)
    assert calls == []


@pytest.mark.parametrize("arg", ["", "%20%20", "-%2Dhelp", "--help", "x%00y", "a%0Ab"])
def test_bad_template_arguments_are_refused(tmp_path: Path, arg: str) -> None:
    result, calls = _read(tmp_path, f"xaffinity://field-catalogs/{arg}")
    assert result.returncode == 4, (arg, result.stderr)
    assert calls == []


# --- environment -------------------------------------------------------------------------------


def test_providers_and_tools_get_the_same_env_allowlist() -> None:
    out = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; echo "$MCPBASH_TOOL_ENV_ALLOWLIST|$MCPBASH_PROVIDER_ENV_ALLOWLIST|$MCPBASH_PROVIDER_ENV_MODE"',
            "_",
            str(MCP / "server.d" / "env.sh"),
        ],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": "/nonexistent", "XAFFINITY_NO_UPDATE_CHECK": "1"},
        timeout=30,
        check=False,
    ).stdout.strip()
    tools, providers, mode = out.split("|")
    assert tools == providers and mode == "allowlist"
    assert "AFFINITY_API_KEY" in tools.split(",")
