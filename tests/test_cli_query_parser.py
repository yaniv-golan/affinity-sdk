"""Tests for the query parser."""

from __future__ import annotations

import pytest

from affinity.cli.query import (
    QueryParseError,
    QueryValidationError,
    parse_query,
    parse_query_from_file,
)


class TestParseQuery:
    """Tests for parse_query function."""

    @pytest.mark.req("QUERY-PARSE-001")
    def test_parse_simple_from_and_limit(self) -> None:
        """Parse simple query with from and limit."""
        result = parse_query({"from": "persons", "limit": 10})
        assert result.query.from_ == "persons"
        assert result.query.limit == 10

    @pytest.mark.req("QUERY-PARSE-001")
    def test_parse_with_version(self) -> None:
        """Parse query with explicit version."""
        result = parse_query({"$version": "1.0", "from": "companies", "limit": 5})
        assert result.query.version == "1.0"
        assert result.query.from_ == "companies"
        assert len(result.warnings) == 0

    @pytest.mark.req("QUERY-PARSE-001")
    def test_parse_warns_on_missing_version(self) -> None:
        """Warn when $version is missing."""
        result = parse_query({"from": "persons"})
        assert len(result.warnings) == 1
        assert "$version" in result.warnings[0]

    @pytest.mark.req("QUERY-PARSE-002")
    def test_parse_compound_where_and(self) -> None:
        """Parse compound WHERE with AND."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "fields.Status", "op": "eq", "value": "Active"},
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.and_ is not None
        assert len(result.query.where.and_) == 2

    @pytest.mark.req("QUERY-PARSE-002")
    def test_parse_compound_where_or(self) -> None:
        """Parse compound WHERE with OR."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "or": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "listId", "op": "eq", "value": 456},
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.or_ is not None
        assert len(result.query.where.or_) == 2

    @pytest.mark.req("QUERY-PARSE-003")
    def test_reject_invalid_operator(self) -> None:
        """Reject unknown operators."""
        with pytest.raises(QueryParseError) as exc:
            parse_query(
                {
                    "from": "persons",
                    "where": {"path": "name", "op": "like", "value": "%test%"},
                }
            )
        assert "like" in str(exc.value)

    @pytest.mark.req("QUERY-PARSE-003")
    def test_reject_unsupported_version(self) -> None:
        """Reject unsupported query version."""
        with pytest.raises(QueryParseError) as exc:
            parse_query({"$version": "99.0", "from": "persons"})
        assert "99.0" in str(exc.value)
        assert "Unsupported" in str(exc.value)

    @pytest.mark.req("QUERY-PARSE-004")
    def test_reject_aggregate_with_include(self) -> None:
        """Reject aggregate combined with include."""
        with pytest.raises(QueryValidationError) as exc:
            parse_query(
                {
                    "from": "persons",
                    "include": ["companies"],
                    "aggregate": {"total": {"count": True}},
                }
            )
        assert "aggregate" in str(exc.value).lower()

    @pytest.mark.req("QUERY-PARSE-005")
    def test_parse_quantifier_all(self) -> None:
        """Parse quantifier 'all'."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {
                            "all": {
                                "path": "interactions",
                                "where": {"path": "type", "op": "eq", "value": "MEETING"},
                            }
                        },
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.and_ is not None
        # Find the quantifier branch
        quant = next(b for b in result.query.where.and_ if b.all_ is not None)
        assert quant.all_ is not None
        assert quant.all_.path == "interactions"

    @pytest.mark.req("QUERY-PARSE-005")
    def test_parse_quantifier_none(self) -> None:
        """Parse quantifier 'none'."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {
                            "none": {
                                "path": "persons",
                                "where": {"path": "role", "op": "eq", "value": "CEO"},
                            }
                        },
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.and_ is not None
        quant = next(b for b in result.query.where.and_ if b.none_ is not None)
        assert quant.none_ is not None

    @pytest.mark.req("QUERY-PARSE-006")
    def test_parse_exists_subquery(self) -> None:
        """Parse EXISTS subquery."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {
                            "exists": {
                                "from": "interactions",
                                "via": "personId",
                                "where": {"path": "type", "op": "eq", "value": "MEETING"},
                            }
                        },
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.and_ is not None
        existsb = next(b for b in result.query.where.and_ if b.exists_ is not None)
        assert existsb.exists_ is not None
        assert existsb.exists_.from_ == "interactions"

    @pytest.mark.req("QUERY-PARSE-007")
    def test_parse_count_pseudo_field(self) -> None:
        """Parse _count pseudo-field in WHERE."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "companies._count", "op": "gte", "value": 2},
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.and_ is not None
        countb = next(b for b in result.query.where.and_ if b.path == "companies._count")
        assert countb.path == "companies._count"

    def test_parse_all_operators(self) -> None:
        """Ensure all supported operators parse correctly."""
        operators = [
            ("eq", "test"),
            ("neq", "test"),
            ("gt", 10),
            ("gte", 10),
            ("lt", 10),
            ("lte", 10),
            ("contains", "test"),
            ("starts_with", "test"),
            ("in", ["a", "b"]),
            ("between", [1, 10]),
            ("is_null", None),
            ("is_not_null", None),
            ("contains_any", ["a", "b"]),
            ("contains_all", ["a", "b"]),
        ]
        for op, value in operators:
            # Use listEntries with required listId filter plus the operator under test
            result = parse_query(
                {
                    "from": "listEntries",
                    "where": {
                        "and": [
                            {"path": "listId", "op": "eq", "value": 123},
                            {"path": "field", "op": op, "value": value},
                        ]
                    },
                }
            )
            assert result.query.where is not None
            assert result.query.where.and_ is not None
            opbranch = next(b for b in result.query.where.and_ if b.path == "field")
            assert opbranch.op == op

    def test_parse_between_requires_two_elements(self) -> None:
        """Between operator requires exactly two elements."""
        with pytest.raises(QueryValidationError) as exc:
            parse_query(
                {
                    "from": "persons",
                    "where": {"path": "age", "op": "between", "value": [1, 2, 3]},
                }
            )
        assert "between" in str(exc.value).lower()

    def test_parse_in_requires_array(self) -> None:
        """In operator requires array value."""
        with pytest.raises(QueryValidationError) as exc:
            parse_query(
                {
                    "from": "persons",
                    "where": {"path": "status", "op": "in", "value": "single_value"},
                }
            )
        assert "in" in str(exc.value).lower()

    def test_reject_unknown_entity(self) -> None:
        """Reject unknown entity types."""
        with pytest.raises(QueryValidationError) as exc:
            parse_query({"from": "unknownEntity"})
        assert "unknownEntity" in str(exc.value)

    def test_parse_with_order_by(self) -> None:
        """Parse query with orderBy."""
        result = parse_query(
            {
                "from": "persons",
                "orderBy": [
                    {"field": "lastName", "direction": "asc"},
                    {"field": "firstName", "direction": "desc"},
                ],
            }
        )
        assert result.query.order_by is not None
        assert len(result.query.order_by) == 2
        assert result.query.order_by[0].field == "lastName"
        assert result.query.order_by[0].direction == "asc"

    def test_parse_with_select(self) -> None:
        """Parse query with select fields."""
        result = parse_query(
            {
                "from": "persons",
                "select": ["firstName", "lastName", "email"],
            }
        )
        assert result.query.select is not None
        assert len(result.query.select) == 3

    def test_parse_with_include(self) -> None:
        """Parse query with includes."""
        result = parse_query(
            {
                "from": "persons",
                "include": ["companies", "opportunities"],
            }
        )
        assert result.query.include is not None
        assert len(result.query.include) == 2

    def test_parse_aggregate_count(self) -> None:
        """Parse aggregate with count."""
        result = parse_query(
            {
                "from": "persons",
                "aggregate": {"total": {"count": True}},
            }
        )
        assert result.query.aggregate is not None
        assert "total" in result.query.aggregate

    def test_parse_aggregate_sum(self) -> None:
        """Parse aggregate with sum."""
        result = parse_query(
            {
                "from": "opportunities",
                "aggregate": {"totalAmount": {"sum": "amount"}},
            }
        )
        assert result.query.aggregate is not None
        assert "totalAmount" in result.query.aggregate

    def test_parse_group_by(self) -> None:
        """Parse query with groupBy."""
        result = parse_query(
            {
                "from": "persons",
                "groupBy": "company",
                "aggregate": {"count": {"count": True}},
            }
        )
        assert result.query.group_by == "company"

    def test_reject_group_by_without_aggregate(self) -> None:
        """Reject groupBy without aggregate."""
        with pytest.raises(QueryValidationError) as exc:
            parse_query(
                {
                    "from": "persons",
                    "groupBy": "company",
                }
            )
        assert "groupBy" in str(exc.value)

    def test_reject_having_without_aggregate(self) -> None:
        """Reject having without aggregate."""
        with pytest.raises(QueryValidationError) as exc:
            parse_query(
                {
                    "from": "persons",
                    "having": {"path": "count", "op": "gt", "value": 5},
                }
            )
        assert "having" in str(exc.value)

    def test_parse_from_json_string(self) -> None:
        """Parse query from JSON string."""
        result = parse_query('{"from": "persons", "limit": 5}')
        assert result.query.from_ == "persons"
        assert result.query.limit == 5

    def test_reject_invalid_json(self) -> None:
        """Reject invalid JSON string."""
        with pytest.raises(QueryParseError) as exc:
            parse_query('{"from": "persons", limit: 5}')  # Missing quotes around limit
        assert "Invalid JSON" in str(exc.value)

    def test_parse_version_override(self) -> None:
        """Version override takes precedence."""
        result = parse_query(
            {"$version": "1.0", "from": "persons"},
            version_override="1.0",  # Same version, but explicitly overridden
        )
        assert result.query.version == "1.0"

    def test_parse_not_condition(self) -> None:
        """Parse NOT condition."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"not": {"path": "fields.Status", "op": "is_null"}},
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.and_ is not None
        notb = next(b for b in result.query.where.and_ if b.not_ is not None)
        assert notb.not_ is not None

    def test_warns_on_zero_limit(self) -> None:
        """Warn when limit is 0."""
        result = parse_query({"from": "persons", "limit": 0})
        assert any("limit=0" in w for w in result.warnings)

    def test_listentries_requires_listid_or_listname_error(self) -> None:
        """Error for listEntries without required filter shows both options."""
        with pytest.raises(QueryParseError) as exc:
            parse_query({"from": "listEntries"})

        error_msg = str(exc.value)
        # Error should mention both listId and listName as alternatives
        assert "listId" in error_msg
        assert "listName" in error_msg
        # Should show example for both
        assert "By ID:" in error_msg or "By name:" in error_msg


class TestParseQueryFromFile:
    """Tests for parse_query_from_file function."""

    def test_parse_from_file(self, tmp_path) -> None:
        """Parse query from a file."""
        query_file = tmp_path / "query.json"
        query_file.write_text('{"from": "persons", "limit": 10}')

        result = parse_query_from_file(str(query_file))
        assert result.query.from_ == "persons"
        assert result.query.limit == 10

    def test_parse_from_nonexistent_file(self, tmp_path) -> None:
        """Error on nonexistent file."""
        with pytest.raises(QueryParseError) as exc:
            parse_query_from_file(str(tmp_path / "nonexistent.json"))
        assert "Failed to read" in str(exc.value)


# =============================================================================
# Tests for OR Branch Validation
# =============================================================================


class TestOrBranchValidation:
    """Tests for validation of OR branches in REQUIRES_PARENT entities."""

    def test_or_branches_each_need_required_filter(self) -> None:
        """Each OR branch must have the required filter."""
        with pytest.raises(QueryParseError, match="OR branches"):
            parse_query(
                {
                    "from": "listEntries",
                    "where": {
                        "or": [
                            {"path": "listId", "op": "eq", "value": 123},
                            {"path": "status", "op": "eq", "value": "active"},  # Missing listId
                        ]
                    },
                }
            )

    def test_or_branches_all_have_required_filter_passes(self) -> None:
        """OR branches all having required filter passes validation."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "or": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "listId", "op": "eq", "value": 456},
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.or_ is not None

    def test_or_inside_and_with_required_filter_accepted(self) -> None:
        """OR inside AND that has required filter is valid.

        This is the key fix - when AND has listId, nested OR branches
        don't need their own listId filters.
        """
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 12345},
                        {
                            "or": [
                                {"path": "fields.Status", "op": "eq", "value": "Passed"},
                                {"path": "fields.Status", "op": "eq", "value": "Lost"},
                            ]
                        },
                    ]
                },
            }
        )
        assert result.query.where is not None
        assert result.query.where.and_ is not None

    def test_or_inside_and_without_required_filter_rejected(self) -> None:
        """OR inside AND without required filter is rejected.

        When the AND clause doesn't have the required filter (listId/listName),
        the query fails at the 'required filter missing' check.
        """
        with pytest.raises(QueryParseError, match=r"requires.*listId.*listName"):
            parse_query(
                {
                    "from": "listEntries",
                    "where": {
                        "and": [
                            {
                                "path": "status",
                                "op": "eq",
                                "value": "active",
                            },  # Not a required filter
                            {
                                "or": [
                                    {"path": "type", "op": "eq", "value": "A"},
                                    {"path": "type", "op": "eq", "value": "B"},
                                ]
                            },
                        ]
                    },
                }
            )

    def test_nested_or_each_branch_has_required(self) -> None:
        """Nested OR with each branch having required filter is valid."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "or": [
                        {
                            "and": [
                                {"path": "listId", "op": "eq", "value": 111},
                                {"path": "status", "op": "eq", "value": "active"},
                            ]
                        },
                        {
                            "and": [
                                {"path": "listId", "op": "eq", "value": 222},
                                {"path": "status", "op": "eq", "value": "inactive"},
                            ]
                        },
                    ]
                },
            }
        )
        assert result.query.where is not None

    def test_listname_accepted_as_required_filter(self) -> None:
        """listName is accepted as alternative to listId."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listName", "op": "eq", "value": "My Deals"},
            }
        )
        assert result.query.where is not None

    def test_or_with_mixed_listid_listname(self) -> None:
        """OR branches can mix listId and listName."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "or": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "listName", "op": "eq", "value": "My List"},
                    ]
                },
            }
        )
        assert result.query.where is not None


# =============================================================================
# Tests for Negated Required Filter Validation
# =============================================================================


class TestNegatedFilterValidation:
    """Tests for validation of negated required filters."""

    def test_not_listid_rejected(self) -> None:
        """NOT(listId=X) is rejected - would match all other lists."""
        with pytest.raises(QueryParseError, match=r"negate.*required"):
            parse_query(
                {
                    "from": "listEntries",
                    "where": {
                        "and": [
                            {"path": "listId", "op": "eq", "value": 123},
                            {"not": {"path": "listId", "op": "eq", "value": 456}},
                        ]
                    },
                }
            )

    def test_not_wrapping_listid_inside_and_rejected(self) -> None:
        """NOT containing listId is rejected even when nested."""
        with pytest.raises(QueryParseError, match=r"negate.*required"):
            parse_query(
                {
                    "from": "listEntries",
                    "where": {
                        "and": [
                            {"path": "listId", "op": "eq", "value": 123},
                            {
                                "not": {
                                    "and": [
                                        {"path": "listId", "op": "eq", "value": 456},
                                        {"path": "status", "op": "eq", "value": "x"},
                                    ]
                                }
                            },
                        ]
                    },
                }
            )

    def test_not_on_non_required_field_accepted(self) -> None:
        """NOT on non-required fields is allowed."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"not": {"path": "status", "op": "eq", "value": "deleted"}},
                    ]
                },
            }
        )
        assert result.query.where is not None


# =============================================================================
# Tests for Required Filter Operator Validation
# =============================================================================


class TestRequiredFilterOperators:
    """Tests for operator validation on required filters."""

    def test_eq_operator_allowed(self) -> None:
        """eq operator is allowed for listId."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 123},
            }
        )
        assert result.query.where is not None

    def test_in_operator_allowed(self) -> None:
        """in operator is allowed for listId (multi-list queries)."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "in", "value": [123, 456]},
            }
        )
        assert result.query.where is not None

    def test_invalid_operator_for_required_filter_rejected(self) -> None:
        """Operators other than eq/in are rejected for required filters."""
        with pytest.raises(QueryParseError, match="Invalid operator"):
            parse_query(
                {
                    "from": "listEntries",
                    "where": {"path": "listId", "op": "gt", "value": 100},
                }
            )

    def test_contains_operator_for_listname_rejected(self) -> None:
        """contains operator for listName is rejected."""
        with pytest.raises(QueryParseError, match="Invalid operator"):
            parse_query(
                {
                    "from": "listEntries",
                    "where": {"path": "listName", "op": "contains", "value": "Deal"},
                }
            )


# =============================================================================
# Tests for Extract Filter Fields
# =============================================================================


class TestExtractFilterFields:
    """Tests for extract_filter_fields helper."""

    def test_extracts_direct_path(self) -> None:
        """Extracts path from direct condition."""
        from affinity.cli.query.models import WhereClause
        from affinity.cli.query.parser import extract_filter_fields

        where = WhereClause(path="listId", op="eq", value=123)
        fields = extract_filter_fields(where)
        assert fields == {"listId"}

    def test_extracts_from_and(self) -> None:
        """Extracts paths from AND conditions."""
        from affinity.cli.query.models import WhereClause
        from affinity.cli.query.parser import extract_filter_fields

        where = WhereClause(
            and_=[
                WhereClause(path="listId", op="eq", value=123),
                WhereClause(path="status", op="eq", value="active"),
            ]
        )
        fields = extract_filter_fields(where)
        assert fields == {"listId", "status"}

    def test_extracts_from_or(self) -> None:
        """Extracts paths from OR conditions."""
        from affinity.cli.query.models import WhereClause
        from affinity.cli.query.parser import extract_filter_fields

        where = WhereClause(
            or_=[
                WhereClause(path="listId", op="eq", value=123),
                WhereClause(path="listId", op="eq", value=456),
            ]
        )
        fields = extract_filter_fields(where)
        assert fields == {"listId"}

    def test_does_not_extract_from_not(self) -> None:
        """Does not extract paths from inside NOT clause."""
        from affinity.cli.query.models import WhereClause
        from affinity.cli.query.parser import extract_filter_fields

        where = WhereClause(not_=WhereClause(path="listId", op="eq", value=123))
        fields = extract_filter_fields(where)
        # listId is inside NOT, so it's not extracted (negated filters don't satisfy requirements)
        assert fields == set()

    def test_extracts_none_returns_empty(self) -> None:
        """Returns empty set for None where clause."""
        from affinity.cli.query.parser import extract_filter_fields

        fields = extract_filter_fields(None)
        assert fields == set()


# =============================================================================
# Tests for Additional Parser Edge Cases
# =============================================================================


class TestParserEdgeCases:
    """Additional edge case tests for parser."""

    def test_condition_without_path_or_expr_rejected(self) -> None:
        """Condition must have path or expr."""
        with pytest.raises(QueryValidationError, match=r"path.*expr"):
            parse_query(
                {
                    "from": "persons",
                    "where": {"op": "eq", "value": "test"},  # Missing path/expr
                }
            )

    def test_value_required_for_comparison_operators(self) -> None:
        """Comparison operators require a value."""
        with pytest.raises(QueryValidationError, match=r"requires.*value"):
            parse_query(
                {
                    "from": "persons",
                    "where": {"path": "name", "op": "eq"},  # Missing value
                }
            )

    def test_is_null_does_not_require_value(self) -> None:
        """is_null operator doesn't require value."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "fields.Email", "op": "is_null"},
                    ]
                },
            }
        )
        assert result.query.where is not None

    def test_is_not_null_does_not_require_value(self) -> None:
        """is_not_null operator doesn't require value."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "fields.Email", "op": "is_not_null"},
                    ]
                },
            }
        )
        assert result.query.where is not None

    def test_invalid_select_path_rejected(self) -> None:
        """Empty or invalid select paths are rejected."""
        with pytest.raises(QueryValidationError, match="select"):
            parse_query(
                {
                    "from": "persons",
                    "select": ["name", "", "email"],  # Empty string
                }
            )

    def test_invalid_include_path_rejected(self) -> None:
        """Empty or invalid include paths are rejected."""
        with pytest.raises(QueryValidationError, match="include"):
            parse_query(
                {
                    "from": "persons",
                    "include": ["companies", ""],  # Empty string
                }
            )

    def test_negative_limit_rejected(self) -> None:
        """Negative limit is rejected."""
        with pytest.raises(QueryValidationError, match="limit"):
            parse_query({"from": "persons", "limit": -1})

    def test_exists_with_unknown_entity_rejected(self) -> None:
        """EXISTS with unknown entity type is rejected."""
        with pytest.raises(QueryValidationError, match=r"Unknown entity.*EXISTS"):
            parse_query(
                {
                    "from": "persons",
                    "where": {
                        "exists": {
                            "from": "unknownEntity",
                            "via": "personId",
                        }
                    },
                }
            )

    def test_query_not_dict_rejected(self) -> None:
        """Query must be a dict/object."""
        with pytest.raises(QueryParseError, match="must be a JSON object"):
            parse_query([1, 2, 3])  # type: ignore[arg-type]

    def test_query_missing_from_rejected(self) -> None:
        """Query must have 'from' field."""
        with pytest.raises(QueryParseError, match="must have a 'from' field"):
            parse_query({"limit": 10})

    def test_pydantic_single_validation_error(self) -> None:
        """Single Pydantic validation error is formatted properly."""
        with pytest.raises(QueryParseError):
            # Invalid orderBy format triggers a Pydantic validation error
            parse_query(
                {
                    "from": "persons",
                    "orderBy": "invalid",  # Should be a list
                }
            )

    def test_pydantic_multiple_validation_errors(self) -> None:
        """Multiple Pydantic validation errors are listed."""
        with pytest.raises(QueryParseError, match="Multiple validation errors"):
            # Multiple invalid fields trigger multiple errors
            parse_query(
                {
                    "from": "persons",
                    "orderBy": "invalid",
                    "aggregate": "invalid",
                }
            )


# =============================================================================
# Tests for ParseResult
# =============================================================================


class TestParseResult:
    """Tests for ParseResult class."""

    def test_version_property_with_version(self) -> None:
        """ParseResult.version returns query version when present."""
        result = parse_query({"$version": "1.0", "from": "persons"})
        assert result.version == "1.0"

    def test_version_property_without_version(self) -> None:
        """ParseResult.version returns default when query version is None."""
        result = parse_query({"from": "persons"})
        # Query was parsed without version, so query.version may be set by parser
        # but the property should return CURRENT_VERSION as fallback
        assert result.version == "1.0"


# =============================================================================
# Tests for Relationship-Only Entity Validation
# =============================================================================


class TestRelationshipOnlyEntities:
    """Tests for entities that can only be accessed via relationships."""

    def test_notes_cannot_be_queried_directly(self) -> None:
        """Notes entity cannot be queried directly."""
        with pytest.raises(QueryParseError, match="cannot be queried directly"):
            parse_query({"from": "notes"})

    def test_interactions_cannot_be_queried_directly(self) -> None:
        """Interactions entity cannot be queried directly."""
        with pytest.raises(QueryParseError, match="cannot be queried directly"):
            parse_query({"from": "interactions"})
