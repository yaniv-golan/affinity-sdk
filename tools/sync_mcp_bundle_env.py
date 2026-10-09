#!/usr/bin/env python3
"""Write the MCPB bundle's per-platform env into mcp/server.d/server.meta.json.

Claude Desktop starts the bundle with ``mcp_config.env`` but never runs ``server.d/env.sh``, so
the env policy that lets tools, resource providers and completion scripts see
``AFFINITY_API_KEY`` must be in the manifest. mcp-bash 1.4.0 can only add env per platform
(``platform_overrides.<platform>.env``), and Claude Desktop *replaces* the base env with it
rather than merging. So each platform gets the full base env (what ``mcp-bash bundle`` puts in
``mcp_config.env``: three framework variables plus the ``MCPB_USER_CONFIG_ENV_MAP`` mappings
from ``mcp/mcpb.conf``) plus the four policy keys built from ``_XAFFINITY_ENV_ALLOWLIST`` in
``mcp/server.d/env.sh``.

Usage:
    python tools/sync_mcp_bundle_env.py          # rewrite server.meta.json if needed
    python tools/sync_mcp_bundle_env.py --check  # exit 1 if it is out of date

Replace with server.meta.json "env" once mcp-bash 1.5.0 supports it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MCP = Path(__file__).resolve().parent.parent / "mcp"
META = MCP / "server.d" / "server.meta.json"
PLATFORMS = ("darwin", "linux", "win32")


def _shell_value(text: str, name: str) -> str:
    match = re.search(rf'^\s*{name}="([^"]*)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit(f'{name}="..." not found')
    return match.group(1)


def expected_env() -> dict[str, str]:
    conf = (MCP / "mcpb.conf").read_text()
    env: dict[str, str] = {
        "MCPBASH_PROJECT_ROOT": "${__dirname}/server",
        "MCPBASH_TOOL_ALLOWLIST": "*",
        "MCPBASH_STATIC_REGISTRY": "1",
    }
    for pair in _shell_value(conf, "MCPB_USER_CONFIG_ENV_MAP").split(","):
        key, var = pair.split("=", 1)
        env[var.strip()] = "${user_config." + key.strip() + "}"
    allowlist = _shell_value((MCP / "server.d" / "env.sh").read_text(), "_XAFFINITY_ENV_ALLOWLIST")
    bad = [n for n in allowlist.split(",") if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n)]
    if bad:  # mcp-bash 1.5.0 rejects names outside this pattern
        raise SystemExit(f"invalid variable names in _XAFFINITY_ENV_ALLOWLIST: {bad}")
    env.update(
        {
            "MCPBASH_TOOL_ENV_MODE": "allowlist",
            "MCPBASH_TOOL_ENV_ALLOWLIST": allowlist,
            "MCPBASH_PROVIDER_ENV_MODE": "allowlist",
            "MCPBASH_PROVIDER_ENV_ALLOWLIST": allowlist,
        }
    )
    return env


def expected_overrides() -> dict[str, dict[str, dict[str, str]]]:
    env = expected_env()
    return {platform: {"env": dict(env)} for platform in PLATFORMS}


def main(argv: list[str]) -> int:
    meta = json.loads(META.read_text())
    wanted = expected_overrides()
    if meta.get("platform_overrides") == wanted:
        return 0
    if "--check" in argv:
        print(
            f"{META.relative_to(MCP.parent)} platform_overrides is out of date: run "
            "python tools/sync_mcp_bundle_env.py",
            file=sys.stderr,
        )
        return 1
    meta["platform_overrides"] = wanted
    META.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Updated {META.relative_to(MCP.parent)} platform_overrides")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
