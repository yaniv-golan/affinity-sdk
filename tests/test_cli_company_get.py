from __future__ import annotations

import json
import re

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


def test_company_get_by_id_minimal(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "company", "get", "123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    assert payload["command"]["name"] == "company get"
    assert payload["data"]["company"]["id"] == 123
    assert payload["meta"]["resolved"]["company"]["source"] == "id"


def test_company_get_accepts_affinity_dot_com_url(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "company", "get", "https://mydomain.affinity.com/companies/123"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    assert payload["meta"]["resolved"]["company"]["source"] == "url"
    assert payload["meta"]["resolved"]["company"]["companyId"] == 123


def test_company_get_field_union_uses_field_ids(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "dealroom-url",
                        "name": "Dealroom.co URL",
                        "type": "enriched",
                        "valueType": "text",
                        "allowsMultiple": False,
                    },
                    {
                        "id": "field-1",
                        "name": "Stage",
                        "type": "global",
                        "valueType": "dropdown",
                        "allowsMultiple": False,
                    },
                ]
            },
        )
    )

    route = respx_mock.get(
        "https://api.affinity.co/v2/companies/123?fieldIds=dealroom-url&fieldIds=field-1"
    ).mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Acme Corp",
                "domain": "acme.com",
                "domains": ["acme.com"],
                "fields": [
                    {
                        "id": "dealroom-url",
                        "type": "enriched",
                        "name": "Dealroom.co URL",
                        "value": {"type": "text", "data": "https://dealroom.co/acme"},
                    },
                    {
                        "id": "field-1",
                        "type": "global",
                        "name": "Stage",
                        "value": {"type": "dropdown", "data": {"id": 1, "text": "Seed"}},
                    },
                ],
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "company",
            "get",
            "123",
            "--field",
            "Dealroom.co URL",
            "--field-type",
            "global",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    selection = payload["meta"]["resolved"]["fieldSelection"]
    assert "fieldIds" in selection
    assert selection["fieldTypes"] == ["global"]

    assert route.called
    req = route.calls[0].request
    field_ids = req.url.params.get_list("fieldIds")
    assert "dealroom-url" in field_ids
    assert "field-1" in field_ids
    assert req.url.params.get_list("fieldTypes") == []


def test_company_get_expand_list_entries_filtered_by_list_id(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=100").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": 1, "listId": 10, "createdAt": "2020-01-01T00:00:00Z", "fields": {}},
                    {"id": 2, "listId": 11, "createdAt": "2020-01-01T00:00:00Z", "fields": {}},
                ]
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "company", "get", "123", "--expand", "list-entries", "--list", "10"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    entries = payload["data"]["listEntries"]
    assert [e["id"] for e in entries] == [1]
    assert payload["meta"]["resolved"]["list"]["listId"] == 10


def test_company_get_expand_persons_v1(respx_mock: respx.MockRouter) -> None:
    """Test company get with --expand persons uses V2 batch lookup."""
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )
    # V1: Get person IDs from company
    respx_mock.get("https://api.affinity.co/organizations/123").mock(
        return_value=Response(200, json={"id": 123, "person_ids": [11, 22]})
    )
    # V2: Batch lookup persons
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/persons\?ids=.*").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 11,
                        "firstName": "Ada",
                        "lastName": "Lovelace",
                        "primaryEmailAddress": "ada@example.com",
                        "type": "external",
                    },
                    {
                        "id": 22,
                        "firstName": "Alan",
                        "lastName": "Turing",
                        "primaryEmailAddress": "alan@example.com",
                        "type": "internal",
                    },
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "company", "get", "123", "--expand", "persons"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    assert payload["data"]["persons"] == [
        {
            "id": 11,
            "name": "Ada Lovelace",
            "primaryEmail": "ada@example.com",
            "type": "external",
        },
        {
            "id": 22,
            "name": "Alan Turing",
            "primaryEmail": "alan@example.com",
            "type": "internal",
        },
    ]
    assert "persons" in payload["meta"]["resolved"]["expand"]


def test_company_get_list_filter_auto_implies_expand(respx_mock: respx.MockRouter) -> None:
    """Test that --list auto-implies --expand list-entries for better DX."""
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Acme Corp"})
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=100").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": 1, "listId": 10, "creatorId": 1, "createdAt": "2024-01-01T00:00:00Z"}
                ],
                "pagination": {"prevUrl": None, "nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "company", "get", "123", "--list", "10"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    # Verify list-entries was auto-expanded
    assert "list-entries" in payload["meta"]["resolved"]["expand"]


def test_company_get_human_output_hides_envelope_pagination(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/lists?limit=100").mock(
        return_value=Response(
            200,
            json={
                "data": [{"id": 1, "name": "Dealflow", "isPublic": False}],
                "pagination": {"prevUrl": None, "nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["company", "get", "123", "--expand", "lists"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    assert "Acme Corp" in result.output
    assert "Dealflow" in result.output
    assert "pagination" not in result.output
    assert "nextUrl" not in result.output


def test_company_get_human_output_shows_fields_when_requested(respx_mock: respx.MockRouter) -> None:
    url_re = re.compile(r"https://api\.affinity\.co/v2/companies/123(\?.*)?$")
    respx_mock.get(url_re).mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Acme Corp",
                "domain": "acme.com",
                "fields": [
                    {
                        "id": "dealroom-year-founded",
                        "type": "enriched",
                        "name": "Year Founded",
                        "value": {"type": "number", "data": 2019.0},
                    }
                ],
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["company", "get", "123", "--all-fields"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    assert "Company" in result.output
    assert "Fields" in result.output
    assert "Year Founded" in result.output
    assert "2019" in result.output


def test_company_get_human_output_list_entries_default_is_count_only(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=100").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 135563331,
                        "listId": 41780,
                        "creatorId": 116834779,
                        "createdAt": "2023-08-06T11:39:40Z",
                        "fields": [
                            {
                                "id": "source-of-introduction",
                                "type": "relationship-intelligence",
                                "enrichmentSource": None,
                                "name": "Source of Introduction",
                                "value": {
                                    "type": "person",
                                    "data": {
                                        "id": 26229794,
                                        "firstName": "Yaniv",
                                        "lastName": "Golan",
                                        "primaryEmailAddress": "yaniv@example.com",
                                        "type": "internal",
                                    },
                                },
                            },
                            {
                                "id": "affinity-data-number-of-employees",
                                "type": "enriched",
                                "enrichmentSource": "affinity-data",
                                "name": "Number of Employees",
                                "value": {"type": "number", "data": 15.0},
                            },
                        ],
                    }
                ],
                "pagination": {"prevUrl": None, "nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["company", "get", "123", "--expand", "list-entries"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    assert "List Entries" in result.output
    assert "fieldsCount" in result.output
    assert "Source of Introduction" not in result.output
    assert "{'id':" not in result.output


def test_company_get_human_output_show_list_entry_fields_requires_max_results(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Acme Corp"})
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=100").mock(
        return_value=Response(200, json={"data": []})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["company", "get", "123", "--expand", "list-entries", "--show-list-entry-fields"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2


def test_company_get_human_output_show_list_entry_fields_renders_fields_tables(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )
    respx_mock.get("https://api.affinity.co/v2/lists/41780").mock(
        return_value=Response(
            200,
            json={
                "id": 41780,
                "name": "Dealflow",
                "type": "company",
                "isPublic": False,
                "ownerId": 1,
                "creatorId": 1,
                "listSize": 0,
            },
        )
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=1").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 135563331,
                        "listId": 41780,
                        "creatorId": 116834779,
                        "createdAt": "2023-08-06T11:39:40Z",
                        "fields": [
                            {
                                "id": "field-1",
                                "type": "list",
                                "enrichmentSource": None,
                                "name": "Status",
                                "value": {
                                    "type": "ranked-dropdown",
                                    "data": {
                                        "dropdownOptionId": 1,
                                        "text": "Intro Meeting",
                                        "rank": 2,
                                        "color": "orange",
                                    },
                                },
                            },
                            {
                                "id": "source-of-introduction",
                                "type": "relationship-intelligence",
                                "enrichmentSource": None,
                                "name": "Source of Introduction",
                                "value": {
                                    "type": "person",
                                    "data": {
                                        "id": 26229794,
                                        "firstName": "Yaniv",
                                        "lastName": "Golan",
                                        "primaryEmailAddress": "yaniv@example.com",
                                        "type": "internal",
                                    },
                                },
                            },
                        ],
                        "pagination": {"prevUrl": None, "nextUrl": None},
                    }
                ],
                "pagination": {"prevUrl": None, "nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "company",
            "get",
            "123",
            "--expand",
            "list-entries",
            "--max-results",
            "1",
            "--show-list-entry-fields",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    assert "List Entries" in result.output
    assert "fieldsCount" in result.output
    normalized = re.sub(r"[│┃\s]+", " ", result.output)
    assert "List Entry 135563331" in normalized
    assert "Status" in normalized
    assert "Intro Meeting" in normalized
    assert "Some non-list fields hidden" in result.output


def test_company_get_show_list_entry_fields_scope_hint_suppressed_when_all_list(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Acme Corp"})
    )
    respx_mock.get("https://api.affinity.co/v2/lists/41780").mock(
        return_value=Response(
            200,
            json={
                "id": 41780,
                "name": "Dealflow",
                "type": "company",
                "isPublic": False,
                "ownerId": 1,
                "creatorId": 1,
                "listSize": 0,
            },
        )
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=1").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 135563331,
                        "listId": 41780,
                        "creatorId": 116834779,
                        "createdAt": "2023-08-06T11:39:40Z",
                        "fields": [
                            {
                                "id": "field-1",
                                "type": "list",
                                "enrichmentSource": None,
                                "name": "Status",
                                "value": {"type": "text", "data": "Active"},
                            }
                        ],
                    }
                ],
                "pagination": {"prevUrl": None, "nextUrl": None},
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "company",
            "get",
            "123",
            "--expand",
            "list-entries",
            "--max-results",
            "1",
            "--show-list-entry-fields",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0
    assert "Some non-list fields hidden" not in result.output


def test_company_get_list_entry_fields_scope_requires_show(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Acme Corp"})
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=100").mock(
        return_value=Response(200, json={"data": []})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "company",
            "get",
            "123",
            "--expand",
            "list-entries",
            "--list-entry-fields-scope",
            "all",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2


def test_company_get_human_output_list_entry_field_requires_list_for_names(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Acme Corp"})
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=100").mock(
        return_value=Response(200, json={"data": []})
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "company",
            "get",
            "123",
            "--expand",
            "list-entries",
            "--list-entry-field",
            "Stage Name",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2


def test_company_get_default_reports_not_requested(respx_mock: respx.MockRouter) -> None:
    """Plain get fetches no fields/list entries; meta.notRequested must say so."""
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )

    result = CliRunner().invoke(
        cli, ["--json", "company", "get", "123"], env={"AFFINITY_API_KEY": "test-key"}
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["meta"]["notRequested"] == [
        {"key": "data.company.fields", "flag": "--all-fields"},
        {"key": "data.listEntries", "flag": "--expand list-entries"},
    ]
    assert payload["warnings"] == []


def test_company_get_omits_not_requested_when_all_requested(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/companies/123(\?.*)?$").mock(
        return_value=Response(
            200,
            json={"id": 123, "name": "Acme Corp", "domain": "acme.com", "domains": ["acme.com"]},
        )
    )
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/companies/fields.*").mock(
        return_value=Response(200, json={"data": [], "pagination": {"nextUrl": None}})
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=100").mock(
        return_value=Response(200, json={"data": []})
    )

    result = CliRunner().invoke(
        cli,
        ["--json", "company", "get", "123", "--all-fields", "--expand", "list-entries"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert "notRequested" not in payload["meta"]


def test_company_get_list_entry_fields_use_list_name_without_lookup(
    respx_mock: respx.MockRouter,
) -> None:
    """V2 list entries carry `listName`; the table uses it instead of one `lists.get` per list."""
    respx_mock.get("https://api.affinity.co/v2/companies/123").mock(
        return_value=Response(200, json={"id": 123, "name": "Acme Corp"})
    )
    list_lookup = respx_mock.get("https://api.affinity.co/v2/lists/41780").mock(
        return_value=Response(500)
    )
    respx_mock.get("https://api.affinity.co/v2/companies/123/list-entries?limit=1").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": 135563331,
                        "listId": 41780,
                        "listName": "Dealflow",
                        "creatorId": 1,
                        "createdAt": "2023-08-06T11:39:40Z",
                        "fields": [
                            {
                                "id": "field-1",
                                "type": "list",
                                "name": "Status",
                                "value": {"type": "text", "data": "Intro Meeting"},
                            }
                        ],
                    }
                ],
                "pagination": {"prevUrl": None, "nextUrl": None},
            },
        )
    )
    result = CliRunner().invoke(
        cli,
        [
            "company",
            "get",
            "123",
            "--expand",
            "list-entries",
            "--max-results",
            "1",
            "--show-list-entry-fields",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    assert "Dealflow" in result.output
    assert list_lookup.call_count == 0
