"""MCP server and CLI plugin hook fixes: every CLI call carries the project .env location and
reads carry --readonly (writes don't); the query tool keeps a caller's cursor as one argument;
the launcher's tool allowlist comes from server.d/policy.sh; `config setup-key` is not exposed;
flag aliases count for required and duplicate checks; a write dry run uses no confirmation;
cache TTLs work with GNU and BSD stat; the Read hook covers the xaffinity config file.
Runs tool scripts directly with a fake CLI that logs one argument per line."""

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
HOOK = REPO / "plugins" / "xaffinity-cli" / "hooks" / "guard-env-read.sh"

pytestmark = pytest.mark.skipif(JQ is None, reason="jq not installed")

# Each call is logged as its arguments, one per line, then a "--END--" line.
FAKE_CLI = r"""#!/usr/bin/env bash
for a in "$@"; do
    if [[ "$a" == "version" ]]; then
        printf '{"ok":true,"data":{"version":"%s"}}\n' "$FAKE_VERSION"
        exit 0
    fi
done
{ printf '%s\n' "$@"; echo "--END--"; } >> "$0.log"
for a in "$@"; do
    [[ "$a" == "--dry-run" ]] && { echo '{"execution":{"estimatedApiCalls":1}}'; exit 0; }
done
echo '{"ok":true,"data":{}}'
"""


def _min_version() -> str:
    data = json.loads((MCP / "server.d" / "requirements.json").read_text())
    return next(d["minVersion"] for d in data["dependencies"] if d["name"] == "xaffinity")


def _run(
    tmp_path: Path, tool: str, args: dict, extra_env: dict[str, str] | None = None
) -> tuple[dict, list[list[str]]]:  # type: ignore[type-arg]
    fake = tmp_path / "bin" / "xaffinity"
    if not fake.exists():
        fake.parent.mkdir()
        fake.write_text(FAKE_CLI)
        fake.chmod(0o755)
    log = Path(f"{fake}.log")
    log.unlink(missing_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCP_SDK": str(MCP / ".mcp-bash" / "sdk"),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "MCPBASH_JSON_TOOL": "jq",
        "MCP_TOOL_ARGS_JSON": json.dumps(args),
        "XAFFINITY_CLI": str(fake),
        "FAKE_VERSION": _min_version(),
        **(extra_env or {}),
    }
    proc = subprocess.run(
        ["bash", str(MCP / "tools" / tool / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=MCP,
        timeout=60,
        check=False,
    )
    calls: list[list[str]] = []
    if log.exists():
        current: list[str] = []
        for line in log.read_text().splitlines():
            if line == "--END--":
                calls.append(current)
                current = []
            else:
                current.append(line)
    return json.loads(proc.stdout.strip().splitlines()[-1]), calls


def _env_file(tmp_path: Path) -> Path:
    project = tmp_path / "My Project"
    project.mkdir()
    env_file = project / ".env"
    env_file.write_text("AFFINITY_API_KEY=placeholder\n")
    return env_file


def _before_command(call: list[str], command_word: str) -> list[str]:
    return call[: call.index(command_word)]


# --- 39: .env location and --readonly on every CLI path ---------------------------------------


def test_read_command_passes_env_file_and_readonly(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)
    result, calls = _run(
        tmp_path,
        "execute-read-command",
        {"command": "person ls", "argv": []},
        {"XAFFINITY_ENV_FILE": str(env_file)},
    )
    assert result.get("isError") is not True, result
    assert len(calls) == 1
    pre = _before_command(calls[0], "person")
    assert pre == ["--dotenv", "--env-file", str(env_file), "--readonly"]
    executed = result["structuredContent"]["result"]["executed"]
    assert executed[1:5] == ["--dotenv", "--env-file", str(env_file), "--readonly"]


def test_read_command_with_progress_passes_flags_once(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)
    progress = tmp_path / "progress.ndjson"
    progress.touch()
    result, calls = _run(
        tmp_path,
        "execute-read-command",
        {"command": "field history-bulk", "argv": ["field-1", "--list-id", "1"]},
        {"XAFFINITY_ENV_FILE": str(env_file), "MCP_PROGRESS_STREAM": str(progress)},
    )
    assert result.get("isError") is not True, result
    assert len(calls) == 1
    call = calls[0]
    assert call.count("--dotenv") == 1
    assert call.count("--env-file") == 1
    assert call.count("--readonly") == 1
    assert _before_command(call, "field")[:3] == ["--dotenv", "--env-file", str(env_file)]


def test_no_env_file_means_no_dotenv(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "execute-read-command",
        {"command": "person ls", "argv": []},
        {"XAFFINITY_CLI_PATTERN": "xaffinity --dotenv --readonly <command> --json"},
    )
    assert result.get("isError") is not True, result
    # A bare --dotenv would search the server directory (tools' cwd) and fail
    assert "--dotenv" not in calls[0]
    assert _before_command(calls[0], "person") == ["--readonly"]


def test_write_command_passes_env_file_without_readonly(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)
    result, calls = _run(
        tmp_path,
        "execute-write-command",
        {"command": "person create", "argv": ["--first-name", "Jane", "--last-name", "Doe"]},
        {"XAFFINITY_ENV_FILE": str(env_file)},
    )
    assert result.get("isError") is not True, result
    assert len(calls) == 1
    assert _before_command(calls[0], "person") == ["--dotenv", "--env-file", str(env_file)]
    assert "--readonly" not in calls[0]


def test_query_tool_passes_env_file_and_readonly_on_both_calls(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)
    result, calls = _run(
        tmp_path,
        "query",
        {"query": {"from": "persons", "limit": 1}},
        {"XAFFINITY_ENV_FILE": str(env_file)},
    )
    assert result.get("isError") is not True, result
    # The timeout estimate (dry run) and the query itself
    assert len(calls) == 2
    for call in calls:
        assert _before_command(call, "query") == [
            "--dotenv",
            "--env-file",
            str(env_file),
            "--readonly",
        ]


def test_resource_reads_pass_env_file(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)
    fake = tmp_path / "bin" / "xaffinity"
    fake.parent.mkdir()
    fake.write_text(FAKE_CLI)
    fake.chmod(0o755)
    script = f'source "{MCP}/lib/common.sh"; run_xaffinity_readonly whoami --output json >/dev/null'
    subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "MCPBASH_PROJECT_ROOT": str(MCP),
            "XAFFINITY_CLI": str(fake),
            "XAFFINITY_ENV_FILE": str(env_file),
        },
        check=True,
        timeout=30,
    )
    logged = Path(f"{fake}.log").read_text().splitlines()
    assert logged[:5] == ["--dotenv", "--env-file", str(env_file), "--readonly", "whoami"]


def test_launcher_exports_the_absolute_env_file_path() -> None:
    text = (MCP / "xaffinity-mcp.sh").read_text()
    assert 'export XAFFINITY_ENV_FILE="${PWD}/.env"' in text
    for allowlist_file in ("server.d/env.sh", "server.d/server.meta.json"):
        assert "XAFFINITY_ENV_FILE" in (MCP / allowlist_file).read_text()


# --- 38: query cursor stays one argument ------------------------------------------------------


def test_query_cursor_is_one_argument(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("{}")
    cursor = "x --file /tmp/other.json *.json"
    result, calls = _run(
        tmp_path,
        "query",
        {"query": {"from": "persons"}, "cursor": cursor, "timeout": 30},
    )
    assert result.get("isError") is not True, result
    assert len(calls) == 1  # timeout given: no dry-run estimate
    call = calls[0]
    assert f"--cursor={cursor}" in call
    assert call.count("--file") == 1
    assert "a.json" not in call


def test_query_executed_shows_the_command_as_run(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)
    cursor = "abc def"
    result, calls = _run(
        tmp_path,
        "query",
        {"query": {"from": "persons"}, "cursor": cursor, "timeout": 30},
        {"XAFFINITY_ENV_FILE": str(env_file)},
    )
    assert result.get("isError") is not True, result
    executed = result["structuredContent"]["result"]["executed"]
    assert executed[0] == "xaffinity"
    assert executed[1:5] == ["--dotenv", "--env-file", str(env_file), "--readonly"]
    assert f"--cursor={cursor}" in executed
    assert executed[executed.index("--file") + 1] == "<query.json>"
    # Same arguments as the call, apart from the program and the temp file
    assert len(executed) == len(calls[0]) + 1


# --- 41: launcher allowlist from policy.sh ----------------------------------------------------


def _launcher_allowlist(read_only: bool) -> set[str]:
    script = f'source "{MCP}/server.d/policy.sh"; ' + (
        'echo "$AFFINITY_MCP_TOOLS_READONLY $AFFINITY_MCP_TOOLS_SELF_READONLY"'
        if read_only
        else 'echo "$AFFINITY_MCP_TOOLS_ALL"'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def test_launcher_allowlist_comes_from_policy() -> None:
    text = (MCP / "xaffinity-mcp.sh").read_text()
    assert 'source "${SCRIPT_DIR}/server.d/policy.sh"' in text
    assert "get-entity-dossier read-xaffinity-resource" not in text  # no second copy
    for read_only in (True, False):
        assert "get-file-url" in _launcher_allowlist(read_only)
    assert "execute-write-command" in _launcher_allowlist(False)


# --- 42: config setup-key is not exposed ------------------------------------------------------


def test_setup_key_is_not_in_the_registry(tmp_path: Path) -> None:
    registry = json.loads((MCP / ".registry" / "commands.generated.json").read_text())
    names = {c["name"] for c in registry["commands"]}
    assert "config setup-key" not in names
    assert "config check-key" in names
    for cmd in registry["commands"]:
        assert "config setup-key" not in cmd.get("relatedCommands", [])
    result, calls = _run(
        tmp_path, "execute-read-command", {"command": "config setup-key", "argv": ["--force"]}
    )
    assert result.get("isError") is True
    assert calls == []


def test_generator_refuses_setup_key(tmp_path: Path) -> None:
    from tools.generate_mcp_command_registry import generate_registry

    config = tmp_path / "mcp" / ".registry" / "mcp-commands.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"commands": {"config setup-key": {}}}))
    with pytest.raises(SystemExit):
        generate_registry(config, tmp_path / "out.json")
    assert not (tmp_path / "out.json").exists()


# --- 43: aliases count for required and duplicate checks --------------------------------------


def test_alias_satisfies_required_flag(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "execute-read-command",
        {"command": "interaction get", "argv": ["456", "-t", "email"]},
    )
    assert result.get("isError") is not True, result
    assert len(calls) == 1


def test_repeated_alias_of_a_multiple_flag_is_allowed(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "execute-read-command",
        {
            "command": "interaction ls",
            "argv": ["--person-id", "1", "-t", "email", "--type", "meeting"],
        },
    )
    assert result.get("isError") is not True, result
    assert len(calls) == 1


def test_alias_and_primary_of_a_single_flag_are_a_duplicate(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "execute-read-command",
        {"command": "interaction get", "argv": ["456", "-t", "email", "--type", "call"]},
    )
    assert result.get("isError") is True
    assert "multiple times" in json.dumps(result)
    assert calls == []


# --- 48: a dry run neither asks nor uses up a confirmation ------------------------------------


def test_dry_run_keeps_the_confirmation_token(tmp_path: Path) -> None:
    delete = {"command": "company delete", "argv": ["123"]}
    # The server asks first (no dialog): a token is stored
    asked, calls = _run(tmp_path, "execute-write-command", delete)
    assert "confirmation_required" in json.dumps(asked)
    assert calls == []

    dry, calls = _run(
        tmp_path, "execute-write-command", {**delete, "confirm": True, "dryRun": True}
    )
    assert dry.get("isError") is not True, dry
    shown = dry["structuredContent"]["result"]["command"]
    assert dry["structuredContent"]["result"]["dryRun"] is True
    assert "--yes" not in shown
    assert calls == []

    ran, calls = _run(tmp_path, "execute-write-command", {**delete, "confirm": True})
    assert ran.get("isError") is not True, ran
    assert len(calls) == 1
    assert "--yes" in calls[0]


def test_dry_run_of_a_destructive_command_does_not_ask(tmp_path: Path) -> None:
    dry, calls = _run(
        tmp_path,
        "execute-write-command",
        {"command": "company delete", "argv": ["123"], "dryRun": True},
    )
    assert dry.get("isError") is not True, dry
    assert "confirmation_required" not in json.dumps(dry)
    assert calls == []


def test_yes_in_argv_is_refused_on_a_dry_run_too(tmp_path: Path) -> None:
    dry, _ = _run(
        tmp_path,
        "execute-write-command",
        {"command": "company delete", "argv": ["123", "--yes"], "dryRun": True},
    )
    assert dry.get("isError") is True
    assert "--yes flag not allowed" in json.dumps(dry)


# --- 45: cache TTL with GNU and BSD stat ------------------------------------------------------

GNU_STAT = r"""#!/usr/bin/env bash
if [[ "$1" == "-f" ]]; then printf '  File: "%s"\n    ID: 0 Namelen: 255\n' "$3"; exit 0; fi
if [[ "$1" == "-c" ]]; then date +%s; exit 0; fi
exit 1
"""

BSD_STAT = r"""#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then echo "stat: illegal option -- c" >&2; exit 1; fi
if [[ "$1" == "-f" ]]; then date +%s; exit 0; fi
exit 1
"""


@pytest.mark.parametrize("fake_stat", [GNU_STAT, BSD_STAT], ids=["gnu", "bsd"])
def test_cache_hits_with_either_stat(tmp_path: Path, fake_stat: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "stat").write_text(fake_stat)
    (bin_dir / "stat").chmod(0o755)
    cache = tmp_path / "cache"
    cache.mkdir()
    script = (
        f'source "{MCP}/lib/cache.sh"; jq_tool() {{ command jq "$@"; }}; '
        "cache_set k '{\"a\":1}' && cache_get k && "
        "set_me_person_id_cached 12345 && get_me_person_id_cached"
    )
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "AFFINITY_SESSION_CACHE": str(cache),
        },
        check=False,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ['{"a":1}', "12345"]


def test_cache_misses_when_stat_gives_no_number(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "stat").write_text("#!/usr/bin/env bash\necho nonsense\n")
    (bin_dir / "stat").chmod(0o755)
    cache = tmp_path / "cache"
    cache.mkdir()
    out = subprocess.run(
        ["bash", "-c", f'source "{MCP}/lib/cache.sh"; cache_set k 1; cache_get k'],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "AFFINITY_SESSION_CACHE": str(cache)},
        check=False,
        timeout=30,
    )
    assert out.returncode == 1
    assert out.stdout == ""


# --- 49: the Read hook covers the xaffinity config file ---------------------------------------


def _hook(file_path: str) -> int:
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": file_path}})
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True, timeout=30, check=False
    ).returncode


@pytest.mark.parametrize(
    "path",
    [
        "/Users/jane/Library/Application Support/xaffinity/config.toml",
        "/home/jane/.config/xaffinity/config.toml",
        "/home/jane/custom-xdg/xaffinity/config.toml",
        "C:\\Users\\jane\\AppData\\Local\\Affinity\\xaffinity\\config.toml",
        "/Users/jane/Library/Application Support/XAffinity/Config.TOML",
        "/repo/.env",
    ],
)
def test_hook_blocks_key_files(path: str) -> None:
    assert _hook(path) == 2


@pytest.mark.parametrize(
    "path", ["/repo/config.toml", "/repo/pyproject.toml", "/repo/xaffinity/config.toml.bak"]
)
def test_hook_allows_other_files(path: str) -> None:
    assert _hook(path) == 0
