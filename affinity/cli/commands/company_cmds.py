from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from ..session_cache import SessionCache

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn

from affinity.exceptions import DuplicateEntityError
from affinity.models.entities import Company, CompanyCreate, CompanyUpdate
from affinity.models.types import EnrichedFieldId, FieldId
from affinity.types import CompanyId, FieldType, ListId, PersonId

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..csv_utils import write_csv_to_stdout
from ..decorators import category, destructive, progress_capable
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import csv_output_options, csv_suboption_callback, output_options
from ..progress import ProgressManager, ProgressSettings
from ..resolve import get_company_fields, resolve_list_selector
from ..resolvers import ResolvedEntity
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli
from ._entity_files_dump import download_single_file, dump_entity_files_bundle
from ._entity_files_read import parse_size, read_file_content
from ._list_entry_fields import (
    ListEntryFieldsScope,
    build_list_entry_field_rows,
    filter_list_entry_fields,
)
from ._v1_parsing import validate_domain
from .resolve_url_cmd import _parse_affinity_url


def _fetch_v2_collection(
    *,
    client: Any,
    path: str,
    section: str,
    default_limit: int,
    default_cap: int | None,
    allow_unbounded: bool,
    max_results: int | None,
    all_pages: bool,
    warnings: list[str],
    pagination: dict[str, Any],
    keep_item: Callable[[Any], bool] | None = None,
) -> list[Any]:
    """Fetch a paginated V2 collection with configurable limits.

    This helper centralizes the pagination logic for fetching lists, list-entries,
    and other V2 collections. It handles:
    - Page size limits
    - Max result caps
    - Pagination cursor tracking
    - Optional item filtering

    Args:
        client: The Affinity client instance.
        path: The API path to fetch (e.g., "/companies/123/lists").
        section: Name for this section (used in warnings and pagination keys).
        default_limit: Default page size for API requests.
        default_cap: Default max items if no explicit cap and not fetching all pages.
        allow_unbounded: Whether unbounded fetching is allowed without --all.
        max_results: Explicit max results limit (from --max-results).
        all_pages: Whether to fetch all pages (from --all flag).
        warnings: List to append warnings to (mutated in place).
        pagination: Dict to store pagination cursors (mutated in place).
        keep_item: Optional filter function to keep only matching items.

    Returns:
        List of fetched items.
    """
    effective_cap = max_results
    if effective_cap is None and default_cap is not None and not all_pages:
        effective_cap = default_cap
    if effective_cap is not None and effective_cap <= 0:
        return []

    should_paginate = all_pages or allow_unbounded or effective_cap is not None
    limit = default_limit
    if effective_cap is not None:
        limit = min(default_limit, effective_cap)

    truncated_mid_page = False
    payload = client._http.get(path, params={"limit": limit} if limit else None)
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        rows = []
    page_items = list(rows)
    if keep_item is not None:
        page_items = [r for r in page_items if keep_item(r)]
    items: list[Any] = page_items

    page_pagination = payload.get("pagination", {})
    if not isinstance(page_pagination, dict):
        page_pagination = {}
    next_url = page_pagination.get("nextUrl")
    prev_url = page_pagination.get("prevUrl")

    if effective_cap is not None and len(items) > effective_cap:
        truncated_mid_page = True
        items = items[:effective_cap]
        next_url = None

    while (
        should_paginate
        and isinstance(next_url, str)
        and next_url
        and (effective_cap is None or len(items) < effective_cap)
    ):
        payload = client._http.get_url(next_url)
        rows = payload.get("data", [])
        if isinstance(rows, list):
            page_items = list(rows)
            if keep_item is not None:
                page_items = [r for r in page_items if keep_item(r)]
            items.extend(page_items)
        page_pagination = payload.get("pagination", {})
        if not isinstance(page_pagination, dict):
            page_pagination = {}
        next_url = page_pagination.get("nextUrl")
        prev_url = page_pagination.get("prevUrl")

        if effective_cap is not None and len(items) > effective_cap:
            truncated_mid_page = True
            items = items[:effective_cap]
            next_url = None
            break

    if truncated_mid_page and effective_cap is not None:
        warnings.append(
            f"{section} limited to {effective_cap:,} items. Use --all to fetch all results."
        )
    elif isinstance(next_url, str) and next_url:
        pagination[section] = {"nextCursor": next_url, "prevCursor": prev_url}

    return items


def _normalize_sdk_item(item: Any) -> Any:
    """Normalize an SDK model or dict to a plain dict for JSON output."""
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        payload = dump(by_alias=True, mode="json")
        fields_raw = getattr(item, "fields_raw", None)
        if isinstance(fields_raw, list):
            payload["fields"] = fields_raw
        return payload
    return item


def _fetch_v2_collection_sdk(
    *,
    fetch_page: Callable[[int | None, str | None], Any],
    section: str,
    default_limit: int,
    default_cap: int | None,
    allow_unbounded: bool,
    max_results: int | None,
    all_pages: bool,
    warnings: list[str],
    pagination: dict[str, Any],
    keep_item: Callable[[Any], bool] | None = None,
) -> list[Any]:
    """Fetch a paginated V2 collection using SDK methods.

    This helper centralizes the pagination logic for fetching lists, list-entries,
    and other V2 collections using SDK page methods. It handles:
    - Page size limits
    - Max result caps
    - Pagination cursor tracking
    - Optional item filtering
    - Normalizing SDK models to dicts

    Args:
        fetch_page: Callback to fetch a page, takes (limit, cursor) and returns a Page object.
        section: Name for this section (used in warnings and pagination keys).
        default_limit: Default page size for API requests.
        default_cap: Default max items if no explicit cap and not fetching all pages.
        allow_unbounded: Whether unbounded fetching is allowed without --all.
        max_results: Explicit max results limit (from --max-results).
        all_pages: Whether to fetch all pages (from --all flag).
        warnings: List to append warnings to (mutated in place).
        pagination: Dict to store pagination cursors (mutated in place).
        keep_item: Optional filter function to keep only matching items.

    Returns:
        List of fetched items (as dicts).
    """
    effective_cap = max_results
    if effective_cap is None and default_cap is not None and not all_pages:
        effective_cap = default_cap
    if effective_cap is not None and effective_cap <= 0:
        return []

    should_paginate = all_pages or allow_unbounded or effective_cap is not None
    limit = default_limit
    if effective_cap is not None:
        limit = min(default_limit, effective_cap)

    truncated_mid_page = False
    page = fetch_page(limit, None)
    rows = page.data if isinstance(page.data, list) else []
    page_items = [_normalize_sdk_item(r) for r in rows]
    if keep_item is not None:
        page_items = [r for r in page_items if keep_item(r)]
    items: list[Any] = list(page_items)

    page_pagination = getattr(page, "pagination", None)
    next_url = getattr(page_pagination, "next_cursor", None) if page_pagination else None
    prev_url = getattr(page_pagination, "prev_cursor", None) if page_pagination else None

    if effective_cap is not None and len(items) > effective_cap:
        truncated_mid_page = True
        items = items[:effective_cap]
        next_url = None

    while (
        should_paginate
        and isinstance(next_url, str)
        and next_url
        and (effective_cap is None or len(items) < effective_cap)
    ):
        page = fetch_page(None, next_url)
        rows = page.data if isinstance(page.data, list) else []
        if rows:
            page_items = [_normalize_sdk_item(r) for r in rows]
            if keep_item is not None:
                page_items = [r for r in page_items if keep_item(r)]
            items.extend(page_items)
        page_pagination = getattr(page, "pagination", None)
        next_url = getattr(page_pagination, "next_cursor", None) if page_pagination else None
        prev_url = getattr(page_pagination, "prev_cursor", None) if page_pagination else None

        if effective_cap is not None and len(items) > effective_cap:
            truncated_mid_page = True
            items = items[:effective_cap]
            next_url = None
            break

    if truncated_mid_page and effective_cap is not None:
        warnings.append(
            f"{section} limited to {effective_cap:,} items. Use --all to fetch all results."
        )
    elif isinstance(next_url, str) and next_url:
        pagination[section] = {"nextCursor": next_url, "prevCursor": prev_url}

    return items


@click.group(name="company", cls=RichGroup)
def company_group() -> None:
    """Company commands."""


def _parse_field_types(values: tuple[str, ...]) -> list[FieldType] | None:
    """Parse --field-type option values to FieldType enums."""
    if not values:
        return None
    result: list[FieldType] = []
    valid_types = {ft.value.lower(): ft for ft in FieldType}
    for v in values:
        lower = v.lower()
        if lower not in valid_types:
            raise CLIError(
                f"Unknown field type: {v}",
                exit_code=2,
                error_type="usage_error",
                hint=f"Valid types: {', '.join(sorted(valid_types.keys()))}",
            )
        result.append(valid_types[lower])
    return result


@category("read")
@company_group.command(name="ls", cls=RichCommand)
@click.option("--page-size", "-s", type=int, default=None, help="Page size (limit).")
@click.option(
    "--cursor", type=str, default=None, help="Resume from cursor (incompatible with --page-size)."
)
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N items total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@click.option(
    "--field",
    "field_ids",
    type=str,
    multiple=True,
    help="Field ID or name to include (repeatable).",
)
@click.option(
    "--field-type",
    "field_types",
    type=str,
    multiple=True,
    help="Field type to include (repeatable). Values: global, enriched, relationship-intelligence.",
)
@click.option(
    "--filter",
    "filter_expr",
    type=str,
    default=None,
    help="Filter: 'field op value'. Ops: = != =~ =^ =$ > < >= <=. E.g., 'name =~ \"Acme\"'.",
)
@click.option(
    "--query",
    "-q",
    type=str,
    default=None,
    help="Fuzzy text search (simple matching). Use --filter for structured queries.",
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
def company_ls(
    ctx: CLIContext,
    *,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
    field_ids: tuple[str, ...],
    field_types: tuple[str, ...],
    filter_expr: str | None,
    query: str | None,
    csv_bom: bool,
) -> None:
    """
    List companies.

    Supports field selection and field types. The V2 /companies endpoint does
    not support server-side filtering — use --query for free-text search
    (name/domain fuzzy match).

    Examples:

    - `xaffinity company ls`
    - `xaffinity company ls --page-size 50`
    - `xaffinity company ls --field-type enriched --all`
    - `xaffinity company ls --query "Acme" --all`
    - `xaffinity company ls --all --csv > companies.csv`
    - `xaffinity company ls --all --output csv --csv-bom > companies.csv`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if filter_expr is not None:
            raise CLIError(
                "The V2 /companies endpoint does not support server-side filtering. "
                "The --filter flag was silently ignored in previous versions. "
                "Use --query TERM for name/domain fuzzy search instead.",
                exit_code=2,
                error_type="unsupported_filter",
                hint='Try: xaffinity company ls --query "Acme"',
                details={"rejected_filter": str(filter_expr)},
            )

        client = ctx.get_client(warnings=warnings)

        if cursor is not None and page_size is not None:
            raise CLIError(
                "--cursor cannot be combined with --page-size.",
                exit_code=2,
                error_type="usage_error",
            )

        if query is not None and filter_expr is not None:
            raise CLIError(
                "--query cannot be combined with --filter (different APIs).",
                exit_code=2,
                error_type="usage_error",
                hint="Use --query for free-text search or --filter for structured filtering.",
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
        if field_ids:
            ctx_modifiers["fieldIds"] = list(field_ids)
        if field_types:
            ctx_modifiers["fieldTypes"] = list(field_types)
        if filter_expr:
            ctx_modifiers["filter"] = filter_expr
        if query:
            ctx_modifiers["query"] = query
        if ctx.output == "csv":
            ctx_modifiers["csv"] = True
        if csv_bom:
            ctx_modifiers["csvBom"] = True

        cmd_context = CommandContext(
            name="company ls",
            inputs={},
            modifiers=ctx_modifiers,
        )

        parsed_field_types = _parse_field_types(field_types)
        parsed_field_ids: list[FieldId] | None = (
            [FieldId(fid) for fid in field_ids] if field_ids else None
        )

        rows: list[dict[str, object]] = []
        first_page = True
        use_v1_search = query is not None
        wants_fields = bool(field_ids or field_types)

        show_progress = (
            ctx.progress != "never"
            and not ctx.quiet
            and (ctx.progress == "always" or sys.stderr.isatty())
        )

        # Progress description based on operation type
        task_description = "Searching" if use_v1_search else "Fetching"

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
                task_id = progress.add_task(task_description, total=max_results)

            # Helper to check max_results and return early if needed
            def _check_max_results(
                rows: list[dict[str, object]],
                idx: int,
                page_len: int,
                next_cursor: str | None,
                prev_cursor: str | None,
            ) -> CommandOutput | None:
                if max_results is not None and len(rows) >= max_results:
                    stopped_mid_page = idx < (page_len - 1)
                    if stopped_mid_page:
                        warnings.append(
                            "Results limited by --max-results. Use --all to fetch all results."
                        )
                    pagination = None
                    if next_cursor and not stopped_mid_page and next_cursor != cursor:
                        pagination = {
                            "companies": {
                                "nextCursor": next_cursor,
                                "prevCursor": prev_cursor,
                            }
                        }
                    return CommandOutput(
                        data={"companies": rows[:max_results]},
                        context=cmd_context,
                        pagination=pagination,
                        api_called=True,
                    )
                return None

            # Three paths: V2-only, V1-only, or Hybrid (V1 search + V2 batch fetch)
            if use_v1_search and wants_fields:
                # Hybrid: V1 search for IDs, then V2 batch fetch with field data
                assert query is not None
                for v1_page in client.companies.search_pages(
                    query,
                    page_size=page_size,
                    page_token=cursor,
                ):
                    next_cursor = v1_page.next_cursor
                    prev_cursor = None  # V1 doesn't have prev cursor

                    if v1_page.data:
                        # Batch fetch from V2 with field data
                        company_ids = [CompanyId(c.id) for c in v1_page.data]
                        v2_response = client.companies.list(
                            ids=company_ids,
                            field_ids=parsed_field_ids,
                            field_types=parsed_field_types,
                        )
                        for idx, company in enumerate(v2_response.data):
                            rows.append(_company_ls_row(company))
                            if progress and task_id is not None:
                                progress.update(task_id, completed=len(rows))
                            result = _check_max_results(
                                rows, idx, len(v2_response.data), next_cursor, prev_cursor
                            )
                            if result is not None:
                                return result

                    if first_page and not all_pages and max_results is None:
                        return CommandOutput(
                            data={"companies": rows},
                            context=cmd_context,
                            pagination=(
                                {"companies": {"nextCursor": next_cursor, "prevCursor": None}}
                                if next_cursor
                                else None
                            ),
                            api_called=True,
                        )
                    first_page = False

            elif use_v1_search:
                # Search without field data
                assert query is not None
                for search_page in client.companies.search_pages(
                    query,
                    page_size=page_size,
                    page_token=cursor,
                ):
                    next_cursor = search_page.next_cursor
                    prev_cursor = None  # Search doesn't have prev cursor

                    for idx, company in enumerate(search_page.data):
                        rows.append(_company_ls_row(company))
                        if progress and task_id is not None:
                            progress.update(task_id, completed=len(rows))
                        result = _check_max_results(
                            rows, idx, len(search_page.data), next_cursor, prev_cursor
                        )
                        if result is not None:
                            return result

                    if first_page and not all_pages and max_results is None:
                        return CommandOutput(
                            data={"companies": rows},
                            context=cmd_context,
                            pagination=(
                                {"companies": {"nextCursor": next_cursor, "prevCursor": None}}
                                if next_cursor
                                else None
                            ),
                            api_called=True,
                        )
                    first_page = False

            else:
                # List with optional field data
                for page in client.companies.pages(
                    field_ids=parsed_field_ids,
                    field_types=parsed_field_types,
                    limit=page_size,
                    cursor=cursor,
                ):
                    next_cursor = page.pagination.next_cursor
                    prev_cursor = page.pagination.prev_cursor

                    for idx, company in enumerate(page.data):
                        rows.append(_company_ls_row(company))
                        if progress and task_id is not None:
                            progress.update(task_id, completed=len(rows))
                        result = _check_max_results(
                            rows, idx, len(page.data), next_cursor, prev_cursor
                        )
                        if result is not None:
                            return result

                    if first_page and not all_pages and max_results is None:
                        return CommandOutput(
                            data={"companies": rows},
                            context=cmd_context,
                            pagination=(
                                {
                                    "companies": {
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
            data={"companies": rows},
            context=cmd_context,
            pagination=None,
            api_called=True,
        )

    run_command(ctx, command="company ls", fn=fn)


def _company_ls_row(company: Company) -> dict[str, object]:
    """Build a row for company ls output."""
    return {
        "id": int(company.id),
        "name": company.name,
        "domain": company.domain,
        "domains": company.domains,
    }


_COMPANY_FIELDS_ALL_TYPES: tuple[str, ...] = (
    FieldType.GLOBAL.value,
    FieldType.ENRICHED.value,
    FieldType.RELATIONSHIP_INTELLIGENCE.value,
)


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _resolve_company_selector(
    *, client: Any, selector: str, cache: SessionCache | None = None
) -> tuple[CompanyId, dict[str, Any]]:
    raw = selector.strip()
    if raw.isdigit():
        company_id = CompanyId(int(raw))
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(company_id),
            entity_type="company",
            source="id",
        )
        return company_id, {"company": resolved.to_dict()}

    if raw.startswith(("http://", "https://")):
        url_resolved = _parse_affinity_url(raw)
        if url_resolved.type != "company" or url_resolved.company_id is None:
            raise CLIError(
                "Expected a company URL like https://<tenant>.affinity.(co|com)/companies/<id>",
                exit_code=2,
                error_type="usage_error",
                details={"input": selector, "resolvedType": url_resolved.type},
            )
        company_id = CompanyId(int(url_resolved.company_id))
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(company_id),
            entity_type="company",
            source="url",
            canonical_url=f"https://app.affinity.co/companies/{int(company_id)}",
        )
        return company_id, {"company": resolved.to_dict()}

    lowered = raw.lower()
    if lowered.startswith("domain:"):
        domain = _strip_wrapping_quotes(raw.split(":", 1)[1])
        company_id = _resolve_company_by_domain(client=client, domain=domain, cache=cache)
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(company_id),
            entity_type="company",
            source="domain",
        )
        return company_id, {"company": resolved.to_dict()}

    if lowered.startswith("name:"):
        name = _strip_wrapping_quotes(raw.split(":", 1)[1])
        company_id = _resolve_company_by_name(client=client, name=name, cache=cache)
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(company_id),
            entity_type="company",
            source="name",
        )
        return company_id, {"company": resolved.to_dict()}

    raise CLIError(
        "Unrecognized company selector.",
        exit_code=2,
        error_type="usage_error",
        hint='Use a numeric id, an Affinity URL, or "domain:<x>" / "name:<x>".',
        details={"input": selector},
    )


def _resolve_company_by_domain(
    *, client: Any, domain: str, cache: SessionCache | None = None
) -> CompanyId:
    domain = domain.strip()
    if not domain:
        raise CLIError("Domain cannot be empty.", exit_code=2, error_type="usage_error")

    cache_key = f"company_resolve_domain_{domain.lower()}"

    # Check cache first
    if cache and cache.enabled:
        cached = cache.get(cache_key, Company)
        if cached is not None:
            return CompanyId(int(cached.id))

    matches: list[Company] = []
    domain_lower = domain.lower()

    for page in client.companies.search_pages(domain, page_size=500):
        for company in page.data:
            domains: list[str] = []
            if company.domain:
                domains.append(company.domain)
            domains.extend(company.domains or [])
            if any(d.lower() == domain_lower for d in domains):
                matches.append(company)
                if len(matches) >= 20:
                    break
        if len(matches) >= 20 or not page.next_cursor:
            break

    if not matches:
        raise CLIError(
            f'Company not found for domain "{domain}"',
            exit_code=4,
            error_type="not_found",
            hint=f'Run `xaffinity company ls --query "{domain}"` to explore matches.',
            details={"domain": domain},
        )
    if len(matches) > 1:
        raise CLIError(
            f'Ambiguous company domain "{domain}" ({len(matches)} matches)',
            exit_code=2,
            error_type="ambiguous_resolution",
            details={
                "domain": domain,
                "matches": [
                    {"companyId": int(c.id), "name": c.name, "domain": c.domain}
                    for c in matches[:20]
                ],
            },
        )

    # Cache the result after successful resolution
    if cache and cache.enabled:
        cache.set(cache_key, matches[0])

    return CompanyId(int(matches[0].id))


def _resolve_company_by_name(
    *, client: Any, name: str, cache: SessionCache | None = None
) -> CompanyId:
    name = name.strip()
    if not name:
        raise CLIError("Name cannot be empty.", exit_code=2, error_type="usage_error")

    cache_key = f"company_resolve_name_{name.lower()}"

    # Check cache first
    if cache and cache.enabled:
        cached = cache.get(cache_key, Company)
        if cached is not None:
            return CompanyId(int(cached.id))

    matches: list[Company] = []
    name_lower = name.lower()

    for page in client.companies.search_pages(name, page_size=500):
        for company in page.data:
            if company.name.lower() == name_lower:
                matches.append(company)
                if len(matches) >= 20:
                    break
        if len(matches) >= 20 or not page.next_cursor:
            break

    if not matches:
        raise CLIError(
            f'Company not found for name "{name}"',
            exit_code=4,
            error_type="not_found",
            hint=f'Run `xaffinity company ls --query "{name}"` to explore matches.',
            details={"name": name},
        )
    if len(matches) > 1:
        raise CLIError(
            f'Ambiguous company name "{name}" ({len(matches)} matches)',
            exit_code=2,
            error_type="ambiguous_resolution",
            details={
                "name": name,
                "matches": [
                    {"companyId": int(c.id), "name": c.name, "domain": c.domain}
                    for c in matches[:20]
                ],
            },
        )

    # Cache the result after successful resolution
    if cache and cache.enabled:
        cache.set(cache_key, matches[0])

    return CompanyId(int(matches[0].id))


def _resolve_company_field_ids(
    *,
    client: Any,
    fields: tuple[str, ...],
    field_types: list[str],
    cache: SessionCache | None = None,
) -> tuple[list[str], dict[str, Any]]:
    meta = get_company_fields(client=client, cache=cache)
    field_by_id: dict[str, Any] = {str(f.id): f for f in meta}
    by_name: dict[str, list[str]] = {}
    for f in meta:
        by_name.setdefault(str(f.name).lower(), []).append(str(f.id))

    resolved_fields: list[str] = []
    for raw in fields:
        text = _strip_wrapping_quotes(str(raw)).strip()
        if not text:
            continue
        if text in field_by_id:
            resolved_fields.append(text)
            continue
        name_matches = by_name.get(text.lower(), [])
        if len(name_matches) == 1:
            resolved_fields.append(name_matches[0])
            continue
        if len(name_matches) > 1:
            raise CLIError(
                f'Ambiguous field name "{text}" ({len(name_matches)} matches)',
                exit_code=2,
                error_type="ambiguous_resolution",
                details={
                    "name": text,
                    "matches": [
                        {
                            "fieldId": fid,
                            "name": getattr(field_by_id.get(fid), "name", None),
                            "type": getattr(field_by_id.get(fid), "type", None),
                            "valueType": getattr(field_by_id.get(fid), "value_type", None),
                        }
                        for fid in name_matches[:20]
                    ],
                },
            )

        raise CLIError(
            f'Unknown field: "{text}"',
            exit_code=2,
            error_type="usage_error",
            hint="Tip: run `xaffinity company get <id> --all-fields --json` and inspect "
            "`data.company.fields[*].id` / `data.company.fields[*].name`.",
            details={"field": text},
        )

    expanded: list[str] = []
    for field_type in field_types:
        wanted = field_type.strip()
        if not wanted:
            continue
        candidates = [f for f in meta if f.type == wanted]
        candidates.sort(
            key=lambda f: (
                str(f.name).lower(),
                str(f.id),
            )
        )
        expanded.extend([str(f.id) for f in candidates])

    ordered: list[str] = []
    seen: set[str] = set()
    for fid in [*resolved_fields, *expanded]:
        if fid in seen:
            continue
        ordered.append(fid)
        seen.add(fid)

    resolved_info = {
        "fieldIds": ordered,
        "fieldTypes": field_types,
        "explicitFields": list(fields),
    }
    return ordered, resolved_info


@company_group.group(name="files", cls=RichGroup)
def company_files_group() -> None:
    """Company files."""


@category("read")
@company_files_group.command(name="ls", cls=RichCommand)
@click.argument("company", type=str)
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
def company_files_ls(
    ctx_obj: CLIContext,
    company: str,
    *,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """
    List files attached to a company.

    COMPANY can be an ID, URL, name:NAME, or domain:DOMAIN.

    Examples:

    - `xaffinity company files ls 12345`
    - `xaffinity company files ls "name:Acme Corp"`
    - `xaffinity company files ls "domain:acme.com" --max-results 10`
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
        cache = ctx.session_cache

        # Resolve company selector to ID
        company_id, resolved = _resolve_company_selector(
            client=client, selector=company, cache=cache
        )

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
            name="company files ls",
            inputs={"selector": company},
            modifiers=modifiers,
        )

        # When truncated by --max-results, remove allPages from context (misleading)
        cmd_context_truncated = CommandContext(
            name="company files ls",
            inputs={"selector": company},
            modifiers={k: v for k, v in modifiers.items() if k != "allPages"},
        )

        results: list[dict[str, object]] = []
        first_page = True
        page_token: str | None = cursor

        while True:
            page = client.files.list(
                company_id=company_id,
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

    run_command(ctx_obj, command="company files ls", fn=fn)


@category("read")
@company_files_group.command(name="read", cls=RichCommand)
@click.argument("company_id", type=int)
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
def company_files_read(
    ctx: CLIContext,
    company_id: int,
    *,
    file_id: int,
    offset: int,
    limit: str,
) -> None:
    """Read file content with chunking support.

    Returns base64-encoded content. For large files, use --offset and --limit
    to fetch in chunks. The response includes 'nextOffset' for easy iteration.

    Examples:

    - `xaffinity company files read 123 --file-id 456`
    - `xaffinity company files read 123 --file-id 456 --offset 1048576`
    - `xaffinity company files read 123 --file-id 456 --limit 500KB`
    """
    limit_bytes = parse_size(limit)
    read_file_content(
        ctx=ctx,
        entity_type="company",
        entity_id=company_id,
        file_id=file_id,
        offset=offset,
        limit=limit_bytes,
    )


@category("read")
@company_files_group.command(name="download", cls=RichCommand)
@click.argument("company_id", type=int)
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
def company_files_download(
    ctx: CLIContext,
    company_id: int,
    *,
    file_id: int | None,
    out_path: str | None,
    overwrite: bool,
    concurrency: int,
    page_size: int,
    max_files: int | None,
) -> None:
    """Download files attached to a company.

    Single file mode (with --file-id):
        company files download 123 --file-id 456 --out ./pitch.pdf

    Bulk mode (without --file-id):
        company files download 123 --out ./backups/
    """
    if file_id is not None:
        # Single file mode
        download_single_file(
            ctx=ctx,
            entity_type="company",
            entity_id=company_id,
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
                name="company files download",
                inputs={"companyId": company_id},
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
                    default_dirname=f"affinity-company-{company_id}-files",
                    manifest_entity={"type": "company", "companyId": company_id},
                    files_list_kwargs={"company_id": CompanyId(company_id)},
                    context=cmd_context,
                )
            )

        run_command(ctx, command="company files download", fn=fn)


@category("write")
@progress_capable
@company_files_group.command(name="upload", cls=RichCommand)
@click.argument("company_id", type=int)
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
def company_files_upload(
    ctx: CLIContext,
    company_id: int,
    *,
    file_paths: tuple[str, ...],
) -> None:
    """
    Upload files to a company.

    Examples:

    - `xaffinity company files upload 123 --file doc.pdf`
    - `xaffinity company files upload 123 --file a.pdf --file b.pdf`
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
                    company_id=CompanyId(company_id),
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
            name="company files upload",
            inputs={"companyId": company_id},
            modifiers={"files": list(file_paths)},
        )

        return CommandOutput(
            data={"uploads": results, "companyId": company_id},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="company files upload", fn=fn)


@category("read")
@company_group.command(name="get", cls=RichCommand)
@click.argument("company_selector", type=str)
@click.option(
    "-f",
    "--field",
    "fields",
    multiple=True,
    help="Field id or exact field name (repeatable).",
)
@click.option(
    "-t",
    "--field-type",
    "field_types",
    multiple=True,
    type=click.Choice(list(_COMPANY_FIELDS_ALL_TYPES)),
    help="Include all fields of this type (repeatable).",
)
@click.option(
    "--all-fields",
    is_flag=True,
    help="Include all supported (non-list-specific) field data.",
)
@click.option("--no-fields", is_flag=True, help="Do not request field data.")
@click.option(
    "--expand",
    "expand",
    multiple=True,
    type=click.Choice(["lists", "list-entries", "persons"]),
    help="Include related data (repeatable).",
)
@click.option(
    "--list",
    "list_selector",
    type=str,
    default=None,
    help=(
        "Filter list-entries expansion to a list id or exact list name "
        "(implies --expand list-entries)."
    ),
)
@click.option(
    "--list-entry-field",
    "list_entry_fields",
    multiple=True,
    help=(
        "Project a list-entry field into its own column (repeatable; implies --expand "
        "list-entries)."
    ),
)
@click.option(
    "--show-list-entry-fields",
    "show_list_entry_fields",
    is_flag=True,
    help=(
        "Render per-list-entry Fields tables in human output (implies --expand list-entries; "
        "requires --max-results <= 3)."
    ),
)
@click.option(
    "--list-entry-fields-scope",
    "list_entry_fields_scope",
    type=click.Choice(["list-only", "all"]),
    default="list-only",
    show_default=True,
    help="Control which fields appear in list entry tables (human output only).",
)
@click.option(
    "--max-results",
    "--limit",
    "-n",
    type=int,
    default=None,
    help="Maximum items to fetch per expansion section (applies to --expand).",
)
@click.option(
    "--all",
    "all_pages",
    is_flag=True,
    help="Fetch all pages for expansions (still capped by --max-results if set).",
)
@click.option(
    "--with-interaction-dates",
    "with_interaction_dates",
    is_flag=True,
    help="Include interaction date summaries (last/next meeting, email dates).",
)
@click.option(
    "--with-interaction-persons",
    "with_interaction_persons",
    is_flag=True,
    help="Include person IDs for each interaction (requires --with-interaction-dates).",
)
@output_options
@click.pass_obj
def company_get(
    ctx: CLIContext,
    company_selector: str,
    *,
    fields: tuple[str, ...],
    field_types: tuple[str, ...],
    all_fields: bool,
    no_fields: bool,
    expand: tuple[str, ...],
    list_selector: str | None,
    list_entry_fields: tuple[str, ...],
    show_list_entry_fields: bool,
    list_entry_fields_scope: ListEntryFieldsScope,
    max_results: int | None,
    all_pages: bool,
    with_interaction_dates: bool,
    with_interaction_persons: bool,
) -> None:
    """
    Get a company by id, URL, domain, or name.

    The COMPANY_SELECTOR can be:

    - Company ID (e.g., `12345`)
    - Company URL (e.g., `https://app.affinity.co/companies/12345`)
    - Domain (e.g., `domain:acme.com`)
    - Name (e.g., `name:"Acme Inc"`)

    List Entry Fields:

    Use --list-entry-field and related flags to customize which list-entry
    fields are shown in table output. These flags are ignored in JSON mode
    to ensure full-fidelity output.

    JSON Output:

    When using --json, all list-entry fields are included regardless of
    --list-entry-field flags. Use table output for selective field display.

    Examples:

    - `xaffinity company get 223384905`
    - `xaffinity company get https://mydomain.affinity.com/companies/223384905`
    - `xaffinity company get domain:acme.com`
    - `xaffinity company get name:"Acme Inc"`
    - `xaffinity company get 223384905 --expand list-entries --list "Portfolio"`
    - `xaffinity company get 223384905 --json  # Full data, ignores field filters`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        company_id, resolved = _resolve_company_selector(
            client=client, selector=company_selector, cache=cache
        )

        # Build CommandContext for company get
        ctx_modifiers: dict[str, object] = {}
        if fields:
            ctx_modifiers["fields"] = list(fields)
        if field_types:
            ctx_modifiers["fieldTypes"] = list(field_types)
        if all_fields:
            ctx_modifiers["allFields"] = True
        if no_fields:
            ctx_modifiers["noFields"] = True
        if expand:
            ctx_modifiers["expand"] = list(expand)
        if list_selector:
            ctx_modifiers["list"] = list_selector
        if list_entry_fields:
            ctx_modifiers["listEntryFields"] = list(list_entry_fields)
        if show_list_entry_fields:
            ctx_modifiers["showListEntryFields"] = True
        if list_entry_fields_scope != "list-only":
            ctx_modifiers["listEntryFieldsScope"] = list_entry_fields_scope
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["allPages"] = True

        # Extract resolved company name if available for context
        ctx_resolved: dict[str, str] | None = None
        company_resolved = resolved.get("company", {})
        if isinstance(company_resolved, dict):
            entity_name = company_resolved.get("entityName")
            if entity_name:
                ctx_resolved = {"selector": str(entity_name)}

        cmd_context = CommandContext(
            name="company get",
            inputs={"selector": company_selector},
            modifiers=ctx_modifiers,
            resolved=ctx_resolved,
        )

        expand_set = {e.strip() for e in expand if e and e.strip()}

        # Auto-imply --expand list-entries when list-entry-related flags are used.
        # This improves DX by removing a redundant flag requirement.
        if (list_selector or list_entry_fields or show_list_entry_fields) and (
            "list-entries" not in expand_set
        ):
            expand_set.add("list-entries")

        effective_list_entry_fields = tuple(list_entry_fields)
        effective_show_list_entry_fields = bool(show_list_entry_fields)
        effective_list_entry_fields_scope: ListEntryFieldsScope = list_entry_fields_scope
        if ctx.output == "json":
            # These flags are human/table presentation features; keep JSON stable and full-fidelity.
            effective_list_entry_fields = ()
            effective_show_list_entry_fields = False
            effective_list_entry_fields_scope = "all"

        scope_source = None
        click_ctx = click.get_current_context(silent=True)
        if click_ctx is not None:
            get_source = getattr(cast(Any, click_ctx), "get_parameter_source", None)
            if callable(get_source):
                scope_source = get_source("list_entry_fields_scope")
        source_enum = getattr(cast(Any, click.core), "ParameterSource", None)
        default_source = getattr(source_enum, "DEFAULT", None) if source_enum else None
        if (
            ctx.output != "json"
            and scope_source is not None
            and default_source is not None
            and scope_source != default_source
            and not show_list_entry_fields
        ):
            raise CLIError(
                "--list-entry-fields-scope requires --show-list-entry-fields.",
                exit_code=2,
                error_type="usage_error",
            )

        # Note: --list now auto-implies --expand list-entries (handled above)

        if no_fields and (fields or field_types or all_fields):
            raise CLIError(
                "--no-fields cannot be combined with --field/--field-type/--all-fields.",
                exit_code=2,
                error_type="usage_error",
            )

        # Note: --list-entry-field/--show-list-entry-fields now auto-imply --expand list-entries

        if effective_list_entry_fields and effective_show_list_entry_fields:
            raise CLIError(
                "--list-entry-field and --show-list-entry-fields are mutually exclusive.",
                exit_code=2,
                error_type="usage_error",
            )

        if effective_show_list_entry_fields:
            if max_results is None:
                raise CLIError(
                    "--show-list-entry-fields requires --max-results N (N <= 3).",
                    exit_code=2,
                    error_type="usage_error",
                    hint=(
                        "Add --max-results 3 to limit output, or use --json / --list-entry-field "
                        "for large outputs."
                    ),
                )
            if max_results <= 0:
                raise CLIError(
                    "--max-results must be >= 1 when used with --show-list-entry-fields.",
                    exit_code=2,
                    error_type="usage_error",
                )
            if max_results > 3:
                raise CLIError(
                    f"--show-list-entry-fields is limited to --max-results 3 (got {max_results}).",
                    exit_code=2,
                    error_type="usage_error",
                    hint=(
                        "Options: set --max-results 3, use --json for full structured data, or "
                        "use --list-entry-field <field> to project specific fields."
                    ),
                )

        if effective_list_entry_fields and not list_selector:
            for spec in effective_list_entry_fields:
                if any(ch.isspace() for ch in spec):
                    raise CLIError(
                        (
                            "Field names are only allowed with --list because names aren't "
                            "unique across lists."
                        ),
                        exit_code=2,
                        error_type="usage_error",
                        hint=(
                            "Tip: run `xaffinity list get <list>` to discover list-entry field IDs."
                        ),
                        details={"field": spec},
                    )

        requested_types: list[str] = []
        if all_fields:
            requested_types.extend(list(_COMPANY_FIELDS_ALL_TYPES))
        requested_types.extend([t for t in field_types if t])

        seen_types: set[str] = set()
        deduped_types: list[str] = []
        for t in requested_types:
            if t in seen_types:
                continue
            deduped_types.append(t)
            seen_types.add(t)
        requested_types = deduped_types

        selected_field_ids: list[str] = []
        selection_resolved: dict[str, Any] = {}
        if not no_fields and (fields or requested_types):
            if fields:
                selected_field_ids, selection_resolved = _resolve_company_field_ids(
                    client=client,
                    fields=fields,
                    field_types=requested_types,
                    cache=cache,
                )
            else:
                selection_resolved = {"fieldTypes": requested_types}

        field_ids: list[FieldId | EnrichedFieldId] | None = None
        if selected_field_ids:
            coerced_field_ids: list[FieldId | EnrichedFieldId] = []
            for field_id in selected_field_ids:
                text = field_id.strip()
                if not text:
                    continue
                try:
                    coerced_field_ids.append(FieldId(text))
                except Exception:
                    coerced_field_ids.append(EnrichedFieldId(text))
            field_ids = coerced_field_ids or None
        field_types_param = (
            [FieldType(t) for t in requested_types] if not fields and requested_types else None
        )
        company = client.companies.get(
            company_id,
            field_ids=field_ids,
            field_types=field_types_param,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
        )
        company_payload = serialize_model_for_cli(company)
        fields_raw = getattr(company, "fields_raw", None)
        if isinstance(fields_raw, list):
            company_payload["fields"] = fields_raw

        data: dict[str, Any] = {"company": company_payload}
        pagination: dict[str, Any] = {}

        # Show spinner for expansion operations
        show_expand_progress = (
            expand_set
            and ctx.progress != "never"
            and not ctx.quiet
            and (ctx.progress == "always" or sys.stderr.isatty())
        )

        # Variables needed for list-entries expansion
        list_id: ListId | None = None
        entries_items: list[Any] = []

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

            if "lists" in expand_set:

                def fetch_lists_page(limit: int | None, cursor: str | None) -> Any:
                    return client.companies.get_lists(
                        company_id,
                        limit=limit,
                        cursor=cursor,
                    )

                data["lists"] = _fetch_v2_collection_sdk(
                    fetch_page=fetch_lists_page,
                    section="lists",
                    default_limit=100,
                    default_cap=100,
                    allow_unbounded=True,
                    max_results=max_results,
                    all_pages=all_pages,
                    warnings=warnings,
                    pagination=pagination,
                )

            if "list-entries" in expand_set:
                if list_selector:
                    raw_list_selector = list_selector.strip()
                    if raw_list_selector.isdigit():
                        list_id = ListId(int(raw_list_selector))
                        resolved.update({"list": {"input": list_selector, "listId": int(list_id)}})
                    else:
                        resolved_list_obj = resolve_list_selector(
                            client=client, selector=list_selector, cache=cache
                        )
                        list_id = ListId(int(resolved_list_obj.list.id))
                        resolved.update(resolved_list_obj.resolved)

                def keep_entry(item: Any) -> bool:
                    if list_id is None:
                        return True
                    return isinstance(item, dict) and item.get("listId") == int(list_id)

                def fetch_list_entries_page(limit: int | None, cursor: str | None) -> Any:
                    return client.companies.get_list_entries(
                        company_id,
                        limit=limit,
                        cursor=cursor,
                    )

                entries_items = _fetch_v2_collection_sdk(
                    fetch_page=fetch_list_entries_page,
                    section="listEntries",
                    default_limit=100,
                    default_cap=None,
                    allow_unbounded=False,
                    max_results=max_results,
                    all_pages=all_pages,
                    warnings=warnings,
                    pagination=pagination,
                    keep_item=keep_entry if list_id is not None else None,
                )
                data["listEntries"] = entries_items

            if "persons" in expand_set:
                persons_cap = max_results
                if persons_cap is None and not all_pages:
                    persons_cap = 100
                if persons_cap is not None and persons_cap <= 0:
                    data["persons"] = []
                else:
                    person_ids = client.companies.get_associated_person_ids(company_id)
                    total_persons = len(person_ids)
                    if persons_cap is not None and total_persons > persons_cap:
                        warnings.append(
                            f"Persons truncated at {persons_cap:,} items; re-run with --all "
                            "or a higher --max-results to fetch more."
                        )

                    persons = client.companies.get_associated_people(
                        company_id,
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

        if "list-entries" in expand_set and entries_items and ctx.output != "json":
            list_name_by_id: dict[int, str] = {}
            if isinstance(data.get("lists"), list):
                for item in data.get("lists", []):
                    if not isinstance(item, dict):
                        continue
                    lid = item.get("id")
                    name = item.get("name")
                    if isinstance(lid, int) and isinstance(name, str) and name.strip():
                        list_name_by_id[lid] = name.strip()
            if effective_show_list_entry_fields:
                needed_list_ids: set[int] = set()
                for entry in entries_items:
                    if not isinstance(entry, dict):
                        continue
                    lid = entry.get("listId")
                    if isinstance(lid, int) and lid not in list_name_by_id:
                        needed_list_ids.add(lid)
                for lid in sorted(needed_list_ids):
                    try:
                        list_obj = client.lists.get(ListId(lid))
                    except Exception:
                        continue
                    if getattr(list_obj, "name", None):
                        list_name_by_id[lid] = str(list_obj.name)

            resolved_list_entry_fields: list[tuple[str, str]] = []
            if effective_list_entry_fields:
                if list_id is not None:
                    fields_meta = client.lists.get_fields(list_id)
                    by_id: dict[str, str] = {}
                    by_name: dict[str, list[str]] = {}
                    for f in fields_meta:
                        fid = str(getattr(f, "id", "")).strip()
                        name = str(getattr(f, "name", "")).strip()
                        if fid:
                            by_id[fid] = name or fid
                        if name:
                            by_name.setdefault(name.lower(), []).append(fid or name)

                    for spec in effective_list_entry_fields:
                        raw = spec.strip()
                        if not raw:
                            continue
                        if raw in by_id:
                            resolved_list_entry_fields.append((raw, by_id[raw]))
                            continue
                        matches = by_name.get(raw.lower(), [])
                        if len(matches) == 1:
                            fid = matches[0]
                            resolved_list_entry_fields.append((fid, by_id.get(fid, raw)))
                            continue
                        if len(matches) > 1:
                            raise CLIError(
                                (
                                    f'Ambiguous list-entry field name "{raw}" '
                                    f"({len(matches)} matches)"
                                ),
                                exit_code=2,
                                error_type="ambiguous_resolution",
                                details={"name": raw, "matches": matches[:20]},
                            )
                        raise CLIError(
                            f'Unknown list-entry field: "{raw}"',
                            exit_code=2,
                            error_type="usage_error",
                            hint=(
                                "Tip: run `xaffinity list get <list>` and inspect "
                                "`data.fields[*].id` / `data.fields[*].name`."
                            ),
                            details={"field": raw},
                        )
                else:
                    for spec in effective_list_entry_fields:
                        raw = spec.strip()
                        if raw:
                            resolved_list_entry_fields.append((raw, raw))

            def unique_label(label: str, *, used: set[str], fallback: str) -> str:
                base = (label or "").strip() or fallback
                if base not in used:
                    used.add(base)
                    return base
                idx = 2
                while f"{base} ({idx})" in used:
                    idx += 1
                final = f"{base} ({idx})"
                used.add(final)
                return final

            used_labels: set[str] = {
                "list",
                "listId",
                "listEntryId",
                "createdAt",
                "fieldsCount",
            }
            projected: list[tuple[str, str]] = []
            for fid, label in resolved_list_entry_fields:
                projected.append((fid, unique_label(label, used=used_labels, fallback=fid)))

            summary_rows: list[dict[str, Any]] = []
            for entry in entries_items:
                if not isinstance(entry, dict):
                    continue
                list_id_value = entry.get("listId")
                list_name = (
                    list_name_by_id.get(list_id_value) if isinstance(list_id_value, int) else None
                )
                list_label = list_name or (str(list_id_value) if list_id_value is not None else "")
                fields_payload = entry.get("fields", [])
                fields_list = fields_payload if isinstance(fields_payload, list) else []
                row: dict[str, Any] = {}
                row["list"] = list_label
                row["listId"] = list_id_value if isinstance(list_id_value, int) else None
                row["listEntryId"] = entry.get("id")
                row["createdAt"] = entry.get("createdAt")
                fields_count = len(fields_list)
                if effective_show_list_entry_fields:
                    _filtered, list_only_count, total_count = filter_list_entry_fields(
                        fields_list,
                        scope=effective_list_entry_fields_scope,
                    )
                    if effective_list_entry_fields_scope == "list-only":
                        fields_count = list_only_count
                    else:
                        fields_count = total_count
                row["fieldsCount"] = fields_count

                field_by_id: dict[str, dict[str, Any]] = {}
                for f in fields_list:
                    if not isinstance(f, dict):
                        continue
                    fld_id = f.get("id")
                    if isinstance(fld_id, str) and fld_id:
                        field_by_id[fld_id] = f

                for fid, label in projected:
                    field_obj = field_by_id.get(fid)
                    value_obj = field_obj.get("value") if isinstance(field_obj, dict) else None
                    row[label] = value_obj

                summary_rows.append(row)

            data["listEntries"] = summary_rows

            if effective_show_list_entry_fields:
                for entry in entries_items:
                    if not isinstance(entry, dict):
                        continue
                    list_entry_id = entry.get("id")
                    list_id_value = entry.get("listId")
                    list_name = (
                        list_name_by_id.get(list_id_value)
                        if isinstance(list_id_value, int)
                        else None
                    )
                    if list_name:
                        list_hint = (
                            f"{list_name} (listId={list_id_value})"
                            if list_id_value is not None
                            else str(list_name)
                        )
                    else:
                        list_hint = (
                            f"listId={list_id_value}"
                            if list_id_value is not None
                            else "listId=unknown"
                        )
                    title = f"List Entry {list_entry_id} ({list_hint}) Fields"

                    fields_payload = entry.get("fields", [])
                    fields_list = fields_payload if isinstance(fields_payload, list) else []
                    filtered_fields, list_only_count, total_count = filter_list_entry_fields(
                        fields_list,
                        scope=effective_list_entry_fields_scope,
                    )
                    if total_count == 0:
                        data[title] = {"_text": "(no fields)"}
                        continue
                    if effective_list_entry_fields_scope == "list-only" and list_only_count == 0:
                        data[title] = {
                            "_text": (
                                f"(no list-specific fields; {total_count} non-list fields "
                                "available with --list-entry-fields-scope all)"
                            )
                        }
                        continue

                    field_rows = build_list_entry_field_rows(filtered_fields)
                    if (
                        effective_list_entry_fields_scope == "list-only"
                        and list_only_count < total_count
                    ):
                        data[title] = {
                            "_rows": field_rows,
                            "_hint": (
                                "Some non-list fields hidden — use "
                                "--list-entry-fields-scope all to include them"
                            ),
                        }
                    else:
                        data[title] = field_rows

        if selection_resolved:
            resolved["fieldSelection"] = selection_resolved
        if expand_set:
            resolved["expand"] = sorted(expand_set)

        # Fetch field metadata if fields were requested and present in response
        company_fields = (
            company_payload.get("fields") if isinstance(company_payload, dict) else None
        )
        if isinstance(company_fields, list) and company_fields:
            try:
                from ..field_utils import build_field_id_to_name_map

                field_metadata = client.companies.get_fields()
                resolved["fieldMetadata"] = build_field_id_to_name_map(field_metadata)
            except Exception:
                # Field metadata is optional - continue without names if fetch fails
                pass

        return CommandOutput(
            data=data,
            context=cmd_context,
            pagination=pagination or None,
            resolved=resolved,
            api_called=True,
        )

    run_command(ctx, command="company get", fn=fn)


@category("write")
@company_group.command(name="create", cls=RichCommand)
@click.option("--name", required=True, help="Company name.")
@click.option("--domain", default=None, help="Primary domain.")
@click.option(
    "--person-id",
    "person_ids",
    multiple=True,
    type=int,
    help="Associated person id (repeatable).",
)
@click.option(
    "--allow-duplicate",
    is_flag=True,
    default=False,
    help=(
        "Force create even when an existing company matches by name or domain. "
        "By default (if_not_exists=True) duplicates are refused with exit code 6. "
        "Not recommended; prefer using the existing companyId."
    ),
)
@output_options
@click.pass_obj
def company_create(
    ctx: CLIContext,
    *,
    name: str,
    domain: str | None,
    person_ids: tuple[int, ...],
    allow_duplicate: bool,
) -> None:
    """Create a company."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        validated_domain = validate_domain(domain, label="domain")

        client = ctx.get_client(warnings=warnings)
        try:
            created = client.companies.create(
                CompanyCreate(
                    name=name,
                    domain=validated_domain,
                    person_ids=[PersonId(pid) for pid in person_ids],
                ),
                if_not_exists=not allow_duplicate,
            )
        except DuplicateEntityError as e:
            details = {
                "existing": {
                    "companyId": e.existing_id,
                    "name": e.existing_name,
                    "domain": e.existing_domain,
                    "isGlobal": e.existing_is_global,
                }
            }
            if e.existing_is_global:
                message = (
                    f"A global Affinity directory record matches (id={e.existing_id}); "
                    "global records cannot be duplicated as tenant-scoped companies."
                )
                hint = (
                    f"Use the existing global companyId ({e.existing_id}) directly - "
                    f"e.g., `xaffinity list entry add --list LIST --company-id {e.existing_id}` "
                    "to add it to a list. --allow-duplicate is available but not recommended "
                    "for global directory matches."
                )
            else:
                identifier_parts = [f"name={name!r}"]
                if validated_domain:
                    identifier_parts.append(f"domain={validated_domain!r}")
                message = (
                    f"Company with {' or '.join(identifier_parts)} already exists "
                    f"(id={e.existing_id})."
                )
                hint = (
                    "Run with --allow-duplicate to force create, "
                    f"or use the existing companyId ({e.existing_id})."
                )
            raise CLIError(
                message,
                exit_code=6,
                error_type="duplicate_exists",
                details=details,
                hint=hint,
                cause=e,
            ) from e
        payload = serialize_model_for_cli(created)

        ctx_modifiers: dict[str, object] = {"name": name}
        if domain:
            ctx_modifiers["domain"] = domain
        if person_ids:
            ctx_modifiers["personIds"] = list(person_ids)

        cmd_context = CommandContext(
            name="company create",
            inputs={},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"company": payload},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="company create", fn=fn)


@category("write")
@company_group.command(name="update", cls=RichCommand)
@click.argument("company_id", type=int)
@click.option("--name", default=None, help="Updated company name.")
@click.option("--domain", default=None, help="Updated primary domain.")
@click.option(
    "--person-id",
    "person_ids",
    multiple=True,
    type=int,
    help="Replace associated person ids (repeatable).",
)
@output_options
@click.pass_obj
def company_update(
    ctx: CLIContext,
    company_id: int,
    *,
    name: str | None,
    domain: str | None,
    person_ids: tuple[int, ...],
) -> None:
    """Update a company."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if not (name or domain or person_ids):
            raise CLIError(
                "Provide at least one field to update.",
                exit_code=2,
                error_type="usage_error",
                hint="Use --name, --domain, or --person-id.",
            )
        validated_domain = validate_domain(domain, label="domain")

        client = ctx.get_client(warnings=warnings)
        updated = client.companies.update(
            CompanyId(company_id),
            CompanyUpdate(
                name=name,
                domain=validated_domain,
                person_ids=[PersonId(pid) for pid in person_ids] if person_ids else None,
            ),
        )
        payload = serialize_model_for_cli(updated)

        ctx_modifiers: dict[str, object] = {}
        if name:
            ctx_modifiers["name"] = name
        if domain:
            ctx_modifiers["domain"] = domain
        if person_ids:
            ctx_modifiers["personIds"] = list(person_ids)

        cmd_context = CommandContext(
            name="company update",
            inputs={"companyId": company_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"company": payload},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="company update", fn=fn)


@category("write")
@destructive
@company_group.command(name="delete", cls=RichCommand)
@click.argument("company_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def company_delete(ctx: CLIContext, company_id: int, yes: bool) -> None:
    """Delete a company."""
    if not yes:
        click.confirm(f"Delete company {company_id}?", abort=True)

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        success = client.companies.delete(CompanyId(company_id))

        cmd_context = CommandContext(
            name="company delete",
            inputs={"companyId": company_id},
            modifiers={},
        )

        return CommandOutput(
            data={"success": success},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="company delete", fn=fn)


@category("write")
@company_group.command(name="merge", cls=RichCommand)
@click.argument("primary_id", type=int)
@click.argument("duplicate_id", type=int)
@output_options
@click.pass_obj
def company_merge(
    ctx: CLIContext,
    primary_id: int,
    duplicate_id: int,
) -> None:
    """Merge a duplicate company into a primary (beta).

    Returns a taskUrl for tracking progress. Use 'task wait <url>' to wait for completion.
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        task_url = client.companies.merge(
            CompanyId(primary_id),
            CompanyId(duplicate_id),
        )

        cmd_context = CommandContext(
            name="company merge",
            inputs={"primaryId": primary_id, "duplicateId": duplicate_id},
            modifiers={},
        )

        return CommandOutput(
            data={
                "survivingId": primary_id,
                "mergedId": duplicate_id,
                "affinityUrl": f"https://app.affinity.co/companies/{primary_id}",
                "taskUrl": task_url,
            },
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="company merge", fn=fn)


@category("write")
@company_group.command(name="field", cls=RichCommand)
@click.argument("company_id", type=int)
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
def company_field(
    ctx: CLIContext,
    company_id: int,
    *,
    set_values: tuple[tuple[str, str], ...],
    unset_fields: tuple[str, ...],
    json_input: str | None,
    get_fields: tuple[str, ...],
) -> None:
    """
    Manage company field values.

    Unified command for getting, setting, and unsetting field values.
    For field names with spaces, use quotes.

    Examples:

    - `xaffinity company field 123 --set Industry "Technology"`
    - `xaffinity company field 123 --set Industry "Tech" --set Size "Large"`
    - `xaffinity company field 123 --unset Industry`
    - `xaffinity company field 123 --set-json '{"Industry": "Tech", "Size": "Large"}'`
    - `xaffinity company field 123 --get Industry --get Size`
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
        field_metadata = fetch_field_metadata(client=client, entity_type="company")
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
            existing_values = client.field_values.list(company_id=CompanyId(company_id))
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
                name="company field",
                inputs={"companyId": company_id},
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

        # Phase 2: hoist all field-name resolution upfront (was inside the loop).
        # Failures here abort cleanly with no API side effects.
        resolved_set_ops: list[tuple[str, Any]] = []
        for field_name, value in raw_set_operations:
            target_field_id = resolver.resolve_field_name_or_id(field_name, context="field")
            resolved_set_ops.append((target_field_id, value))

        # Phase 3: pre-validate ALL values up front. New behavior addition for
        # V1 commands: entity-reference fields gain client-side validation, so
        # e.g. ``--set Owner "<full name>"`` aborts before any write instead
        # of partial-committing prior --sets and then failing server-side.
        pre_resolved_set = pre_validate_set_operations(resolver, resolved_set_ops)

        # Phase 4: fetch existing values, execute set phase with no-op short-circuit.
        existing_values = client.field_values.list(company_id=CompanyId(company_id))
        existing_values_serialized = [serialize_model_for_cli(v) for v in existing_values]

        created_values, set_deleted_count = execute_v1_set_phase(
            client=client,
            entity_kind="company",
            entity_id=company_id,
            pre_resolved_ops=pre_resolved_set,
            existing_values_serialized=existing_values_serialized,
            resolver=resolver,
        )

        # Handle --unset: remove field values. Hoist name resolution + V1 mapping
        # upfront here too so a typo in any --unset aborts before deletes happen.
        deleted_count = set_deleted_count
        unset_numeric_ids: list[int] = []
        for field_name in unset_fields:
            target_field_id = resolver.resolve_field_name_or_id(field_name, context="field")
            numeric_field_id = resolver.to_v1_numeric(
                client, target_field_id, entity_type="company"
            )
            unset_numeric_ids.append(numeric_field_id)
        # Refresh existing values once after set phase (sets may have changed them).
        if unset_numeric_ids:
            existing_values = client.field_values.list(company_id=CompanyId(company_id))
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
            name="company field",
            inputs={"companyId": company_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data=results,
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="company field", fn=fn)
