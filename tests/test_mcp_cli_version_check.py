"""MCP tools check the CLI version against server.d/requirements.json (the MCPB bundle has no
COMPATIBILITY and never runs xaffinity-mcp.sh): writes are refused with an old CLI, reads run
and carry a warning. Runs tool scripts directly with a fake CLI."""

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

# Logs every call; `version` answers $FAKE_VERSION (or fails with "fail"); other commands
# succeed, or fail with a CLI error when $FAKE_FAIL is set.
FAKE_CLI = r"""#!/usr/bin/env bash
echo "$*" >> "$0.log"
for a in "$@"; do
    if [[ "$a" == "version" ]]; then
        [[ "$FAKE_VERSION" == "fail" ]] && exit 1
        printf '{"ok":true,"data":{"version":"%s"}}\n' "$FAKE_VERSION"
        exit 0
    fi
done
if [[ -n "${FAKE_FAIL:-}" ]]; then
    printf '{"ok":false,"error":{"type":"usage_error","message":"No such option: --json"}}\n'
    exit 2
fi
printf '{"ok":true,"data":{"url":"https://example.invalid/f","persons":[]}}\n'
"""

WRITE = ("execute-write-command", '{"command": "person merge", "argv": ["1", "2"]}')
READ = ("execute-read-command", '{"command": "person ls", "argv": []}')
FILE_URL = ("get-file-url", '{"fileId": "123"}')


def _min_version() -> str:
    data = json.loads((MCP / "server.d" / "requirements.json").read_text())
    return next(d["minVersion"] for d in data["dependencies"] if d["name"] == "xaffinity")


def _run(
    tmp_path: Path,
    tool: tuple[str, str],
    version: str,
    home: Path | None = None,
    fail: bool = False,
) -> dict:  # type: ignore[type-arg]
    fake = tmp_path / "bin" / "xaffinity"
    if not fake.exists():
        fake.parent.mkdir()
        fake.write_text(FAKE_CLI)
        fake.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home or tmp_path),
        "MCP_SDK": str(MCP / ".mcp-bash" / "sdk"),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "MCPBASH_JSON_TOOL": "jq",
        "MCP_TOOL_ARGS_JSON": tool[1],
        "XAFFINITY_CLI": str(fake),
        "FAKE_VERSION": version,
        "FAKE_FAIL": "1" if fail else "",
    }
    out = subprocess.run(
        ["bash", str(MCP / "tools" / tool[0] / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "bin" / "xaffinity.log"
    return log.read_text().splitlines() if log.exists() else []


def _version_calls(tmp_path: Path) -> int:
    return sum(c.startswith("version") for c in _calls(tmp_path))


def _ran(tmp_path: Path, command: str) -> bool:
    return any(command in c for c in _calls(tmp_path))


def _cache(tmp_path: Path) -> Path:
    return tmp_path / ".cache" / "xaffinity" / "mcp-cli-version"


# --- writes -------------------------------------------------------------------------------------


def test_write_with_old_cli_is_refused_before_confirmation(tmp_path: Path) -> None:
    result = _run(tmp_path, WRITE, "1.0.0")
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["type"] == "cli_too_old"
    text = result["content"][0]["text"]
    assert _min_version() in text
    assert "1.0.0" in text
    assert "update the CLI" in text
    assert not _ran(tmp_path, "merge")


def test_write_with_new_enough_cli_goes_on_to_confirmation(tmp_path: Path) -> None:
    result = _run(tmp_path, WRITE, _min_version())
    assert result["structuredContent"]["error"]["type"] == "confirmation_required"


def test_upgrade_unblocks_writes_on_the_next_call(tmp_path: Path) -> None:
    _run(tmp_path, READ, "1.0.0")  # caches the old version for reads
    # Same path and file (like a pyenv shim after pip install -U), now a new version
    result = _run(tmp_path, WRITE, _min_version())
    assert result["structuredContent"]["error"]["type"] == "confirmation_required"


def test_cached_version_below_a_raised_minimum_is_probed_again(tmp_path: Path) -> None:
    _run(tmp_path, READ, _min_version())
    cli, _ = _cache(tmp_path).read_text().rstrip("\n").split("\t")
    _cache(tmp_path).write_text(f"{cli}\t1.0.0\n")
    result = _run(tmp_path, WRITE, "1.0.0")
    assert result["structuredContent"]["error"]["type"] == "cli_too_old"


# --- reads --------------------------------------------------------------------------------------


def test_read_with_old_cli_runs_and_warns_first(tmp_path: Path) -> None:
    result = _run(tmp_path, READ, "1.0.0")
    assert result["isError"] is False
    assert _ran(tmp_path, "person ls")
    warning = result["content"][0]["text"]
    assert warning.startswith("Warning: the xaffinity CLI 1.0.0 is older")
    assert _min_version() in warning
    assert result["structuredContent"]["cliWarning"] == warning
    assert len(result["content"]) == 2  # the result text follows


def test_read_with_new_enough_cli_has_no_warning(tmp_path: Path) -> None:
    result = _run(tmp_path, READ, _min_version())
    assert len(result["content"]) == 1
    assert "cliWarning" not in result["structuredContent"]
    assert "Warning:" not in json.dumps(result)


def test_read_error_with_old_cli_names_the_old_cli(tmp_path: Path) -> None:
    result = _run(tmp_path, READ, "1.0.0", fail=True)
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "No such option" in text
    assert "older than this MCP server needs" in text


def test_emit_json_read_puts_the_warning_first(tmp_path: Path) -> None:
    # get-file-url emits its data as is; the framework turns it into the text
    result = _run(tmp_path, FILE_URL, "1.0.0")
    assert next(iter(result)) == "cliWarning"
    assert result["url"] == "https://example.invalid/f"


def test_old_version_is_reused_by_reads_for_a_while(tmp_path: Path) -> None:
    _run(tmp_path, READ, "1.0.0")
    _run(tmp_path, READ, "1.0.0")
    assert _version_calls(tmp_path) == 1


def test_new_enough_version_is_cached(tmp_path: Path) -> None:
    _run(tmp_path, READ, _min_version())
    _run(tmp_path, WRITE, _min_version())
    assert _version_calls(tmp_path) == 1


def test_unreadable_version_neither_blocks_nor_warns(tmp_path: Path) -> None:
    assert _run(tmp_path, WRITE, "fail")["structuredContent"]["error"]["type"] == (
        "confirmation_required"
    )
    assert len(_run(tmp_path, READ, "fail")["content"]) == 1


def test_no_cache_when_home_is_the_project_root(tmp_path: Path) -> None:
    # The framework sets HOME to the project root when HOME is missing; don't write there.
    _run(tmp_path, READ, _min_version(), home=MCP)
    assert not (MCP / ".cache").exists()


# --- wiring -------------------------------------------------------------------------------------

READ_TOOLS = {"execute-read-command", "get-entity-dossier", "get-file-url", "query"}


def test_tools_check_the_cli_version() -> None:
    tools = {p.parent.name: p.read_text() for p in (MCP / "tools").glob("*/tool.sh")}
    writes = {n for n, t in tools.items() if "xaffinity_require_cli_version || exit 0" in t}
    reads = {n for n, t in tools.items() if "\nxaffinity_note_cli_version\n" in t}
    assert writes == {"execute-write-command"}
    assert reads == READ_TOOLS


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_read_tools_emit_results_through_the_warning_helpers(tool: str) -> None:
    lines = (MCP / "tools" / tool / "tool.sh").read_text().splitlines()
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    for plain in ("mcp_result_success ", "mcp_result_error ", "mcp_emit_json "):
        assert plain not in text, f"{tool} uses {plain.strip()}; use the xaffinity_ helper"
