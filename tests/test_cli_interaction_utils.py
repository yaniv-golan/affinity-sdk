"""Unit tests for interaction_utils.py.

Tests the transform and flatten functions used by list export --expand interactions
and query expand: ["interactionDates"].
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from affinity.cli.interaction_utils import (
    INTERACTION_CSV_COLUMNS,
    _resolve_person_names_async,
    flatten_interactions_for_csv,
    resolve_interaction_names_async,
    transform_interaction_data,
)


class TestTransformInteractionData:
    """Tests for transform_interaction_data function."""

    def test_returns_none_when_interaction_dates_is_none(self) -> None:
        """Test that None is returned when interaction_dates is None."""
        result = transform_interaction_data(None, None)
        assert result is None

    def test_transforms_last_meeting(self) -> None:
        """Test transformation of last meeting (last_event) data."""
        from affinity.models.entities import InteractionDates

        interaction_dates = InteractionDates(
            last_event_date=datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
        )

        with patch("affinity.cli.interaction_utils.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
            mock_datetime.side_effect = datetime

            result = transform_interaction_data(interaction_dates, None)

        assert result is not None
        assert "lastMeeting" in result
        assert result["lastMeeting"]["date"] == "2026-01-10T10:00:00+00:00"
        assert result["lastMeeting"]["daysSince"] == 5

    def test_transforms_next_meeting(self) -> None:
        """Test transformation of next meeting (next_event) data."""
        from affinity.models.entities import InteractionDates

        interaction_dates = InteractionDates(
            next_event_date=datetime(2026, 1, 25, 14, 0, 0, tzinfo=timezone.utc),
        )

        with patch("affinity.cli.interaction_utils.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
            mock_datetime.side_effect = datetime

            result = transform_interaction_data(interaction_dates, None)

        assert result is not None
        assert "nextMeeting" in result
        assert result["nextMeeting"]["date"] == "2026-01-25T14:00:00+00:00"
        assert result["nextMeeting"]["daysUntil"] == 10

    def test_transforms_last_email(self) -> None:
        """Test transformation of last email data."""
        from affinity.models.entities import InteractionDates

        interaction_dates = InteractionDates(
            last_email_date=datetime(2026, 1, 12, 9, 30, 0, tzinfo=timezone.utc),
        )

        with patch("affinity.cli.interaction_utils.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
            mock_datetime.side_effect = datetime

            result = transform_interaction_data(interaction_dates, None)

        assert result is not None
        assert "lastEmail" in result
        assert result["lastEmail"]["date"] == "2026-01-12T09:30:00+00:00"
        assert result["lastEmail"]["daysSince"] == 3

    def test_transforms_last_interaction(self) -> None:
        """Test transformation of last interaction data."""
        from affinity.models.entities import InteractionDates

        interaction_dates = InteractionDates(
            last_interaction_date=datetime(2026, 1, 14, 15, 0, 0, tzinfo=timezone.utc),
        )

        with patch("affinity.cli.interaction_utils.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
            mock_datetime.side_effect = datetime

            result = transform_interaction_data(interaction_dates, None)

        assert result is not None
        assert "lastInteraction" in result
        assert result["lastInteraction"]["date"] == "2026-01-14T15:00:00+00:00"
        assert result["lastInteraction"]["daysSince"] == 0  # Same day

    def test_includes_team_member_ids_from_interactions(self) -> None:
        """Test that team member IDs are extracted from interactions model."""
        from affinity.models.entities import InteractionDates, InteractionEvent, Interactions

        interaction_dates = InteractionDates(
            last_event_date=datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
        )
        interactions = Interactions(
            last_event=InteractionEvent(person_ids=[101, 102, 103]),
        )

        result = transform_interaction_data(interaction_dates, interactions)

        assert result is not None
        assert "lastMeeting" in result
        assert result["lastMeeting"]["teamMemberIds"] == [101, 102, 103]

    def test_resolves_person_names_when_client_provided(self) -> None:
        """Test that person names are resolved when client is provided."""
        from affinity.models.entities import InteractionDates, InteractionEvent, Interactions

        interaction_dates = InteractionDates(
            last_event_date=datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
        )
        interactions = Interactions(
            last_event=InteractionEvent(person_ids=[101, 102]),
        )

        # Mock client and person responses
        mock_client = MagicMock()
        mock_person_1 = MagicMock()
        mock_person_1.full_name = "Alice Smith"
        mock_person_2 = MagicMock()
        mock_person_2.full_name = "Bob Jones"
        mock_client.persons.get.side_effect = [mock_person_1, mock_person_2]

        result = transform_interaction_data(interaction_dates, interactions, client=mock_client)

        assert result is not None
        assert "lastMeeting" in result
        assert result["lastMeeting"]["teamMemberNames"] == ["Alice Smith", "Bob Jones"]

    def test_uses_person_name_cache(self) -> None:
        """Test that person name cache is used to avoid duplicate lookups."""
        from affinity.models.entities import InteractionDates, InteractionEvent, Interactions

        interaction_dates = InteractionDates(
            last_event_date=datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
        )
        interactions = Interactions(
            last_event=InteractionEvent(person_ids=[101, 102]),
        )

        mock_client = MagicMock()
        mock_person = MagicMock()
        mock_person.full_name = "New Person"
        mock_client.persons.get.return_value = mock_person

        # Pre-populate cache
        cache: dict[int, str] = {101: "Cached Alice"}

        result = transform_interaction_data(
            interaction_dates, interactions, client=mock_client, person_name_cache=cache
        )

        assert result is not None
        assert result["lastMeeting"]["teamMemberNames"] == ["Cached Alice", "New Person"]
        # Only one API call made (for person 102, 101 was cached)
        assert mock_client.persons.get.call_count == 1

    def test_returns_empty_dict_when_no_dates(self) -> None:
        """Test that None is returned when InteractionDates has no dates."""
        from affinity.models.entities import InteractionDates

        interaction_dates = InteractionDates()  # All fields None

        result = transform_interaction_data(interaction_dates, None)

        assert result is None

    def test_all_dates_combined(self) -> None:
        """Test transformation with all date types present."""
        from affinity.models.entities import InteractionDates

        interaction_dates = InteractionDates(
            last_event_date=datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc),
            next_event_date=datetime(2026, 1, 20, 14, 0, 0, tzinfo=timezone.utc),
            last_email_date=datetime(2026, 1, 8, 9, 0, 0, tzinfo=timezone.utc),
            last_interaction_date=datetime(2026, 1, 10, 15, 0, 0, tzinfo=timezone.utc),
        )

        with patch("affinity.cli.interaction_utils.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
            mock_datetime.side_effect = datetime

            result = transform_interaction_data(interaction_dates, None)

        assert result is not None
        assert "lastMeeting" in result
        assert "nextMeeting" in result
        assert "lastEmail" in result
        assert "lastInteraction" in result


class TestFlattenInteractionsForCsv:
    """Tests for flatten_interactions_for_csv function."""

    def test_returns_all_columns_with_empty_strings_when_none(self) -> None:
        """Test that all columns are returned with empty strings when input is None."""
        result = flatten_interactions_for_csv(None)

        assert len(result) == len(INTERACTION_CSV_COLUMNS)
        for col in INTERACTION_CSV_COLUMNS:
            assert col in result
            assert result[col] == ""

    def test_returns_all_columns_with_empty_strings_when_empty_dict(self) -> None:
        """Test that all columns are returned with empty strings for empty dict."""
        result = flatten_interactions_for_csv({})

        assert len(result) == len(INTERACTION_CSV_COLUMNS)
        for col in INTERACTION_CSV_COLUMNS:
            assert result[col] == ""

    def test_flattens_last_meeting(self) -> None:
        """Test flattening of lastMeeting data."""
        interactions = {
            "lastMeeting": {
                "date": "2026-01-10T10:00:00Z",
                "daysSince": 5,
                "teamMemberNames": ["Alice", "Bob"],
            }
        }

        result = flatten_interactions_for_csv(interactions)

        assert result["lastMeetingDate"] == "2026-01-10T10:00:00Z"
        assert result["lastMeetingDaysSince"] == "5"
        assert result["lastMeetingTeamMembers"] == "Alice, Bob"

    def test_flattens_next_meeting(self) -> None:
        """Test flattening of nextMeeting data."""
        interactions = {
            "nextMeeting": {
                "date": "2026-01-25T14:00:00Z",
                "daysUntil": 10,
                "teamMemberNames": ["Carol"],
            }
        }

        result = flatten_interactions_for_csv(interactions)

        assert result["nextMeetingDate"] == "2026-01-25T14:00:00Z"
        assert result["nextMeetingDaysUntil"] == "10"
        assert result["nextMeetingTeamMembers"] == "Carol"

    def test_flattens_last_email(self) -> None:
        """Test flattening of lastEmail data."""
        interactions = {
            "lastEmail": {
                "date": "2026-01-12T09:30:00Z",
                "daysSince": 3,
            }
        }

        result = flatten_interactions_for_csv(interactions)

        assert result["lastEmailDate"] == "2026-01-12T09:30:00Z"
        assert result["lastEmailDaysSince"] == "3"

    def test_flattens_last_interaction(self) -> None:
        """Test flattening of lastInteraction data."""
        interactions = {
            "lastInteraction": {
                "date": "2026-01-14T15:00:00Z",
                "daysSince": 1,
            }
        }

        result = flatten_interactions_for_csv(interactions)

        assert result["lastInteractionDate"] == "2026-01-14T15:00:00Z"
        assert result["lastInteractionDaysSince"] == "1"

    def test_handles_missing_team_members(self) -> None:
        """Test handling when teamMemberNames is missing."""
        interactions = {
            "lastMeeting": {
                "date": "2026-01-10T10:00:00Z",
                "daysSince": 5,
                # No teamMemberNames
            }
        }

        result = flatten_interactions_for_csv(interactions)

        assert result["lastMeetingTeamMembers"] == ""

    def test_handles_empty_team_members(self) -> None:
        """Test handling when teamMemberNames is empty list."""
        interactions = {
            "lastMeeting": {
                "date": "2026-01-10T10:00:00Z",
                "daysSince": 5,
                "teamMemberNames": [],
            }
        }

        result = flatten_interactions_for_csv(interactions)

        assert result["lastMeetingTeamMembers"] == ""

    def test_handles_days_since_zero(self) -> None:
        """Test that daysSince=0 is properly converted to string."""
        interactions = {
            "lastMeeting": {
                "date": "2026-01-15T10:00:00Z",
                "daysSince": 0,
            }
        }

        result = flatten_interactions_for_csv(interactions)

        assert result["lastMeetingDaysSince"] == "0"


class TestInteractionCsvColumns:
    """Tests for INTERACTION_CSV_COLUMNS constant."""

    def test_has_expected_columns(self) -> None:
        """Test that all expected columns are present."""
        expected = [
            "lastMeetingDate",
            "lastMeetingDaysSince",
            "lastMeetingTeamMembers",
            "nextMeetingDate",
            "nextMeetingDaysUntil",
            "nextMeetingTeamMembers",
            "lastEmailDate",
            "lastEmailDaysSince",
            "lastInteractionDate",
            "lastInteractionDaysSince",
        ]
        assert expected == INTERACTION_CSV_COLUMNS

    def test_has_ten_columns(self) -> None:
        """Test that there are exactly 10 columns."""
        assert len(INTERACTION_CSV_COLUMNS) == 10


# ==============================================================================
# Async Person Resolution Tests
# ==============================================================================


class TestResolvePersonNamesAsync:
    """Tests for _resolve_person_names_async with parallel fetching."""

    @pytest.mark.asyncio
    async def test_resolves_person_names_in_parallel(self) -> None:
        """Person fetches should run in parallel, not sequentially."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        # Track concurrent calls
        concurrent_calls = 0
        max_concurrent = 0
        call_times: list[float] = []

        async def mock_get(person_id):
            nonlocal concurrent_calls, max_concurrent
            concurrent_calls += 1
            max_concurrent = max(max_concurrent, concurrent_calls)
            call_times.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.01)  # Small delay to allow overlap
            concurrent_calls -= 1
            person = MagicMock()
            person.full_name = f"Person {int(person_id)}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        # Fetch 5 persons - should run in parallel
        result = await _resolve_person_names_async(mock_client, [1, 2, 3, 4, 5])

        assert result == ["Person 1", "Person 2", "Person 3", "Person 4", "Person 5"]
        # With parallel execution, we should see >1 concurrent call
        assert max_concurrent > 1, (
            f"Expected parallel execution, but max concurrent was {max_concurrent}"
        )

    @pytest.mark.asyncio
    async def test_uses_shared_semaphore_for_bounded_concurrency(self) -> None:
        """Person fetches should respect the shared semaphore limit."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        max_concurrent = 0
        concurrent_calls = 0

        async def mock_get(person_id):
            nonlocal concurrent_calls, max_concurrent
            concurrent_calls += 1
            max_concurrent = max(max_concurrent, concurrent_calls)
            await asyncio.sleep(0.01)
            concurrent_calls -= 1
            person = MagicMock()
            person.full_name = f"Person {int(person_id)}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        # Semaphore with limit of 3
        semaphore = asyncio.Semaphore(3)

        # Fetch 10 persons with semaphore limit of 3
        result = await _resolve_person_names_async(
            mock_client, list(range(1, 11)), person_semaphore=semaphore
        )

        assert len(result) == 10
        # Should never exceed semaphore limit
        assert max_concurrent <= 3, f"Expected max 3 concurrent, got {max_concurrent}"

    @pytest.mark.asyncio
    async def test_uses_cache_to_skip_fetches(self) -> None:
        """Cached person IDs should not trigger API calls."""
        from unittest.mock import AsyncMock, MagicMock

        call_count = 0

        async def mock_get(person_id):
            nonlocal call_count
            call_count += 1
            person = MagicMock()
            person.full_name = f"Fetched {int(person_id)}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        # Pre-populated cache
        cache = {1: "Cached Alice", 3: "Cached Carol"}

        result = await _resolve_person_names_async(mock_client, [1, 2, 3, 4], cache=cache)

        assert result == ["Cached Alice", "Fetched 2", "Cached Carol", "Fetched 4"]
        assert call_count == 2, "Only uncached IDs should trigger API calls"

    @pytest.mark.asyncio
    async def test_gone_person_gets_fallback_name(self) -> None:
        """A person that is gone gets the fallback name."""
        from unittest.mock import AsyncMock, MagicMock

        from affinity.exceptions import NotFoundError

        async def mock_get(person_id):
            if int(person_id) == 2:
                raise NotFoundError("Person not found")
            person = MagicMock()
            person.full_name = f"Name {int(person_id)}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        result = await _resolve_person_names_async(mock_client, [1, 2, 3])

        assert result == ["Name 1", "Person 2", "Name 3"]

    @pytest.mark.asyncio
    async def test_other_errors_propagate(self) -> None:
        """Any error but a gone person propagates (a fallback would hide it)."""
        from unittest.mock import AsyncMock

        async def mock_get(_person_id):
            raise Exception("API Error")

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        with pytest.raises(Exception, match="API Error"):
            await _resolve_person_names_async(mock_client, [1, 2, 3])


class TestResolveInteractionNamesAsync:
    """Tests for resolve_interaction_names_async with section parallelization."""

    @pytest.mark.asyncio
    async def test_resolves_all_sections_in_parallel(self) -> None:
        """Sections (lastMeeting, nextMeeting, lastEmail) should resolve in parallel."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        async def mock_get(person_id):
            # Add delay to make parallel vs sequential observable
            await asyncio.sleep(0.02)
            person = MagicMock()
            person.full_name = f"Person {int(person_id)}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        interaction_data = {
            "lastMeeting": {"teamMemberIds": [1, 2]},
            "nextMeeting": {"teamMemberIds": [3]},
            "lastEmail": {"teamMemberIds": [4, 5]},
        }

        start = asyncio.get_event_loop().time()
        await resolve_interaction_names_async(mock_client, interaction_data)
        duration = asyncio.get_event_loop().time() - start

        # Verify all sections resolved
        assert interaction_data["lastMeeting"]["teamMemberNames"] == ["Person 1", "Person 2"]
        assert interaction_data["nextMeeting"]["teamMemberNames"] == ["Person 3"]
        assert interaction_data["lastEmail"]["teamMemberNames"] == ["Person 4", "Person 5"]

        # With 5 person fetches at 20ms each:
        # - Sequential (sections): 3 sections x ~40ms = ~120ms
        # - Parallel (sections + persons): ~40ms (2 persons is longest section)
        # Allow some margin for test execution overhead
        assert duration < 0.1, f"Expected parallel execution, took {duration:.3f}s"

    @pytest.mark.asyncio
    async def test_passes_shared_semaphore_to_person_resolution(self) -> None:
        """Shared semaphore should limit concurrent person fetches across sections."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        max_concurrent = 0
        concurrent_calls = 0

        async def mock_get(person_id):
            nonlocal concurrent_calls, max_concurrent
            concurrent_calls += 1
            max_concurrent = max(max_concurrent, concurrent_calls)
            await asyncio.sleep(0.01)
            concurrent_calls -= 1
            person = MagicMock()
            person.full_name = f"Person {int(person_id)}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        # 9 person IDs across 3 sections, but semaphore limit of 2
        interaction_data = {
            "lastMeeting": {"teamMemberIds": [1, 2, 3]},
            "nextMeeting": {"teamMemberIds": [4, 5, 6]},
            "lastEmail": {"teamMemberIds": [7, 8, 9]},
        }

        semaphore = asyncio.Semaphore(2)
        await resolve_interaction_names_async(
            mock_client, interaction_data, person_semaphore=semaphore
        )

        # Should never exceed semaphore limit
        assert max_concurrent <= 2, f"Expected max 2 concurrent, got {max_concurrent}"

    @pytest.mark.asyncio
    async def test_handles_none_interaction_data(self) -> None:
        """Should handle None interaction_data gracefully."""
        from unittest.mock import AsyncMock

        mock_client = AsyncMock()

        # Should not raise
        await resolve_interaction_names_async(mock_client, None)

    @pytest.mark.asyncio
    async def test_handles_missing_sections(self) -> None:
        """Should handle interaction_data with missing sections."""
        from unittest.mock import AsyncMock, MagicMock

        async def mock_get(person_id):
            person = MagicMock()
            person.full_name = f"Person {int(person_id)}"
            return person

        mock_client = AsyncMock()
        mock_client.persons.get = mock_get

        # Only lastMeeting has teamMemberIds
        interaction_data = {
            "lastMeeting": {"teamMemberIds": [1]},
            "lastInteraction": {"date": "2026-01-10"},  # No teamMemberIds
        }

        await resolve_interaction_names_async(mock_client, interaction_data)

        assert interaction_data["lastMeeting"]["teamMemberNames"] == ["Person 1"]
        assert "teamMemberNames" not in interaction_data.get("lastInteraction", {})
