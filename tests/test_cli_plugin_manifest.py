"""The xaffinity-cli plugin manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = (
    Path(__file__).parent.parent / "plugins" / "xaffinity-cli" / ".claude-plugin" / "plugin.json"
)


@pytest.mark.req("CLI-PLUGIN-MANIFEST")
def test_manifest_declares_no_cli_credentials():
    """A `clis` block routes xaffinity through Cowork's CLI credential broker.

    Until Anthropic enables that broker it refuses every call ("cli-wrapper: plugin oauth
    disabled", measured in local Cowork 2026-10-11), so the CLI never runs. Re-add it only once
    a live Cowork run shows the broker passing the key through.
    """
    assert "clis" not in json.loads(MANIFEST.read_text())
