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


class TestResolveDatetimeFieldValue:
    """The V2 API rejects a bare date with 400 "does not match format: date-time" (found by the
    live write test). Date-only input becomes noon UTC: the same calendar date in Pacific time,
    which is the date Affinity stores."""

    def test_date_only_becomes_noon_utc(self, resolver: FieldResolver) -> None:
        assert resolver.resolve_field_value("field-106", "2024-04-01") == (
            "2024-04-01T12:00:00Z",
            "datetime",
        )

    def test_datetime_with_zone_is_normalized_to_utc(self, resolver: FieldResolver) -> None:
        assert resolver.resolve_field_value("field-106", "2024-04-01T18:30:00+03:00") == (
            "2024-04-01T15:30:00Z",
            "datetime",
        )

    def test_unparseable_date_fails_before_any_write(self, resolver: FieldResolver) -> None:
        with pytest.raises(CLIError):
            resolver.resolve_field_value("field-106", "next tuesday")

    def test_resolved_date_is_noop_against_stored_midnight_pacific(
        self, resolver: FieldResolver
    ) -> None:
        resolved, _ = resolver.resolve_field_value("field-106", "2024-04-01")
        meta = _meta(resolver, "field-106")
        assert value_equals_existing(meta, resolved, [{"value": "2024-04-01T07:00:00Z"}]) is True


class TestValueEqualsExistingDateGranular:
    """Since 2026-01-01 Affinity stores date fields at midnight Pacific Time.

    2024-04-01 is returned as 2024-04-01T07:00:00Z (PDT) and 2024-01-15 as
    2024-01-15T08:00:00Z (PST). Re-setting the same calendar date must be a no-op, whatever
    the user's local timezone (a date-only input is a calendar date, not local midnight).
    """

    @pytest.fixture(autouse=True)
    def _east_of_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        monkeypatch.setenv("TZ", "Asia/Jerusalem")
        time.tzset()
        yield
        monkeypatch.undo()
        time.tzset()

    def test_same_date_summer_is_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-106")
        existing = [{"value": "2024-04-01T07:00:00.000Z"}]
        assert value_equals_existing(meta, "2024-04-01", existing) is True

    def test_same_date_winter_is_noop(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-106")
        existing = [{"value": {"type": "datetime", "data": "2024-01-15T08:00:00Z"}}]
        assert value_equals_existing(meta, "2024-01-15", existing) is True

    def test_input_with_time_compares_its_pacific_date(self, resolver: FieldResolver) -> None:
        """15:30Z is 08:30 PDT on Apr 1 -> Affinity would store Apr 1."""
        meta = _meta(resolver, "field-106")
        existing = [{"value": "2024-04-01T07:00:00Z"}]
        assert value_equals_existing(meta, "2024-04-01T15:30:00Z", existing) is True

    def test_different_date_writes(self, resolver: FieldResolver) -> None:
        meta = _meta(resolver, "field-106")
        existing = [{"value": "2024-04-01T07:00:00Z"}]
        assert value_equals_existing(meta, "2024-04-02", existing) is False

    def test_existing_with_time_of_day_keeps_exact_compare(self, resolver: FieldResolver) -> None:
        """Not midnight PT (pre-2026 value): writing would change it, so not a no-op."""
        meta = _meta(resolver, "field-106")
        existing = [{"value": "2024-04-01T15:30:00Z"}]
        assert value_equals_existing(meta, "2024-04-01", existing) is False


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

    def test_change_replaces_without_deleting_first(self, resolver: FieldResolver) -> None:
        """The V2 write replaces the value; deleting first would empty the field if the write
        were then rejected."""
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
        client.field_values.delete.assert_not_called()
        entries.update_field_value.assert_called_once()
        assert deleted == 0
        assert len(created) == 1
        # Refreshed list reflects: old replaced, new added.
        assert all(fv["id"] != 1 for fv in refreshed)

    def test_rejected_write_deletes_nothing(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        entries = MagicMock()
        entries.update_field_value.side_effect = RuntimeError("400 invalid value")
        existing = [_make_field_value(1, "field-100", {"id": 200, "text": "Active"})]
        with pytest.raises(RuntimeError):
            execute_v2_set_phase(
                client=client,
                entries=entries,
                list_entry_id=123,
                pre_resolved_ops={
                    "field-100": ("Intro Meeting", {"dropdownOptionId": 202}, "dropdown")
                },
                existing_values_serialized=existing,
                resolver=resolver,
            )
        client.field_values.delete.assert_not_called()

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

    def test_single_value_updated_in_place(self, resolver: FieldResolver) -> None:
        """An existing single value is updated in place (PUT); nothing is deleted."""
        client = MagicMock()
        client.field_values.update.return_value.model_dump.return_value = _make_field_value(
            1, "field-107", "Goodbye"
        )
        existing = [_make_field_value(1, "field-107", "Hello")]
        created, deleted = execute_v1_set_phase(
            client=client,
            entity_kind="company",
            entity_id=555,
            pre_resolved_ops={"field-107": ("Goodbye", "Goodbye", "text")},
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.update.assert_called_once_with(1, "Goodbye")
        client.field_values.delete.assert_not_called()
        client.field_values.create.assert_not_called()
        assert deleted == 0
        assert len(created) == 1

    def test_single_value_created_when_empty(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        client.field_values.create.return_value.model_dump.return_value = _make_field_value(
            2, "field-105", 5
        )
        created, deleted = execute_v1_set_phase(
            client=client,
            entity_kind="company",
            entity_id=555,
            pre_resolved_ops={"field-105": ("5", 5, "number")},
            existing_values_serialized=[],
            resolver=resolver,
        )
        fvc = client.field_values.create.call_args.args[0]
        assert fvc.value == 5  # sent as a number, not the string typed
        assert fvc.entity_id == 555
        assert deleted == 0
        assert len(created) == 1

    def test_dropdown_sent_as_exact_option_text(self, resolver: FieldResolver) -> None:
        """V1 creates a new option for unknown text, so send the matched option's text."""
        client = MagicMock()
        existing = [_make_field_value(1, "field-100", "Active")]
        execute_v1_set_phase(
            client=client,
            entity_kind="opportunity",
            entity_id=555,
            pre_resolved_ops={
                "field-100": ("intro meeting", {"dropdownOptionId": 202}, "dropdown")
            },
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.update.assert_called_once_with(1, "Intro Meeting")

    def test_ranked_dropdown_sent_as_option_id(self, resolver: FieldResolver) -> None:
        """V1 rejects text for ranked dropdowns; it takes the option id."""
        client = MagicMock()
        meta = resolver.get_field_metadata("field-100")
        assert meta is not None
        object.__setattr__(meta, "value_type", "ranked-dropdown")
        existing = [_make_field_value(1, "field-100", {"id": 200, "text": "Active"})]
        execute_v1_set_phase(
            client=client,
            entity_kind="opportunity",
            entity_id=555,
            pre_resolved_ops={
                "field-100": ("Intro Meeting", {"dropdownOptionId": 202}, "ranked-dropdown")
            },
            existing_values_serialized=existing,
            resolver=resolver,
        )
        client.field_values.update.assert_called_once_with(1, 202)

    @pytest.mark.parametrize("entity_kind", ["company", "person"])
    def test_company_person_plain_dropdown_written_by_id_via_v2(
        self, resolver: FieldResolver, entity_kind: str
    ) -> None:
        """Plain dropdowns on companies/persons go through the V2 write (option ids), which
        can't create options; nothing is written through V1."""
        client = MagicMock()
        existing = [_make_field_value(1, "field-100", "Active")]
        created, deleted = execute_v1_set_phase(
            client=client,
            entity_kind=entity_kind,  # type: ignore[arg-type]
            entity_id=555,
            pre_resolved_ops={"field-100": ("closed", {"dropdownOptionId": 201}, "dropdown")},
            existing_values_serialized=existing,
            resolver=resolver,
        )
        collection = "companies" if entity_kind == "company" else "persons"
        client._http.post.assert_called_once_with(
            f"/{collection}/555/fields/field-100",
            json={"value": {"type": "dropdown", "data": {"dropdownOptionId": 201}}},
        )
        client.field_values.update.assert_not_called()
        client.field_values.create.assert_not_called()
        client.field_values.delete.assert_not_called()
        assert deleted == 0
        assert len(created) == 1

    def test_text_dropdown_row_is_noop_when_same_option(self, resolver: FieldResolver) -> None:
        """V1 stores plain dropdown values as text; re-setting the same option writes nothing."""
        client = MagicMock()
        created, _ = execute_v1_set_phase(
            client=client,
            entity_kind="company",
            entity_id=555,
            pre_resolved_ops={"field-100": ("active", {"dropdownOptionId": 200}, "dropdown")},
            existing_values_serialized=[_make_field_value(1, "field-100", "Active")],
            resolver=resolver,
        )
        assert created == []
        client._http.post.assert_not_called()

    def test_multi_value_adds_before_removing(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        calls: list[str] = []
        client.field_values.create.side_effect = lambda data: calls.append(f"create {data.value}")
        client.field_values.delete.side_effect = lambda fv_id: calls.append(f"delete {fv_id}")
        existing = [_make_field_value(1, "field-104", 7), _make_field_value(2, "field-104", 8)]
        _, deleted = execute_v1_set_phase(
            client=client,
            entity_kind="company",
            entity_id=555,
            pre_resolved_ops={"field-104": (["8", "9"], [{"id": 8}, {"id": 9}], "person-multi")},
            existing_values_serialized=existing,
            resolver=resolver,
        )
        # 8 is kept, 9 is added, then 7 is removed.
        assert calls == ["create 9", "delete 1"]
        assert deleted == 1

    def test_multi_value_failed_add_removes_nothing(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        client.field_values.create.side_effect = RuntimeError("422")
        existing = [_make_field_value(1, "field-104", 7)]
        with pytest.raises(RuntimeError):
            execute_v1_set_phase(
                client=client,
                entity_kind="company",
                entity_id=555,
                pre_resolved_ops={"field-104": (["9"], [{"id": 9}], "person-multi")},
                existing_values_serialized=existing,
                resolver=resolver,
            )
        client.field_values.delete.assert_not_called()

    def test_multi_value_partial_failure_is_reported(self, resolver: FieldResolver) -> None:
        client = MagicMock()
        client.field_values.delete.side_effect = RuntimeError("timeout")
        existing = [_make_field_value(1, "field-104", 7)]
        with pytest.raises(CLIError) as exc_info:
            execute_v1_set_phase(
                client=client,
                entity_kind="company",
                entity_id=555,
                pre_resolved_ops={"field-104": (["9"], [{"id": 9}], "person-multi")},
                existing_values_serialized=existing,
                resolver=resolver,
            )
        assert exc_info.value.error_type == "partial_write"
        assert "no value was lost" in exc_info.value.message


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
