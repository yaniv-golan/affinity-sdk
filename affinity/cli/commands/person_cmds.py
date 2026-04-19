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

from affinity.models.entities import Person, PersonCreate, PersonUpdate
from affinity.models.types import FieldId
from affinity.types import CompanyId, FieldType, ListId, PersonId

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..csv_utils import write_csv_to_stdout
from ..decorators import category, destructive, progress_capable
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import csv_output_options, csv_suboption_callback, output_options
from ..progress import ProgressManager, ProgressSettings
from ..resolve import get_person_fields, resolve_list_selector
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
        path: The API path to fetch (e.g., "/persons/123/lists").
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


@click.group(name="person", cls=RichGroup)
def person_group() -> None:
    """Person commands."""


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
@person_group.command(name="ls", cls=RichCommand)
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
    help="Filter: 'field op value'. Ops: = != =~ =^ =$ > < >= <=. E.g., 'Email =~ \"@acme\"'.",
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
def person_ls(
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
    List persons.

    Supports field selection, field types, and filter expressions.
    Use --query for free-text search.

    Examples:

    - `xaffinity person ls`
    - `xaffinity person ls --page-size 50`
    - `xaffinity person ls --field-type enriched --all`
    - `xaffinity person ls --filter 'Email =~ "@acme.com"'`
    - `xaffinity person ls --query "alice@example.com" --all`
    - `xaffinity person ls --all --csv > people.csv`
    - `xaffinity person ls --all --output csv --csv-bom > people.csv`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
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

        cmd_context = CommandContext(
            name="person ls",
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
                            "persons": {
                                "nextCursor": next_cursor,
                                "prevCursor": prev_cursor,
                            }
                        }
                    return CommandOutput(
                        data={"persons": rows[:max_results]},
                        context=cmd_context,
                        pagination=pagination,
                        api_called=True,
                    )
                return None

            # Three paths: V2-only, V1-only, or Hybrid (V1 search + V2 batch fetch)
            if use_v1_search and wants_fields:
                # Hybrid: V1 search for IDs, then V2 batch fetch with field data
                assert query is not None
                for v1_page in client.persons.search_pages(
                    query,
                    page_size=page_size,
                    page_token=cursor,
                ):
                    next_cursor = v1_page.next_cursor
                    prev_cursor = None  # V1 doesn't have prev cursor

                    if v1_page.data:
                        # Batch fetch from V2 with field data
                        person_ids = [PersonId(p.id) for p in v1_page.data]
                        v2_response = client.persons.list(
                            ids=person_ids,
                            field_ids=parsed_field_ids,
                            field_types=parsed_field_types,
                        )
                        for idx, person in enumerate(v2_response.data):
                            rows.append(_person_ls_row(person))
                            if progress and task_id is not None:
                                progress.update(task_id, completed=len(rows))
                            result = _check_max_results(
                                rows, idx, len(v2_response.data), next_cursor, prev_cursor
                            )
                            if result is not None:
                                return result

                    if first_page and not all_pages and max_results is None:
                        return CommandOutput(
                            data={"persons": rows},
                            context=cmd_context,
                            pagination=(
                                {"persons": {"nextCursor": next_cursor, "prevCursor": None}}
                                if next_cursor
                                else None
                            ),
                            api_called=True,
                        )
                    first_page = False

            elif use_v1_search:
                # Search without field data
                assert query is not None
                for search_page in client.persons.search_pages(
                    query,
                    page_size=page_size,
                    page_token=cursor,
                ):
                    next_cursor = search_page.next_cursor
                    prev_cursor = None  # Search doesn't have prev cursor

                    for idx, person in enumerate(search_page.data):
                        rows.append(_person_ls_row(person))
                        if progress and task_id is not None:
                            progress.update(task_id, completed=len(rows))
                        result = _check_max_results(
                            rows, idx, len(search_page.data), next_cursor, prev_cursor
                        )
                        if result is not None:
                            return result

                    if first_page and not all_pages and max_results is None:
                        return CommandOutput(
                            data={"persons": rows},
                            context=cmd_context,
                            pagination=(
                                {"persons": {"nextCursor": next_cursor, "prevCursor": None}}
                                if next_cursor
                                else None
                            ),
                            api_called=True,
                        )
                    first_page = False

            else:
                # List with optional field data
                for page in client.persons.pages(
                    field_ids=parsed_field_ids,
                    field_types=parsed_field_types,
                    filter=filter_expr,
                    limit=page_size,
                    cursor=cursor,
                ):
                    next_cursor = page.pagination.next_cursor
                    prev_cursor = page.pagination.prev_cursor

                    for idx, person in enumerate(page.data):
                        rows.append(_person_ls_row(person))
                        if progress and task_id is not None:
                            progress.update(task_id, completed=len(rows))
                        result = _check_max_results(
                            rows, idx, len(page.data), next_cursor, prev_cursor
                        )
                        if result is not None:
                            return result

                    if first_page and not all_pages and max_results is None:
                        return CommandOutput(
                            data={"persons": rows},
                            context=cmd_context,
                            pagination=(
                                {
                                    "persons": {
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
            data={"persons": rows},
            context=cmd_context,
            pagination=None,
            api_called=True,
        )

    run_command(ctx, command="person ls", fn=fn)


def _person_ls_row(person: Person) -> dict[str, object]:
    """Build a row for person ls output."""
    return {
        "id": int(person.id),
        "name": person.full_name,
        "primaryEmail": person.primary_email,
        "emails": person.emails,
    }


_PERSON_FIELDS_ALL_TYPES: tuple[str, ...] = (
    FieldType.GLOBAL.value,
    FieldType.ENRICHED.value,
    FieldType.RELATIONSHIP_INTELLIGENCE.value,
)


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _resolve_person_selector(
    *, client: Any, selector: str, cache: SessionCache | None = None
) -> tuple[PersonId, dict[str, Any]]:
    raw = selector.strip()
    if raw.isdigit():
        person_id = PersonId(int(raw))
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(person_id),
            entity_type="person",
            source="id",
        )
        return person_id, {"person": resolved.to_dict()}

    if raw.startswith(("http://", "https://")):
        url_resolved = _parse_affinity_url(raw)
        if url_resolved.type != "person" or url_resolved.person_id is None:
            raise CLIError(
                "Expected a person URL like https://<tenant>.affinity.(co|com)/persons/<id>",
                exit_code=2,
                error_type="usage_error",
                details={"input": selector, "resolvedType": url_resolved.type},
            )
        person_id = PersonId(int(url_resolved.person_id))
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(person_id),
            entity_type="person",
            source="url",
            canonical_url=f"https://app.affinity.co/persons/{int(person_id)}",
        )
        return person_id, {"person": resolved.to_dict()}

    lowered = raw.lower()
    if lowered.startswith("email:"):
        email = _strip_wrapping_quotes(raw.split(":", 1)[1])
        person_id = _resolve_person_by_email(client=client, email=email, cache=cache)
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(person_id),
            entity_type="person",
            source="email",
        )
        return person_id, {"person": resolved.to_dict()}

    if lowered.startswith("name:"):
        name = _strip_wrapping_quotes(raw.split(":", 1)[1])
        person_id = _resolve_person_by_name(client=client, name=name, cache=cache)
        resolved = ResolvedEntity(
            input=selector,
            entity_id=int(person_id),
            entity_type="person",
            source="name",
        )
        return person_id, {"person": resolved.to_dict()}

    raise CLIError(
        "Unrecognized person selector.",
        exit_code=2,
        error_type="usage_error",
        hint='Use a numeric id, an Affinity URL, or "email:<x>" / "name:<x>".',
        details={"input": selector},
    )


def _resolve_person_by_email(
    *, client: Any, email: str, cache: SessionCache | None = None
) -> PersonId:
    email = email.strip()
    if not email:
        raise CLIError("Email cannot be empty.", exit_code=2, error_type="usage_error")

    cache_key = f"person_resolve_email_{email.lower()}"

    # Check cache first
    if cache and cache.enabled:
        cached = cache.get(cache_key, Person)
        if cached is not None:
            return PersonId(int(cached.id))

    matches: list[Person] = []
    email_lower = email.lower()
    for page in client.persons.search_pages(email, page_size=500):
        for person in page.data:
            emails = []
            if person.primary_email:
                emails.append(person.primary_email)
            emails.extend(person.emails or [])
            if any(e.lower() == email_lower for e in emails if e):
                matches.append(person)
                if len(matches) >= 20:
                    break
        if len(matches) >= 20 or not page.next_cursor:
            break

    if not matches:
        raise CLIError(
            f'Person not found for email "{email}"',
            exit_code=4,
            error_type="not_found",
            hint=f'Run `xaffinity person ls --query "{email}"` to explore matches.',
            details={"email": email},
        )
    if len(matches) > 1:
        raise CLIError(
            f'Ambiguous person email "{email}" ({len(matches)} matches)',
            exit_code=2,
            error_type="ambiguous_resolution",
            details={
                "email": email,
                "matches": [
                    {
                        "personId": int(p.id),
                        "name": p.full_name,
                        "primaryEmail": p.primary_email,
                    }
                    for p in matches[:20]
                ],
            },
        )

    # Cache the result after successful resolution
    if cache and cache.enabled:
        cache.set(cache_key, matches[0])

    return PersonId(int(matches[0].id))


def _resolve_person_by_name(
    *, client: Any, name: str, cache: SessionCache | None = None
) -> PersonId:
    name = name.strip()
    if not name:
        raise CLIError("Name cannot be empty.", exit_code=2, error_type="usage_error")

    cache_key = f"person_resolve_name_{name.lower()}"

    # Check cache first
    if cache and cache.enabled:
        cached = cache.get(cache_key, Person)
        if cached is not None:
            return PersonId(int(cached.id))

    matches: list[Person] = []
    name_lower = name.lower()
    for page in client.persons.search_pages(name, page_size=500):
        for person in page.data:
            if person.full_name.lower() == name_lower:
                matches.append(person)
                if len(matches) >= 20:
                    break
        if len(matches) >= 20 or not page.next_cursor:
            break

    if not matches:
        raise CLIError(
            f'Person not found for name "{name}"',
            exit_code=4,
            error_type="not_found",
            hint=f'Run `xaffinity person ls --query "{name}"` to explore matches.',
            details={"name": name},
        )
    if len(matches) > 1:
        raise CLIError(
            f'Ambiguous person name "{name}" ({len(matches)} matches)',
            exit_code=2,
            error_type="ambiguous_resolution",
            details={
                "name": name,
                "matches": [
                    {"personId": int(p.id), "name": p.full_name, "primaryEmail": p.primary_email}
                    for p in matches[:20]
                ],
            },
        )

    # Cache the result after successful resolution
    if cache and cache.enabled:
        cache.set(cache_key, matches[0])

    return PersonId(int(matches[0].id))


def _resolve_person_field_ids(
    *,
    client: Any,
    fields: tuple[str, ...],
    field_types: list[str],
    cache: SessionCache | None = None,
) -> tuple[list[str], dict[str, Any]]:
    meta = get_person_fields(client=client, cache=cache)
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
            hint="Tip: run `xaffinity person get <id> --all-fields --json` and inspect "
            "`data.person.fields[*].id` / `data.person.fields[*].name`.",
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


@category("read")
@person_group.command(name="get", cls=RichCommand)
@click.argument("person_selector", type=str)
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
    type=click.Choice(list(_PERSON_FIELDS_ALL_TYPES)),
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
    type=click.Choice(["lists", "list-entries"]),
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
def person_get(
    ctx: CLIContext,
    person_selector: str,
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
    Get a person by id, URL, email, or name.

    The PERSON_SELECTOR can be:

    - Person ID (e.g., `12345`)
    - Person URL (e.g., `https://app.affinity.co/persons/12345`)
    - Email (e.g., `email:john@example.com`)
    - Name (e.g., `name:"John Smith"`)

    List Entry Fields:

    Use --list-entry-field and related flags to customize which list-entry
    fields are shown in table output. These flags are ignored in JSON mode
    to ensure full-fidelity output.

    JSON Output:

    When using --json, all list-entry fields are included regardless of
    --list-entry-field flags. Use table output for selective field display.

    Examples:

    - `xaffinity person get 223384905`
    - `xaffinity person get https://mydomain.affinity.com/persons/223384905`
    - `xaffinity person get email:alice@example.com`
    - `xaffinity person get name:"Alice Smith"`
    - `xaffinity person get 223384905 --expand list-entries --list "Sales Pipeline"`
    - `xaffinity person get 223384905 --json  # Full data, ignores field filters`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        person_id, resolved = _resolve_person_selector(
            client=client, selector=person_selector, cache=cache
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
            requested_types.extend(list(_PERSON_FIELDS_ALL_TYPES))
        requested_types.extend([t for t in field_types if t])

        seen_types: set[str] = set()
        deduped_types: list[str] = []
        for t in requested_types:
            if t in seen_types:
                continue
            deduped_types.append(t)
            seen_types.add(t)
        requested_types = deduped_types

        params: dict[str, Any] = {}
        selection_resolved: dict[str, Any] = {}
        if not no_fields and (fields or requested_types):
            if fields:
                selected_field_ids, selection_resolved = _resolve_person_field_ids(
                    client=client,
                    fields=fields,
                    field_types=requested_types,
                    cache=cache,
                )
                if selected_field_ids:
                    params["fieldIds"] = selected_field_ids
            else:
                params["fieldTypes"] = requested_types
                selection_resolved = {"fieldTypes": requested_types}

        # Add interaction date parameters
        if with_interaction_dates:
            params["with_interaction_dates"] = True
        if with_interaction_persons:
            params["with_interaction_persons"] = True

        # Use V1 API when interaction dates are requested, V2 otherwise
        if with_interaction_dates:
            person_payload = client._http.get(
                f"/persons/{int(person_id)}", params=params or None, v1=True
            )
        else:
            person_payload = client._http.get(f"/persons/{int(person_id)}", params=params or None)

        data: dict[str, Any] = {"person": person_payload}
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
                data["lists"] = _fetch_v2_collection(
                    client=client,
                    path=f"/persons/{int(person_id)}/lists",
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

                entries_items = _fetch_v2_collection(
                    client=client,
                    path=f"/persons/{int(person_id)}/list-entries",
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
                    field_id = f.get("id")
                    if isinstance(field_id, str) and field_id:
                        field_by_id[field_id] = f

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
        person_fields = person_payload.get("fields") if isinstance(person_payload, dict) else None
        if isinstance(person_fields, list) and person_fields:
            try:
                from ..field_utils import build_field_id_to_name_map

                field_metadata = client.persons.get_fields()
                resolved["fieldMetadata"] = build_field_id_to_name_map(field_metadata)
            except Exception:
                # Field metadata is optional - continue without names if fetch fails
                pass

        # Build CommandContext for structured output
        ctx_inputs: dict[str, Any] = {}
        ctx_modifiers: dict[str, Any] = {}
        ctx_resolved: dict[str, str] = {}

        # Determine if selector or ID was used
        raw_selector = person_selector.strip()
        if raw_selector.isdigit():
            ctx_inputs["personId"] = int(person_id)
        else:
            ctx_inputs["selector"] = raw_selector

        # Build modifiers from non-default options
        if expand_set:
            ctx_modifiers["expand"] = sorted(expand_set)
        if fields:
            ctx_modifiers["fields"] = list(fields)
        if field_types:
            ctx_modifiers["fieldTypes"] = list(field_types)
        if all_fields:
            ctx_modifiers["allFields"] = True
        if no_fields:
            ctx_modifiers["noFields"] = True
        if list_selector:
            ctx_modifiers["list"] = list_selector
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["allPages"] = True

        # Resolve person name from response
        if isinstance(person_payload, dict):
            first = person_payload.get("firstName", "")
            last = person_payload.get("lastName", "")
            name = f"{first} {last}".strip()
            if name:
                if "personId" in ctx_inputs:
                    ctx_resolved["personId"] = name
                elif "selector" in ctx_inputs:
                    ctx_resolved["selector"] = name

        context = CommandContext(
            name="person get",
            inputs=ctx_inputs,
            modifiers=ctx_modifiers,
            resolved=ctx_resolved if ctx_resolved else None,
        )

        return CommandOutput(
            data=data,
            context=context,
            pagination=pagination or None,
            resolved=resolved,
            api_called=True,
        )

    run_command(ctx, command="person get", fn=fn)


@person_group.group(name="files", cls=RichGroup)
def person_files_group() -> None:
    """Person files."""


@category("read")
@person_files_group.command(name="ls", cls=RichCommand)
@click.argument("person", type=str)
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
def person_files_ls(
    ctx_obj: CLIContext,
    person: str,
    *,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """
    List files attached to a person.

    PERSON can be an ID, URL, email:EMAIL, or name:NAME.

    Examples:

    - `xaffinity person files ls 12345`
    - `xaffinity person files ls "email:john@example.com"`
    - `xaffinity person files ls "name:John Smith" --max-results 10`
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

        # Resolve person selector to ID
        person_id, resolved = _resolve_person_selector(client=client, selector=person, cache=cache)

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
            name="person files ls",
            inputs={"selector": person},
            modifiers=modifiers,
        )

        # When truncated by --max-results, remove allPages from context (misleading)
        cmd_context_truncated = CommandContext(
            name="person files ls",
            inputs={"selector": person},
            modifiers={k: v for k, v in modifiers.items() if k != "allPages"},
        )

        results: list[dict[str, object]] = []
        first_page = True
        page_token: str | None = cursor

        while True:
            page = client.files.list(
                person_id=person_id,
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

    run_command(ctx_obj, command="person files ls", fn=fn)


@category("read")
@person_files_group.command(name="read", cls=RichCommand)
@click.argument("person_id", type=int)
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
def person_files_read(
    ctx: CLIContext,
    person_id: int,
    *,
    file_id: int,
    offset: int,
    limit: str,
) -> None:
    """Read file content with chunking support.

    Returns base64-encoded content. For large files, use --offset and --limit
    to fetch in chunks. The response includes 'nextOffset' for easy iteration.

    Examples:

    - `xaffinity person files read 123 --file-id 456`
    - `xaffinity person files read 123 --file-id 456 --offset 1048576`
    - `xaffinity person files read 123 --file-id 456 --limit 500KB`
    """
    limit_bytes = parse_size(limit)
    read_file_content(
        ctx=ctx,
        entity_type="person",
        entity_id=person_id,
        file_id=file_id,
        offset=offset,
        limit=limit_bytes,
    )


@category("read")
@person_files_group.command(name="download", cls=RichCommand)
@click.argument("person_id", type=int)
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
def person_files_download(
    ctx: CLIContext,
    person_id: int,
    *,
    file_id: int | None,
    out_path: str | None,
    overwrite: bool,
    concurrency: int,
    page_size: int,
    max_files: int | None,
) -> None:
    """Download files attached to a person.

    Single file mode (with --file-id):
        person files download 123 --file-id 456 --out ./resume.pdf

    Bulk mode (without --file-id):
        person files download 123 --out ./backups/
    """
    if file_id is not None:
        # Single file mode
        download_single_file(
            ctx=ctx,
            entity_type="person",
            entity_id=person_id,
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
                name="person files download",
                inputs={"personId": person_id},
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
                    default_dirname=f"affinity-person-{person_id}-files",
                    manifest_entity={"type": "person", "personId": person_id},
                    files_list_kwargs={"person_id": PersonId(person_id)},
                    context=cmd_context,
                )
            )

        run_command(ctx, command="person files download", fn=fn)


@category("write")
@progress_capable
@person_files_group.command(name="upload", cls=RichCommand)
@click.argument("person_id", type=int)
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
def person_files_upload(
    ctx: CLIContext,
    person_id: int,
    *,
    file_paths: tuple[str, ...],
) -> None:
    """
    Upload files to a person.

    Examples:

    - `xaffinity person files upload 123 --file doc.pdf`
    - `xaffinity person files upload 123 --file a.pdf --file b.pdf`
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
                    person_id=PersonId(person_id),
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
            name="person files upload",
            inputs={"personId": person_id},
            modifiers={"files": list(file_paths)},
        )

        return CommandOutput(
            data={"uploads": results, "personId": person_id},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="person files upload", fn=fn)


@category("write")
@person_group.command(name="create", cls=RichCommand)
@click.option("--first-name", required=True, help="Person's first name (required).")
@click.option("--last-name", required=True, help="Person's last name (required).")
@click.option(
    "--email",
    "emails",
    multiple=True,
    help="Email address (repeatable).",
)
@click.option(
    "--company-id",
    "company_ids",
    multiple=True,
    type=int,
    help="Associated company id (repeatable).",
)
@output_options
@click.pass_obj
def person_create(
    ctx: CLIContext,
    *,
    first_name: str,
    last_name: str,
    emails: tuple[str, ...],
    company_ids: tuple[int, ...],
) -> None:
    """
    Create a person.

    Both --first-name and --last-name are required by the Affinity API.

    Examples:

    - `xaffinity person create --first-name "Alice" --last-name "Smith"`
    - `xaffinity person create --first-name "Bob" --last-name "Jones" --email bob@example.com`
    - `xaffinity person create --first-name "Carol" --last-name "Lee" --email a@x.com`
    - `xaffinity person create --first-name "Dan" --last-name "Park" --company-id 12345`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        created = client.persons.create(
            PersonCreate(
                first_name=first_name,
                last_name=last_name,
                emails=list(emails),
                company_ids=[CompanyId(cid) for cid in company_ids],
            )
        )
        payload = serialize_model_for_cli(created)

        ctx_modifiers: dict[str, object] = {
            "firstName": first_name,
            "lastName": last_name,
        }
        if emails:
            ctx_modifiers["emails"] = list(emails)
        if company_ids:
            ctx_modifiers["companyIds"] = list(company_ids)

        cmd_context = CommandContext(
            name="person create",
            inputs={},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"person": payload},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="person create", fn=fn)


@category("write")
@person_group.command(name="update", cls=RichCommand)
@click.argument("person_id", type=int)
@click.option("--first-name", default=None, help="Updated first name.")
@click.option("--last-name", default=None, help="Updated last name.")
@click.option(
    "--email",
    "emails",
    multiple=True,
    help="Replace emails (repeatable).",
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
def person_update(
    ctx: CLIContext,
    person_id: int,
    *,
    first_name: str | None,
    last_name: str | None,
    emails: tuple[str, ...],
    company_ids: tuple[int, ...],
) -> None:
    """Update a person."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if not (first_name or last_name or emails or company_ids):
            raise CLIError(
                "Provide at least one field to update.",
                exit_code=2,
                error_type="usage_error",
                hint="Use --first-name, --last-name, --email, or --company-id.",
            )
        client = ctx.get_client(warnings=warnings)
        updated = client.persons.update(
            PersonId(person_id),
            PersonUpdate(
                first_name=first_name,
                last_name=last_name,
                emails=list(emails) if emails else None,
                company_ids=[CompanyId(cid) for cid in company_ids] if company_ids else None,
            ),
        )
        payload = serialize_model_for_cli(updated)

        ctx_modifiers: dict[str, object] = {}
        if first_name:
            ctx_modifiers["firstName"] = first_name
        if last_name:
            ctx_modifiers["lastName"] = last_name
        if emails:
            ctx_modifiers["emails"] = list(emails)
        if company_ids:
            ctx_modifiers["companyIds"] = list(company_ids)

        cmd_context = CommandContext(
            name="person update",
            inputs={"personId": person_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"person": payload},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="person update", fn=fn)


@category("write")
@destructive
@person_group.command(name="delete", cls=RichCommand)
@click.argument("person_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def person_delete(ctx: CLIContext, person_id: int, yes: bool) -> None:
    """Delete a person."""
    if not yes:
        click.confirm(f"Delete person {person_id}?", abort=True)

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        success = client.persons.delete(PersonId(person_id))

        cmd_context = CommandContext(
            name="person delete",
            inputs={"personId": person_id},
            modifiers={},
        )

        return CommandOutput(
            data={"success": success},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="person delete", fn=fn)


@category("write")
@person_group.command(name="merge", cls=RichCommand)
@click.argument("primary_id", type=int)
@click.argument("duplicate_id", type=int)
@output_options
@click.pass_obj
def person_merge(
    ctx: CLIContext,
    primary_id: int,
    duplicate_id: int,
) -> None:
    """Merge a duplicate person into a primary (beta).

    Returns a taskUrl for tracking progress. Use 'task wait <url>' to wait for completion.
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        task_url = client.persons.merge(PersonId(primary_id), PersonId(duplicate_id))

        cmd_context = CommandContext(
            name="person merge",
            inputs={"primaryId": primary_id, "duplicateId": duplicate_id},
            modifiers={},
        )

        return CommandOutput(
            data={
                "survivingId": primary_id,
                "mergedId": duplicate_id,
                "affinityUrl": f"https://app.affinity.co/persons/{primary_id}",
                "taskUrl": task_url,
            },
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="person merge", fn=fn)


@category("write")
@person_group.command(name="field", cls=RichCommand)
@click.argument("person_id", type=int)
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
def person_field(
    ctx: CLIContext,
    person_id: int,
    *,
    set_values: tuple[tuple[str, str], ...],
    unset_fields: tuple[str, ...],
    json_input: str | None,
    get_fields: tuple[str, ...],
) -> None:
    """
    Manage person field values.

    Unified command for getting, setting, and unsetting field values.
    For field names with spaces, use quotes.

    Examples:

    - `xaffinity person field 123 --set Phone "+1-555-0123"`
    - `xaffinity person field 123 --set Phone "+1..." --set Title "CEO"`
    - `xaffinity person field 123 --unset Phone`
    - `xaffinity person field 123 --set-json '{"Phone": "+1...", "Title": "CEO"}'`
    - `xaffinity person field 123 --get Phone --get Email`
    """
    import json as json_module

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        from affinity.models.entities import FieldValueCreate
        from affinity.types import FieldId as FieldIdType

        from ..field_utils import (
            FieldResolver,
            fetch_field_metadata,
            find_field_values_for_field,
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
        field_metadata = fetch_field_metadata(client=client, entity_type="person")
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
            existing_values = client.field_values.list(person_id=PersonId(person_id))
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
                name="person field",
                inputs={"personId": person_id},
                modifiers=ctx_modifiers,
            )

            return CommandOutput(
                data=results,
                context=cmd_context,
                api_called=True,
            )

        # Handle --set and --json: set field values
        set_operations: list[tuple[str, Any]] = []

        # Collect from --set options
        for field_name, value in set_values:
            set_operations.append((field_name, value))

        # Collect from --json
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
                    set_operations.append((field_name, value))
            except json_module.JSONDecodeError as e:
                raise CLIError(
                    f"Invalid JSON: {e}",
                    exit_code=2,
                    error_type="usage_error",
                ) from e

        # Execute set operations
        created_values: list[dict[str, Any]] = []
        for field_name, value in set_operations:
            target_field_id = resolver.resolve_field_name_or_id(field_name, context="field")
            resolved_name = resolver.get_field_name(target_field_id) or field_name

            # Resolve enriched ID to V1 numeric once; reuse for delete scan and create.
            numeric_field_id = resolver.to_v1_numeric(client, target_field_id, entity_type="person")

            # Check for existing values and delete them first (replace behavior)
            existing_values = client.field_values.list(person_id=PersonId(person_id))
            existing_for_field = find_field_values_for_field(
                field_values=[serialize_model_for_cli(v) for v in existing_values],
                field_id=numeric_field_id,
            )
            for fv in existing_for_field:
                fv_id = fv.get("id")
                if fv_id:
                    client.field_values.delete(fv_id)

            # Create new value
            created = client.field_values.create(
                FieldValueCreate(
                    field_id=FieldIdType(numeric_field_id),
                    entity_id=person_id,
                    value=value,
                )
            )
            created_values.append(serialize_model_for_cli(created))

        # Handle --unset: remove field values
        deleted_count = 0
        for field_name in unset_fields:
            target_field_id = resolver.resolve_field_name_or_id(field_name, context="field")
            numeric_field_id = resolver.to_v1_numeric(client, target_field_id, entity_type="person")
            existing_values = client.field_values.list(person_id=PersonId(person_id))
            existing_for_field = find_field_values_for_field(
                field_values=[serialize_model_for_cli(v) for v in existing_values],
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
            name="person field",
            inputs={"personId": person_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data=results,
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="person field", fn=fn)
