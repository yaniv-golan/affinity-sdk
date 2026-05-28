from __future__ import annotations

import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

from affinity.models.secondary import Note, NoteCreate, NoteUpdate
from affinity.models.types import InteractionType, NoteType
from affinity.types import CompanyId, NoteId, OpportunityId, PersonId, UserId

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..decorators import category, destructive
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ._v1_parsing import parse_choice, parse_iso_datetime


@click.group(name="note", cls=RichGroup)
def note_group() -> None:
    """Note commands."""


_NOTE_TYPE_MAP = {
    "plain-text": NoteType.PLAIN_TEXT,
    "plain": NoteType.PLAIN_TEXT,
    "html": NoteType.HTML,
    "ai-notetaker": NoteType.AI_NOTETAKER,
    "email-derived": NoteType.EMAIL_DERIVED,
}


def _get_cached_user_id(ctx: CLIContext, client: Any) -> int | None:
    """Return the authenticated user's id, calling /auth/whoami at most once.

    /auth/whoami is rate-limit-exempt (per CLAUDE.md). The cached value lives
    on ``ctx._cached_user_id`` for the lifetime of this CLI invocation. On
    transient failure (auth/network), returns ``None`` so callers can skip
    creator-scoped logic gracefully rather than aborting the write.
    """
    if ctx._cached_user_id is not None:
        return ctx._cached_user_id
    try:
        whoami_result = client.auth.whoami()
        user_id = int(whoami_result.user.id)
        ctx._cached_user_id = user_id
        return user_id
    except Exception:
        return None


def _maybe_warn_duplicate_note(
    *,
    ctx: CLIContext,
    client: Any,
    warnings: list[str],
    content: str,
    person_ids: tuple[int, ...],
    company_ids: tuple[int, ...],
    opportunity_ids: tuple[int, ...],
    explicit_creator_id: int | None,
    window_seconds: int,
) -> None:
    """Append a stderr warning to ``warnings`` if a recent duplicate exists.

    Looks at each attached entity ID, queries the last few notes scoped to
    the authenticated user (or ``explicit_creator_id`` when provided), and
    compares stripped content + recency. Stops at the first match.

    Cost: N + M + K extra V1 read calls (where N/M/K are the number of
    attached person/company/opportunity IDs) plus 1 cached /auth/whoami call.
    Use ``--skip-duplicate-check`` for high-fan-out callers.
    """
    scoped_creator_id: int
    if explicit_creator_id is not None:
        scoped_creator_id = explicit_creator_id
    else:
        resolved_id = _get_cached_user_id(ctx, client)
        if resolved_id is None:
            # Can't scope by creator → checking the entire entity's note feed
            # would risk false positives across users. Bail quietly.
            return
        scoped_creator_id = resolved_id

    normalized_new = content.strip()
    if not normalized_new:
        return

    now = datetime.now(timezone.utc)
    window = timedelta(seconds=max(0, window_seconds))

    creator_id_typed = UserId(scoped_creator_id)

    def _check(entity_kwarg: str, entity_id: int, entity_label: str) -> bool:
        try:
            page = client.notes.list(
                **{entity_kwarg: entity_id},
                creator_id=creator_id_typed,
                page_size=5,
            )
        except Exception:
            # transient list failure abort the actual note write.
            return False
        for existing in getattr(page, "data", []) or []:
            existing_content = (existing.content or "").strip()
            if existing_content != normalized_new:
                continue
            created_at = getattr(existing, "created_at", None)
            if created_at is None:
                continue
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            delta = now - created_at
            if delta <= window:
                seconds = int(delta.total_seconds())
                warnings.append(
                    f"Possible duplicate of note {int(existing.id)} ({seconds}s ago) "
                    f"on {entity_label} {entity_id}. "
                    "Pass --skip-duplicate-check to suppress this check."
                )
                return True
        return False

    for pid in person_ids:
        if _check("person_id", int(pid), "person"):
            return
    for cid in company_ids:
        if _check("company_id", int(cid), "company"):
            return
    for oid in opportunity_ids:
        if _check("opportunity_id", int(oid), "opportunity"):
            return


def _note_payload(note: Note) -> dict[str, object]:
    # Convert enum values back to names for CLI display
    type_name = NoteType(note.type).name.lower().replace("_", "-")
    interaction_type_name = (
        InteractionType(note.interaction_type).name.lower().replace("_", "-")
        if note.interaction_type is not None
        else None
    )
    return {
        "id": int(note.id),
        "type": type_name,
        "creatorId": int(note.creator_id),
        "content": note.content,
        "personIds": [int(p) for p in note.person_ids],
        "associatedPersonIds": [int(p) for p in note.associated_person_ids],
        "interactionPersonIds": [int(p) for p in note.interaction_person_ids],
        "mentionedPersonIds": [int(p) for p in note.mentioned_person_ids],
        "companyIds": [int(o) for o in note.company_ids],
        "opportunityIds": [int(o) for o in note.opportunity_ids],
        "interactionId": note.interaction_id,
        "interactionType": interaction_type_name,
        "isMeeting": note.is_meeting,
        "parentId": int(note.parent_id) if note.parent_id else None,
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
    }


@category("read")
@note_group.command(name="ls", cls=RichCommand)
@click.option("--person-id", type=int, default=None, help="Filter by person id.")
@click.option("--company-id", type=int, default=None, help="Filter by company id.")
@click.option("--opportunity-id", type=int, default=None, help="Filter by opportunity id.")
@click.option("--creator-id", type=int, default=None, help="Filter by creator id.")
@click.option("--page-size", "-s", type=int, default=None, help="Page size (max 500).")
@click.option(
    "--cursor", type=str, default=None, help="Resume from cursor (incompatible with --page-size)."
)
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N results total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@output_options
@click.pass_obj
@apply_mcp_limits()
def note_ls(
    ctx: CLIContext,
    *,
    person_id: int | None,
    company_id: int | None,
    opportunity_id: int | None,
    creator_id: int | None,
    page_size: int | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """
    List notes.

    Examples:

    - `xaffinity note ls --person-id 12345`
    - `xaffinity note ls --company-id 67890 --all`
    - `xaffinity note ls --creator-id 111 --max-results 50`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        results: list[dict[str, object]] = []
        first_page = True
        page_token = cursor
        person_id_value = PersonId(person_id) if person_id is not None else None
        company_id_value = CompanyId(company_id) if company_id is not None else None
        opportunity_id_value = OpportunityId(opportunity_id) if opportunity_id is not None else None
        creator_id_value = UserId(creator_id) if creator_id is not None else None

        # Build CommandContext upfront for use in all return paths
        ctx_modifiers: dict[str, object] = {}
        if person_id is not None:
            ctx_modifiers["personId"] = person_id
        if company_id is not None:
            ctx_modifiers["companyId"] = company_id
        if opportunity_id is not None:
            ctx_modifiers["opportunityId"] = opportunity_id
        if creator_id is not None:
            ctx_modifiers["creatorId"] = creator_id
        if page_size is not None:
            ctx_modifiers["pageSize"] = page_size
        if cursor is not None:
            ctx_modifiers["cursor"] = cursor
        if max_results is not None:
            ctx_modifiers["maxResults"] = max_results
        if all_pages:
            ctx_modifiers["allPages"] = True

        cmd_context = CommandContext(
            name="note ls",
            inputs={},
            modifiers=ctx_modifiers,
        )

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

            while True:
                page = client.notes.list(
                    person_id=person_id_value,
                    company_id=company_id_value,
                    opportunity_id=opportunity_id_value,
                    creator_id=creator_id_value,
                    page_size=page_size,
                    page_token=page_token,
                )

                for idx, note in enumerate(page.data):
                    results.append(_note_payload(note))
                    if progress and task_id is not None:
                        progress.update(task_id, completed=len(results))
                    if max_results is not None and len(results) >= max_results:
                        stopped_mid_page = idx < (len(page.data) - 1)
                        if stopped_mid_page:
                            warnings.append(
                                "Results limited by --max-results. Use --all to fetch all results."
                            )
                        pagination = None
                        if page.next_cursor and not stopped_mid_page:
                            pagination = {"nextCursor": page.next_cursor, "prevCursor": None}
                        return CommandOutput(
                            data=results[:max_results],  # Direct array, not wrapped
                            context=cmd_context,
                            pagination=pagination,
                            api_called=True,
                        )

                if first_page and not all_pages and max_results is None:
                    pagination = (
                        {"nextCursor": page.next_cursor, "prevCursor": None}
                        if page.next_cursor
                        else None
                    )
                    return CommandOutput(
                        data=results,  # Direct array, not wrapped
                        context=cmd_context,
                        pagination=pagination,
                        api_called=True,
                    )
                first_page = False

                page_token = page.next_cursor
                if not page_token:
                    break

        return CommandOutput(
            data=results,  # Direct array, not wrapped
            context=cmd_context,
            pagination=None,
            api_called=True,
        )

    run_command(ctx, command="note ls", fn=fn)


@category("read")
@note_group.command(name="get", cls=RichCommand)
@click.argument("note_id", type=int)
@output_options
@click.pass_obj
def note_get(ctx: CLIContext, note_id: int) -> None:
    """
    Get a note by id.

    Example: `xaffinity note get 12345`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        note = client.notes.get(NoteId(note_id))

        cmd_context = CommandContext(
            name="note get",
            inputs={"noteId": note_id},
            modifiers={},
        )

        payload = _note_payload(note)

        # For table display, separate content from metadata for better readability
        if ctx.output != "json":
            content = payload.pop("content", None)
            data: dict[str, object] = {"note": payload}
            if content:
                # Use _text marker for clean text rendering without "Value:" wrapper
                data["Content"] = {"_text": content}
        else:
            data = {"note": payload}

        return CommandOutput(
            data=data,
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="note get", fn=fn)


@category("write")
@note_group.command(name="create", cls=RichCommand)
@click.option("--content", type=str, required=True, help="Note content.")
@click.option(
    "--type",
    "note_type",
    type=click.Choice(sorted(_NOTE_TYPE_MAP.keys())),
    default=None,
    help="Note type (plain-text, html, ai-notetaker, email-derived).",
)
@click.option("--person-id", "person_ids", multiple=True, type=int, help="Associate person id.")
@click.option(
    "--company-id",
    "company_ids",
    multiple=True,
    type=int,
    help="Associate company id.",
)
@click.option(
    "--opportunity-id",
    "opportunity_ids",
    multiple=True,
    type=int,
    help="Associate opportunity id.",
)
@click.option("--parent-id", type=int, default=None, help="Parent note id (reply).")
@click.option("--creator-id", type=int, default=None, help="Creator id override.")
@click.option(
    "--created-at",
    type=str,
    default=None,
    help="Creation timestamp (ISO-8601).",
)
@click.option(
    "--skip-duplicate-check",
    is_flag=True,
    default=False,
    help=(
        "Skip the recent-duplicate-note warning. Use for high-fan-out callers "
        "(many entity IDs) where the extra read calls per entity are too costly."
    ),
)
@click.option(
    "--duplicate-window-seconds",
    type=int,
    default=300,
    show_default=True,
    help="Seconds to look back for a content-identical duplicate by the same author.",
)
@output_options
@click.pass_obj
def note_create(
    ctx: CLIContext,
    *,
    content: str,
    note_type: str | None,
    person_ids: tuple[int, ...],
    company_ids: tuple[int, ...],
    opportunity_ids: tuple[int, ...],
    parent_id: int | None,
    creator_id: int | None,
    created_at: str | None,
    skip_duplicate_check: bool,
    duplicate_window_seconds: int,
) -> None:
    """
    Create a note attached to an entity.

    Note: The Affinity API escapes underscores in note content (e.g., "test_note"
    becomes "test\\_note"). This is server-side markdown escaping and cannot be
    prevented. If you need to search for or compare note content, account for
    this transformation.

    Examples:

    - `xaffinity note create --content "Meeting notes" --person-id 12345`
    - `xaffinity note create --content "<b>Summary</b>" --type html --company-id 67890`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if not (person_ids or company_ids or opportunity_ids or parent_id):
            raise CLIError(
                "Notes must be attached to at least one entity or parent note.",
                exit_code=2,
                error_type="usage_error",
                hint="Provide --person-id/--company-id/--opportunity-id or --parent-id.",
            )

        parsed_type = parse_choice(note_type, _NOTE_TYPE_MAP, label="note type")
        created_at_value = (
            parse_iso_datetime(created_at, label="created-at") if created_at else None
        )

        client = ctx.get_client(warnings=warnings)

        # Optional duplicate-note warning. Looks up recent notes on each
        # attached entity (scoped to the authenticated user by default) and
        # appends to ``warnings`` if a content-identical note was created
        # within the configured window. Always proceeds with the create —
        # warning is informational only, exit code unchanged.
        if (
            not skip_duplicate_check
            and not ctx.quiet
            and (person_ids or company_ids or opportunity_ids)
        ):
            _maybe_warn_duplicate_note(
                ctx=ctx,
                client=client,
                warnings=warnings,
                content=content,
                person_ids=person_ids,
                company_ids=company_ids,
                opportunity_ids=opportunity_ids,
                explicit_creator_id=creator_id,
                window_seconds=duplicate_window_seconds,
            )

        note = client.notes.create(
            NoteCreate(
                content=content,
                type=parsed_type or NoteType.PLAIN_TEXT,
                person_ids=[PersonId(pid) for pid in person_ids],
                company_ids=[CompanyId(cid) for cid in company_ids],
                opportunity_ids=[OpportunityId(oid) for oid in opportunity_ids],
                parent_id=NoteId(parent_id) if parent_id else None,
                creator_id=UserId(creator_id) if creator_id is not None else None,
                created_at=created_at_value,
            )
        )

        ctx_modifiers: dict[str, object] = {}
        if note_type:
            ctx_modifiers["type"] = note_type
        if person_ids:
            ctx_modifiers["personIds"] = list(person_ids)
        if company_ids:
            ctx_modifiers["companyIds"] = list(company_ids)
        if opportunity_ids:
            ctx_modifiers["opportunityIds"] = list(opportunity_ids)
        if parent_id:
            ctx_modifiers["parentId"] = parent_id
        if creator_id is not None:
            ctx_modifiers["creatorId"] = creator_id
        if created_at:
            ctx_modifiers["createdAt"] = created_at

        cmd_context = CommandContext(
            name="note create",
            inputs={},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(
            data={"note": _note_payload(note)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="note create", fn=fn)


@category("write")
@note_group.command(name="update", cls=RichCommand)
@click.argument("note_id", type=int)
@click.option("--content", type=str, required=True, help="Updated note content.")
@output_options
@click.pass_obj
def note_update(ctx: CLIContext, note_id: int, *, content: str) -> None:
    """Update a note."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        note = client.notes.update(NoteId(note_id), NoteUpdate(content=content))

        cmd_context = CommandContext(
            name="note update",
            inputs={"noteId": note_id},
            modifiers={"content": content},
        )

        return CommandOutput(
            data={"note": _note_payload(note)},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="note update", fn=fn)


@category("write")
@destructive
@note_group.command(name="delete", cls=RichCommand)
@click.argument("note_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def note_delete(ctx: CLIContext, note_id: int, yes: bool) -> None:
    """Delete a note."""
    if not yes:
        click.confirm(f"Delete note {note_id}?", abort=True)

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        success = client.notes.delete(NoteId(note_id))

        cmd_context = CommandContext(
            name="note delete",
            inputs={"noteId": note_id},
            modifiers={},
        )

        return CommandOutput(
            data={"success": success},
            context=cmd_context,
            api_called=True,
        )

    run_command(ctx, command="note delete", fn=fn)
