"""Tests for query executor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from affinity.cli.query import (
    QueryExecutionError,
    QuerySafetyLimitError,
    QueryTimeoutError,
)
from affinity.cli.query.executor import (
    ExecutionContext,
    NullProgressCallback,
    QueryExecutor,
    QueryProgressCallback,
    _normalize_list_entry_fields,
    can_use_streaming,
    execute_query,
)
from affinity.cli.query.models import (
    AggregateFunc,
    ExecutionPlan,
    OrderByClause,
    PlanStep,
    Query,
    WhereClause,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock AsyncAffinity client."""
    client = AsyncMock()
    client.whoami = AsyncMock(return_value={"id": 1, "email": "test@test.com"})
    return client


def create_mock_record(data: dict) -> MagicMock:
    """Create a mock record with proper model_dump."""
    record = MagicMock()
    record.model_dump = MagicMock(return_value=data)
    return record


def create_mock_page_iterator(records: list[dict]):
    """Create a mock page iterator for testing."""

    class MockPageIterator:
        def pages(self, on_progress=None):
            async def generator():
                page = MagicMock()
                page.data = [create_mock_record(r) for r in records]
                if on_progress:
                    from affinity.models.pagination import PaginationProgress

                    on_progress(
                        PaginationProgress(
                            page_number=1,
                            items_in_page=len(records),
                            items_so_far=len(records),
                            has_next=False,
                        )
                    )
                yield page

            return generator()

    return MockPageIterator()


@pytest.fixture
def mock_service() -> MagicMock:
    """Create a mock service with paginated results."""
    service = MagicMock()

    records = [
        {"id": 1, "name": "Alice", "email": "alice@test.com"},
        {"id": 2, "name": "Bob", "email": "bob@test.com"},
    ]
    service.all.return_value = create_mock_page_iterator(records)
    return service


@pytest.fixture
def simple_query() -> Query:
    """Create a simple query for testing."""
    return Query(from_="persons", limit=10)


@pytest.fixture
def simple_plan(simple_query: Query) -> ExecutionPlan:
    """Create a simple execution plan."""
    return ExecutionPlan(
        query=simple_query,
        steps=[
            PlanStep(
                step_id=0,
                operation="fetch",
                entity="persons",
                description="Fetch persons",
                estimated_api_calls=1,
            ),
            PlanStep(
                step_id=1,
                operation="limit",
                description="Limit to 10",
                depends_on=[0],
            ),
        ],
        total_api_calls=1,
        estimated_records_fetched=10,
        estimated_memory_mb=0.01,
        warnings=[],
        recommendations=[],
        has_expensive_operations=False,
        requires_full_scan=False,
    )


# =============================================================================
# can_use_streaming Tests
# =============================================================================


class TestCanUseStreaming:
    """Tests for can_use_streaming function."""

    def test_with_limit_returns_true(self) -> None:
        """Returns True when query has explicit limit."""
        query = Query(from_="persons", limit=10)
        assert can_use_streaming(query) is True

    def test_without_limit_returns_false(self) -> None:
        """Returns False when query has no limit and max_records_explicit=False."""
        query = Query(from_="persons")
        assert can_use_streaming(query) is False

    def test_with_max_records_explicit_returns_true(self) -> None:
        """Returns True when max_records_explicit=True even without query limit."""
        query = Query(from_="persons")
        assert can_use_streaming(query, max_records_explicit=True) is True

    def test_with_order_by_returns_false(self) -> None:
        """Returns False when query has orderBy (needs all records for sorting)."""
        query = Query(
            from_="persons",
            limit=10,
            order_by=[OrderByClause(field="name", direction="asc")],
        )
        assert can_use_streaming(query) is False

    def test_with_order_by_and_max_records_explicit_returns_false(self) -> None:
        """Returns False when query has orderBy even with max_records_explicit."""
        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="name", direction="asc")],
        )
        assert can_use_streaming(query, max_records_explicit=True) is False

    def test_with_aggregate_returns_false(self) -> None:
        """Returns False when query has aggregate (needs all records)."""
        query = Query(
            from_="persons",
            limit=10,
            aggregate={"total": {"count": "*"}},
        )
        assert can_use_streaming(query) is False

    def test_with_group_by_returns_false(self) -> None:
        """Returns False when query has groupBy (needs all records)."""
        query = Query(
            from_="persons",
            limit=10,
            group_by="type",
        )
        assert can_use_streaming(query) is False


# =============================================================================
# ExecutionContext Tests
# =============================================================================


class TestExecutionContext:
    """Tests for ExecutionContext."""

    def test_check_timeout_no_error(self, simple_query: Query) -> None:
        """No error when within timeout."""
        ctx = ExecutionContext(query=simple_query)
        # Should not raise
        ctx.check_timeout(300.0)

    def test_check_timeout_raises(self, simple_query: Query) -> None:
        """Raises QueryTimeoutError when exceeded."""
        ctx = ExecutionContext(query=simple_query)
        ctx.start_time = 0  # Started at epoch
        with pytest.raises(QueryTimeoutError) as exc:
            ctx.check_timeout(1.0)
        assert "exceeded timeout" in str(exc.value)

    def test_check_max_records_no_error(self, simple_query: Query) -> None:
        """No error when under limit."""
        ctx = ExecutionContext(query=simple_query, max_records=100)
        ctx.records = [{"id": i} for i in range(50)]
        # Should not raise
        ctx.check_max_records()

    def test_check_max_records_raises(self, simple_query: Query) -> None:
        """Raises QuerySafetyLimitError when exceeded."""
        ctx = ExecutionContext(query=simple_query, max_records=10)
        ctx.records = [{"id": i} for i in range(10)]
        with pytest.raises(QuerySafetyLimitError) as exc:
            ctx.check_max_records()
        assert "10 records" in str(exc.value)

    def test_build_result(self, simple_query: Query) -> None:
        """Builds QueryResult correctly."""
        ctx = ExecutionContext(query=simple_query)
        ctx.records = [{"id": 1}, {"id": 2}]
        ctx.included = {"companies": [{"id": 10}]}

        result = ctx.build_result()

        assert len(result.data) == 2
        assert result.included == {"companies": [{"id": 10}]}
        # Summary contains row count and included counts
        assert result.summary is not None
        assert result.summary.total_rows == 2
        assert result.summary.included_counts == {"companies": 1}


class TestSelectProjection:
    """Tests for select clause projection in build_result."""

    def test_no_select_returns_all_fields(self) -> None:
        """When select is None, all fields are returned."""
        query = Query(from_="persons")
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {"id": 1, "firstName": "John", "lastName": "Doe", "email": "john@example.com"}
        ]

        result = ctx.build_result()

        assert result.data == [
            {"id": 1, "firstName": "John", "lastName": "Doe", "email": "john@example.com"}
        ]

    def test_simple_field_projection(self) -> None:
        """Projects simple top-level fields."""
        query = Query(from_="persons", select=["id", "firstName"])
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {"id": 1, "firstName": "John", "lastName": "Doe", "email": "john@example.com"}
        ]

        result = ctx.build_result()

        assert result.data == [{"id": 1, "firstName": "John"}]

    def test_nested_field_projection(self) -> None:
        """Projects nested fields like fields.Status."""
        query = Query(from_="listEntries", select=["id", "fields.Status"])
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {"id": 1, "entityId": 100, "fields": {"Status": "Active", "Priority": "High"}}
        ]

        result = ctx.build_result()

        assert result.data == [{"id": 1, "fields": {"Status": "Active"}}]

    def test_fields_wildcard_projection(self) -> None:
        """Projects fields.* wildcard includes all custom fields."""
        query = Query(from_="listEntries", select=["id", "fields.*"])
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {
                "id": 1,
                "entityId": 100,
                "entityType": "person",
                "fields": {"Status": "Active", "Priority": "High"},
            }
        ]

        result = ctx.build_result()

        assert result.data == [{"id": 1, "fields": {"Status": "Active", "Priority": "High"}}]

    def test_mixed_projection(self) -> None:
        """Projects mix of simple and nested fields."""
        query = Query(from_="listEntries", select=["id", "entityId", "fields.Status"])
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {
                "id": 1,
                "entityId": 100,
                "entityType": "person",
                "listId": 5,
                "fields": {"Status": "New", "Owner": "Jane"},
            }
        ]

        result = ctx.build_result()

        assert result.data == [{"id": 1, "entityId": 100, "fields": {"Status": "New"}}]

    def test_missing_field_included_as_null(self) -> None:
        """Fields that don't exist in record are included as null when explicitly selected."""
        query = Query(from_="persons", select=["id", "nonexistent"])
        ctx = ExecutionContext(query=query)
        ctx.records = [{"id": 1, "firstName": "John"}]

        result = ctx.build_result()

        # Explicitly selected fields appear even if null
        assert result.data == [{"id": 1, "nonexistent": None}]

    def test_null_field_included_when_selected(self) -> None:
        """Null field values are included when explicitly selected."""
        query = Query(from_="listEntries", select=["id", "entityName", "fields.Status"])
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {"id": 1, "entityName": "Acme", "fields": {"Status": None, "Priority": "High"}}
        ]

        result = ctx.build_result()

        # Status is null but should appear since explicitly selected
        assert result.data == [{"id": 1, "entityName": "Acme", "fields": {"Status": None}}]

    def test_multiple_records_projection(self) -> None:
        """Projects all records in result."""
        query = Query(from_="persons", select=["id", "email"])
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {"id": 1, "firstName": "John", "email": "john@example.com"},
            {"id": 2, "firstName": "Jane", "email": "jane@example.com"},
            {"id": 3, "firstName": "Bob", "email": "bob@example.com"},
        ]

        result = ctx.build_result()

        assert result.data == [
            {"id": 1, "email": "john@example.com"},
            {"id": 2, "email": "jane@example.com"},
            {"id": 3, "email": "bob@example.com"},
        ]

    def test_no_select_returns_full_records(self) -> None:
        """Without select clause, returns full records without projection."""
        query = Query(from_="listEntries")  # No select
        ctx = ExecutionContext(query=query)
        ctx.records = [{"id": 1, "entityName": "Acme", "listId": 5, "fields": {"Status": None}}]

        result = ctx.build_result()

        # Full record returned as-is, no projection applied
        assert result.data == [
            {"id": 1, "entityName": "Acme", "listId": 5, "fields": {"Status": None}}
        ]

    def test_expand_preserved_when_select_specified(self) -> None:
        """Expansions are automatically included in output even when select filters other fields."""
        query = Query(
            from_="listEntries",
            select=["id", "fields.Status"],
            expand=["interactionDates", "unreplied"],
        )
        ctx = ExecutionContext(query=query)
        ctx.records = [
            {
                "id": 1,
                "entityId": 100,
                "fields": {"Status": "New", "Owner": "Jane"},
                "interactionDates": {"lastEmail": {"date": "2026-01-15"}},
                "unreplied": [{"subject": "Follow up"}],
            }
        ]

        result = ctx.build_result()

        # Select filters entityId and fields.Owner, but expansions are preserved
        assert result.data == [
            {
                "id": 1,
                "fields": {"Status": "New"},
                "interactionDates": {"lastEmail": {"date": "2026-01-15"}},
                "unreplied": [{"subject": "Follow up"}],
            }
        ]


class TestNormalizeListEntryFields:
    """Tests for _normalize_list_entry_fields function.

    The function normalizes FieldValues format (from model_dump) to simple dict
    keyed by field name. Field data is located at entity.fields.data (not at
    top-level fields).

    Input format::

        {"entity": {"fields": {"data": {"field-123": {"name": "Status", ...}}}}}

    Output format: {"fields": {"Status": "Active"}}
    """

    def test_normalizes_dropdown_field(self) -> None:
        """Normalizes dropdown field with text value."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-123": {
                            "id": "field-123",
                            "name": "Status",
                            "value": {"data": {"text": "Active", "id": 123}},
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Status": "Active"}

    def test_normalizes_simple_field(self) -> None:
        """Normalizes simple field with direct data value."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-456": {"id": "field-456", "name": "Amount", "value": {"data": 50000}}
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Amount": 50000}

    def test_normalizes_multiple_fields(self) -> None:
        """Normalizes multiple fields from entity.fields.data."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Status",
                            "value": {"data": {"text": "New"}},
                        },
                        "field-2": {
                            "id": "field-2",
                            "name": "Priority",
                            "value": {"data": {"text": "High"}},
                        },
                        "field-3": {"id": "field-3", "name": "Amount", "value": {"data": 10000}},
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Status": "New", "Priority": "High", "Amount": 10000}

    def test_handles_null_value(self) -> None:
        """Handles field with null value."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {"field-123": {"id": "field-123", "name": "Status", "value": None}},
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Status": None}

    def test_handles_multi_select(self) -> None:
        """Handles multi-select field with array of values."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-789": {
                            "id": "field-789",
                            "name": "Tags",
                            "value": {
                                "data": [
                                    {"text": "VIP", "id": 1},
                                    {"text": "Priority", "id": 2},
                                ]
                            },
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Tags": ["VIP", "Priority"]}

    def test_normalizes_person_reference_field(self) -> None:
        """Normalizes person reference field to display name."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Owner",
                            "value": {"data": {"firstName": "Jane", "lastName": "Doe", "id": 123}},
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Owner": "Jane Doe"}

    def test_normalizes_company_reference_field(self) -> None:
        """Normalizes company reference field to name."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Account",
                            "value": {
                                "data": {"name": "Acme Corp", "id": 456, "domain": "acme.com"}
                            },
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Account": "Acme Corp"}

    def test_normalizes_multi_select_person_field(self) -> None:
        """Normalizes multi-select person field to list of names."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Team Members",
                            "value": {
                                "data": [
                                    {"firstName": "Jane", "lastName": "Doe"},
                                    {"firstName": "John", "lastName": "Smith"},
                                ]
                            },
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Team Members": ["Jane Doe", "John Smith"]}

    def test_normalizes_multi_select_company_field(self) -> None:
        """Normalizes multi-select company field to list of names."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Partner Companies",
                            "value": {
                                "data": [
                                    {"name": "Acme Corp", "id": 1},
                                    {"name": "TechStart", "id": 2},
                                ]
                            },
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Partner Companies": ["Acme Corp", "TechStart"]}

    def test_normalizes_person_with_first_name_only(self) -> None:
        """Handles person with only first name."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Owner",
                            "value": {"data": {"firstName": "Jane", "id": 123}},
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Owner": "Jane"}

    def test_normalizes_person_with_last_name_only(self) -> None:
        """Handles person with only last name."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Owner",
                            "value": {"data": {"lastName": "Doe", "id": 123}},
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Owner": "Doe"}

    def test_normalizes_person_with_empty_names(self) -> None:
        """Handles person with empty/missing names."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Owner",
                            "value": {"data": {"firstName": "", "lastName": "", "id": 123}},
                        }
                    },
                },
            },
        }

        result = _normalize_list_entry_fields(record)

        assert result["fields"] == {"Owner": None}

    def test_adds_aliases_without_entity(self) -> None:
        """Adds listEntryId and entityType aliases even without entity."""
        record = {"id": 1, "type": "company", "firstName": "John"}

        result = _normalize_list_entry_fields(record)

        assert result["listEntryId"] == 1
        assert result["entityType"] == "company"
        assert result["fields"] == {}  # Always added

    def test_adds_entity_aliases(self) -> None:
        """Adds entityId and entityName from entity object."""
        record = {"id": 1, "type": "company", "entity": {"id": 100, "name": "Acme Corp"}}

        result = _normalize_list_entry_fields(record)

        assert result["listEntryId"] == 1
        assert result["entityId"] == 100
        assert result["entityName"] == "Acme Corp"
        assert result["entityType"] == "company"
        assert result["fields"] == {}

    def test_adds_entity_aliases_for_person(self) -> None:
        """Adds entityName computed from firstName/lastName for Person entities."""
        record = {
            "id": 1,
            "type": "person",
            "entity": {"id": 100, "firstName": "Jane", "lastName": "Doe"},
        }

        result = _normalize_list_entry_fields(record)

        assert result["listEntryId"] == 1
        assert result["entityId"] == 100
        assert result["entityName"] == "Jane Doe"
        assert result["entityType"] == "person"

    def test_adds_entity_aliases_for_person_first_name_only(self) -> None:
        """Handles Person with only firstName."""
        record = {"id": 1, "type": "person", "entity": {"id": 100, "firstName": "Jane"}}

        result = _normalize_list_entry_fields(record)

        assert result["entityName"] == "Jane"

    def test_adds_entity_aliases_for_person_last_name_only(self) -> None:
        """Handles Person with only lastName."""
        record = {"id": 1, "type": "person", "entity": {"id": 100, "lastName": "Doe"}}

        result = _normalize_list_entry_fields(record)

        assert result["entityName"] == "Doe"

    def test_adds_entity_aliases_for_person_empty_names(self) -> None:
        """Returns None when Person has empty firstName and lastName."""
        record = {
            "id": 1,
            "type": "person",
            "entity": {"id": 100, "firstName": "", "lastName": ""},
        }

        result = _normalize_list_entry_fields(record)

        assert result["entityName"] is None

    def test_adds_aliases_without_fields_data(self) -> None:
        """Adds aliases even when entity.fields.data doesn't exist."""
        record = {"id": 1, "entity": {"id": 100, "name": "Test", "fields": {"requested": False}}}

        result = _normalize_list_entry_fields(record)

        assert result["listEntryId"] == 1
        assert result["entityId"] == 100
        assert result["entityName"] == "Test"
        assert result["fields"] == {}

    def test_fields_key_always_exists(self) -> None:
        """Fields key is always present, defaulting to empty dict."""
        record = {"id": 1, "entity": {"id": 100, "fields": {"requested": True, "data": {}}}}

        result = _normalize_list_entry_fields(record)

        # fields key always exists, even if no custom fields
        assert result["fields"] == {}


# =============================================================================
# QueryExecutor Tests
# =============================================================================


class TestQueryExecutor:
    """Tests for QueryExecutor."""

    @pytest.mark.req("QUERY-EXEC-001")
    @pytest.mark.asyncio
    async def test_execute_simple_fetch_and_limit(
        self, mock_client: AsyncMock, mock_service: AsyncMock, simple_plan: ExecutionPlan
    ) -> None:
        """Execute simple fetch + limit query."""
        mock_client.persons = mock_service

        executor = QueryExecutor(mock_client, max_records=100)
        result = await executor.execute(simple_plan)

        assert len(result.data) == 2
        assert result.data[0]["name"] == "Alice"
        mock_client.whoami.assert_called_once()

    @pytest.mark.req("QUERY-EXEC-002")
    @pytest.mark.asyncio
    async def test_execute_client_side_filtering(
        self, mock_client: AsyncMock, mock_service: AsyncMock
    ) -> None:
        """Execute query with client-side filtering."""
        mock_client.persons = mock_service

        query = Query(
            from_="persons",
            where=WhereClause(path="name", op="eq", value="Alice"),
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=1,
                ),
                PlanStep(step_id=1, operation="filter", description="Filter", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should only have Alice after filtering
        assert len(result.data) == 1
        assert result.data[0]["name"] == "Alice"

    @pytest.mark.req("QUERY-EXEC-002")
    @pytest.mark.asyncio
    async def test_execute_fetch_streaming_operation(
        self, mock_client: AsyncMock, mock_service: AsyncMock
    ) -> None:
        """Execute plan with fetch_streaming operation (used for client-side filters).

        Regression test: The planner sets operation="fetch_streaming" for queries
        with client-side filters. The executor must route this to _execute_fetch(),
        not silently skip it.
        """
        mock_client.persons = mock_service

        query = Query(
            from_="persons",
            where=WhereClause(path="name", op="eq", value="Alice"),
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch_streaming",  # Key: test the streaming operation
                    entity="persons",
                    description="Fetch persons (paginated, client-side filter)",
                    estimated_api_calls=1,
                ),
                PlanStep(step_id=1, operation="filter", description="Filter", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # fetch_streaming must be handled - records should be fetched and filtered
        assert len(result.data) == 1
        assert result.data[0]["name"] == "Alice"

    @pytest.mark.req("QUERY-EXEC-004")
    @pytest.mark.asyncio
    async def test_execute_aggregations(self, mock_client: AsyncMock) -> None:
        """Execute query with aggregation."""
        # Create service that returns records with amounts
        service = MagicMock()
        records = [
            {"id": 1, "amount": 100},
            {"id": 2, "amount": 200},
            {"id": 3, "amount": 300},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.opportunities = service

        query = Query(
            from_="opportunities",
            aggregate={"total": AggregateFunc(sum="amount"), "count": AggregateFunc(count=True)},
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="opportunities",
                    description="Fetch",
                    estimated_api_calls=1,
                ),
                PlanStep(step_id=1, operation="aggregate", description="Aggregate", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        assert len(result.data) == 1
        assert result.data[0]["total"] == 600
        assert result.data[0]["count"] == 3

    @pytest.mark.req("QUERY-EXEC-005")
    @pytest.mark.asyncio
    async def test_reports_progress_callbacks(
        self, mock_client: AsyncMock, mock_service: AsyncMock
    ) -> None:
        """Progress callbacks are invoked."""
        mock_client.persons = mock_service

        # Use query with order_by to force non-streaming mode (tests step-by-step path)
        query = Query(
            from_="persons", limit=10, order_by=[OrderByClause(field="name", direction="asc")]
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch persons",
                    estimated_api_calls=1,
                ),
                PlanStep(
                    step_id=1,
                    operation="sort",
                    description="Sort by name",
                    depends_on=[0],
                ),
                PlanStep(
                    step_id=2,
                    operation="limit",
                    description="Limit to 10",
                    depends_on=[1],
                ),
            ],
            total_api_calls=1,
            estimated_records_fetched=10,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        progress = MagicMock(spec=QueryProgressCallback)

        executor = QueryExecutor(mock_client, progress=progress)
        await executor.execute(plan)

        # Should have called on_step_start for each step
        assert progress.on_step_start.call_count == 3
        # Should have called on_step_complete for each step
        assert progress.on_step_complete.call_count == 3

    @pytest.mark.req("QUERY-EXEC-007")
    @pytest.mark.asyncio
    async def test_enforce_max_records_limit(self, mock_client: AsyncMock) -> None:
        """Stops fetching when max_records reached."""
        # Create service that returns many records across multiple pages
        service = MagicMock()

        class MultiPageIterator:
            def pages(self, on_progress=None):  # noqa: ARG002
                async def generator():
                    for i in range(10):
                        page = MagicMock()
                        page.data = [create_mock_record({"id": i * 10 + j}) for j in range(10)]
                        yield page

                return generator()

        service.all.return_value = MultiPageIterator()
        mock_client.persons = service

        query = Query(from_="persons")
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=10,
                ),
            ],
            total_api_calls=10,
            estimated_records_fetched=100,
            estimated_memory_mb=0.1,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=True,
        )

        executor = QueryExecutor(mock_client, max_records=25)
        result = await executor.execute(plan)

        # Should stop at max_records
        assert len(result.data) <= 25

    @pytest.mark.req("QUERY-EXEC-007b")
    @pytest.mark.asyncio
    async def test_max_records_with_filter_fetches_all_then_truncates(
        self, mock_client: AsyncMock
    ) -> None:
        """max_records doesn't stop fetch when filter exists, but truncates after."""
        # Create service that returns 50 records, but only last 10 match filter
        service = MagicMock()

        class MultiPageIterator:
            def pages(self, on_progress=None):  # noqa: ARG002
                async def generator():
                    # Page 1: records 0-24, none match (status="inactive")
                    page1 = MagicMock()
                    page1.data = [
                        create_mock_record({"id": i, "status": "inactive"}) for i in range(25)
                    ]
                    yield page1
                    # Page 2: records 25-49, all match (status="active")
                    page2 = MagicMock()
                    page2.data = [
                        create_mock_record({"id": 25 + i, "status": "active"}) for i in range(25)
                    ]
                    yield page2

                return generator()

        service.all.return_value = MultiPageIterator()
        mock_client.persons = service

        # Query with filter - should find records in page 2
        query = Query(
            from_="persons",
            where=WhereClause(path="status", op="eq", value="active"),
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=2,
                ),
                PlanStep(
                    step_id=1,
                    operation="filter",
                    description="Filter",
                    depends_on=[0],
                ),
            ],
            total_api_calls=2,
            estimated_records_fetched=50,
            estimated_memory_mb=0.1,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=True,
        )

        # Set max_records to 20, which is less than position of matching records (25+)
        # Without fix: would stop at 20, find 0 matches
        # With fix: fetches all 50, filters to 25 matches, truncates to 20
        executor = QueryExecutor(mock_client, max_records=20)
        result = await executor.execute(plan)

        # Should find matching records from page 2, truncated to max_records
        assert len(result.data) == 20
        assert all(r["status"] == "active" for r in result.data)

    @pytest.mark.req("QUERY-EXEC-007c")
    @pytest.mark.asyncio
    async def test_sort_with_limit_fetches_all_records(self, mock_client: AsyncMock) -> None:
        """Sort+limit without filter fetches all records to get actual top N."""
        # Create service that returns 50 records, highest IDs are in page 2
        service = MagicMock()

        class MultiPageIterator:
            def pages(self, on_progress=None):  # noqa: ARG002
                async def generator():
                    # Page 1: records with low values (id: 1-25)
                    page1 = MagicMock()
                    page1.data = [create_mock_record({"id": i, "value": i}) for i in range(1, 26)]
                    yield page1
                    # Page 2: records with high values (id: 26-50)
                    page2 = MagicMock()
                    page2.data = [create_mock_record({"id": i, "value": i}) for i in range(26, 51)]
                    yield page2

                return generator()

        service.all.return_value = MultiPageIterator()
        mock_client.persons = service

        # Query: get top 5 by value descending (should be IDs 50, 49, 48, 47, 46)
        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="value", direction="desc")],
            limit=5,
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=2,
                ),
                PlanStep(
                    step_id=1,
                    operation="sort",
                    description="Sort by value desc",
                    depends_on=[0],
                ),
                PlanStep(
                    step_id=2,
                    operation="limit",
                    description="Take first 5",
                    depends_on=[1],
                ),
            ],
            total_api_calls=2,
            estimated_records_fetched=50,
            estimated_memory_mb=0.1,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        # Without fix: would stop at limit=5 during fetch, getting IDs 1-5
        # Then sort (no-op, already sorted by id), then limit (no-op)
        # Result: [1,2,3,4,5] sorted descending = [5,4,3,2,1] - WRONG!
        # With fix: fetches all 50, sorts all, takes top 5 = [50,49,48,47,46] - CORRECT!
        executor = QueryExecutor(mock_client, max_records=100)
        result = await executor.execute(plan)

        # Should get actual top 5 (highest values from page 2)
        assert len(result.data) == 5
        values = [r["value"] for r in result.data]
        assert values == [50, 49, 48, 47, 46], f"Expected top 5 values, got {values}"

    @pytest.mark.req("QUERY-EXEC-007d")
    @pytest.mark.asyncio
    async def test_aggregate_with_limit_fetches_all_records(self, mock_client: AsyncMock) -> None:
        """Aggregate+limit without filter fetches all records for accurate counts."""
        # Create service that returns 50 records across 2 pages
        service = MagicMock()

        class MultiPageIterator:
            def pages(self, on_progress=None):  # noqa: ARG002
                async def generator():
                    # Page 1: 25 records
                    page1 = MagicMock()
                    page1.data = [create_mock_record({"id": i, "value": 10}) for i in range(25)]
                    yield page1
                    # Page 2: 25 more records
                    page2 = MagicMock()
                    page2.data = [
                        create_mock_record({"id": 25 + i, "value": 10}) for i in range(25)
                    ]
                    yield page2

                return generator()

        service.all.return_value = MultiPageIterator()
        mock_client.persons = service

        # Query: count all records and sum values
        query = Query(
            from_="persons",
            aggregate={"total": AggregateFunc(count=True), "sum": AggregateFunc(sum="value")},
            limit=1,  # limit on output (one aggregate result row)
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=2,
                ),
                PlanStep(
                    step_id=1,
                    operation="aggregate",
                    description="Compute aggregates",
                    depends_on=[0],
                ),
                PlanStep(
                    step_id=2,
                    operation="limit",
                    description="Take first 1",
                    depends_on=[1],
                ),
            ],
            total_api_calls=2,
            estimated_records_fetched=50,
            estimated_memory_mb=0.1,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        # Without fix: would stop at limit=1 during fetch, getting 1 record
        # Then aggregate: count=1, sum=10 - WRONG!
        # With fix: fetches all 50, aggregates all: count=50, sum=500 - CORRECT!
        executor = QueryExecutor(mock_client, max_records=100)
        result = await executor.execute(plan)

        # Should have accurate counts from all 50 records
        assert len(result.data) == 1
        assert result.data[0]["total"] == 50, f"Expected count=50, got {result.data[0]['total']}"
        assert result.data[0]["sum"] == 500, f"Expected sum=500, got {result.data[0]['sum']}"

    @pytest.mark.req("QUERY-EXEC-009")
    @pytest.mark.asyncio
    async def test_limit_propagation_stops_early(
        self, mock_client: AsyncMock, mock_service: AsyncMock
    ) -> None:
        """Query limit stops fetching early."""
        mock_client.persons = mock_service

        query = Query(from_="persons", limit=1)
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=1,
                ),
                PlanStep(step_id=1, operation="limit", description="Limit", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        assert len(result.data) == 1


class TestQueryExecutorSorting:
    """Tests for sort step execution."""

    @pytest.mark.asyncio
    async def test_sort_ascending(self, mock_client: AsyncMock) -> None:
        """Sort records in ascending order."""
        service = MagicMock()
        records = [
            {"id": 1, "name": "Charlie"},
            {"id": 2, "name": "Alice"},
            {"id": 3, "name": "Bob"},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="name", direction="asc")],
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=1,
                ),
                PlanStep(step_id=1, operation="sort", description="Sort", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        assert result.data[0]["name"] == "Alice"
        assert result.data[1]["name"] == "Bob"
        assert result.data[2]["name"] == "Charlie"

    @pytest.mark.asyncio
    async def test_sort_descending(self, mock_client: AsyncMock) -> None:
        """Sort records in descending order."""
        service = MagicMock()
        records = [
            {"id": 1, "value": 10},
            {"id": 2, "value": 30},
            {"id": 3, "value": 20},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="value", direction="desc")],
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="persons",
                    description="Fetch",
                    estimated_api_calls=1,
                ),
                PlanStep(step_id=1, operation="sort", description="Sort", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        assert result.data[0]["value"] == 30
        assert result.data[1]["value"] == 20
        assert result.data[2]["value"] == 10


class TestQueryExecutorErrors:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_auth_failure(self, mock_client: AsyncMock, simple_plan: ExecutionPlan) -> None:
        """Auth failure raises QueryExecutionError."""
        mock_client.whoami.side_effect = Exception("Unauthorized")

        executor = QueryExecutor(mock_client)
        with pytest.raises(QueryExecutionError) as exc:
            await executor.execute(simple_plan)
        assert "Authentication failed" in str(exc.value)

    @pytest.mark.asyncio
    async def test_fetch_error(self, mock_client: AsyncMock, simple_plan: ExecutionPlan) -> None:
        """Fetch error raises QueryExecutionError."""
        service = MagicMock()

        class ErrorPageIterator:
            def pages(self, on_progress=None):  # noqa: ARG002
                async def generator():
                    raise Exception("API Error")
                    yield  # Make it a generator

                return generator()

        service.all.return_value = ErrorPageIterator()
        mock_client.persons = service

        executor = QueryExecutor(mock_client)
        with pytest.raises(QueryExecutionError) as exc:
            await executor.execute(simple_plan)
        assert "Failed to fetch" in str(exc.value)


class TestNullProgressCallback:
    """Tests for NullProgressCallback."""

    def test_no_op_methods(self) -> None:
        """All methods are no-ops."""
        callback = NullProgressCallback()
        step = PlanStep(step_id=0, operation="fetch", description="test")

        # Should not raise
        callback.on_step_start(step)
        callback.on_step_progress(step, 10, 100)
        callback.on_step_complete(step, 10)
        callback.on_step_error(step, Exception("test"))


class TestExecuteQueryFunction:
    """Tests for execute_query convenience function."""

    @pytest.mark.asyncio
    async def test_convenience_function(
        self, mock_client: AsyncMock, mock_service: AsyncMock, simple_plan: ExecutionPlan
    ) -> None:
        """execute_query convenience function works."""
        mock_client.persons = mock_service

        result = await execute_query(mock_client, simple_plan)

        assert len(result.data) == 2


# =============================================================================
# _extract_parent_ids Tests
# =============================================================================


class TestExtractParentIds:
    """Tests for _extract_parent_ids helper method.

    NOTE: _extract_parent_ids is a method on QueryExecutor. Since the method
    doesn't use self.client for extraction, we use a minimal mock.
    """

    @pytest.fixture
    def executor(self) -> QueryExecutor:
        """Create QueryExecutor with minimal mock client."""
        mock_client = MagicMock()
        return QueryExecutor(mock_client, max_records=100)

    def test_direct_condition(self, executor: QueryExecutor) -> None:
        """Extract parent ID from direct eq condition."""
        where = {"path": "listId", "op": "eq", "value": 123}
        assert executor._extract_parent_ids(where, "listId") == [123]

    def test_and_condition(self, executor: QueryExecutor) -> None:
        """Extract parent ID from AND condition."""
        where = {
            "and": [
                {"path": "listId", "op": "eq", "value": 123},
                {"path": "status", "op": "eq", "value": "active"},
            ]
        }
        assert executor._extract_parent_ids(where, "listId") == [123]

    def test_or_condition_multiple_ids(self, executor: QueryExecutor) -> None:
        """Extract multiple parent IDs from OR condition."""
        where = {
            "or": [
                {"path": "listId", "op": "eq", "value": 123},
                {"path": "listId", "op": "eq", "value": 456},
            ]
        }
        assert executor._extract_parent_ids(where, "listId") == [123, 456]

    def test_nested_and_or(self, executor: QueryExecutor) -> None:
        """Extract parent IDs from nested AND/OR conditions."""
        where = {
            "and": [
                {
                    "or": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "listId", "op": "eq", "value": 456},
                    ]
                },
                {"path": "status", "op": "eq", "value": "active"},
            ]
        }
        assert executor._extract_parent_ids(where, "listId") == [123, 456]

    def test_deduplication(self, executor: QueryExecutor) -> None:
        """Duplicate IDs are deduplicated."""
        where = {
            "or": [
                {"path": "listId", "op": "eq", "value": 123},
                {"path": "listId", "op": "eq", "value": 123},  # duplicate
            ]
        }
        assert executor._extract_parent_ids(where, "listId") == [123]

    def test_in_operator(self, executor: QueryExecutor) -> None:
        """Extract multiple IDs from 'in' operator."""
        where = {"path": "listId", "op": "in", "value": [123, 456, 789]}
        assert executor._extract_parent_ids(where, "listId") == [123, 456, 789]

    def test_string_id(self, executor: QueryExecutor) -> None:
        """String IDs are converted to int."""
        where = {"path": "listId", "op": "eq", "value": "123"}
        assert executor._extract_parent_ids(where, "listId") == [123]

    def test_mixed_string_int_ids(self, executor: QueryExecutor) -> None:
        """Mixed string and int IDs in 'in' operator."""
        where = {"path": "listId", "op": "in", "value": [123, "456", 789]}
        assert executor._extract_parent_ids(where, "listId") == [123, 456, 789]

    def test_invalid_string_id_ignored(self, executor: QueryExecutor) -> None:
        """Non-numeric strings are silently ignored."""
        where = {"path": "listId", "op": "in", "value": [123, "not-a-number", 456]}
        assert executor._extract_parent_ids(where, "listId") == [123, 456]

    def test_none_where(self, executor: QueryExecutor) -> None:
        """Returns empty list for None where clause."""
        assert executor._extract_parent_ids(None, "listId") == []

    def test_none_field_name(self, executor: QueryExecutor) -> None:
        """Returns empty list for None field name."""
        where = {"path": "listId", "op": "eq", "value": 123}
        assert executor._extract_parent_ids(where, None) == []

    def test_pydantic_model_conversion(self, executor: QueryExecutor) -> None:
        """Handles WhereClause pydantic model by calling model_dump."""
        where = WhereClause(path="listId", op="eq", value=123)
        assert executor._extract_parent_ids(where, "listId") == [123]


# =============================================================================
# _resolve_list_names_to_ids Tests
# =============================================================================


class TestListNameResolution:
    """Tests for _resolve_list_names_to_ids helper method.

    NOTE: _resolve_list_names_to_ids uses self.client.lists.all() to fetch
    list metadata, so we need a mock client that returns known lists.
    """

    @pytest.fixture
    def executor(self) -> QueryExecutor:
        """Create QueryExecutor with mock client returning known lists."""
        # Create mock list objects
        mock_list_1 = MagicMock()
        mock_list_1.name = "My Deals"
        mock_list_1.id = 12345

        mock_list_2 = MagicMock()
        mock_list_2.name = "Leads"
        mock_list_2.id = 67890

        # Create async iterator for client.lists.all()
        async def mock_lists_all():
            for lst in [mock_list_1, mock_list_2]:
                yield lst

        mock_client = MagicMock()
        mock_client.lists.all = mock_lists_all
        mock_client.whoami = AsyncMock(return_value={"id": 1})

        return QueryExecutor(mock_client, max_records=100)

    @pytest.mark.asyncio
    async def test_single_list_name_resolved(self, executor: QueryExecutor) -> None:
        """Single listName is resolved to listId."""
        where = {"path": "listName", "op": "eq", "value": "My Deals"}
        resolved = await executor._resolve_list_names_to_ids(where)
        assert resolved == {"path": "listId", "op": "eq", "value": 12345}

    @pytest.mark.asyncio
    async def test_multiple_list_names_resolved(self, executor: QueryExecutor) -> None:
        """Multiple listNames in 'in' operator are resolved."""
        where = {"path": "listName", "op": "in", "value": ["My Deals", "Leads"]}
        resolved = await executor._resolve_list_names_to_ids(where)
        assert resolved == {"path": "listId", "op": "in", "value": [12345, 67890]}

    @pytest.mark.asyncio
    async def test_unknown_list_name_raises_error(self, executor: QueryExecutor) -> None:
        """Unknown list name raises QueryExecutionError."""
        where = {"path": "listName", "op": "eq", "value": "Nonexistent List"}
        with pytest.raises(QueryExecutionError, match="List not found"):
            await executor._resolve_list_names_to_ids(where)

    @pytest.mark.asyncio
    async def test_nested_list_name_resolved(self, executor: QueryExecutor) -> None:
        """listName in nested conditions is resolved."""
        where = {
            "and": [
                {"path": "listName", "op": "eq", "value": "My Deals"},
                {"path": "status", "op": "eq", "value": "active"},
            ]
        }
        resolved = await executor._resolve_list_names_to_ids(where)
        assert resolved["and"][0] == {"path": "listId", "op": "eq", "value": 12345}
        assert resolved["and"][1] == {"path": "status", "op": "eq", "value": "active"}

    @pytest.mark.asyncio
    async def test_non_listname_passthrough(self, executor: QueryExecutor) -> None:
        """Non-listName conditions pass through unchanged."""
        where = {"path": "listId", "op": "eq", "value": 999}
        resolved = await executor._resolve_list_names_to_ids(where)
        assert resolved == {"path": "listId", "op": "eq", "value": 999}

    @pytest.mark.asyncio
    async def test_cache_reused(self, executor: QueryExecutor) -> None:
        """List name cache is reused across multiple resolutions."""
        where1 = {"path": "listName", "op": "eq", "value": "My Deals"}
        where2 = {"path": "listName", "op": "eq", "value": "Leads"}

        # First resolution populates cache
        await executor._resolve_list_names_to_ids(where1)

        # Second resolution should use cache (lists.all called only once)
        await executor._resolve_list_names_to_ids(where2)

        # Cache should exist
        assert hasattr(executor, "_list_name_cache")
        assert len(executor._list_name_cache) == 2


# =============================================================================
# _resolve_field_names_to_ids Tests
# =============================================================================


class TestFieldNameResolution:
    """Tests for _resolve_field_names_to_ids helper method.

    This tests the feature that resolves human-readable field names
    to field IDs in query where clauses. For example:
        {"path": "fields.Status", ...} -> {"path": "fields.12345", ...}
    """

    @pytest.fixture
    def ctx(self) -> ExecutionContext:
        """Create execution context for field name resolution tests."""
        return ExecutionContext(query=Query(from_="listEntries"))

    @pytest.fixture
    def executor(self) -> QueryExecutor:
        """Create QueryExecutor with mock client returning known fields."""
        # Create mock field objects
        mock_field_1 = MagicMock()
        mock_field_1.name = "Status"
        mock_field_1.id = "field-260415"

        mock_field_2 = MagicMock()
        mock_field_2.name = "Deal Value"
        mock_field_2.id = "field-260416"

        mock_field_3 = MagicMock()
        mock_field_3.name = "Priority"
        mock_field_3.id = "field-260417"

        # Create mock for lists.get_fields
        async def mock_get_fields(_list_id: Any) -> list[Any]:
            return [mock_field_1, mock_field_2, mock_field_3]

        mock_client = MagicMock()
        mock_client.lists.get_fields = mock_get_fields
        mock_client.whoami = AsyncMock(return_value={"id": 1})

        return QueryExecutor(mock_client, max_records=100)

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_single_field_name_resolved(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Single field name is resolved to field ID."""
        where = {"path": "fields.Status", "op": "eq", "value": "Active"}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        assert resolved == {"path": "fields.field-260415", "op": "eq", "value": "Active"}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_field_name_case_insensitive(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Field name resolution is case-insensitive."""
        where = {"path": "fields.status", "op": "eq", "value": "Active"}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        assert resolved == {"path": "fields.field-260415", "op": "eq", "value": "Active"}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_field_with_space_resolved(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Field names with spaces are resolved."""
        where = {"path": "fields.Deal Value", "op": "gt", "value": 10000}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        assert resolved == {"path": "fields.field-260416", "op": "gt", "value": 10000}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_numeric_field_id_passthrough(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Numeric field IDs pass through unchanged."""
        where = {"path": "fields.12345", "op": "eq", "value": "Active"}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        # Should not change since it's already a numeric ID
        assert resolved == {"path": "fields.12345", "op": "eq", "value": "Active"}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_field_id_prefix_passthrough(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """field- prefixed IDs pass through unchanged."""
        where = {"path": "fields.field-260415", "op": "eq", "value": "Active"}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        assert resolved == {"path": "fields.field-260415", "op": "eq", "value": "Active"}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_unknown_field_name_passthrough(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Unknown field names pass through unchanged (no error)."""
        where = {"path": "fields.NonexistentField", "op": "eq", "value": "X"}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        # Should pass through since field not found
        assert resolved == {"path": "fields.NonexistentField", "op": "eq", "value": "X"}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_nested_and_conditions_resolved(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Field names in nested AND conditions are resolved."""
        where = {
            "and": [
                {"path": "fields.Status", "op": "eq", "value": "Active"},
                {"path": "fields.Priority", "op": "eq", "value": "High"},
            ]
        }
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        assert resolved["and"][0] == {"path": "fields.field-260415", "op": "eq", "value": "Active"}
        assert resolved["and"][1] == {"path": "fields.field-260417", "op": "eq", "value": "High"}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_nested_or_conditions_resolved(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Field names in nested OR conditions are resolved."""
        where = {
            "or": [
                {"path": "fields.Status", "op": "eq", "value": "Active"},
                {"path": "fields.Status", "op": "eq", "value": "Pending"},
            ]
        }
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        assert resolved["or"][0] == {"path": "fields.field-260415", "op": "eq", "value": "Active"}
        assert resolved["or"][1] == {"path": "fields.field-260415", "op": "eq", "value": "Pending"}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_non_field_paths_passthrough(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Non-fields.* paths pass through unchanged."""
        where = {"path": "listId", "op": "eq", "value": 12345}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)
        assert resolved == {"path": "listId", "op": "eq", "value": 12345}

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_empty_list_ids_passthrough(
        self, executor: QueryExecutor, ctx: ExecutionContext
    ) -> None:
        """Empty list IDs returns where unchanged."""
        where = {"path": "fields.Status", "op": "eq", "value": "Active"}
        resolved = await executor._resolve_field_names_to_ids(where, [], ctx)
        assert resolved == where

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_cache_reused(self, executor: QueryExecutor, ctx: ExecutionContext) -> None:
        """Field name cache is reused across multiple resolutions."""
        where1 = {"path": "fields.Status", "op": "eq", "value": "Active"}
        where2 = {"path": "fields.Priority", "op": "eq", "value": "High"}

        # First resolution populates cache
        await executor._resolve_field_names_to_ids(where1, [12345], ctx)

        # Second resolution should use cache
        await executor._resolve_field_names_to_ids(where2, [12345], ctx)

        # Cache should exist
        assert hasattr(executor, "_field_name_to_id_cache")
        assert len(executor._field_name_to_id_cache) == 3  # All 3 fields cached

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-010")
    async def test_warns_on_get_fields_failure(self) -> None:
        """Adds warning and returns where unchanged when get_fields fails."""
        mock_client = MagicMock()
        mock_client.lists.get_fields = AsyncMock(side_effect=Exception("Network timeout"))
        mock_client.whoami = AsyncMock(return_value={"id": 1})
        executor = QueryExecutor(mock_client, max_records=100)

        query = Query(from_="listEntries", select=["fields.Status"])
        ctx = ExecutionContext(query=query)

        where = {"path": "fields.Status", "op": "eq", "value": "Active"}
        resolved = await executor._resolve_field_names_to_ids(where, [12345], ctx)

        # Where clause returned unchanged (field name not resolved to ID)
        assert resolved == where
        # Warning surfaced to user
        assert len(ctx.warnings) == 1
        assert "Network timeout" in ctx.warnings[0]


# =============================================================================
# Tests for Field Reference Collection and Field ID Resolution
# =============================================================================


class TestCollectFieldRefs:
    """Tests for _collect_field_refs_from_query method."""

    @pytest.fixture
    def executor(self, mock_client: AsyncMock) -> QueryExecutor:
        """Create executor for testing."""
        return QueryExecutor(mock_client, max_records=100)

    def test_collects_from_select(self, executor: QueryExecutor) -> None:
        """Collects field names from select clause."""
        query = Query(
            from_="listEntries",
            select=["id", "fields.Status", "fields.Owner", "entityName"],
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Status", "Owner"}

    def test_collects_from_groupby(self, executor: QueryExecutor) -> None:
        """Collects field names from groupBy clause."""
        query = Query(
            from_="listEntries",
            group_by="fields.Status",
            aggregate={"count": AggregateFunc(count=True)},
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Status"}

    def test_collects_from_aggregate(self, executor: QueryExecutor) -> None:
        """Collects field names from aggregate functions."""
        query = Query(
            from_="listEntries",
            aggregate={
                "total": AggregateFunc(sum="fields.Deal Value"),
                "avg_amount": AggregateFunc(avg="fields.Amount"),
            },
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Deal Value", "Amount"}

    def test_collects_from_where(self, executor: QueryExecutor) -> None:
        """Collects field names from where clause."""
        query = Query(
            from_="listEntries",
            where=WhereClause(path="fields.Status", op="eq", value="Active"),
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Status"}

    def test_collects_from_compound_where(self, executor: QueryExecutor) -> None:
        """Collects field names from compound where clause."""
        query = Query(
            from_="listEntries",
            where=WhereClause(
                and_=[
                    WhereClause(path="listId", op="eq", value=123),
                    WhereClause(path="fields.Status", op="eq", value="Active"),
                    WhereClause(path="fields.Priority", op="in", value=["High", "Medium"]),
                ]
            ),
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Status", "Priority"}

    def test_returns_wildcard_for_fields_star_in_select(self, executor: QueryExecutor) -> None:
        """Returns wildcard when fields.* is in select."""
        query = Query(from_="listEntries", select=["id", "fields.*"])
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"*"}

    def test_returns_wildcard_for_fields_star_in_groupby(self, executor: QueryExecutor) -> None:
        """Returns wildcard when fields.* is in groupBy."""
        query = Query(
            from_="listEntries",
            group_by="fields.*",
            aggregate={"count": AggregateFunc(count=True)},
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"*"}

    def test_returns_empty_for_no_field_refs(self, executor: QueryExecutor) -> None:
        """Returns empty set when no fields.* references."""
        query = Query(
            from_="listEntries",
            select=["id", "entityName", "entityType"],
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == set()

    def test_collects_from_all_clauses(self, executor: QueryExecutor) -> None:
        """Collects from select, groupBy, aggregate, and where."""
        query = Query(
            from_="listEntries",
            select=["fields.A", "fields.B"],
            group_by="fields.C",
            aggregate={"total": AggregateFunc(sum="fields.D")},
            where=WhereClause(path="fields.E", op="eq", value="X"),
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"A", "B", "C", "D", "E"}

    def test_collects_from_percentile_aggregate(self, executor: QueryExecutor) -> None:
        """Collects field names from percentile aggregate."""
        query = Query(
            from_="listEntries",
            aggregate={"p90": AggregateFunc(percentile={"field": "fields.Amount", "p": 90})},
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Amount"}


class TestResolveFieldIdsForListEntries:
    """Tests for _resolve_field_ids_for_list_entries method."""

    @pytest.fixture
    def mock_fields(self) -> list[MagicMock]:
        """Create mock field objects."""
        fields = []
        for i, name in enumerate(["Status", "Priority", "Amount"]):
            field = MagicMock()
            field.id = f"field-{100 + i}"
            field.name = name
            fields.append(field)
        return fields

    @pytest.fixture
    def executor(self, mock_client: AsyncMock, mock_fields: list[MagicMock]) -> QueryExecutor:
        """Create executor with mocked get_fields."""
        mock_client.lists.get_fields = AsyncMock(return_value=mock_fields)
        return QueryExecutor(mock_client, max_records=100)

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_resolves_field_names_to_ids(
        self, executor: QueryExecutor, mock_client: AsyncMock
    ) -> None:
        """Resolves field names to field IDs."""
        query = Query(
            from_="listEntries",
            group_by="fields.Status",
            aggregate={"count": AggregateFunc(count=True)},
        )
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        assert field_ids == ["field-100"]
        mock_client.lists.get_fields.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_resolves_multiple_fields(self, executor: QueryExecutor) -> None:
        """Resolves multiple field names."""
        query = Query(
            from_="listEntries",
            select=["fields.Status", "fields.Priority"],
        )
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        assert sorted(field_ids) == ["field-100", "field-101"]

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_returns_all_fields_for_wildcard(self, executor: QueryExecutor) -> None:
        """Returns all field IDs for fields.* wildcard."""
        query = Query(from_="listEntries", select=["fields.*"])
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        assert sorted(field_ids) == ["field-100", "field-101", "field-102"]

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_returns_none_for_no_field_refs(self, executor: QueryExecutor) -> None:
        """Returns None when no field references in query."""
        query = Query(from_="listEntries", select=["id", "entityName"])
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        assert field_ids is None

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_skips_unknown_field_names(self, executor: QueryExecutor) -> None:
        """Skips field names not found in list metadata."""
        query = Query(
            from_="listEntries",
            select=["fields.Status", "fields.UnknownField"],
        )
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        # Should only include Status, not UnknownField
        assert field_ids == ["field-100"]

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_case_insensitive_field_lookup(self, executor: QueryExecutor) -> None:
        """Field names are resolved case-insensitively."""
        query = Query(
            from_="listEntries",
            group_by="fields.status",  # lowercase
        )
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        assert field_ids == ["field-100"]

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_caches_field_metadata(
        self, executor: QueryExecutor, mock_client: AsyncMock
    ) -> None:
        """Field metadata is cached per list ID."""
        query = Query(from_="listEntries", group_by="fields.Status")
        ctx = ExecutionContext(query=query)

        # First call
        await executor._resolve_field_ids_for_list_entries(ctx, 12345)
        # Second call for same list
        await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        # Should only fetch fields once
        assert mock_client.lists.get_fields.call_count == 1

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-011")
    async def test_handles_get_fields_error(self, mock_client: AsyncMock) -> None:
        """Returns None and adds warning if get_fields fails."""
        mock_client.lists.get_fields = AsyncMock(side_effect=Exception("API Error"))
        executor = QueryExecutor(mock_client, max_records=100)

        query = Query(from_="listEntries", group_by="fields.Status")
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        assert field_ids is None
        assert len(ctx.warnings) == 1
        assert "API Error" in ctx.warnings[0]
        assert "field" in ctx.warnings[0].lower()

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-013")
    async def test_warns_on_missing_field(self, executor: QueryExecutor) -> None:
        """Adds warning when referenced field doesn't exist on list."""
        query = Query(from_="listEntries", group_by="fields.NonExistentField")
        ctx = ExecutionContext(query=query)

        await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        # Should have warning about missing field
        assert len(ctx.warnings) == 1
        assert "NonExistentField" in ctx.warnings[0]
        assert "Available fields:" in ctx.warnings[0]

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXECUTOR-013")
    async def test_warns_on_multiple_missing_fields(self, mock_client: AsyncMock) -> None:
        """Adds warning listing all missing fields."""
        mock_client.lists.get_fields = AsyncMock(
            return_value=[
                type("Field", (), {"id": "field-100", "name": "Status"})(),
            ]
        )
        executor = QueryExecutor(mock_client, max_records=100)

        query = Query(
            from_="listEntries",
            select=["fields.Missing1", "fields.Missing2", "fields.Status"],
        )
        ctx = ExecutionContext(query=query)

        field_ids = await executor._resolve_field_ids_for_list_entries(ctx, 12345)

        # Should return the one valid field
        assert field_ids == ["field-100"]
        # Should warn about both missing fields
        assert len(ctx.warnings) == 1
        assert "Missing1" in ctx.warnings[0]
        assert "Missing2" in ctx.warnings[0]


# =============================================================================
# Edge Case Tests for Normalize List Entry Fields
# =============================================================================


class TestNormalizeListEntryFieldsEdgeCases:
    """Additional edge case tests for _normalize_list_entry_fields."""

    def test_entity_not_dict(self) -> None:
        """Adds aliases even when entity is not a dict."""
        record = {"id": 1, "entity": "not a dict"}
        result = _normalize_list_entry_fields(record)
        # Aliases added, fields defaults to empty
        assert result["listEntryId"] == 1
        assert result["fields"] == {}
        # entityId/entityName not added since entity is not a dict

    def test_fields_container_not_dict(self) -> None:
        """Adds aliases even when entity.fields is not a dict."""
        record = {"id": 1, "entity": {"id": 100, "fields": "not a dict"}}
        result = _normalize_list_entry_fields(record)
        # Aliases added from entity
        assert result["listEntryId"] == 1
        assert result["entityId"] == 100
        assert result["fields"] == {}

    def test_fields_data_not_dict(self) -> None:
        """Adds aliases even when entity.fields.data is not a dict."""
        record = {"id": 1, "entity": {"id": 100, "fields": {"data": "not a dict"}}}
        result = _normalize_list_entry_fields(record)
        # Aliases added from entity
        assert result["listEntryId"] == 1
        assert result["entityId"] == 100
        assert result["fields"] == {}

    def test_field_obj_not_dict(self) -> None:
        """Skips field objects that are not dicts."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": "not a dict",  # Should be skipped
                        "field-2": {"id": "field-2", "name": "Status", "value": {"data": "Active"}},
                    },
                },
            },
        }
        result = _normalize_list_entry_fields(record)
        assert result["fields"] == {"Status": "Active"}

    def test_field_without_name(self) -> None:
        """Skips fields that don't have a name."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {"id": "field-1", "value": {"data": "NoName"}},  # Missing name
                        "field-2": {"id": "field-2", "name": "Status", "value": {"data": "Active"}},
                    },
                },
            },
        }
        result = _normalize_list_entry_fields(record)
        assert result["fields"] == {"Status": "Active"}

    def test_value_wrapper_not_dict(self) -> None:
        """Handles value wrapper that is not a dict (direct value)."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "DirectValue",
                            "value": "just a string",
                        },
                    },
                },
            },
        }
        result = _normalize_list_entry_fields(record)
        assert result["fields"] == {"DirectValue": "just a string"}

    def test_multi_select_with_non_dict_items(self) -> None:
        """Handles multi-select with mixed dict and non-dict items."""
        record = {
            "id": 1,
            "entity": {
                "id": 100,
                "fields": {
                    "requested": True,
                    "data": {
                        "field-1": {
                            "id": "field-1",
                            "name": "Tags",
                            "value": {"data": ["simple_tag", {"text": "complex_tag"}]},
                        },
                    },
                },
            },
        }
        result = _normalize_list_entry_fields(record)
        assert result["fields"] == {"Tags": ["simple_tag", "complex_tag"]}


# =============================================================================
# Edge Case Tests for Sort
# =============================================================================


class TestSortEdgeCases:
    """Tests for _execute_sort edge cases."""

    @pytest.mark.asyncio
    async def test_sort_with_null_values_asc(self, mock_client: AsyncMock) -> None:
        """Sort with null values - nulls go to end in ascending order."""
        service = MagicMock()
        records = [
            {"id": 1, "name": None},
            {"id": 2, "name": "Alice"},
            {"id": 3, "name": "Bob"},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="name", direction="asc")],
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="sort", description="Sort", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Nulls should be at end for ascending
        assert result.data[0]["name"] == "Alice"
        assert result.data[1]["name"] == "Bob"
        assert result.data[2]["name"] is None

    @pytest.mark.asyncio
    async def test_sort_with_null_values_desc(self, mock_client: AsyncMock) -> None:
        """Sort with null values - nulls go to end in descending order."""
        service = MagicMock()
        records = [
            {"id": 1, "name": None},
            {"id": 2, "name": "Alice"},
            {"id": 3, "name": "Bob"},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="name", direction="desc")],
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="sort", description="Sort", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # For desc, Bob > Alice, then null at end
        assert result.data[0]["name"] == "Bob"
        assert result.data[1]["name"] == "Alice"
        assert result.data[2]["name"] is None

    @pytest.mark.asyncio
    async def test_sort_datetime_strings_desc(self, mock_client: AsyncMock) -> None:
        """Sort datetime strings in descending order correctly.

        Regression test for bug where string values couldn't be negated,
        causing desc order to return same results as asc order.
        """
        service = MagicMock()
        records = [
            {"id": 1, "createdAt": "2019-10-02T22:38:38Z"},  # oldest
            {"id": 2, "createdAt": "2025-06-15T10:00:00Z"},  # middle
            {"id": 3, "createdAt": "2026-02-02T15:18:00Z"},  # newest
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="createdAt", direction="desc")],
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="sort", description="Sort", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Descending: newest first
        assert result.data[0]["createdAt"] == "2026-02-02T15:18:00Z"
        assert result.data[1]["createdAt"] == "2025-06-15T10:00:00Z"
        assert result.data[2]["createdAt"] == "2019-10-02T22:38:38Z"

    @pytest.mark.asyncio
    async def test_sort_mixed_types_fallback(self, mock_client: AsyncMock) -> None:
        """Sort with mixed types falls back to string comparison."""
        service = MagicMock()
        records = [
            {"id": 1, "value": "text"},
            {"id": 2, "value": 100},
            {"id": 3, "value": "another"},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            order_by=[OrderByClause(field="value", direction="asc")],
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="sort", description="Sort", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should not raise - falls back to string comparison
        assert len(result.data) == 3

    @pytest.mark.asyncio
    async def test_sort_no_order_by(self, mock_client: AsyncMock, mock_service: AsyncMock) -> None:
        """Sort step with no order_by is a no-op."""
        mock_client.persons = mock_service

        query = Query(from_="persons")  # No order_by
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="sort", description="Sort", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=2,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should return records unchanged
        assert len(result.data) == 2


# =============================================================================
# Edge Case Tests for _extract_parent_ids
# =============================================================================


class TestExtractParentIdsEdgeCases:
    """Additional edge case tests for _extract_parent_ids."""

    @pytest.fixture
    def executor(self) -> QueryExecutor:
        """Create QueryExecutor with minimal mock client."""
        mock_client = MagicMock()
        return QueryExecutor(mock_client, max_records=100)

    def test_where_not_dict(self, executor: QueryExecutor) -> None:
        """Returns empty list when where is not a dict."""
        assert executor._extract_parent_ids("not a dict", "listId") == []

    def test_float_value_ignored(self, executor: QueryExecutor) -> None:
        """Float values are not converted to int."""
        where = {"path": "listId", "op": "eq", "value": 123.45}
        # Float is not int or str, so to_int returns None
        assert executor._extract_parent_ids(where, "listId") == []

    def test_deeply_nested_and_or(self, executor: QueryExecutor) -> None:
        """Handles deeply nested AND/OR structures."""
        where = {
            "and": [
                {
                    "or": [
                        {"and": [{"path": "listId", "op": "eq", "value": 111}]},
                        {"path": "listId", "op": "eq", "value": 222},
                    ]
                },
                {"path": "listId", "op": "eq", "value": 333},
            ]
        }
        result = executor._extract_parent_ids(where, "listId")
        assert sorted(result) == [111, 222, 333]

    def test_not_clause_ignored(self, executor: QueryExecutor) -> None:
        """NOT clauses are intentionally not traversed."""
        where = {
            "and": [
                {"path": "listId", "op": "eq", "value": 123},
                {"not": {"path": "listId", "op": "eq", "value": 456}},  # Should be ignored
            ]
        }
        result = executor._extract_parent_ids(where, "listId")
        assert result == [123]

    def test_string_ids_in_list_converted(self, executor: QueryExecutor) -> None:
        """String IDs in 'in' operator list are converted."""
        where = {"path": "listId", "op": "in", "value": ["100", "200", "300"]}
        result = executor._extract_parent_ids(where, "listId")
        assert result == [100, 200, 300]


# =============================================================================
# Edge Case Tests for _collect_field_refs_from_where
# =============================================================================


class TestCollectFieldRefsEdgeCases:
    """Additional edge case tests for field reference collection."""

    @pytest.fixture
    def executor(self, mock_client: AsyncMock) -> QueryExecutor:
        """Create executor for testing."""
        return QueryExecutor(mock_client, max_records=100)

    def test_collects_from_nested_not_condition(self, executor: QueryExecutor) -> None:
        """Collects field names from nested NOT conditions."""
        query = Query(
            from_="listEntries",
            where=WhereClause(not_=WhereClause(path="fields.Status", op="eq", value="Inactive")),
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Status"}

    def test_collects_from_deeply_nested_where(self, executor: QueryExecutor) -> None:
        """Collects from deeply nested where clause."""
        query = Query(
            from_="listEntries",
            where=WhereClause(
                and_=[
                    WhereClause(
                        or_=[
                            WhereClause(path="fields.A", op="eq", value="X"),
                            WhereClause(not_=WhereClause(path="fields.B", op="eq", value="Y")),
                        ]
                    ),
                    WhereClause(path="fields.C", op="gt", value=100),
                ]
            ),
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"A", "B", "C"}

    def test_where_clause_non_string_path(self, executor: QueryExecutor) -> None:
        """Handles where clause with non-string path gracefully."""
        # This tests the defensive check in _collect_field_refs_from_where
        where_dict = {"path": 123, "op": "eq", "value": "X"}  # path is not a string
        field_names: set[str] = set()
        executor._collect_field_refs_from_where(where_dict, field_names)
        assert field_names == set()  # No crash, no fields collected

    def test_collects_from_min_max_first_last(self, executor: QueryExecutor) -> None:
        """Collects from min, max, first, last aggregate functions."""
        query = Query(
            from_="listEntries",
            aggregate={
                "min_val": AggregateFunc(min="fields.Min"),
                "max_val": AggregateFunc(max="fields.Max"),
                "first_val": AggregateFunc(first="fields.First"),
                "last_val": AggregateFunc(last="fields.Last"),
            },
        )
        field_names = executor._collect_field_refs_from_query(query)
        assert field_names == {"Min", "Max", "First", "Last"}


# =============================================================================
# Edge Case Tests for _should_stop
# =============================================================================


class TestShouldStop:
    """Tests for _should_stop helper method."""

    def test_stops_at_max_records(self) -> None:
        """Stops when max_records reached."""
        mock_client = MagicMock()
        executor = QueryExecutor(mock_client, max_records=10)

        query = Query(from_="persons")
        ctx = ExecutionContext(query=query, max_records=10)
        ctx.records = [{"id": i} for i in range(10)]

        assert executor._should_stop(ctx) is True

    def test_stops_at_query_limit(self) -> None:
        """Stops when query limit reached."""
        mock_client = MagicMock()
        executor = QueryExecutor(mock_client, max_records=100)

        query = Query(from_="persons", limit=5)
        ctx = ExecutionContext(query=query, max_records=100)
        ctx.records = [{"id": i} for i in range(5)]

        assert executor._should_stop(ctx) is True

    def test_does_not_stop_when_under_limits(self) -> None:
        """Does not stop when under both limits."""
        mock_client = MagicMock()
        executor = QueryExecutor(mock_client, max_records=100)

        query = Query(from_="persons", limit=10)
        ctx = ExecutionContext(query=query, max_records=100)
        ctx.records = [{"id": i} for i in range(3)]

        assert executor._should_stop(ctx) is False

    def test_no_limit_checks_only_max_records(self) -> None:
        """When no query limit, only checks max_records."""
        mock_client = MagicMock()
        executor = QueryExecutor(mock_client, max_records=5)

        query = Query(from_="persons")  # No limit
        ctx = ExecutionContext(query=query, max_records=5)
        ctx.records = [{"id": i} for i in range(3)]

        assert executor._should_stop(ctx) is False

    def test_needs_full_fetch_prevents_stop_at_limit(self) -> None:
        """When needs_full_fetch is True, does not stop at limit."""
        mock_client = MagicMock()
        executor = QueryExecutor(mock_client, max_records=100)

        query = Query(from_="persons", limit=5)
        ctx = ExecutionContext(query=query, max_records=100, needs_full_fetch=True)
        ctx.records = [{"id": i} for i in range(50)]  # Way over limit

        # Even though we have 50 records and limit is 5, needs_full_fetch prevents stopping
        assert executor._should_stop(ctx) is False

    def test_needs_full_fetch_prevents_stop_at_max_records(self) -> None:
        """When needs_full_fetch is True, does not stop at max_records."""
        mock_client = MagicMock()
        executor = QueryExecutor(mock_client, max_records=10)

        query = Query(from_="persons")
        ctx = ExecutionContext(query=query, max_records=10, needs_full_fetch=True)
        ctx.records = [{"id": i} for i in range(20)]  # Over max_records

        # Even though we have 20 records and max_records is 10, needs_full_fetch prevents stopping
        assert executor._should_stop(ctx) is False

    def test_needs_full_fetch_false_allows_stop(self) -> None:
        """When needs_full_fetch is False, normal limits apply."""
        mock_client = MagicMock()
        executor = QueryExecutor(mock_client, max_records=100)

        query = Query(from_="persons", limit=5)
        ctx = ExecutionContext(query=query, max_records=100, needs_full_fetch=False)
        ctx.records = [{"id": i} for i in range(5)]

        # With needs_full_fetch=False, should stop at limit
        assert executor._should_stop(ctx) is True


# =============================================================================
# Edge Case Tests for _resolve_list_names_to_ids
# =============================================================================


class TestResolveListNamesEdgeCases:
    """Additional edge case tests for _resolve_list_names_to_ids."""

    @pytest.mark.asyncio
    async def test_non_dict_where_passthrough(self) -> None:
        """Non-dict where passes through unchanged."""
        mock_client = MagicMock()
        mock_client.whoami = AsyncMock(return_value={"id": 1})
        executor = QueryExecutor(mock_client, max_records=100)

        result = await executor._resolve_list_names_to_ids("not a dict")  # type: ignore[arg-type]
        assert result == "not a dict"

    @pytest.mark.asyncio
    async def test_unknown_list_in_multiple_raises(self) -> None:
        """Unknown list in 'in' operator raises error."""
        mock_list = MagicMock()
        mock_list.name = "Known List"
        mock_list.id = 12345

        async def mock_lists_all():
            yield mock_list

        mock_client = MagicMock()
        mock_client.lists.all = mock_lists_all
        mock_client.whoami = AsyncMock(return_value={"id": 1})

        executor = QueryExecutor(mock_client, max_records=100)

        where = {"path": "listName", "op": "in", "value": ["Known List", "Unknown List"]}
        with pytest.raises(QueryExecutionError, match="List not found: 'Unknown List'"):
            await executor._resolve_list_names_to_ids(where)

    @pytest.mark.asyncio
    async def test_or_conditions_resolved(self) -> None:
        """OR conditions are recursively resolved."""
        mock_list = MagicMock()
        mock_list.name = "Deals"
        mock_list.id = 111

        async def mock_lists_all():
            yield mock_list

        mock_client = MagicMock()
        mock_client.lists.all = mock_lists_all
        mock_client.whoami = AsyncMock(return_value={"id": 1})

        executor = QueryExecutor(mock_client, max_records=100)

        where = {
            "or": [
                {"path": "listName", "op": "eq", "value": "Deals"},
                {"path": "status", "op": "eq", "value": "active"},
            ]
        }
        result = await executor._resolve_list_names_to_ids(where)
        assert result["or"][0] == {"path": "listId", "op": "eq", "value": 111}
        assert result["or"][1] == {"path": "status", "op": "eq", "value": "active"}


# =============================================================================
# Edge Case Tests for Aggregation Step
# =============================================================================


class TestAggregateStepEdgeCases:
    """Tests for _execute_aggregate edge cases."""

    @pytest.mark.asyncio
    async def test_aggregate_no_aggregate_clause(
        self, mock_client: AsyncMock, mock_service: AsyncMock
    ) -> None:
        """Aggregate step with no aggregate clause is a no-op."""
        mock_client.persons = mock_service

        query = Query(from_="persons")  # No aggregate
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="aggregate", description="Aggregate", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=2,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Records should be unchanged (no aggregation)
        assert len(result.data) == 2


# =============================================================================
# Tests for _execute_include (N+1 Relationship Fetching)
# =============================================================================


class TestExecuteInclude:
    """Tests for _execute_include method."""

    @pytest.mark.asyncio
    async def test_include_entity_method_strategy(self, mock_client: AsyncMock) -> None:
        """Test include with entity_method fetch strategy."""
        # Setup persons service
        persons_service = MagicMock()
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        persons_service.all.return_value = create_mock_page_iterator(records)
        # Setup entity method that returns related IDs
        persons_service.get_associated_company_ids = AsyncMock(side_effect=[[100, 101], [102]])
        mock_client.persons = persons_service

        # Setup companies service for batch fetch (V2 uses iter(ids=...))
        async def mock_companies_iter(ids: list[int]):
            for id_ in ids:
                yield MagicMock(
                    model_dump=MagicMock(
                        return_value={"id": id_, "name": f"Company {id_}", "domain": f"co{id_}.com"}
                    )
                )

        companies_service = MagicMock()
        companies_service.iter = mock_companies_iter
        mock_client.companies = companies_service

        query = Query(from_="persons", include=["companies"])
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity="persons",
                    relationship="companies",
                    description="Include companies",
                    depends_on=[0],
                ),
            ],
            total_api_calls=3,
            estimated_records_fetched=2,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=True,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        assert len(result.data) == 2
        assert "companies" in result.included
        # 3 unique companies returned (100, 101, 102)
        assert len(result.included["companies"]) == 3
        # Verify full records (not just IDs) are returned
        assert all("name" in c for c in result.included["companies"])
        # Verify included_by_parent is populated
        assert "companies" in result.included_by_parent
        assert 1 in result.included_by_parent["companies"]  # Alice's ID
        assert 2 in result.included_by_parent["companies"]  # Bob's ID
        assert len(result.included_by_parent["companies"][1]) == 2  # Alice has 2 companies
        assert len(result.included_by_parent["companies"][2]) == 1  # Bob has 1 company

    @pytest.mark.asyncio
    async def test_include_global_service_strategy(self, mock_client: AsyncMock) -> None:
        """Test include with global_service fetch strategy."""
        # Setup persons service
        persons_service = MagicMock()
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        persons_service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = persons_service

        # Setup notes service (global_service strategy)
        notes_service = MagicMock()
        note1 = MagicMock()
        note1.model_dump = MagicMock(return_value={"id": 10, "content": "Note for Alice"})
        note2 = MagicMock()
        note2.model_dump = MagicMock(return_value={"id": 20, "content": "Note for Bob"})
        notes_service.list = AsyncMock(
            side_effect=[
                MagicMock(data=[note1]),
                MagicMock(data=[note2]),
            ]
        )
        mock_client.notes = notes_service

        query = Query(from_="persons", include=["notes"])
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity="persons",
                    relationship="notes",
                    description="Include notes",
                    depends_on=[0],
                ),
            ],
            total_api_calls=3,
            estimated_records_fetched=2,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=True,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        assert len(result.data) == 2
        assert "notes" in result.included
        assert len(result.included["notes"]) == 2

    @pytest.mark.asyncio
    async def test_include_unknown_relationship_raises(self, mock_client: AsyncMock) -> None:
        """Test include with unknown relationship raises error."""
        persons_service = MagicMock()
        records = [{"id": 1, "name": "Alice"}]
        persons_service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = persons_service

        query = Query(from_="persons", include=["unknown_rel"])
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity="persons",
                    relationship="unknown_rel",
                    description="Include unknown",
                    depends_on=[0],
                ),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        with pytest.raises(QueryExecutionError, match="Unknown relationship"):
            await executor.execute(plan)

    @pytest.mark.asyncio
    async def test_include_with_missing_entity_skipped(self, mock_client: AsyncMock) -> None:
        """Test include step with no entity/relationship is skipped."""
        persons_service = MagicMock()
        records = [{"id": 1, "name": "Alice"}]
        persons_service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = persons_service

        query = Query(from_="persons")
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity=None,  # Missing entity
                    relationship=None,  # Missing relationship
                    description="No-op include",
                    depends_on=[0],
                ),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should complete without error
        assert len(result.data) == 1

    @pytest.mark.asyncio
    async def test_include_entity_method_handles_errors(self, mock_client: AsyncMock) -> None:
        """Test include gracefully handles errors in entity method calls."""
        persons_service = MagicMock()
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        persons_service.all.return_value = create_mock_page_iterator(records)
        # First call succeeds, second raises error
        persons_service.get_associated_company_ids = AsyncMock(
            side_effect=[[100], Exception("API Error")]
        )
        mock_client.persons = persons_service

        # Setup companies service for batch fetch
        async def mock_companies_iter(ids: list[int]):
            for id_ in ids:
                yield MagicMock(
                    model_dump=MagicMock(
                        return_value={"id": id_, "name": f"Company {id_}", "domain": f"co{id_}.com"}
                    )
                )

        companies_service = MagicMock()
        companies_service.iter = mock_companies_iter
        mock_client.companies = companies_service

        query = Query(from_="persons", include=["companies"])
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity="persons",
                    relationship="companies",
                    description="Include companies",
                    depends_on=[0],
                ),
            ],
            total_api_calls=3,
            estimated_records_fetched=2,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=True,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should complete with partial results (only Alice's companies)
        assert len(result.data) == 2
        assert "companies" in result.included
        assert len(result.included["companies"]) == 1  # Only from Alice
        # Verify included_by_parent still populated for successful fetches
        assert "companies" in result.included_by_parent
        assert 1 in result.included_by_parent["companies"]  # Alice's ID
        assert 2 in result.included_by_parent["companies"]  # Bob's ID (empty due to error)
        assert len(result.included_by_parent["companies"][1]) == 1  # Alice has 1 company
        assert len(result.included_by_parent["companies"][2]) == 0  # Bob has 0 (error)

    @pytest.mark.asyncio
    async def test_include_record_missing_id(self, mock_client: AsyncMock) -> None:
        """Test include skips records without id field."""
        persons_service = MagicMock()
        # Record without id field
        records = [{"name": "No ID"}]
        persons_service.all.return_value = create_mock_page_iterator(records)
        persons_service.get_associated_company_ids = AsyncMock(return_value=[100])
        mock_client.persons = persons_service

        query = Query(from_="persons", include=["companies"])
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity="persons",
                    relationship="companies",
                    description="Include companies",
                    depends_on=[0],
                ),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=True,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should complete but no companies included
        assert len(result.data) == 1
        assert result.included["companies"] == []

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-INCLUDE-BATCH-001")
    async def test_batch_fetch_handles_deleted_entities(self, mock_client: AsyncMock) -> None:
        """Test batch fetch gracefully handles deleted/missing entities.

        When some IDs don't return data from batch fetch (e.g., deleted entities),
        the executor should include only the records that exist.
        """
        persons_service = MagicMock()
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        persons_service.all.return_value = create_mock_page_iterator(records)
        # Alice has companies 100, 101; Bob has company 102
        # Company 101 was deleted (won't be returned by batch fetch)
        persons_service.get_associated_company_ids = AsyncMock(side_effect=[[100, 101], [102]])
        mock_client.persons = persons_service

        # Setup companies service - only returns 100 and 102, not 101 (deleted)
        async def mock_companies_iter(ids: list[int]):
            # Simulate 101 being deleted - only return 100 and 102
            for id_ in ids:
                if id_ == 101:
                    continue  # Deleted entity - not returned
                yield MagicMock(
                    model_dump=MagicMock(
                        return_value={"id": id_, "name": f"Company {id_}", "domain": f"co{id_}.com"}
                    )
                )

        companies_service = MagicMock()
        companies_service.iter = mock_companies_iter
        mock_client.companies = companies_service

        query = Query(from_="persons", include=["companies"])
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity="persons",
                    relationship="companies",
                    description="Include companies",
                    depends_on=[0],
                ),
            ],
            total_api_calls=3,
            estimated_records_fetched=2,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=True,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should have 3 entries in included (100, 101 as stub, 102)
        # 101 is missing from API but included as ID-only fallback for parent mapping
        assert len(result.data) == 2
        included_ids = {c["id"] for c in result.included["companies"]}
        assert 100 in included_ids
        assert 102 in included_ids
        # 101 is in included as ID-only fallback (preserves parent mapping)
        assert 101 in included_ids

        # Verify 100 and 102 have full data, 101 only has id
        companies_by_id = {c["id"]: c for c in result.included["companies"]}
        assert "name" in companies_by_id[100]  # Full record
        assert "name" in companies_by_id[102]  # Full record
        # 101 should be ID-only stub (no name field or just id field)
        assert len(companies_by_id[101]) == 1 or "name" not in companies_by_id[101]

        # Check per-parent mapping
        # Alice had [100, 101] - should have both (101 as stub)
        alice_companies = result.included_by_parent["companies"][1]
        alice_ids = {c.get("id") for c in alice_companies}
        assert 100 in alice_ids
        # 101 should be tracked as ID-only fallback
        assert 101 in alice_ids
        # Bob had [102] - should have just 102
        bob_companies = result.included_by_parent["companies"][2]
        assert len(bob_companies) == 1
        assert bob_companies[0]["id"] == 102


# =============================================================================
# Tests for Aggregate with GroupBy + HAVING
# =============================================================================


class TestAggregateWithGroupByAndHaving:
    """Tests for _execute_aggregate with groupBy and HAVING."""

    @pytest.mark.asyncio
    async def test_aggregate_groupby_with_having(self, mock_client: AsyncMock) -> None:
        """Test aggregate with groupBy and HAVING filter."""
        from affinity.cli.query.models import HavingClause

        service = MagicMock()
        records = [
            {"id": 1, "status": "Active", "amount": 100},
            {"id": 2, "status": "Active", "amount": 200},
            {"id": 3, "status": "Inactive", "amount": 50},
            {"id": 4, "status": "Pending", "amount": 300},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            group_by="status",
            aggregate={"total": AggregateFunc(sum="amount"), "count": AggregateFunc(count=True)},
            having=HavingClause(path="total", op="gt", value=100),
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="aggregate", description="Aggregate", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=4,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Only Active (total=300) and Pending (total=300) should pass having filter
        assert len(result.data) == 2
        statuses = [r["status"] for r in result.data]
        assert "Active" in statuses
        assert "Pending" in statuses
        assert "Inactive" not in statuses

    @pytest.mark.asyncio
    async def test_aggregate_groupby_without_having(self, mock_client: AsyncMock) -> None:
        """Test aggregate with groupBy but no HAVING."""
        service = MagicMock()
        records = [
            {"id": 1, "status": "A", "amount": 100},
            {"id": 2, "status": "B", "amount": 200},
            {"id": 3, "status": "A", "amount": 150},
        ]
        service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = service

        query = Query(
            from_="persons",
            group_by="status",
            aggregate={"total": AggregateFunc(sum="amount")},
            # No having clause
        )
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="aggregate", description="Aggregate", depends_on=[0]),
            ],
            total_api_calls=1,
            estimated_records_fetched=3,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Both groups should be present
        assert len(result.data) == 2


# =============================================================================
# Tests for KeyboardInterrupt with allow_partial
# =============================================================================


class TestKeyboardInterruptHandling:
    """Tests for KeyboardInterrupt handling."""

    @pytest.mark.asyncio
    async def test_interrupt_without_allow_partial_raises(self, mock_client: AsyncMock) -> None:
        """KeyboardInterrupt without allow_partial raises QueryInterruptedError."""
        from affinity.cli.query import QueryInterruptedError

        service = MagicMock()

        # Create a mock page iterator that raises KeyboardInterrupt
        class InterruptingPageIterator:
            def pages(self, on_progress=None):  # noqa: ARG002
                async def generator():
                    raise KeyboardInterrupt()
                    yield  # Make it a generator

                return generator()

        service.all.return_value = InterruptingPageIterator()
        mock_client.persons = service

        query = Query(from_="persons")
        plan = ExecutionPlan(
            query=query,
            steps=[PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch")],
            total_api_calls=1,
            estimated_records_fetched=100,
            estimated_memory_mb=0.1,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client, allow_partial=False)
        with pytest.raises(QueryInterruptedError) as exc:
            await executor.execute(plan)

        assert "interrupted" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_interrupt_with_allow_partial_returns_results(
        self, mock_client: AsyncMock
    ) -> None:
        """KeyboardInterrupt with allow_partial returns partial results."""
        service = MagicMock()

        # Create a mock page iterator that yields one page then raises KeyboardInterrupt
        class PartialPageIterator:
            def pages(self, on_progress=None):  # noqa: ARG002
                async def generator():
                    # Yield one page of records
                    page = MagicMock()
                    page.data = [
                        create_mock_record({"id": 1, "name": "Alice"}),
                        create_mock_record({"id": 2, "name": "Bob"}),
                    ]
                    yield page
                    # Then raise interrupt
                    raise KeyboardInterrupt()

                return generator()

        service.all.return_value = PartialPageIterator()
        mock_client.persons = service

        query = Query(from_="persons")
        plan = ExecutionPlan(
            query=query,
            steps=[PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch")],
            total_api_calls=1,
            estimated_records_fetched=100,
            estimated_memory_mb=0.1,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client, allow_partial=True)
        result = await executor.execute(plan)

        # Should return partial results
        assert len(result.data) == 2
        assert result.meta["interrupted"] is True


# =============================================================================
# Tests for Parent ID Extraction with IN Operator
# =============================================================================


class TestExtractParentIdsWithInOperator:
    """Tests for _extract_parent_ids with IN operator."""

    @pytest.fixture
    def executor(self) -> QueryExecutor:
        """Create executor for testing."""
        return QueryExecutor(MagicMock(), max_records=100)

    def test_in_operator_extracts_multiple_ids(self, executor: QueryExecutor) -> None:
        """IN operator extracts all IDs from list."""
        where = {"path": "listId", "op": "in", "value": [100, 200, 300]}
        result = executor._extract_parent_ids(where, "listId")
        assert result == [100, 200, 300]

    def test_in_operator_with_string_ids(self, executor: QueryExecutor) -> None:
        """IN operator converts string IDs to int."""
        where = {"path": "listId", "op": "in", "value": ["100", "200"]}
        result = executor._extract_parent_ids(where, "listId")
        assert result == [100, 200]

    def test_in_operator_skips_invalid_values(self, executor: QueryExecutor) -> None:
        """IN operator skips non-convertible values."""
        where = {"path": "listId", "op": "in", "value": [100, "abc", 200, None]}
        result = executor._extract_parent_ids(where, "listId")
        assert result == [100, 200]

    def test_in_operator_with_non_list_returns_empty(self, executor: QueryExecutor) -> None:
        """IN operator with non-list value returns empty."""
        where = {"path": "listId", "op": "in", "value": "not a list"}
        result = executor._extract_parent_ids(where, "listId")
        assert result == []

    def test_combined_eq_and_in_in_or(self, executor: QueryExecutor) -> None:
        """OR with eq and in operators extracts all IDs."""
        where = {
            "or": [
                {"path": "listId", "op": "eq", "value": 100},
                {"path": "listId", "op": "in", "value": [200, 300]},
            ]
        }
        result = executor._extract_parent_ids(where, "listId")
        assert sorted(result) == [100, 200, 300]


# =============================================================================
# Tests for Field Refs Collection from Aggregates
# =============================================================================


class TestCollectFieldRefsFromAggregates:
    """Tests for collecting field references from aggregate clauses."""

    @pytest.fixture
    def executor(self, mock_client: AsyncMock) -> QueryExecutor:
        """Create executor for testing."""
        return QueryExecutor(mock_client, max_records=100)

    def test_collects_from_sum_aggregate(self, executor: QueryExecutor) -> None:
        """Collects field from sum aggregate."""
        query = Query(
            from_="listEntries",
            aggregate={"total": AggregateFunc(sum="fields.Amount")},
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == {"Amount"}

    def test_collects_from_avg_aggregate(self, executor: QueryExecutor) -> None:
        """Collects field from avg aggregate."""
        query = Query(
            from_="listEntries",
            aggregate={"average": AggregateFunc(avg="fields.Score")},
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == {"Score"}

    def test_collects_from_percentile_aggregate(self, executor: QueryExecutor) -> None:
        """Collects field from percentile aggregate."""
        query = Query(
            from_="listEntries",
            aggregate={"p90": AggregateFunc(percentile={"field": "fields.Value", "p": 90})},
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == {"Value"}

    def test_wildcard_in_aggregate_returns_star(self, executor: QueryExecutor) -> None:
        """Wildcard in aggregate returns {'*'}."""
        query = Query(
            from_="listEntries",
            aggregate={"first": AggregateFunc(first="fields.*")},
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == {"*"}

    def test_wildcard_in_groupby_returns_star(self, executor: QueryExecutor) -> None:
        """Wildcard in groupBy returns {'*'}."""
        query = Query(
            from_="listEntries",
            group_by="fields.*",
            aggregate={"count": AggregateFunc(count=True)},
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == {"*"}

    def test_collects_from_multiple_aggregates(self, executor: QueryExecutor) -> None:
        """Collects fields from multiple aggregates."""
        query = Query(
            from_="listEntries",
            aggregate={
                "sum": AggregateFunc(sum="fields.Amount"),
                "avg": AggregateFunc(avg="fields.Score"),
                "min": AggregateFunc(min="fields.Price"),
            },
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == {"Amount", "Score", "Price"}

    def test_no_fields_refs_returns_empty(self, executor: QueryExecutor) -> None:
        """Query without field refs returns empty set."""
        query = Query(
            from_="listEntries",
            aggregate={"count": AggregateFunc(count=True)},
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == set()

    def test_percentile_wildcard_returns_star(self, executor: QueryExecutor) -> None:
        """Percentile with wildcard field returns {'*'}."""
        query = Query(
            from_="listEntries",
            aggregate={"p50": AggregateFunc(percentile={"field": "fields.*", "p": 50})},
        )
        fields = executor._collect_field_refs_from_query(query)
        assert fields == {"*"}


# =============================================================================
# Tests for _execute_filter with Resolved Where
# =============================================================================


class TestExecuteFilterWithResolvedWhere:
    """Tests for _execute_filter using resolved where clause."""

    @pytest.mark.asyncio
    async def test_filter_uses_resolved_where(self, mock_client: AsyncMock) -> None:
        """Filter step uses resolved where clause when available."""
        # Create executor context directly to test resolved_where
        query = Query(
            from_="listEntries",
            where=WhereClause(path="listName", op="eq", value="My List"),
        )
        ctx = ExecutionContext(query=query, max_records=100)

        # Simulate records already fetched
        ctx.records = [
            {"id": 1, "listId": 123, "name": "Entry 1"},
            {"id": 2, "listId": 456, "name": "Entry 2"},
        ]

        # Set resolved_where (as if listName was resolved to listId)
        ctx.resolved_where = {"path": "listId", "op": "eq", "value": 123}

        executor = QueryExecutor(mock_client, max_records=100)
        step = PlanStep(step_id=1, operation="filter", description="Filter")

        executor._execute_filter(step, ctx)

        # Only record with listId=123 should remain
        assert len(ctx.records) == 1
        assert ctx.records[0]["listId"] == 123

    @pytest.mark.asyncio
    async def test_filter_without_resolved_where_uses_query_where(
        self, mock_client: AsyncMock
    ) -> None:
        """Filter step uses query.where when resolved_where is None."""
        query = Query(
            from_="persons",
            where=WhereClause(path="name", op="eq", value="Alice"),
        )
        ctx = ExecutionContext(query=query, max_records=100)
        ctx.records = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        ctx.resolved_where = None  # Not resolved

        executor = QueryExecutor(mock_client, max_records=100)
        step = PlanStep(step_id=1, operation="filter", description="Filter")

        executor._execute_filter(step, ctx)

        assert len(ctx.records) == 1
        assert ctx.records[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_filter_with_no_where_clause_keeps_all(self, mock_client: AsyncMock) -> None:
        """Filter step with no where clause keeps all records."""
        query = Query(from_="persons")  # No where
        ctx = ExecutionContext(query=query, max_records=100)
        ctx.records = [{"id": 1}, {"id": 2}, {"id": 3}]

        executor = QueryExecutor(mock_client, max_records=100)
        step = PlanStep(step_id=1, operation="filter", description="Filter")

        executor._execute_filter(step, ctx)

        assert len(ctx.records) == 3

    @pytest.mark.asyncio
    async def test_filter_with_normalized_alias_entityName(self, mock_client: AsyncMock) -> None:
        """Filter works with normalized entityName alias on listEntries."""
        query = Query(
            from_="listEntries",
            where=WhereClause(path="entityName", op="contains", value="Acme"),
        )
        ctx = ExecutionContext(query=query, max_records=100)
        # Records after normalization (entityName alias added)
        ctx.records = [
            {"id": 1, "listEntryId": 1, "entityId": 100, "entityName": "Acme Corp", "fields": {}},
            {"id": 2, "listEntryId": 2, "entityId": 101, "entityName": "Beta Inc", "fields": {}},
            {"id": 3, "listEntryId": 3, "entityId": 102, "entityName": "Acme Labs", "fields": {}},
        ]

        executor = QueryExecutor(mock_client, max_records=100)
        step = PlanStep(step_id=1, operation="filter", description="Filter")

        executor._execute_filter(step, ctx)

        # Only records with "Acme" in entityName should remain
        assert len(ctx.records) == 2
        assert ctx.records[0]["entityName"] == "Acme Corp"
        assert ctx.records[1]["entityName"] == "Acme Labs"


# =============================================================================
# Tests for Fetch Errors
# =============================================================================


class TestFetchErrors:
    """Tests for fetch error handling."""

    @pytest.mark.asyncio
    async def test_fetch_missing_entity_raises(self, mock_client: AsyncMock) -> None:
        """Fetch step without entity raises error."""
        query = Query(from_="persons")
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity=None,  # Missing entity
                    description="Fetch",
                ),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        with pytest.raises(QueryExecutionError, match="missing entity"):
            await executor.execute(plan)

    @pytest.mark.asyncio
    async def test_fetch_unknown_entity_raises(self, mock_client: AsyncMock) -> None:
        """Fetch step with unknown entity raises error."""
        query = Query(from_="unknown_entity")
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(
                    step_id=0,
                    operation="fetch",
                    entity="unknown_entity",
                    description="Fetch",
                ),
            ],
            total_api_calls=1,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=False,
        )

        executor = QueryExecutor(mock_client)
        with pytest.raises(QueryExecutionError, match="Unknown entity"):
            await executor.execute(plan)


# =============================================================================
# Pre-Include Helper Function Tests
# =============================================================================


class TestNeedsFullRecords:
    """Tests for _needs_full_records() helper function."""

    def test_returns_false_for_none(self) -> None:
        """Returns False for None where clause."""
        from affinity.cli.query.executor import _needs_full_records

        assert _needs_full_records(None) is False

    def test_returns_false_for_id_only(self) -> None:
        """Returns False when only id field is referenced."""
        from affinity.cli.query.executor import _needs_full_records

        where = WhereClause(path="id", op="gt", value=0)
        assert _needs_full_records(where) is False

    def test_returns_true_for_non_id_field(self) -> None:
        """Returns True when non-id field is referenced."""
        from affinity.cli.query.executor import _needs_full_records

        where = WhereClause(path="name", op="contains", value="Acme")
        assert _needs_full_records(where) is True

    def test_returns_true_for_nested_non_id(self) -> None:
        """Returns True when non-id field is in compound clause."""
        from affinity.cli.query.executor import _needs_full_records

        where = WhereClause(
            and_=[
                WhereClause(path="id", op="gt", value=0),
                WhereClause(path="name", op="contains", value="Inc"),
            ]
        )
        assert _needs_full_records(where) is True


class TestAnyQuantifierNeedsFullRecords:
    """Tests for _any_quantifier_needs_full_records() helper function."""

    def test_returns_false_for_none(self) -> None:
        """Returns False for None where clause."""
        from affinity.cli.query.executor import _any_quantifier_needs_full_records
        from affinity.cli.query.schema import SCHEMA_REGISTRY

        schema = SCHEMA_REGISTRY["persons"]
        assert _any_quantifier_needs_full_records(None, "companies", schema) is False

    def test_returns_false_for_id_only_quantifier(self) -> None:
        """Returns False when quantifier only references id."""
        from affinity.cli.query.executor import _any_quantifier_needs_full_records
        from affinity.cli.query.models import QuantifierClause
        from affinity.cli.query.schema import SCHEMA_REGISTRY

        schema = SCHEMA_REGISTRY["persons"]
        where = WhereClause(
            all_=QuantifierClause(
                path="companies",
                where=WhereClause(path="id", op="gt", value=0),
            )
        )
        assert _any_quantifier_needs_full_records(where, "companies", schema) is False

    def test_returns_true_for_non_id_quantifier(self) -> None:
        """Returns True when quantifier references non-id field."""
        from affinity.cli.query.executor import _any_quantifier_needs_full_records
        from affinity.cli.query.models import QuantifierClause
        from affinity.cli.query.schema import SCHEMA_REGISTRY

        schema = SCHEMA_REGISTRY["persons"]
        where = WhereClause(
            all_=QuantifierClause(
                path="companies",
                where=WhereClause(path="name", op="contains", value="Inc"),
            )
        )
        assert _any_quantifier_needs_full_records(where, "companies", schema) is True

    def test_checks_all_quantifiers_for_relationship(self) -> None:
        """Checks ALL quantifiers for a relationship, not just first match."""
        from affinity.cli.query.executor import _any_quantifier_needs_full_records
        from affinity.cli.query.models import QuantifierClause
        from affinity.cli.query.schema import SCHEMA_REGISTRY

        schema = SCHEMA_REGISTRY["persons"]
        # First quantifier only needs id, second needs name
        where = WhereClause(
            and_=[
                WhereClause(
                    all_=QuantifierClause(
                        path="companies",
                        where=WhereClause(path="id", op="gt", value=0),
                    )
                ),
                WhereClause(
                    none_=QuantifierClause(
                        path="companies",
                        where=WhereClause(path="name", op="contains", value="Spam"),
                    )
                ),
            ]
        )
        # Should return True because second quantifier needs "name"
        assert _any_quantifier_needs_full_records(where, "companies", schema) is True

    def test_ignores_other_relationships(self) -> None:
        """Ignores quantifiers on other relationships."""
        from affinity.cli.query.executor import _any_quantifier_needs_full_records
        from affinity.cli.query.models import QuantifierClause
        from affinity.cli.query.schema import SCHEMA_REGISTRY

        schema = SCHEMA_REGISTRY["persons"]
        where = WhereClause(
            all_=QuantifierClause(
                path="companies",  # Different relationship
                where=WhereClause(path="name", op="contains", value="Inc"),
            )
        )
        # Should return False for "opportunities" relationship
        assert _any_quantifier_needs_full_records(where, "opportunities", schema) is False


class TestPreIncludeExecution:
    """Tests for _execute_filter_with_preinclude method."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        """Create a mock AsyncAffinity client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_simple_filter_skips_preinclude(self, mock_client: AsyncMock) -> None:
        """Simple filter without quantifiers skips pre-include step."""
        # Set up mock service using the helper function
        records = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        mock_service = MagicMock()
        mock_service.all.return_value = create_mock_page_iterator(records)
        mock_client.persons = mock_service

        # Create a simple query without quantifiers
        query = Query(from_="persons", where=WhereClause(path="name", op="eq", value="Alice"))
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(step_id=1, operation="filter", description="Filter"),
            ],
            total_api_calls=1,
            estimated_records_fetched=10,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=False,
            requires_full_scan=True,
        )

        executor = QueryExecutor(mock_client)
        result = await executor.execute(plan)

        # Should have filtered down to just Alice
        assert len(result.data) == 1
        assert result.data[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_quantifier_filter_triggers_preinclude(self, mock_client: AsyncMock) -> None:
        """Filter with quantifier triggers pre-include data fetch."""
        from affinity.cli.query.executor import ExecutionContext
        from affinity.cli.query.models import QuantifierClause

        # Create query with quantifier
        query = Query(
            from_="persons",
            where=WhereClause(
                all_=QuantifierClause(
                    path="companies",
                    where=WhereClause(path="name", op="contains", value="Inc"),
                )
            ),
        )

        # Create execution context with existing records
        ctx = ExecutionContext(
            query=query,
            records=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
        )

        # Mock the persons service for relationship fetching
        mock_persons_service = AsyncMock()

        async def mock_get_associated_company_ids(person_id: int) -> list[int]:
            if person_id == 1:
                return [101, 102]  # Alice has 2 companies
            return []  # Bob has no companies

        mock_persons_service.get_associated_company_ids = mock_get_associated_company_ids
        mock_client.persons = mock_persons_service

        # Mock companies service for batch fetch
        mock_companies_service = AsyncMock()

        async def mock_get(company_id: int):
            result = MagicMock()
            result.model_dump = MagicMock(
                return_value={"id": company_id, "name": f"Company {company_id} Inc"}
            )
            return result

        mock_companies_service.get = mock_get
        mock_client.companies = mock_companies_service

        executor = QueryExecutor(mock_client)

        # Create a mock PlanStep for the filter operation
        step = PlanStep(step_id=1, operation="filter", description="Filter")

        # Execute the filter with pre-include
        await executor._execute_filter_with_preinclude(step, ctx)

        # Alice should match (all her companies have "Inc" in name)
        # Bob should match too (vacuous truth - no companies)
        assert len(ctx.records) == 2

        # Verify relationship data was populated
        assert "companies" in ctx.relationship_data
        assert 1 in ctx.relationship_data["companies"]  # Alice's data


# =============================================================================
# Integration Tests for IDs-Only Upgrade
# =============================================================================


class TestIDsOnlyUpgrade:
    """Tests for IDs-only relationship upgrade to full records.

    When a relationship method returns only IDs (e.g., get_associated_company_ids),
    but the quantifier's WHERE clause filters on other fields (e.g., name),
    the executor must "upgrade" by batch-fetching full records.
    """

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        """Create a mock AsyncAffinity client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_ids_only_no_upgrade_for_count(self, mock_client: AsyncMock) -> None:
        """_count queries don't need full records, just counts."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor

        # Set up mock persons service returning IDs
        mock_persons = AsyncMock()
        mock_persons.get_associated_company_ids = AsyncMock(return_value=[101, 102])
        mock_client.persons = mock_persons

        # Don't set up companies.get - if called, test will fail
        mock_client.companies = MagicMock(spec=[])  # No 'get' method

        query = Query(
            from_="persons",
            where=WhereClause(path="companies._count", op="gte", value=1),
        )
        ctx = ExecutionContext(
            query=query,
            records=[{"id": 1, "name": "Alice"}],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        # Should NOT raise - doesn't need to batch fetch full records
        await executor._execute_filter_with_preinclude(step, ctx)

        # Count is 2 >= 1, so Alice should pass
        assert len(ctx.records) == 1
        assert ctx.relationship_counts.get("companies", {}).get(1) == 2

    @pytest.mark.asyncio
    async def test_ids_only_upgrades_for_property_filter(self, mock_client: AsyncMock) -> None:
        """When filtering on properties, IDs must be upgraded to full records."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import QuantifierClause

        # Set up mock persons service returning IDs only
        mock_persons = AsyncMock()
        mock_persons.get_associated_company_ids = AsyncMock(return_value=[101, 102])
        mock_client.persons = mock_persons

        # Set up mock companies service for batch fetch
        mock_companies = AsyncMock()

        async def mock_get(company_id: int):
            result = MagicMock()
            if company_id == 101:
                result.model_dump = MagicMock(return_value={"id": 101, "name": "Acme Inc"})
            else:
                result.model_dump = MagicMock(return_value={"id": 102, "name": "Tech Corp"})
            return result

        mock_companies.get = mock_get
        mock_client.companies = mock_companies

        # Query filters on 'name' which isn't in IDs-only response
        query = Query(
            from_="persons",
            where=WhereClause(
                all_=QuantifierClause(
                    path="companies",
                    where=WhereClause(path="name", op="contains", value="Inc"),
                )
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[{"id": 1, "name": "Alice"}],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        await executor._execute_filter_with_preinclude(step, ctx)

        # Only Acme Inc has "Inc", so all_ should return False (Tech Corp doesn't match)
        assert len(ctx.records) == 0  # Alice filtered out

    @pytest.mark.asyncio
    async def test_ids_only_no_upgrade_for_id_filter(self, mock_client: AsyncMock) -> None:
        """Filtering only on 'id' doesn't need full records."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import QuantifierClause

        # Set up mock persons service returning IDs only
        mock_persons = AsyncMock()
        mock_persons.get_associated_company_ids = AsyncMock(return_value=[101, 102])
        mock_client.persons = mock_persons

        # Don't set up companies.get - shouldn't be called
        mock_client.companies = MagicMock(spec=[])

        # Query filters only on 'id' which IS in IDs-only response
        query = Query(
            from_="persons",
            where=WhereClause(
                all_=QuantifierClause(
                    path="companies",
                    where=WhereClause(path="id", op="gt", value=100),
                )
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[{"id": 1, "name": "Alice"}],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        # Should NOT raise - doesn't need batch fetch
        await executor._execute_filter_with_preinclude(step, ctx)

        # Both companies have id > 100, so all_ returns True
        assert len(ctx.records) == 1

    @pytest.mark.asyncio
    async def test_multiple_quantifiers_mixed_fields_upgrades(self, mock_client: AsyncMock) -> None:
        """Multiple quantifiers on same rel: one needs id, one needs name -> upgrade.

        CRITICAL TEST: This catches the bug where only the first quantifier's
        where clause was checked. The second quantifier needs 'name' field,
        so full records must be fetched.
        """
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import QuantifierClause

        # Set up mock persons service returning IDs only
        mock_persons = AsyncMock()
        mock_persons.get_associated_company_ids = AsyncMock(return_value=[101, 102])
        mock_client.persons = mock_persons

        # Set up mock companies service for batch fetch
        mock_companies = AsyncMock()

        async def mock_get(company_id: int):
            result = MagicMock()
            if company_id == 101:
                result.model_dump = MagicMock(return_value={"id": 101, "name": "Acme Inc"})
            else:
                result.model_dump = MagicMock(return_value={"id": 102, "name": "Good Corp"})
            return result

        mock_companies.get = mock_get
        mock_client.companies = mock_companies

        # Query has TWO quantifiers on companies:
        # - First filters only on 'id' (wouldn't need upgrade alone)
        # - Second filters on 'name' (needs upgrade)
        query = Query(
            from_="persons",
            where=WhereClause(
                and_=[
                    WhereClause(
                        all_=QuantifierClause(
                            path="companies",
                            where=WhereClause(path="id", op="gt", value=0),
                        )
                    ),
                    WhereClause(
                        none_=QuantifierClause(
                            path="companies",
                            where=WhereClause(path="name", op="contains", value="Spam"),
                        )
                    ),
                ]
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[{"id": 1, "name": "Alice"}],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        await executor._execute_filter_with_preinclude(step, ctx)

        # All companies have id > 0 (passes all_)
        # No companies have "Spam" in name (passes none_)
        # Alice should pass both conditions
        assert len(ctx.records) == 1

        # Verify full records were fetched (have 'name' field)
        company_data = ctx.relationship_data.get("companies", {}).get(1, [])
        assert len(company_data) == 2
        assert all("name" in c for c in company_data)


# =============================================================================
# Integration Tests for Quantifier Execution
# =============================================================================


class TestQuantifierIntegration:
    """End-to-end tests for quantifier query execution."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        """Create a mock AsyncAffinity client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_all_quantifier_filters_correctly(self, mock_client: AsyncMock) -> None:
        """all_ quantifier correctly filters based on all related items."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import QuantifierClause

        # Set up mock with two persons: Alice and Bob
        # Alice: all companies have ".com" domain
        # Bob: one company has ".org" domain
        mock_persons = AsyncMock()

        async def mock_get_companies(person_id: int) -> list[int]:
            if person_id == 1:  # Alice
                return [101, 102]
            return [103, 104]  # Bob

        mock_persons.get_associated_company_ids = mock_get_companies
        mock_client.persons = mock_persons

        # Set up companies
        mock_companies = AsyncMock()

        async def mock_get(company_id: int):
            result = MagicMock()
            domains = {
                101: "acme.com",
                102: "tech.com",
                103: "good.com",
                104: "nonprofit.org",  # Bob has this - doesn't match ".com"
            }
            result.model_dump = MagicMock(
                return_value={"id": company_id, "domain": domains[company_id]}
            )
            return result

        mock_companies.get = mock_get
        mock_client.companies = mock_companies

        query = Query(
            from_="persons",
            where=WhereClause(
                all_=QuantifierClause(
                    path="companies",
                    where=WhereClause(path="domain", op="contains", value=".com"),
                )
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        await executor._execute_filter_with_preinclude(step, ctx)

        # Only Alice passes - all her companies have .com
        # Bob fails - nonprofit.org doesn't contain .com
        assert len(ctx.records) == 1
        assert ctx.records[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_none_quantifier_filters_correctly(self, mock_client: AsyncMock) -> None:
        """none_ quantifier correctly filters based on no related items matching."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import QuantifierClause

        # Alice: no companies with "spam" in name
        # Bob: one company with "spam" in name
        mock_persons = AsyncMock()

        async def mock_get_companies(person_id: int) -> list[int]:
            return [101] if person_id == 1 else [102]

        mock_persons.get_associated_company_ids = mock_get_companies
        mock_client.persons = mock_persons

        mock_companies = AsyncMock()

        async def mock_get(company_id: int):
            result = MagicMock()
            names = {
                101: "Acme Corp",
                102: "Spam Inc",  # Bob's company
            }
            result.model_dump = MagicMock(
                return_value={"id": company_id, "name": names[company_id]}
            )
            return result

        mock_companies.get = mock_get
        mock_client.companies = mock_companies

        query = Query(
            from_="persons",
            where=WhereClause(
                none_=QuantifierClause(
                    path="companies",
                    where=WhereClause(path="name", op="contains", value="Spam"),
                )
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        await executor._execute_filter_with_preinclude(step, ctx)

        # Alice passes - no spam companies
        # Bob fails - has "Spam Inc"
        assert len(ctx.records) == 1
        assert ctx.records[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_exists_with_filter(self, mock_client: AsyncMock) -> None:
        """exists_ with where clause correctly filters."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import ExistsClause

        # Alice: has email interaction
        # Bob: has call interaction only
        mock_persons = AsyncMock()
        mock_client.persons = mock_persons

        mock_interactions = AsyncMock()

        async def mock_list(**kwargs):
            person_id = kwargs.get("person_id")
            result = MagicMock()
            if person_id == 1:  # Alice
                item = MagicMock()
                item.model_dump = MagicMock(return_value={"id": 1, "type": "email"})
                result.data = [item]
            else:  # Bob
                item = MagicMock()
                item.model_dump = MagicMock(return_value={"id": 2, "type": "call"})
                result.data = [item]
            return result

        mock_interactions.list = mock_list

        # _fetch_interactions_for_entity uses iter() which yields from async generator
        from affinity.models.pagination import AsyncPageIterator, PaginatedResponse

        def mock_iter(**kwargs):
            async def fetch_page(_cursor):
                result = await mock_list(**kwargs)
                items = result.data
                return PaginatedResponse(data=items)

            return AsyncPageIterator(fetch_page)

        mock_interactions.iter = mock_iter
        mock_client.interactions = mock_interactions

        query = Query(
            from_="persons",
            where=WhereClause(
                exists_=ExistsClause(
                    **{
                        "from": "interactions",
                        "where": {"path": "type", "op": "eq", "value": "email"},
                    }
                )
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        await executor._execute_filter_with_preinclude(step, ctx)

        # Only Alice has email interaction
        assert len(ctx.records) == 1
        assert ctx.records[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_count_filter(self, mock_client: AsyncMock) -> None:
        """_count pseudo-field correctly counts related items."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor

        # Alice: 3 companies
        # Bob: 1 company
        mock_persons = AsyncMock()

        async def mock_get_companies(person_id: int) -> list[int]:
            return [101, 102, 103] if person_id == 1 else [104]

        mock_persons.get_associated_company_ids = mock_get_companies
        mock_client.persons = mock_persons

        query = Query(
            from_="persons",
            where=WhereClause(path="companies._count", op="gte", value=2),
        )
        ctx = ExecutionContext(
            query=query,
            records=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        await executor._execute_filter_with_preinclude(step, ctx)

        # Alice has 3 >= 2, Bob has 1 < 2
        assert len(ctx.records) == 1
        assert ctx.records[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_vacuous_truth_for_empty_relationship(self, mock_client: AsyncMock) -> None:
        """all_ returns True for records with no related items (vacuous truth)."""
        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import QuantifierClause

        # Alice: no companies
        mock_persons = AsyncMock()
        mock_persons.get_associated_company_ids = AsyncMock(return_value=[])
        mock_client.persons = mock_persons

        query = Query(
            from_="persons",
            where=WhereClause(
                all_=QuantifierClause(
                    path="companies",
                    where=WhereClause(path="name", op="contains", value="Inc"),
                )
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[{"id": 1, "name": "Alice"}],
        )

        step = PlanStep(step_id=1, operation="filter", description="Filter")
        executor = QueryExecutor(mock_client)

        await executor._execute_filter_with_preinclude(step, ctx)

        # Alice passes - vacuous truth (no companies to fail the condition)
        assert len(ctx.records) == 1


# =============================================================================
# Integration Tests for Field Value Fetching
# =============================================================================
# NOTE: Full integration tests using httpx.MockTransport with AsyncAffinity client
# are deferred due to complexity with pytest-asyncio + MockTransport async handler
# setup. The unit tests above verify all field extraction and resolution logic.
# Integration testing should be done via manual testing or a dedicated integration
# test suite that runs against a test environment.


# =============================================================================
# Phase 3: _batch_fetch_by_ids V2 batch lookup tests
# =============================================================================


class TestBatchFetchByIds:
    """Tests for _batch_fetch_by_ids V2 batch optimization."""

    @pytest.mark.asyncio
    async def test_batch_fetch_uses_iter_for_v2_entities(self, mock_client: AsyncMock) -> None:
        """Verify _batch_fetch_by_ids uses iter(ids=...) for V2 entities."""
        mock_company = MagicMock()
        mock_company.model_dump = MagicMock(
            return_value={"id": 100, "name": "Acme Corp", "domain": "acme.com"}
        )

        async def mock_iter(ids: list[int]):
            for id_ in ids:
                yield MagicMock(
                    model_dump=MagicMock(
                        return_value={"id": id_, "name": f"Company {id_}", "domain": f"co{id_}.com"}
                    )
                )

        mock_service = MagicMock()
        mock_service.iter = mock_iter
        mock_service.get = AsyncMock()  # Should not be called

        mock_client.companies = mock_service

        executor = QueryExecutor(mock_client)
        result = await executor._batch_fetch_by_ids("companies", [100, 200, 300])

        # Verify results
        assert len(result) == 3
        assert result[0]["id"] == 100
        assert result[1]["id"] == 200
        assert result[2]["id"] == 300

        # Verify get() was NOT called (batch was used)
        mock_service.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_fetch_falls_back_to_get_for_v1_entities(
        self, mock_client: AsyncMock
    ) -> None:
        """Verify _batch_fetch_by_ids uses individual get() for V1-only entities like notes."""
        mock_note = MagicMock()
        mock_note.model_dump = MagicMock(return_value={"id": 1, "content": "Note content"})

        mock_service = MagicMock()
        mock_service.get = AsyncMock(return_value=mock_note)
        # iter exists but notes are V1-only, so should fall back to get()

        mock_client.notes = mock_service

        executor = QueryExecutor(mock_client)
        # Since "notes" is not in V2_BATCH_ENTITIES, should use get()
        await executor._batch_fetch_by_ids("notes", [1, 2])

        # Verify get() was called for each ID
        assert mock_service.get.call_count == 2

    @pytest.mark.asyncio
    async def test_batch_fetch_handles_iter_failure_with_fallback(
        self, mock_client: AsyncMock
    ) -> None:
        """Verify _batch_fetch_by_ids falls back to get() if iter() fails."""

        async def failing_iter(_ids: list[int]):
            raise Exception("Simulated batch failure")
            yield  # Make it a generator

        mock_record = MagicMock()
        mock_record.model_dump = MagicMock(return_value={"id": 100, "name": "Fallback Company"})

        mock_service = MagicMock()
        mock_service.iter = failing_iter
        mock_service.get = AsyncMock(return_value=mock_record)

        mock_client.companies = mock_service

        executor = QueryExecutor(mock_client)
        result = await executor._batch_fetch_by_ids("companies", [100])

        # Should have fallen back to get()
        assert len(result) == 1
        assert result[0]["id"] == 100
        mock_service.get.assert_called_once_with(100)

    @pytest.mark.asyncio
    async def test_batch_fetch_returns_id_only_for_unknown_entity(
        self, mock_client: AsyncMock
    ) -> None:
        """Verify _batch_fetch_by_ids returns ID-only dict for unknown entities."""
        executor = QueryExecutor(mock_client)
        result = await executor._batch_fetch_by_ids("unknown_entity", [1, 2, 3])

        assert result == [{"id": 1}, {"id": 2}, {"id": 3}]

    @pytest.mark.asyncio
    async def test_batch_fetch_handles_get_errors_gracefully(self, mock_client: AsyncMock) -> None:
        """Verify _batch_fetch_by_ids returns ID-only for failed individual fetches."""

        async def failing_iter(_ids: list[int]):
            raise Exception("Batch failed")
            yield

        async def partial_failure_get(id_: int):
            if id_ == 200:
                raise Exception("Not found")
            record = MagicMock()
            record.model_dump = MagicMock(return_value={"id": id_, "name": f"Company {id_}"})
            return record

        mock_service = MagicMock()
        mock_service.iter = failing_iter
        mock_service.get = partial_failure_get

        mock_client.companies = mock_service

        executor = QueryExecutor(mock_client)
        result = await executor._batch_fetch_by_ids("companies", [100, 200, 300])

        # Should have 3 results: 100 full, 200 ID-only, 300 full
        assert len(result) == 3
        assert result[0]["name"] == "Company 100"
        assert result[1] == {"id": 200}  # Fallback on error
        assert result[2]["name"] == "Company 300"


# =============================================================================
# Multi-Parent Normalization Tests
# =============================================================================


class TestMultiParentNormalization:
    """Tests that multi-parent fetch path normalizes list entry fields."""

    def test_multi_parent_records_are_normalized(self) -> None:
        """Records from multi-parent fetch have fields normalized like single-parent."""
        # Simulate a record as returned by model_dump (before normalization)
        raw_record = {
            "id": 1,
            "listId": 100,
            "type": "company",
            "createdAt": "2025-01-01T00:00:00Z",
            "entity": {
                "id": 500,
                "name": "Acme Corp",
                "fields": {
                    "requested": True,
                    "data": {
                        "field-100": {
                            "id": "field-100",
                            "name": "Status",
                            "value": {"data": {"text": "Active"}},
                        },
                    },
                },
            },
            "fields": {"requested": False, "data": {}},
        }

        normalized = _normalize_list_entry_fields(raw_record)

        # Fields should be extracted to top-level dict keyed by name
        assert normalized["fields"] == {"Status": "Active"}
        assert normalized["entityName"] == "Acme Corp"
        assert normalized["entityId"] == 500
        assert normalized["listEntryId"] == 1


class TestListEntryDirectLookupFields:
    """listId + id equality uses entries.get(); it must request and normalize fields."""

    @pytest.mark.asyncio
    async def test_direct_lookup_returns_referenced_field_values(self) -> None:
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import FieldMetadata, ListEntryWithEntity

        query = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 100},
                        {"path": "id", "op": "eq", "value": 456},
                    ]
                },
                "select": ["id", "entityName", "fields.Status"],
            }
        ).query
        plan = create_planner().plan(query)

        get_kwargs: list[dict[str, Any]] = []

        async def mock_entry_get(_entry_id: Any, **kwargs: Any) -> ListEntryWithEntity:
            get_kwargs.append(kwargs)
            entity: dict[str, Any] = {"id": 789, "name": "Acme"}
            # V2 omits entity.fields unless fieldIds/fieldTypes is requested.
            if "field-7" in [str(f) for f in kwargs.get("field_ids") or []]:
                entity["fields"] = [
                    {
                        "id": "field-7",
                        "name": "Status",
                        "type": "list",
                        "value": {"type": "text", "data": "Intro Meeting"},
                    }
                ]
            return ListEntryWithEntity.model_validate(
                {
                    "id": 456,
                    "listId": 100,
                    "type": "company",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "entity": entity,
                }
            )

        mock_client = AsyncMock()
        mock_client.lists.get_fields = AsyncMock(
            return_value=[
                FieldMetadata.model_validate(
                    {"id": "field-7", "name": "Status", "type": "list", "valueType": "text"}
                )
            ]
        )
        mock_entries_service = MagicMock()
        mock_entries_service.get = mock_entry_get
        mock_client.lists.entries = MagicMock(return_value=mock_entries_service)

        executor = QueryExecutor(client=mock_client, max_records=100, concurrency=1)
        result = await executor.execute(plan)

        assert get_kwargs, "direct lookup should call entries.get()"
        assert [str(f) for f in get_kwargs[0].get("field_ids") or []] == ["field-7"]
        assert result.data == [
            {"id": 456, "entityName": "Acme", "fields": {"Status": "Intro Meeting"}}
        ]
