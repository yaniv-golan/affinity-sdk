"""
Strongly-typed IDs and core type definitions for the Affinity API.

This module provides type-safe ID wrappers to prevent mixing up different entity IDs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Annotated, Any, SupportsInt, TypeAlias, cast

from pydantic import AfterValidator, Field, GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

# =============================================================================
# Typed IDs - These provide type safety to prevent mixing up different entity IDs
# =============================================================================


class IntId(int):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        _ = source_type
        return core_schema.no_info_after_validator_function(cls, handler(int))


class StrId(str):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        _ = source_type
        return core_schema.no_info_after_validator_function(cls, handler(str))


class PersonId(IntId):
    pass


class CompanyId(IntId):
    """Called Organization in V1."""


class OpportunityId(IntId):
    pass


class ListId(IntId):
    pass


class ListEntryId(IntId):
    pass


_FIELD_ID_RE = re.compile(r"^field-(\d+)$")


class FieldId(StrId):
    """
    V2-style field id (e.g. 'field-123').

    FieldId provides normalized comparison semantics:
    - ``FieldId(123) == FieldId("123")`` → ``True``
    - ``FieldId("field-123") == "field-123"`` → ``True``
    - ``FieldId("field-123") == 123`` → ``True``

    This normalization is specific to FieldId because field IDs uniquely come
    from mixed sources (some APIs return integers, some return strings like
    "field-123"). Other TypedId subclasses (PersonId, CompanyId, etc.) don't
    have this problem - they consistently use integers.
    """

    def __new__(cls, value: Any) -> FieldId:
        """Normalize value to 'field-xxx' format at construction time."""
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            return str.__new__(cls, f"field-{value}")
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.isdigit():
                return str.__new__(cls, f"field-{candidate}")
            if _FIELD_ID_RE.match(candidate):
                return str.__new__(cls, candidate)
        raise ValueError("FieldId must be an int, digits, or 'field-<digits>'")

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        _ = source_type
        _ = handler

        def validate(value: Any) -> FieldId:
            # Use __new__ which handles all normalization
            return cls(value)

        return core_schema.no_info_plain_validator_function(validate)

    def __eq__(self, other: object) -> bool:
        """
        Normalize comparison for FieldId.

        Supports comparison with:
        - Other FieldId instances
        - Strings (e.g., "field-123" or "123")
        - Integers (e.g., 123)
        """
        if isinstance(other, FieldId):
            # Both are FieldId - compare string representations
            return str.__eq__(self, other)
        if isinstance(other, str):
            # Compare with string - could be "field-123" or "123"
            try:
                other_normalized = FieldId(other)
                return str.__eq__(self, other_normalized)
            except ValueError:
                return False
        if isinstance(other, int):
            # Compare with integer
            return str.__eq__(self, f"field-{other}")
        return NotImplemented

    def __hash__(self) -> int:
        """Hash the string representation for dict/set usage."""
        return str.__hash__(self)

    def __repr__(self) -> str:
        """Return a representation useful for debugging."""
        return f"FieldId({str.__repr__(self)})"

    def __str__(self) -> str:
        """Return the canonical string value (e.g., 'field-123')."""
        return str.__str__(self)


class FieldValueId(IntId):
    pass


class NoteId(IntId):
    pass


class ReminderIdType(IntId):
    pass


class WebhookId(IntId):
    pass


class InteractionId(IntId):
    pass


class FileId(IntId):
    pass


class SavedViewId(IntId):
    pass


class DropdownOptionId(IntId):
    pass


class UserId(PersonId):
    """Workspace member ID. A subtype of PersonId — users are internal persons in Affinity."""


class TenantId(IntId):
    pass


class FieldValueChangeId(IntId):
    pass


class TaskId(StrId):
    """UUIDs for async tasks."""


class EnrichedFieldId(StrId):
    """Enriched field IDs are strings in V2 (e.g., 'affinity-data-description')."""


# Combined Field ID type - can be either numeric or string
AnyFieldId: TypeAlias = FieldId | EnrichedFieldId


def field_id_to_v1_numeric(field_id: AnyFieldId) -> int:
    """
    Convert v2 FieldId into v1 numeric field_id.

    Accepts:
    - FieldId('field-123') -> 123
    Rejects:
    - EnrichedFieldId(...) (cannot be represented as v1 numeric id)
    """
    if isinstance(field_id, EnrichedFieldId):
        raise ValueError(
            "Field IDs must be 'field-<digits>' for v1 conversion; "
            "enriched/relationship-intelligence IDs (e.g., 'affinity-data-*', "
            "'source-of-introduction') are not supported."
        )

    match = _FIELD_ID_RE.match(str(field_id))
    if match is None:
        raise ValueError(
            "Field IDs must be 'field-<digits>' for v1 conversion; "
            "enriched/relationship-intelligence IDs (e.g., 'affinity-data-*', "
            "'source-of-introduction') are not supported."
        )
    return int(match.group(1))


# =============================================================================
# Enums - Replace all magic numbers with type-safe enums
# =============================================================================


class OpenIntEnum(IntEnum):
    @classmethod
    def _missing_(cls, value: object) -> OpenIntEnum:
        try:
            int_value = int(cast(SupportsInt | str | bytes | bytearray, value))
        except (TypeError, ValueError) as e:
            raise ValueError(value) from e

        obj = int.__new__(cls, int_value)
        obj._value_ = int_value
        obj._name_ = f"UNKNOWN_{int_value}"
        cls._value2member_map_[int_value] = obj
        return obj


class OpenStrEnum(str, Enum):
    @classmethod
    def _missing_(cls, value: object) -> OpenStrEnum:
        text = str(value)
        obj = str.__new__(cls, text)
        obj._value_ = text
        obj._name_ = f"UNKNOWN_{text}"
        cls._value2member_map_[text] = obj
        return obj

    def __str__(self) -> str:
        return str(self.value)


class ListType(OpenIntEnum):
    """Type of entities a list can contain."""

    PERSON = 0
    COMPANY = 1
    OPPORTUNITY = 8

    # V1 compatibility alias - prefer COMPANY in new code
    ORGANIZATION = COMPANY

    @classmethod
    def _missing_(cls, value: object) -> OpenIntEnum:
        # V2 list endpoints commonly return string types (e.g. "company").
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("person", "people"):
                return cls.PERSON
            if text in ("company", "organization", "organisation"):
                return cls.COMPANY
            if text in ("opportunity", "opportunities"):
                return cls.OPPORTUNITY
        return super()._missing_(value)


class EntityType(OpenIntEnum):
    """Entity types in Affinity."""

    PERSON = 0
    ORGANIZATION = 1
    OPPORTUNITY = 8


class PersonType(OpenStrEnum):
    """Types of persons in Affinity."""

    INTERNAL = "internal"
    EXTERNAL = "external"
    COLLABORATOR = "collaborator"


class FieldValueType(OpenStrEnum):
    """
    Field value types (V2-first).

    V2 represents `valueType` as strings (e.g. "dropdown-multi", "ranked-dropdown", "interaction").
    V1 represents field value types as numeric codes; numeric inputs are normalized into the closest
    V2 string type where possible.
    """

    TEXT = "text"

    NUMBER = "number"
    NUMBER_MULTI = "number-multi"

    DATETIME = "datetime"  # V2 canonical (V1 docs call this "Date")

    LOCATION = "location"
    LOCATION_MULTI = "location-multi"

    DROPDOWN = "dropdown"
    DROPDOWN_MULTI = "dropdown-multi"
    RANKED_DROPDOWN = "ranked-dropdown"

    PERSON = "person"
    PERSON_MULTI = "person-multi"

    COMPANY = "company"  # V1 calls this "organization"
    COMPANY_MULTI = "company-multi"

    FILTERABLE_TEXT = "filterable-text"
    FILTERABLE_TEXT_MULTI = "filterable-text-multi"

    INTERACTION = "interaction"  # V2-only (relationship-intelligence)

    @classmethod
    def _missing_(cls, value: object) -> OpenStrEnum:
        # Normalize known V1 numeric codes to canonical V2 strings.
        # V1 API field value types :
        #   0 = Person, 1 = Organization, 2 = Dropdown (simple),
        #   3 = Number, 4 = Date, 5 = Location,
        #   6 = Text (long text block), 7 = Ranked Dropdown (with colors),
        #   10 = Filterable Text
        if isinstance(value, int):
            mapping: dict[int, FieldValueType] = {
                0: cls.PERSON,
                1: cls.COMPANY,
                2: cls.DROPDOWN,  # V1 "Dropdown" (simple, free text)
                3: cls.NUMBER,
                4: cls.DATETIME,
                5: cls.LOCATION,
                6: cls.TEXT,  # V1 "Text" (long text block)
                7: cls.RANKED_DROPDOWN,  # V1 "Ranked Dropdown" (with colors)
                10: cls.FILTERABLE_TEXT,
            }
            if value in mapping:
                return mapping[value]

            # Keep "unknown numeric" inputs stable by caching under the int key as well.
            text = str(value)
            existing = cls._value2member_map_.get(text)
            if existing is not None:
                existing_enum = cast(OpenStrEnum, existing)
                cls._value2member_map_[value] = existing_enum
                return existing_enum
            created = super()._missing_(text)
            cls._value2member_map_[value] = created
            return created

        if isinstance(value, str):
            text = value.strip()
            lowered = text.lower()
            if lowered == "date":
                return cls.DATETIME
            if lowered in ("organization", "organisation"):
                return cls.COMPANY
            if lowered in ("organization-multi", "organisation-multi"):
                return cls.COMPANY_MULTI
            if lowered == "filterable_text":
                return cls.FILTERABLE_TEXT

        return super()._missing_(value)


def to_v1_value_type_code(
    *,
    value_type: FieldValueType,
    raw: str | int | None = None,
) -> int | None:
    """
    Convert a V2-first `FieldValueType` into a V1 numeric code (when possible).

    Notes:
    - If `raw` is already a known V1 numeric code, it is returned as-is.
    - `interaction` is V2-only and has no V1 equivalent; returns None.
    """

    if isinstance(raw, int):
        if raw in (0, 1, 2, 3, 4, 5, 7, 10):
            return raw
        return raw

    # V1 API field value types :
    #   0 = Person, 1 = Organization, 2 = Dropdown (simple),
    #   3 = Number, 4 = Date, 5 = Location,
    #   6 = Text (long text block), 7 = Ranked Dropdown (with colors),
    #   10 = Filterable Text
    match value_type:
        case FieldValueType.PERSON | FieldValueType.PERSON_MULTI:
            return 0
        case FieldValueType.COMPANY | FieldValueType.COMPANY_MULTI:
            return 1
        case FieldValueType.DROPDOWN | FieldValueType.DROPDOWN_MULTI:
            return 2  # V1 "Dropdown" (simple, free text)
        case FieldValueType.NUMBER | FieldValueType.NUMBER_MULTI:
            return 3
        case FieldValueType.DATETIME:
            return 4
        case FieldValueType.LOCATION | FieldValueType.LOCATION_MULTI:
            return 5
        case FieldValueType.TEXT:
            return 6  # V1 "Text" (long text block)
        case FieldValueType.RANKED_DROPDOWN:
            return 7  # V1 "Ranked Dropdown" (with colors)
        case FieldValueType.FILTERABLE_TEXT | FieldValueType.FILTERABLE_TEXT_MULTI:
            return 10
        case FieldValueType.INTERACTION:
            return None
        case _:
            return None


class FieldType(OpenStrEnum):
    """
    Field type for API parameters and response metadata.

    Note: LIST is only valid in requests to list entry endpoints.
    Company/person endpoints accept ENRICHED, GLOBAL, and
    RELATIONSHIP_INTELLIGENCE only.
    """

    ENRICHED = "enriched"
    LIST = "list"
    GLOBAL = "global"
    RELATIONSHIP_INTELLIGENCE = "relationship-intelligence"


# Field types valid for entity (company/person) endpoints — LIST is not valid there
_ENTITY_FIELD_TYPES = frozenset(
    {FieldType.ENRICHED, FieldType.GLOBAL, FieldType.RELATIONSHIP_INTELLIGENCE}
)


def validate_entity_field_types(field_types: Any, *, endpoint: str) -> None:
    """Raise ValueError if field_types contains LIST (only valid for list entry endpoints)."""
    if field_types is None:
        return
    for ft in field_types:
        if ft == FieldType.LIST:
            raise ValueError(
                f"FieldType.LIST is not valid for {endpoint} endpoints. "
                f"LIST is only valid for list entry endpoints. "
                f"Valid types: enriched, global, relationship-intelligence."
            )


class ResolveMode(Enum):
    """Controls how FieldResolver.get() returns complex field values."""

    RAW = "raw"  # Return extracted values as-is (default)
    TEXT = "text"  # Resolve all complex types to human-readable strings


class InteractionType(OpenIntEnum):
    """Types of interactions."""

    MEETING = 0  # Also called Event
    CALL = 1
    CHAT_MESSAGE = 2
    EMAIL = 3


class InteractionDirection(OpenIntEnum):
    """Direction of communication for interactions."""

    OUTGOING = 0
    INCOMING = 1


class InteractionLoggingType(OpenIntEnum):
    """How the interaction was logged."""

    AUTOMATIC = 0
    MANUAL = 1


class ReminderType(OpenIntEnum):
    """Types of reminders."""

    ONE_TIME = 0
    RECURRING = 1


class ReminderResetType(OpenIntEnum):
    """How recurring reminders get reset."""

    INTERACTION = 0  # Email or meeting
    EMAIL = 1
    MEETING = 2


class ReminderStatus(OpenIntEnum):
    """Current status of a reminder."""

    COMPLETED = 0
    ACTIVE = 1
    OVERDUE = 2


class NoteType(OpenIntEnum):
    """Types of notes."""

    PLAIN_TEXT = 0
    EMAIL_DERIVED = 1  # Deprecated creation method
    HTML = 2
    AI_NOTETAKER = 3


class NoteKind(OpenStrEnum):
    """Kind of a note as reported by V2 note search (``note.kind``)."""

    NOTE = "note"
    MEETING_NOTE = "meeting-note"
    EMAIL_NOTE = "email-note"
    AI_SUMMARY = "ai-summary"
    MEETING_AI_SUMMARY = "meeting-ai-summary"
    CHAT_MESSAGE_NOTE = "chat-message-note"


class ListRole(OpenIntEnum):
    """Roles for list-level permissions."""

    ADMIN = 0
    BASIC = 1
    STANDARD = 2


class FieldValueChangeAction(OpenIntEnum):
    """Types of changes that can occur to field values."""

    CREATE = 0
    DELETE = 1
    UPDATE = 2


class WebhookEvent(OpenStrEnum):
    """
    Supported webhook events (30 total).

    Events cover CRUD operations on Affinity entities:

    - **Lists**: created, updated, deleted
    - **List entries**: created, deleted
    - **Notes**: created, updated, deleted
    - **Fields**: created, updated, deleted
    - **Field values**: created, updated, deleted
    - **Persons**: created, updated, deleted
    - **Organizations (companies)**: created, updated, deleted, merged
    - **Opportunities**: created, updated, deleted
    - **Files**: created, deleted
    - **Smart interaction values**: updated
    - **Reminders**: created, updated, deleted

    This enum extends ``OpenStrEnum`` for forward compatibility - any unknown
    events from Affinity are preserved as strings rather than raising errors.

    See the webhooks guide for complete documentation and usage examples.
    """

    LIST_CREATED = "list.created"
    LIST_UPDATED = "list.updated"
    LIST_DELETED = "list.deleted"
    LIST_ENTRY_CREATED = "list_entry.created"
    LIST_ENTRY_DELETED = "list_entry.deleted"
    NOTE_CREATED = "note.created"
    NOTE_UPDATED = "note.updated"
    NOTE_DELETED = "note.deleted"
    FIELD_CREATED = "field.created"
    FIELD_UPDATED = "field.updated"
    FIELD_DELETED = "field.deleted"
    FIELD_VALUE_CREATED = "field_value.created"
    FIELD_VALUE_UPDATED = "field_value.updated"
    FIELD_VALUE_DELETED = "field_value.deleted"
    PERSON_CREATED = "person.created"
    PERSON_UPDATED = "person.updated"
    PERSON_DELETED = "person.deleted"
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
    ORGANIZATION_MERGED = "organization.merged"
    OPPORTUNITY_CREATED = "opportunity.created"
    OPPORTUNITY_UPDATED = "opportunity.updated"
    OPPORTUNITY_DELETED = "opportunity.deleted"
    FILE_CREATED = "file.created"
    FILE_DELETED = "file.deleted"
    SMART_INTERACTION_VALUE_UPDATED = "smart_interaction_value.updated"
    REMINDER_CREATED = "reminder.created"
    REMINDER_UPDATED = "reminder.updated"
    REMINDER_DELETED = "reminder.deleted"


class DropdownOptionColor(IntEnum):
    """
    Colors for dropdown options.

    Affinity uses integer color codes for dropdown field options.
    """

    DEFAULT = 0
    BLUE = 1
    GREEN = 2
    YELLOW = 3
    ORANGE = 4
    RED = 5
    PURPLE = 6
    GRAY = 7


class MergeStatus(str, Enum):
    """Status of async merge operations (V2 spec spelling: ``in-progress``)."""

    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    SUCCESS = "success"
    FAILED = "failed"

    @classmethod
    def _missing_(cls, value: object) -> MergeStatus | None:
        # Accept the SDK's earlier underscore spelling ("in_progress").
        if isinstance(value, str) and "_" in value:
            return cls._value2member_map_.get(value.replace("_", "-"))  # type: ignore[return-value]
        return None


# =============================================================================
# API Version tracking
# =============================================================================


class APIVersion(str, Enum):
    """API versions with their base URLs."""

    V1 = "v1"
    V2 = "v2"


# Base URLs
V1_BASE_URL = "https://api.affinity.co"
V2_BASE_URL = "https://api.affinity.co/v2"


# =============================================================================
# Common type aliases with validation
# =============================================================================

PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
EmailStr = Annotated[str, Field(pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$")]

# =============================================================================
# Datetime with UTC normalization
# =============================================================================


def _normalize_to_utc(v: datetime) -> datetime:
    """
    Normalize datetime to UTC-aware.

    This validator ensures all ISODatetime values are:
    1. Timezone-aware (never naive)
    2. Normalized to UTC

    Input handling:
    - Naive datetime: Assumed UTC, tzinfo added
    - UTC datetime: Passed through unchanged
    - Non-UTC aware: Converted to UTC equivalent

    This guarantees ISODatetime values are always directly comparable
    without risk of TypeError from naive/aware mixing.

    Note: This differs from CLI input parsing (parse_iso_datetime)
    which interprets naive strings as local time for user convenience.
    SDK uses UTC assumption because API responses are always UTC.
    """
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


# Datetime with UTC normalization - all values guaranteed UTC-aware
# IMPORTANT: Use AfterValidator, not BeforeValidator!
# BeforeValidator runs before Pydantic's type coercion, so input could be a string.
# AfterValidator runs after Pydantic parses the string to datetime.
ISODatetime = Annotated[datetime, AfterValidator(_normalize_to_utc)]


# =============================================================================
# Filter operators for V2 API query language
# =============================================================================


class FilterOperator(str, Enum):
    """Operators for V2 filtering."""

    EQUALS = "="
    NOT_EQUALS = "!="
    STARTS_WITH = "=^"
    ENDS_WITH = "=$"
    CONTAINS = "=~"
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    IS_NULL = "!= *"
    IS_NOT_NULL = "= *"
    IS_EMPTY = '= ""'
