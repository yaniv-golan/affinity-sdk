"""API version 2026-07-15+: a field on a restricted opportunity the API key can't manage comes
back as ``type: "hidden"`` with an empty value. It is masked, not empty."""

from __future__ import annotations

from affinity.cli.commands._list_entry_fields import filter_list_entry_fields
from affinity.cli.field_utils import truncation_warnings
from affinity.cli.query.executor import _normalize_list_entry_fields
from affinity.models.entities import Company

HIDDEN = {
    "id": "field-9",
    "name": "Deal Size",
    "type": "hidden",
    "value": {"type": "number", "data": None},
}
VISIBLE = {
    "id": "field-8",
    "name": "Stage",
    "type": "list",
    "value": {"type": "text", "data": "Open"},
}


def test_field_values_report_hidden() -> None:
    fields = Company.model_validate({"id": 1, "name": "Acme", "fields": [HIDDEN, VISIBLE]}).fields
    assert fields.is_hidden("field-9") is True
    assert fields.is_hidden("field-8") is False
    assert fields.hidden_fields() == ["field-9"]
    assert fields.get_value("field-9") is None  # unchanged: still None


def test_warning_says_masked_not_empty() -> None:
    (msg,) = truncation_warnings([("entry 5", [HIDDEN, VISIBLE])])
    assert "hidden by Affinity" in msg
    assert "'Deal Size' on entry 5" in msg
    assert "not empty" in msg


def test_filtered_warning_mentions_filters() -> None:
    (msg,) = truncation_warnings([("e", [HIDDEN])], filtered=True)
    assert "Filters treated them as empty" in msg


def test_list_only_scope_keeps_hidden_list_fields() -> None:
    kept, list_count, total = filter_list_entry_fields([HIDDEN, VISIBLE], scope="list-only")
    assert {f["id"] for f in kept} == {"field-8", "field-9"}
    assert (list_count, total) == (2, 2)


def test_query_normalization_records_hidden_fields() -> None:
    record = {
        "id": 7,
        "entity": {
            "id": 1,
            "name": "Acme",
            "fields": {"requested": True, "data": {"field-9": HIDDEN}},
        },
    }
    seen: list = []
    _normalize_list_entry_fields(record, seen)
    assert any("hidden by Affinity" in m for m in truncation_warnings(seen))
