"""Smoke tests for pre-xaffinity.sh hook (Area 1)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

HOOK_PATH = str(
    Path(__file__).parent.parent / "plugins" / "xaffinity-cli" / "hooks" / "pre-xaffinity.sh"
)

# Resolve absolute path to bash so tests that override PATH still work.
_BASH = shutil.which("bash") or "/bin/bash"


def _run_hook(
    command: str,
    env_override: dict | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the hook with a mock tool input."""
    hook_input = json.dumps({"tool_input": {"command": command}})
    env = {**os.environ, **(env_override or {})}
    # Remove AFFINITY_API_KEY from env unless explicitly set in override
    if env_override is None or "AFFINITY_API_KEY" not in env_override:
        env.pop("AFFINITY_API_KEY", None)
    # Set AFFINITY_SESSION_CACHE to skip session cache start in tests
    # (avoids hanging on real xaffinity session start)
    if "AFFINITY_SESSION_CACHE" not in (env_override or {}):
        env["AFFINITY_SESSION_CACHE"] = "/tmp/test-session-cache"
    return subprocess.run(
        [_BASH, HOOK_PATH],
        input=hook_input,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=15,
        check=False,
    )


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_allows_non_xaffinity_commands():
    """Non-xaffinity commands pass through immediately."""
    result = _run_hook("ls -la")
    assert result.returncode == 0


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_allows_help_commands():
    """Help/version commands are always allowed."""
    for cmd in [
        "xaffinity --help",
        "xaffinity --version",
        "xaffinity config check-key",
    ]:
        result = _run_hook(cmd)
        assert result.returncode == 0, f"Failed for: {cmd}"


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_allows_when_env_var_set():
    """Hook exits 0 when AFFINITY_API_KEY env var is set."""
    result = _run_hook(
        "xaffinity --readonly person ls --json",
        env_override={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.returncode == 0


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_allows_via_dotenv_check(tmp_path):
    """Hook exits 0 when xaffinity --dotenv check-key reports configured (tier 2)."""
    # Create mock xaffinity that only succeeds with --dotenv
    mock_bin = tmp_path / "xaffinity"
    mock_bin.write_text(
        textwrap.dedent("""\
        #!/bin/bash
        if [[ "$*" == *"--dotenv"* ]]; then
            echo '{"data":{"configured":true}}'
        else
            echo '{"data":{"configured":false}}'
        fi
    """)
    )
    mock_bin.chmod(mock_bin.stat().st_mode | stat.S_IEXEC)

    jq_dir = str(Path(shutil.which("jq") or "/usr/bin/jq").parent)
    system_dirs = {jq_dir, "/bin", "/usr/bin"}
    minimal_path = os.pathsep.join([str(tmp_path), *sorted(system_dirs)])

    env = {"PATH": minimal_path, "HOME": str(tmp_path)}
    result = _run_hook("xaffinity --readonly person ls --json", env_override=env, cwd=str(tmp_path))
    assert result.returncode == 0


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_allows_via_plain_check(tmp_path):
    """Hook exits 0 when plain check-key (no --dotenv) reports configured (tier 3 / config.toml)."""
    # Create mock xaffinity that only succeeds WITHOUT --dotenv
    mock_bin = tmp_path / "xaffinity"
    mock_bin.write_text(
        textwrap.dedent("""\
        #!/bin/bash
        if [[ "$*" == *"--dotenv"* ]]; then
            echo '{"data":{"configured":false}}'
            exit 1
        else
            echo '{"data":{"configured":true}}'
        fi
    """)
    )
    mock_bin.chmod(mock_bin.stat().st_mode | stat.S_IEXEC)

    jq_dir = str(Path(shutil.which("jq") or "/usr/bin/jq").parent)
    system_dirs = {jq_dir, "/bin", "/usr/bin"}
    minimal_path = os.pathsep.join([str(tmp_path), *sorted(system_dirs)])

    env = {"PATH": minimal_path, "HOME": str(tmp_path)}
    result = _run_hook("xaffinity --readonly person ls --json", env_override=env, cwd=str(tmp_path))
    assert result.returncode == 0


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_blocks_when_no_key(tmp_path):
    """Hook blocks xaffinity commands when no key source is available."""
    # Create a mock xaffinity that always reports unconfigured
    mock_bin = tmp_path / "xaffinity"
    mock_bin.write_text(
        textwrap.dedent("""\
        #!/bin/bash
        echo '{"data":{"configured":false}}'
    """)
    )
    mock_bin.chmod(mock_bin.stat().st_mode | stat.S_IEXEC)

    # Build a minimal PATH: mock dir first (for our fake xaffinity),
    # plus system dirs so bash/jq/cat/echo are still available.
    jq_dir = str(Path(shutil.which("jq") or "/usr/bin/jq").parent)
    system_dirs = {jq_dir, "/bin", "/usr/bin"}
    minimal_path = os.pathsep.join([str(tmp_path), *sorted(system_dirs)])

    env = {"PATH": minimal_path, "HOME": str(tmp_path)}
    result = _run_hook(
        "xaffinity --readonly person ls --json",
        env_override=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 2


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_denies_when_install_failed(tmp_path):
    """Hook denies xaffinity commands when install-status marker says INSTALL_FAILED."""
    # Create INSTALL_FAILED marker
    marker = tmp_path / ".xaffinity-install-status"
    marker.write_text("INSTALL_FAILED")

    # Create a mock xaffinity so command -v succeeds (wrapper would be on PATH)
    mock_bin = tmp_path / "xaffinity"
    mock_bin.write_text("#!/bin/bash\nexit 1\n")
    mock_bin.chmod(mock_bin.stat().st_mode | stat.S_IEXEC)

    jq_dir = str(Path(shutil.which("jq") or "/usr/bin/jq").parent)
    system_dirs = {jq_dir, "/bin", "/usr/bin"}
    minimal_path = os.pathsep.join([str(tmp_path), *sorted(system_dirs)])

    env = {"PATH": minimal_path, "HOME": str(tmp_path)}
    result = _run_hook(
        "xaffinity --readonly person ls --json",
        env_override=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 2
    assert "installation failed" in result.stderr.lower()


@pytest.mark.req("CLI-PRETOOL-HOOK")
def test_hook_allows_when_install_succeeded(tmp_path):
    """Hook proceeds normally when install-status marker says INSTALLED."""
    # Create INSTALLED marker
    marker = tmp_path / ".xaffinity-install-status"
    marker.write_text("INSTALLED")

    # Create mock xaffinity that reports key configured and handles session start
    mock_bin = tmp_path / "xaffinity"
    mock_bin.write_text(
        textwrap.dedent("""\
        #!/bin/bash
        if [[ "$*" == *"session start"* ]]; then
            echo "/tmp/fake-cache"
        else
            echo '{"data":{"configured":true}}'
        fi
    """)
    )
    mock_bin.chmod(mock_bin.stat().st_mode | stat.S_IEXEC)

    jq_dir = str(Path(shutil.which("jq") or "/usr/bin/jq").parent)
    system_dirs = {jq_dir, "/bin", "/usr/bin"}
    minimal_path = os.pathsep.join([str(tmp_path), *sorted(system_dirs)])

    # Don't set AFFINITY_SESSION_CACHE — let the hook trigger session start
    env = {"PATH": minimal_path, "HOME": str(tmp_path), "AFFINITY_SESSION_CACHE": ""}
    result = _run_hook(
        "xaffinity --readonly person ls --json",
        env_override=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
