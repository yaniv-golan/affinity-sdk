"""--filter on list export requires --all, --max-results, or --first-page-only."""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

from affinity.cli.main import cli


@pytest.mark.req("REQ-EXPORT-FILTER-SAFETY-001")
def test_list_export_filter_without_scope_errors(monkeypatch):
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
            "--filter",
            'entityName =~ "Fusion Mantle"',
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    err = payload["error"]
    assert err["type"] in ("usage_error", "unsupported_filter")
    msg = (err.get("message") or "") + (err.get("hint") or "")
    assert "--all" in msg
    assert "--first-page-only" in msg


@pytest.mark.req("REQ-EXPORT-FILTER-SAFETY-002")
def test_list_export_filter_with_all_is_allowed(monkeypatch):
    """--filter --all passes validation (no flag-combo error)."""
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
            "--filter",
            'name = "X"',
            "--all",
        ],
        catch_exceptions=False,
    )
    # We want to prove the flag-combo check doesn't fire. Error kind may
    # vary (no transport); just assert it's not the scope error.
    if result.exit_code == 2:
        payload = json.loads(result.output)
        err_msg = (payload["error"].get("message") or "") + (payload["error"].get("hint") or "")
        assert "--first-page-only" not in err_msg, "scope-error fired despite --all"


@pytest.mark.req("REQ-EXPORT-FILTER-SAFETY-003")
def test_list_export_first_page_only_sets_truncation_meta(make_mock_transport, monkeypatch):
    """--first-page-only must set meta.truncated=true + reason=firstPageOnly.

    Exercises pagination (asserts nextCursor exists to prove truncation), so
    uses make_mock_transport (httpx.MockTransport) per .cursorrules.
    """
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    _list_json = {
        "id": 10,
        "name": "Pipeline",
        "type": 0,
        "public": False,
        "owner_id": 1,
        "creator_id": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v2/lists/10"):
            return httpx.Response(200, json=_list_json, request=request)
        if url.rstrip("/").endswith("/lists/10") and "v2" not in url:
            return httpx.Response(200, json=_list_json, request=request)
        if "/v2/lists/10/list-entries" in url:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 1,
                            "listId": 10,
                            "type": "company",
                            "entity": {"id": 555, "name": "Fusion Mantle"},
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "pagination": {"nextCursor": "opaque-cursor-1", "prevCursor": None},
                },
                request=request,
            )
        if "/v2/lists/10/fields" in url:
            return httpx.Response(
                200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
            )
        if "/v2/lists/10/saved-views" in url:
            return httpx.Response(
                200, json={"data": [], "pagination": {"nextUrl": None}}, request=request
            )
        if url.endswith("/fields") or "/fields?" in url:
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(404, json={}, request=request)

    make_mock_transport(handler)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--readonly",
            "--quiet",
            "--json",
            "list",
            "export",
            "10",
            "--filter",
            'name = "Fusion Mantle"',
            "--first-page-only",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # Last non-empty line is the JSON envelope (progress/warnings go to stderr).
    lines = [line for line in result.output.splitlines() if line.strip()]
    payload = json.loads(lines[-1])
    assert payload["meta"].get("truncated") is True, payload
    assert payload["meta"].get("truncationReason") == "firstPageOnly", payload
