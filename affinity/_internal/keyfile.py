"""
Shared helpers for reading API keys from files and external commands.

These implement two well-known 12-factor / secret-manager conventions:

- File-path convention (Docker secrets, k8s mounted Secrets, Hashicorp Vault agent,
  Stripe CLI, and others): the caller sets an env var to the path of a file
  containing the secret, and the SDK reads the file at startup.

- Command convention (Git credential helpers, gpg --passphrase-cmd, pass, 1Password
  CLI, HashiCorp Vault, and others): the caller sets an env var to a shell command
  whose stdout is the secret, and the SDK runs that command at startup.

Both helpers are used by:
  - :func:`affinity.client._api_key_from_env` (SDK ``from_env()`` path)
  - :meth:`affinity.cli.context.CLIContext.resolve_api_key` (CLI path)
"""

from __future__ import annotations

import os
import stat
import subprocess
import warnings
from pathlib import Path


def _posix_permission_warnings(path: Path) -> list[str]:
    """
    Return a list of human-readable warnings if *path* has group- or world-readable
    permissions.  Returns an empty list on non-Posix systems or if the file does
    not exist.

    This mirrors the policy in :func:`affinity.cli.config.config_file_permission_warnings`
    but is inlined here to avoid importing CLI-specific modules (which pull in
    click, rich, tomllib, etc.) from the generic SDK code path.
    """
    if os.name != "posix":
        return []
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return []
    insecure = bool(mode & (stat.S_IRGRP | stat.S_IROTH))
    if insecure:
        return [
            f"Config file is group/world readable: {path} "
            "(consider `chmod 600` to protect secrets)."
        ]
    return []


def read_key_file(path: Path) -> str:
    """
    Read an API key from *path*.

    Raises:
        ValueError: if the path does not exist, or if the file exists but is
            empty after stripping whitespace.
        UserWarning: (Posix only) if the file's permissions are group- or
            world-readable.  The warning is emitted via :func:`warnings.warn`
            and the value is still returned.
    """
    if not path.is_file():
        raise ValueError(f"AFFINITY_API_KEY_FILE points to non-existent file: {path}")

    for warning_text in _posix_permission_warnings(path):
        warnings.warn(warning_text, UserWarning, stacklevel=3)

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Empty API key file: {path}")

    return content


def read_key_command(cmd: str) -> str:
    """
    Run *cmd* via a shell and return its stdout as the API key.

    ``shell=True`` is intentional and matches established conventions in
    ``git credential.helper``, ``gpg --passphrase-program``, ``pass``, and
    ``op`` (1Password CLI).  The trust boundary is "the user set this env var"
    — the same boundary as any user-controlled executable path.

    The default timeout is 30 seconds, overridable via the
    ``AFFINITY_API_KEY_COMMAND_TIMEOUT`` environment variable.

    Raises:
        ValueError: if the command times out, exits with a non-zero status,
            or produces empty stdout.  Stderr in non-zero-exit errors is
            capped at 500 characters to limit blast radius if the helper
            accidentally echoes the secret on failure.
    """
    timeout_str = os.environ.get("AFFINITY_API_KEY_COMMAND_TIMEOUT", "30")
    try:
        timeout = float(timeout_str)
    except ValueError:
        timeout = 30.0

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"AFFINITY_API_KEY_COMMAND timed out after {timeout}s: {cmd}") from exc

    if result.returncode != 0:
        stderr_snippet = result.stderr.strip()[:500]
        raise ValueError(
            f"AFFINITY_API_KEY_COMMAND failed (exit {result.returncode}): {stderr_snippet}"
        )

    content = result.stdout.strip()
    if not content:
        raise ValueError("AFFINITY_API_KEY_COMMAND produced empty stdout")

    return content
