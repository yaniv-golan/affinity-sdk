"""Filter operators for query WHERE clauses.

This module provides extended filter operators beyond what the SDK supports.
It is CLI-only and NOT part of the public SDK API.

Uses the shared compare module (affinity/compare.py) for comparison logic,
ensuring consistent behavior between SDK filter and Query tool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from ...compare import compare_values
from .dates import parse_date_value
from .exceptions import QueryValidationError
from .models import ExistsClause, QuantifierClause, WhereClause

if TYPE_CHECKING:
    from .schema import EntitySchema


# =============================================================================
# Filter Classification (for lazy loading optimization)
# =============================================================================


class FilterClass(Enum):
    """Classification of filter cost for lazy loading optimization."""

    CHEAP = "cheap"  # No API calls (local field comparison)
    EXPENSIVE = "expensive"  # Requires relationship data (N+1 calls)


def classify_filter(where: WhereClause, entity_type: str) -> FilterClass:
    """Classify a filter by cost.

    Args:
        where: The filter clause to classify
        entity_type: The entity being queried (needed for schema lookup)

    Returns:
        FilterClass.CHEAP for local comparisons, EXPENSIVE for relationship data
    """
    from .schema import SCHEMA_REGISTRY

    # Quantifiers are always expensive
    if where.all_ is not None or where.none_ is not None or where.exists_ is not None:
        return FilterClass.EXPENSIVE

    # _count pseudo-field requires relationship data
    # Use .endswith() to avoid matching fields like "my_count_field"
    if where.path and where.path.endswith("._count"):
        return FilterClass.EXPENSIVE

    # Check if path traverses a relationship (NOT just has a dot!)
    # "fields.Status" is CHEAP (list entry field)
    # "persons.name" is EXPENSIVE (traverses relationship)
    if where.path and "." in where.path:
        first_segment = where.path.split(".")[0]
        schema = SCHEMA_REGISTRY.get(entity_type)
        if schema is None:
            # Unknown entity - shouldn't happen (query validation catches this),
            # but CHEAP is safer than EXPENSIVE (avoids invalid API calls)
            return FilterClass.CHEAP
        if first_segment in (schema.relationships or {}):
            return FilterClass.EXPENSIVE
        # Not a relationship - it's a nested field like "fields.Status"

    # Check compound clauses recursively
    if where.and_ is not None:
        for clause in where.and_:
            if classify_filter(clause, entity_type) == FilterClass.EXPENSIVE:
                return FilterClass.EXPENSIVE
    if where.or_ is not None:
        for clause in where.or_:
            if classify_filter(clause, entity_type) == FilterClass.EXPENSIVE:
                return FilterClass.EXPENSIVE
    if where.not_ is not None and classify_filter(where.not_, entity_type) == FilterClass.EXPENSIVE:
        return FilterClass.EXPENSIVE

    return FilterClass.CHEAP


def partition_where(
    where: WhereClause | None, entity_type: str
) -> tuple[WhereClause | None, WhereClause | None]:
    """Split WHERE into (cheap_filter, expensive_filter).

    Cheap filter runs first to reduce dataset before N+1 calls.

    Args:
        where: The WHERE clause to partition
        entity_type: The entity being queried (for schema lookup)

    Returns:
        (cheap, expensive) tuple where either can be None
    """
    if where is None:
        return (None, None)

    # AND clauses can be partitioned
    if where.and_ is not None:
        cheap_parts = [
            c for c in where.and_ if classify_filter(c, entity_type) == FilterClass.CHEAP
        ]
        expensive_parts = [
            c for c in where.and_ if classify_filter(c, entity_type) == FilterClass.EXPENSIVE
        ]

        cheap = WhereClause(and_=cheap_parts) if cheap_parts else None
        expensive = WhereClause(and_=expensive_parts) if expensive_parts else None
        return (cheap, expensive)

    # OR clauses cannot be partitioned (all branches must evaluate)
    if where.or_ is not None:
        return (None, where)

    # Single filter
    cls = classify_filter(where, entity_type)
    if cls == FilterClass.CHEAP:
        return (where, None)
    return (None, where)


def extract_single_id_lookup(where: WhereClause | None) -> int | None:
    """Extract ID value if filter is a simple single-ID lookup.

    Detects patterns like:
        {"path": "id", "op": "eq", "value": 123}

    This enables optimization: instead of scanning all pages with client-side
    filtering, we can use service.get(id) directly (1 API call).

    Args:
        where: The WHERE clause to analyze

    Returns:
        The ID value (int) if this is a single-ID lookup, None otherwise.

    Note:
        Only detects exact match on "id" field with "eq" operator.
        Does NOT match:
        - Multiple conditions (AND/OR)
        - Negation (NOT)
        - Other operators (neq, in, etc.)
        - Other fields (organizationIds, etc.)
    """
    if where is None:
        return None

    # Must be a simple condition (no AND/OR/NOT/quantifiers)
    if where.and_ is not None or where.or_ is not None or where.not_ is not None:
        return None
    if where.all_ is not None or where.none_ is not None or where.exists_ is not None:
        return None

    # Must be path="id" with op="eq"
    if where.path != "id" or where.op != "eq":
        return None

    # Value must be an integer (ID)
    if not isinstance(where.value, int):
        return None

    return where.value


def extract_parent_and_id_lookup(
    where: WhereClause | None, parent_field: str
) -> tuple[int, int] | None:
    """Extract parent ID and entity ID from a compound filter.

    Detects patterns like (for listEntries with parent_field="listId"):
        {"and": [
            {"path": "listId", "op": "eq", "value": 123},
            {"path": "id", "op": "eq", "value": 456}
        ]}

    This enables single-ID lookup optimization for REQUIRES_PARENT entities.

    Args:
        where: The WHERE clause to analyze
        parent_field: The field name for the parent ID (e.g., "listId")

    Returns:
        Tuple of (parent_id, entity_id) if pattern matches, None otherwise.

    Note:
        Only detects exact AND of two equality conditions.
        Does NOT match nested ANDs, ORs, negation, or additional conditions.
    """
    if where is None:
        return None

    # Must be an AND with exactly 2 conditions
    if where.and_ is None or len(where.and_) != 2:
        return None

    # No other compound operators allowed at this level
    if where.or_ is not None or where.not_ is not None:
        return None
    if where.all_ is not None or where.none_ is not None or where.exists_ is not None:
        return None

    parent_id: int | None = None
    entity_id: int | None = None

    for clause in where.and_:
        # Each clause must be a simple equality condition
        if clause.and_ is not None or clause.or_ is not None or clause.not_ is not None:
            return None
        if clause.all_ is not None or clause.none_ is not None or clause.exists_ is not None:
            return None
        if clause.op != "eq":
            return None
        if not isinstance(clause.value, int):
            return None

        if clause.path == parent_field:
            parent_id = clause.value
        elif clause.path == "id":
            entity_id = clause.value
        else:
            # Extra condition that's not parent or id - not a match
            return None

    if parent_id is not None and entity_id is not None:
        return (parent_id, entity_id)

    return None


logger = logging.getLogger(__name__)

# =============================================================================
# Operator Definitions
# =============================================================================

# Type alias for operator functions
OperatorFunc = Callable[[Any, Any], bool]


def _make_operator(op_name: str) -> OperatorFunc:
    """Create an operator function that delegates to compare_values().

    This is a factory function that creates operator functions for the OPERATORS
    registry. Each function wraps compare_values() with the appropriate operator name.
    """

    def op_func(field_value: Any, target: Any) -> bool:
        return compare_values(field_value, target, op_name)

    return op_func


# Operator registry - all operators delegate to compare_values() from the shared module
# This ensures consistent comparison behavior between SDK filter and Query tool
OPERATORS: dict[str, OperatorFunc] = {
    "eq": _make_operator("eq"),
    "neq": _make_operator("neq"),
    "gt": _make_operator("gt"),
    "gte": _make_operator("gte"),
    "lt": _make_operator("lt"),
    "lte": _make_operator("lte"),
    "contains": _make_operator("contains"),
    "starts_with": _make_operator("starts_with"),
    "ends_with": _make_operator("ends_with"),  # New: was missing in query tool
    "in": _make_operator("in"),
    "between": _make_operator("between"),
    "is_null": _make_operator("is_null"),
    "is_not_null": _make_operator("is_not_null"),
    "is_empty": _make_operator("is_empty"),  # New: was missing in query tool
    "contains_any": _make_operator("contains_any"),
    "contains_all": _make_operator("contains_all"),
    "has_any": _make_operator("has_any"),
    "has_all": _make_operator("has_all"),
    "starts_word": _make_operator("starts_word"),
}


# =============================================================================
# Field Path Resolution
# =============================================================================


def resolve_field_path(record: dict[str, Any], path: str) -> Any:
    """Resolve a field path to a value.

    Supports:
    - Simple fields: "firstName"
    - Nested fields: "address.city"
    - Array fields: "emails[0]"
    - Special fields: "fields.Status" for list entry fields

    Args:
        record: The record to extract value from
        path: The field path

    Returns:
        The resolved value, or None if not found
    """
    if not path:
        return None

    parts = _parse_field_path(path)
    current: Any = record

    for part in parts:
        if current is None:
            return None

        if isinstance(part, int):
            # Array index
            if isinstance(current, list) and 0 <= part < len(current):
                current = current[part]
            else:
                return None
        elif isinstance(current, dict):
            # Object property
            current = current.get(part)
        else:
            return None

    return current


def _parse_field_path(path: str) -> list[str | int]:
    """Parse a field path into parts.

    Examples:
        "firstName" -> ["firstName"]
        "address.city" -> ["address", "city"]
        "emails[0]" -> ["emails", 0]
        "fields.Status" -> ["fields", "Status"]
    """
    parts: list[str | int] = []
    current = ""
    i = 0

    while i < len(path):
        char = path[i]

        if char == ".":
            if current:
                parts.append(current)
                current = ""
            i += 1
        elif char == "[":
            if current:
                parts.append(current)
                current = ""
            # Find closing bracket
            end = path.find("]", i)
            if end == -1:
                raise QueryValidationError(f"Unclosed bracket in field path: {path}")
            index_str = path[i + 1 : end]
            try:
                parts.append(int(index_str))
            except ValueError:
                # Non-numeric index, treat as string
                parts.append(index_str)
            i = end + 1
        else:
            current += char
            i += 1

    if current:
        parts.append(current)

    return parts


# =============================================================================
# Filter Compilation
# =============================================================================


def compile_filter(where: WhereClause) -> Callable[[dict[str, Any]], bool]:
    """Compile a WHERE clause into a filter function.

    Args:
        where: The WHERE clause to compile

    Returns:
        A function that takes a record and returns True if it matches
    """
    # Single condition
    if where.op is not None:
        return _compile_condition(where)

    # Compound conditions
    if where.and_ is not None:
        filters = [compile_filter(clause) for clause in where.and_]
        return lambda record: all(f(record) for f in filters)

    if where.or_ is not None:
        filters = [compile_filter(clause) for clause in where.or_]
        return lambda record: any(f(record) for f in filters)

    if where.not_ is not None:
        inner = compile_filter(where.not_)
        return lambda record: not inner(record)

    # Quantifiers require relationship data - use compile_filter_with_context() instead
    # Note: 'all_' is the Python attribute name; JSON uses 'all' (alias)
    if where.all_ is not None:
        raise NotImplementedError(
            "The 'all_' quantifier requires relationship data. "
            "Use compile_filter_with_context() with pre-fetched data."
        )
    if where.none_ is not None:
        raise NotImplementedError(
            "The 'none_' quantifier requires relationship data. "
            "Use compile_filter_with_context() with pre-fetched data."
        )

    # exists_ requires relationship data - use compile_filter_with_context() instead
    if where.exists_ is not None:
        raise NotImplementedError(
            "The 'exists_' subquery requires relationship data. "
            "Use compile_filter_with_context() with pre-fetched data."
        )

    # No conditions - match all
    return lambda _: True


def _compile_condition(where: WhereClause) -> Callable[[dict[str, Any]], bool]:
    """Compile a single filter condition."""
    if where.op is None:
        return lambda _: True

    op_func = OPERATORS.get(where.op)
    if op_func is None:
        raise QueryValidationError(f"Unknown operator: {where.op}")

    path = where.path
    value = where.value

    # Parse date values if they look like relative dates
    if value is not None and isinstance(value, str):
        parsed_value = parse_date_value(value)
        if parsed_value is not None:
            value = parsed_value

    # Handle _count pseudo-field - not implemented
    # _count requires relationship data - use compile_filter_with_context() instead
    if path and path.endswith("._count"):
        raise NotImplementedError(
            f"The '_count' pseudo-field ({path}) requires relationship data. "
            "Use compile_filter_with_context() with pre-fetched data."
        )

    def filter_func(record: dict[str, Any]) -> bool:
        if path is None:
            return True
        field_value = resolve_field_path(record, path)
        return op_func(field_value, value)

    return filter_func


def matches(record: dict[str, Any], where: WhereClause | None) -> bool:
    """Check if a record matches a WHERE clause.

    Args:
        record: The record to check
        where: The WHERE clause, or None (matches all)

    Returns:
        True if the record matches
    """
    if where is None:
        return True
    filter_func = compile_filter(where)
    return filter_func(record)


# =============================================================================
# Enhanced Filter Context (for quantifiers, exists, _count)
# =============================================================================


@dataclass
class FilterContext:
    """Context available during record filtering with relationship data.

    Used by compile_filter_with_context() to provide access to pre-fetched
    relationship data needed for quantifier, exists, and _count operations.
    """

    # Structure: {rel_name: {record_id: [related_records]}}
    relationship_data: dict[str, dict[int, list[dict[str, Any]]]]
    # Structure: {rel_name: {record_id: count}} - matches ExecutionContext field
    relationship_counts: dict[str, dict[int, int]]
    schema: EntitySchema  # Needed for exists_ relationship mapping
    id_field: str = "id"


def requires_relationship_data(where: WhereClause | None) -> set[str]:
    """Detect if a WHERE clause requires relationship data for filtering.

    Scans the WHERE clause for:
    - all_/none_ quantifiers (return relationship name from path)
    - exists_ clauses (return entity type from from_)
    - _count pseudo-field (return relationship name)

    Args:
        where: The WHERE clause to analyze

    Returns:
        Set of relationship names or entity types that need pre-fetching.
        Empty set if no relationship data is needed.
    """
    if where is None:
        return set()

    required: set[str] = set()

    # Check quantifiers
    if where.all_ is not None:
        required.add(where.all_.path)
    if where.none_ is not None:
        required.add(where.none_.path)

    # Check exists
    if where.exists_ is not None:
        required.add(where.exists_.from_)

    # Check _count pseudo-field (only single-level supported)
    if where.path and where.path.endswith("._count"):
        base_path = where.path.rsplit("._count", 1)[0]
        # Reject malformed paths (e.g., just "_count" with no relationship)
        if not base_path:
            raise QueryValidationError(
                f"Invalid _count path: {where.path}. "
                "Must specify a relationship, e.g., 'companies._count'."
            )
        # Reject nested _count paths (e.g., "companies.tags._count")
        if "." in base_path:
            raise QueryValidationError(
                f"Nested _count paths not supported: {where.path}. "
                "Only single-level counts like 'companies._count' are allowed."
            )
        required.add(base_path)

    # Recurse into compound clauses
    if where.and_ is not None:
        for clause in where.and_:
            required.update(requires_relationship_data(clause))
    if where.or_ is not None:
        for clause in where.or_:
            required.update(requires_relationship_data(clause))
    if where.not_ is not None:
        required.update(requires_relationship_data(where.not_))

    return required


def _check_no_nested_quantifiers(where: WhereClause, context: str) -> None:
    """Validate that where clause contains no nested quantifiers.

    Current limitation: Nested quantifiers would require multi-level relationship
    pre-fetching and context switching, which is not supported.

    Args:
        where: The inner where clause to check
        context: Description for error message (e.g., "all_ quantifier")

    Raises:
        QueryValidationError: If nested quantifiers are detected
    """
    if where.all_ is not None:
        raise QueryValidationError(
            f"Nested quantifiers not supported: {context} contains nested all_. "
            "Only one level of quantifier nesting is allowed."
        )
    if where.none_ is not None:
        raise QueryValidationError(
            f"Nested quantifiers not supported: {context} contains nested none_. "
            "Only one level of quantifier nesting is allowed."
        )
    if where.exists_ is not None:
        raise QueryValidationError(
            f"Nested quantifiers not supported: {context} contains nested exists_. "
            "Only one level of quantifier nesting is allowed."
        )

    # Also check compound clauses
    if where.and_:
        for clause in where.and_:
            _check_no_nested_quantifiers(clause, context)
    if where.or_:
        for clause in where.or_:
            _check_no_nested_quantifiers(clause, context)
    if where.not_:
        _check_no_nested_quantifiers(where.not_, context)


def compile_filter_with_context(
    where: WhereClause,
    ctx: FilterContext,
) -> Callable[[dict[str, Any]], bool]:
    """Compile WHERE clause with access to relationship data.

    This is the enhanced version that supports quantifiers, exists, and _count.

    Current Limitation: Does NOT support nested quantifiers (all_/none_/exists_ inside
    another all_/none_/exists_). This would require multi-level relationship
    pre-fetching which is complex. Raises QueryValidationError if detected.
    """
    # Handle quantifiers (with nesting validation)
    if where.all_ is not None:
        _check_no_nested_quantifiers(where.all_.where, "all_")
        return _compile_all_quantifier(where.all_, ctx)

    if where.none_ is not None:
        _check_no_nested_quantifiers(where.none_.where, "none_")
        return _compile_none_quantifier(where.none_, ctx)

    if where.exists_ is not None:
        if where.exists_.where:
            _check_no_nested_quantifiers(where.exists_.where, "exists_")
        return _compile_exists(where.exists_, ctx)

    # Handle _count pseudo-field
    if where.path and where.path.endswith("._count"):
        return _compile_count_condition(where.path, where.op, where.value, ctx)

    # Handle compound clauses (recurse)
    if where.and_ is not None:
        filters = [compile_filter_with_context(c, ctx) for c in where.and_]
        return lambda record: all(f(record) for f in filters)

    if where.or_ is not None:
        filters = [compile_filter_with_context(c, ctx) for c in where.or_]
        return lambda record: any(f(record) for f in filters)

    if where.not_ is not None:
        inner = compile_filter_with_context(where.not_, ctx)
        return lambda record: not inner(record)

    # Simple condition - delegate to existing compile_filter
    return compile_filter(where)


def _compile_all_quantifier(
    clause: QuantifierClause,
    ctx: FilterContext,
) -> Callable[[dict[str, Any]], bool]:
    """Compile all_ quantifier: ALL related items must match.

    Semantics:
    - If no related items exist, returns True (vacuous truth)
    - If any related item fails the condition, returns False

    Note: Uses compile_filter (not compile_filter_with_context) for inner clause
    because nested quantifiers are disallowed in the current implementation.
    """
    # Use simple compile_filter - nested quantifiers are validated/rejected earlier
    inner_filter = compile_filter(clause.where)
    rel_name = clause.path

    def all_match(record: dict[str, Any]) -> bool:
        record_id = record.get(ctx.id_field)
        if record_id is None:
            return True  # Can't check, pass through

        # Access pattern: {rel_name: {record_id: [records]}}
        related = ctx.relationship_data.get(rel_name, {}).get(record_id, [])

        # Vacuous truth: if no items, condition is satisfied
        if not related:
            return True

        return all(inner_filter(item) for item in related)

    return all_match


def _compile_none_quantifier(
    clause: QuantifierClause,
    ctx: FilterContext,
) -> Callable[[dict[str, Any]], bool]:
    """Compile none_ quantifier: NO related items may match.

    Semantics:
    - If no related items exist, returns True
    - If any related item matches the condition, returns False

    Note: Uses compile_filter (not compile_filter_with_context) for inner clause
    because nested quantifiers are disallowed in the current implementation.
    """
    inner_filter = compile_filter(clause.where)
    rel_name = clause.path

    def none_match(record: dict[str, Any]) -> bool:
        record_id = record.get(ctx.id_field)
        if record_id is None:
            return True  # Can't check, pass through

        related = ctx.relationship_data.get(rel_name, {}).get(record_id, [])

        # If no items, condition is satisfied
        if not related:
            return True

        # Return True only if NO items match the inner condition
        return not any(inner_filter(item) for item in related)

    return none_match


def _compile_exists(
    clause: ExistsClause,
    ctx: FilterContext,
) -> Callable[[dict[str, Any]], bool]:
    """Compile exists_ clause: at least one related item exists (optionally matching).

    Semantics:
    - If where is None, just check for any related items
    - If where is provided, check that at least one matches

    CRITICAL: Must map entity type (clause.from_) to relationship name.

    Note: Uses compile_filter (not compile_filter_with_context) for inner clause
    because nested quantifiers are disallowed in the current implementation.
    """
    # Import here to avoid circular import
    from .schema import find_relationship_by_target

    # Handle unsupported 'via' field (per Finding #13 and #32)
    if clause.via is not None:
        logger.warning(
            f"ExistsClause.via field is not implemented and will be ignored. "
            f"Provided value: {clause.via}"
        )

    # Map entity type to relationship name
    rel_name = find_relationship_by_target(ctx.schema, clause.from_)
    if rel_name is None:
        raise QueryValidationError(
            f"No relationship to entity '{clause.from_}' found. "
            f"Available relationships: {list(ctx.schema.relationships.keys())}"
        )

    # Compile inner filter if provided
    inner_filter: Callable[[dict[str, Any]], bool] | None = None
    if clause.where is not None:
        inner_filter = compile_filter(clause.where)

    def exists(record: dict[str, Any]) -> bool:
        record_id = record.get(ctx.id_field)
        if record_id is None:
            return False

        related = ctx.relationship_data.get(rel_name, {}).get(record_id, [])

        # No related items means exists is False
        if not related:
            return False

        # If no inner filter, just check for existence
        if inner_filter is None:
            return True

        # Check if any related item matches
        return any(inner_filter(item) for item in related)

    return exists


def _compile_count_condition(
    path: str,
    op: str | None,
    value: Any,
    ctx: FilterContext,
) -> Callable[[dict[str, Any]], bool]:
    """Compile _count pseudo-field condition.

    Example: {"path": "companies._count", "op": "gte", "value": 2}
    """
    base_path = path.rsplit("._count", 1)[0]  # "companies" from "companies._count"
    op_func = OPERATORS.get(op) if op else None

    if op_func is None:
        raise QueryValidationError(f"Unknown operator for _count: {op}")

    # Validate value is numeric (count comparisons require int/float)
    if not isinstance(value, (int, float)):
        raise QueryValidationError(
            f"_count comparison requires numeric value, got {type(value).__name__}: {value}"
        )

    def count_matches(record: dict[str, Any]) -> bool:
        record_id = record.get(ctx.id_field)
        if record_id is None:
            return False

        # Access pattern: {rel_name: {record_id: count}}
        count = ctx.relationship_counts.get(base_path, {}).get(record_id, 0)
        return op_func(count, value)

    return count_matches
