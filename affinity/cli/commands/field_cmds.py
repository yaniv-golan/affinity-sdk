from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from affinity.models.entities import FieldCreate, FieldMetadata, FieldValueChange
from affinity.models.types import EntityType, FieldValueType
from affinity.types import (
    CompanyId,
    FieldId,
    FieldValueChangeAction,
    ListEntryId,
    ListId,
    OpportunityId,
    PersonId,
)

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..decorators import category, destructive, progress_capable
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import csv_output_options, output_options
from ..resolve import resolve_list_selector
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli
from ._v1_parsing import parse_choice


@click.group(name="field", cls=RichGroup)
def field_group() -> None:
    """Field commands."""


_ENTITY_TYPE_MAP = {
    "person": EntityType.PERSON,
    "people": EntityType.PERSON,
    "company": EntityType.ORGANIZATION,
    "organization": EntityType.ORGANIZATION,
    "opportunity": EntityType.OPPORTUNITY,
}

# What V1 `POST /fields` can create - an explicit set, not every FieldValueType member (the enum
# also models read-only/computed types: interaction, formula-number, note, reminder, list-multi,
# and filterable-text, which only Affinity creates).
_CREATABLE_VALUE_TYPES: tuple[FieldValueType, ...] = (
    FieldValueType.PERSON,
    FieldValueType.PERSON_MULTI,
    FieldValueType.COMPANY,
    FieldValueType.COMPANY_MULTI,
    FieldValueType.DROPDOWN,
    FieldValueType.DROPDOWN_MULTI,
    FieldValueType.NUMBER,
    FieldValueType.NUMBER_MULTI,
    FieldValueType.DATETIME,
    FieldValueType.LOCATION,
    FieldValueType.LOCATION_MULTI,
    FieldValueType.TEXT,
    FieldValueType.RANKED_DROPDOWN,
)
# `-multi` variants are created as the base V1 type with allows_multiple=True.
_MULTI_VALUE_TYPES = frozenset(vt for vt in _CREATABLE_VALUE_TYPES if vt.value.endswith("-multi"))
_VALUE_TYPE_MAP = {ft.value: ft for ft in _CREATABLE_VALUE_TYPES}

_ACTION_TYPE_MAP = {
    "create": FieldValueChangeAction.CREATE,
    "delete": FieldValueChangeAction.DELETE,
    "update": FieldValueChangeAction.UPDATE,
}

_ACTION_TYPE_NAMES = {
    FieldValueChangeAction.CREATE: "create",
    FieldValueChangeAction.DELETE: "delete",
    FieldValueChangeAction.UPDATE: "update",
}


def _field_payload(field: FieldMetadata) -> dict[str, object]:
    return serialize_model_for_cli(field)


def _field_value_change_payload(item: FieldValueChange) -> dict[str, object]:
    """Convert FieldValueChange to CLI output format."""
    # Display enum name instead of integer (consistent with interaction_cmds.py)
    action_name = _ACTION_TYPE_NAMES.get(
        FieldValueChangeAction(item.action_type),
        str(item.action_type),
    )

    # Flatten changer name for table display
    changer_name = None
    if item.changer:
        first = item.changer.first_name or ""
        last = item.changer.last_name or ""
        changer_name = f"{first} {last}".strip() or None

    return {
        "id": int(item.id),
        "fieldId": str(item.field_id),
        "entityId": item.entity_id,
        "listEntryId": int(item.list_entry_id) if item.list_entry_id else None,
        "actionType": action_name,
        "value": item.value,
        "changedAt": item.changed_at,
        "changerName": changer_name,
        "changer": serialize_model_for_cli(item.changer) if item.changer else None,
    }


def _validate_exactly_one_selector(
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
    list_entry_id: int | None,
) -> None:
    """Validate that exactly one entity selector is provided."""
    selectors = {
        "--person-id": person_id,
        "--company-id": company_id,
        "--opportunity-id": opportunity_id,
        "--list-entry-id": list_entry_id,
    }
    provided = [name for name, value in selectors.items() if value is not None]

    if len(provided) == 1:
        return

    if len(provided) == 0:
        raise CLIError(
            "Exactly one entity selector is required: "
            "--person-id, --company-id, --opportunity-id, or --list-entry-id.\n"
            "Example: xaffinity field history field-123 --person-id 456",
            error_type="usage_error",
            exit_code=2,
        )

    raise CLIError(
        f"Only one entity selector allowed, but got {len(provided)}: {', '.join(provided)}",
        error_type="usage_error",
        exit_code=2,
    )


@category("read")
@field_group.command(name="ls", cls=RichCommand)
@click.option(
    "--list-id",
    "--list",
    type=str,
    default=None,
    help="Filter by list (ID or name).",
)
@click.option(
    "--entity-type",
    type=click.Choice(sorted(_ENTITY_TYPE_MAP.keys())),
    default=None,
    help="Filter by entity type (person/company/opportunity).",
)
@output_options
@click.pass_obj
def field_ls(
    ctx: CLIContext,
    *,
    list_id: str | None,
    entity_type: str | None,
) -> None:
    """List fields with dropdown options."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        cache = ctx.session_cache
        parsed_type = parse_choice(entity_type, _ENTITY_TYPE_MAP, label="entity type")

        # Resolve list selector (accepts name or ID)
        resolved_list_id: int | None = None
        ctx_resolved: dict[str, str] | None = None
        if list_id is not None:
            resolved = resolve_list_selector(client=client, selector=list_id, cache=cache)
            resolved_list_id = int(resolved.list.id)
            # Only include resolved name if different from input (i.e., name was provided)
            if resolved.list.name and resolved.list.name != list_id:
                ctx_resolved = {"listId": resolved.list.name}

        # Build cache key from resolved ID (not input string) for consistency
        cache_key = f"fields_v1_list{resolved_list_id or 'all'}_type{entity_type or 'all'}"

        # Check session cache first
        fields: list[FieldMetadata] | None = None
        api_called = False
        if cache.enabled:
            fields = cache.get_list(cache_key, FieldMetadata)

        if fields is None:
            fields = client.fields.list(
                list_id=ListId(resolved_list_id) if resolved_list_id is not None else None,
                entity_type=parsed_type,
            )
            api_called = True
            # Cache the result
            if cache.enabled:
                cache.set(cache_key, fields)

        payload = [_field_payload(field) for field in fields]

        # Build CommandContext
        ctx_modifiers: dict[str, object] = {}
        if resolved_list_id is not None:
            ctx_modifiers["listId"] = resolved_list_id
        if entity_type:
            ctx_modifiers["entityType"] = entity_type

        cmd_context = CommandContext(
            name="field ls",
            inputs={},
            modifiers=ctx_modifiers,
            resolved=ctx_resolved,
        )

        return CommandOutput(data={"fields": payload}, context=cmd_context, api_called=api_called)

    run_command(ctx, command="field ls", fn=fn)


@category("write")
@field_group.command(name="create", cls=RichCommand)
@click.option("--name", required=True, help="Field name.")
@click.option(
    "--entity-type",
    type=click.Choice(sorted(_ENTITY_TYPE_MAP.keys())),
    required=True,
    help="Entity type (person/company/opportunity).",
)
@click.option(
    "--value-type",
    type=click.Choice(sorted(_VALUE_TYPE_MAP.keys())),
    required=True,
    help=(
        "Field value type (e.g. text, dropdown, person, number). "
        "A -multi type implies --allows-multiple."
    ),
)
@click.option("--list-id", type=int, default=None, help="List id for list-specific field.")
@click.option("--allows-multiple", is_flag=True, help="Allow multiple values.")
@click.option("--list-specific", is_flag=True, help="Mark as list-specific.")
@click.option("--required", is_flag=True, help="Mark as required.")
@output_options
@click.pass_obj
def field_create(
    ctx: CLIContext,
    *,
    name: str,
    entity_type: str,
    value_type: str,
    list_id: int | None,
    allows_multiple: bool,
    list_specific: bool,
    required: bool,
) -> None:
    """Create a field."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        parsed_entity_type = parse_choice(entity_type, _ENTITY_TYPE_MAP, label="entity type")
        parsed_value_type = parse_choice(value_type, _VALUE_TYPE_MAP, label="value type")
        if parsed_entity_type is None or parsed_value_type is None:
            raise CLIError("Missing required field options.", error_type="usage_error", exit_code=2)
        multiple = allows_multiple or parsed_value_type in _MULTI_VALUE_TYPES
        client = ctx.get_client(warnings=warnings)
        created = client.fields.create(
            FieldCreate(
                name=name,
                entity_type=parsed_entity_type,
                value_type=parsed_value_type,
                list_id=ListId(list_id) if list_id is not None else None,
                allows_multiple=multiple,
                is_list_specific=list_specific,
                is_required=required,
            )
        )

        # Invalidate field-related caches
        cache = ctx.session_cache
        cache.invalidate_prefix("list_fields_")
        cache.invalidate_prefix("person_fields_")
        cache.invalidate_prefix("company_fields_")
        cache.invalidate_prefix("fields_v1_")

        payload = _field_payload(created)

        # Build CommandContext
        ctx_modifiers: dict[str, object] = {
            "entityType": entity_type,
            "valueType": value_type,
        }
        if list_id is not None:
            ctx_modifiers["listId"] = list_id
        if multiple:
            ctx_modifiers["allowsMultiple"] = True
        if list_specific:
            ctx_modifiers["listSpecific"] = True
        if required:
            ctx_modifiers["required"] = True

        cmd_context = CommandContext(
            name="field create",
            inputs={"name": name},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(data={"field": payload}, context=cmd_context, api_called=True)

    run_command(ctx, command="field create", fn=fn)


@category("write")
@destructive
@field_group.command(name="delete", cls=RichCommand)
@click.argument("field_id", type=str)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def field_delete(ctx: CLIContext, field_id: str, yes: bool) -> None:
    """Delete a field."""
    if not yes:
        click.confirm(f"Delete field {field_id}?", abort=True)

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        success = client.fields.delete(FieldId(field_id))

        # Invalidate field-related caches
        cache = ctx.session_cache
        cache.invalidate_prefix("list_fields_")
        cache.invalidate_prefix("person_fields_")
        cache.invalidate_prefix("company_fields_")
        cache.invalidate_prefix("fields_v1_")

        cmd_context = CommandContext(
            name="field delete",
            inputs={"fieldId": field_id},
            modifiers={},
        )

        return CommandOutput(data={"success": success}, context=cmd_context, api_called=True)

    run_command(ctx, command="field delete", fn=fn)


@category("read")
@field_group.command(name="history", cls=RichCommand)
@click.argument("field_id", type=str)
@click.option("--person-id", type=int, default=None, help="Filter by person ID.")
@click.option("--company-id", type=int, default=None, help="Filter by company ID.")
@click.option("--opportunity-id", type=int, default=None, help="Filter by opportunity ID.")
@click.option("--list-entry-id", type=int, default=None, help="Filter by list entry ID.")
@click.option(
    "--action-type",
    type=click.Choice(["create", "update", "delete"]),
    default=None,
    help="Filter by action type.",
)
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Limit number of results."
)
@output_options
@click.pass_obj
@apply_mcp_limits(all_pages_param=None)
def field_history(
    ctx: CLIContext,
    *,
    field_id: str,
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
    list_entry_id: int | None,
    action_type: str | None,
    max_results: int | None,
) -> None:
    """Show field value change history.

    FIELD_ID is the field identifier (e.g., 'field-123').
    Use 'xaffinity field ls --list-id LIST' to find field IDs.

    Exactly one entity selector is required.

    Examples:

    - `xaffinity field history field-123 --person-id 456`

    - `xaffinity field history field-260415 --list-entry-id 789 --action-type update`

    - `xaffinity field history field-123 --company-id 100 --max-results 10`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _validate_exactly_one_selector(person_id, company_id, opportunity_id, list_entry_id)

        client = ctx.get_client(warnings=warnings)
        changes = client.field_value_changes.list(
            field_id=FieldId(field_id),
            person_id=PersonId(person_id) if person_id is not None else None,
            company_id=CompanyId(company_id) if company_id is not None else None,
            opportunity_id=OpportunityId(opportunity_id) if opportunity_id is not None else None,
            list_entry_id=ListEntryId(list_entry_id) if list_entry_id is not None else None,
            action_type=_ACTION_TYPE_MAP[action_type] if action_type else None,
        )

        # Apply client-side max_results limit
        if max_results is not None:
            changes = changes[:max_results]

        payload = [_field_value_change_payload(item) for item in changes]

        # Build CommandContext for richer output metadata
        # Per spec: required params are inputs, optional params are modifiers
        # fieldId and exactly one entity selector are required → both are inputs
        inputs: dict[str, object] = {"fieldId": field_id}
        if person_id is not None:
            inputs["personId"] = person_id
        elif company_id is not None:
            inputs["companyId"] = company_id
        elif opportunity_id is not None:
            inputs["opportunityId"] = opportunity_id
        elif list_entry_id is not None:
            inputs["listEntryId"] = list_entry_id

        modifiers: dict[str, object] = {}
        if action_type is not None:
            modifiers["actionType"] = action_type
        if max_results is not None:
            modifiers["maxResults"] = max_results

        cmd_context = CommandContext(
            name="field history",
            inputs=inputs,
            modifiers=modifiers,
        )

        return CommandOutput(
            data={"fieldValueChanges": payload}, context=cmd_context, api_called=True
        )

    run_command(ctx, command="field history", fn=fn)


# ---------------------------------------------------------------------------
# history-bulk
# ---------------------------------------------------------------------------

_HISTORY_BULK_DEFAULT_CONCURRENCY = 15


def _get_entity_name_from_entry(entry: Any) -> str | None:
    """Extract entity name from a ListEntryWithEntity."""
    if entry.entity is None:
        return None
    name = getattr(entry.entity, "name", None)
    if name is None and hasattr(entry.entity, "full_name"):
        name = entry.entity.full_name or None
    return name


@progress_capable
@category("read")
@field_group.command(name="history-bulk", cls=RichCommand)
@click.argument("field_id", type=str)
@click.option(
    "--list-id",
    "--list",
    type=str,
    default=None,
    help="List ID or name — required unless --list-entry-ids is provided.",
)
@click.option(
    "--list-entry-ids",
    type=str,
    default=None,
    help="Comma-separated list entry IDs (mutually exclusive with --list-id).",
)
@click.option(
    "--max-results",
    "--limit",
    "-n",
    type=int,
    default=None,
    help="Max number of entries to process.",
)
@click.option("--all", "all_entries", is_flag=True, help="Process all entries in the list.")
@click.option(
    "--action-type",
    type=click.Choice(["create", "update", "delete"]),
    default=None,
    help="Filter by action type.",
)
@click.option("--dry-run", is_flag=True, help="Show estimated API calls without executing.")
@csv_output_options
@click.pass_obj
def field_history_bulk(
    ctx: CLIContext,
    *,
    field_id: str,
    list_id: str | None,
    list_entry_ids: str | None,
    max_results: int | None,
    all_entries: bool,
    action_type: str | None,
    dry_run: bool,
) -> None:
    """Fetch field value change history for multiple list entries.

    Uses async fan-out with bounded concurrency.

    FIELD_ID is the field identifier (e.g., 'field-123').

    Examples:

    - `xaffinity field history-bulk field-123 --list-id 42 --all`

    - `xaffinity field history-bulk field-123 --list-entry-ids 10,20,30`

    - `xaffinity field history-bulk field-123 --list-id "My Pipeline" --max-results 50`

    - `xaffinity field history-bulk field-123 --list-id 42 --all --dry-run`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        # --- Validation ---
        if list_id is not None and list_entry_ids is not None:
            raise CLIError(
                "--list-id and --list-entry-ids are mutually exclusive.",
                error_type="usage_error",
                exit_code=2,
            )

        if list_id is None and list_entry_ids is None:
            raise CLIError(
                "Specify --list-entry-ids, --max-results, or --all to set a bound.\n"
                "Use --dry-run to estimate the number of API calls first.",
                error_type="usage_error",
                exit_code=2,
            )

        if list_entry_ids is not None and all_entries:
            raise CLIError(
                "--list-entry-ids and --all are mutually exclusive. "
                "Entry IDs are already explicit.",
                error_type="usage_error",
                exit_code=2,
            )

        if list_id is not None and not all_entries and max_results is None:
            raise CLIError(
                "Specify --list-entry-ids, --max-results, or --all to set a bound.\n"
                "Use --dry-run to estimate the number of API calls first.",
                error_type="usage_error",
                exit_code=2,
            )

        # --- Resolve entries ---
        # entry_ids: list of ListEntryId to process
        # entry_names: mapping entry_id -> entity name (populated only for --list-id path)
        entry_ids: list[int] = []
        entry_names: dict[int, str | None] = {}

        if list_entry_ids is not None:
            # Parse comma-separated IDs
            for part in list_entry_ids.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    entry_ids.append(int(part))
                except ValueError:
                    raise CLIError(
                        f"Invalid list entry ID: {part!r}",
                        error_type="usage_error",
                        exit_code=2,
                    ) from None
        else:
            # --list-id path: use sync client to resolve list and fetch entries
            client = ctx.get_client(warnings=warnings)
            cache = ctx.session_cache
            resolved = resolve_list_selector(client=client, selector=list_id, cache=cache)  # type: ignore[arg-type]
            resolved_list_id = int(resolved.list.id)

            # Fetch entries from the list
            entries_iter = client.lists.entries(ListId(resolved_list_id)).all()
            for count, entry in enumerate(entries_iter, 1):
                entry_ids.append(int(entry.id))
                entry_names[int(entry.id)] = _get_entity_name_from_entry(entry)
                if max_results is not None and count >= max_results:
                    break

        total = len(entry_ids)

        # --- Dry run ---
        if dry_run:
            cmd_context = CommandContext(
                name="field history-bulk",
                inputs={"fieldId": field_id},
                modifiers={"dryRun": True},
            )
            return CommandOutput(
                data={
                    "dryRun": True,
                    "entries": total,
                    "estimatedApiCalls": total,
                    "fieldId": field_id,
                },
                context=cmd_context,
                api_called=False,
            )

        # --- Execute async fan-out ---
        parsed_action_type = _ACTION_TYPE_MAP[action_type] if action_type else None
        concurrency = int(
            os.environ.get("XAFFINITY_CONCURRENCY", _HISTORY_BULK_DEFAULT_CONCURRENCY)
        )
        show_progress = (
            ctx.progress != "never"
            and not ctx.quiet
            and (ctx.progress == "always" or sys.stderr.isatty())
        )

        async def _run() -> tuple[list[dict[str, object]], list[str]]:
            from affinity import AsyncAffinity
            from affinity.hooks import ResponseInfo

            from ..query.executor import RateLimitedExecutor, user_rate_limit_remaining

            rate_limiter = RateLimitedExecutor(concurrency=concurrency)

            settings = ctx.resolve_client_settings(warnings=warnings)

            original_on_response = settings.on_response

            def combined_on_response(res: ResponseInfo) -> None:
                if original_on_response is not None:
                    original_on_response(res)
                rate_limiter.on_response(res.status_code, user_rate_limit_remaining(res.headers))

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
                affinity_api_version=settings.affinity_api_version,
            ) as async_client:
                results: list[dict[str, object]] = []
                async_warnings: list[str] = []
                completed = 0
                succeeded = 0
                failed = 0

                async def fetch_one(eid: int) -> list[dict[str, object]]:
                    nonlocal completed, succeeded, failed
                    try:
                        async with rate_limiter:
                            changes = await async_client.field_value_changes.list(
                                field_id=FieldId(field_id),
                                list_entry_id=ListEntryId(eid),
                                action_type=parsed_action_type,
                            )
                        rows: list[dict[str, object]] = []
                        for item in changes:
                            payload = _field_value_change_payload(item)
                            payload["entityName"] = entry_names.get(eid)
                            rows.append(payload)
                        succeeded += 1
                        return rows
                    except Exception as exc:
                        failed += 1
                        async_warnings.append(f"Entry {eid}: {exc}")
                        return []
                    finally:
                        completed += 1
                        if show_progress:
                            sys.stderr.write(f"\r{completed}/{total} entries processed")
                            sys.stderr.flush()

                tasks = [asyncio.create_task(fetch_one(eid)) for eid in entry_ids]
                for task in asyncio.as_completed(tasks):
                    batch = await task
                    results.extend(batch)

                # Clear progress line
                if show_progress and total > 0:
                    sys.stderr.write("\r" + " " * 40 + "\r")
                    sys.stderr.flush()

                if failed > 0:
                    async_warnings.append(
                        f"{succeeded} of {total} entries succeeded, {failed} failed"
                    )
                return results, async_warnings

        all_changes, async_warnings = asyncio.run(_run())
        warnings.extend(async_warnings)

        # Build CommandContext
        inputs: dict[str, object] = {"fieldId": field_id}
        modifiers: dict[str, object] = {}
        if list_id is not None:
            inputs["listId"] = list_id
        if list_entry_ids is not None:
            inputs["listEntryIds"] = list_entry_ids
        if action_type is not None:
            modifiers["actionType"] = action_type
        if max_results is not None:
            modifiers["maxResults"] = max_results
        if all_entries:
            modifiers["all"] = True

        cmd_context = CommandContext(
            name="field history-bulk",
            inputs=inputs,
            modifiers=modifiers,
        )

        return CommandOutput(
            data={"fieldValueChanges": all_changes},
            context=cmd_context,
            api_called=True,
            warnings=warnings,
        )

    run_command(ctx, command="field history-bulk", fn=fn)
