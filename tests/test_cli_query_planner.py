"""Tests for the query planner."""

from __future__ import annotations

import pytest

from affinity.cli.query import (
    QueryPlanner,
    QueryValidationError,
    create_planner,
    parse_query,
)


class TestQueryPlanner:
    """Tests for QueryPlanner class."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner(max_records=10000, concurrency=10)

    @pytest.mark.req("QUERY-PLAN-001")
    def test_plan_generates_fetch_step(self, planner: QueryPlanner) -> None:
        """Plan generates fetch step for primary entity."""
        result = parse_query({"from": "persons", "limit": 10})
        plan = planner.plan(result.query)

        assert len(plan.steps) >= 1
        assert plan.steps[0].operation == "fetch"
        assert plan.steps[0].entity == "persons"

    @pytest.mark.req("QUERY-PLAN-002")
    def test_plan_estimates_n_plus_1_for_includes(self, planner: QueryPlanner) -> None:
        """Plan estimates N+1 API calls for includes."""
        result = parse_query(
            {
                "from": "persons",
                "include": ["companies"],
            }
        )
        plan = planner.plan(result.query)

        include_steps = [s for s in plan.steps if s.operation == "include"]
        assert len(include_steps) == 1
        assert include_steps[0].estimated_api_calls > 0
        assert include_steps[0].relationship == "companies"

    @pytest.mark.req("QUERY-PLAN-003")
    def test_plan_warns_on_expensive_operations(self, planner: QueryPlanner) -> None:
        """Plan warns on expensive operations."""
        result = parse_query(
            {
                "from": "persons",
                "include": ["companies", "opportunities", "interactions"],
            }
        )
        plan = planner.plan(result.query)

        # Should have warnings about API calls
        assert plan.has_expensive_operations or len(plan.warnings) > 0

    @pytest.mark.req("QUERY-PLAN-004")
    def test_plan_dependencies_correct(self, planner: QueryPlanner) -> None:
        """Plan generates correct step dependencies."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "fields.Email", "op": "eq", "value": "x@test.com"},
                    ]
                },
                "include": ["persons"],
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        # First step has no dependencies
        assert len(plan.steps[0].depends_on) == 0

        # Subsequent steps depend on previous
        for i, step in enumerate(plan.steps[1:], start=1):
            assert len(step.depends_on) > 0
            # All dependencies should be earlier steps
            assert all(dep < i for dep in step.depends_on)

    @pytest.mark.req("QUERY-PLAN-005")
    def test_plan_calculates_total_api_calls(self, planner: QueryPlanner) -> None:
        """Plan calculates total estimated API calls."""
        result = parse_query(
            {
                "from": "persons",
                "include": ["companies"],
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        assert plan.total_api_calls > 0
        assert plan.total_api_calls == sum(s.estimated_api_calls for s in plan.steps)

    def test_plan_simple_fetch_and_limit(self, planner: QueryPlanner) -> None:
        """Simple query generates fetch and limit steps."""
        result = parse_query({"from": "persons", "limit": 10})
        plan = planner.plan(result.query)

        operations = [s.operation for s in plan.steps]
        assert "fetch" in operations
        assert "limit" in operations

    def test_plan_with_filter_adds_filter_step(self, planner: QueryPlanner) -> None:
        """Query with WHERE adds filter step."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "fields.Email", "op": "contains", "value": "@test.com"},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1
        assert filter_steps[0].is_client_side

    def test_plan_with_sort_adds_sort_step(self, planner: QueryPlanner) -> None:
        """Query with orderBy adds sort step."""
        result = parse_query(
            {
                "from": "persons",
                "orderBy": [{"field": "lastName", "direction": "asc"}],
            }
        )
        plan = planner.plan(result.query)

        sort_steps = [s for s in plan.steps if s.operation == "sort"]
        assert len(sort_steps) == 1
        assert sort_steps[0].is_client_side

    def test_plan_with_aggregate_adds_aggregate_step(self, planner: QueryPlanner) -> None:
        """Query with aggregate adds aggregate step."""
        result = parse_query(
            {
                "from": "opportunities",
                "aggregate": {"total": {"count": True}},
            }
        )
        plan = planner.plan(result.query)

        agg_steps = [s for s in plan.steps if s.operation == "aggregate"]
        assert len(agg_steps) == 1
        assert agg_steps[0].is_client_side

    def test_plan_rejects_unknown_include(self, planner: QueryPlanner) -> None:
        """Plan rejects unknown include relationship with helpful error."""
        result = parse_query(
            {
                "from": "persons",
                "include": ["unknownRelation"],
            }
        )

        with pytest.raises(QueryValidationError) as exc:
            planner.plan(result.query)
        error_msg = str(exc.value)
        # Error should mention the unknown relationship
        assert "unknownRelation" in error_msg
        # Error should suggest available relationships
        assert "Available:" in error_msg
        assert "companies" in error_msg  # persons can include companies

    def test_plan_estimates_memory(self, planner: QueryPlanner) -> None:
        """Plan estimates memory usage."""
        result = parse_query({"from": "persons"})
        plan = planner.plan(result.query)

        assert plan.estimated_memory_mb is not None
        assert plan.estimated_memory_mb > 0

    def test_plan_with_limit_reduces_estimates(self, planner: QueryPlanner) -> None:
        """Limit reduces record estimates."""
        result_no_limit = parse_query({"from": "persons"})
        result_with_limit = parse_query({"from": "persons", "limit": 10})

        plan_no_limit = planner.plan(result_no_limit.query)
        plan_with_limit = planner.plan(result_with_limit.query)

        assert plan_with_limit.estimated_records_fetched <= plan_no_limit.estimated_records_fetched

    def test_plan_different_entities(self, planner: QueryPlanner) -> None:
        """Plan works for different entity types that support direct querying."""
        # Only GLOBAL entities can be queried directly without filters
        entities = ["persons", "companies", "opportunities", "lists"]

        for entity in entities:
            result = parse_query({"from": entity, "limit": 10})
            plan = planner.plan(result.query)
            assert plan.steps[0].entity == entity

    def test_plan_listentries_with_filter(self, planner: QueryPlanner) -> None:
        """Plan works for listEntries with required listId filter."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 123},
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)
        assert plan.steps[0].entity == "listEntries"


class TestExecutionLevels:
    """Tests for execution level computation."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_get_execution_levels_simple(self, planner: QueryPlanner) -> None:
        """Simple query has sequential levels."""
        result = parse_query({"from": "persons", "limit": 10})
        plan = planner.plan(result.query)
        levels = planner.get_execution_levels(plan)

        # Each level should have at least one step
        assert len(levels) > 0
        for level in levels:
            assert len(level) >= 1

        # Total steps across levels should match plan steps
        total_steps = sum(len(level) for level in levels)
        assert total_steps == len(plan.steps)

    def test_get_execution_levels_respects_dependencies(self, planner: QueryPlanner) -> None:
        """Execution levels respect step dependencies."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "fields.Email", "op": "eq", "value": "x"},
                    ]
                },
                "include": ["persons"],
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)
        levels = planner.get_execution_levels(plan)

        # Build step_id -> level mapping
        step_to_level: dict[int, int] = {}
        for level_idx, level in enumerate(levels):
            for step in level:
                step_to_level[step.step_id] = level_idx

        # Verify each step appears after its dependencies
        for step in plan.steps:
            for dep_id in step.depends_on:
                assert step_to_level[dep_id] < step_to_level[step.step_id]


class TestUnknownEntity:
    """Tests for unknown entity type handling."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_plan_unknown_entity_raises_error(self, planner: QueryPlanner) -> None:
        """Planning a query with unknown entity type raises QueryValidationError."""
        from affinity.cli.query.models import Query

        query = Query(from_="unknownEntity")
        with pytest.raises(QueryValidationError) as exc:
            planner.plan(query)
        assert "Unknown entity type 'unknownEntity'" in str(exc.value)
        assert exc.value.field == "from"


class TestEntityWithoutRelationships:
    """Tests for entities that don't support includes."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_plan_include_on_entity_without_relationships(
        self, planner: QueryPlanner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Planning include on entity without relationships raises error."""
        from affinity.cli.query import planner as planner_module
        from affinity.cli.query.models import Query
        from affinity.cli.query.schema import EntitySchema

        # Create a mock entity schema with no relationships (using dataclass fields)
        mock_schema = EntitySchema(
            name="testEntity",
            service_attr="test_entities",
            id_field="id",
            filterable_fields=frozenset(["id", "name"]),
            computed_fields=frozenset(),
            relationships={},  # No relationships
        )

        original_get_schema = planner_module.get_entity_schema
        original_get_rel = planner_module.get_relationship

        def mock_get_entity_schema(entity_type: str) -> EntitySchema | None:
            if entity_type == "testEntity":
                return mock_schema
            return original_get_schema(entity_type)

        def mock_get_relationship(entity_type: str, rel_name: str):
            if entity_type == "testEntity":
                return None
            return original_get_rel(entity_type, rel_name)

        # Patch on planner module since that's where they're imported
        monkeypatch.setattr(planner_module, "get_entity_schema", mock_get_entity_schema)
        monkeypatch.setattr(planner_module, "get_relationship", mock_get_relationship)

        query = Query(from_="testEntity", include=["something"])
        with pytest.raises(QueryValidationError) as exc:
            planner.plan(query)
        assert "does not support includes" in str(exc.value)
        assert exc.value.field == "include"


class TestNPlusOneWarnings:
    """Tests for N+1 include warnings."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_n_plus_one_include_generates_warning(self, planner: QueryPlanner) -> None:
        """Include that requires N+1 generates step warning."""
        result = parse_query(
            {
                "from": "persons",
                "include": ["companies"],
                "limit": 5,
            }
        )
        plan = planner.plan(result.query)

        include_steps = [s for s in plan.steps if s.operation == "include"]
        assert len(include_steps) == 1
        # The step should have warnings about N+1 calls
        assert len(include_steps[0].warnings) > 0
        assert "API calls" in include_steps[0].warnings[0]

    def test_global_service_include_no_n_plus_one_warning(
        self, planner: QueryPlanner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Include using global service strategy has no N+1 warning."""
        from affinity.cli.query import planner as planner_module
        from affinity.cli.query.schema import RelationshipDef

        original_get_rel = planner_module.get_relationship

        def mock_get_relationship(entity_type: str, rel_name: str):
            if entity_type == "persons" and rel_name == "companies":
                # Return a relationship that doesn't require N+1
                return RelationshipDef(
                    target_entity="companies",
                    fetch_strategy="global_service",
                    method_or_service="companies",
                    requires_n_plus_1=False,
                )
            return original_get_rel(entity_type, rel_name)

        # Patch on planner module since that's where it's imported
        monkeypatch.setattr(planner_module, "get_relationship", mock_get_relationship)

        result = parse_query(
            {
                "from": "persons",
                "include": ["companies"],
                "limit": 5,
            }
        )
        plan = planner.plan(result.query)

        include_steps = [s for s in plan.steps if s.operation == "include"]
        assert len(include_steps) == 1
        # No N+1 warnings for global service
        assert len(include_steps[0].warnings) == 0
        # Estimated API calls should be 1 (single global call)
        assert include_steps[0].estimated_api_calls == 1


class TestCountConditions:
    """Tests for condition counting in WHERE clauses."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_count_conditions_with_or_clause(self, planner: QueryPlanner) -> None:
        """OR clause counts conditions correctly."""
        # Use listEntries with listId replicated across each OR branch so
        # the required-filter check passes while keeping 3 OR conditions.
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "or": [
                        {
                            "and": [
                                {"path": "listId", "op": "eq", "value": 123},
                                {"path": "fields.Name", "op": "eq", "value": "John"},
                            ]
                        },
                        {
                            "and": [
                                {"path": "listId", "op": "eq", "value": 123},
                                {"path": "fields.Name", "op": "eq", "value": "Jane"},
                            ]
                        },
                        {
                            "and": [
                                {"path": "listId", "op": "eq", "value": 123},
                                {"path": "fields.Name", "op": "eq", "value": "Bob"},
                            ]
                        },
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        # OR clause with 3 conditions should have filter step
        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1
        # Check description mentions OR
        assert "OR" in filter_steps[0].description
        assert "3 conditions" in filter_steps[0].description

    def test_count_conditions_with_not_clause(self, planner: QueryPlanner) -> None:
        """NOT clause counts conditions correctly."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"not": {"path": "fields.Email", "op": "is_null"}},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        # NOT clause should have filter step
        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1
        # Estimated records should be a reasonable non-None estimate
        assert filter_steps[0].estimated_records is not None
        assert filter_steps[0].estimated_records > 0


class TestDescribeWhere:
    """Tests for WHERE clause description generation."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_describe_where_unary_is_null(self, planner: QueryPlanner) -> None:
        """Unary is_null operator described correctly."""
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
        plan = planner.plan(result.query)

        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1
        # Description should not include value for unary op
        assert "is_null" in filter_steps[0].description
        assert "Email" in filter_steps[0].description

    def test_describe_where_unary_is_not_null(self, planner: QueryPlanner) -> None:
        """Unary is_not_null operator described correctly."""
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
        plan = planner.plan(result.query)

        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1
        assert "is_not_null" in filter_steps[0].description

    def test_describe_where_or_condition(self, planner: QueryPlanner) -> None:
        """OR condition described correctly."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "or": [
                        {
                            "and": [
                                {"path": "listId", "op": "eq", "value": 123},
                                {"path": "fields.Name", "op": "eq", "value": "John"},
                            ]
                        },
                        {
                            "and": [
                                {"path": "listId", "op": "eq", "value": 123},
                                {"path": "fields.Name", "op": "eq", "value": "Jane"},
                            ]
                        },
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1
        assert "2 conditions (OR)" in filter_steps[0].description

    def test_describe_where_not_condition(self, planner: QueryPlanner) -> None:
        """NOT condition described correctly."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"not": {"path": "fields.Email", "op": "eq", "value": "test@test.com"}},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1
        assert "NOT condition" in filter_steps[0].description


class TestFilterPushdown:
    """Tests for filter pushdown optimization."""

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    @pytest.mark.req("QUERY-OPT-001")
    def test_identifies_pushdown_for_list_entries(self, planner: QueryPlanner) -> None:
        """Identifies filter pushdown for listEntries."""
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
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.filter_pushdown is True
        assert fetch_step.pushdown_filter is not None

    @pytest.mark.req("QUERY-OPT-002")
    def test_generates_pushdown_filter_string(self, planner: QueryPlanner) -> None:
        """Generates correct pushdown filter string."""
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
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.pushdown_filter == "Status=Active"

    @pytest.mark.req("QUERY-OPT-003")
    def test_warns_when_no_pushdown_available(self, planner: QueryPlanner) -> None:
        """Warns when no pushdown is available."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "name", "op": "contains", "value": "test"},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        # Should have warning about no server-side filtering
        assert any("server-side" in w.lower() for w in plan.warnings)

    def test_no_pushdown_for_non_list_entries(self, planner: QueryPlanner) -> None:
        """No pushdown optimization for non-listEntries entities."""
        # Global entities with empty filterable_fields (persons/companies/
        # opportunities) reject where clauses entirely. `lists` retains
        # filterable fields, so use it to exercise the non-listEntries path.
        result = parse_query(
            {
                "from": "lists",
                "where": {"path": "name", "op": "eq", "value": "Dealflow"},
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.filter_pushdown is False

    def test_no_pushdown_for_complex_operators(self, planner: QueryPlanner) -> None:
        """No pushdown for complex operators like contains."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "fields.Status", "op": "contains", "value": "Act"},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.filter_pushdown is False

    def test_no_pushdown_when_path_is_none(self, planner: QueryPlanner) -> None:
        """No pushdown when WHERE condition has no path (e.g., expression)."""
        from affinity.cli.query.models import Query, WhereClause

        # Create WHERE clause with no path (simulating an expression condition)
        where = WhereClause(op="eq", value=True)  # No path
        query = Query(from_="listEntries", where=where)
        plan = planner.plan(query)

        fetch_step = plan.steps[0]
        # Should not crash and should have no pushdown
        assert fetch_step.filter_pushdown is False


class TestParentFilterStripping:
    """Tests for parent filter field stripping from client-side filter steps.

    For REQUIRES_PARENT entities (like listEntries), the parent_filter_field
    (e.g., listId) is used to scope the API call, NOT as a client-side filter.
    This test ensures the planner correctly strips these conditions to avoid
    unnecessary filter steps that would disable early termination.

    Regression test for bug where listEntries queries with limit would fetch
    ALL records because listId was treated as a client-side filter.
    """

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    @pytest.mark.req("QUERY-PLAN-PARENT-001")
    def test_listid_only_filter_creates_no_filter_step(self, planner: QueryPlanner) -> None:
        """Query with only listId filter should not create client-side filter step.

        listId is the parent_filter_field for listEntries and is used to scope
        the API call, not as a client-side filter.
        """
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 12345},
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        # Should NOT have a filter step
        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 0, (
            "listId-only filter should not create client-side filter step - "
            "listId is used to scope the fetch, not as a client-side filter"
        )

    @pytest.mark.req("QUERY-PLAN-PARENT-001")
    def test_listname_only_filter_creates_no_filter_step(self, planner: QueryPlanner) -> None:
        """Query with only listName filter should not create client-side filter step.

        listName is resolved to listId which is the parent_filter_field.
        """
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listName", "op": "eq", "value": "Dealflow"},
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        # Should NOT have a filter step
        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 0, (
            "listName-only filter should not create client-side filter step - "
            "listName resolves to listId which scopes the fetch"
        )

    @pytest.mark.req("QUERY-PLAN-PARENT-002")
    def test_listid_with_other_filter_strips_listid(self, planner: QueryPlanner) -> None:
        """Query with listId AND other conditions should strip listId from filter step.

        The filter step should only contain the non-parent conditions.
        """
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 12345},
                        {"path": "entityName", "op": "contains", "value": "Acme"},
                    ]
                },
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        # Should have exactly one filter step
        filter_steps = [s for s in plan.steps if s.operation == "filter"]
        assert len(filter_steps) == 1

        # The filter step should describe only the entityName condition
        assert "entityName" in filter_steps[0].description
        assert "listId" not in filter_steps[0].description

    @pytest.mark.req("QUERY-PLAN-PARENT-003")
    def test_no_full_scan_warning_for_listid_only(self, planner: QueryPlanner) -> None:
        """Query with only listId filter should not have full scan recommendation."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 12345},
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        # Should not have the "requires client-side filtering" recommendation
        assert not plan.requires_full_scan, (
            "listId-only filter should not require full scan - "
            "it's used to scope the fetch, not as a client-side filter"
        )


class TestSingleIdLookupOptimization:
    """Tests for single-ID lookup optimization in the planner.

    When a query has a simple `id eq X` filter, the planner should:
    1. Detect this pattern and use "fetch" operation (not "fetch_streaming")
    2. Estimate exactly 1 API call
    3. Generate description indicating direct lookup
    """

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_single_id_lookup_uses_direct_fetch(self, planner: QueryPlanner) -> None:
        """Single ID lookup uses direct fetch operation.

        Post-6.1 only listEntries (with listId + id) reaches the
        single-ID-lookup fast path — global entity where clauses are
        rejected by validate_entity_queryable.
        """
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "id", "op": "eq", "value": 12345},
                    ]
                },
                "limit": 1,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.operation == "fetch"  # Not fetch_streaming
        assert "direct lookup" in fetch_step.description.lower()

    def test_single_id_lookup_estimates_one_api_call(self, planner: QueryPlanner) -> None:
        """Single ID lookup estimates exactly 1 API call."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "id", "op": "eq", "value": 12345},
                    ]
                },
                "limit": 1,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.estimated_api_calls == 1

    def test_single_id_lookup_works_for_companies(self, planner: QueryPlanner) -> None:  # noqa: ARG002
        """Single ID lookup for companies is no longer supported.

        Post-6.1, companies cannot be queried with any where clause.
        This test remains as a regression guard that the change is enforced.
        """
        from affinity.cli.query.exceptions import QueryValidationError

        with pytest.raises(QueryValidationError):
            parse_query(
                {
                    "from": "companies",
                    "where": {"path": "id", "op": "eq", "value": 99999},
                }
            )

    def test_single_id_lookup_works_for_opportunities(self, planner: QueryPlanner) -> None:  # noqa: ARG002
        """Single ID lookup for opportunities is no longer supported.

        Post-6.1, opportunities cannot be queried with any where clause.
        """
        from affinity.cli.query.exceptions import QueryValidationError

        with pytest.raises(QueryValidationError):
            parse_query(
                {
                    "from": "opportunities",
                    "where": {"path": "id", "op": "eq", "value": 12345},
                }
            )

    def test_compound_filter_not_single_id_lookup(self, planner: QueryPlanner) -> None:
        """Compound filter with id does not trigger single-ID optimization."""
        # Use listEntries (only entity that reaches compound-filter path now).
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "id", "op": "eq", "value": 12345},
                        {"path": "entityType", "op": "eq", "value": "company"},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Should not be a direct lookup due to extra condition
        assert "direct lookup" not in fetch_step.description.lower()

    def test_non_id_field_not_single_id_lookup(self, planner: QueryPlanner) -> None:
        """Filter on non-id field does not trigger single-ID optimization."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "entityName", "op": "eq", "value": "Alice"},
                    ]
                },
                "limit": 1,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Should use streaming for client-side filter
        assert fetch_step.operation == "fetch_streaming"
        assert "direct lookup" not in fetch_step.description.lower()

    def test_list_entries_single_id_lookup(self, planner: QueryPlanner) -> None:
        """listEntries with listId + id triggers single-ID optimization."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "id", "op": "eq", "value": 456},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.operation == "fetch"
        assert fetch_step.estimated_api_calls == 1
        assert "direct lookup" in fetch_step.description.lower()

    def test_list_entries_only_list_id_not_single_lookup(self, planner: QueryPlanner) -> None:
        """listEntries with only listId does not trigger single-ID optimization."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 123},
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Should not be a direct lookup - needs to paginate the list
        assert (
            fetch_step.operation != "fetch" or "direct lookup" not in fetch_step.description.lower()
        )

    def test_list_entries_extra_condition_not_single_lookup(self, planner: QueryPlanner) -> None:
        """listEntries with extra conditions beyond listId + id not optimized."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "id", "op": "eq", "value": 456},
                        {"path": "entityType", "op": "eq", "value": "company"},
                    ]
                },
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Should not be a direct lookup due to extra condition
        assert "direct lookup" not in fetch_step.description.lower()


class TestClientSideFilterEstimate:
    """Tests for improved client-side filter API call estimates.

    When a query has a client-side filter, the planner should estimate
    API calls based on expected scan size, not output size.
    """

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_client_side_filter_estimates_scan_size(self, planner: QueryPlanner) -> None:
        """Client-side filter estimates based on scan size, not output."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "entityName", "op": "contains", "value": "Test"},
                    ]
                },
                "limit": 100,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Should estimate more than 1 page for limit=100 with client-side filter
        # because we expect to scan more records than we return
        assert fetch_step.estimated_api_calls > 1

    def test_no_filter_estimates_output_size(self, planner: QueryPlanner) -> None:
        """Query without filter estimates based on output size directly."""
        result = parse_query(
            {
                "from": "companies",
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Small limit with no filter should estimate few pages
        assert fetch_step.estimated_api_calls <= 2

    def test_streaming_operation_for_client_side_filter(self, planner: QueryPlanner) -> None:
        """Client-side filter uses streaming operation."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "entityName", "op": "contains", "value": "@acme.com"},
                    ]
                },
                "limit": 50,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        assert fetch_step.operation == "fetch_streaming"
        assert "client-side filter" in fetch_step.description.lower()


class TestIncludeEstimateWithFilter:
    """Tests for include API call estimates with filter + limit.

    When a query has both limit and filter, include estimates should use
    the limit (we'll find at most limit matches) rather than the
    selectivity-reduced estimate.
    """

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner()

    def test_include_uses_limit_when_filter_present(self, planner: QueryPlanner) -> None:
        """Include estimate uses limit when both limit and filter exist."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "entityName", "op": "contains", "value": "@acme.com"},
                    ]
                },
                "include": ["persons"],
                "limit": 5,
            }
        )
        plan = planner.plan(result.query)

        include_step = next(s for s in plan.steps if s.operation == "include")
        # Should estimate 5 API calls (limit), not 2-3 (limit * 0.5 selectivity)
        assert include_step.estimated_api_calls == 5

    def test_include_uses_filtered_estimate_without_limit(self, planner: QueryPlanner) -> None:
        """Include estimate uses filtered estimate when no limit."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "entityName", "op": "contains", "value": "@acme.com"},
                    ]
                },
                "include": ["persons"],
            }
        )
        plan = planner.plan(result.query)

        include_step = next(s for s in plan.steps if s.operation == "include")
        # Without limit, uses estimated_records (selectivity-reduced)
        # This is a large number based on default entity count * 0.5
        assert include_step.estimated_api_calls > 100

    def test_include_uses_estimated_records_no_filter(self, planner: QueryPlanner) -> None:
        """Include estimate uses estimated_records when no filter."""
        result = parse_query(
            {
                "from": "persons",
                "include": ["companies"],
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        include_step = next(s for s in plan.steps if s.operation == "include")
        # No filter means estimated_records = limit
        assert include_step.estimated_api_calls == 10

    def test_expand_uses_limit_when_filter_present(self, planner: QueryPlanner) -> None:
        """Expand estimate uses limit when both limit and filter exist."""
        # expand: interactionDates is only supported on persons/companies, but
        # those no longer accept where clauses. Use listEntries with an include
        # expansion target would require different approach; instead assert the
        # no-filter path: limit only.
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "entityName", "op": "contains", "value": "@acme.com"},
                    ]
                },
                "expand": ["interactionDates"],
                "limit": 5,
            }
        )
        plan = planner.plan(result.query)

        expand_step = next(s for s in plan.steps if s.operation == "expand")
        # Should estimate 5 API calls (limit), not 2-3 (limit * 0.5 selectivity)
        assert expand_step.estimated_api_calls == 5


class TestOrderByEstimation:
    """Tests for orderBy requiring full fetch estimation.

    Regression tests for bug where limit was used as API call estimate
    even when orderBy requires fetching all records first.
    """

    @pytest.fixture
    def planner(self) -> QueryPlanner:
        """Create a planner for testing."""
        return create_planner(max_records=10000, concurrency=10)

    def test_orderby_without_filter_estimates_full_scan(self, planner: QueryPlanner) -> None:
        """Query with orderBy but no filter estimates full scan.

        Even with limit: 10, orderBy requires fetching all records first,
        so API call estimate should be based on full entity count, not limit.
        """
        result = parse_query(
            {
                "from": "persons",
                "orderBy": [{"field": "lastName", "direction": "desc"}],
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Should estimate full scan (5000 persons / 100 per page = 50 API calls)
        # Not limit-based (10 records / 100 per page = 1 API call)
        assert fetch_step.estimated_api_calls == 50
        assert "full scan for sort" in fetch_step.description

    def test_orderby_with_filter_estimates_full_scan(self, planner: QueryPlanner) -> None:
        """Query with orderBy and filter estimates full scan.

        orderBy requires fetching all records matching the filter first,
        so API call estimate should be based on full entity count, not limit.
        """
        result = parse_query(
            {
                "from": "listEntries",
                "where": {
                    "and": [
                        {"path": "listId", "op": "eq", "value": 123},
                        {"path": "entityName", "op": "contains", "value": "@acme.com"},
                    ]
                },
                "orderBy": [{"field": "entityName", "direction": "desc"}],
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Should estimate full scan (listEntries default count / 100 per page).
        # The exact value is > 1 because orderBy forces a full scan regardless
        # of the small limit.
        assert fetch_step.estimated_api_calls > 1

    def test_no_orderby_uses_limit_estimate(self, planner: QueryPlanner) -> None:
        """Query without orderBy uses limit for efficient estimation."""
        result = parse_query(
            {
                "from": "persons",
                "limit": 10,
            }
        )
        plan = planner.plan(result.query)

        fetch_step = plan.steps[0]
        # Without orderBy, can use limit directly (10 records / 100 per page = 1 API call)
        assert fetch_step.estimated_api_calls == 1
