from __future__ import annotations

import json
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

from affinity.exceptions import AffinityError
from affinity.models.secondary import Interaction, InteractionCreate, InteractionUpdate
from affinity.models.types import InteractionDirection, InteractionType
from affinity.types import CompanyId, InteractionId, OpportunityId, PersonId

from ..click_compat import RichCommand, RichGroup, click
from ..confirm import run_destructive
from ..context import CLIContext
from ..csv_utils import write_csv_to_stdout
from ..date_utils import ChunkedFetchResult, chunk_date_range
from ..decorators import category, destructive
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import csv_output_options, csv_suboption_callback, output_options
from ..results import CommandContext, DateRange, ResultSummary
from ..runner import CommandOutput, run_command
from ._v1_parsing import parse_choice, parse_iso_datetime


@click.group(name="interaction", cls=RichGroup)
def interaction_group() -> None:
    """Interaction commands."""


_INTERACTION_TYPE_MAP = {
    "meeting": InteractionType.MEETING,
    "call": InteractionType.CALL,
    "chat-message": InteractionType.CHAT_MESSAGE,
    "chat": InteractionType.CHAT_MESSAGE,
    "email": InteractionType.EMAIL,
}

_INTERACTION_DIRECTION_MAP = {
    "outgoing": InteractionDirection.OUTGOING,
    "incoming": InteractionDirection.INCOMING,
}

# Canonical types for --type all expansion and output
_CANONICAL_TYPES = ["call", "chat-message", "email", "meeting"]

# Accepted types includes aliases (e.g., "chat" for "chat-message")
_ACCEPTED_TYPES = sorted(_INTERACTION_TYPE_MAP.keys())


@dataclass
class TypeStats:
    """Per-type statistics for multi-type fetch."""

    count: int
    chunks_processed: int


@dataclass
class MultiTypeFetchResult:
    """Result from multi-type interaction fetching."""

    interactions: list[Interaction]
    type_stats: dict[str, TypeStats]


@dataclass
class _NDJSONProgress:
    """NDJSON progress emitter for non-TTY environments (MCP consumption)."""

    enabled: bool = False
    # Rate limit at 0.65s (matches ProgressManager) to stay under mcp-bash 100/min limit
    _min_interval: float = 0.65
    _last_emit_time: float = field(default_factory=lambda: float("-inf"))

    def emit(
        self,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
        force: bool = False,
    ) -> None:
        """Emit NDJSON progress to stderr for MCP consumption.

        Args:
            message: Human-readable progress message.
            current: Current count (e.g., interactions fetched so far).
            total: Total count if known (None for indeterminate).
            force: Bypass rate limiting (for final summary).
        """
        if not self.enabled:
            return

        # Rate limit (unless forced)
        now = time.monotonic()
        if not force and now - self._last_emit_time < self._min_interval:
            return
        self._last_emit_time = now

        # Compute percent if both current and total are known
        percent = (current * 100 // total) if (current is not None and total) else None

        obj: dict[str, int | str | None] = {
            "type": "progress",
            "progress": percent,
            "message": message,
        }
        if current is not None:
            obj["current"] = current
        if total is not None:
            obj["total"] = total

        # flush=True is CRITICAL: Python buffers stderr when not a TTY
        print(json.dumps(obj), file=sys.stderr, flush=True)


def _resolve_types(interaction_types: tuple[str, ...]) -> list[str]:
    """Expand 'all' and deduplicate by canonical type.

    Handles aliases: 'chat' → 'chat-message'
    Returns canonical type names only.
    """
    if "all" in interaction_types:
        return _CANONICAL_TYPES.copy()

    # Deduplicate by resolved enum (handles chat vs chat-message)
    seen_enums: set[InteractionType] = set()
    result: list[str] = []
    for t in interaction_types:
        enum_val = _INTERACTION_TYPE_MAP[t]
        if enum_val not in seen_enums:
            seen_enums.add(enum_val)
            # Use canonical name (e.g., chat → chat-message)
            canonical = InteractionType(enum_val).name.lower().replace("_", "-")
            result.append(canonical)
    return result


def _interaction_payload(interaction: Interaction) -> dict[str, object]:
    # Convert enum values back to names for CLI display
    type_name = InteractionType(interaction.type).name.lower().replace("_", "-")
    direction_name = (
        InteractionDirection(interaction.direction).name.lower()
        if interaction.direction is not None
        else None
    )
    return {
        "id": int(interaction.id),
        "type": type_name,
        "date": interaction.date,
        "direction": direction_name,
        "title": interaction.title,
        "subject": interaction.subject,
        "startTime": interaction.start_time,
        "endTime": interaction.end_time,
        "personIds": [int(p.id) for p in interaction.persons],
        "attendees": interaction.attendees,
        "notes": [int(n) for n in interaction.notes],
    }


def _resolve_date_range(
    after: str | None,
    before: str | None,
    days: int | None,
) -> tuple[datetime, datetime]:
    """Resolve date flags to start/end datetimes.

    If neither --days nor --after is specified, defaults to "all time"
    (starting from 2010-01-01, which predates all possible Affinity data).
    """
    now = datetime.now(timezone.utc)

    # Mutual exclusion
    if days is not None and after is not None:
        raise CLIError(
            "--days and --after are mutually exclusive.",
            error_type="usage_error",
            exit_code=2,
        )

    # Resolve start
    if days is not None:
        start = now - timedelta(days=days)
    elif after is not None:
        # parse_iso_datetime returns UTC-aware datetime
        # (naive strings interpreted as local time, then converted to UTC)
        start = parse_iso_datetime(after, label="after")
    else:
        # Default: all time (Affinity founded 2014, so 2010 predates all possible data)
        # Using a fixed date rather than datetime.min avoids timezone edge cases
        start = datetime(2010, 1, 1, tzinfo=timezone.utc)

    # Resolve end (parse_iso_datetime returns UTC-aware datetime)
    end = parse_iso_datetime(before, label="before") if before is not None else now

    # Validate
    if start >= end:
        raise CLIError(
            f"Start date ({start.date()}) must be before end date ({end.date()}).",
            error_type="usage_error",
            exit_code=2,
        )

    return start, end


def _fetch_interactions_chunked(
    client: object,  # Affinity client (typed as object to avoid import cycle)
    *,
    interaction_type: InteractionType,
    start: datetime,
    end: datetime,
    person_id: PersonId | None,
    company_id: CompanyId | None,
    opportunity_id: OpportunityId | None,
    page_size: int | None,
    max_results: int | None,
    progress: Progress | None,
    task_id: TaskID | None,
    suppress_chunk_description: bool = False,
    progress_offset: int = 0,
) -> ChunkedFetchResult:
    """
    Fetch interactions across date chunks.

    Returns ChunkedFetchResult with interactions and chunk count for metadata.

    Args:
        suppress_chunk_description: If True, don't update progress description with
            chunk info (used in multi-type mode where outer loop controls description).
        progress_offset: Offset to add to progress counts (for cumulative totals
            in multi-type mode).

    Note: Relies on API using exclusive end_time boundary.
    If an interaction has timestamp exactly at chunk boundary,
    it will appear in the later chunk (not both).
    """
    chunks = list(chunk_date_range(start, end))
    total_chunks = len(chunks)
    results: list[Interaction] = []
    chunks_processed = 0

    for chunk_idx, (chunk_start, chunk_end) in enumerate(chunks, 1):
        chunks_processed = chunk_idx

        # Update progress description with chunk info (suppressed in multi-type mode)
        if not suppress_chunk_description and progress and task_id is not None:
            desc = f"{chunk_start.date()} - {chunk_end.date()} ({chunk_idx}/{total_chunks})"
            progress.update(task_id, description=desc)

        # Paginate within chunk
        page_token: str | None = None
        while True:
            try:
                page = client.interactions.list(  # type: ignore[attr-defined]
                    type=interaction_type,
                    start_time=chunk_start,
                    end_time=chunk_end,
                    person_id=person_id,
                    company_id=company_id,
                    opportunity_id=opportunity_id,
                    page_size=page_size,
                    page_token=page_token,
                )
            except AffinityError as e:
                raise CLIError(
                    f"Failed on chunk {chunk_idx}/{total_chunks} "
                    f"({chunk_start.date()} \u2192 {chunk_end.date()}): {e}",
                    error_type="api_error",
                    exit_code=1,
                ) from e

            for interaction in page.data:
                results.append(interaction)
                if progress and task_id is not None:
                    progress.update(task_id, completed=progress_offset + len(results))

                # Check max_results limit
                if max_results is not None and len(results) >= max_results:
                    return ChunkedFetchResult(
                        interactions=results[:max_results],
                        chunks_processed=chunks_processed,
                    )

            page_token = page.next_cursor
            if not page_token:
                break

    return ChunkedFetchResult(
        interactions=results,
        chunks_processed=chunks_processed,
    )


def _fetch_interactions_multi_type(
    client: object,  # Affinity client
    *,
    types: list[str],
    start: datetime,
    end: datetime,
    person_id: PersonId | None,
    company_id: CompanyId | None,
    opportunity_id: OpportunityId | None,
    page_size: int | None,
    progress: Progress | None,
    task_id: TaskID | None,
    ndjson_progress: _NDJSONProgress | None = None,
) -> MultiTypeFetchResult:
    """Fetch interactions across multiple types, merging results.

    Note: max_results is NOT applied here - it's applied after sorting in the caller.
    This ensures correct "most recent N" semantics across types.
    """
    all_interactions: list[Interaction] = []
    type_stats: dict[str, TypeStats] = {}
    is_multi_type = len(types) > 1
    cumulative_count = 0

    for type_idx, itype in enumerate(types):
        # Build progress message for this type
        desc = f"{itype} ({type_idx + 1}/{len(types)})" if is_multi_type else f"Fetching {itype}"

        # Update Rich progress (TTY mode)
        if progress and task_id is not None:
            progress.update(task_id, description=desc, completed=cumulative_count)

        # Emit NDJSON progress (non-TTY mode for MCP)
        if ndjson_progress:
            ndjson_progress.emit(desc, current=cumulative_count)

        # Fetch this type completely (no max_results - applied after sorting)
        result = _fetch_interactions_chunked(
            client,
            interaction_type=_INTERACTION_TYPE_MAP[itype],
            start=start,
            end=end,
            person_id=person_id,
            company_id=company_id,
            opportunity_id=opportunity_id,
            page_size=page_size,
            max_results=None,  # Fetch all - truncate after sorting
            progress=progress,
            task_id=task_id,
            suppress_chunk_description=is_multi_type,
            progress_offset=cumulative_count,
        )

        all_interactions.extend(result.interactions)
        cumulative_count += len(result.interactions)
        type_stats[itype] = TypeStats(
            count=len(result.interactions),
            chunks_processed=result.chunks_processed,
        )

    # Ensure all requested types appear in stats (even with 0 count)
    for itype in types:
        if itype not in type_stats:
            type_stats[itype] = TypeStats(count=0, chunks_processed=0)

    return MultiTypeFetchResult(
        interactions=all_interactions,
        type_stats=type_stats,
    )


@category("read")
@interaction_group.command(name="ls", cls=RichCommand)
@click.option(
    "--type",
    "-t",
    "interaction_types",
    type=click.Choice([*_ACCEPTED_TYPES, "all"]),
    multiple=True,
    required=True,
    help="Interaction type(s): call, chat-message, email, meeting, or 'all'. Repeatable.",
)
@click.option(
    "--after",
    type=str,
    default=None,
    help="Start date (ISO-8601). Mutually exclusive with --days.",
)
@click.option(
    "--before",
    type=str,
    default=None,
    help="End date (ISO-8601). Default: now.",
)
@click.option(
    "--days",
    "-d",
    type=int,
    default=None,
    help="Fetch last N days. Mutually exclusive with --after.",
)
@click.option("--person-id", type=int, default=None, help="Filter by person id.")
@click.option("--company-id", type=int, default=None, help="Filter by company id.")
@click.option("--opportunity-id", type=int, default=None, help="Filter by opportunity id.")
@click.option("--page-size", "-s", type=int, default=None, help="Page size (max 500).")
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N results total."
)
@click.option(
    "--csv-bom",
    is_flag=True,
    help="Add UTF-8 BOM for Excel compatibility.",
    callback=csv_suboption_callback,
    expose_value=True,
)
@csv_output_options
@click.pass_obj
@apply_mcp_limits(all_pages_param=None)
def interaction_ls(
    ctx: CLIContext,
    *,
    interaction_types: tuple[str, ...],
    after: str | None,
    before: str | None,
    days: int | None,
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
    page_size: int | None,
    max_results: int | None,
    csv_bom: bool,
) -> None:
    """List interactions with automatic date range handling.

    Requires --type (repeatable) and one entity selector (--person-id, --company-id, or
    --opportunity-id). Multiple types can be specified with -t/--type flags, or use
    --type all to fetch all interaction types.

    Date range defaults to all time if not specified. Use --days (relative) or
    --after/--before (absolute) to limit the range. Ranges exceeding 1 year are
    automatically split into chunks.

    Examples:

      # All interactions ever with a company
      xaffinity interaction ls --type all --company-id 456

      # Last 30 days of meetings with a person
      xaffinity interaction ls --type meeting --person-id 123 --days 30

      # Emails and meetings from a specific date range
      xaffinity interaction ls -t email -t meeting --company-id 456 --after 2023-01-01

      # Last 2 years of calls (auto-chunked)
      xaffinity interaction ls -t call --person-id 789 --days 730
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        # Validate entity selector
        entity_count = sum(1 for x in [person_id, company_id, opportunity_id] if x is not None)
        if entity_count == 0:
            raise CLIError(
                "Specify --person-id, --company-id, or --opportunity-id.",
                error_type="usage_error",
                exit_code=2,
            )
        if entity_count > 1:
            raise CLIError(
                "Only one entity selector allowed.",
                error_type="usage_error",
                exit_code=2,
            )

        # Resolve types (expand 'all', deduplicate aliases)
        resolved_types = _resolve_types(interaction_types)

        # Resolve dates (validates mutual exclusion, defaults to all-time)
        start, end = _resolve_date_range(after, before, days)

        client = ctx.get_client(warnings=warnings)

        # Determine if Rich progress (TTY) should be shown
        show_rich_progress = (
            ctx.progress != "never"
            and not ctx.quiet
            and (ctx.progress == "always" or sys.stderr.isatty())
        )

        # NDJSON progress for non-TTY environments (MCP consumption)
        ndjson_progress = _NDJSONProgress(
            enabled=(ctx.progress != "never" and not ctx.quiet and not sys.stderr.isatty())
        )

        with ExitStack() as stack:
            progress: Progress | None = None
            task_id: TaskID | None = None

            if show_rich_progress:
                progress = stack.enter_context(
                    Progress(
                        TextColumn("{task.description}"),
                        BarColumn(),
                        TextColumn("{task.completed} rows"),
                        TimeElapsedColumn(),
                        console=Console(file=sys.stderr),
                        transient=True,
                    )
                )
                task_id = progress.add_task("Fetching interactions", total=None)

            fetch_result = _fetch_interactions_multi_type(
                client,
                types=resolved_types,
                start=start,
                end=end,
                person_id=PersonId(person_id) if person_id else None,
                company_id=CompanyId(company_id) if company_id else None,
                opportunity_id=OpportunityId(opportunity_id) if opportunity_id else None,
                page_size=page_size,
                progress=progress,
                task_id=task_id,
                ndjson_progress=ndjson_progress,
            )

        # Emit final summary (NDJSON mode, forced to bypass rate limit)
        total_count = sum(s.count for s in fetch_result.type_stats.values())
        types_with_data = sum(1 for s in fetch_result.type_stats.values() if s.count > 0)
        if types_with_data > 1:
            summary_msg = f"{total_count} interactions across {types_with_data} types"
        else:
            summary_msg = f"{total_count} interactions"
        ndjson_progress.emit(summary_msg, current=total_count, force=True)

        # Sort by date descending, then by type name, then by id for stability
        sorted_interactions = sorted(
            fetch_result.interactions,
            key=lambda i: (
                i.date or datetime.min.replace(tzinfo=timezone.utc),
                InteractionType(i.type).name,  # Alphabetical: CALL < CHAT_MESSAGE < EMAIL < MEETING
                i.id,
            ),
            reverse=True,
        )

        # Apply max_results AFTER sorting (correct "most recent N" semantics)
        if max_results and len(sorted_interactions) > max_results:
            sorted_interactions = sorted_interactions[:max_results]
            warnings.append(f"Results limited to {max_results}. Remove --max-results for all.")

        # Convert to output format
        results = [_interaction_payload(i) for i in sorted_interactions]

        # CSV output
        if ctx.output == "csv":
            fieldnames = list(results[0].keys()) if results else []
            write_csv_to_stdout(rows=results, fieldnames=fieldnames, bom=csv_bom)
            sys.exit(0)

        # Build type breakdown for summary (only types with results)
        type_breakdown = {
            itype: stats.count
            for itype, stats in fetch_result.type_stats.items()
            if stats.count > 0
        }
        total_chunks = sum(stats.chunks_processed for stats in fetch_result.type_stats.values())

        # Build context - types is always an array
        cmd_context = CommandContext(
            name="interaction ls",
            inputs={},
            modifiers={
                "types": resolved_types,  # Always array, even for single type
                "start": start.isoformat(),
                "end": end.isoformat(),
                **({"personId": person_id} if person_id else {}),
                **({"companyId": company_id} if company_id else {}),
                **({"opportunityId": opportunity_id} if opportunity_id else {}),
            },
        )

        # Build summary for footer display
        summary = ResultSummary(
            total_rows=len(results),
            date_range=DateRange(start=start, end=end),
            type_breakdown=type_breakdown if type_breakdown else None,
            chunks_processed=total_chunks if total_chunks > 0 else None,
        )

        return CommandOutput(
            data=results,  # Direct array, not wrapped
            context=cmd_context,
            summary=summary,
            api_called=True,
        )

    run_command(ctx, command="interaction ls", fn=fn)


@category("read")
@interaction_group.command(name="get", cls=RichCommand)
@click.argument("interaction_id", type=int)
@click.option(
    "--type",
    "-t",
    "interaction_type",
    type=click.Choice(sorted(_INTERACTION_TYPE_MAP.keys())),
    required=True,
    help="Interaction type (required by API).",
)
@output_options
@click.pass_obj
def interaction_get(ctx: CLIContext, interaction_id: int, *, interaction_type: str) -> None:
    """Get an interaction by id.

    The --type flag is required because the Affinity API stores interactions
    in type-specific tables.

    Examples:

    - `xaffinity interaction get 123 --type meeting`
    - `xaffinity interaction get 456 -t email`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        parsed_type = parse_choice(
            interaction_type,
            _INTERACTION_TYPE_MAP,
            label="interaction type",
        )
        if parsed_type is None:
            raise CLIError("Missing interaction type.", error_type="usage_error", exit_code=2)
        client = ctx.get_client(warnings=warnings)
        interaction = client.interactions.get(InteractionId(interaction_id), parsed_type)

        cmd_context = CommandContext(
            name="interaction get",
            inputs={"interactionId": interaction_id},
            modifiers={"type": interaction_type},
        )

        return CommandOutput(
            data={"interaction": _interaction_payload(interaction)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="interaction get", fn=fn)


@category("write")
@interaction_group.command(name="create", cls=RichCommand)
@click.option(
    "--type",
    "-t",
    "interaction_type",
    type=click.Choice(sorted(_INTERACTION_TYPE_MAP.keys())),
    required=True,
    help="Interaction type (required).",
)
@click.option("--person-id", "person_ids", multiple=True, type=int, help="Person id.")
@click.option("--content", type=str, required=True, help="Interaction content.")
@click.option("--date", type=str, required=True, help="Interaction date (ISO-8601).")
@click.option(
    "--direction",
    type=click.Choice(sorted(_INTERACTION_DIRECTION_MAP.keys())),
    default=None,
    help="Direction (incoming, outgoing).",
)
@click.option(
    "--include-me",
    is_flag=True,
    default=False,
    help="Auto-include your person ID (resolved via whoami).",
)
@output_options
@click.pass_obj
def interaction_create(
    ctx: CLIContext,
    *,
    interaction_type: str,
    person_ids: tuple[int, ...],
    content: str,
    date: str,
    direction: str | None,
    include_me: bool,
) -> None:
    """Create an interaction."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if not person_ids and not include_me:
            raise CLIError(
                "At least one --person-id is required (or use --include-me).",
                error_type="usage_error",
                exit_code=2,
            )

        parsed_type = parse_choice(
            interaction_type,
            _INTERACTION_TYPE_MAP,
            label="interaction type",
        )
        if parsed_type is None:
            raise CLIError("Missing interaction type.", error_type="usage_error", exit_code=2)
        parsed_direction = parse_choice(direction, _INTERACTION_DIRECTION_MAP, label="direction")
        date_value = parse_iso_datetime(date, label="date")

        client = ctx.get_client(warnings=warnings)

        resolved_ids = list(person_ids)
        if include_me:
            who = client.whoami()
            if who.user.id not in resolved_ids:
                resolved_ids.insert(0, who.user.id)

        interaction = client.interactions.create(
            InteractionCreate(
                type=parsed_type,
                person_ids=[PersonId(pid) for pid in resolved_ids],
                content=content,
                date=date_value,
                direction=parsed_direction,
            )
        )

        # Build CommandContext for interaction create
        ctx_modifiers: dict[str, object] = {
            "type": interaction_type,
            "personIds": resolved_ids,
            "date": date,
        }
        if direction:
            ctx_modifiers["direction"] = direction

        cmd_context = CommandContext(
            name="interaction create",
            inputs={"type": interaction_type},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"interaction": _interaction_payload(interaction)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="interaction create", fn=fn)


@category("write")
@interaction_group.command(name="update", cls=RichCommand)
@click.argument("interaction_id", type=int)
@click.option(
    "--type",
    "-t",
    "interaction_type",
    type=click.Choice(sorted(_INTERACTION_TYPE_MAP.keys())),
    required=True,
    help="Interaction type (required by API).",
)
@click.option("--person-id", "person_ids", multiple=True, type=int, help="Person id.")
@click.option("--content", type=str, default=None, help="Interaction content.")
@click.option("--date", type=str, default=None, help="Interaction date (ISO-8601).")
@click.option(
    "--direction",
    type=click.Choice(sorted(_INTERACTION_DIRECTION_MAP.keys())),
    default=None,
    help="Direction (incoming, outgoing).",
)
@output_options
@click.pass_obj
def interaction_update(
    ctx: CLIContext,
    interaction_id: int,
    *,
    interaction_type: str,
    person_ids: tuple[int, ...],
    content: str | None,
    date: str | None,
    direction: str | None,
) -> None:
    """Update an interaction."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        parsed_type = parse_choice(
            interaction_type,
            _INTERACTION_TYPE_MAP,
            label="interaction type",
        )
        if parsed_type is None:
            raise CLIError("Missing interaction type.", error_type="usage_error", exit_code=2)

        parsed_direction = parse_choice(direction, _INTERACTION_DIRECTION_MAP, label="direction")
        date_value = parse_iso_datetime(date, label="date") if date else None

        if not (person_ids or content or date_value or parsed_direction is not None):
            raise CLIError(
                "Provide at least one field to update.",
                error_type="usage_error",
                exit_code=2,
                hint="Use --person-id, --content, --date, or --direction.",
            )

        client = ctx.get_client(warnings=warnings)
        interaction = client.interactions.update(
            InteractionId(interaction_id),
            parsed_type,
            InteractionUpdate(
                person_ids=[PersonId(pid) for pid in person_ids] if person_ids else None,
                content=content,
                date=date_value,
                direction=parsed_direction,
            ),
        )

        # Build CommandContext for interaction update
        ctx_modifiers: dict[str, object] = {"type": interaction_type}
        if person_ids:
            ctx_modifiers["personIds"] = list(person_ids)
        if content:
            ctx_modifiers["content"] = content
        if date:
            ctx_modifiers["date"] = date
        if direction:
            ctx_modifiers["direction"] = direction

        cmd_context = CommandContext(
            name="interaction update",
            inputs={"interactionId": interaction_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"interaction": _interaction_payload(interaction)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="interaction update", fn=fn)


@category("write")
@destructive
@interaction_group.command(name="delete", cls=RichCommand)
@click.argument("interaction_id", type=int)
@click.option(
    "--type",
    "-t",
    "interaction_type",
    type=click.Choice(sorted(_INTERACTION_TYPE_MAP.keys())),
    required=True,
    help="Interaction type (required by API).",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def interaction_delete(
    ctx: CLIContext, interaction_id: int, *, interaction_type: str, yes: bool
) -> None:
    """Delete an interaction."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        parsed_type = parse_choice(
            interaction_type,
            _INTERACTION_TYPE_MAP,
            label="interaction type",
        )
        if parsed_type is None:
            raise CLIError("Missing interaction type.", error_type="usage_error", exit_code=2)
        client = ctx.get_client(warnings=warnings)
        success = client.interactions.delete(InteractionId(interaction_id), parsed_type)

        cmd_context = CommandContext(
            name="interaction delete",
            inputs={"interactionId": interaction_id},
            modifiers={"type": interaction_type},
        )

        return CommandOutput(
            data={"success": success},
            context=cmd_context,
            api_called=True,
        )

    run_destructive(
        ctx,
        command="interaction delete",
        yes=yes,
        prompt=f"Delete interaction {interaction_id}?",
        fn=fn,
    )
