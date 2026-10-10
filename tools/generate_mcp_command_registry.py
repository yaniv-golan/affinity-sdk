#!/usr/bin/env python3
"""
Generate MCP command registry from explicit whitelist.

Only commands listed in mcp-commands.json are included in the output.
This ensures MCP exposure is explicit opt-in, not default.

Reads:
    mcp/.registry/mcp-commands.json (source of truth for what to expose)

Writes:
    mcp/.registry/commands.generated.json (auto-generated, don't edit)

Usage:
    python tools/generate_mcp_command_registry.py

Requirements:
    - Run with a Python that has affinity-sdk installed (any version, e.g. an editable install;
      `pip install -e '.[dev]'`). The CLI is run from this repo's source (`python -m affinity.cli`),
      so the registry always matches the current code and no reinstall is needed after a version
      bump. cliVersion is the pyproject.toml version.

CI Integration:
    Add to .github/workflows/ci.yml:
        - name: Verify MCP command registry is up to date
          run: |
            python tools/generate_mcp_command_registry.py
            git diff --exit-code mcp/.registry/commands.generated.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]


def get_pyproject_version() -> str:
    """Get version from pyproject.toml (source of truth)."""
    repo_root = Path(__file__).parent.parent
    pyproject_path = repo_root / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def run_cli_from_source(*args: str) -> str:
    """Run the CLI from this repo's source tree and return stdout.

    `-m` puts the working directory (the repo root) first on sys.path, ahead of any installed
    copy; PYTHONPATH covers interpreters started with -P / PYTHONSAFEPATH.
    """
    repo_root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(repo_root), env.get("PYTHONPATH", "")) if p)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "affinity.cli", *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        cwd=repo_root,
        env=env,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        print(
            f"Error: could not run the CLI from source with {sys.executable}. It needs "
            "affinity-sdk installed in this interpreter (any version) with the CLI "
            "dependencies: pip install -e '.[dev]'",
            file=sys.stderr,
        )
        raise subprocess.CalledProcessError(result.returncode, result.args)
    return result.stdout


def get_cli_commands() -> dict[str, dict]:
    """Get all CLI commands as a dict keyed by command name.

    Uses `xaffinity --help --json`, run from the repo's source.
    """
    data = json.loads(run_cli_from_source("--help", "--json"))
    commands = data.get("commands", [])
    # Convert to dict keyed by name for easy lookup
    return {cmd["name"]: cmd for cmd in commands}


def load_mcp_config(config_path: Path) -> dict[str, dict]:
    """Load MCP commands config (whitelist + metadata).

    Returns a dict mapping command name to metadata dict.
    Raises FileNotFoundError if config doesn't exist.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"MCP config not found: {config_path}")

    data = json.loads(config_path.read_text())
    # Extract commands dict, ignore _comment
    commands = data.get("commands", {})
    if not commands:
        print("Warning: No commands in mcp-commands.json", file=sys.stderr)
    return commands


def get_param_with_aliases(params: dict, flag_name: str) -> tuple[str, list[str]] | None:
    """Get a parameter and all its aliases from CLI JSON parameters.

    The JSON output has structure like:
    {"--max-results": {"aliases": ["--limit", "-n"], ...}}

    Returns (canonical_flag, [all_aliases]) or None if not found.
    """
    if flag_name in params:
        param = params[flag_name]
        aliases = param.get("aliases", [])
        return flag_name, [flag_name, *aliases]
    # Check if flag_name is an alias of another param
    for canonical, param in params.items():
        if flag_name in param.get("aliases", []):
            return canonical, [canonical, *param.get("aliases", [])]
    return None


def add_limit_config(cmd: dict) -> None:
    """Add limitConfig to command if it supports pagination.

    ``mcp-commands.json`` may set ``limitConfig`` for a command: ``false`` means none (e.g. a
    ``--limit`` that is a byte size), an object sets ``default`` / ``max`` (e.g. an API that
    caps results at 100). The flag names always come from the CLI. Two optional keys are kept
    for the gateway: ``enforceMax`` (refuse a limit above ``max`` instead of leaving it to the
    CLI) and ``allowUnboundedWith`` (an option and value that make ``--all`` acceptable, e.g.
    ``["--strategy", "field"]``).
    """
    configured = cmd.get("limitConfig")
    if configured is False:
        del cmd["limitConfig"]
        return
    params = cmd.get("parameters", {})

    # Check for limit parameter (--max-results preferred, fall back to --limit)
    limit_info = get_param_with_aliases(params, "--max-results")
    if limit_info is None:
        limit_info = get_param_with_aliases(params, "--limit")
    if limit_info is None:
        if configured is not None:
            raise ValueError(
                f"{cmd.get('name')}: limitConfig is configured but the command has no "
                "--max-results / --limit option"
            )
        return  # No pagination support

    limit_flag, limit_aliases = limit_info
    values = configured if isinstance(configured, dict) else {}
    cmd["limitConfig"] = {
        "flag": limit_flag,
        "flagAliases": limit_aliases,
        "default": values.get("default", 1000),
        "max": values.get("max", 10000),
    }
    if values.get("enforceMax"):
        cmd["limitConfig"]["enforceMax"] = True
    if values.get("allowUnboundedWith"):
        cmd["limitConfig"]["allowUnboundedWith"] = list(values["allowUnboundedWith"])

    # Check for unbounded flag (--all)
    all_info = get_param_with_aliases(params, "--all")
    if all_info is not None:
        all_flag, all_aliases = all_info
        cmd["limitConfig"]["unboundedFlag"] = all_flag
        cmd["limitConfig"]["unboundedFlagAliases"] = all_aliases


def merge_command_with_config(cli_cmd: dict, config_meta: dict) -> dict:
    """Merge CLI command data with config metadata.

    CLI command provides: name, description, category, parameters, positionals, etc.
    Config provides: whenToUse, examples, relatedCommands, and any additional metadata.

    All config metadata is merged into the output, allowing rich command-specific
    documentation (e.g., syntax references, critical notes, usage patterns).
    """
    # Start with CLI data
    merged = cli_cmd.copy()

    # Merge all config metadata (config overrides CLI if same key exists)
    for key, value in config_meta.items():
        merged[key] = value

    return merged


def sort_registry(commands: list[dict]) -> list[dict]:
    """Sort commands and their parameters for deterministic output."""
    sorted_commands = sorted(commands, key=lambda c: c["name"])
    for cmd in sorted_commands:
        if cmd.get("parameters"):
            cmd["parameters"] = dict(sorted(cmd["parameters"].items()))
    return sorted_commands


def generate_registry(config_path: Path, output_path: Path) -> None:
    """Generate the MCP command registry file."""
    # The help comes from the repo's source, so it describes the pyproject version
    cli_version = get_pyproject_version()

    # Load MCP config (whitelist)
    try:
        mcp_config = load_mcp_config(config_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Get all CLI commands
    try:
        cli_commands = get_cli_commands()
    except subprocess.CalledProcessError as e:
        print(f"Error: CLI returned error: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: CLI did not return valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter and merge: only include commands from whitelist
    output_commands = []
    missing_commands = []

    for cmd_name, config_meta in mcp_config.items():
        if cmd_name in cli_commands:
            merged = merge_command_with_config(cli_commands[cmd_name], config_meta)
            add_limit_config(merged)
            output_commands.append(merged)
        else:
            missing_commands.append(cmd_name)

    # Report missing commands (in config but not in CLI)
    if missing_commands:
        print(
            f"Warning: {len(missing_commands)} commands in config not found in CLI:",
            file=sys.stderr,
        )
        for name in missing_commands:
            print(f"  - {name}", file=sys.stderr)

    # Sort for deterministic output
    sorted_commands = sort_registry(output_commands)

    # Build registry with generation metadata
    registry = {
        "_generated": {
            "warning": "DO NOT EDIT - This file is auto-generated",
            "generator": "tools/generate_mcp_command_registry.py",
            "cliVersion": cli_version,
            "sourceConfig": str(config_path.relative_to(config_path.parent.parent.parent)),
        },
        "version": 1,
        "cliVersion": cli_version,
        "commands": sorted_commands,
        "total": len(sorted_commands),
    }

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write with consistent formatting
    output_path.write_text(
        json.dumps(registry, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    )

    total_cli = len(cli_commands)
    included = len(sorted_commands)
    excluded = total_cli - included
    print(
        f"Generated {output_path.name} with {included} commands "
        f"({excluded} excluded, CLI v{cli_version})"
    )


def main() -> None:
    """Main entry point."""
    repo_root = Path(__file__).parent.parent
    config_path = repo_root / "mcp" / ".registry" / "mcp-commands.json"
    output_path = repo_root / "mcp" / ".registry" / "commands.generated.json"
    generate_registry(config_path, output_path)


if __name__ == "__main__":
    main()
