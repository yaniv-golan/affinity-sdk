"""Tests for enriched field write support (plan v3.7).

Covers:
- FieldResolver.resolve_field_name_or_id ID-branch accepts any _by_id member (not just "field-*")
- find_field_values_for_field normalizes both sides (V1 raw int ↔ V2 "field-<n>")
- FieldService.list(skip_cache=True) bypasses the 5-min cache

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
def test_unsupported_operation_error_exported() -> None:
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
def test_opportunity_field_set_reaches_the_write() -> None:
    """`opportunity field --set` loads the fields of the opportunity's list (it used to call
    fetch_field_metadata without a list id and always exit 2) and writes through its list
    entry's update-fields PATCH."""
    import json

    from click.testing import CliRunner

    from affinity.cli.main import cli

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((request.method, path))
        if path == "/opportunities/42":
            return httpx.Response(200, json={"id": 42, "list_entries": [{"id": 555}]})
        if path == "/v2/opportunities/42":
            return httpx.Response(200, json={"id": 42, "name": "Deal", "listId": 9})
        if path == "/v2/lists/9/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "field-7", "name": "Amount", "type": "list", "valueType": "number"}
                    ],
                    "pagination": {"nextUrl": None},
                },
            )
        if path == "/field-values" and request.method == "GET":
            return httpx.Response(
                200, json=[{"id": 500, "field_id": 7, "entity_id": 42, "value": 3}]
            )
        lst = {"id": 9, "name": "Deals", "type": 8, "public": False, "owner_id": 1}
        if path == "/v2/lists/9":
            return httpx.Response(200, json={**lst, "isPublic": False, "ownerId": 1})
        if path == "/lists/9":
            return httpx.Response(200, json=lst)
        if path == "/fields":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 7,
                            "name": "Amount",
                            "value_type": 3,
                            "list_id": 9,
                            "allows_multiple": False,
                        }
                    ]
                },
            )
        if path == "/v2/lists/9/list-entries/555/fields" and request.method == "PATCH":
            assert json.loads(request.content)["updates"] == [
                {"id": "field-7", "value": {"type": "number", "data": 5}}
            ]
            return httpx.Response(200, json={"operation": "update-fields"})
        return httpx.Response(404, json={"errors": [{"message": f"unmocked {path}"}]})

    import respx

    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.affinity.co").mock(side_effect=handler)
        result = CliRunner().invoke(
            cli,
            ["--json", "opportunity", "field", "42", "--set", "Amount", "5"],
            env={"AFFINITY_API_KEY": "test"},
        )
    assert result.exit_code == 0, result.output
    assert ("PATCH", "/v2/lists/9/list-entries/555/fields") in seen
    assert not any(method in ("PUT", "DELETE") for method, _ in seen)
