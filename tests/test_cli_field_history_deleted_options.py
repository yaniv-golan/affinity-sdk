"""field history: a dropdown option deleted since comes back as {id: null, text, deleted: true}."""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.commands.field_cmds import _is_dropdown
from affinity.cli.main import cli
from affinity.models.entities import FieldMetadata

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V1 = "https://api.affinity.co"
LIVE = {"id": 11, "text": "Lead", "rank": 1, "color": 2}


def _change(change_id: int, value: Any) -> dict[str, Any]:
    return {
        "id": change_id,
        "fieldId": "field-123",
        "entityId": 456,
        "listEntryId": 789,
        "actionType": 2,
        "value": value,
        "changedAt": "2024-01-15T10:30:00Z",
        "changer": None,
    }


def _v1_field(value_type: int, options: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": 123,
        "name": "Status",
        "list_id": 9,
        "value_type": value_type,
        "allows_multiple": False,
        "track_changes": True,
        "enrichment_source": "none",
        "dropdown_options": options or [],
    }


def _runner() -> CliRunner:
    """A runner with stderr kept apart from stdout on click 8.1 too (8.2+ always does)."""
    if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    return CliRunner()


def _history(respx_mock: respx.MockRouter, values: list[Any]) -> dict[str, Any]:
    respx_mock.get(f"{V1}/field-value-changes").mock(
        return_value=Response(200, json=[_change(i, v) for i, v in enumerate(values, 1)])
    )
    result = _runner().invoke(
        cli,
        ["--json", "field", "history", "field-123", "--list-entry-id", "789"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize(
    "field",
    [_v1_field(7), _v1_field(2, [{"id": 11, "text": "Lead", "rank": 1, "color": 0}])],
    ids=["ranked-dropdown", "dropdown-with-options"],
)
def test_a_deleted_option_gets_the_live_shape(
    respx_mock: respx.MockRouter, field: dict[str, Any]
) -> None:
    respx_mock.get(f"{V1}/fields").mock(return_value=Response(200, json=[field]))
    out = _history(respx_mock, [LIVE, "Reach Out"])
    values = [c["value"] for c in out["data"]["fieldValueChanges"]]
    assert values == [LIVE, {"id": None, "text": "Reach Out", "deleted": True}]


@pytest.mark.parametrize("value_type", [6, 2], ids=["text", "v1-type-2-without-options"])
def test_text_field_values_stay_strings(respx_mock: respx.MockRouter, value_type: int) -> None:
    respx_mock.get(f"{V1}/fields").mock(return_value=Response(200, json=[_v1_field(value_type)]))
    out = _history(respx_mock, ["hello"])
    assert out["data"]["fieldValueChanges"][0]["value"] == "hello"


def test_no_metadata_read_when_every_value_is_an_option(respx_mock: respx.MockRouter) -> None:
    fields = respx_mock.get(f"{V1}/fields")
    out = _history(respx_mock, [LIVE])
    assert out["data"]["fieldValueChanges"][0]["value"] == LIVE
    assert not fields.called


def test_unreadable_metadata_leaves_values_and_warns(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V1}/fields").mock(return_value=Response(500, json={"message": "boom"}))
    out = _history(respx_mock, ["Reach Out"])
    assert out["data"]["fieldValueChanges"][0]["value"] == "Reach Out"
    assert any("Could not read field-123's type" in w for w in out["warnings"])


@pytest.mark.parametrize(
    ("value_type", "expected"),
    [
        ("dropdown", True),
        ("dropdown-multi", True),
        ("ranked-dropdown", True),
        ("status-dropdown", True),
        ("text", False),
        ("number", False),
    ],
)
def test_v2_metadata_names_the_dropdown_kinds(value_type: str, expected: bool) -> None:
    meta = FieldMetadata.model_validate({"id": "field-1", "name": "S", "valueType": value_type})
    assert _is_dropdown(meta) is expected
