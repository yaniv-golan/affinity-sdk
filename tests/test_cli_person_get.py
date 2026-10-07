from __future__ import annotations

import json

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)


def test_person_get_by_id_minimal(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "firstName": "Alice",
                "lastName": "Smith",
                "primaryEmailAddress": "alice@example.com",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "person", "get", "123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    assert payload["command"]["name"] == "person get"
    assert payload["data"]["person"]["id"] == 123
    assert payload["meta"]["resolved"]["person"]["source"] == "id"


def test_person_get_accepts_affinity_dot_com_url(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "firstName": "Alice",
                "lastName": "Smith",
                "primaryEmailAddress": "alice@example.com",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "person", "get", "https://mydomain.affinity.com/persons/123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    assert payload["meta"]["resolved"]["person"]["source"] == "url"
    assert payload["meta"]["resolved"]["person"]["personId"] == 123


def test_person_get_expand_list_entries_filtered_by_list_id(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "firstName": "Alice",
                "lastName": "Smith",
                "primaryEmailAddress": "alice@example.com",
            },
        )
    )
    respx_mock.get("https://api.affinity.co/v2/persons/123/list-entries?limit=100").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": 1, "listId": 10, "createdAt": "2020-01-01T00:00:00Z"},
                    {"id": 2, "listId": 11, "createdAt": "2020-01-01T00:00:00Z"},
                ]
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "person", "get", "123", "--expand", "list-entries", "--list", "10"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    entries = payload["data"]["listEntries"]
    assert [e["id"] for e in entries] == [1]
    assert payload["meta"]["resolved"]["list"]["listId"] == 10


def test_person_get_default_reports_not_requested(respx_mock: respx.MockRouter) -> None:
    """Plain get fetches no fields/list entries; meta.notRequested must say so."""
    respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
        return_value=Response(200, json={"id": 123, "firstName": "Alice", "lastName": "Smith"})
    )

    result = CliRunner().invoke(
        cli, ["--json", "person", "get", "123"], env={"AFFINITY_API_KEY": "test-key"}
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["meta"]["notRequested"] == [
        {"key": "data.person.fields", "flag": "--all-fields"},
        {"key": "data.listEntries", "flag": "--expand list-entries"},
    ]


def test_person_get_no_fields_flag_is_not_reported(respx_mock: respx.MockRouter) -> None:
    """An explicit --no-fields is a deliberate choice, not something to flag."""
    respx_mock.get("https://api.affinity.co/v2/persons/123").mock(
        return_value=Response(200, json={"id": 123, "firstName": "Alice", "lastName": "Smith"})
    )

    result = CliRunner().invoke(
        cli,
        ["--json", "person", "get", "123", "--no-fields"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["meta"]["notRequested"] == [
        {"key": "data.listEntries", "flag": "--expand list-entries"},
    ]
