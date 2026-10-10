"""get-entity-dossier: list memberships come from `<type> get --expand list-entries`, and the
team's strongest relationships from `<type> relationships`. Runs tool.sh with a fake CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MCP = REPO / "mcp"
JQ = shutil.which("jq")

pytestmark = pytest.mark.skipif(JQ is None, reason="jq not installed")

ENTRIES = [{"id": 7, "listId": 1, "listName": "Dealflow"}]
RELS = [{"person1": {"personId": 5}, "person2": {"personId": 9}, "interactionScore": 0.9}]

FAKE_CLI = r"""#!/usr/bin/env bash
echo "$*" >> "$0.log"
case "$*" in
  *version*) printf '{"ok":true,"data":{"version":"%s"}}\n' "$FAKE_VERSION" ;;
  *list-entry*) echo '{"ok":false,"error":{"message":"No such command"}}'; exit 2 ;;
  *"get 10"*"--expand list-entries"*)
    printf '{"ok":true,"data":{"%s":{"id":10,"name":"Acme"},"listEntries":%s}}\n' \
      "$FAKE_TYPE" "$ENTRIES" ;;
  *"get 10"*) printf '{"ok":true,"data":{"%s":{"id":10,"name":"Acme"}}}\n' "$FAKE_TYPE" ;;
  *"relationships 10"*) printf '{"ok":true,"data":{"relationships":%s}}\n' "$RELS" ;;
  *) echo '{"ok":true,"data":[]}' ;;
esac
"""


def _min_version() -> str:
    data = json.loads((MCP / "server.d" / "requirements.json").read_text())
    return next(d["minVersion"] for d in data["dependencies"] if d["name"] == "xaffinity")


def _dossier(tmp_path: Path, entity_type: str, **args: object) -> tuple[dict, list[str]]:  # type: ignore[type-arg]
    fake = tmp_path / "xaffinity"
    fake.write_text(FAKE_CLI)
    fake.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCP_SDK": str(MCP / ".mcp-bash" / "sdk"),
        "MCPBASH_PROJECT_ROOT": str(MCP),
        "MCPBASH_JSON_TOOL_BIN": str(JQ),
        "MCPBASH_JSON_TOOL": "jq",
        "MCP_TOOL_ARGS_JSON": json.dumps({"entityType": entity_type, "entityId": 10, **args}),
        "XAFFINITY_CLI": str(fake),
        "FAKE_VERSION": _min_version(),
        "FAKE_TYPE": entity_type,
        "ENTRIES": json.dumps(ENTRIES),
        "RELS": json.dumps(RELS),
    }
    out = subprocess.run(
        ["bash", str(MCP / "tools" / "get-entity-dossier" / "tool.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    ).stdout
    calls = (tmp_path / "xaffinity.log").read_text().splitlines()
    return json.loads(out.strip().splitlines()[-1]), calls


@pytest.mark.parametrize("entity_type", ["company", "person"])
def test_list_memberships_and_relationships(tmp_path: Path, entity_type: str) -> None:
    dossier, calls = _dossier(tmp_path, entity_type)
    assert dossier["details"] == {"id": 10, "name": "Acme"}
    assert dossier["listMemberships"] == ENTRIES
    assert dossier["relationships"] == RELS
    assert not any("list-entry" in c or "relationship-strength" in c for c in calls)


def test_without_lists_the_get_is_not_expanded(tmp_path: Path) -> None:
    dossier, calls = _dossier(tmp_path, "company", includeLists=False)
    assert dossier["listMemberships"] == []
    assert not any("--expand" in c for c in calls)


def test_opportunity_has_no_relationships_call(tmp_path: Path) -> None:
    dossier, calls = _dossier(tmp_path, "opportunity")
    assert dossier["details"] == {"id": 10, "name": "Acme"}
    assert dossier["relationships"] == []
    assert not any("relationships" in c for c in calls)
