import json
from unittest.mock import patch

from click.testing import CliRunner

from affinity.cli.main import cli
from affinity.exceptions import DuplicateEntityError
from affinity.models.entities import Person


def test_person_create_refuses_duplicate_by_default(monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    def raise_dup(self, data, *, if_not_exists=True):  # noqa: ARG001
        raise DuplicateEntityError(
            "Person exists",
            entity_type="person",
            existing_id=500,
            existing_name="Alex Rivera",
        )

    with patch("affinity.services.persons.PersonService.create", raise_dup):
        result = runner.invoke(
            cli,
            [
                "person",
                "create",
                "--first-name",
                "Alex",
                "--last-name",
                "Rivera",
                "--email",
                "alex@acme.com",
                "--json",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 6, result.output
    payload = json.loads(result.output)
    assert payload["error"]["type"] == "duplicate_exists"
    assert payload["error"]["details"]["existing"]["personId"] == 500


def test_person_create_allow_duplicate_bypasses(monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("AFFINITY_API_KEY", "test")

    captured = {}

    def fake_create(self, data, *, if_not_exists=True):  # noqa: ARG001
        captured["if_not_exists"] = if_not_exists
        return Person(
            id=999,
            first_name=data.first_name,
            last_name=data.last_name,
            emails=list(data.emails or []),
        )

    with patch("affinity.services.persons.PersonService.create", fake_create):
        result = runner.invoke(
            cli,
            [
                "person",
                "create",
                "--first-name",
                "Alex",
                "--last-name",
                "Rivera",
                "--email",
                "alex@acme.com",
                "--allow-duplicate",
                "--json",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert captured["if_not_exists"] is False
