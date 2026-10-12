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


def test_note_ls_minimal(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/notes").mock(
        return_value=Response(
            200,
            json={
                "notes": [
                    {
                        "id": 1,
                        "creator_id": 2,
                        "content": "Hello",
                        "type": 0,
                        "personIds": [3],
                        "organizationIds": [4],
                        "opportunityIds": [5],
                        "createdAt": "2024-01-01T00:00:00Z",
                    }
                ],
                "next_page_token": "next",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "note", "ls"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"][0]["id"] == 1
    assert payload["data"][0]["companyIds"] == [4]
    assert payload["meta"]["pagination"]["nextCursor"] == "next"


def test_note_create_update_delete(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("https://api.affinity.co/notes").mock(
        return_value=Response(
            200,
            json={
                "id": 10,
                "creator_id": 2,
                "content": "Created",
                "type": 0,
                "person_ids": [3],
                "organization_ids": [4],
                "opportunity_ids": [],
                "created_at": "2024-01-01T00:00:00Z",
            },
        )
    )
    respx_mock.put("https://api.affinity.co/notes/10").mock(
        return_value=Response(
            200,
            json={
                "id": 10,
                "creator_id": 2,
                "content": "Updated",
                "type": 0,
                "person_ids": [3],
                "organization_ids": [4],
                "opportunity_ids": [],
                "created_at": "2024-01-01T00:00:00Z",
            },
        )
    )
    respx_mock.delete("https://api.affinity.co/notes/10").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()

    created = runner.invoke(
        cli,
        [
            "--json",
            "note",
            "create",
            "--content",
            "Created",
            "--person-id",
            "3",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert created.exit_code == 0
    created_payload = json.loads(created.output.strip())
    assert created_payload["data"]["note"]["id"] == 10

    updated = runner.invoke(
        cli,
        ["--json", "note", "update", "10", "--content", "Updated"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert updated.exit_code == 0
    updated_payload = json.loads(updated.output.strip())
    assert updated_payload["data"]["note"]["content"] == "Updated"

    deleted = runner.invoke(
        cli,
        ["--json", "note", "delete", "10", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert deleted.exit_code == 0
    deleted_payload = json.loads(deleted.output.strip())
    assert deleted_payload["data"]["success"] is True


def test_interaction_ls_minimal(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/interactions").mock(
        return_value=Response(
            200,
            json={
                "interactions": [
                    {
                        "id": 5,
                        "type": 3,
                        "date": "2024-01-01T00:00:00Z",
                        "subject": "Hello",
                        "persons": [{"id": 1, "type": "external"}],
                    }
                ],
                "next_page_token": None,
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "-q",
            "interaction",
            "ls",
            "--type",
            "email",
            "--person-id",
            "1",
            "--days",
            "7",  # Required: must specify date range
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"][0]["id"] == 5
    # Pagination is no longer returned for interaction ls (auto-chunking fetches all)


def test_interaction_create_update_delete(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("https://api.affinity.co/interactions").mock(
        return_value=Response(
            200,
            json={
                "id": 10,
                "type": 3,
                "date": "2024-01-01T00:00:00Z",
                "subject": "Hello",
                "persons": [{"id": 1, "type": "external"}],
            },
        )
    )
    respx_mock.put("https://api.affinity.co/interactions/10").mock(
        return_value=Response(
            200,
            json={
                "id": 10,
                "type": 3,
                "date": "2024-01-01T00:00:00Z",
                "subject": "Updated",
                "persons": [{"id": 1, "type": "external"}],
            },
        )
    )
    respx_mock.delete("https://api.affinity.co/interactions/10?type=3").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()

    created = runner.invoke(
        cli,
        [
            "--json",
            "interaction",
            "create",
            "--type",
            "email",
            "--person-id",
            "1",
            "--content",
            "Hello",
            "--date",
            "2024-01-01T00:00:00Z",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert created.exit_code == 0
    created_payload = json.loads(created.output.strip())
    assert created_payload["data"]["interaction"]["id"] == 10

    updated = runner.invoke(
        cli,
        [
            "--json",
            "interaction",
            "update",
            "10",
            "--type",
            "email",
            "--content",
            "Updated",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert updated.exit_code == 0
    updated_payload = json.loads(updated.output.strip())
    assert updated_payload["data"]["interaction"]["subject"] == "Updated"

    deleted = runner.invoke(
        cli,
        ["--json", "interaction", "delete", "10", "--type", "email", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert deleted.exit_code == 0
    deleted_payload = json.loads(deleted.output.strip())
    assert deleted_payload["data"]["success"] is True


@pytest.mark.req("CLI-INTERACTION-INCLUDE-ME")
def test_interaction_create_include_me(respx_mock: respx.MockRouter) -> None:
    """--include-me resolves whoami and prepends person ID."""
    respx_mock.get("https://api.affinity.co/v2/auth/whoami").mock(
        return_value=Response(
            200,
            json={
                "tenant": {"id": 1, "name": "Test", "subdomain": "test"},
                "user": {
                    "id": 100,
                    "firstName": "Me",
                    "lastName": "User",
                    "emailAddress": "me@co.com",
                },
                "grant": {
                    "type": "api_key",
                    "scopes": ["all"],
                    "createdAt": "2024-01-01T00:00:00Z",
                },
            },
        )
    )
    respx_mock.post("https://api.affinity.co/interactions").mock(
        return_value=Response(
            200,
            json={
                "id": 20,
                "type": 3,
                "date": "2024-01-01T00:00:00Z",
                "subject": "Meeting notes",
                "persons": [
                    {"id": 100, "type": "internal"},
                    {"id": 42, "type": "external"},
                ],
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "interaction",
            "create",
            "--type",
            "meeting",
            "--person-id",
            "42",
            "--include-me",
            "--content",
            "Meeting notes",
            "--date",
            "2024-01-01T00:00:00Z",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["interaction"]["id"] == 20

    # Verify the POST body included both person IDs (100 from whoami + 42)
    post_call = respx_mock.calls[-1]
    body = json.loads(post_call.request.content)
    assert 100 in body["person_ids"]
    assert 42 in body["person_ids"]


@pytest.mark.req("CLI-INTERACTION-INCLUDE-ME")
def test_interaction_create_include_me_dedup(respx_mock: respx.MockRouter) -> None:
    """--include-me deduplicates if user already passed their own ID."""
    respx_mock.get("https://api.affinity.co/v2/auth/whoami").mock(
        return_value=Response(
            200,
            json={
                "tenant": {"id": 1, "name": "Test", "subdomain": "test"},
                "user": {
                    "id": 100,
                    "firstName": "Me",
                    "lastName": "User",
                    "emailAddress": "me@co.com",
                },
                "grant": {
                    "type": "api_key",
                    "scopes": ["all"],
                    "createdAt": "2024-01-01T00:00:00Z",
                },
            },
        )
    )
    respx_mock.post("https://api.affinity.co/interactions").mock(
        return_value=Response(
            200,
            json={
                "id": 21,
                "type": 3,
                "date": "2024-01-01T00:00:00Z",
                "subject": "Dedup test",
                "persons": [
                    {"id": 100, "type": "internal"},
                    {"id": 42, "type": "external"},
                ],
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "interaction",
            "create",
            "--type",
            "meeting",
            "--person-id",
            "100",
            "--person-id",
            "42",
            "--include-me",
            "--content",
            "Dedup test",
            "--date",
            "2024-01-01T00:00:00Z",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0

    # Verify person_ids has no duplicates
    post_call = respx_mock.calls[-1]
    body = json.loads(post_call.request.content)
    assert body["person_ids"].count(100) == 1
