"""Tests for CLI field utility functions."""

from __future__ import annotations

import pytest

from affinity.cli.errors import CLIError
from affinity.cli.field_utils import FieldResolver as CLIFieldResolver
from affinity.models.entities import DropdownOption, FieldMetadata
from affinity.models.types import DropdownOptionId, FieldId, FieldValueType


@pytest.fixture
def dropdown_multi_resolver() -> CLIFieldResolver:
    """Create a CLI FieldResolver with dropdown-multi field metadata."""
    fields = [
        FieldMetadata(
            id=FieldId(2166604),
            name="Sourced by",
            value_type=FieldValueType.DROPDOWN_MULTI,
            dropdown_options=[
                DropdownOption(id=DropdownOptionId(5064548), text="YG"),
                DropdownOption(id=DropdownOptionId(5064549), text="AB"),
            ],
        ),
        FieldMetadata(
            id=FieldId(100),
            name="Status",
            value_type=FieldValueType.DROPDOWN,
            dropdown_options=[
                DropdownOption(id=DropdownOptionId(200), text="Active"),
                DropdownOption(id=DropdownOptionId(201), text="Closed"),
            ],
        ),
        FieldMetadata(
            id=FieldId(300),
            name="Notes",
            value_type=FieldValueType.TEXT,
        ),
    ]
    return CLIFieldResolver(fields)


@pytest.fixture
def v1_dropdown_multi_resolver() -> CLIFieldResolver:
    """Simulate V1 API: value_type=DROPDOWN with allows_multiple=True.

    V1 returns value_type=2 (mapped to "dropdown") for both single and multi
    dropdown fields, relying on allows_multiple=True to distinguish them.
    """
    fields = [
        FieldMetadata(
            id=FieldId(2166604),
            name="Sourced by",
            value_type=FieldValueType.DROPDOWN,
            allows_multiple=True,
            dropdown_options=[
                DropdownOption(id=DropdownOptionId(5064548), text="YG"),
                DropdownOption(id=DropdownOptionId(5064549), text="AB"),
            ],
        ),
        FieldMetadata(
            id=FieldId(100),
            name="Status",
            value_type=FieldValueType.DROPDOWN,
            allows_multiple=False,
            dropdown_options=[
                DropdownOption(id=DropdownOptionId(200), text="Active"),
                DropdownOption(id=DropdownOptionId(201), text="Closed"),
            ],
        ),
    ]
    return CLIFieldResolver(fields)


@pytest.mark.req("CLI-DROPDOWN-MULTI-FIX")
class TestResolveDropdownValue:
    """Tests for resolve_dropdown_value with dropdown-multi fields."""

    def test_dropdown_multi_resolves_text_to_array(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """dropdown-multi should resolve text to array of option objects."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value("field-2166604", "YG")
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}]

    def test_dropdown_multi_resolves_id_to_array(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """dropdown-multi should resolve numeric ID to array of option objects."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value("field-2166604", "5064548")
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}]

    def test_dropdown_multi_case_insensitive(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """dropdown-multi should resolve case-insensitively."""
        value, _type_str = dropdown_multi_resolver.resolve_dropdown_value("field-2166604", "yg")
        assert value == [{"dropdownOptionId": 5064548}]

    def test_dropdown_multi_invalid_option_raises(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """dropdown-multi should raise CLIError for unknown option text."""
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="not found"):
            dropdown_multi_resolver.resolve_dropdown_value("field-2166604", "Unknown")

    def test_regular_dropdown_returns_single_object(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """Regular dropdown should still return single object (not array)."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value("field-100", "Active")
        assert type_str == "dropdown"
        assert value == {"dropdownOptionId": 200}

    def test_text_field_returns_unchanged(self, dropdown_multi_resolver: CLIFieldResolver) -> None:
        """Non-dropdown fields should return value unchanged."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value("field-300", "some text")
        assert type_str == "text"
        assert value == "some text"

    def test_dropdown_multi_list_value_resolves_all(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """List value from --set-json resolves each element for dropdown-multi."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value(
            "field-2166604", ["YG", "AB"]
        )
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}, {"dropdownOptionId": 5064549}]

    def test_dropdown_multi_list_single_element(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """Single-element list resolves to single-element array."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value("field-2166604", ["YG"])
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}]

    def test_dropdown_multi_list_with_ids(self, dropdown_multi_resolver: CLIFieldResolver) -> None:
        """List of numeric IDs resolves correctly."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value(
            "field-2166604", ["5064548", "5064549"]
        )
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}, {"dropdownOptionId": 5064549}]

    def test_list_value_rejected_for_regular_dropdown(
        self, dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """List values should be rejected for non-multi dropdown fields."""
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="only supported for dropdown-multi"):
            dropdown_multi_resolver.resolve_dropdown_value("field-100", ["Active"])


@pytest.mark.req("CLI-DROPDOWN-MULTI-FIX")
class TestV1DropdownMultiPromotion:
    """Tests for V1 API path: value_type=DROPDOWN with allows_multiple=True.

    V1 API returns value_type="dropdown" for both single and multi dropdown
    fields.  resolve_dropdown_value must promote to "dropdown-multi" when
    allows_multiple=True so the API payload format is correct.
    """

    def test_v1_dropdown_multi_text_wraps_in_array(
        self, v1_dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """V1 dropdown+allows_multiple=True should wrap in array and return 'dropdown-multi'."""
        value, type_str = v1_dropdown_multi_resolver.resolve_dropdown_value("field-2166604", "YG")
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}]

    def test_v1_dropdown_multi_id_wraps_in_array(
        self, v1_dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """V1 dropdown+allows_multiple=True should wrap numeric ID in array."""
        value, type_str = v1_dropdown_multi_resolver.resolve_dropdown_value(
            "field-2166604", "5064548"
        )
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}]

    def test_v1_dropdown_multi_list_value(
        self, v1_dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """V1 dropdown+allows_multiple=True should handle list values."""
        value, type_str = v1_dropdown_multi_resolver.resolve_dropdown_value(
            "field-2166604", ["YG", "AB"]
        )
        assert type_str == "dropdown-multi"
        assert value == [{"dropdownOptionId": 5064548}, {"dropdownOptionId": 5064549}]

    def test_v1_single_dropdown_unchanged(
        self, v1_dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """V1 dropdown+allows_multiple=False should still return single object."""
        value, type_str = v1_dropdown_multi_resolver.resolve_dropdown_value("field-100", "Active")
        assert type_str == "dropdown"
        assert value == {"dropdownOptionId": 200}

    def test_v1_single_dropdown_rejects_list(
        self, v1_dropdown_multi_resolver: CLIFieldResolver
    ) -> None:
        """V1 single dropdown should reject list values."""
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="only supported for dropdown-multi"):
            v1_dropdown_multi_resolver.resolve_dropdown_value("field-100", ["Active"])


# ============================================================================
# Entity-reference field tests (person, company, person-multi, company-multi)
# ============================================================================


@pytest.fixture
def entity_ref_resolver() -> CLIFieldResolver:
    """Create a CLI FieldResolver with person/company field metadata."""
    fields = [
        FieldMetadata(
            id=FieldId(400),
            name="Owner",
            value_type=FieldValueType.PERSON,
        ),
        FieldMetadata(
            id=FieldId(401),
            name="Team Members",
            value_type=FieldValueType.PERSON_MULTI,
        ),
        FieldMetadata(
            id=FieldId(402),
            name="Parent Company",
            value_type=FieldValueType.COMPANY,
        ),
        FieldMetadata(
            id=FieldId(403),
            name="Related Companies",
            value_type=FieldValueType.COMPANY_MULTI,
        ),
        FieldMetadata(
            id=FieldId(300),
            name="Notes",
            value_type=FieldValueType.TEXT,
        ),
        FieldMetadata(
            id=FieldId(500),
            name="Amount",
            value_type=FieldValueType.NUMBER,
        ),
    ]
    return CLIFieldResolver(fields)


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestResolveEntityRefValue:
    """Tests for resolve_field_value with person/company fields."""

    def test_person_string_id_wraps(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Person field wraps string ID in {"id": int}."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-400", "26229794")
        assert type_str == "person"
        assert value == {"id": 26229794}

    def test_person_int_id_wraps(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Person field wraps int ID in {"id": int}."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-400", 26229794)
        assert type_str == "person"
        assert value == {"id": 26229794}

    def test_company_string_id_wraps(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Company field wraps string ID in {"id": int}."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-402", "789")
        assert type_str == "company"
        assert value == {"id": 789}

    def test_company_int_id_wraps(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Company field wraps int ID in {"id": int}."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-402", 789)
        assert type_str == "company"
        assert value == {"id": 789}

    def test_person_multi_single_value_wraps_in_array(
        self, entity_ref_resolver: CLIFieldResolver
    ) -> None:
        """Person-multi wraps single ID in [{"id": int}]."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-401", "12345")
        assert type_str == "person-multi"
        assert value == [{"id": 12345}]

    def test_person_multi_list_value(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Person-multi with list input wraps each in {"id": int}."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-401", ["111", "222"])
        assert type_str == "person-multi"
        assert value == [{"id": 111}, {"id": 222}]

    def test_company_multi_single_value(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Company-multi wraps single ID in [{"id": int}]."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-403", "999")
        assert type_str == "company-multi"
        assert value == [{"id": 999}]

    def test_company_multi_list_value(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Company-multi with list input wraps each in {"id": int}."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-403", ["100", "200"])
        assert type_str == "company-multi"
        assert value == [{"id": 100}, {"id": 200}]

    def test_person_zero_id_accepted(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Zero is a valid entity ID (used in test fixtures)."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-400", "0")
        assert type_str == "person"
        assert value == {"id": 0}


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestResolveEntityRefValidation:
    """Validation tests for entity-reference field resolution."""

    def test_person_non_numeric_string_raises(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Person field rejects non-numeric string."""
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="Invalid entity ID"):
            entity_ref_resolver.resolve_field_value("field-400", "not-a-number")

    def test_person_boolean_raises(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Person field rejects boolean values."""
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="Invalid entity ID"):
            entity_ref_resolver.resolve_field_value("field-400", True)

    def test_person_list_on_non_multi_raises(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """List values rejected for non-multi person field."""
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="List values not supported"):
            entity_ref_resolver.resolve_field_value("field-400", ["111", "222"])

    def test_company_list_on_non_multi_raises(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """List values rejected for non-multi company field."""
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="List values not supported"):
            entity_ref_resolver.resolve_field_value("field-402", ["111", "222"])


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestResolveFieldValueBackwardCompat:
    """Regression tests: resolve_dropdown_value alias still works."""

    def test_dropdown_via_alias(self, dropdown_multi_resolver: CLIFieldResolver) -> None:
        """resolve_dropdown_value alias resolves dropdown correctly."""
        value, type_str = dropdown_multi_resolver.resolve_dropdown_value("field-100", "Active")
        assert type_str == "dropdown"
        assert value == {"dropdownOptionId": 200}

    def test_text_passthrough(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Text fields pass through unchanged."""
        value, type_str = entity_ref_resolver.resolve_field_value("field-300", "hello")
        assert type_str == "text"
        assert value == "hello"

    def test_number_coerced(self, entity_ref_resolver: CLIFieldResolver) -> None:
        """Numeric strings become numbers: the V2 API rejects "42" for a number field."""
        assert entity_ref_resolver.resolve_field_value("field-500", "42") == (42, "number")
        assert entity_ref_resolver.resolve_field_value("field-500", " 4.5 ") == (4.5, "number")
        assert entity_ref_resolver.resolve_field_value("field-500", 7) == (7, "number")

    @pytest.mark.parametrize("bad", ["abc", "", "nan", "inf", True, [1]])
    def test_number_invalid_rejected(
        self, entity_ref_resolver: CLIFieldResolver, bad: object
    ) -> None:
        with pytest.raises(CLIError):
            entity_ref_resolver.resolve_field_value("field-500", bad)


@pytest.mark.req("CLI-ENTITY-REF-FIELD-FIX")
class TestExtractEntityId:
    """Tests for _extract_entity_id helper."""

    def test_dict_with_int_id(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id({"id": 123}) == 123

    def test_dict_with_string_id(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id({"id": "456"}) == 456

    def test_scalar_int(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id(789) == 789

    def test_scalar_string(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id("101") == 101

    def test_none_returns_none(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id(None) is None

    def test_bool_returns_none(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id(True) is None

    def test_unparseable_string_returns_none(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id("not-a-number") is None

    def test_dict_without_id_returns_none(self) -> None:
        from affinity.cli.field_utils import _extract_entity_id

        assert _extract_entity_id({"name": "test"}) is None
