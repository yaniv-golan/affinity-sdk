"""The ``--filter`` text language as a query WHERE clause.

``--filter 'Status = "Won" & Amount > 5'`` and the JSON ``where`` of ``query`` are one filter
language written two ways; this turns the first into the second, so both go through the same
server-side split (:mod:`.pushdown`). The text filter still runs locally exactly as before.

This module is CLI-only and NOT part of the public SDK API.
"""

from __future__ import annotations

from ...compare import map_operator
from ...filters import (
    AndExpression,
    FieldComparison,
    FilterExpression,
    NotExpression,
    OrExpression,
    RawToken,
)
from .models import WhereClause

# Row-level keys a list entry filter sees besides its fields (they aren't list fields).
ROW_KEYS = frozenset(
    {"name", "domain", "primary_email", "listEntryId", "entityId", "entityName", "entityType"}
)


def where_from_filter(expr: FilterExpression) -> WhereClause:
    """The WHERE clause for a parsed ``--filter`` expression on list entries: field names
    become ``fields.<name>`` paths; row keys (``entityName``, ...) keep their own path."""
    if isinstance(expr, AndExpression):
        return WhereClause(and_=[where_from_filter(expr.left), where_from_filter(expr.right)])
    if isinstance(expr, OrExpression):
        return WhereClause(or_=[where_from_filter(expr.left), where_from_filter(expr.right)])
    if isinstance(expr, NotExpression):
        return WhereClause(not_=where_from_filter(expr.expr))
    if isinstance(expr, FieldComparison):
        name = expr.field_name
        path = name if name in ROW_KEYS else f"fields.{name}"
        if isinstance(expr.value, RawToken) and expr.value.token == "*":
            # `field = *` is "has a value"; `field != *` is "no value"
            op = "is_not_null" if expr.operator == "=" else "is_null"
            return WhereClause(path=path, op=op)
        return WhereClause(path=path, op=map_operator(expr.operator), value=expr.value)
    # Raw filter strings can't be analysed: keep them local
    return WhereClause(path=None, op=None)
