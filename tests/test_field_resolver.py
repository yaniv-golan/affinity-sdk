"""
Tests for FieldResolver helper class.
"""

from __future__ import annotations

import pytest

from affinity.field_resolver import FieldResolver
from affinity.models.entities import Company, DropdownOption, FieldMetadata
from affinity.models.types import DropdownOptionId, FieldId, FieldValueType, ResolveMode


@pytest.mark.req("SDK-FIELD-RESOLVER")
class TestFieldResolver:
    """Tests for FieldResolver helper."""

    @pytest.fixture
    def resolver(self):
        """Create resolver with sample field metadata."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Status", value_type=FieldValueType.TEXT),
            FieldMetadata(id=FieldId(2), name="Stage", value_type=FieldValueType.DROPDOWN),
        ]
        return FieldResolver(fields)

    def test_get_by_name(self, resolver) -> None:
        """get() should find field by name and extract value."""
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [{"id": "field-1", "value": {"data": "Active"}}],
            }
        )
        assert resolver.get(company, "Status") == "Active"

    def test_get_case_insensitive(self, resolver) -> None:
        """get() should be case-insensitive for field names."""
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [{"id": "field-1", "value": {"data": "Active"}}],
            }
        )
        assert resolver.get(company, "STATUS") == "Active"
        assert resolver.get(company, "status") == "Active"

    def test_get_resolve_dropdowns(self, resolver) -> None:
        """get(resolve=ResolveMode.TEXT) should return option text."""
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-2",
                        "value": {
                            "type": "dropdown",
                            "data": {"dropdownOptionId": 101, "text": "Negotiation"},
                        },
                    }
                ],
            }
        )
        raw = resolver.get(company, "Stage")
        assert isinstance(raw, DropdownOption)
        assert raw.text == "Negotiation"
        assert resolver.get(company, "Stage", resolve=ResolveMode.TEXT) == "Negotiation"

    def test_get_resolve_ranked_dropdown(self, resolver) -> None:
        """get(resolve=ResolveMode.TEXT) should handle ranked-dropdown."""
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-2",
                        "value": {
                            "type": "ranked-dropdown",
                            "data": {
                                "dropdownOptionId": 100,
                                "text": "Lead",
                                "rank": 1,
                                "color": "blue",
                            },
                        },
                    }
                ],
            }
        )
        raw = resolver.get(company, "Stage")
        assert isinstance(raw, DropdownOption)
        assert raw.text == "Lead"
        assert raw.rank == 1
        assert raw.color == "blue"
        assert resolver.get(company, "Stage", resolve=ResolveMode.TEXT) == "Lead"

    def test_get_resolve_dropdown_multi(self) -> None:
        """get(resolve=ResolveMode.TEXT) should resolve dropdown-multi to text list."""
        fields = [
            FieldMetadata(id=FieldId(3), name="Tags", value_type=FieldValueType.DROPDOWN),
        ]
        resolver = FieldResolver(fields)
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-3",
                        "value": {
                            "type": "dropdown-multi",
                            "data": [
                                {"dropdownOptionId": 10, "text": "VIP"},
                                {"dropdownOptionId": 11, "text": "Partner"},
                            ],
                        },
                    }
                ],
            }
        )
        raw = resolver.get(company, "Tags")
        assert len(raw) == 2
        assert all(isinstance(opt, DropdownOption) for opt in raw)
        assert resolver.get(company, "Tags", resolve=ResolveMode.TEXT) == ["VIP", "Partner"]

    def test_resolve_dropdown_returns_text(self) -> None:
        """_resolve_dropdown should return DropdownOption.text."""
        fields = [
            FieldMetadata(id=FieldId(1), name="F", value_type=FieldValueType.DROPDOWN),
        ]
        resolver = FieldResolver(fields)
        option = DropdownOption(id=DropdownOptionId(999), text="Unknown")
        assert resolver._resolve_dropdown(option) == "Unknown"

    def test_resolve_person(self) -> None:
        """resolve=TEXT should resolve person fields to display name."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Owner", value_type=FieldValueType.PERSON),
        ]
        resolver = FieldResolver(fields)
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-1",
                        "value": {
                            "type": "person",
                            "data": {"id": 42, "firstName": "Ada", "lastName": "Lovelace"},
                        },
                    }
                ],
            }
        )
        assert resolver.get(company, "Owner") == {
            "id": 42,
            "firstName": "Ada",
            "lastName": "Lovelace",
        }
        assert resolver.get(company, "Owner", resolve=ResolveMode.TEXT) == "Ada Lovelace"

    def test_resolve_person_multi(self) -> None:
        """resolve=TEXT should resolve person-multi to list of names."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Team", value_type=FieldValueType.PERSON_MULTI),
        ]
        resolver = FieldResolver(fields)
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-1",
                        "value": [
                            {"data": {"id": 1, "firstName": "Ada", "lastName": "Lovelace"}},
                            {"data": {"id": 2, "firstName": "Bob", "lastName": "Smith"}},
                        ],
                    }
                ],
            }
        )
        assert resolver.get(company, "Team", resolve=ResolveMode.TEXT) == [
            "Ada Lovelace",
            "Bob Smith",
        ]

    def test_resolve_company_field(self) -> None:
        """resolve=TEXT should resolve company fields to name."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Parent", value_type=FieldValueType.COMPANY),
        ]
        resolver = FieldResolver(fields)
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-1",
                        "value": {"data": {"id": 5, "name": "Acme Corp", "domain": "acme.com"}},
                    }
                ],
            }
        )
        assert resolver.get(company, "Parent", resolve=ResolveMode.TEXT) == "Acme Corp"

    def test_resolve_location(self) -> None:
        """resolve=TEXT should resolve location fields to string."""
        fields = [
            FieldMetadata(id=FieldId(1), name="HQ", value_type=FieldValueType.LOCATION),
        ]
        resolver = FieldResolver(fields)
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-1",
                        "value": {
                            "city": "San Francisco",
                            "state": "CA",
                            "country": "United States",
                        },
                    }
                ],
            }
        )
        assert (
            resolver.get(company, "HQ", resolve=ResolveMode.TEXT)
            == "San Francisco, CA, United States"
        )

    def test_resolve_raw_passthrough(self, resolver) -> None:
        """resolve=RAW (default) should return DropdownOption, not text."""
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {
                        "id": "field-2",
                        "value": {
                            "type": "dropdown",
                            "data": {"dropdownOptionId": 101, "text": "Negotiation"},
                        },
                    }
                ],
            }
        )
        # Default (RAW) returns DropdownOption, not resolved text
        raw = resolver.get(company, "Stage")
        assert isinstance(raw, DropdownOption)
        assert raw.id == DropdownOptionId(101)
        assert raw.text == "Negotiation"

    def test_get_many(self, resolver) -> None:
        """get_many() should extract multiple fields."""
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {"id": "field-1", "value": {"data": "Active"}},
                    {
                        "id": "field-2",
                        "value": {
                            "type": "dropdown",
                            "data": {"dropdownOptionId": 100, "text": "Lead"},
                        },
                    },
                ],
            }
        )
        values = resolver.get_many(company, ["Status", "Stage", "Missing"])
        assert values["Status"] == "Active"
        assert isinstance(values["Stage"], DropdownOption)
        assert values["Stage"].text == "Lead"
        assert values["Missing"] is None

    def test_get_warns_when_fields_not_requested(self, resolver) -> None:
        """get() should emit UserWarning when entity.fields.requested is False."""
        company = Company.model_validate({"id": 1, "name": "Test"})
        assert not company.fields.requested

        with pytest.warns(UserWarning, match="Fields were not requested"):
            result = resolver.get(company, "Status")
        assert result is None

    def test_has_field(self, resolver) -> None:
        """has_field() should check field existence case-insensitively."""
        assert resolver.has_field("Status") is True
        assert resolver.has_field("status") is True
        assert resolver.has_field("Nonexistent") is False

    def test_field_names(self, resolver) -> None:
        """field_names() should return all available names."""
        names = resolver.field_names()
        assert "Status" in names
        assert "Stage" in names
        assert len(names) == 2

    def test_get_by_id(self, resolver) -> None:
        """get_by_id() should access field value by ID directly."""
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [{"id": "field-1", "value": {"data": "Active"}}],
            }
        )
        assert resolver.get_by_id(company, FieldId(1)) == "Active"

    def test_duplicate_field_name_warns_on_get(self) -> None:
        """FieldResolver should warn at get() time for ambiguous field names, not __init__."""
        import warnings as _warnings

        fields = [
            FieldMetadata(
                id=FieldId(1),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="dealroom",
            ),
            FieldMetadata(
                id=FieldId(2),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="affinity-data",
            ),
        ]
        # No warning at init time
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            resolver = FieldResolver(fields)
        assert not any("Ambiguous" in str(x.message) for x in w)

        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {"id": "field-1", "value": {"data": "Dealroom desc"}},
                    {"id": "field-2", "value": {"data": "Affinity desc"}},
                ],
            }
        )

        # Bare name warns once and returns first match
        with pytest.warns(UserWarning, match="Ambiguous field name"):
            result = resolver.get(company, "Description")
        assert result == "Dealroom desc"

        # Second access doesn't warn again
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            result2 = resolver.get(company, "Description")
        assert result2 == "Dealroom desc"
        assert not any("Ambiguous" in str(x.message) for x in w)

    def test_source_qualified_field_access(self) -> None:
        """Source-qualified names should access specific enrichment fields without warning."""
        import warnings as _warnings

        fields = [
            FieldMetadata(
                id=FieldId(1),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="dealroom",
            ),
            FieldMetadata(
                id=FieldId(2),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="affinity-data",
            ),
        ]
        resolver = FieldResolver(fields)

        company = Company.model_validate(
            {
                "id": 1,
                "name": "Test",
                "fields": [
                    {"id": "field-1", "value": {"data": "Dealroom desc"}},
                    {"id": "field-2", "value": {"data": "Affinity desc"}},
                ],
            }
        )

        # Source-qualified access — no warning
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            assert resolver.get(company, "dealroom:Description") == "Dealroom desc"
            assert resolver.get(company, "affinity-data:Description") == "Affinity desc"
        assert not any("Ambiguous" in str(x.message) for x in w)

        # has_field recognizes both forms
        assert resolver.has_field("Description")
        assert resolver.has_field("dealroom:Description")
        assert resolver.has_field("affinity-data:Description")

    def test_field_names_with_ambiguous_fields(self) -> None:
        """field_names() should return source-qualified names for ambiguous fields."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Status", value_type=FieldValueType.TEXT),
            FieldMetadata(
                id=FieldId(2),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="dealroom",
            ),
            FieldMetadata(
                id=FieldId(3),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="affinity-data",
            ),
        ]
        resolver = FieldResolver(fields)
        names = resolver.field_names()
        assert "Status" in names
        assert "dealroom:Description" in names
        assert "affinity-data:Description" in names
        # Bare "Description" should NOT appear since it's ambiguous
        assert "Description" not in names

    def test_get_delegates_to_list_entry_entity(self, resolver) -> None:
        """get() should delegate to entry.entity when entry.fields not requested."""
        import warnings as _warnings

        from affinity.models.entities import ListEntryWithEntity

        entry = ListEntryWithEntity.model_validate(
            {
                "id": 1,
                "listId": 100,
                "createdAt": "2026-01-01T00:00:00Z",
                "type": "company",
                "entity": {
                    "id": 1,
                    "name": "Test Co",
                    "fields": [{"id": "field-1", "value": {"data": "Active"}}],
                },
            }
        )
        assert not entry.fields.requested
        assert entry.entity.fields.requested

        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            result = resolver.get(entry, "Status")
        assert result == "Active"
        assert not any("Fields were not requested" in str(x.message) for x in w)

    def test_get_by_id_delegates_to_list_entry_entity(self, resolver) -> None:
        """get_by_id() should delegate to entry.entity when entry.fields not requested."""
        from affinity.models.entities import ListEntryWithEntity

        entry = ListEntryWithEntity.model_validate(
            {
                "id": 1,
                "listId": 100,
                "createdAt": "2026-01-01T00:00:00Z",
                "type": "company",
                "entity": {
                    "id": 1,
                    "name": "Test Co",
                    "fields": [{"id": "field-1", "value": {"data": "Active"}}],
                },
            }
        )
        result = resolver.get_by_id(entry, FieldId(1))
        assert result == "Active"

    def test_get_many_delegates_to_list_entry_entity(self, resolver) -> None:
        """get_many() should work with ListEntryWithEntity via delegation."""
        from affinity.models.entities import ListEntryWithEntity

        entry = ListEntryWithEntity.model_validate(
            {
                "id": 1,
                "listId": 100,
                "createdAt": "2026-01-01T00:00:00Z",
                "type": "company",
                "entity": {
                    "id": 1,
                    "name": "Test Co",
                    "fields": [
                        {"id": "field-1", "value": {"data": "Active"}},
                        {
                            "id": "field-2",
                            "value": {
                                "type": "dropdown",
                                "data": {"dropdownOptionId": 100, "text": "Lead"},
                            },
                        },
                    ],
                },
            }
        )
        values = resolver.get_many(entry, ["Status", "Stage"])
        assert values["Status"] == "Active"
        assert isinstance(values["Stage"], DropdownOption)
        assert values["Stage"].text == "Lead"

    def test_get_no_delegation_when_entity_is_none(self, resolver) -> None:
        """get() should not delegate when entry.entity is None."""
        from affinity.models.entities import ListEntryWithEntity

        entry = ListEntryWithEntity.model_validate(
            {
                "id": 1,
                "listId": 100,
                "createdAt": "2026-01-01T00:00:00Z",
                "type": "company",
            }
        )
        assert entry.entity is None
        assert not entry.fields.requested

        with pytest.warns(UserWarning, match="Fields were not requested"):
            result = resolver.get(entry, "Status")
        assert result is None

    def test_get_no_delegation_when_entry_fields_requested(self, resolver) -> None:
        """get() should use entry's own fields when entry.fields.requested is True."""
        from affinity.models.entities import ListEntryWithEntity

        entry = ListEntryWithEntity.model_validate(
            {
                "id": 1,
                "listId": 100,
                "createdAt": "2026-01-01T00:00:00Z",
                "type": "company",
                "fields": [{"id": "field-1", "value": {"data": "FromEntry"}}],
                "entity": {
                    "id": 1,
                    "name": "Test Co",
                    "fields": [{"id": "field-1", "value": {"data": "FromEntity"}}],
                },
            }
        )
        assert entry.fields.requested
        assert entry.entity.fields.requested

        result = resolver.get(entry, "Status")
        assert result == "FromEntry"


@pytest.mark.req("SDK-FIELD-RESOLVER")
class TestFindField:
    """Tests for FieldResolver.find_field() method."""

    def test_find_field_basic_lookup(self) -> None:
        """find_field() should return FieldMetadata for a known field."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Status", value_type=FieldValueType.TEXT),
            FieldMetadata(id=FieldId(2), name="Stage", value_type=FieldValueType.DROPDOWN),
        ]
        resolver = FieldResolver(fields)
        result = resolver.find_field("Status")
        assert result is not None
        assert result.id == FieldId(1)
        assert result.name == "Status"

    def test_find_field_case_insensitive(self) -> None:
        """find_field() should match case-insensitively."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Status", value_type=FieldValueType.TEXT),
        ]
        resolver = FieldResolver(fields)
        result = resolver.find_field("status")
        assert result is not None
        assert result.id == FieldId(1)

        result2 = resolver.find_field("STATUS")
        assert result2 is not None
        assert result2.id == FieldId(1)

    def test_find_field_missing_returns_none(self) -> None:
        """find_field() should return None for unknown fields."""
        fields = [
            FieldMetadata(id=FieldId(1), name="Status", value_type=FieldValueType.TEXT),
        ]
        resolver = FieldResolver(fields)
        assert resolver.find_field("Nonexistent") is None

    def test_find_field_ambiguous_with_enrichment_raises(self) -> None:
        """find_field() raises AmbiguousFieldError for ambiguous enrichment names."""
        from affinity.field_resolver import AmbiguousFieldError

        fields = [
            FieldMetadata(
                id=FieldId(1),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="dealroom",
            ),
            FieldMetadata(
                id=FieldId(2),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="affinity-data",
            ),
        ]
        resolver = FieldResolver(fields)

        with pytest.raises(
            AmbiguousFieldError, match="Ambiguous field name 'Description'"
        ) as exc_info:
            resolver.find_field("Description")

        err = exc_info.value
        assert err.field_name == "Description"
        assert len(err.candidates) == 2
        assert "dealroom:Description" in str(err)

    def test_find_field_ambiguous_without_enrichment_raises(self) -> None:
        """find_field() should raise AmbiguousFieldError with ID hint when no enrichment sources."""
        from affinity.field_resolver import AmbiguousFieldError

        fields = [
            FieldMetadata(id=FieldId(10), name="Notes", value_type=FieldValueType.TEXT),
            FieldMetadata(id=FieldId(20), name="Notes", value_type=FieldValueType.TEXT),
        ]
        resolver = FieldResolver(fields)

        with pytest.raises(AmbiguousFieldError, match="Candidate IDs") as exc_info:
            resolver.find_field("Notes")

        err = exc_info.value
        assert err.field_name == "Notes"
        assert len(err.candidates) == 2
        # No source-qualified hint when no enrichment sources
        assert "source:name" not in str(err)

    def test_find_field_source_qualified_disambiguation(self) -> None:
        """find_field() with source-qualified name should return the specific field."""
        fields = [
            FieldMetadata(
                id=FieldId(1),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="dealroom",
            ),
            FieldMetadata(
                id=FieldId(2),
                name="Description",
                value_type=FieldValueType.TEXT,
                enrichment_source="affinity-data",
            ),
        ]
        resolver = FieldResolver(fields)

        result = resolver.find_field("dealroom:Description")
        assert result is not None
        assert result.id == FieldId(1)
        assert result.enrichment_source == "dealroom"

        result2 = resolver.find_field("affinity-data:Description")
        assert result2 is not None
        assert result2.id == FieldId(2)
