"""Coverage tests for affinity.cli.field_utils.

Targets fetch_field_metadata, build_field_*_map, FieldResolver methods,
_coerce_entity_id,
find_field_values_for_field, and format_value_for_comparison.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from affinity.cli.errors import CLIError
from affinity.cli.field_utils import (
    FieldResolver,
    _coerce_entity_id,
    build_field_id_to_name_map,
    build_field_name_to_id_map,
    fetch_field_metadata,
    find_field_values_for_field,
    format_value_for_comparison,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_field(
    field_id: str = "field-100",
    name: str = "Status",
    *,
    value_type: str = "text",
    allows_multiple: bool = False,
    dropdown_options: list[Any] | None = None,
) -> MagicMock:
    """Create a mock FieldMetadata."""
    f = MagicMock()
    f.id = field_id
    f.name = name
    f.value_type = value_type
    f.allows_multiple = allows_multiple
    f.dropdown_options = dropdown_options or []
    return f


# ---------------------------------------------------------------------------
# fetch_field_metadata
# ---------------------------------------------------------------------------


class TestFetchFieldMetadata:
    def test_person_entity_type(self) -> None:
        client = MagicMock()
        client.persons.get_fields.return_value = [_make_field()]
        result = fetch_field_metadata(client=client, entity_type="person")
        assert len(result) == 1
        client.persons.get_fields.assert_called_once()

    def test_company_entity_type(self) -> None:
        client = MagicMock()
        client.companies.get_fields.return_value = [_make_field()]
        result = fetch_field_metadata(client=client, entity_type="company")
        assert len(result) == 1
        client.companies.get_fields.assert_called_once()

    def test_opportunity_requires_list_id(self) -> None:
        client = MagicMock()
        with pytest.raises(CLIError, match="list_id is required"):
            fetch_field_metadata(client=client, entity_type="opportunity")

    def test_list_entry_requires_list_id(self) -> None:
        client = MagicMock()
        with pytest.raises(CLIError, match="list_id is required"):
            fetch_field_metadata(client=client, entity_type="list-entry")

    def test_opportunity_with_list_id(self) -> None:
        client = MagicMock()
        client.lists.get_fields.return_value = [_make_field()]
        result = fetch_field_metadata(client=client, entity_type="opportunity", list_id=42)
        assert len(result) == 1
        client.lists.get_fields.assert_called_once()

    def test_unknown_entity_type(self) -> None:
        client = MagicMock()
        with pytest.raises(CLIError, match="Unknown entity type"):
            fetch_field_metadata(
                client=client,
                entity_type="widget",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# build_field_*_map
# ---------------------------------------------------------------------------


class TestBuildFieldMaps:
    def test_id_to_name(self) -> None:
        fields = [
            _make_field("field-1", "Name"),
            _make_field("field-2", "Email"),
        ]
        mapping = build_field_id_to_name_map(fields)
        assert mapping == {"field-1": "Name", "field-2": "Email"}

    def test_id_to_name_empty(self) -> None:
        assert build_field_id_to_name_map([]) == {}

    def test_name_to_id_basic(self) -> None:
        fields = [
            _make_field("field-1", "Name"),
            _make_field("field-2", "Email"),
        ]
        mapping = build_field_name_to_id_map(fields)
        assert mapping == {"name": ["field-1"], "email": ["field-2"]}

    def test_name_to_id_duplicates(self) -> None:
        fields = [
            _make_field("field-1", "Status"),
            _make_field("field-2", "status"),  # same name, different case
        ]
        mapping = build_field_name_to_id_map(fields)
        assert "status" in mapping
        assert set(mapping["status"]) == {"field-1", "field-2"}

    def test_name_to_id_skips_empty_names(self) -> None:
        fields = [_make_field("field-1", "")]
        mapping = build_field_name_to_id_map(fields)
        assert mapping == {}


# ---------------------------------------------------------------------------
# FieldResolver
# ---------------------------------------------------------------------------


class TestFieldResolver:
    @pytest.fixture
    def resolver(self) -> FieldResolver:
        fields = [
            _make_field("field-1", "Name"),
            _make_field("field-2", "Email"),
            _make_field("field-3", "Status"),
        ]
        return FieldResolver(fields)

    def test_available_names(self, resolver: FieldResolver) -> None:
        names = resolver.available_names
        assert names == ["Email", "Name", "Status"]

    def test_resolve_by_field_id(self, resolver: FieldResolver) -> None:
        assert resolver.resolve_field_name_or_id("field-1") == "field-1"

    def test_resolve_by_field_id_not_found(self, resolver: FieldResolver) -> None:
        with pytest.raises(CLIError, match="not found"):
            resolver.resolve_field_name_or_id("field-999")

    def test_resolve_by_name(self, resolver: FieldResolver) -> None:
        assert resolver.resolve_field_name_or_id("Name") == "field-1"

    def test_resolve_by_name_case_insensitive(self, resolver: FieldResolver) -> None:
        assert resolver.resolve_field_name_or_id("name") == "field-1"
        assert resolver.resolve_field_name_or_id("EMAIL") == "field-2"

    def test_resolve_by_name_not_found(self, resolver: FieldResolver) -> None:
        with pytest.raises(CLIError, match="not found"):
            resolver.resolve_field_name_or_id("Phone")

    def test_resolve_empty_name_raises(self, resolver: FieldResolver) -> None:
        with pytest.raises(CLIError, match="Empty"):
            resolver.resolve_field_name_or_id("")

    def test_resolve_ambiguous(self) -> None:
        fields = [
            _make_field("field-1", "Tag"),
            _make_field("field-2", "tag"),
        ]
        resolver = FieldResolver(fields)
        with pytest.raises(CLIError, match="Ambiguous"):
            resolver.resolve_field_name_or_id("Tag")

    def test_resolve_all_batch(self, resolver: FieldResolver) -> None:
        updates = {"Name": "Alice", "Email": "a@b.com"}
        resolved, errors = resolver.resolve_all_field_names_or_ids(updates)
        assert resolved == {"field-1": "Alice", "field-2": "a@b.com"}
        assert errors == []

    def test_resolve_all_invalid_raises(self, resolver: FieldResolver) -> None:
        updates = {"Name": "Alice", "Phone": "123"}
        with pytest.raises(CLIError, match="Invalid"):
            resolver.resolve_all_field_names_or_ids(updates)

    def test_resolve_all_skips_empty_keys(self, resolver: FieldResolver) -> None:
        updates = {"": "ignored", "Name": "Alice"}
        resolved, _ = resolver.resolve_all_field_names_or_ids(updates)
        assert "field-1" in resolved
        assert len(resolved) == 1

    def test_resolve_all_by_field_id(self, resolver: FieldResolver) -> None:
        updates = {"field-1": "Alice"}
        resolved, _ = resolver.resolve_all_field_names_or_ids(updates)
        assert resolved == {"field-1": "Alice"}

    def test_resolve_all_invalid_field_id(self, resolver: FieldResolver) -> None:
        updates = {"field-999": "x"}
        with pytest.raises(CLIError, match="Invalid"):
            resolver.resolve_all_field_names_or_ids(updates)

    def test_get_field_name(self, resolver: FieldResolver) -> None:
        assert resolver.get_field_name("field-1") == "Name"
        assert resolver.get_field_name("field-999") == ""

    def test_get_field_metadata(self, resolver: FieldResolver) -> None:
        assert resolver.get_field_metadata("field-1") is not None
        assert resolver.get_field_metadata("field-999") is None


# ---------------------------------------------------------------------------
# _coerce_entity_id
# ---------------------------------------------------------------------------


class TestCoerceEntityId:
    def test_int_value(self) -> None:
        assert _coerce_entity_id(42, "Name", "person") == 42

    def test_string_value(self) -> None:
        assert _coerce_entity_id("42", "Name", "person") == 42

    def test_bool_rejected(self) -> None:
        with pytest.raises(CLIError, match="Invalid entity ID"):
            _coerce_entity_id(True, "Name", "person")

    def test_non_numeric_string_rejected(self) -> None:
        with pytest.raises(CLIError, match="Invalid entity ID"):
            _coerce_entity_id("abc", "Name", "person")


# ---------------------------------------------------------------------------
# find_field_values_for_field
# ---------------------------------------------------------------------------


class TestFindFieldValuesForField:
    def test_filters_correctly(self) -> None:
        fvs = [
            {"fieldId": "field-1", "value": "A"},
            {"fieldId": "field-2", "value": "B"},
            {"fieldId": "field-1", "value": "C"},
        ]
        result = find_field_values_for_field(field_values=fvs, field_id="field-1")
        assert len(result) == 2
        assert result[0]["value"] == "A"
        assert result[1]["value"] == "C"

    def test_no_matches(self) -> None:
        fvs = [{"fieldId": "field-1", "value": "A"}]
        result = find_field_values_for_field(field_values=fvs, field_id="field-999")
        assert result == []

    def test_handles_field_id_key(self) -> None:
        fvs = [{"field_id": "field-1", "value": "A"}]
        result = find_field_values_for_field(field_values=fvs, field_id="field-1")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# format_value_for_comparison
# ---------------------------------------------------------------------------


class TestFormatValueForComparison:
    def test_none(self) -> None:
        assert format_value_for_comparison(None) == ""

    def test_string(self) -> None:
        assert format_value_for_comparison("hello") == "hello"

    def test_bool(self) -> None:
        assert format_value_for_comparison(True) == "true"
        assert format_value_for_comparison(False) == "false"

    def test_int(self) -> None:
        assert format_value_for_comparison(42) == "42"

    def test_float(self) -> None:
        assert format_value_for_comparison(3.14) == "3.14"

    def test_dict_with_data(self) -> None:
        assert format_value_for_comparison({"type": "text", "data": "hello"}) == "hello"

    def test_dict_with_text(self) -> None:
        assert format_value_for_comparison({"text": "Foo"}) == "Foo"

    def test_dict_with_name(self) -> None:
        assert format_value_for_comparison({"name": "Bar"}) == "Bar"

    def test_list(self) -> None:
        assert format_value_for_comparison(["a", "b"]) == "a, b"

    def test_other_type(self) -> None:
        assert format_value_for_comparison(object()) != ""
