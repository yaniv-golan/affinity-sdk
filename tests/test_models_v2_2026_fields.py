"""Response fields added to the V2 spec between 2026-02 and 2026-10.

The V2 spec marks several of these required, but the same models also parse V1 payloads
(which never carry them), so every one must stay optional with a safe default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from affinity.models.entities import (
    AffinityList,
    FieldMetadata,
    ListEntry,
    ListSummary,
    Opportunity,
)


class TestOpportunityRestriction:
    def test_v2_payload_keeps_list_name_and_restriction_flags(self) -> None:
        opp = Opportunity.model_validate(
            {
                "id": 1,
                "name": "[Hidden]",
                "listId": 10,
                "listName": "Dealflow",
                "isRestricted": True,
                "isRedacted": True,
            }
        )
        assert opp.list_name == "Dealflow"
        assert opp.is_restricted is True
        assert opp.is_redacted is True

    def test_v1_payload_without_new_fields_defaults_safely(self) -> None:
        opp = Opportunity.model_validate({"id": 1, "name": "Series A", "list_id": 10})
        assert opp.list_name is None
        assert opp.is_restricted is False
        assert opp.is_redacted is False


class TestListCreatedAt:
    def test_affinity_list_created_at(self) -> None:
        lst = AffinityList.model_validate(
            {
                "id": 10,
                "name": "Dealflow",
                "type": 8,
                "isPublic": True,
                "ownerId": 5,
                "creatorId": 5,
                "createdAt": "2024-03-07T20:21:42Z",
            }
        )
        assert lst.created_at == datetime(2024, 3, 7, 20, 21, 42, tzinfo=timezone.utc)

    def test_affinity_list_v1_without_created_at(self) -> None:
        lst = AffinityList.model_validate(
            {"id": 10, "name": "Dealflow", "type": 8, "public": True, "owner_id": 5}
        )
        assert lst.created_at is None

    def test_list_summary_created_at(self) -> None:
        summary = ListSummary.model_validate({"id": 10, "createdAt": "2024-03-07T20:21:42Z"})
        assert summary.created_at == datetime(2024, 3, 7, 20, 21, 42, tzinfo=timezone.utc)
        assert ListSummary.model_validate({"id": 10}).created_at is None


class TestListEntryListName:
    def test_v2_list_entry_keeps_list_name(self) -> None:
        entry = ListEntry.model_validate(
            {
                "id": 7,
                "listId": 10,
                "listName": "All companies",
                "createdAt": "2023-01-01T00:00:00Z",
                "fields": [],
            }
        )
        assert entry.list_name == "All companies"

    def test_list_entry_without_list_name(self) -> None:
        entry = ListEntry.model_validate(
            {"id": 7, "list_id": 10, "created_at": "2023-01-01T00:00:00Z"}
        )
        assert entry.list_name is None


_FIELD_BASE: dict[str, Any] = {"id": "field-1", "name": "HQ", "valueType": "text", "type": "list"}


class TestFieldMetadataAdditions:
    def test_created_at_and_description(self) -> None:
        meta = FieldMetadata.model_validate(
            {
                **_FIELD_BASE,
                "createdAt": "2024-03-07T20:21:42Z",
                "description": "Where this company is headquartered",
            }
        )
        assert meta.created_at == datetime(2024, 3, 7, 20, 21, 42, tzinfo=timezone.utc)
        assert meta.description == "Where this company is headquartered"

    def test_built_in_field_has_null_created_at_and_description(self) -> None:
        meta = FieldMetadata.model_validate({**_FIELD_BASE, "createdAt": None, "description": None})
        assert meta.created_at is None
        assert meta.description is None

    def test_filterability_not_requested_is_unknown_not_false(self) -> None:
        """Absent means 'not requested' - must not read as 'not filterable'."""
        meta = FieldMetadata.model_validate(_FIELD_BASE)
        assert meta.filterability is None
        assert meta.is_filterable is None
        assert meta.is_sortable is None

    def test_filterability_null_means_not_filterable(self) -> None:
        meta = FieldMetadata.model_validate(
            {**_FIELD_BASE, "filterability": None, "sortability": None}
        )
        assert meta.is_filterable is False
        assert meta.is_sortable is False

    def test_filterability_object_means_filterable(self) -> None:
        meta = FieldMetadata.model_validate(
            {
                **_FIELD_BASE,
                "filterability": {"type": "field-only", "operators": ["=", "=~"]},
                "sortability": {"type": "field-only"},
            }
        )
        assert meta.is_filterable is True
        assert meta.is_sortable is True
        assert meta.filterability == {"type": "field-only", "operators": ["=", "=~"]}
