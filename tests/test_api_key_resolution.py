"""
Tests for the API key resolver chain added in PR-2.

Covers AFFINITY_API_KEY_FILE and AFFINITY_API_KEY_COMMAND resolution
in both the SDK (_api_key_from_env / Affinity.from_env) and CLI
(CLIContext.resolve_api_key) paths.

All tests are tagged @pytest.mark.req("REQ-AUTH-RESOLVE-NNN") per .cursorrules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_key_file(tmp_path: Path, content: str | bytes, name: str = "api_key.txt") -> Path:
    """Write *content* to tmp_path/name with mode 0o600 and return the path.

    Use this for every key-file fixture that isn't explicitly testing the
    permissive-mode warning (REQ-AUTH-RESOLVE-004); those tests should chmod
    to 0o644 themselves.
    """
    key_file = tmp_path / name
    if isinstance(content, bytes):
        key_file.write_bytes(content)
    else:
        key_file.write_text(content)
    if os.name == "posix":
        key_file.chmod(0o600)
    return key_file


def _make_cli_context(**overrides: Any):
    """Build a minimal CLIContext for resolver tests."""
    pytest.importorskip("rich_click")
    pytest.importorskip("platformdirs")

    from affinity.cli.context import CLIContext

    defaults: dict[str, Any] = {
        "output": "json",
        "quiet": False,
        "verbosity": 0,
        "pager": None,
        "progress": "never",
        "profile": None,
        "dotenv": False,
        "env_file": None,
        "api_key_file": None,
        "api_key_stdin": False,
        "timeout": 30.0,
        "max_retries": 0,
        "readonly": True,
        "trace": False,
        "log_file": None,
        "enable_log_file": False,
        "enable_beta_endpoints": False,
    }
    defaults.update(overrides)
    return CLIContext(**defaults)


def _resolve_cli_key(ctx) -> str:
    """Call resolve_api_key and return the key (raising CLIError on failure)."""
    w: list[str] = []
    return ctx.resolve_api_key(warnings=w)


# ---------------------------------------------------------------------------
# AFFINITY_API_KEY_FILE tests — SDK path
# ---------------------------------------------------------------------------


@pytest.mark.req("REQ-AUTH-RESOLVE-001")
def test_file_happy_path_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AFFINITY_API_KEY_FILE: SDK resolves key from a valid file."""
    key_file = _make_key_file(tmp_path, "my-secret-sdk-key")
    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_COMMAND", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(key_file))

    from affinity._internal.keyfile import read_key_file

    assert read_key_file(key_file) == "my-secret-sdk-key"


@pytest.mark.req("REQ-AUTH-RESOLVE-002")
def test_file_nonexistent_sdk(tmp_path: Path) -> None:
    """AFFINITY_API_KEY_FILE: non-existent file raises ValueError."""
    from affinity._internal.keyfile import read_key_file

    missing = tmp_path / "does_not_exist.txt"
    with pytest.raises(ValueError, match="non-existent file"):
        read_key_file(missing)


@pytest.mark.req("REQ-AUTH-RESOLVE-003")
def test_file_empty_sdk(tmp_path: Path) -> None:
    """AFFINITY_API_KEY_FILE: empty file raises ValueError."""
    from affinity._internal.keyfile import read_key_file

    key_file = _make_key_file(tmp_path, "", name="empty.txt")
    with pytest.raises(ValueError, match="Empty API key file"):
        read_key_file(key_file)


@pytest.mark.req("REQ-AUTH-RESOLVE-004")
@pytest.mark.skipif(os.name != "posix", reason="Permission check is Posix-only")
def test_file_permissive_mode_warns_sdk(tmp_path: Path) -> None:
    """AFFINITY_API_KEY_FILE: mode 0644 emits UserWarning but still returns the key."""
    from affinity._internal.keyfile import read_key_file

    key_file = tmp_path / "api_key.txt"
    key_file.write_text("my-secret-key")
    key_file.chmod(0o644)  # group+other readable

    with pytest.warns(UserWarning, match="group/world readable"):
        result = read_key_file(key_file)

    assert result == "my-secret-key"


@pytest.mark.req("REQ-AUTH-RESOLVE-005")
@pytest.mark.skipif(os.name == "posix", reason="Tests non-Posix behaviour — skip on Posix")
def test_file_permission_check_noop_on_windows(tmp_path: Path) -> None:
    """AFFINITY_API_KEY_FILE: permission check is a no-op on non-Posix platforms."""
    from affinity._internal.keyfile import _posix_permission_warnings

    key_file = tmp_path / "api_key.txt"
    key_file.write_text("my-secret-key")
    # On Windows os.name != "posix", so the helper should return an empty list.
    assert _posix_permission_warnings(key_file) == []


@pytest.mark.req("REQ-AUTH-RESOLVE-006")
def test_file_strips_whitespace(tmp_path: Path) -> None:
    """AFFINITY_API_KEY_FILE: trailing whitespace and CRLF are stripped."""
    from affinity._internal.keyfile import read_key_file

    key_file = _make_key_file(tmp_path, b"my-secret-key\r\n  ")
    assert read_key_file(key_file) == "my-secret-key"


# ---------------------------------------------------------------------------
# AFFINITY_API_KEY_COMMAND tests — SDK path
# ---------------------------------------------------------------------------


@pytest.mark.req("REQ-AUTH-RESOLVE-007")
def test_command_happy_path_sdk() -> None:
    """AFFINITY_API_KEY_COMMAND: simple echo command returns key."""
    from affinity._internal.keyfile import read_key_command

    result = read_key_command("echo test-key")
    assert result == "test-key"


@pytest.mark.req("REQ-AUTH-RESOLVE-008")
def test_command_nonzero_exit_sdk() -> None:
    """AFFINITY_API_KEY_COMMAND: non-zero exit raises ValueError with exit code and stderr."""
    from affinity._internal.keyfile import read_key_command

    # A command that writes to stderr and exits 1
    cmd = "sh -c 'echo error-details >&2; exit 1'"
    with pytest.raises(ValueError, match="exit 1") as exc_info:
        read_key_command(cmd)
    assert "error-details" in str(exc_info.value)


@pytest.mark.req("REQ-AUTH-RESOLVE-009")
def test_command_empty_stdout_sdk() -> None:
    """AFFINITY_API_KEY_COMMAND: command producing empty stdout raises ValueError."""
    from affinity._internal.keyfile import read_key_command

    with pytest.raises(ValueError, match="empty stdout"):
        read_key_command("true")  # exits 0 but produces no output


@pytest.mark.req("REQ-AUTH-RESOLVE-010")
def test_command_timeout_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """AFFINITY_API_KEY_COMMAND: timeout raises ValueError mentioning the timeout duration."""
    from affinity._internal.keyfile import read_key_command

    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND_TIMEOUT", "1")
    with pytest.raises(ValueError, match="timed out after 1"):
        read_key_command("sleep 5")


# ---------------------------------------------------------------------------
# Precedence tests — SDK path (via _api_key_from_env)
# ---------------------------------------------------------------------------


@pytest.mark.req("REQ-AUTH-RESOLVE-011")
def test_direct_env_wins_over_file_and_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AFFINITY_API_KEY set wins over _FILE and _COMMAND (don't open or run either)."""
    # Write a file that, if opened, would return a different key
    key_file = _make_key_file(tmp_path, "wrong-file-key", name="should_not_read.txt")

    monkeypatch.setenv("AFFINITY_API_KEY", "direct-env-key")
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "echo wrong-cmd-key")

    from affinity.client import _api_key_from_env

    result = _api_key_from_env(
        env_var="AFFINITY_API_KEY",
        load_dotenv=False,
        dotenv_path=None,
        dotenv_override=False,
    )
    assert result == "direct-env-key"


@pytest.mark.req("REQ-AUTH-RESOLVE-012")
def test_file_wins_over_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AFFINITY_API_KEY_FILE set wins over _COMMAND (don't run the command)."""
    key_file = _make_key_file(tmp_path, "file-key", name="key.txt")

    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "echo wrong-cmd-key")

    from affinity.client import _api_key_from_env

    result = _api_key_from_env(
        env_var="AFFINITY_API_KEY",
        load_dotenv=False,
        dotenv_path=None,
        dotenv_override=False,
    )
    assert result == "file-key"


@pytest.mark.req("REQ-AUTH-RESOLVE-013")
def test_empty_string_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty string is treated as unset for AFFINITY_API_KEY, _FILE, and _COMMAND."""
    # Set all three to empty — resolver should fall through all and raise ValueError
    monkeypatch.setenv("AFFINITY_API_KEY", "")
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", "")
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "")

    from affinity.client import _api_key_from_env

    with pytest.raises(ValueError, match="Missing API key"):
        _api_key_from_env(
            env_var="AFFINITY_API_KEY",
            load_dotenv=False,
            dotenv_path=None,
            dotenv_override=False,
        )


@pytest.mark.req("REQ-AUTH-RESOLVE-014")
def test_explicit_constructor_arg_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Affinity(api_key='x') wins over all env paths.

    Verified two ways:
      1. Env values are poisoned (file does not exist; command exits non-zero) —
         if the resolver were invoked, those would raise and fail the test.
      2. The resolved key on the underlying HTTPClient.config is asserted to
         equal the explicit value (proves "explicit-key" actually flowed
         through to the auth layer, not "env-key" or anything else).
    """
    monkeypatch.setenv("AFFINITY_API_KEY", "env-key")
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", "/nonexistent/path")
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "false")  # exit 1, empty stdout

    from affinity import Affinity

    client = Affinity(api_key="explicit-key", max_retries=0)
    try:
        assert client._http._config.api_key == "explicit-key"
    finally:
        client.close()


@pytest.mark.req("REQ-AUTH-RESOLVE-015")
def test_file_leading_trailing_newline_stripped(tmp_path: Path) -> None:
    """AFFINITY_API_KEY_FILE: leading/trailing newline is stripped to actual key."""
    from affinity._internal.keyfile import read_key_file

    key_file = _make_key_file(tmp_path, b"\nmy-real-key\n", name="key.txt")
    assert read_key_file(key_file) == "my-real-key"


# ---------------------------------------------------------------------------
# Async parity tests
# ---------------------------------------------------------------------------


@pytest.mark.req("REQ-AUTH-RESOLVE-016")
@pytest.mark.asyncio
async def test_async_from_env_honors_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AsyncAffinity.from_env() reads AFFINITY_API_KEY_FILE (parity with sync).

    Asserts the actual resolved key on the underlying AsyncHTTPClient.config,
    not just that the client was constructed.
    """
    key_file = _make_key_file(tmp_path, "async-file-key", name="key.txt")

    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_COMMAND", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(key_file))

    from affinity import AsyncAffinity

    async with AsyncAffinity.from_env(max_retries=0) as client:
        assert client._http._config.api_key == "async-file-key"


@pytest.mark.req("REQ-AUTH-RESOLVE-017")
@pytest.mark.asyncio
async def test_async_from_env_honors_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """AsyncAffinity.from_env() reads AFFINITY_API_KEY_COMMAND (parity with sync).

    Asserts the actual resolved key on the underlying AsyncHTTPClient.config.
    """
    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_FILE", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "echo async-cmd-key")

    from affinity import AsyncAffinity

    async with AsyncAffinity.from_env(max_retries=0) as client:
        assert client._http._config.api_key == "async-cmd-key"


# ---------------------------------------------------------------------------
# CLI path tests
# ---------------------------------------------------------------------------


@pytest.mark.req("REQ-AUTH-RESOLVE-018")
def test_cli_honors_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI AppContext.resolve_api_key() reads AFFINITY_API_KEY_FILE."""
    key_file = _make_key_file(tmp_path, "cli-file-key", name="key.txt")

    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_COMMAND", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(key_file))

    ctx = _make_cli_context()
    assert _resolve_cli_key(ctx) == "cli-file-key"


@pytest.mark.req("REQ-AUTH-RESOLVE-019")
def test_cli_honors_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI AppContext.resolve_api_key() reads AFFINITY_API_KEY_COMMAND."""
    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_FILE", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "echo cli-cmd-key")

    ctx = _make_cli_context()
    assert _resolve_cli_key(ctx) == "cli-cmd-key"


# ---------------------------------------------------------------------------
# `xaffinity config check-key` parity tests — verifies the auxiliary key
# detector (_find_existing_key) sees the new env vars too. Without these,
# check-key reports "configured: false" for users who configured via _FILE
# or _COMMAND, breaking the xaffinity-cli-usage skill's verification flow.
# ---------------------------------------------------------------------------


def _find_existing_key_with_isolated_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bool, str | None]:
    """Call _find_existing_key with paths pointed at empty tmp dirs so a
    real ~/.config/xaffinity profile or a project-local .env can't shadow
    the test.

    Strategy: monkeypatch HOME / XDG_CONFIG_HOME *before* CLIContext is
    constructed, so its frozen ``_paths`` field (built via ``get_paths()``)
    points at an empty tmp directory rather than the user's real
    ``~/.config/xaffinity``.
    """
    pytest.importorskip("rich_click")
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    from affinity.cli.commands.config_cmds import _find_existing_key

    # env_file points at a non-existent path so the .env branch skips
    ctx = _make_cli_context(env_file=tmp_path / ".env-does-not-exist")
    return _find_existing_key(ctx)


@pytest.mark.req("REQ-AUTH-RESOLVE-022")
def test_check_key_detects_file_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`xaffinity config check-key` reports configured=True with source='file'
    when AFFINITY_API_KEY_FILE points at a valid key file."""
    key_file = _make_key_file(tmp_path, "from-file")

    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_COMMAND", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(key_file))

    found, source = _find_existing_key_with_isolated_paths(tmp_path, monkeypatch)
    assert found is True
    assert source == "file"


@pytest.mark.req("REQ-AUTH-RESOLVE-023")
def test_check_key_detects_command_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`xaffinity config check-key` reports configured=True with source='command'
    when AFFINITY_API_KEY_COMMAND is set, without actually running the command."""
    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_FILE", raising=False)
    # Use a command that would *fail* if check-key tried to run it; we expect
    # check-key to detect the env var without executing the helper (so the
    # failing command is harmless).
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "exit 99")

    found, source = _find_existing_key_with_isolated_paths(tmp_path, monkeypatch)
    assert found is True
    assert source == "command"


@pytest.mark.req("REQ-AUTH-RESOLVE-024")
def test_check_key_handles_missing_file_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check-key reports configured=False when AFFINITY_API_KEY_FILE points at
    a non-existent path (don't crash, fall through to next source)."""
    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_COMMAND", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(tmp_path / "nonexistent"))

    found, source = _find_existing_key_with_isolated_paths(tmp_path, monkeypatch)
    assert found is False
    assert source is None


@pytest.mark.req("REQ-AUTH-RESOLVE-025")
def test_check_key_precedence_matches_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both AFFINITY_API_KEY_FILE and AFFINITY_API_KEY_COMMAND are set,
    check-key reports source='file' to match resolve_api_key's chain
    (file at step 3, command at step 4)."""
    key_file = _make_key_file(tmp_path, "from-file")

    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "echo from-cmd")

    found, source = _find_existing_key_with_isolated_paths(tmp_path, monkeypatch)
    assert found is True
    assert source == "file"


@pytest.mark.req("REQ-AUTH-RESOLVE-021")
def test_cli_env_var_wins_over_api_key_file_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the CLI, AFFINITY_API_KEY_FILE env var (step 3) wins over --api-key-file
    flag (step 5).

    This is intentional precedence per the resolver chain: env vars (steps 2-4)
    are checked before CLI flags (steps 5-6). This is unusual — most CLI tools
    have explicit flags win over env — but it preserves the pre-existing
    xaffinity convention where env-set credentials win over flags. Pin this
    behavior so a future refactor doesn't silently invert it.
    """
    env_file = _make_key_file(tmp_path, "key-from-env-var", name="env_var.txt")
    flag_file = _make_key_file(tmp_path, "key-from-cli-flag", name="flag_file.txt")

    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.delenv("AFFINITY_API_KEY_COMMAND", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(env_file))

    ctx = _make_cli_context(api_key_file=str(flag_file))
    assert _resolve_cli_key(ctx) == "key-from-env-var"


@pytest.mark.req("REQ-AUTH-RESOLVE-020")
def test_command_stderr_capped_at_500_chars() -> None:
    """AFFINITY_API_KEY_COMMAND: stderr in error message is capped at 500 chars."""
    from affinity._internal.keyfile import read_key_command

    # Generate 2000 bytes of stderr, exit 1
    # Use printf repeated — portable across sh implementations
    cmd = r"sh -c 'python3 -c \"print(chr(120)*2000, end='')\" >&2; exit 1'"
    # Simpler portable approach:
    cmd = "sh -c 'printf \"%2000s\" x >&2; exit 1'"

    with pytest.raises(ValueError) as exc_info:
        read_key_command(cmd)

    err_msg = str(exc_info.value)
    # The message itself must be bounded.  The cap is 500 chars of stderr snippet;
    # the full error message has a prefix too, so total length will be > 500 but
    # the snippet portion must not exceed 500.
    assert "exit 1" in err_msg
    # Verify that the message length is bounded (prefix + up to 500 chars of stderr)
    # The message format is: "AFFINITY_API_KEY_COMMAND failed (exit 1): <stderr[:500]>"
    # So total is at most ~550 chars. Use 600 as a generous upper bound.
    assert len(err_msg) <= 600, f"Error message too long: {len(err_msg)} chars"
