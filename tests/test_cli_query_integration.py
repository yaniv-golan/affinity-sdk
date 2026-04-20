"""Integration tests for query language.

These tests verify that entity queryability rules are enforced correctly
at parse time, ensuring users get helpful error messages.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from affinity.cli.query.exceptions import QueryParseError, QueryValidationError
from affinity.cli.query.parser import parse_query
from affinity.models.entities import Company, ListEntryWithEntity, Person


class TestEntityQueryability:
    """Test that entity queryability rules are enforced at parse time."""

    @pytest.mark.req("QUERY-INTEGRATION-001")
    def test_relationship_only_rejected_at_parse_time(self) -> None:
        """RELATIONSHIP_ONLY entities should fail at parse time."""
        with pytest.raises(QueryParseError, match="cannot be queried directly"):
            parse_query({"from": "interactions", "limit": 1})

        with pytest.raises(QueryParseError, match="cannot be queried directly"):
            parse_query({"from": "notes", "limit": 1})

    @pytest.mark.req("QUERY-INTEGRATION-001")
    def test_relationship_only_error_suggests_include(self) -> None:
        """RELATIONSHIP_ONLY error message should suggest using include."""
        with pytest.raises(QueryParseError, match=r'include.*"interactions"'):
            parse_query({"from": "interactions"})

    @pytest.mark.req("QUERY-INTEGRATION-002")
    def test_requires_parent_without_filter_rejected(self) -> None:
        """REQUIRES_PARENT entities without filter should fail at parse time."""
        with pytest.raises(QueryParseError, match=r"requires a 'list"):
            parse_query({"from": "listEntries", "limit": 1})

    @pytest.mark.req("QUERY-INTEGRATION-002")
    def test_requires_parent_with_invalid_operator_rejected(self) -> None:
        """REQUIRES_PARENT with invalid operator should fail at parse time."""
        with pytest.raises(QueryParseError, match="Invalid operator 'gt'"):
            parse_query(
                {
                    "from": "listEntries",
                    "where": {"path": "listId", "op": "gt", "value": 100},
                }
            )

    @pytest.mark.req("QUERY-INTEGRATION-002")
    def test_requires_parent_with_filter_accepted(self) -> None:
        """REQUIRES_PARENT entities with filter should parse successfully."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 123},
                "limit": 1,
            }
        )
        assert result.query.from_ == "listEntries"

    @pytest.mark.req("QUERY-INTEGRATION-002")
    def test_requires_parent_with_in_operator_accepted(self) -> None:
        """REQUIRES_PARENT entities with 'in' operator should parse successfully."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "in", "value": [123, 456]},
                "limit": 10,
            }
        )
        assert result.query.from_ == "listEntries"

    @pytest.mark.req("QUERY-INTEGRATION-002")
    def test_requires_parent_with_or_filter_accepted(self) -> None:
        """REQUIRES_PARENT entities with OR filter should parse successfully."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "or": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "listId", "op": "eq", "value": 456},
                    ]
                },
                "limit": 10,
            }
        )
        assert result.query.from_ == "listEntries"

    @pytest.mark.req("QUERY-INTEGRATION-002")
    def test_requires_parent_with_listname_accepted(self) -> None:
        """REQUIRES_PARENT entities with listName filter should parse successfully."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listName", "op": "eq", "value": "My Deals"},
                "limit": 1,
            }
        )
        assert result.query.from_ == "listEntries"

    @pytest.mark.req("QUERY-INTEGRATION-003")
    def test_requires_parent_negated_filter_rejected(self) -> None:
        """REQUIRES_PARENT with negated required filter should fail."""
        # Query has a valid listId filter but also negates another listId
        # The negation makes this query unbounded
        with pytest.raises(QueryParseError, match="Cannot negate required filter"):
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

    @pytest.mark.req("QUERY-INTEGRATION-003")
    def test_requires_parent_or_branch_missing_filter_rejected(self) -> None:
        """OR branch without required filter should fail."""
        with pytest.raises(QueryParseError, match="All OR branches must include"):
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

    @pytest.mark.req("QUERY-INTEGRATION-003")
    def test_or_inside_and_with_required_filter_accepted(self) -> None:
        """OR inside AND is valid when AND has the required filter."""
        # This pattern: AND [listId=X, OR[A, B]] should be valid because
        # the listId applies to the whole AND, covering the OR branches
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {
                            "or": [
                                {"path": "fields.Status", "op": "eq", "value": "Active"},
                                {"path": "fields.Status", "op": "eq", "value": "Passed"},
                            ]
                        },
                    ]
                },
            }
        )
        assert result.query.from_ == "listEntries"
        assert result.query.where is not None
        assert result.query.where.and_ is not None

    @pytest.mark.req("QUERY-INTEGRATION-004")
    def test_global_entities_parse_without_filters(self) -> None:
        """GLOBAL entities should parse without any filters."""
        for entity in ["persons", "companies", "opportunities", "lists"]:
            result = parse_query({"from": entity, "limit": 1})
            assert result.query.from_ == entity

    @pytest.mark.req("QUERY-INTEGRATION-004")
    def test_global_entities_reject_arbitrary_filters(self) -> None:
        """Post Task 6.1: global entities (persons/companies/opportunities) reject
        any where clause because the V2 API does not support server-side
        filtering and previous versions silently returned unfiltered results.
        """
        with pytest.raises(QueryValidationError, match="server-side filtering"):
            parse_query(
                {
                    "from": "persons",
                    "where": {"path": "firstName", "op": "eq", "value": "John"},
                    "limit": 10,
                }
            )


class TestModelSerialization:
    """Tests for model serialization matching query paths."""

    @pytest.mark.req("QUERY-INTEGRATION-005")
    def test_list_entry_model_dump_uses_camel_case(self) -> None:
        """Verify ListEntryWithEntity.model_dump(by_alias=True) produces camelCase."""
        entry = ListEntryWithEntity(
            id=1,
            list_id=123,
            created_at=datetime.now(timezone.utc),
            type="person",
            entity=None,
        )

        dumped = entry.model_dump(mode="json", by_alias=True)

        # Should have camelCase keys matching query language
        assert "listId" in dumped, "Expected 'listId' (camelCase), got 'list_id'"
        assert "createdAt" in dumped, "Expected 'createdAt' (camelCase), got 'created_at'"
        assert "list_id" not in dumped
        assert "created_at" not in dumped

    @pytest.mark.req("QUERY-INTEGRATION-005")
    def test_person_model_dump_uses_camel_case(self) -> None:
        """Verify Person.model_dump(by_alias=True) produces camelCase."""
        person = Person(
            id=1,
            first_name="John",
            last_name="Doe",
            primary_email="john@example.com",
        )

        dumped = person.model_dump(mode="json", by_alias=True)

        # Should have camelCase keys matching query language
        assert "firstName" in dumped, "Expected 'firstName' (camelCase)"
        assert "lastName" in dumped, "Expected 'lastName' (camelCase)"
        # Note: Person model uses alias "primaryEmailAddress" not "primaryEmail"
        assert "primaryEmailAddress" in dumped, "Expected 'primaryEmailAddress' (camelCase)"
        assert "first_name" not in dumped
        assert "last_name" not in dumped
        assert "primary_email" not in dumped

    @pytest.mark.req("QUERY-INTEGRATION-005")
    def test_company_model_dump_uses_camel_case(self) -> None:
        """Verify Company.model_dump(by_alias=True) produces camelCase."""
        company = Company(
            id=1,
            name="Acme Corp",
        )

        dumped = company.model_dump(mode="json", by_alias=True)

        # Should have camelCase keys matching query language
        assert "id" in dumped
        assert "name" in dumped


class TestIncludeOutputIntegration:
    """Integration tests for include display in different output formats.

    Tests verify that included data is correctly rendered in table and JSON outputs.
    The default include_style is "inline" which merges included data into parent rows.
    """

    @pytest.mark.req("QUERY-INCLUDE-OUTPUT-001")
    def test_include_data_renders_inline_by_default(self) -> None:
        """Test that included data appears inline by default (merged into parent rows)."""
        from affinity.cli.query.models import QueryResult
        from affinity.cli.query.output import format_table

        # Create result with included data AND included_by_parent for inline expansion
        result = QueryResult(
            data=[
                {"id": 1, "firstName": "Alice", "organizationIds": [100, 101]},
                {"id": 2, "firstName": "Bob", "organizationIds": [100]},
            ],
            included={
                "companies": [
                    {"id": 100, "name": "Acme Corp", "domain": "acme.com"},
                    {"id": 101, "name": "Beta Inc", "domain": "beta.io"},
                ]
            },
            included_by_parent={
                "companies": {
                    1: [
                        {"id": 100, "name": "Acme Corp", "domain": "acme.com"},
                        {"id": 101, "name": "Beta Inc", "domain": "beta.io"},
                    ],
                    2: [{"id": 100, "name": "Acme Corp", "domain": "acme.com"}],
                }
            },
        )

        output = format_table(result)  # Default is inline

        # Main data should be in output
        assert "Alice" in output
        assert "Bob" in output

        # Included data should appear as inline column with display values
        # Note: Rich may truncate column headers, so check for partial match
        assert "included.compan" in output  # Column header (may be truncated)
        assert "Acme Corp" in output
        # Note: Rich may wrap "Beta Inc" across lines, so check for "Beta" separately
        assert "Beta" in output

        # Should NOT have separate tables (inline mode)
        assert "Included: companies" not in output

    @pytest.mark.req("QUERY-INCLUDE-OUTPUT-001b")
    def test_include_data_renders_as_separate_table_with_style(self) -> None:
        """Test that included data appears as separate table with include_style='separate'."""
        from affinity.cli.query.models import QueryResult
        from affinity.cli.query.output import format_table

        # Create result with included data
        result = QueryResult(
            data=[
                {"id": 1, "firstName": "Alice", "organizationIds": [100, 101]},
                {"id": 2, "firstName": "Bob", "organizationIds": [100]},
            ],
            included={
                "companies": [
                    {"id": 100, "name": "Acme Corp", "domain": "acme.com"},
                    {"id": 101, "name": "Beta Inc", "domain": "beta.io"},
                ]
            },
        )

        output = format_table(result, include_style="separate")

        # Main data should be in output
        assert "Alice" in output
        assert "Bob" in output

        # Included data should appear as separate table
        assert "Included: companies" in output
        assert "Acme Corp" in output
        assert "Beta Inc" in output

    @pytest.mark.req("QUERY-INCLUDE-OUTPUT-002")
    def test_include_data_appears_in_json_output(self) -> None:
        """Test that included data appears in JSON output."""
        import json

        from affinity.cli.query.models import QueryResult
        from affinity.cli.query.output import format_json

        result = QueryResult(
            data=[{"id": 1, "name": "Alice"}],
            included={
                "companies": [
                    {"id": 100, "name": "Acme Corp"},
                ]
            },
        )

        output = format_json(result)
        parsed = json.loads(output)

        # JSON output should have both data and included
        assert "data" in parsed
        assert "included" in parsed
        assert len(parsed["included"]["companies"]) == 1
        assert parsed["included"]["companies"][0]["name"] == "Acme Corp"

    @pytest.mark.req("QUERY-INCLUDE-OUTPUT-003")
    def test_multiple_includes_render_as_separate_tables(self) -> None:
        """Test that multiple include types each get their own table (with separate style)."""
        from affinity.cli.query.models import QueryResult
        from affinity.cli.query.output import format_table

        result = QueryResult(
            data=[{"id": 1, "name": "Acme Corp"}],
            included={
                "persons": [
                    {"id": 200, "firstName": "Alice", "lastName": "Smith"},
                ],
                "opportunities": [
                    {"id": 300, "name": "Big Deal", "status": "Active"},
                ],
            },
        )

        output = format_table(result, include_style="separate")

        # Each relationship should have its own section
        assert "Included: persons" in output
        assert "Included: opportunities" in output
        assert "Alice" in output
        assert "Big Deal" in output

    @pytest.mark.req("QUERY-INCLUDE-OUTPUT-004")
    def test_empty_includes_produce_no_extra_output(self) -> None:
        """Test that empty included data doesn't add extra output."""
        from affinity.cli.query.models import QueryResult
        from affinity.cli.query.output import format_table

        result = QueryResult(
            data=[{"id": 1, "name": "Alice"}],
            included={},  # Empty includes
        )

        output = format_table(result)

        # Should have main data but no "Included:" sections
        assert "Alice" in output
        assert "Included:" not in output
