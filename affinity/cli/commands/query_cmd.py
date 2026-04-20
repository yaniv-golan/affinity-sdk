"""CLI query command.

Executes structured queries against Affinity data.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

from ..click_compat import RichCommand, click
from ..context import CLIContext
from ..decorators import category, progress_capable
from ..errors import CLIError
from ..options import csv_output_options, csv_suboption_callback

# =============================================================================
# CLI Command
# =============================================================================


@category("read")
@progress_capable
@click.command(name="query", cls=RichCommand)
@click.option(
    "--file",
    "-f",
    "file_path",
    type=click.Path(exists=True, path_type=Path),  # type: ignore[type-var]
    help="Read query from JSON file.",
)
@click.option(
    "--query",
    "query_str",
    type=str,
    help="Inline JSON query string.",
)
@click.option(
    "--query-version",
    type=str,
    help="Override $version in query (e.g., '1.0').",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show execution plan without running.",
)
@click.option(
    "--dry-run-verbose",
    is_flag=True,
    help="Show detailed plan with API call breakdown.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Require confirmation before expensive operations.",
)
@click.option(
    "--max-records",
    type=int,
    default=10000,
    show_default=True,
    help="Safety limit on total records fetched.",
)
@click.option(
    "--timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Overall timeout in seconds.",
)
@click.option(
    "--csv-bom",
    is_flag=True,
    help="Add UTF-8 BOM for Excel (use with redirection: --csv --csv-bom > file.csv).",
    callback=csv_suboption_callback,
    expose_value=True,
)
@click.option(
    "--include-meta",
    is_flag=True,
    help="Include execution metadata in output.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress progress output.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed progress.",
)
@click.option(
    "--max-output-bytes",
    type=int,
    default=None,
    help="Truncate output to max bytes (for MCP use). Exits with code 100 if truncated.",
)
@click.option(
    "--include-style",
    type=click.Choice(["inline", "separate", "ids-only"]),
    default="inline",
    help="How to display included data: inline (default), separate tables, or raw IDs.",
)
@click.option(
    "--cursor",
    "cursor_str",
    type=str,
    default=None,
    help="Resume from cursor (from previous truncated response).",
)
@csv_output_options
@click.pass_obj
def query_cmd(
    ctx: CLIContext,
    file_path: Path | None,
    query_str: str | None,
    query_version: str | None,
    dry_run: bool,
    dry_run_verbose: bool,
    confirm: bool,
    max_records: int,
    timeout: float,
    csv_bom: bool,
    include_meta: bool,
    quiet: bool,
    verbose: bool,
    max_output_bytes: int | None,
    include_style: str,
    cursor_str: str | None,
) -> None:
    """Execute a structured query against Affinity data.

    The query can be provided via --file, --query, or piped from stdin.

    \b
    Examples:
      # From file
      xaffinity query --file query.json

      # Inline JSON
      xaffinity query --query '{"from": "persons", "limit": 10}'

      # Dry-run to preview execution plan
      xaffinity query --file query.json --dry-run

      # CSV output (--csv is alias for --output csv)
      xaffinity query --file query.json --csv
      xaffinity query --file query.json --output csv

      # JSON output
      xaffinity query --file query.json --json
    """
    # Validate numeric options (Bug #35)
    if max_records <= 0:
        raise click.BadParameter("must be positive", param_hint="'--max-records'")
    if timeout <= 0:
        raise click.BadParameter("must be positive", param_hint="'--timeout'")
    if max_output_bytes is not None and max_output_bytes <= 0:
        raise click.BadParameter("must be positive", param_hint="'--max-output-bytes'")

    # Detect if --max-records was explicitly provided using Click's ParameterSource
    # (see person_cmds.py:908-914 for similar pattern in the codebase)
    max_records_explicit = False
    click_ctx = click.get_current_context(silent=True)
    if click_ctx is not None:
        get_source = getattr(cast(Any, click_ctx), "get_parameter_source", None)
        if callable(get_source):
            source_enum = getattr(cast(Any, click.core), "ParameterSource", None)
            default_source = getattr(source_enum, "DEFAULT", None) if source_enum else None
            actual_source = get_source("max_records")
            max_records_explicit = actual_source != default_source

    try:
        _query_cmd_impl(
            ctx=ctx,
            file_path=file_path,
            query_str=query_str,
            query_version=query_version,
            dry_run=dry_run,
            dry_run_verbose=dry_run_verbose,
            confirm=confirm,
            max_records=max_records,
            max_records_explicit=max_records_explicit,
            timeout=timeout,
            csv_flag=ctx.output == "csv",
            csv_bom=csv_bom,
            include_meta=include_meta,
            quiet=quiet,
            verbose=verbose,
            max_output_bytes=max_output_bytes,
            include_style=include_style,
            cursor_str=cursor_str,
        )
    except CLIError as e:
        # When --json/-o json, emit a structured error envelope on stdout so
        # callers (MCP, scripts) can parse the failure mode via error.type.
        if ctx.output == "json":
            import time as _time

            from ..context import build_result
            from ..results import CommandContext, ErrorInfo
            from ..runner import _emit_json

            # `profile` is optional and may be absent on test Mock contexts
            profile = getattr(ctx, "profile", None)
            if not isinstance(profile, (str, type(None))):
                profile = None

            result = build_result(
                ok=False,
                command=CommandContext(name="query"),
                started_at=_time.time(),
                data=None,
                warnings=[],
                profile=profile,
                rate_limit=None,
                error=ErrorInfo(
                    type=e.error_type,
                    message=e.message,
                    hint=e.hint,
                    details=e.details,
                ),
            )
            _emit_json(result)
            raise click.exceptions.Exit(e.exit_code) from None

        # Display error cleanly without traceback
        click.echo(f"Error: {e.message}", err=True)
        if e.hint:
            click.echo(f"Hint: {e.hint}", err=True)
        raise click.exceptions.Exit(e.exit_code) from None


def _query_cmd_impl(
    *,
    ctx: CLIContext,
    file_path: Path | None,
    query_str: str | None,
    query_version: str | None,
    dry_run: bool,
    dry_run_verbose: bool,
    confirm: bool,
    max_records: int,
    max_records_explicit: bool,
    timeout: float,
    csv_flag: bool,
    csv_bom: bool,
    include_meta: bool,
    quiet: bool,
    verbose: bool,
    max_output_bytes: int | None,
    include_style: str,
    cursor_str: str | None,
) -> None:
    """Internal implementation of query command."""
    from affinity.cli.constants import EXIT_TRUNCATED
    from affinity.cli.query import (
        QueryExecutionError,
        QueryInterruptedError,
        QueryParseError,
        QuerySafetyLimitError,
        QueryTimeoutError,
        QueryValidationError,
        create_planner,
        parse_query,
    )
    from affinity.cli.query.cursor import (
        CursorExpired,
        CursorPayload,
        CursorQueryMismatch,
        InvalidCursor,
        cleanup_cache,
        create_full_fetch_cursor,
        create_streaming_cursor,
        decode_cursor,
        encode_cursor,
        find_resume_position,
        hash_query,
        read_cache,
        validate_cursor,
        write_cache,
    )

    # Clean up expired cache files on startup (per design doc)
    cleanup_cache()
    from affinity.cli.query.executor import QueryExecutor
    from affinity.cli.query.output import (
        emit_cursor_to_stderr,
        format_dry_run,
        format_dry_run_json,
        format_json,
        format_query_result,
        format_table,
        insert_cursor_in_toon_truncation,
        truncate_csv_output,
        truncate_json_result,
        truncate_jsonl_output,
        truncate_markdown_output,
        truncate_toon_output,
    )
    from affinity.cli.query.progress import (
        RichQueryProgress,
        create_progress_callback,
        emit_cache_progress,
    )

    # Get query input
    query_dict = _get_query_input(file_path, query_str)

    # Parse and validate query
    try:
        parse_result = parse_query(query_dict, version_override=query_version)
    except (QueryParseError, QueryValidationError) as e:
        raise CLIError(
            f"Query validation failed: {e}",
            error_type="validation_error",
            exit_code=2,
        ) from None

    query = parse_result.query

    # Decode and validate cursor if provided
    cursor: CursorPayload | None = None
    output_format = ctx.output or "table"  # Default to table if not specified
    if cursor_str:
        try:
            cursor = decode_cursor(cursor_str)
            validate_cursor(cursor, query, output_format)
        except CursorExpired as e:
            raise CLIError(
                str(e), hint="Re-run the original query to get a fresh cursor."
            ) from None
        except CursorQueryMismatch as e:
            raise CLIError(str(e)) from None
        except InvalidCursor as e:
            raise CLIError(f"Invalid cursor: {e}") from None

    # Show parsing warnings
    if parse_result.warnings and not quiet:
        for warning in parse_result.warnings:
            click.echo(f"[warning] {warning}", err=True)

    # Create execution plan
    planner = create_planner(max_records=max_records)
    try:
        plan = planner.plan(query)
    except QueryValidationError as e:
        raise CLIError(
            f"Query planning failed: {e}",
            error_type="validation_error",
            exit_code=2,
        ) from None

    # Determine execution mode: full-fetch if orderBy/aggregate/groupBy
    is_full_fetch_mode = (
        query.order_by is not None or query.aggregate is not None or query.group_by is not None
    )

    # Try cache resumption for full-fetch mode cursors
    cached_data: list[dict[str, Any]] | None = None
    cache_file_path: str | None = None
    cache_content_hash: str | None = None

    if cursor and cursor.mode == "full-fetch" and cursor.cache_file:
        # Try to read from cache (no API calls needed)
        cached_data = read_cache(cursor)
        if cached_data is not None:
            if not quiet:
                click.echo("[info] Resuming from cache (no API calls)", err=True)
                # Emit NDJSON progress for MCP clients (per design doc)
                if not sys.stderr.isatty():
                    emit_cache_progress("Serving from cache (no API calls)")
            # Store cache info for potential re-use if still truncated
            cache_file_path = cursor.cache_file
            cache_content_hash = cursor.cache_hash
        elif not quiet:
            click.echo("[warning] Cache expired or missing, re-executing query", err=True)

    # Dry-run mode
    if dry_run or dry_run_verbose:
        if ctx.output == "json":
            click.echo(format_dry_run_json(plan))
        else:
            click.echo(format_dry_run(plan, verbose=dry_run_verbose))
        return

    # Check for expensive operations
    if (
        plan.has_expensive_operations
        and confirm
        and not click.confirm(
            f"This query will make approximately {plan.total_api_calls} API calls. Continue?"
        )
    ):
        raise CLIError("Query cancelled by user.")

    # Show warnings
    if plan.warnings and not quiet:
        for warning in plan.warnings:
            click.echo(f"[warning] {warning}", err=True)

    # Resolve client settings before async execution
    warnings_list: list[str] = []
    settings = ctx.resolve_client_settings(warnings=warnings_list)
    for warning in warnings_list:
        click.echo(f"[warning] {warning}", err=True)

    # Execute query (or use cached data)
    result: Any = None  # Will hold QueryResult

    if cached_data is not None:
        # Use cached data - no execution needed
        from affinity.cli.query.models import QueryResult

        result = QueryResult(
            data=cached_data,
            meta={"fromCache": True},
            pagination={"total": len(cached_data)},
        )
    else:
        # Normal execution path
        async def run_query() -> Any:
            from affinity import AsyncAffinity
            from affinity.hooks import ResponseInfo

            from ..query.executor import RateLimitedExecutor

            # Create rate limiter for adaptive throttling
            rate_limiter = RateLimitedExecutor()

            # Combine on_response to feed rate limiter with response data
            original_on_response = settings.on_response

            def combined_on_response(res: ResponseInfo) -> None:
                # Call original callback if it exists
                if original_on_response is not None:
                    original_on_response(res)
                # Feed rate limiter with status and remaining quota
                remaining_str = res.headers.get("X-RateLimit-Remaining")
                remaining = (
                    int(remaining_str) if remaining_str and remaining_str.isdigit() else None
                )
                rate_limiter.on_response(res.status_code, remaining)

            async with AsyncAffinity(
                api_key=settings.api_key,
                v1_base_url=settings.v1_base_url,
                v2_base_url=settings.v2_base_url,
                timeout=settings.timeout,
                log_requests=settings.log_requests,
                max_retries=settings.max_retries,
                on_request=settings.on_request,
                on_response=combined_on_response,
                on_error=settings.on_error,
                policies=settings.policies,
            ) as client:
                # Create progress callback
                if quiet:
                    progress = None
                else:
                    progress = create_progress_callback(
                        total_steps=len(plan.steps),
                        quiet=quiet,
                        force_ndjson=ctx.output == "json",
                    )

                # O(1) streaming resumption: use stored API cursor if available
                # This avoids re-fetching pages that were already returned
                resume_api_cursor: str | None = None
                if cursor and cursor.mode == "streaming" and cursor.api_cursor:
                    resume_api_cursor = cursor.api_cursor

                # Use context manager for Rich progress
                if isinstance(progress, RichQueryProgress):
                    with progress:
                        executor = QueryExecutor(
                            client,
                            progress=progress,
                            max_records=max_records,
                            max_records_explicit=max_records_explicit,
                            timeout=timeout,
                            allow_partial=True,
                            rate_limiter=rate_limiter,
                            resume_api_cursor=resume_api_cursor,
                        )
                        exec_result = await executor.execute(plan)
                else:
                    executor = QueryExecutor(
                        client,
                        progress=progress,
                        max_records=max_records,
                        max_records_explicit=max_records_explicit,
                        timeout=timeout,
                        allow_partial=True,
                        rate_limiter=rate_limiter,
                        resume_api_cursor=resume_api_cursor,
                    )
                    exec_result = await executor.execute(plan)

                # Capture rate limit before client closes
                exec_result.rate_limit = client.rate_limits.snapshot()
                return exec_result

        try:
            result = asyncio.run(run_query())
        except QueryValidationError as e:
            # Unbounded quantifier query without explicit --max-records
            raise CLIError(str(e)) from None
        except QueryTimeoutError as e:
            raise CLIError(f"Query timed out after {e.elapsed_seconds:.1f}s: {e}") from None
        except QuerySafetyLimitError as e:
            raise CLIError(f"Query exceeded safety limit: {e}") from None
        except QueryInterruptedError as e:
            if e.partial_results:
                click.echo(
                    f"[interrupted] Returning {len(e.partial_results)} partial results",
                    err=True,
                )
                from affinity.cli.query.models import QueryResult

                result = QueryResult(data=e.partial_results, meta={"interrupted": True})
            else:
                raise CLIError(f"Query interrupted: {e}") from None
        except QueryExecutionError as e:
            raise CLIError(f"Query execution failed: {e}") from None

    # Apply cursor skip position if resuming
    # This must happen BEFORE formatting so we only format remaining records
    # NOTE: Skip this for O(1) api_cursor resumption - we already started at the right position
    resume_position = 0
    all_data_for_cache = result.data  # Keep original for cache writing
    used_api_cursor_resumption = (
        cursor is not None
        and cursor.mode == "streaming"
        and cursor.api_cursor is not None
        and cached_data is None  # Not from cache
    )
    if cursor is not None and result.data and not used_api_cursor_resumption:
        # Skip-based resumption (fallback when api_cursor not available)
        resume_position, resume_warnings = find_resume_position(result.data, cursor)
        for warning in resume_warnings:
            if not quiet:
                click.echo(f"[warning] {warning}", err=True)
        # Slice data to only include remaining records
        result.data = result.data[resume_position:]
        if not quiet and resume_position > 0:
            click.echo(f"[info] Resuming from position {resume_position}", err=True)
    elif used_api_cursor_resumption and not quiet:
        click.echo("[info] Resumed via API cursor (O(1) resumption)", err=True)

    # Format and output results
    was_truncated = False

    # Track pre-truncation count for JSON format (see cursor logic below)
    json_pre_truncation_count: int = 0

    if csv_flag:
        from ..csv_utils import write_csv_to_stdout

        if not result.data:
            click.echo("No results.", err=True)
            sys.exit(0)

        # Collect all unique field names from data
        all_keys: set[str] = set()
        for record in result.data:
            all_keys.update(record.keys())
        fieldnames = sorted(all_keys)

        write_csv_to_stdout(rows=result.data, fieldnames=fieldnames, bom=csv_bom)
        sys.exit(0)
    elif ctx.output == "json":
        # Truncate at object level BEFORE serialization to avoid precision loss
        # Save count before truncation for cursor logic (line ~593)
        json_pre_truncation_count = len(result.data) if result.data else 0
        if max_output_bytes:
            result, _items_kept, was_truncated = truncate_json_result(
                result, max_output_bytes, include_meta=include_meta
            )
            # Handle can't-truncate case: envelope too large or empty data with large included
            # IMPORTANT: Always check size when not truncated, even if data is empty
            if not was_truncated:
                test_output = format_json(result, pretty=False, include_meta=include_meta)
                if len(test_output.encode()) > max_output_bytes:
                    # Can't truncate but output exceeds limit - this is an error
                    raise CLIError(
                        f"JSON output exceeds {max_output_bytes} bytes and cannot be "
                        "truncated. Reduce 'select' fields, remove 'include'/'expand', "
                        "or increase 'maxOutputBytes'."
                    )
        output = format_json(result, pretty=False, include_meta=include_meta)
    elif ctx.output in ("toon", "markdown"):
        # TOON and markdown support full envelope (pagination, included)
        output = format_query_result(result, ctx.output, pretty=False, include_meta=include_meta)

        # Apply truncation if requested
        if max_output_bytes and len(output.encode()) > max_output_bytes:
            original_total = result.pagination.get("total") if result.pagination else None
            if ctx.output == "toon":
                output, was_truncated = truncate_toon_output(output, max_output_bytes)
            elif ctx.output == "markdown":
                output, was_truncated = truncate_markdown_output(
                    output, max_output_bytes, original_total=original_total
                )
    elif ctx.output in ("jsonl", "csv"):
        # Data-only export formats
        output = format_query_result(result, ctx.output, pretty=False, include_meta=include_meta)

        # Apply truncation if requested
        if max_output_bytes and len(output.encode()) > max_output_bytes:
            if ctx.output == "jsonl":
                output, was_truncated = truncate_jsonl_output(output, max_output_bytes)
            elif ctx.output == "csv":
                output, was_truncated = truncate_csv_output(output, max_output_bytes)
    else:
        # Default to table for interactive use
        # Cast include_style to the expected Literal type
        from affinity.cli.query.output import IncludeStyle

        style: IncludeStyle = include_style  # type: ignore[assignment]
        output = format_table(result, include_style=style)

    # Handle truncation cursor creation BEFORE output
    # This allows us to insert cursor into TOON truncation section for human readability
    cursor_encoded: str | None = None
    cursor_mode: str | None = None

    if was_truncated:
        rows_shown = _count_rows_in_output(output, output_format)
        total_records = len(all_data_for_cache) if all_data_for_cache else 0
        # For JSON, use pre-truncation count since truncate_json_result modifies result.data
        remaining_records = (
            json_pre_truncation_count
            if output_format == "json" and json_pre_truncation_count > 0
            else len(result.data)
            if result.data
            else 0
        )

        if rows_shown < remaining_records:
            # Calculate new skip position (cumulative from original position)
            new_skip = resume_position + rows_shown
            last_shown_record = (
                result.data[rows_shown - 1] if rows_shown > 0 and result.data else None
            )
            last_id = (
                last_shown_record.get("id") or last_shown_record.get("listEntryId")
                if last_shown_record
                else None
            )

            if is_full_fetch_mode:
                # Full-fetch mode: write/reuse cache and create full-fetch cursor
                if cache_file_path is None or cache_content_hash is None:
                    # First truncation - write cache
                    try:
                        query_hash = hash_query(query, output_format)
                        cache_file_path, cache_content_hash = write_cache(
                            all_data_for_cache, query_hash
                        )
                        if not quiet:
                            click.echo(
                                f"[info] Cached {len(all_data_for_cache)} records for resumption",
                                err=True,
                            )
                    except Exception as e:
                        # Cache write failed - fall back to streaming cursor
                        if not quiet:
                            click.echo(f"[warning] Cache write failed: {e}", err=True)
                        cache_file_path = None
                        cache_content_hash = None

                if cache_file_path and cache_content_hash:
                    new_cursor = create_full_fetch_cursor(
                        query=query,
                        output_format=output_format,
                        skip=new_skip,
                        cache_file=cache_file_path,
                        cache_hash=cache_content_hash,
                        last_id=last_id,
                        total=total_records,
                    )
                else:
                    # Fall back to streaming cursor if cache failed
                    new_cursor = create_streaming_cursor(
                        query=query,
                        output_format=output_format,
                        skip=new_skip,
                        last_id=last_id,
                        total=total_records,
                        api_cursor=result.api_cursor,
                    )
            else:
                # Streaming mode: create streaming cursor with API cursor for O(1) resumption
                new_cursor = create_streaming_cursor(
                    query=query,
                    output_format=output_format,
                    skip=new_skip,
                    last_id=last_id,
                    total=total_records,
                    api_cursor=result.api_cursor,
                )

            cursor_encoded = encode_cursor(new_cursor)
            cursor_mode = new_cursor.mode

            # Insert cursor into TOON truncation section for human readability
            if output_format == "toon":
                output = insert_cursor_in_toon_truncation(output, cursor_encoded)

    # Output the formatted result
    click.echo(output)

    # Signal truncation via exit code and emit cursor to stderr for MCP extraction
    if was_truncated:
        if cursor_encoded and cursor_mode:
            emit_cursor_to_stderr(cursor_encoded, cursor_mode)
        sys.exit(EXIT_TRUNCATED)

    # Show summary if not quiet
    if not quiet and include_meta and result.meta:
        exec_time = result.meta.get("executionTime", 0)
        click.echo(f"\n[info] {len(result.data)} records in {exec_time:.2f}s", err=True)

    # Show rate limit info (at verbose level, matching other commands)
    if verbose and not quiet and result.rate_limit is not None:
        rl = result.rate_limit
        parts: list[str] = []
        if rl.api_key_per_minute.remaining is not None and rl.api_key_per_minute.limit is not None:
            parts.append(f"user {rl.api_key_per_minute.remaining}/{rl.api_key_per_minute.limit}")
        if rl.org_monthly.remaining is not None and rl.org_monthly.limit is not None:
            parts.append(f"org {rl.org_monthly.remaining}/{rl.org_monthly.limit}")
        if parts:
            click.echo(f"rate-limit[{rl.source}]: " + " | ".join(parts), err=True)


def _count_rows_in_output(output: str, format: str) -> int:
    """Count rows in truncated output for cursor skip calculation.

    Args:
        output: Truncated output string
        format: Output format (toon, markdown, json, jsonl, csv)

    Returns:
        Number of data rows in output
    """
    import re

    if format == "toon":
        # TOON format: look for "data[N]{...}:" header or truncation section
        # Pattern: "data[count]{fields}:"
        match = re.search(r"^data\[(\d+)\]", output, re.MULTILINE)
        if match:
            return int(match.group(1))
        # Fallback: count indented lines (data rows are indented with 2 spaces)
        return sum(
            1
            for line in output.split("\n")
            if line.startswith("  ") and not line.startswith("  rows")
        )

    elif format == "markdown":
        # Markdown table: count rows (lines starting with |, excluding header/separator)
        lines = [line for line in output.split("\n") if line.startswith("|")]
        return max(0, len(lines) - 2)  # Subtract header and separator rows

    elif format == "jsonl":
        # JSONL: count non-empty lines (excluding truncation marker)
        lines = [line for line in output.strip().split("\n") if line and '"truncated"' not in line]
        return len(lines)

    elif format == "csv":
        # CSV: count lines minus header
        lines = [line for line in output.strip().split("\n") if line]
        return max(0, len(lines) - 1)

    elif format == "json":
        # JSON: count items in data array
        try:
            data = json.loads(output)
            if isinstance(data, dict) and "data" in data:
                return len(data["data"])
        except json.JSONDecodeError:
            pass
        return 0

    return 0


def _get_query_input(file_path: Path | None, query_str: str | None) -> dict[str, Any]:
    """Get query input from file, string, or stdin.

    Args:
        file_path: Path to query file
        query_str: Inline JSON string

    Returns:
        Parsed query dict

    Raises:
        CLIError: If no input provided or parsing fails
    """
    if file_path:
        try:
            content = file_path.read_text()
            result: dict[str, Any] = json.loads(content)
            return result
        except json.JSONDecodeError as e:
            raise CLIError(f"Invalid JSON in file: {e}") from None
        except OSError as e:
            raise CLIError(f"Failed to read file: {e}") from None

    if query_str:
        try:
            result = json.loads(query_str)
            return result
        except json.JSONDecodeError as e:
            raise CLIError(f"Invalid JSON: {e}") from None

    # Try stdin
    if not sys.stdin.isatty():
        try:
            content = sys.stdin.read()
            result = json.loads(content)
            return result
        except json.JSONDecodeError as e:
            raise CLIError(f"Invalid JSON from stdin: {e}") from None

    raise CLIError(
        "No query provided. Use --file, --query, or pipe JSON to stdin.\n\n"
        "Examples:\n"
        "  xaffinity query --file query.json\n"
        '  xaffinity query --query \'{"from": "persons", "limit": 10}\'\n'
        '  echo \'{"from": "persons"}\' | xaffinity query'
    )
