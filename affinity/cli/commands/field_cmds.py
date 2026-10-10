from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any

from pydantic import ValidationError

from affinity.exceptions import ApiVersionTooOldError
from affinity.models.entities import DropdownOption, FieldCreate, FieldMetadata, FieldValueChange
from affinity.models.secondary import FieldValueChangeV2
from affinity.models.types import EntityType, FieldValueType
from affinity.types import (
    AnyFieldId,
    CompanyId,
    EnrichedFieldId,
    FieldId,
    FieldValueChangeAction,
    ListEntryId,
    ListId,
    OpportunityId,
    PersonId,
)

from ..click_compat import RichCommand, RichGroup, click
from ..confirm import run_destructive
from ..context import CLIContext
from ..decorators import category, destructive, progress_capable
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import csv_output_options, output_options
from ..resolve import resolve_list_selector
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli
from ._v1_parsing import parse_choice, parse_date_flexible


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


def _validate_history_selector(
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
    list_entry_id: int | None,
    *,
    changed_after: str | None,
    max_results: int | None,
) -> None:
    """At most one entity selector; without one, --changed-after or --max-results must bound the
    query (a field's whole history across all entities can time out)."""
    selectors = {
        "--person-id": person_id,
        "--company-id": company_id,
        "--opportunity-id": opportunity_id,
        "--list-entry-id": list_entry_id,
    }
    provided = [name for name, value in selectors.items() if value is not None]

    if len(provided) > 1:
        raise CLIError(
            f"Only one entity selector allowed, but got {len(provided)}: {', '.join(provided)}",
            error_type="usage_error",
            exit_code=2,
        )

    if not provided and changed_after is None and max_results is None:
        raise CLIError(
            "Without an entity selector (--person-id, --company-id, --opportunity-id or "
            "--list-entry-id), history covers every entity: bound it with --changed-after "
            "and/or --max-results.\n"
            "Example: xaffinity field history field-123 --changed-after 2025-01-01",
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

    run_destructive(ctx, command="field delete", yes=yes, prompt=f"Delete field {field_id}?", fn=fn)


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
    "--changed-after",
    type=str,
    default=None,
    help="Only changes at or after this time (ISO date/datetime or relative, e.g. -30d).",
)
@click.option(
    "--order",
    type=click.Choice(["desc", "asc"]),
    default="desc",
    show_default=True,
    help="Newest first (desc) or oldest first (asc).",
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
    changed_after: str | None,
    order: str,
    max_results: int | None,
) -> None:
    """Show field value change history.

    FIELD_ID is the field identifier (e.g., 'field-123').
    Use 'xaffinity field ls --list-id LIST' to find field IDs.

    Give one entity selector to see that entity's history. Without one, changes for every
    entity are returned; then --changed-after and/or --max-results is required.
    --max-results returns the most recent N (or the oldest N with --order asc).
    Action types are create/update/delete. For changes across all fields and entities, see
    `xaffinity field changes`.

    Examples:

    - `xaffinity field history field-123 --person-id 456`

    - `xaffinity field history field-123 --changed-after 2025-01-01 --order asc`

    - `xaffinity field history field-260415 --list-entry-id 789 --action-type update`

    - `xaffinity field history field-123 --company-id 100 --max-results 10`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _validate_history_selector(
            person_id,
            company_id,
            opportunity_id,
            list_entry_id,
            changed_after=changed_after,
            max_results=max_results,
        )
        if max_results is not None and max_results < 1:
            raise CLIError(
                "--max-results must be at least 1.", error_type="usage_error", exit_code=2
            )
        changed_after_dt = (
            parse_date_flexible(changed_after, label="--changed-after")
            if changed_after is not None
            else None
        )

        client = ctx.get_client(warnings=warnings)
        changes = client.field_value_changes.list(
            field_id=FieldId(field_id),
            person_id=PersonId(person_id) if person_id is not None else None,
            company_id=CompanyId(company_id) if company_id is not None else None,
            opportunity_id=OpportunityId(opportunity_id) if opportunity_id is not None else None,
            list_entry_id=ListEntryId(list_entry_id) if list_entry_id is not None else None,
            action_type=_ACTION_TYPE_MAP[action_type] if action_type else None,
            changed_after=changed_after_dt,
            limit=max_results,
            order_by="asc" if order == "asc" else "desc",
        )

        # Keep the client-side limit too, in case the server returns more
        if max_results is not None:
            changes = changes[:max_results]

        payload = [_field_value_change_payload(item) for item in changes]

        # Build CommandContext for richer output metadata
        # Per spec: required params are inputs, optional params are modifiers
        # fieldId is required; the entity selector (if any) is an input too
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
        if changed_after_dt is not None:
            modifiers["changedAfter"] = changed_after_dt.isoformat()
        if order != "desc":
            modifiers["order"] = order
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


def _any_field_id(value: str) -> AnyFieldId:
    """A custom field ID (`field-123` or `123`) or an enriched one (e.g. `affinity-data-x`)."""
    return (
        FieldId(value) if value.isdigit() or value.startswith("field-") else EnrichedFieldId(value)
    )


def _field_value_change_v2_payload(item: FieldValueChangeV2) -> dict[str, object]:
    """Convert a V2 field value change to CLI output format."""
    changer_name = None
    if item.changer:
        changer_name = f"{item.changer.first_name} {item.changer.last_name or ''}".strip() or None
    return {
        "id": item.id,
        "fieldId": item.field.id,
        "fieldName": item.field.name,
        "fieldEntityType": item.field.entity_type,
        "fieldScope": item.field.type,
        "entityId": item.entity.id,
        "listEntryId": int(item.list_entry.id) if item.list_entry else None,
        "listId": int(item.list_entry.list_id) if item.list_entry else None,
        "actionType": item.action_type,
        "valueType": item.value_type,
        "value": item.value,
        "changedAt": item.changed_at,
        "changerId": int(item.changer.id) if item.changer else None,
        "changerName": changer_name,
    }


@category("read")
@field_group.command(name="changes", cls=RichCommand)
@click.option(
    "--field-id",
    "field_ids",
    multiple=True,
    help="Only this field (e.g. field-123); repeat for several.",
)
@click.option(
    "--list-entry-id",
    "list_entry_ids",
    type=int,
    multiple=True,
    help="Only this list entry; repeat for several.",
)
@click.option("--changer-id", type=int, default=None, help="Only changes made by this person.")
@click.option(
    "--changed-after",
    type=str,
    default=None,
    help="Only changes at or after this time (ISO date/datetime or relative, e.g. -7d).",
)
@click.option(
    "--changed-before",
    type=str,
    default=None,
    help="Only changes before this time (ISO date/datetime or relative).",
)
@click.option(
    "--action-type",
    type=click.Choice(["add", "update", "delete"]),
    default=None,
    help="Only this kind of change.",
)
@click.option(
    "--order",
    type=click.Choice(["asc", "desc"]),
    default="asc",
    show_default=True,
    help="Oldest first (asc) or newest first (desc).",
)
@click.option("--cursor", type=str, default=None, help="Resume from a previous nextCursor.")
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N results total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@output_options
@click.pass_obj
@apply_mcp_limits()
def field_changes(
    ctx: CLIContext,
    *,
    field_ids: tuple[str, ...],
    list_entry_ids: tuple[int, ...],
    changer_id: int | None,
    changed_after: str | None,
    changed_before: str | None,
    action_type: str | None,
    order: str,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List field value changes across all entities and fields (V2).

    For delta sync and audits: what changed, where, and who changed it. Filters combine with
    AND; repeated --field-id / --list-entry-id values combine with OR. Action types are
    add/update/delete (V2 names; `field history` uses create/update/delete). To follow one
    person, company or opportunity, use `xaffinity field history` instead. Only fields with
    change tracking are included. Times in filters are rounded outward to whole seconds.

    Without --all or --max-results, one page (up to 100) is returned with a nextCursor.

    Examples:

    - `xaffinity field changes --field-id field-123 --changed-after -7d`

    - `xaffinity field changes --list-entry-id 789 --order desc --max-results 20`

    - `xaffinity field changes --changed-after 2025-06-01T00:00:00Z --all`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        filters_given = bool(
            field_ids
            or list_entry_ids
            or changer_id is not None
            or changed_after
            or changed_before
            or action_type
            or order != "asc"
        )
        if cursor is not None and filters_given:
            raise CLIError(
                "--cursor can't be combined with filters or --order; the cursor carries them.",
                error_type="usage_error",
                exit_code=2,
            )
        if max_results is not None and max_results < 1:
            raise CLIError(
                "--max-results must be at least 1.", error_type="usage_error", exit_code=2
            )
        after_dt = (
            parse_date_flexible(changed_after, label="--changed-after") if changed_after else None
        )
        before_dt = (
            parse_date_flexible(changed_before, label="--changed-before")
            if changed_before
            else None
        )

        modifiers: dict[str, object] = {}
        if field_ids:
            modifiers["fieldIds"] = list(field_ids)
        if list_entry_ids:
            modifiers["listEntryIds"] = list(list_entry_ids)
        if changer_id is not None:
            modifiers["changerId"] = changer_id
        if after_dt is not None:
            modifiers["changedAfter"] = after_dt.isoformat()
        if before_dt is not None:
            modifiers["changedBefore"] = before_dt.isoformat()
        if action_type is not None:
            modifiers["actionType"] = action_type
        if order != "asc":
            modifiers["order"] = order
        if cursor is not None:
            modifiers["cursor"] = cursor
        if max_results is not None:
            modifiers["maxResults"] = max_results
        if all_pages:
            modifiers["allPages"] = True
        cmd_context = CommandContext(name="field changes", inputs={}, modifiers=modifiers)

        client = ctx.get_client(warnings=warnings)
        service = client.field_value_changes
        page_limit = min(max_results, 100) if max_results is not None else None
        results: list[dict[str, object]] = []
        next_cursor: str | None = None
        page = (
            service.list_global(cursor=cursor)
            if cursor is not None
            else service.list_global(
                field_id=[_any_field_id(f) for f in field_ids] or None,
                list_entry_id=list(list_entry_ids) or None,
                changer_id=changer_id,
                changed_after=after_dt,
                changed_before=before_dt,
                action_type=action_type,  # type: ignore[arg-type]
                order="desc" if order == "desc" else "asc",
                limit=page_limit,
            )
        )
        while True:
            for item in page.data:
                results.append(_field_value_change_v2_payload(item))
            next_cursor = page.next_cursor
            if max_results is not None and len(results) >= max_results:
                if len(results) > max_results:
                    # Stopped inside a page: its cursor would skip the rest of it
                    results = results[:max_results]
                    next_cursor = None
                    warnings.append(
                        "Results limited by --max-results. Use --all to fetch all results."
                    )
                break
            if not next_cursor or not (all_pages or max_results is not None):
                break
            page = service.list_global(cursor=next_cursor)

        pagination = {"nextCursor": next_cursor, "prevCursor": None} if next_cursor else None
        return CommandOutput(
            data={"fieldValueChanges": results},
            context=cmd_context,
            pagination=pagination,
            api_called=True,
        )

    run_command(ctx, command="field changes", fn=fn)


# ---------------------------------------------------------------------------
# options (V2 dropdown options)
# ---------------------------------------------------------------------------

_OPTION_TYPES = ("dropdown", "ranked-dropdown", "status-dropdown")
_OPTION_COLORS = ("white", "gray", "blue", "green", "purple", "orange", "red")
_STATUS_CATEGORIES = ("open", "won", "lost", "on-hold")
_OPTION_DELETE_WARNING = (
    "Affinity also clears this field on every list entry set to the option; "
    "those values cannot be recovered."
)


def _option_payload(option: DropdownOption) -> dict[str, object]:
    return serialize_model_for_cli(option)


def _resolve_list_id(ctx: CLIContext, client: Any, selector: str) -> int:
    resolved = resolve_list_selector(client=client, selector=selector, cache=ctx.session_cache)
    return int(resolved.list.id)


@field_group.group(name="options", cls=RichGroup)
def field_options_group() -> None:
    """Dropdown options of a field: list them; add, change or delete them on list fields."""


@category("read")
@field_options_group.command(name="ls", cls=RichCommand)
@click.argument("field_id", type=str)
@click.option("--list-id", "--list", "list_id", default=None, help="The field's list (ID or name).")
@click.option(
    "--entity-type",
    type=click.Choice(["company", "person"]),
    default=None,
    help="For a global company or person field (instead of --list-id).",
)
@output_options
@click.pass_obj
def field_options_ls(
    ctx: CLIContext, *, field_id: str, list_id: str | None, entity_type: str | None
) -> None:
    """List a dropdown, ranked-dropdown or status field's options.

    Give --list-id for a list field (also opportunity fields) or --entity-type for a global
    company/person field. Each option has id, text, type, rank, color and, for status fields,
    statusCategory and winRate.

    Examples:

    - `xaffinity field options ls field-123 --list-id Dealflow`

    - `xaffinity field options ls field-456 --entity-type company`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if (list_id is None) == (entity_type is None):
            raise CLIError(
                "Give exactly one of --list-id (list field) or --entity-type (global field).",
                error_type="usage_error",
                exit_code=2,
            )
        client = ctx.get_client(warnings=warnings)
        inputs: dict[str, object] = {"fieldId": field_id}
        fid = _any_field_id(field_id)
        if list_id is not None:
            lid = _resolve_list_id(ctx, client, list_id)
            inputs["listId"] = lid

            def read(status_types: bool) -> list[DropdownOption]:
                return client.lists.get_field_dropdown_options(
                    ListId(lid), fid, with_status_types=status_types
                )
        else:
            inputs["entityType"] = entity_type
            service = client.companies if entity_type == "company" else client.persons

            def read(status_types: bool) -> list[DropdownOption]:
                return service.get_field_dropdown_options(fid, with_status_types=status_types)

        # Status options show as ranked-dropdown on older API versions: read with the version
        # the option writes use, unless the client is pinned older
        try:
            options = read(True)
        except ApiVersionTooOldError:
            warnings.append(
                "Pinned to an older Affinity API version: status options show as "
                "ranked-dropdown without statusCategory."
            )
            options = read(False)
        return CommandOutput(
            data={"options": [_option_payload(o) for o in options]},
            context=CommandContext(name="field options ls", inputs=inputs, modifiers={}),
            api_called=True,
        )

    run_command(ctx, command="field options ls", fn=fn)


def _option_value_options(func: Any) -> Any:
    for decorator in reversed(
        [
            click.option("--rank", type=int, default=None, help="Sort position (0 or more)."),
            click.option("--color", type=click.Choice(_OPTION_COLORS), default=None, help="Color."),
            click.option(
                "--status-category",
                type=click.Choice(_STATUS_CATEGORIES),
                default=None,
                help="Status fields: open, won, lost or on-hold.",
            ),
            click.option(
                "--win-rate",
                type=int,
                default=None,
                help="Status fields, open category: chance of winning, 0-100.",
            ),
        ]
    ):
        func = decorator(func)
    return func


@category("write")
@field_options_group.command(name="create", cls=RichCommand)
@click.argument("field_id", type=str)
@click.option(
    "--list-id", "--list", "list_id", required=True, help="The field's list (ID or name)."
)
@click.option("--text", required=True, help="The option's text (1-255 characters).")
@click.option(
    "--type",
    "option_type",
    type=click.Choice(_OPTION_TYPES),
    default=None,
    help="Option type; taken from the field's existing options when omitted.",
)
@_option_value_options
@output_options
@click.pass_obj
def field_options_create(
    ctx: CLIContext,
    *,
    field_id: str,
    list_id: str,
    text: str,
    option_type: str | None,
    rank: int | None,
    color: str | None,
    status_category: str | None,
    win_rate: int | None,
) -> None:
    """Add an option to a list field's dropdown, ranked-dropdown or status field.

    The type comes from the field's existing options (pass --type if it has none). For ranked
    and status fields, --rank defaults to after the last option and --color to white; status
    fields also need --status-category. Global company/person fields' options can't be changed
    through the API.

    Examples:

    - `xaffinity field options create field-123 --list-id Dealflow --text "Due diligence"`

    - `xaffinity field options create field-124 --list-id Dealflow --text Won --status-category won`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        lid = _resolve_list_id(ctx, client, list_id)
        existing = client.lists.get_field_dropdown_options(
            ListId(lid), _any_field_id(field_id), with_status_types=True
        )
        kind = option_type or next((o.type for o in existing if o.type), None)
        if kind is None:
            raise CLIError(
                f"{field_id} has no options to take the type from: pass --type "
                "(dropdown, ranked-dropdown or status-dropdown).",
                error_type="usage_error",
                exit_code=2,
            )
        new_rank, new_color = rank, color
        if kind != "dropdown":
            if new_rank is None:
                new_rank = max((o.rank or 0 for o in existing), default=-1) + 1
            if new_color is None:
                new_color = "white"
        try:
            option = client.lists.create_field_dropdown_option(
                ListId(lid),
                _any_field_id(field_id),
                option_type=kind,  # type: ignore[arg-type]
                text=text,
                rank=new_rank,
                color=new_color,  # type: ignore[arg-type]
                status_category=status_category,  # type: ignore[arg-type]
                win_rate=win_rate,
            )
        except ValidationError:
            raise  # a response that didn't parse is not a usage error
        except ValueError as e:
            raise CLIError(str(e), error_type="usage_error", exit_code=2) from e
        return CommandOutput(
            data={"option": _option_payload(option)},
            context=CommandContext(
                name="field options create",
                inputs={"fieldId": field_id, "listId": lid},
                modifiers={"text": text, "type": kind},
            ),
            api_called=True,
        )

    run_command(ctx, command="field options create", fn=fn)


@category("write")
@field_options_group.command(name="update", cls=RichCommand)
@click.argument("field_id", type=str)
@click.argument("option_id", type=int)
@click.option(
    "--list-id", "--list", "list_id", required=True, help="The field's list (ID or name)."
)
@click.option("--text", default=None, help="New text (1-255 characters).")
@_option_value_options
@output_options
@click.pass_obj
def field_options_update(
    ctx: CLIContext,
    *,
    field_id: str,
    option_id: int,
    list_id: str,
    text: str | None,
    rank: int | None,
    color: str | None,
    status_category: str | None,
    win_rate: int | None,
) -> None:
    """Change an option of a list field (only the values you pass).

    Renaming changes the value shown on every entry that has the option. Dropdown options can
    change text; ranked options also rank and color; status options also status category and
    win rate.

    Example:

    - `xaffinity field options update field-123 4567 --list-id Dealflow --text "Closed - won"`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        lid = _resolve_list_id(ctx, client, list_id)
        try:
            option = client.lists.update_field_dropdown_option(
                ListId(lid),
                _any_field_id(field_id),
                option_id,
                text=text,
                rank=rank,
                color=color,  # type: ignore[arg-type]
                status_category=status_category,  # type: ignore[arg-type]
                win_rate=win_rate,
            )
        except ValidationError:
            raise  # a response that didn't parse is not a usage error
        except ValueError as e:
            raise CLIError(str(e), error_type="usage_error", exit_code=2) from e
        return CommandOutput(
            data={"option": _option_payload(option)},
            context=CommandContext(
                name="field options update",
                inputs={"fieldId": field_id, "optionId": option_id, "listId": lid},
                modifiers={},
            ),
            api_called=True,
        )

    run_command(ctx, command="field options update", fn=fn)


@category("write")
@destructive
@field_options_group.command(name="delete", cls=RichCommand)
@click.argument("field_id", type=str)
@click.argument("option_id", type=int)
@click.option(
    "--list-id", "--list", "list_id", required=True, help="The field's list (ID or name)."
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def field_options_delete(
    ctx: CLIContext, *, field_id: str, option_id: int, list_id: str, yes: bool
) -> None:
    """Delete an option of a list field. Cannot be undone.

    Affinity also clears this field on every list entry that has the option; those values
    cannot be recovered. Asks for confirmation; scripts pass --yes.

    Example:

    - `xaffinity field options delete field-123 4567 --list-id Dealflow --yes`
    """
    # Name the option in the prompt so a wrong id is caught (best effort: errors surface below)
    label = str(option_id)
    if not yes:
        with contextlib.suppress(Exception):
            client = ctx.get_client(warnings=[])
            lid = _resolve_list_id(ctx, client, list_id)
            option = client.lists.get_field_dropdown_option(
                ListId(lid), _any_field_id(field_id), option_id
            )
            label = f'{option_id} "{option.text}"'

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        lid = _resolve_list_id(ctx, client, list_id)
        client.lists.delete_field_dropdown_option(ListId(lid), _any_field_id(field_id), option_id)
        return CommandOutput(
            data={"success": True},
            context=CommandContext(
                name="field options delete",
                inputs={"fieldId": field_id, "optionId": option_id, "listId": lid},
                modifiers={},
            ),
            api_called=True,
        )

    run_destructive(
        ctx,
        command="field options delete",
        yes=yes,
        prompt=f"Delete option {label} of {field_id}? {_OPTION_DELETE_WARNING}",
        fn=fn,
    )


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
