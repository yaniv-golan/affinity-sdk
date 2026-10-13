"""Tests for the filter parser and matches() functionality."""

from __future__ import annotations

import pytest

from affinity.filters import (
    AndExpression,
    FieldComparison,
    NotExpression,
    OrExpression,
    RawFilter,
    RawToken,
    parse,
)

# =============================================================================
# Parser tests - simple conditions
# =============================================================================


def test_parse_simple_equality() -> None:
    """Test parsing simple equality expression."""
    expr = parse("name=Alice")
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "name"
    assert expr.operator == "="
    assert expr.value == "Alice"


def test_parse_simple_inequality() -> None:
    """Test parsing simple inequality expression."""
    expr = parse("name!=Bob")
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "name"
    assert expr.operator == "!="
    assert expr.value == "Bob"


def test_parse_contains() -> None:
    """Test parsing contains operator."""
    expr = parse("name=~Corp")
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "name"
    assert expr.operator == "=~"
    assert expr.value == "Corp"


def test_parse_is_null() -> None:
    """Test parsing IS NULL (!=*) expression."""
    expr = parse("email!=*")
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "email"
    assert expr.operator == "!="
    assert isinstance(expr.value, RawToken)
    assert expr.value.token == "*"


def test_parse_is_not_null() -> None:
    """Test parsing IS NOT NULL (=*) expression."""
    expr = parse("email=*")
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "email"
    assert expr.operator == "="
    assert isinstance(expr.value, RawToken)
    assert expr.value.token == "*"


def test_parse_with_whitespace() -> None:
    """Test parsing with spaces around operators."""
    expr = parse("name = Alice")
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "name"
    assert expr.value == "Alice"


def test_parse_quoted_field_name() -> None:
    """Test parsing quoted field name with spaces."""
    expr = parse('"Primary Email Status"=Valid')
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "Primary Email Status"
    assert expr.value == "Valid"


def test_parse_quoted_value() -> None:
    """Test parsing quoted value with spaces."""
    expr = parse('status="Active User"')
    assert isinstance(expr, FieldComparison)
    assert expr.field_name == "status"
    assert expr.value == "Active User"


def test_parse_quoted_with_escapes() -> None:
    """Test parsing quoted string with escapes."""
    expr = parse('name="Alice \\"Bob\\" Smith"')
    assert isinstance(expr, FieldComparison)
    assert expr.value == 'Alice "Bob" Smith'


# =============================================================================
# Parser tests - boolean operators
# =============================================================================


def test_parse_or() -> None:
    """Test parsing OR expression."""
    expr = parse("status=Active | status=Pending")
    assert isinstance(expr, OrExpression)
    assert isinstance(expr.left, FieldComparison)
    assert isinstance(expr.right, FieldComparison)


def test_parse_and() -> None:
    """Test parsing AND expression."""
    expr = parse("status=Active & role=CEO")
    assert isinstance(expr, AndExpression)
    assert isinstance(expr.left, FieldComparison)
    assert isinstance(expr.right, FieldComparison)


def test_parse_not() -> None:
    """Test parsing NOT expression."""
    expr = parse("!(status=Inactive)")
    assert isinstance(expr, NotExpression)
    assert isinstance(expr.expr, FieldComparison)


def test_parse_grouped() -> None:
    """Test parsing grouped expression with parentheses."""
    expr = parse("(status=A | status=B) & role=CEO")
    assert isinstance(expr, AndExpression)
    assert isinstance(expr.left, OrExpression)
    assert isinstance(expr.right, FieldComparison)


def test_parse_precedence() -> None:
    """Test that AND has higher precedence than OR."""
    # a=1 | b=2 & c=3 should be parsed as a=1 | (b=2 & c=3)
    expr = parse("a=1 | b=2 & c=3")
    assert isinstance(expr, OrExpression)
    assert isinstance(expr.left, FieldComparison)
    assert isinstance(expr.right, AndExpression)


def test_parse_multiple_or() -> None:
    """Test parsing multiple OR expressions."""
    expr = parse("a=1 | b=2 | c=3")
    assert isinstance(expr, OrExpression)


def test_parse_multiple_and() -> None:
    """Test parsing multiple AND expressions."""
    expr = parse("a=1 & b=2 & c=3")
    assert isinstance(expr, AndExpression)


# =============================================================================
# matches() tests - FieldComparison
# =============================================================================


def test_matches_equality() -> None:
    """Test equality matching."""
    expr = parse("name=Alice")
    assert expr.matches({"name": "Alice"})
    assert not expr.matches({"name": "Bob"})


def test_matches_inequality() -> None:
    """Test inequality matching."""
    expr = parse("name!=Bob")
    assert expr.matches({"name": "Alice"})
    assert not expr.matches({"name": "Bob"})


def test_matches_contains() -> None:
    """Test contains matching (case-insensitive)."""
    expr = parse("name=~Corp")
    assert expr.matches({"name": "Acme Corp"})
    assert expr.matches({"name": "CORP International"})
    assert not expr.matches({"name": "Acme Inc"})


def test_matches_is_null() -> None:
    """Test IS NULL matching."""
    expr = parse("email!=*")
    assert expr.matches({"email": None})
    assert expr.matches({"email": ""})
    assert expr.matches({})  # missing key
    assert not expr.matches({"email": "test@example.com"})


def test_matches_is_not_null() -> None:
    """Test IS NOT NULL matching."""
    expr = parse("email=*")
    assert expr.matches({"email": "test@example.com"})
    assert not expr.matches({"email": None})
    assert not expr.matches({"email": ""})
    assert not expr.matches({})


def test_matches_string_coercion() -> None:
    """Test that values are coerced to strings."""
    expr = parse("count=5")
    assert expr.matches({"count": 5})  # int coerced to "5"
    assert expr.matches({"count": "5"})  # already string


def test_matches_boolean_coercion() -> None:
    """Test that booleans are coerced to strings."""
    expr = parse("active=True")
    assert expr.matches({"active": True})  # bool coerced to "True"
    assert not expr.matches({"active": False})


# =============================================================================
# matches() tests - compound expressions
# =============================================================================


def test_matches_or() -> None:
    """Test OR matching."""
    expr = parse("status=Unknown | status=Valid")
    assert expr.matches({"status": "Unknown"})
    assert expr.matches({"status": "Valid"})
    assert not expr.matches({"status": "Invalid"})


def test_matches_and() -> None:
    """Test AND matching."""
    expr = parse("status=Active & role=CEO")
    assert expr.matches({"status": "Active", "role": "CEO"})
    assert not expr.matches({"status": "Active", "role": "CTO"})
    assert not expr.matches({"status": "Inactive", "role": "CEO"})


def test_matches_not() -> None:
    """Test NOT matching."""
    expr = parse("!(status=Inactive)")
    assert expr.matches({"status": "Active"})
    assert not expr.matches({"status": "Inactive"})


def test_matches_complex() -> None:
    """Test complex expression matching."""
    expr = parse("(status=A | status=B) & role=CEO")
    assert expr.matches({"status": "A", "role": "CEO"})
    assert expr.matches({"status": "B", "role": "CEO"})
    assert not expr.matches({"status": "A", "role": "CTO"})
    assert not expr.matches({"status": "C", "role": "CEO"})


def test_matches_precedence() -> None:
    """Test precedence in matching."""
    # a=1 | b=2 & c=3 should be parsed as a=1 | (b=2 & c=3)
    expr = parse("a=1 | b=2 & c=3")
    assert expr.matches({"a": "1"})  # OR short-circuits
    assert expr.matches({"b": "2", "c": "3"})  # AND on right side
    assert not expr.matches({"b": "2", "c": "4"})  # AND fails


# =============================================================================
# matches() tests - field name normalization
# =============================================================================


def test_matches_field_name_exact() -> None:
    """Test exact field name matching."""
    expr = parse('"Primary Email Status"=Valid')
    assert expr.matches({"Primary Email Status": "Valid"})


def test_matches_field_name_lowercase_fallback() -> None:
    """Test lowercase field name fallback."""
    expr = parse("Email=test@example.com")
    assert expr.matches({"email": "test@example.com"})


def test_matches_field_name_prefix_fallback() -> None:
    """Test prefixed field name fallback."""
    expr = parse("name=Alice")
    assert expr.matches({"person.name": "Alice"})


# =============================================================================
# RawFilter tests
# =============================================================================


def test_raw_filter_matches_raises() -> None:
    """Test that RawFilter.matches() raises NotImplementedError."""
    expr = RawFilter("custom raw expression")
    with pytest.raises(NotImplementedError):
        expr.matches({"any": "data"})


# =============================================================================
# Error handling tests
# =============================================================================


def test_parse_empty_expression() -> None:
    """Test that empty expression raises ValueError."""
    with pytest.raises(ValueError, match="Empty"):
        parse("")


def test_parse_whitespace_only() -> None:
    """Test that whitespace-only expression raises ValueError."""
    with pytest.raises(ValueError, match="Empty"):
        parse("   ")


def test_parse_unbalanced_parens() -> None:
    """Test that unbalanced parentheses raises ValueError."""
    with pytest.raises(ValueError, match=r"[Uu]nbalanced"):
        parse("(a=1 | b=2")


def test_parse_missing_field_name() -> None:
    """Test that missing field name raises ValueError."""
    with pytest.raises(ValueError):
        parse("=value")


def test_parse_missing_value() -> None:
    """Test that missing value raises ValueError."""
    with pytest.raises(ValueError):
        parse("field=")


def test_parse_unterminated_quote() -> None:
    """Test that unterminated quote raises ValueError."""
    with pytest.raises(ValueError, match=r"[Uu]nterminated"):
        parse('"unclosed')


def test_parse_invalid_operator() -> None:
    """Test that invalid operator syntax raises ValueError."""
    # Test invalid multi-character operators
    with pytest.raises(ValueError):
        parse("field >> value")  # >> is not a valid operator

    with pytest.raises(ValueError):
        parse("field << value")  # << is not a valid operator

    with pytest.raises(ValueError):
        parse("field <> value")  # <> is not a valid operator


def test_parse_multi_word_field_suggests_quoting() -> None:
    """Test that unquoted multi-word field names give helpful error message."""
    with pytest.raises(ValueError, match=r'Hint.*"Team Member"'):
        parse('Team Member = "LB"')

    # Three-word field name
    with pytest.raises(ValueError, match=r'Hint.*"Primary Email Status"'):
        parse("Primary Email Status = Valid")


def test_parse_invalid_operator_combinations() -> None:
    """Test that invalid operator combinations give errors.

    Note: <>, >>, << are tokenized as combinations of valid operators,
    so they produce different errors than "unsupported operator".
    """
    # <> is tokenized as < then >, so after parsing "count <" it expects a value but gets >
    with pytest.raises(ValueError, match=r"Expected value after operator"):
        parse("count <> 5")

    # >> is tokenized as > then >, so after parsing "count > >" the second > is unexpected
    with pytest.raises(ValueError):
        parse("count >> 5")

    # << is tokenized as < then <
    with pytest.raises(ValueError):
        parse("count << 5")


def test_parse_comparison_operators() -> None:
    """Test that >, >=, <, <= operators are now supported."""
    # These should parse successfully
    expr = parse("count > 5")
    assert isinstance(expr, FieldComparison)
    assert expr.operator == ">"

    expr = parse("count >= 5")
    assert isinstance(expr, FieldComparison)
    assert expr.operator == ">="

    expr = parse("count < 5")
    assert isinstance(expr, FieldComparison)
    assert expr.operator == "<"

    expr = parse("count <= 5")
    assert isinstance(expr, FieldComparison)
    assert expr.operator == "<="


def test_parse_starts_with_ends_with_operators() -> None:
    """Test that =^ and =$ operators work."""
    expr = parse('name =^ "A"')
    assert isinstance(expr, FieldComparison)
    assert expr.operator == "=^"

    expr = parse('name =$ "z"')
    assert isinstance(expr, FieldComparison)
    assert expr.operator == "=$"


def test_parse_multi_word_value_suggests_quoting() -> None:
    """Test that unquoted multi-word values give helpful error message."""
    with pytest.raises(ValueError, match=r"Hint.*Values with spaces must be quoted"):
        parse("Status = Intro Meeting")

    with pytest.raises(ValueError, match=r"Hint.*Values with spaces must be quoted"):
        parse("Status = Intro Meeting Scheduled")


def test_parse_sql_keywords_suggest_symbols() -> None:
    """Test that SQL-like AND/OR keywords suggest correct symbols."""
    with pytest.raises(ValueError, match=r"Hint.*Use '&' for AND"):
        parse("status = A AND role = B")

    with pytest.raises(ValueError, match=r"Hint.*Use '\|' for OR"):
        parse("status = A OR status = B")


def test_parse_double_equals_suggests_single() -> None:
    """Test that == suggests using single =."""
    with pytest.raises(ValueError, match=r"Hint.*Use single '=' for equality"):
        parse("status == Active")


def test_parse_unknown_operator_suggests_similar() -> None:
    """Test that misspelled operators suggest similar valid operators."""
    # Prefix match suggestions
    with pytest.raises(ValueError, match=r"Did you mean: contains\?"):
        parse("name cont 'test'")

    # Typo suggestions for word operators
    with pytest.raises(ValueError, match=r"Did you mean: contains\?"):
        parse("name containz 'test'")

    with pytest.raises(ValueError, match=r"Did you mean: starts_with\?"):
        parse("name starts_witth 'A'")

    with pytest.raises(ValueError, match=r"Did you mean: ends_with\?"):
        parse("name ends_wit 'z'")

    # Short operator typos
    with pytest.raises(ValueError, match=r"Did you mean: gt\?"):
        parse("count gtt 5")

    with pytest.raises(ValueError, match=r"Did you mean: lte\?"):
        parse("count ltee 5")

    # Collection operator suggestions
    with pytest.raises(ValueError, match=r"Did you mean: has_any\?"):
        parse("tags has_ann [a, b]")

    with pytest.raises(ValueError, match=r"Did you mean: between\?"):
        parse("count betwee [1, 10]")


# =============================================================================
# Integration tests - realistic use cases
# =============================================================================


def test_realistic_email_status_filter() -> None:
    """Test realistic Primary Email Status filter from the proposal."""
    # Filter for people with "Primary Email Status" being Unknown, Valid, or not set
    expr = parse(
        '"Primary Email Status"=Unknown | "Primary Email Status"=Valid | "Primary Email Status"!=*'
    )

    # Should match
    assert expr.matches({"Primary Email Status": "Unknown"})
    assert expr.matches({"Primary Email Status": "Valid"})
    assert expr.matches({"Primary Email Status": None})
    assert expr.matches({"Primary Email Status": ""})
    assert expr.matches({})  # missing key

    # Should not match
    assert not expr.matches({"Primary Email Status": "Invalid"})
    assert not expr.matches({"Primary Email Status": "Bounced"})


def test_realistic_industry_filter() -> None:
    """Test realistic industry filter."""
    expr = parse("Industry=Tech | Industry=Finance")
    assert expr.matches({"Industry": "Tech"})
    assert expr.matches({"Industry": "Finance"})
    assert not expr.matches({"Industry": "Healthcare"})


def test_realistic_combined_filter() -> None:
    """Test realistic combined filter with AND and OR."""
    # Filter for people with email set AND status is Valid
    expr = parse("email=* & status=Valid")
    assert expr.matches({"email": "test@example.com", "status": "Valid"})
    assert not expr.matches({"email": "test@example.com", "status": "Invalid"})
    assert not expr.matches({"email": None, "status": "Valid"})
    assert not expr.matches({"status": "Valid"})  # email missing


# =============================================================================
# matches() tests - array fields (multi-select dropdown support)
# =============================================================================


class TestArrayFieldMatching:
    """Test filtering on multi-select/array fields."""

    @pytest.mark.req("SDK-FILT-007")
    def test_eq_array_field_membership(self) -> None:
        """eq operator on array field checks membership."""
        expr = FieldComparison("Team Member", "=", "LB")
        assert expr.matches({"Team Member": ["LB", "MA", "RK"]})
        assert not expr.matches({"Team Member": ["MA", "RK"]})

    @pytest.mark.req("SDK-FILT-007")
    def test_eq_array_to_list_set_equality(self) -> None:
        """eq with list value checks set equality (order-insensitive)."""
        expr = FieldComparison("Team Member", "=", ["LB", "MA"])
        assert expr.matches({"Team Member": ["LB", "MA"]})
        assert expr.matches({"Team Member": ["MA", "LB"]})  # order doesn't matter
        assert not expr.matches({"Team Member": ["LB", "RK"]})

    @pytest.mark.req("SDK-FILT-007")
    def test_neq_array_field_not_in(self) -> None:
        """neq operator on array field checks value NOT in array."""
        expr = FieldComparison("Team Member", "!=", "LB")
        assert expr.matches({"Team Member": ["MA", "RK"]})  # LB not in list
        assert not expr.matches({"Team Member": ["LB", "MA"]})  # LB is in list

    @pytest.mark.req("SDK-FILT-007")
    def test_neq_array_to_list_set_inequality(self) -> None:
        """neq with list value checks set inequality."""
        expr = FieldComparison("Team Member", "!=", ["LB", "MA"])
        assert expr.matches({"Team Member": ["LB", "RK"]})  # different sets
        assert not expr.matches({"Team Member": ["MA", "LB"]})  # same set

    @pytest.mark.req("SDK-FILT-007")
    def test_contains_array_field(self) -> None:
        """contains operator on array field checks any element."""
        expr = FieldComparison("Tags", "=~", "tech")
        assert expr.matches({"Tags": ["technology", "startup"]})
        assert expr.matches({"Tags": ["Tech Company", "Finance"]})
        assert not expr.matches({"Tags": ["finance", "healthcare"]})

    @pytest.mark.req("SDK-FILT-007")
    def test_empty_array_no_match(self) -> None:
        """Empty array should not match any value."""
        expr = FieldComparison("Team Member", "=", "LB")
        assert not expr.matches({"Team Member": []})

    @pytest.mark.req("SDK-FILT-007")
    def test_single_element_array(self) -> None:
        """Single-element array should match its element."""
        expr = FieldComparison("Team Member", "=", "LB")
        assert expr.matches({"Team Member": ["LB"]})

    @pytest.mark.req("SDK-FILT-007")
    def test_array_with_int_value(self) -> None:
        """Array field with integer values."""
        expr = FieldComparison("IDs", "=", 2)
        assert expr.matches({"IDs": [1, 2, 3]})
        assert not expr.matches({"IDs": [1, 3, 5]})

    @pytest.mark.req("SDK-FILT-007")
    def test_parsed_filter_array_membership(self) -> None:
        """Test array membership via parsed filter string."""
        # Quoted field name for multi-word field
        expr = parse('"Team Member"=LB')
        assert expr.matches({"Team Member": ["LB", "MA"]})
        assert not expr.matches({"Team Member": ["MA", "RK"]})

    @pytest.mark.req("SDK-FILT-007")
    def test_parsed_filter_array_contains(self) -> None:
        """Test array contains via parsed filter string."""
        expr = parse('"Team Member"=~LB')
        assert expr.matches({"Team Member": ["LB", "MA"]})
        assert expr.matches({"Team Member": ["XLB", "MA"]})  # contains "LB"
        assert not expr.matches({"Team Member": ["MA", "RK"]})

    @pytest.mark.req("SDK-FILT-007")
    def test_scalar_field_unchanged(self) -> None:
        """Scalar fields should work as before (no regression)."""
        expr = FieldComparison("Status", "=", "Active")
        assert expr.matches({"Status": "Active"})
        assert not expr.matches({"Status": "Inactive"})


# =============================================================================
# matches() tests - new operators (=^, =$, >, >=, <, <=)
# =============================================================================


class TestNewOperatorMatching:
    """Tests for newly supported operators in matches()."""

    def test_starts_with_scalar(self) -> None:
        """=^ operator checks prefix match (case-insensitive)."""
        expr = FieldComparison("name", "=^", "Acme")
        assert expr.matches({"name": "Acme Corp"})
        assert expr.matches({"name": "ACME Industries"})  # case-insensitive
        assert not expr.matches({"name": "Company Acme"})

    def test_starts_with_array(self) -> None:
        """=^ operator on array checks if any element starts with prefix."""
        expr = FieldComparison("tags", "=^", "tech")
        assert expr.matches({"tags": ["technology", "finance"]})
        assert expr.matches({"tags": ["other", "Tech Stack"]})  # case-insensitive
        assert not expr.matches({"tags": ["high-tech", "finance"]})  # mid-word

    def test_ends_with_scalar(self) -> None:
        """=$ operator checks suffix match (case-insensitive)."""
        expr = FieldComparison("email", "=$", "@example.com")
        assert expr.matches({"email": "user@example.com"})
        assert expr.matches({"email": "USER@EXAMPLE.COM"})  # case-insensitive
        assert not expr.matches({"email": "user@example.org"})

    def test_ends_with_array(self) -> None:
        """=$ operator on array checks if any element ends with suffix."""
        expr = FieldComparison("domains", "=$", ".com")
        assert expr.matches({"domains": ["example.com", "example.org"]})
        assert not expr.matches({"domains": ["example.org", "example.net"]})

    def test_greater_than(self) -> None:
        """> operator for numeric comparison."""
        expr = FieldComparison("count", ">", 5)
        assert expr.matches({"count": 10})
        assert not expr.matches({"count": 5})
        assert not expr.matches({"count": 3})

    def test_greater_than_or_equal(self) -> None:
        """>= operator for numeric comparison."""
        expr = FieldComparison("count", ">=", 5)
        assert expr.matches({"count": 10})
        assert expr.matches({"count": 5})
        assert not expr.matches({"count": 3})

    def test_less_than(self) -> None:
        """< operator for numeric comparison."""
        expr = FieldComparison("count", "<", 5)
        assert expr.matches({"count": 3})
        assert not expr.matches({"count": 5})
        assert not expr.matches({"count": 10})

    def test_less_than_or_equal(self) -> None:
        """<= operator for numeric comparison."""
        expr = FieldComparison("count", "<=", 5)
        assert expr.matches({"count": 3})
        assert expr.matches({"count": 5})
        assert not expr.matches({"count": 10})

    def test_comparison_with_none(self) -> None:
        """Comparison operators return False for None values."""
        expr = FieldComparison("count", ">", 5)
        assert not expr.matches({"count": None})
        assert not expr.matches({})  # missing field

    def test_parsed_starts_with(self) -> None:
        """Test =^ via parsed filter string."""
        expr = parse('name =^ "Acme"')
        assert expr.matches({"name": "Acme Corp"})
        assert not expr.matches({"name": "Corp Acme"})

    def test_parsed_ends_with(self) -> None:
        """Test =$ via parsed filter string."""
        expr = parse('email =$ "@example.com"')
        assert expr.matches({"email": "user@example.com"})
        assert not expr.matches({"email": "user@other.com"})

    def test_parsed_greater_than(self) -> None:
        """Test > via parsed filter string."""
        expr = parse("count > 5")
        assert expr.matches({"count": 10})
        assert not expr.matches({"count": 3})


# =============================================================================
# Word-based operator aliases (SDK extensions for LLM/human clarity)
# =============================================================================


class TestWordBasedOperatorAliases:
    """Tests for word-based operator aliases.

    These are SDK extensions for LLM/human clarity. They map to the
    underlying symbolic operators but allow more readable filter strings.
    """

    # -------------------------------------------------------------------------
    # Single-word aliases
    # -------------------------------------------------------------------------

    def test_contains_alias(self) -> None:
        """'contains' alias maps to =~ operator."""
        expr = parse('name contains "Acme"')
        assert isinstance(expr, FieldComparison)
        assert expr.operator == "=~"
        assert expr.matches({"name": "Acme Corp"})
        assert not expr.matches({"name": "Other Corp"})

    def test_starts_with_alias(self) -> None:
        """'starts_with' alias maps to =^ operator."""
        expr = parse('name starts_with "Acme"')
        assert isinstance(expr, FieldComparison)
        assert expr.operator == "=^"
        assert expr.matches({"name": "Acme Corp"})
        assert not expr.matches({"name": "The Acme Corp"})

    def test_ends_with_alias(self) -> None:
        """'ends_with' alias maps to =$ operator."""
        expr = parse('email ends_with "@example.com"')
        assert isinstance(expr, FieldComparison)
        assert expr.operator == "=$"
        assert expr.matches({"email": "user@example.com"})
        assert not expr.matches({"email": "user@other.com"})

    def test_gt_alias(self) -> None:
        """'gt' alias maps to > operator."""
        expr = parse("count gt 5")
        assert isinstance(expr, FieldComparison)
        assert expr.operator == ">"
        assert expr.matches({"count": 10})
        assert not expr.matches({"count": 5})

    def test_gte_alias(self) -> None:
        """'gte' alias maps to >= operator."""
        expr = parse("count gte 5")
        assert isinstance(expr, FieldComparison)
        assert expr.operator == ">="
        assert expr.matches({"count": 5})
        assert not expr.matches({"count": 4})

    def test_lt_alias(self) -> None:
        """'lt' alias maps to < operator."""
        expr = parse("count lt 5")
        assert isinstance(expr, FieldComparison)
        assert expr.operator == "<"
        assert expr.matches({"count": 3})
        assert not expr.matches({"count": 5})

    def test_lte_alias(self) -> None:
        """'lte' alias maps to <= operator."""
        expr = parse("count lte 5")
        assert isinstance(expr, FieldComparison)
        assert expr.operator == "<="
        assert expr.matches({"count": 5})
        assert not expr.matches({"count": 6})

    # -------------------------------------------------------------------------
    # Multi-word aliases
    # -------------------------------------------------------------------------

    def test_is_null_alias(self) -> None:
        """'is null' alias for null check."""
        expr = parse("email is null")
        assert isinstance(expr, FieldComparison)
        # Maps to != * (which means IS NULL in Affinity convention)
        assert expr.operator == "!="
        assert isinstance(expr.value, RawToken)
        assert expr.value.token == "*"
        assert expr.matches({"email": None})
        assert expr.matches({"email": ""})
        assert expr.matches({})  # missing field
        assert not expr.matches({"email": "test@example.com"})

    def test_is_not_null_alias(self) -> None:
        """'is not null' alias for not-null check."""
        expr = parse("email is not null")
        assert isinstance(expr, FieldComparison)
        # Maps to = * (which means IS NOT NULL in Affinity convention)
        assert expr.operator == "="
        assert isinstance(expr.value, RawToken)
        assert expr.value.token == "*"
        assert expr.matches({"email": "test@example.com"})
        assert not expr.matches({"email": None})
        assert not expr.matches({"email": ""})

    def test_is_empty_alias(self) -> None:
        """'is empty' alias for empty check."""
        expr = parse("tags is empty")
        assert isinstance(expr, FieldComparison)
        assert expr.operator == "is empty"
        assert expr.matches({"tags": None})
        assert expr.matches({"tags": ""})
        assert expr.matches({"tags": []})
        assert not expr.matches({"tags": ["a"]})
        assert not expr.matches({"tags": "value"})

    # -------------------------------------------------------------------------
    # Case insensitivity
    # -------------------------------------------------------------------------

    def test_word_aliases_case_insensitive(self) -> None:
        """Word aliases should be case-insensitive."""
        # Contains
        expr1 = parse('name CONTAINS "Acme"')
        assert expr1.operator == "=~"

        expr2 = parse('name Contains "Acme"')
        assert expr2.operator == "=~"

        # IS NULL
        expr3 = parse("email IS NULL")
        assert expr3.matches({"email": None})

        expr4 = parse("email Is Null")
        assert expr4.matches({"email": None})

        # IS NOT NULL
        expr5 = parse("email IS NOT NULL")
        assert expr5.matches({"email": "test@example.com"})

    # -------------------------------------------------------------------------
    # Combined with boolean operators
    # -------------------------------------------------------------------------

    def test_word_alias_with_and(self) -> None:
        """Word aliases work with & operator."""
        expr = parse('name contains "Corp" & email is not null')
        assert expr.matches({"name": "Acme Corp", "email": "test@example.com"})
        assert not expr.matches({"name": "Acme Corp", "email": None})
        assert not expr.matches({"name": "Acme Inc", "email": "test@example.com"})

    def test_word_alias_with_or(self) -> None:
        """Word aliases work with | operator."""
        expr = parse("status = Active | tags is empty")
        assert expr.matches({"status": "Active", "tags": ["a"]})
        assert expr.matches({"status": "Inactive", "tags": []})
        assert not expr.matches({"status": "Inactive", "tags": ["a"]})

    def test_word_alias_with_parentheses(self) -> None:
        """Word aliases work with parentheses."""
        expr = parse('(name contains "Corp" | name contains "Inc") & email is not null')
        assert expr.matches({"name": "Acme Corp", "email": "test@example.com"})
        assert expr.matches({"name": "Acme Inc", "email": "test@example.com"})
        assert not expr.matches({"name": "Acme LLC", "email": "test@example.com"})

    # -------------------------------------------------------------------------
    # Quoted field names with word aliases
    # -------------------------------------------------------------------------

    def test_quoted_field_with_word_alias(self) -> None:
        """Quoted field names work with word aliases."""
        expr = parse('"Primary Email" is not null')
        assert expr.matches({"Primary Email": "test@example.com"})
        assert not expr.matches({"Primary Email": None})

        expr2 = parse('"Company Name" contains "Acme"')
        assert expr2.matches({"Company Name": "Acme Corporation"})
        assert not expr2.matches({"Company Name": "Other Company"})


# =============================================================================
# Collection bracket syntax and operators (SDK extensions)
# =============================================================================


class TestCollectionBracketSyntax:
    """Tests for collection bracket syntax [A, B, C]."""

    # -------------------------------------------------------------------------
    # Basic bracket parsing
    # -------------------------------------------------------------------------

    def test_bracket_list_parsing(self) -> None:
        """Bracket list is parsed as a list value."""
        expr = parse("status in [A, B, C]")
        assert isinstance(expr, FieldComparison)
        assert expr.operator == "in"
        assert expr.value == ["A", "B", "C"]

    def test_bracket_list_with_spaces(self) -> None:
        """Bracket list with spaces around values."""
        expr = parse("status in [ A , B , C ]")
        assert expr.value == ["A", "B", "C"]

    def test_bracket_list_quoted_values(self) -> None:
        """Bracket list with quoted values."""
        expr = parse('status in ["Active User", "Pending"]')
        assert expr.value == ["Active User", "Pending"]

    def test_bracket_list_mixed_values(self) -> None:
        """Bracket list with mixed quoted and unquoted values."""
        expr = parse('tags in [tech, "machine learning", finance]')
        assert expr.value == ["tech", "machine learning", "finance"]

    def test_empty_bracket_list(self) -> None:
        """Empty bracket list []."""
        expr = parse("tags = []")
        assert expr.value == []

    def test_single_item_bracket_list(self) -> None:
        """Single item bracket list."""
        expr = parse("status in [Active]")
        assert expr.value == ["Active"]

    # -------------------------------------------------------------------------
    # Collection operators
    # -------------------------------------------------------------------------

    def test_in_operator(self) -> None:
        """'in' operator checks if value is in list."""
        expr = parse("status in [Active, Pending]")
        assert expr.matches({"status": "Active"})
        assert expr.matches({"status": "Pending"})
        assert not expr.matches({"status": "Inactive"})

    def test_between_operator(self) -> None:
        """'between' operator checks if value is in range."""
        expr = parse("count between [1, 10]")
        assert expr.matches({"count": 1})
        assert expr.matches({"count": 5})
        assert expr.matches({"count": 10})
        assert not expr.matches({"count": 0})
        assert not expr.matches({"count": 11})

    def test_has_any_operator(self) -> None:
        """'has_any' operator checks if array has any of values."""
        expr = parse("tags has_any [tech, finance]")
        assert expr.matches({"tags": ["tech", "healthcare"]})
        assert expr.matches({"tags": ["finance"]})
        assert not expr.matches({"tags": ["healthcare", "education"]})

    def test_has_all_operator(self) -> None:
        """'has_all' operator checks if array has all values."""
        expr = parse("tags has_all [tech, startup]")
        assert expr.matches({"tags": ["tech", "startup", "finance"]})
        assert not expr.matches({"tags": ["tech"]})
        assert not expr.matches({"tags": ["startup"]})

    def test_contains_any_operator(self) -> None:
        """'contains_any' operator does substring match for any term."""
        expr = parse("name contains_any [Corp, Inc]")
        assert expr.matches({"name": "Acme Corporation"})
        assert expr.matches({"name": "Acme Inc"})
        assert not expr.matches({"name": "Acme LLC"})

    def test_contains_all_operator(self) -> None:
        """'contains_all' operator does substring match for all terms."""
        expr = parse("description contains_all [tech, company]")
        assert expr.matches({"description": "A tech company in SF"})
        assert not expr.matches({"description": "A tech startup"})
        assert not expr.matches({"description": "A company in SF"})

    # -------------------------------------------------------------------------
    # Set equality with brackets (official V2 API syntax)
    # -------------------------------------------------------------------------

    def test_eq_with_bracket_list(self) -> None:
        """= with bracket list checks set equality."""
        expr = parse("tags = [A, B]")
        assert expr.matches({"tags": ["A", "B"]})
        assert expr.matches({"tags": ["B", "A"]})  # order doesn't matter
        assert not expr.matches({"tags": ["A"]})
        assert not expr.matches({"tags": ["A", "B", "C"]})

    def test_neq_with_bracket_list(self) -> None:
        """!= with bracket list checks set inequality."""
        expr = parse("tags != [A, B]")
        assert expr.matches({"tags": ["A"]})
        assert expr.matches({"tags": ["A", "B", "C"]})
        assert not expr.matches({"tags": ["A", "B"]})
        assert not expr.matches({"tags": ["B", "A"]})

    def test_eq_empty_bracket(self) -> None:
        """= [] checks for empty array."""
        expr = parse("tags = []")
        assert expr.matches({"tags": []})
        assert not expr.matches({"tags": ["A"]})

    # -------------------------------------------------------------------------
    # Error cases
    # -------------------------------------------------------------------------

    def test_unclosed_bracket_error(self) -> None:
        """Unclosed bracket gives helpful error."""
        with pytest.raises(ValueError, match=r"Unclosed bracket.*closing bracket"):
            parse("status in [A, B")

    def test_trailing_comma_error(self) -> None:
        """Trailing comma gives helpful error."""
        with pytest.raises(ValueError, match=r"Unexpected '\]' after comma.*trailing comma"):
            parse("status in [A, B,]")

    def test_double_comma_error(self) -> None:
        """Double comma gives helpful error."""
        with pytest.raises(ValueError, match=r"Expected value before comma"):
            parse("status in [A,, B]")

    # -------------------------------------------------------------------------
    # Combined with boolean operators
    # -------------------------------------------------------------------------

    def test_bracket_with_and(self) -> None:
        """Bracket syntax with & operator."""
        expr = parse("status in [Active, Pending] & email is not null")
        assert expr.matches({"status": "Active", "email": "test@example.com"})
        assert not expr.matches({"status": "Active", "email": None})
        assert not expr.matches({"status": "Inactive", "email": "test@example.com"})

    def test_bracket_with_parentheses(self) -> None:
        """Bracket syntax with parentheses."""
        expr = parse("(status in [Active, Pending]) | (tags has_any [priority])")
        assert expr.matches({"status": "Active", "tags": []})
        assert expr.matches({"status": "Inactive", "tags": ["priority"]})

    # -------------------------------------------------------------------------
    # V2 API collection contains syntax (=~ [a, b])
    # -------------------------------------------------------------------------

    def test_contains_bracket_v2_api_syntax(self) -> None:
        """V2 API: =~ [a, b] means 'array contains all elements' (has_all semantics)."""
        # This is the official Affinity V2 API behavior for collection contains
        expr = parse("tags =~ [tech, startup]")
        assert expr.matches({"tags": ["tech", "startup", "finance"]})  # has both
        assert expr.matches({"tags": ["startup", "tech"]})  # exact match, different order
        assert not expr.matches({"tags": ["tech"]})  # missing startup
        assert not expr.matches({"tags": ["startup"]})  # missing tech
        assert not expr.matches({"tags": []})  # empty array

    def test_contains_bracket_word_alias(self) -> None:
        """'contains [a, b]' word alias also has 'has_all' semantics."""
        expr = parse("tags contains [tech, startup]")
        assert expr.matches({"tags": ["tech", "startup", "finance"]})
        assert not expr.matches({"tags": ["tech", "finance"]})

    def test_contains_bracket_single_element(self) -> None:
        """=~ [single] checks if array contains that element."""
        expr = parse("tags =~ [tech]")
        assert expr.matches({"tags": ["tech", "finance"]})
        assert expr.matches({"tags": ["tech"]})
        assert not expr.matches({"tags": ["finance"]})

    def test_contains_bracket_scalar_field_no_match(self) -> None:
        """=~ [a, b] on scalar field returns False (field must be array)."""
        expr = parse("name =~ [Acme, Corp]")
        # Scalar field cannot "contain all" from a list
        assert not expr.matches({"name": "Acme Corp"})
        assert not expr.matches({"name": "Acme"})

    def test_contains_scalar_still_works(self) -> None:
        """Ensure scalar =~ still does substring match (regression test)."""
        expr = parse('name =~ "Corp"')
        assert expr.matches({"name": "Acme Corp"})
        assert expr.matches({"name": "Corporation"})
        assert not expr.matches({"name": "Acme Inc"})

    # -------------------------------------------------------------------------
    # Empty string check (= "")
    # -------------------------------------------------------------------------

    def test_eq_empty_string(self) -> None:
        """= '' checks for empty string."""
        expr = parse('name = ""')
        assert expr.matches({"name": ""})
        assert not expr.matches({"name": "Acme"})
        assert not expr.matches({"name": None})  # None != ""

    def test_neq_empty_string(self) -> None:
        """!= '' checks for non-empty string."""
        expr = parse('name != ""')
        assert expr.matches({"name": "Acme"})
        assert expr.matches({"name": None})  # None != ""
        assert not expr.matches({"name": ""})


def test_text_filters_compare_relative_dates_as_dates() -> None:
    from affinity.filters import parse

    recent = parse('Close > "-30d"')
    assert not recent.matches({"Close": "2019-01-01T00:00:00Z"})
    assert recent.matches({"Close": "2999-01-01T00:00:00Z"})
    # Equality stays textual: "today" is the word, not a date
    assert parse('Code = "today"').matches({"Code": "today"})
