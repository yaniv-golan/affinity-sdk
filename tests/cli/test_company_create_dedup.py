import json
from unittest.mock import patch

from click.testing import CliRunner

from affinity.cli.main import cli
from affinity.exceptions import DuplicateEntityError
from affinity.models.entities import Company


def test_company_create_refuses_duplicate_by_default(monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    def raise_dup(self, data, *, if_not_exists=True):  # noqa: ARG001
        raise DuplicateEntityError(
            "Company 'Elssway' exists",
            entity_type="company",
            existing_id=282327760,
            existing_name="Elssway",
            existing_domain="elssway.com",
            existing_is_global=False,
        )

    with patch("affinity.services.companies.CompanyService.create", raise_dup):
        result = runner.invoke(
            cli,
            ["company", "create", "--name", "Elssway", "--json"],
            catch_exceptions=False,
        )

    assert result.exit_code == 6, result.output
    payload = json.loads(result.output)
    assert payload["error"]["type"] == "duplicate_exists"
    assert payload["error"]["details"]["existing"]["companyId"] == 282327760
    assert payload["error"]["details"]["existing"]["name"] == "Elssway"
    assert payload["error"]["details"]["existing"]["domain"] == "elssway.com"
    assert payload["error"]["details"]["existing"]["isGlobal"] is False


def test_company_create_refuses_global_duplicate_with_targeted_hint(monkeypatch):
    """Global directory matches get a specific hint to use List Entries."""
    runner = CliRunner()
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    def raise_dup(self, data, *, if_not_exists=True):  # noqa: ARG001
        raise DuplicateEntityError(
            "A global Affinity directory record matches (id=9999)",
            entity_type="company",
            existing_id=9999,
            existing_name="Stripe",
            existing_domain="stripe.com",
            existing_is_global=True,
        )

    with patch("affinity.services.companies.CompanyService.create", raise_dup):
        result = runner.invoke(
            cli,
            ["company", "create", "--name", "Stripe", "--domain", "stripe.com", "--json"],
            catch_exceptions=False,
        )

    assert result.exit_code == 6, result.output
    payload = json.loads(result.output)
    assert payload["error"]["type"] == "duplicate_exists"
    assert payload["error"]["details"]["existing"]["isGlobal"] is True
    hint = payload["error"]["hint"]
    assert "global" in hint.lower()
    assert "list" in hint.lower() or "List Entries" in hint


def test_company_create_allow_duplicate_bypasses(monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    captured = {}

    def fake_create(self, data, *, if_not_exists=True):  # noqa: ARG001
        captured["if_not_exists"] = if_not_exists
        return Company(id=999, name=data.name, domain=data.domain, domains=[], is_global=False)

    with patch("affinity.services.companies.CompanyService.create", fake_create):
        result = runner.invoke(
            cli,
            ["company", "create", "--name", "Elssway", "--allow-duplicate", "--json"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert captured["if_not_exists"] is False
