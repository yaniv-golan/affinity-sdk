#!/usr/bin/env python3
"""Sync version from mcp/VERSION to MCP server metadata.

This script syncs the MCP server version to:
- mcp/server.d/server.meta.json

The single source of truth for MCP server version is mcp/VERSION.

It also copies CLI_MIN_VERSION from mcp/COMPATIBILITY to the xaffinity minVersion in
mcp/server.d/requirements.json. The MCPB bundle (Claude Desktop) has no COMPATIBILITY file and
never runs xaffinity-mcp.sh, so its tools read the minimum CLI version from there.

Note: mcp/.claude-plugin/plugin.json is NOT synced — the MCP plugin has
independent versioning (see VERSIONING.md).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Files that should match mcp/VERSION
# Note: mcp/.claude-plugin/plugin.json is intentionally excluded —
# the MCP plugin has independent versioning (see VERSIONING.md)
MCP_VERSION_TARGETS = [
    "mcp/server.d/server.meta.json",
]


def main() -> int:
    """Sync version and exit with 1 if files were modified."""
    root = Path(__file__).parent.parent
    modified = False

    # Read version from mcp/VERSION
    version_path = root / "mcp" / "VERSION"
    if not version_path.exists():
        print("Error: mcp/VERSION not found")
        return 1

    mcp_version = version_path.read_text().strip()

    # Update each target file
    for rel_path in MCP_VERSION_TARGETS:
        target_path = root / rel_path
        if not target_path.exists():
            print(f"Warning: {rel_path} not found, skipping")
            continue

        with target_path.open() as f:
            data = json.load(f)

        if data.get("version") == mcp_version:
            continue  # Already in sync

        # Update version
        data["version"] = mcp_version
        with target_path.open("w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

        print(f"Updated {rel_path} version to {mcp_version}")
        modified = True

    if sync_cli_min_version(root):
        modified = True

    return 1 if modified else 0


def sync_cli_min_version(root: Path) -> bool:
    """Copy CLI_MIN_VERSION to requirements.json; return True if the file changed."""
    compat = (root / "mcp" / "COMPATIBILITY").read_text()
    match = re.search(r'^CLI_MIN_VERSION="([^"]+)"', compat, re.MULTILINE)
    if not match:
        print("Error: CLI_MIN_VERSION not found in mcp/COMPATIBILITY")
        sys.exit(1)
    cli_min = match.group(1)

    rel_path = "mcp/server.d/requirements.json"
    target_path = root / rel_path
    data = json.loads(target_path.read_text())
    deps = [d for d in data.get("dependencies", []) if d.get("name") == "xaffinity"]
    if len(deps) != 1:
        print(f"Error: expected one xaffinity dependency in {rel_path}")
        sys.exit(1)
    if deps[0].get("minVersion") == cli_min:
        return False
    deps[0]["minVersion"] = cli_min
    target_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Updated {rel_path} xaffinity minVersion to {cli_min}")
    return True


if __name__ == "__main__":
    sys.exit(main())
