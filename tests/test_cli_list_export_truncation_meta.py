"""Tests for meta.truncated / meta.truncationReason on list export."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

try:
    import respx
except ModuleNotFoundError:
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:
    pytest.skip("respx is not installed", allow_module_level=True)


@pytest.mark.req("REQ-EXPORT-TRUNC-META-001")
def test_list_export_meta_truncated_absent_on_complete_all_export(
    respx_mock: respx.MockRouter,
) -> None:
    """Complete --all export: no truncation signal in meta."""
    _list_json = {
        "id": 10,
        "name": "Pipeline",
        "type": 0,
        "public": False,
        "owner_id": 1,
        "creator_id": 1,
    }
    # V2 resolve list by ID
    respx_mock.get("https://api.affinity.co/v2/lists/10").mock(
        return_value=Response(200, json=_list_json)
    )
    # V1 list metadata
    respx_mock.get("https://api.affinity.co/lists/10").mock(
        return_value=Response(200, json=_list_json)
    )
    # List entries (empty — full export)
    respx_mock.get("https://api.affinity.co/v2/lists/10/list-entries").mock(
        return_value=Response(
            200,
            json={"data": [], "pagination": {"nextUrl": None}},
        )
    )
    # V2 list fields
    respx_mock.get("https://api.affinity.co/v2/lists/10/fields").mock(
        return_value=Response(
            200,
            json={"data": [], "pagination": {"nextUrl": None}},
        )
    )
    # V1 fields (for dropdown_options)
    respx_mock.get("https://api.affinity.co/fields").mock(return_value=Response(200, json=[]))
    # V2 saved views (may be called)
    respx_mock.get("https://api.affinity.co/v2/lists/10/saved-views").mock(
        return_value=Response(
            200,
            json={"data": [], "pagination": {"nextUrl": None}},
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--readonly", "--json", "list", "export", "10", "--all"],
        env={"AFFINITY_API_KEY": "test"},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    meta = payload["meta"]
    assert meta.get("truncated") in (None, False)
    assert meta.get("truncationReason") is None
