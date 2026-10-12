from __future__ import annotations

from typing import Any

from affinity.models.secondary import Reminder, ReminderCreate, ReminderUpdate
from affinity.models.types import ReminderResetType, ReminderStatus, ReminderType
from affinity.types import CompanyId, OpportunityId, PersonId, ReminderIdType, UserId

from ..click_compat import RichCommand, RichGroup, click
from ..confirm import run_destructive
from ..context import CLIContext
from ..decorators import category, destructive
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ._org_reads import collect_pages
from ._v1_parsing import parse_choice, parse_date_flexible


@click.group(name="reminder", cls=RichGroup)
def reminder_group() -> None:
    """Reminder commands."""


_REMINDER_TYPE_MAP = {
    "one-time": ReminderType.ONE_TIME,
    "recurring": ReminderType.RECURRING,
}

# "meeting" is the 1.x name of "event" (a meeting or other calendar event)
_REMINDER_RESET_MAP = {
    "interaction": ReminderResetType.INTERACTION,
    "email": ReminderResetType.EMAIL,
    "event": ReminderResetType.EVENT,
    "meeting": ReminderResetType.EVENT,
}

_REMINDER_STATUS_MAP = {
    "active": ReminderStatus.ACTIVE,
    "completed": ReminderStatus.COMPLETED,
    "overdue": ReminderStatus.OVERDUE,
}


def _ref_id(ref: Any) -> int | None:
    return int(ref.id) if ref is not None else None


def _reminder_payload(reminder: Reminder) -> dict[str, object]:
    return {
        "id": int(reminder.id),
        "type": str(reminder.type),
        "status": str(reminder.status),
        "content": reminder.content,
        "dueDate": reminder.due_date,
        "resetType": str(reminder.reset_type) if reminder.reset_type is not None else None,
        "reminderDays": reminder.reminder_days,
        "ownerId": _ref_id(reminder.owner),
        "creatorId": _ref_id(reminder.creator),
        "completerId": _ref_id(reminder.completer),
        "personId": _ref_id(reminder.person),
        "companyId": _ref_id(reminder.company),
        "opportunityId": _ref_id(reminder.opportunity),
        "createdAt": reminder.created_at,
        "updatedAt": reminder.updated_at,
        "completedAt": reminder.completed_at,
    }


def _usage(exc: ValueError) -> CLIError:
    errors = getattr(exc, "errors", None)
    message = str(exc)
    if callable(errors):  # pydantic ValidationError: keep only the messages
        message = "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in errors())
    return CLIError(message, error_type="usage_error", exit_code=2)


def _validate_single_entity(
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
) -> None:
    count = sum(1 for value in (person_id, company_id, opportunity_id) if value is not None)
    if count > 1:
        raise CLIError(
            "Reminders can be associated with only one entity.",
            error_type="usage_error",
            exit_code=2,
            hint="Provide only one of --person-id, --company-id, or --opportunity-id.",
        )


@category("read")
@reminder_group.command(name="ls", cls=RichCommand)
@click.option("--person-id", type=int, default=None, help="Filter by person id.")
@click.option("--company-id", type=int, default=None, help="Filter by company id.")
@click.option("--opportunity-id", type=int, default=None, help="Filter by opportunity id.")
@click.option("--creator-id", type=int, default=None, help="Filter by creator id.")
@click.option("--owner-id", type=int, default=None, help="Filter by owner id.")
@click.option("--completer-id", type=int, default=None, help="Filter by completer id.")
@click.option(
    "--type",
    "reminder_type",
    type=click.Choice(sorted(_REMINDER_TYPE_MAP.keys())),
    default=None,
    help="Reminder type (one-time, recurring).",
)
@click.option(
    "--reset-type",
    type=click.Choice(sorted(_REMINDER_RESET_MAP.keys())),
    default=None,
    help="What resets a recurring reminder: interaction, email or event (meeting = event).",
)
@click.option(
    "--status",
    type=click.Choice(sorted(_REMINDER_STATUS_MAP.keys())),
    default=None,
    help="Reminder status (active, completed, overdue).",
)
@click.option(
    "--due-after",
    type=str,
    default=None,
    help="Filter reminders due after this date (ISO-8601, relative, or keyword).",
)
@click.option(
    "--due-before",
    type=str,
    default=None,
    help="Filter reminders due before this date (ISO-8601, relative, or keyword).",
)
@click.option("--page-size", "-s", type=int, default=None, help="Page size (max 100).")
@click.option(
    "--cursor", type=str, default=None, help="Resume from a nextCursor (no other filters)."
)
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N results total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@output_options
@click.pass_obj
@apply_mcp_limits()
def reminder_ls(
    ctx: CLIContext,
    *,
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
    creator_id: int | None,
    owner_id: int | None,
    completer_id: int | None,
    reminder_type: str | None,
    reset_type: str | None,
    status: str | None,
    due_after: str | None,
    due_before: str | None,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List reminders.

    Filter by entity (--person-id, --company-id, --opportunity-id), user (--owner-id,
    --creator-id, --completer-id), type, status, or due date range (--due-after/--due-before).

    Date filters accept ISO-8601 (2024-01-01), relative (+7d, +2w), or keywords (today, tomorrow).

    Examples:

    - `xaffinity reminder ls --person-id 123`

    - `xaffinity reminder ls --status active --due-after today`

    - `xaffinity reminder ls --due-before +7d`

    - `xaffinity reminder ls --owner-id 456 --type recurring`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        # Build CommandContext upfront for all return paths
        ctx_modifiers: dict[str, object] = {}
        if person_id is not None:
            ctx_modifiers["personId"] = person_id
        if company_id is not None:
            ctx_modifiers["companyId"] = company_id
        if opportunity_id is not None:
            ctx_modifiers["opportunityId"] = opportunity_id
        if creator_id is not None:
            ctx_modifiers["creatorId"] = creator_id
        if owner_id is not None:
            ctx_modifiers["ownerId"] = owner_id
        if completer_id is not None:
            ctx_modifiers["completerId"] = completer_id
        if reminder_type:
            ctx_modifiers["type"] = reminder_type
        if reset_type:
            ctx_modifiers["resetType"] = reset_type
        if status:
            ctx_modifiers["status"] = status
        if due_after:
            ctx_modifiers["dueAfter"] = due_after
        if due_before:
            ctx_modifiers["dueBefore"] = due_before
        if page_size is not None:
            ctx_modifiers["pageSize"] = page_size
        if cursor is not None:
            ctx_modifiers["cursor"] = cursor
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["allPages"] = True

        cmd_context = CommandContext(
            name="reminder ls",
            inputs={},
            modifiers=ctx_modifiers,
        )

        if page_size is not None and not 1 <= page_size <= 100:
            raise CLIError(
                "--page-size must be between 1 and 100.", error_type="usage_error", exit_code=2
            )
        filters: dict[str, Any] = {
            "person_id": PersonId(person_id) if person_id is not None else None,
            "company_id": CompanyId(company_id) if company_id is not None else None,
            "opportunity_id": OpportunityId(opportunity_id) if opportunity_id is not None else None,
            "creator_id": UserId(creator_id) if creator_id is not None else None,
            "owner_id": UserId(owner_id) if owner_id is not None else None,
            "completer_id": UserId(completer_id) if completer_id is not None else None,
            "type": parse_choice(reminder_type, _REMINDER_TYPE_MAP, label="reminder type"),
            "reset_type": parse_choice(reset_type, _REMINDER_RESET_MAP, label="reset type"),
            "status": parse_choice(status, _REMINDER_STATUS_MAP, label="status"),
            "due_before": parse_date_flexible(due_before, label="due-before")
            if due_before
            else None,
            "due_after": parse_date_flexible(due_after, label="due-after") if due_after else None,
        }
        given = [k for k, v in filters.items() if v is not None]
        if cursor is not None and (given or page_size is not None):
            raise CLIError(
                "--cursor can't be combined with filters or --page-size; the cursor carries them.",
                error_type="usage_error",
                exit_code=2,
            )
        client = ctx.get_client(warnings=warnings)
        limit = page_size
        if limit is None and max_results is not None:
            limit = min(max_results, 100)
        rows, next_cursor = collect_pages(
            (lambda: client.reminders.list(cursor=cursor))
            if cursor is not None
            else (lambda: client.reminders.list(**filters, limit=limit)),
            lambda c: client.reminders.list(cursor=c),
            _reminder_payload,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        return CommandOutput(
            data={"reminders": rows},
            context=cmd_context,
            pagination={"reminders": {"nextCursor": next_cursor, "prevCursor": None}}
            if next_cursor
            else None,
            api_called=True,
        )

    run_command(ctx, command="reminder ls", fn=fn)


@category("read")
@reminder_group.command(name="get", cls=RichCommand)
@click.argument("reminder_id", type=int)
@output_options
@click.pass_obj
def reminder_get(ctx: CLIContext, reminder_id: int) -> None:
    """Get a reminder by id."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        reminder = client.reminders.get(ReminderIdType(reminder_id))

        cmd_context = CommandContext(
            name="reminder get",
            inputs={"reminderId": reminder_id},
            modifiers={},
        )

        return CommandOutput(
            data={"reminder": _reminder_payload(reminder)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="reminder get", fn=fn)


@category("write")
@reminder_group.command(name="create", cls=RichCommand)
@click.option("--owner-id", type=int, required=True, help="Owner id (required).")
@click.option(
    "--type",
    "reminder_type",
    type=click.Choice(sorted(_REMINDER_TYPE_MAP.keys())),
    required=True,
    help="Reminder type (one-time, recurring).",
)
@click.option("--content", type=str, default=None, help="Reminder content.")
@click.option(
    "--due-date",
    type=str,
    default=None,
    help="Due date: ISO-8601 (2026-01-23), relative (+7d, +2w), or keyword (today, tomorrow).",
)
@click.option(
    "--reset-type",
    type=click.Choice(sorted(_REMINDER_RESET_MAP.keys())),
    default=None,
    help="What resets a recurring reminder: interaction, email or event (meeting = event).",
)
@click.option("--reminder-days", type=int, default=None, help="Days before due date to remind.")
@click.option("--person-id", type=int, default=None, help="Associate person id.")
@click.option("--company-id", type=int, default=None, help="Associate company id.")
@click.option("--opportunity-id", type=int, default=None, help="Associate opportunity id.")
@output_options
@click.pass_obj
def reminder_create(
    ctx: CLIContext,
    *,
    owner_id: int,
    reminder_type: str,
    content: str | None,
    due_date: str | None,
    reset_type: str | None,
    reminder_days: int | None,
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
) -> None:
    """
    Create a reminder.

    One-time reminders require --due-date. Recurring reminders require
    --reset-type and --reminder-days (1-3650). Attach it to one of --person-id, --company-id or
    --opportunity-id; a reminder attached to none is created through the V1 API.

    Due date formats:

    - ISO-8601: 2026-01-23, 2026-01-23T14:00:00Z

    - Relative: +7d (7 days from now), +2w (2 weeks), +1m (1 month), +1y (1 year)

    - Keywords: now, today, tomorrow, yesterday

    Note: Relative dates and keywords use UTC. ISO dates without timezone are
    interpreted as local time and converted to UTC.

    Examples:

    - `xaffinity reminder create --type one-time --due-date +7d --owner-id 123 --person-id 456`

    - `xaffinity reminder create --type one-time --due-date tomorrow --owner-id 123 --person-id 456`

    - `xaffinity reminder create --type recurring --reset-type email --reminder-days 30 \\
      --owner-id 123 --company-id 456`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _ = warnings
        _validate_single_entity(person_id, company_id, opportunity_id)

        parsed_type = parse_choice(reminder_type, _REMINDER_TYPE_MAP, label="reminder type")
        if parsed_type is None:
            raise CLIError("Missing reminder type.", error_type="usage_error", exit_code=2)
        parsed_reset = parse_choice(reset_type, _REMINDER_RESET_MAP, label="reset type")
        due_date_value = parse_date_flexible(due_date, label="due-date") if due_date else None

        client = ctx.get_client(warnings=warnings)
        try:
            reminder = client.reminders.create(
                ReminderCreate(
                    owner_id=UserId(owner_id),
                    type=parsed_type,
                    content=content,
                    due_date=due_date_value,
                    reset_type=parsed_reset,
                    reminder_days=reminder_days,
                    person_id=PersonId(person_id) if person_id is not None else None,
                    company_id=CompanyId(company_id) if company_id is not None else None,
                    opportunity_id=OpportunityId(opportunity_id)
                    if opportunity_id is not None
                    else None,
                )
            )
        except ValueError as exc:
            raise _usage(exc) from exc

        # Build CommandContext for reminder create
        ctx_modifiers: dict[str, object] = {
            "ownerId": owner_id,
            "type": reminder_type,
        }
        if content:
            ctx_modifiers["content"] = content
        if due_date:
            ctx_modifiers["dueDate"] = due_date
        if reset_type:
            ctx_modifiers["resetType"] = reset_type
        if reminder_days is not None:
            ctx_modifiers["reminderDays"] = reminder_days
        if person_id is not None:
            ctx_modifiers["personId"] = person_id
        if company_id is not None:
            ctx_modifiers["companyId"] = company_id
        if opportunity_id is not None:
            ctx_modifiers["opportunityId"] = opportunity_id

        cmd_context = CommandContext(
            name="reminder create",
            inputs={},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"reminder": _reminder_payload(reminder)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="reminder create", fn=fn)


@category("write")
@reminder_group.command(name="update", cls=RichCommand)
@click.argument("reminder_id", type=int)
@click.option("--owner-id", type=int, default=None, help="Owner id.")
@click.option(
    "--type",
    "reminder_type",
    type=click.Choice(sorted(_REMINDER_TYPE_MAP.keys())),
    default=None,
    help="Reminder type (one-time, recurring).",
)
@click.option("--content", type=str, default=None, help="Reminder content.")
@click.option(
    "--due-date",
    type=str,
    default=None,
    help="Due date: ISO-8601 (2026-01-23), relative (+7d, +2w), or keyword (today, tomorrow).",
)
@click.option(
    "--reset-type",
    type=click.Choice(sorted(_REMINDER_RESET_MAP.keys())),
    default=None,
    help="What resets a recurring reminder: interaction, email or event (meeting = event).",
)
@click.option("--reminder-days", type=int, default=None, help="Days before due date to remind.")
@click.option("--clear-content", is_flag=True, help="Remove the reminder's content.")
@click.option(
    "--completed/--not-completed",
    "is_completed",
    default=None,
    help="Complete it (a recurring one moves to its next due date), or reopen a one-time one.",
)
@output_options
@click.pass_obj
def reminder_update(
    ctx: CLIContext,
    reminder_id: int,
    *,
    owner_id: int | None,
    reminder_type: str | None,
    content: str | None,
    due_date: str | None,
    reset_type: str | None,
    reminder_days: int | None,
    clear_content: bool,
    is_completed: bool | None,
) -> None:
    """
    Update a reminder: only the options you pass change.

    --type changes a reminder's type through the V1 API (V2 can't). Only the reminder's creator
    or owner may update it.

    Due date formats:

    - ISO-8601: 2026-01-23, 2026-01-23T14:00:00Z

    - Relative: +7d (7 days from now), +2w (2 weeks), +1m (1 month), +1y (1 year)

    - Keywords: now, today, tomorrow, yesterday

    Note: Relative dates and keywords use UTC. ISO dates without timezone are
    interpreted as local time and converted to UTC.
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        parsed_type = parse_choice(reminder_type, _REMINDER_TYPE_MAP, label="reminder type")
        parsed_reset = parse_choice(reset_type, _REMINDER_RESET_MAP, label="reset type")
        due_date_value = parse_date_flexible(due_date, label="due-date") if due_date else None

        changes: dict[str, Any] = {
            "owner_id": UserId(owner_id) if owner_id is not None else None,
            "type": parsed_type,
            "content": content,
            "due_date": due_date_value,
            "reset_type": parsed_reset,
            "reminder_days": reminder_days,
            "is_completed": is_completed,
        }
        changes = {k: v for k, v in changes.items() if v is not None}
        if clear_content:
            changes["clear_content"] = True
        if not changes:
            raise CLIError(
                "Nothing to update: pass at least one option (e.g. --content, --due-date, "
                "--completed).",
                error_type="usage_error",
                exit_code=2,
            )
        client = ctx.get_client(warnings=warnings)
        try:
            reminder = client.reminders.update(
                ReminderIdType(reminder_id), ReminderUpdate.model_validate(changes)
            )
        except ValueError as exc:
            raise _usage(exc) from exc

        # Build CommandContext for reminder update
        ctx_modifiers: dict[str, object] = {}
        if owner_id is not None:
            ctx_modifiers["ownerId"] = owner_id
        if reminder_type:
            ctx_modifiers["type"] = reminder_type
        if content:
            ctx_modifiers["content"] = content
        if due_date:
            ctx_modifiers["dueDate"] = due_date
        if reset_type:
            ctx_modifiers["resetType"] = reset_type
        if reminder_days is not None:
            ctx_modifiers["reminderDays"] = reminder_days
        if clear_content:
            ctx_modifiers["clearContent"] = True
        if is_completed is not None:
            ctx_modifiers["completed"] = is_completed

        cmd_context = CommandContext(
            name="reminder update",
            inputs={"reminderId": reminder_id},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"reminder": _reminder_payload(reminder)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="reminder update", fn=fn)


@category("write")
@destructive
@reminder_group.command(name="delete", cls=RichCommand)
@click.argument("reminder_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def reminder_delete(ctx: CLIContext, reminder_id: int, yes: bool) -> None:
    """Delete a reminder."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        success = client.reminders.delete(ReminderIdType(reminder_id))

        cmd_context = CommandContext(
            name="reminder delete",
            inputs={"reminderId": reminder_id},
            modifiers={},
        )

        return CommandOutput(
            data={"success": success},
            context=cmd_context,
            api_called=True,
        )

    run_destructive(
        ctx, command="reminder delete", yes=yes, prompt=f"Delete reminder {reminder_id}?", fn=fn
    )
