"""
Affinity Python SDK - A modern, strongly-typed wrapper for the Affinity CRM API.

This SDK provides:
- V2 terminology throughout (Company, not Organization)
- V2 API for reads, V1 for writes where V2 isn't available
- Strong typing with Pydantic V2 models
- Typed ID classes to prevent type confusion (PersonId, CompanyId, etc.)
- Automatic pagination iterators
- Optional response caching for field metadata
- Rate limit handling with automatic retry
- Both sync and async clients

Example:
    ```python
    from affinity import Affinity
    from affinity.types import CompanyId, FieldId, FieldType, ListId, PersonId

    # Initialize
    with Affinity(api_key="your-key") as client:
        # Iterate all companies with enriched data
        for company in client.companies.all(field_types=[FieldType.ENRICHED]):
            print(f"{company.name}: {company.domain}")

        # Get a person with field values
        person = client.persons.get(PersonId(12345))

        # Add to a list and update fields
        entries = client.lists.entries(ListId(789))
        entry = entries.add_company(CompanyId(456))
        entries.update_field_value(entry.id, FieldId(101), "New status")
    ```
"""

from __future__ import annotations

import logging
from importlib.metadata import version as _get_version

# Main client
from . import models, types
from .client import Affinity, AsyncAffinity

# Exceptions
from .exceptions import (
    AffinityError,
    AuthenticationError,
    AuthorizationError,
    CompanyMergedError,
    CompanyNotFoundError,
    ConfigurationError,
    ConflictError,
    EnrichedFieldNotWritableError,
    EntityNotFoundError,
    FilterParseError,
    MergedEntityError,
    NetworkError,
    NotFoundError,
    OpportunityNotFoundError,
    PersonMergedError,
    PersonNotFoundError,
    PolicyError,
    RateLimitError,
    ServerError,
    TimeoutError,
    TooManyResultsError,
    UnsupportedOperationError,
    ValidationError,
    VersionCompatibilityError,
    WebhookInvalidJsonError,
    WebhookInvalidPayloadError,
    WebhookInvalidSentAtError,
    WebhookMissingKeyError,
    WebhookParseError,
    WriteNotAllowedError,
)

# Field resolution
from .field_resolver import AmbiguousFieldError, FieldResolver

# Filter builder (FR-007)
from .filters import F, Filter, FilterExpression

# Inbound webhook parsing helpers (optional)
from .inbound_webhooks import BodyRegistry, WebhookEnvelope, dispatch_webhook, parse_webhook

# Pagination helpers
from .models.pagination import PaginationProgress
from .models.types import ResolveMode

# Policies
from .policies import ExternalHookPolicy, Policies, WritePolicy

# File download helpers
from .services.v1_only import PresignedUrl

__version__ = _get_version("affinity-sdk")

_logger = logging.getLogger("affinity_sdk")
if not any(isinstance(h, logging.NullHandler) for h in _logger.handlers):
    _logger.addHandler(logging.NullHandler())

__all__ = [
    # Main clients
    "Affinity",
    "AsyncAffinity",
    # Exceptions
    "AffinityError",
    "AuthenticationError",
    "AuthorizationError",
    "NotFoundError",
    "EntityNotFoundError",
    "PersonNotFoundError",
    "CompanyNotFoundError",
    "OpportunityNotFoundError",
    "MergedEntityError",
    "CompanyMergedError",
    "PersonMergedError",
    "ValidationError",
    "RateLimitError",
    "ConflictError",
    "ServerError",
    "ConfigurationError",
    "TimeoutError",
    "NetworkError",
    "PolicyError",
    "WriteNotAllowedError",
    "TooManyResultsError",
    "VersionCompatibilityError",
    "UnsupportedOperationError",
    "EnrichedFieldNotWritableError",
    "WebhookParseError",
    "WebhookInvalidJsonError",
    "WebhookInvalidPayloadError",
    "WebhookMissingKeyError",
    "WebhookInvalidSentAtError",
    "FilterParseError",
    "AmbiguousFieldError",
    # Filter builder
    "Filter",
    "FilterExpression",
    "F",
    # Inbound webhooks
    "WebhookEnvelope",
    "parse_webhook",
    "dispatch_webhook",
    "BodyRegistry",
    # Policies
    "WritePolicy",
    "ExternalHookPolicy",
    "Policies",
    # Pagination helpers
    "PaginationProgress",
    # Field resolution
    "FieldResolver",
    "ResolveMode",
    # File download helpers
    "PresignedUrl",
    # Type aliases (re-exported for convenience)
    "types",
    "models",
]
