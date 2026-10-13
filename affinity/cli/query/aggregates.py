"""Aggregate functions for query results.

This module provides aggregation functions like sum, avg, count, etc.
It is CLI-only and NOT part of the public SDK API.
"""

from __future__ import annotations

import contextlib
import statistics
from collections import defaultdict
from typing import Any

from .exceptions import QueryValidationError
from .filters import resolve_field_path
from .models import AggregateFunc, HavingClause

# =============================================================================
# Aggregate Functions
# =============================================================================


def compute_sum(records: list[dict[str, Any]], field: str) -> float:
    """Compute sum of a field across records."""
    total = 0.0
    for record in records:
        value = resolve_field_path(record, field)
        if value is not None:
            with contextlib.suppress(ValueError, TypeError):
                total += float(value)
    return total


def compute_avg(records: list[dict[str, Any]], field: str) -> float | None:
    """Compute average of a field across records."""
    values: list[float] = []
    for record in records:
        value = resolve_field_path(record, field)
        if value is not None:
            with contextlib.suppress(ValueError, TypeError):
                values.append(float(value))

    if not values:
        return None
    return sum(values) / len(values)


def _comparable_values(records: list[dict[str, Any]], field: str, func: str) -> list[Any]:
    """The values of ``field`` that min/max compare: numbers when there are any, else text
    (ISO dates compare as text). Values of different kinds never compare with each other."""
    numbers: list[Any] = []
    texts: list[str] = []
    other = False
    for record in records:
        value = resolve_field_path(record, field)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            numbers.append(value)
        elif isinstance(value, str):
            texts.append(value)
        else:
            other = True
    if numbers:
        return numbers
    if texts:
        return texts
    if other:
        raise QueryValidationError(
            f"{func} needs numbers, dates or text, and '{field}' holds objects or lists",
            field="aggregate",
        )
    return []


def compute_min(records: list[dict[str, Any]], field: str) -> Any:
    """Compute minimum value of a field across records."""
    values = _comparable_values(records, field, "min")
    if not values:
        return None
    return min(values)


def compute_max(records: list[dict[str, Any]], field: str) -> Any:
    """Compute maximum value of a field across records."""
    values = _comparable_values(records, field, "max")
    if not values:
        return None
    return max(values)


def compute_count(records: list[dict[str, Any]], field: str | bool | None = None) -> int:
    """Compute count of records.

    Args:
        records: List of records
        field: If True or None, count all records.
               If a string, count records where field is not null.
    """
    if field is None or field is True:
        return len(records)

    if isinstance(field, str):
        count = 0
        for record in records:
            value = resolve_field_path(record, field)
            if value is not None:
                count += 1
        return count

    return len(records)


def compute_percentile(records: list[dict[str, Any]], field: str, p: int | float) -> float | None:
    """Compute percentile of a field across records.

    Args:
        records: List of records
        field: Field to compute percentile for
        p: Percentile value (0-100)

    Returns:
        The percentile value, or None if no valid values
    """
    values: list[float] = []
    for record in records:
        value = resolve_field_path(record, field)
        if value is not None:
            with contextlib.suppress(ValueError, TypeError):
                values.append(float(value))

    if not values:
        return None

    values.sort()

    # Handle edge cases
    if len(values) == 1:
        return values[0]
    if p <= 0:
        return values[0]  # P0 = min
    if p >= 100:
        return values[-1]  # P100 = max

    # statistics.quantiles(n=100) returns 99 cut points (indices 0-98 for P1-P99)
    quantile = p / 100.0
    idx = min(int(quantile * 99), 98)  # Clamp to valid range
    return statistics.quantiles(values, n=100)[idx]


def compute_first(records: list[dict[str, Any]], field: str) -> Any:
    """Get first non-null value of a field."""
    for record in records:
        value = resolve_field_path(record, field)
        if value is not None:
            return value
    return None


def compute_last(records: list[dict[str, Any]], field: str) -> Any:
    """Get last non-null value of a field."""
    for record in reversed(records):
        value = resolve_field_path(record, field)
        if value is not None:
            return value
    return None


# =============================================================================
# Expression Aggregates
# =============================================================================


def compute_expression(
    values: dict[str, Any],
    operation: str,
    operands: list[str | int | float],
) -> float | None:
    """Compute an expression aggregate.

    Args:
        values: Dict of computed aggregate values
        operation: One of "multiply", "divide", "add", "subtract"
        operands: List of aggregate names or literal numbers

    Returns:
        Computed value, or None if any operand is None
    """
    resolved: list[float] = []
    for operand in operands:
        if isinstance(operand, (int, float)):
            resolved.append(float(operand))
        elif isinstance(operand, str):
            value = values.get(operand)
            if value is None:
                return None
            try:
                resolved.append(float(value))
            except (ValueError, TypeError):
                return None

    if len(resolved) < 2:
        return None

    result = resolved[0]
    for val in resolved[1:]:
        if operation == "multiply":
            result *= val
        elif operation == "divide":
            if val == 0:
                return None
            result /= val
        elif operation == "add":
            result += val
        elif operation == "subtract":
            result -= val

    return result


# =============================================================================
# Main Aggregation Function
# =============================================================================


def compute_aggregates(
    records: list[dict[str, Any]],
    aggregates: dict[str, AggregateFunc],
) -> dict[str, Any]:
    """Compute all aggregates for a set of records.

    Args:
        records: List of records to aggregate
        aggregates: Dict of aggregate name -> AggregateFunc

    Returns:
        Dict of aggregate name -> computed value
    """
    results: dict[str, Any] = {}
    expression_aggs: list[tuple[str, str, list[str | int | float]]] = []

    # First pass: compute non-expression aggregates
    for name, agg_func in aggregates.items():
        if agg_func.sum is not None:
            results[name] = compute_sum(records, agg_func.sum)
        elif agg_func.avg is not None:
            results[name] = compute_avg(records, agg_func.avg)
        elif agg_func.min is not None:
            results[name] = compute_min(records, agg_func.min)
        elif agg_func.max is not None:
            results[name] = compute_max(records, agg_func.max)
        elif agg_func.count is not None:
            results[name] = compute_count(records, agg_func.count)
        elif agg_func.percentile is not None:
            field = agg_func.percentile.get("field", "")
            p = agg_func.percentile.get("p", 50)
            results[name] = compute_percentile(records, field, p)
        elif agg_func.first is not None:
            results[name] = compute_first(records, agg_func.first)
        elif agg_func.last is not None:
            results[name] = compute_last(records, agg_func.last)
        elif agg_func.multiply is not None:
            expression_aggs.append((name, "multiply", agg_func.multiply))
        elif agg_func.divide is not None:
            expression_aggs.append((name, "divide", agg_func.divide))
        elif agg_func.add is not None:
            expression_aggs.append((name, "add", agg_func.add))
        elif agg_func.subtract is not None:
            expression_aggs.append((name, "subtract", agg_func.subtract))

    # Second pass: compute expression aggregates
    for name, operation, operands in expression_aggs:
        results[name] = compute_expression(results, operation, operands)

    return results


def group_and_aggregate(
    records: list[dict[str, Any]],
    group_by: str,
    aggregates: dict[str, AggregateFunc],
) -> list[dict[str, Any]]:
    """Group records and compute aggregates for each group.

    Args:
        records: List of records to group
        group_by: Field to group by
        aggregates: Dict of aggregate name -> AggregateFunc

    Returns:
        List of result dicts, one per group, sorted with null values at end
    """
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    # Group keys made from objects (a location, a multi-select of objects) show the value
    objects: dict[Any, Any] = {}

    def make_hashable(value: Any) -> Any:
        """Convert unhashable types (lists, dicts) to hashable types (tuples).

        For multi-select fields, we sort the values so that different orderings
        (e.g., ["Team", "Market"] vs ["Market", "Team"]) are treated as the same group.
        """
        if isinstance(value, dict):
            return (
                "__object__",
                tuple(sorted((str(k), make_hashable(v)) for k, v in value.items())),
            )
        if isinstance(value, list):
            items = [make_hashable(v) for v in value]
            try:
                return tuple(sorted(items))
            except TypeError:
                # Mixed kinds or objects: a stable order that doesn't compare them
                return tuple(sorted(items, key=repr))
        return value

    for record in records:
        key = resolve_field_path(record, group_by)
        hashable_key = make_hashable(key)
        groups[hashable_key].append(record)
        if isinstance(key, dict) or (
            isinstance(key, list) and any(isinstance(v, (dict, list)) for v in key)
        ):
            objects.setdefault(hashable_key, key)

    results: list[dict[str, Any]] = []
    null_result: dict[str, Any] | None = None

    for key, group_records in groups.items():
        agg_values = compute_aggregates(group_records, aggregates)

        # Convert tuple back to list for display, use "(no value)" for null
        display_key: Any
        if key is None:
            display_key = "(no value)"
        elif key in objects:
            display_key = objects[key]
        elif isinstance(key, tuple):
            display_key = list(key)
        else:
            display_key = key
        result = {group_by: display_key, **agg_values}

        # Collect null group separately to append at end
        if key is None:
            null_result = result
        else:
            results.append(result)

    # Append null group at end if present
    if null_result is not None:
        results.append(null_result)

    return results


def apply_having(
    results: list[dict[str, Any]],
    having: HavingClause,
) -> list[dict[str, Any]]:
    """Apply HAVING clause to filter aggregated results.

    Args:
        results: List of aggregated result dicts
        having: HAVING clause to apply

    Returns:
        Filtered list of results
    """
    from .filters import matches

    # Convert HavingClause to WhereClause for filtering
    # (They have the same structure for simple conditions)
    from .models import WhereClause

    # Build a WhereClause from HavingClause
    if having.path is not None and having.op is not None:
        where = WhereClause(path=having.path, op=having.op, value=having.value)
    elif having.and_ is not None:
        where = WhereClause(
            and_=[
                WhereClause(path=h.path, op=h.op, value=h.value)
                for h in having.and_
                if h.path is not None
            ]
        )
    elif having.or_ is not None:
        where = WhereClause(
            or_=[
                WhereClause(path=h.path, op=h.op, value=h.value)
                for h in having.or_
                if h.path is not None
            ]
        )
    else:
        return results

    return [r for r in results if matches(r, where)]
