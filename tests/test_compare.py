"""Tests for the shared comparison module (affinity/compare.py).

This module is the single source of truth for comparison operations used by both
SDK filter and Query tool.
"""

from __future__ import annotations

import pytest

from affinity.compare import (
    SDK_OPERATOR_MAP,
    compare_values,
    map_operator,
    normalize_value,
)

# =============================================================================
# normalize_value() tests
# =============================================================================


class TestNormalizeValue:
    """Tests for value normalization."""

    def test_none_passthrough(self) -> None:
        """None passes through unchanged."""
        assert normalize_value(None) is None

    def test_scalar_passthrough(self) -> None:
        """Scalar values pass through unchanged."""
        assert normalize_value("hello") == "hello"
        assert normalize_value(42) == 42
        assert normalize_value(3.14) == 3.14
        assert normalize_value(True) is True

    def test_dropdown_dict_extracts_text(self) -> None:
        """Dropdown dict extracts 'text' field."""
        assert normalize_value({"text": "Active", "id": 123}) == "Active"
        assert normalize_value({"text": "Status"}) == "Status"

    def test_multi_select_array_extracts_text(self) -> None:
        """Multi-select array extracts 'text' from each dict."""
        result = normalize_value([{"text": "A"}, {"text": "B"}, {"text": "C"}])
        assert result == ["A", "B", "C"]

    def test_plain_array_passthrough(self) -> None:
        """Plain arrays (no dict elements) pass through unchanged."""
        assert normalize_value(["A", "B", "C"]) == ["A", "B", "C"]
        assert normalize_value([1, 2, 3]) == [1, 2, 3]

    def test_empty_array_passthrough(self) -> None:
        """Empty arrays pass through unchanged."""
        assert normalize_value([]) == []

    def test_dict_without_text_passthrough(self) -> None:
        """A dict that isn't a dropdown, person or company passes through unchanged."""
        value = {"city": "Tel Aviv", "country": "Israel"}
        assert normalize_value(value) == value

    def test_persons_and_companies_become_names(self) -> None:
        """Person and company values compare by name, as in query."""
        assert normalize_value({"firstName": "Jane", "lastName": "Doe", "id": 1}) == "Jane Doe"
        assert normalize_value({"name": "Acme", "domain": "acme.com", "id": 7}) == "Acme"
        assert normalize_value([{"firstName": "Jane", "lastName": None}, {"name": "Acme"}]) == [
            "Jane",
            "Acme",
        ]


# =============================================================================
# compare_values() - equality operators
# =============================================================================


class TestEqualityOperators:
    """Tests for eq and neq operators."""

    def test_eq_scalar_match(self) -> None:
        """Scalar equality matching."""
        assert compare_values("hello", "hello", "eq") is True
        assert compare_values("hello", "world", "eq") is False

    def test_eq_numeric(self) -> None:
        """Numeric equality."""
        assert compare_values(42, 42, "eq") is True
        assert compare_values(42, 43, "eq") is False

    def test_eq_boolean_coercion(self) -> None:
        """Boolean coercion: string 'true'/'false' matches Python bool."""
        # Filter parser outputs lowercase "true"/"false"
        assert compare_values(True, "true", "eq") is True
        assert compare_values(True, "TRUE", "eq") is True  # Case insensitive
        assert compare_values(True, "True", "eq") is True
        assert compare_values(True, "false", "eq") is False

        assert compare_values(False, "false", "eq") is True
        assert compare_values(False, "FALSE", "eq") is True
        assert compare_values(False, "False", "eq") is True
        assert compare_values(False, "true", "eq") is False

        # Reverse direction (string compared to bool)
        assert compare_values("true", True, "eq") is True
        assert compare_values("false", False, "eq") is True

    def test_eq_none_handling(self) -> None:
        """None equality handling."""
        assert compare_values(None, None, "eq") is True
        assert compare_values(None, "value", "eq") is False
        assert compare_values("value", None, "eq") is False

    @pytest.mark.req("SDK-FILT-007")
    def test_eq_array_membership(self) -> None:
        """eq on array field checks membership."""
        assert compare_values(["A", "B", "C"], "B", "eq") is True
        assert compare_values(["A", "B", "C"], "D", "eq") is False

    @pytest.mark.req("SDK-FILT-007")
    def test_eq_array_to_array_set_equality(self) -> None:
        """eq with array to array checks set equality (order-insensitive)."""
        assert compare_values(["A", "B"], ["B", "A"], "eq") is True
        assert compare_values(["A", "B"], ["A", "B"], "eq") is True
        assert compare_values(["A", "B"], ["A", "C"], "eq") is False

    def test_neq_scalar(self) -> None:
        """Scalar not-equal."""
        assert compare_values("hello", "world", "neq") is True
        assert compare_values("hello", "hello", "neq") is False

    def test_neq_none_handling(self) -> None:
        """None not-equal handling."""
        assert compare_values(None, "value", "neq") is True
        assert compare_values("value", None, "neq") is True
        assert compare_values(None, None, "neq") is False

    @pytest.mark.req("SDK-FILT-007")
    def test_neq_array_not_in(self) -> None:
        """neq on array field checks value NOT in array."""
        assert compare_values(["A", "B", "C"], "D", "neq") is True
        assert compare_values(["A", "B", "C"], "B", "neq") is False

    @pytest.mark.req("SDK-FILT-007")
    def test_neq_array_to_array_set_inequality(self) -> None:
        """neq with array to array checks set inequality."""
        assert compare_values(["A", "B"], ["A", "C"], "neq") is True
        assert compare_values(["A", "B"], ["B", "A"], "neq") is False


# =============================================================================
# compare_values() - string operators
# =============================================================================


class TestStringOperators:
    """Tests for contains, starts_with, ends_with operators."""

    def test_contains_substring(self) -> None:
        """Contains checks for substring."""
        assert compare_values("hello world", "world", "contains") is True
        assert compare_values("hello world", "foo", "contains") is False

    def test_contains_case_insensitive(self) -> None:
        """Contains is case-insensitive."""
        assert compare_values("Hello World", "WORLD", "contains") is True
        assert compare_values("HELLO", "hello", "contains") is True

    def test_contains_none_handling(self) -> None:
        """Contains with None returns False."""
        assert compare_values(None, "test", "contains") is False
        assert compare_values("test", None, "contains") is False

    @pytest.mark.req("SDK-FILT-007")
    def test_contains_array_any_element(self) -> None:
        """Contains on array checks if any element contains substring."""
        assert compare_values(["technology", "finance"], "tech", "contains") is True
        assert compare_values(["hello", "world"], "foo", "contains") is False

    def test_starts_with(self) -> None:
        """Starts with prefix match."""
        assert compare_values("hello world", "hello", "starts_with") is True
        assert compare_values("hello world", "world", "starts_with") is False

    def test_starts_with_case_insensitive(self) -> None:
        """Starts with is case-insensitive."""
        assert compare_values("Hello World", "HELLO", "starts_with") is True

    def test_starts_with_array(self) -> None:
        """Starts with on array checks any element."""
        assert compare_values(["hello", "world"], "hel", "starts_with") is True
        assert compare_values(["hello", "world"], "wor", "starts_with") is True
        assert compare_values(["hello", "world"], "foo", "starts_with") is False

    def test_ends_with(self) -> None:
        """Ends with suffix match."""
        assert compare_values("hello world", "world", "ends_with") is True
        assert compare_values("hello world", "hello", "ends_with") is False

    def test_ends_with_case_insensitive(self) -> None:
        """Ends with is case-insensitive."""
        assert compare_values("Hello World", "WORLD", "ends_with") is True

    def test_ends_with_array(self) -> None:
        """Ends with on array checks any element."""
        assert compare_values(["hello", "world"], "llo", "ends_with") is True
        assert compare_values(["hello", "world"], "rld", "ends_with") is True
        assert compare_values(["hello", "world"], "foo", "ends_with") is False


# =============================================================================
# compare_values() - numeric operators
# =============================================================================


class TestNumericOperators:
    """Tests for gt, gte, lt, lte operators."""

    def test_gt(self) -> None:
        """Greater than."""
        assert compare_values(10, 5, "gt") is True
        assert compare_values(5, 5, "gt") is False
        assert compare_values(5, 10, "gt") is False

    def test_gte(self) -> None:
        """Greater than or equal."""
        assert compare_values(10, 5, "gte") is True
        assert compare_values(5, 5, "gte") is True
        assert compare_values(5, 10, "gte") is False

    def test_lt(self) -> None:
        """Less than."""
        assert compare_values(5, 10, "lt") is True
        assert compare_values(5, 5, "lt") is False
        assert compare_values(10, 5, "lt") is False

    def test_lte(self) -> None:
        """Less than or equal."""
        assert compare_values(5, 10, "lte") is True
        assert compare_values(5, 5, "lte") is True
        assert compare_values(10, 5, "lte") is False

    def test_numeric_none_handling(self) -> None:
        """Numeric operators with None return False."""
        assert compare_values(None, 5, "gt") is False
        assert compare_values(5, None, "gt") is False

    def test_numeric_string_coercion(self) -> None:
        """Numeric operators coerce strings to numbers for comparison."""
        # String "5" is coerced to 5, "10" to 10, so 5 > 10 is False
        assert compare_values("5", "10", "gt") is False
        assert compare_values("10", "5", "gt") is True
        # Mixed types also work
        assert compare_values(10, "5", "gt") is True
        assert compare_values("10", 5, "gt") is True

    def test_numeric_string_fallback_non_numeric(self) -> None:
        """Non-numeric strings fall back to string comparison."""
        # "banana" > "apple" is True (lexicographic)
        assert compare_values("banana", "apple", "gt") is True
        # "apple" > "banana" is False (lexicographic)
        assert compare_values("apple", "banana", "gt") is False


# =============================================================================
# compare_values() - collection operators
# =============================================================================


class TestCollectionOperators:
    """Tests for in, between, has_any, has_all operators."""

    def test_in_scalar(self) -> None:
        """In operator with scalar field."""
        assert compare_values("A", ["A", "B", "C"], "in") is True
        assert compare_values("D", ["A", "B", "C"], "in") is False

    def test_in_array_any(self) -> None:
        """In operator with array field checks if ANY element is in target list."""
        assert compare_values(["A", "D"], ["A", "B", "C"], "in") is True
        assert compare_values(["D", "E"], ["A", "B", "C"], "in") is False

    def test_in_none_handling(self) -> None:
        """In with None returns False."""
        assert compare_values(None, ["A", "B"], "in") is False

    def test_in_invalid_target(self) -> None:
        """In with non-list target returns False."""
        assert compare_values("A", "A", "in") is False

    def test_between_inclusive(self) -> None:
        """Between is inclusive on both ends."""
        assert compare_values(5, [1, 10], "between") is True
        assert compare_values(1, [1, 10], "between") is True
        assert compare_values(10, [1, 10], "between") is True
        assert compare_values(0, [1, 10], "between") is False
        assert compare_values(11, [1, 10], "between") is False

    def test_between_invalid_target(self) -> None:
        """Between with invalid target returns False."""
        assert compare_values(5, [1], "between") is False
        assert compare_values(5, [1, 2, 3], "between") is False
        assert compare_values(5, None, "between") is False
        assert compare_values(None, [1, 10], "between") is False

    def test_has_any_exact_match(self) -> None:
        """has_any checks exact array membership."""
        assert compare_values(["A", "B", "C"], ["B", "D"], "has_any") is True
        assert compare_values(["A", "B", "C"], ["D", "E"], "has_any") is False

    def test_has_any_requires_arrays(self) -> None:
        """has_any requires both values to be arrays."""
        assert compare_values("A", ["A"], "has_any") is False
        assert compare_values(["A"], "A", "has_any") is False

    def test_has_any_empty_filter(self) -> None:
        """has_any with empty filter list returns False."""
        assert compare_values(["A", "B"], [], "has_any") is False

    def test_has_all_exact_match(self) -> None:
        """has_all checks all elements present."""
        assert compare_values(["A", "B", "C"], ["A", "B"], "has_all") is True
        assert compare_values(["A", "B"], ["A", "B", "C"], "has_all") is False

    def test_has_all_requires_arrays(self) -> None:
        """has_all requires both values to be arrays."""
        assert compare_values("A", ["A"], "has_all") is False
        assert compare_values(["A"], "A", "has_all") is False

    def test_has_all_empty_filter(self) -> None:
        """has_all with empty filter list returns False (avoid vacuous truth)."""
        assert compare_values(["A", "B"], [], "has_all") is False


# =============================================================================
# compare_values() - substring collection operators
# =============================================================================


class TestSubstringCollectionOperators:
    """Tests for contains_any, contains_all operators."""

    def test_contains_any_scalar(self) -> None:
        """contains_any on scalar checks any term is substring."""
        assert compare_values("hello world", ["wor", "foo"], "contains_any") is True
        assert compare_values("hello world", ["foo", "bar"], "contains_any") is False

    def test_contains_any_case_insensitive(self) -> None:
        """contains_any is case-insensitive."""
        assert compare_values("Hello World", ["WORLD"], "contains_any") is True

    def test_contains_any_array(self) -> None:
        """contains_any on array checks across elements."""
        assert compare_values(["hello", "world"], ["wor"], "contains_any") is True
        assert compare_values(["hello", "world"], ["foo"], "contains_any") is False

    def test_contains_all_scalar(self) -> None:
        """contains_all on scalar checks all terms are substrings."""
        assert compare_values("hello world", ["hello", "world"], "contains_all") is True
        assert compare_values("hello world", ["hello", "foo"], "contains_all") is False

    def test_contains_all_array(self) -> None:
        """contains_all on array allows terms across different elements."""
        assert compare_values(["hello", "world"], ["hel", "wor"], "contains_all") is True
        assert compare_values(["hello", "world"], ["foo"], "contains_all") is False


# =============================================================================
# compare_values() - null/empty operators
# =============================================================================


class TestNullEmptyOperators:
    """Tests for is_null, is_not_null, is_empty operators."""

    def test_is_null_none(self) -> None:
        """is_null returns True for None."""
        assert compare_values(None, None, "is_null") is True

    def test_is_null_empty_string(self) -> None:
        """is_null returns True for empty string (SDK semantics)."""
        assert compare_values("", None, "is_null") is True

    def test_is_null_value(self) -> None:
        """is_null returns False for non-null values."""
        assert compare_values("hello", None, "is_null") is False
        assert compare_values(0, None, "is_null") is False
        assert compare_values(["a"], None, "is_null") is False

    def test_an_empty_list_is_null(self) -> None:
        """A multi-value field with no items has no value, as in Affinity's search."""
        assert compare_values([], None, "is_null") is True
        assert compare_values([], None, "is_not_null") is False

    def test_is_not_null_value(self) -> None:
        """is_not_null returns True for non-null, non-empty values."""
        assert compare_values("hello", None, "is_not_null") is True
        assert compare_values(0, None, "is_not_null") is True

    def test_is_not_null_none(self) -> None:
        """is_not_null returns False for None."""
        assert compare_values(None, None, "is_not_null") is False

    def test_is_not_null_empty_string(self) -> None:
        """is_not_null returns False for empty string (SDK semantics)."""
        assert compare_values("", None, "is_not_null") is False

    def test_is_empty_none(self) -> None:
        """is_empty returns True for None."""
        assert compare_values(None, None, "is_empty") is True

    def test_is_empty_empty_string(self) -> None:
        """is_empty returns True for empty string."""
        assert compare_values("", None, "is_empty") is True

    def test_is_empty_empty_array(self) -> None:
        """is_empty returns True for empty array."""
        assert compare_values([], None, "is_empty") is True

    def test_is_empty_non_empty(self) -> None:
        """is_empty returns False for non-empty values."""
        assert compare_values("hello", None, "is_empty") is False
        assert compare_values(["A"], None, "is_empty") is False
        assert compare_values(0, None, "is_empty") is False


# =============================================================================
# compare_values() - error handling
# =============================================================================


class TestCompareValuesErrorHandling:
    """Tests for error handling in compare_values."""

    def test_unknown_operator_raises(self) -> None:
        """Unknown operator raises ValueError with helpful message."""
        with pytest.raises(ValueError, match=r"Unknown comparison operator: 'invalid'"):
            compare_values("a", "b", "invalid")

    def test_unknown_operator_lists_valid(self) -> None:
        """Error message lists valid operators."""
        with pytest.raises(ValueError, match=r"Valid operators:.*eq.*neq.*contains"):
            compare_values("a", "b", "invalid")


# =============================================================================
# map_operator() tests
# =============================================================================


class TestMapOperator:
    """Tests for operator name mapping."""

    def test_symbolic_operators(self) -> None:
        """Symbolic operators map correctly."""
        assert map_operator("=") == "eq"
        assert map_operator("!=") == "neq"
        assert map_operator("=~") == "contains"
        assert map_operator("=^") == "starts_with"
        assert map_operator("=$") == "ends_with"
        assert map_operator(">") == "gt"
        assert map_operator(">=") == "gte"
        assert map_operator("<") == "lt"
        assert map_operator("<=") == "lte"

    def test_word_aliases(self) -> None:
        """Word-based aliases map correctly."""
        assert map_operator("contains") == "contains"
        assert map_operator("starts_with") == "starts_with"
        assert map_operator("ends_with") == "ends_with"
        assert map_operator("gt") == "gt"
        assert map_operator("gte") == "gte"
        assert map_operator("lt") == "lt"
        assert map_operator("lte") == "lte"
        assert map_operator("is null") == "is_null"
        assert map_operator("is not null") == "is_not_null"
        assert map_operator("is empty") == "is_empty"

    def test_collection_operators(self) -> None:
        """Collection operators map correctly."""
        assert map_operator("in") == "in"
        assert map_operator("between") == "between"
        assert map_operator("has_any") == "has_any"
        assert map_operator("has_all") == "has_all"
        assert map_operator("contains_any") == "contains_any"
        assert map_operator("contains_all") == "contains_all"

    def test_unknown_operator_raises(self) -> None:
        """Unknown operator raises ValueError."""
        with pytest.raises(ValueError, match=r"Unknown operator: 'invalid'"):
            map_operator("invalid")

    def test_unknown_operator_lists_valid(self) -> None:
        """Error message lists valid operators."""
        with pytest.raises(ValueError, match=r"Valid operators:"):
            map_operator("invalid")


# =============================================================================
# SDK_OPERATOR_MAP completeness
# =============================================================================


class TestOperatorMapCompleteness:
    """Tests to ensure operator map is complete."""

    def test_all_symbolic_operators_mapped(self) -> None:
        """All V2 API symbolic operators are mapped."""
        required_symbols = ["=", "!=", "=~", "=^", "=$", ">", ">=", "<", "<="]
        for symbol in required_symbols:
            assert symbol in SDK_OPERATOR_MAP, f"Missing symbol: {symbol}"

    def test_all_word_aliases_mapped(self) -> None:
        """All word-based aliases are mapped."""
        required_aliases = [
            "contains",
            "starts_with",
            "ends_with",
            "gt",
            "gte",
            "lt",
            "lte",
            "is null",
            "is not null",
            "is empty",
            "in",
            "between",
            "has_any",
            "has_all",
            "contains_any",
            "contains_all",
        ]
        for alias in required_aliases:
            assert alias in SDK_OPERATOR_MAP, f"Missing alias: {alias}"
