# ruff: noqa: E501  (the fake CLI prints canned JSON lines)
"""MCP: list-name completion script, bundle env overrides, and read-only flags.

Scripts run with bash and a fake `xaffinity` (XAFFINITY_CLI); HOME is a temp dir.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MCP = REPO / "mcp"
JQ = shutil.which("jq")

pytestmark = pytest.mark.skipif(JQ is None, reason="jq not installed")

FAKE_CLI = r"""#!/usr/bin/env bash
printf '%s|key=%s\n' "$*" "${AFFINITY_API_KEY:-<unset>}" >> "$FAKE_LOG"
case "${FAKE_MODE:-ok}:$*" in
    fail:*) exit 1 ;;
    old:*--query=*) echo '{"ok":false,"error":{"type":"usage_error","message":"No such option: --query"}}'; exit 2 ;;
    *--query=*) echo '{"ok":true,"data":{"lists":[{"id":1,"name":"Dealflow"},{"id":2,"name":"Old deals"},{"id":3,"name":"Deals 2024"},{"id":1,"name":"Dealflow"}]}}' ;;
    *) echo '{"ok":true,"data":{"lists":[{"id":1,"name":"Dealflow"},{"id":4,"name":"Portfolio"}]}}' ;;
esac
"""


def _complete(
    tmp_path: Path,
    argument: str,
    value: str,
    *,
    prompt: str = "pipeline-review",
    script: Path | None = None,
    limit: int = 5,
    offset: int = 0,
    mode: str = "ok",
    project_root: Path = MCP,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake = tmp_path / "xaffinity"
    fake.write_text(FAKE_CLI)
    fake.chmod(0o755)
    log = tmp_path / "argv.log"
    args = {"argument": {"name": argument, "value": value}, "query": value, "prefix": value}
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCPBASH_PROJECT_ROOT": str(project_root),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "XAFFINITY_CLI": str(fake),
        "FAKE_LOG": str(log),
        "FAKE_MODE": mode,
        "AFFINITY_API_KEY": "K",
        "MCP_COMPLETION_ARGS_JSON": json.dumps(args),
        "MCP_COMPLETION_LIMIT": str(limit),
        "MCP_COMPLETION_OFFSET": str(offset),
    }
    script = script or MCP / "prompts" / prompt / f"{prompt}.completion.sh"
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    return result, (log.read_text().splitlines() if log.exists() else [])


@pytest.mark.parametrize("prompt", ["pipeline-review", "change-status"])
def test_list_names_matching_the_prefix_first(tmp_path: Path, prompt: str) -> None:
    result, calls = _complete(tmp_path, "listName", " deal ", prompt=prompt)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "suggestions": ["Dealflow", "Deals 2024", "Old deals"],
        "hasMore": False,
    }
    assert calls == [
        "--readonly --quiet --timeout 3 --max-retries 0 list ls --all --query=deal --output json|key=K"
    ]


def test_pages(tmp_path: Path) -> None:
    result, _ = _complete(tmp_path, "listName", "deal", limit=2)
    assert json.loads(result.stdout) == {
        "suggestions": ["Dealflow", "Deals 2024"],
        "hasMore": True,
        "next": 2,
    }
    result, _ = _complete(tmp_path, "listName", "deal", limit=2, offset=2)
    assert json.loads(result.stdout) == {"suggestions": ["Old deals"], "hasMore": False}


def test_blank_prefix_reads_one_page(tmp_path: Path) -> None:
    result, calls = _complete(tmp_path, "listName", "  ")
    assert json.loads(result.stdout)["suggestions"] == ["Dealflow", "Portfolio"]
    assert "--max-results 100" in calls[0] and "--query" not in calls[0]


def test_other_arguments_get_nothing_and_no_cli_call(tmp_path: Path) -> None:
    result, calls = _complete(tmp_path, "entityName", "ac", prompt="change-status")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"suggestions": [], "hasMore": False}
    assert calls == []


TEMPLATES = MCP / "resources"


def test_field_catalog_template_suggests_entity_types_and_lists(tmp_path: Path) -> None:
    script = TEMPLATES / "xaffinity-field-catalogs.completion.sh"
    result, _ = _complete(tmp_path, "entityType", "", script=script, limit=10)
    assert json.loads(result.stdout)["suggestions"] == [
        "company",
        "person",
        "opportunity",
        "Dealflow",
        "Portfolio",
    ]
    result, _ = _complete(tmp_path, "entityType", "pe", script=script, limit=10)
    assert json.loads(result.stdout)["suggestions"] == ["person"]


@pytest.mark.parametrize("name", ["xaffinity-saved-views", "xaffinity-workflow-config"])
def test_list_templates_suggest_list_names(tmp_path: Path, name: str) -> None:
    script = TEMPLATES / f"{name}.completion.sh"
    result, _ = _complete(tmp_path, "listId", "deal", script=script)
    assert json.loads(result.stdout)["suggestions"] == ["Dealflow", "Deals 2024", "Old deals"]
    result, _ = _complete(tmp_path, "entityType", "deal", script=script)
    assert json.loads(result.stdout)["suggestions"] == []


@pytest.mark.parametrize("mode", ["fail"])
def test_cli_failure_gives_empty_suggestions_and_exit_0(tmp_path: Path, mode: str) -> None:
    result, _ = _complete(tmp_path, "listName", "deal", mode=mode)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"suggestions": [], "hasMore": False}


def test_prefix_that_looks_like_an_option_stays_a_value(tmp_path: Path) -> None:
    result, calls = _complete(tmp_path, "listName", "-q")
    assert result.returncode == 0
    assert "--query=-q " in calls[0] + " "


def test_completion_wrappers_are_executable_in_git() -> None:
    out = subprocess.run(
        [
            "git",
            "ls-files",
            "-s",
            "mcp/prompts/*/*.completion.sh",
            "mcp/resources/*.completion.sh",
            "mcp/completions/list-name.sh",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    ).stdout.splitlines()
    assert len(out) == 6
    assert all(line.startswith("100755") for line in out), out


# --- bundle env ---------------------------------------------------------------------------------


def _sync_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "sync_mcp_bundle_env", REPO / "tools" / "sync_mcp_bundle_env.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_server_meta_env_matches_env_sh() -> None:
    """The bundle reads server.meta.json "env"; xaffinity-mcp.sh reads env.sh (whose values win
    per scope), so both must allow the same variables. mcp-bash refuses MCPBASH_* names in
    server.meta.json."""
    sync = _sync_module()
    meta = json.loads((MCP / "server.d" / "server.meta.json").read_text())
    assert meta["env"] == sync.expected_env()
    assert "platform_overrides" not in meta
    env = meta["env"]
    names = env["MCPBASH_TOOL_ENV_ALLOWLIST"].split(",")
    assert env["MCPBASH_PROVIDER_ENV_ALLOWLIST"] == env["MCPBASH_TOOL_ENV_ALLOWLIST"]
    assert not [n for n in names if n.startswith("MCPBASH_")]
    assert {"AFFINITY_API_KEY", "SYSTEMROOT", "LOCALAPPDATA", "COMSPEC"} <= set(names)
    assert set(names) == {n for n in sync.env_sh_allowlist() if not n.startswith("MCPBASH_")}


# --- CLI errors ---------------------------------------------------------------------------------


def _cli_error(tmp_path: Path, stdout: str, stderr: str, code: str) -> dict:  # type: ignore[type-arg]
    script = 'source "$1/lib/common.sh"; xaffinity_cli_error_json "$2" "$3" "$4"'
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "XAFFINITY_CLI": "/bin/true",
    }
    out = subprocess.run(
        ["bash", "-c", script, "_", str(MCP), stdout, stderr, code],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    ).stdout
    return json.loads(out)


def test_cli_error_json_reads_the_cli_error(tmp_path: Path) -> None:
    error = {"type": "auth_error", "message": "[401] Invalid key", "hint": "Run setup-key"}
    stdout = json.dumps({"ok": False, "error": error})
    assert _cli_error(tmp_path, stdout, "", "3") == {
        "message": "[401] Invalid key",
        "errorType": "auth_error",
        "hint": "Run setup-key",
    }
    assert _cli_error(tmp_path, "", "boom", "1") == {"message": "boom"}
    assert _cli_error(tmp_path, "", "", "4") == {"message": "CLI exited with code 4"}


# --- read-only / disable-destructive flags ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "blocked"),
    [("1", True), ("true", True), ("TRUE", True), ("false", False), ("", False)],
)
def test_read_only_accepts_the_bundle_true(value: str, blocked: bool) -> None:
    script = (
        'source "$1"; mcp_tools_policy_check execute-write-command && echo allowed || echo blocked'
    )
    out = subprocess.run(
        ["bash", "-c", script, "_", str(MCP / "server.d" / "policy.sh")],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "AFFINITY_MCP_READ_ONLY": value},
        timeout=30,
        check=False,
    ).stdout.strip()
    assert out == ("blocked" if blocked else "allowed")


@pytest.mark.parametrize(
    ("value", "enabled"),
    [("1", True), ("true", True), ("True", True), ("0", False), ("false", False), ("", False)],
)
def test_flag_helper(tmp_path: Path, value: str, enabled: bool) -> None:
    script = 'source "$1/lib/common.sh"; xaffinity_flag_enabled "$2" && echo on || echo off'
    out = subprocess.run(
        ["bash", "-c", script, "_", str(MCP), value],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "MCPBASH_PROJECT_ROOT": str(MCP),
            "XAFFINITY_CLI": "/bin/true",
        },
        timeout=30,
        check=False,
    ).stdout.strip()
    assert out == ("on" if enabled else "off")
