from __future__ import annotations

import json
import sys
import time
import warnings as stdlib_warnings
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any, Literal, cast

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

from affinity.filters import FilterExpression
from affinity.filters import parse as parse_filter
from affinity.models.entities import FieldMetadata, ListCreate, ListEntryWithEntity
from affinity.models.pagination import FilterStats
from affinity.models.types import InteractionType, ListType
from affinity.types import (
    AnyFieldId,
    CompanyId,
    EnrichedFieldId,
    FieldId,
    FieldType,
    ListEntryId,
    ListId,
    OpportunityId,
    PersonId,
)

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..csv_utils import write_csv_to_stdout
from ..decorators import category, destructive
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import csv_output_options, csv_suboption_callback, output_options
from ..render import format_duration
from ..resolve import (
    list_all_saved_views,
    list_fields_for_list,
    resolve_list_selector,
    resolve_saved_view,
)
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli, serialize_models_for_cli


@click.group(name="list", cls=RichGroup)
def list_group() -> None:
    """List commands."""


def _parse_list_type(value: str | None) -> ListType | None:
    if value is None:
        return None
    value = value.lower()
    if value in {"person", "people"}:
        return ListType.PERSON
    if value in {"company", "companies", "organization", "org"}:
        return ListType.COMPANY
    if value in {"opportunity", "opp"}:
        return ListType.OPPORTUNITY
    raise CLIError(f"Unknown list type: {value}", exit_code=2, error_type="usage_error")


def _parse_unreplied_types(types_str: str) -> list[InteractionType]:
    """Parse comma-separated unreplied type string into InteractionType list.

    Args:
        types_str: Comma-separated types (e.g., "email,chat" or "all")

    Returns:
        List of InteractionType values to check

    Raises:
        CLIError: If invalid types are specified
    """
    types_list = [t.strip().lower() for t in types_str.split(",") if t.strip()]

    # Handle "all" shorthand
    if "all" in types_list:
        return [InteractionType.EMAIL, InteractionType.CHAT_MESSAGE]

    valid_types = {"email", "chat"}
    invalid = set(types_list) - valid_types
    if invalid:
        raise CLIError(
            f"Invalid unreplied types: {invalid}. Supported: email, chat, all.",
            exit_code=2,
            error_type="usage_error",
            hint="Meetings and calls don't have direction, so 'unreplied' doesn't apply.",
        )

    result: list[InteractionType] = []
    if "email" in types_list:
        result.append(InteractionType.EMAIL)
    if "chat" in types_list:
        result.append(InteractionType.CHAT_MESSAGE)
    return result


@category("read")
@list_group.command(name="ls", cls=RichCommand)
@click.option("--type", "list_type", type=str, default=None, help="Filter by list type.")
@click.option("--page-size", "-s", type=int, default=None, help="Page size (limit).")
@click.option(
    "--cursor", type=str, default=None, help="Resume from cursor (incompatible with --page-size)."
)
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N items total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@output_options
@click.pass_obj
@apply_mcp_limits()
def list_ls(
    ctx: CLIContext,
    *,
    list_type: str | None,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """
    List all lists in the workspace.

    Examples:

    - `xaffinity list ls`
    - `xaffinity list ls --type person`
    - `xaffinity list ls --type company --all`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        lt = _parse_list_type(list_type)

        if cursor is not None and page_size is not None:
            raise CLIError(
                "--cursor cannot be combined with --page-size.",
                exit_code=2,
                error_type="usage_error",
            )

        # Build CommandContext upfront for all return paths
        ctx_modifiers: dict[str, object] = {}
        if list_type:
            ctx_modifiers["type"] = list_type
        if page_size is not None:
            ctx_modifiers["pageSize"] = page_size
        if cursor is not None:
            ctx_modifiers["cursor"] = cursor
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["allPages"] = True

        cmd_context = CommandContext(
            name="list ls",
            inputs={},
            modifiers=ctx_modifiers,
        )

        pages = client.lists.pages(limit=page_size, cursor=cursor)
        rows: list[dict[str, object]] = []
        first_page = True

        show_progress = (
            ctx.progress != "never"
            and not ctx.quiet
            and (ctx.progress == "always" or sys.stderr.isatty())
        )

        with ExitStack() as stack:
            progress: Progress | None = None
            task_id: TaskID | None = None
            if show_progress:
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
                task_id = progress.add_task("Fetching", total=max_results)

            for page in pages:
                for idx, item in enumerate(page.data):
                    if lt is not None and item.type != lt:
                        continue
                    rows.append(
                        {
                            "id": int(item.id),
                            "name": item.name,
                            "type": ListType(item.type).name.lower(),
                            "ownerId": int(item.owner_id)
                            if getattr(item, "owner_id", None)
                            else None,
                            "isPublic": getattr(item, "is_public", None),
                        }
                    )
                    if progress and task_id is not None:
                        progress.update(task_id, completed=len(rows))
                    if max_results is not None and len(rows) >= max_results:
                        stopped_mid_page = idx < (len(page.data) - 1)
                        if stopped_mid_page:
                            warnings.append(
                                "Results limited by --max-results. Use --all to fetch all results."
                            )
                        pagination = None
                        if (
                            page.pagination.next_cursor
                            and not stopped_mid_page
                            and page.pagination.next_cursor != cursor
                        ):
                            pagination = {
                                "lists": {
                                    "nextCursor": page.pagination.next_cursor,
                                    "prevCursor": page.pagination.prev_cursor,
                                }
                            }
                        return CommandOutput(
                            data={"lists": rows[:max_results]},
                            context=cmd_context,
                            pagination=pagination,
                            api_called=True,
                        )

                if first_page and not all_pages and max_results is None:
                    return CommandOutput(
                        data={"lists": rows},
                        context=cmd_context,
                        pagination=(
                            {
                                "lists": {
                                    "nextCursor": page.pagination.next_cursor,
                                    "prevCursor": page.pagination.prev_cursor,
                                }
                            }
                            if page.pagination.next_cursor
                            else None
                        ),
                        api_called=True,
                    )
                first_page = False

        return CommandOutput(
            data={"lists": rows},
            context=cmd_context,
            pagination=None,
            api_called=True,
        )

    run_command(ctx, command="list ls", fn=fn)


@category("write")
@list_group.command(name="create", cls=RichCommand)
@click.option("--name", required=True, help="List name.")
@click.option("--type", "list_type", required=True, help="List type (person/company/opportunity).")
@click.option(
    "--public/--private",
    "is_public",
    default=False,
    help="Whether the list is public (default: private).",
)
@click.option("--owner-id", type=int, default=None, help="Owner id.")
@output_options
@click.pass_obj
def list_create(
    ctx: CLIContext,
    *,
    name: str,
    list_type: str,
    is_public: bool,
    owner_id: int | None,
) -> None:
    """
    Create a new list.

    Examples:

    - `xaffinity list create --name "Prospects" --type company`
    - `xaffinity list create --name "Candidates" --type person --public`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _ = warnings
        lt = _parse_list_type(list_type)
        if lt is None:
            raise CLIError(
                "Missing list type.",
                exit_code=2,
                error_type="usage_error",
                hint="Use --type person|company|opportunity.",
            )
        client = ctx.get_client(warnings=warnings)
        created = client.lists.create(
            ListCreate(
                name=name,
                type=lt,
                is_public=is_public,
                owner_id=owner_id,
            )
        )

        # Invalidate list-related caches after creation
        cache = ctx.session_cache
        cache.invalidate_prefix("list_resolve_")

        # Build CommandContext for list create
        ctx_modifiers: dict[str, object] = {"name": name, "type": list_type}
        if is_public:
            ctx_modifiers["isPublic"] = True
        if owner_id is not None:
            ctx_modifiers["ownerId"] = owner_id

        cmd_context = CommandContext(
            name="list create",
            inputs={},
            modifiers=ctx_modifiers,
        )

        payload = serialize_model_for_cli(created)
        return CommandOutput(data={"list": payload}, context=cmd_context, api_called=True)

    run_command(ctx, command="list create", fn=fn)


@category("read")
@list_group.command(name="get", cls=RichCommand)
@click.argument("list_selector", type=str)
@output_options
@click.pass_obj
def list_get(ctx: CLIContext, list_selector: str) -> None:
    """
    Get list details, fields, and saved views.

    LIST_SELECTOR can be a list id or exact list name.

    Examples:

    - `xaffinity list get 12345`
    - `xaffinity list get "Pipeline"`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        resolved = resolve_list_selector(client=client, selector=list_selector, cache=cache)
        list_id = ListId(int(resolved.list.id))
        fields = list_fields_for_list(client=client, list_id=list_id, cache=cache)
        views = list_all_saved_views(client=client, list_id=list_id, cache=cache)

        # Extract resolved list name for context
        ctx_resolved: dict[str, str] | None = None
        list_resolved = resolved.resolved.get("list", {})
        if isinstance(list_resolved, dict):
            list_name = list_resolved.get("entityName")
            if list_name:
                ctx_resolved = {"selector": str(list_name)}

        cmd_context = CommandContext(
            name="list get",
            inputs={"selector": list_selector},
            modifiers={},
            resolved=ctx_resolved,
        )

        list_data = serialize_model_for_cli(resolved.list)
        # Add listSize for MCP compatibility (get_size uses V1 API for accurate values)
        list_data["listSize"] = client.lists.get_size(list_id)
        data = {
            "list": list_data,
            "fields": serialize_models_for_cli(fields),
            "savedViews": serialize_models_for_cli(views),
        }
        return CommandOutput(
            data=data, context=cmd_context, resolved=resolved.resolved, api_called=True
        )

    run_command(ctx, command="list get", fn=fn)


CsvHeaderMode = Literal["names", "ids"]


ExpandChoice = Literal["persons", "companies", "opportunities"]
CsvMode = Literal["flat", "nested"]
ExpandOnError = Literal["raise", "skip"]


@category("read")
@list_group.command(name="export", cls=RichCommand)
@click.argument("list_selector", type=str)
@click.option("--saved-view", type=str, default=None, help="Saved view id or name.")
@click.option("--field", "fields", type=str, multiple=True, help="Field name or id (repeatable).")
@click.option(
    "--filter",
    "filter_expr",
    type=str,
    default=None,
    help='Client-side filter (field op value). Quote multi-word: Status="Intro Meeting".',
)
@click.option(
    "--page-size", "-s", type=int, default=100, show_default=True, help="Page size (max 100)."
)
@click.option(
    "--cursor", type=str, default=None, help="Resume from cursor (incompatible with --page-size)."
)
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N rows total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all rows.")
@click.option(
    "--first-page-only",
    "first_page_only",
    is_flag=True,
    help=(
        "Explicitly opt in to first-page-only results (required with --filter if "
        "--all/--max-results are not set). Sets meta.truncated=true."
    ),
)
@click.option(
    "--company-id",
    "company_ids",
    type=int,
    multiple=True,
    help=(
        "Scope export to specific company IDs (repeatable). Valid on company lists. "
        "Uses the V2 entity->list-entries endpoint. Emits a warning if any ID is not "
        "on the list - the dedup signal for safe-to-create."
    ),
)
@click.option(
    "--person-id",
    "person_ids",
    type=int,
    multiple=True,
    help="Scope export to specific person IDs (repeatable). Valid on person lists.",
)
@click.option(
    "--csv-header",
    type=click.Choice(["names", "ids"]),
    default="names",
    show_default=True,
    help="Use field names or IDs for CSV headers.",
    callback=csv_suboption_callback,
)
@click.option(
    "--csv-bom",
    is_flag=True,
    help="Add UTF-8 BOM for Excel (use with redirection: --csv --csv-bom > file.csv).",
    callback=csv_suboption_callback,
    expose_value=True,
)
@click.option("--dry-run", is_flag=True, help="Validate selectors and print export plan.")
# Expand options (Phase 1)
@click.option(
    "--expand",
    "expand",
    multiple=True,
    type=click.Choice(["persons", "companies", "opportunities", "interactions"]),
    help="Expand associated entities or interaction data (repeatable). "
    "'interactions' adds last/next meeting and email date summaries.",
)
@click.option(
    "--expand-max-results",
    type=int,
    default=100,
    show_default=True,
    help="Max associations per entry per type.",
)
@click.option(
    "--expand-all",
    is_flag=True,
    help="Fetch all associations per entry (no limit).",
)
@click.option(
    "--expand-on-error",
    type=click.Choice(["raise", "skip"]),
    default="raise",
    show_default=True,
    help="How to handle per-entry expansion errors.",
)
@click.option(
    "--csv-mode",
    type=click.Choice(["flat", "nested"]),
    default="flat",
    show_default=True,
    help="CSV expansion format: flat (one row per association) or nested (JSON arrays).",
    callback=csv_suboption_callback,
)
# Phase 4: --expand-fields and --expand-field-type for expanded entity fields
@click.option(
    "--expand-fields",
    "expand_fields",
    multiple=True,
    type=str,
    help="Include specific field by name or ID in expanded entities (repeatable).",
)
@click.option(
    "--expand-field-type",
    "expand_field_types",
    multiple=True,
    type=click.Choice(["global", "enriched", "relationship-intelligence"], case_sensitive=False),
    help="Include all fields of this type in expanded entities (repeatable).",
)
# Phase 5: --expand-filter and --expand-opportunities-list
@click.option(
    "--expand-filter",
    "expand_filter",
    type=str,
    default=None,
    help="Filter expanded entities (e.g., 'field=value' or 'field!=value').",
)
@click.option(
    "--expand-opportunities-list",
    "expand_opps_list",
    type=str,
    default=None,
    help="Scope --expand opportunities to a specific list (id or name).",
)
# Phase 2 enhancement: --check-unreplied (generalized from email-only)
@click.option(
    "--check-unreplied",
    "check_unreplied",
    is_flag=True,
    help="Check for unreplied incoming messages (email/chat). Adds API call per entry.",
)
@click.option(
    "--unreplied-types",
    "unreplied_types",
    type=str,
    default="email,chat",
    show_default=True,
    help="Comma-separated interaction types to check: email, chat, all (shorthand for both).",
)
@click.option(
    "--unreplied-lookback-days",
    "unreplied_lookback_days",
    type=int,
    default=30,
    show_default=True,
    help="Days to look back for unreplied message detection.",
)
@csv_output_options
@click.pass_obj
@apply_mcp_limits()
def list_export(
    ctx: CLIContext,
    list_selector: str,
    *,
    saved_view: str | None,
    fields: tuple[str, ...],
    filter_expr: str | None,
    page_size: int,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
    first_page_only: bool,
    company_ids: tuple[int, ...],
    person_ids: tuple[int, ...],
    csv_header: CsvHeaderMode,
    csv_bom: bool,
    dry_run: bool,
    # Expand options
    expand: tuple[str, ...],
    expand_max_results: int,
    expand_all: bool,
    expand_on_error: str,
    csv_mode: str,
    # Phase 4 options
    expand_fields: tuple[str, ...],
    expand_field_types: tuple[str, ...],
    # Phase 5 options
    expand_filter: str | None,
    expand_opps_list: str | None,
    # Phase 2 enhancement: unreplied messages (email/chat)
    check_unreplied: bool,
    unreplied_types: str,
    unreplied_lookback_days: int,
) -> None:
    """
    Export list entries to JSON or CSV.

    LIST_SELECTOR can be a list id or exact list name.

    Examples:

    - `xaffinity list export "Pipeline" --all`
    - `xaffinity list export 12345 --csv --all > pipeline.csv`
    - `xaffinity list export "Pipeline" --saved-view "Active Deals" --output csv > deals.csv`
    - `xaffinity list export "Pipeline" --field Status --field "Deal Size" --all`
    - `xaffinity list export "Pipeline" --expand persons --all --csv > opps-with-persons.csv`
    - `xaffinity list export "Pipeline" --expand persons --expand companies --all`

    JSON output structure:

        {"data": {"rows": [{"listEntryId": 123, "entityType": "company",
        "entityId": 456, "entityName": "Acme", "Status": "Active", ...}]},
        "meta": {"pagination": {"rows": {"nextCursor": "...", "prevCursor": null}}}}

    Each row always contains: listEntryId, entityType, entityId, entityName,
    plus field values keyed by field name. Note: the key is "rows", not
    "listEntries" or "entries".
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        # Track start time for summary line
        export_start_time = time.time()

        # Parse and validate expand options early
        expand_set = {e.strip().lower() for e in expand if e and e.strip()}
        want_expand = len(expand_set) > 0

        # Validate expand field options require --expand
        if (expand_fields or expand_field_types) and not want_expand:
            raise CLIError(
                "--expand-fields and --expand-field-type require --expand.",
                exit_code=2,
                error_type="usage_error",
                hint="Use --expand persons/companies to expand entity associations.",
            )

        # Parse expand field types to FieldType enum
        parsed_expand_field_types: list[FieldType] | None = None
        if expand_field_types:
            parsed_expand_field_types = []
            for ft in expand_field_types:
                ft_lower = ft.strip().lower()
                if ft_lower == "global":
                    parsed_expand_field_types.append(FieldType.GLOBAL)
                elif ft_lower == "enriched":
                    parsed_expand_field_types.append(FieldType.ENRICHED)
                elif ft_lower == "relationship-intelligence":
                    parsed_expand_field_types.append(FieldType.RELATIONSHIP_INTELLIGENCE)

        # Note: expand_fields will be validated and resolved after client is obtained
        # to enable name→ID resolution via API lookup

        # Validate --expand-filter requires --expand (Phase 5)
        if expand_filter and not want_expand:
            raise CLIError(
                "--expand-filter requires --expand.",
                exit_code=2,
                error_type="usage_error",
                hint="Use --expand persons/companies/opportunities to expand entity associations.",
            )

        # Parse and validate --unreplied-types
        parsed_unreplied_types: list[InteractionType] | None = None
        if check_unreplied:
            parsed_unreplied_types = _parse_unreplied_types(unreplied_types)

        # Validate --expand-opportunities-list requires --expand opportunities (Phase 5)
        if expand_opps_list and "opportunities" not in expand_set:
            raise CLIError(
                "--expand-opportunities-list requires --expand opportunities.",
                exit_code=2,
                error_type="usage_error",
                hint="Use --expand opportunities --expand-opportunities-list <list>.",
            )

        # Parse expand filter expression (Phase 5)
        parsed_expand_filters: FilterExpression | None = None
        if expand_filter:
            try:
                parsed_expand_filters = parse_filter(expand_filter)
            except ValueError as e:
                raise CLIError(
                    f"Invalid expand filter: {e}",
                    exit_code=2,
                    error_type="usage_error",
                    hint=(
                        "Use 'field=value', 'field!=value', 'field=*' (not null), "
                        "or 'field!=*' (is null). "
                        "Combine with '|' (or) and '&' (and)."
                    ),
                ) from e

        if saved_view and filter_expr:
            raise CLIError(
                "--saved-view and --filter are mutually exclusive.",
                exit_code=2,
                error_type="usage_error",
            )
        if cursor and (saved_view or filter_expr or fields):
            raise CLIError(
                "--cursor cannot be combined with --saved-view/--filter/--field.",
                exit_code=2,
                error_type="usage_error",
            )
        if cursor and page_size != 100:
            raise CLIError(
                "--cursor cannot be combined with --page-size (cursor encodes page size).",
                exit_code=2,
                error_type="usage_error",
            )

        if want_expand and cursor:
            raise CLIError(
                "--cursor cannot be combined with --expand.",
                exit_code=2,
                error_type="usage_error",
                hint="For large exports, use streaming CSV output or the SDK with checkpointing.",
            )

        entity_ids_provided = bool(company_ids) or bool(person_ids)
        if entity_ids_provided and (saved_view or filter_expr or cursor):
            raise CLIError(
                "--company-id / --person-id cannot be combined with "
                "--saved-view, --filter, or --cursor.",
                exit_code=2,
                error_type="usage_error",
            )
        if company_ids and person_ids:
            raise CLIError(
                "--company-id and --person-id are mutually exclusive on a single list.",
                exit_code=2,
                error_type="usage_error",
            )

        # --filter on list entries is client-side. Without explicit scope, it silently
        # returns page-1-only results. Require one of --all / --max-results /
        # --first-page-only so the caller is making an informed choice.
        if (
            filter_expr
            and not saved_view
            and not (all_pages or max_results is not None or first_page_only)
        ):
            raise CLIError(
                "--filter on list export fetches pages client-side. Without a scope flag, "
                "this silently returns first-page results and was the root cause of "
                "duplicate-check incidents. Add --all to scan everything, --max-results N "
                "to cap, or --first-page-only to explicitly opt in.",
                exit_code=2,
                error_type="usage_error",
                hint="Prefer --saved-view for server-side filtering on large lists.",
                details={"rejected_filter": str(filter_expr)},
            )

        # Warn about client-side filtering (API doesn't support server-side filtering).
        # Fires for the now-informed path: user opted in via --all / --max-results /
        # --first-page-only but we still note the cost.
        if filter_expr and not saved_view:
            warnings.append(
                "The Affinity API does not support server-side filtering on list entries. "
                "Filtering is being applied client-side after fetching data. "
                "For large lists, consider using saved views instead (--saved-view)."
            )

        # Warn if both --expand-all and --expand-max-results specified
        if expand_all and expand_max_results != 100:
            warnings.append(
                f"--expand-all specified; ignoring --expand-max-results {expand_max_results}"
            )

        # Determine effective expansion limit
        effective_expand_limit: int | None = None if expand_all else expand_max_results

        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        resolved_list = resolve_list_selector(client=client, selector=list_selector, cache=cache)
        list_id = ListId(int(resolved_list.list.id))
        # Note: AffinityModel uses use_enum_values=True, so list.type is an int
        list_type_value = resolved_list.list.type
        list_type = (
            ListType(list_type_value) if isinstance(list_type_value, int) else list_type_value
        )

        if company_ids and list_type != ListType.COMPANY:
            raise CLIError(
                f"--company-id is not valid on {list_type.name.lower()} lists. "
                "Use --person-id for person lists. "
                "Opportunity-list support is tracked separately.",
                exit_code=2,
                error_type="usage_error",
            )
        if person_ids and list_type != ListType.PERSON:
            raise CLIError(
                f"--person-id is not valid on {list_type.name.lower()} lists. "
                "Use --company-id for company lists.",
                exit_code=2,
                error_type="usage_error",
            )

        resolved: dict[str, Any] = dict(resolved_list.resolved)

        # Extract resolved list name for CommandContext (string values only)
        ctx_resolved: dict[str, str] | None = None
        if resolved_list.list.name:
            ctx_resolved = {"listId": resolved_list.list.name}

        # Build CommandContext upfront (used by all return paths)
        ctx_modifiers: dict[str, object] = {}
        if saved_view:
            ctx_modifiers["savedView"] = saved_view
        if fields:
            ctx_modifiers["fields"] = list(fields)
        if filter_expr:
            ctx_modifiers["filter"] = filter_expr
        if page_size != 100:
            ctx_modifiers["pageSize"] = page_size
        if cursor:
            ctx_modifiers["cursor"] = cursor
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["all"] = True
        if first_page_only:
            ctx_modifiers["firstPageOnly"] = True
        if company_ids:
            ctx_modifiers["companyIds"] = list(company_ids)
        if person_ids:
            ctx_modifiers["personIds"] = list(person_ids)
        if ctx.output == "csv":
            ctx_modifiers["csv"] = True
        if expand:
            ctx_modifiers["expand"] = list(expand)
        if dry_run:
            ctx_modifiers["dryRun"] = True

        cmd_context = CommandContext(
            name="list export",
            inputs={"listId": int(list_id)},
            modifiers=ctx_modifiers,
            resolved=ctx_resolved,
        )

        # Validate expand options for list type
        # Note: "interactions" is valid for all list types (adds date summaries for the entity)
        if want_expand:
            valid_expand_for_type: dict[ListType, set[str]] = {
                ListType.OPPORTUNITY: {"persons", "companies", "interactions"},
                ListType.PERSON: {"companies", "opportunities", "interactions"},
                ListType.COMPANY: {"persons", "opportunities", "interactions"},
            }
            valid_for_this_type = valid_expand_for_type.get(list_type, set())
            invalid_expands = expand_set - valid_for_this_type

            if invalid_expands:
                raise CLIError(
                    f"--expand {', '.join(sorted(invalid_expands))} is not valid for "
                    f"{list_type.name.lower()} lists.",
                    exit_code=2,
                    error_type="usage_error",
                    details={"validExpand": sorted(valid_for_this_type)},
                    hint=f"Valid values for {list_type.name.lower()} lists: "
                    f"{', '.join(sorted(valid_for_this_type))}.",
                )

        # Validate and resolve --expand-fields (Phase 4 - Gap 4 fix)
        # Uses API to fetch field metadata and validate field names/IDs
        parsed_expand_fields: list[tuple[str, AnyFieldId]] | None = None
        if expand_fields and want_expand:
            parsed_expand_fields = _validate_and_resolve_expand_fields(
                client=client,
                expand_set=expand_set,
                field_specs=expand_fields,
            )

        # Resolve --expand-opportunities-list if provided (Phase 5)
        resolved_opps_list_id: ListId | None = None
        if expand_opps_list and "opportunities" in expand_set:
            resolved_opps_list = resolve_list_selector(
                client=client, selector=expand_opps_list, cache=cache
            )
            # Validate it's an opportunity list
            opps_list_type_value = resolved_opps_list.list.type
            opps_list_type = (
                ListType(opps_list_type_value)
                if isinstance(opps_list_type_value, int)
                else opps_list_type_value
            )
            if opps_list_type != ListType.OPPORTUNITY:
                raise CLIError(
                    f"--expand-opportunities-list must reference an opportunity list, "
                    f"got {opps_list_type.name.lower()} list.",
                    exit_code=2,
                    error_type="usage_error",
                )
            resolved_opps_list_id = ListId(int(resolved_opps_list.list.id))
            resolved["expandOpportunitiesList"] = {
                "listId": int(resolved_opps_list_id),
                "listName": resolved_opps_list.list.name,
            }

        # Warn about expensive --expand opportunities without scoping (Phase 5)
        if "opportunities" in expand_set and resolved_opps_list_id is None:
            warnings.append(
                "Expanding opportunities without --expand-opportunities-list will search "
                "all opportunity lists. This may be slow for large workspaces. "
                "Consider using --expand-opportunities-list to scope the search."
            )

        # Resolve columns/fields.
        field_meta = list_fields_for_list(client=client, list_id=list_id, cache=cache)
        field_by_id: dict[str, FieldMetadata] = {str(f.id): f for f in field_meta}

        selected_field_ids: list[str] = []
        if saved_view:
            _, view_resolved = resolve_saved_view(
                client=client, list_id=list_id, selector=saved_view, cache=cache
            )
            resolved.update(view_resolved)
            # Note: API's view.field_ids is typically empty; use --field to specify fields
            if fields:
                selected_field_ids = _resolve_field_selectors(
                    fields=fields, field_by_id=field_by_id
                )
            else:
                # No explicit fields requested with saved view - return all fields
                selected_field_ids = [str(f.id) for f in field_meta]
        elif fields:
            selected_field_ids = _resolve_field_selectors(fields=fields, field_by_id=field_by_id)
        else:
            selected_field_ids = [str(f.id) for f in field_meta]

        columns = _columns_meta(selected_field_ids, field_by_id=field_by_id)

        if dry_run:
            want_csv = ctx.output == "csv"
            if want_expand:
                # Cleaner output for --expand mode (omit irrelevant fields like cursor)
                data: dict[str, Any] = {
                    "listId": int(list_id),
                    "listName": resolved_list.list.name,
                    "listType": list_type.name.lower(),
                    "csv": want_csv,
                }
                if filter_expr:
                    data["filter"] = filter_expr
            else:
                # Standard export - show all query params
                data = {
                    "listId": int(list_id),
                    "listName": resolved_list.list.name,
                    "listType": list_type.name.lower(),
                    "savedView": saved_view,
                    "fieldIds": selected_field_ids,
                    "filter": filter_expr,
                    "pageSize": page_size,
                    "cursor": cursor,
                    "csv": want_csv,
                }
            if want_expand:
                # Estimate API calls for expansion (get_size uses V1 API for accurate values)
                entry_count = client.lists.get_size(list_id)
                expand_calls = entry_count  # 1 call per entry (optimized for dual)
                data["expand"] = sorted(expand_set)
                data["expandMaxResults"] = effective_expand_limit
                data["csvMode"] = csv_mode if want_csv else None
                # Add dry run warnings
                dry_run_warnings: list[str] = []
                # Handle unreliable listSize from API (often returns 0 for non-empty lists)
                if entry_count == 0:
                    data["estimatedEntries"] = "unknown (API metadata unavailable)"
                    data["estimatedApiCalls"] = "unknown"
                    data["estimatedDuration"] = "unknown"
                    dry_run_warnings.append(
                        "Cannot estimate - Affinity API reports 0 entries but list may "
                        "contain data. The export will fetch all available entries."
                    )
                else:
                    data["estimatedEntries"] = entry_count
                    data["estimatedApiCalls"] = {
                        "listEntries": max(1, entry_count // page_size),
                        "associations": expand_calls,
                        "total": max(1, entry_count // page_size) + expand_calls,
                        "note": (
                            "Using get_associations() optimization "
                            "(both persons+companies in 1 call per entry)"
                            if "persons" in expand_set and "companies" in expand_set
                            else "1 call per entry"
                        ),
                    }
                    # Estimate duration based on entry count
                    if entry_count <= 50:
                        data["estimatedDuration"] = "~30 seconds to 1 minute"
                    elif entry_count <= 150:
                        data["estimatedDuration"] = f"~2-5 minutes for {entry_count} entries"
                    elif entry_count <= 500:
                        data["estimatedDuration"] = f"~5-10 minutes for {entry_count} entries"
                    else:
                        data["estimatedDuration"] = f"~10-20+ minutes for {entry_count} entries"
                    if entry_count > 1000:
                        dry_run_warnings.append(
                            f"Large export ({entry_count} entries) may take 10-15 minutes or more."
                        )
                dry_run_warnings.append(
                    "Expansion of related entities may be slower for large datasets."
                )
                if effective_expand_limit is not None:
                    dry_run_warnings.append(
                        f"Using --expand-max-results {effective_expand_limit} (default). "
                        "Some entries may have more associations. "
                        "Use --expand-all for complete data."
                    )
                data["warnings"] = dry_run_warnings
            return CommandOutput(
                data=data,
                context=cmd_context,
                resolved=resolved,
                columns=columns,
                api_called=True,
            )

        # Entity-scoped export via --company-id / --person-id.
        # Uses the V2 entity->list-entries endpoint per ID, then fetches the full
        # ListEntryWithEntity (with field values) for each match. Bypasses the
        # streaming/expand machinery — this is a targeted lookup path, not a bulk
        # export. Primary use case: dedup-check before `list add-entry`.
        if entity_ids_provided:
            entity_rows: list[dict[str, Any]] = []
            missing_ids: list[int] = []
            entries_service = client.lists.entries(list_id)

            if company_ids:
                id_source: tuple[int, ...] = company_ids
                finder: Callable[[Any], list[Any]] = entries_service.find_all_company
                id_ctor: Callable[[int], Any] = CompanyId
                missing_label = "company_ids"
            else:
                id_source = person_ids
                finder = entries_service.find_all_person
                id_ctor = PersonId
                missing_label = "person_ids"

            for raw_id in id_source:
                typed = id_ctor(int(raw_id))
                stub_entries = finder(typed)
                if not stub_entries:
                    missing_ids.append(int(raw_id))
                    continue
                for stub in stub_entries:
                    full = entries_service.get(ListEntryId(int(stub.id)))
                    entity_rows.append(
                        _entry_to_row(full, selected_field_ids, field_by_id, key_mode="names")
                    )

            if missing_ids:
                warnings.append(f"Not on this list: {missing_label}={missing_ids}")

            return CommandOutput(
                data={"rows": entity_rows},
                context=cmd_context,
                resolved=resolved,
                columns=columns,
                api_called=True,
            )

        # Build expand field data structures from parsed_expand_fields
        # - expand_field_ids: list of field IDs for API calls
        # - field_id_to_display: dict mapping field ID (str) -> display name (original spec)
        expand_field_ids: list[AnyFieldId] | None = None
        field_id_to_display: dict[str, str] | None = None
        if parsed_expand_fields:
            expand_field_ids = [field_id for _, field_id in parsed_expand_fields]
            field_id_to_display = {
                str(field_id): original for original, field_id in parsed_expand_fields
            }

        # Prepare CSV writing.
        want_csv = ctx.output == "csv"
        rows_written = 0
        next_cursor: str | None = None

        # Helper to format progress description with association counts
        def _format_progress_desc(
            entries: int,
            total: int | None,
            persons_count: int,
            companies_count: int,
            opportunities_count: int,
            interactions_count: int,
            expand_set: set[str],
        ) -> str:
            if total and total > 0:
                pct = int(100 * entries / total)
                desc = f"Exporting: {entries}/{total} entries ({pct}%)"
            else:
                desc = f"Exporting: {entries} entries"
            if expand_set:
                parts = []
                if "persons" in expand_set and persons_count > 0:
                    parts.append(f"{persons_count} persons")
                if "companies" in expand_set and companies_count > 0:
                    parts.append(f"{companies_count} companies")
                if "opportunities" in expand_set and opportunities_count > 0:
                    parts.append(f"{opportunities_count} opportunities")
                if "interactions" in expand_set and interactions_count > 0:
                    parts.append(f"{interactions_count} interactions")
                if parts:
                    desc += ", " + " + ".join(parts)
            return desc

        def _format_filter_progress(state: dict[str, Any] | None) -> str | None:
            """Format progress description for filtered queries.

            Returns a description showing scanning context. The progress bar
            separately shows the exported row count, so we don't duplicate that here.
            """
            if state is None:
                return None
            filter_stats = state.get("filterStats")
            if filter_stats is None:
                return None
            scanned = filter_stats.get("scanned", 0)
            matched = filter_stats.get("matched", 0)
            return f"Exporting ({matched} matches from {scanned} scanned)"

        with ExitStack() as stack:
            progress: Progress | None = None
            task_id: TaskID | None = None
            show_progress = (
                ctx.progress != "never"
                and not ctx.quiet
                and (ctx.progress == "always" or sys.stderr.isatty())
            )
            entry_total = client.lists.get_size(list_id) if want_expand else None
            if show_progress:
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
                initial_desc = (
                    "Exporting"
                    if not want_expand
                    else _format_progress_desc(0, entry_total, 0, 0, 0, 0, expand_set)
                )
                task_id = progress.add_task(
                    initial_desc, total=max_results if max_results else None
                )

            if want_csv:
                field_headers = [
                    (
                        (field_by_id[fid].name if fid in field_by_id else fid)
                        if csv_header == "names"
                        else fid
                    )
                    for fid in selected_field_ids
                ]
                base_header = [
                    "listEntryId",
                    "entityType",
                    "entityId",
                    "entityName",
                    *field_headers,
                ]

                # Add expansion columns if needed
                if want_expand or check_unreplied:
                    header = _expand_csv_headers(
                        base_header,
                        expand_set,
                        csv_mode,
                        expand_fields=parsed_expand_fields,
                        header_mode=csv_header,
                        check_unreplied=check_unreplied,
                    )
                else:
                    header = base_header

                csv_iter_state: dict[str, Any] = {}
                entries_with_truncated_assoc: list[int] = []
                skipped_entries: list[int] = []
                entries_with_large_nested_assoc: list[int] = []
                csv_associations_fetched: dict[str, int] = {
                    "persons": 0,
                    "companies": 0,
                    "opportunities": 0,
                    "interactions": 0,
                }
                # Cache for person name resolution (shared across all entries)
                person_name_cache: dict[int, str] = {}
                csv_entries_processed = 0

                def iter_rows() -> Any:
                    nonlocal rows_written, next_cursor, csv_entries_processed

                    # Rate limiting for MCP progress (0.65s interval)
                    last_mcp_progress_time: float = float("-inf")
                    # MCP mode: emit JSON progress when not TTY but progress still desired
                    # IMPORTANT: If Rich progress bar is active (show_progress=True),
                    # don't also emit JSON progress - they're mutually exclusive
                    mcp_mode = (
                        not show_progress
                        and not sys.stderr.isatty()
                        and ctx.progress != "never"
                        and not ctx.quiet
                    )

                    # Create callback for real-time filter progress updates
                    def on_filter_progress(stats: FilterStats) -> None:
                        nonlocal last_mcp_progress_time

                        desc = f"Scanning {stats.scanned}... ({stats.matched} matches)"

                        # Rich Progress bar (TTY)
                        if progress is not None and task_id is not None:
                            progress.update(task_id, description=desc)

                        # NDJSON for MCP (non-TTY) with rate limiting
                        if mcp_mode:
                            now = time.monotonic()
                            if now - last_mcp_progress_time >= 0.65:
                                last_mcp_progress_time = now
                                obj = {"type": "progress", "progress": None, "message": desc}
                                print(json.dumps(obj), file=sys.stderr, flush=True)

                    # Use callback if we have a filter and either Rich progress or MCP mode
                    has_rich_progress = progress is not None and task_id is not None
                    filter_callback = (
                        on_filter_progress
                        if filter_expr and (mcp_mode or has_rich_progress)
                        else None
                    )

                    for row, page_next_cursor in _iterate_list_entries(
                        client=client,
                        list_id=list_id,
                        saved_view=saved_view,
                        filter_expr=filter_expr,
                        selected_field_ids=selected_field_ids,
                        page_size=page_size,
                        cursor=cursor,
                        max_results=max_results,
                        all_pages=all_pages,
                        field_by_id=field_by_id,
                        key_mode=csv_header,
                        state=csv_iter_state,
                        cache=cache,
                        filter_progress_callback=filter_callback,
                    ):
                        next_cursor = page_next_cursor

                        if not want_expand and not check_unreplied:
                            # No expansion and no unreplied email check - yield row as-is
                            rows_written += 1
                            if progress is not None and task_id is not None:
                                filter_desc = _format_filter_progress(csv_iter_state)
                                if filter_desc:
                                    progress.update(
                                        task_id,
                                        completed=rows_written,
                                        description=filter_desc,
                                    )
                                else:
                                    progress.update(task_id, completed=rows_written)
                            yield row
                            continue

                        # Handle expansion for opportunity lists
                        entity_id = row.get("entityId")
                        if entity_id is None:
                            # No entity - emit row with empty expansion columns
                            expanded_row = dict(row)
                            expanded_row["expandedType"] = ""
                            expanded_row["expandedId"] = ""
                            expanded_row["expandedName"] = ""
                            if "persons" in expand_set:
                                expanded_row["expandedEmail"] = ""
                            if "companies" in expand_set:
                                expanded_row["expandedDomain"] = ""
                            rows_written += 1
                            if progress is not None and task_id is not None:
                                progress.update(task_id, completed=rows_written)
                            yield expanded_row
                            continue

                        # Fetch associations based on list type
                        # For flat CSV mode, use prefixed field keys (person.X, company.X)
                        # For nested CSV mode, use unprefixed keys in JSON arrays
                        result = _fetch_associations(
                            client=client,
                            list_type=list_type,
                            entity_id=entity_id,
                            expand_set=expand_set,
                            max_results=effective_expand_limit,
                            on_error=expand_on_error,
                            warnings=warnings,
                            expand_field_types=parsed_expand_field_types,
                            expand_field_ids=expand_field_ids,
                            expand_filters=parsed_expand_filters,
                            expand_opps_list_id=resolved_opps_list_id,
                            field_id_to_display=field_id_to_display,
                            prefix_fields=(csv_mode == "flat"),
                            person_name_cache=person_name_cache,
                            check_unreplied=check_unreplied,
                            unreplied_types=parsed_unreplied_types,
                            unreplied_lookback_days=unreplied_lookback_days,
                        )

                        if result is None:
                            # Error occurred and on_error='skip'
                            skipped_entries.append(entity_id)
                            continue

                        (
                            persons,
                            companies,
                            opportunities,
                            interactions_data,
                            unreplied_data,
                        ) = result
                        csv_entries_processed += 1
                        csv_associations_fetched["persons"] += len(persons)
                        csv_associations_fetched["companies"] += len(companies)
                        csv_associations_fetched["opportunities"] += len(opportunities)
                        if interactions_data is not None:
                            csv_associations_fetched["interactions"] += 1

                        # Update progress description with association counts
                        if progress is not None and task_id is not None:
                            progress.update(
                                task_id,
                                description=_format_progress_desc(
                                    csv_entries_processed,
                                    entry_total,
                                    csv_associations_fetched["persons"],
                                    csv_associations_fetched["companies"],
                                    csv_associations_fetched["opportunities"],
                                    csv_associations_fetched["interactions"],
                                    expand_set,
                                ),
                            )

                        # Check for truncation
                        if effective_expand_limit is not None and (
                            len(persons) >= effective_expand_limit
                            or len(companies) >= effective_expand_limit
                            or len(opportunities) >= effective_expand_limit
                        ):
                            entries_with_truncated_assoc.append(entity_id)

                        # Handle CSV mode
                        if csv_mode == "flat":
                            # Flat mode: one row per association
                            emitted_any = False

                            # Prepare interaction columns (added to every row)
                            interaction_cols: dict[str, str] = {}
                            if "interactions" in expand_set:
                                from affinity.cli.interaction_utils import (
                                    INTERACTION_CSV_COLUMNS,
                                    flatten_interactions_for_csv,
                                )

                                interaction_cols = flatten_interactions_for_csv(interactions_data)

                            # Prepare unreplied email columns (added to every row)
                            unreplied_cols: dict[str, str] = {}
                            if check_unreplied:
                                from affinity.cli.interaction_utils import (
                                    UNREPLIED_CSV_COLUMNS,
                                    flatten_unreplied_for_csv,
                                )

                                unreplied_cols = flatten_unreplied_for_csv(unreplied_data)

                            def _add_interaction_cols(
                                row_dict: dict[str, Any],
                                cols: dict[str, str] = interaction_cols,
                            ) -> None:
                                """Add interaction columns to an expanded row."""
                                if "interactions" in expand_set:
                                    for col in INTERACTION_CSV_COLUMNS:
                                        row_dict[col] = cols.get(col, "")

                            def _add_unreplied_cols(
                                row_dict: dict[str, Any],
                                cols: dict[str, str] = unreplied_cols,
                            ) -> None:
                                """Add unreplied email columns to an expanded row."""
                                if check_unreplied:
                                    for col in UNREPLIED_CSV_COLUMNS:
                                        row_dict[col] = cols.get(col, "")

                            # Emit person rows
                            for person in persons:
                                expanded_row = dict(row)
                                expanded_row["expandedType"] = "person"
                                expanded_row["expandedId"] = person["id"]
                                expanded_row["expandedName"] = person["name"]
                                if "persons" in expand_set:
                                    expanded_row["expandedEmail"] = person.get("primaryEmail") or ""
                                if "companies" in expand_set:
                                    expanded_row["expandedDomain"] = ""
                                if "opportunities" in expand_set:
                                    expanded_row["expandedListId"] = ""
                                # Copy prefixed field values (Phase 4)
                                for key, val in person.items():
                                    if key.startswith("person."):
                                        expanded_row[key] = val if val is not None else ""
                                _add_interaction_cols(expanded_row)
                                _add_unreplied_cols(expanded_row)
                                rows_written += 1
                                emitted_any = True
                                if progress is not None and task_id is not None:
                                    progress.update(task_id, completed=rows_written)
                                yield expanded_row

                            # Emit company rows
                            for company in companies:
                                expanded_row = dict(row)
                                expanded_row["expandedType"] = "company"
                                expanded_row["expandedId"] = company["id"]
                                expanded_row["expandedName"] = company["name"]
                                if "persons" in expand_set:
                                    expanded_row["expandedEmail"] = ""
                                if "companies" in expand_set:
                                    expanded_row["expandedDomain"] = company.get("domain") or ""
                                if "opportunities" in expand_set:
                                    expanded_row["expandedListId"] = ""
                                # Copy prefixed field values (Phase 4)
                                for key, val in company.items():
                                    if key.startswith("company."):
                                        expanded_row[key] = val if val is not None else ""
                                _add_interaction_cols(expanded_row)
                                _add_unreplied_cols(expanded_row)
                                rows_written += 1
                                emitted_any = True
                                if progress is not None and task_id is not None:
                                    progress.update(task_id, completed=rows_written)
                                yield expanded_row

                            # Emit opportunity rows (Phase 5)
                            for opp in opportunities:
                                expanded_row = dict(row)
                                expanded_row["expandedType"] = "opportunity"
                                expanded_row["expandedId"] = opp["id"]
                                expanded_row["expandedName"] = opp.get("name") or ""
                                if "persons" in expand_set:
                                    expanded_row["expandedEmail"] = ""
                                if "companies" in expand_set:
                                    expanded_row["expandedDomain"] = ""
                                if "opportunities" in expand_set:
                                    expanded_row["expandedListId"] = opp.get("listId") or ""
                                _add_interaction_cols(expanded_row)
                                _add_unreplied_cols(expanded_row)
                                rows_written += 1
                                emitted_any = True
                                if progress is not None and task_id is not None:
                                    progress.update(task_id, completed=rows_written)
                                yield expanded_row

                            # If no associations, emit one row with empty expansion columns
                            # (but interaction columns will still have data if available)
                            if not emitted_any:
                                expanded_row = dict(row)
                                expanded_row["expandedType"] = ""
                                expanded_row["expandedId"] = ""
                                expanded_row["expandedName"] = ""
                                if "persons" in expand_set:
                                    expanded_row["expandedEmail"] = ""
                                if "companies" in expand_set:
                                    expanded_row["expandedDomain"] = ""
                                if "opportunities" in expand_set:
                                    expanded_row["expandedListId"] = ""
                                _add_interaction_cols(expanded_row)
                                _add_unreplied_cols(expanded_row)
                                rows_written += 1
                                if progress is not None and task_id is not None:
                                    progress.update(task_id, completed=rows_written)
                                yield expanded_row

                        else:
                            # Nested mode: JSON arrays in columns
                            total_assoc = len(persons) + len(companies) + len(opportunities)
                            if total_assoc > 100:
                                entries_with_large_nested_assoc.append(entity_id)
                            expanded_row = dict(row)
                            if "persons" in expand_set:
                                persons_json = json.dumps(persons) if persons else "[]"
                                expanded_row["_expand_persons"] = persons_json
                            if "companies" in expand_set:
                                companies_json = json.dumps(companies) if companies else "[]"
                                expanded_row["_expand_companies"] = companies_json
                            if "opportunities" in expand_set:
                                opps_json = json.dumps(opportunities) if opportunities else "[]"
                                expanded_row["_expand_opportunities"] = opps_json
                            if "interactions" in expand_set:
                                # Interactions is a single dict, not an array
                                interactions_json = (
                                    json.dumps(interactions_data) if interactions_data else "{}"
                                )
                                expanded_row["_expand_interactions"] = interactions_json
                            if check_unreplied:
                                unreplied_json = (
                                    json.dumps(unreplied_data) if unreplied_data else "null"
                                )
                                expanded_row["_expand_unreplied"] = unreplied_json
                            rows_written += 1
                            if progress is not None and task_id is not None:
                                progress.update(task_id, completed=rows_written)
                            yield expanded_row

                # Write CSV to stdout
                try:
                    write_csv_to_stdout(
                        rows=iter_rows(),
                        fieldnames=header,
                        bom=csv_bom,
                    )
                except KeyboardInterrupt:
                    # Partial output already sent to stdout
                    Console(file=sys.stderr).print(f"\nInterrupted ({rows_written} rows written)")
                    sys.exit(130)

                # Print warnings to stderr before exit
                if entries_with_truncated_assoc:
                    count = len(entries_with_truncated_assoc)
                    Console(file=sys.stderr).print(
                        f"Warning: {count} entries had associations truncated at "
                        f"{effective_expand_limit} (use --expand-all for complete data)"
                    )

                if entries_with_large_nested_assoc and csv_mode == "nested":
                    count = len(entries_with_large_nested_assoc)
                    first_id = entries_with_large_nested_assoc[0]
                    Console(file=sys.stderr).print(
                        f"Warning: {count} entries have >100 associations. "
                        f"Large nested arrays may impact memory (e.g., entry {first_id}). "
                        "Consider --csv-mode flat."
                    )

                if skipped_entries:
                    if len(skipped_entries) <= 10:
                        ids_str = ", ".join(str(eid) for eid in skipped_entries)
                        Console(file=sys.stderr).print(
                            f"Warning: {len(skipped_entries)} entries skipped due to errors: "
                            f"{ids_str} (use --expand-on-error raise to fail on errors)"
                        )
                    else:
                        first_ids = ", ".join(str(eid) for eid in skipped_entries[:5])
                        Console(file=sys.stderr).print(
                            f"Warning: {len(skipped_entries)} entries skipped due to errors "
                            f"(first 5: {first_ids}, ...) "
                            "(use --expand-on-error raise to fail on errors)"
                        )

                if csv_iter_state.get("truncatedMidPage") is True:
                    Console(file=sys.stderr).print(
                        "Warning: Results limited by --max-results. Use --all to fetch all results."
                    )

                # Asymmetry: CSV streams to stdout before CommandOutput is built, so
                # meta.truncated/truncationReason can't be populated after the fact.
                # Emit an equivalent stderr warning instead. (With --filter the SDK
                # virtualizes pages and next_cursor is always None, so we key off
                # the explicit opt-in flag.)
                if first_page_only:
                    Console(file=sys.stderr).print(
                        "Warning: Results limited to first page by --first-page-only "
                        "(more pages may be available). Use --all or --max-results "
                        "for broader scans."
                    )

                # Print export summary to stderr
                if show_progress:
                    elapsed = time.time() - export_start_time
                    filter_stats = csv_iter_state.get("filterStats")
                    if filter_stats:
                        scanned = filter_stats.get("scanned", 0)
                        Console(file=sys.stderr).print(
                            f"Exported {rows_written:,} rows "
                            f"(filtered from {scanned:,} scanned) "
                            f"in {format_duration(elapsed)}"
                        )
                    else:
                        Console(file=sys.stderr).print(
                            f"Exported {rows_written:,} rows in {format_duration(elapsed)}"
                        )

                sys.exit(0)

            # JSON/table rows in-memory (small exports).
            # Emit memory warning for large JSON exports with expansion
            if want_expand:
                entry_count = client.lists.get_size(list_id)
                # Rough estimate: each entry with associations is ~1KB
                estimated_rows = entry_count
                if estimated_rows > 1000:
                    warnings.append(
                        f"JSON output will buffer ~{estimated_rows} rows in memory. "
                        "For large exports, consider --csv for streaming output."
                    )

            rows: list[dict[str, Any]] = []
            table_iter_state: dict[str, Any] = {}
            json_entries_with_truncated_assoc: list[int] = []
            json_skipped_entries: list[int] = []
            associations_fetched: dict[str, int] = {
                "persons": 0,
                "companies": 0,
                "opportunities": 0,
                "interactions": 0,
            }
            # Cache for person name resolution (shared across all entries)
            json_person_name_cache: dict[int, str] = {}

            # Rate limiting for MCP progress (0.65s interval)
            json_last_mcp_progress_time: float = float("-inf")
            # MCP mode: emit JSON progress when not TTY but progress still desired
            # IMPORTANT: If Rich progress bar is active (show_progress=True),
            # don't also emit JSON progress - they're mutually exclusive
            json_mcp_mode = (
                not show_progress
                and not sys.stderr.isatty()
                and ctx.progress != "never"
                and not ctx.quiet
            )

            # Create callback for real-time filter progress updates (JSON output)
            def on_json_filter_progress(stats: FilterStats) -> None:
                nonlocal json_last_mcp_progress_time

                desc = f"Scanning {stats.scanned}... ({stats.matched} matches)"

                # Rich Progress bar (TTY)
                if progress is not None and task_id is not None:
                    progress.update(task_id, description=desc)

                # NDJSON for MCP (non-TTY) with rate limiting
                if json_mcp_mode:
                    now = time.monotonic()
                    if now - json_last_mcp_progress_time >= 0.65:
                        json_last_mcp_progress_time = now
                        print(
                            json.dumps({"type": "progress", "progress": None, "message": desc}),
                            file=sys.stderr,
                            flush=True,
                        )

            # Use callback if we have a filter and either Rich progress or MCP mode
            json_filter_callback = (
                on_json_filter_progress
                if filter_expr and (json_mcp_mode or (progress is not None and task_id is not None))
                else None
            )

            for row, page_next_cursor in _iterate_list_entries(
                client=client,
                list_id=list_id,
                saved_view=saved_view,
                filter_expr=filter_expr,
                selected_field_ids=selected_field_ids,
                page_size=page_size,
                cursor=cursor,
                max_results=max_results,
                all_pages=all_pages,
                field_by_id=field_by_id,
                key_mode="names",
                state=table_iter_state,
                cache=cache,
                filter_progress_callback=json_filter_callback,
            ):
                next_cursor = page_next_cursor

                if not want_expand and not check_unreplied:
                    # No expansion and no unreplied email check - add row as-is
                    rows.append(row)
                    if progress is not None and task_id is not None:
                        filter_desc = _format_filter_progress(table_iter_state)
                        if filter_desc:
                            progress.update(task_id, completed=len(rows), description=filter_desc)
                        else:
                            progress.update(task_id, completed=len(rows))
                    continue

                # Handle expansion for JSON output (nested arrays)
                entity_id = row.get("entityId")
                if entity_id is None:
                    # No entity - add row with empty arrays
                    expanded_row = dict(row)
                    if "persons" in expand_set:
                        expanded_row["persons"] = []
                    if "companies" in expand_set:
                        expanded_row["companies"] = []
                    if "opportunities" in expand_set:
                        expanded_row["opportunities"] = []
                    expanded_row["associations"] = "—"
                    rows.append(expanded_row)
                    if progress is not None and task_id is not None:
                        progress.update(task_id, completed=len(rows))
                    continue

                # Fetch associations based on list type
                # For JSON output, use unprefixed field keys in nested arrays
                result = _fetch_associations(
                    client=client,
                    list_type=list_type,
                    entity_id=entity_id,
                    expand_set=expand_set,
                    max_results=effective_expand_limit,
                    on_error=expand_on_error,
                    warnings=warnings,
                    expand_field_types=parsed_expand_field_types,
                    expand_field_ids=expand_field_ids,
                    expand_filters=parsed_expand_filters,
                    expand_opps_list_id=resolved_opps_list_id,
                    field_id_to_display=field_id_to_display,
                    prefix_fields=False,
                    person_name_cache=json_person_name_cache,
                    check_unreplied=check_unreplied,
                    unreplied_types=parsed_unreplied_types,
                    unreplied_lookback_days=unreplied_lookback_days,
                )

                if result is None:
                    # Error occurred and on_error='skip' - skip this entry entirely
                    json_skipped_entries.append(entity_id)
                    continue

                persons, companies, opportunities, interactions_data, unreplied_data = result

                # Check for truncation
                if effective_expand_limit is not None and (
                    len(persons) >= effective_expand_limit
                    or len(companies) >= effective_expand_limit
                    or len(opportunities) >= effective_expand_limit
                ):
                    json_entries_with_truncated_assoc.append(entity_id)

                # Track counts
                associations_fetched["persons"] += len(persons)
                associations_fetched["companies"] += len(companies)
                associations_fetched["opportunities"] += len(opportunities)
                if interactions_data is not None:
                    associations_fetched["interactions"] += 1

                # Update progress description with association counts
                if progress is not None and task_id is not None:
                    progress.update(
                        task_id,
                        description=_format_progress_desc(
                            len(rows) + 1,  # +1 for current entry being processed
                            entry_total,
                            associations_fetched["persons"],
                            associations_fetched["companies"],
                            associations_fetched["opportunities"],
                            associations_fetched["interactions"],
                            expand_set,
                        ),
                    )

                # Add nested arrays to row
                expanded_row = dict(row)
                if "persons" in expand_set:
                    expanded_row["persons"] = persons
                if "companies" in expand_set:
                    expanded_row["companies"] = companies
                if "opportunities" in expand_set:
                    expanded_row["opportunities"] = opportunities
                if "interactions" in expand_set:
                    expanded_row["interactions"] = interactions_data
                if check_unreplied:
                    expanded_row["unreplied"] = unreplied_data

                # Add associations summary for table mode
                summary_parts = []
                if "persons" in expand_set:
                    pc = len(persons)
                    if pc > 0:
                        label = "+ persons" if pc >= 100 else " person" if pc == 1 else " persons"
                        summary_parts.append(f"{pc}{label}")
                if "companies" in expand_set:
                    cc = len(companies)
                    if cc > 0:
                        if cc >= 100:
                            label = "+ companies"
                        elif cc == 1:
                            label = " company"
                        else:
                            label = " companies"
                        summary_parts.append(f"{cc}{label}")
                if "opportunities" in expand_set:
                    oc = len(opportunities)
                    if oc > 0:
                        if oc >= 100:
                            label = "+ opps"
                        elif oc == 1:
                            label = " opp"
                        else:
                            label = " opps"
                        summary_parts.append(f"{oc}{label}")
                assoc_summary = ", ".join(summary_parts) if summary_parts else "—"
                expanded_row["associations"] = assoc_summary

                rows.append(expanded_row)
                if progress is not None and task_id is not None:
                    progress.update(task_id, completed=len(rows))

            if table_iter_state.get("truncatedMidPage") is True:
                warnings.append("Results limited by --max-results. Use --all to fetch all results.")

            # Add truncation warning for JSON output
            if json_entries_with_truncated_assoc:
                count = len(json_entries_with_truncated_assoc)
                warnings.append(
                    f"{count} entries had associations truncated at {effective_expand_limit} "
                    "(use --expand-all for complete data)"
                )

            # Add skipped entries summary for JSON output with IDs
            if json_skipped_entries:
                if len(json_skipped_entries) <= 10:
                    ids_str = ", ".join(str(eid) for eid in json_skipped_entries)
                    warnings.append(
                        f"{len(json_skipped_entries)} entries skipped due to errors: {ids_str} "
                        "(use --expand-on-error raise to fail on errors)"
                    )
                else:
                    first_ids = ", ".join(str(eid) for eid in json_skipped_entries[:5])
                    warnings.append(
                        f"{len(json_skipped_entries)} entries skipped due to errors "
                        f"(first 5: {first_ids}, ...) "
                        "(use --expand-on-error raise to fail on errors)"
                    )

            # Build output data
            output_data: dict[str, Any] = {"rows": rows}
            if want_expand:
                output_data["entriesProcessed"] = len(rows) + len(json_skipped_entries)
                output_data["associationsFetched"] = {
                    k: v for k, v in associations_fetched.items() if k in expand_set
                }

            # Print export summary to stderr
            if show_progress:
                elapsed = time.time() - export_start_time
                filter_stats = table_iter_state.get("filterStats")
                if filter_stats:
                    scanned = filter_stats.get("scanned", 0)
                    Console(file=sys.stderr).print(
                        f"Exported {len(rows):,} rows "
                        f"(filtered from {scanned:,} scanned) "
                        f"in {format_duration(elapsed)}"
                    )
                else:
                    Console(file=sys.stderr).print(
                        f"Exported {len(rows):,} rows in {format_duration(elapsed)}"
                    )

            return CommandOutput(
                data=output_data,
                context=cmd_context,
                pagination={"rows": {"nextCursor": next_cursor, "prevCursor": None}}
                if next_cursor
                else None,
                resolved=resolved,
                columns=columns,
                api_called=True,
                # Note: when --filter is set, the SDK virtualizes pages and sets
                # next_cursor=None on every yield, so next_cursor alone can't
                # detect unfetched pages. --first-page-only is an explicit opt-in
                # to truncation, so mark truncated=True whenever the flag is set.
                truncated=True if first_page_only else None,
                truncation_reason="firstPageOnly" if first_page_only else None,
            )
        raise AssertionError("unreachable")

    run_command(ctx, command="list export", fn=fn)


def _resolve_field_selectors(
    *,
    fields: tuple[str, ...],
    field_by_id: dict[str, FieldMetadata],
) -> list[str]:
    resolved: list[str] = []
    # Build name index for list-scoped fields
    by_name: dict[str, list[str]] = {}
    for fid, meta in field_by_id.items():
        by_name.setdefault(meta.name.lower(), []).append(fid)

    for raw in fields:
        raw = raw.strip()
        if not raw:
            continue
        if raw.isdigit():
            resolved.append(raw)
            continue
        # treat as ID if exact key exists
        if raw in field_by_id:
            resolved.append(raw)
            continue
        matches = by_name.get(raw.lower(), [])
        if not matches:
            raise CLIError(f'Unknown field: "{raw}"', exit_code=2, error_type="usage_error")
        if len(matches) > 1:
            raise CLIError(
                f'Ambiguous field name: "{raw}"',
                exit_code=2,
                error_type="ambiguous_resolution",
                details={"name": raw, "fieldIds": matches},
            )
        resolved.append(matches[0])
    return resolved


def _columns_meta(
    field_ids: list[str],
    *,
    field_by_id: dict[str, FieldMetadata],
) -> list[dict[str, Any]]:
    cols: list[dict[str, Any]] = []
    for fid in field_ids:
        meta = field_by_id.get(fid)
        cols.append(
            {
                "fieldId": fid,
                "fieldName": meta.name if meta else fid,
                "fieldType": meta.type if meta else None,
                "valueType": meta.value_type if meta else None,
            }
        )
    return cols


def _iterate_list_entries(
    *,
    client: Any,
    list_id: ListId,
    saved_view: str | None,
    filter_expr: str | None,
    selected_field_ids: list[str],
    page_size: int,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
    field_by_id: dict[str, FieldMetadata],
    key_mode: Literal["names", "ids"],
    state: dict[str, Any] | None = None,
    cache: Any = None,
    filter_progress_callback: Callable[[FilterStats], None] | None = None,
) -> Any:
    """
    Yield `(row_dict, next_cursor)` where `next_cursor` resumes at the next page (not per-row).

    Args:
        filter_progress_callback: Optional callback invoked after each physical page
            fetch during filtered queries. Useful for real-time progress updates
            while scanning many rows with few matches.
    """
    # Suppress SDK's client-side filtering warning (CLI handles this warning itself)
    stdlib_warnings.filterwarnings(
        "ignore",
        message=".*does not support server-side filtering.*",
        category=UserWarning,
    )

    fetched = 0

    entries = client.lists.entries(list_id)

    if saved_view:
        next_page_cursor: str | None = None
        if cursor:
            page = entries.list(cursor=cursor)
        else:
            view, _ = resolve_saved_view(
                client=client, list_id=list_id, selector=saved_view, cache=cache
            )
            page = entries.from_saved_view(view.id, field_ids=selected_field_ids, limit=page_size)

        next_page_cursor = page.pagination.next_cursor
        for idx, entry in enumerate(page.data):
            fetched += 1
            yield (
                _entry_to_row(entry, selected_field_ids, field_by_id, key_mode=key_mode),
                None
                if max_results is not None and fetched >= max_results and idx < (len(page.data) - 1)
                else next_page_cursor,
            )
            if max_results is not None and fetched >= max_results:
                if idx < (len(page.data) - 1) and state is not None:
                    state["truncatedMidPage"] = True
                return

        if not all_pages and max_results is None:
            return

        while next_page_cursor:
            page = entries.list(cursor=next_page_cursor)
            next_page_cursor = page.pagination.next_cursor
            for idx, entry in enumerate(page.data):
                fetched += 1
                yield (
                    _entry_to_row(entry, selected_field_ids, field_by_id, key_mode=key_mode),
                    None
                    if max_results is not None
                    and fetched >= max_results
                    and idx < (len(page.data) - 1)
                    else next_page_cursor,
                )
                if max_results is not None and fetched >= max_results:
                    if idx < (len(page.data) - 1) and state is not None:
                        state["truncatedMidPage"] = True
                    return
        return

    pages = (
        entries.pages(cursor=cursor)
        if cursor is not None
        else entries.pages(
            field_ids=selected_field_ids,
            filter=filter_expr,
            limit=page_size,
            progress_callback=filter_progress_callback,
        )
    )

    first_page = True
    for page in pages:
        next_page_cursor = page.pagination.next_cursor
        # Track filter stats for progress reporting
        if state is not None and page.filter_stats is not None:
            state["filterStats"] = {
                "scanned": page.filter_stats.scanned,
                "matched": page.filter_stats.matched,
            }
        for idx, entry in enumerate(page.data):
            fetched += 1
            yield (
                _entry_to_row(entry, selected_field_ids, field_by_id, key_mode=key_mode),
                None
                if max_results is not None and fetched >= max_results and idx < (len(page.data) - 1)
                else next_page_cursor,
            )
            if max_results is not None and fetched >= max_results:
                if idx < (len(page.data) - 1) and state is not None:
                    state["truncatedMidPage"] = True
                return

        if first_page and not all_pages and max_results is None:
            return
        first_page = False


def _extract_field_values(obj: Any) -> dict[str, Any]:
    """Extract field values from an object with fields_raw (V2 API) or fields.data (fallback).

    The V2 API returns fields as an array: [{"id": "field-X", "value": {"data": ...}}, ...]
    This helper parses that format into a dict mapping field_id -> value.

    Args:
        obj: An object with `fields_raw` (list) and/or `fields.data` (dict) attributes

    Returns:
        Dict mapping field_id (str) -> field value
    """
    field_values: dict[str, Any] = {}
    fields_raw = getattr(obj, "fields_raw", None)
    if isinstance(fields_raw, list):
        for field_obj in fields_raw:
            if isinstance(field_obj, dict) and "id" in field_obj:
                fid_key = str(field_obj["id"])
                value_wrapper = field_obj.get("value")
                if isinstance(value_wrapper, dict):
                    field_values[fid_key] = value_wrapper.get("data")
                else:
                    field_values[fid_key] = value_wrapper
    else:
        # Fallback to fields.data for older API formats
        fields_attr = getattr(obj, "fields", None)
        if fields_attr is not None and hasattr(fields_attr, "data") and fields_attr.data:
            field_values = dict(fields_attr.data)
    return field_values


def _entry_to_row(
    entry: ListEntryWithEntity,
    field_ids: list[str],
    field_by_id: dict[str, FieldMetadata],
    *,
    key_mode: Literal["names", "ids"],
) -> dict[str, Any]:
    entity_id: int | None = None
    entity_name: str | None = None
    if entry.entity is not None:
        entity_id = int(entry.entity.id)
        entity_name = getattr(entry.entity, "name", None)
        if entity_name is None and hasattr(entry.entity, "full_name"):
            entity_name = cast(Any, entry.entity).full_name
    row: dict[str, Any] = {
        "listEntryId": int(entry.id),
        "entityType": entry.type,
        "entityId": entity_id,
        "entityName": entity_name,
    }

    # Extract field values from entity (V2 API stores fields on entity, not entry)
    field_values = _extract_field_values(entry.entity) if entry.entity else {}

    for fid in field_ids:
        key = fid if key_mode == "ids" else field_by_id[fid].name if fid in field_by_id else fid
        row[key] = field_values.get(str(fid))
    return row


def _person_to_expand_dict(
    person: Any,
    field_types: list[FieldType] | None = None,
    field_ids: list[AnyFieldId] | None = None,
    field_id_to_display: dict[str, str] | None = None,
    prefix_fields: bool = True,
) -> dict[str, Any]:
    """Convert a Person object to an expand dict, including field values if present.

    Args:
        field_id_to_display: Mapping from field ID to display name for --expand-fields
        prefix_fields: If True, prefix field keys with "person." (for flat CSV mode).
                      If False, use unprefixed display names (for nested JSON mode).
    """
    result: dict[str, Any] = {
        "id": int(person.id),
        "name": person.full_name,
        "primaryEmail": person.primary_email or (person.emails[0] if person.emails else None),
    }
    # Include field values if requested and present
    if (field_types or field_ids) and hasattr(person, "fields") and person.fields.requested:
        field_values = _extract_field_values(person)
        for field_id, value in field_values.items():
            # Get display name from mapping, fallback to field_id
            display_name = (
                field_id_to_display.get(str(field_id), str(field_id))
                if field_id_to_display
                else str(field_id)
            )
            if prefix_fields:
                result[f"person.{display_name}"] = value
            else:
                result[display_name] = value
    return result


def _company_to_expand_dict(
    company: Any,
    field_types: list[FieldType] | None = None,
    field_ids: list[AnyFieldId] | None = None,
    field_id_to_display: dict[str, str] | None = None,
    prefix_fields: bool = True,
) -> dict[str, Any]:
    """Convert a Company object to an expand dict, including field values if present.

    Args:
        field_id_to_display: Mapping from field ID to display name for --expand-fields
        prefix_fields: If True, prefix field keys with "company." (for flat CSV mode).
                      If False, use unprefixed display names (for nested JSON mode).
    """
    result: dict[str, Any] = {
        "id": int(company.id),
        "name": company.name,
        "domain": company.domain,
    }
    # Include field values if requested and present
    if (field_types or field_ids) and hasattr(company, "fields") and company.fields.requested:
        field_values = _extract_field_values(company)
        for field_id, value in field_values.items():
            # Get display name from mapping, fallback to field_id
            display_name = (
                field_id_to_display.get(str(field_id), str(field_id))
                if field_id_to_display
                else str(field_id)
            )
            if prefix_fields:
                result[f"company.{display_name}"] = value
            else:
                result[display_name] = value
    return result


def _fetch_opportunity_associations(
    client: Any,
    opportunity_id: OpportunityId,
    *,
    expand_set: set[str],
    max_results: int | None,
    on_error: str,
    warnings: list[str],
    expand_field_types: list[FieldType] | None = None,
    expand_field_ids: list[AnyFieldId] | None = None,
    field_id_to_display: dict[str, str] | None = None,
    prefix_fields: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """
    Fetch persons and/or companies associated with an opportunity.

    Returns:
        Tuple of (persons_list, companies_list) where each list contains dicts with
        id, name, primaryEmail/domain, plus field values if expand_field_types/ids specified.
        Returns None if error occurred and on_error='skip'.
    """
    want_persons = "persons" in expand_set
    want_companies = "companies" in expand_set
    want_fields = bool(expand_field_types or expand_field_ids)

    persons: list[dict[str, Any]] = []
    companies: list[dict[str, Any]] = []

    try:
        # Use dual optimization if both are requested
        if want_persons and want_companies:
            assoc = client.opportunities.get_associations(opportunity_id)
            person_ids = [int(pid) for pid in assoc.person_ids]
            company_ids = [int(cid) for cid in assoc.company_ids]
        else:
            person_ids = []
            company_ids = []
            if want_persons:
                person_ids = [
                    int(pid)
                    for pid in client.opportunities.get_associated_person_ids(opportunity_id)
                ]
            if want_companies:
                company_ids = [
                    int(cid)
                    for cid in client.opportunities.get_associated_company_ids(opportunity_id)
                ]

        # Apply max_results limit to IDs before fetching
        if max_results is not None and max_results >= 0:
            person_ids = person_ids[:max_results]
            company_ids = company_ids[:max_results]

        # Fetch persons details
        if want_persons and person_ids:
            if want_fields:
                # Use V2 API with field types to get field values
                for pid in person_ids:
                    person = client.persons.get(
                        PersonId(pid),
                        field_types=expand_field_types,
                        field_ids=expand_field_ids,
                    )
                    persons.append(
                        _person_to_expand_dict(
                            person,
                            expand_field_types,
                            expand_field_ids,
                            field_id_to_display,
                            prefix_fields,
                        )
                    )
            else:
                # Use existing V1 method for core fields only
                fetched_persons = client.opportunities.get_associated_people(
                    opportunity_id, max_results=max_results
                )
                persons = [_person_to_expand_dict(p) for p in fetched_persons]

        # Fetch company details
        if want_companies and company_ids:
            if want_fields:
                # Use V2 API with field types to get field values
                for cid in company_ids:
                    company = client.companies.get(
                        CompanyId(cid),
                        field_types=expand_field_types,
                        field_ids=expand_field_ids,
                    )
                    companies.append(
                        _company_to_expand_dict(
                            company,
                            expand_field_types,
                            expand_field_ids,
                            field_id_to_display,
                            prefix_fields,
                        )
                    )
            else:
                # Use existing V1 method for core fields only
                fetched_companies = client.opportunities.get_associated_companies(
                    opportunity_id, max_results=max_results
                )
                companies = [_company_to_expand_dict(c) for c in fetched_companies]

    except Exception as e:
        if on_error == "skip":
            warnings.append(f"Skipped expansion for opportunity {int(opportunity_id)}: {e}")
            return None
        raise

    return persons, companies


def _fetch_company_associations(
    client: Any,
    company_id: CompanyId,
    *,
    expand_set: set[str],
    max_results: int | None,
    on_error: str,
    warnings: list[str],
    expand_field_types: list[FieldType] | None = None,
    expand_field_ids: list[AnyFieldId] | None = None,
    field_id_to_display: dict[str, str] | None = None,
    prefix_fields: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """
    Fetch persons associated with a company.

    For company lists, only 'persons' expansion is valid.

    Returns:
        Tuple of (persons_list, []) where persons_list contains dicts with
        id, name, primaryEmail, plus field values if expand_field_types/ids specified.
        Returns None if error occurred and on_error='skip'.
    """
    want_persons = "persons" in expand_set
    want_fields = bool(expand_field_types or expand_field_ids)

    persons: list[dict[str, Any]] = []

    try:
        if want_persons:
            # Get person IDs first
            person_ids = client.companies.get_associated_person_ids(
                company_id, max_results=max_results
            )

            if want_fields:
                # Use V2 API with field types to get field values
                for pid in person_ids:
                    person = client.persons.get(
                        pid,
                        field_types=expand_field_types,
                        field_ids=expand_field_ids,
                    )
                    persons.append(
                        _person_to_expand_dict(
                            person,
                            expand_field_types,
                            expand_field_ids,
                            field_id_to_display,
                            prefix_fields,
                        )
                    )
            else:
                # Use existing V1 method for core fields only
                fetched_persons = client.companies.get_associated_people(
                    company_id, max_results=max_results
                )
                persons = [_person_to_expand_dict(p) for p in fetched_persons]

    except Exception as e:
        if on_error == "skip":
            warnings.append(f"Skipped expansion for company {int(company_id)}: {e}")
            return None
        raise

    # Return (persons, []) - companies is always empty for company list expansion
    return persons, []


def _fetch_person_associations(
    client: Any,
    person_id: PersonId,
    *,
    expand_set: set[str],
    max_results: int | None,
    on_error: str,
    warnings: list[str],
    expand_field_types: list[FieldType] | None = None,
    expand_field_ids: list[AnyFieldId] | None = None,
    field_id_to_display: dict[str, str] | None = None,
    prefix_fields: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """
    Fetch companies associated with a person.

    For person lists, only 'companies' expansion is valid.
    Note: V2 API doesn't return company_ids, so we use V1 fallback to get IDs.

    Returns:
        Tuple of ([], companies_list) where companies_list contains dicts with
        id, name, domain, plus field values if expand_field_types/ids specified.
        Returns None if error occurred and on_error='skip'.
    """
    want_companies = "companies" in expand_set
    want_fields = bool(expand_field_types or expand_field_ids)

    companies: list[dict[str, Any]] = []

    try:
        if want_companies:
            # V1 fallback: fetch person via V1 API to get organization_ids
            person_data = client._http.get(f"/persons/{person_id}", v1=True)
            company_ids_raw = (
                person_data.get("organization_ids") or person_data.get("organizationIds") or []
            )
            company_ids = [int(cid) for cid in company_ids_raw if cid is not None]

            # Apply max_results limit
            if max_results is not None and max_results >= 0:
                company_ids = company_ids[:max_results]

            if want_fields:
                # Use V2 API with field types to get field values
                for cid in company_ids:
                    company = client.companies.get(
                        CompanyId(cid),
                        field_types=expand_field_types,
                        field_ids=expand_field_ids,
                    )
                    companies.append(
                        _company_to_expand_dict(
                            company,
                            expand_field_types,
                            expand_field_ids,
                            field_id_to_display,
                            prefix_fields,
                        )
                    )
            else:
                # Fetch company details via V1 API (core fields only)
                for cid in company_ids:
                    company_data = client._http.get(f"/organizations/{cid}", v1=True)
                    companies.append(
                        {
                            "id": cid,
                            "name": company_data.get("name"),
                            "domain": company_data.get("domain"),
                        }
                    )

    except Exception as e:
        if on_error == "skip":
            warnings.append(f"Skipped expansion for person {int(person_id)}: {e}")
            return None
        raise

    # Return ([], companies) - persons is always empty for person list expansion
    return [], companies


def _fetch_interaction_dates(
    client: Any,
    entity_type: str,
    entity_id: int,
    *,
    person_name_cache: dict[int, str] | None = None,
) -> dict[str, Any] | None:
    """Fetch interaction date summaries for an entity.

    Args:
        client: Affinity client
        entity_type: "company" or "person" (opportunity not supported)
        entity_id: The entity ID
        person_name_cache: Optional cache for person name resolution.
            Will be mutated to store resolved names.

    Returns:
        Transformed interaction data dict, or None if no data/error.
    """
    from affinity.cli.interaction_utils import transform_interaction_data

    try:
        if entity_type == "company":
            company = client.companies.get(
                CompanyId(entity_id),
                with_interaction_dates=True,
                with_interaction_persons=True,
            )
            return transform_interaction_data(
                company.interaction_dates,
                company.interactions,
                client=client,
                person_name_cache=person_name_cache,
            )
        elif entity_type == "person":
            person = client.persons.get(
                PersonId(entity_id),
                with_interaction_dates=True,
                with_interaction_persons=True,
            )
            return transform_interaction_data(
                person.interaction_dates,
                person.interactions,
                client=client,
                person_name_cache=person_name_cache,
            )
        else:
            # Opportunities don't support interaction dates in the same way
            return None
    except Exception:
        return None


def _fetch_associations(
    client: Any,
    list_type: ListType,
    entity_id: int,
    *,
    expand_set: set[str],
    max_results: int | None,
    on_error: str,
    warnings: list[str],
    expand_field_types: list[FieldType] | None = None,
    expand_field_ids: list[AnyFieldId] | None = None,
    expand_filters: FilterExpression | None = None,
    expand_opps_list_id: ListId | None = None,
    field_id_to_display: dict[str, str] | None = None,
    prefix_fields: bool = True,
    person_name_cache: dict[int, str] | None = None,
    check_unreplied: bool = False,
    unreplied_types: list[InteractionType] | None = None,
    unreplied_lookback_days: int = 30,
) -> (
    tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]
    | None
):
    """
    Dispatch to the correct association fetcher based on list type.

    Routes to:
    - _fetch_opportunity_associations for opportunity lists
    - _fetch_company_associations for company/organization lists
    - _fetch_person_associations for person lists

    Args:
        field_id_to_display: Mapping from field ID to display name for --expand-fields
        prefix_fields: If True, prefix field keys with entity type (for flat CSV mode).
        person_name_cache: Optional cache for resolving person IDs to names (for interactions).
        check_unreplied: If True, check for unreplied incoming messages.
        unreplied_types: Interaction types to check (EMAIL, CHAT_MESSAGE). Default: both.
        unreplied_lookback_days: Days to look back for unreplied message detection.

    Returns:
        Tuple of (persons_list, companies_list, opportunities_list,
        interactions_dict, unreplied_dict).
        Returns None if error occurred and on_error='skip'.
    """
    persons: list[dict[str, Any]] = []
    companies: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    interactions: dict[str, Any] | None = None
    unreplied: dict[str, Any] | None = None

    try:
        if list_type == ListType.OPPORTUNITY:
            result = _fetch_opportunity_associations(
                client=client,
                opportunity_id=OpportunityId(entity_id),
                expand_set=expand_set,
                max_results=max_results,
                on_error=on_error,
                warnings=warnings,
                expand_field_types=expand_field_types,
                expand_field_ids=expand_field_ids,
                field_id_to_display=field_id_to_display,
                prefix_fields=prefix_fields,
            )
            if result is None:
                return None
            persons, companies = result

        elif list_type == ListType.COMPANY:
            result = _fetch_company_associations(
                client=client,
                company_id=CompanyId(entity_id),
                expand_set=expand_set,
                max_results=max_results,
                on_error=on_error,
                warnings=warnings,
                expand_field_types=expand_field_types,
                expand_field_ids=expand_field_ids,
                field_id_to_display=field_id_to_display,
                prefix_fields=prefix_fields,
            )
            if result is None:
                return None
            persons, _ = result

            # Fetch opportunities if requested (Phase 5)
            if "opportunities" in expand_set:
                opportunities = _fetch_entity_opportunities(
                    client=client,
                    entity_type="company",
                    entity_id=CompanyId(entity_id),
                    opps_list_id=expand_opps_list_id,
                    max_results=max_results,
                    on_error=on_error,
                    warnings=warnings,
                )

        elif list_type == ListType.PERSON:
            result = _fetch_person_associations(
                client=client,
                person_id=PersonId(entity_id),
                expand_set=expand_set,
                max_results=max_results,
                on_error=on_error,
                warnings=warnings,
                expand_field_types=expand_field_types,
                expand_field_ids=expand_field_ids,
                field_id_to_display=field_id_to_display,
                prefix_fields=prefix_fields,
            )
            if result is None:
                return None
            _, companies = result

            # Fetch opportunities if requested (Phase 5)
            if "opportunities" in expand_set:
                opportunities = _fetch_entity_opportunities(
                    client=client,
                    entity_type="person",
                    entity_id=PersonId(entity_id),
                    opps_list_id=expand_opps_list_id,
                    max_results=max_results,
                    on_error=on_error,
                    warnings=warnings,
                )

        else:
            raise ValueError(f"Unsupported list type for expansion: {list_type}")

        # Fetch interaction dates if requested
        if "interactions" in expand_set:
            entity_type_map = {
                ListType.COMPANY: "company",
                ListType.PERSON: "person",
                ListType.OPPORTUNITY: "opportunity",  # Not supported but handled gracefully
            }
            entity_type = entity_type_map.get(list_type, "")
            interactions = _fetch_interaction_dates(
                client=client,
                entity_type=entity_type,
                entity_id=entity_id,
                person_name_cache=person_name_cache,
            )

        # Check for unreplied messages if requested
        if check_unreplied:
            from affinity.cli.interaction_utils import check_unreplied as _check_unreplied

            entity_type_map = {
                ListType.COMPANY: "company",
                ListType.PERSON: "person",
                ListType.OPPORTUNITY: "opportunity",
            }
            entity_type = entity_type_map.get(list_type, "")
            if entity_type in ("company", "person", "opportunity"):
                unreplied = _check_unreplied(
                    client=client,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    interaction_types=unreplied_types,
                    lookback_days=unreplied_lookback_days,
                )

        # Apply expand filters (Phase 5)
        if expand_filters:
            persons = [p for p in persons if expand_filters.matches(p)]
            companies = [c for c in companies if expand_filters.matches(c)]
            opportunities = [o for o in opportunities if expand_filters.matches(o)]

        return persons, companies, opportunities, interactions, unreplied

    except Exception as e:
        if on_error == "skip":
            warnings.append(f"Skipped expansion for entity {entity_id}: {e}")
            return None
        raise


def _fetch_entity_opportunities(
    client: Any,
    entity_type: str,
    entity_id: PersonId | CompanyId,
    *,
    opps_list_id: ListId | None,
    max_results: int | None,
    on_error: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """
    Fetch opportunities associated with a person or company.

    If opps_list_id is provided, only search that specific opportunity list.
    Otherwise, search all accessible opportunity lists.

    Returns list of opportunity dicts with id, name, listId.
    """
    opportunities: list[dict[str, Any]] = []

    try:
        # Get opportunity lists to search
        if opps_list_id is not None:
            opp_list_ids = [opps_list_id]
        else:
            # Fetch all opportunity lists the user has access to
            opp_list_ids = []
            for page in client.lists.pages():
                for lst in page.data:
                    if lst.type == ListType.OPPORTUNITY:
                        opp_list_ids.append(ListId(int(lst.id)))

        # Search each opportunity list for entries associated with this entity
        for list_id in opp_list_ids:
            entries = client.lists.entries(list_id)

            # Fetch entries from this list and check associations
            # Note: This is expensive as we need to check each entry's associations
            for page in entries.pages(limit=100):
                for entry in page.data:
                    if entry.entity is None:
                        continue

                    opp_id = OpportunityId(int(entry.entity.id))

                    # Check if this opportunity is associated with our entity
                    try:
                        assoc = client.opportunities.get_associations(opp_id)
                        is_associated = False

                        if entity_type == "person":
                            person_ids = [int(pid) for pid in assoc.person_ids]
                            is_associated = int(entity_id) in person_ids
                        elif entity_type == "company":
                            company_ids = [int(cid) for cid in assoc.company_ids]
                            is_associated = int(entity_id) in company_ids

                        if is_associated:
                            opportunities.append(
                                {
                                    "id": int(opp_id),
                                    "name": getattr(entry.entity, "name", None),
                                    "listId": int(list_id),
                                }
                            )

                            # Apply max results limit
                            if max_results is not None and len(opportunities) >= max_results:
                                return opportunities

                    except Exception:
                        # Skip opportunities we can't access
                        continue

                # Stop pagination if we have enough results
                if max_results is not None and len(opportunities) >= max_results:
                    break

    except Exception as e:
        if on_error == "skip":
            warnings.append(f"Error fetching opportunities for {entity_type} {int(entity_id)}: {e}")
        else:
            raise

    return opportunities


def _validate_and_resolve_expand_fields(
    client: Any,
    expand_set: set[str],
    field_specs: tuple[str, ...],
) -> list[tuple[str, AnyFieldId]]:
    """
    Validate --expand-fields against available global/enriched fields.

    Fetches field metadata for expanded entity types (person/company) and validates
    that each field spec exists. Field specs can be:
    - Field names (resolved to IDs via metadata lookup)
    - Field IDs (validated against metadata)

    Args:
        client: Affinity client instance
        expand_set: Set of expand types ("persons", "companies")
        field_specs: Tuple of field spec strings from --expand-fields

    Returns:
        List of (original_spec, resolved_field_id) tuples

    Raises:
        CLIError: If a field spec doesn't match any available field
    """
    # Build combined field lookup from person and company metadata
    # Maps lowercase name -> (display_name, field_id) for name resolution
    # Also stores field_id -> (display_name, field_id) for ID validation
    name_to_field: dict[str, tuple[str, AnyFieldId]] = {}
    id_to_field: dict[str, tuple[str, AnyFieldId]] = {}
    all_field_names: set[str] = set()

    if "persons" in expand_set:
        person_fields = client.persons.get_fields()
        for f in person_fields:
            name_lower = f.name.lower()
            name_to_field[name_lower] = (f.name, f.id)
            id_to_field[str(f.id)] = (f.name, f.id)
            all_field_names.add(f.name)

    if "companies" in expand_set:
        company_fields = client.companies.get_fields()
        for f in company_fields:
            name_lower = f.name.lower()
            # Only add if not already present (person fields take precedence)
            if name_lower not in name_to_field:
                name_to_field[name_lower] = (f.name, f.id)
            if str(f.id) not in id_to_field:
                id_to_field[str(f.id)] = (f.name, f.id)
                all_field_names.add(f.name)

    # Resolve each field spec
    parsed: list[tuple[str, AnyFieldId]] = []
    for spec in field_specs:
        spec = spec.strip()
        if not spec:
            continue

        # Try to match by field ID first (exact match)
        if spec in id_to_field:
            display_name, field_id = id_to_field[spec]
            parsed.append((display_name, field_id))
            continue

        # Try to parse as FieldId format (field-123)
        try:
            field_id = FieldId(spec)
            if str(field_id) in id_to_field:
                display_name, _ = id_to_field[str(field_id)]
                parsed.append((display_name, field_id))
                continue
            # Valid FieldId format but not found - try name lookup next
        except ValueError:
            pass

        # Try to match by name (case-insensitive)
        spec_lower = spec.lower()
        if spec_lower in name_to_field:
            display_name, field_id = name_to_field[spec_lower]
            parsed.append((display_name, field_id))
            continue

        # Not found - raise error with helpful message
        # Show a sample of available field names (up to 10)
        sample_names = sorted(all_field_names)[:10]
        hint_suffix = ", ..." if len(all_field_names) > 10 else ""
        raise CLIError(
            f"Unknown expand field: '{spec}'",
            exit_code=2,
            error_type="usage_error",
            details={"availableFields": sorted(all_field_names)[:20]},
            hint=f"Available fields include: {', '.join(sample_names)}{hint_suffix}",
        )

    return parsed


def _expand_csv_headers(
    base_headers: list[str],
    expand_set: set[str],
    csv_mode: str = "flat",
    expand_fields: list[tuple[str, AnyFieldId]] | None = None,
    header_mode: CsvHeaderMode = "names",
    check_unreplied: bool = False,
) -> list[str]:
    """
    Add expansion columns to CSV headers.

    Flat mode: expandedType, expandedId, expandedName, expandedEmail, expandedDomain,
               plus prefixed field columns (person.{name/id}, company.{name/id}) for --expand-fields
    Nested mode: _expand_persons, _expand_companies (JSON arrays)

    Args:
        expand_fields: List of (original_spec, field_id) tuples
        header_mode: "names" uses original spec, "ids" uses field ID
        check_unreplied: If True, add unreplied email columns
    """
    headers = list(base_headers)
    if csv_mode == "nested":
        # Nested mode: add JSON array columns
        if "persons" in expand_set:
            headers.append("_expand_persons")
        if "companies" in expand_set:
            headers.append("_expand_companies")
        if "opportunities" in expand_set:
            headers.append("_expand_opportunities")
        if "interactions" in expand_set:
            headers.append("_expand_interactions")
        if check_unreplied:
            headers.append("_expand_unreplied")
    else:
        # Flat mode: add row-per-association columns
        headers.append("expandedType")
        headers.append("expandedId")
        headers.append("expandedName")
        if "persons" in expand_set:
            headers.append("expandedEmail")
        if "companies" in expand_set:
            headers.append("expandedDomain")
        if "opportunities" in expand_set:
            headers.append("expandedListId")
        # Add prefixed columns for --expand-fields (Phase 4)
        if expand_fields:
            for original_spec, field_id in expand_fields:
                # Use original spec name for "names" mode, field ID for "ids" mode
                display_name = original_spec if header_mode == "names" else str(field_id)
                if "persons" in expand_set:
                    headers.append(f"person.{display_name}")
                if "companies" in expand_set:
                    headers.append(f"company.{display_name}")
        # Add interaction date columns (Phase 1)
        if "interactions" in expand_set:
            from affinity.cli.interaction_utils import INTERACTION_CSV_COLUMNS

            headers.extend(INTERACTION_CSV_COLUMNS)
        # Add unreplied email columns (Phase 2)
        if check_unreplied:
            from affinity.cli.interaction_utils import UNREPLIED_CSV_COLUMNS

            headers.extend(UNREPLIED_CSV_COLUMNS)
    return headers


@list_group.group(name="entry", cls=RichGroup)
def list_entry_group() -> None:
    """List entry commands."""


@category("read")
@list_entry_group.command(name="get", cls=RichCommand)
@click.argument("list_selector", type=str)
@click.argument("entry_id", type=int)
@output_options
@click.pass_obj
def list_entry_get(
    ctx: CLIContext,
    list_selector: str,
    entry_id: int,
) -> None:
    """
    Get a single list entry by ID.

    Displays the list entry with its field values and field names.

    Examples:

    - `xaffinity list entry get "Portfolio" 12345`
    - `xaffinity list entry get 67890 12345`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        resolved_list = resolve_list_selector(client=client, selector=list_selector, cache=cache)
        entries = client.lists.entries(resolved_list.list.id)
        entry = entries.get(ListEntryId(entry_id))
        payload = serialize_model_for_cli(entry)

        # Include raw fields if available
        fields_raw = getattr(entry, "fields_raw", None)
        if isinstance(fields_raw, list):
            payload["fields"] = fields_raw

        resolved = dict(resolved_list.resolved)

        # Fetch field metadata if fields are present
        entry_fields = payload.get("fields") if isinstance(payload, dict) else None
        if isinstance(entry_fields, list) and entry_fields:
            try:
                from ..field_utils import build_field_id_to_name_map

                field_metadata = client.lists.get_fields(resolved_list.list.id)
                resolved["fieldMetadata"] = build_field_id_to_name_map(field_metadata)
            except Exception:
                # Field metadata is optional - continue without names if fetch fails
                pass

        # Extract resolved list name for context
        ctx_resolved: dict[str, str] | None = None
        list_resolved = resolved_list.resolved.get("list", {})
        if isinstance(list_resolved, dict):
            list_name = list_resolved.get("entityName")
            if list_name:
                ctx_resolved = {"listId": str(list_name)}

        cmd_context = CommandContext(
            name="list entry get",
            inputs={"listId": int(resolved_list.list.id), "entryId": entry_id},
            modifiers={},
            resolved=ctx_resolved,
        )

        return CommandOutput(
            data={"listEntry": payload},
            context=cmd_context,
            resolved=resolved,
            api_called=True,
        )

    run_command(ctx, command="list entry get", fn=fn)


def _validate_entry_target(
    person_id: int | None,
    company_id: int | None,
) -> None:
    count = sum(1 for value in (person_id, company_id) if value is not None)
    if count == 1:
        return
    raise CLIError(
        "Provide exactly one of --person-id or --company-id.",
        error_type="usage_error",
        exit_code=2,
    )


@category("write")
@list_entry_group.command(name="add", cls=RichCommand)
@click.argument("list_selector", type=str)
@click.option("--person-id", type=int, default=None, help="Person id to add.")
@click.option("--company-id", type=int, default=None, help="Company id to add.")
@click.option("--creator-id", type=int, default=None, help="Creator id override.")
@output_options
@click.pass_obj
def list_entry_add(
    ctx: CLIContext,
    list_selector: str,
    *,
    person_id: int | None,
    company_id: int | None,
    creator_id: int | None,
) -> None:
    """Add a person or company to a list.

    Note: Opportunities cannot be added to lists this way. Use 'opportunity create --list-id'
    instead, which creates both the opportunity and its list entry atomically.
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _validate_entry_target(person_id, company_id)
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        resolved_list = resolve_list_selector(client=client, selector=list_selector, cache=cache)
        entries = client.lists.entries(resolved_list.list.id)

        if person_id is not None:
            created = entries.add_person(PersonId(person_id), creator_id=creator_id)
        else:
            assert company_id is not None
            created = entries.add_company(CompanyId(company_id), creator_id=creator_id)

        # Build CommandContext for list entry add
        ctx_modifiers: dict[str, object] = {}
        if person_id is not None:
            ctx_modifiers["personId"] = person_id
        if company_id is not None:
            ctx_modifiers["companyId"] = company_id
        if creator_id is not None:
            ctx_modifiers["creatorId"] = creator_id

        # Extract resolved list name for context
        ctx_resolved: dict[str, str] | None = None
        list_resolved = resolved_list.resolved.get("list", {})
        if isinstance(list_resolved, dict):
            list_name = list_resolved.get("entityName")
            if list_name:
                ctx_resolved = {"listId": str(list_name)}

        cmd_context = CommandContext(
            name="list entry add",
            inputs={"listId": int(resolved_list.list.id)},
            modifiers=ctx_modifiers,
            resolved=ctx_resolved,
        )

        payload = serialize_model_for_cli(created)
        return CommandOutput(
            data={"listEntry": payload},
            context=cmd_context,
            resolved=resolved_list.resolved,
            api_called=True,
        )

    run_command(ctx, command="list entry add", fn=fn)


@category("write")
@destructive
@list_entry_group.command(name="delete", cls=RichCommand)
@click.argument("list_selector", type=str)
@click.argument("entry_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def list_entry_delete(ctx: CLIContext, list_selector: str, entry_id: int, yes: bool) -> None:
    """Delete a list entry."""
    if not yes:
        click.confirm(f"Delete entry {entry_id} from list '{list_selector}'?", abort=True)

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        resolved_list = resolve_list_selector(client=client, selector=list_selector, cache=cache)
        entries = client.lists.entries(resolved_list.list.id)
        success = entries.delete(ListEntryId(entry_id))

        # Extract resolved list name for context
        ctx_resolved: dict[str, str] | None = None
        list_resolved = resolved_list.resolved.get("list", {})
        if isinstance(list_resolved, dict):
            list_name = list_resolved.get("entityName")
            if list_name:
                ctx_resolved = {"listId": str(list_name)}

        cmd_context = CommandContext(
            name="list entry delete",
            inputs={"listId": int(resolved_list.list.id), "entryId": entry_id},
            modifiers={},
            resolved=ctx_resolved,
        )

        return CommandOutput(
            data={"success": success},
            context=cmd_context,
            resolved=resolved_list.resolved,
            api_called=True,
        )

    run_command(ctx, command="list entry delete", fn=fn)


@category("write")
@list_entry_group.command(name="field", cls=RichCommand)
@click.argument("list_selector", type=str)
@click.argument("entry_id", type=int)
@click.option(
    "--set",
    "set_values",
    nargs=2,
    multiple=True,
    metavar="FIELD VALUE",
    help="Set field value (repeatable). Replaces existing value(s).",
)
@click.option(
    "--append",
    "append_values",
    nargs=2,
    multiple=True,
    metavar="FIELD VALUE",
    help="Append to multi-value field (repeatable). Adds without replacing.",
)
@click.option(
    "--unset",
    "unset_fields",
    multiple=True,
    metavar="FIELD",
    help="Unset all values for field (repeatable).",
)
@click.option(
    "--unset-value",
    "unset_values",
    nargs=2,
    multiple=True,
    metavar="FIELD VALUE",
    help="Unset specific value from multi-value field (repeatable).",
)
@click.option(
    "--set-json",
    "json_input",
    type=str,
    help="JSON object of field:value pairs to set.",
)
@click.option(
    "--get",
    "get_fields",
    multiple=True,
    metavar="FIELD",
    help="Get specific field values (repeatable).",
)
@output_options
@click.pass_obj
def list_entry_field(
    ctx: CLIContext,
    list_selector: str,
    entry_id: int,
    *,
    set_values: tuple[tuple[str, str], ...],
    append_values: tuple[tuple[str, str], ...],
    unset_fields: tuple[str, ...],
    unset_values: tuple[tuple[str, str], ...],
    json_input: str | None,
    get_fields: tuple[str, ...],
) -> None:
    """
    Manage list entry field values.

    Unified command for getting, setting, appending, and unsetting field values.
    Field names are resolved case-insensitively. Field IDs (field-123) can also be used.

    Operation order: --set/--set-json first, then --append, then --unset/--unset-value.

    Examples:

    - `xaffinity entry field "Portfolio" 123 --set Status "Active"`
    - `xaffinity entry field "Portfolio" 123 --set Status "Active" --set Priority "High"`
    - `xaffinity entry field "Portfolio" 123 --append Tags "Priority"`
    - `xaffinity entry field "Portfolio" 123 --unset Status`
    - `xaffinity entry field "Portfolio" 123 --unset-value Tags "OldTag"`
    - `xaffinity entry field "Portfolio" 123 --set-json '{"Status": "Active"}'`
    - `xaffinity entry field "Portfolio" 123 --get Status --get Priority`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        from ..field_utils import (
            FieldResolver,
            find_field_values_for_field,
            format_value_for_comparison,
        )

        # Validate: at least one operation must be specified
        has_set = bool(set_values) or bool(json_input)
        has_append = bool(append_values)
        has_unset = bool(unset_fields)
        has_unset_value = bool(unset_values)
        has_get = bool(get_fields)

        if not any([has_set, has_append, has_unset, has_unset_value, has_get]):
            raise CLIError(
                "No operation specified. Use --set, --append, --unset, --unset-value, "
                "--set-json, or --get.",
                exit_code=2,
                error_type="usage_error",
            )

        # Validate: --get is exclusive (can't mix read with write)
        if has_get and (has_set or has_append or has_unset or has_unset_value):
            raise CLIError(
                "--get cannot be combined with write operations "
                "(--set, --append, --unset, --unset-value, --set-json).",
                exit_code=2,
                error_type="usage_error",
            )

        # Collect all fields from operations for conflict detection
        set_option_fields: set[str] = {fv[0] for fv in set_values}
        set_json_fields: set[str] = set()
        if json_input:
            try:
                json_data = json.loads(json_input)
                if isinstance(json_data, dict):
                    set_json_fields = set(json_data.keys())
            except json.JSONDecodeError:
                pass  # Handled later

        # Check for duplicate fields between --set and --set-json
        duplicate_fields = set_option_fields & set_json_fields
        if duplicate_fields:
            raise CLIError(
                f"Field(s) in both --set and --set-json: {duplicate_fields}",
                exit_code=2,
                error_type="usage_error",
            )

        all_set_fields = set_option_fields | set_json_fields
        all_append_fields: set[str] = {av[0] for av in append_values}
        all_unset_fields: set[str] = set(unset_fields)
        all_unset_value_fields: set[str] = {uv[0] for uv in unset_values}

        # Check for conflicting operations on same field
        if all_set_fields & all_append_fields:
            raise CLIError(
                f"Field(s) in both --set and --append: {all_set_fields & all_append_fields}",
                exit_code=2,
                error_type="usage_error",
            )
        if all_set_fields & all_unset_fields:
            raise CLIError(
                f"Field(s) in both --set and --unset: {all_set_fields & all_unset_fields}",
                exit_code=2,
                error_type="usage_error",
            )
        if all_set_fields & all_unset_value_fields:
            raise CLIError(
                f"Field(s) in both --set and --unset-value: "
                f"{all_set_fields & all_unset_value_fields}",
                exit_code=2,
                error_type="usage_error",
            )
        if all_append_fields & all_unset_fields:
            raise CLIError(
                f"Field(s) in both --append and --unset: {all_append_fields & all_unset_fields}",
                exit_code=2,
                error_type="usage_error",
            )
        if all_unset_fields & all_unset_value_fields:
            raise CLIError(
                f"Field(s) in both --unset and --unset-value: "
                f"{all_unset_fields & all_unset_value_fields}",
                exit_code=2,
                error_type="usage_error",
            )
        # Note: --append + --unset-value on same field is ALLOWED (tag swap pattern)

        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        resolved_list = resolve_list_selector(client=client, selector=list_selector, cache=cache)
        resolved = dict(resolved_list.resolved)

        # Fetch field metadata
        field_metadata = list_fields_for_list(
            client=client, list_id=resolved_list.list.id, cache=cache
        )
        resolver = FieldResolver(field_metadata)

        # Pattern for field IDs: must be "field-" followed by digits
        # Note: pure numeric strings like "2024" are treated as field NAMES, not IDs
        import re

        _field_id_pattern = re.compile(r"^field-\d+$")

        # Helper to resolve field name or ID and validate it exists on the list
        def resolve_field(field_spec: str) -> str:
            """Resolve field name/ID, auto-detecting format.

            Args:
                field_spec: Field name or ID to resolve.

            Returns:
                The resolved field ID.

            Raises:
                CLIError: If field is not found on the list.
            """
            # Check for FieldId format (pattern: field-\d+)
            # Note: pure numeric strings like "2024" are treated as field names
            if _field_id_pattern.match(field_spec):
                # Validate field ID exists on this list
                if not resolver.get_field_name(field_spec):
                    raise CLIError(
                        f"Field '{field_spec}' not found on list '{list_selector}'.",
                        exit_code=2,
                        error_type="not_found",
                    )
                return field_spec  # It's a valid field ID
            # Check for EnrichedFieldId prefix (affinity-data-* or other known prefixes)
            # Note: enriched fields may not appear in list field metadata, so skip validation
            if field_spec.startswith("affinity-data-") or field_spec.startswith(
                "source-of-introduction"
            ):
                return field_spec  # It's a valid enriched field ID
            # Resolve as field name (this already throws if not found)
            return resolver.resolve_field_name_or_id(field_spec, context="field")

        # Build modifiers for CommandContext
        ctx_modifiers: dict[str, object] = {}
        if set_values:
            ctx_modifiers["set"] = [list(sv) for sv in set_values]
        if append_values:
            ctx_modifiers["append"] = [list(av) for av in append_values]
        if unset_fields:
            ctx_modifiers["unset"] = list(unset_fields)
        if unset_values:
            ctx_modifiers["unsetValue"] = [list(uv) for uv in unset_values]
        if json_input:
            ctx_modifiers["json"] = json_input
        if get_fields:
            ctx_modifiers["get"] = list(get_fields)

        results: dict[str, Any] = {}

        # Upfront field resolution: resolve ALL fields before any API calls (fail-fast)
        # This catches typos and invalid field names before any side effects
        resolved_fields: dict[str, str] = {}  # field_spec -> resolved_field_id

        all_field_specs: list[str] = []
        all_field_specs.extend(fv[0] for fv in set_values)
        all_field_specs.extend(av[0] for av in append_values)
        all_field_specs.extend(unset_fields)
        all_field_specs.extend(uv[0] for uv in unset_values)
        all_field_specs.extend(get_fields)
        if json_input:
            try:
                json_data_for_fields = json.loads(json_input)
                if isinstance(json_data_for_fields, dict):
                    all_field_specs.extend(json_data_for_fields.keys())
            except json.JSONDecodeError:
                pass  # Will be caught later with better error message

        for field_spec in all_field_specs:
            if field_spec not in resolved_fields:
                resolved_fields[field_spec] = resolve_field(field_spec)

        # Handle --get: read field values via V2 API (returns resolved person/company objects)
        if has_get:
            entries = client.lists.entries(resolved_list.list.id)
            target_field_ids = [resolved_fields[fs] for fs in get_fields]
            v2_fields = entries.get_field_values(
                ListEntryId(entry_id),
                ids=target_field_ids,
            )

            field_results: dict[str, Any] = {}
            for field_spec in get_fields:
                target_field_id = resolved_fields[field_spec]
                resolved_name = resolver.get_field_name(target_field_id) or field_spec
                # Extract raw .value.data — same approach as _extract_field_values()
                # and list export. Do NOT use get_value() which returns typed objects.
                field_obj = v2_fields.data.get(target_field_id)
                if field_obj is not None:
                    value_wrapper = field_obj.get("value")
                    if isinstance(value_wrapper, dict) and "data" in value_wrapper:
                        field_results[resolved_name] = value_wrapper["data"]
                    else:
                        field_results[resolved_name] = value_wrapper
                else:
                    field_results[resolved_name] = None

            results["fields"] = field_results

            cmd_context = CommandContext(
                name="entry field",
                inputs={"listSelector": list_selector, "entryId": entry_id},
                modifiers=ctx_modifiers,
                resolved={k: str(v) for k, v in resolved.items() if v is not None}
                if resolved
                else None,
            )

            return CommandOutput(
                data=results,
                context=cmd_context,
                resolved=resolved,
                api_called=True,
            )

        # Fetch existing values once for all write operations
        existing_values_list = list(client.field_values.list(list_entry_id=ListEntryId(entry_id)))
        existing_values_serialized = [serialize_model_for_cli(v) for v in existing_values_list]

        created_values: list[dict[str, Any]] = []
        deleted_count = 0

        # Get the entries API for this list
        entries = client.lists.entries(resolved_list.list.id)

        # Phase 1: Handle --set and --set-json (replace semantics)
        set_operations: list[tuple[str, Any]] = []

        # Collect from --set options
        for field_spec, value in set_values:
            set_operations.append((field_spec, value))

        # Collect from --set-json
        if json_input:
            try:
                json_data = json.loads(json_input)
                if not isinstance(json_data, dict):
                    raise CLIError(
                        "--set-json must be a JSON object.",
                        exit_code=2,
                        error_type="usage_error",
                    )
                for field_spec, value in json_data.items():
                    set_operations.append((field_spec, value))
            except json.JSONDecodeError as e:
                raise CLIError(
                    f"Invalid JSON in --set-json: {e}",
                    exit_code=2,
                    error_type="usage_error",
                ) from e

        # Execute set operations (delete existing, then create new)
        for field_spec, value in set_operations:
            target_field_id = resolved_fields[field_spec]  # Already resolved upfront
            resolved_name = resolver.get_field_name(target_field_id) or field_spec

            # Delete existing values for this field (replace semantics)
            existing_for_field = find_field_values_for_field(
                field_values=existing_values_serialized,
                field_id=target_field_id,
            )
            if len(existing_for_field) > 1:
                # Emit warning for multi-value replace
                old_vals = [fv.get("value") for fv in existing_for_field]
                if len(old_vals) > 5:
                    display_vals = [*old_vals[:3], f"...{len(old_vals) - 3} more..."]
                else:
                    display_vals = old_vals
                click.echo(
                    f"Warning: Replaced {len(existing_for_field)} existing values "
                    f"for field '{resolved_name}': {display_vals}",
                    err=True,
                )

            for fv in existing_for_field:
                fv_id = fv.get("id")
                if fv_id:
                    client.field_values.delete(fv_id)
                    deleted_count += 1

            # Create new value using V2 API
            try:
                parsed_field_id: AnyFieldId = FieldId(target_field_id)
            except ValueError:
                parsed_field_id = EnrichedFieldId(target_field_id)

            # Resolve dropdown values (text → option ID) and get correct value_type
            resolved_value, value_type_str = resolver.resolve_field_value(target_field_id, value)

            result = entries.update_field_value(
                ListEntryId(entry_id), parsed_field_id, resolved_value, value_type=value_type_str
            )
            created_values.append(serialize_model_for_cli(result))

        # Phase 2: Handle --append (add without replacing)
        # Group appends by field to aggregate repeated --append on the same field
        # (e.g., --append Tags A --append Tags B → single write with [existing, A, B])
        from collections import OrderedDict

        append_groups: OrderedDict[str, list[str]] = OrderedDict()
        for field_spec, value in append_values:
            target_field_id = resolved_fields[field_spec]
            append_groups.setdefault(target_field_id, []).append(value)

        for target_field_id, values_for_field in append_groups.items():
            try:
                parsed_field_id = FieldId(target_field_id)
            except ValueError:
                parsed_field_id = EnrichedFieldId(target_field_id)

            # Resolve all values and determine value_type
            all_new_resolved: list[Any] = []
            value_type_str = "text"
            for val in values_for_field:
                resolved_value, value_type_str = resolver.resolve_field_value(target_field_id, val)
                if isinstance(resolved_value, list):
                    all_new_resolved.extend(resolved_value)
                else:
                    all_new_resolved.append(resolved_value)

            # For multi-value fields (dropdown-multi, person-multi, company-multi),
            # the V2 API replaces the entire array on POST.
            # To truly append, merge new values with existing ones.
            if value_type_str == "dropdown-multi" and all_new_resolved:
                existing_for_field = find_field_values_for_field(
                    field_values=existing_values_serialized,
                    field_id=target_field_id,
                )
                existing_option_ids: set[int] = set()
                existing_options: list[dict[str, int]] = []
                for fv in existing_for_field:
                    fv_value = fv.get("value")
                    if fv_value is None:
                        continue
                    existing_resolved, _ = resolver.resolve_field_value(
                        target_field_id, str(fv_value)
                    )
                    if isinstance(existing_resolved, list):
                        for opt in existing_resolved:
                            if isinstance(opt, dict):
                                opt_id = opt.get("dropdownOptionId")
                                if opt_id is not None and opt_id not in existing_option_ids:
                                    existing_option_ids.add(opt_id)
                                    existing_options.append(opt)
                for opt in all_new_resolved:
                    if isinstance(opt, dict):
                        opt_id = opt.get("dropdownOptionId")
                        if opt_id is not None and opt_id not in existing_option_ids:
                            existing_option_ids.add(opt_id)
                            existing_options.append(opt)
                final_value: Any = existing_options

            elif value_type_str in ("person-multi", "company-multi") and all_new_resolved:
                from ..field_utils import _extract_entity_id

                existing_for_field = find_field_values_for_field(
                    field_values=existing_values_serialized,
                    field_id=target_field_id,
                )
                existing_entity_ids: set[int] = set()
                existing_entities: list[dict[str, int]] = []
                for fv in existing_for_field:
                    fv_value = fv.get("value")
                    eid = _extract_entity_id(fv_value)
                    if eid is not None and eid not in existing_entity_ids:
                        existing_entity_ids.add(eid)
                        existing_entities.append({"id": eid})
                for opt in all_new_resolved:
                    if isinstance(opt, dict):
                        eid = opt.get("id")
                        if eid is not None and eid not in existing_entity_ids:
                            existing_entity_ids.add(eid)
                            existing_entities.append(opt)
                final_value = existing_entities

            elif len(values_for_field) == 1:
                # Single append on non-multi field: use resolved value directly
                final_value = (
                    all_new_resolved[0] if len(all_new_resolved) == 1 else all_new_resolved
                )
            else:
                # Multiple appends on non-multi field: use last value
                final_value = all_new_resolved[-1] if all_new_resolved else all_new_resolved

            result = entries.update_field_value(
                ListEntryId(entry_id), parsed_field_id, final_value, value_type=value_type_str
            )
            created_values.append(serialize_model_for_cli(result))

        # Refresh existing values for unset operations (in case set/append modified them)
        if has_unset or has_unset_value:
            existing_values_list = list(
                client.field_values.list(list_entry_id=ListEntryId(entry_id))
            )
            existing_values_serialized = [serialize_model_for_cli(v) for v in existing_values_list]

        # Phase 3a: Handle --unset (delete all values for field)
        for field_spec in unset_fields:
            target_field_id = resolved_fields[field_spec]  # Already resolved upfront
            existing_for_field = find_field_values_for_field(
                field_values=existing_values_serialized,
                field_id=target_field_id,
            )
            for fv in existing_for_field:
                fv_id = fv.get("id")
                if fv_id:
                    client.field_values.delete(fv_id)
                    deleted_count += 1

        # Phase 3b: Handle --unset-value (delete specific value)
        for field_spec, value_to_remove in unset_values:
            target_field_id = resolved_fields[field_spec]  # Already resolved upfront
            existing_for_field = find_field_values_for_field(
                field_values=existing_values_serialized,
                field_id=target_field_id,
            )
            value_str = value_to_remove.strip()
            found = False
            for fv in existing_for_field:
                fv_value = fv.get("value")
                if format_value_for_comparison(fv_value) == value_str:
                    fv_id = fv.get("id")
                    if fv_id:
                        client.field_values.delete(fv_id)
                        deleted_count += 1
                        found = True
                        break
            # Idempotent: silent success if value not found
            if not found:
                resolved_name = resolver.get_field_name(target_field_id) or field_spec
                warnings.append(
                    f"Value '{value_to_remove}' not found for field '{resolved_name}' "
                    "(already removed or never existed)."
                )

        # Build result
        if created_values:
            results["created"] = created_values
        if deleted_count > 0:
            results["deleted"] = deleted_count

        cmd_context = CommandContext(
            name="entry field",
            inputs={"listSelector": list_selector, "entryId": entry_id},
            modifiers=ctx_modifiers,
            resolved={k: str(v) for k, v in resolved.items() if v is not None}
            if resolved
            else None,
        )

        return CommandOutput(
            data=results,
            context=cmd_context,
            resolved=resolved,
            api_called=True,
        )

    run_command(ctx, command="entry field", fn=fn)
