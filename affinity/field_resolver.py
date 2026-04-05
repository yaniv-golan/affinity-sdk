"""Field resolution utilities for extracting values by name."""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .field_resolve_utils import (
    resolve_company,
    resolve_interaction,
    resolve_location,
    resolve_person,
)
from .models.types import FieldValueType, ResolveMode

__all__ = ["AmbiguousFieldError", "FieldResolver"]

if TYPE_CHECKING:
    from .models.entities import (
        Company,
        FieldMetadata,
        ListEntryWithEntity,
        Opportunity,
        Person,
    )
    from .models.types import AnyFieldId


class AmbiguousFieldError(Exception):
    """Raised when find_field() matches multiple fields and disambiguation is needed."""

    def __init__(self, field_name: str, candidates: list[FieldMetadata]) -> None:
        self.field_name = field_name
        self.candidates = candidates
        sources = ", ".join(f.enrichment_source or f.type or "unknown" for f in candidates)
        id_list = ", ".join(str(f.id) for f in candidates)
        has_sources = any(f.enrichment_source for f in candidates)
        if has_sources:
            example = next(f for f in candidates if f.enrichment_source)
            hint = (
                f"Disambiguate with 'source:name' syntax "
                f"(e.g., '{example.enrichment_source}:{field_name}'), "
                f"or use the field ID directly. "
                f"Candidate IDs: {id_list}"
            )
        else:
            hint = f"Use the field ID directly. Candidate IDs: {id_list}"
        super().__init__(
            f"Ambiguous field name '{field_name}' matches "
            f"{len(candidates)} fields ({sources}). {hint}"
        )


class FieldResolver:
    """
    Helper for extracting field values by name from entities.

    Caches field metadata internally for efficient repeated lookups.

    Example:
        >>> # Fetch field metadata once
        >>> resolver = FieldResolver(client.companies.get_fields())
        >>>
        >>> # Use throughout your code
        >>> for company in companies:
        ...     status = resolver.get(company, "Status")
        ...     industry = resolver.get(company, "Industry")
        ...     print(f"{company.name}: {status}, {industry}")
    """

    def __init__(self, fields: Sequence[FieldMetadata]) -> None:
        """
        Create a resolver from field metadata.

        Args:
            fields: Field metadata from client.companies.get_fields(),
                    client.persons.get_fields(), etc.
        """
        # Build case-insensitive name -> field(s) mapping.
        # Multiple fields can share the same display name (e.g., enrichment
        # fields from different sources both called "Description").
        self._by_name: dict[str, list[FieldMetadata]] = {}
        self._by_id: dict[str, FieldMetadata] = {}
        self._warned_names: set[str] = set()

        for field in fields:
            name_key = field.name.casefold()
            self._by_name.setdefault(name_key, []).append(field)

            # Register source-qualified key for enrichment fields (e.g., "dealroom:description")
            if field.enrichment_source:
                qualified_key = f"{field.enrichment_source}:{name_key}"
                self._by_name.setdefault(qualified_key, []).append(field)

            self._by_id[str(field.id)] = field

    def get(
        self,
        entity: Company | Person | Opportunity | ListEntryWithEntity,
        field_name: str,
        *,
        resolve: ResolveMode = ResolveMode.RAW,
    ) -> Any:
        """
        Get a field value by name from an entity.

        Args:
            entity: The entity (Company, Person, Opportunity, or ListEntry)
            field_name: The field display name (case-insensitive)
            resolve: ResolveMode.RAW returns extracted values as-is.
                     ResolveMode.TEXT resolves all complex types to strings
                     (dropdowns to text, persons to names, etc.).

        Returns:
            The extracted field value, or None if field not found or not populated.

        Example:
            >>> status = resolver.get(company, "Status")
            >>> print(status)  # "Active"

            >>> # Resolve complex types to text
            >>> stage = resolver.get(opportunity, "Stage", resolve=ResolveMode.TEXT)
            >>> print(stage)  # "Negotiation" instead of 12345
        """
        # Delegate to inner entity for ListEntryWithEntity
        if (
            hasattr(entity, "entity")
            and entity.entity is not None
            and hasattr(entity.entity, "fields")
            and not entity.fields.requested
            and entity.entity.fields.requested
        ):
            entity = entity.entity

        if not entity.fields.requested:
            warnings.warn(
                "Fields were not requested for this entity. "
                "Pass field_ids or field_types when fetching to populate field values.",
                UserWarning,
                stacklevel=2,
            )
            return None

        field = self._resolve_name(field_name, strict=False)
        if field is None:
            return None

        value = entity.fields.get_value(str(field.id))

        if resolve == ResolveMode.TEXT and value is not None:
            value = self._resolve_value(value, field.value_type)

        return value

    def get_many(
        self,
        entity: Company | Person | Opportunity | ListEntryWithEntity,
        field_names: Sequence[str],
        *,
        resolve: ResolveMode = ResolveMode.RAW,
    ) -> dict[str, Any]:
        """
        Get multiple field values by name.

        Args:
            entity: The entity (Company, Person, Opportunity, or ListEntry)
            field_names: List of field names to extract
            resolve: ResolveMode.RAW or ResolveMode.TEXT

        Returns:
            Dict mapping field_name -> value (None for missing/unpopulated fields)

        Example:
            >>> values = resolver.get_many(company, ["Status", "Industry", "Size"])
            >>> print(values)
            {'Status': 'Active', 'Industry': 'Tech', 'Size': None}
        """
        return {name: self.get(entity, name, resolve=resolve) for name in field_names}

    def find_field(self, field_name: str) -> FieldMetadata | None:
        """
        Find field metadata by name (case-insensitive).

        Useful for getting the FieldId needed by write operations like
        ``update_field_value()``. Supports source-qualified names like
        ``'dealroom:Description'`` for ambiguous fields.

        Returns:
            The FieldMetadata, or None if no field matches.

        Raises:
            AmbiguousFieldError: If the name matches multiple fields.
                Use a source-qualified name or get_by_id() to disambiguate.
        """
        return self._resolve_name(field_name, strict=True)

    def _resolve_name(self, field_name: str, *, strict: bool) -> FieldMetadata | None:
        """Resolve a field name to metadata.

        Args:
            field_name: Display name (case-insensitive) or source-qualified.
            strict: If True, raise AmbiguousFieldError on ambiguity.
                    If False, warn and return the first match.

        Returns:
            FieldMetadata or None if not found.
        """
        name_key = field_name.casefold()
        candidates = self._by_name.get(name_key, [])
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        # Ambiguous name
        if strict:
            raise AmbiguousFieldError(field_name, candidates)

        # Warn once per ambiguous bare name at access time
        if name_key not in self._warned_names:
            self._warned_names.add(name_key)
            sources = ", ".join(f.enrichment_source or f.type or "unknown" for f in candidates)
            example_source = candidates[0].enrichment_source
            hint = (
                f"Disambiguate with 'source:name' syntax, e.g., '{example_source}:{field_name}'."
                if example_source
                else "Use get_by_id() for unambiguous access."
            )
            warnings.warn(
                f"Ambiguous field name '{field_name}' matches "
                f"{len(candidates)} fields "
                f"({sources}). Using first match. {hint}",
                UserWarning,
                stacklevel=3,
            )
        return candidates[0]

    def _resolve_value(self, value: Any, value_type: FieldValueType | None) -> Any:
        """Resolve a field value to human-readable text based on its type."""
        if value_type in (
            FieldValueType.DROPDOWN,
            FieldValueType.RANKED_DROPDOWN,
            FieldValueType.DROPDOWN_MULTI,
        ):
            return self._resolve_dropdown(value)

        if value_type == FieldValueType.PERSON:
            return resolve_person(value) or value
        if value_type == FieldValueType.PERSON_MULTI:
            if isinstance(value, list):
                return [resolve_person(v) or v for v in value]
            return value

        if value_type == FieldValueType.COMPANY:
            return resolve_company(value) or value
        if value_type == FieldValueType.COMPANY_MULTI:
            if isinstance(value, list):
                return [resolve_company(v) or v for v in value]
            return value

        if value_type == FieldValueType.LOCATION:
            return resolve_location(value) or value
        if value_type == FieldValueType.LOCATION_MULTI:
            if isinstance(value, list):
                return [resolve_location(v) or v for v in value]
            return value

        if value_type == FieldValueType.INTERACTION:
            return resolve_interaction(value) or value

        return value

    def _resolve_dropdown(self, value: Any) -> Any:
        """Resolve dropdown DropdownOption(s) to text."""
        from .models.entities import DropdownOption  # noqa: PLC0415

        if isinstance(value, list):
            return [self._resolve_dropdown(v) for v in value]
        if isinstance(value, DropdownOption):
            return value.text
        return value

    def get_by_id(
        self,
        entity: Company | Person | Opportunity | ListEntryWithEntity,
        field_id: str | AnyFieldId,
        *,
        resolve: ResolveMode = ResolveMode.RAW,
    ) -> Any:
        """
        Get a field value by ID (unambiguous, for duplicate field names).

        Args:
            entity: The entity
            field_id: The field ID (e.g., "field-123" or FieldId(123))
            resolve: ResolveMode.RAW or ResolveMode.TEXT

        Returns:
            The extracted field value, or None if not found.
        """
        # Delegate to inner entity for ListEntryWithEntity
        if (
            hasattr(entity, "entity")
            and entity.entity is not None
            and hasattr(entity.entity, "fields")
            and not entity.fields.requested
            and entity.entity.fields.requested
        ):
            entity = entity.entity

        field_id_str = str(field_id)
        value = entity.fields.get_value(field_id_str)
        if resolve == ResolveMode.TEXT and value is not None:
            field = self._by_id.get(field_id_str)
            value_type = field.value_type if field else None
            value = self._resolve_value(value, value_type)
        return value

    def field_names(self) -> list[str]:
        """Get all available field names (including source-qualified names for ambiguous fields)."""
        seen: set[str] = set()
        names: list[str] = []
        for key, fields in self._by_name.items():
            # Skip source-qualified keys — they'll be added when we detect ambiguity
            if ":" in key:
                continue
            if len(fields) == 1:
                name = fields[0].name
                if name not in seen:
                    seen.add(name)
                    names.append(name)
            else:
                # Ambiguous: emit source-qualified names
                for f in fields:
                    qualified = f"{f.enrichment_source}:{f.name}" if f.enrichment_source else f.name
                    if qualified not in seen:
                        seen.add(qualified)
                        names.append(qualified)
        return names

    def has_field(self, field_name: str) -> bool:
        """Check if a field exists by name.

        Supports source-qualified names like ``'dealroom:Description'``.
        """
        return field_name.casefold() in self._by_name
