"""Shared comparison logic for filter matching.

Used by both:
- SDK filter (affinity/filters.py) for client-side filtering of API responses
- Query tool (affinity/cli/query/filters.py) for in-memory query filtering

This module is the single source of truth for comparison operations.
"""

from __future__ import annotations

import re
from typing import Any


def normalize_value(value: Any) -> Any:
    """Normalize a value for comparison.

    Handles Affinity API response formats:
    - Dropdowns become their text: {"text": "Active"} -> "Active"
    - Persons become their name: {"firstName": "Jane", "lastName": "Doe"} -> "Jane Doe"
    - Companies become their name: {"name": "Acme", "domain": ...} -> "Acme"
    - Multi-value arrays become lists of those: [{"text": "A"}, {"text": "B"}] -> ["A", "B"]
    - Scalars pass through unchanged

    These are the values ``query`` filters on too, so a filter reads the same everywhere.
    """
    if value is None:
        return None
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return [_normalize_item(item) for item in value]
        return value
    if isinstance(value, dict):
        return _normalize_item(value)
    return value


def _normalize_item(item: dict[str, Any]) -> Any:
    if "text" in item:
        return item["text"]
    if "firstName" in item or "lastName" in item:
        parts = [
            p.strip() for p in (item.get("firstName"), item.get("lastName")) if isinstance(p, str)
        ]
        return " ".join(p for p in parts if p) or None
    if "name" in item:
        return item["name"]
    return item


def compare_values(
    field_value: Any,
    target: Any,
    operator: str,
) -> bool:
    """Compare field value against target using the specified operator.

    This is the core comparison function used by both SDK filter and Query tool.

    Handles:
    - Scalar comparisons (string, number)
    - Array membership (multi-select dropdown fields)
    - Set equality for array-to-array comparisons

    Operators:
    - eq: equality or array membership
    - neq: not equal or not in array
    - contains: substring match (case-insensitive)
    - starts_with: prefix match (case-insensitive)
    - ends_with: suffix match (case-insensitive)
    - starts_word: a word starts with the text (case-insensitive), as Affinity's name search
    - gt, gte, lt, lte: numeric/date comparisons
    - in: value in list of allowed values
    - between: value in range [low, high] (inclusive)
    - has_any: array has any of target values (exact match)
    - has_all: array has all of target values (exact match)
    - contains_any: any element contains any substring (case-insensitive)
    - contains_all: any element contains all substrings (case-insensitive)
    - is_null: value is None or empty string
    - is_not_null: value is not None and not empty string
    - is_empty: value is empty (empty string or empty array)

    Args:
        field_value: The value from the entity/record being filtered
        target: The target value to compare against
        operator: The comparison operator name

    Returns:
        True if the comparison passes, False otherwise

    Raises:
        ValueError: If the operator is not recognized
    """
    # Dispatch to specific comparison function
    if operator == "eq":
        return _eq(field_value, target)
    elif operator == "neq":
        return _neq(field_value, target)
    elif operator == "contains":
        return _contains(field_value, target)
    elif operator == "starts_with":
        return _starts_with(field_value, target)
    elif operator == "ends_with":
        return _ends_with(field_value, target)
    elif operator == "starts_word":
        return _starts_word(field_value, target)
    elif operator == "gt":
        return _gt(field_value, target)
    elif operator == "gte":
        return _gte(field_value, target)
    elif operator == "lt":
        return _lt(field_value, target)
    elif operator == "lte":
        return _lte(field_value, target)
    elif operator == "in":
        return _in(field_value, target)
    elif operator == "between":
        return _between(field_value, target)
    elif operator == "has_any":
        return _has_any(field_value, target)
    elif operator == "has_all":
        return _has_all(field_value, target)
    elif operator == "contains_any":
        return _contains_any(field_value, target)
    elif operator == "contains_all":
        return _contains_all(field_value, target)
    elif operator == "is_null":
        return _is_null(field_value, target)
    elif operator == "is_not_null":
        return _is_not_null(field_value, target)
    elif operator == "is_empty":
        return _is_empty(field_value, target)
    else:
        raise ValueError(
            f"Unknown comparison operator: '{operator}'. "
            f"Valid operators: eq, neq, contains, starts_with, ends_with, starts_word, "
            f"gt, gte, lt, lte, in, between, has_any, has_all, "
            f"contains_any, contains_all, is_null, is_not_null, is_empty"
        )


# =============================================================================
# Comparison Functions
# =============================================================================


def _eq(a: Any, b: Any) -> bool:
    """Equality operator with array membership support.

    For scalar fields: standard equality (a == b), with string coercion fallback
    For array fields (multi-select dropdowns):
      - eq with scalar: checks if scalar is IN the array (membership)
      - eq with array: checks set equality (order-insensitive, same elements)
    """
    if a is None:
        return b is None

    # If field value is a list, check if filter value is IN the list
    if isinstance(a, list):
        # If comparing list to list, check set equality (order-insensitive)
        if isinstance(b, list):
            try:
                return set(a) == set(b)
            except TypeError:
                # Unhashable elements - fall back to sorted comparison
                try:
                    return sorted(a) == sorted(b)
                except TypeError:
                    return a == b  # Last resort: order-sensitive equality
        return b in a

    # Try direct comparison first
    if a == b:
        return True

    # Handle boolean coercion: filter string "true"/"false" should match Python bool
    # Filter parser outputs lowercase "true"/"false", Python str(True) is "True"
    if isinstance(a, bool) and isinstance(b, str):
        return (a is True and b.lower() == "true") or (a is False and b.lower() == "false")
    if isinstance(b, bool) and isinstance(a, str):
        return (b is True and a.lower() == "true") or (b is False and a.lower() == "false")

    # Fall back to string comparison for type mismatches (e.g., int 5 vs string "5")
    # This is important for SDK filter where parsed values are always strings
    return str(a) == str(b)


def _neq(a: Any, b: Any) -> bool:
    """Not equal operator with array membership support."""
    if a is None:
        return b is not None

    if isinstance(a, list):
        if isinstance(b, list):
            try:
                return set(a) != set(b)
            except TypeError:
                try:
                    return sorted(a) != sorted(b)
                except TypeError:
                    return a != b
        return b not in a

    # Use _eq for consistency (handles string coercion)
    return not _eq(a, b)


def _contains(a: Any, b: Any) -> bool:
    """Contains operator (case-insensitive substring match).

    For scalar targets: substring match
    For list targets (V2 API collection syntax): checks if field array contains ALL elements
      - `tags =~ [A, B]` means "tags array contains both A and B" (has_all semantics)

    For array fields with scalar target: checks if ANY element contains the substring.
    """
    if a is None or b is None:
        return False

    # V2 API collection syntax: =~ [a, b] means "contains all elements"
    # This is the official Affinity V2 API behavior for collection contains
    if isinstance(b, list):
        if not isinstance(a, list):
            return False
        # Check if field array contains ALL elements from filter list
        return all(elem in a for elem in b)

    # Handle array fields - check if any element contains the substring
    if isinstance(a, list):
        b_lower = str(b).lower()
        return any(b_lower in str(elem).lower() for elem in a)

    return str(b).lower() in str(a).lower()


def _starts_with(a: Any, b: Any) -> bool:
    """Starts with operator (case-insensitive prefix match).

    For array fields: checks if ANY element starts with the prefix.
    """
    if a is None or b is None:
        return False

    # Handle array fields
    if isinstance(a, list):
        b_lower = str(b).lower()
        return any(str(elem).lower().startswith(b_lower) for elem in a)

    return str(a).lower().startswith(str(b).lower())


def _ends_with(a: Any, b: Any) -> bool:
    """Ends with operator (case-insensitive suffix match).

    For array fields: checks if ANY element ends with the suffix.
    """
    if a is None or b is None:
        return False

    # Handle array fields
    if isinstance(a, list):
        b_lower = str(b).lower()
        return any(str(elem).lower().endswith(b_lower) for elem in a)

    return str(a).lower().endswith(str(b).lower())


def _starts_word(a: Any, b: Any) -> bool:
    """Starts-word operator: the text appears at the start of a word (case-insensitive), the
    way Affinity's search matches names: "Jan" matches "Jane Doe" and "Mary-Jane", "ane"
    doesn't. For array fields: any element."""
    if a is None or b is None or str(b) == "":
        return False
    pattern = re.compile(r"(?<!\w)" + re.escape(str(b)), re.IGNORECASE)
    if isinstance(a, list):
        return any(pattern.search(str(elem)) is not None for elem in a)
    return pattern.search(str(a)) is not None


def _safe_compare(a: Any, b: Any, op: Any) -> bool:
    """Safely compare values, handling None and type mismatches.

    For numeric comparisons, attempts to coerce string values to numbers.
    This is important for SDK filter where parsed values are always strings.
    """
    if a is None or b is None:
        return False

    # Try numeric coercion first (for SDK filter where parsed values are strings)
    try:
        # If both can be converted to numbers, compare as numbers
        a_num = float(a) if isinstance(a, str) else a
        b_num = float(b) if isinstance(b, str) else b
        return bool(op(a_num, b_num))
    except (TypeError, ValueError):
        pass

    try:
        return bool(op(a, b))
    except TypeError:
        # Type mismatch - try string comparison
        return bool(op(str(a), str(b)))


def _gt(a: Any, b: Any) -> bool:
    """Greater than operator."""
    return _safe_compare(a, b, lambda x, y: x > y)


def _gte(a: Any, b: Any) -> bool:
    """Greater than or equal operator."""
    return _safe_compare(a, b, lambda x, y: x >= y)


def _lt(a: Any, b: Any) -> bool:
    """Less than operator."""
    return _safe_compare(a, b, lambda x, y: x < y)


def _lte(a: Any, b: Any) -> bool:
    """Less than or equal operator."""
    return _safe_compare(a, b, lambda x, y: x <= y)


def _in(a: Any, b: Any) -> bool:
    """In operator - checks if value(s) exist in filter list.

    For scalar fields: checks if field value is in filter list
    For array fields: checks if ANY element of field array is in filter list
    """
    if a is None:
        return False
    if not isinstance(b, list):
        return False

    # If a is a list, check if ANY element of a is in b
    if isinstance(a, list):
        return any(item in b for item in a)

    return a in b


def _between(a: Any, b: Any) -> bool:
    """Between operator (inclusive range).

    Target must be a list of exactly 2 elements: [low, high].
    Handles string-to-number coercion for parsed filter values.
    """
    if a is None or not isinstance(b, list) or len(b) != 2:
        return False

    low, high = b[0], b[1]

    # Try numeric coercion first (filter values are often parsed as strings)
    try:
        a_num = float(a) if isinstance(a, str) else a
        low_num = float(low) if isinstance(low, str) else low
        high_num = float(high) if isinstance(high, str) else high
        return bool(low_num <= a_num <= high_num)
    except (TypeError, ValueError):
        pass

    # Fall back to direct comparison
    try:
        return bool(low <= a <= high)
    except TypeError:
        return False


def _has_any(a: Any, b: Any) -> bool:
    """Check if array field contains ANY of the specified elements.

    Unlike contains_any (which does case-insensitive substring matching),
    this does exact array membership checking.
    """
    if not isinstance(a, list) or not isinstance(b, list):
        return False
    if not b:  # Empty filter list = no match
        return False
    return any(elem in a for elem in b)


def _has_all(a: Any, b: Any) -> bool:
    """Check if array field contains ALL of the specified elements.

    Unlike contains_all (which does case-insensitive substring matching),
    this does exact array membership checking.
    """
    if not isinstance(a, list) or not isinstance(b, list):
        return False
    if not b:  # Empty filter list = no match (avoid vacuous truth)
        return False
    return all(elem in a for elem in b)


def _contains_any(a: Any, b: Any) -> bool:
    """Contains any of the given terms (case-insensitive substring match).

    For array fields: checks across all elements.
    """
    if a is None or not isinstance(b, list):
        return False

    # Handle array fields - check if any element contains any term
    if isinstance(a, list):
        for elem in a:
            elem_lower = str(elem).lower()
            if any(str(term).lower() in elem_lower for term in b):
                return True
        return False

    a_lower = str(a).lower()
    return any(str(term).lower() in a_lower for term in b)


def _contains_all(a: Any, b: Any) -> bool:
    """Contains all of the given terms (case-insensitive substring match).

    For scalar fields: all terms must be found in the single value.
    For array fields: all terms must be found (can be across different elements).
    """
    if a is None or not isinstance(b, list):
        return False

    # Handle array fields - all terms must be found somewhere in the elements
    if isinstance(a, list):
        for term in b:
            term_lower = str(term).lower()
            found = any(term_lower in str(elem).lower() for elem in a)
            if not found:
                return False
        return True

    a_lower = str(a).lower()
    return all(str(term).lower() in a_lower for term in b)


def _is_null(a: Any, _b: Any) -> bool:
    """Is null operator: no value. ``None``, an empty string and an empty list (a multi-value
    field with no items) all count as empty, as in Affinity's search."""
    return a is None or a == "" or (isinstance(a, list) and not a)


def _is_not_null(a: Any, _b: Any) -> bool:
    """Is not null operator: the opposite of :func:`_is_null`."""
    return not _is_null(a, _b)


def _is_empty(a: Any, _b: Any) -> bool:
    """Is empty operator.

    Checks if value is:
    - None
    - Empty string ""
    - Empty array []
    """
    if a is None:
        return True
    if a == "":
        return True
    return isinstance(a, list) and len(a) == 0


# =============================================================================
# Operator Name Mapping
# =============================================================================

# Map from SDK filter symbols/aliases to canonical operator names
SDK_OPERATOR_MAP: dict[str, str] = {
    # Official V2 API symbolic operators
    "=": "eq",
    "!=": "neq",
    "=~": "contains",
    "=^": "starts_with",
    "=$": "ends_with",
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
    # Word-based aliases (SDK extensions for LLM/human clarity)
    "contains": "contains",
    "starts_with": "starts_with",
    "ends_with": "ends_with",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    # Null/empty check aliases
    "is null": "is_null",
    "is not null": "is_not_null",
    "is empty": "is_empty",
    # Collection operators (SDK extensions)
    "in": "in",
    "between": "between",
    "has_any": "has_any",
    "has_all": "has_all",
    "contains_any": "contains_any",
    "contains_all": "contains_all",
    "starts_word": "starts_word",
}


def map_operator(sdk_operator: str) -> str:
    """Map an SDK filter operator symbol to the canonical operator name.

    Args:
        sdk_operator: The operator as used in SDK filter strings (e.g., "=~", "contains")

    Returns:
        The canonical operator name (e.g., "contains")

    Raises:
        ValueError: If the operator is not recognized
    """
    canonical = SDK_OPERATOR_MAP.get(sdk_operator)
    if canonical is None:
        valid_ops = ", ".join(sorted(set(SDK_OPERATOR_MAP.keys())))
        raise ValueError(f"Unknown operator: '{sdk_operator}'. Valid operators: {valid_ops}")
    return canonical
