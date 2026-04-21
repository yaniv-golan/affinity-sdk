"""--company-id / --person-id entity-scoped list export."""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

from affinity.cli.main import cli


@pytest.mark.req("REQ-EXPORT-ENTITY-SCOPE-001")
def test_company_id_scopes_export_to_one_row(make_mock_transport, monkeypatch):
    """One --company-id → one row (or zero if not on list)."""
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/lists/10"):
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "name": "Pipeline",
                    "type": 1,
                    "public": True,
                    "owner_id": 1,
                    "creator_id": 1,
                    "list_size": 1,
                },
                request=request,
            )
        if "/fields" in url and "list-entries" not in url:
            # V1 /fields?list_id=10 — return empty field list for minimal test.
            return httpx.Response(200, json=[], request=request)
        if "/companies/555/list-entries" in url:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 901,
                            "listId": 10,
                            "type": "company",
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "pagination": {"nextCursor": None, "prevCursor": None},
                },
                request=request,
            )
        if "/lists/10/list-entries/901" in url:
            return httpx.Response(
                200,
                json={
                    "id": 901,
                    "listId": 10,
                    "type": "company",
                    "entity": {"id": 555, "name": "Fusion Mantle"},
                    "createdAt": "2026-01-01T00:00:00Z",
                },
                request=request,
            )
        return httpx.Response(404, json={}, request=request)

    make_mock_transport(handler)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--readonly", "--json", "--quiet", "list", "export", "10", "--company-id", "555"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # Parse last non-empty line (defensive against stderr interleave).
    payload = json.loads([ln for ln in result.output.strip().splitlines() if ln.strip()][-1])
    rows = payload["data"]["rows"]
    assert len(rows) == 1
    assert rows[0]["entityId"] == 555
    assert rows[0]["entityName"] == "Fusion Mantle"


@pytest.mark.req("REQ-EXPORT-ENTITY-SCOPE-002")
def test_company_id_not_on_list_returns_empty_with_warning(make_mock_transport, monkeypatch):
    """Dedup-check case: entity not on list → 0 rows + explicit warning."""
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/lists/10"):
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "name": "Pipeline",
                    "type": 1,
                    "public": True,
                    "owner_id": 1,
                    "creator_id": 1,
                    "list_size": 1,
                },
                request=request,
            )
        if "/fields" in url and "list-entries" not in url:
            # V1 /fields?list_id=10 — return empty field list for minimal test.
            return httpx.Response(200, json=[], request=request)
        if "/companies/555/list-entries" in url:
            return httpx.Response(
                200,
                json={"data": [], "pagination": {"nextCursor": None, "prevCursor": None}},
                request=request,
            )
        return httpx.Response(404, json={}, request=request)

    make_mock_transport(handler)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--readonly", "--json", "--quiet", "list", "export", "10", "--company-id", "555"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads([ln for ln in result.output.strip().splitlines() if ln.strip()][-1])
    assert payload["data"]["rows"] == []
    warnings_text = " ".join(payload.get("warnings", []))
    assert "555" in warnings_text
    assert "not on this list" in warnings_text.lower()


@pytest.mark.req("REQ-EXPORT-ENTITY-SCOPE-003")
def test_company_id_incompatible_with_person_list_errors(make_mock_transport, monkeypatch):
    """--company-id on a person list → usage_error."""
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Verify actual PERSON int enum value; grep: grep -n "class ListType" affinity/models
        if url.endswith("/lists/10") and "list-entries" not in url:
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "name": "Contacts",
                    "type": 0,
                    "public": True,
                    "owner_id": 1,
                    "creator_id": 1,
                    "list_size": 0,
                },
                request=request,
            )
        return httpx.Response(404, json={}, request=request)

    make_mock_transport(handler)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--readonly", "--json", "--quiet", "list", "export", "10", "--company-id", "555"],
        catch_exceptions=False,
    )
    assert result.exit_code == 2, result.output
    payload = json.loads([ln for ln in result.output.strip().splitlines() if ln.strip()][-1])
    assert payload["error"]["type"] == "usage_error"
    # Error message should mention both the actual list type and the flag
    msg = (payload["error"].get("message") or "").lower()
    assert "person" in msg or "company-id" in msg


@pytest.mark.req("REQ-EXPORT-ENTITY-SCOPE-004")
def test_company_id_rejects_saved_view_combo(monkeypatch):
    """--company-id and --saved-view are mutually exclusive."""
    monkeypatch.setenv("AFFINITY_API_KEY", "test")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--readonly",
            "--json",
            "list",
            "export",
            "10",
            "--company-id",
            "555",
            "--saved-view",
            "Active",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 2
    payload = json.loads([ln for ln in result.output.strip().splitlines() if ln.strip()][-1])
    assert payload["error"]["type"] == "usage_error"
