"""`company field` / `person field`: every write of a command in one V2 update-fields PATCH."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

pytest.importorskip("rich_click")
respx = pytest.importorskip("respx")

from click.testing import CliRunner

from affinity.api_versions import AFFINITY_API_VERSION_HEADER
from affinity.cli.main import cli

Seen = list[tuple[str, str, Any, str | None]]


def _page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": None, "prevUrl": None}}


COMPANY_FIELDS = _page(
    [
        {"id": "field-10", "name": "Stage", "type": "global", "valueType": "dropdown"},
        {
            "id": "dealroom-description",
            "name": "Description",
            "type": "enriched",
            "valueType": "text",
        },
        {
            "id": "dealroom-technologies",
            "name": "Technologies",
            "type": "enriched",
            "valueType": "filterable-text-multi",
        },
        {
            "id": "source-of-introduction",
            "name": "Source of Introduction",
            "type": "relationship-intelligence",
            "valueType": "person",
        },
        {
            "id": "first-email",
            "name": "First Email",
            "type": "relationship-intelligence",
            "valueType": "interaction",
        },
    ]
)


def _existing(**values: Any) -> dict[str, Any]:
    return _page([{"id": fid, "value": value} for fid, value in values.items()])


def _run(
    args: list[str],
    *,
    existing: dict[str, Any] | None = None,
    patch: Callable[[httpx.Request], httpx.Response] | None = None,
    env: dict[str, str] | None = None,
    fields: dict[str, Any] = COMPANY_FIELDS,
    entity: str = "companies",
) -> tuple[Any, Seen]:
    seen: Seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, path, body, request.headers.get(AFFINITY_API_VERSION_HEADER)))
        if request.method == "GET" and path == f"/v2/{entity}/fields":
            return httpx.Response(200, json=fields)
        if request.method == "GET" and path.endswith("/fields") and path.count("/") == 4:
            return httpx.Response(200, json=existing or _page([]))
        if request.method == "PATCH":
            if patch is not None:
                return patch(request)
            echo = {
                AFFINITY_API_VERSION_HEADER: request.headers.get(AFFINITY_API_VERSION_HEADER, "")
            }
            return httpx.Response(200, json={"operation": "update-fields"}, headers=echo)
        return httpx.Response(404, json={"errors": [{"message": f"unmocked {path}"}]})

    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.affinity.co").mock(side_effect=handler)
        result = CliRunner().invoke(
            cli, ["--json", *args], env={"AFFINITY_API_KEY": "test", **(env or {})}
        )
    return result, seen


def _patches(seen: Seen) -> list[tuple[str, Any, str | None]]:
    return [(p, b, v) for m, p, b, v in seen if m == "PATCH"]


def test_sets_and_unsets_in_one_patch_at_the_needed_version() -> None:
    result, seen = _run(
        ["company", "field", "5", "--set", "Description", "AI infra", "--unset", "Stage"]
    )
    assert result.exit_code == 0, result.output
    assert _patches(seen) == [
        (
            "/v2/companies/5/fields",
            {
                "operation": "update-fields",
                "updates": [
                    {"id": "dealroom-description", "value": {"type": "text", "data": "AI infra"}},
                    {"id": "field-10", "value": {"type": "dropdown", "data": None}},
                ],
            },
            "2026-07-15",
        )
    ]
    payload = json.loads(result.output)
    assert payload["data"] == {
        "created": [
            {"fieldId": "dealroom-description", "name": "Description", "value": "AI infra"}
        ],
        "cleared": [{"fieldId": "field-10", "name": "Stage"}],
    }
    assert payload["meta"]["affinityApiVersionPerOperation"] == ["2026-07-15"]


def test_unchanged_value_is_not_written() -> None:
    existing = _existing(**{"dealroom-description": {"type": "text", "data": "AI infra"}})
    result, seen = _run(
        ["company", "field", "5", "--set", "Description", "AI infra"], existing=existing
    )
    assert result.exit_code == 0, result.output
    assert _patches(seen) == []


def test_single_value_wrapped_for_multi_text_and_numbers_sent_as_text() -> None:
    result, seen = _run(
        [
            "company",
            "field",
            "5",
            "--set",
            "Technologies",
            "Python",
            "--set-json",
            json.dumps({"Description": 42}),
        ]
    )
    assert result.exit_code == 0, result.output
    (_path, body, _v) = _patches(seen)[0]
    assert body["updates"] == [
        {
            "id": "dealroom-technologies",
            "value": {"type": "filterable-text-multi", "data": ["Python"]},
        },
        {"id": "dealroom-description", "value": {"type": "text", "data": "42"}},
    ]


def test_source_of_introduction_is_written_by_person_id() -> None:
    result, seen = _run(["company", "field", "5", "--set", "Source of Introduction", "77"])
    assert result.exit_code == 0, result.output
    assert _patches(seen)[0][1]["updates"] == [
        {"id": "source-of-introduction", "value": {"type": "person", "data": {"id": 77}}}
    ]


def test_interaction_fields_are_refused_before_any_write() -> None:
    result, seen = _run(["company", "field", "5", "--unset", "First Email"])
    assert result.exit_code == 2, result.output
    assert "doesn't accept writes" in result.output
    assert _patches(seen) == []


def test_rejected_update_says_nothing_changed() -> None:
    def reject(_request: httpx.Request) -> httpx.Response:
        body = {"errors": [{"code": "validation", "message": "Field value is invalid"}]}
        return httpx.Response(400, json=body)

    result, _seen = _run(["company", "field", "5", "--set", "Description", "x"], patch=reject)
    assert result.exit_code == 1, result.output
    error = json.loads(result.output)["error"]
    assert error["type"] == "api_error"
    assert "nothing was changed" in error["message"]


def test_no_response_says_rerunning_is_safe() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result, _seen = _run(["company", "field", "5", "--set", "Description", "x"], patch=timeout)
    assert result.exit_code == 1, result.output
    assert "Re-running the command is safe" in json.loads(result.output)["error"]["message"]


def test_pin_older_than_the_write_endpoint_fails_before_any_request() -> None:
    result, seen = _run(
        ["company", "field", "5", "--set", "Description", "x"],
        env={"AFFINITY_API_VERSION": "2024-01-01"},
    )
    assert result.exit_code == 2, result.output
    error = json.loads(result.output)["error"]
    assert error["type"] == "api_version_error"
    assert error["details"]["requiredApiVersion"] == "2026-07-15"
    assert seen == []


@pytest.mark.parametrize(
    "args",
    [
        ["--set", "Description", "a", "--set", "description", "b"],
        ["--set", "Description", "a", "--set", "dealroom-description", "b"],
        ["--set", "Description", "a", "--unset", "dealroom-description"],
    ],
)
def test_one_field_twice_is_refused(args: list[str]) -> None:
    result, seen = _run(["company", "field", "5", *args])
    assert result.exit_code == 2, result.output
    assert _patches(seen) == []


def test_more_than_100_fields_is_refused() -> None:
    fields = _page(
        [
            {"id": f"field-{i}", "name": f"F{i}", "type": "global", "valueType": "text"}
            for i in range(101)
        ]
    )
    values = {f"F{i}": "x" for i in range(101)}
    result, seen = _run(["company", "field", "5", "--set-json", json.dumps(values)], fields=fields)
    assert result.exit_code == 2, result.output
    assert "at most 100" in result.output
    assert _patches(seen) == []


def test_get_reads_v2_values_including_enriched() -> None:
    existing = _existing(
        **{"dealroom-technologies": {"type": "filterable-text-multi", "data": ["Go"]}}
    )
    result, seen = _run(["company", "field", "5", "--get", "Technologies"], existing=existing)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"] == {"fields": {"Technologies": ["Go"]}}
    assert ("GET", "/v2/companies/5/fields") in [(m, p) for m, p, _b, _v in seen]


def test_person_field_uses_person_endpoints() -> None:
    fields = _page(
        [
            {
                "id": "affinity-data-current-organization",
                "name": "Current Organization",
                "type": "enriched",
                "valueType": "company",
            }
        ]
    )
    result, seen = _run(
        ["person", "field", "8", "--set", "Current Organization", "314"],
        fields=fields,
        entity="persons",
    )
    assert result.exit_code == 0, result.output
    assert _patches(seen) == [
        (
            "/v2/persons/8/fields",
            {
                "operation": "update-fields",
                "updates": [
                    {
                        "id": "affinity-data-current-organization",
                        "value": {"type": "company", "data": {"id": 314}},
                    }
                ],
            },
            "2026-07-15",
        )
    ]
