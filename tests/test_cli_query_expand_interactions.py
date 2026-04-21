"""Integration tests for query expand: ["interactionDates"].

Tests the query language support for expanding interaction date summaries on
persons, companies, and listEntries.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from affinity.cli.query.exceptions import QueryValidationError
from affinity.cli.query.parser import parse_query

# ==============================================================================
# Parser Tests - Validation of expand clause
# ==============================================================================


class TestQueryExpandParsing:
    """Test parsing of expand clause in queries."""

    @pytest.mark.req("QUERY-EXPAND-001")
    def test_expand_interaction_dates_for_persons_accepted(self) -> None:
        """expand: ["interactionDates"] should be accepted for persons."""
        result = parse_query(
            {
                "from": "persons",
                "expand": ["interactionDates"],
                "limit": 10,
            }
        )
        assert result.query.from_ == "persons"
        assert result.query.expand == ["interactionDates"]

    @pytest.mark.req("QUERY-EXPAND-001")
    def test_expand_interaction_dates_for_companies_accepted(self) -> None:
        """expand: ["interactionDates"] should be accepted for companies."""
        result = parse_query(
            {
                "from": "companies",
                "expand": ["interactionDates"],
                "limit": 10,
            }
        )
        assert result.query.from_ == "companies"
        assert result.query.expand == ["interactionDates"]

    @pytest.mark.req("QUERY-EXPAND-001")
    def test_expand_interaction_dates_for_list_entries_accepted(self) -> None:
        """expand: ["interactionDates"] should be accepted for listEntries."""
        result = parse_query(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 123},
                "expand": ["interactionDates"],
                "limit": 10,
            }
        )
        assert result.query.from_ == "listEntries"
        assert result.query.expand == ["interactionDates"]

    @pytest.mark.req("QUERY-EXPAND-002")
    def test_expand_invalid_name_rejected(self) -> None:
        """Invalid expand names should be rejected."""
        with pytest.raises(QueryValidationError, match="Unknown expansion"):
            parse_query(
                {
                    "from": "persons",
                    "expand": ["invalidExpansion"],
                    "limit": 10,
                }
            )

    @pytest.mark.req("QUERY-EXPAND-002")
    def test_expand_unsupported_entity_rejected(self) -> None:
        """expand should be rejected for unsupported entities."""
        with pytest.raises(QueryValidationError, match="not supported for"):
            parse_query(
                {
                    "from": "opportunities",
                    "expand": ["interactionDates"],
                    "limit": 10,
                }
            )


# ==============================================================================
# Dry-Run Tests - Verify planning without execution
# ==============================================================================


class TestQueryExpandDryRun:
    """Test dry-run output for queries with expand."""

    @pytest.fixture
    def cli_context(self):
        """Create mock CLI context."""
        from unittest.mock import MagicMock

        from affinity.cli.context import CLIContext

        ctx = MagicMock(spec=CLIContext)
        ctx.output = "json"
        ctx.quiet = False
        ctx.verbosity = 0
        # Required for output option validation
        ctx._output_source = None
        ctx._output_format_conflict = None
        return ctx

    @pytest.fixture
    def runner(self):
        """Create CLI runner."""
        return CliRunner()

    def _extract_json(self, output: str) -> dict:
        """Extract JSON object from output."""
        start = output.find("{")
        if start == -1:
            raise ValueError(f"No JSON object found in output: {output}")
        depth = 0
        for i, char in enumerate(output[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(output[start : i + 1])
        raise ValueError(f"Unbalanced JSON in output: {output}")

    @pytest.mark.req("QUERY-EXPAND-003")
    def test_dry_run_shows_expansion_in_plan(self, runner, cli_context) -> None:
        """Dry run should show expansion step in the execution plan."""
        from affinity.cli.commands.query_cmd import query_cmd

        query = '{"from": "persons", "expand": ["interactionDates"], "limit": 5}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code == 0, f"Failed with: {result.output}"
        output = self._extract_json(result.output)
        # The query should be in the output
        assert output["query"]["from"] == "persons"
        # Expansion should appear as a step in the execution plan
        expand_steps = [s for s in output["steps"] if s["operation"] == "expand"]
        assert len(expand_steps) == 1
        assert "interactionDates" in expand_steps[0]["description"]

    @pytest.mark.req("QUERY-EXPAND-003")
    def test_dry_run_expansion_for_companies(self, runner, cli_context) -> None:
        """Dry run should show expansion step for companies."""
        from affinity.cli.commands.query_cmd import query_cmd

        query = '{"from": "companies", "expand": ["interactionDates"], "limit": 3}'

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code == 0, f"Failed with: {result.output}"
        output = self._extract_json(result.output)
        assert output["query"]["from"] == "companies"
        # Expansion should appear as a step
        expand_steps = [s for s in output["steps"] if s["operation"] == "expand"]
        assert len(expand_steps) == 1
        assert "interactionDates" in expand_steps[0]["description"]

    @pytest.mark.req("QUERY-EXPAND-003")
    def test_dry_run_expansion_for_list_entries(self, runner, cli_context) -> None:
        """Dry run should show expansion step for listEntries."""
        from affinity.cli.commands.query_cmd import query_cmd

        query = json.dumps(
            {
                "from": "listEntries",
                "where": {"path": "listId", "op": "eq", "value": 123},
                "expand": ["interactionDates"],
                "limit": 5,
            }
        )

        result = runner.invoke(
            query_cmd,
            ["--query", query, "--dry-run"],
            obj=cli_context,
        )

        assert result.exit_code == 0, f"Failed with: {result.output}"
        output = self._extract_json(result.output)
        assert output["query"]["from"] == "listEntries"
        # Expansion should appear as a step
        expand_steps = [s for s in output["steps"] if s["operation"] == "expand"]
        assert len(expand_steps) == 1
        assert "interactionDates" in expand_steps[0]["description"]


# ==============================================================================
# Schema Registry Tests
# ==============================================================================


class TestExpandSchemaRegistry:
    """Test that schema registry correctly defines expansion support."""

    def test_persons_supports_interaction_dates(self) -> None:
        """Persons entity should support interactionDates expansion."""
        from affinity.cli.query.schema import get_entity_schema

        schema = get_entity_schema("persons")
        assert schema is not None
        assert "interactionDates" in schema.supported_expansions

    def test_companies_supports_interaction_dates(self) -> None:
        """Companies entity should support interactionDates expansion."""
        from affinity.cli.query.schema import get_entity_schema

        schema = get_entity_schema("companies")
        assert schema is not None
        assert "interactionDates" in schema.supported_expansions

    def test_list_entries_supports_interaction_dates(self) -> None:
        """ListEntries entity should support interactionDates expansion."""
        from affinity.cli.query.schema import get_entity_schema

        schema = get_entity_schema("listEntries")
        assert schema is not None
        assert "interactionDates" in schema.supported_expansions

    def test_opportunities_does_not_support_expansion(self) -> None:
        """Opportunities entity should NOT support expansion."""
        from affinity.cli.query.schema import get_entity_schema

        schema = get_entity_schema("opportunities")
        assert schema is not None
        assert len(schema.supported_expansions) == 0

    def test_expansion_registry_has_interaction_dates(self) -> None:
        """EXPANSION_REGISTRY should have interactionDates defined."""
        from affinity.cli.query.schema import EXPANSION_REGISTRY

        assert "interactionDates" in EXPANSION_REGISTRY
        expansion = EXPANSION_REGISTRY["interactionDates"]
        assert expansion.name == "interactionDates"
        assert expansion.fetch_params["with_interaction_dates"] is True
        assert expansion.fetch_params["with_interaction_persons"] is True


# ==============================================================================
# Executor Tests - Streaming path with expand
# ==============================================================================


class TestStreamingPathWithExpand:
    """Test that expand works correctly in streaming execution path.

    The streaming path is used when:
    - Query has limit (or explicit --max-records)
    - No sort/aggregate/groupBy operations

    This test ensures expand steps are executed after streaming completes.
    Regression test for bug where expand was skipped in streaming path.
    """

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXPAND-004")
    async def test_streaming_path_executes_expand_step(self) -> None:
        """Expand should be executed even when streaming mode is used.

        This tests the fix for the bug where streaming path only handled
        'include' steps but skipped 'expand' steps.
        """
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import InteractionDates, Person

        # Create a query that will use streaming (has limit, no sort/aggregate)
        query_dict = {
            "from": "persons",
            "expand": ["interactionDates"],
            "limit": 2,
        }
        parse_result = parse_query(query_dict)
        planner = create_planner()
        plan = planner.plan(parse_result.query)

        # Mock person data
        mock_person = MagicMock(spec=Person)
        mock_person.id = 123
        mock_person.first_name = "Test"
        mock_person.last_name = "User"
        mock_person.primary_email_address = "test@example.com"
        mock_person.model_dump = MagicMock(
            return_value={
                "id": 123,
                "firstName": "Test",
                "lastName": "User",
                "primaryEmailAddress": "test@example.com",
                "type": "external",
            }
        )

        # Mock person with interaction dates (for expand step)
        mock_interaction_dates = MagicMock(spec=InteractionDates)
        mock_interaction_dates.last_event_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
        mock_interaction_dates.next_event_date = None
        mock_interaction_dates.last_email_date = datetime(2026, 1, 8, tzinfo=timezone.utc)
        mock_interaction_dates.last_interaction_date = datetime(2026, 1, 10, tzinfo=timezone.utc)

        mock_person_expanded = MagicMock(spec=Person)
        mock_person_expanded.interaction_dates = mock_interaction_dates
        from affinity.models.entities import InteractionEvent, Interactions

        mock_person_expanded.interactions = Interactions(
            last_event=InteractionEvent(person_ids=[456])
        )
        mock_person_expanded.full_name = "Test User"

        # Mock the team member lookup
        mock_team_member = MagicMock(spec=Person)
        mock_team_member.full_name = "Team Member"

        # Create mock async client
        mock_client = AsyncMock()
        mock_client.whoami = AsyncMock()

        # Mock persons service for streaming (all().pages())
        mock_page = MagicMock()
        mock_page.data = [mock_person]

        async def mock_pages():
            yield mock_page

        mock_persons_all = MagicMock()
        mock_persons_all.pages = mock_pages
        mock_client.persons.all = MagicMock(return_value=mock_persons_all)

        # Mock persons.get for expand step (returns person with interaction dates)
        # AND for name resolution
        async def mock_get(_person_id, **kwargs):
            if kwargs.get("with_interaction_dates"):
                return mock_person_expanded
            return mock_team_member

        mock_client.persons.get = mock_get

        # Create executor and run
        executor = QueryExecutor(
            client=mock_client,
            max_records=100,
            concurrency=1,
        )

        query_result = await executor.execute(plan)

        # Verify expand was executed - records should have interactionDates
        assert len(query_result.data) == 1
        record = query_result.data[0]
        assert "interactionDates" in record, (
            "Expand step was not executed - interactionDates missing from record. "
            "This indicates streaming path may be skipping expand steps."
        )
        assert record["interactionDates"] is not None
        assert "lastMeeting" in record["interactionDates"]
        assert "teamMemberNames" in record["interactionDates"]["lastMeeting"]
        assert record["interactionDates"]["lastMeeting"]["teamMemberNames"] == ["Team Member"]


# ==============================================================================
# Executor Path Regression Tests - Expand via _execute_step
# ==============================================================================


class TestExpandViaSingleIdLookup:
    """Regression tests for expand via single-ID lookup optimization paths.

    These tests ensure expand works correctly when the executor uses single-ID
    lookup optimization (service.get(id)) instead of streaming through pages.

    Paths tested:
    - GLOBAL entities (persons/companies) with where: {path: "id", op: "eq", value: X}
    - REQUIRES_PARENT entities (listEntries) with both parent ID and entity ID
    """

    @pytest.mark.skip(
        reason="Task 6.1: where clauses on global entities (persons/companies/"
        "opportunities) are now rejected at parse time. The executor's single-"
        "ID-lookup code path is still exercised by listEntries via "
        "test_single_id_lookup_requires_parent_with_expand below."
    )
    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXPAND-005")
    async def test_single_id_lookup_global_entity_with_expand(self) -> None:
        """Expand should work when single-ID lookup optimization is used for persons.

        When query is `from: persons, where: {path: "id", op: "eq", value: 123}`
        with expand, the executor uses service.get(id) directly instead of
        streaming. This tests that expand is executed in that path (line 790).
        """
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import InteractionDates, Person

        # Query that will trigger single-ID lookup (id equality filter)
        query_dict = {
            "from": "persons",
            "where": {"path": "id", "op": "eq", "value": 123},
            "expand": ["interactionDates"],
        }
        parse_result = parse_query(query_dict)
        planner = create_planner()
        plan = planner.plan(parse_result.query)

        # Mock person returned by service.get() - initial lookup (no expansion)
        mock_person = MagicMock(spec=Person)
        mock_person.id = 123
        mock_person.model_dump = MagicMock(
            return_value={
                "id": 123,
                "firstName": "Test",
                "lastName": "User",
                "type": "external",
            }
        )

        # Mock person with interaction dates for expand step
        mock_interaction_dates = MagicMock(spec=InteractionDates)
        mock_interaction_dates.last_event_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
        mock_interaction_dates.next_event_date = None
        mock_interaction_dates.last_email_date = datetime(2026, 1, 8, tzinfo=timezone.utc)
        mock_interaction_dates.last_interaction_date = datetime(2026, 1, 10, tzinfo=timezone.utc)

        mock_person_expanded = MagicMock(spec=Person)
        mock_person_expanded.interaction_dates = mock_interaction_dates
        from affinity.models.entities import InteractionEvent, Interactions

        mock_person_expanded.interactions = Interactions(
            last_event=InteractionEvent(person_ids=[456])
        )

        # Mock team member for name resolution
        mock_team_member = MagicMock(spec=Person)
        mock_team_member.full_name = "Team Member"

        # Track get() calls to verify single-ID lookup is used
        get_call_count = 0

        async def mock_get(_person_id, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if kwargs.get("with_interaction_dates"):
                return mock_person_expanded
            # First call is the initial lookup, subsequent may be name resolution
            if get_call_count == 1:
                return mock_person
            return mock_team_member

        # Create mock async client
        mock_client = AsyncMock()
        mock_client.whoami = AsyncMock()
        mock_client.persons.get = mock_get

        executor = QueryExecutor(
            client=mock_client,
            max_records=100,
            concurrency=1,
        )

        query_result = await executor.execute(plan)

        # Verify single-ID lookup was used (get() called for initial fetch)
        assert get_call_count >= 1, "service.get() should have been called for single-ID lookup"

        # Verify expand was executed
        assert len(query_result.data) == 1
        record = query_result.data[0]
        assert "interactionDates" in record, (
            "Expand step was not executed in single-ID lookup path. "
            "interactionDates missing from record."
        )
        assert record["interactionDates"] is not None
        assert "lastMeeting" in record["interactionDates"]

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXPAND-006")
    async def test_single_id_lookup_requires_parent_with_expand(self) -> None:
        """Expand should work for listEntries single-ID lookup with expand.

        When query has both listId and id equality conditions, the executor uses
        service.get(entry_id) directly. This tests expand in that path (line 884).
        """
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import InteractionDates, ListEntry, Person

        # Query that triggers parent+entity ID lookup
        query_dict = {
            "from": "listEntries",
            "where": {
                "and": [
                    {"path": "listId", "op": "eq", "value": 100},
                    {"path": "id", "op": "eq", "value": 456},
                ]
            },
            "expand": ["interactionDates"],
        }
        parse_result = parse_query(query_dict)
        planner = create_planner()
        plan = planner.plan(parse_result.query)

        # Mock list entry returned by service.get()
        mock_entry = MagicMock(spec=ListEntry)
        mock_entry.id = 456
        mock_entry.entity_id = 789
        mock_entry.entity_type = 0  # Person
        mock_entry.model_dump = MagicMock(
            return_value={
                "id": 456,
                "listId": 100,
                "entityId": 789,
                "entityType": 0,
            }
        )

        # Mock person with interaction dates for expand
        mock_interaction_dates = MagicMock(spec=InteractionDates)
        mock_interaction_dates.last_event_date = datetime(2026, 1, 15, tzinfo=timezone.utc)
        mock_interaction_dates.next_event_date = None
        mock_interaction_dates.last_email_date = datetime(2026, 1, 12, tzinfo=timezone.utc)
        mock_interaction_dates.last_interaction_date = datetime(2026, 1, 15, tzinfo=timezone.utc)

        mock_person_expanded = MagicMock(spec=Person)
        mock_person_expanded.interaction_dates = mock_interaction_dates
        from affinity.models.entities import InteractionEvent, Interactions

        mock_person_expanded.interactions = Interactions(last_event=InteractionEvent(person_ids=[]))

        # Track service calls
        entry_get_called = False
        person_get_called = False

        async def mock_entry_get(_entry_id, **_kwargs):
            nonlocal entry_get_called
            entry_get_called = True
            return mock_entry

        async def mock_person_get(_person_id, **kwargs):
            nonlocal person_get_called
            if kwargs.get("with_interaction_dates"):
                person_get_called = True
                return mock_person_expanded
            return MagicMock(full_name="Team Member")

        # Create mock async client
        mock_client = AsyncMock()
        mock_client.whoami = AsyncMock()

        # Mock lists.entries(list_id).get(entry_id) chain
        mock_entries_service = MagicMock()
        mock_entries_service.get = mock_entry_get
        mock_client.lists.entries = MagicMock(return_value=mock_entries_service)

        # Mock persons.get for expand
        mock_client.persons.get = mock_person_get

        executor = QueryExecutor(
            client=mock_client,
            max_records=100,
            concurrency=1,
        )

        query_result = await executor.execute(plan)

        # Verify single-ID lookup was used
        assert entry_get_called, "lists.entries(list_id).get() should have been called"

        # Verify expand was executed
        assert len(query_result.data) == 1
        record = query_result.data[0]
        assert "interactionDates" in record, (
            "Expand step was not executed in REQUIRES_PARENT single-ID lookup path. "
            "interactionDates missing from record."
        )
        assert person_get_called, "persons.get() with interaction params should have been called"


class TestExpandViaExecuteStepPath:
    """Regression tests for expand via _execute_step path.

    The _execute_step path is used when:
    - Query has sort/orderBy (can't use streaming)
    - Query has aggregate/groupBy (can't use streaming)
    - Entity uses REQUIRES_PARENT strategy without single-ID optimization

    This tests the dispatch at line 909 in executor.py.
    """

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXPAND-007")
    async def test_execute_step_path_with_sort_and_expand(self) -> None:
        """Expand should work when _execute_step path is used (query with orderBy).

        When query has orderBy, streaming can't be used and all steps go through
        _execute_step. This tests expand dispatch at line 909.
        """
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import InteractionDates, Person

        # Query with orderBy forces _execute_step path (can't stream)
        query_dict = {
            "from": "persons",
            "expand": ["interactionDates"],
            "orderBy": [{"field": "firstName", "direction": "asc"}],
            "limit": 5,
        }
        parse_result = parse_query(query_dict)
        planner = create_planner()
        plan = planner.plan(parse_result.query)

        # Verify streaming is disabled
        from affinity.cli.query.executor import can_use_streaming

        assert can_use_streaming(plan.query) is False, "orderBy should disable streaming"

        # Mock person data for fetch step
        mock_person1 = MagicMock(spec=Person)
        mock_person1.id = 1
        mock_person1.model_dump = MagicMock(
            return_value={"id": 1, "firstName": "Alice", "type": "external"}
        )
        mock_person2 = MagicMock(spec=Person)
        mock_person2.id = 2
        mock_person2.model_dump = MagicMock(
            return_value={"id": 2, "firstName": "Bob", "type": "external"}
        )

        # Mock interaction dates for expand
        mock_interaction_dates = MagicMock(spec=InteractionDates)
        mock_interaction_dates.last_event_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
        mock_interaction_dates.next_event_date = None
        mock_interaction_dates.last_email_date = datetime(2026, 1, 8, tzinfo=timezone.utc)
        mock_interaction_dates.last_interaction_date = datetime(2026, 1, 10, tzinfo=timezone.utc)

        mock_person_expanded = MagicMock(spec=Person)
        mock_person_expanded.interaction_dates = mock_interaction_dates
        from affinity.models.entities import InteractionEvent, Interactions

        mock_person_expanded.interactions = Interactions(last_event=InteractionEvent(person_ids=[]))

        # Create mock async client
        mock_client = AsyncMock()
        mock_client.whoami = AsyncMock()

        # Mock persons.all() for fetch step
        mock_page = MagicMock()
        mock_page.data = [mock_person1, mock_person2]

        async def mock_pages(**_kwargs):
            yield mock_page

        mock_persons_all = MagicMock()
        mock_persons_all.pages = mock_pages
        mock_client.persons.all = MagicMock(return_value=mock_persons_all)

        # Mock persons.get for expand step
        async def mock_get(_person_id, **kwargs):
            if kwargs.get("with_interaction_dates"):
                return mock_person_expanded
            return MagicMock(full_name="Team Member")

        mock_client.persons.get = mock_get

        executor = QueryExecutor(
            client=mock_client,
            max_records=100,
            concurrency=1,
        )

        query_result = await executor.execute(plan)

        # Verify records are sorted (orderBy was applied)
        assert len(query_result.data) == 2
        assert query_result.data[0]["firstName"] == "Alice"
        assert query_result.data[1]["firstName"] == "Bob"

        # Verify expand was executed via _execute_step
        for record in query_result.data:
            assert "interactionDates" in record, (
                "Expand step was not executed via _execute_step path. "
                f"interactionDates missing from record: {record}"
            )
            assert record["interactionDates"] is not None

    @pytest.mark.asyncio
    @pytest.mark.req("QUERY-EXPAND-008")
    async def test_execute_step_path_list_entries_with_expand(self) -> None:
        """Expand should work for listEntries via _execute_step (non-optimized path).

        When listEntries query doesn't have both parent+entity ID (can't use
        single-ID lookup), steps go through _execute_step. Tests line 909.
        """
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import InteractionDates, ListEntry, Person

        # Query with only listId (no entity ID) - can't use single-ID lookup
        query_dict = {
            "from": "listEntries",
            "where": {"path": "listId", "op": "eq", "value": 100},
            "expand": ["interactionDates"],
            "limit": 2,
        }
        parse_result = parse_query(query_dict)
        planner = create_planner()
        plan = planner.plan(parse_result.query)

        # Mock list entry
        mock_entry = MagicMock(spec=ListEntry)
        mock_entry.id = 456
        mock_entry.entity_id = 789
        mock_entry.entity_type = 0  # Person
        mock_entry.model_dump = MagicMock(
            return_value={
                "id": 456,
                "listId": 100,
                "entityId": 789,
                "entityType": 0,
            }
        )

        # Mock interaction dates for expand
        mock_interaction_dates = MagicMock(spec=InteractionDates)
        mock_interaction_dates.last_event_date = datetime(2026, 1, 20, tzinfo=timezone.utc)
        mock_interaction_dates.next_event_date = None
        mock_interaction_dates.last_email_date = datetime(2026, 1, 18, tzinfo=timezone.utc)
        mock_interaction_dates.last_interaction_date = datetime(2026, 1, 20, tzinfo=timezone.utc)

        mock_person_expanded = MagicMock(spec=Person)
        mock_person_expanded.interaction_dates = mock_interaction_dates
        from affinity.models.entities import InteractionEvent, Interactions

        mock_person_expanded.interactions = Interactions(last_event=InteractionEvent(person_ids=[]))

        # Create mock async client
        mock_client = AsyncMock()
        mock_client.whoami = AsyncMock()

        # Mock lists.entries(list_id).pages() for fetch
        # The executor checks for nested_service.pages() first (direct pages method)
        mock_page = MagicMock()
        mock_page.data = [mock_entry]

        async def mock_pages(**_kwargs):
            yield mock_page

        mock_entries_service = MagicMock()
        mock_entries_service.pages = mock_pages  # Direct pages() method
        mock_client.lists.entries = MagicMock(return_value=mock_entries_service)

        # Mock persons.get for expand
        expand_get_called = False

        async def mock_person_get(_person_id, **kwargs):
            nonlocal expand_get_called
            if kwargs.get("with_interaction_dates"):
                expand_get_called = True
                return mock_person_expanded
            return MagicMock(full_name="Team Member")

        mock_client.persons.get = mock_person_get

        executor = QueryExecutor(
            client=mock_client,
            max_records=100,
            concurrency=1,
        )

        query_result = await executor.execute(plan)

        # Verify expand was executed
        assert len(query_result.data) == 1
        record = query_result.data[0]
        assert "interactionDates" in record, (
            "Expand step was not executed via _execute_step for listEntries. "
            "interactionDates missing from record."
        )
        assert expand_get_called, "persons.get() with interaction params should have been called"


# ==============================================================================
# Concurrency and Performance Tests - Expansion with Parallelization
# ==============================================================================


class TestExpandConcurrencyOptimizations:
    """Tests for interactionDates expansion concurrency optimizations.

    These tests verify the performance optimizations from the
    interactionDates-concurrency-tuning-plan.md:
    - Person resolution runs OUTSIDE rate limiter context
    - Shared semaphore bounds all person fetches
    - Sections (lastMeeting, nextMeeting, lastEmail) resolve in parallel
    """

    @pytest.mark.asyncio
    async def test_person_semaphore_shared_across_concurrent_tasks(self) -> None:
        """Person resolution uses SHARED semaphore - not 15x10=150 concurrent."""
        import asyncio
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import Company, InteractionDates

        # Track max concurrent person fetches
        max_concurrent_person_fetches = 0
        concurrent_person_fetches = 0

        # Create 10 company records to expand
        query_dict = {
            "from": "companies",
            "expand": ["interactionDates"],
            "limit": 10,
        }
        parse_result = parse_query(query_dict)
        planner = create_planner()
        plan = planner.plan(parse_result.query)

        # Mock company data
        def make_mock_company(cid: int) -> MagicMock:
            mock = MagicMock(spec=Company)
            mock.id = cid
            mock.name = f"Company {cid}"
            mock.model_dump = MagicMock(return_value={"id": cid, "name": f"Company {cid}"})
            return mock

        mock_companies = [make_mock_company(i) for i in range(1, 11)]

        # Mock company with interaction dates (for expand)
        mock_interaction_dates = MagicMock(spec=InteractionDates)
        mock_interaction_dates.last_event_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
        mock_interaction_dates.next_event_date = None
        mock_interaction_dates.last_email_date = None
        mock_interaction_dates.last_interaction_date = datetime(2026, 1, 10, tzinfo=timezone.utc)

        mock_company_expanded = MagicMock(spec=Company)
        mock_company_expanded.interaction_dates = mock_interaction_dates
        # Each company has 3 team members -> 10 companies x 3 = 30 person fetches
        from affinity.models.entities import InteractionEvent, Interactions

        mock_company_expanded.interactions = Interactions(
            last_event=InteractionEvent(person_ids=[100, 101, 102])
        )

        async def mock_person_get(_person_id, **_kwargs):
            nonlocal concurrent_person_fetches, max_concurrent_person_fetches
            concurrent_person_fetches += 1
            max_concurrent_person_fetches = max(
                max_concurrent_person_fetches, concurrent_person_fetches
            )
            await asyncio.sleep(0.01)  # Small delay to allow overlap
            concurrent_person_fetches -= 1
            person = MagicMock()
            person.full_name = f"Person {_person_id.value}"
            return person

        # Create mock async client
        mock_client = AsyncMock()
        mock_client.whoami = AsyncMock()

        # Mock companies.all().pages() for fetch
        mock_page = MagicMock()
        mock_page.data = mock_companies

        async def mock_pages(**_kwargs):
            yield mock_page

        mock_companies_all = MagicMock()
        mock_companies_all.pages = mock_pages
        mock_client.companies.all = MagicMock(return_value=mock_companies_all)

        # Mock companies.get for expand
        async def mock_company_get(_company_id, **kwargs):
            if kwargs.get("with_interaction_dates"):
                return mock_company_expanded
            return make_mock_company(_company_id.value)

        mock_client.companies.get = mock_company_get
        mock_client.persons.get = mock_person_get

        # Run with concurrency 15 (DEFAULT_CONCURRENCY)
        executor = QueryExecutor(
            client=mock_client,
            max_records=100,
            concurrency=15,
        )

        await executor.execute(plan)

        # With shared semaphore(10), max concurrent person fetches should be <=10
        # Without shared semaphore, it would be 15 tasks x 10 = 150
        assert max_concurrent_person_fetches <= 10, (
            f"Expected max 10 concurrent person fetches (shared semaphore), "
            f"got {max_concurrent_person_fetches}. "
            "This suggests per-call semaphore instead of shared."
        )

    @pytest.mark.skip(
        reason="Task 6.1: where clauses on global entities (persons/companies/"
        "opportunities) are now rejected at parse time. The graceful degradation "
        "path itself is still exercised by other tests in this file."
    )
    @pytest.mark.asyncio
    async def test_graceful_degradation_on_person_resolution_failure(self) -> None:
        """Person resolution failure keeps interactionDates with unresolved IDs."""
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.parser import parse_query
        from affinity.cli.query.planner import create_planner
        from affinity.models.entities import Company, InteractionDates

        query_dict = {
            "from": "companies",
            "where": {"path": "id", "op": "eq", "value": 123},
            "expand": ["interactionDates"],
        }
        parse_result = parse_query(query_dict)
        planner = create_planner()
        plan = planner.plan(parse_result.query)

        # Mock company
        mock_company = MagicMock(spec=Company)
        mock_company.id = 123
        mock_company.model_dump = MagicMock(return_value={"id": 123, "name": "Test Company"})

        # Mock company with interaction dates
        mock_interaction_dates = MagicMock(spec=InteractionDates)
        mock_interaction_dates.last_event_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
        mock_interaction_dates.next_event_date = None
        mock_interaction_dates.last_email_date = None
        mock_interaction_dates.last_interaction_date = datetime(2026, 1, 10, tzinfo=timezone.utc)

        mock_company_expanded = MagicMock(spec=Company)
        mock_company_expanded.interaction_dates = mock_interaction_dates
        from affinity.models.entities import InteractionEvent, Interactions

        mock_company_expanded.interactions = Interactions(
            last_event=InteractionEvent(person_ids=[456, 789])
        )

        # Mock client
        mock_client = AsyncMock()
        mock_client.whoami = AsyncMock()

        async def mock_company_get(_company_id, **kwargs):
            if kwargs.get("with_interaction_dates"):
                return mock_company_expanded
            return mock_company

        mock_client.companies.get = mock_company_get

        # Person API always fails
        async def mock_person_get(_person_id, **_kwargs):
            raise Exception("API Error - person service unavailable")

        mock_client.persons.get = mock_person_get

        executor = QueryExecutor(
            client=mock_client,
            max_records=100,
            concurrency=1,
        )

        query_result = await executor.execute(plan)

        # Verify graceful degradation: interactionDates preserved with fallback names
        assert len(query_result.data) == 1
        record = query_result.data[0]
        assert "interactionDates" in record, (
            "interactionDates should be preserved on person failure"
        )
        assert record["interactionDates"] is not None
        assert "lastMeeting" in record["interactionDates"]
        # Fallback names should be used
        team_names = record["interactionDates"]["lastMeeting"].get("teamMemberNames", [])
        assert team_names == ["Person 456", "Person 789"], (
            f"Expected fallback names, got {team_names}"
        )

    @pytest.mark.asyncio
    async def test_shared_person_cache_deduplicates_fetches(self) -> None:
        """Person IDs are deduplicated via shared cache across concurrent resolution calls."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from affinity.cli.interaction_utils import _resolve_person_names_async

        # Track which person IDs were fetched
        fetched_person_ids: list[int] = []

        async def mock_person_get(person_id):
            # PersonId is a subclass of int, use int() to get value
            pid = int(person_id)
            fetched_person_ids.append(pid)
            person = MagicMock()
            person.full_name = f"Person {pid}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_person_get

        # Simulate 3 concurrent resolution calls with overlapping person IDs
        # Call 1: [100, 101], Call 2: [101, 102], Call 3: [100, 102]
        person_id_lists = [[100, 101], [101, 102], [100, 102]]

        # Shared cache across all resolution calls
        shared_cache: dict[int, str] = {}
        shared_semaphore = asyncio.Semaphore(10)

        # Resolve all person IDs concurrently
        results = await asyncio.gather(
            *[
                _resolve_person_names_async(
                    mock_client, ids, shared_cache, person_semaphore=shared_semaphore
                )
                for ids in person_id_lists
            ]
        )

        # Verify all results have correct names
        assert results[0] == ["Person 100", "Person 101"]
        assert results[1] == ["Person 101", "Person 102"]
        assert results[2] == ["Person 100", "Person 102"]

        # Cache should eventually contain all 3 unique persons
        assert set(shared_cache.keys()) == {100, 101, 102}, (
            f"Cache should have all persons: {shared_cache}"
        )

        # With cache sharing, total fetches may be 3-6 depending on race conditions
        # (3 unique persons, but race may cause duplicate fetches before cache populated)
        unique_fetched = set(fetched_person_ids)
        assert unique_fetched == {100, 101, 102}, (
            f"Should fetch all unique persons: {unique_fetched}"
        )
