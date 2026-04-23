"""
Core entity models for the Affinity API.

These models represent the main entities in Affinity: Persons, Companies,
Opportunities, Lists, and List Entries. Uses V2 terminology throughout.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from .types import (
    AnyFieldId,
    CompanyId,
    DropdownOptionId,
    EntityType,
    FieldId,
    FieldValueChangeAction,
    FieldValueChangeId,
    FieldValueId,
    FieldValueType,
    ISODatetime,
    ListEntryId,
    ListId,
    ListRole,
    ListType,
    OpportunityId,
    PersonId,
    PersonType,
    SavedViewId,
    UserId,
)

# Use the library logger; affinity/__init__.py installs a NullHandler by default.
_logger = logging.getLogger("affinity_sdk")

# =============================================================================
# Base configuration for all models
# =============================================================================


class AffinityModel(BaseModel):
    """Base model with common configuration."""

    model_config = ConfigDict(
        extra="ignore",  # Ignore unknown fields from API
        populate_by_name=True,  # Allow both alias and field name
        use_enum_values=True,  # Serialize enums as values
        validate_assignment=True,  # Validate on attribute assignment
    )


class FieldValues(AffinityModel):
    """
    Field values container that preserves the "requested vs not requested"
    semantics.

    - `requested=False` means the caller did not request field data and/or the
      API omitted field data.
    - `requested=True` means field data was requested and returned (possibly
      empty/null-normalized).
    """

    requested: bool = False
    data: dict[str, Any] = Field(default_factory=dict)

    def get(self, field_id: str | AnyFieldId) -> dict[str, Any] | None:
        """
        Get a field value by ID.

        Args:
            field_id: The field ID (e.g., "field-123" or FieldId(123))

        Returns:
            The field value dict containing 'id' and 'value' keys, or None if not found.

        Example:
            >>> company = client.companies.get(CompanyId(123), field_types=[FieldType.GLOBAL])
            >>> field_data = company.fields.get("field-456")
            >>> if field_data:
            ...     print(field_data["value"])
        """
        fid = str(field_id)
        return self.data.get(fid)

    def get_value(self, field_id: str | AnyFieldId) -> Any:
        """
        Get the extracted field value by ID.

        Handles nested value structures automatically:
        - Text fields: returns string
        - Dropdown fields: returns :class:`DropdownOption` (with id, text, rank, color)
        - Person/Company references: returns dict with id + name fields
        - Location fields: returns dict
        - Multi-value fields: returns list of values

        Args:
            field_id: The field ID (e.g., "field-123" or FieldId(123))

        Returns:
            The extracted value, or None if field not found.

        Example:
            >>> status = company.fields.get_value("field-456")
            >>> print(status)  # "Active" (not {"data": "Active"})
        """
        field_data = self.get(field_id)
        if field_data is None:
            return None
        return self._extract_value(field_data.get("value"))

    @staticmethod
    def _extract_value(value: Any) -> Any:
        """Extract the actual value from nested field value structure.

        Returns typed objects where possible:
        - Dropdown values → :class:`DropdownOption`
        - Text/number → primitive
        - Location → dict (pass-through)
        - Multi-value → list of extracted values
        """
        if value is None:
            return None

        # Multi-value: list of value objects
        if isinstance(value, list):
            return [FieldValues._extract_value(v) for v in value]

        # Single value object with nested data
        if isinstance(value, dict):
            # Dropdown option → typed DropdownOption
            if "dropdownOptionId" in value:
                return DropdownOption(
                    id=DropdownOptionId(value["dropdownOptionId"]),
                    text=value.get("text", ""),
                    rank=value.get("rank"),
                    color=value.get("color"),
                )
            # Standard data field (text, number, person ref, etc.)
            if "data" in value:
                return FieldValues._extract_value(value["data"])
            # Location or other complex type - return as-is
            return value

        # Primitive value (shouldn't happen but handle gracefully)
        return value

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_api(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if value is None:
            return {"requested": True, "data": {}}
        if isinstance(value, list):
            # API returns fields as array: [{"id": "field-123", "value": {...}}, ...]
            # Convert to dict keyed by field ID for easier access
            data = {item["id"]: item for item in value if isinstance(item, dict) and "id" in item}
            return {"requested": True, "data": data}
        if isinstance(value, dict):
            # Already in FieldValues format — pass through without re-wrapping
            if "requested" in value and "data" in value and isinstance(value["data"], dict):
                return value
            return {"requested": True, "data": value}
        return {"requested": True, "data": {}}


def _normalize_null_lists(value: Any, keys: Sequence[str]) -> Any:
    if not isinstance(value, Mapping):
        return value

    data: dict[str, Any] = dict(value)
    for key in keys:
        if key in data and data[key] is None:
            data[key] = []
    return data


def _preserve_fields_raw(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value

    data: dict[str, Any] = dict(value)
    fields = data.get("fields")
    if isinstance(fields, list):
        data["fields_raw"] = fields
    return data


def _normalize_person_type(value: Any) -> Any:
    if value is None:
        return value
    if isinstance(value, PersonType):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            try:
                value = int(text)
            except ValueError:
                return value
        else:
            return value
    if isinstance(value, int):
        mapping = {
            0: PersonType.EXTERNAL,
            1: PersonType.INTERNAL,
            2: PersonType.COLLABORATOR,
        }
        return mapping.get(value, value)
    return value


# =============================================================================
# Location Value
# =============================================================================


class Location(AffinityModel):
    """Geographic location value."""

    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    continent: str | None = None


# =============================================================================
# Dropdown Option
# =============================================================================


class DropdownOption(AffinityModel):
    """A selectable option in a dropdown field."""

    id: DropdownOptionId
    text: str
    rank: int | None = None
    color: int | str | None = None


# =============================================================================
# Person Models
# =============================================================================


class PersonSummary(AffinityModel):
    """Minimal person data returned in nested contexts."""

    id: PersonId
    first_name: str | None = Field(None, alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    primary_email: str | None = Field(None, alias="primaryEmailAddress")
    type: PersonType

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_person_type(cls, value: Any) -> Any:
        return _normalize_person_type(value)


class Person(AffinityModel):
    """
    Full person representation.

    Note: Companies are called Organizations in V1 API.
    """

    id: PersonId
    first_name: str | None = Field(None, alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    primary_email: str | None = Field(None, alias="primaryEmailAddress")
    # V2 uses emailAddresses, V1 uses emails - accept both via alias
    emails: list[str] = Field(default_factory=list, alias="emailAddresses")
    type: PersonType = PersonType.EXTERNAL

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_person_type(cls, value: Any) -> Any:
        return _normalize_person_type(value)

    # Associations (V1 uses organizationIds)
    company_ids: list[CompanyId] = Field(default_factory=list, alias="organizationIds")
    opportunity_ids: list[OpportunityId] = Field(default_factory=list, alias="opportunityIds")

    # V1: only returned when `with_current_organizations=true`
    current_company_ids: list[CompanyId] = Field(
        default_factory=list, alias="currentOrganizationIds"
    )

    # Field values (requested-vs-not-requested preserved)
    fields: FieldValues = Field(default_factory=FieldValues, alias="fields")
    fields_raw: list[dict[str, Any]] | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_null_lists_before(cls, value: Any) -> Any:
        value = _normalize_null_lists(
            value,
            (
                "emails",
                "emailAddresses",
                "companyIds",
                "company_ids",
                "organizationIds",
                "organization_ids",
                "currentCompanyIds",
                "current_company_ids",
                "currentOrganizationIds",
                "current_organization_ids",
                "opportunityIds",
                "opportunity_ids",
            ),
        )
        return _preserve_fields_raw(value)

    @model_validator(mode="after")
    def _mark_fields_not_requested_when_omitted(self) -> Person:
        if "fields" not in self.__pydantic_fields_set__:
            self.fields.requested = False
        return self

    # Interaction dates (V1 format, returned when with_interaction_dates=True)
    interaction_dates: InteractionDates | None = Field(None, alias="interactionDates")

    # Detailed interaction data with person IDs (returned when with_interaction_persons=True)
    interactions: Interactions | None = None

    # List entries (returned for single person fetch)
    list_entries: list[ListEntry] | None = Field(None, alias="listEntries")

    @property
    def full_name(self) -> str:
        """Get the person's full name."""
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p) or ""


class PersonCreate(AffinityModel):
    """Data for creating a new person.

    Note: "Current Organization" and "Current Job Title" cannot be set during
    creation. "Current Organization" is a derived/system-managed field (read-only
    via API, driven by enrichment and email domain). "Current Job Title" can be
    updated after creation via the field-values endpoint.
    """

    first_name: str
    last_name: str
    emails: list[str] = Field(default_factory=list)
    company_ids: list[CompanyId] = Field(default_factory=list, alias="organization_ids")


class PersonUpdate(AffinityModel):
    """Data for updating a person (V1 API)."""

    first_name: str | None = None
    last_name: str | None = None
    emails: list[str] | None = None
    company_ids: list[CompanyId] | None = Field(None, alias="organization_ids")


# =============================================================================
# Company (Organization) Models
# =============================================================================


class CompanySummary(AffinityModel):
    """Minimal company data returned in nested contexts."""

    id: CompanyId
    name: str
    domain: str | None = None


class Company(AffinityModel):
    """
    Full company representation.

    Note: Called Organization in V1 API.
    """

    id: CompanyId
    name: str
    domain: str | None = None
    domains: list[str] = Field(default_factory=list)
    is_global: bool = Field(False, alias="global")

    # Associations
    person_ids: list[PersonId] = Field(default_factory=list, alias="personIds")
    opportunity_ids: list[OpportunityId] = Field(default_factory=list, alias="opportunityIds")

    # Field values (requested-vs-not-requested preserved)
    fields: FieldValues = Field(default_factory=FieldValues, alias="fields")
    fields_raw: list[dict[str, Any]] | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_null_lists_before(cls, value: Any) -> Any:
        value = _normalize_null_lists(
            value,
            (
                "domains",
                "personIds",
                "person_ids",
                "opportunityIds",
                "opportunity_ids",
            ),
        )
        return _preserve_fields_raw(value)

    @model_validator(mode="after")
    def _mark_fields_not_requested_when_omitted(self) -> Company:
        if "fields" not in self.__pydantic_fields_set__:
            self.fields.requested = False
        return self

    # List entries (returned for single company fetch)
    list_entries: list[ListEntry] | None = Field(None, alias="listEntries")

    # Interaction dates
    interaction_dates: InteractionDates | None = Field(None, alias="interactionDates")

    # Detailed interaction data with person IDs (returned when with_interaction_persons=True)
    interactions: Interactions | None = None


class CompanyCreate(AffinityModel):
    """Data for creating a new company (V1 API)."""

    name: str
    domain: str | None = None
    person_ids: list[PersonId] = Field(default_factory=list)


class CompanyUpdate(AffinityModel):
    """Data for updating a company (V1 API)."""

    name: str | None = None
    domain: str | None = None
    person_ids: list[PersonId] | None = None


# =============================================================================
# Opportunity Models
# =============================================================================


class Opportunity(AffinityModel):
    """
    Deal/opportunity in a pipeline.

    Note:
        The V2 API returns empty ``person_ids`` and ``company_ids`` arrays even
        when associations exist. Use ``client.opportunities.get_associated_person_ids()``
        or ``client.opportunities.get_details()`` to retrieve association data.

        See the opportunity-associations guide for details.
    """

    id: OpportunityId
    name: str
    list_id: ListId | None = Field(None, alias="listId")

    # Associations (Note: V2 API returns empty arrays; use get_details() or
    # get_associated_person_ids() for populated data)
    person_ids: list[PersonId] = Field(default_factory=list, alias="personIds")
    company_ids: list[CompanyId] = Field(default_factory=list, alias="organizationIds")

    # Field values (requested-vs-not-requested preserved)
    fields: FieldValues = Field(default_factory=FieldValues, alias="fields")
    fields_raw: list[dict[str, Any]] | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _preserve_fields_raw_before(cls, value: Any) -> Any:
        return _preserve_fields_raw(value)

    # List entries
    list_entries: list[ListEntry] | None = Field(None, alias="listEntries")

    @model_validator(mode="after")
    def _post_validate(self) -> Opportunity:
        # Mark fields as not requested when omitted
        if "fields" not in self.__pydantic_fields_set__:
            self.fields.requested = False

        # Extract list_id from list_entries when not set directly
        # (V1 API returns list_id in list_entries, not at top level)
        if self.list_id is None and self.list_entries:
            self.list_id = self.list_entries[0].list_id

        return self


class OpportunityCreate(AffinityModel):
    """Data for creating a new opportunity (V1 API)."""

    name: str
    list_id: ListId
    person_ids: list[PersonId] = Field(default_factory=list)
    company_ids: list[CompanyId] = Field(default_factory=list, alias="organization_ids")


class OpportunityUpdate(AffinityModel):
    """Data for updating an opportunity (V1 API)."""

    name: str | None = None
    person_ids: list[PersonId] | None = None
    company_ids: list[CompanyId] | None = Field(None, alias="organization_ids")


# =============================================================================
# Opportunity Summary
# =============================================================================


class OpportunitySummary(AffinityModel):
    """Minimal opportunity data returned in nested contexts."""

    id: OpportunityId
    name: str


# =============================================================================
# List Models
# =============================================================================


class ListPermission(AffinityModel):
    """Additional permission on a list."""

    internal_person_id: UserId = Field(alias="internalPersonId")
    role_id: ListRole = Field(alias="roleId")


class AffinityList(AffinityModel):
    """
    A list (spreadsheet) in Affinity.

    Named AffinityList to avoid collision with Python's list type.

    Note:
        The list_size field was removed in v0.13.0 because the V2 API returns
        incorrect values (often 0 for non-empty lists). Use
        ``client.lists.get_size(list_id)`` to get accurate list size via V1 API.
    """

    id: ListId
    name: str
    type: ListType
    is_public: bool = Field(alias="public")
    owner_id: UserId = Field(alias="ownerId")
    creator_id: UserId | None = Field(None, alias="creatorId")

    # Fields on this list (returned for single list fetch)
    fields: list[FieldMetadata] | None = None

    # Permissions
    additional_permissions: list[ListPermission] = Field(
        default_factory=list, alias="additionalPermissions"
    )

    # Internal - not guaranteed accurate from V2. Excluded from serialization.
    # Populated from listSize (V2) or list_size (V1) via model_validator + model_post_init.
    _list_size_hint: int = PrivateAttr(default=0)

    # Temporary field to pass list_size from validator to model_post_init (excluded from output)
    list_size_temp: int | None = Field(None, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _extract_list_size(cls, data: dict[str, Any]) -> dict[str, Any]:
        # V2 list endpoints use `isPublic`; v1 uses `public`.
        if isinstance(data, Mapping):
            data = dict(data)
            if "public" not in data and "isPublic" in data:
                data["public"] = data.get("isPublic")
            # Extract listSize and pass via temp field (V2 uses listSize, V1 uses list_size)
            if "listSize" in data:
                data["list_size_temp"] = data.pop("listSize")
            elif "list_size" in data:
                data["list_size_temp"] = data.pop("list_size")
        return data

    def model_post_init(self, __context: Any) -> None:
        """Transfer list_size_temp to _list_size_hint private attr."""
        if self.list_size_temp is not None:
            object.__setattr__(self, "_list_size_hint", self.list_size_temp)
            # Clear the temp field
            object.__setattr__(self, "list_size_temp", None)


class ListSummary(AffinityModel):
    """Minimal list reference used by relationship endpoints."""

    id: ListId
    name: str | None = None
    type: ListType | None = None
    is_public: bool | None = Field(None, alias="public")
    owner_id: UserId | None = Field(None, alias="ownerId")
    list_size: int | None = Field(None, alias="listSize")

    @model_validator(mode="before")
    @classmethod
    def _coerce_v2_is_public(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "public" not in value and "isPublic" in value:
            data = dict(value)
            data["public"] = data.get("isPublic")
            return data
        return value


class ListCreate(AffinityModel):
    """Data for creating a new list (V1 API)."""

    name: str
    type: ListType
    is_public: bool
    owner_id: UserId | None = None
    additional_permissions: list[ListPermission] = Field(default_factory=list)


# =============================================================================
# List Entry Models
# =============================================================================


class ListEntry(AffinityModel):
    """
    A row in a list, linking an entity to a list.

    Contains the entity data and list-specific field values.
    """

    id: ListEntryId
    list_id: ListId = Field(alias="listId")
    creator_id: UserId | None = Field(None, alias="creatorId")
    entity_id: int | None = Field(None, alias="entityId")
    entity_type: EntityType | None = Field(None, alias="entityType")
    created_at: ISODatetime = Field(alias="createdAt")

    # The entity this entry represents (can be Person, Company, or Opportunity)
    entity: PersonSummary | CompanySummary | OpportunitySummary | dict[str, Any] | None = None

    # Field values on this list entry (requested-vs-not-requested preserved)
    fields: FieldValues = Field(default_factory=FieldValues, alias="fields")
    fields_raw: list[dict[str, Any]] | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_entity_by_entity_type(cls, value: Any) -> Any:
        """
        The v1 list-entry payload includes `entity_type` alongside a minimal `entity` dict.

        Some entity summaries overlap in shape (e.g. opportunity and company both have
        `{id, name}`), so we must use `entity_type` as the discriminator to avoid mis-parsing.
        """
        if not isinstance(value, Mapping):
            return value

        data: dict[str, Any] = dict(value)
        fields = data.get("fields")
        if isinstance(fields, list):
            data["fields_raw"] = fields

        entity = data.get("entity")
        if not isinstance(entity, Mapping):
            return data

        raw_entity_type = data.get("entityType")
        if raw_entity_type is None:
            raw_entity_type = data.get("entity_type")
        if raw_entity_type is None:
            return data

        try:
            entity_type = EntityType(raw_entity_type)
        except ValueError as e:
            _logger.debug("Unknown entity type %r, returning raw data: %s", raw_entity_type, e)
            return data

        if entity_type == EntityType.PERSON:
            try:
                data["entity"] = PersonSummary.model_validate(entity)
            except Exception as e:
                _logger.debug("Failed to validate PersonSummary, returning raw data: %s", e)
                return data
        elif entity_type == EntityType.ORGANIZATION:
            try:
                data["entity"] = CompanySummary.model_validate(entity)
            except Exception as e:
                _logger.debug("Failed to validate CompanySummary, returning raw data: %s", e)
                return data
        elif entity_type == EntityType.OPPORTUNITY:
            try:
                data["entity"] = OpportunitySummary.model_validate(entity)
            except Exception as e:
                _logger.debug("Failed to validate OpportunitySummary, returning raw data: %s", e)
                return data

        return data

    @model_validator(mode="after")
    def _mark_fields_not_requested_when_omitted(self) -> ListEntry:
        if "fields" not in self.__pydantic_fields_set__:
            self.fields.requested = False
        return self


class ListEntryWithEntity(AffinityModel):
    """List entry with full entity data included (V2 response format)."""

    id: ListEntryId
    list_id: ListId = Field(alias="listId")
    creator: PersonSummary | None = None
    created_at: ISODatetime = Field(alias="createdAt")

    # Entity type and data
    type: str  # "person", "company", or "opportunity"
    entity: Person | Company | Opportunity | None = None

    # Field values — always requested=False at entry level because the V2 API
    # nests field data inside entity.fields, not here. Use entry.entity.fields
    # to access field data, or pass entries to FieldResolver.get() which
    # auto-delegates to the inner entity.
    fields: FieldValues = Field(default_factory=FieldValues, alias="fields")
    fields_raw: list[dict[str, Any]] | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _preserve_fields_raw_before(cls, value: Any) -> Any:
        return _preserve_fields_raw(value)

    @model_validator(mode="after")
    def _mark_fields_not_requested_when_omitted(self) -> ListEntryWithEntity:
        if "fields" not in self.__pydantic_fields_set__:
            self.fields.requested = False
        return self


class ListEntryCreate(AffinityModel):
    """Data for adding an entity to a list (V1 API)."""

    entity_id: int
    creator_id: UserId | None = None


# =============================================================================
# Saved View Models
# =============================================================================


class SavedView(AffinityModel):
    """A saved view configuration for a list."""

    id: SavedViewId
    name: str
    type: str | None = None  # V2 field: view type
    list_id: ListId | None = Field(None, alias="listId")
    # The Affinity API does not consistently include this field.
    is_default: bool | None = Field(None, alias="isDefault")
    created_at: ISODatetime | None = Field(None, alias="createdAt")

    # Field IDs included in this view
    field_ids: list[str] = Field(default_factory=list, alias="fieldIds")


# =============================================================================
# Field Metadata Models
# =============================================================================


class FieldMetadata(AffinityModel):
    """
    Metadata about a field (column) in Affinity.

    Includes both V1 numeric IDs and V2 string IDs for enriched fields.
    """

    model_config = ConfigDict(use_enum_values=False)

    id: AnyFieldId  # Can be int (field-123) or string (affinity-data-description)
    name: str
    value_type: FieldValueType = Field(alias="valueType")
    allows_multiple: bool = Field(False, alias="allowsMultiple")
    value_type_raw: str | int | None = Field(None, exclude=True)

    # V2 field type classification
    type: str | None = None  # "enriched", "list", "global", "relationship-intelligence"

    # V1 specific fields
    list_id: ListId | None = Field(None, alias="listId")
    track_changes: bool = Field(False, alias="trackChanges")
    enrichment_source: str | None = Field(None, alias="enrichmentSource")
    is_required: bool = Field(False, alias="isRequired")

    # Dropdown options for dropdown fields
    dropdown_options: list[DropdownOption] = Field(default_factory=list, alias="dropdownOptions")

    @model_validator(mode="before")
    @classmethod
    def _preserve_value_type_raw(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value

        data: dict[str, Any] = dict(value)
        raw = data.get("valueType")
        if raw is None and "value_type" in data:
            raw = data.get("value_type")
        data["value_type_raw"] = raw
        return data

    @model_validator(mode="after")
    def _coerce_allows_multiple_from_value_type(self) -> FieldMetadata:
        # If the server returns a `*-multi` value type, treat it as authoritative for multiplicity.
        try:
            text = str(self.value_type)
        except Exception:
            text = ""
        if text.endswith("-multi") and not self.allows_multiple:
            _logger.debug(
                "FieldMetadata allowsMultiple mismatch: valueType=%s allowsMultiple=%s "
                "(auto-correcting)",
                text,
                self.allows_multiple,
            )
            self.allows_multiple = True
        return self


class FieldCreate(AffinityModel):
    """Data for creating a new field (V1 API)."""

    model_config = ConfigDict(use_enum_values=False)

    name: str
    entity_type: EntityType
    value_type: FieldValueType
    list_id: ListId | None = None
    allows_multiple: bool = False
    is_list_specific: bool = False
    is_required: bool = False


# =============================================================================
# Field Value Models
# =============================================================================


class FieldValue(AffinityModel):
    """
    A single field value (cell data).

    The value can be various types depending on the field's value_type.
    """

    id: FieldValueId
    field_id: AnyFieldId = Field(alias="fieldId")
    entity_id: int = Field(alias="entityId")
    list_entry_id: ListEntryId | None = Field(None, alias="listEntryId")

    # The actual value - type depends on field type
    value: Any

    # Timestamps
    created_at: ISODatetime | None = Field(None, alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")


class FieldValueCreate(AffinityModel):
    """Data for creating a field value (V1 API)."""

    field_id: FieldId
    entity_id: int
    value: Any
    list_entry_id: ListEntryId | None = None


class FieldValueUpdate(AffinityModel):
    """Data for updating a field value (V1 or V2 API)."""

    value: Any


# =============================================================================
# Field Value Change (History) Models
# =============================================================================


class FieldValueChange(AffinityModel):
    """Historical change to a field value."""

    id: FieldValueChangeId
    field_id: FieldId = Field(alias="fieldId")
    entity_id: int = Field(alias="entityId")
    list_entry_id: ListEntryId | None = Field(None, alias="listEntryId")
    action_type: FieldValueChangeAction = Field(alias="actionType")
    value: Any
    changed_at: ISODatetime = Field(alias="changedAt")
    changer: PersonSummary | None = None


# =============================================================================
# Interaction Models
# =============================================================================


class InteractionDates(AffinityModel):
    """Dates of interactions with an entity."""

    first_email_date: ISODatetime | None = Field(None, alias="firstEmailDate")
    last_email_date: ISODatetime | None = Field(None, alias="lastEmailDate")
    first_event_date: ISODatetime | None = Field(None, alias="firstEventDate")
    last_event_date: ISODatetime | None = Field(None, alias="lastEventDate")
    next_event_date: ISODatetime | None = Field(None, alias="nextEventDate")
    last_chat_message_date: ISODatetime | None = Field(None, alias="lastChatMessageDate")
    last_interaction_date: ISODatetime | None = Field(None, alias="lastInteractionDate")


class InteractionEvent(AffinityModel):
    """Details of an interaction event (meeting, email, etc.)."""

    date: ISODatetime | None = None
    person_ids: list[int] = Field(default_factory=list, alias="personIds")


class Interactions(AffinityModel):
    """Detailed interaction data with person IDs for each interaction type.

    Returned when with_interaction_dates=True and with_interaction_persons=True.
    Fields correspond to those in InteractionDates.
    """

    first_email: InteractionEvent | None = Field(None, alias="firstEmail")
    last_email: InteractionEvent | None = Field(None, alias="lastEmail")
    first_event: InteractionEvent | None = Field(None, alias="firstEvent")
    last_event: InteractionEvent | None = Field(None, alias="lastEvent")
    next_event: InteractionEvent | None = Field(None, alias="nextEvent")
    last_chat_message: InteractionEvent | None = Field(None, alias="lastChatMessage")
    last_interaction: InteractionEvent | None = Field(None, alias="lastInteraction")


# Forward reference resolution
ListEntry.model_rebuild()
Company.model_rebuild()
