"""Tests for session-setup.sh hook (lightweight session bootstrap)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

HOOK_PATH = str(
    Path(__file__).parent.parent / "plugins" / "xaffinity-cli" / "hooks" / "session-setup.sh"
)

# Resolve absolute path to bash so tests that override PATH still work.
_BASH = shutil.which("bash") or "/bin/bash"


def _run_hook(env_override: dict | None = None) -> subprocess.CompletedProcess:
    """Run the session-setup.sh hook with optional env overrides."""
    env = {**os.environ, **(env_override or {})}
    return subprocess.run(
        [_BASH, HOOK_PATH],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )


def _make_mock_python3(tmp_path: Path) -> Path:
    """Create a mock python3 that outputs ``tmp_path/bin`` for any invocation.

    session-setup.sh runs::

        python3 -c "import site, os; print(os.path.join(site.getuserbase(), 'bin'))"

    The mock simply echoes ``<tmp_path>/bin`` regardless of arguments.
    """
    mock_python = tmp_path / "python3"
    mock_python.write_text(f'#!/bin/bash\necho "{tmp_path}/bin"\n')
    mock_python.chmod(mock_python.stat().st_mode | stat.S_IEXEC)
    return mock_python


def _env_without_xaffinity(tmp_path: Path) -> dict:
    """Return an env dict where ``command -v xaffinity`` will fail.

    PATH is restricted to ``tmp_path`` (which contains our mock python3)
    plus ``/bin`` and ``/usr/bin`` for core utilities.  No real xaffinity
    should be reachable through these directories.
    """
    return {
        "PATH": os.pathsep.join([str(tmp_path), "/bin", "/usr/bin"]),
        "HOME": str(tmp_path),
    }


@pytest.mark.req("CLI-SESSION-SETUP")
def test_noop_when_xaffinity_on_path(tmp_path: Path):
    """When xaffinity is already on PATH, hook exits 0 and creates no wrapper."""
    # Create a mock xaffinity so ``command -v xaffinity`` succeeds.
    mock_xaffinity = tmp_path / "xaffinity"
    mock_xaffinity.write_text("#!/bin/bash\nexit 0\n")
    mock_xaffinity.chmod(mock_xaffinity.stat().st_mode | stat.S_IEXEC)

    # We still need python3 on PATH for the user_bin derivation.
    _make_mock_python3(tmp_path)

    user_bin = tmp_path / "bin"

    env = {
        "PATH": os.pathsep.join([str(tmp_path), "/bin", "/usr/bin"]),
        "HOME": str(tmp_path),
    }
    result = _run_hook(env_override=env)

    assert result.returncode == 0
    # The wrapper at user_bin/xaffinity should NOT have been created
    # (there may be the user_bin directory itself, which is fine).
    wrapper = user_bin / "xaffinity"
    assert not wrapper.exists(), (
        f"Wrapper should not be created when xaffinity is already on PATH, but found: {wrapper}"
    )


@pytest.mark.req("CLI-SESSION-SETUP")
def test_creates_wrapper_when_xaffinity_missing(tmp_path: Path):
    """When xaffinity is not on PATH, hook creates an executable wrapper script."""
    _make_mock_python3(tmp_path)
    env = _env_without_xaffinity(tmp_path)

    result = _run_hook(env_override=env)

    assert result.returncode == 0

    wrapper = tmp_path / "bin" / "xaffinity"
    assert wrapper.exists(), "Wrapper should be created when xaffinity is not on PATH"
    assert wrapper.stat().st_mode & stat.S_IEXEC, "Wrapper should be executable"

    wrapper_content = wrapper.read_text()
    assert "_XAFFINITY_BOOTSTRAP" in wrapper_content, (
        "Wrapper should contain the _XAFFINITY_BOOTSTRAP recursion guard"
    )
    assert "pip install" in wrapper_content, (
        "Wrapper should contain 'pip install' for deferred installation"
    )


@pytest.mark.req("CLI-SESSION-SETUP")
def test_clears_stale_marker_on_wrapper_creation(tmp_path: Path):
    """Stale install marker is removed when the hook creates a wrapper."""
    _make_mock_python3(tmp_path)
    env = _env_without_xaffinity(tmp_path)

    # Pre-create a stale marker file as if a previous install had failed.
    marker = tmp_path / ".xaffinity-install-status"
    marker.write_text("INSTALL_FAILED")
    assert marker.exists()

    result = _run_hook(env_override=env)

    assert result.returncode == 0
    assert not marker.exists(), (
        "Stale .xaffinity-install-status marker should be removed after hook run"
    )


@pytest.mark.req("CLI-SESSION-SETUP")
def test_writes_claude_env_file(tmp_path: Path):
    """CLAUDE_ENV_FILE receives a PATH export when set."""
    _make_mock_python3(tmp_path)

    env_file = tmp_path / "claude_env"
    env_file.touch()  # Create an empty file

    env = {
        **_env_without_xaffinity(tmp_path),
        "CLAUDE_ENV_FILE": str(env_file),
    }
    result = _run_hook(env_override=env)

    assert result.returncode == 0

    contents = env_file.read_text()
    assert "export PATH=" in contents, (
        f"CLAUDE_ENV_FILE should contain a PATH export, got: {contents!r}"
    )
    # Verify it includes the user_bin directory
    assert str(tmp_path / "bin") in contents, (
        f"CLAUDE_ENV_FILE PATH export should include user_bin directory, got: {contents!r}"
    )
