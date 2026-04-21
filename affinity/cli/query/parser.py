"""Query parser and validator.

Parses JSON queries into validated Query objects.
This module is CLI-only and NOT part of the public SDK API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .exceptions import QueryParseError, QueryValidationError
from .models import Query, WhereClause
from .schema import EXPANSION_REGISTRY, SCHEMA_REGISTRY, FetchStrategy

# =============================================================================
# Version Configuration
# =============================================================================

CURRENT_VERSION = "1.0"
SUPPORTED_VERSIONS = frozenset(["1.0"])
DEPRECATED_VERSIONS: frozenset[str] = frozenset()

# Supported operators per version
SUPPORTED_OPERATORS_V1 = frozenset(
    [
        "eq",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "contains",
        "starts_with",
        "in",
        "between",
        "is_null",
        "is_not_null",
        "contains_any",
        "contains_all",
        "has_any",
        "has_all",
    ]
)

# Supported entities
SUPPORTED_ENTITIES = frozenset(
    [
        "persons",
        "companies",
        "opportunities",
        "lists",
        "listEntries",
        "interactions",
        "notes",
    ]
)


# =============================================================================
# Parse Result
# =============================================================================


class ParseResult:
    """Result of parsing a query."""

    def __init__(self, query: Query, warnings: list[str]) -> None:
        self.query = query
        self.warnings = warnings

    @property
    def version(self) -> str:
        return self.query.version or CURRENT_VERSION


# =============================================================================
# Validation Functions
# =============================================================================


def validate_version(version: str | None) -> tuple[str, list[str]]:
    """Validate and normalize query version.

    Returns:
        Tuple of (resolved_version, warnings)

    Raises:
        QueryParseError: If version is not supported
    """
    warnings: list[str] = []

    if version is None:
        warnings.append(
            "Query missing '$version' field. Assuming version 1.0. "
            'Add \'"$version": "1.0"\' for forward compatibility.'
        )
        return CURRENT_VERSION, warnings

    if version not in SUPPORTED_VERSIONS and version not in DEPRECATED_VERSIONS:
        raise QueryParseError(
            f"Unsupported query version '{version}'. "
            f"Supported versions: {', '.join(sorted(SUPPORTED_VERSIONS))}"
        )

    if version in DEPRECATED_VERSIONS:
        warnings.append(
            f"Query version '{version}' is deprecated. "
            "Run 'xaffinity query migrate --file query.json' to upgrade."
        )

    return version, warnings


def validate_entity(entity: str) -> None:
    """Validate that entity type is supported."""
    if entity not in SUPPORTED_ENTITIES:
        raise QueryValidationError(
            f"Unknown entity type '{entity}'. "
            f"Supported entities: {', '.join(sorted(SUPPORTED_ENTITIES))}",
            field="from",
        )


def validate_operator(op: str, _version: str = CURRENT_VERSION) -> None:
    """Validate that operator is supported for the given version."""
    supported = SUPPORTED_OPERATORS_V1  # Currently only v1
    if op not in supported:
        raise QueryParseError(
            f"Unknown operator '{op}'. Supported operators: {', '.join(sorted(supported))}",
            field="op",
        )


def validate_where_clause(where: WhereClause, version: str = CURRENT_VERSION) -> None:
    """Recursively validate a WHERE clause."""
    # Check for single condition
    if where.op is not None:
        validate_operator(where.op, version)

        # Validate that path or expr is provided
        if where.path is None and where.expr is None:
            raise QueryValidationError(
                "Condition must have 'path' or 'expr' field",
                field="where",
            )

        # Validate value for operators that require it
        if where.op not in ("is_null", "is_not_null") and where.value is None:
            raise QueryValidationError(
                f"Operator '{where.op}' requires a 'value' field",
                field="where",
            )

        # Validate 'between' has two-element list
        if where.op == "between" and (not isinstance(where.value, list) or len(where.value) != 2):
            raise QueryValidationError(
                "'between' operator requires a two-element array [min, max]",
                field="where.value",
            )

        # Validate 'in' has a list
        if where.op == "in" and not isinstance(where.value, list):
            raise QueryValidationError(
                "'in' operator requires an array value",
                field="where.value",
            )

    # Validate compound conditions
    if where.and_ is not None:
        for clause in where.and_:
            validate_where_clause(clause, version)

    if where.or_ is not None:
        for clause in where.or_:
            validate_where_clause(clause, version)

    if where.not_ is not None:
        validate_where_clause(where.not_, version)

    # Validate quantifiers
    if where.all_ is not None:
        validate_where_clause(where.all_.where, version)

    if where.none_ is not None:
        validate_where_clause(where.none_.where, version)

    # Validate exists
    if where.exists_ is not None:
        if where.exists_.from_ not in SUPPORTED_ENTITIES:
            raise QueryValidationError(
                f"Unknown entity type '{where.exists_.from_}' in EXISTS clause",
                field="where.exists.from",
            )
        if where.exists_.where is not None:
            validate_where_clause(where.exists_.where, version)


# =============================================================================
# Entity Queryability Validation
# =============================================================================


def extract_filter_fields(where: WhereClause | None, *, inside_not: bool = False) -> set[str]:
    """Recursively extract all field paths from a where clause.

    Args:
        where: The where clause to extract from
        inside_not: True if we're inside a NOT clause (used to track negated filters)

    Returns:
        Set of field paths found in the where clause
    """
    if where is None:
        return set()

    fields: set[str] = set()

    # Direct condition
    if where.path and not inside_not:
        fields.add(where.path)

    # Compound conditions
    if where.and_:
        for clause in where.and_:
            fields.update(extract_filter_fields(clause, inside_not=inside_not))
    if where.or_:
        for clause in where.or_:
            fields.update(extract_filter_fields(clause, inside_not=inside_not))
    if where.not_:
        # Don't extract fields from inside NOT - negated filters don't satisfy requirements
        pass

    return fields


def extract_filter_operators(where: WhereClause | None, field: str) -> set[str]:
    """Extract all operators used for a specific field in where clause.

    NOTE: Does NOT traverse into NOT clauses - negated filters should be
    rejected by validate_entity_queryable, not validated for operators.
    """
    if where is None:
        return set()

    ops: set[str] = set()

    # Direct condition
    if where.path == field and where.op:
        ops.add(where.op)

    # Compound conditions
    if where.and_:
        for clause in where.and_:
            ops.update(extract_filter_operators(clause, field))
    if where.or_:
        for clause in where.or_:
            ops.update(extract_filter_operators(clause, field))
    # NOTE: "not" clauses are not traversed - negated required filters are invalid

    return ops


def _check_required_filter_in_not(
    where: WhereClause | None, required_fields: frozenset[str]
) -> bool:
    """Check if any required filter field appears inside a NOT clause.

    Returns True if a required filter is negated (which is invalid).
    """
    if where is None:
        return False

    # Check NOT clause for required fields
    if where.not_ and _where_contains_field(where.not_, required_fields):
        return True

    # Recurse into compound conditions
    if where.and_:
        for clause in where.and_:
            if _check_required_filter_in_not(clause, required_fields):
                return True
    if where.or_:
        for clause in where.or_:
            if _check_required_filter_in_not(clause, required_fields):
                return True

    return False


def _where_contains_field(where: WhereClause | None, fields: frozenset[str]) -> bool:
    """Check if a where clause contains any of the specified fields."""
    if where is None:
        return False

    if where.path and where.path in fields:
        return True

    if where.and_:
        for clause in where.and_:
            if _where_contains_field(clause, fields):
                return True
    if where.or_:
        for clause in where.or_:
            if _where_contains_field(clause, fields):
                return True
    return bool(where.not_ and _where_contains_field(where.not_, fields))


def _validate_or_branches_have_required_filter(
    where: WhereClause | None,
    required_fields: frozenset[str],
    parent_has_required: bool = False,
) -> bool:
    """Validate that all OR branches contain at least one required filter.

    Args:
        where: Where clause to validate
        required_fields: Set of required field names (e.g., {"listId", "listName"})
        parent_has_required: True if an ancestor AND clause already has the required filter

    Returns True if valid, False if any OR branch is missing required filter.

    Example valid structures:
    - AND [listName=X, OR[A, B]]  # OR branches covered by sibling listName in AND
    - OR [AND[listName=X, A], AND[listName=Y, B]]  # Each OR branch has its own
    """
    if where is None:
        return True

    # Check OR branches - each must have required filter UNLESS parent AND has it
    if where.or_ and not parent_has_required:
        for branch in where.or_:
            branch_fields = extract_filter_fields(branch)
            if not (branch_fields & required_fields):
                return False

    # For AND clauses: check if any sibling clause provides the required filter
    # If so, nested OR clauses within this AND don't need their own
    if where.and_:
        # Check if this AND has the required filter at its level
        and_has_required = parent_has_required
        for clause in where.and_:
            # Only check direct (non-compound) conditions at this level
            if clause.path and clause.path in required_fields:
                and_has_required = True
                break

        # Recurse into AND clauses with updated context
        for clause in where.and_:
            if not _validate_or_branches_have_required_filter(
                clause, required_fields, and_has_required
            ):
                return False

    # Recurse into OR clauses - each branch is independent
    if where.or_:
        for clause in where.or_:
            # Each OR branch provides its own context
            clause_fields = extract_filter_fields(clause)
            branch_has_required = parent_has_required or bool(clause_fields & required_fields)
            if not _validate_or_branches_have_required_filter(
                clause, required_fields, branch_has_required
            ):
                return False

    return True


def validate_entity_queryable(query: Query) -> None:
    """Validate that the entity can be queried directly.

    Checks:
    1. RELATIONSHIP_ONLY entities cannot be queried directly
    2. REQUIRES_PARENT entities must have required filter with valid operator
    3. Required filters cannot be negated (inside NOT clause)
    4. All OR branches must have the required filter

    Raises:
        QueryParseError: If entity cannot be queried as specified
    """
    schema = SCHEMA_REGISTRY.get(query.from_)
    if schema is None:
        raise QueryParseError(f"Unknown entity: '{query.from_}'")

    # Global entities with empty filterable_fields (companies/persons/opportunities)
    # accept NO server-side filters. Fail fast with a helpful hint.
    if not schema.filterable_fields and query.where is not None:
        singular = query.from_[:-1] if query.from_.endswith("s") else query.from_
        raise QueryValidationError(
            f"The {query.from_!r} endpoint does not support server-side filtering. "
            f"Previous versions silently returned unfiltered results. "
            f"For name/email fuzzy search, use: xaffinity {singular} ls --query TERM. "
            f"For list-specific filters, query listEntries with a listId filter instead.",
            field="where",
        )

    # Check if entity can be queried directly
    if schema.fetch_strategy == FetchStrategy.RELATIONSHIP_ONLY:
        raise QueryParseError(
            f"'{query.from_}' cannot be queried directly. "
            f"Use it as an 'include' on a parent entity instead. "
            f'Example: {{"from": "persons", "include": ["{query.from_}"]}}'
        )

    # Check required filters for REQUIRES_PARENT entities
    if schema.fetch_strategy == FetchStrategy.REQUIRES_PARENT:
        present = extract_filter_fields(query.where)
        # For listEntries, either listId OR listName is acceptable
        if not (present & schema.required_filters):
            # Show all alternatives in error message
            filter_options = sorted(schema.required_filters)
            if len(filter_options) > 1:
                filter_desc = " or ".join(f"'{f}'" for f in filter_options)
                # For listEntries, show both ID and name examples
                raise QueryParseError(
                    f"Query for '{query.from_}' requires a {filter_desc} filter. "
                    f"Examples:\n"
                    f'  By ID: {{"from": "{query.from_}", "where": '
                    f'{{"path": "listId", "op": "eq", "value": 12345}}}}\n'
                    f'  By name: {{"from": "{query.from_}", "where": '
                    f'{{"path": "listName", "op": "eq", "value": "My List"}}}}'
                )
            else:
                example_filter = filter_options[0]
                raise QueryParseError(
                    f"Query for '{query.from_}' requires a '{example_filter}' filter. "
                    f'Example: {{"from": "{query.from_}", "where": '
                    f'{{"path": "{example_filter}", "op": "eq", "value": 12345}}}}'
                )

        # Check for negated required filters
        if _check_required_filter_in_not(query.where, schema.required_filters):
            raise QueryParseError(
                f"Cannot negate required filter for '{query.from_}'. "
                f"A negated filter like NOT(listId=X) would match all other lists, "
                f"which is unbounded. Use a positive filter instead."
            )

        # Validate operators for required filters (must be eq or in)
        valid_ops = {"eq", "in"}
        for required_field in schema.required_filters:
            ops = extract_filter_operators(query.where, required_field)
            invalid_ops = ops - valid_ops
            if invalid_ops:
                raise QueryParseError(
                    f"Invalid operator '{next(iter(invalid_ops))}' for required filter "
                    f"'{required_field}'. Only 'eq' and 'in' operators are supported. "
                    f'Example: {{"path": "{required_field}", "op": "eq", "value": 12345}}'
                )

        # Validate all OR branches have required filter
        if not _validate_or_branches_have_required_filter(query.where, schema.required_filters):
            filter_options = sorted(schema.required_filters)
            filter_desc = " or ".join(f"'{f}'" for f in filter_options)
            raise QueryParseError(
                f"All OR branches must include a {filter_desc} filter. "
                f"Each branch of an OR condition must specify which parent to fetch from."
            )


def validate_query_semantics(query: Query) -> list[str]:
    """Validate semantic constraints on the query.

    Returns:
        List of warnings (non-fatal issues)

    Raises:
        QueryValidationError: For fatal semantic errors
    """
    warnings: list[str] = []

    # Aggregate with include is not allowed
    if query.aggregate is not None and query.include is not None:
        raise QueryValidationError(
            "Cannot use 'aggregate' with 'include'. "
            "Aggregates collapse records, making includes meaningless.",
            field="aggregate",
        )

    # groupBy requires aggregate
    if query.group_by is not None and query.aggregate is None:
        raise QueryValidationError(
            "'groupBy' requires 'aggregate' to be specified.",
            field="groupBy",
        )

    # having requires aggregate
    if query.having is not None and query.aggregate is None:
        raise QueryValidationError(
            "'having' requires 'aggregate' to be specified.",
            field="having",
        )

    # Validate include specifications
    # After normalization, query.include is dict[str, IncludeConfig]
    if query.include is not None:
        for rel_name, include_config in query.include.items():
            # Validate relationship name
            if not rel_name or not isinstance(rel_name, str):
                raise QueryValidationError(
                    f"Invalid include path: {rel_name!r}",
                    field="include",
                )
            # Validate display fields if specified
            if include_config.display is not None:
                for field_name in include_config.display:
                    if not field_name or not isinstance(field_name, str):
                        raise QueryValidationError(
                            f"Invalid display field in include.{rel_name}: {field_name!r}",
                            field=f"include.{rel_name}.display",
                        )

    # Validate select paths
    if query.select is not None:
        for select_path in query.select:
            if not select_path or not isinstance(select_path, str):
                raise QueryValidationError(
                    f"Invalid select path: {select_path!r}",
                    field="select",
                )

    # Validate limit
    if query.limit is not None:
        if query.limit < 0:
            raise QueryValidationError(
                "limit must be non-negative",
                field="limit",
            )
        if query.limit == 0:
            warnings.append("Query has limit=0, which will return no results.")

    # Validate expand paths
    if query.expand is not None:
        for expansion in query.expand:
            if not expansion or not isinstance(expansion, str):
                raise QueryValidationError(
                    f"Invalid expand path: {expansion!r}",
                    field="expand",
                )
            expansion_def = EXPANSION_REGISTRY.get(expansion)
            if expansion_def is None:
                raise QueryValidationError(
                    f"Unknown expansion: '{expansion}'. "
                    f"Available: {', '.join(EXPANSION_REGISTRY.keys())}",
                    field="expand",
                )
            # Check if entity supports this expansion (listEntries validated at runtime)
            if query.from_ != "listEntries" and query.from_ not in expansion_def.supported_entities:
                raise QueryValidationError(
                    f"Expansion '{expansion}' not supported for '{query.from_}'. "
                    f"Supported entities: {', '.join(expansion_def.supported_entities)}",
                    field="expand",
                )

        # Warn about N+1 API calls for large result sets
        if query.from_ == "listEntries" and (query.limit is None or query.limit > 100):
            warnings.append(
                f"expand: {query.expand} on listEntries requires fetching each "
                "entity individually (N+1 queries). Consider adding 'limit' for "
                "large lists, or use 'list export --expand interactions' for streaming output."
            )

    return warnings


# =============================================================================
# Main Parse Function
# =============================================================================


def parse_query(
    query_input: dict[str, Any] | str,
    *,
    version_override: str | None = None,
) -> ParseResult:
    """Parse and validate a query.

    Args:
        query_input: Either a dict (already parsed JSON) or a JSON string
        version_override: If provided, overrides $version in query

    Returns:
        ParseResult with validated Query and warnings

    Raises:
        QueryParseError: For syntax/parsing errors
        QueryValidationError: For semantic validation errors
    """
    warnings: list[str] = []

    # Parse JSON if string
    if isinstance(query_input, str):
        try:
            query_dict = json.loads(query_input)
        except json.JSONDecodeError as e:
            raise QueryParseError(f"Invalid JSON: {e}") from None
    else:
        query_dict = query_input

    if not isinstance(query_dict, dict):
        raise QueryParseError("Query must be a JSON object")

    # Handle version
    version = version_override or query_dict.get("$version")
    resolved_version, version_warnings = validate_version(version)
    warnings.extend(version_warnings)

    # Set version in query dict for Pydantic
    query_dict["$version"] = resolved_version

    # Validate entity type before Pydantic parsing
    if "from" not in query_dict:
        raise QueryParseError("Query must have a 'from' field specifying the entity type")
    validate_entity(query_dict["from"])

    # Parse with Pydantic
    try:
        query = Query.model_validate(query_dict)
    except ValidationError as e:
        # Convert Pydantic errors to QueryParseError
        errors = e.errors()
        if len(errors) == 1:
            err = errors[0]
            field_path = ".".join(str(loc) for loc in err["loc"])
            raise QueryParseError(err["msg"], field=field_path) from None
        else:
            error_msgs = [
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors
            ]
            raise QueryParseError("Multiple validation errors:\n" + "\n".join(error_msgs)) from None

    # Validate WHERE clause operators
    if query.where is not None:
        validate_where_clause(query.where, resolved_version)

    # Validate entity queryability (RELATIONSHIP_ONLY, REQUIRES_PARENT checks)
    validate_entity_queryable(query)

    # Validate semantic constraints
    semantic_warnings = validate_query_semantics(query)
    warnings.extend(semantic_warnings)

    return ParseResult(query, warnings)


def parse_query_from_file(
    filepath: str | Path, *, version_override: str | None = None
) -> ParseResult:
    """Parse a query from a file.

    Args:
        filepath: Path to JSON file
        version_override: If provided, overrides $version in query

    Returns:
        ParseResult with validated Query and warnings

    Raises:
        QueryParseError: For file read or parsing errors
    """
    path = Path(filepath) if isinstance(filepath, str) else filepath
    try:
        content = path.read_text()
    except OSError as e:
        raise QueryParseError(f"Failed to read query file: {e}") from None

    return parse_query(content, version_override=version_override)
