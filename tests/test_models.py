"""
Unit tests for Affinity SDK types and models.
"""

import inspect
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import httpx
import pytest

import affinity.clients.http as http_mod
import affinity.types as types_mod
from affinity import Affinity, AsyncAffinity
from affinity.hooks import RequestInfo
from affinity.models import (
    AffinityList,
    Company,
    FieldMetadata,
    FieldValue,
    FieldValueChange,
    ListEntry,
    Person,
)
from affinity.services.companies import CompanyService
from affinity.services.lists import AsyncListEntryService, ListEntryService, ListService
from affinity.services.persons import PersonService
from affinity.services.v1_only import (
    EntityFileService,
    FieldValueService,
    InteractionService,
    NoteService,
    ReminderService,
)
from affinity.types import (
    CompanyId,
    DropdownOptionId,
    EnrichedFieldId,
    FieldId,
    FieldType,
    FieldValueChangeId,
    FieldValueId,
    FieldValueType,
    InteractionId,
    InteractionType,
    ListId,
    ListType,
    OpportunityId,
    PersonId,
    PersonType,
    TenantId,
    UserId,
    field_id_to_v1_numeric,
)


@pytest.mark.req("TR-003")
class TestTypedIds:
    """Test NewType ID types."""

    def test_person_id_creation(self) -> None:
        """Test PersonId can be created from int."""
        pid = PersonId(123)
        assert pid == 123
        assert isinstance(pid, int)

    def test_company_id_creation(self) -> None:
        """Test CompanyId can be created from int."""
        cid = CompanyId(456)
        assert cid == 456

    def test_list_id_creation(self) -> None:
        """Test ListId can be created from int."""
        lid = ListId(789)
        assert lid == 789

    def test_interaction_id_creation(self) -> None:
        """Test InteractionId can be created from int."""
        iid = InteractionId(123)
        assert iid == 123
        assert isinstance(iid, int)

    def test_dropdown_option_id_creation(self) -> None:
        """Test DropdownOptionId can be created from int."""
        oid = DropdownOptionId(456)
        assert oid == 456
        assert isinstance(oid, int)

    def test_tenant_id_creation(self) -> None:
        """Test TenantId can be created from int."""
        tid = TenantId(99)
        assert tid == 99
        assert isinstance(tid, int)

    def test_field_value_change_id_creation(self) -> None:
        """Test FieldValueChangeId can be created from int."""
        cid = FieldValueChangeId(123)
        assert cid == 123
        assert isinstance(cid, int)

    @pytest.mark.req("TR-003b")
    def test_ids_are_runtime_distinct_types(self) -> None:
        assert type(PersonId(1)) is PersonId
        assert type(CompanyId(1)) is CompanyId
        assert type(ListId(1)) is ListId

    def test_typed_ids_are_different_types(self) -> None:
        """Test that different ID types are distinct at runtime."""
        # At runtime they're all ints, but type checkers treat them differently
        pid = PersonId(100)
        cid = CompanyId(100)
        lid = ListId(100)

        # They're equal as ints
        assert pid == cid == lid == 100
        # But they should be used with type annotations


@pytest.mark.req("DX-005")
class TestFieldIdNormalization:
    """Test FieldId normalization (Enhancement 5)."""

    def test_field_id_equality_with_field_id(self) -> None:
        """FieldId compares equal to another FieldId with same value."""
        assert FieldId("field-123") == FieldId("field-123")
        assert FieldId(123) == FieldId("field-123")
        assert FieldId("123") == FieldId("field-123")

    def test_field_id_equality_with_string(self) -> None:
        """FieldId compares equal to equivalent strings."""
        assert FieldId("field-123") == "field-123"
        assert FieldId(123) == "field-123"
        assert FieldId("field-123") == "123"

    def test_field_id_equality_with_int(self) -> None:
        """FieldId compares equal to its numeric value."""
        assert FieldId("field-123") == 123
        assert FieldId(123) == 123

    def test_field_id_inequality(self) -> None:
        """FieldId correctly reports inequality."""
        assert FieldId("field-123") != FieldId("field-456")
        assert FieldId("field-123") != "field-456"
        assert FieldId("field-123") != 456
        assert FieldId("field-123") != "invalid"

    def test_field_id_hash_consistency(self) -> None:
        """FieldId hash is consistent for equal values."""
        # Same FieldId values should have same hash
        assert hash(FieldId("field-123")) == hash(FieldId(123))
        assert hash(FieldId("field-123")) == hash(FieldId("123"))

    def test_field_id_in_dict(self) -> None:
        """FieldId can be used as dict key."""
        d: dict[FieldId, str] = {FieldId("field-123"): "value1"}
        assert d[FieldId(123)] == "value1"
        assert d[FieldId("123")] == "value1"
        assert d[FieldId("field-123")] == "value1"

    def test_field_id_in_set(self) -> None:
        """FieldId can be used in sets with proper deduplication."""
        s = {FieldId("field-123"), FieldId(123), FieldId("123")}
        assert len(s) == 1  # All refer to same field

    def test_field_id_repr(self) -> None:
        """FieldId has informative repr."""
        fid = FieldId("field-123")
        assert repr(fid) == "FieldId('field-123')"

    def test_field_id_str(self) -> None:
        """FieldId str returns the canonical value."""
        fid = FieldId(123)
        assert str(fid) == "field-123"

    def test_field_id_invalid_values_not_equal(self) -> None:
        """FieldId does not equal invalid strings."""
        fid = FieldId("field-123")
        assert fid != "not-a-field-id"
        assert fid != "field-abc"  # Non-numeric
        assert fid != ""


@pytest.mark.req("TR-004")
class TestEnums:
    """Test enum definitions."""

    def test_list_type_values(self) -> None:
        """Test ListType enum values match API."""
        assert ListType.PERSON == 0
        assert ListType.COMPANY == 1
        assert ListType.OPPORTUNITY == 8
        # V1 compatibility alias
        assert ListType.ORGANIZATION == ListType.COMPANY

    def test_person_type_values(self) -> None:
        """Test PersonType enum values match API."""
        assert PersonType.INTERNAL == "internal"
        assert PersonType.EXTERNAL == "external"
        assert PersonType.COLLABORATOR == "collaborator"

    @pytest.mark.req("TR-004a")
    def test_open_str_enum_preserves_unknown_values(self) -> None:
        value = PersonType("future-new-type")
        assert value.value == "future-new-type"
        assert PersonType("future-new-type") is value

    @pytest.mark.req("TR-004a")
    def test_open_str_enum_preserves_unknown_values_for_field_value_type(self) -> None:
        value = FieldValueType(999)
        assert value.value == "999"
        assert FieldValueType(999) is value

    def test_field_value_type_values(self) -> None:
        """Test FieldValueType enum values."""
        assert FieldValueType.TEXT == "text"
        assert FieldValueType.NUMBER == "number"
        assert FieldValueType.NUMBER_MULTI == "number-multi"
        assert FieldValueType.DATETIME == "datetime"
        assert FieldValueType.LOCATION == "location"
        assert FieldValueType.LOCATION_MULTI == "location-multi"
        assert FieldValueType.DROPDOWN == "dropdown"
        assert FieldValueType.DROPDOWN_MULTI == "dropdown-multi"
        assert FieldValueType.RANKED_DROPDOWN == "ranked-dropdown"
        assert FieldValueType.PERSON == "person"
        assert FieldValueType.PERSON_MULTI == "person-multi"
        assert FieldValueType.COMPANY == "company"
        assert FieldValueType.COMPANY_MULTI == "company-multi"
        assert FieldValueType.FILTERABLE_TEXT == "filterable-text"
        assert FieldValueType.FILTERABLE_TEXT_MULTI == "filterable-text-multi"
        assert FieldValueType.INTERACTION == "interaction"

    def test_field_type_values(self) -> None:
        """Test FieldType string enum values."""
        assert FieldType.ENRICHED == "enriched"
        assert FieldType.GLOBAL == "global"
        assert FieldType.LIST == "list"
        assert FieldType.RELATIONSHIP_INTELLIGENCE == "relationship-intelligence"

    def test_interaction_type_values(self) -> None:
        """Test InteractionType enum values."""
        assert InteractionType.MEETING == 0  # Also called Event
        assert InteractionType.CALL == 1
        assert InteractionType.CHAT_MESSAGE == 2
        assert InteractionType.EMAIL == 3


@pytest.mark.req("TR-002")
class TestPersonModel:
    """Test Person model."""

    def test_person_from_v2_response(self) -> None:
        """Test Person model validates V2 API response."""
        data = {
            "id": 123,
            "firstName": "John",
            "lastName": "Doe",
            "primaryEmailAddress": "john@example.com",
            "emails": ["john@example.com", "jd@work.com"],
            "type": "external",
            "organizationIds": [456, 789],
            "fields": {"stage": "active"},
        }
        person = Person.model_validate(data)

        assert person.id == PersonId(123)
        assert person.first_name == "John"
        assert person.last_name == "Doe"
        assert person.primary_email == "john@example.com"
        assert len(person.emails) == 2
        assert person.type == PersonType.EXTERNAL
        assert len(person.company_ids) == 2

    def test_person_with_minimal_data(self) -> None:
        """Test Person model with minimal required fields."""
        data = {
            "id": 100,
            "type": "internal",
        }
        person = Person.model_validate(data)

        assert person.id == 100
        assert person.first_name is None
        assert person.last_name is None
        assert person.emails == []

    def test_person_type_enum_parsing(self) -> None:
        """Test PersonType is parsed correctly."""
        for type_str in ["internal", "external", "collaborator"]:
            data = {"id": 1, "type": type_str}
            person = Person.model_validate(data)
            assert person.type == PersonType(type_str)

    def test_person_type_from_v1_numeric(self) -> None:
        data = {"id": 1, "type": 0}
        person = Person.model_validate(data)
        assert person.type == PersonType.EXTERNAL
        data = {"id": 1, "type": 1}
        person = Person.model_validate(data)
        assert person.type == PersonType.INTERNAL

    def test_person_accepts_v1_current_organization_ids_and_interactions(self) -> None:
        # V1 API returns snake_case field names for interactions
        data = {
            "id": 1,
            "type": "external",
            "currentOrganizationIds": [123],
            "interactions": {
                "last_interaction": {"date": "2025-01-01T12:00:00Z", "person_ids": [999]}
            },
        }
        person = Person.model_validate(data)
        assert person.current_company_ids == [CompanyId(123)]
        assert person.interactions is not None
        assert person.interactions.last_interaction is not None
        assert person.interactions.last_interaction.person_ids == [999]


@pytest.mark.req("TR-002")
class TestCompanyModel:
    """Test Company model."""

    def test_company_from_v2_response(self) -> None:
        """Test Company model validates V2 API response."""
        data = {
            "id": 456,
            "name": "Acme Corp",
            "domain": "acme.com",
            "domains": ["acme.com", "acme.io"],
            "personIds": [1, 2, 3],
            "fields": {"industry": "Technology"},
        }
        company = Company.model_validate(data)

        assert company.id == CompanyId(456)
        assert company.name == "Acme Corp"
        assert company.domain == "acme.com"
        assert len(company.domains) == 2
        assert len(company.person_ids) == 3

    def test_company_with_minimal_data(self) -> None:
        """Test Company model with minimal required fields."""
        data = {
            "id": 100,
            "name": "Test Co",
        }
        company = Company.model_validate(data)

        assert company.id == 100
        assert company.name == "Test Co"
        assert company.domain is None
        assert company.domains == []


@pytest.mark.req("TR-002")
class TestAffinityListModel:
    """Test AffinityList model."""

    def test_list_from_v2_response(self) -> None:
        """Test AffinityList model validates V2 response."""
        data = {
            "id": 789,
            "name": "Deal Pipeline",
            "type": 8,  # OPPORTUNITY
            "public": True,
            "ownerId": 100,
            "creatorId": 100,
            "listSize": 50,
        }
        lst = AffinityList.model_validate(data)

        assert lst.id == ListId(789)
        assert lst.name == "Deal Pipeline"
        assert lst.type == ListType.OPPORTUNITY
        assert lst.is_public is True
        # list_size is now private, access via _list_size_hint
        assert lst._list_size_hint == 50

    def test_list_size_hint_from_list_size_key(self) -> None:
        """Test _list_size_hint is populated from list_size key (V1 format)."""
        data = {
            "id": 123,
            "name": "Test List",
            "type": 0,
            "public": False,
            "ownerId": 1,
            "list_size": 100,  # V1 snake_case key
        }
        lst = AffinityList.model_validate(data)
        assert lst._list_size_hint == 100

    def test_list_size_hint_excluded_from_model_dump(self) -> None:
        """Test _list_size_hint is excluded from model_dump() output."""
        data = {
            "id": 456,
            "name": "Test List",
            "type": 0,
            "public": False,
            "ownerId": 1,
            "listSize": 75,
        }
        lst = AffinityList.model_validate(data)
        dumped = lst.model_dump()
        assert "listSize" not in dumped
        assert "list_size" not in dumped
        assert "_list_size_hint" not in dumped
        # But the hint is still accessible
        assert lst._list_size_hint == 75

    def test_list_type_parsing(self) -> None:
        """Test list type enum is parsed correctly."""
        for type_int, expected in [
            (0, ListType.PERSON),
            (1, ListType.COMPANY),
            (8, ListType.OPPORTUNITY),
        ]:
            data = {"id": 1, "name": "Test", "type": type_int, "public": False, "ownerId": 1}
            lst = AffinityList.model_validate(data)
            assert lst.type == expected


@pytest.mark.req("TR-002")
class TestFieldMetadataModel:
    """Test FieldMetadata model."""

    def test_field_metadata_from_v2_response(self) -> None:
        """Test FieldMetadata validates V2 response."""
        data = {
            "id": "field-123",
            "name": "Deal Size",
            "type": "list",
            "valueType": 3,  # NUMBER
            "allowsMultiple": False,
            "isRequired": False,
        }
        field = FieldMetadata.model_validate(data)

        assert field.id == "field-123"
        assert field.name == "Deal Size"
        assert field.type == FieldType.LIST
        assert field.value_type == FieldValueType.NUMBER
        assert field.allows_multiple is False

    def test_enriched_field(self) -> None:
        """Test enriched field type."""
        data = {
            "id": "enriched-industry",
            "name": "Industry",
            "type": "enriched",
            "valueType": 2,  # DROPDOWN (simple, V1 code 2)
        }
        field = FieldMetadata.model_validate(data)

        assert field.type == FieldType.ENRICHED

    def test_field_metadata_parses_dropdown_options(self) -> None:
        field = FieldMetadata.model_validate(
            {
                "id": "field-1",
                "name": "Status",
                "type": "list-specific",
                "valueType": 7,  # RANKED_DROPDOWN (v1 numeric)
                "allowsMultiple": False,
                "dropdownOptions": [{"id": 10, "text": "Active"}],
            }
        )
        assert len(field.dropdown_options) == 1
        assert type(field.dropdown_options[0].id) is DropdownOptionId
        assert field.dropdown_options[0].id == 10


@pytest.mark.req("TR-011")
class TestNullVsEmptyNormalization:
    def test_person_null_arrays_normalize_to_empty_lists(self) -> None:
        person = Person.model_validate(
            {
                "id": 1,
                "type": "external",
                "emails": None,
                "organizationIds": None,
            }
        )
        assert person.emails == []
        assert person.company_ids == []

    def test_company_null_arrays_normalize_to_empty_lists(self) -> None:
        company = Company.model_validate(
            {
                "id": 1,
                "name": "Acme",
                "domains": None,
                "personIds": None,
            }
        )
        assert company.domains == []
        assert company.person_ids == []


@pytest.mark.req("TR-012")
class TestDateTimePolicy:
    def test_parse_utc_datetime_is_timezone_aware(self) -> None:
        field_value = FieldValue.model_validate(
            {
                "id": 1,
                "fieldId": 1,
                "entityId": 1,
                "value": "x",
                "createdAt": "2025-01-01T12:00:00Z",
            }
        )
        assert type(field_value.id) is FieldValueId
        assert field_value.created_at is not None
        assert field_value.created_at.tzinfo is not None
        assert field_value.created_at.utcoffset() is not None

    def test_serialize_timezone_aware_datetime_is_deterministic(self) -> None:
        created_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        field_value = FieldValue(
            id=1,
            field_id=FieldId(1),
            entity_id=1,
            list_entry_id=None,
            value="x",
            created_at=created_at,
            updated_at=None,
        )
        # Use mode="json" to get serialized strings instead of Python objects
        dumped = field_value.model_dump(by_alias=True, mode="json")
        assert isinstance(dumped["createdAt"], str)
        assert "2025-01-01T12:00:00" in dumped["createdAt"]
        assert dumped["createdAt"].endswith("Z") or "+00:00" in dumped["createdAt"]


@pytest.mark.req("TR-003")
def test_field_value_change_id_is_typed() -> None:
    change = FieldValueChange.model_validate(
        {
            "id": 1,
            "fieldId": 1,
            "entityId": 1,
            "actionType": 0,
            "value": "x",
            "changedAt": "2025-01-01T12:00:00Z",
        }
    )
    assert type(change.id) is FieldValueChangeId


@pytest.mark.req("TR-002")
def test_list_entry_entity_is_discriminated_by_entity_type_for_opportunities() -> None:
    entry = ListEntry.model_validate(
        {
            "id": 1,
            "list_id": 2,
            "creator_id": 3,
            "entity_id": 4,
            "entity_type": 8,  # EntityType.OPPORTUNITY (v1 numeric)
            "created_at": "2025-01-01T12:00:00Z",
            "entity": {"id": 4, "name": "Deal A"},
        }
    )
    assert entry.entity is not None
    assert type(entry.entity.id) is OpportunityId  # type: ignore[union-attr]


@pytest.mark.req("TR-001")
def test_python_version_is_supported() -> None:
    assert sys.version_info >= (3, 10)


@pytest.mark.req("TR-003a")
def test_field_id_to_v1_numeric_convertible() -> None:
    assert field_id_to_v1_numeric(FieldId("field-123")) == 123


@pytest.mark.req("TR-003a")
def test_field_id_to_v1_numeric_rejects_enriched() -> None:
    with pytest.raises(ValueError):
        field_id_to_v1_numeric(EnrichedFieldId("affinity-data-description"))


@pytest.mark.req("TR-008")
def test_dependency_management_pyproject_contains_core_deps() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    # Core deps
    assert "dependencies = [" in text
    assert '"httpx>=' in text
    assert '"pydantic>=' in text

    # Dev extras exist and include testing/linting tools
    assert "[project.optional-dependencies]" in text
    assert "dev = [" in text
    assert '"pytest>=' in text
    # ruff is pinned exactly: its 0.x minors change defaults and broke CI unannounced
    assert '"ruff==' in text
    assert '"mypy>=' in text


@pytest.mark.req("TR-006b")
def test_core_dependencies_do_not_include_retry_middleware() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    if "dependencies = [" not in text:
        raise AssertionError("Could not find core 'dependencies = [' in pyproject.toml")

    core_block = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "tenacity" not in core_block
    assert "httpx-retries" not in core_block


def _contains_dict_type(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is dict:
        return True
    if annotation is dict:
        return True
    return any(_contains_dict_type(arg) for arg in get_args(annotation))


@pytest.mark.req("TR-005")
def test_public_service_return_types_do_not_expose_dict() -> None:
    service_classes = [
        CompanyService,
        PersonService,
        ListService,
        ListEntryService,
        NoteService,
        ReminderService,
        InteractionService,
        EntityFileService,
    ]

    offenders: list[str] = []
    for cls in service_classes:
        for name, member in inspect.getmembers(cls):
            if name.startswith("_"):
                continue
            # *_batch methods intentionally return dict[EntityId, list[AssocId]] mappings
            if name.endswith("_batch"):
                continue
            if not inspect.isfunction(member):
                continue
            hints = get_type_hints(member, include_extras=True)
            if "return" not in hints:
                continue
            if _contains_dict_type(hints["return"]):
                offenders.append(f"{cls.__name__}.{name} -> {hints['return']!r}")

    assert offenders == []


@pytest.mark.req("DX-002")
def test_field_value_service_list_requires_exactly_one_id() -> None:
    http = http_mod.HTTPClient(http_mod.ClientConfig(api_key="k", max_retries=0))
    try:
        service = FieldValueService(http)
        with pytest.raises(ValueError, match="one entity ID"):
            service.list()
        with pytest.raises(ValueError, match="one entity ID"):
            service.list(person_id=PersonId(1), company_id=CompanyId(2))
    finally:
        http.close()


@pytest.mark.req("NFR-005")
def test_public_apis_do_not_expose_version_or_url_routing_controls() -> None:
    client = Affinity(api_key="test")
    async_client = AsyncAffinity(api_key="test")
    try:
        assert not hasattr(client, "http")
        assert not hasattr(async_client, "http")

        service_classes = [
            CompanyService,
            PersonService,
            ListService,
            ListEntryService,
            NoteService,
            ReminderService,
            InteractionService,
            EntityFileService,
        ]

        forbidden_param_names = {
            "v1",
            "v2",
            "url",
            "base_url",
            "v1_base_url",
            "v2_base_url",
        }

        offenders: list[str] = []
        for cls in service_classes:
            for name, member in inspect.getmembers(cls):
                if name.startswith("_"):
                    continue
                if not inspect.isfunction(member):
                    continue
                sig = inspect.signature(member)
                for param_name in sig.parameters:
                    if param_name in forbidden_param_names:
                        offenders.append(f"{cls.__name__}.{name}({param_name}=...)")

        assert offenders == []
    finally:
        client.close()


@pytest.mark.req("DX-004")
def test_package_includes_py_typed_marker() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "affinity" / "py.typed").is_file()


@pytest.mark.req("NFR-005")
def test_affinity_types_module_is_public() -> None:
    assert types_mod.PersonId(1) == 1
    assert types_mod.CompanyId(2) == 2
    assert hasattr(types_mod, "FieldType")


@pytest.mark.req("DX-008")
def test_affinity_hooks_module_is_public() -> None:
    req = RequestInfo(method="GET", url="https://api.affinity.co/v2/companies", headers={})
    assert req.method == "GET"


@pytest.mark.req("DX-006")
def test_library_logger_has_null_handler_and_no_output_by_default(
    capsys: Any,
) -> None:
    logger = logging.getLogger("affinity_sdk")
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)

    logger.warning("should-not-print")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.req("NFR-001")
def test_httpclient_reuses_single_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[int] = []
    original = http_mod.httpx.Client

    class CountingClient(original):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append(1)
            super().__init__(*args, **kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []}, request=request)

    monkeypatch.setattr(http_mod.httpx, "Client", CountingClient)
    config = http_mod.ClientConfig(
        api_key="test",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    client = http_mod.HTTPClient(config)
    client.get("/lists")
    client.get("/lists")
    client.close()

    assert len(created) == 1


@pytest.mark.asyncio
@pytest.mark.req("NFR-001")
async def test_asynchttpclient_reuses_single_httpx_async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[int] = []
    original = http_mod.httpx.AsyncClient

    class CountingAsyncClient(original):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append(1)
            super().__init__(*args, **kwargs)

    class StaticAsyncTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []}, request=request)

    monkeypatch.setattr(http_mod.httpx, "AsyncClient", CountingAsyncClient)
    config = http_mod.ClientConfig(
        api_key="test",
        max_retries=0,
        async_transport=StaticAsyncTransport(),
    )
    client = http_mod.AsyncHTTPClient(config)
    await client.get("/lists")
    await client.get("/lists")
    await client.close()

    assert len(created) == 1


@pytest.mark.req("FR-003a")
def test_sync_client_exposes_core_entity_services() -> None:
    client = Affinity(api_key="test")
    try:
        assert hasattr(client, "companies")
        assert hasattr(client, "persons")
        assert hasattr(client, "lists")
        assert hasattr(client, "opportunities")

        assert callable(client.companies.get)
        assert callable(client.companies.list)
        assert callable(client.companies.iter)

        assert callable(client.persons.get)
        assert callable(client.persons.list)
        assert callable(client.persons.iter)

        assert callable(client.lists.get)
        assert callable(client.lists.list)
        assert callable(client.lists.iter)

        assert callable(client.opportunities.get)
        assert callable(client.opportunities.list)
        assert callable(client.opportunities.iter)
    finally:
        client.close()


@pytest.mark.req("FR-003a")
def test_async_client_exposes_core_entity_services() -> None:
    # Note: AsyncAffinity doesn't create httpx client until first request,
    # so we don't need to close it for this inspection-only test.
    # But we close it anyway for consistency.
    client = AsyncAffinity(api_key="test")

    assert hasattr(client, "companies")
    assert hasattr(client, "persons")
    assert hasattr(client, "lists")
    assert hasattr(client, "opportunities")

    assert callable(client.companies.get)
    assert callable(client.companies.list)
    assert callable(client.companies.iter)

    assert callable(client.persons.get)
    assert callable(client.persons.list)
    assert callable(client.persons.iter)

    assert callable(client.lists.get)
    assert callable(client.lists.list)
    assert callable(client.lists.iter)

    assert callable(client.opportunities.get)
    assert callable(client.opportunities.list)
    assert callable(client.opportunities.iter)

    assert hasattr(client, "notes")
    assert hasattr(client, "reminders")
    assert hasattr(client, "webhooks")
    assert hasattr(client, "interactions")
    assert hasattr(client, "fields")
    assert hasattr(client, "field_values")
    assert hasattr(client, "files")
    assert hasattr(client, "relationships")
    assert hasattr(client, "auth")

    assert callable(client.notes.list)
    assert callable(client.reminders.list)
    assert callable(client.webhooks.list)
    assert callable(client.interactions.list)
    assert callable(client.fields.list)
    assert callable(client.field_values.list)
    assert callable(client.files.list)
    assert callable(client.relationships.get)
    assert callable(client.auth.whoami)


@pytest.mark.req("DX-001a")
def test_service_tree_avoids_top_level_callable_entries() -> None:
    sync_client = Affinity(api_key="test")
    try:
        assert not hasattr(sync_client, "entries")
        entries_service = sync_client.lists.entries(ListId(1))
        assert isinstance(entries_service, ListEntryService)
    finally:
        sync_client.close()

    # AsyncAffinity doesn't create httpx client until first request
    async_client = AsyncAffinity(api_key="test")
    assert not hasattr(async_client, "entries")
    async_entries_service = async_client.lists.entries(ListId(1))
    assert isinstance(async_entries_service, AsyncListEntryService)


@pytest.mark.req("SDK-USERID-PERSONID")
class TestUserIdPersonIdRelationship:
    """UserId is a subtype of PersonId — workspace users are internal persons."""

    def test_userid_is_subclass_of_personid(self) -> None:
        assert issubclass(UserId, PersonId)

    def test_userid_instance_is_personid(self) -> None:
        uid = UserId(123)
        assert isinstance(uid, PersonId)
        assert isinstance(uid, int)

    def test_userid_accepted_where_personid_expected(self) -> None:
        """UserId can be used directly in PersonId-typed contexts."""
        uid = UserId(42)
        d: dict[PersonId, str] = {PersonId(42): "Alice"}
        assert d[uid] == "Alice"

    def test_pydantic_roundtrip_preserves_userid(self) -> None:
        """Pydantic deserialization produces UserId, not PersonId."""
        from affinity.models.secondary import Note, NoteType

        data = {
            "id": 1,
            "creatorId": 99,
            "content": "test",
            "type": NoteType.PLAIN_TEXT.value,
            "createdAt": "2024-01-01T00:00:00Z",
        }
        note = Note.model_validate(data)
        assert type(note.creator_id) is UserId
        assert note.creator_id == 99

    def test_userid_equality_with_personid(self) -> None:
        assert UserId(5) == PersonId(5)
        assert PersonId(5) == UserId(5)

    def test_userid_not_assignable_from_personid(self) -> None:
        """PersonId should NOT be assignable to UserId (not all persons are users)."""
        pid = PersonId(10)
        assert not isinstance(pid, UserId)
