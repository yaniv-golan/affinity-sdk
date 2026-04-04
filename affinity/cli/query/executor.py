"""Query executor.

Executes query plans by orchestrating SDK service calls.
This module is CLI-only and NOT part of the public SDK API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import pydantic

from ...exceptions import AuthenticationError, AuthorizationError, NotFoundError
from ..interaction_utils import resolve_interaction_names_async, transform_interaction_data
from .aggregates import apply_having, compute_aggregates, group_and_aggregate
from .exceptions import (
    QueryExecutionError,
    QueryInterruptedError,
    QuerySafetyLimitError,
    QueryTimeoutError,
    QueryValidationError,
)
from .filters import (
    FilterContext,
    compile_filter,
    compile_filter_with_context,
    requires_relationship_data,
    resolve_field_path,
)
from .models import ExecutionPlan, PlanStep, Query, QueryResult, WhereClause
from .schema import (
    EXPANSION_REGISTRY,
    SCHEMA_REGISTRY,
    UNBOUNDED_ENTITIES,
    FetchStrategy,
    find_relationship_by_target,
    get_relationship,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from affinity import AsyncAffinity
    from affinity.models.pagination import PaginationProgress
    from affinity.services.v1_only import AsyncInteractionService

    from .schema import EntitySchema

from affinity.types import InteractionType

_ALL_INTERACTION_TYPES = [
    InteractionType.EMAIL,
    InteractionType.MEETING,
    InteractionType.CALL,
    InteractionType.CHAT_MESSAGE,
]
_DEFAULT_INTERACTION_LOOKBACK_DAYS = 90


async def _fetch_interactions_for_entity(
    service: AsyncInteractionService,
    entity_filter: dict[str, Any],
    *,
    days: int = _DEFAULT_INTERACTION_LOOKBACK_DAYS,
    limit: int = 100,
    semaphore: asyncio.Semaphore | None = None,
    rate_limiter: Any | None = None,
) -> list[dict[str, Any]]:
    """Fetch interactions across all types for a single entity.

    Args:
        service: AsyncInteractionService instance
        entity_filter: e.g. {"person_id": PersonId(123)}
        days: Lookback window (default 90)
        limit: Max interactions to return
        semaphore: Optional concurrency limiter
        rate_limiter: Optional rate limiter
    """
    from datetime import datetime, timedelta, timezone

    end_t = datetime.now(timezone.utc)
    start_t = end_t - timedelta(days=days)
    results: list[dict[str, Any]] = []
    for itype in _ALL_INTERACTION_TYPES:
        if len(results) >= limit:
            break
        try:
            async with contextlib.AsyncExitStack() as stack:
                if semaphore is not None:
                    await stack.enter_async_context(semaphore)
                if rate_limiter is not None:
                    await stack.enter_async_context(rate_limiter)
                async for i in service.iter(
                    **entity_filter,
                    type=itype,
                    start_time=start_t,
                    end_time=end_t,
                ):
                    results.append(i.model_dump(mode="json", by_alias=True))
                    if len(results) >= limit:
                        break
        except (AuthenticationError, AuthorizationError):
            raise
        except (
            httpx.HTTPStatusError,
            httpx.TimeoutException,
            httpx.ConnectError,
            pydantic.ValidationError,
        ) as e:
            logger.warning(
                "Failed to fetch %s interactions for %s: %s",
                itype.name,
                entity_filter,
                e,
            )
            continue
    return results


# =============================================================================
# Field Projection Utilities
# =============================================================================


def _set_nested_value(target: dict[str, Any], path: str, value: Any) -> None:
    """Set a value at a nested path in a dict.

    Creates intermediate dicts as needed.

    Args:
        target: Dict to set value in
        path: Dot-separated path like "fields.Status"
        value: Value to set
    """
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _extract_person_display_name(data: dict[str, Any]) -> str | None:
    """Extract display name from a person reference dict."""
    from affinity.field_resolve_utils import resolve_person

    return resolve_person(data)


def _normalize_list_entry_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize list entry field values from API format to query-friendly format.

    The Affinity API returns field values on the entity inside the list entry::

        {"entity": {"fields": {"requested": true, "data": {
            "field-123": {"name": "Status", "value": {...}}
        }}}}

    This function:
    1. Extracts custom fields into a top-level fields dict keyed by field name:
       {"fields": {"Status": "Active"}}
    2. Adds convenience aliases: listEntryId, entityId, entityName, entityType
    3. Ensures fields key always exists (defaults to {})

    This allows paths like "fields.Status", "entityName" to work in filters/select.
    """
    entity = record.get("entity")

    # Extract custom field values if available
    normalized_fields: dict[str, Any] = {}
    if entity and isinstance(entity, dict):
        fields_container = entity.get("fields")
        if fields_container and isinstance(fields_container, dict):
            fields_data = fields_container.get("data")
            if fields_data and isinstance(fields_data, dict):
                # Extract field values into a dict keyed by field name
                for _field_id, field_obj in fields_data.items():
                    if not isinstance(field_obj, dict):
                        continue

                    field_name = field_obj.get("name")
                    if not field_name:
                        continue

                    value_wrapper = field_obj.get("value")
                    if value_wrapper is None:
                        normalized_fields[field_name] = None
                        continue

                    if isinstance(value_wrapper, dict):
                        data = value_wrapper.get("data")
                        # Handle dropdown/ranked-dropdown with text value
                        if isinstance(data, dict) and "text" in data:
                            normalized_fields[field_name] = data["text"]
                        # Handle multi-select (array of values)
                        elif isinstance(data, list):
                            # Extract text/names from each item
                            extracted = []
                            for item in data:
                                if isinstance(item, dict) and "text" in item:
                                    # Dropdown item
                                    extracted.append(item["text"])
                                elif isinstance(item, dict) and (
                                    "firstName" in item or "lastName" in item
                                ):
                                    # Person reference in multi-select
                                    name = _extract_person_display_name(item)
                                    if name:
                                        extracted.append(name)
                                elif isinstance(item, dict) and "name" in item:
                                    # Company reference in multi-select
                                    extracted.append(item["name"])
                                else:
                                    extracted.append(item)
                            normalized_fields[field_name] = extracted
                        # Handle person reference: {"firstName": "Jane", "lastName": "Doe"}
                        elif isinstance(data, dict) and ("firstName" in data or "lastName" in data):
                            normalized_fields[field_name] = _extract_person_display_name(data)
                        # Handle company reference: {"name": "Acme", "domain": "acme.com"}
                        elif isinstance(data, dict) and "name" in data:
                            normalized_fields[field_name] = data["name"]
                        else:
                            normalized_fields[field_name] = data
                    else:
                        normalized_fields[field_name] = value_wrapper

    # Replace the complex fields structure with a simple dict keyed by name
    if normalized_fields:
        record["fields"] = normalized_fields

    # Add top-level convenience aliases for common entity properties
    # These allow LLMs to use intuitive field names like "entityName" instead of "entity.name"
    record["listEntryId"] = record.get("id")
    if entity and isinstance(entity, dict):
        record["entityId"] = entity.get("id")
        # Person entities have firstName/lastName instead of name
        if "firstName" in entity or "lastName" in entity:
            record["entityName"] = _extract_person_display_name(entity)
        else:
            record["entityName"] = entity.get("name")
    # Copy V2 "type" to "entityType" for consistency, but don't overwrite V1 "entityType"
    if "type" in record:
        record["entityType"] = record["type"]

    # Ensure fields key always exists for predictable output
    if "fields" not in record:
        record["fields"] = {}

    return record


def _apply_select_projection(
    records: list[dict[str, Any]], select: list[str]
) -> list[dict[str, Any]]:
    """Apply select clause projection to records.

    Filters each record to only include fields specified in select.
    Supports:
    - Simple fields: "id", "firstName"
    - Nested paths: "fields.Status", "address.city"
    - Wildcard for fields: "fields.*" (includes all custom fields)

    Args:
        records: List of record dicts to project
        select: List of field paths to include

    Returns:
        New list of projected records
    """
    if not select:
        return records

    # Check for fields.* wildcard - means include all fields
    include_all_fields = "fields.*" in select
    # Filter out the wildcard from paths to process
    paths = [p for p in select if p != "fields.*"]

    projected: list[dict[str, Any]] = []
    for record in records:
        new_record: dict[str, Any] = {}

        # Apply explicit paths - always include value even if None
        # This ensures explicitly selected fields appear in output
        for path in paths:
            value = resolve_field_path(record, path)
            _set_nested_value(new_record, path, value)

        # Handle fields.* wildcard - copy entire fields dict
        if include_all_fields and "fields" in record:
            new_record["fields"] = record["fields"]

        projected.append(new_record)

    return projected


# =============================================================================
# Progress Callback Protocol
# =============================================================================


class QueryProgressCallback(Protocol):
    """Protocol for query execution progress callbacks."""

    def on_step_start(self, step: PlanStep) -> None:
        """Called when a step starts."""
        ...

    def on_step_progress(self, step: PlanStep, current: int, total: int | None) -> None:
        """Called during step execution with progress update."""
        ...

    def on_step_complete(self, step: PlanStep, records: int) -> None:
        """Called when a step completes."""
        ...

    def on_step_error(self, step: PlanStep, error: Exception) -> None:
        """Called when a step fails."""
        ...


class NullProgressCallback:
    """No-op progress callback."""

    def on_step_start(self, step: PlanStep) -> None:
        pass

    def on_step_progress(self, step: PlanStep, current: int, total: int | None) -> None:
        pass

    def on_step_complete(self, step: PlanStep, records: int) -> None:
        pass

    def on_step_error(self, step: PlanStep, error: Exception) -> None:
        pass


# =============================================================================
# Execution Context
# =============================================================================


@dataclass
class ExecutionContext:
    """Tracks state during query execution."""

    query: Query
    records: list[dict[str, Any]] = field(default_factory=list)
    included: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Per-parent mapping: {rel_name: {parent_id: [related_records]}}
    # Enables inline expansion in table output (correlating included data to parent records)
    included_by_parent: dict[str, dict[int, list[dict[str, Any]]]] = field(default_factory=dict)
    relationship_counts: dict[str, dict[int, int]] = field(default_factory=dict)
    # Structure: {rel_name: {record_id: [related_records]}} - for quantifier/exists filtering
    relationship_data: dict[str, dict[int, list[dict[str, Any]]]] = field(default_factory=dict)
    current_step: int = 0
    start_time: float = field(default_factory=time.time)
    max_records: int = 10000
    interrupted: bool = False
    resolved_where: dict[str, Any] | None = None  # Where clause with resolved names
    warnings: list[str] = field(default_factory=list)  # Warnings collected during execution
    needs_full_fetch: bool = False  # True if filter/aggregate/sort exists (need all records first)
    early_terminated: bool = False  # True if stopped early due to limit (streaming mode)
    last_api_cursor: str | None = None  # API cursor at pagination stop (for streaming resumption)

    def check_timeout(self, timeout: float) -> None:
        """Check if execution has exceeded timeout."""
        elapsed = time.time() - self.start_time
        if elapsed > timeout:
            raise QueryTimeoutError(
                f"Query execution exceeded timeout of {timeout}s",
                timeout_seconds=timeout,
                elapsed_seconds=elapsed,
                partial_results=self.records,
            )

    def check_max_records(self) -> None:
        """Check if max records limit has been reached."""
        if len(self.records) >= self.max_records:
            raise QuerySafetyLimitError(
                f"Query would exceed maximum of {self.max_records} records",
                limit_name="max_records",
                limit_value=self.max_records,
                estimated_value=len(self.records),
            )

    def build_result(self) -> QueryResult:
        """Build final query result.

        Applies select clause projection if specified in the query.
        Expansions are automatically included in the effective select list.
        """
        from ..results import ResultSummary

        # Apply select projection if specified
        data = self.records
        if self.query.select:
            # Auto-include expansions in select - if user requested expand,
            # they clearly want that data in output
            effective_select = list(self.query.select)
            if self.query.expand:
                for exp in self.query.expand:
                    if exp not in effective_select:
                        effective_select.append(exp)
            data = _apply_select_projection(self.records, effective_select)

        # Build included counts for summary
        included_counts: dict[str, int] | None = None
        if self.included:
            included_counts = {k: len(v) for k, v in self.included.items() if v}
            if not included_counts:
                included_counts = None

        meta: dict[str, Any] = {
            "executionTime": time.time() - self.start_time,
            "interrupted": self.interrupted,
        }
        if self.early_terminated:
            meta["earlyTerminated"] = True

        return QueryResult(
            data=data,
            included=self.included,
            included_by_parent=self.included_by_parent,
            include_configs=self.query.include or {},
            source_entity=self.query.from_,
            summary=ResultSummary(
                total_rows=len(data),
                included_counts=included_counts,
            ),
            meta=meta,
            warnings=self.warnings,
            explicit_select=self.query.select,
            explicit_expand=self.query.expand,
            api_cursor=self.last_api_cursor,
        )


# =============================================================================
# Pre-Include Helper Functions
# =============================================================================


def _needs_full_records(where: WhereClause | None) -> bool:
    """Check if where clause references fields beyond 'id'.

    If so, we need to fetch full records, not just IDs.

    Args:
        where: The WHERE clause to check

    Returns:
        True if full records are needed (references fields other than 'id')
    """
    if where is None:
        return False

    # Check if path references a field other than id
    if where.path and where.path != "id" and not where.path.startswith("id."):
        return True

    # Recurse into compound clauses
    if where.and_:
        return any(_needs_full_records(c) for c in where.and_)
    if where.or_:
        return any(_needs_full_records(c) for c in where.or_)
    if where.not_:
        return _needs_full_records(where.not_)

    return False


def _any_quantifier_needs_full_records(
    where: WhereClause | None,
    rel_name: str,
    schema: Any,
) -> bool:
    """Check if ANY quantifier for rel_name needs fields beyond 'id'.

    CRITICAL: Unlike a simple first-match approach, this function checks ALL
    quantifiers for the relationship. This handles queries with multiple quantifiers
    on the same relationship:

    Example that would break with first-match approach::

        {
            "and_": [
                {"all_": {"path": "companies", "where": {...}}},
                {"none_": {"path": "companies", "where": {...}}}
            ]
        }

    The first quantifier only needs "id", but the second needs "name" - we must
    check ALL and upgrade if ANY needs full records.

    Args:
        where: The WHERE clause to search
        rel_name: The relationship name to check
        schema: Entity schema (needed to map exists_ entity types to rel names)

    Returns:
        True if any quantifier for this relationship needs full records
    """
    if where is None:
        return False

    # Check ALL quantifiers for this relationship (don't return early on first match!)
    if where.all_ and where.all_.path == rel_name and _needs_full_records(where.all_.where):
        return True
    if where.none_ and where.none_.path == rel_name and _needs_full_records(where.none_.where):
        return True

    # exists_ uses entity type - map to relationship name
    if where.exists_:
        exists_rel_name = find_relationship_by_target(schema, where.exists_.from_)
        if exists_rel_name == rel_name and _needs_full_records(where.exists_.where):
            return True

    # Recurse into ALL branches (don't return early!)
    if where.and_ and any(
        _any_quantifier_needs_full_records(c, rel_name, schema) for c in where.and_
    ):
        return True
    if where.or_ and any(
        _any_quantifier_needs_full_records(c, rel_name, schema) for c in where.or_
    ):
        return True

    return bool(where.not_ and _any_quantifier_needs_full_records(where.not_, rel_name, schema))


# =============================================================================
# Safety Guard Constants
# =============================================================================

# Safe limit: 100 records x 1 call each = ~3s at 30 req/s rate limit
SAFE_MAX_RECORDS = 100
DEFAULT_MAX_RECORDS = 10000  # Default --max-records value

# Concurrency for N+1 operations (entity fetches with expansion)
# Tunable via XAFFINITY_QUERY_CONCURRENCY environment variable
# PERF: entity_concurrency_limit
DEFAULT_CONCURRENCY = 15


# =============================================================================
# Rate-Aware Throttling
# =============================================================================


class RateLimitedExecutor:
    """Executor helper that respects rate limits via adaptive delays.

    Uses delay-based throttling rather than dynamic semaphore reduction.
    Tracks consecutive 429s for exponential backoff.
    """

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        """Initialize rate limiter.

        Args:
            concurrency: Maximum concurrent requests
        """
        self.semaphore = asyncio.Semaphore(concurrency)
        self._delay_until: float = 0  # Monotonic timestamp
        self._consecutive_429s: int = 0

    async def acquire(self) -> None:
        """Acquire permit with rate-limit-aware delay.

        Delays BEFORE acquiring semaphore to avoid holding permit during sleep.
        """
        now = time.monotonic()
        if now < self._delay_until:
            await asyncio.sleep(self._delay_until - now)

    async def __aenter__(self) -> RateLimitedExecutor:
        """Context manager entry - acquire semaphore."""
        await self.acquire()
        await self.semaphore.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Context manager exit - release semaphore."""
        self.semaphore.release()

    def on_response(self, status_code: int, remaining: int | None = None) -> None:
        """Adjust delays based on response.

        Args:
            status_code: HTTP status code
            remaining: X-RateLimit-Remaining header value if available
        """
        if status_code == 429:
            self._consecutive_429s += 1
            # Exponential backoff: 1s, 2s, 4s... capped at 30s
            # Cap exponent first to avoid computing large powers (Bug #26)
            capped_exponent = min(5, self._consecutive_429s)  # 2**5 = 32 > 30
            delay = min(30, 2**capped_exponent)
            self._delay_until = time.monotonic() + delay
            logger.debug("Rate limited, backing off for %ds", delay)
        else:
            self._consecutive_429s = 0

            # Proactive throttling based on remaining quota
            if remaining is not None and remaining < 10:
                # Getting low, add small delay
                self._delay_until = time.monotonic() + 0.5
                logger.debug("Low rate limit remaining (%d), adding 0.5s delay", remaining)


def validate_quantifier_query(
    query: Query,
    _max_records: int,
    max_records_explicit: bool,
) -> None:
    """Validate that unbounded quantifier queries have explicit limits.

    Args:
        query: The query being executed
        _max_records: Current max_records limit (unused, kept for API compatibility)
        max_records_explicit: True if --max-records was explicitly provided

    Raises:
        QueryValidationError: If unbounded quantifier query without explicit limit
    """
    # Derive has_quantifier from requires_relationship_data() - don't pass as parameter
    required_rels = requires_relationship_data(query.where) if query.where else set()
    if not required_rels:
        return  # No quantifier/relationship filter, no restriction

    if query.from_ not in UNBOUNDED_ENTITIES:
        return  # Bounded entity (listEntries), OK

    if not max_records_explicit:
        # User didn't explicitly set --max-records
        raise QueryValidationError(
            f"Quantifier filters on '{query.from_}' require explicit --max-records.\n"
            f"Your database may have tens of thousands of {query.from_}.\n\n"
            f"Options:\n"
            f"  1. Add --max-records {SAFE_MAX_RECORDS} to limit scope\n"
            f"  2. Query listEntries instead (bounded by list size)\n"
            f"  3. Add --max-records {DEFAULT_MAX_RECORDS} to explicitly allow large scope"
        )


def can_use_streaming(query: Query, *, max_records_explicit: bool = False) -> bool:
    """Check if streaming mode with early termination is applicable.

    Streaming mode processes pages one at a time and stops early when
    the limit is reached. This is only valid when we don't need to see
    all records before returning results.

    Args:
        query: The query to check
        max_records_explicit: Whether --max-records was explicitly provided by user

    Returns:
        True if streaming can be used, False otherwise
    """
    # Sort/aggregate/groupBy need all records - can't stream
    if query.order_by is not None or query.aggregate is not None or query.group_by is not None:
        return False

    # Has explicit limit in query - can stream
    if query.limit is not None:
        return True

    # User explicitly set --max-records - can use that as effective limit for streaming
    return bool(max_records_explicit)


# =============================================================================
# Query Executor
# =============================================================================


class QueryExecutor:
    """Executes query plans using SDK services.

    This class orchestrates SDK service calls to execute structured queries.
    It is CLI-specific and NOT part of the public SDK API.
    """

    def __init__(
        self,
        client: AsyncAffinity,
        *,
        progress: QueryProgressCallback | None = None,
        concurrency: int | None = None,
        max_records: int = 10000,
        max_records_explicit: bool = False,
        timeout: float = 300.0,
        allow_partial: bool = False,
        rate_limiter: RateLimitedExecutor | None = None,
        resume_api_cursor: str | None = None,
    ) -> None:
        """Initialize the executor.

        Args:
            client: AsyncAffinity client for API calls
            progress: Optional progress callback
            concurrency: Max concurrent API calls for N+1 operations.
                         Defaults to XAFFINITY_QUERY_CONCURRENCY env var or DEFAULT_CONCURRENCY.
            max_records: Safety limit on total records
            max_records_explicit: True if --max-records was explicitly provided
            timeout: Total execution timeout in seconds
            allow_partial: If True, return partial results on interruption
            rate_limiter: Optional rate limiter for adaptive throttling.
                          If provided, should be wired to client's on_response for feedback.
            resume_api_cursor: API cursor for O(1) streaming resumption. When provided,
                               pagination starts from this cursor instead of from the beginning,
                               avoiding re-fetching records that were already returned.
        """
        self.client = client
        self.progress = progress or NullProgressCallback()
        # Use explicit value, env var, or default
        if concurrency is None:
            concurrency = int(os.environ.get("XAFFINITY_QUERY_CONCURRENCY", DEFAULT_CONCURRENCY))
        self.concurrency = concurrency
        self.max_records = max_records
        self.max_records_explicit = max_records_explicit
        self.timeout = timeout
        self.allow_partial = allow_partial
        self.rate_limiter = rate_limiter or RateLimitedExecutor(self.concurrency)
        self.resume_api_cursor = resume_api_cursor

    async def execute(self, plan: ExecutionPlan) -> QueryResult:
        """Execute a query plan.

        Args:
            plan: The execution plan to run

        Returns:
            QueryResult with data and included records

        Raises:
            QueryExecutionError: If execution fails
            QueryInterruptedError: If interrupted (Ctrl+C)
            QueryTimeoutError: If timeout exceeded
            QuerySafetyLimitError: If max_records exceeded
            QueryValidationError: If unbounded quantifier query without explicit --max-records
        """
        # Safety guard: Block unbounded quantifier queries without explicit --max-records
        validate_quantifier_query(
            plan.query,
            self.max_records,
            self.max_records_explicit,
        )

        # Check if plan has steps that need ALL records before limiting (filter/aggregate/sort)
        # - Filter: must see all records to find matches
        # - Aggregate: must see all records for accurate counts/sums
        # - Sort: must see all records to find actual top N (not random N sorted)
        needs_full_fetch = any(
            step.operation in ("filter", "aggregate", "sort") for step in plan.steps
        )

        ctx = ExecutionContext(
            query=plan.query,
            max_records=self.max_records,
            needs_full_fetch=needs_full_fetch,
        )

        try:
            # Verify auth before starting
            await self._verify_auth()

            # Check for single-ID lookup optimization
            # This is much faster than streaming through all pages
            single_id_result = await self._try_single_id_lookup(plan, ctx)
            if single_id_result is not None:
                return single_id_result

            # Check if streaming mode is applicable for early termination
            # Streaming works when: has limit OR explicit max_records, no sort/aggregate/groupBy
            if can_use_streaming(plan.query, max_records_explicit=self.max_records_explicit):
                schema = SCHEMA_REGISTRY.get(plan.query.from_)
                # Only use streaming for GLOBAL entities (persons, companies, opportunities)
                if schema and schema.fetch_strategy == FetchStrategy.GLOBAL:
                    await self._execute_streaming(plan, ctx)
                    # Handle includes and expands after streaming (if any)
                    for step in plan.steps:
                        if step.operation == "include":
                            await self._execute_include(step, ctx)
                        elif step.operation == "expand":
                            await self._execute_expand(step, ctx)
                    return ctx.build_result()

            # Execute steps in dependency order (normal path)
            for step in plan.steps:
                ctx.current_step = step.step_id
                ctx.check_timeout(self.timeout)

                self.progress.on_step_start(step)

                try:
                    await self._execute_step(step, ctx)
                    self.progress.on_step_complete(step, len(ctx.records))
                except Exception as e:
                    self.progress.on_step_error(step, e)
                    raise

            return ctx.build_result()

        except KeyboardInterrupt:
            ctx.interrupted = True
            if self.allow_partial and ctx.records:
                return ctx.build_result()
            raise QueryInterruptedError(
                f"Query interrupted at step {ctx.current_step}. "
                f"{len(ctx.records)} records fetched before interruption.",
                step_id=ctx.current_step,
                records_fetched=len(ctx.records),
                partial_results=ctx.records,
            ) from None

    async def _verify_auth(self) -> None:
        """Verify client is authenticated."""
        try:
            await self.client.whoami()
        except Exception as e:
            raise QueryExecutionError(
                "Authentication failed. Check your API key before running queries.",
                cause=e,
            ) from None

    async def _try_single_id_lookup(
        self, plan: ExecutionPlan, ctx: ExecutionContext
    ) -> QueryResult | None:
        """Attempt single-ID lookup optimization.

        If the query is a simple `where: {path: "id", op: "eq", value: X}`,
        use service.get(id) directly instead of streaming through all pages.

        Also supports REQUIRES_PARENT entities like listEntries when the query
        has both parent ID and entity ID as equality conditions.

        Args:
            plan: The execution plan
            ctx: The execution context

        Returns:
            QueryResult if optimization applied, None to continue with normal execution.
        """
        from .filters import extract_single_id_lookup

        query = plan.query
        schema = SCHEMA_REGISTRY.get(query.from_)
        if schema is None:
            return None

        # Try REQUIRES_PARENT optimization (e.g., listEntries)
        if schema.fetch_strategy == FetchStrategy.REQUIRES_PARENT:
            return await self._try_parent_entity_id_lookup(plan, ctx, schema)

        # Try GLOBAL entity optimization
        if schema.fetch_strategy != FetchStrategy.GLOBAL:
            return None

        single_id = extract_single_id_lookup(query.where)
        if single_id is None:
            return None

        # Only applies to entities with direct get() support
        if query.from_ not in ("persons", "companies", "opportunities"):
            return None

        logger.debug("Using single-ID lookup optimization for %s id=%s", query.from_, single_id)

        # Create progress step
        fetch_step = PlanStep(
            step_id=0,
            operation="fetch",
            description=f"Fetch {query.from_} by ID (direct lookup)",
            entity=query.from_,
            estimated_api_calls=1,
        )
        self.progress.on_step_start(fetch_step)

        try:
            service = getattr(self.client, schema.service_attr)
            # Use the appropriate typed ID based on entity
            if query.from_ == "persons":
                from affinity.types import PersonId

                record = await service.get(PersonId(single_id))
            elif query.from_ == "companies":
                from affinity.types import CompanyId

                record = await service.get(CompanyId(single_id))
            elif query.from_ == "opportunities":
                from affinity.types import OpportunityId

                record = await service.get(OpportunityId(single_id))
            else:
                return None  # Shouldn't reach here

            # Convert to dict
            ctx.records = [record.model_dump(mode="json", by_alias=True)]
            self.progress.on_step_complete(fetch_step, 1)

            # Handle includes and expands if present
            for step in plan.steps:
                if step.operation == "include":
                    await self._execute_include(step, ctx)
                elif step.operation == "expand":
                    await self._execute_expand(step, ctx)

            return ctx.build_result()

        except NotFoundError:
            # Record doesn't exist - return empty result
            ctx.records = []
            self.progress.on_step_complete(fetch_step, 0)
            return ctx.build_result()
        except Exception as e:
            # Other error - fall through to normal execution
            logger.debug("Single-ID lookup failed, falling back to streaming: %s", e)
            return None

    async def _try_parent_entity_id_lookup(
        self, plan: ExecutionPlan, ctx: ExecutionContext, schema: EntitySchema
    ) -> QueryResult | None:
        """Attempt single-ID lookup for REQUIRES_PARENT entities.

        For entities like listEntries that require a parent ID, this detects
        patterns like:
            {"and": [
                {"path": "listId", "op": "eq", "value": 123},
                {"path": "id", "op": "eq", "value": 456}
            ]}

        And uses service.get(entry_id) directly instead of streaming.

        Args:
            plan: The execution plan
            ctx: The execution context
            schema: The entity schema

        Returns:
            QueryResult if optimization applied, None to continue with normal execution.
        """
        from .filters import extract_parent_and_id_lookup

        query = plan.query

        # Check if this entity supports direct get()
        # Currently only listEntries is supported
        if query.from_ != "listEntries":
            return None

        # Extract parent ID and entity ID from the filter
        parent_field = schema.parent_filter_field
        parent_method = schema.parent_method_name
        if parent_field is None or parent_method is None:
            return None

        ids = extract_parent_and_id_lookup(query.where, parent_field)
        if ids is None:
            return None

        parent_id, entity_id = ids
        logger.debug(
            "Using single-ID lookup optimization for %s %s=%s id=%s",
            query.from_,
            parent_field,
            parent_id,
            entity_id,
        )

        # Create progress step
        fetch_step = PlanStep(
            step_id=0,
            operation="fetch",
            description=f"Fetch {query.from_} by ID (direct lookup)",
            entity=query.from_,
            estimated_api_calls=1,
        )
        self.progress.on_step_start(fetch_step)

        try:
            # Get the parent service and then the child service
            parent_service = getattr(self.client, schema.service_attr)
            # e.g., client.lists.entries(list_id)
            child_service = getattr(parent_service, parent_method)(parent_id)
            # Call get() on the child service
            from affinity.types import ListEntryId

            record = await child_service.get(ListEntryId(entity_id))

            # Convert to dict
            ctx.records = [record.model_dump(mode="json", by_alias=True)]
            self.progress.on_step_complete(fetch_step, 1)

            # Handle includes and expands if present
            for step in plan.steps:
                if step.operation == "include":
                    await self._execute_include(step, ctx)
                elif step.operation == "expand":
                    await self._execute_expand(step, ctx)

            return ctx.build_result()

        except NotFoundError:
            # Record doesn't exist - return empty result
            ctx.records = []
            self.progress.on_step_complete(fetch_step, 0)
            return ctx.build_result()
        except Exception as e:
            # Other error - fall through to normal execution
            logger.debug("Single-ID lookup failed for %s, falling back: %s", query.from_, e)
            return None

    async def _execute_step(self, step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute a single plan step."""
        if step.operation in ("fetch", "fetch_streaming"):
            await self._execute_fetch(step, ctx)
        elif step.operation == "filter":
            await self._execute_filter_with_preinclude(step, ctx)
        elif step.operation == "include":
            await self._execute_include(step, ctx)
        elif step.operation == "expand":
            await self._execute_expand(step, ctx)
        elif step.operation == "aggregate":
            self._execute_aggregate(step, ctx)
        elif step.operation == "sort":
            self._execute_sort(step, ctx)
        elif step.operation == "limit":
            self._execute_limit(step, ctx)

    async def _execute_fetch(self, step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute a fetch step.

        Routes to appropriate fetch strategy based on schema configuration.
        """
        if step.entity is None:
            raise QueryExecutionError("Fetch step missing entity", step=step)

        schema = SCHEMA_REGISTRY.get(step.entity)
        if schema is None:
            raise QueryExecutionError(f"Unknown entity: {step.entity}", step=step)

        try:
            match schema.fetch_strategy:
                case FetchStrategy.GLOBAL:
                    await self._fetch_global(step, ctx, schema)
                case FetchStrategy.REQUIRES_PARENT:
                    await self._fetch_with_parent(step, ctx, schema)
                case FetchStrategy.RELATIONSHIP_ONLY:
                    # Should never reach here - parser rejects these
                    raise QueryExecutionError(
                        f"'{step.entity}' cannot be queried directly. "
                        "This should have been caught at parse time.",
                        step=step,
                    )
        except QueryExecutionError:
            raise
        except Exception as e:
            raise QueryExecutionError(
                f"Failed to fetch {step.entity}: {e}",
                step=step,
                cause=e,
                partial_results=ctx.records,
            ) from None

    async def _fetch_global(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        schema: Any,
    ) -> None:
        """Fetch entities that support global iteration (service.all()).

        Supports O(1) resumption via resume_api_cursor - when set, pagination
        starts from the cursor instead of from the beginning.
        """
        service = getattr(self.client, schema.service_attr)

        def on_progress(p: PaginationProgress) -> None:
            self.progress.on_step_progress(step, p.items_so_far, None)

        # O(1) streaming resumption: start from stored API cursor instead of beginning
        if self.resume_api_cursor:
            # Resume from cursor - no need to re-fetch earlier pages
            current_cursor: str | None = self.resume_api_cursor
            items_so_far = 0

            while current_cursor:
                # Fetch page starting from cursor
                page = await service.list(cursor=current_cursor)
                for record in page.data:
                    record_dict = record.model_dump(mode="json", by_alias=True)
                    ctx.records.append(record_dict)
                    items_so_far += 1

                    if self._should_stop(ctx):
                        # Capture API cursor for potential next resumption
                        ctx.last_api_cursor = page.next_cursor
                        return

                # Progress update after each page
                self.progress.on_step_progress(step, items_so_far, None)

                # Move to next page
                current_cursor = page.next_cursor
            return

        # Standard path: iterate from beginning
        async for page in service.all().pages(on_progress=on_progress):
            for record in page.data:
                record_dict = record.model_dump(mode="json", by_alias=True)
                ctx.records.append(record_dict)

                if self._should_stop(ctx):
                    # Capture API cursor for potential streaming resumption
                    ctx.last_api_cursor = page.next_cursor
                    return

    async def _fetch_with_parent(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        schema: Any,
    ) -> None:
        """Fetch entities that require a parent ID filter.

        Uses schema configuration to determine:
        - Which field to extract from the where clause (parent_filter_field)
        - What type to cast the ID to (parent_id_type)
        - Which method to call on the parent service (parent_method_name)

        Supports OR/IN conditions by extracting ALL parent IDs and fetching from each
        in parallel, merging results.
        """
        # Resolve name-based lookups BEFORE extracting parent IDs
        where = ctx.query.where
        if where is not None:
            # Convert WhereClause to dict for resolution
            where_as_dict: dict[str, Any] = (
                where.model_dump(mode="json", by_alias=True)
                if hasattr(where, "model_dump")
                else where  # type: ignore[assignment]
            )
            where_dict = await self._resolve_list_names_to_ids(where_as_dict)
        else:
            where_dict = None

        # Extract ALL parent IDs from where clause (supports OR/IN conditions)
        parent_ids = self._extract_parent_ids(where_dict, schema.parent_filter_field)

        # Store resolved where for use in filtering step
        # NOTE: We store BEFORE field name→ID resolution because:
        # - The normalized records have fields keyed by NAME (e.g., "Status")
        # - Field ID resolution is only for the API call, not client-side filtering
        if where_dict is not None:
            ctx.resolved_where = where_dict

        # Resolve field names to IDs for listEntries queries (after we know parent IDs)
        # This is only used for the API call, NOT for client-side filtering
        if where_dict is not None and parent_ids:
            where_dict = await self._resolve_field_names_to_ids(where_dict, parent_ids, ctx)
        if not parent_ids:
            # Should never happen - parser validates this
            raise QueryExecutionError(
                f"Query for '{step.entity}' requires a '{schema.parent_filter_field}' filter.",
                step=step,
            )

        # Get the parent service (e.g., client.lists)
        parent_service = getattr(self.client, schema.service_attr)

        # Cast all IDs to typed IDs if configured
        if schema.parent_id_type:
            from affinity import types as affinity_types

            id_type = getattr(affinity_types, schema.parent_id_type)
            parent_ids = [id_type(pid) for pid in parent_ids]

        nested_method = getattr(parent_service, schema.parent_method_name)

        # Resolve field_ids for listEntries queries
        # This auto-detects which custom fields are referenced in the query
        field_ids: list[str] | None = None
        if step.entity == "listEntries" and parent_ids:
            # Use first parent_id to get field metadata (all lists in an OR should have same fields)
            # parent_ids are already typed IDs, extract the raw int
            raw_parent_id = (
                parent_ids[0].value if hasattr(parent_ids[0], "value") else int(parent_ids[0])
            )
            field_ids = await self._resolve_field_ids_for_list_entries(ctx, raw_parent_id)

        # For single parent ID, use simple sequential fetch
        if len(parent_ids) == 1:
            await self._fetch_from_single_parent(
                step, ctx, nested_method, parent_ids[0], field_ids=field_ids
            )
            return

        # For multiple parent IDs, fetch in parallel
        async def fetch_from_parent(parent_id: Any) -> list[dict[str, Any]]:
            """Fetch all records from a single parent."""
            nested_service = nested_method(parent_id)
            results: list[dict[str, Any]] = []

            # Try paginated iteration first
            if hasattr(nested_service.all(), "pages"):
                # Build pages() kwargs, including field_ids if provided
                pages_kwargs: dict[str, Any] = {}
                if field_ids is not None:
                    pages_kwargs["field_ids"] = field_ids

                async for page in nested_service.all().pages(**pages_kwargs):
                    for record in page.data:
                        record_dict = record.model_dump(mode="json", by_alias=True)
                        record_dict = _normalize_list_entry_fields(record_dict)
                        results.append(record_dict)
            else:
                async for record in nested_service.all():
                    record_dict = record.model_dump(mode="json", by_alias=True)
                    record_dict = _normalize_list_entry_fields(record_dict)
                    results.append(record_dict)

            return results

        # Execute all fetches in parallel
        all_results = await asyncio.gather(*[fetch_from_parent(pid) for pid in parent_ids])

        # Merge results, respecting limits
        for results in all_results:
            for record_dict in results:
                ctx.records.append(record_dict)
                if self._should_stop(ctx):
                    return

            # Report progress after each parent completes
            self.progress.on_step_progress(step, len(ctx.records), None)

    async def _fetch_from_single_parent(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        nested_method: Callable[..., Any],
        parent_id: Any,
        *,
        field_ids: list[str] | None = None,
    ) -> None:
        """Fetch from a single parent with progress reporting.

        Supports O(1) resumption via resume_api_cursor - when set, pagination
        starts from the cursor instead of from the beginning.

        Args:
            step: The plan step being executed
            ctx: Execution context
            nested_method: Method to call with parent_id to get nested service
            parent_id: The parent entity ID
            field_ids: Optional list of field IDs to request for listEntries
        """
        nested_service = nested_method(parent_id)
        items_fetched = 0

        def on_progress(p: PaginationProgress) -> None:
            nonlocal items_fetched
            items_fetched = p.items_so_far
            self.progress.on_step_progress(step, items_fetched, None)

        # O(1) streaming resumption: start from stored API cursor instead of beginning
        if (
            self.resume_api_cursor
            and hasattr(nested_service, "list")
            and callable(nested_service.list)
        ):
            # Resume from cursor - no need to re-fetch earlier pages
            current_cursor: str | None = self.resume_api_cursor

            while current_cursor:
                # Build list() kwargs
                list_kwargs: dict[str, Any] = {"cursor": current_cursor}
                if field_ids is not None:
                    list_kwargs["field_ids"] = field_ids

                # Fetch page starting from cursor
                page = await nested_service.list(**list_kwargs)
                for record in page.data:
                    record_dict = record.model_dump(mode="json", by_alias=True)
                    # Normalize list entry fields for query-friendly access
                    record_dict = _normalize_list_entry_fields(record_dict)
                    ctx.records.append(record_dict)
                    items_fetched += 1

                    if self._should_stop(ctx):
                        # Capture API cursor for potential next resumption
                        ctx.last_api_cursor = page.next_cursor
                        return

                # Progress update after each page
                self.progress.on_step_progress(step, items_fetched, None)

                # Move to next page
                current_cursor = page.next_cursor
            return

        # Check if service has a direct pages() method (e.g., AsyncListEntryService)
        # This is preferred over all().pages() because it supports field_ids
        if hasattr(nested_service, "pages") and callable(nested_service.pages):
            # Build pages() kwargs
            pages_kwargs: dict[str, Any] = {}
            if field_ids is not None:
                pages_kwargs["field_ids"] = field_ids

            async for page in nested_service.pages(**pages_kwargs):
                for record in page.data:
                    record_dict = record.model_dump(mode="json", by_alias=True)
                    # Normalize list entry fields for query-friendly access
                    record_dict = _normalize_list_entry_fields(record_dict)
                    ctx.records.append(record_dict)
                    items_fetched += 1
                    if self._should_stop(ctx):
                        # Capture API cursor for potential streaming resumption
                        ctx.last_api_cursor = page.next_cursor
                        return
                # Report progress after each page
                self.progress.on_step_progress(step, items_fetched, None)

        # Try all().pages() for services that return PageIterator from all()
        elif hasattr(nested_service.all(), "pages"):
            pages_kwargs = {"on_progress": on_progress}
            if field_ids is not None:
                pages_kwargs["field_ids"] = field_ids

            async for page in nested_service.all().pages(**pages_kwargs):
                for record in page.data:
                    record_dict = record.model_dump(mode="json", by_alias=True)
                    # Normalize list entry fields for query-friendly access
                    record_dict = _normalize_list_entry_fields(record_dict)
                    ctx.records.append(record_dict)
                    if self._should_stop(ctx):
                        # Capture API cursor for potential streaming resumption
                        ctx.last_api_cursor = page.next_cursor
                        return
        else:
            # Fall back to async iteration for services without pages()
            all_kwargs: dict[str, Any] = {}
            if field_ids is not None:
                all_kwargs["field_ids"] = field_ids

            async for record in nested_service.all(**all_kwargs):
                record_dict = record.model_dump(mode="json", by_alias=True)
                # Normalize list entry fields for query-friendly access
                record_dict = _normalize_list_entry_fields(record_dict)
                ctx.records.append(record_dict)
                items_fetched += 1

                if items_fetched % 100 == 0:
                    self.progress.on_step_progress(step, items_fetched, None)

                if self._should_stop(ctx):
                    return

    def _should_stop(self, ctx: ExecutionContext) -> bool:
        """Check if we should stop fetching.

        The limit and max_records are only applied during fetch when there's
        NO operation that needs all records first (filter, aggregate, sort).

        - Filter: Must see all records to find matches - can't stop at 10
          records when matches might be at position 100+
        - Aggregate: Must see all records for accurate counts/sums - stopping
          early gives wrong results
        - Sort: Must see all records to find actual top N - stopping early
          gives random N sorted, not actual top N

        Note: When needs_full_fetch is True, we rely on the underlying
        entity's total count and per-list limits rather than max_records.
        After filter/aggregate/sort, the final results are truncated.
        """
        # Only apply limits during fetch if no operation needs all records first
        if ctx.needs_full_fetch:
            return False
        # Stop at max_records safety limit
        if len(ctx.records) >= ctx.max_records:
            return True
        # Stop at query limit
        return bool(ctx.query.limit and len(ctx.records) >= ctx.query.limit)

    def _extract_parent_ids(self, where: Any, field_name: str | None) -> list[int]:
        """Extract ALL parent ID values from where clause.

        Handles all condition types:
        - Direct eq: {"path": "listId", "op": "eq", "value": 12345}
        - Direct eq (string): {"path": "listId", "op": "eq", "value": "12345"}
        - Direct in: {"path": "listId", "op": "in", "value": [123, 456, 789]}
        - AND: {"and": [{"path": "listId", "op": "eq", "value": 123}, ...]}
        - OR: {"or": [{"path": "listId", "op": "eq", "value": 123},
                      {"path": "listId", "op": "eq", "value": 456}]}

        Accepts both integer and string IDs (strings are converted to int).
        Returns deduplicated list of all parent IDs found.
        """
        if where is None or field_name is None:
            return []

        if hasattr(where, "model_dump"):
            where = where.model_dump(mode="json", by_alias=True)

        if not isinstance(where, dict):
            return []

        def to_int(value: Any) -> int | None:
            """Convert value to int, supporting both int and numeric strings."""
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                try:
                    return int(value)
                except ValueError:
                    return None
            return None

        ids: list[int] = []

        # Direct condition with "eq" operator
        if where.get("path") == field_name and where.get("op") == "eq":
            value = where.get("value")
            int_val = to_int(value)
            if int_val is not None:
                ids.append(int_val)

        # Direct condition with "in" operator (list of IDs)
        if where.get("path") == field_name and where.get("op") == "in":
            value = where.get("value")
            if isinstance(value, list):
                for v in value:
                    int_val = to_int(v)
                    if int_val is not None:
                        ids.append(int_val)

        # Compound "and" conditions - traverse recursively
        if where.get("and"):
            for condition in where["and"]:
                ids.extend(self._extract_parent_ids(condition, field_name))

        # Compound "or" conditions - traverse recursively
        if where.get("or"):
            for condition in where["or"]:
                ids.extend(self._extract_parent_ids(condition, field_name))

        # NOTE: "not" clauses are intentionally NOT traversed.
        # Negated parent filters are rejected by the parser.

        # Deduplicate while preserving order
        seen: set[int] = set()
        unique_ids: list[int] = []
        for id_ in ids:
            if id_ not in seen:
                seen.add(id_)
                unique_ids.append(id_)

        return unique_ids

    def _collect_field_refs_from_query(self, query: Query) -> set[str]:
        """Collect all fields.* references from the query.

        Scans select, groupBy, aggregate, and where clauses for fields.* paths
        and returns the set of field names (without the "fields." prefix).

        Supports the "fields.*" wildcard which indicates all fields are needed.

        Returns:
            Set of field names referenced, or {"*"} if all fields are needed.
        """
        field_names: set[str] = set()

        # Check for fields.* wildcard in select
        if query.select:
            for path in query.select:
                if path == "fields.*":
                    return {"*"}  # Wildcard means all fields
                if path.startswith("fields."):
                    field_names.add(path[7:])  # Remove "fields." prefix

        # Collect from groupBy
        if query.group_by:
            if query.group_by == "fields.*":
                return {"*"}
            if query.group_by.startswith("fields."):
                field_names.add(query.group_by[7:])

        # Collect from aggregates
        if query.aggregate:
            for agg in query.aggregate.values():
                for attr in ["sum", "avg", "min", "max", "first", "last"]:
                    field = getattr(agg, attr, None)
                    if field and isinstance(field, str):
                        if field == "fields.*":
                            return {"*"}
                        if field.startswith("fields."):
                            field_names.add(field[7:])
                # Handle percentile which has nested structure
                if agg.percentile and isinstance(agg.percentile, dict):
                    pct_field = agg.percentile.get("field", "")
                    if pct_field == "fields.*":
                        return {"*"}
                    if pct_field.startswith("fields."):
                        field_names.add(pct_field[7:])

        # Collect from where clause (recursive)
        if query.where:
            where_dict = (
                query.where.model_dump(mode="json", by_alias=True)
                if hasattr(query.where, "model_dump")
                else query.where
            )
            self._collect_field_refs_from_where(where_dict, field_names)
            if "*" in field_names:
                return {"*"}

        return field_names

    def _collect_field_refs_from_where(
        self, where: dict[str, Any] | Any, field_names: set[str]
    ) -> None:
        """Recursively collect fields.* references from a where clause.

        Args:
            where: The where clause dict or sub-clause
            field_names: Set to add field names to (modified in place)
        """
        if not isinstance(where, dict):
            return

        # Check if this is a direct condition with fields.* path
        path = where.get("path", "")
        if isinstance(path, str):
            if path == "fields.*":
                field_names.add("*")
                return
            if path.startswith("fields."):
                field_names.add(path[7:])

        # Recurse into compound conditions
        for key in ["and", "or", "and_", "or_"]:
            if key in where and isinstance(where[key], list):
                for sub_clause in where[key]:
                    self._collect_field_refs_from_where(sub_clause, field_names)

        # Recurse into not clause
        for key in ["not", "not_"]:
            if key in where:
                self._collect_field_refs_from_where(where[key], field_names)

    async def _resolve_field_ids_for_list_entries(
        self,
        ctx: ExecutionContext,
        list_id: int,
    ) -> list[str] | None:
        """Resolve field names to IDs for listEntries queries.

        Automatically detects which custom fields are referenced in the query
        (in select, groupBy, aggregate, or where clauses) and requests them
        from the API.

        Args:
            ctx: Execution context containing the query
            list_id: The list ID to fetch field metadata from

        Returns:
            List of field IDs to request, or None if no custom fields needed.
            If wildcard (fields.*) is used, returns all field IDs for the list.
        """
        from affinity.types import ListId

        # Collect all field references from the query
        field_names = self._collect_field_refs_from_query(ctx.query)

        if not field_names:
            # No field references in query - don't request any custom fields
            # This avoids expensive API calls for lists with many fields
            return None

        # Ensure field name cache is populated for this list
        if not hasattr(self, "_field_name_cache"):
            self._field_name_cache: dict[str, dict[str, Any]] = {}

        # Check if we need to fetch field metadata
        cache_key = f"list_{list_id}"
        if cache_key not in self._field_name_cache:
            try:
                fields = await self.client.lists.get_fields(ListId(list_id))
                # Build a mapping of lowercase name -> field ID
                field_map: dict[str, str] = {}
                all_field_ids: list[str] = []
                for field in fields:
                    if field.name:
                        field_map[field.name.lower()] = str(field.id)
                    all_field_ids.append(str(field.id))
                self._field_name_cache[cache_key] = {
                    "by_name": field_map,
                    "all_ids": all_field_ids,
                }
            except Exception as exc:
                logger.warning("Failed to fetch field metadata for list %s: %s", list_id, exc)
                ctx.warnings.append(
                    f"Could not fetch field metadata (fields.* values will be null): {exc}"
                )
                return None

        cache = self._field_name_cache[cache_key]

        # Handle wildcard: return all field IDs
        if "*" in field_names:
            all_ids: list[str] = cache["all_ids"]
            return all_ids

        # Resolve specific field names to IDs
        field_ids: list[str] = []
        missing_fields: list[str] = []
        for name in field_names:
            field_id = cache["by_name"].get(name.lower())
            if field_id is not None:
                field_ids.append(field_id)
            else:
                missing_fields.append(name)

        # Add warning for missing fields (don't break query - typos shouldn't fail)
        if missing_fields:
            available_fields = sorted(cache["by_name"].keys())
            if len(missing_fields) == 1:
                ctx.warnings.append(
                    f"Field 'fields.{missing_fields[0]}' not found on list. "
                    f"Available fields: {', '.join(available_fields[:10])}"
                    + ("..." if len(available_fields) > 10 else "")
                )
            else:
                missing_str = ", ".join(f"fields.{f}" for f in missing_fields)
                available_str = ", ".join(available_fields[:10])
                suffix = "..." if len(available_fields) > 10 else ""
                ctx.warnings.append(
                    f"Fields not found on list: {missing_str}. "
                    f"Available fields: {available_str}{suffix}"
                )

        return field_ids if field_ids else None

    async def _resolve_list_names_to_ids(self, where: dict[str, Any]) -> dict[str, Any]:
        """Resolve listName references to listId.

        Transforms:
            {"path": "listName", "op": "eq", "value": "My Deals"}
        Into:
            {"path": "listId", "op": "eq", "value": 12345}

        Also handles:
            {"path": "listName", "op": "in", "value": ["Deals", "Leads"]}

        Cache behavior: The list name cache is populated once per QueryExecutor
        instance. Since QueryExecutor is created fresh for each execute() call,
        the cache is effectively per-query.
        """
        if not isinstance(where, dict):
            return where

        # Check if this is a listName condition
        if where.get("path") == "listName":
            names = where.get("value")
            op = where.get("op")

            # Fetch all lists once and cache
            if not hasattr(self, "_list_name_cache"):
                self._list_name_cache: dict[str, int] = {}
                async for list_obj in self.client.lists.all():
                    self._list_name_cache[list_obj.name] = list_obj.id

            if op == "eq" and isinstance(names, str):
                list_id = self._list_name_cache.get(names)
                if list_id is None:
                    raise QueryExecutionError(f"List not found: '{names}'")
                return {"path": "listId", "op": "eq", "value": list_id}

            if op == "in" and isinstance(names, list):
                list_ids = []
                for name in names:
                    list_id = self._list_name_cache.get(name)
                    if list_id is None:
                        raise QueryExecutionError(f"List not found: '{name}'")
                    list_ids.append(list_id)
                return {"path": "listId", "op": "in", "value": list_ids}

        # Recursively process compound conditions
        result = dict(where)
        if where.get("and"):
            result["and"] = [await self._resolve_list_names_to_ids(c) for c in where["and"]]
        if where.get("or"):
            result["or"] = [await self._resolve_list_names_to_ids(c) for c in where["or"]]

        return result

    async def _resolve_field_names_to_ids(
        self,
        where: dict[str, Any],
        list_ids: list[int],
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Resolve field name references to field IDs in fields.* paths.

        Transforms:
            {"path": "fields.Status", "op": "eq", "value": "Active"}
        Into:
            {"path": "fields.12345", "op": "eq", "value": "Active"}

        Field names are resolved case-insensitively against the field definitions
        for the specified list(s).

        Args:
            where: The where clause to transform
            list_ids: List IDs to fetch field metadata from

        Returns:
            Transformed where clause with field names resolved to IDs
        """
        if not isinstance(where, dict) or not list_ids:
            return where

        # Build flat field name -> ID cache for all lists
        if not hasattr(self, "_field_name_to_id_cache"):
            self._field_name_to_id_cache: dict[str, str] = {}

            from affinity.types import ListId

            for list_id in list_ids:
                try:
                    fields = await self.client.lists.get_fields(ListId(list_id))
                    for field in fields:
                        if field.name:
                            # Map lowercase name to field ID
                            self._field_name_to_id_cache[field.name.lower()] = str(field.id)
                except Exception as exc:
                    logger.warning("Failed to fetch field metadata for list %s: %s", list_id, exc)
                    ctx.warnings.append(
                        f"Could not fetch field metadata for where clause resolution: {exc}"
                    )

        # Check if this is a fields.* condition
        path = where.get("path", "")
        if isinstance(path, str) and path.startswith("fields."):
            field_ref = path[7:]  # Everything after "fields."

            # Skip if already a field ID (numeric or "field-" prefix)
            if not field_ref.isdigit() and not field_ref.startswith("field-"):
                # Try to resolve by name (case-insensitive)
                field_id = self._field_name_to_id_cache.get(field_ref.lower())
                if field_id is not None:
                    result = dict(where)
                    result["path"] = f"fields.{field_id}"
                    return result

        # Recursively process compound conditions
        result = dict(where)
        if where.get("and"):
            result["and"] = [
                await self._resolve_field_names_to_ids(c, list_ids, ctx) for c in where["and"]
            ]
        if where.get("or"):
            result["or"] = [
                await self._resolve_field_names_to_ids(c, list_ids, ctx) for c in where["or"]
            ]

        return result

    async def _execute_filter_with_preinclude(
        self,
        _step: PlanStep,
        ctx: ExecutionContext,
    ) -> None:
        """Execute WHERE clause filtering with lazy loading optimization.

        Two-phase execution:
        1. Apply cheap filters first (no API calls) to reduce dataset
        2. Pre-fetch relationship data only for survivors
        3. Apply expensive filters on reduced dataset

        ctx.query.where vs ctx.resolved_where:
        - ctx.query.where: Original WHERE clause with user-provided names (e.g., listName)
        - ctx.resolved_where: After name resolution (e.g., listId replaced)

        We use ctx.query.where for requires_relationship_data() because:
        - Relationship names match original field paths, not resolved IDs
        - Example: "companies" path stays "companies", doesn't get resolved to IDs
        """
        from .filters import partition_where

        # Get where clause (prefer resolved, fall back to original)
        where: WhereClause | None
        if ctx.resolved_where is not None:
            where = WhereClause.model_validate(ctx.resolved_where)
        else:
            where = ctx.query.where

        if where is None:
            return

        # Get schema from registry
        schema = SCHEMA_REGISTRY.get(ctx.query.from_)
        entity_type = ctx.query.from_

        # Partition filter into cheap and expensive parts
        cheap_filter, expensive_filter = partition_where(where, entity_type)

        # Phase 1: Apply cheap filter first (no API calls) to reduce dataset
        if cheap_filter is not None:
            before_count = len(ctx.records)
            cheap_fn = compile_filter(cheap_filter)
            ctx.records = [r for r in ctx.records if cheap_fn(r)]
            after_count = len(ctx.records)
            if before_count > after_count:
                logger.debug(
                    "Lazy loading: pre-filter reduced %d → %d records", before_count, after_count
                )

        # Phase 2: Pre-fetch relationship data ONLY for survivors
        if expensive_filter is not None and schema:
            # Check what relationships are needed for the expensive filter
            required_rels = requires_relationship_data(expensive_filter)

            if required_rels:
                # Pre-fetch relationship data BEFORE applying expensive filter
                await self._execute_pre_include(ctx, required_rels, schema)

                # Phase 3: Apply expensive filter with relationship context
                id_field = schema.id_field or "id"
                filter_ctx = FilterContext(
                    relationship_data=ctx.relationship_data,
                    relationship_counts=ctx.relationship_counts,
                    schema=schema,
                    id_field=id_field,
                )
                filter_fn = compile_filter_with_context(expensive_filter, filter_ctx)
                ctx.records = [r for r in ctx.records if filter_fn(r)]
            else:
                # Expensive filter but no relationship data needed (shouldn't happen)
                filter_fn = compile_filter(expensive_filter)
                ctx.records = [r for r in ctx.records if filter_fn(r)]

        # Apply max_records limit after filtering (not during fetch)
        # This ensures we find matching records even if they're beyond position max_records
        if len(ctx.records) > ctx.max_records:
            ctx.records = ctx.records[: ctx.max_records]

    def _execute_filter(self, _step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute a client-side filter step.

        Note: This method is kept for backwards compatibility and simple cases.
        The main entry point is now _execute_filter_with_preinclude().
        """
        from .models import WhereClause as WC

        # Use resolved where clause if available (has listName → listId resolved)
        where: WC | None
        if ctx.resolved_where is not None:
            # Convert dict back to WhereClause for compile_filter
            where = WC.model_validate(ctx.resolved_where)
        else:
            where = ctx.query.where
        if where is None:
            return

        filter_func = compile_filter(where)
        ctx.records = [r for r in ctx.records if filter_func(r)]

        # Apply max_records limit after filtering (not during fetch)
        # This ensures we find matching records even if they're beyond position max_records
        if len(ctx.records) > ctx.max_records:
            ctx.records = ctx.records[: ctx.max_records]

    async def _execute_include(self, step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute an include step with batch fetch optimization.

        For entity_method strategy:
        - Fetches relationship IDs in parallel (N calls - inherent API limitation)
        - Deduplicates and batch fetches full records using V2 batch lookup
        - Maps results back to parent records for inline expansion

        For global_service strategy:
        - Calls service.list() per parent (already returns full records)
        - Builds parent mapping for inline expansion
        """
        if step.relationship is None or step.entity is None:
            return

        rel = get_relationship(step.entity, step.relationship)
        if rel is None:
            raise QueryExecutionError(
                f"Unknown relationship: {step.entity}.{step.relationship}",
                step=step,
            )

        included_records: list[dict[str, Any]] = []
        parent_mapping: dict[int, list[dict[str, Any]]] = {}

        if rel.fetch_strategy == "entity_method":
            # Phase 1: Fetch IDs for all records IN PARALLEL (preserve existing parallelism)
            async def fetch_ids_for_record(
                record: dict[str, Any],
            ) -> tuple[int | None, list[int]]:
                entity_id = record.get("id")
                if entity_id is None:
                    return None, []

                service = getattr(self.client, step.entity or "")
                method = getattr(service, rel.method_or_service, None)
                if method is None:
                    return entity_id, []

                async with self.rate_limiter:
                    try:
                        ids = await method(entity_id)
                        return entity_id, list(ids) if ids else []
                    except Exception:
                        return entity_id, []

            # Execute ID fetches in parallel with bounded concurrency
            tasks = [fetch_ids_for_record(r) for r in ctx.records]
            results = await asyncio.gather(*tasks)

            # Build parent-to-related-ids mapping
            parent_to_related_ids: dict[int, list[int]] = {}
            for entity_id, ids in results:
                if entity_id is not None:
                    parent_to_related_ids[entity_id] = ids

            # Phase 2: Deduplicate and batch fetch full records
            all_unique_ids = list({id_ for ids in parent_to_related_ids.values() for id_ in ids})

            if all_unique_ids:
                full_records = await self._batch_fetch_by_ids(rel.target_entity, all_unique_ids)
            else:
                full_records = []

            # Phase 3: Build lookup table and map back to parents
            records_by_id: dict[int, dict[str, Any]] = {
                r["id"]: r for r in full_records if "id" in r
            }

            for parent_id, related_ids in parent_to_related_ids.items():
                parent_records = [records_by_id.get(id_, {"id": id_}) for id_ in related_ids]
                parent_mapping[parent_id] = parent_records
                included_records.extend(parent_records)

            # Deduplicate included_records (same record may appear for multiple parents)
            seen_ids: set[int] = set()
            unique_records: list[dict[str, Any]] = []
            for record in included_records:
                rec_id = record.get("id")
                if isinstance(rec_id, int) and rec_id not in seen_ids:
                    seen_ids.add(rec_id)
                    unique_records.append(record)
            included_records = unique_records

        elif rel.fetch_strategy == "global_service":
            # N calls per parent (inherent API limitation - filter requires single entity ID)
            service = getattr(self.client, rel.method_or_service)

            # Collect all entity IDs
            entity_ids: list[int] = [r["id"] for r in ctx.records if isinstance(r.get("id"), int)]

            if rel.method_or_service == "interactions":
                # Extract include config for configurable days/limit
                include_config = None
                if ctx.query.include:
                    for item in ctx.query.include:
                        if isinstance(item, dict):
                            for key, val in item.items():
                                if key == step.relationship:
                                    from .models import IncludeConfig

                                    include_config = (
                                        IncludeConfig.model_validate(val) if val else None
                                    )
                                    break
                i_days = (
                    include_config.days
                    if include_config and include_config.days
                    else _DEFAULT_INTERACTION_LOOKBACK_DAYS
                )
                i_limit = include_config.limit if include_config and include_config.limit else 100
                assert rel.filter_field is not None  # schema guarantees this for global_service
                for ent_id in entity_ids:
                    i_records = await _fetch_interactions_for_entity(
                        service,
                        {rel.filter_field: ent_id},
                        days=i_days,
                        limit=i_limit,
                        rate_limiter=self.rate_limiter,
                    )
                    parent_mapping[ent_id] = i_records
                    included_records.extend(i_records)
            else:
                assert rel.filter_field is not None  # schema guarantees this for global_service
                for ent_id in entity_ids:
                    try:
                        filter_kwargs: dict[str, Any] = {rel.filter_field: ent_id}
                        response = await service.list(**filter_kwargs)
                        ent_records: list[dict[str, Any]] = []
                        for item in response.data:
                            record = item.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
                            ent_records.append(record)
                            included_records.append(record)
                        parent_mapping[ent_id] = ent_records
                    except Exception:
                        parent_mapping[ent_id] = []
                        continue

        elif rel.fetch_strategy == "list_entry_indirect":
            # For listEntries: fetch related entities via entity associations
            # The target entity type is stored in method_or_service
            target_entity_type = rel.method_or_service

            # Extract include config (limit, days, where, list)
            include_config = None
            if ctx.query.include:
                for item in ctx.query.include:
                    if isinstance(item, dict):
                        for key, val in item.items():
                            if key == step.relationship:
                                from .models import IncludeConfig

                                include_config = IncludeConfig.model_validate(val) if val else None
                                break

            # Fetch related entities using the specialized handler
            # Pass progress callback to emit incremental progress during N+1 operations
            # This prevents MCP timeout by extending the watchdog timer
            parent_to_related = await self._fetch_list_entry_indirect(
                ctx.records,
                target_entity_type,
                limit=include_config.limit if include_config else None,
                days=include_config.days if include_config else None,
                list_id=await self._resolve_list_id(include_config.list_)
                if include_config and include_config.list_
                else None,
                progress_callback=lambda cur, tot: self.progress.on_step_progress(step, cur, tot),
            )

            # Apply where filter if specified
            if include_config and include_config.where:
                filter_fn = compile_filter(include_config.where)
                for record_id in parent_to_related:
                    parent_to_related[record_id] = [
                        r for r in parent_to_related[record_id] if filter_fn(r)
                    ]

            # Build included_records and parent_mapping
            seen_record_ids: set[int] = set()
            for record_id, related in parent_to_related.items():
                parent_mapping[record_id] = related
                for rec in related:
                    rec_id = rec.get("id")
                    if isinstance(rec_id, int) and rec_id not in seen_record_ids:
                        seen_record_ids.add(rec_id)
                        included_records.append(rec)

        # Store flat list for backward compatibility (JSON/TOON output)
        ctx.included[step.relationship] = included_records
        # Store per-parent mapping for inline expansion (table output)
        ctx.included_by_parent[step.relationship] = parent_mapping

        # Update progress
        self.progress.on_step_progress(step, len(included_records), None)

    async def _execute_expand(self, step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute an expand step (enrich records with computed data).

        Unlike include (which fetches related entities into ctx.included),
        expand modifies records in ctx.records directly.

        Uses the sync Affinity client (self.sync_client) for SDK calls since
        service.get() methods are synchronous.
        """
        expansion_name = step.expansion
        if not expansion_name:
            return

        expansion_def = EXPANSION_REGISTRY.get(expansion_name)
        if not expansion_def:
            raise QueryExecutionError(f"Unknown expansion: {expansion_name}", step=step)

        entity_type = step.entity

        # For listEntries, we need to expand based on the underlying entity type
        if entity_type == "listEntries":
            await self._expand_list_entries(step, ctx, expansion_def)
        elif entity_type in expansion_def.supported_entities:
            await self._expand_direct_entities(step, ctx, entity_type, expansion_def)
        else:
            raise QueryExecutionError(
                f"Expansion '{expansion_name}' not supported for '{entity_type}'. "
                f"Supported: {', '.join(expansion_def.supported_entities)}",
                step=step,
            )

    # PERF: rate_limiter_boundary - expand_direct_entities
    async def _expand_direct_entities(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        entity_type: str,
        expansion_def: Any,
    ) -> None:
        """Expand records by re-fetching with expansion params.

        Uses the async Affinity client directly for SDK calls.
        Person name resolution runs OUTSIDE the rate limiter to avoid holding
        permits during sequential person fetches.
        """
        from affinity.models.entities import Company, Person
        from affinity.types import CompanyId, PersonId

        # Shared cache for person name resolution across all records
        person_name_cache: dict[int, str] = {}
        # SHARED semaphore for person resolution - bounded across ALL concurrent tasks
        person_semaphore = asyncio.Semaphore(10)

        record_count = len(ctx.records)
        completed = 0

        # Warn for expensive expansions
        if record_count > 100:
            logger.warning(
                f"Expanding {expansion_def.name} for {record_count} {entity_type} records. "
                f"This may take a while (~{record_count // 15}s estimated)."
            )

        async def fetch_with_expansion(record: dict[str, Any]) -> None:
            nonlocal completed
            raw_id = record.get("id")
            if raw_id is None:
                return

            # PERF: Entity fetch WITH rate limiter
            async with self.rate_limiter:
                try:
                    # Call async service.get() with expansion params
                    entity: Company | Person
                    if entity_type == "companies":
                        entity = await self.client.companies.get(
                            CompanyId(raw_id), **expansion_def.fetch_params
                        )
                    elif entity_type == "persons":
                        entity = await self.client.persons.get(
                            PersonId(raw_id), **expansion_def.fetch_params
                        )
                    else:
                        return  # Unknown type, skip

                    # Transform interaction dates (no API calls)
                    if expansion_def.name == "interactionDates":
                        record["interactionDates"] = transform_interaction_data(
                            entity.interaction_dates,
                            entity.interactions,
                        )
                except Exception as e:
                    logger.debug(f"Failed to expand {expansion_def.name} for record {raw_id}: {e}")
                    record["interactionDates"] = None
                    return
            # Rate limiter permit released here

            # PERF: Person resolution OUTSIDE rate limiter, with SHARED semaphore
            if expansion_def.name == "interactionDates" and record.get("interactionDates"):
                try:
                    await resolve_interaction_names_async(
                        self.client,
                        record["interactionDates"],
                        person_name_cache,
                        person_semaphore=person_semaphore,
                    )
                except Exception as e:
                    # Graceful degradation: keep interactionDates with unresolved IDs
                    logger.debug(f"Failed to resolve person names for record {raw_id}: {e}")

            # Report progress after each record
            completed += 1
            # SAFETY: No await between read/increment/write - atomic under asyncio
            self.progress.on_step_progress(step, completed, record_count)

        # Execute in parallel with bounded concurrency
        tasks = [fetch_with_expansion(r) for r in ctx.records]
        await asyncio.gather(*tasks)

    # PERF: rate_limiter_boundary - expand_list_entries
    async def _expand_list_entries(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        expansion_def: Any,
    ) -> None:
        """Expand list entries by fetching their underlying entities.

        Uses the async Affinity client directly for SDK calls.
        Person name resolution runs OUTSIDE the rate limiter to avoid holding
        permits during sequential person fetches.
        """
        from affinity.models.entities import Company, Person
        from affinity.types import CompanyId, PersonId

        # Shared cache for person name resolution across all records
        person_name_cache: dict[int, str] = {}
        # SHARED semaphore for person resolution - bounded across ALL concurrent tasks
        person_semaphore = asyncio.Semaphore(10)

        record_count = len(ctx.records)
        completed = 0

        # Warn for expensive expansions
        if record_count > 100:
            logger.warning(
                f"Expanding {expansion_def.name} for {record_count} list entries. "
                f"This may take a while (~{record_count // 15}s estimated)."
            )

        async def fetch_entity_expansion(record: dict[str, Any]) -> None:
            nonlocal completed
            # For list entries, entityId and entityType may be nested in entity
            raw_id = record.get("entityId")
            entity_type = record.get("entityType")

            # Also check nested entity structure
            if raw_id is None and "entity" in record:
                entity_data = record.get("entity", {})
                if isinstance(entity_data, dict):
                    raw_id = entity_data.get("id")
            if entity_type is None:
                entity_type = record.get("type")

            if raw_id is None or entity_type is None:
                return

            # Handle unreplied expansion separately (doesn't require entity refetch)
            if expansion_def.name == "unreplied":
                from ..interaction_utils import async_check_unreplied

                async with self.rate_limiter:
                    try:
                        result = await async_check_unreplied(self.client, entity_type, raw_id)
                        record["unreplied"] = result
                    except Exception as e:
                        logger.debug(f"Failed to check unreplied for {entity_type} {raw_id}: {e}")
                        record["unreplied"] = None

                # Report progress
                completed += 1
                self.progress.on_step_progress(step, completed, record_count)
                return

            # Map list entry entity types to service names
            # Handles both V1 format (integer: 0=person, 1=company) and
            # V2 format (string: "person", "company")
            service_name: str | None = None
            if entity_type in ("company", 1, "organization"):
                service_name = "companies"
            elif entity_type in ("person", 0):
                service_name = "persons"

            if service_name is None:
                return  # Skip unsupported entity types (e.g., opportunities)

            if service_name not in expansion_def.supported_entities:
                return

            # PERF: Entity fetch WITH rate limiter
            async with self.rate_limiter:
                try:
                    # Call async service.get() with expansion params
                    entity: Company | Person
                    if service_name == "companies":
                        entity = await self.client.companies.get(
                            CompanyId(raw_id), **expansion_def.fetch_params
                        )
                    else:  # persons
                        entity = await self.client.persons.get(
                            PersonId(raw_id), **expansion_def.fetch_params
                        )

                    # Transform interaction dates (no API calls)
                    if expansion_def.name == "interactionDates":
                        record["interactionDates"] = transform_interaction_data(
                            entity.interaction_dates,
                            entity.interactions,
                        )
                except Exception as e:
                    logger.debug(
                        f"Failed to expand {expansion_def.name} for {entity_type} {raw_id}: {e}"
                    )
                    record["interactionDates"] = None
                    return
            # Rate limiter permit released here

            # PERF: Person resolution OUTSIDE rate limiter, with SHARED semaphore
            if expansion_def.name == "interactionDates" and record.get("interactionDates"):
                try:
                    await resolve_interaction_names_async(
                        self.client,
                        record["interactionDates"],
                        person_name_cache,
                        person_semaphore=person_semaphore,
                    )
                except Exception as e:
                    # Graceful degradation: keep interactionDates with unresolved IDs
                    logger.debug(f"Failed to resolve person names for {entity_type} {raw_id}: {e}")

            # Report progress after each record
            completed += 1
            # SAFETY: No await between read/increment/write - atomic under asyncio
            self.progress.on_step_progress(step, completed, record_count)

        tasks = [fetch_entity_expansion(r) for r in ctx.records]
        await asyncio.gather(*tasks)

    async def _execute_pre_include(
        self,
        ctx: ExecutionContext,
        required_rels: set[str],
        schema: Any,
    ) -> None:
        """Pre-fetch relationship data required for WHERE clause filtering.

        Called from _execute_filter_with_preinclude() which has already:
        - Detected required relationships via requires_relationship_data()
        - Obtained schema from SCHEMA_REGISTRY

        Args:
            ctx: Execution context with records to process
            required_rels: Set of relationship names/entity types needed for filtering
            schema: Entity schema for the query's from_ entity

        Note: required_rels may contain a mix of:
        - Relationship names (from all_, none_, _count)
        - Entity types (from exists_) - resolved via find_relationship_by_target()
        """
        for rel_ref in required_rels:
            # Try direct relationship name first
            rel_info = schema.relationships.get(rel_ref)
            rel_name: str = rel_ref

            # If not found, try to find by target entity (for exists_ clauses)
            if rel_info is None:
                found_rel_name = find_relationship_by_target(schema, rel_ref)
                if found_rel_name:
                    rel_name = found_rel_name
                    rel_info = schema.relationships.get(rel_name)

            if rel_info is None:
                # Provide helpful error message that distinguishes between relationship names
                # and entity types (exists_ uses entity types like "interactions")
                raise QueryValidationError(
                    f"No relationship found for '{rel_ref}'. "
                    f"If using exists_, ensure the entity type is correct. "
                    f"Available relationships: {list(schema.relationships.keys())}"
                )

            # Fetch relationship data for all records
            await self._fetch_relationship_for_filter(ctx, rel_name, rel_info, schema)

    async def _fetch_relationship_for_filter(
        self,
        ctx: ExecutionContext,
        rel_name: str,
        rel_info: Any,
        schema: Any,
    ) -> None:
        """Fetch relationship data and attach to ExecutionContext.

        Storage structure (matches existing relationship_counts field):
        - relationship_data[rel_name][record_id] = [related_records]
        - relationship_counts[rel_name][record_id] = count

        IDs-only upgrade: Some relationships (entity_method with *_ids methods)
        only return IDs. If the quantifier's where clause needs to filter on
        other fields, we batch-fetch full records.
        """
        id_field = schema.id_field or "id"

        # Initialize nested dicts for this relationship
        if rel_name not in ctx.relationship_data:
            ctx.relationship_data[rel_name] = {}
        if rel_name not in ctx.relationship_counts:
            ctx.relationship_counts[rel_name] = {}

        # Check if this is an IDs-only relationship that needs upgrading
        # CRITICAL: Use _any_quantifier_needs_full_records() which checks ALL quantifiers
        # for this relationship, not just the first one found
        is_ids_only = (
            rel_info.fetch_strategy == "entity_method"
            and rel_info.method_or_service
            and rel_info.method_or_service.endswith("_ids")
        )
        needs_upgrade = is_ids_only and _any_quantifier_needs_full_records(
            ctx.query.where, rel_name, schema
        )

        async def fetch_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
            """Fetch related records for a single parent record."""
            async with self.rate_limiter:  # Use executor's semaphore for concurrency control
                record_id = record.get(id_field)
                if record_id is None:
                    return []

                related: list[dict[str, Any]] = []

                if rel_info.fetch_strategy == "entity_method":
                    # N+1: fetch via entity method (e.g., get_associated_company_ids)
                    service = getattr(self.client, ctx.query.from_, None)
                    method = getattr(service, rel_info.method_or_service, None) if service else None
                    if method is None:
                        return []

                    try:
                        result = await method(record_id)
                        # Result might be list of IDs or list of records
                        # Check all elements are ints to avoid type errors in comprehension
                        if result and all(isinstance(item, int) for item in result):
                            related = [{"id": id_} for id_ in result]  # IDs only
                        else:
                            related = [
                                r.model_dump(mode="json", by_alias=True)
                                if hasattr(r, "model_dump")
                                else r
                                for r in result
                            ]
                    except (AuthenticationError, AuthorizationError):
                        # Let auth errors propagate - these indicate real problems
                        raise
                    except Exception as e:
                        # Graceful degradation: treat failed fetch as "no relationships"
                        logger.debug(f"Failed to fetch {rel_name} for record {record_id}: {e}")
                        return []

                elif rel_info.fetch_strategy == "global_service":
                    # Single filtered call (e.g., interactions.list(person_id=...))
                    service = getattr(self.client, rel_info.method_or_service, None)
                    if service is None:
                        return []

                    try:
                        if rel_info.method_or_service == "interactions":
                            related = await _fetch_interactions_for_entity(
                                service,
                                {rel_info.filter_field: record_id},
                                rate_limiter=self.rate_limiter,
                            )
                        else:
                            filter_kwargs = {rel_info.filter_field: record_id}
                            response = await service.list(**filter_kwargs)
                            related = [
                                item.model_dump(mode="json", by_alias=True)
                                for item in response.data
                            ]
                    except (AuthenticationError, AuthorizationError):
                        # Let auth errors propagate - these indicate real problems
                        raise
                    except Exception as e:
                        # Graceful degradation: treat failed fetch as "no relationships"
                        logger.debug(f"Failed to fetch {rel_name} for record {record_id}: {e}")
                        return []

                return related

        # Phase 1: Fetch IDs/records for all parent records in parallel
        async def fetch_for_parent(
            record: dict[str, Any],
        ) -> tuple[int | None, list[dict[str, Any]]]:
            """Fetch related records/IDs for a single parent record."""
            record_id = record.get(id_field)
            if record_id is None:
                return (None, [])
            related = await fetch_for_record(record)
            return (record_id, related)

        results = await asyncio.gather(*[fetch_for_parent(r) for r in ctx.records])

        # Build parent -> related_data mapping
        parent_to_related: dict[int, list[dict[str, Any]]] = {}
        for record_id, related in results:
            if record_id is not None:
                parent_to_related[record_id] = related

        # Phase 2: If IDs-only and needs upgrade, batch fetch ALL unique IDs at once
        if needs_upgrade:
            # Collect all unique IDs needing upgrade
            all_ids_to_fetch: set[int] = set()
            for _rec_id, related in parent_to_related.items():
                if related and isinstance(related[0].get("id"), int) and len(related[0]) == 1:
                    all_ids_to_fetch.update(r["id"] for r in related)

            if all_ids_to_fetch:
                # Phase 3: Single batch fetch for all unique IDs
                full_records = await self._batch_fetch_by_ids(
                    rel_info.target_entity, list(all_ids_to_fetch)
                )
                records_by_id = {r["id"]: r for r in full_records if "id" in r}

                # Phase 4: Map full records back to each parent
                for record_id, related in parent_to_related.items():
                    if related and isinstance(related[0].get("id"), int) and len(related[0]) == 1:
                        parent_to_related[record_id] = [
                            records_by_id.get(r["id"], r) for r in related
                        ]

        # Store results in context
        for record_id, related in parent_to_related.items():
            ctx.relationship_data[rel_name][record_id] = related
            ctx.relationship_counts[rel_name][record_id] = len(related)

    async def _batch_fetch_by_ids(
        self,
        entity_type: str,
        ids: list[int],
    ) -> list[dict[str, Any]]:
        """Batch fetch records by IDs for IDs-only relationship upgrade.

        Uses V2 batch lookup (service.iter(ids=...)) for companies, persons,
        and opportunities. Falls back to individual get() calls for V1-only
        entities or if batch lookup fails.
        """
        # Get schema to find service attribute
        schema = SCHEMA_REGISTRY.get(entity_type)
        if schema is None:
            return [{"id": id_} for id_ in ids]  # Fallback to ID-only

        service = getattr(self.client, schema.service_attr, None)
        if service is None:
            return [{"id": id_} for id_ in ids]  # Fallback to ID-only

        # Try batch lookup first (V2 entities only - they support ids param)
        # V1-only services (notes, interactions, tasks) have iter() but WITHOUT ids param
        V2_BATCH_ENTITIES = {"companies", "persons", "opportunities"}
        if entity_type in V2_BATCH_ENTITIES and hasattr(service, "iter"):
            try:
                records: list[dict[str, Any]] = []
                # self.client is AsyncAffinity, so service.iter() returns AsyncIterator
                async for item in service.iter(ids=ids):
                    records.append(item.model_dump(mode="json", by_alias=True))
                return records
            except Exception as e:
                # Fall through to individual fetches for graceful per-ID degradation
                logger.debug(f"Batch lookup failed for {entity_type}, trying individual: {e}")

        # Fallback: individual fetches (V1-only entities OR batch-failed V2 entities)
        # Preserves per-ID graceful degradation: 99 successes + 1 failure = 99 full + 1 ID-only
        if hasattr(service, "get"):

            async def fetch_one(id_: int) -> dict[str, Any]:
                async with self.rate_limiter:
                    try:
                        record = await service.get(id_)
                        if hasattr(record, "model_dump"):
                            result: dict[str, Any] = record.model_dump(mode="json", by_alias=True)
                            return result
                        return dict(record)  # type: ignore[arg-type]
                    except Exception as e:
                        # See Finding #46 about broad exception handling trade-offs
                        logger.debug(f"Failed to fetch {entity_type} {id_}: {e}")
                        return {"id": id_}  # ID-only stub on failure

            results = await asyncio.gather(*[fetch_one(id_) for id_ in ids])
            return list(results)

        return [{"id": id_} for id_ in ids]  # Fallback to ID-only

    # =========================================================================
    # List Entry Indirect Fetch Handlers
    # =========================================================================

    async def _resolve_list_id(self, selector: str | int) -> int | None:
        """Resolve a list selector (name or ID) to a list ID."""
        if isinstance(selector, int):
            return selector
        try:
            from ..resolve import async_resolve_list_selector

            resolved = await async_resolve_list_selector(
                client=self.client,
                selector=str(selector),
            )
            return int(resolved.list.id)
        except Exception:
            return None

    async def _fetch_list_entry_indirect(
        self,
        entries: list[dict[str, Any]],
        target_entity_type: str,
        *,
        limit: int | None = None,
        days: int | None = None,
        list_id: int | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> dict[int, list[dict[str, Any]]]:
        """Fetch related entities for list entries based on entityType.

        Args:
            entries: List entry records with id, entityId, entityType fields
            target_entity_type: What to fetch (persons/companies/opportunities/interactions)
            limit: Max records per entity (for interactions)
            days: Lookback window in days (for interactions)
            list_id: Scope to specific opportunity list (for opportunities)
            progress_callback: Optional callback(current, total) for progress emission
                during N+1 operations to prevent MCP timeout

        Returns:
            Dict mapping listEntryId -> list of related entity records
        """
        results: dict[int, list[dict[str, Any]]] = {}
        semaphore = asyncio.Semaphore(50)  # Limit concurrent connections

        if target_entity_type == "persons":
            await self._fetch_persons_for_list_entries(
                entries, results, semaphore, progress_callback=progress_callback
            )
        elif target_entity_type == "companies":
            await self._fetch_companies_for_list_entries(
                entries, results, semaphore, progress_callback=progress_callback
            )
        elif target_entity_type == "opportunities":
            await self._fetch_opportunities_for_list_entries(
                entries, results, semaphore, list_id=list_id, progress_callback=progress_callback
            )
        elif target_entity_type == "interactions":
            await self._fetch_interactions_for_list_entries(
                entries,
                results,
                semaphore,
                limit=limit,
                days=days,
                progress_callback=progress_callback,
            )

        return results

    async def _fetch_persons_for_list_entries(
        self,
        entries: list[dict[str, Any]],
        results: dict[int, list[dict[str, Any]]],
        semaphore: asyncio.Semaphore,
        *,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Fetch associated persons for list entries."""
        from affinity.types import CompanyId, OpportunityId

        async def get_person_ids_for_entry(entry: dict[str, Any]) -> tuple[int, list[int]]:
            entry_id = entry["id"]
            entity_id = entry.get("entityId")
            entity_type = entry.get("entityType")

            if entity_id is None:
                return (entry_id, [])

            # Handle V1 (integer) and V2 (string) entityType formats
            if entity_type in ("person", 0):
                # Person entry: the entity IS the person
                return (entry_id, [entity_id])

            # Company/Opportunity entries: fetch associated person IDs
            ids: list[int] = []
            async with semaphore, self.rate_limiter:
                try:
                    if entity_type in ("company", 1, "organization"):
                        ids = list(
                            await self.client.companies.get_associated_person_ids(
                                CompanyId(entity_id)
                            )
                        )
                    elif entity_type == "opportunity":
                        ids = list(
                            await self.client.opportunities.get_associated_person_ids(
                                OpportunityId(entity_id)
                            )
                        )
                except Exception:
                    pass  # Return empty list on error

            return (entry_id, ids)

        # Phase 1: Parallel fetch all association IDs with incremental progress
        # Use as_completed to emit progress during N+1 fetches (prevents MCP timeout)
        total = len(entries)
        tasks = [asyncio.create_task(get_person_ids_for_entry(e)) for e in entries]
        id_results: list[tuple[int, list[int]]] = []

        for completed, future in enumerate(asyncio.as_completed(tasks), start=1):
            result = await future
            id_results.append(result)
            # Emit progress every 10 items or at completion to extend MCP timeout
            if progress_callback and (completed % 10 == 0 or completed == total):
                progress_callback(completed, total)

        entry_to_person_ids = dict(id_results)

        # Phase 2: Deduplicate and batch fetch full records
        all_person_ids = list({pid for pids in entry_to_person_ids.values() for pid in pids})
        if all_person_ids:
            full_records = await self._batch_fetch_by_ids("persons", all_person_ids)
            id_to_record = {r["id"]: r for r in full_records if "id" in r}

            for entry_id, person_ids in entry_to_person_ids.items():
                results[entry_id] = [id_to_record[pid] for pid in person_ids if pid in id_to_record]
        else:
            for entry_id in entry_to_person_ids:
                results[entry_id] = []

    async def _fetch_companies_for_list_entries(
        self,
        entries: list[dict[str, Any]],
        results: dict[int, list[dict[str, Any]]],
        semaphore: asyncio.Semaphore,
        *,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Fetch associated companies for list entries."""
        from affinity.types import OpportunityId, PersonId

        async def get_company_ids_for_entry(entry: dict[str, Any]) -> tuple[int, list[int]]:
            entry_id = entry["id"]
            entity_id = entry.get("entityId")
            entity_type = entry.get("entityType")

            if entity_id is None:
                return (entry_id, [])

            # Handle V1 (integer) and V2 (string) entityType formats
            if entity_type in ("company", 1, "organization"):
                # Company entry: the entity IS the company
                return (entry_id, [entity_id])

            ids: list[int] = []
            async with semaphore, self.rate_limiter:
                try:
                    if entity_type in ("person", 0):
                        ids = list(
                            await self.client.persons.get_associated_company_ids(
                                PersonId(entity_id)
                            )
                        )
                    elif entity_type == "opportunity":
                        ids = list(
                            await self.client.opportunities.get_associated_company_ids(
                                OpportunityId(entity_id)
                            )
                        )
                except Exception:
                    pass  # Return empty list on error

            return (entry_id, ids)

        # Same two-phase pattern as persons with incremental progress
        total = len(entries)
        tasks = [asyncio.create_task(get_company_ids_for_entry(e)) for e in entries]
        id_results: list[tuple[int, list[int]]] = []

        for completed, future in enumerate(asyncio.as_completed(tasks), start=1):
            result = await future
            id_results.append(result)
            if progress_callback and (completed % 10 == 0 or completed == total):
                progress_callback(completed, total)

        entry_to_company_ids = dict(id_results)

        all_company_ids = list({cid for cids in entry_to_company_ids.values() for cid in cids})
        if all_company_ids:
            full_records = await self._batch_fetch_by_ids("companies", all_company_ids)
            id_to_record = {r["id"]: r for r in full_records if "id" in r}

            for entry_id, company_ids in entry_to_company_ids.items():
                results[entry_id] = [
                    id_to_record[cid] for cid in company_ids if cid in id_to_record
                ]
        else:
            for entry_id in entry_to_company_ids:
                results[entry_id] = []

    async def _fetch_opportunities_for_list_entries(
        self,
        entries: list[dict[str, Any]],
        results: dict[int, list[dict[str, Any]]],
        semaphore: asyncio.Semaphore,
        *,
        list_id: int | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Fetch associated opportunities for list entries.

        Args:
            list_id: If provided, only return opportunities from this specific list.
            progress_callback: Optional callback(current, total) for progress emission
        """
        from affinity.types import CompanyId, PersonId

        async def get_opportunity_ids_for_entry(entry: dict[str, Any]) -> tuple[int, list[int]]:
            entry_id = entry["id"]
            entity_id = entry.get("entityId")
            entity_type = entry.get("entityType")

            if entity_id is None:
                return (entry_id, [])

            # Handle V1 (integer) and V2 (string) entityType formats
            if entity_type == "opportunity":
                # Opportunity entry: the entity IS the opportunity
                return (entry_id, [entity_id])

            ids: list[int] = []
            async with semaphore, self.rate_limiter:
                try:
                    if entity_type in ("person", 0):
                        ids = list(
                            await self.client.persons.get_associated_opportunity_ids(
                                PersonId(entity_id)
                            )
                        )
                    elif entity_type in ("company", 1, "organization"):
                        ids = list(
                            await self.client.companies.get_associated_opportunity_ids(
                                CompanyId(entity_id)
                            )
                        )
                except Exception:
                    pass  # Return empty list on error

            return (entry_id, ids)

        # Same two-phase pattern with incremental progress
        total = len(entries)
        tasks = [asyncio.create_task(get_opportunity_ids_for_entry(e)) for e in entries]
        id_results: list[tuple[int, list[int]]] = []

        for completed, future in enumerate(asyncio.as_completed(tasks), start=1):
            result = await future
            id_results.append(result)
            if progress_callback and (completed % 10 == 0 or completed == total):
                progress_callback(completed, total)

        entry_to_opp_ids = dict(id_results)

        all_opp_ids = list({oid for oids in entry_to_opp_ids.values() for oid in oids})
        if all_opp_ids:
            full_records = await self._batch_fetch_by_ids("opportunities", all_opp_ids)

            # Filter to specific list if scoped (--expand-opportunities-list parity)
            if list_id is not None:
                full_records = [r for r in full_records if r.get("listId") == list_id]

            id_to_record = {r["id"]: r for r in full_records if "id" in r}

            for entry_id, opp_ids in entry_to_opp_ids.items():
                results[entry_id] = [id_to_record[oid] for oid in opp_ids if oid in id_to_record]
        else:
            for entry_id in entry_to_opp_ids:
                results[entry_id] = []

    async def _fetch_interactions_for_list_entries(
        self,
        entries: list[dict[str, Any]],
        results: dict[int, list[dict[str, Any]]],
        semaphore: asyncio.Semaphore,
        *,
        limit: int | None = None,
        days: int | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Fetch interactions for list entries.

        Args:
            limit: Max interactions per entity (default 100)
            days: Lookback window in days (default 90)
            progress_callback: Optional callback(current, total) for progress emission
        """
        from affinity.types import CompanyId, OpportunityId, PersonId

        effective_limit = limit if limit is not None else 100
        effective_days = days if days is not None else _DEFAULT_INTERACTION_LOOKBACK_DAYS

        async def get_interactions_for_entry(
            entry: dict[str, Any],
        ) -> tuple[int, list[dict[str, Any]]]:
            entry_id = entry["id"]
            entity_id = entry.get("entityId")
            entity_type = entry.get("entityType")

            if entity_id is None:
                return (entry_id, [])

            # Build entity-specific filter kwargs
            base_kwargs: dict[str, Any] = {}
            if entity_type in ("person", 0):
                base_kwargs["person_id"] = PersonId(entity_id)
            elif entity_type in ("company", 1, "organization"):
                base_kwargs["company_id"] = CompanyId(entity_id)
            elif entity_type == "opportunity":
                base_kwargs["opportunity_id"] = OpportunityId(entity_id)
            else:
                return (entry_id, [])

            interactions = await _fetch_interactions_for_entity(
                self.client.interactions,
                base_kwargs,
                days=effective_days,
                limit=effective_limit,
                semaphore=semaphore,
                rate_limiter=self.rate_limiter,
            )
            return (entry_id, interactions)

        # Direct fetch with incremental progress (no ID→record indirection needed)
        total = len(entries)
        tasks = [asyncio.create_task(get_interactions_for_entry(e)) for e in entries]
        interaction_results: list[tuple[int, list[dict[str, Any]]]] = []

        for completed, future in enumerate(asyncio.as_completed(tasks), start=1):
            result = await future
            interaction_results.append(result)
            if progress_callback and (completed % 10 == 0 or completed == total):
                progress_callback(completed, total)

        results.update(dict(interaction_results))

    def _execute_aggregate(self, _step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute aggregation step."""
        if ctx.query.aggregate is None:
            return

        if ctx.query.group_by is not None:
            # Group and aggregate
            results = group_and_aggregate(
                ctx.records,
                ctx.query.group_by,
                ctx.query.aggregate,
            )

            # Apply having if present
            if ctx.query.having is not None:
                results = apply_having(results, ctx.query.having)

            ctx.records = results
        else:
            # Simple aggregate (single result)
            agg_result = compute_aggregates(ctx.records, ctx.query.aggregate)
            ctx.records = [agg_result]

    def _execute_sort(self, _step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute sort step."""
        order_by = ctx.query.order_by
        if order_by is None:
            return

        # Wrapper class for descending sort on non-numeric values
        class _Descending:
            """Wrapper that inverts comparison for descending sort."""

            __slots__ = ("value",)
            __hash__ = None  # type: ignore[assignment]  # Unhashable since we define __eq__

            def __init__(self, value: Any) -> None:
                self.value = value

            def __lt__(self, other: object) -> bool:
                if not isinstance(other, _Descending):
                    return NotImplemented  # type: ignore[return-value]
                # Handle None values - None should sort after non-None
                if self.value is None and other.value is None:
                    return False
                if self.value is None:
                    return False  # None is not less than anything (sorts last)
                if other.value is None:
                    return True  # Non-None is less than None (comes first)
                return bool(self.value > other.value)

            def __gt__(self, other: object) -> bool:
                if not isinstance(other, _Descending):
                    return NotImplemented  # type: ignore[return-value]
                if self.value is None and other.value is None:
                    return False
                if self.value is None:
                    return True  # None is greater (sorts last)
                if other.value is None:
                    return False  # Non-None is not greater than None
                return bool(self.value < other.value)

            def __eq__(self, other: object) -> bool:
                if not isinstance(other, _Descending):
                    return NotImplemented  # type: ignore[return-value]
                return bool(self.value == other.value)

            def __le__(self, other: object) -> bool:
                if not isinstance(other, _Descending):
                    return NotImplemented  # type: ignore[return-value]
                return not self.__gt__(other)

            def __ge__(self, other: object) -> bool:
                if not isinstance(other, _Descending):
                    return NotImplemented  # type: ignore[return-value]
                return not self.__lt__(other)

        # Build sort key function
        def sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
            keys: list[Any] = []
            for order in order_by:
                value = resolve_field_path(record, order.field) if order.field else None

                if order.direction == "asc":
                    # Ascending: None sorts to end via tuple ordering
                    if value is None:
                        keys.append((1, None))
                    else:
                        keys.append((0, value))
                else:
                    # Descending: wrap ALL values in _Descending for consistent comparison
                    keys.append((0, _Descending(value)))

            return tuple(keys)

        # Sort with stable algorithm
        try:
            ctx.records.sort(key=sort_key)
        except TypeError:
            # Mixed types - fall back to string comparison
            for order in reversed(order_by):
                reverse = order.direction == "desc"
                field = order.field or ""

                def make_key(f: str) -> Callable[[dict[str, Any]], str]:
                    return lambda r: str(resolve_field_path(r, f) or "")

                ctx.records.sort(key=make_key(field), reverse=reverse)

    def _execute_limit(self, _step: PlanStep, ctx: ExecutionContext) -> None:
        """Execute limit step."""
        if ctx.query.limit is not None:
            ctx.records = ctx.records[: ctx.query.limit]

    async def _execute_streaming(
        self,
        _plan: ExecutionPlan,
        ctx: ExecutionContext,
    ) -> None:
        """Execute query in streaming mode with early termination.

        Processes pages one at a time, applying filters per page, and stops
        early when the limit is reached. This avoids fetching all records
        when only a few matches are needed.

        Only applicable when can_use_streaming(query) returns True.
        """
        from .filters import partition_where

        query = ctx.query
        # Use query.limit if set, otherwise use max_records as the effective limit
        # This enables streaming when --max-records is explicitly provided
        limit = query.limit if query.limit is not None else self.max_records

        schema = SCHEMA_REGISTRY.get(query.from_)
        if schema is None:
            raise QueryExecutionError(f"Unknown entity: {query.from_}")

        # Only GLOBAL fetch strategy supports streaming (persons, companies, opportunities)
        if schema.fetch_strategy != FetchStrategy.GLOBAL:
            # Fall back to normal execution for non-global entities
            return

        # Partition filter into cheap and expensive parts
        cheap_filter_clause, expensive_filter_clause = partition_where(query.where, query.from_)

        # Compile cheap filter
        cheap_fn: Callable[[dict[str, Any]], bool] | None = None
        if cheap_filter_clause is not None:
            cheap_fn = compile_filter(cheap_filter_clause)

        # Check if expensive filter needs relationship data
        required_rels: set[str] = set()
        if expensive_filter_clause is not None:
            required_rels = requires_relationship_data(expensive_filter_clause)

        # Compile expensive filter (with or without context)
        expensive_fn: Callable[[dict[str, Any]], bool] | None = None
        if expensive_filter_clause is not None and not required_rels:
            # No relationship data needed - use simple filter
            expensive_fn = compile_filter(expensive_filter_clause)

        # Get service for streaming pages
        service = getattr(self.client, schema.service_attr)
        accumulated: list[dict[str, Any]] = []

        # Create a fake step for progress reporting
        fetch_step = PlanStep(
            step_id=0,
            operation="fetch_streaming",
            description=f"Streaming fetch from {query.from_} with early termination",
            entity=query.from_,
            estimated_api_calls=1,
        )
        self.progress.on_step_start(fetch_step)

        pages_processed = 0
        try:
            # O(1) streaming resumption: use cursor-based pagination if resuming
            if self.resume_api_cursor:
                # Manual cursor-based iteration for resumption
                current_cursor: str | None = self.resume_api_cursor
                while current_cursor:
                    page = await service.list(cursor=current_cursor)
                    pages_processed += 1
                    page_records = [r.model_dump(mode="json", by_alias=True) for r in page.data]

                    # Phase 1: Apply cheap filter (no API calls)
                    if cheap_fn is not None:
                        page_records = [r for r in page_records if cheap_fn(r)]

                    # Phase 2: Handle expensive filter
                    if expensive_filter_clause is not None and page_records:
                        if required_rels:
                            page_ctx = ExecutionContext(
                                query=query,
                                records=page_records,
                                max_records=self.max_records,
                            )
                            await self._execute_pre_include(page_ctx, required_rels, schema)
                            id_field = schema.id_field or "id"
                            filter_ctx = FilterContext(
                                relationship_data=page_ctx.relationship_data,
                                relationship_counts=page_ctx.relationship_counts,
                                schema=schema,
                                id_field=id_field,
                            )
                            expensive_fn_ctx = compile_filter_with_context(
                                expensive_filter_clause, filter_ctx
                            )
                            page_records = [r for r in page_records if expensive_fn_ctx(r)]
                        elif expensive_fn is not None:
                            page_records = [r for r in page_records if expensive_fn(r)]

                    # Accumulate matches
                    accumulated.extend(page_records)
                    self.progress.on_step_progress(fetch_step, len(accumulated), None)

                    # Early termination check
                    if len(accumulated) >= limit:
                        ctx.records = accumulated[:limit]
                        ctx.early_terminated = True
                        ctx.last_api_cursor = page.next_cursor
                        self.progress.on_step_complete(fetch_step, len(ctx.records))
                        return

                    # Safety check
                    if len(accumulated) >= ctx.max_records:
                        ctx.records = accumulated[: ctx.max_records]
                        ctx.last_api_cursor = page.next_cursor
                        self.progress.on_step_complete(fetch_step, len(ctx.records))
                        return

                    current_cursor = page.next_cursor

                # Exhausted all pages via cursor resumption
                ctx.records = accumulated
                self.progress.on_step_complete(fetch_step, len(ctx.records))
                return

            # Standard path: iterate from beginning
            async for page in service.all().pages():
                pages_processed += 1
                page_records = [r.model_dump(mode="json", by_alias=True) for r in page.data]

                # Phase 1: Apply cheap filter (no API calls)
                if cheap_fn is not None:
                    page_records = [r for r in page_records if cheap_fn(r)]

                # Phase 2: Handle expensive filter
                if expensive_filter_clause is not None and page_records:
                    if required_rels:
                        # Need to pre-fetch relationship data for this page's survivors
                        # Create temporary context for this page
                        page_ctx = ExecutionContext(
                            query=query,
                            records=page_records,
                            max_records=self.max_records,
                        )

                        # Pre-fetch relationship data
                        await self._execute_pre_include(page_ctx, required_rels, schema)

                        # Apply expensive filter with context
                        id_field = schema.id_field or "id"
                        filter_ctx = FilterContext(
                            relationship_data=page_ctx.relationship_data,
                            relationship_counts=page_ctx.relationship_counts,
                            schema=schema,
                            id_field=id_field,
                        )
                        expensive_fn_ctx = compile_filter_with_context(
                            expensive_filter_clause, filter_ctx
                        )
                        page_records = [r for r in page_records if expensive_fn_ctx(r)]
                    elif expensive_fn is not None:
                        page_records = [r for r in page_records if expensive_fn(r)]

                # Accumulate matches
                accumulated.extend(page_records)

                # Report progress
                self.progress.on_step_progress(fetch_step, len(accumulated), None)

                # Early termination check
                if len(accumulated) >= limit:
                    ctx.records = accumulated[:limit]
                    ctx.early_terminated = True
                    # Capture API cursor for O(1) streaming resumption
                    ctx.last_api_cursor = page.next_cursor
                    logger.debug(
                        "Streaming early termination: found %d matches in %d pages",
                        limit,
                        pages_processed,
                    )
                    self.progress.on_step_complete(fetch_step, len(ctx.records))
                    return

                # Safety check - don't exceed max_records even before limit
                if len(accumulated) >= ctx.max_records:
                    ctx.records = accumulated[: ctx.max_records]
                    # Capture API cursor for O(1) streaming resumption
                    ctx.last_api_cursor = page.next_cursor
                    self.progress.on_step_complete(fetch_step, len(ctx.records))
                    return

                # Timeout check
                ctx.check_timeout(self.timeout)

            # Exhausted all pages without hitting limit
            ctx.records = accumulated[:limit] if len(accumulated) > limit else accumulated
            self.progress.on_step_complete(fetch_step, len(ctx.records))

        except QueryExecutionError:
            raise
        except Exception as e:
            raise QueryExecutionError(
                f"Failed to fetch {query.from_} in streaming mode: {e}",
                cause=e,
                partial_results=accumulated,
            ) from None


# =============================================================================
# Convenience Function
# =============================================================================


async def execute_query(
    client: AsyncAffinity,
    plan: ExecutionPlan,
    *,
    progress: QueryProgressCallback | None = None,
    concurrency: int = 10,
    max_records: int = 10000,
    timeout: float = 300.0,
) -> QueryResult:
    """Execute a query plan.

    Convenience function that creates an executor and runs the plan.

    Args:
        client: AsyncAffinity client
        plan: Execution plan
        progress: Optional progress callback
        concurrency: Max concurrent API calls
        max_records: Safety limit
        timeout: Execution timeout

    Returns:
        QueryResult
    """
    executor = QueryExecutor(
        client,
        progress=progress,
        concurrency=concurrency,
        max_records=max_records,
        timeout=timeout,
    )
    return await executor.execute(plan)
