"""Entity schema registry.

Defines entity types, their fields, and relationships for the query engine.
This module is CLI-only and NOT part of the public SDK API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Literal


class FetchStrategy(Enum):
    """How an entity type can be fetched as a top-level query."""

    # Can call service.all() directly - e.g., persons, companies, opportunities
    GLOBAL = auto()

    # Requires a parent ID filter - e.g., listEntries needs listId
    REQUIRES_PARENT = auto()

    # Can only be fetched as a relationship, not directly queried
    RELATIONSHIP_ONLY = auto()


@dataclass(frozen=True)
class RelationshipDef:
    """Defines how to fetch related entities.

    Attributes:
        target_entity: The entity type being related to
        fetch_strategy: How to fetch the related entities:
            - "entity_method": Call method on entity service
            - "global_service": Call global service with filter
            - "list_entry_indirect": For listEntries - fetch via entity associations
              (uses method_or_service as target entity type: "persons", "companies", etc.)
        method_or_service: Method name for entity_method, service attr for global,
            or target entity type for list_entry_indirect
        filter_field: For global_service: the filter param name
        cardinality: Whether the relationship is one-to-one or one-to-many
        requires_n_plus_1: Does fetching require per-record API calls?
        display_fields: Default fields to display in inline expansion.
            If None, uses _display_value() fallback priority: name → firstName → title → email → id
    """

    target_entity: str
    fetch_strategy: Literal["entity_method", "global_service", "list_entry_indirect"]
    method_or_service: str
    filter_field: str | None = None
    cardinality: Literal["one", "many"] = "many"
    requires_n_plus_1: bool = True
    display_fields: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ExpansionDef:
    """Defines how to expand/enrich records with computed data.

    Unlike relationships (which fetch separate entities), expansions
    add computed data directly to the main records.

    Attributes:
        name: Expansion name (e.g., "interactionDates")
        supported_entities: Entity types that support this expansion
        fetch_params: Parameters to pass to service.get() to fetch expansion data
        requires_refetch: Whether entity must be re-fetched with params
    """

    name: str
    supported_entities: frozenset[str]
    fetch_params: dict[str, Any]
    requires_refetch: bool = True


@dataclass(frozen=True)
class EntitySchema:
    """Schema definition for an entity type.

    Attributes:
        name: Entity type name (e.g., "persons", "companies")
        service_attr: Attribute name on Affinity client (e.g., "persons")
        id_field: Name of the ID field (usually "id")
        filterable_fields: Fields that can be used in WHERE clauses
        computed_fields: Fields that are computed (e.g., "firstEmail", "lastEmail")
        relationships: Dict of relationship name -> RelationshipDef
        api_version: Primary API version for this entity ("v1" or "v2")
        fetch_strategy: How to fetch this entity as a top-level query
        required_filters: Filter fields required for REQUIRES_PARENT entities
        parent_filter_field: Field name in where clause (e.g., "listId")
        parent_id_type: Type name to cast to (e.g., "ListId")
        parent_method_name: Method to call on parent service (e.g., "entries")
    """

    name: str
    service_attr: str
    id_field: str
    filterable_fields: frozenset[str]
    computed_fields: frozenset[str]
    relationships: dict[str, RelationshipDef]
    api_version: Literal["v1", "v2"] = "v2"
    fetch_strategy: FetchStrategy = FetchStrategy.GLOBAL
    required_filters: frozenset[str] = field(default_factory=frozenset)
    parent_filter_field: str | None = None
    parent_id_type: str | None = None
    parent_method_name: str | None = None
    supported_expansions: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate schema configuration at definition time."""
        # Validate REQUIRES_PARENT has all required fields
        if self.fetch_strategy == FetchStrategy.REQUIRES_PARENT:
            if not self.required_filters:
                raise ValueError(
                    f"Entity '{self.name}' with REQUIRES_PARENT must have required_filters"
                )
            if not self.parent_filter_field:
                raise ValueError(
                    f"Entity '{self.name}' with REQUIRES_PARENT must have parent_filter_field"
                )
            if not self.parent_method_name:
                raise ValueError(
                    f"Entity '{self.name}' with REQUIRES_PARENT must have parent_method_name"
                )

        # Validate parent_id_type exists in affinity.types (fail-fast at import time)
        if self.parent_id_type:
            from affinity import types as affinity_types

            if not hasattr(affinity_types, self.parent_id_type):
                raise ValueError(
                    f"Entity '{self.name}' references unknown type '{self.parent_id_type}'. "
                    f"Must be a type in affinity.types module."
                )


# =============================================================================
# Schema Registry
# =============================================================================

SCHEMA_REGISTRY: dict[str, EntitySchema] = {
    "persons": EntitySchema(
        name="persons",
        service_attr="persons",
        id_field="id",
        filterable_fields=frozenset(),  # V2 /persons does not support server-side filter
        computed_fields=frozenset(["firstEmail", "lastEmail"]),
        relationships={
            "companies": RelationshipDef(
                target_entity="companies",
                fetch_strategy="entity_method",
                method_or_service="get_associated_company_ids",
                requires_n_plus_1=True,
                display_fields=("name",),
            ),
            "opportunities": RelationshipDef(
                target_entity="opportunities",
                fetch_strategy="entity_method",
                method_or_service="get_associated_opportunity_ids",
                requires_n_plus_1=True,
                display_fields=("name",),
            ),
            "interactions": RelationshipDef(
                target_entity="interactions",
                fetch_strategy="global_service",
                method_or_service="interactions",
                filter_field="person_id",
                requires_n_plus_1=True,  # API requires one entity ID per call
                display_fields=("type", "happenedAt"),
            ),
            "notes": RelationshipDef(
                target_entity="notes",
                fetch_strategy="global_service",
                method_or_service="notes",
                filter_field="person_id",
                requires_n_plus_1=True,  # API requires one entity ID per call
                display_fields=("content",),
            ),
            "listEntries": RelationshipDef(
                target_entity="listEntries",
                fetch_strategy="entity_method",
                method_or_service="get_list_entries",
                requires_n_plus_1=True,
                display_fields=("id",),  # List entries don't have a name field
            ),
        },
        fetch_strategy=FetchStrategy.GLOBAL,
        supported_expansions=frozenset(["interactionDates"]),
    ),
    "companies": EntitySchema(
        name="companies",
        service_attr="companies",
        id_field="id",
        filterable_fields=frozenset(),  # V2 /companies does not support server-side filter
        computed_fields=frozenset([]),
        relationships={
            "persons": RelationshipDef(
                target_entity="persons",
                fetch_strategy="entity_method",
                method_or_service="get_associated_person_ids",
                requires_n_plus_1=True,
                display_fields=("firstName", "lastName"),
            ),
            "opportunities": RelationshipDef(
                target_entity="opportunities",
                fetch_strategy="entity_method",
                method_or_service="get_associated_opportunity_ids",
                requires_n_plus_1=True,
                display_fields=("name",),
            ),
            "interactions": RelationshipDef(
                target_entity="interactions",
                fetch_strategy="global_service",
                method_or_service="interactions",
                filter_field="company_id",
                requires_n_plus_1=True,  # API requires one entity ID per call
                display_fields=("type", "happenedAt"),
            ),
            "notes": RelationshipDef(
                target_entity="notes",
                fetch_strategy="global_service",
                method_or_service="notes",
                filter_field="company_id",
                requires_n_plus_1=True,  # API requires one entity ID per call
                display_fields=("content",),
            ),
            "listEntries": RelationshipDef(
                target_entity="listEntries",
                fetch_strategy="entity_method",
                method_or_service="get_list_entries",
                requires_n_plus_1=True,
                display_fields=("id",),  # List entries don't have a name field
            ),
        },
        fetch_strategy=FetchStrategy.GLOBAL,
        supported_expansions=frozenset(["interactionDates"]),
    ),
    "opportunities": EntitySchema(
        name="opportunities",
        service_attr="opportunities",
        id_field="id",
        filterable_fields=frozenset(),  # V2 /opportunities does not support server-side filter
        computed_fields=frozenset([]),
        relationships={
            "persons": RelationshipDef(
                target_entity="persons",
                fetch_strategy="entity_method",
                method_or_service="get_associated_person_ids",
                requires_n_plus_1=True,
                display_fields=("firstName", "lastName"),
            ),
            "companies": RelationshipDef(
                target_entity="companies",
                fetch_strategy="entity_method",
                method_or_service="get_associated_company_ids",
                requires_n_plus_1=True,
                display_fields=("name",),
            ),
            "interactions": RelationshipDef(
                target_entity="interactions",
                fetch_strategy="global_service",
                method_or_service="interactions",
                filter_field="opportunity_id",
                requires_n_plus_1=True,  # API requires one entity ID per call
                display_fields=("type", "happenedAt"),
            ),
        },
        api_version="v1",
        fetch_strategy=FetchStrategy.GLOBAL,
    ),
    "lists": EntitySchema(
        name="lists",
        service_attr="lists",
        id_field="id",
        filterable_fields=frozenset(
            [
                "id",
                "name",
                "type",
                "createdAt",
            ]
        ),
        computed_fields=frozenset([]),
        relationships={
            "entries": RelationshipDef(
                target_entity="listEntries",
                fetch_strategy="entity_method",
                method_or_service="entries",
                cardinality="many",
                requires_n_plus_1=True,
                display_fields=("id",),  # List entries don't have a name field
            ),
        },
        fetch_strategy=FetchStrategy.GLOBAL,
        api_version="v2",
    ),
    "listEntries": EntitySchema(
        name="listEntries",
        service_attr="lists",  # Uses list service, then .entries()
        id_field="id",
        filterable_fields=frozenset(
            [
                "id",
                "listId",
                "listName",  # Alternative to listId (resolved at execution time)
                "entityId",
                "entityType",
                "createdAt",
                "updatedAt",
            ]
        ),
        computed_fields=frozenset([]),
        relationships={
            "entity": RelationshipDef(
                target_entity="entity",  # Dynamic based on entityType
                fetch_strategy="entity_method",
                method_or_service="get_entity",
                cardinality="one",
                requires_n_plus_1=True,
            ),
            "persons": RelationshipDef(
                target_entity="persons",
                fetch_strategy="list_entry_indirect",
                method_or_service="persons",  # Target entity type for handler
                cardinality="many",
                requires_n_plus_1=True,
                display_fields=("firstName", "lastName", "primaryEmail"),
            ),
            "companies": RelationshipDef(
                target_entity="companies",
                fetch_strategy="list_entry_indirect",
                method_or_service="companies",
                cardinality="many",
                requires_n_plus_1=True,
                display_fields=("name", "domain"),
            ),
            "opportunities": RelationshipDef(
                target_entity="opportunities",
                fetch_strategy="list_entry_indirect",
                method_or_service="opportunities",
                cardinality="many",
                requires_n_plus_1=True,
                display_fields=("name",),
            ),
            "interactions": RelationshipDef(
                target_entity="interactions",
                fetch_strategy="list_entry_indirect",
                method_or_service="interactions",
                cardinality="many",
                requires_n_plus_1=True,
                display_fields=("type", "subject", "happenedAt"),
            ),
        },
        api_version="v2",
        fetch_strategy=FetchStrategy.REQUIRES_PARENT,
        required_filters=frozenset(["listId", "listName"]),  # Either listId OR listName
        parent_filter_field="listId",
        parent_id_type="ListId",
        parent_method_name="entries",
        # listEntries supports expansion by fetching the underlying entity
        supported_expansions=frozenset(["interactionDates", "unreplied"]),
    ),
    "interactions": EntitySchema(
        name="interactions",
        service_attr="interactions",
        id_field="id",
        filterable_fields=frozenset(
            [
                "id",
                "type",
                "subject",
                "createdAt",
                "happenedAt",
            ]
        ),
        computed_fields=frozenset([]),
        relationships={
            "persons": RelationshipDef(
                target_entity="persons",
                fetch_strategy="entity_method",
                method_or_service="get_associated_person_ids",
                requires_n_plus_1=True,
                display_fields=("firstName", "lastName"),
            ),
        },
        api_version="v1",
        fetch_strategy=FetchStrategy.RELATIONSHIP_ONLY,
    ),
    "notes": EntitySchema(
        name="notes",
        service_attr="notes",
        id_field="id",
        filterable_fields=frozenset(
            [
                "id",
                "content",
                "createdAt",
                "creatorId",
            ]
        ),
        computed_fields=frozenset([]),
        relationships={},
        api_version="v1",
        fetch_strategy=FetchStrategy.RELATIONSHIP_ONLY,
    ),
}


# Entities with unbounded record counts (FetchStrategy.GLOBAL, excluding "lists").
# Used by safety guards to require explicit --max-records for quantifier queries.
# Equivalent to:
#   {t for t, s in SCHEMA_REGISTRY.items()
#    if s.fetch_strategy == FetchStrategy.GLOBAL and t != "lists"}
UNBOUNDED_ENTITIES: frozenset[str] = frozenset({"persons", "companies", "opportunities"})


# =============================================================================
# Expansion Registry
# =============================================================================

EXPANSION_REGISTRY: dict[str, ExpansionDef] = {
    "interactionDates": ExpansionDef(
        name="interactionDates",
        supported_entities=frozenset(["persons", "companies"]),
        fetch_params={
            "with_interaction_dates": True,
            "with_interaction_persons": True,
        },
        requires_refetch=True,
    ),
    "unreplied": ExpansionDef(
        name="unreplied",
        # NOT listEntries - handled via _expand_list_entries() pattern
        supported_entities=frozenset(["persons", "companies", "opportunities"]),
        fetch_params={"check_unreplied": True},
        requires_refetch=False,  # Uses separate API call, not entity refetch
    ),
}


def get_entity_schema(entity_name: str) -> EntitySchema | None:
    """Get schema for an entity type.

    Args:
        entity_name: Entity type name (e.g., "persons")

    Returns:
        EntitySchema or None if not found
    """
    return SCHEMA_REGISTRY.get(entity_name)


def get_relationship(entity_name: str, relationship_name: str) -> RelationshipDef | None:
    """Get relationship definition.

    Args:
        entity_name: Source entity type (e.g., "persons")
        relationship_name: Relationship name (e.g., "companies")

    Returns:
        RelationshipDef or None if not found
    """
    schema = SCHEMA_REGISTRY.get(entity_name)
    if schema is None:
        return None
    return schema.relationships.get(relationship_name)


def is_valid_field_path(entity_name: str, path: str) -> bool:
    """Check if a field path is valid for an entity.

    Handles nested paths like "companies._count" or "fields.Status".

    Args:
        entity_name: Entity type name
        path: Field path to validate

    Returns:
        True if the path is valid
    """
    schema = SCHEMA_REGISTRY.get(entity_name)
    if schema is None:
        return False

    parts = path.split(".")

    # Simple field
    if len(parts) == 1:
        field_name = parts[0]
        return (
            field_name in schema.filterable_fields
            or field_name in schema.computed_fields
            or field_name == schema.id_field
            or field_name.startswith("fields.")  # List entry fields
        )

    # Relationship path (e.g., "companies._count")
    first_part = parts[0]
    if first_part in schema.relationships:
        remaining = ".".join(parts[1:])
        # _count is always valid for relationships
        if remaining == "_count":
            return True
        # Validate against target entity
        rel = schema.relationships[first_part]
        return is_valid_field_path(rel.target_entity, remaining)

    # fields.* for list entries
    return first_part == "fields"


def get_supported_entities() -> list[str]:
    """Get list of all supported entity types."""
    return list(SCHEMA_REGISTRY.keys())


def get_entity_relationships(entity_name: str) -> list[str]:
    """Get list of relationship names for an entity."""
    schema = SCHEMA_REGISTRY.get(entity_name)
    if schema is None:
        return []
    return list(schema.relationships.keys())


def find_relationship_by_target(schema: EntitySchema, target_entity: str) -> str | None:
    """Find relationship name by target entity type.

    Used by exists_ clause to map entity type (e.g., "interactions")
    to relationship name (e.g., "interactions").

    Args:
        schema: The entity schema containing relationship definitions
        target_entity: The target entity type to search for

    Returns:
        The relationship name if found, None otherwise.
        Returns first matching relationship if multiple exist.

    Example:
        # Schema: persons has relationship "companies" -> target_entity="companies"
        find_relationship_by_target(person_schema, "companies")  # Returns "companies"
        find_relationship_by_target(person_schema, "unknown")     # Returns None
    """
    for rel_name, rel_def in schema.relationships.items():
        if rel_def.target_entity == target_entity:
            return rel_name
    return None
