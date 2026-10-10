#!/usr/bin/env python3
"""Sync MCP CLI commands registry with CLI introspection.

This pre-commit hook regenerates mcp/.registry/commands.json whenever
CLI command files or pyproject.toml change. This ensures the registry
stays in sync with the actual CLI behavior.

The hook:
1. Regenerates the registry from CLI introspection (the generator runs the CLI from this
   repo's source, so no reinstall is needed after a version bump)
2. Returns exit code 1 if files were modified (pre-commit re-stages)
3. Returns exit code 0 if no changes needed

Unlike version-based sync, this approach catches ALL CLI changes
(new options, changed defaults, etc.) not just version bumps.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def regenerate_registry() -> bool:
    """Regenerate the registry using the generator script.

    Returns True if successful, False otherwise.
    """
    repo_root = Path(__file__).parent.parent
    generator = repo_root / "tools" / "generate_mcp_command_registry.py"

    try:
        subprocess.run(
            [sys.executable, str(generator)],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error regenerating registry: {e.stderr}", file=sys.stderr)
        return False


def registry_changed() -> bool:
    """Check if registry file has uncommitted changes."""
    repo_root = Path(__file__).parent.parent
    registry_path = repo_root / "mcp" / ".registry" / "commands.generated.json"

    try:
        result = subprocess.run(
            ["git", "diff", "--quiet", str(registry_path)],
            check=False,
            cwd=repo_root,
            capture_output=True,
        )
        return result.returncode != 0
    except subprocess.CalledProcessError:
        return True  # Assume changed if git fails


def main() -> int:
    """Regenerate registry and signal if modified."""
    if not regenerate_registry():
        print("Failed to regenerate registry", file=sys.stderr)
        return 1

    if registry_changed():
        print("Registry updated - staging changes")
        return 1  # Pre-commit will re-stage

    return 0  # No changes needed


if __name__ == "__main__":
    sys.exit(main())
