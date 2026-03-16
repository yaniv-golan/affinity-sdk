from __future__ import annotations

import json
from pathlib import Path

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


def test_opportunity_ls_minimal(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/opportunities").mock(
        return_value=Response(
            200,
            json={
                "data": [{"id": 10, "name": "Seed", "listId": 41780}],
                "pagination": {
                    "nextUrl": "https://api.affinity.co/v2/opportunities?cursor=next",
                    "prevUrl": None,
                },
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "ls"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["opportunities"][0]["id"] == 10
    assert payload["meta"]["pagination"]["opportunities"]["nextCursor"].endswith("cursor=next")


def test_opportunity_get_by_id_minimal(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Series A", "listId": 41780})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "get", "123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["opportunity"]["id"] == 123
    assert payload["meta"]["resolved"]["opportunity"]["source"] == "id"


def test_opportunity_get_accepts_affinity_dot_com_url(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Series A", "listId": 41780})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "get", "https://mydomain.affinity.com/opportunities/123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["meta"]["resolved"]["opportunity"]["source"] == "url"
    assert payload["meta"]["resolved"]["opportunity"]["opportunityId"] == 123


def test_opportunity_get_details_uses_v1(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/opportunities/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Series A",
                "list_id": 41780,
                "person_ids": [1],
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "get", "123", "--details"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["opportunity"]["listId"] == 41780
    assert payload["data"]["opportunity"]["personIds"] == [1]


def test_opportunity_create_update_delete(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/lists/41780").mock(
        return_value=Response(
            200,
            json={
                "id": 41780,
                "name": "Dealflow",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 0,
            },
        )
    )
    respx_mock.post("https://api.affinity.co/opportunities").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Seed", "list_id": 41780},
        )
    )
    respx_mock.put("https://api.affinity.co/opportunities/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Seed (Updated)", "list_id": 41780},
        )
    )
    respx_mock.delete("https://api.affinity.co/opportunities/123").mock(
        return_value=Response(200, json={"success": True})
    )

    runner = CliRunner()

    created = runner.invoke(
        cli,
        ["--json", "opportunity", "create", "--name", "Seed", "--list", "41780"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert created.exit_code == 0
    created_payload = json.loads(created.output.strip())
    assert created_payload["data"]["opportunity"]["id"] == 123

    updated = runner.invoke(
        cli,
        ["--json", "opportunity", "update", "123", "--name", "Seed (Updated)"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert updated.exit_code == 0
    updated_payload = json.loads(updated.output.strip())
    assert updated_payload["data"]["opportunity"]["name"] == "Seed (Updated)"

    deleted = runner.invoke(
        cli,
        ["--json", "opportunity", "delete", "123", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert deleted.exit_code == 0
    deleted_payload = json.loads(deleted.output.strip())
    assert deleted_payload["data"]["success"] is True


def test_opportunity_get_expand_persons(respx_mock: respx.MockRouter) -> None:
    """Test --expand persons fetches associated persons via V2 batch API."""
    # V2 get for opportunity
    respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Series A", "listId": 41780})
    )
    # V1 get for person_ids
    respx_mock.get("https://api.affinity.co/opportunities/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Series A",
                "list_id": 41780,
                "person_ids": [1001, 1002],
                "organization_ids": [],
            },
        )
    )
    # V2 batch get for persons
    respx_mock.route(method="GET", url__startswith="https://api.affinity.co/v2/persons?ids=").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1001,
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "emails": ["alice@example.com"],
                        "type": "external",
                    },
                    {
                        "id": 1002,
                        "firstName": "Bob",
                        "lastName": "Jones",
                        "emails": ["bob@example.com"],
                        "type": "external",
                    },
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "get", "123", "--expand", "persons"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["opportunity"]["id"] == 123
    assert len(payload["data"]["persons"]) == 2
    assert payload["data"]["persons"][0]["id"] == 1001
    assert payload["data"]["persons"][0]["name"] == "Alice Smith"
    assert payload["data"]["persons"][1]["id"] == 1002
    assert payload["meta"]["resolved"]["expand"] == ["persons"]


def test_opportunity_get_expand_companies(respx_mock: respx.MockRouter) -> None:
    """Test --expand companies fetches associated companies via V2 batch API."""
    # V2 get for opportunity
    respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Series A", "listId": 41780})
    )
    # V1 get for company_ids
    respx_mock.get("https://api.affinity.co/opportunities/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Series A",
                "list_id": 41780,
                "person_ids": [],
                "organization_ids": [2001],
            },
        )
    )
    # V2 batch get for companies
    respx_mock.route(
        method="GET", url__startswith="https://api.affinity.co/v2/companies?ids="
    ).mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": 2001, "name": "Acme Corp", "domain": "acme.com"},
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "get", "123", "--expand", "companies"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["opportunity"]["id"] == 123
    assert len(payload["data"]["companies"]) == 1
    assert payload["data"]["companies"][0]["id"] == 2001
    assert payload["data"]["companies"][0]["name"] == "Acme Corp"
    assert payload["data"]["companies"][0]["domain"] == "acme.com"
    assert payload["meta"]["resolved"]["expand"] == ["companies"]


def test_opportunity_get_expand_both(respx_mock: respx.MockRouter) -> None:
    """Test --expand persons --expand companies fetches both via V2 batch API."""
    # V2 get for opportunity
    respx_mock.get("https://api.affinity.co/v2/opportunities/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Series A", "listId": 41780})
    )
    # V1 get for associations (called once - get_associations() fetches both)
    respx_mock.get("https://api.affinity.co/opportunities/123").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Series A",
                "list_id": 41780,
                "person_ids": [1001],
                "organization_ids": [2001],
            },
        )
    )
    # V2 batch get for persons
    respx_mock.route(method="GET", url__startswith="https://api.affinity.co/v2/persons?ids=").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 1001,
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "emails": ["alice@example.com"],
                        "type": "external",
                    },
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )
    # V2 batch get for companies
    respx_mock.route(
        method="GET", url__startswith="https://api.affinity.co/v2/companies?ids="
    ).mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": 2001, "name": "Acme Corp", "domain": "acme.com"},
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "get", "123", "--expand", "persons", "--expand", "companies"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["data"]["opportunity"]["id"] == 123
    assert len(payload["data"]["persons"]) == 1
    assert len(payload["data"]["companies"]) == 1
    assert payload["meta"]["resolved"]["expand"] == ["companies", "persons"]  # sorted


def test_opportunity_files_download(respx_mock: respx.MockRouter, tmp_path: object) -> None:
    """Test opportunity files download downloads files and creates manifest."""
    out_dir = Path(tmp_path) / "opp-files"  # type: ignore[arg-type]

    # Mock files list endpoint (V1 entity-files API)
    respx_mock.get("https://api.affinity.co/entity-files").mock(
        return_value=Response(
            200,
            json={
                "files": [
                    {
                        "id": 5001,
                        "name": "pitch.pdf",
                        "size": 1024,
                        "content_type": "application/pdf",
                        "uploader_id": 1,
                        "created_at": "2024-01-01T00:00:00Z",
                    }
                ],
                "next_page_token": None,
            },
        )
    )

    # Mock file download endpoint (V1 entity-files download)
    respx_mock.get("https://api.affinity.co/entity-files/download/5001").mock(
        return_value=Response(200, content=b"fake pdf content")
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "opportunity", "files", "download", "123", "--out", str(out_dir)],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    # Output is JSONL: progress lines followed by the result — parse last line
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    assert payload["data"]["filesDownloaded"] == 1
    assert payload["data"]["filesTotal"] == 1

    # Verify manifest was created
    manifest_path = out_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["entity"]["type"] == "opportunity"
    assert manifest["entity"]["opportunityId"] == 123
    assert len(manifest["files"]) == 1
    assert manifest["files"][0]["name"] == "pitch.pdf"

    # Verify file was downloaded
    downloaded_file = out_dir / "files" / "pitch.pdf"
    assert downloaded_file.exists()
    assert downloaded_file.read_bytes() == b"fake pdf content"
