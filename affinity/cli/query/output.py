"""Output formatters for query results.

Formats query results as JSON, JSONL, Markdown, TOON, CSV, or table.
This module is CLI-only and NOT part of the public SDK API.

Supported output formats:
- JSON: Full structure with data, included, pagination, meta
- JSONL: One JSON object per line (data rows only)
- Markdown: GitHub-flavored markdown table + pagination footer
- TOON: Full envelope (token-efficient format)
- CSV: Comma-separated values (data rows only)
- Table: Rich terminal tables
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from rich.console import Console

from ..formatters import (
    OutputFormat,
    format_data,
    format_jsonl,
    format_markdown,
    format_toon_envelope,
)
from .models import ExecutionPlan, QueryResult

logger = logging.getLogger(__name__)

# =============================================================================
# Include Style Type
# =============================================================================

IncludeStyle = Literal["inline", "separate", "ids-only"]


# =============================================================================
# Inline Expansion Functions
# =============================================================================


def _display_value(
    record: dict[str, Any] | None,
    display_fields: list[str] | None = None,
) -> str:
    """Extract display-friendly value from included record.

    Args:
        record: The included record to extract display value from
        display_fields: Custom field priority list from include config.
            If provided, tries these fields in order.
            If None, uses default priority: name → firstName lastName → title → email → id

    Returns:
        Human-readable display string for the record
    """
    if record is None:
        return "<unknown>"

    # If custom display fields specified, use them
    if display_fields:
        values = []
        for field in display_fields:
            val = record.get(field)
            if val is not None and val != "":
                values.append(str(val))
        if values:
            return " ".join(values)
        # Fall through to default if no custom fields have values

    # Try name first (companies, opportunities)
    if record.get("name"):
        return str(record["name"])

    # Try firstName + lastName for persons
    if record.get("firstName"):
        first = record["firstName"]
        last = record.get("lastName", "")
        return f"{first} {last}".strip()

    # Fallback chain
    for field in ("title", "email"):
        if record.get(field):
            return str(record[field])

    # Last resort: id or unknown
    if record.get("id"):
        return f"<unknown> ({record['id']})"
    return "<unknown>"


def expand_includes(
    data: list[dict[str, Any]],
    included_by_parent: dict[str, dict[int, list[dict[str, Any]]]] | None,
    include_configs: dict[str, Any] | None = None,
    source_entity: str | None = None,
) -> list[dict[str, Any]]:
    """Merge included data into parent records with inline display values.

    Adds new columns named "included.{relationship}" containing human-readable
    values extracted from the included records.

    Display field priority:
    1. Custom display fields from include_configs (user-specified in query)
    2. Default display_fields from schema registry (per-entity defaults)
    3. _display_value() fallback: name → firstName lastName → title → email → id

    Args:
        data: List of parent records
        included_by_parent: Mapping of {rel_name: {parent_id: [related_records]}}
        include_configs: Optional mapping of {rel_name: IncludeConfig} for custom display fields
        source_entity: Optional source entity name (e.g., "persons") for schema lookup

    Returns:
        New list of records with included data merged as "included.{rel}" columns
    """
    from .schema import get_relationship

    if not included_by_parent:
        return data

    expanded = []
    for row in data:
        row = dict(row)  # Copy to avoid mutation
        parent_id = row.get("id")

        for rel_name, parent_map in included_by_parent.items():
            related = parent_map.get(parent_id, []) if parent_id is not None else []
            # Column name: included.companies, included.persons, etc.
            col_name = f"included.{rel_name}"

            # Get display fields in priority order:
            # 1. Custom from include_configs (user-specified)
            # 2. Default from schema registry
            display_fields: list[str] | None = None

            # Check custom config first
            if include_configs:
                config = include_configs.get(rel_name)
                if config is not None:
                    # Handle both IncludeConfig objects and dicts
                    if hasattr(config, "display"):
                        display_fields = config.display
                    elif isinstance(config, dict):
                        display_fields = config.get("display")

            # Fall back to schema defaults if no custom fields
            if display_fields is None and source_entity:
                rel_def = get_relationship(source_entity, rel_name)
                if rel_def and rel_def.display_fields:
                    display_fields = list(rel_def.display_fields)

            row[col_name] = [_display_value(r, display_fields) for r in related]

        expanded.append(row)
    return expanded


# =============================================================================
# JSON Output
# =============================================================================


def format_json(
    result: QueryResult,
    *,
    pretty: bool = False,
    include_meta: bool = False,
) -> str:
    """Format query result as JSON.

    Args:
        result: Query result
        pretty: If True, pretty-print with indentation
        include_meta: If True, include metadata in output

    Returns:
        JSON string
    """
    output: dict[str, Any] = {"data": result.data}

    if result.included:
        output["included"] = result.included

    if include_meta:
        meta: dict[str, Any] = {}
        # Add standardized summary (using camelCase aliases for consistency)
        if result.summary:
            meta["summary"] = result.summary.model_dump(by_alias=True, exclude_none=True)
        # Add additional execution metadata
        if result.meta:
            meta.update(result.meta)
        output["meta"] = meta

    if result.pagination:
        output["pagination"] = result.pagination

    if result.warnings:
        output["warnings"] = list(result.warnings)

    indent = 2 if pretty else None
    return json.dumps(output, indent=indent, default=str)


# =============================================================================
# Field Flattening Functions
# =============================================================================


def _extract_display_value(value: Any) -> Any:
    """Extract human-readable display value from complex field values.

    Handles:
    - Lists: ["A", "B"] → "A, B"
    - Location dicts: {"city": "NYC", "country": "USA"} → "NYC, USA"
    - Primitives: pass through unchanged

    Note: Person/company references are normalized at the executor layer,
    so by the time we get here, they're already strings.
    """
    if value is None:
        return None
    if isinstance(value, list):
        # Join list values with comma
        if all(isinstance(x, (str, int, float, bool)) for x in value):
            return ", ".join(str(x) for x in value)
        # List of dicts - try to extract display values
        if all(isinstance(x, dict) for x in value):
            names = [_extract_display_value(x) for x in value]
            return ", ".join(str(n) for n in names if n)
        return value
    if isinstance(value, dict):
        # Location fields (not covered by executor normalization)
        if "city" in value or "country" in value:
            parts = [value.get("city"), value.get("state"), value.get("country")]
            return ", ".join(p for p in parts if p) or None
        # Interaction reference (email, meeting)
        if "type" in value and "sentAt" in value:
            return value.get("sentAt")
        if "type" in value and "startTime" in value:
            return value.get("startTime")
        # Generic dict - return as-is
        return value
    return value


def _flatten_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten record['fields'] dict to top-level 'fields.X' keys.

    Input:  {"id": 1, "fields": {"Status": "New", "Owner": "Jane Doe"}}
    Output: {"id": 1, "fields.Status": "New", "fields.Owner": "Jane Doe"}

    Edge case: {"id": 1, "fields": {}} → {"id": 1}  (no fields.* columns added)
    """
    result = {}
    for key, value in record.items():
        if key == "fields" and isinstance(value, dict):
            # Empty dict produces no fields.* columns (handled naturally by loop)
            for field_name, field_value in value.items():
                # Extract display value for complex field types
                display_value = _extract_display_value(field_value)
                result[f"fields.{field_name}"] = display_value
        else:
            result[key] = value
    return result


def _flatten_interaction_dates(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten interactionDates to top-level columns with consistent schema.

    ALWAYS produces the canonical 8 columns when interactionDates key is present,
    ensuring consistent schema across all records regardless of which interaction
    types have data. This is critical for TOON format which uses the first record's
    keys as column headers.

    Input:  {"id": 1, "interactionDates": {"lastMeeting": {"date": "2026-01-10", "daysSince": 7}}}
    Output: {"id": 1, "lastMeeting": "2026-01-10", "lastMeetingDaysSince": 7,
             "nextMeeting": None, "nextMeetingDaysUntil": None, ...}
    """
    # Canonical columns - must match interaction_utils.py definitions
    CANONICAL_COLUMNS: dict[str, str] = {
        "lastMeeting": "DaysSince",
        "nextMeeting": "DaysUntil",
        "lastEmail": "DaysSince",
        "lastInteraction": "DaysSince",
    }

    result: dict[str, Any] = {}
    for key, value in record.items():
        if key == "interactionDates":
            # Initialize ALL canonical columns to None (guarantees schema consistency)
            for col, suffix in CANONICAL_COLUMNS.items():
                result[col] = None
                result[f"{col}{suffix}"] = None

            # Overwrite with actual data if present
            if isinstance(value, dict) and value:
                for interaction_type, interaction_data in value.items():
                    if isinstance(interaction_data, dict):
                        if "date" in interaction_data:
                            result[interaction_type] = interaction_data["date"]
                        if "daysSince" in interaction_data:
                            result[f"{interaction_type}DaysSince"] = interaction_data["daysSince"]
                        if "daysUntil" in interaction_data:
                            result[f"{interaction_type}DaysUntil"] = interaction_data["daysUntil"]
                    else:
                        result[interaction_type] = interaction_data
            # Note: interactionDates key is NOT copied to result (flattened away)
        else:
            result[key] = value
    return result


def _apply_explicit_flattening(
    data: list[dict[str, Any]],
    explicit_select: list[str] | None,
    explicit_expand: list[str] | None,
) -> list[dict[str, Any]]:
    """Apply flattening for explicitly-selected nested structures.

    Only flattens when user explicitly requested the data via select or expand.
    This ensures default behavior (hiding complex nested columns) is preserved
    while showing data the user explicitly asked for.
    """
    if not data:
        return data

    # Check if fields were explicitly selected
    flatten_fields = False
    if explicit_select:
        flatten_fields = any(s == "fields.*" or s.startswith("fields.") for s in explicit_select)

    # Check if interactionDates was explicitly expanded
    flatten_interactions = False
    if explicit_expand:
        flatten_interactions = "interactionDates" in explicit_expand

    if not flatten_fields and not flatten_interactions:
        return data

    result = []
    for record in data:
        row = dict(record)
        if flatten_fields:
            row = _flatten_fields(row)
        if flatten_interactions:
            row = _flatten_interaction_dates(row)
        result.append(row)

    return result


def _get_excluded_columns(
    explicit_select: list[str] | None,
    explicit_expand: list[str] | None,
) -> frozenset[str]:
    """Get columns to exclude, respecting explicit selections.

    Returns a modified set of excluded columns based on what was explicitly selected.
    If fields were explicitly selected, they've been flattened to fields.X columns.
    If interactionDates was explicitly expanded, it's been flattened to individual columns.
    """
    # Start with default exclusions
    excluded = set(_EXCLUDED_TABLE_COLUMNS)

    # If fields were explicitly selected, don't exclude fields
    # (they're now flattened to fields.X columns, so "fields" key doesn't exist anyway)
    if explicit_select and any(s == "fields.*" or s.startswith("fields.") for s in explicit_select):
        excluded.discard("fields")

    # If interactionDates was explicitly expanded, don't exclude interaction_dates
    # (they're now flattened to individual columns)
    if explicit_expand and "interactionDates" in explicit_expand:
        excluded.discard("interaction_dates")
        excluded.discard("interactionDates")

    return frozenset(excluded)


# =============================================================================
# Table Output (Rich Table)
# =============================================================================

# Columns to exclude from table output by default (following CLI conventions)
# These are complex nested structures that don't display well in tables
# Use --json to see full data including these columns
_EXCLUDED_TABLE_COLUMNS = frozenset(
    {
        "fields",  # Custom field values - use --json or entity get --all-fields
        "interaction_dates",  # Complex nested dates
        "list_entries",  # List entry associations
        "interactions",  # Null unless explicitly loaded
        "company_ids",  # Empty array unless relationships loaded
        "opportunity_ids",  # Empty array unless relationships loaded
        "current_company_ids",  # Empty array unless relationships loaded
    }
)


def format_table(
    result: QueryResult,
    *,
    include_style: IncludeStyle = "inline",
) -> str:  # pragma: no cover
    """Format query result as a Rich table (matching CLI conventions).

    Args:
        result: Query result
        include_style: How to display included data:
            - "inline": Merge included data into parent rows as "included.{rel}" columns (default)
            - "separate": Show included data as separate tables after main table
            - "ids-only": Don't expand, show raw ID arrays

    Returns:
        Rendered table string
    """
    # Use the CLI's standard table rendering
    from ..render import _render_summary_footer, _table_from_rows

    if not result.data:
        return "No results."

    # Prepare data based on include style
    display_data: list[dict[str, Any]]
    if include_style == "inline" and result.included_by_parent:
        # Merge included data into parent rows, using custom display fields if specified
        display_data = expand_includes(
            result.data,
            result.included_by_parent,
            result.include_configs,
            result.source_entity,
        )
    else:
        display_data = result.data

    # Apply flattening for explicitly-selected nested structures (fields.*, interactionDates)
    display_data = _apply_explicit_flattening(
        display_data,
        explicit_select=result.explicit_select,
        explicit_expand=result.explicit_expand,
    )

    # Get excluded columns, respecting explicit selections
    excluded = _get_excluded_columns(
        explicit_select=result.explicit_select,
        explicit_expand=result.explicit_expand,
    )

    # Filter out excluded columns (following CLI convention - ls commands don't show fields)
    filtered_data = [{k: v for k, v in row.items() if k not in excluded} for row in display_data]

    # Build Rich table using CLI's standard function
    table, omitted = _table_from_rows(filtered_data)

    # Render to string
    console = Console(force_terminal=False, width=None)
    with console.capture() as capture:
        console.print(table)

    output = capture.get()

    # Add standardized summary footer
    footer_parts: list[str] = []
    if result.summary:
        footer = _render_summary_footer(result.summary)
        if footer:
            footer_parts.append(footer.plain)

    # Column omission notice
    if omitted > 0:
        footer_parts.append(f"({omitted} columns hidden — use --json for full data)")

    main_output = output + "\n".join(footer_parts)

    # Add included tables only for "separate" style (Option B display)
    if include_style == "separate":
        included_output = format_included_tables(result)
        if included_output:
            main_output += "\n\n" + included_output

    return main_output


def format_included_tables(result: QueryResult) -> str:  # pragma: no cover
    """Format included data as separate tables (Option B display).

    Renders each included relationship as a separate table section,
    allowing users to see full included data without inline expansion.

    Args:
        result: Query result with included data

    Returns:
        Rendered included tables string, or empty string if no included data
    """
    from ..render import _table_from_rows

    if not result.included:
        return ""

    sections: list[str] = []
    console = Console(force_terminal=False, width=None)

    for rel_name, records in result.included.items():
        if not records:
            continue

        # Filter out excluded columns for included tables too
        filtered_records = [
            {k: v for k, v in row.items() if k not in _EXCLUDED_TABLE_COLUMNS} for row in records
        ]

        if not filtered_records:
            continue

        # Build Rich table using CLI's standard function
        table, _omitted = _table_from_rows(filtered_records)
        table.title = f"Included: {rel_name}"

        with console.capture() as capture:
            console.print(table)

        section = capture.get()
        sections.append(section.rstrip())

    return "\n\n".join(sections)


# =============================================================================
# Dry-Run Output
# =============================================================================


def format_dry_run(plan: ExecutionPlan, *, verbose: bool = False) -> str:  # pragma: no cover
    """Format execution plan for dry-run output.

    Args:
        plan: Execution plan
        verbose: If True, show detailed API call breakdown

    Returns:
        Formatted plan string
    """
    lines: list[str] = []

    lines.append("Query Execution Plan")
    lines.append("=" * 40)
    lines.append("")

    # Query summary
    lines.append("Query:")
    lines.append(f"  $version: {plan.version}")
    lines.append(f"  from: {plan.query.from_}")

    if plan.query.where is not None:
        lines.append("  where: <filter condition>")

    if plan.query.include is not None:
        lines.append(f"  include: {', '.join(plan.query.include.keys())}")

    if plan.query.order_by is not None:
        order_fields = [ob.field or "expr" for ob in plan.query.order_by]
        lines.append(f"  orderBy: {', '.join(order_fields)}")

    if plan.query.limit is not None:
        lines.append(f"  limit: {plan.query.limit}")

    lines.append("")

    # Execution summary
    lines.append("Execution Summary:")
    lines.append(f"  Total steps: {len(plan.steps)}")
    lines.append(f"  Estimated API calls: {plan.total_api_calls}")

    if plan.estimated_records_fetched is not None:
        lines.append(f"  Estimated records: {plan.estimated_records_fetched}")

    if plan.estimated_memory_mb is not None:
        lines.append(f"  Estimated memory: {plan.estimated_memory_mb:.1f} MB")

    lines.append("")

    # Steps
    lines.append("Execution Steps:")
    for step in plan.steps:
        status = "[client]" if step.is_client_side else f"[~{step.estimated_api_calls} calls]"
        lines.append(f"  {step.step_id}. {step.description} {status}")

        if verbose:
            if step.depends_on:
                lines.append(f"      depends on: step {', '.join(map(str, step.depends_on))}")
            if step.filter_pushdown:
                lines.append(f"      pushdown: {step.pushdown_filter}")
            for warning in step.warnings:
                lines.append(f"      [!] {warning}")

    lines.append("")

    # Warnings
    if plan.warnings:
        lines.append("Warnings:")
        for warning in plan.warnings:
            lines.append(f"  [!] {warning}")
        lines.append("")

    # Recommendations
    if plan.recommendations:
        lines.append("Recommendations:")
        for rec in plan.recommendations:
            lines.append(f"  - {rec}")
        lines.append("")

    # Assumptions (always show in verbose, or when includes present)
    has_includes = bool(plan.query.include)
    if verbose or has_includes:
        lines.append("Assumptions:")
        if plan.query.limit is not None:
            lines.append(f"  - Record count: {plan.query.limit} (from limit)")
        else:
            lines.append(f"  - Record count: {plan.estimated_records_fetched} (heuristic estimate)")
        if plan.query.where is not None:
            lines.append("  - Filter selectivity: 50% (heuristic)")
        if has_includes:
            lines.append("  - Include calls: 1 API call per parent record (N+1)")
        lines.append("  - Actual counts may vary; use --dry-run to preview before execution")
        lines.append("")

    return "\n".join(lines)


def format_dry_run_json(plan: ExecutionPlan) -> str:
    """Format execution plan as JSON for MCP.

    Args:
        plan: Execution plan

    Returns:
        JSON string
    """
    # Build execution section with optional note for unbounded queries
    execution: dict[str, Any] = {
        "totalSteps": len(plan.steps),
        "estimatedApiCalls": plan.total_api_calls,
        "estimatedRecords": plan.estimated_records_fetched,
        "estimatedMemoryMb": plan.estimated_memory_mb,
        "requiresExplicitMaxRecords": plan.requires_explicit_max_records,
    }

    # Add explanatory note for unbounded queries
    if plan.total_api_calls == "UNBOUNDED":
        execution["estimatedApiCallsNote"] = "Could be 10K-100K+ based on database size"

    # Add warning for high API call counts (helps LLMs notice expensive operations)
    if isinstance(plan.total_api_calls, int) and plan.total_api_calls > 50:
        execution["warning"] = (
            f"High API call count ({plan.total_api_calls}). "
            "Consider reducing limit or removing expand/include."
        )

    output = {
        "version": plan.version,
        "query": {
            "from": plan.query.from_,
            "where": plan.query.where.model_dump() if plan.query.where else None,
            "include": (
                {
                    name: cfg.model_dump(exclude_none=True)
                    for name, cfg in plan.query.include.items()
                }
                if plan.query.include
                else None
            ),
            "orderBy": [ob.model_dump() for ob in plan.query.order_by]
            if plan.query.order_by
            else None,
            "limit": plan.query.limit,
        },
        "execution": execution,
        "steps": [
            {
                "stepId": step.step_id,
                "operation": step.operation,
                "description": step.description,
                "estimatedApiCalls": step.estimated_api_calls,
                "isClientSide": step.is_client_side,
                "dependsOn": step.depends_on,
                "warnings": step.warnings,
            }
            for step in plan.steps
        ],
        "warnings": plan.warnings,
        "recommendations": plan.recommendations,
        "hasExpensiveOperations": plan.has_expensive_operations,
        "requiresFullScan": plan.requires_full_scan,
    }

    return json.dumps(output, indent=2, default=str)


# =============================================================================
# Markdown Pagination Footer
# =============================================================================


def _format_markdown_footer(count: int, pagination: dict[str, Any] | None) -> str | None:
    """Generate pagination footer for markdown output.

    Args:
        count: Number of rows in the output data
        pagination: Pagination dict with hasMore (bool), total (int|None)

    Note: `count` reflects rows returned after any limit/filter applied.
    `total` is the total matching records (may differ if limit < total).

    Truth table:
    | count | total | hasMore | Output |
    |-------|-------|---------|--------|
    | 0 | 0 | false | `> _No results_` |
    | 0 | N/A | false | None (no footer) |
    | 0 | any | true | `> _more results available (none shown)_` |
    | N | N | false | None (complete results) |
    | N | M>N | false | `> _N of M results_` |
    | N | M | true | `> _N of M results | more available_` |
    | N | N/A | true | `> _N results | more available_` |
    """
    if not pagination:
        return None

    total = pagination.get("total")
    has_more = pagination.get("hasMore", False)

    # Edge cases - handle total=0 explicitly first (most specific)
    # Note: total=0 is distinct from total=None (unknown)
    if pagination.get("total") == 0:
        return "> _No results_"
    # Empty data with unknown total and no more pages - no footer needed
    if count == 0 and not has_more and total is None:
        return None
    if count == 0 and has_more:
        return "> _more results available (none shown due to limit)_"

    # Only hide footer if we have exactly all results AND no more available.
    # Note: count < total is valid when limit is applied.
    if total is not None and count == total and not has_more:
        return None  # Complete results, no footer needed

    # Build footer - always show if hasMore or if count < total
    if total and has_more:
        return f"> _{count} of {total} results | more available_"
    elif total and count < total:
        return f"> _{count} of {total} results_"  # Limited, but no more pages
    elif has_more:
        return f"> _{count} results | more available_"
    return None


# =============================================================================
# Truncation Support
# =============================================================================

# Bytes reserved for truncation metadata section
# Calculation: "truncation:\n  rowsShown: 999999\n  rowsOmitted: 999999\n" ≈ 55 bytes
# Doubled for safety buffer → 100 bytes
_TRUNCATION_SECTION_RESERVE = 100

# Bytes reserved for header line when envelope is very large
# Calculation: "data[0]{field1,field2,...,field20}:\n" ≈ 40 bytes
# Rounded up for safety → 50 bytes
_HEADER_RESERVE = 50

# Bytes reserved for markdown truncation footer
# Calculation: "\n\n> _...truncated (999999 rows shown)_" ≈ 42 bytes
# Rounded up for safety → 50 bytes
_MARKDOWN_FOOTER_RESERVE = 50

# Regex for parsing TOON array header: "name[count]{field1,field2,...}:"
# Requires named arrays (data[N], included_companies[N]) - assumes Phase 2 format change complete
_TOON_ARRAY_HEADER_RE = re.compile(r"^(\w+)\[(\d+)\]\{([^}]*)\}:$")


def _parse_toon_header(header: str) -> tuple[str, int, str] | None:
    """Parse TOON array header, returning (name, count, fieldnames) or None."""
    match = _TOON_ARRAY_HEADER_RE.match(header)
    if match:
        return match.group(1), int(match.group(2)), match.group(3)
    return None


def truncate_toon_output(content: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate TOON output, preserving envelope structure.

    Returns (truncated_content, was_truncated).
    """
    if len(content.encode()) <= max_bytes:
        return content, False

    lines = content.split("\n")

    # Find data section boundaries using regex
    data_start = None
    parsed_header = None
    for i, line in enumerate(lines):
        parsed = _parse_toon_header(line)
        if parsed and parsed[0] == "data":
            data_start = i
            parsed_header = parsed
            break

    if data_start is None or parsed_header is None:
        # No valid data section - could be old anonymous format [N]{...}:
        # Log warning to help diagnose; fall back to line-based truncation
        logger.warning(
            "TOON truncation requires named array format (data[N]{...}:). "
            "Old anonymous format [N]{...}: is not supported. Falling back to line truncation."
        )
        # Truncate to last complete line (avoid corrupting mid-row)
        truncated = content[:max_bytes]
        last_newline = truncated.rfind("\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]
        return truncated, True

    _, original_count, fieldnames = parsed_header

    # Find end of data section (first non-indented line after header)
    data_end = next(
        (
            i
            for i, line in enumerate(lines[data_start + 1 :], data_start + 1)
            if line and not line.startswith("  ")
        ),
        len(lines),
    )

    # Preserve envelope (everything after data section)
    envelope_lines = lines[data_end:]
    envelope_size = sum(len(line.encode()) + 1 for line in envelope_lines)

    # Edge case: envelope alone exceeds max_bytes - keep as much envelope as fits
    if envelope_size >= max_bytes - _TRUNCATION_SECTION_RESERVE:
        header = f"data[0]{{{fieldnames}}}:\n"
        trunc_str = "truncation:\n  rowsShown: 0\n  reason: envelope exceeds limit\n"

        # Keep as much envelope as fits
        remaining = max_bytes - len(header.encode()) - len(trunc_str.encode()) - _HEADER_RESERVE
        kept_envelope: list[str] = []
        kept_size = 0
        for line in envelope_lines:
            line_size = len(line.encode()) + 1
            if kept_size + line_size > remaining:
                break
            kept_envelope.append(line)
            kept_size += line_size

        return header + trunc_str + "\n".join(kept_envelope), True

    # Calculate how many data rows we can keep
    available = max_bytes - envelope_size - _TRUNCATION_SECTION_RESERVE
    data_header = lines[data_start]

    kept_rows = []
    current_size = len(data_header.encode()) + 1
    for line in lines[data_start + 1 : data_end]:
        line_size = len(line.encode()) + 1
        if current_size + line_size > available:
            break
        kept_rows.append(line)
        current_size += line_size

    # Rebuild with updated count and native TOON truncation key
    rows_omitted = original_count - len(kept_rows)
    new_header = f"data[{len(kept_rows)}]{{{fieldnames}}}:"

    # Insert truncation section between data and envelope (native TOON key)
    truncation_section = [
        "truncation:",
        f"  rowsShown: {len(kept_rows)}",
        f"  rowsOmitted: {rows_omitted}",
    ]
    result_lines = [new_header, *kept_rows, *truncation_section, *envelope_lines]
    return "\n".join(result_lines), True


def truncate_markdown_output(
    content: str, max_bytes: int, *, original_total: int | None = None
) -> tuple[str, bool]:
    """Truncate markdown table, keeping header and preserving pagination context.

    Args:
        content: Markdown table content
        max_bytes: Maximum byte size
        original_total: Original total from pagination (preserved in truncation footer)
    """
    if len(content.encode()) <= max_bytes:
        return content, False

    lines = content.split("\n")

    # Defensive: ensure we have at least header + separator
    if len(lines) < 2:
        logger.warning("Markdown truncation: malformed input (fewer than 2 lines)")
        return content[:max_bytes], True

    # Defensive: verify header looks like a markdown table
    if not lines[0].startswith("|") or not lines[1].startswith("|"):
        logger.warning("Markdown truncation: input doesn't look like a markdown table")
        # Fall back to simple byte truncation
        truncated = content[:max_bytes]
        last_newline = truncated.rfind("\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]
        return truncated, True

    header = lines[:2]  # Header row + separator

    kept_rows = []
    current_size = sum(len(line.encode()) + 1 for line in header) + _MARKDOWN_FOOTER_RESERVE
    for line in lines[2:]:
        if not line.startswith("|"):
            continue  # Skip footer if present
        line_size = len(line.encode()) + 1
        if current_size + line_size > max_bytes:
            break
        kept_rows.append(line)
        current_size += line_size

    result = "\n".join(header + kept_rows)
    # Preserve original total in truncation footer when available
    if original_total is not None:
        result += f"\n\n> _...truncated ({len(kept_rows)} of {original_total} rows shown)_"
    else:
        result += f"\n\n> _...truncated ({len(kept_rows)} rows shown)_"
    return result, True


def truncate_jsonl_output(content: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate JSONL output, removing lines from end.

    Appends a final {"truncated": true} line to signal truncation.
    JSONL consumers process line-by-line and can handle this metadata line.
    """
    if len(content.encode()) <= max_bytes:
        return content, False

    # Defensive: handle empty content
    if not content.strip():
        return '{"truncated":true}', True

    lines = content.rstrip("\n").split("\n")

    # Defensive: verify lines look like JSON (start with { or [)
    valid_json_lines = [line for line in lines if line.strip().startswith(("{", "["))]
    if len(valid_json_lines) < len(lines) * 0.5:  # Less than 50% valid
        logger.warning("JSONL truncation: content doesn't look like valid JSONL")

    truncation_line = '{"truncated":true}'
    available = max_bytes - len(truncation_line.encode()) - 1  # -1 for newline

    kept_lines = []
    current_size = 0
    for line in lines:
        line_size = len(line.encode()) + 1  # +1 for newline
        if current_size + line_size > available:
            break
        kept_lines.append(line)
        current_size += line_size

    return "\n".join(kept_lines) + "\n" + truncation_line, True


def truncate_csv_output(content: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate CSV output, keeping header row.

    CSV format cannot include a truncation notice without breaking the format
    (any extra row would be parsed as data). Truncation is signaled only via
    the MCP response's `truncated: true` flag.
    """
    if len(content.encode()) <= max_bytes:
        return content, False

    lines = content.split("\n")

    # Defensive: handle empty or single-line content
    if not lines:
        logger.warning("CSV truncation: empty content")
        return content, True
    if len(lines) == 1:
        # Only header, no data rows to truncate
        return content[:max_bytes], True

    # Defensive: verify header looks like CSV (contains comma)
    header = lines[0]
    if "," not in header:
        logger.warning("CSV truncation: header doesn't contain comma, may not be valid CSV")

    header_size = len(header.encode()) + 1

    # Defensive: if header alone exceeds max, truncate header
    if header_size > max_bytes:
        logger.warning("CSV truncation: header exceeds max_bytes, truncating header")
        return header[:max_bytes], True

    kept_rows = []
    current_size = header_size
    for line in lines[1:]:
        if not line:  # Skip empty lines
            continue
        line_size = len(line.encode()) + 1
        if current_size + line_size > max_bytes:
            break
        kept_rows.append(line)
        current_size += line_size

    return "\n".join([header, *kept_rows]), True


def truncate_json_result(
    result: QueryResult,
    max_bytes: int,
    *,
    include_meta: bool = False,
) -> tuple[QueryResult, int, bool]:
    """Truncate QueryResult data for JSON output to fit within byte limit.

    Operates on the Python object BEFORE serialization to avoid precision loss
    from JSON parse/re-serialize round-trip. This is important because JSON
    number serialization can lose precision for large integers or floats.

    Args:
        result: QueryResult to potentially truncate
        max_bytes: Maximum byte size for serialized output
        include_meta: Whether metadata will be included in output

    Returns:
        Tuple of (result, items_kept, was_truncated)
        - result: QueryResult with data potentially truncated
        - items_kept: Number of data items kept (for cursor calculation)
        - was_truncated: True if truncation occurred
    """
    # First, check if truncation is needed
    test_output = format_json(result, pretty=False, include_meta=include_meta)
    if len(test_output.encode()) <= max_bytes:
        return result, len(result.data) if result.data else 0, False

    if not result.data:
        # No data to truncate - can't help (caller must handle)
        return result, 0, False

    original_data = result.data
    original_count = len(original_data)

    # Binary search for maximum items that fit
    lo, hi = 0, original_count
    best_fit = 0

    while lo <= hi:
        mid = (lo + hi) // 2
        result.data = original_data[:mid]
        test_output = format_json(result, pretty=False, include_meta=include_meta)

        if len(test_output.encode()) <= max_bytes:
            best_fit = mid
            lo = mid + 1
        else:
            hi = mid - 1

    # Edge case: can't fit even one item (envelope too large)
    if best_fit == 0:
        result.data = original_data  # Restore
        return result, original_count, False  # Can't truncate

    # Edge case: all items fit (shouldn't happen since we checked above)
    if best_fit >= original_count:
        result.data = original_data
        return result, original_count, False

    # Apply truncation
    result.data = original_data[:best_fit]

    # Final sanity check - binary search guarantees best_fit items fit,
    # but verify to catch any edge cases
    final_output = format_json(result, pretty=False, include_meta=include_meta)
    if len(final_output.encode()) > max_bytes:
        # This shouldn't happen given binary search, but be defensive
        logger.error(
            "JSON truncation: output %d bytes > max %d - binary search bug",
            len(final_output.encode()),
            max_bytes,
        )
        # Decrement by one as fallback
        result.data = original_data[: max(0, best_fit - 1)]
        best_fit = max(0, best_fit - 1)

    return result, best_fit, True


# =============================================================================
# Unified Format Output
# =============================================================================


def format_query_result(
    result: QueryResult,
    format: OutputFormat,
    *,
    pretty: bool = False,
    include_meta: bool = False,
) -> str:
    """Format query result with full structure support.

    Args:
        result: Query result to format
        format: Output format (json, jsonl, markdown, toon, csv, table)
        pretty: Pretty-print JSON output
        include_meta: Include metadata in JSON output

    Returns:
        Formatted string

    Note:
        - JSON: Full envelope (data, included, pagination, meta)
        - TOON: Full envelope (token-efficient format)
        - Markdown: Data table + pagination footer
        - CSV/JSONL: Data only (export formats - warn if losing included data)
    """
    if format == "json":
        # Full structure
        return format_json(result, pretty=pretty, include_meta=include_meta)

    if format == "jsonl":
        # Data rows only, one per line (export format)
        if result.included:
            logger.warning(
                "Included data omitted in %s output (use --output json to see included entities)",
                format,
            )
        return format_jsonl(result.data or [])

    if format == "toon":
        # Full envelope in token-efficient format
        # Apply flattening for explicitly-selected nested structures (fields.*, interactionDates)
        display_data = _apply_explicit_flattening(
            result.data or [],
            explicit_select=result.explicit_select,
            explicit_expand=result.explicit_expand,
        )
        first_row = display_data[0] if display_data else None
        fieldnames = list(first_row.keys()) if isinstance(first_row, dict) else []
        return format_toon_envelope(
            display_data,
            fieldnames,
            pagination=result.pagination,
            included=result.included,
        )

    if format == "markdown":
        # Data table + pagination footer
        # Apply flattening for explicitly-selected nested structures
        display_data = _apply_explicit_flattening(
            result.data or [],
            explicit_select=result.explicit_select,
            explicit_expand=result.explicit_expand,
        )
        first_row = display_data[0] if display_data else None
        fieldnames = list(first_row.keys()) if isinstance(first_row, dict) else []
        output = format_markdown(display_data, fieldnames)
        footer = _format_markdown_footer(len(result.data or []), result.pagination)
        if footer:
            output += f"\n\n{footer}"
        return output

    if format == "csv":
        # Data only (export format)
        if result.included:
            logger.warning(
                "Included data omitted in %s output (use --output json to see included entities)",
                format,
            )
        # Apply flattening for explicitly-selected nested structures
        display_data = _apply_explicit_flattening(
            result.data or [],
            explicit_select=result.explicit_select,
            explicit_expand=result.explicit_expand,
        )
        fieldnames = list(display_data[0].keys()) if display_data else []
        return format_data(display_data, format, fieldnames=fieldnames)

    if format == "table":
        return format_table(result)

    raise ValueError(f"Unknown format: {format}")


# =============================================================================
# Cursor Output (stderr NDJSON)
# =============================================================================


def emit_cursor_to_stderr(cursor: str, mode: str) -> None:
    """Emit cursor to stderr as NDJSON for MCP extraction.

    The cursor is output to stderr (not stdout) to keep it separate from
    the query results. MCP tool.sh extracts it via:
        jq 'select(.type == "cursor")'

    Args:
        cursor: Base64-encoded cursor string
        mode: Cursor mode ("streaming" or "full-fetch")
    """
    import sys

    cursor_obj = {
        "type": "cursor",
        "cursor": cursor,
        "mode": mode,
    }
    # Flush to ensure immediate delivery (Python buffers stderr when not a TTY)
    print(json.dumps(cursor_obj), file=sys.stderr, flush=True)


def insert_cursor_in_toon_truncation(output: str, cursor: str) -> str:
    """Insert cursor into TOON truncation section for human readability.

    The cursor is a debugging reference - the authoritative cursor is
    emitted to stderr as NDJSON. This adds it to the truncation section
    so humans can copy-paste it for manual CLI resumption.

    Args:
        output: TOON formatted output with truncation section
        cursor: Base64-encoded cursor string

    Returns:
        Output with cursor added to truncation section
    """
    import re

    # Find the truncation section and insert cursor after rowsOmitted
    # Pattern: truncation:\n  rowsShown: N\n  rowsOmitted: N
    pattern = r"(truncation:\n  rowsShown: \d+\n  rowsOmitted: \d+)"
    replacement = rf"\1\n  cursor: {cursor}"

    # Only replace if truncation section exists
    if "truncation:" in output:
        return re.sub(pattern, replacement, output)
    return output
