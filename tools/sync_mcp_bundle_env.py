#!/usr/bin/env python3
"""Write the env policy into mcp/server.d/server.meta.json "env" (mcp-bash 1.5.0+).

The MCPB bundle starts mcp-bash directly and never runs ``server.d/env.sh``; mcp-bash reads the
four env-policy keys from ``server.meta.json`` "env" instead, so tools, resource providers and
completion scripts receive ``AFFINITY_API_KEY`` and friends. The allowlist is
``_XAFFINITY_ENV_ALLOWLIST`` from ``mcp/server.d/env.sh`` minus ``MCPBASH_*`` names: mcp-bash
refuses those in server.meta.json (tools receive every ``MCPBASH_*`` variable anyway). Also
removes the old per-platform ``platform_overrides`` workaround.

Usage:
    python tools/sync_mcp_bundle_env.py          # rewrite server.meta.json if needed
    python tools/sync_mcp_bundle_env.py --check  # exit 1 if it is out of date
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MCP = Path(__file__).resolve().parent.parent / "mcp"
META = MCP / "server.d" / "server.meta.json"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def env_sh_allowlist() -> list[str]:
    text = (MCP / "server.d" / "env.sh").read_text()
    match = re.search(r'^\s*_XAFFINITY_ENV_ALLOWLIST="([^"]*)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit('_XAFFINITY_ENV_ALLOWLIST="..." not found in env.sh')
    names = match.group(1).split(",")
    bad = [n for n in names if not _NAME.fullmatch(n)]
    if bad:
        raise SystemExit(f"invalid variable names in _XAFFINITY_ENV_ALLOWLIST: {bad}")
    return names


def expected_env() -> dict[str, str]:
    allowlist = ",".join(n for n in env_sh_allowlist() if not n.startswith("MCPBASH_"))
    return {
        "MCPBASH_TOOL_ENV_MODE": "allowlist",
        "MCPBASH_TOOL_ENV_ALLOWLIST": allowlist,
        "MCPBASH_PROVIDER_ENV_MODE": "allowlist",
        "MCPBASH_PROVIDER_ENV_ALLOWLIST": allowlist,
    }


def main(argv: list[str]) -> int:
    meta = json.loads(META.read_text())
    wanted = expected_env()
    if meta.get("env") == wanted and "platform_overrides" not in meta:
        return 0
    if "--check" in argv:
        print(
            f"{META.relative_to(MCP.parent)} env is out of date: run "
            "python tools/sync_mcp_bundle_env.py",
            file=sys.stderr,
        )
        return 1
    meta.pop("platform_overrides", None)
    meta["env"] = wanted
    META.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Updated {META.relative_to(MCP.parent)} env")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
