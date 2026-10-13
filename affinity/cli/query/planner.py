"""Query execution planner.

Analyzes queries and generates execution plans with cost estimates.
This module is CLI-only and NOT part of the public SDK API.
"""

from __future__ import annotations

from graphlib import TopologicalSorter
from typing import TYPE_CHECKING, Literal

from .exceptions import QueryValidationError
from .filters import (
    extract_parent_and_id_lookup,
    extract_single_id_lookup,
    partition_where,
    requires_relationship_data,
)
from .models import ExecutionPlan, PlanStep, Query, WhereClause
from .schema import (
    EXPANSION_REGISTRY,
    UNBOUNDED_ENTITIES,
    FetchStrategy,
    RelationshipDef,
    find_relationship_by_target,
    get_entity_schema,
    get_relationship,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Cost Model Constants
# =============================================================================

# Estimated records per entity type (for planning purposes)
ESTIMATED_ENTITY_COUNTS: dict[str, int] = {
    "persons": 5000,
    "companies": 2000,
    "opportunities": 1000,
    "listEntries": 10000,
    "interactions": 10000,
    "notes": 5000,
}

# Default estimate when entity type unknown
DEFAULT_ENTITY_COUNT = 1000

# Average related entities per record
ESTIMATED_RELATIONSHIPS: dict[str, int] = {
    "companies": 2,  # persons -> companies
    "persons": 3,  # companies -> persons
    "opportunities": 5,  # persons/companies -> opportunities
    "interactions": 20,
    "notes": 10,
    "listEntries": 5,
}

# Thresholds for warnings
EXPENSIVE_OPERATION_THRESHOLD = 100  # API calls
VERY_EXPENSIVE_OPERATION_THRESHOLD = 500
MAX_RECORDS_WARNING_THRESHOLD = 1000

# Memory estimation (bytes per record)
BYTES_PER_RECORD = 2000


# =============================================================================
# Helper Functions
# =============================================================================


def _strip_parent_filter(where: WhereClause | None, parent_field: str | None) -> WhereClause | None:
    """Remove parent filter field conditions from where clause.

    For REQUIRES_PARENT entities (like listEntries), the parent_filter_field
    (e.g., listId) is used to scope the API call, NOT as a client-side filter.
    This function strips those conditions to avoid unnecessary filter steps.

    Args:
        where: The where clause to process
        parent_field: The parent filter field name (e.g., "listId", "listName")

    Returns:
        Where clause without parent filter conditions, or None if empty.
    """
    if where is None or parent_field is None:
        return where

    # For listEntries, both listId and listName are parent filters
    parent_fields = {parent_field}
    if parent_field == "listId":
        parent_fields.add("listName")

    # Single condition - check if it's the parent filter
    if where.path is not None:
        if where.path in parent_fields:
            return None  # This condition is handled by the fetch
        return where  # Keep other conditions

    # Compound condition - recursively strip parent filters from children
    if where.and_ is not None:
        filtered = [_strip_parent_filter(child, parent_field) for child in where.and_]
        filtered = [c for c in filtered if c is not None]
        if not filtered:
            return None
        if len(filtered) == 1:
            return filtered[0]
        return WhereClause(and_=filtered)

    if where.or_ is not None:
        filtered = [_strip_parent_filter(child, parent_field) for child in where.or_]
        filtered = [c for c in filtered if c is not None]
        if not filtered:
            return None
        if len(filtered) == 1:
            return filtered[0]
        return WhereClause(or_=filtered)

    # Not, quantifiers, exists - recurse on nested where
    if where.not_ is not None:
        inner = _strip_parent_filter(where.not_, parent_field)
        if inner is None:
            return None
        return WhereClause(not_=inner)

    # Pass through quantifiers and exists unchanged (they don't contain parent filters)
    return where


def _field_paths(where: WhereClause) -> set[str]:
    """The fields.<name> paths a WHERE clause tests (outside quantifiers)."""
    paths: set[str] = set()
    if where.path and where.path.startswith("fields."):
        paths.add(where.path)
    for child in [*(where.and_ or []), *(where.or_ or [])]:
        paths |= _field_paths(child)
    if where.not_ is not None:
        paths |= _field_paths(where.not_)
    return paths


# =============================================================================
# Query Planner
# =============================================================================


class QueryPlanner:
    """Generates execution plans from parsed queries."""

    def __init__(self, *, max_records: int = 10000, concurrency: int = 10) -> None:
        """Initialize the planner.

        Args:
            max_records: Maximum records to fetch (safety limit)
            concurrency: Concurrency level for N+1 operations
        """
        self.max_records = max_records
        self.concurrency = concurrency

    def plan(self, query: Query) -> ExecutionPlan:
        """Generate an execution plan for a query.

        Args:
            query: Validated Query object

        Returns:
            ExecutionPlan with steps, estimates, and warnings

        Raises:
            QueryPlanError: If plan cannot be generated
            QueryValidationError: If query references unknown entities/relationships
        """
        steps: list[PlanStep] = []
        warnings: list[str] = []
        recommendations: list[str] = []
        step_id = 0

        # Validate entity exists in schema
        entity_schema = get_entity_schema(query.from_)
        if entity_schema is None:
            raise QueryValidationError(
                f"Unknown entity type '{query.from_}'",
                field="from",
            )

        # Step 1: Fetch primary entity
        # Check for single-ID lookup optimization first
        single_id = extract_single_id_lookup(query.where)
        base_entity_count = ESTIMATED_ENTITY_COUNTS.get(query.from_, DEFAULT_ENTITY_COUNT)

        # Also check for REQUIRES_PARENT entities (e.g., listEntries with listId + id)
        parent_and_id: tuple[int, int] | None = None
        if (
            single_id is None
            and entity_schema.fetch_strategy == FetchStrategy.REQUIRES_PARENT
            and entity_schema.parent_filter_field
            and query.from_ == "listEntries"  # Currently only listEntries supported
        ):
            parent_and_id = extract_parent_and_id_lookup(
                query.where, entity_schema.parent_filter_field
            )

        fetch_operation: Literal["fetch", "fetch_streaming"]
        if single_id is not None or parent_and_id is not None:
            # Single ID lookup: service.get(id) is 1 API call
            estimated_records = 1
            fetch_api_calls = 1
            fetch_description = f"Fetch {query.from_} by ID (direct lookup)"
            fetch_operation = "fetch"
        elif query.where is not None:
            # Client-side filter: must scan pages until enough matches found
            # Estimate output size based on limit (for downstream steps)
            estimated_records = self._estimate_primary_records(query)
            # Estimate API calls based on how much we need to scan
            if query.order_by is not None:
                # orderBy requires ALL records before sorting, then limit is applied
                scan_estimate = base_entity_count
            else:
                # With early termination, we expect to scan ~(records_needed / selectivity) records
                # Heuristic: assume 50% selectivity, double the limit for expected scan size
                scan_estimate = min(base_entity_count, (query.limit or base_entity_count) * 2)
            fetch_api_calls = self._estimate_pages(scan_estimate)
            fetch_description = (
                f"Fetch {query.from_} through Affinity's search (filtered by Affinity where "
                f"possible, the rest locally)"
                if query.from_ in ("companies", "persons") and _field_paths(query.where)
                else f"Fetch {query.from_} (paginated, client-side filter)"
            )
            fetch_operation = "fetch_streaming"
        # No filter
        elif query.order_by is not None:
            # orderBy requires ALL records before sorting, then limit is applied
            estimated_records = base_entity_count
            fetch_api_calls = self._estimate_pages(base_entity_count)
            fetch_description = f"Fetch {query.from_} (paginated, full scan for sort)"
            fetch_operation = "fetch_streaming"
        else:
            # Can use limit directly for early termination
            estimated_records = self._estimate_primary_records(query)
            fetch_api_calls = self._estimate_pages(estimated_records)
            fetch_description = f"Fetch {query.from_} (paginated)"
            fetch_operation = "fetch"

        fetch_step = PlanStep(
            step_id=step_id,
            operation=fetch_operation,
            description=fetch_description,
            entity=query.from_,
            estimated_api_calls=fetch_api_calls,
            estimated_records=estimated_records,
            is_client_side=False,
        )

        # Step 2: Client-side filter (if WHERE clause and no pushdown)
        # For REQUIRES_PARENT entities, strip parent filter (already used to scope fetch)
        client_side_where = query.where
        if (
            entity_schema.fetch_strategy == FetchStrategy.REQUIRES_PARENT
            and entity_schema.parent_filter_field
            and query.where is not None
        ):
            client_side_where = _strip_parent_filter(query.where, entity_schema.parent_filter_field)

        # Affinity's search can answer conditions on list fields; which ones is decided when the
        # query runs (it needs the list's field metadata). The full WHERE still runs locally.
        if query.from_ == "listEntries" and client_side_where is not None:
            candidates = sorted(_field_paths(client_side_where))
            if candidates:
                fetch_step.filter_pushdown = True
                fetch_step.pushdown_filter = (
                    f"Affinity filters what it can of: {', '.join(candidates)} "
                    "(decided when the query runs)"
                )
                fetch_step.description += " (filtered by Affinity where possible)"
            else:
                warnings.append(
                    "Affinity can only filter list fields (fields.<name>); this query reads the "
                    "whole list and filters it locally."
                )

        steps.append(fetch_step)
        step_id += 1

        # Track required_rels for unbounded query detection
        filter_required_rels: set[str] = set()
        if client_side_where is not None:
            # Check if filter requires relationship data (quantifiers, exists, _count)
            required_rels = requires_relationship_data(client_side_where)
            filter_required_rels = required_rels  # Store for unbounded check later
            filter_api_calls = 0
            filter_warnings: list[str] = []

            # Check for lazy loading optimization opportunity
            cheap_filter, expensive_filter = partition_where(client_side_where, query.from_)
            has_lazy_loading = cheap_filter is not None and expensive_filter is not None

            if required_rels and entity_schema:
                # For quantifier/exists/_count filters, we must fetch relationship data
                # for filtered records. With lazy loading, we pre-filter first to reduce N+1.
                base_estimate = ESTIMATED_ENTITY_COUNTS.get(query.from_, DEFAULT_ENTITY_COUNT)

                if has_lazy_loading:
                    # Lazy loading: cheap filter runs first, reducing records before N+1
                    # Estimate ~50% reduction from cheap filter (heuristic)
                    records_before_n_plus_1 = min(
                        int(base_estimate * 0.5),  # Post cheap-filter estimate
                        self.max_records,
                    )
                    recommendations.append(
                        f"Lazy loading enabled: cheap filters run first, "
                        f"reducing N+1 calls from ~{base_estimate} to ~{records_before_n_plus_1}"
                    )
                else:
                    # No lazy loading: all records need N+1 before filtering
                    records_before_n_plus_1 = min(base_estimate, self.max_records)

                # Estimate N+1 API calls for each required relationship
                for rel_ref in required_rels:
                    # Try direct relationship name first
                    rel_name = rel_ref
                    if rel_ref not in (entity_schema.relationships or {}):
                        # Try to find by target entity (for exists_ clauses)
                        rel_name = find_relationship_by_target(entity_schema, rel_ref) or rel_ref

                    # Each record requires 1 API call per relationship
                    rel_calls = records_before_n_plus_1
                    filter_api_calls += rel_calls
                    filter_warnings.append(
                        f"Pre-fetching {rel_name} requires ~{rel_calls} API calls "
                        f"(1 per record for quantifier/exists/_count filter)"
                    )

            filter_description = self._describe_where(client_side_where)
            if has_lazy_loading:
                filter_description += " [lazy loading]"

            filter_step = PlanStep(
                step_id=step_id,
                operation="filter",
                description=filter_description,
                entity=query.from_,
                estimated_api_calls=filter_api_calls,
                estimated_records=self._estimate_filtered_records(
                    estimated_records, client_side_where
                ),
                is_client_side=filter_api_calls == 0,  # Only client-side if no API calls
                depends_on=[0],
                warnings=filter_warnings,
            )
            steps.append(filter_step)
            step_id += 1

            # Update estimated records after filter
            estimated_records = filter_step.estimated_records or estimated_records

        # Fix: For include/expand estimation, use limit when both limit and filter exist
        # With streaming + early termination, we stop at limit matches.
        # Includes run on those limit records, not the selectivity-reduced estimate.
        # Example: limit=5, filter=email contains "@acme.com" → expect 5 include calls
        # (not 5 * 0.5 = 2 from selectivity heuristic)
        include_estimate = query.limit if query.limit and query.where else estimated_records

        # Step 3: Includes (N+1 API calls)
        # Note: query.include is now dict[str, IncludeConfig] after normalization
        if query.include is not None:
            for include_path in query.include:
                # Validate relationship exists
                rel = get_relationship(query.from_, include_path)
                if rel is None:
                    # Get available relationships for helpful error message
                    entity_schema = get_entity_schema(query.from_)
                    available = (
                        sorted(entity_schema.relationships.keys())
                        if entity_schema and entity_schema.relationships
                        else []
                    )
                    if available:
                        raise QueryValidationError(
                            f"Unknown relationship '{include_path}' for entity '{query.from_}'. "
                            f"Available: {', '.join(available)}",
                            field="include",
                        )
                    else:
                        raise QueryValidationError(
                            f"Entity '{query.from_}' does not support includes",
                            field="include",
                        )

                include_calls = self._estimate_include_calls(include_estimate, include_path, rel)
                include_step = PlanStep(
                    step_id=step_id,
                    operation="include",
                    description=f"Include {include_path} (N+1 API calls)",
                    entity=query.from_,
                    relationship=include_path,
                    estimated_api_calls=include_calls,
                    is_client_side=False,
                    depends_on=[step_id - 1],  # Depends on previous step
                )

                if rel.requires_n_plus_1:
                    include_step.warnings.append(
                        f"Fetching {include_path} requires {include_calls} API calls "
                        f"({include_estimate} records x 1 call each)"
                    )

                steps.append(include_step)
                step_id += 1

        # Step 3b: Expansions (N+1 API calls for computed data)
        if query.expand is not None:
            for expansion in query.expand:
                expansion_def = EXPANSION_REGISTRY.get(expansion)
                if expansion_def is None:
                    raise QueryValidationError(
                        f"Unknown expansion '{expansion}'. "
                        f"Available: {', '.join(EXPANSION_REGISTRY.keys())}",
                        field="expand",
                    )

                # Check if entity supports this expansion
                # For listEntries, check is done at runtime based on entityType
                if (
                    query.from_ != "listEntries"
                    and query.from_ not in expansion_def.supported_entities
                ):
                    raise QueryValidationError(
                        f"Expansion '{expansion}' not supported for '{query.from_}'. "
                        f"Supported entities: {', '.join(expansion_def.supported_entities)}",
                        field="expand",
                    )

                expand_calls = include_estimate  # N+1 API calls
                expand_step = PlanStep(
                    step_id=step_id,
                    operation="expand",
                    description=f"Expand {expansion} (N+1 API calls)",
                    entity=query.from_,
                    expansion=expansion,
                    estimated_api_calls=expand_calls,
                    is_client_side=False,
                    depends_on=[step_id - 1],
                )

                expand_step.warnings.append(
                    f"Fetching {expansion} requires {expand_calls} API calls "
                    f"({include_estimate} records x 1 call each)"
                )

                steps.append(expand_step)
                step_id += 1

        # Step 4: Aggregation (if applicable)
        if query.aggregate is not None:
            agg_step = PlanStep(
                step_id=step_id,
                operation="aggregate",
                description=f"Compute aggregates: {', '.join(query.aggregate.keys())}",
                entity=query.from_,
                estimated_api_calls=0,
                is_client_side=True,
                depends_on=[step_id - 1],
            )
            steps.append(agg_step)
            step_id += 1

        # Step 5: Sort (if orderBy)
        if query.order_by is not None:
            sort_fields = [ob.field or "expression" for ob in query.order_by]
            sort_step = PlanStep(
                step_id=step_id,
                operation="sort",
                description=f"Sort by: {', '.join(sort_fields)}",
                entity=query.from_,
                estimated_api_calls=0,
                is_client_side=True,
                depends_on=[step_id - 1],
            )
            steps.append(sort_step)
            step_id += 1

        # Step 6: Limit (if specified)
        if query.limit is not None:
            limit_step = PlanStep(
                step_id=step_id,
                operation="limit",
                description=f"Take first {query.limit} results",
                entity=query.from_,
                estimated_api_calls=0,
                is_client_side=True,
                depends_on=[step_id - 1],
            )
            steps.append(limit_step)
            step_id += 1

        # Calculate totals
        total_api_calls: int | str = sum(s.estimated_api_calls for s in steps)
        estimated_fetched = steps[0].estimated_records

        # Check for unbounded quantifier queries
        requires_explicit_max_records = False
        if filter_required_rels:
            unbounded_estimate, unbounded_warnings, requires_explicit = (
                self._check_unbounded_quantifier(query, filter_required_rels)
            )
            if requires_explicit:
                total_api_calls = unbounded_estimate  # "UNBOUNDED"
                requires_explicit_max_records = True
            warnings.extend(unbounded_warnings)

        # Generate warnings and recommendations
        has_expensive = (
            isinstance(total_api_calls, int) and total_api_calls >= EXPENSIVE_OPERATION_THRESHOLD
        )
        requires_full_scan = not fetch_step.filter_pushdown and client_side_where is not None

        if (
            isinstance(total_api_calls, int)
            and total_api_calls >= VERY_EXPENSIVE_OPERATION_THRESHOLD
        ):
            warnings.append(
                f"This query will make approximately {total_api_calls} API calls. "
                "Consider adding filters or reducing the scope."
            )

        if estimated_fetched and estimated_fetched > MAX_RECORDS_WARNING_THRESHOLD:
            recommendations.append(
                f"Query may fetch up to {estimated_fetched} records. "
                "Use --dry-run to preview before executing."
            )

        if requires_full_scan:
            recommendations.append(
                "Query requires client-side filtering. For better performance, "
                "consider using saved views or list export with --filter."
            )

        # Estimate memory
        estimated_memory = None
        if estimated_fetched:
            estimated_memory = (estimated_fetched * BYTES_PER_RECORD) / (1024 * 1024)

        return ExecutionPlan(
            query=query,
            steps=steps,
            total_api_calls=total_api_calls,
            estimated_records_fetched=estimated_fetched,
            estimated_memory_mb=estimated_memory,
            warnings=warnings,
            recommendations=recommendations,
            has_expensive_operations=has_expensive,
            requires_full_scan=requires_full_scan,
            version=query.version or "1.0",
            requires_explicit_max_records=requires_explicit_max_records,
        )

    def get_execution_levels(self, plan: ExecutionPlan) -> list[list[PlanStep]]:
        """Group steps by execution level using topological sort.

        Steps in the same level can be executed in parallel.

        Args:
            plan: Execution plan

        Returns:
            List of levels, each containing steps that can run in parallel
        """
        ts: TopologicalSorter[int] = TopologicalSorter()
        step_map = {s.step_id: s for s in plan.steps}

        for step in plan.steps:
            ts.add(step.step_id, *step.depends_on)

        levels: list[list[PlanStep]] = []
        ts.prepare()

        while ts.is_active():
            ready_ids = list(ts.get_ready())
            levels.append([step_map[i] for i in ready_ids])
            for node_id in ready_ids:
                ts.done(node_id)

        return levels

    def _estimate_primary_records(self, query: Query) -> int:
        """Estimate number of records for primary entity."""
        base_estimate = ESTIMATED_ENTITY_COUNTS.get(query.from_, DEFAULT_ENTITY_COUNT)

        # If limit is set, use it as upper bound
        if query.limit is not None:
            return min(base_estimate, query.limit)

        return base_estimate

    def _estimate_pages(self, records: int, page_size: int = 100) -> int:
        """Estimate number of API pages needed."""
        return max(1, (records + page_size - 1) // page_size)

    def _estimate_filtered_records(self, total: int, where: WhereClause) -> int:
        """Estimate records remaining after client-side filter.

        This is a rough heuristic - actual results vary widely.
        """
        # Simple heuristic: each condition reduces by ~50%
        conditions = self._count_conditions(where)
        reduction = 0.5**conditions
        return max(1, int(total * reduction))

    def _count_conditions(self, where: WhereClause) -> int:
        """Count number of filter conditions."""
        count = 0

        # Single condition
        if where.op is not None:
            count = 1

        # Compound conditions
        if where.and_ is not None:
            count += sum(self._count_conditions(c) for c in where.and_)
        if where.or_ is not None:
            count += sum(self._count_conditions(c) for c in where.or_)
        if where.not_ is not None:
            count += self._count_conditions(where.not_)

        return count

    def _estimate_include_calls(
        self, records: int, _include_path: str, rel: RelationshipDef
    ) -> int:
        """Estimate API calls for an include operation."""
        if not rel.requires_n_plus_1:
            # Global service: single filtered call
            return 1

        # N+1: one call per record
        return records

    def _describe_where(self, where: WhereClause) -> str:
        """Generate human-readable description of WHERE clause."""
        if where.op is not None:
            path = where.path or "expression"
            # Unary operators don't need a value
            if where.op in ("is_null", "is_not_null"):
                return f"Client-side filter: {path} {where.op}"
            return f"Client-side filter: {path} {where.op} {where.value!r}"

        if where.and_ is not None:
            return f"Client-side filter: {len(where.and_)} conditions (AND)"

        if where.or_ is not None:
            return f"Client-side filter: {len(where.or_)} conditions (OR)"

        if where.not_ is not None:
            return "Client-side filter: NOT condition"

        return "Client-side filter"

    def _check_unbounded_quantifier(
        self, query: Query, required_rels: set[str]
    ) -> tuple[str | int, list[str], bool]:
        """Generate warnings and estimate for unbounded quantifier queries.

        Args:
            query: The query being planned
            required_rels: Set of relationships required by quantifier filters

        Returns:
            (estimate, warnings, requires_explicit) where:
            - estimate is "UNBOUNDED" (str) or an integer
            - warnings is list of warning messages
            - requires_explicit is True if query needs explicit --max-records
        """
        warnings: list[str] = []

        if query.from_ in UNBOUNDED_ENTITIES and required_rels:
            # Don't fake an estimate - be honest
            warnings.append(
                f"⚠️  UNBOUNDED: '{query.from_}' count unknown. "
                f"Could be 10K-100K+ API calls. "
                f"Use --max-records or start from listEntries."
            )
            return ("UNBOUNDED", warnings, True)

        # Bounded entity - max_records is the upper bound for N+1 calls
        # Each record requires one relationship fetch per required relationship
        estimated_calls = self.max_records * len(required_rels)
        return (estimated_calls, warnings, False)


def create_planner(
    *,
    max_records: int = 10000,
    concurrency: int = 10,
) -> QueryPlanner:
    """Create a query planner with configuration.

    Args:
        max_records: Maximum records safety limit
        concurrency: Concurrency level for N+1 operations

    Returns:
        Configured QueryPlanner
    """
    return QueryPlanner(max_records=max_records, concurrency=concurrency)
