"""AFFINITY_API_KEY_FILE / --api-key-file read .env files and named pipes (FIFOs), e.g. a
1Password Environments locally mounted .env (a FIFO that 1Password fills on demand)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from affinity._internal.keyfile import read_key_file
from affinity.cli.commands.config_cmds import (
    _probe_key_default_api_version as real_probe_key_default_api_version,
)

posix_only = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_raw_key_file(tmp_path: Path) -> None:
    assert read_key_file(_write(tmp_path, "abc123\n")) == "abc123"


@pytest.mark.parametrize(
    "text",
    [
        "AFFINITY_API_KEY=abc123\n",
        "OTHER=1\nAFFINITY_API_KEY = abc123\n",
        "# comment\nexport AFFINITY_API_KEY='abc123'\n",
        'AFFINITY_API_KEY="abc123" # set by 1Password\n',
    ],
)
def test_dotenv_format(tmp_path: Path, text: str) -> None:
    assert read_key_file(_write(tmp_path, text)) == "abc123"


def test_dotenv_without_the_key_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="AFFINITY_API_KEY"):
        read_key_file(_write(tmp_path, "OTHER=1\nMORE=2\n"))


def _fifo(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    os.mkfifo(path, 0o600)
    return path


@posix_only
def test_fifo_with_a_late_writer(tmp_path: Path) -> None:
    path = _fifo(tmp_path)

    def writer() -> None:
        time.sleep(0.3)  # 1Password asks for approval before writing
        with path.open("w") as f:
            f.write("AFFINITY_API_KEY=from-1password\n")

    threading.Thread(target=writer, daemon=True).start()
    assert read_key_file(path, fifo_timeout=5) == "from-1password"


@posix_only
def test_fifo_without_a_writer_times_out(tmp_path: Path) -> None:
    path = _fifo(tmp_path)
    start = time.monotonic()
    with pytest.raises(ValueError, match="1Password"):
        read_key_file(path, fifo_timeout=0.5)
    assert time.monotonic() - start < 3


@posix_only
def test_sdk_from_env_reads_a_fifo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from affinity.client import _api_key_from_env

    path = _fifo(tmp_path)

    def writer() -> None:
        with path.open("w") as f:
            f.write("AFFINITY_API_KEY=via-fifo\n")

    threading.Thread(target=writer, daemon=True).start()
    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(path))
    key = _api_key_from_env(
        env_var="AFFINITY_API_KEY", load_dotenv=False, dotenv_path=None, dotenv_override=False
    )
    assert key == "via-fifo"


def test_cli_api_key_file_flag_reads_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from affinity.cli.context import CLIContext

    path = _write(tmp_path, "AFFINITY_API_KEY=flag-key\n")
    for var in ("AFFINITY_API_KEY", "AFFINITY_API_KEY_FILE", "AFFINITY_API_KEY_COMMAND"):
        monkeypatch.delenv(var, raising=False)
    ctx = CLIContext.__new__(CLIContext)
    ctx.api_key_file = str(path)
    ctx.api_key_stdin = False
    assert CLIContext.resolve_api_key(ctx, warnings=[]) == "flag-key"


@posix_only
def test_check_key_does_not_read_a_fifo_for_its_version_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version probe needs the key; reading a 1Password pipe would prompt the user."""
    import json

    from click.testing import CliRunner

    from affinity.cli.commands import config_cmds
    from affinity.cli.main import cli

    monkeypatch.setattr(  # conftest stubs the probe; this test is about the real one
        config_cmds, "_probe_key_default_api_version", real_probe_key_default_api_version
    )
    path = _fifo(tmp_path)  # no writer: reading it would wait for the timeout
    for var in ("AFFINITY_API_KEY", "AFFINITY_API_KEY_COMMAND"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY_FILE", str(path))
    start = time.monotonic()
    result = CliRunner().invoke(cli, ["--json", "config", "check-key"])
    assert time.monotonic() - start < 5
    data = json.loads(result.stdout.strip().splitlines()[-1])["data"]
    assert data["configured"] is True and data["keyDefaultApiVersion"] is None
