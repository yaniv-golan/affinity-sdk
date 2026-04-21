"""Tests for Task 6.1: Query rejects filters on global entities.

Global entities (companies/persons/opportunities) have empty filterable_fields
because the V2 API does not support server-side filtering on these endpoints.
"""

import json

import pytest
from click.testing import CliRunner

from affinity.cli.main import cli
from affinity.cli.query.schema import SCHEMA_REGISTRY


@pytest.mark.req("REQ-FILTER-003")
def test_companies_schema_has_no_filterable_fields():
    assert SCHEMA_REGISTRY["companies"].filterable_fields == frozenset()


@pytest.mark.req("REQ-FILTER-003")
def test_persons_schema_has_no_filterable_fields():
    assert SCHEMA_REGISTRY["persons"].filterable_fields == frozenset()


@pytest.mark.req("REQ-FILTER-003")
def test_opportunities_schema_has_no_filterable_fields():
    assert SCHEMA_REGISTRY["opportunities"].filterable_fields == frozenset()


@pytest.mark.req("REQ-FILTER-003")
def test_listentries_schema_still_has_filterable_fields():
    """Regression guard: listEntries filter must still work (client-side)."""
    assert "id" in SCHEMA_REGISTRY["listEntries"].filterable_fields


@pytest.mark.req("REQ-FILTER-003")
def test_query_companies_with_filter_errors_with_hint(monkeypatch):
    monkeypatch.setenv("AFFINITY_API_KEY", "test")
    runner = CliRunner()
    query_json = json.dumps(
        {
            "from": "companies",
            "where": {"path": "name", "op": "eq", "value": "Acme"},
        }
    )
    result = runner.invoke(
        cli,
        ["--readonly", "query", "--query", query_json, "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    err = payload["error"]
    assert err["type"] in ("unsupported_filter", "validation_error", "usage_error")
    hint = (err.get("hint") or "") + (err.get("message") or "")
    assert "--query" in hint or "search_pages" in hint
