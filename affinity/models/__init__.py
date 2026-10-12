"""
Affinity data models.

All Pydantic models are available from this module.

Tip:
    ID types and enums live in `affinity.types`.
"""

from __future__ import annotations

# Core entities
from .entities import (
    # List
    AffinityList,
    # Base
    AffinityModel,
    # Company
    Company,
    CompanyCreate,
    CompanyUpdate,
    DropdownOption,
    FieldCreate,
    # Field
    FieldMetadata,
    FieldValue,
    FieldValueChange,
    FieldValueCreate,
    ListCreate,
    # List Entry
    ListEntry,
    ListEntryCreate,
    ListEntryWithEntity,
    ListPermission,
    ListSummary,
    # Opportunity
    Opportunity,
    OpportunityCreate,
    OpportunitySummary,
    OpportunityUpdate,
    # Person
    Person,
    PersonCreate,
    PersonUpdate,
    # Saved View
    SavedView,
)
from .interactions_v2 import CallV2, ChatMessageV2, EmailV2, InteractionAttendee, MeetingV2

# Pagination
from .pagination import (
    AsyncPageIterator,
    BatchOperationResponse,
    BatchOperationResult,
    PageIterator,
    PaginatedResponse,
    PaginationInfo,
    PaginationProgress,
)

# Rate limit snapshot (unified)
from .rate_limit_snapshot import RateLimitBucket, RateLimitSnapshot
from .relationships_v2 import Relationship, RelationshipPerson

# Search (V2)
from .search import (
    FileRef,
    FileSearchResult,
    NoteRef,
    NoteSearchResult,
    SemanticCompany,
    SemanticSearchResult,
)

# Secondary models
from .secondary import (
    # File
    CompanyMergeState,
    CompanyRef,
    EntityFile,
    # Field value change (V2)
    FieldValueChangeEntity,
    FieldValueChangeField,
    FieldValueChangeListEntry,
    FieldValueChangeV2,
    Grant,
    # Interaction
    Interaction,
    InteractionCreate,
    InteractionUpdate,
    # Note
    Note,
    NoteCreate,
    NoteUpdate,
    OpportunityRef,
    PersonMergeState,
    PersonRef,
    RateLimitInfo,
    RateLimits,
    # Relationship
    RelationshipStrength,
    # Reminder
    Reminder,
    ReminderCreate,
    ReminderRecurrence,
    ReminderUpdate,
    Tenant,
    # Webhook
    Webhook,
    WebhookCreate,
    WebhookUpdate,
    # Auth
    WhoAmI,
)
from .transcripts import Transcript, TranscriptFragment

__all__ = [
    # Base
    "AffinityModel",
    # Person
    "Person",
    "PersonCreate",
    "PersonUpdate",
    # Company
    "Company",
    "CompanyCreate",
    "CompanyUpdate",
    # Opportunity
    "Opportunity",
    "OpportunityCreate",
    "OpportunitySummary",
    "OpportunityUpdate",
    # List
    "AffinityList",
    "ListSummary",
    "ListCreate",
    "ListPermission",
    # List Entry
    "ListEntry",
    "ListEntryCreate",
    "ListEntryWithEntity",
    # Field
    "FieldMetadata",
    "FieldCreate",
    "FieldValue",
    "FieldValueChange",
    "FieldValueChangeEntity",
    "FieldValueChangeField",
    "FieldValueChangeListEntry",
    "FieldValueChangeV2",
    "Transcript",
    "CallV2",
    "ChatMessageV2",
    "EmailV2",
    "InteractionAttendee",
    "MeetingV2",
    "Relationship",
    "RelationshipPerson",
    "CompanyMergeState",
    "PersonMergeState",
    "TranscriptFragment",
    "FieldValueCreate",
    "DropdownOption",
    # Saved View
    "SavedView",
    # Note
    "Note",
    "NoteCreate",
    "NoteUpdate",
    # Reminder
    "Reminder",
    "ReminderCreate",
    "ReminderRecurrence",
    "ReminderUpdate",
    # Webhook
    "Webhook",
    "WebhookCreate",
    "WebhookUpdate",
    "PersonRef",
    "CompanyRef",
    "OpportunityRef",
    # Interaction
    "Interaction",
    "InteractionCreate",
    "InteractionUpdate",
    # File
    "EntityFile",
    # Search (V2)
    "NoteRef",
    "NoteSearchResult",
    "FileRef",
    "FileSearchResult",
    "SemanticCompany",
    "SemanticSearchResult",
    # Relationship
    "RelationshipStrength",
    # Auth
    "WhoAmI",
    "RateLimits",
    "RateLimitInfo",
    "RateLimitBucket",
    "RateLimitSnapshot",
    "Tenant",
    "Grant",
    # Pagination
    "PaginationInfo",
    "PaginationProgress",
    "PaginatedResponse",
    "PageIterator",
    "AsyncPageIterator",
    "BatchOperationResponse",
    "BatchOperationResult",
]
