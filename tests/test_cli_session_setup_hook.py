"""Tests for session-setup.sh hook (lightweight session bootstrap)."""

from __future__ import annotations

import os
import re
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


@pytest.mark.req("CLI-SESSION-SETUP")
def test_noop_on_cowork_host(tmp_path: Path):
    """Local Cowork runs hooks on the host: no wrapper, no PATH line, nothing installed there."""
    _make_mock_python3(tmp_path)
    env_file = tmp_path / "claude_env"
    env_file.touch()
    env = {
        **_env_without_xaffinity(tmp_path),
        "CLAUDE_ENV_FILE": str(env_file),
        "CLAUDE_CODE_IS_COWORK": "1",
        "CLAUDE_CODE_ENTRYPOINT": "local-agent",
        "_XAFFINITY_COWORK_SESSIONS_DIR": str(tmp_path / "no-such-dir"),
    }
    result = _run_hook(env_override=env)

    assert result.returncode == 0
    assert not (tmp_path / "bin" / "xaffinity").exists()
    assert env_file.read_text() == ""


@pytest.mark.req("CLI-SESSION-SETUP")
def test_creates_wrapper_inside_cowork_vm(tmp_path: Path):
    """Hooks inside the Cowork sandbox (/sessions exists) still bootstrap the CLI."""
    _make_mock_python3(tmp_path)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    env = {
        **_env_without_xaffinity(tmp_path),
        "CLAUDE_CODE_IS_COWORK": "1",
        "CLAUDE_CODE_ENTRYPOINT": "local-agent",
        "_XAFFINITY_COWORK_SESSIONS_DIR": str(sessions),
    }
    result = _run_hook(env_override=env)

    assert result.returncode == 0
    assert (tmp_path / "bin" / "xaffinity").exists()


_REPO = Path(__file__).parent.parent
_PINNED_FILES = [
    "plugins/xaffinity-cli/hooks/session-setup.sh",
    "plugins/xaffinity-cli/hooks/pre-xaffinity.sh",
    "plugins/xaffinity-cli/skills/xaffinity-cli-usage/SKILL.md",
    "plugins/xaffinity-cli/skills/xaffinity-cli-usage/references/setup.md",
]


def _package_version() -> str:
    match = re.search(r'^version = "([^"]+)"', (_REPO / "pyproject.toml").read_text(), re.M)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def test_cli_install_pin_matches_package_version():
    """Every install line in the CLI plugin pins the CLI version from the same commit."""
    expected = f"affinity-sdk[cli]=={_package_version()}"
    for rel in _PINNED_FILES:
        text = (_REPO / rel).read_text()
        specs = re.findall(r"affinity-sdk\[cli\]==[0-9][0-9A-Za-z.\-]*", text)
        assert specs, f"{rel} has no pinned install spec"
        assert set(specs) == {expected}, f"{rel} pins {sorted(set(specs))}, expected {expected}"


def _run_wrapper_with_fake_pip(tmp_path: Path, extra_env: dict[str, str]) -> str:
    """Create the wrapper, run it against a fake pip, and return the args pip received."""
    _make_mock_python3(tmp_path)
    env = _env_without_xaffinity(tmp_path)
    assert _run_hook(env_override=env).returncode == 0

    log = tmp_path / "pip-args"
    fake_pip = tmp_path / "pip"  # tmp_path is first on the restricted PATH
    fake_pip.write_text(f'#!/bin/sh\necho "$@" > "{log}"\nexit 1\n')
    fake_pip.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    subprocess.run(
        [_BASH, str(tmp_path / "bin" / "xaffinity"), "--version"],
        capture_output=True,
        text=True,
        env={**os.environ, **env, "HOME": str(home), **extra_env},
        timeout=15,
        check=False,
    )
    return log.read_text().strip()


def test_wrapper_installs_pinned_version(tmp_path: Path):
    args = _run_wrapper_with_fake_pip(tmp_path, {})
    assert args == f"install --user affinity-sdk[cli]=={_package_version()}"


def test_wrapper_install_spec_can_be_overridden(tmp_path: Path):
    args = _run_wrapper_with_fake_pip(tmp_path, {"XAFFINITY_CLI_SPEC": "affinity-sdk[cli]"})
    assert args == "install --user affinity-sdk[cli]"
