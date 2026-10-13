"""Pydantic models for the query language.

These models define the structure of structured queries. They are CLI-only
and NOT part of the public SDK API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from ..results import ResultSummary

# =============================================================================
# Filter Condition Models
# =============================================================================


class FilterCondition(BaseModel):
    """A single filter condition.

    Examples:
        {"path": "email", "op": "contains", "value": "@acme.com"}
        {"path": "createdAt", "op": "gt", "value": "-30d"}
        {"path": "companies._count", "op": "gte", "value": 2}
    """

    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    expr: dict[str, Any] | None = None
    op: Literal[
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
        "starts_word",
    ]
    value: Any = None


class QuantifierClause(BaseModel):
    """Quantifier clause for 'all' or 'none' predicates.

    Examples:
        {"all": {"path": "interactions", "where": {"path": "type", "op": "eq", "value": "MEETING"}}}
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    where: WhereClause


class ExistsClause(BaseModel):
    """EXISTS subquery clause.

    Examples:
        {"exists": {"from": "interactions", "via": "personId", "where": {...}}}
    """

    model_config = ConfigDict(extra="forbid")

    from_: str = Field(..., alias="from")
    via: str | None = None
    where: WhereClause | None = None


class WhereClause(BaseModel):
    """WHERE clause supporting compound conditions.

    Can be:
    - A single condition: {"path": "x", "op": "eq", "value": "y"}
    - Compound: {"and": [...]} or {"or": [...]}
    - Negation: {"not": {...}}
    - Quantifiers: {"all": {...}} or {"none": {...}}
    - Existence: {"exists": {...}}
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Single condition fields
    path: str | None = None
    expr: dict[str, Any] | None = None
    op: str | None = None
    value: Any = None

    # Compound conditions
    and_: list[WhereClause] | None = Field(None, alias="and")
    or_: list[WhereClause] | None = Field(None, alias="or")
    not_: WhereClause | None = Field(None, alias="not")

    # Quantifiers
    all_: QuantifierClause | None = Field(None, alias="all")
    none_: QuantifierClause | None = Field(None, alias="none")

    # Existence
    exists_: ExistsClause | None = Field(None, alias="exists")


# =============================================================================
# Aggregate Models
# =============================================================================


class AggregateFunc(BaseModel):
    """Aggregate function definition.

    Examples:
        {"sum": "amount"}
        {"count": true}
        {"avg": "score"}
        {"percentile": {"field": "amount", "p": 90}}
        {"multiply": ["count", "avgAmount"]}
    """

    model_config = ConfigDict(extra="forbid")

    # Simple aggregates
    sum: str | None = None
    avg: str | None = None
    min: str | None = None
    max: str | None = None
    count: bool | str | None = None  # True for count(*), str for count(field)

    # Advanced aggregates
    percentile: dict[str, Any] | None = None  # {"field": "x", "p": 90}
    first: str | None = None
    last: str | None = None

    # Expression aggregates (operate on other aggregates)
    multiply: list[str | int | float] | None = None
    divide: list[str | int | float] | None = None
    add: list[str | int | float] | None = None
    subtract: list[str | int | float] | None = None


class HavingClause(BaseModel):
    """HAVING clause for filtering aggregated results.

    Examples:
        {"path": "totalAmount", "op": "gt", "value": 1000}
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str | None = None
    op: str | None = None
    value: Any = None

    and_: list[HavingClause] | None = Field(None, alias="and")
    or_: list[HavingClause] | None = Field(None, alias="or")


# =============================================================================
# Order By Models
# =============================================================================


class OrderByClause(BaseModel):
    """ORDER BY clause.

    Examples:
        {"field": "lastName", "direction": "asc"}
        {"field": "createdAt", "direction": "desc"}
        {"expr": {"daysUntil": "dueDate"}, "direction": "asc"}
    """

    model_config = ConfigDict(extra="forbid")

    field: str | None = None
    expr: dict[str, Any] | None = None
    direction: Literal["asc", "desc"] = "asc"


# =============================================================================
# Subquery Models
# =============================================================================


class SubqueryDef(BaseModel):
    """Named subquery definition.

    Examples:
        {
            "recentDeals": {
                "from": "opportunities",
                "where": {"path": "closedAt", "op": "gt", "value": "-90d"}
            }
        }
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(..., alias="from")
    where: WhereClause | None = None
    select: list[str] | None = None
    limit: int | None = None


# =============================================================================
# Main Query Model
# =============================================================================


class IncludeConfig(BaseModel):
    """Configuration for an included relationship.

    Supports extended syntax for customizing display fields and parameters:
        {"companies": {"display": ["name", "domain"]}}
        {"interactions": {"limit": 50, "days": 180}}
        {"opportunities": {"list": "Pipeline"}}
        {"persons": {"where": {"path": "name", "op": "contains", "value": "Smith"}}}

    Attributes:
        display: List of field names to use for display value.
            Fields are tried in order; first non-empty value is used.
            If None, uses default priority: name → firstName lastName → title → email → id
        limit: Max records to fetch (default 100 for interactions)
        days: Lookback window in days (default 90 for interactions)
        where: Filter to apply to included entities (--expand-filter parity)
        list: Scope to specific opportunity list name or ID (--expand-opportunities-list parity)
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    display: list[str] | None = None
    limit: int | None = None
    days: int | None = None
    where: WhereClause | None = None
    list_: str | int | None = Field(None, alias="list")


class Query(BaseModel):
    """The main query model.

    This is the top-level structure for a structured query.

    Examples:
        {
            "$version": "1.0",
            "from": "persons",
            "where": {"path": "email", "op": "contains", "value": "@acme.com"},
            "include": ["companies"],
            "orderBy": [{"field": "lastName", "direction": "asc"}],
            "limit": 50
        }

        # Extended syntax with display fields:
        {
            "from": "persons",
            "include": {"companies": {"display": ["name", "domain"]}}
        }
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Version (optional but recommended)
    version: str | None = Field(None, alias="$version")

    # Required: entity to query
    from_: str = Field(..., alias="from")

    # Optional: field selection
    select: list[str] | None = None

    # Optional: filtering
    where: WhereClause | None = None

    # Optional: includes (relationships to fetch)
    # Supports both simple and extended syntax:
    #   Simple: ["companies", "opportunities"]
    #   Extended: {"companies": {"display": ["name", "domain"]}}
    #   Mixed: ["companies", {"opportunities": {"display": ["name"]}}]
    # Normalized to dict[str, IncludeConfig] after validation
    include: dict[str, IncludeConfig] | None = None

    # Optional: expansions (computed data to add to records)
    # Unlike include (which fetches separate entities into result.included),
    # expand adds computed data directly to each record in result.data
    expand: list[str] | None = None

    # Optional: named subqueries
    subqueries: dict[str, SubqueryDef] | None = None

    # Optional: ordering
    order_by: list[OrderByClause] | None = Field(None, alias="orderBy")

    # Optional: grouping and aggregation
    group_by: str | None = Field(None, alias="groupBy")
    aggregate: dict[str, AggregateFunc] | None = None
    having: HavingClause | None = None

    # Optional: pagination
    limit: int | None = None
    cursor: str | None = None

    @field_validator("include", mode="before")
    @classmethod
    def normalize_include(cls, v: Any) -> dict[str, dict[str, Any]] | None:
        """Normalize include field to dict[str, IncludeConfig] form.

        Supports multiple input formats:
          - Simple list: ["companies", "opportunities"]
          - Extended dict: {"companies": {"display": ["name", "domain"]}}
          - Mixed list: ["companies", {"opportunities": {"display": ["name"]}}]

        All formats are normalized to: {"companies": {...}, "opportunities": {...}}
        """
        if v is None:
            return None

        result: dict[str, dict[str, Any]] = {}

        if isinstance(v, list):
            # List format: mix of strings and dicts
            for item in v:
                if isinstance(item, str):
                    # Simple string: "companies" → {"companies": {}}
                    result[item] = {}
                elif isinstance(item, dict):
                    # Dict item: {"companies": {"display": [...]}}
                    for rel_name, config in item.items():
                        if isinstance(config, dict):
                            result[rel_name] = config
                        else:
                            # Invalid config, let pydantic handle validation error
                            result[rel_name] = {"_invalid": config}
                else:
                    # Invalid item type, let pydantic handle validation error
                    continue

        elif isinstance(v, dict):
            # Dict format: {"companies": {"display": [...]}, "opportunities": {}}
            for rel_name, config in v.items():
                if config is None:
                    # {"companies": null} → {"companies": {}}
                    result[rel_name] = {}
                elif isinstance(config, dict):
                    result[rel_name] = config
                else:
                    # Invalid config, let pydantic handle validation error
                    result[rel_name] = {"_invalid": config}

        else:
            # Invalid type - return as-is and let pydantic fail validation
            return v  # type: ignore[no-any-return]

        return result


# =============================================================================
# Execution Plan Models (used by planner and executor)
# =============================================================================


@dataclass
class PlanStep:
    """A single step in the execution plan."""

    step_id: int
    operation: Literal[
        "fetch",
        "fetch_streaming",
        "filter",
        "include",
        "expand",
        "aggregate",
        "sort",
        "limit",
        "exists_check",
        "count_relationship",
    ]
    description: str
    entity: str | None = None
    relationship: str | None = None
    estimated_api_calls: int = 0
    estimated_records: int | None = None
    is_client_side: bool = False
    warnings: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)

    # Additional metadata for specific operations
    filter_pushdown: bool = False
    pushdown_filter: str | None = None
    client_filter: WhereClause | None = None

    # Expansion name when operation="expand"
    expansion: str | None = None


@dataclass
class ExecutionPlan:
    """Complete execution plan for a query."""

    query: Query
    steps: list[PlanStep]
    total_api_calls: int | str  # Can be "UNBOUNDED" for unbounded quantifier queries
    estimated_records_fetched: int | None
    estimated_memory_mb: float | None
    warnings: list[str]
    recommendations: list[str]
    has_expensive_operations: bool
    requires_full_scan: bool
    version: str = "1.0"
    requires_explicit_max_records: bool = False  # True for unbounded quantifier queries


@dataclass
class QueryResult:
    """Result of query execution."""

    data: list[dict[str, Any]]
    included: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Per-parent mapping: {rel_name: {parent_id: [related_records]}}
    # Enables inline expansion in table output (correlating included data to parent records)
    included_by_parent: dict[str, dict[int, list[dict[str, Any]]]] = field(default_factory=dict)
    # Display configs from query.include (used for inline expansion)
    # Maps relationship name to IncludeConfig for custom display fields
    include_configs: dict[str, IncludeConfig] = field(default_factory=dict)
    # Source entity name (e.g., "persons") for schema lookup in inline expansion
    source_entity: str | None = None
    summary: ResultSummary | None = None  # Standardized result summary
    meta: dict[str, Any] = field(default_factory=dict)  # Additional metadata (executionTime, etc.)
    pagination: dict[str, Any] | None = None
    rate_limit: Any | None = None  # RateLimitSnapshot from client
    warnings: list[str] = field(default_factory=list)
    # Track explicit selections for display formatting (field flattening)
    explicit_select: list[str] | None = None  # e.g., ["id", "fields.*", "fields.Status"]
    explicit_expand: list[str] | None = None  # e.g., ["interactionDates"]
    # API cursor for streaming mode resumption (captured when pagination stops)
    api_cursor: str | None = None


# Enable forward references
WhereClause.model_rebuild()
HavingClause.model_rebuild()
