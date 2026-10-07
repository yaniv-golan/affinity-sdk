"""C4: field value/field types the API returns, and the CLI choices built from them.

- New enum members (V2 2026-07-15 / 2026-09-17): ``FieldType.HIDDEN`` and ``FieldValueType``
  ``formula-number``, ``list-multi``, ``note``, ``reminder``.
- CLI choices must not grow with the enums: ``--field-type`` on company/person accepts only
  global/enriched/relationship-intelligence, and ``field create --value-type`` only what V1
  ``POST /fields`` can create (``-multi`` variants force ``allows_multiple``).
- Model gaps against the spec: ``creatorId`` on ``ListSummary``/``ListEntryWithEntity``,
  ``isPublic`` read via alias choices, ``FieldMetadata.description``/``isRequired``.
"""

from __future__ import annotations

import json
from typing import Any

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

from affinity.cli.commands.field_cmds import field_create
from affinity.cli.main import cli
from affinity.cli.render import _table_from_rows
from affinity.models.entities import (
    AffinityList,
    FieldMetadata,
    ListEntryWithEntity,
    ListSummary,
)
from affinity.models.types import FieldType, FieldValueType, to_v1_value_type_code

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

ENV = {"AFFINITY_API_KEY": "test-key"}
CREATE_ARGS = ["--json", "field", "create", "--name", "X", "--entity-type", "company"]

CREATABLE = {
    "person",
    "person-multi",
    "company",
    "company-multi",
    "dropdown",
    "dropdown-multi",
    "number",
    "number-multi",
    "datetime",
    "location",
    "location-multi",
    "text",
    "ranked-dropdown",
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "member"),
    [
        ("formula-number", "FORMULA_NUMBER"),
        ("list-multi", "LIST_MULTI"),
        ("note", "NOTE"),
        ("reminder", "REMINDER"),
    ],
)
def test_new_field_value_types_are_named_members(raw: str, member: str) -> None:
    value_type = FieldValueType(raw)
    assert value_type is getattr(FieldValueType, member)
    assert value_type.name == member
    # None of them has a V1 code: they cannot be created through V1 POST /fields.
    assert to_v1_value_type_code(value_type=value_type) is None


def test_field_type_hidden_is_a_named_member() -> None:
    assert FieldType("hidden") is FieldType.HIDDEN


def test_field_metadata_parses_new_value_types_and_hidden() -> None:
    meta = FieldMetadata.model_validate(
        {"id": "field-9", "name": "Notes", "valueType": "note", "type": "hidden"}
    )
    assert meta.value_type is FieldValueType.NOTE
    assert meta.type == "hidden"


# ---------------------------------------------------------------------------
# --field-type on company/person ls and get
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entity", ["company", "person"])
@pytest.mark.parametrize("bad", ["list", "hidden", "LIST"])
def test_ls_field_type_rejects_non_entity_types(entity: str, bad: str) -> None:
    result = CliRunner().invoke(cli, ["--json", entity, "ls", "--field-type", bad], env=ENV)
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output.strip())
    assert payload["ok"] is False
    assert "global, enriched, relationship-intelligence" in json.dumps(payload) or (
        "enriched, global, relationship-intelligence" in json.dumps(payload)
    )


@pytest.mark.parametrize("entity", ["company", "person"])
@pytest.mark.parametrize("bad", ["list", "hidden"])
def test_get_field_type_rejects_non_entity_types(entity: str, bad: str) -> None:
    result = CliRunner().invoke(cli, ["--json", entity, "get", "1", "--field-type", bad], env=ENV)
    assert result.exit_code == 2, result.output


@pytest.mark.parametrize("entity", ["company", "person"])
def test_ls_field_type_accepts_entity_types(entity: str, respx_mock: respx.MockRouter) -> None:
    plural = {"company": "companies", "person": "persons"}[entity]
    route = respx_mock.get(f"https://api.affinity.co/v2/{plural}").mock(
        return_value=Response(200, json={"data": [], "pagination": {"nextUrl": None}})
    )
    result = CliRunner().invoke(
        cli, ["--json", entity, "ls", "--field-type", "Enriched", "-n", "1"], env=ENV
    )
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params.get_list("fieldTypes") == ["enriched"]


# ---------------------------------------------------------------------------
# field create --value-type
# ---------------------------------------------------------------------------


def _choices(command: Any, option: str) -> set[str]:
    for param in command.params:
        if option in getattr(param, "opts", []):
            return set(param.type.choices)
    raise AssertionError(f"{option} not found")


def test_field_create_value_type_choices_are_the_creatable_set() -> None:
    assert _choices(field_create, "--value-type") == CREATABLE


@pytest.mark.parametrize(
    "bad",
    [
        "interaction",
        "filterable-text",
        "filterable-text-multi",
        "formula-number",
        "list-multi",
        "note",
        "reminder",
    ],
)
def test_field_create_rejects_non_creatable_value_types(bad: str) -> None:
    result = CliRunner().invoke(
        cli,
        [*CREATE_ARGS, "--value-type", bad],
        env=ENV,
    )
    assert result.exit_code == 2, result.output


def _mock_field_post(respx_mock: respx.MockRouter) -> Any:
    return respx_mock.post("https://api.affinity.co/fields").mock(
        return_value=Response(
            200,
            json={"id": "field-5", "name": "X", "value_type": 0, "entity_type": 1},
        )
    )


@pytest.mark.parametrize(
    ("value_type", "v1_code"),
    [
        ("person-multi", 0),
        ("company-multi", 1),
        ("dropdown-multi", 2),
        ("number-multi", 3),
        ("location-multi", 5),
    ],
)
def test_field_create_multi_value_type_forces_allows_multiple(
    value_type: str, v1_code: int, respx_mock: respx.MockRouter
) -> None:
    route = _mock_field_post(respx_mock)
    result = CliRunner().invoke(
        cli,
        [*CREATE_ARGS, "--value-type", value_type],
        env=ENV,
    )
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["value_type"] == v1_code
    assert body["allows_multiple"] is True
    payload = json.loads(result.output.strip())
    assert payload["command"]["modifiers"]["allowsMultiple"] is True


def test_field_create_single_value_type_keeps_allows_multiple_off(
    respx_mock: respx.MockRouter,
) -> None:
    route = _mock_field_post(respx_mock)
    result = CliRunner().invoke(
        cli,
        [*CREATE_ARGS, "--value-type", "person"],
        env=ENV,
    )
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["value_type"] == 0
    assert body.get("allows_multiple", False) is False


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [AffinityList, ListSummary])
@pytest.mark.parametrize("key", ["isPublic", "public"])
def test_list_models_read_is_public_from_either_key(model: Any, key: str) -> None:
    data = {"id": 1, "name": "L", "type": "company", "ownerId": 2, key: True}
    parsed = model.model_validate(data)
    assert parsed.is_public is True
    # Serialization keeps the V1-era alias.
    dumped = parsed.model_dump(by_alias=True)
    assert dumped["public"] is True
    assert "isPublic" not in dumped


def test_affinity_list_still_extracts_list_size() -> None:
    parsed = AffinityList.model_validate(
        {"id": 1, "name": "L", "type": "company", "ownerId": 2, "isPublic": False, "listSize": 7}
    )
    assert parsed.is_public is False
    assert parsed._list_size_hint == 7


def test_affinity_list_construct_by_field_name() -> None:
    parsed = AffinityList(id=1, name="L", type=1, is_public=True, owner_id=2)  # type: ignore[arg-type]
    assert parsed.is_public is True


def test_list_summary_creator_id() -> None:
    parsed = ListSummary.model_validate({"id": 1, "creatorId": 42, "isPublic": True})
    assert parsed.creator_id == 42
    assert parsed.model_dump(by_alias=True)["creatorId"] == 42


def test_list_entry_with_entity_creator_id() -> None:
    parsed = ListEntryWithEntity.model_validate(
        {
            "id": 5,
            "listId": 1,
            "creatorId": 42,
            "createdAt": "2026-01-01T00:00:00Z",
            "type": "company",
            "entity": {"id": 9, "name": "Acme"},
        }
    )
    assert parsed.creator_id == 42


def test_field_metadata_description_and_is_required_across_versions() -> None:
    # 2024-01-01: neither key is present.
    old = FieldMetadata.model_validate(
        {"id": "field-1", "name": "A", "valueType": "text", "type": "global"}
    )
    assert old.description is None
    assert old.is_required is False
    # 2026-09-17: both are always present (description may be null).
    new = FieldMetadata.model_validate(
        {
            "id": "field-1",
            "name": "A",
            "valueType": "text",
            "type": "global",
            "description": "Why",
            "isRequired": True,
        }
    )
    assert new.description == "Why"
    assert new.is_required is True
    null_desc = FieldMetadata.model_validate(
        {
            "id": "field-1",
            "name": "A",
            "valueType": "text",
            "description": None,
            "isRequired": False,
        }
    )
    assert null_desc.description is None
    # V1 snake_case
    v1 = FieldMetadata.model_validate(
        {"id": 1, "name": "A", "value_type": 6, "is_required": True}  # type: ignore[dict-item]
    )
    assert v1.is_required is True


# ---------------------------------------------------------------------------
# Table rendering of the new value types
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    table, _omitted = _table_from_rows([{"value": value}])
    return str(table.columns[0]._cells[0])


def test_render_note_value() -> None:
    text = _cell(
        {
            "type": "note",
            "data": {
                "id": 11,
                "content": {"html": "<p>This is a <b>note</b>!</p>"},
                "creator": {"id": 1, "firstName": "Jane", "lastName": "Smith"},
                "mentions": [],
                "createdAt": "2023-01-01T00:00:00Z",
                "updatedAt": None,
            },
        }
    )
    assert "This is a note!" in text
    assert "id=11" in text
    assert "<p>" not in text


def test_render_reminder_value() -> None:
    text = _cell(
        {
            "type": "reminder",
            "data": {
                "id": 3,
                "type": "one-time",
                "content": "Follow up about the funding round",
                "createdAt": "2023-01-01T00:00:00Z",
                "dueDate": "2026-06-01T17:00:00Z",
            },
        }
    )
    assert "Follow up about the funding round" in text
    assert "2026-06-01" in text


def test_render_formula_number_value() -> None:
    assert _cell({"type": "formula-number", "data": {"calculatedValue": 1234.0}}) == "1,234"
    assert _cell({"type": "formula-number", "data": {"calculatedValue": None}}) == ""


def test_render_list_multi_value() -> None:
    text = _cell(
        {
            "type": "list-multi",
            "data": [
                {"id": 1, "name": "Q4 Pipeline", "type": "company", "entityCount": 42},
                {"id": 2, "name": "Portfolio", "type": "company", "entityCount": 7},
            ],
            "totalCount": 2,
        }
    )
    assert text == "Q4 Pipeline, Portfolio"


@pytest.mark.parametrize("value_type", ["note", "reminder", "list-multi", "formula-number"])
def test_render_null_new_value_types(value_type: str) -> None:
    assert _cell({"type": value_type, "data": None}) == ""


@pytest.mark.parametrize("bad", [FieldType.HIDDEN, FieldType.LIST])
def test_entity_services_reject_hidden_and_list_field_types(bad: FieldType) -> None:
    from affinity.models.types import validate_entity_field_types

    with pytest.raises(ValueError, match=bad.name):
        validate_entity_field_types([bad], endpoint="companies")
