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


def test_person_crud_and_merge(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("https://api.affinity.co/persons").mock(
        return_value=Response(
            200,
            json={
                "id": 1,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "emails": ["ada@example.com"],
            },
        )
    )
    respx_mock.put("https://api.affinity.co/persons/1").mock(
        return_value=Response(
            200,
            json={
                "id": 1,
                "first_name": "Ada",
                "last_name": "Byron",
                "emails": ["ada@example.com"],
            },
        )
    )
    respx_mock.delete("https://api.affinity.co/persons/1").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.post("https://api.affinity.co/v2/person-merges").mock(
        return_value=Response(
            200, json={"taskUrl": "https://api.affinity.co/tasks/person-merges/123"}
        )
    )

    runner = CliRunner()
    created = runner.invoke(
        cli,
        [
            "--json",
            "person",
            "create",
            "--allow-duplicate",
            "--first-name",
            "Ada",
            "--last-name",
            "Lovelace",
            "--email",
            "ada@example.com",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert created.exit_code == 0
    created_payload = json.loads(created.output.strip())
    assert created_payload["data"]["person"]["id"] == 1

    updated = runner.invoke(
        cli,
        ["--json", "person", "update", "1", "--last-name", "Byron"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert updated.exit_code == 0
    updated_payload = json.loads(updated.output.strip())
    assert updated_payload["data"]["person"]["lastName"] == "Byron"

    deleted = runner.invoke(
        cli,
        ["--json", "person", "delete", "1", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert deleted.exit_code == 0
    deleted_payload = json.loads(deleted.output.strip())
    assert deleted_payload["data"]["success"] is True

    merged = runner.invoke(
        cli,
        ["--json", "--beta", "person", "merge", "1", "2"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert merged.exit_code == 0
    merged_payload = json.loads(merged.output.strip())
    assert merged_payload["data"]["taskUrl"].endswith("/tasks/person-merges/123")


def test_company_crud_and_merge(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("https://api.affinity.co/organizations").mock(
        return_value=Response(
            200,
            json={
                "id": 224925494,
                "name": "Acme Corp",
                "domain": "acme.com",
            },
        )
    )
    respx_mock.put("https://api.affinity.co/organizations/224925494").mock(
        return_value=Response(
            200,
            json={
                "id": 224925494,
                "name": "Acme Corp",
                "domain": "acme.co",
            },
        )
    )
    respx_mock.delete("https://api.affinity.co/organizations/224925494").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.post("https://api.affinity.co/v2/company-merges").mock(
        return_value=Response(
            200, json={"taskUrl": "https://api.affinity.co/tasks/company-merges/456"}
        )
    )

    runner = CliRunner()
    created = runner.invoke(
        cli,
        [
            "--json",
            "company",
            "create",
            "--allow-duplicate",
            "--name",
            "Acme Corp",
            "--domain",
            "acme.com",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert created.exit_code == 0
    created_payload = json.loads(created.output.strip())
    assert created_payload["data"]["company"]["id"] == 224925494

    updated = runner.invoke(
        cli,
        ["--json", "company", "update", "224925494", "--domain", "acme.co"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert updated.exit_code == 0
    updated_payload = json.loads(updated.output.strip())
    assert updated_payload["data"]["company"]["domain"] == "acme.co"

    deleted = runner.invoke(
        cli,
        ["--json", "company", "delete", "224925494", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert deleted.exit_code == 0
    deleted_payload = json.loads(deleted.output.strip())
    assert deleted_payload["data"]["success"] is True

    merged = runner.invoke(
        cli,
        ["--json", "--beta", "company", "merge", "1", "2"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert merged.exit_code == 0
    merged_payload = json.loads(merged.output.strip())
    assert merged_payload["data"]["taskUrl"].endswith("/tasks/company-merges/456")


def test_list_create_and_entry_ops(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("https://api.affinity.co/lists").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Dealflow",
                "type": 1,
                "is_public": False,
                "ownerId": 42,
            },
        )
    )
    respx_mock.get("https://api.affinity.co/lists/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Dealflow",
                "type": 1,
                "public": False,
                "owner_id": 42,
            },
        )
    )
    respx_mock.post("https://api.affinity.co/lists/123/list-entries").mock(
        return_value=Response(
            200,
            json={
                "id": 98765,
                "listId": 123,
                "entityId": 224925494,
                "entityType": 1,
                "createdAt": "2024-01-01T00:00:00Z",
            },
        )
    )
    respx_mock.delete("https://api.affinity.co/lists/123/list-entries/98765").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.post("https://api.affinity.co/v2/lists/123/list-entries/98765/fields/field-1").mock(
        return_value=Response(200, json={"field-1": "Active"})
    )
    respx_mock.patch("https://api.affinity.co/v2/lists/123/list-entries/98765/fields").mock(
        return_value=Response(
            200,
            json={
                "results": [{"fieldId": "field-1", "success": True}],
            },
        )
    )
    # Mock for field metadata fetch (used by set-field/set-fields) - V2 API
    respx_mock.get("https://api.affinity.co/v2/lists/123/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "field-1",
                        "name": "Status",
                        "valueType": "text",
                        "allowsMultiple": False,
                        "listId": 123,
                    }
                ]
            },
        )
    )
    # Mock fields (V1) - list_fields_for_list fetches from V1 for dropdown_options
    respx_mock.get(url__regex=r"https://api\.affinity\.co/fields(\?.*)?$").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "field-1",
                        "name": "Status",
                        "value_type": 6,
                        "allows_multiple": False,
                        "list_id": 123,
                    }
                ]
            },
        )
    )
    # Mock for existing field values check (used by set-field)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    runner = CliRunner()
    created = runner.invoke(
        cli,
        ["--json", "list", "create", "--name", "Dealflow", "--type", "company"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert created.exit_code == 0
    created_payload = json.loads(created.output.strip())
    assert created_payload["data"]["list"]["id"] == 123

    added = runner.invoke(
        cli,
        ["--json", "list", "entry", "add", "123", "--company-id", "224925494"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert added.exit_code == 0
    added_payload = json.loads(added.output.strip())
    assert added_payload["data"]["listEntry"]["id"] == 98765

    # Test the unified 'entry field' command (replaced set-field/set-fields)
    updated_field = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "entry",
            "field",
            "123",
            "98765",
            "--set",
            "field-1",
            "Active",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert updated_field.exit_code == 0
    updated_payload = json.loads(updated_field.output.strip())
    assert "created" in updated_payload["data"]

    # Test --set-json for batch updates
    batch_updated = runner.invoke(
        cli,
        [
            "--json",
            "list",
            "entry",
            "field",
            "123",
            "98765",
            "--set-json",
            '{"field-1": "Active"}',
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert batch_updated.exit_code == 0
    batch_payload = json.loads(batch_updated.output.strip())
    assert "created" in batch_payload["data"]

    deleted = runner.invoke(
        cli,
        ["--json", "list", "entry", "delete", "123", "98765", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert deleted.exit_code == 0
    deleted_payload = json.loads(deleted.output.strip())
    assert deleted_payload["data"]["success"] is True


def test_field_and_field_value_ops(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "field-123",
                        "name": "Stage",
                        "value_type": 6,
                        "entity_type": 8,
                    }
                ]
            },
        )
    )
    respx_mock.post("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json={
                "id": "field-123",
                "name": "Stage",
                "value_type": 6,
                "entity_type": 8,
            },
        )
    )
    respx_mock.delete("https://api.affinity.co/fields/123").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()
    field_ls = runner.invoke(
        cli,
        ["--json", "field", "ls", "--entity-type", "opportunity"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert field_ls.exit_code == 0
    field_ls_payload = json.loads(field_ls.output.strip())
    assert field_ls_payload["data"]["fields"][0]["id"] == "field-123"

    field_create = runner.invoke(
        cli,
        [
            "--json",
            "field",
            "create",
            "--name",
            "Stage",
            "--entity-type",
            "opportunity",
            "--value-type",
            "dropdown",
            "--list-specific",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert field_create.exit_code == 0
    field_create_payload = json.loads(field_create.output.strip())
    assert field_create_payload["data"]["field"]["name"] == "Stage"

    field_delete = runner.invoke(
        cli,
        ["--json", "field", "delete", "field-123", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert field_delete.exit_code == 0
    field_delete_payload = json.loads(field_delete.output.strip())
    assert field_delete_payload["data"]["success"] is True


def test_relationship_strength_and_task(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/relationships-strengths").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"internalId": 7, "externalId": 42, "strength": 0.8},
                ]
            },
        )
    )
    respx_mock.get("https://api.affinity.co/tasks/person-merges/123").mock(
        return_value=Response(
            200,
            json={"id": "123", "status": "success"},
        )
    )

    runner = CliRunner()
    strengths = runner.invoke(
        cli,
        ["--json", "relationship-strength", "ls", "--external-id", "42"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert strengths.exit_code == 0
    strengths_payload = json.loads(strengths.output.strip())
    assert strengths_payload["data"]["relationshipStrengths"][0]["strength"] == 0.8

    task_get = runner.invoke(
        cli,
        ["--json", "task", "get", "https://api.affinity.co/tasks/person-merges/123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert task_get.exit_code == 0
    task_payload = json.loads(task_get.output.strip())
    assert task_payload["data"]["task"]["status"] == "success"
