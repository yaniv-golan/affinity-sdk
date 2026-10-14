"""Tests for InteractionService date validation, auto-chunking, and executor fixes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.exceptions import AuthenticationError
from affinity.models.pagination import PaginatedResponse
from affinity.models.secondary import Interaction
from affinity.models.types import (
    InteractionType,
    PersonId,
)
from affinity.services.v1_only import (
    AsyncInteractionService,
    InteractionService,
    _chunk_date_range,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_T1 = datetime(2024, 6, 1, tzinfo=timezone.utc)
_T365 = _T0 + timedelta(days=365)


def _make_interaction(id_: int = 1) -> dict:
    return {
        "id": id_,
        "type": int(InteractionType.EMAIL),
        "date": "2024-03-01T00:00:00Z",
    }


def _noop_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"emails": [_make_interaction()], "next_page_token": None},
        request=request,
    )


def _make_service(handler=None) -> InteractionService:
    http = HTTPClient(
        ClientConfig(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler or _noop_handler),
        )
    )
    return InteractionService(http)


def _make_async_service(handler=None) -> AsyncHTTPClient:
    return AsyncHTTPClient(
        ClientConfig(
            api_key="test",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler or _noop_handler),
        )
    )


# =============================================================================
# Sync list() validation
# =============================================================================


@pytest.mark.req("SDK-INTERACTIONS-DATE-VALIDATION")
class TestInteractionDateValidation:
    def test_list_requires_start_time(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="start_time is required"):
            svc.list(type=InteractionType.EMAIL, end_time=_T1, person_id=PersonId(1))

    def test_list_requires_end_time(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="end_time is required"):
            svc.list(type=InteractionType.EMAIL, start_time=_T0, person_id=PersonId(1))

    def test_list_requires_entity_id(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="At least one entity filter"):
            svc.list(type=InteractionType.EMAIL, start_time=_T0, end_time=_T1)

    def test_list_rejects_range_over_365_days(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="exceeds 365 days"):
            svc.list(
                type=InteractionType.EMAIL,
                start_time=_T0,
                end_time=_T0 + timedelta(days=366),
                person_id=PersonId(1),
            )

    def test_list_rejects_365_days_plus_one_second(self) -> None:
        """timedelta comparison catches fractional-day overflow that .days misses."""
        svc = _make_service()
        with pytest.raises(ValueError, match="exceeds 365 days"):
            svc.list(
                type=InteractionType.EMAIL,
                start_time=_T0,
                end_time=_T0 + timedelta(days=365, seconds=1),
                person_id=PersonId(1),
            )

    def test_list_rejects_inverted_range(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="start_time must be before end_time"):
            svc.list(
                type=InteractionType.EMAIL,
                start_time=_T1,
                end_time=_T0,
                person_id=PersonId(1),
            )

    def test_list_accepts_exact_365_day_range(self) -> None:
        svc = _make_service()
        result = svc.list(
            type=InteractionType.EMAIL,
            start_time=_T0,
            end_time=_T365,
            person_id=PersonId(1),
        )
        assert len(result.data) == 1

    def test_list_type_still_required(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="type is required"):
            svc.list(start_time=_T0, end_time=_T1, person_id=PersonId(1))


# =============================================================================
# Sync iter() auto-chunking
# =============================================================================


@pytest.mark.req("SDK-INTERACTIONS-AUTO-CHUNK")
class TestInteractionAutoChunk:
    def test_iter_defaults_end_time_to_now(self) -> None:
        """When end_time is omitted, iter() defaults to now(utc)."""
        svc = _make_service()
        # Should not raise — end_time defaulted
        iterator = svc.iter(
            type=InteractionType.EMAIL,
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            person_id=PersonId(1),
        )
        items = list(iterator)
        assert len(items) >= 1

    def test_iter_single_chunk_passthrough(self) -> None:
        """Range <= 365 days creates 1 chunk, normal pagination."""
        svc = _make_service()
        items = list(
            svc.iter(
                type=InteractionType.EMAIL,
                start_time=_T0,
                end_time=_T1,
                person_id=PersonId(1),
            )
        )
        assert len(items) == 1
        assert items[0].id == 1

    def test_iter_multi_chunk(self) -> None:
        """500-day range => 2 chunks, synthetic cursor bridges them."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200,
                json={
                    "emails": [_make_interaction(call_count)],
                    "next_page_token": None,
                },
                request=request,
            )

        svc = _make_service(handler)
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=500)
        items = list(
            svc.iter(
                type=InteractionType.EMAIL,
                start_time=start,
                end_time=end,
                person_id=PersonId(1),
            )
        )
        assert len(items) == 2
        assert call_count == 2
        assert items[0].id == 1
        assert items[1].id == 2

    def test_iter_requires_start_time(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="start_time is required"):
            svc.iter(type=InteractionType.EMAIL, person_id=PersonId(1))

    def test_iter_requires_type(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="type is required"):
            svc.iter(start_time=_T0, person_id=PersonId(1))

    def test_iter_requires_entity_id(self) -> None:
        svc = _make_service()
        with pytest.raises(ValueError, match="At least one entity filter"):
            svc.iter(type=InteractionType.EMAIL, start_time=_T0, end_time=_T1)

    def test_iter_rejects_start_after_resolved_end(self) -> None:
        svc = _make_service()
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="start_time must be before end_time"):
            svc.iter(
                type=InteractionType.EMAIL,
                start_time=future,
                end_time=past,
                person_id=PersonId(1),
            )


# =============================================================================
# Falsy truthiness fix
# =============================================================================


@pytest.mark.req("SDK-INTERACTIONS-FALSY-FIX")
class TestInteractionFalsyFix:
    def test_list_sends_person_id_zero(self) -> None:
        """PersonId(0) must appear in request params (not dropped by falsy check)."""
        seen_params: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            for key, val in request.url.params.items():
                seen_params[key] = val
            return httpx.Response(
                200,
                json={"emails": [], "next_page_token": None},
                request=request,
            )

        svc = _make_service(handler)
        svc.list(
            type=InteractionType.EMAIL,
            person_id=PersonId(0),
            start_time=_T0,
            end_time=_T1,
        )
        assert "person_id" in seen_params
        assert seen_params["person_id"] == "0"

    def test_list_sends_page_size_zero_if_not_none(self) -> None:
        """page_size=0 should be sent (not dropped)."""
        seen_params: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            for key, val in request.url.params.items():
                seen_params[key] = val
            return httpx.Response(
                200,
                json={"emails": [], "next_page_token": None},
                request=request,
            )

        svc = _make_service(handler)
        svc.list(
            type=InteractionType.EMAIL,
            person_id=PersonId(1),
            start_time=_T0,
            end_time=_T1,
            page_size=0,
        )
        assert "page_size" in seen_params
        assert seen_params["page_size"] == "0"


# =============================================================================
# Timezone validation
# =============================================================================


@pytest.mark.req("SDK-INTERACTIONS-DATE-VALIDATION")
class TestInteractionTimezoneValidation:
    def test_list_rejects_mixed_tz_naive_start_aware_end(self) -> None:
        svc = _make_service()
        naive = datetime(2024, 1, 1)  # no tzinfo
        aware = datetime(2024, 6, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="both be timezone-aware or both naive"):
            svc.list(
                type=InteractionType.EMAIL,
                start_time=naive,
                end_time=aware,
                person_id=PersonId(1),
            )

    def test_iter_rejects_naive_start_when_end_defaults_to_utc(self) -> None:
        svc = _make_service()
        naive = datetime(2024, 1, 1)
        with pytest.raises(ValueError, match="both be timezone-aware or both naive"):
            svc.iter(
                type=InteractionType.EMAIL,
                start_time=naive,
                person_id=PersonId(1),
            )


# =============================================================================
# Async date validation
# =============================================================================


@pytest.mark.req("SDK-INTERACTIONS-DATE-VALIDATION")
class TestAsyncInteractionDateValidation:
    @pytest.mark.asyncio
    async def test_async_list_requires_start_time(self) -> None:
        http = _make_async_service()
        async with http:
            svc = AsyncInteractionService(http)
            with pytest.raises(ValueError, match="start_time is required"):
                await svc.list(type=InteractionType.EMAIL, end_time=_T1, person_id=PersonId(1))

    @pytest.mark.asyncio
    async def test_async_list_requires_end_time(self) -> None:
        http = _make_async_service()
        async with http:
            svc = AsyncInteractionService(http)
            with pytest.raises(ValueError, match="end_time is required"):
                await svc.list(type=InteractionType.EMAIL, start_time=_T0, person_id=PersonId(1))

    @pytest.mark.asyncio
    async def test_async_list_requires_entity_id(self) -> None:
        http = _make_async_service()
        async with http:
            svc = AsyncInteractionService(http)
            with pytest.raises(ValueError, match="At least one entity filter"):
                await svc.list(type=InteractionType.EMAIL, start_time=_T0, end_time=_T1)

    @pytest.mark.asyncio
    async def test_async_iter_auto_chunks(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200,
                json={
                    "emails": [_make_interaction(call_count)],
                    "next_page_token": None,
                },
                request=request,
            )

        http = _make_async_service(handler)
        async with http:
            svc = AsyncInteractionService(http)
            start = datetime(2023, 1, 1, tzinfo=timezone.utc)
            end = start + timedelta(days=500)
            items = []
            async for item in svc.iter(
                type=InteractionType.EMAIL,
                start_time=start,
                end_time=end,
                person_id=PersonId(1),
            ):
                items.append(item)
            assert len(items) == 2
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_iter_rejects_invalid_range(self) -> None:
        http = _make_async_service()
        async with http:
            svc = AsyncInteractionService(http)
            with pytest.raises(ValueError, match="start_time must be before end_time"):
                svc.iter(
                    type=InteractionType.EMAIL,
                    start_time=_T1,
                    end_time=_T0,
                    person_id=PersonId(1),
                )


# =============================================================================
# _chunk_date_range utility
# =============================================================================


class TestChunkDateRange:
    def test_single_chunk(self) -> None:
        chunks = _chunk_date_range(_T0, _T1)
        assert len(chunks) == 1
        assert chunks[0] == (_T0, _T1)

    def test_multi_chunk(self) -> None:
        end = _T0 + timedelta(days=500)
        chunks = _chunk_date_range(_T0, end)
        assert len(chunks) == 2
        assert chunks[0] == (_T0, _T0 + timedelta(days=365))
        assert chunks[1] == (_T0 + timedelta(days=365), end)

    def test_exact_boundary(self) -> None:
        end = _T0 + timedelta(days=365)
        chunks = _chunk_date_range(_T0, end)
        assert len(chunks) == 1

    def test_empty_range(self) -> None:
        chunks = _chunk_date_range(_T0, _T0)
        assert len(chunks) == 0


# =============================================================================
# Executor helper tests
# =============================================================================


@pytest.mark.req("SDK-INTERACTIONS-EXECUTOR-FIX")
class TestExecutorInteractionFetch:
    @pytest.mark.asyncio
    async def test_fetch_interactions_for_entity_loops_all_types(self) -> None:
        from affinity.cli.query.executor import _fetch_interactions_for_entity

        types_called: list[InteractionType] = []

        async def mock_list(*, type, **_kw):
            types_called.append(type)
            return PaginatedResponse[Interaction](data=[], next_page_token=None)

        service = AsyncMock()
        service.list = mock_list

        def mock_iter(**kwargs):
            from affinity.models.pagination import AsyncPageIterator

            async def fetch_page(_cursor):
                return await mock_list(**kwargs)

            return AsyncPageIterator(fetch_page)

        service.iter = mock_iter
        await _fetch_interactions_for_entity(service, {"person_id": PersonId(1)}, days=30)
        assert len(types_called) == 4

    @pytest.mark.asyncio
    async def test_fetch_interactions_for_entity_respects_limit(self) -> None:
        from affinity.cli.query.executor import _fetch_interactions_for_entity

        async def mock_list(**_kw):
            items = [Interaction.model_validate(_make_interaction(i)) for i in range(50)]
            return PaginatedResponse[Interaction](data=items, next_page_token=None)

        service = AsyncMock()
        service.list = mock_list

        def mock_iter(**kwargs):
            from affinity.models.pagination import AsyncPageIterator

            async def fetch_page(_cursor):
                return await mock_list(**kwargs)

            return AsyncPageIterator(fetch_page)

        service.iter = mock_iter
        results = await _fetch_interactions_for_entity(
            service, {"person_id": PersonId(1)}, limit=10, days=30
        )
        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_fetch_interactions_for_entity_propagates_a_failed_type(self) -> None:
        """A failed interaction type fails the lookup: returning the other types' results
        would make a _count / none filter match on missing data."""
        from affinity.cli.query.executor import _fetch_interactions_for_entity

        call_count = 0

        async def mock_list(**_kw):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise httpx.ConnectError("connection refused")
            return PaginatedResponse[Interaction](
                data=[Interaction.model_validate(_make_interaction(call_count))],
                next_page_token=None,
            )

        service = AsyncMock()
        service.list = mock_list

        def mock_iter(**kwargs):
            from affinity.models.pagination import AsyncPageIterator

            async def fetch_page(_cursor):
                return await mock_list(**kwargs)

            return AsyncPageIterator(fetch_page)

        service.iter = mock_iter
        with pytest.raises(httpx.ConnectError):
            await _fetch_interactions_for_entity(service, {"person_id": PersonId(1)}, days=30)

    @pytest.mark.asyncio
    async def test_fetch_interactions_for_entity_propagates_auth_error(self) -> None:
        from affinity.cli.query.executor import _fetch_interactions_for_entity

        async def mock_list(**_kw):
            raise AuthenticationError("bad key")

        service = AsyncMock()
        service.list = mock_list

        def mock_iter(**kwargs):
            from affinity.models.pagination import AsyncPageIterator

            async def fetch_page(_cursor):
                return await mock_list(**kwargs)

            return AsyncPageIterator(fetch_page)

        service.iter = mock_iter
        with pytest.raises(AuthenticationError):
            await _fetch_interactions_for_entity(service, {"person_id": PersonId(1)}, days=30)

    @pytest.mark.asyncio
    async def test_fetch_interactions_for_entity_propagates_programming_bug(self) -> None:
        from affinity.cli.query.executor import _fetch_interactions_for_entity

        async def mock_list(**_kw):
            raise TypeError("unexpected None")

        service = AsyncMock()
        service.list = mock_list

        def mock_iter(**kwargs):
            from affinity.models.pagination import AsyncPageIterator

            async def fetch_page(_cursor):
                return await mock_list(**kwargs)

            return AsyncPageIterator(fetch_page)

        service.iter = mock_iter
        with pytest.raises(TypeError):
            await _fetch_interactions_for_entity(service, {"person_id": PersonId(1)}, days=30)

    @pytest.mark.asyncio
    async def test_include_global_service_interactions_wiring(self) -> None:
        """Include path (global_service) calls _fetch_interactions_for_entity with config."""
        from unittest.mock import MagicMock, patch

        from affinity.cli.query.executor import QueryExecutor
        from affinity.cli.query.models import ExecutionPlan, PlanStep, Query

        mock_client = AsyncMock()

        # Persons service for fetch step
        persons_service = MagicMock()

        class MockPageIter:
            def pages(self, **_kwargs):
                async def gen():
                    page = MagicMock()
                    r = MagicMock()
                    r.model_dump = MagicMock(return_value={"id": 1, "name": "Alice"})
                    page.data = [r]
                    yield page

                return gen()

        persons_service.all.return_value = MockPageIter()
        mock_client.persons = persons_service

        # Interactions service — will be called via _fetch_interactions_for_entity
        mock_interactions = AsyncMock()
        mock_client.interactions = mock_interactions

        query = Query(from_="persons", include=["interactions"])
        plan = ExecutionPlan(
            query=query,
            steps=[
                PlanStep(step_id=0, operation="fetch", entity="persons", description="Fetch"),
                PlanStep(
                    step_id=1,
                    operation="include",
                    entity="persons",
                    relationship="interactions",
                    description="Include interactions",
                    depends_on=[0],
                ),
            ],
            total_api_calls=2,
            estimated_records_fetched=1,
            estimated_memory_mb=0.01,
            warnings=[],
            recommendations=[],
            has_expensive_operations=True,
            requires_full_scan=False,
        )

        captured_calls: list[dict] = []

        async def mock_helper(_service, entity_filter, **kwargs):
            captured_calls.append({"entity_filter": entity_filter, **kwargs})
            return [{"id": 100, "type": "email"}]

        executor = QueryExecutor(mock_client)
        with patch(
            "affinity.cli.query.executor._fetch_interactions_for_entity",
            side_effect=mock_helper,
        ):
            result = await executor.execute(plan)

        assert len(captured_calls) == 1
        assert captured_calls[0]["entity_filter"] == {"person_id": 1}
        assert "interactions" in result.included
        assert len(result.included["interactions"]) == 1

    @pytest.mark.asyncio
    async def test_expand_global_service_interactions_wiring(self) -> None:
        """Filter/exists path (global_service) calls _fetch_interactions_for_entity."""
        from unittest.mock import patch

        from affinity.cli.query.executor import ExecutionContext, QueryExecutor
        from affinity.cli.query.models import ExistsClause, Query, WhereClause

        mock_client = AsyncMock()
        mock_interactions = AsyncMock()
        mock_client.interactions = mock_interactions

        query = Query(
            from_="persons",
            where=WhereClause(
                exists_=ExistsClause(**{"from": "interactions"}),
            ),
        )
        ctx = ExecutionContext(
            query=query,
            records=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
        )

        captured_calls: list[dict] = []

        async def mock_helper(_service, entity_filter, **kwargs):
            captured_calls.append({"entity_filter": entity_filter, **kwargs})
            # Return data for Alice, empty for Bob
            pid = entity_filter.get("person_id")
            if pid == 1:
                return [{"id": 100, "type": "email"}]
            return []

        executor = QueryExecutor(mock_client)
        with patch(
            "affinity.cli.query.executor._fetch_interactions_for_entity",
            side_effect=mock_helper,
        ):
            from affinity.cli.query.models import PlanStep

            step = PlanStep(step_id=1, operation="filter", description="Filter")
            await executor._execute_filter_with_preinclude(step, ctx)

        # Both records should have been queried
        assert len(captured_calls) == 2
        entity_filters = [c["entity_filter"] for c in captured_calls]
        assert {"person_id": 1} in entity_filters
        assert {"person_id": 2} in entity_filters
        # Only Alice has interactions → Bob filtered out
        assert len(ctx.records) == 1
        assert ctx.records[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_list_entries_interactions_wiring(self) -> None:
        """Path 1: _fetch_interactions_for_list_entries passes semaphore."""
        import asyncio
        from unittest.mock import patch

        from affinity.cli.query.executor import QueryExecutor

        mock_client = AsyncMock()
        mock_interactions = AsyncMock()
        mock_client.interactions = mock_interactions

        entries = [{"id": 100, "entityId": 1, "entityType": "person"}]
        results: dict[int, list] = {}

        captured_calls: list[dict] = []

        async def mock_helper(_service, entity_filter, **kwargs):
            captured_calls.append({"entity_filter": entity_filter, **kwargs})
            return [{"id": 200, "type": "email"}]

        executor = QueryExecutor(mock_client)
        executor.rate_limiter = AsyncMock()
        executor.rate_limiter.__aenter__ = AsyncMock()
        executor.rate_limiter.__aexit__ = AsyncMock()

        sem = asyncio.Semaphore(50)

        with patch(
            "affinity.cli.query.executor._fetch_interactions_for_entity",
            side_effect=mock_helper,
        ):
            await executor._fetch_interactions_for_list_entries(
                entries, results, sem, limit=10, days=30
            )

        assert len(captured_calls) == 1
        assert "semaphore" in captured_calls[0]
        assert captured_calls[0]["semaphore"] is sem
        assert "rate_limiter" in captured_calls[0]
        assert captured_calls[0]["days"] == 30
        assert captured_calls[0]["limit"] == 10
        assert 100 in results
        assert len(results[100]) == 1
