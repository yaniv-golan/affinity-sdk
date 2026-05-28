"""Unit tests for set-phase helpers in ``affinity.cli.field_utils``.

Covers:

- :func:`value_equals_existing` per-type no-op comparator rules
- :func:`pre_validate_set_operations` error aggregation
- :func:`execute_v2_set_phase` short-circuit / write decisions
- :func:`execute_v1_set_phase` short-circuit / write decisions
- :func:`execute_append_phase` multi-value merge + no-op
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from affinity.cli.errors import CLIError
from affinity.cli.field_utils import (
    FieldResolver,
    execute_append_phase,
    execute_v1_set_phase,
    execute_v2_set_phase,
    pre_validate_set_operations,
    value_equals_existing,
)
from affinity.models.entities import DropdownOption, FieldMetadata
from affinity.models.types import DropdownOptionId, FieldId, FieldValueType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver() -> FieldResolver:
    fields = [
        FieldMetadata(
            id=FieldId(100),
            name="Status",
            value_type=FieldValueType.DROPDOWN,
            dropdown_options=[
                DropdownOption(id=DropdownOptionId(200), text="Active"),
                DropdownOption(id=DropdownOptionId(201), text="Closed"),
                DropdownOption(id=DropdownOptionId(202), text="Intro Meeting"),
            ],
        ),
        FieldMetadata(
            id=FieldId(101),
            name="Priority",
            value_type=FieldValueType.DROPDOWN,
            dropdown_options=[
                DropdownOption(id=DropdownOptionId(300), text="High"),
                DropdownOption(id=DropdownOptionId(301), text="Low"),
                DropdownOption(id=DropdownOptionId(302), text="New"),
            ],
        ),
        FieldMetadata(
            id=FieldId(102),
            name="Tags",
            value_type=FieldValueType.DROPDOWN_MULTI,
            dropdown_options=[
                DropdownOption(id=DropdownOptionId(400), text="A"),
                DropdownOption(id=DropdownOptionId(401), text="B"),
                DropdownOption(id=DropdownOptionId(402), text="C"),
            ],
        ),
        FieldMetadata(
            id=FieldId(103),
            name="Owner",
            value_type=FieldValueType.PERSON,
        ),
        FieldMetadata(
            id=FieldId(104),
            name="Investors",
            value_type=FieldValueType.PERSON_MULTI,
        ),
        FieldMetadata(
            id=FieldId(105),
            name="Score",
            value_type=FieldValueType.NUMBER,
        ),
        FieldMetadata(
            id=FieldId(106),
            name="Closed At",
            value_type=FieldValueType.DATETIME,
        ),
        FieldMetadata(
            id=FieldId(107),
            name="Notes",
            value_type=FieldValueType.TEXT,
        ),
    ]
    return FieldResolver(fields)


def _meta(resolver: FieldResolver, field_id: str) -> FieldMetadata:
    meta = resolver.get_field_metadata(field_id)
    assert meta is not None
    return meta


# ---------------------------------------------------------------------------
# value_equals_existing
# ---------------------------------------------------------------------------


class TestValueEqualsExistingDropdown:
    def test_single_dropdown_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-100")
        existing = [{"value": {"id": 202, "text": "Intro Meeting"}}]
        assert value_equals_existing(meta, {"dropdownOptionId": 202}, existing) is True

    def test_single_dropdown_different_value_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-100")
        existing = [{"value": {"id": 200, "text": "Active"}}]
        assert value_equals_existing(meta, {"dropdownOptionId": 202}, existing) is False

    def test_single_dropdown_no_existing_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-100")
        assert value_equals_existing(meta, {"dropdownOptionId": 200}, []) is False

    def test_multi_existing_for_single_field_writes(self, resolver: FieldResolver) -> None:
        # Defensive: multiple existing rows on a single-value field is not a no-op.
        meta = _meta(resolver, "field-100")
        existing = [
            {"value": {"id": 200, "text": "Active"}},
            {"value": {"id": 201, "text": "Closed"}},
        ]
        assert value_equals_existing(meta, {"dropdownOptionId": 200}, existing) is False


class TestValueEqualsExistingDropdownMulti:
    def test_set_equality_is_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-102")
        existing = [
            {"value": {"id": 400, "text": "A"}},
            {"value": {"id": 401, "text": "B"}},
        ]
        new = [{"dropdownOptionId": 400}, {"dropdownOptionId": 401}]
        assert value_equals_existing(meta, new, existing) is True

    def test_order_insensitive(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-102")
        existing = [
            {"value": {"id": 401, "text": "B"}},
            {"value": {"id": 400, "text": "A"}},
        ]
        new = [{"dropdownOptionId": 400}, {"dropdownOptionId": 401}]
        assert value_equals_existing(meta, new, existing) is True

    def test_subset_is_not_noop(self, resolver: FieldResolver) -> None:
        # --set Tags A when existing is [A, B, C] is a REPLACE that drops B/C.
        meta = _meta(resolver, "field-102")
        existing = [
            {"value": {"id": 400, "text": "A"}},
            {"value": {"id": 401, "text": "B"}},
            {"value": {"id": 402, "text": "C"}},
        ]
        new = [{"dropdownOptionId": 400}]
        assert value_equals_existing(meta, new, existing) is False

    def test_superset_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-102")
        existing = [{"value": {"id": 400, "text": "A"}}]
        new = [{"dropdownOptionId": 400}, {"dropdownOptionId": 401}]
        assert value_equals_existing(meta, new, existing) is False


class TestValueEqualsExistingPerson:
    def test_single_person_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-103")
        existing = [{"value": {"id": 42}}]
        assert value_equals_existing(meta, {"id": 42}, existing) is True

    def test_single_person_writes_on_different_id(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-103")
        existing = [{"value": {"id": 42}}]
        assert value_equals_existing(meta, {"id": 99}, existing) is False

    def test_person_multi_set_equality(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-104")
        existing = [{"value": {"id": 1}}, {"value": {"id": 2}}]
        assert value_equals_existing(meta, [{"id": 2}, {"id": 1}], existing) is True

    def test_person_multi_subset_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-104")
        existing = [{"value": {"id": 1}}, {"value": {"id": 2}}, {"value": {"id": 3}}]
        assert value_equals_existing(meta, [{"id": 1}, {"id": 2}], existing) is False


class TestValueEqualsExistingNumber:
    def test_int_existing_string_new_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-105")
        existing = [{"value": 42}]
        assert value_equals_existing(meta, "42", existing) is True

    def test_whitespace_tolerant(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-105")
        existing = [{"value": 42}]
        assert value_equals_existing(meta, "  42  ", existing) is True

    def test_float_vs_int_equal(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-105")
        existing = [{"value": 42}]
        assert value_equals_existing(meta, "42.0", existing) is True

    def test_unparseable_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-105")
        existing = [{"value": 42}]
        assert value_equals_existing(meta, "not a number", existing) is False


class TestValueEqualsExistingDatetime:
    def test_iso_equivalence(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-106")
        existing = [{"value": "2024-06-01T12:00:00+00:00"}]
        assert value_equals_existing(meta, "2024-06-01T12:00:00Z", existing) is True

    def test_different_datetimes_write(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-106")
        existing = [{"value": "2024-06-01T12:00:00Z"}]
        assert value_equals_existing(meta, "2024-06-02T12:00:00Z", existing) is False


class TestValueEqualsExistingText:
    def test_exact_match_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-107")
        existing = [{"value": "Hello"}]
        assert value_equals_existing(meta, "Hello", existing) is True

    def test_strips_whitespace(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-107")
        existing = [{"value": "  Hello  "}]
        assert value_equals_existing(meta, "Hello", existing) is True

    def test_case_sensitive_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-107")
        existing = [{"value": "Hello"}]
        assert value_equals_existing(meta, "hello", existing) is False


class TestValueEqualsExistingEmpty:
    def test_empty_existing_empty_new_is_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-107")
        assert value_equals_existing(meta, "", []) is True
        assert value_equals_existing(meta, None, []) is True

    def test_empty_existing_nonempty_new_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-107")
        assert value_equals_existing(meta, "Hello", []) is False


# ---------------------------------------------------------------------------
# pre_validate_set_operations
# ---------------------------------------------------------------------------


class TestPreValidateSetOperations:
    def test_all_valid_returns_raw_resolved_payloads(self, resolver: FieldResolver) -> None:
        ops = [("field-100", "Intro Meeting"), ("field-107", "Some text")]
        result = pre_validate_set_operations(resolver, ops)
        # 3-tuple: (raw, resolved, value_type_str)
        assert result["field-100"] == (
            "Intro Meeting",
            {"dropdownOptionId": 202},
            "dropdown",
        )
        assert result["field-107"] == ("Some text", "Some text", "text")

    def test_invalid_entity_id_aggregates(self, resolver: FieldResolver) -> None:
        ops = [
            ("field-100", "Intro Meeting"),
            ("field-103", "Jane Doe"),  # person field — name is not numeric
        ]
        with pytest.raises(CLIError) as exc_info:
            pre_validate_set_operations(resolver, ops)
        # The good value did not silently apply (we raised, so caller must abort).
        assert "Owner" in exc_info.value.message
        assert "Jane Doe" in exc_info.value.message
        # Structured details for callers that want to render programmatically.
        details = exc_info.value.details or {}
        failures = details.get("failures", [])
        assert len(failures) == 1
        assert failures[0]["field"] == "Owner"

    def test_multiple_errors_all_reported(self, resolver: FieldResolver) -> None:
        ops = [
            ("field-100", "Not A Real Status"),  # bad dropdown option
            ("field-103", "Jane Doe"),  # bad person id
        ]
        with pytest.raises(CLIError) as exc_info:
            pre_validate_set_operations(resolver, ops)
        details = exc_info.value.details or {}
        failures = details.get("failures", [])
        assert len(failures) == 2
        field_names = {f["field"] for f in failures}
        assert field_names == {"Status", "Owner"}


# ---------------------------------------------------------------------------
# execute_v2_set_phase
# ---------------------------------------------------------------------------


def _make_field_value(fv_id: int, field_id: str, value: Any) -> dict[str, Any]:
    return {"id": fv_id, "fieldId": field_id, "entityId": 1, "value": value}


class TestExecuteV2SetPhase:
    def test_noop_skips_delete_and_create(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        entries = MagicMock()
        existing = [_make_field_value(1, "field-100", {"id": 202, "text": "Intro Meeting"})]

        pre_resolved = {
            "field-100": ("Intro Meeting", {"dropdownOptionId": 202}, "dropdown"),
        }
        created, deleted, refreshed = execute_v2_set_phase(
            client=client,
            entries=entries,
            list_entry_id=123,
            pre_resolved_ops=pre_resolved,
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.delete.assert_not_called()
        entries.update_field_value.assert_not_called()
        assert created == []
        assert deleted == 0
        assert refreshed == existing

    def test_change_deletes_then_creates(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        entries = MagicMock()
        new_fv = _make_field_value(2, "field-100", {"id": 202, "text": "Intro Meeting"})
        # update_field_value should return a model-like; mock anything serializable.
        update_result = MagicMock()
        update_result.model_dump.return_value = new_fv
        entries.update_field_value.return_value = update_result

        existing = [_make_field_value(1, "field-100", {"id": 200, "text": "Active"})]

        pre_resolved = {
            "field-100": ("Intro Meeting", {"dropdownOptionId": 202}, "dropdown"),
        }
        created, deleted, refreshed = execute_v2_set_phase(
            client=client,
            entries=entries,
            list_entry_id=123,
            pre_resolved_ops=pre_resolved,
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.delete.assert_called_once_with(1)
        entries.update_field_value.assert_called_once()
        assert deleted == 1
        assert len(created) == 1
        # Refreshed list reflects: old removed, new added.
        assert all(fv["id"] != 1 for fv in refreshed)

    def test_partial_noop(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        entries = MagicMock()
        new_fv = _make_field_value(3, "field-101", {"id": 302, "text": "New"})
        update_result = MagicMock()
        update_result.model_dump.return_value = new_fv
        entries.update_field_value.return_value = update_result

        existing = [
            _make_field_value(1, "field-100", {"id": 200, "text": "Active"}),
        ]

        pre_resolved = {
            "field-100": (
                "Active",
                {"dropdownOptionId": 200},
                "dropdown",
            ),  # already Active → no-op
            "field-101": (
                "New",
                {"dropdownOptionId": 302},
                "dropdown",
            ),  # Priority not set → write
        }
        created, deleted, _ = execute_v2_set_phase(
            client=client,
            entries=entries,
            list_entry_id=123,
            pre_resolved_ops=pre_resolved,
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.delete.assert_not_called()
        entries.update_field_value.assert_called_once()
        assert deleted == 0
        assert len(created) == 1


# ---------------------------------------------------------------------------
# execute_v1_set_phase
# ---------------------------------------------------------------------------


class TestExecuteV1SetPhase:
    def test_noop_skips_delete_and_create(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        existing = [_make_field_value(1, "field-107", "Hello")]

        # 3-tuple: (raw, resolved, value_type). For text, raw == resolved.
        pre_resolved = {"field-107": ("Hello", "Hello", "text")}
        created, deleted = execute_v1_set_phase(
            client=client,
            entity_kind="company",
            entity_id=555,
            pre_resolved_ops=pre_resolved,
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.delete.assert_not_called()
        client.field_values.create.assert_not_called()
        assert created == []
        assert deleted == 0

    def test_change_deletes_then_creates_with_raw_value(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        created_result = MagicMock()
        created_result.model_dump.return_value = _make_field_value(2, "field-107", "Goodbye")
        client.field_values.create.return_value = created_result

        existing = [_make_field_value(1, "field-107", "Hello")]
        pre_resolved = {"field-107": ("Goodbye", "Goodbye", "text")}
        created, deleted = execute_v1_set_phase(
            client=client,
            entity_kind="company",
            entity_id=555,
            pre_resolved_ops=pre_resolved,
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.delete.assert_called_once_with(1)
        client.field_values.create.assert_called_once()
        # Confirm RAW value (not the resolved dict) reached FieldValueCreate.
        call_kwargs = client.field_values.create.call_args
        fvc = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("data")
        assert fvc.value == "Goodbye"
        assert fvc.entity_id == 555
        assert deleted == 1
        assert len(created) == 1


# ---------------------------------------------------------------------------
# execute_append_phase
# ---------------------------------------------------------------------------


class TestExecuteAppendPhase:
    def test_dropdown_multi_append_merges_with_existing(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        entries = MagicMock()
        update_result = MagicMock()
        update_result.model_dump.return_value = {"id": 99, "value": []}
        entries.update_field_value.return_value = update_result

        existing = [_make_field_value(1, "field-102", {"id": 400, "text": "A"})]
        # Append "B" (option-id 401).
        append_ops = [("field-102", "B")]

        created, _ = execute_append_phase(
            client=client,
            entries=entries,
            list_entry_id=123,
            append_ops=append_ops,
            existing_values_serialized=existing,
            resolver=resolver,
        )
        entries.update_field_value.assert_called_once()
        call_args = entries.update_field_value.call_args
        wire_value = call_args.kwargs.get("value") or call_args.args[2]
        # Both A (existing) and B (new) should be in the payload.
        ids = {item["dropdownOptionId"] for item in wire_value}
        assert ids == {400, 401}
        assert len(created) == 1

    def test_dropdown_multi_append_existing_value_is_noop(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        entries = MagicMock()

        existing = [
            _make_field_value(1, "field-102", {"id": 400, "text": "A"}),
            _make_field_value(2, "field-102", {"id": 401, "text": "B"}),
        ]
        # Re-appending "A" — already there.
        append_ops = [("field-102", "A")]

        created, _ = execute_append_phase(
            client=client,
            entries=entries,
            list_entry_id=123,
            append_ops=append_ops,
            existing_values_serialized=existing,
            resolver=resolver,
        )
        entries.update_field_value.assert_not_called()
        assert created == []
