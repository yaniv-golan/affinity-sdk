"""CLI read paths warn when Affinity cut a multi-value field off at 100 values (``totalCount``)."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.field_utils import truncation_warnings
from affinity.cli.main import cli
from affinity.cli.query.executor import _normalize_list_entry_fields

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)


def _truncated_field(fid: str = "field-1", name: str = "Investors", n: int = 100, total: int = 250):
    return {
        "id": fid,
        "name": name,
        "type": "global",
        "value": {
            "type": "company-multi",
            "data": [{"id": i} for i in range(n)],
            "totalCount": total,
        },
    }


class TestTruncationWarningsHelper:
    def test_single_field_message(self) -> None:
        msgs = truncation_warnings([("company 'Acme'", [_truncated_field()])])
        assert msgs == [
            "Field 'Investors' on company 'Acme' shows 100 of 250 values "
            "(Affinity returns at most 100 values per field)."
        ]

    def test_nothing_truncated(self) -> None:
        not_cut = _truncated_field(n=3, total=3)
        assert truncation_warnings([("x", [not_cut]), ("y", None), ("z", {})]) == []

    def test_summary_for_many_records_and_filter_note(self) -> None:
        records = [(f"entry {i}", [_truncated_field()]) for i in range(5)]
        (msg,) = truncation_warnings(records, filtered=True)
        assert msg.startswith("5 multi-value field(s) on 5 record(s) were cut off")
        assert "and 2 more" in msg
        assert "Filters on those fields were evaluated on the returned values only." in msg

    def test_accepts_dict_keyed_by_field_id(self) -> None:
        fields = {"field-1": _truncated_field()}
        assert len(truncation_warnings([("x", fields)])) == 1


def test_company_get_all_fields_warns_in_json(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/companies/123(\?.*)?$").mock(
        return_value=Response(
            200,
            json={
                "id": 123,
                "name": "Acme Corp",
                "domain": "acme.com",
                "domains": ["acme.com"],
                "fields": [_truncated_field()],
            },
        )
    )
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/companies/fields.*").mock(
        return_value=Response(200, json={"data": [], "pagination": {"nextUrl": None}})
    )
    result = CliRunner().invoke(
        cli,
        ["--json", "company", "get", "123", "--all-fields"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert any("shows 100 of 250 values" in w for w in payload["warnings"]), payload["warnings"]
    # The data itself is unchanged.
    assert payload["data"]["company"]["fields"][0]["value"]["totalCount"] == 250


def test_query_normalization_records_truncation_and_json_carries_warnings() -> None:
    record: dict[str, Any] = {
        "id": 7,
        "entity": {
            "id": 1,
            "name": "Acme",
            "fields": {"requested": True, "data": {"field-1": _truncated_field()}},
        },
    }
    seen: list[tuple[str, Any]] = []
    normalized = _normalize_list_entry_fields(record, seen)
    assert len(normalized["fields"]["Investors"]) == 100
    assert truncation_warnings(seen) == [
        "Field 'Investors' on 'Acme' shows 100 of 250 values "
        "(Affinity returns at most 100 values per field)."
    ]

    from affinity.cli.query.models import QueryResult
    from affinity.cli.query.output import format_json

    out = json.loads(format_json(QueryResult(data=[], warnings=["w1"]), include_meta=False))
    assert out["warnings"] == ["w1"]
    assert "warnings" not in json.loads(format_json(QueryResult(data=[]), include_meta=False))


def _mock_list_export(respx_mock: respx.MockRouter) -> None:
    lst = {"id": 10, "name": "Pipeline", "type": 0, "public": False, "owner_id": 1, "creator_id": 1}
    respx_mock.get("https://api.affinity.co/v2/lists/10").mock(return_value=Response(200, json=lst))
    respx_mock.get("https://api.affinity.co/lists/10").mock(return_value=Response(200, json=lst))
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/lists/10/fields.*").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "field-1",
                        "name": "Investors",
                        "type": "list",
                        "valueType": "company-multi",
                    }
                ],
                "pagination": {"nextUrl": None},
            },
        )
    )
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Investors",
                    "list_id": 10,
                    "value_type": 1,
                    "allows_multiple": True,
                }
            ],
        )
    )
    entry = {
        "id": 77,
        "listId": 10,
        "type": "company",
        "createdAt": "2026-01-01T00:00:00Z",
        "creatorId": 1,
        "entity": {"id": 5, "name": "Acme", "domain": "acme.com", "fields": [_truncated_field()]},
    }
    respx_mock.get(url__regex=r"https://api\.affinity\.co/v2/lists/10/list-entries.*").mock(
        return_value=Response(200, json={"data": [entry], "pagination": {"nextUrl": None}})
    )


def test_list_export_json_warns(respx_mock: respx.MockRouter) -> None:
    _mock_list_export(respx_mock)
    result = CliRunner().invoke(
        cli,
        ["--readonly", "--json", "list", "export", "10", "--all", "--field", "Investors"],
        env={"AFFINITY_API_KEY": "test"},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert any("'Investors' on 'Acme' shows 100 of 250" in w for w in payload["warnings"]), payload


def test_list_export_csv_warns_on_stderr(respx_mock: respx.MockRouter) -> None:
    _mock_list_export(respx_mock)
    result = CliRunner().invoke(
        cli,
        ["--readonly", "list", "export", "10", "--all", "--field", "Investors", "--csv"],
        env={"AFFINITY_API_KEY": "test"},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "shows 100 of 250 values" in result.stderr
    assert "shows 100 of 250" not in result.stdout
