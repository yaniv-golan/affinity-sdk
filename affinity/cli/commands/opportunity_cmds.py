from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from affinity.models.entities import Opportunity, OpportunityCreate, OpportunityUpdate
from affinity.models.pagination import PaginatedResponse
from affinity.models.types import ListType
from affinity.types import CompanyId, ListId, OpportunityId, PersonId

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..csv_utils import write_csv_to_stdout
from ..decorators import category, destructive, progress_capable
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import csv_output_options, csv_suboption_callback, output_options
from ..progress import ProgressManager, ProgressSettings
from ..resolve import resolve_list_selector
from ..resolvers import ResolvedEntity
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli
from ._entity_files_dump import download_single_file, dump_entity_files_bundle
from ._entity_files_read import parse_size, read_file_content
from .resolve_url_cmd import _parse_affinity_url


@click.group(name="opportunity", cls=RichGroup)
def opportunity_group() -> None:
    """Opportunity commands."""


def _resolve_opportunity_selector(
    *,
    selector: str,
) -> tuple[OpportunityId, dict[str, Any]]:
    raw = selector.strip()
    if raw.isdigit():
        opportunity_id = OpportunityId(int(raw))
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(opportunity_id),
            entity_type="opportunity",
            source="id",
        )
        return opportunity_id, {"opportunity": resolved.to_dict()}

    if raw.startswith(("http://", "https://")):
        url_parsed = _parse_affinity_url(raw)
        if url_parsed.type != "opportunity" or url_parsed.opportunity_id is None:
            raise CLIError(
                "Expected an opportunity URL like https://<tenant>.affinity.(co|com)/opportunities/<id>",
                exit_code=2,
                error_type="usage_error",
                details={"input": selector, "resolvedType": url_parsed.type},
            )
        opportunity_id = OpportunityId(int(url_parsed.opportunity_id))
        url_resolved = ResolvedEntity(
            input=selector,
            entity_id=int(opportunity_id),
            entity_type="opportunity",
            source="url",
            canonical_url=f"https://app.affinity.co/opportunities/{int(opportunity_id)}",
        )
        return opportunity_id, {"opportunity": url_resolved.to_dict()}

    raise CLIError(
        "Unrecognized opportunity selector.",
        exit_code=2,
        error_type="usage_error",
        hint='Use a numeric id or an Affinity URL like "https://<tenant>.affinity.co/opportunities/<id>".',
        details={"input": selector},
    )


@category("read")
@opportunity_group.command(name="ls", cls=RichCommand)
@click.option("--page-size", "-s", type=int, default=None, help="Page size (limit).")
@click.option(
    "--cursor", type=str, default=None, help="Resume from cursor (incompatible with --page-size)."
)
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N items total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@click.option(
    "--query",
    "-q",
    type=str,
    default=None,
    help="Fuzzy text search (simple matching).",
)
@click.option(
    "--csv-bom",
    is_flag=True,
    help="Add UTF-8 BOM for Excel (use with redirection: --csv --csv-bom > file.csv).",
    callback=csv_suboption_callback,
    expose_value=True,
)
@csv_output_options
@click.pass_obj
@apply_mcp_limits()
def opportunity_ls(
    ctx: CLIContext,
    *,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
    query: str | None,
    csv_bom: bool,
) -> None:
    """
    List opportunities.

    Use --query for free-text search.

    Examples:
    - `xaffinity opportunity ls`
    - `xaffinity opportunity ls --page-size 200`
    - `xaffinity opportunity ls --query "Series A" --all`
    - `xaffinity opportunity ls --cursor <cursor>`
    - `xaffinity opportunity ls --all --csv > opportunities.csv`
    - `xaffinity opportunity ls --all --output csv --csv-bom > opportunities.csv`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)

        if cursor is not None and page_size is not None:
            raise CLIError(
                "--cursor cannot be combined with --page-size.",
                exit_code=2,
                error_type="usage_error",
            )

        # Build CommandContext upfront for all return paths
        ctx_modifiers: dict[str, object] = {}
        if page_size is not None:
            ctx_modifiers["pageSize"] = page_size
        if cursor is not None:
            ctx_modifiers["cursor"] = cursor
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["allPages"] = True
        if query:
            ctx_modifiers["query"] = query
        if ctx.output == "csv":
            ctx_modifiers["csv"] = True
        if csv_bom:
            ctx_modifiers["csvBom"] = True

        cmd_context = CommandContext(
            name="opportunity ls",
            inputs={},
            modifiers=ctx_modifiers,
        )

        rows: list[dict[str, object]] = []
        first_page = True
        use_v1_search = query is not None

        show_progress = (
            ctx.progress != "never"
            and not ctx.quiet
            and (ctx.progress == "always" or sys.stderr.isatty())
        )

        # Use V1 search when --query is provided, otherwise V2 list
        pages_iter: Iterator[PaginatedResponse[Opportunity]]
        if use_v1_search:
            assert query is not None
            pages_iter = client.opportunities.search_pages(
                query,
                page_size=page_size,
                page_token=cursor,
            )
        else:
            pages_iter = client.opportunities.pages(limit=page_size, cursor=cursor)

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

            for page in pages_iter:
                # Get next cursor (unified property handles both V1 and V2 formats)
                next_cursor = page.next_cursor
                prev_cursor = page.pagination.prev_cursor  # V1 responses will have None here

                for idx, opportunity in enumerate(page.data):
                    rows.append(_opportunity_ls_row(opportunity))
                    if progress and task_id is not None:
                        progress.update(task_id, completed=len(rows))
                    if max_results is not None and len(rows) >= max_results:
                        stopped_mid_page = idx < (len(page.data) - 1)
                        if stopped_mid_page:
                            warnings.append(
                                "Results limited by --max-results. Use --all to fetch all results."
                            )
                        pagination = None
                        if next_cursor and not stopped_mid_page and next_cursor != cursor:
                            pagination = {
                                "opportunities": {
                                    "nextCursor": next_cursor,
                                    "prevCursor": prev_cursor,
                                }
                            }
                        return CommandOutput(
                            data={"opportunities": rows[:max_results]},
                            context=cmd_context,
                            pagination=pagination,
                            api_called=True,
                        )

                if first_page and not all_pages and max_results is None:
                    return CommandOutput(
                        data={"opportunities": rows},
                        context=cmd_context,
                        pagination=(
                            {
                                "opportunities": {
                                    "nextCursor": next_cursor,
                                    "prevCursor": prev_cursor,
                                }
                            }
                            if next_cursor
                            else None
                        ),
                        api_called=True,
                    )
                first_page = False

        # CSV output to stdout
        if ctx.output == "csv":
            fieldnames = list(rows[0].keys()) if rows else []
            write_csv_to_stdout(
                rows=rows,
                fieldnames=fieldnames,
                bom=csv_bom,
            )
            sys.exit(0)

        return CommandOutput(
            data={"opportunities": rows},
            context=cmd_context,
            pagination=None,
            api_called=True,
        )

    run_command(ctx, command="opportunity ls", fn=fn)


def _opportunity_ls_row(opportunity: Opportunity) -> dict[str, object]:
    """Build a row for opportunity ls output."""
    return {
        "id": int(opportunity.id),
        "name": opportunity.name,
        "listId": int(opportunity.list_id) if opportunity.list_id else None,
    }


@category("read")
@opportunity_group.command(name="get", cls=RichCommand)
@click.argument("opportunity_selector", type=str)
@click.option(
    "--details",
    "details",
    is_flag=True,
    help="Fetch a fuller payload with associations and list entries.",
)
@click.option(
    "--expand",
    "expand",
    multiple=True,
    type=click.Choice(["persons", "companies"]),
    help="Include related data (repeatable).",
)
@click.option(
    "--max-results",
    "--limit",
    "-n",
    type=int,
    default=None,
    help="Maximum items per expansion (default: 100).",
)
@click.option(
    "--all",
    "all_pages",
    is_flag=True,
    help="Fetch all expanded items (no limit).",
)
@output_options
@click.pass_obj
def opportunity_get(
    ctx: CLIContext,
    opportunity_selector: str,
    *,
    details: bool,
    expand: tuple[str, ...],
    max_results: int | None,
    all_pages: bool,
) -> None:
    """
    Get an opportunity by id or URL.

    Examples:
    - `xaffinity opportunity get 123`
    - `xaffinity opportunity get https://mydomain.affinity.com/opportunities/123`
    - `xaffinity opportunity get 123 --details`
    - `xaffinity opportunity get 123 --expand persons`
    - `xaffinity opportunity get 123 --expand persons --expand companies`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        opportunity_id, resolved = _resolve_opportunity_selector(selector=opportunity_selector)

        # Build CommandContext for opportunity get
        ctx_modifiers: dict[str, object] = {}
        if details:
            ctx_modifiers["details"] = True
        if expand:
            ctx_modifiers["expand"] = list(expand)
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["allPages"] = True

        cmd_context = CommandContext(
            name="opportunity get",
            inputs={"selector": opportunity_selector},
            modifiers=ctx_modifiers,
        )

        expand_set = {e.strip() for e in expand if e and e.strip()}

        # Use service methods instead of raw HTTP
        if details:
            opp = client.opportunities.get_details(opportunity_id)
        else:
            opp = client.opportunities.get(opportunity_id)

        data: dict[str, Any] = {"opportunity": serialize_model_for_cli(opp)}
        if not details and not opp.fields:
            data["opportunity"].pop("fields", None)

        # Fetch associations once if both persons and companies are requested (saves 1 V1 call)
        want_persons = "persons" in expand_set
        want_companies = "companies" in expand_set
        cached_person_ids: list[int] | None = None
        cached_company_ids: list[int] | None = None

        # Show spinner for expansion operations
        show_expand_progress = (
            expand_set
            and ctx.progress != "never"
            and not ctx.quiet
            and (ctx.progress == "always" or sys.stderr.isatty())
        )

        with ExitStack() as stack:
            if show_expand_progress:
                progress = stack.enter_context(
                    Progress(
                        SpinnerColumn(),
                        TextColumn("Fetching expanded data..."),
                        console=Console(file=sys.stderr),
                        transient=True,
                    )
                )
                progress.add_task("expand", total=None)

            if want_persons and want_companies:
                assoc = client.opportunities.get_associations(opportunity_id)
                cached_person_ids = [int(pid) for pid in assoc.person_ids]
                cached_company_ids = [int(cid) for cid in assoc.company_ids]

            # Handle persons expansion
            if want_persons:
                persons_cap = max_results
                if persons_cap is None and not all_pages:
                    persons_cap = 100
                if persons_cap is not None and persons_cap <= 0:
                    data["persons"] = []
                else:
                    # Use cached IDs if available, otherwise fetch
                    if cached_person_ids is not None:
                        person_ids = cached_person_ids
                    else:
                        person_ids = [
                            int(pid)
                            for pid in client.opportunities.get_associated_person_ids(
                                opportunity_id
                            )
                        ]
                    total_persons = len(person_ids)
                    if persons_cap is not None and total_persons > persons_cap:
                        warnings.append(
                            f"Persons truncated at {persons_cap:,} items; re-run with --all "
                            "or a higher --max-results to fetch more."
                        )
                        if total_persons > 50:
                            warnings.append(
                                f"Fetching {min(persons_cap, total_persons)} persons requires "
                                f"{min(persons_cap, total_persons) + 1} API calls."
                            )

                    persons = client.opportunities.get_associated_people(
                        opportunity_id,
                        max_results=persons_cap,
                    )
                    data["persons"] = [
                        {
                            "id": int(person.id),
                            "name": person.full_name,
                            "primaryEmail": person.primary_email,
                            "type": (
                                person.type.value
                                if hasattr(person.type, "value")
                                else person.type
                                if person.type
                                else None
                            ),
                        }
                        for person in persons
                    ]

            # Handle companies expansion
            if want_companies:
                companies_cap = max_results
                if companies_cap is None and not all_pages:
                    companies_cap = 100
                if companies_cap is not None and companies_cap <= 0:
                    data["companies"] = []
                else:
                    # Use cached IDs if available, otherwise fetch
                    if cached_company_ids is not None:
                        company_ids = cached_company_ids
                    else:
                        company_ids = [
                            int(cid)
                            for cid in client.opportunities.get_associated_company_ids(
                                opportunity_id
                            )
                        ]
                    total_companies = len(company_ids)
                    if companies_cap is not None and total_companies > companies_cap:
                        warnings.append(
                            f"Companies truncated at {companies_cap:,} items; re-run with --all "
                            "or a higher --max-results to fetch more."
                        )

                    companies = client.opportunities.get_associated_companies(
                        opportunity_id,
                        max_results=companies_cap,
                    )
                    data["companies"] = [
                        {
                            "id": int(company.id),
                            "name": company.name,
                            "domain": company.domain,
                        }
                        for company in companies
                    ]

        if expand_set:
            resolved["expand"] = sorted(expand_set)

        # Fetch field metadata if fields are present in response
        opp_payload = data.get("opportunity", {})
        opp_fields = opp_payload.get("fields") if isinstance(opp_payload, dict) else None
        opp_list_id = opp_payload.get("listId") if isinstance(opp_payload, dict) else None
        if isinstance(opp_fields, list) and opp_fields and opp_list_id is not None:
            try:
                from ..field_utils import build_field_id_to_name_map

                field_metadata = client.lists.get_fields(ListId(int(opp_list_id)))
                resolved["fieldMetadata"] = build_field_id_to_name_map(field_metadata)
            except Exception:
                # Field metadata is optional - continue without names if fetch fails
                pass

        return CommandOutput(
            data=data,
            context=cmd_context,
            resolved=resolved,
            api_called=True,
        )

    run_command(ctx, command="opportunity get", fn=fn)


@category("write")
@opportunity_group.command(name="create", cls=RichCommand)
@click.option("--name", required=True, help="Opportunity name.")
@click.option("--list", "list_selector", required=True, help="List id or exact list name.")
@click.option(
    "--person-id",
    "person_ids",
    multiple=True,
    type=int,
    help="Associate a person id (repeatable).",
)
@click.option(
    "--company-id",
    "company_ids",
    multiple=True,
    type=int,
    help="Associate a company id (repeatable).",
)
@output_options
@click.pass_obj
def opportunity_create(
    ctx: CLIContext,
    *,
    name: str,
    list_selector: str,
    person_ids: tuple[int, ...],
    company_ids: tuple[int, ...],
) -> None:
    """
    Create a new opportunity.

    Examples:
    - `xaffinity opportunity create --name "Series A" --list "Dealflow"`
    - `xaffinity opportunity create --name "Series A" --list 123 --person-id 1 --company-id 2`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        resolved_list = resolve_list_selector(client=client, selector=list_selector, cache=cache)
        if resolved_list.list.type != ListType.OPPORTUNITY:
            raise CLIError(
                "List is not an opportunity list.",
                exit_code=2,
                error_type="usage_error",
                details={
                    "listId": int(resolved_list.list.id),
                    "listType": resolved_list.list.type,
                },
            )

        data = OpportunityCreate(
            name=name,
            list_id=ListId(int(resolved_list.list.id)),
            person_ids=[PersonId(pid) for pid in person_ids],
            company_ids=[CompanyId(cid) for cid in company_ids],
        )
        created = client.opportunities.create(data)
        payload = serialize_model_for_cli(created)

        # Build CommandContext for opportunity create
        ctx_modifiers: dict[str, object] = {"name": name}
        if person_ids:
            ctx_modifiers["personIds"] = list(person_ids)
        if company_ids:
            ctx_modifiers["companyIds"] = list(company_ids)

        # Extract resolved list name for context
        ctx_resolved: dict[str, str] | None = None
        list_resolved = resolved_list.resolved.get("list", {})
        if isinstance(list_resolved, dict):
            list_name = list_resolved.get("entityName")
            if list_name:
                ctx_resolved = {"listId": str(list_name)}

        cmd_context = CommandContext(
            name="opportunity create",
            inputs={"listId": int(resolved_list.list.id)},
            modifiers=ctx_modifiers,
            resolved=ctx_resolved,
        )

        return CommandOutput(
            data={"opportunity": payload},
            context=cmd_context,
            resolved=resolved_list.resolved,
            api_called=True,
        )

    run_command(ctx, command="opportunity create", fn=fn)


@category("write")
@opportunity_group.command(name="update", cls=RichCommand)
@click.argument("opportunity_id", type=int)
@click.option("--name", default=None, help="Updated opportunity name.")
@click.option(
    "--person-id",
    "person_ids",
    multiple=True,
    type=int,
    help="Replace associated person ids (repeatable).",
)
@click.option(
    "--company-id",
    "company_ids",
    multiple=True,
    type=int,
    help="Replace associated company ids (repeatable).",
)
@output_options
@click.pass_obj
def opportunity_update(
    ctx: CLIContext,
    opportunity_id: int,
    *,
    name: str | None,
    person_ids: tuple[int, ...],
    company_ids: tuple[int, ...],
) -> None:
    """
    Update an opportunity (replaces association arrays when provided).

    Examples:
    - `xaffinity opportunity update 123 --name "Series A (Closed)"`
    - `xaffinity opportunity update 123 --person-id 1 --person-id 2`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)

        if name is None and not person_ids and not company_ids:
            raise CLIError(
                "No updates specified.",
                exit_code=2,
                error_type="usage_error",
                hint="Provide at least one of --name, --person-id, or --company-id.",
            )

        data = OpportunityUpdate(
            name=name,
            person_ids=[PersonId(pid) for pid in person_ids] if person_ids else None,
            company_ids=[CompanyId(cid) for cid in company_ids] if company_ids else None,
        )
        updated = client.opportunities.update(OpportunityId(opportunity_id), data)
        payload = serialize_model_for_cli(updated)

        resolved = ResolvedEntity(
            input=str(opportunity_id),
            entity_id=int(opportunity_id),
            entity_type="opportunity",
            source="id",
        )

        # Build CommandContext for opportunity update
        ctx_modifiers: dict[str, object] = {}
        if name:
            ctx_modifiers["name"] = name
        if person_ids:
            ctx_modifiers["personIds"] = list(person_ids)
        if company_ids:
            ctx_modifiers["companyIds"] = list(company_ids)

        cmd_context = CommandContext(
            name="opportunity update",
            inputs={"opportunityId": opportunity_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"opportunity": payload},
            context=cmd_context,
            resolved={"opportunity": resolved.to_dict()},
            api_called=True,
        )

    run_command(ctx, command="opportunity update", fn=fn)


@category("write")
@destructive
@opportunity_group.command(name="delete", cls=RichCommand)
@click.argument("opportunity_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def opportunity_delete(
    ctx: CLIContext,
    opportunity_id: int,
    yes: bool,
) -> None:
    """
    Delete an opportunity.

    Example:
    - `xaffinity opportunity delete 123 --yes`
    """
    if not yes:
        click.confirm(f"Delete opportunity {opportunity_id}?", abort=True)

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        success = client.opportunities.delete(OpportunityId(opportunity_id))

        resolved = ResolvedEntity(
            input=str(opportunity_id),
            entity_id=int(opportunity_id),
            entity_type="opportunity",
            source="id",
        )

        cmd_context = CommandContext(
            name="opportunity delete",
            inputs={"opportunityId": opportunity_id},
            modifiers={},
        )

        return CommandOutput(
            data={"opportunityId": opportunity_id, "success": success},
            context=cmd_context,
            resolved={"opportunity": resolved.to_dict()},
            api_called=True,
        )

    run_command(ctx, command="opportunity delete", fn=fn)


@opportunity_group.group(name="files", cls=RichGroup)
def opportunity_files_group() -> None:
    """Opportunity files."""


@category("read")
@opportunity_files_group.command(name="ls", cls=RichCommand)
@click.argument("opportunity", type=str)
@click.option(
    "--page-size",
    "-s",
    type=click.IntRange(1, 100),
    default=None,
    help="Page size (1-100).",
)
@click.option(
    "--cursor",
    type=str,
    default=None,
    help="Resume from pagination cursor (incompatible with --page-size).",
)
@click.option(
    "--max-results",
    "--limit",
    "-n",
    type=click.IntRange(1, None),
    default=None,
    help="Stop after N results (min 1).",
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@output_options
@click.pass_obj
@apply_mcp_limits()
def opportunity_files_ls(
    ctx_obj: CLIContext,
    opportunity: str,
    *,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """
    List files attached to an opportunity.

    OPPORTUNITY can be an ID or URL (name resolution not supported).

    Examples:

    - `xaffinity opportunity files ls 12345`
    - `xaffinity opportunity files ls "https://mycompany.affinity.co/opportunities/12345"`
    """
    # Detect if --max-results was explicitly set by user (vs MCP-injected default)
    max_results_explicit = False
    click_ctx = click.get_current_context(silent=True)
    if click_ctx is not None:
        get_source = getattr(cast(Any, click_ctx), "get_parameter_source", None)
        if callable(get_source):
            source_enum = getattr(cast(Any, click.core), "ParameterSource", None)
            default_source = getattr(source_enum, "DEFAULT", None) if source_enum else None
            source = get_source("max_results")
            if source is not None and source != default_source:
                max_results_explicit = True

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        # Validate cursor/page-size exclusivity
        if cursor is not None and page_size is not None:
            raise CLIError(
                "--cursor cannot be combined with --page-size.",
                exit_code=2,
                error_type="usage_error",
            )

        # Validate --all exclusivity
        if all_pages and cursor is not None:
            raise CLIError(
                "--all cannot be combined with --cursor.",
                exit_code=2,
                error_type="usage_error",
            )
        if all_pages and max_results is not None:
            raise CLIError(
                "--all cannot be combined with --max-results.",
                exit_code=2,
                error_type="usage_error",
            )

        client = ctx.get_client(warnings=warnings)

        # Resolve opportunity selector to ID (no client needed - ID/URL only)
        opportunity_id, resolved = _resolve_opportunity_selector(selector=opportunity)

        # Build modifiers dict (excluding None values)
        modifiers: dict[str, object] = {}
        if page_size is not None:
            modifiers["pageSize"] = page_size
        if cursor is not None:
            modifiers["cursor"] = cursor
        if max_results is not None:
            modifiers["maxResults"] = max_results
        if all_pages:
            modifiers["allPages"] = True

        cmd_context = CommandContext(
            name="opportunity files ls",
            inputs={"selector": opportunity},
            modifiers=modifiers,
        )

        # When truncated by --max-results, remove allPages from context (misleading)
        cmd_context_truncated = CommandContext(
            name="opportunity files ls",
            inputs={"selector": opportunity},
            modifiers={k: v for k, v in modifiers.items() if k != "allPages"},
        )

        results: list[dict[str, object]] = []
        first_page = True
        page_token: str | None = cursor

        while True:
            page = client.files.list(
                opportunity_id=opportunity_id,
                page_size=page_size,
                page_token=page_token,
            )

            for idx, f in enumerate(page.data):
                results.append(
                    {
                        "id": int(f.id),
                        "name": f.name,
                        "size": f.size,
                        "contentType": f.content_type,
                        "uploaderId": int(f.uploader_id),
                        "createdAt": f.created_at.isoformat(),
                    }
                )

                # Check if we've hit max_results
                if max_results is not None and len(results) >= max_results:
                    stopped_mid_page = idx < (len(page.data) - 1)
                    pagination = None
                    is_mcp_injected = not max_results_explicit

                    if page.next_cursor and not stopped_mid_page:
                        pagination = {"nextCursor": page.next_cursor, "prevCursor": None}
                        if is_mcp_injected:
                            warnings.append(
                                f"Results limited to {max_results} (MCP safety limit); "
                                "use --cursor to continue."
                            )
                        else:
                            warnings.append(
                                "Results limited by --max-results; "
                                "more data available (use --cursor to continue)."
                            )
                    elif stopped_mid_page or page.next_cursor:
                        if is_mcp_injected:
                            warnings.append(
                                f"Results limited to {max_results} (MCP safety limit); "
                                "more data may exist."
                            )
                        else:
                            warnings.append(
                                "Results limited by --max-results; "
                                "more data may exist but no resumption cursor available."
                            )
                    return CommandOutput(
                        data=results[:max_results],
                        context=cmd_context_truncated,
                        pagination=pagination,
                        resolved=resolved,
                        warnings=warnings,
                        api_called=True,
                        rate_limit=client.rate_limits.snapshot(),
                    )

            # Single page mode (no --all, no --max-results)
            if first_page and not all_pages and max_results is None:
                pagination = (
                    {"nextCursor": page.next_cursor, "prevCursor": None}
                    if page.next_cursor
                    else None
                )
                return CommandOutput(
                    data=results,
                    context=cmd_context,
                    pagination=pagination,
                    resolved=resolved,
                    warnings=warnings,
                    api_called=True,
                    rate_limit=client.rate_limits.snapshot(),
                )
            first_page = False

            page_token = page.next_cursor
            if not page_token:
                break

        return CommandOutput(
            data=results,
            context=cmd_context,
            pagination=None,
            resolved=resolved,
            warnings=warnings,
            api_called=True,
            rate_limit=client.rate_limits.snapshot(),
        )

    run_command(ctx_obj, command="opportunity files ls", fn=fn)


@category("read")
@opportunity_files_group.command(name="read", cls=RichCommand)
@click.argument("opportunity_id", type=int)
@click.option("--file-id", type=int, required=True, help="File ID to read.")
@click.option(
    "--offset",
    type=int,
    default=0,
    show_default=True,
    help="Byte offset to start reading.",
)
@click.option(
    "--limit",
    type=str,
    default="1MB",
    show_default=True,
    help="Max bytes to read (e.g., '1MB', '500KB', '1048576').",
)
@output_options
@click.pass_obj
def opportunity_files_read(
    ctx: CLIContext,
    opportunity_id: int,
    *,
    file_id: int,
    offset: int,
    limit: str,
) -> None:
    """Read file content with chunking support.

    Returns base64-encoded content. For large files, use --offset and --limit
    to fetch in chunks. The response includes 'nextOffset' for easy iteration.

    Examples:

    - `xaffinity opportunity files read 123 --file-id 456`
    - `xaffinity opportunity files read 123 --file-id 456 --offset 1048576`
    - `xaffinity opportunity files read 123 --file-id 456 --limit 500KB`
    """
    limit_bytes = parse_size(limit)
    read_file_content(
        ctx=ctx,
        entity_type="opportunity",
        entity_id=opportunity_id,
        file_id=file_id,
        offset=offset,
        limit=limit_bytes,
    )


@category("read")
@opportunity_files_group.command(name="download", cls=RichCommand)
@click.argument("opportunity_id", type=int)
@click.option(
    "--file-id",
    type=int,
    default=None,
    help="Download a single file by ID (omit for all files).",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(),
    default=None,
    help="Output path (file path for --file-id, directory for bulk mode).",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing files.")
@click.option(
    "--concurrency",
    type=int,
    default=3,
    show_default=True,
    help="Number of concurrent downloads (bulk mode only).",
)
@click.option(
    "--page-size",
    type=int,
    default=100,
    show_default=True,
    help="Page size for file listing (max 100, bulk mode only).",
)
@click.option("--max-files", type=int, default=None, help="Stop after N files (bulk mode only).")
@output_options
@click.pass_obj
def opportunity_files_download(
    ctx: CLIContext,
    opportunity_id: int,
    *,
    file_id: int | None,
    out_path: str | None,
    overwrite: bool,
    concurrency: int,
    page_size: int,
    max_files: int | None,
) -> None:
    """Download files attached to an opportunity.

    Single file mode (with --file-id):
        opportunity files download 123 --file-id 456 --out ./contract.pdf

    Bulk mode (without --file-id):
        opportunity files download 123 --out ./backups/
    """
    if file_id is not None:
        # Single file mode
        download_single_file(
            ctx=ctx,
            entity_type="opportunity",
            entity_id=opportunity_id,
            file_id=file_id,
            out_path=out_path,
            overwrite=overwrite,
        )
    else:
        # Bulk mode (original dump behavior)
        def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
            ctx_modifiers: dict[str, object] = {}
            if out_path:
                ctx_modifiers["outDir"] = out_path
            if overwrite:
                ctx_modifiers["overwrite"] = True
            if concurrency != 3:
                ctx_modifiers["concurrency"] = concurrency
            if page_size != 100:
                ctx_modifiers["pageSize"] = page_size
            if max_files is not None:
                ctx_modifiers["maxFiles"] = max_files

            cmd_context = CommandContext(
                name="opportunity files download",
                inputs={"opportunityId": opportunity_id},
                modifiers=ctx_modifiers,
            )

            return asyncio.run(
                dump_entity_files_bundle(
                    ctx=ctx,
                    warnings=warnings,
                    out_dir=out_path,
                    overwrite=overwrite,
                    concurrency=concurrency,
                    page_size=page_size,
                    max_files=max_files,
                    default_dirname=f"affinity-opportunity-{opportunity_id}-files",
                    manifest_entity={"type": "opportunity", "opportunityId": opportunity_id},
                    files_list_kwargs={"opportunity_id": OpportunityId(opportunity_id)},
                    context=cmd_context,
                )
            )

        run_command(ctx, command="opportunity files download", fn=fn)


@category("write")
@progress_capable
@opportunity_files_group.command(name="upload", cls=RichCommand)
@click.argument("opportunity_id", type=int)
@click.option(
    "--file",
    "file_paths",
    type=click.Path(exists=False),
    multiple=True,
    required=True,
    help="File path to upload (repeatable).",
)
@output_options
@click.pass_obj
def opportunity_files_upload(
    ctx: CLIContext,
    opportunity_id: int,
    *,
    file_paths: tuple[str, ...],
) -> None:
    """
    Upload files to an opportunity.

    Examples:

    - `xaffinity opportunity files upload 123 --file doc.pdf`
    - `xaffinity opportunity files upload 123 --file a.pdf --file b.pdf`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)

        # Validate all file paths first
        paths: list[Path] = []
        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                raise CLIError(
                    f"File not found: {fp}",
                    exit_code=2,
                    error_type="usage_error",
                    hint="Check the file path and try again.",
                )
            if not p.is_file():
                raise CLIError(
                    f"Not a regular file: {fp}",
                    exit_code=2,
                    error_type="usage_error",
                    hint="Only regular files can be uploaded, not directories.",
                )
            paths.append(p)

        results: list[dict[str, object]] = []
        settings = ProgressSettings(mode=ctx.progress, quiet=ctx.quiet)

        with ProgressManager(settings=settings) as pm:
            for p in paths:
                file_size = p.stat().st_size
                _task_id, cb = pm.task(
                    description=f"upload {p.name}",
                    total_bytes=file_size,
                )
                success = client.files.upload_path(
                    p,
                    opportunity_id=OpportunityId(opportunity_id),
                    on_progress=cb,
                )
                results.append(
                    {
                        "file": str(p),
                        "filename": p.name,
                        "size": file_size,
                        "success": success,
                    }
                )

        cmd_context = CommandContext(
            name="opportunity files upload",
            inputs={"opportunityId": opportunity_id},
            modifiers={"files": list(file_paths)},
        )

        return CommandOutput(
            data={"uploads": results, "opportunityId": opportunity_id},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="opportunity files upload", fn=fn)


def _get_opportunity_list_id(*, client: Any, opportunity_id: int) -> int:
    """Fetch opportunity and return its list_id."""
    opp = client.opportunities.get(OpportunityId(opportunity_id))
    if opp.list_id is None:
        raise CLIError(
            "Opportunity has no list_id.",
            exit_code=2,
            error_type="internal_error",
        )
    return int(opp.list_id)


@category("write")
@opportunity_group.command(name="field", cls=RichCommand)
@click.argument("opportunity_id", type=int)
@click.option(
    "--set",
    "set_values",
    nargs=2,
    multiple=True,
    metavar="FIELD VALUE",
    help="Set field value (repeatable). Use two args: FIELD VALUE.",
)
@click.option(
    "--unset",
    "unset_fields",
    multiple=True,
    metavar="FIELD",
    help="Unset field (repeatable). Removes all values for the field.",
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
def opportunity_field(
    ctx: CLIContext,
    opportunity_id: int,
    *,
    set_values: tuple[tuple[str, str], ...],
    unset_fields: tuple[str, ...],
    json_input: str | None,
    get_fields: tuple[str, ...],
) -> None:
    """
    Manage opportunity field values.

    Unified command for getting, setting, and unsetting field values.
    For field names with spaces, use quotes.

    Examples:

    - `xaffinity opportunity field 123 --set Status "Active"`
    - `xaffinity opportunity field 123 --set Status "Active" --set Stage "Negotiation"`
    - `xaffinity opportunity field 123 --unset Status`
    - `xaffinity opportunity field 123 --set-json '{"Status": "Active", "Stage": "Negotiation"}'`
    - `xaffinity opportunity field 123 --get Status --get Stage`
    """
    import json as json_module

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        from ..field_utils import (
            FieldResolver,
            execute_v1_set_phase,
            fetch_field_metadata,
            find_field_values_for_field,
            pre_validate_set_operations,
        )

        # Validate: at least one operation must be specified
        has_set = bool(set_values) or bool(json_input)
        has_unset = bool(unset_fields)
        has_get = bool(get_fields)

        if not has_set and not has_unset and not has_get:
            raise CLIError(
                "Provide at least one of --set, --unset, --set-json, or --get.",
                exit_code=2,
                error_type="usage_error",
            )

        # Validate: --get is exclusive (can't mix read with write)
        if has_get and (has_set or has_unset):
            raise CLIError(
                "--get cannot be combined with --set, --unset, or --set-json.",
                exit_code=2,
                error_type="usage_error",
            )

        client = ctx.get_client(warnings=warnings)
        field_metadata = fetch_field_metadata(client=client, entity_type="opportunity")
        resolver = FieldResolver(field_metadata)

        results: dict[str, Any] = {}

        # Build modifiers for CommandContext
        ctx_modifiers: dict[str, object] = {}
        if set_values:
            ctx_modifiers["set"] = [list(sv) for sv in set_values]
        if unset_fields:
            ctx_modifiers["unset"] = list(unset_fields)
        if json_input:
            ctx_modifiers["json"] = json_input
        if get_fields:
            ctx_modifiers["get"] = list(get_fields)

        # Handle --get: read field values
        if has_get:
            existing_values = client.field_values.list(opportunity_id=OpportunityId(opportunity_id))
            field_results: dict[str, Any] = {}

            for field_name in get_fields:
                target_field_id = resolver.resolve_field_name_or_id(field_name, context="field")
                field_values = find_field_values_for_field(
                    field_values=[serialize_model_for_cli(v) for v in existing_values],
                    field_id=target_field_id,
                )
                resolved_name = resolver.get_field_name(target_field_id) or field_name
                if field_values:
                    if len(field_values) == 1:
                        field_results[resolved_name] = field_values[0].get("value")
                    else:
                        field_results[resolved_name] = [fv.get("value") for fv in field_values]
                else:
                    field_results[resolved_name] = None

            results["fields"] = field_results

            cmd_context = CommandContext(
                name="opportunity field",
                inputs={"opportunityId": opportunity_id},
                modifiers=ctx_modifiers,
            )

            return CommandOutput(
                data=results,
                context=cmd_context,
                api_called=True,
            )

        # Handle --set and --json: set field values
        # Phase 1: collect raw --set + --set-json operations.
        raw_set_operations: list[tuple[str, Any]] = []

        for field_name, value in set_values:
            raw_set_operations.append((field_name, value))

        if json_input:
            try:
                json_data = json_module.loads(json_input)
                if not isinstance(json_data, dict):
                    raise CLIError(
                        "--json must be a JSON object.",
                        exit_code=2,
                        error_type="usage_error",
                    )
                for field_name, value in json_data.items():
                    raw_set_operations.append((field_name, value))
            except json_module.JSONDecodeError as e:
                raise CLIError(
                    f"Invalid JSON: {e}",
                    exit_code=2,
                    error_type="usage_error",
                ) from e

        # Phase 2: hoist field-name resolution upfront.
        resolved_set_ops: list[tuple[str, Any]] = []
        for field_name, value in raw_set_operations:
            target_field_id = resolver.resolve_field_name_or_id(field_name, context="field")
            resolved_set_ops.append((target_field_id, value))

        # Phase 3: pre-validate ALL values up front (same strict default).
        pre_resolved_set = pre_validate_set_operations(resolver, resolved_set_ops)

        # Phase 4: fetch existing values + execute set phase via shared helper.
        # The shared helper handles V1-numeric mapping for enriched fields via
        # ``resolver.to_v1_numeric()`` — this fixes a latent bug where the
        # in-line implementation passed the V2 enriched literal directly to
        # ``FieldValueCreate``, breaking enriched-field writes on opportunities.
        existing_values = client.field_values.list(opportunity_id=OpportunityId(opportunity_id))
        existing_values_serialized = [serialize_model_for_cli(v) for v in existing_values]

        created_values, set_deleted_count = execute_v1_set_phase(
            client=client,
            entity_kind="opportunity",
            entity_id=opportunity_id,
            pre_resolved_ops=pre_resolved_set,
            existing_values_serialized=existing_values_serialized,
            resolver=resolver,
        )

        # Handle --unset: hoist resolution upfront so a typo aborts cleanly.
        deleted_count = set_deleted_count
        unset_numeric_ids: list[int] = []
        for field_name in unset_fields:
            target_field_id = resolver.resolve_field_name_or_id(field_name, context="field")
            numeric_field_id = resolver.to_v1_numeric(
                client, target_field_id, entity_type="opportunity"
            )
            unset_numeric_ids.append(numeric_field_id)
        if unset_numeric_ids:
            existing_values = client.field_values.list(opportunity_id=OpportunityId(opportunity_id))
            existing_values_serialized = [serialize_model_for_cli(v) for v in existing_values]
        for numeric_field_id in unset_numeric_ids:
            existing_for_field = find_field_values_for_field(
                field_values=existing_values_serialized,
                field_id=numeric_field_id,
            )
            for fv in existing_for_field:
                fv_id = fv.get("id")
                if fv_id:
                    client.field_values.delete(fv_id)
                    deleted_count += 1

        # Build result
        if created_values:
            results["created"] = created_values
        if deleted_count > 0:
            results["deleted"] = deleted_count

        cmd_context = CommandContext(
            name="opportunity field",
            inputs={"opportunityId": opportunity_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data=results,
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="opportunity field", fn=fn)
