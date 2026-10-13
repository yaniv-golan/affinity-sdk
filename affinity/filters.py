"""
Filter builder for V2 API filtering support.

Provides a type-safe, Pythonic way to build filter expressions for V2 list endpoints.
The builder handles proper escaping and quoting of user inputs.

Example:
    from affinity.filters import Filter, F

    # Using the builder (recommended)
    filter = (
        F.field("name").contains("Acme") &
        F.field("status").equals("Active")
    )
    companies = client.companies.list(filter=filter)

    # Or build complex filters
    filter = (
        (F.field("name").contains("Corp") | F.field("name").contains("Inc")) &
        ~F.field("archived").equals(True)
    )

    # Raw filter string escape hatch (power users)
    companies = client.companies.list(filter='name =~ "Acme"')
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, auto
from typing import Any, ClassVar

from affinity.compare import compare_values, map_operator, normalize_value


@dataclass(frozen=True)
class RawToken:
    """
    A raw token inserted into a filter expression without quoting.

    Used for special Affinity Filtering Language literals like `*`.
    """

    token: str


def _escape_string(value: str) -> str:
    """
    Escape a string value for use in a filter expression.

    Handles:
    - Backslashes (must be doubled)
    - Double quotes (must be escaped)
    - Newlines and tabs (escaped as literals)
    - NUL bytes (removed)
    """
    # Order matters: escape backslashes first
    result = value.replace("\\", "\\\\")
    result = result.replace('"', '\\"')
    result = result.replace("\x00", "")
    result = result.replace("\n", "\\n")
    result = result.replace("\t", "\\t")
    result = result.replace("\r", "\\r")
    return result


def _format_value(value: Any) -> str:
    """Format a Python value for use in a filter expression."""
    if isinstance(value, RawToken):
        return value.token
    if value is None:
        raise ValueError("None is not a valid filter literal; use is_null()/is_not_null().")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # Handle datetime before date (datetime is subclass of date)
    if isinstance(value, datetime):
        return f'"{value.isoformat()}"'
    if isinstance(value, date):
        return f'"{value.isoformat()}"'
    # String and fallback
    text = value if isinstance(value, str) else str(value)
    return f'"{_escape_string(text)}"'


def _get_entity_value(entity: dict[str, Any], field_name: str) -> Any:
    """
    Get a field value from an entity dict with fallback normalization.

    Tries multiple key formats to handle field name variations:
    1. Exact field name as provided
    2. Lowercase version
    3. With entity type prefix (person., company., opportunity.)
    """
    value = entity.get(field_name)
    if value is None:
        value = entity.get(field_name.lower())
    if value is None:
        for prefix in ["person.", "company.", "opportunity."]:
            value = entity.get(f"{prefix}{field_name}")
            if value is not None:
                break
    return value


class FilterExpression(ABC):
    """Base class for filter expressions."""

    @abstractmethod
    def to_string(self) -> str:
        """Convert the expression to a filter string."""
        ...

    @abstractmethod
    def matches(self, entity: dict[str, Any]) -> bool:
        """
        Evaluate filter against an entity dict (client-side).

        Used for --expand-filter in list export where filtering happens
        after fetching data from the API.
        """
        ...

    def __and__(self, other: FilterExpression) -> FilterExpression:
        """Combine two expressions with `&`."""
        return AndExpression(self, other)

    def __or__(self, other: FilterExpression) -> FilterExpression:
        """Combine two expressions with `|`."""
        return OrExpression(self, other)

    def __invert__(self) -> FilterExpression:
        """Negate the expression with `!`."""
        return NotExpression(self)

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f"Filter({self.to_string()!r})"


@dataclass
class FieldComparison(FilterExpression):
    """A comparison operation on a field."""

    field_name: str
    operator: str
    value: Any

    def to_string(self) -> str:
        formatted_value = _format_value(self.value)
        return f"{self.field_name} {self.operator} {formatted_value}"

    def matches(self, entity: dict[str, Any]) -> bool:
        """Evaluate field comparison against an entity dict.

        For multi-select dropdown fields (arrays), the operators have special semantics:
        - `=` with scalar: checks if value is IN the array (membership)
        - `=` with list: checks set equality (order-insensitive)
        - `!=` with scalar: checks if value is NOT in the array
        - `!=` with list: checks set inequality
        - `=~` (contains): checks if any array element contains the substring
        - `=^` (starts_with): checks if any array element starts with the prefix
        - `=$` (ends_with): checks if any array element ends with the suffix
        - `>`, `>=`, `<`, `<=`: numeric/date comparisons

        Uses the shared compare module for consistent behavior across SDK and Query tool.
        """
        field_value = _get_entity_value(entity, self.field_name)

        # Normalize dropdown dicts and multi-select arrays
        field_value = normalize_value(field_value)

        # Handle NULL checks (Affinity convention: =* means NOT NULL, !=* means IS NULL)
        if isinstance(self.value, RawToken) and self.value.token == "*":
            if self.operator == "=":
                return compare_values(field_value, None, "is_not_null")
            elif self.operator == "!=":
                return compare_values(field_value, None, "is_null")

        # Extract target value
        target = self.value if not isinstance(self.value, RawToken) else self.value.token

        # Map SDK operator symbol to canonical operator name
        try:
            canonical_op = map_operator(self.operator)
        except ValueError:
            raise ValueError(
                f"Unsupported operator '{self.operator}' for client-side matching. "
                f"Supported operators: =, !=, =~, =^, =$, >, >=, <, <=, "
                f"contains, starts_with, ends_with, gt, gte, lt, lte, "
                f"is null, is not null, is empty, "
                f"in, between, has_any, has_all, contains_any, contains_all, starts_word"
            ) from None

        return compare_values(field_value, target, canonical_op)


@dataclass
class RawFilter(FilterExpression):
    """A raw filter string (escape hatch for power users)."""

    expression: str

    def to_string(self) -> str:
        return self.expression

    def matches(self, entity: dict[str, Any]) -> bool:
        """RawFilter cannot be evaluated client-side."""
        raise NotImplementedError(
            "RawFilter cannot be evaluated client-side. "
            "Use structured filter expressions for --expand-filter."
        )


@dataclass
class AndExpression(FilterExpression):
    """`&` combination of two expressions."""

    left: FilterExpression
    right: FilterExpression

    def to_string(self) -> str:
        left_str = self.left.to_string()
        right_str = self.right.to_string()
        # Wrap in parentheses for correct precedence
        return f"({left_str}) & ({right_str})"

    def matches(self, entity: dict[str, Any]) -> bool:
        """Both sides must match."""
        return self.left.matches(entity) and self.right.matches(entity)


@dataclass
class OrExpression(FilterExpression):
    """`|` combination of two expressions."""

    left: FilterExpression
    right: FilterExpression

    def to_string(self) -> str:
        left_str = self.left.to_string()
        right_str = self.right.to_string()
        return f"({left_str}) | ({right_str})"

    def matches(self, entity: dict[str, Any]) -> bool:
        """Either side must match."""
        return self.left.matches(entity) or self.right.matches(entity)


@dataclass
class NotExpression(FilterExpression):
    """`!` negation of an expression."""

    expr: FilterExpression

    def to_string(self) -> str:
        return f"!({self.expr.to_string()})"

    def matches(self, entity: dict[str, Any]) -> bool:
        """Invert the inner expression."""
        return not self.expr.matches(entity)


class FieldBuilder:
    """Builder for field-based filter expressions."""

    def __init__(self, field_name: str):
        self._field_name = field_name

    def equals(self, value: Any) -> FieldComparison:
        """Field equals value (exact match)."""
        return FieldComparison(self._field_name, "=", value)

    def not_equals(self, value: Any) -> FieldComparison:
        """Field does not equal value."""
        return FieldComparison(self._field_name, "!=", value)

    def contains(self, value: str) -> FieldComparison:
        """Field contains substring (case-insensitive)."""
        return FieldComparison(self._field_name, "=~", value)

    def starts_with(self, value: str) -> FieldComparison:
        """Field starts with prefix."""
        return FieldComparison(self._field_name, "=^", value)

    def ends_with(self, value: str) -> FieldComparison:
        """Field ends with suffix."""
        return FieldComparison(self._field_name, "=$", value)

    def greater_than(self, value: int | float | datetime | date) -> FieldComparison:
        """Field is greater than value."""
        return FieldComparison(self._field_name, ">", value)

    def greater_than_or_equal(self, value: int | float | datetime | date) -> FieldComparison:
        """Field is greater than or equal to value."""
        return FieldComparison(self._field_name, ">=", value)

    def less_than(self, value: int | float | datetime | date) -> FieldComparison:
        """Field is less than value."""
        return FieldComparison(self._field_name, "<", value)

    def less_than_or_equal(self, value: int | float | datetime | date) -> FieldComparison:
        """Field is less than or equal to value."""
        return FieldComparison(self._field_name, "<=", value)

    def is_null(self) -> FieldComparison:
        """Field is null."""
        return FieldComparison(self._field_name, "!=", RawToken("*"))

    def is_not_null(self) -> FieldComparison:
        """Field is not null."""
        return FieldComparison(self._field_name, "=", RawToken("*"))

    def in_list(self, values: list[Any]) -> FilterExpression:
        """Field value is in the given list (OR of equals)."""
        if not values:
            raise ValueError("in_list() requires at least one value")
        expressions: list[FilterExpression] = [self.equals(v) for v in values]
        result: FilterExpression = expressions[0]
        for expr in expressions[1:]:
            result = result | expr
        return result


class Filter:
    """
    Factory for building filter expressions.

    Example:
        # Simple comparison
        Filter.field("name").contains("Acme")

        # Complex boolean logic
        (Filter.field("status").equals("Active") &
         Filter.field("type").in_list(["customer", "prospect"]))

        # Negation
        ~Filter.field("archived").equals(True)
    """

    @staticmethod
    def field(name: str) -> FieldBuilder:
        """Start building a filter on a field."""
        return FieldBuilder(name)

    @staticmethod
    def raw(expression: str) -> RawFilter:
        """
        Create a raw filter expression (escape hatch).

        Use this when you need filter syntax not supported by the builder.
        The expression is passed directly to the API without modification.

        Args:
            expression: Raw filter string (e.g., 'name =~ "Acme"')
        """
        return RawFilter(expression)

    @staticmethod
    def and_(*expressions: FilterExpression) -> FilterExpression:
        """Combine multiple expressions with `&`."""
        if not expressions:
            raise ValueError("and_() requires at least one expression")
        result = expressions[0]
        for expr in expressions[1:]:
            result = result & expr
        return result

    @staticmethod
    def or_(*expressions: FilterExpression) -> FilterExpression:
        """Combine multiple expressions with `|`."""
        if not expressions:
            raise ValueError("or_() requires at least one expression")
        result = expressions[0]
        for expr in expressions[1:]:
            result = result | expr
        return result


# Shorthand alias for convenience
F = Filter


# =============================================================================
# Filter String Parser
# =============================================================================


class _TokenType(Enum):
    """Token types for the filter parser."""

    FIELD = auto()  # Field name (quoted or unquoted)
    OPERATOR = auto()  # =, !=, =~
    VALUE = auto()  # Value (quoted, unquoted, or *)
    AND = auto()  # &
    OR = auto()  # |
    NOT = auto()  # !
    LPAREN = auto()  # (
    RPAREN = auto()  # )
    EOF = auto()  # End of input


@dataclass
class _Token:
    """A token from the filter string."""

    type: _TokenType
    value: str | list[str]  # str for most tokens, list for bracket values
    pos: int  # Position in original string for error messages


class _Tokenizer:
    """Tokenizer for filter strings."""

    # Symbolic operators that can appear after field names
    # IMPORTANT: Multi-character operators MUST come first to avoid partial matches
    # e.g., ">=" must be checked before ">" or it will match as ">" + "="
    OPERATORS: ClassVar[tuple[str, ...]] = (">=", "<=", "!=", "=~", "=^", "=$", ">", "<", "=")

    # Single-word aliases for operators (SDK extensions for LLM/human clarity)
    WORD_OPERATORS: ClassVar[dict[str, str]] = {
        "contains": "=~",
        "starts_with": "=^",
        "ends_with": "=$",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        # Collection operators
        "in": "in",
        "between": "between",
        "has_any": "has_any",
        "has_all": "has_all",
        "contains_any": "contains_any",
        "contains_all": "contains_all",
        "starts_word": "starts_word",
    }

    # Multi-word aliases that need lookahead
    # Checked when we see "is" - peek ahead for "null", "not null", "empty"
    # These are stored as (operator_value, canonical_operator_name)
    MULTI_WORD_OPERATORS: ClassVar[dict[str, tuple[str, str, str | None]]] = {
        # "is null" -> "!= *" equivalent (maps to is_null in compare)
        "is null": ("is null", "!=", "*"),
        # "is not null" -> "= *" equivalent (maps to is_not_null in compare)
        "is not null": ("is not null", "=", "*"),
        # "is empty" -> check for empty string or empty array
        "is empty": ("is empty", "is empty", None),
    }

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def _skip_whitespace(self) -> None:
        """Skip whitespace characters."""
        while self.pos < self.length and self.text[self.pos] in " \t\n\r":
            self.pos += 1

    def _read_quoted_string(self) -> str:
        """Read a quoted string, handling escapes."""
        assert self.text[self.pos] == '"'
        start_pos = self.pos
        self.pos += 1  # Skip opening quote
        result: list[str] = []

        while self.pos < self.length:
            ch = self.text[self.pos]
            if ch == '"':
                self.pos += 1  # Skip closing quote
                return "".join(result)
            elif ch == "\\":
                self.pos += 1
                if self.pos >= self.length:
                    raise ValueError(
                        f"Unexpected end of string after backslash at position {self.pos}"
                    )
                escaped = self.text[self.pos]
                if escaped == "n":
                    result.append("\n")
                elif escaped == "t":
                    result.append("\t")
                elif escaped == "r":
                    result.append("\r")
                elif escaped in ('"', "\\"):
                    result.append(escaped)
                else:
                    result.append(escaped)
                self.pos += 1
            else:
                result.append(ch)
                self.pos += 1

        raise ValueError(f"Unterminated quoted string starting at position {start_pos}")

    def _read_unquoted(self, stop_chars: str) -> str:
        """Read an unquoted token until a stop character or whitespace."""
        start = self.pos
        while self.pos < self.length:
            ch = self.text[self.pos]
            if ch in stop_chars or ch in " \t\n\r":
                break
            self.pos += 1
        return self.text[start : self.pos]

    def _read_bracket_list(self) -> list[str]:
        """Read a bracket-delimited list: [A, B, C] or ["A B", C].

        Returns a list of string values.
        Raises ValueError for syntax errors with helpful messages.
        """
        assert self.text[self.pos] == "["
        start_pos = self.pos
        self.pos += 1  # Skip opening bracket

        items: list[str] = []
        expect_value = True  # Start expecting a value

        while self.pos < self.length:
            self._skip_whitespace()

            if self.pos >= self.length:
                raise ValueError(
                    f"Unclosed bracket at position {start_pos}. "
                    f"Hint: Collection syntax requires closing bracket: [A, B]"
                )

            ch = self.text[self.pos]

            if ch == "]":
                # Check for trailing comma (expect_value=True after comma means trailing comma)
                if items and expect_value:
                    # We just got a comma and now see ]
                    raise ValueError(
                        f"Unexpected ']' after comma at position {self.pos}. "
                        f"Hint: Remove trailing comma: [A, B] not [A, B,]"
                    )
                self.pos += 1  # Skip closing bracket
                return items

            if ch == ",":
                if expect_value:
                    raise ValueError(
                        f"Unexpected ',' at position {self.pos}. Hint: Expected value before comma"
                    )
                self.pos += 1  # Skip comma
                expect_value = True
                continue

            if not expect_value:
                raise ValueError(f"Expected ',' or ']' at position {self.pos}, got '{ch}'")

            # Read a value (quoted or unquoted)
            # Unquoted values stop at comma, bracket, or whitespace
            value = self._read_quoted_string() if ch == '"' else self._read_unquoted(",]")

            if not value:
                raise ValueError(f"Empty value in collection at position {self.pos}")

            items.append(value)
            expect_value = False

        raise ValueError(
            f"Unclosed bracket at position {start_pos}. "
            f"Hint: Collection syntax requires closing bracket: [A, B]"
        )

    def _peek_operator(self) -> str | None:
        """Check if current position starts with a symbolic operator."""
        for op in self.OPERATORS:
            if self.text[self.pos : self.pos + len(op)] == op:
                return op
        return None

    def _peek_word_operator(self) -> tuple[str, str] | None:
        """Check if the next word(s) form a word-based operator.

        Returns (alias, canonical_op) if found, None otherwise.
        Does not advance position - just peeks.
        """
        # Save position for potential rollback
        saved_pos = self.pos
        self._skip_whitespace()

        if self.pos >= self.length:
            self.pos = saved_pos
            return None

        # Read the next word
        word = self._read_unquoted('=!&|()"')
        word_lower = word.lower()

        # Check single-word operators
        if word_lower in self.WORD_OPERATORS:
            self.pos = saved_pos
            return (word_lower, self.WORD_OPERATORS[word_lower])

        # Check multi-word operators starting with "is"
        if word_lower == "is":
            self._skip_whitespace()
            if self.pos < self.length:
                next_word = self._read_unquoted('=!&|()"')
                next_lower = next_word.lower()

                if next_lower == "null":
                    self.pos = saved_pos
                    return ("is null", "is null")
                elif next_lower == "not":
                    self._skip_whitespace()
                    if self.pos < self.length:
                        third_word = self._read_unquoted('=!&|()"')
                        if third_word.lower() == "null":
                            self.pos = saved_pos
                            return ("is not null", "is not null")
                elif next_lower == "empty":
                    self.pos = saved_pos
                    return ("is empty", "is empty")

        self.pos = saved_pos
        return None

    def _consume_word_operator(self, alias: str) -> None:
        """Consume a word operator from the input, advancing position."""
        words = alias.split()
        for expected in words:
            self._skip_whitespace()
            word = self._read_unquoted('=!&|()"')
            # Verify (should match since we already peeked)
            assert word.lower() == expected.lower()

    def tokenize(self) -> list[_Token]:
        """Tokenize the entire filter string."""
        tokens: list[_Token] = []

        while True:
            self._skip_whitespace()

            if self.pos >= self.length:
                tokens.append(_Token(_TokenType.EOF, "", self.pos))
                break

            ch = self.text[self.pos]
            start_pos = self.pos

            # Single-character tokens
            if ch == "(":
                tokens.append(_Token(_TokenType.LPAREN, "(", start_pos))
                self.pos += 1
            elif ch == ")":
                tokens.append(_Token(_TokenType.RPAREN, ")", start_pos))
                self.pos += 1
            elif ch == "&":
                tokens.append(_Token(_TokenType.AND, "&", start_pos))
                self.pos += 1
            elif ch == "|":
                tokens.append(_Token(_TokenType.OR, "|", start_pos))
                self.pos += 1
            elif ch == "!":
                # Check if it's != operator or standalone NOT
                if self.pos + 1 < self.length and self.text[self.pos + 1] == "=":
                    # This is != operator, will be handled as OPERATOR
                    op = self._peek_operator()
                    if op:
                        tokens.append(_Token(_TokenType.OPERATOR, op, start_pos))
                        self.pos += len(op)
                    else:
                        raise ValueError(f"Unexpected character at position {start_pos}")
                else:
                    tokens.append(_Token(_TokenType.NOT, "!", start_pos))
                    self.pos += 1
            elif ch == '"':
                # Quoted string - could be field name or value depending on context
                value = self._read_quoted_string()
                # Determine token type based on context (what comes next)
                self._skip_whitespace()
                if self.pos < self.length and (self._peek_operator() or self._peek_word_operator()):
                    tokens.append(_Token(_TokenType.FIELD, value, start_pos))
                else:
                    tokens.append(_Token(_TokenType.VALUE, value, start_pos))
            elif ch == "*":
                # Wildcard value
                tokens.append(_Token(_TokenType.VALUE, "*", start_pos))
                self.pos += 1
            elif ch == "[":
                # Bracket list value: [A, B, C]
                items = self._read_bracket_list()
                tokens.append(_Token(_TokenType.VALUE, items, start_pos))
            else:
                # Check for symbolic operator first
                op = self._peek_operator()
                if op:
                    tokens.append(_Token(_TokenType.OPERATOR, op, start_pos))
                    self.pos += len(op)
                else:
                    # Unquoted field name, value, or word operator
                    # Read until operator, boolean, paren, or whitespace
                    value = self._read_unquoted('=!&|()"')
                    if not value:
                        raise ValueError(f"Unexpected character '{ch}' at position {start_pos}")

                    value_lower = value.lower()

                    # Check if this is a word operator
                    if value_lower in self.WORD_OPERATORS:
                        # Emit as OPERATOR with the canonical symbol
                        tokens.append(
                            _Token(_TokenType.OPERATOR, self.WORD_OPERATORS[value_lower], start_pos)
                        )
                    elif value_lower == "is":
                        # Check for multi-word operator: "is null", "is not null", "is empty"
                        saved_pos = self.pos
                        self._skip_whitespace()
                        if self.pos < self.length:
                            next_word = self._read_unquoted('=!&|()"')
                            next_lower = next_word.lower()
                            if next_lower == "null":
                                # "is null" -> != *
                                tokens.append(_Token(_TokenType.OPERATOR, "!=", start_pos))
                                tokens.append(_Token(_TokenType.VALUE, "*", self.pos))
                            elif next_lower == "empty":
                                # "is empty" -> is empty operator with placeholder value
                                tokens.append(_Token(_TokenType.OPERATOR, "is empty", start_pos))
                                tokens.append(_Token(_TokenType.VALUE, "", self.pos))  # placeholder
                            elif next_lower == "not":
                                # Could be "is not null"
                                self._skip_whitespace()
                                if self.pos < self.length:
                                    third_word = self._read_unquoted('=!&|()"')
                                    if third_word.lower() == "null":
                                        # "is not null" -> = *
                                        tokens.append(_Token(_TokenType.OPERATOR, "=", start_pos))
                                        tokens.append(_Token(_TokenType.VALUE, "*", self.pos))
                                    else:
                                        # Not a multi-word operator, restore
                                        self.pos = saved_pos
                                        self._skip_whitespace()
                                        if self._peek_operator() or self._peek_word_operator():
                                            tokens.append(
                                                _Token(_TokenType.FIELD, value, start_pos)
                                            )
                                        else:
                                            tokens.append(
                                                _Token(_TokenType.VALUE, value, start_pos)
                                            )
                                else:
                                    # Just "is not" with nothing after - restore
                                    self.pos = saved_pos
                                    tokens.append(_Token(_TokenType.VALUE, value, start_pos))
                            else:
                                # Not a multi-word operator, restore
                                self.pos = saved_pos
                                self._skip_whitespace()
                                if self._peek_operator() or self._peek_word_operator():
                                    tokens.append(_Token(_TokenType.FIELD, value, start_pos))
                                else:
                                    tokens.append(_Token(_TokenType.VALUE, value, start_pos))
                        else:
                            # "is" at end of input - treat as value
                            tokens.append(_Token(_TokenType.VALUE, value, start_pos))
                    else:
                        # Determine token type based on what comes next
                        self._skip_whitespace()
                        if self.pos < self.length and (
                            self._peek_operator() or self._peek_word_operator()
                        ):
                            tokens.append(_Token(_TokenType.FIELD, value, start_pos))
                        else:
                            tokens.append(_Token(_TokenType.VALUE, value, start_pos))

        return tokens


def _suggest_operator(unknown: str) -> str | None:
    """
    Suggest a similar operator for a misspelled word.

    Uses simple heuristics:
    1. Prefix match (at least 3 characters)
    2. Simple edit distance (1 character difference)

    Returns suggestion string or None.
    """
    unknown_lower = unknown.lower()

    # All known word operators
    known_operators = [
        *_Tokenizer.WORD_OPERATORS.keys(),
        "is null",
        "is not null",
        "is empty",
    ]

    # Check prefix match (at least 3 chars)
    if len(unknown_lower) >= 3:
        for op in known_operators:
            # Check if unknown is a prefix of operator
            if op.startswith(unknown_lower):
                return op
            # Check if operator is a prefix of unknown (e.g., "containsall" vs "contains_all")
            op_no_space = op.replace(" ", "").replace("_", "")
            unknown_no_sep = unknown_lower.replace("_", "")
            if op_no_space.startswith(unknown_no_sep[:3]):
                return op

    # Check for simple typos (1 char difference for short ops, 2 for longer)
    for op in known_operators:
        op_lower = op.lower()
        # Skip very different lengths
        if abs(len(unknown_lower) - len(op_lower)) > 2:
            continue
        # Simple character difference count (zip shorter strings, add length diff)
        diff = sum(1 for a, b in zip(unknown_lower, op_lower, strict=False) if a != b)
        diff += abs(len(unknown_lower) - len(op_lower))
        threshold = 2 if len(op_lower) > 4 else 1
        if diff <= threshold:
            return op

    return None


class _Parser:
    """Recursive descent parser for filter expressions."""

    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.pos = 0

    def _current(self) -> _Token:
        """Get current token."""
        return self.tokens[self.pos]

    def _advance(self) -> _Token:
        """Advance to next token and return previous."""
        token = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return token

    def _expect(self, token_type: _TokenType, context: str = "") -> _Token:
        """Expect a specific token type, raise if not found."""
        token = self._current()
        if token.type != token_type:
            ctx = f" {context}" if context else ""
            raise ValueError(
                f"Expected {token_type.name}{ctx} at position {token.pos}, "
                f"got {token.type.name} '{token.value}'"
            )
        return self._advance()

    def parse(self) -> FilterExpression:
        """Parse the token stream into a FilterExpression."""
        if self._current().type == _TokenType.EOF:
            raise ValueError("Empty filter expression")

        expr = self._parse_or_expr()

        if self._current().type != _TokenType.EOF:
            token = self._current()
            # Check if this looks like a multi-word value (extra word after comparison)
            if token.type in (_TokenType.VALUE, _TokenType.FIELD):
                token_val = token.value if isinstance(token.value, str) else str(token.value)
                # Check for SQL-like boolean keywords
                upper_val = token_val.upper()
                if upper_val == "AND":
                    raise ValueError(
                        f"Unexpected 'AND' at position {token.pos}. "
                        f"Hint: Use '&' for AND: expr1 & expr2"
                    )
                if upper_val == "OR":
                    raise ValueError(
                        f"Unexpected 'OR' at position {token.pos}. "
                        f"Hint: Use '|' for OR: expr1 | expr2"
                    )
                # Look back to find the previous value to suggest quoting
                # Collect remaining words
                remaining_words: list[str] = [token_val]
                pos = self.pos + 1
                while pos < len(self.tokens) - 1:
                    next_tok = self.tokens[pos]
                    if next_tok.type in (_TokenType.VALUE, _TokenType.FIELD):
                        next_val = (
                            next_tok.value
                            if isinstance(next_tok.value, str)
                            else str(next_tok.value)
                        )
                        remaining_words.append(next_val)
                        pos += 1
                    else:
                        break
                if len(remaining_words) == 1:
                    raise ValueError(
                        f"Unexpected token '{token_val}' at position {token.pos}. "
                        f'Hint: Values with spaces must be quoted: "... {token_val}"'
                    )
                else:
                    combined = " ".join(remaining_words)
                    raise ValueError(
                        f"Unexpected token '{token_val}' at position {token.pos}. "
                        f'Hint: Values with spaces must be quoted: "...{combined}"'
                    )
            raise ValueError(f"Unexpected token '{token.value}' at position {token.pos}")

        return expr

    def _parse_or_expr(self) -> FilterExpression:
        """Parse OR expressions (lowest precedence)."""
        left = self._parse_and_expr()

        while self._current().type == _TokenType.OR:
            self._advance()  # consume |
            right = self._parse_and_expr()
            left = OrExpression(left, right)

        return left

    def _parse_and_expr(self) -> FilterExpression:
        """Parse AND expressions (medium precedence)."""
        left = self._parse_not_expr()

        while self._current().type == _TokenType.AND:
            self._advance()  # consume &
            right = self._parse_not_expr()
            left = AndExpression(left, right)

        return left

    def _parse_not_expr(self) -> FilterExpression:
        """Parse NOT expressions (high precedence)."""
        if self._current().type == _TokenType.NOT:
            self._advance()  # consume !
            expr = self._parse_not_expr()  # NOT is right-associative
            return NotExpression(expr)

        return self._parse_atom()

    def _parse_atom(self) -> FilterExpression:
        """Parse atomic expressions: comparisons or parenthesized expressions."""
        token = self._current()

        # Parenthesized expression
        if token.type == _TokenType.LPAREN:
            self._advance()  # consume (
            expr = self._parse_or_expr()
            closing = self._current()
            if closing.type != _TokenType.RPAREN:
                raise ValueError(f"Unbalanced parentheses: expected ')' at position {closing.pos}")
            self._advance()  # consume )
            return expr

        # Field comparison
        if token.type == _TokenType.FIELD:
            return self._parse_comparison()

        # Error cases
        if token.type == _TokenType.EOF:
            raise ValueError("Unexpected end of expression")
        if token.type == _TokenType.OPERATOR:
            raise ValueError(
                f"Missing field name before operator '{token.value}' at position {token.pos}"
            )
        if token.type == _TokenType.VALUE:
            # This could be an unquoted field name that wasn't recognized
            # Try to parse it as a comparison
            return self._parse_comparison_from_value()

        raise ValueError(f"Unexpected token '{token.value}' at position {token.pos}")

    def _parse_comparison(self) -> FilterExpression:
        """Parse a field comparison expression."""
        field_token = self._expect(_TokenType.FIELD, "for field name")
        # Field names are always strings (not bracket lists)
        assert isinstance(field_token.value, str)
        field_name = field_token.value

        op_token = self._current()
        if op_token.type != _TokenType.OPERATOR:
            raise ValueError(
                f"Expected operator after field name at position {op_token.pos}, "
                f"got {op_token.type.name}"
            )
        self._advance()
        # Operators are always strings
        assert isinstance(op_token.value, str)
        operator = op_token.value

        value_token = self._current()
        if value_token.type not in (_TokenType.VALUE, _TokenType.FIELD):
            # Check for == instead of =
            if value_token.type == _TokenType.OPERATOR and value_token.value == "=":
                raise ValueError(
                    f"Unexpected '=' at position {value_token.pos}. "
                    f"Hint: Use single '=' for equality, not '=='"
                )
            raise ValueError(f"Expected value after operator at position {value_token.pos}")
        self._advance()

        # Convert value to appropriate type
        if value_token.value == "*":
            value: Any = RawToken("*")
        else:
            value = value_token.value

        return FieldComparison(field_name, operator, value)

    def _parse_comparison_from_value(self) -> FilterExpression:
        """Parse a comparison where the field was tokenized as VALUE."""
        # This happens when field name isn't followed by operator immediately
        value_token = self._advance()
        # Field names are always strings (not bracket lists)
        assert isinstance(value_token.value, str)
        field_name = value_token.value

        op_token = self._current()
        if op_token.type != _TokenType.OPERATOR:
            # Check if this looks like a multi-word field name (next token is word, not operator)
            # Note: the next word might be tokenized as FIELD if it's followed by an operator
            if op_token.type in (_TokenType.VALUE, _TokenType.FIELD):
                op_val = op_token.value if isinstance(op_token.value, str) else str(op_token.value)
                # Check if it looks like an unsupported operator (e.g., <>, >>, <<)
                if op_val in ("<>", ">>", "<<"):
                    raise ValueError(
                        f"Unsupported operator '{op_val}' at position {op_token.pos}. "
                        f"Supported operators: = != =~ =^ =$ > >= < <="
                    )

                # Check if this looks like a misspelled operator
                suggestion = _suggest_operator(op_val)
                if suggestion:
                    raise ValueError(
                        f"Unknown operator '{op_val}' at position {op_token.pos}. "
                        f"Did you mean: {suggestion}?"
                    )

                # Collect subsequent words to suggest the full field name
                words: list[str] = [field_name, op_val]
                pos = self.pos + 1
                while pos < len(self.tokens) - 1:
                    next_tok = self.tokens[pos]
                    if next_tok.type == _TokenType.OPERATOR:
                        break
                    if next_tok.type in (_TokenType.VALUE, _TokenType.FIELD):
                        next_val = (
                            next_tok.value
                            if isinstance(next_tok.value, str)
                            else str(next_tok.value)
                        )
                        # Skip unsupported operator-like tokens
                        if next_val in ("<>", ">>", "<<"):
                            break
                        words.append(next_val)
                        pos += 1
                    else:
                        break
                suggested_field = " ".join(words)
                raise ValueError(
                    f"Expected operator after '{field_name}' at position {op_token.pos}. "
                    f'Hint: For multi-word field names, use quotes: "{suggested_field}"'
                )
            raise ValueError(f"Expected operator after '{field_name}' at position {op_token.pos}")
        self._advance()
        # Operators are always strings
        assert isinstance(op_token.value, str)
        operator = op_token.value

        next_token = self._current()
        if next_token.type not in (_TokenType.VALUE, _TokenType.FIELD):
            # Check for == instead of =
            if next_token.type == _TokenType.OPERATOR and next_token.value == "=":
                raise ValueError(
                    f"Unexpected '=' at position {next_token.pos}. "
                    f"Hint: Use single '=' for equality, not '=='"
                )
            raise ValueError(f"Expected value after operator at position {next_token.pos}")
        self._advance()

        if next_token.value == "*":
            value: Any = RawToken("*")
        else:
            value = next_token.value

        return FieldComparison(field_name, operator, value)


def parse(filter_string: str) -> FilterExpression:
    """
    Parse a filter string into a FilterExpression AST.

    This function converts a human-readable filter string into a structured
    FilterExpression that can be used for client-side filtering with matches().

    Args:
        filter_string: The filter expression to parse

    Returns:
        A FilterExpression AST representing the filter

    Raises:
        ValueError: If the filter string is invalid

    Examples:
        >>> expr = parse('name = "Alice"')
        >>> expr.matches({"name": "Alice"})
        True

        >>> expr = parse('status = Active | status = Pending')
        >>> expr.matches({"status": "Active"})
        True

        >>> expr = parse('email = *')  # IS NOT NULL
        >>> expr.matches({"email": "test@example.com"})
        True

        >>> expr = parse('email != *')  # IS NULL
        >>> expr.matches({"email": None})
        True
    """
    if not filter_string or not filter_string.strip():
        raise ValueError("Empty filter expression")

    tokenizer = _Tokenizer(filter_string)
    tokens = tokenizer.tokenize()
    parser = _Parser(tokens)
    return parser.parse()
