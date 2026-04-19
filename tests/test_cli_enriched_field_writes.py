"""Tests for enriched field write support (plan v3.7).

Covers:
- FieldResolver.to_v1_numeric method (new): enriched ID → V1 numeric via name + enrichment_source
- FieldResolver.resolve_field_name_or_id ID-branch accepts any _by_id member (not just "field-*")
- find_field_values_for_field normalizes both sides (V1 raw int ↔ V2 "field-<n>")
- FieldService.list(skip_cache=True) bypasses the 5-min cache
- EnrichedFieldNotWritableError exists, subclass of UnsupportedOperationError, exported

TDD — these tests encode the intended behavior before implementation lands.
"""

from __future__ import annotations

import httpx
import pytest

from affinity import Affinity
from affinity.cli.field_utils import FieldResolver as CLIFieldResolver
from affinity.cli.field_utils import find_field_values_for_field
from affinity.models.entities import FieldMetadata
from affinity.models.types import EnrichedFieldId, FieldId, FieldValueType
from affinity.types import EntityType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def person_resolver() -> CLIFieldResolver:
    """CLI FieldResolver populated with V2 person field metadata (enriched + RI + global)."""
    fields = [
        # Writable enriched (affinity-data prefix)
        FieldMetadata(
            id=EnrichedFieldId("affinity-data-phone-number"),
            name="Phone Number",
            value_type=FieldValueType.FILTERABLE_TEXT_MULTI,
            type="enriched",
            enrichment_source="affinity-data",
        ),
        # Writable relationship-intelligence (no affinity-data prefix)
        FieldMetadata(
            id=EnrichedFieldId("source-of-introduction"),
            name="Source of Introduction",
            value_type=FieldValueType.PERSON,
            type="relationship-intelligence",
            enrichment_source=None,
        ),
        # Unwritable enriched (no V1 twin)
        FieldMetadata(
            id=EnrichedFieldId("affinity-data-current-organization"),
            name="Current Organization",
            value_type=FieldValueType.COMPANY,
            type="enriched",
            enrichment_source="affinity-data",
        ),
        # Regular global field
        FieldMetadata(
            id=FieldId(501054),
            name="tag",
            value_type=FieldValueType.DROPDOWN_MULTI,
            type="global",
        ),
    ]
    return CLIFieldResolver(fields)


@pytest.fixture
def company_resolver() -> CLIFieldResolver:
    """CLI FieldResolver for company — has collision on 'Industry' (affinity-data vs dealroom)."""
    fields = [
        FieldMetadata(
            id=EnrichedFieldId("affinity-data-industry"),
            name="Industry",
            value_type=FieldValueType.FILTERABLE_TEXT_MULTI,
            type="enriched",
            enrichment_source="affinity-data",
        ),
        FieldMetadata(
            id=EnrichedFieldId("dealroom-industry"),
            name="Industry",
            value_type=FieldValueType.FILTERABLE_TEXT_MULTI,
            type="enriched",
            enrichment_source="dealroom",
        ),
    ]
    return CLIFieldResolver(fields)


def _mock_v1_fields_transport(entity_to_rows: dict[int, list[dict]]) -> httpx.MockTransport:
    """Mock V1 /fields, dispatching by entity_type query param.

    entity_to_rows: {entity_type_int: [row_dict, ...]}
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fields":
            ent = request.url.params.get("entity_type")
            rows = entity_to_rows.get(int(ent) if ent else -1, [])
            return httpx.Response(200, json={"data": rows})
        return httpx.Response(404, json={"errors": [{"message": "not mocked"}]})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# FieldResolver.resolve_field_name_or_id — ID-branch extension (C3)
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
class TestResolveFieldNameOrIdAcceptsEnriched:
    """ID-branch must accept any _by_id member, not just 'field-*' prefix."""

    def test_accepts_affinity_data_enriched_id(self, person_resolver: CLIFieldResolver) -> None:
        result = person_resolver.resolve_field_name_or_id("affinity-data-phone-number")
        assert result == "affinity-data-phone-number"

    def test_accepts_non_affinity_data_enriched_id(self, person_resolver: CLIFieldResolver) -> None:
        """source-of-introduction doesn't have affinity-data- prefix; must still work."""
        result = person_resolver.resolve_field_name_or_id("source-of-introduction")
        assert result == "source-of-introduction"

    def test_accepts_regular_field_id(self, person_resolver: CLIFieldResolver) -> None:
        result = person_resolver.resolve_field_name_or_id("field-501054")
        assert result == "field-501054"

    def test_name_lookup_still_works_for_enriched(self, person_resolver: CLIFieldResolver) -> None:
        result = person_resolver.resolve_field_name_or_id("Phone Number")
        assert result == "affinity-data-phone-number"

    def test_unknown_field_id_raises(self, person_resolver: CLIFieldResolver) -> None:
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError, match="not found"):
            person_resolver.resolve_field_name_or_id("field-99999999")


# ---------------------------------------------------------------------------
# find_field_values_for_field — normalization refactor (C2)
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
class TestFindFieldValuesForFieldNormalization:
    """Must match V1 numeric int ↔ 'field-<n>' ↔ '<n>' forms bidirectionally."""

    def test_matches_v1_int_response_against_canonical_string(self) -> None:
        """V1 returns fieldId=260415 (int); scan with 'field-260415' must match."""
        values = [{"fieldId": 260415, "value": "+1-555-TEST-0199", "id": 1}]
        result = find_field_values_for_field(field_values=values, field_id="field-260415")
        assert len(result) == 1

    def test_matches_numeric_string_against_canonical(self) -> None:
        values = [{"fieldId": "260415", "value": "x", "id": 1}]
        result = find_field_values_for_field(field_values=values, field_id="field-260415")
        assert len(result) == 1

    def test_matches_canonical_against_canonical(self) -> None:
        values = [{"fieldId": "field-260415", "value": "x", "id": 1}]
        result = find_field_values_for_field(field_values=values, field_id="field-260415")
        assert len(result) == 1

    def test_enriched_literal_preserved_through_fallback(self) -> None:
        """Enriched string on both sides matches as plain string."""
        values = [{"fieldId": "affinity-data-phone-number", "value": "x", "id": 1}]
        result = find_field_values_for_field(
            field_values=values, field_id="affinity-data-phone-number"
        )
        assert len(result) == 1

    def test_non_matching_returns_empty(self) -> None:
        values = [{"fieldId": 99999, "value": "x", "id": 1}]
        result = find_field_values_for_field(field_values=values, field_id="field-260415")
        assert result == []


# ---------------------------------------------------------------------------
# FieldResolver.to_v1_numeric — new method
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
class TestToV1Numeric:
    """New FieldResolver.to_v1_numeric method."""

    def test_field_prefix_converts_directly(self, person_resolver: CLIFieldResolver) -> None:
        """'field-501054' → 501054, no V1 lookup needed."""
        client = Affinity(
            api_key="test",
            max_retries=0,
            transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
        )
        try:
            result = person_resolver.to_v1_numeric(client, "field-501054", "person")
            assert result == 501054
        finally:
            client.close()

    def test_enriched_id_resolves_via_name_and_source(
        self, person_resolver: CLIFieldResolver
    ) -> None:
        """affinity-data-phone-number → V1 twin 260415 via (name, enrichment_source)."""
        transport = _mock_v1_fields_transport(
            {
                0: [
                    {
                        "id": 260415,
                        "name": "Phone Number",
                        "list_id": None,
                        "enrichment_source": "affinity-data",
                        "value_type": 6,
                    }
                ]
            }
        )
        with Affinity(api_key="test", max_retries=0, transport=transport) as client:
            result = person_resolver.to_v1_numeric(client, "affinity-data-phone-number", "person")
            assert result == 260415

    def test_collision_resolved_by_enrichment_source(
        self, company_resolver: CLIFieldResolver
    ) -> None:
        """Two V1 global 'Industry' rows — enrichment_source picks the affinity-data one."""
        transport = _mock_v1_fields_transport(
            {
                1: [
                    {
                        "id": 808056,
                        "name": "Industry",
                        "list_id": None,
                        "enrichment_source": "affinity-data",
                        "value_type": 2,
                    },
                    {
                        "id": 2820308,
                        "name": "Industry",
                        "list_id": None,
                        "enrichment_source": "dealroom",
                        "value_type": 2,
                    },
                ]
            }
        )
        with Affinity(api_key="test", max_retries=0, transport=transport) as client:
            assert (
                company_resolver.to_v1_numeric(client, "affinity-data-industry", "company")
                == 808056
            )
            assert company_resolver.to_v1_numeric(client, "dealroom-industry", "company") == 2820308

    def test_v1_none_string_matches_v2_null_source(self, person_resolver: CLIFieldResolver) -> None:
        """V1 enrichment_source='none' must match V2 enrichmentSource=null."""
        transport = _mock_v1_fields_transport(
            {
                0: [
                    {
                        "id": 260417,
                        "name": "Source of Introduction",
                        "list_id": None,
                        "enrichment_source": "none",
                        "value_type": 7,
                    }
                ]
            }
        )
        with Affinity(api_key="test", max_retries=0, transport=transport) as client:
            assert (
                person_resolver.to_v1_numeric(client, "source-of-introduction", "person") == 260417
            )

    def test_no_v1_twin_raises_enriched_not_writable(
        self, person_resolver: CLIFieldResolver
    ) -> None:
        """affinity-data-current-organization has no V1 twin → raises."""
        from affinity.exceptions import EnrichedFieldNotWritableError

        transport = _mock_v1_fields_transport({0: []})  # no V1 fields at all
        with (
            Affinity(api_key="test", max_retries=0, transport=transport) as client,
            pytest.raises(EnrichedFieldNotWritableError),
        ):
            person_resolver.to_v1_numeric(client, "affinity-data-current-organization", "person")

    def test_unknown_enriched_id_raises(self, person_resolver: CLIFieldResolver) -> None:
        """Enriched ID not in resolver metadata → raises."""
        from affinity.exceptions import EnrichedFieldNotWritableError

        transport = _mock_v1_fields_transport({0: []})
        with (
            Affinity(api_key="test", max_retries=0, transport=transport) as client,
            pytest.raises(EnrichedFieldNotWritableError),
        ):
            person_resolver.to_v1_numeric(client, "affinity-data-never-seen-this-before", "person")

    def test_list_scoped_v1_field_ignored(self, person_resolver: CLIFieldResolver) -> None:
        """V1 row with list_id set must NOT match — only list_id=null is a global twin."""
        from affinity.exceptions import EnrichedFieldNotWritableError

        transport = _mock_v1_fields_transport(
            {
                0: [
                    {
                        "id": 9999,
                        "name": "Phone Number",  # same name but list-scoped
                        "list_id": 12345,
                        "enrichment_source": "affinity-data",
                        "value_type": 6,
                    }
                ]
            }
        )
        with (
            Affinity(api_key="test", max_retries=0, transport=transport) as client,
            pytest.raises(EnrichedFieldNotWritableError),
        ):
            person_resolver.to_v1_numeric(client, "affinity-data-phone-number", "person")

    def test_entity_type_string_mapped_to_enum(self, company_resolver: CLIFieldResolver) -> None:
        """CLI layer passes 'company' string — helper maps to EntityType.ORGANIZATION (int=1)."""
        captured_params: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/fields":
                captured_params.append(dict(request.url.params))
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 808056,
                                "name": "Industry",
                                "list_id": None,
                                "enrichment_source": "affinity-data",
                                "value_type": 2,
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        with Affinity(
            api_key="test", max_retries=0, transport=httpx.MockTransport(handler)
        ) as client:
            company_resolver.to_v1_numeric(client, "affinity-data-industry", "company")

        assert captured_params, "V1 /fields should have been called"
        assert captured_params[0].get("entity_type") == "1", (
            f"Expected entity_type=1 (ORGANIZATION), got {captured_params[0]}"
        )


# ---------------------------------------------------------------------------
# FieldService.list(skip_cache=True)
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
class TestFieldServiceSkipCache:
    """New skip_cache param bypasses the 300s TTL."""

    def test_skip_cache_bypasses_cache(self) -> None:
        call_count = 0

        def handler(_req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"data": []})

        transport = httpx.MockTransport(handler)
        with Affinity(
            api_key="test", enable_cache=True, max_retries=0, transport=transport
        ) as client:
            client.fields.list(skip_cache=True)
            client.fields.list(skip_cache=True)
        assert call_count == 2

    def test_default_caches(self) -> None:
        call_count = 0

        def handler(_req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"data": []})

        transport = httpx.MockTransport(handler)
        with Affinity(
            api_key="test", enable_cache=True, max_retries=0, transport=transport
        ) as client:
            client.fields.list()
            client.fields.list()
        assert call_count == 1


# ---------------------------------------------------------------------------
# Exception export
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
class TestEnrichedFieldNotWritableErrorExport:
    def test_class_importable_from_exceptions(self) -> None:
        from affinity.exceptions import EnrichedFieldNotWritableError

        assert issubclass(EnrichedFieldNotWritableError, Exception)

    def test_class_importable_from_affinity_root(self) -> None:
        from affinity import EnrichedFieldNotWritableError  # noqa: F401

    def test_subclass_of_unsupported_operation_error(self) -> None:
        from affinity.exceptions import (
            EnrichedFieldNotWritableError,
            UnsupportedOperationError,
        )

        assert issubclass(EnrichedFieldNotWritableError, UnsupportedOperationError)

    def test_unsupported_operation_error_also_exported(self) -> None:
        """Plan v3.6 M1: both new class AND parent get public export."""
        from affinity import UnsupportedOperationError  # noqa: F401


# ---------------------------------------------------------------------------
# EntityType enum sanity (guards plan C1: no 'COMPANY' member)
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
def test_entity_type_has_organization_not_company() -> None:
    """Regression guard: CLI mapping must use ORGANIZATION, not COMPANY."""
    assert EntityType.ORGANIZATION.value == 1
    assert not hasattr(EntityType, "COMPANY")


# ---------------------------------------------------------------------------
# FieldId(int) coercion (rebuts v3.6 reviewer's C1 false positive)
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
def test_field_id_accepts_int() -> None:
    """FieldValueCreate(field_id=FieldId(260415)) must work — used by the fix."""
    fid = FieldId(260415)
    assert str(fid) == "field-260415"


# ---------------------------------------------------------------------------
# Opportunity xfail (existing broken path, guards against silent revival)
# ---------------------------------------------------------------------------


@pytest.mark.req("SDK-ENRICHED-FIELD-WRITES")
@pytest.mark.xfail(
    strict=True,
    reason="opportunity field command unconditionally raises — list_id wiring missing; "
    "tracked separately. Opportunities have 0 global V1 fields in this tenant.",
)
def test_opportunity_field_command_currently_broken() -> None:
    """When opportunity field gets fixed, this will XPASS and force an update."""
    from affinity.cli.field_utils import fetch_field_metadata

    # Calling without list_id raises today (field_utils.py:45-51)
    transport = httpx.MockTransport(lambda _r: httpx.Response(500))
    with Affinity(api_key="test", max_retries=0, transport=transport) as client:
        # If this stops raising, the xfail fires and we need to update the plan.
        fetch_field_metadata(client=client, entity_type="opportunity", list_id=None)
