"""WHERE clauses: the filter language's structured form (``query``'s JSON ``where``; the
``--filter`` text converts to it). Shared by the SDK's server-side filtering and the CLI.

Private module: not part of the public SDK API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ExistsClause", "FilterCondition", "QuantifierClause", "WhereClause"]


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


WhereClause.model_rebuild()
