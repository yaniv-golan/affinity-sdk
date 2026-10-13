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


def where_from_filter(expr: FilterExpression, row_keys: frozenset[str] = ROW_KEYS) -> WhereClause:
    """The WHERE clause for a parsed ``--filter`` expression: field names become
    ``fields.<name>`` paths; ``row_keys`` (attributes such as ``entityName``) keep their own
    path. The default keys are a list entry's."""
    if isinstance(expr, AndExpression):
        return WhereClause(
            and_=[where_from_filter(expr.left, row_keys), where_from_filter(expr.right, row_keys)]
        )
    if isinstance(expr, OrExpression):
        return WhereClause(
            or_=[where_from_filter(expr.left, row_keys), where_from_filter(expr.right, row_keys)]
        )
    if isinstance(expr, NotExpression):
        return WhereClause(not_=where_from_filter(expr.expr, row_keys))
    if isinstance(expr, FieldComparison):
        name = expr.field_name
        path = name if name in row_keys else f"fields.{name}"
        if isinstance(expr.value, RawToken) and expr.value.token == "*":
            # `field = *` is "has a value"; `field != *` is "no value"
            op = "is_not_null" if expr.operator == "=" else "is_null"
            return WhereClause(path=path, op=op)
        return WhereClause(path=path, op=map_operator(expr.operator), value=expr.value)
    # Raw filter strings can't be analysed: keep them local
    return WhereClause(path=None, op=None)
