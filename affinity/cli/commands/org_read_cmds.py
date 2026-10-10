"""Org-wide V2 reads: `interaction feed`, `company|person merge-history`, `company|person
relationships`, `task ls`. Attached to the existing command groups."""

from __future__ import annotations

from typing import Any, Literal

from affinity.types import NoteId

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..decorators import category
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ._org_reads import (
    collect_pages,
    interaction_row,
    model_row,
    note_row,
    page_limit,
    pagination,
    relationship_row,
)
from ._v1_parsing import parse_date_flexible
from .company_cmds import _resolve_company_selector, company_group
from .interaction_cmds import interaction_group
from .note_cmds import note_group
from .person_cmds import _resolve_person_selector, person_group
from .task_cmds import task_group

_FEED_TYPES = {
    "email": "emails",
    "meeting": "meetings",
    "call": "calls",
    "chat-message": "chat_messages",
    "chat": "chat_messages",
}
_MERGE_STATUSES = ("in-progress", "success", "failed")


def _paging_options(func: Any) -> Any:
    for decorator in reversed(
        [
            click.option("--cursor", type=str, default=None, help="Resume from a nextCursor."),
            click.option(
                "--max-results",
                "--limit",
                "-n",
                type=int,
                default=None,
                help="Stop after N results total.",
            ),
            click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages."),
        ]
    ):
        func = decorator(func)
    return func


def _cursor_alone(cursor: str | None, **others: Any) -> None:
    given = [name for name, value in others.items() if value not in (None, False)]
    if cursor is not None and given:
        raise CLIError(
            f"--cursor can't be combined with {', '.join(given)}; the cursor carries them.",
            error_type="usage_error",
            exit_code=2,
        )


def _when(value: str | None, label: str) -> Any:
    return parse_date_flexible(value, label=label) if value else None


# ---------------------------------------------------------------------------
# interaction feed
# ---------------------------------------------------------------------------


@category("read")
@interaction_group.command(name="feed", cls=RichCommand)
@click.option(
    "--type",
    "kind",
    type=click.Choice(sorted(_FEED_TYPES)),
    required=True,
    help="email, meeting, call or chat-message (chat).",
)
@click.option(
    "--after",
    type=str,
    default=None,
    help="Sent / start time at or after (ISO date/datetime or relative, e.g. -7d).",
)
@click.option("--before", type=str, default=None, help="Sent / start time before.")
@click.option("--created-after", type=str, default=None, help="Logged in Affinity at or after.")
@click.option(
    "--updated-after",
    type=str,
    default=None,
    help="Changed at or after (items never changed are not included).",
)
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def interaction_feed(
    ctx: CLIContext,
    *,
    kind: str,
    after: str | None,
    before: str | None,
    created_after: str | None,
    updated_after: str | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List emails, meetings, calls or chat messages across the organization (V2).

    Only items the API key's user may see; an email subject they may not see shows as
    "********". Each row has up to 10 participants plus the total. For one person, company or
    opportunity use `interaction ls`. Without --all or --max-results, one page is returned.
    There is no sort option: for sync, page to the end and keep the latest times yourself.

    Examples:

    - `xaffinity interaction feed --type email --after -7d --max-results 50`

    - `xaffinity interaction feed --type meeting --after 2025-06-01 --before 2025-07-01 --all`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _cursor_alone(
            cursor,
            after=after,
            before=before,
            created_after=created_after,
            updated_after=updated_after,
        )
        method = _FEED_TYPES[kind]
        service = ctx.get_client(warnings=warnings).interactions
        list_page = getattr(service, f"list_{method}")
        times = {
            "after": _when(after, "--after"),
            "before": _when(before, "--before"),
            "created_after": _when(created_after, "--created-after"),
            "updated_after": _when(updated_after, "--updated-after"),
        }
        rows, next_cursor = collect_pages(
            (lambda: list_page(cursor=cursor))
            if cursor is not None
            else (lambda: list_page(**times, limit=page_limit(max_results))),
            lambda c: list_page(cursor=c),
            interaction_row,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        modifiers: dict[str, object] = {"type": kind}
        modifiers.update({k: v.isoformat() for k, v in times.items() if v is not None})
        if max_results is not None:
            modifiers["maxResults"] = max_results
        if all_pages:
            modifiers["allPages"] = True
        return CommandOutput(
            data={"interactions": rows},
            context=CommandContext(name="interaction feed", inputs={}, modifiers=modifiers),
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="interaction feed", fn=fn)


# ---------------------------------------------------------------------------
# merge-history (company / person)
# ---------------------------------------------------------------------------


def _merge_history_group(entity: str, parent: Any) -> None:
    @parent.group(name="merge-history", cls=RichGroup)
    def group() -> None:
        pass

    group.help = f"Past {entity} merges (needs Manage duplicates + org admin)."
    service_name = "companies" if entity == "company" else "persons"
    ls_name = f"{entity} merge-history ls"
    get_name = f"{entity} merge-history get"

    @category("read")
    @group.command(name="ls", cls=RichCommand)
    @click.option("--status", type=click.Choice(_MERGE_STATUSES), default=None, help="Status.")
    @click.option("--task-id", type=str, default=None, help="One merge task (id or task URL).")
    @_paging_options
    @output_options
    @click.pass_obj
    @apply_mcp_limits()
    def ls(
        ctx: CLIContext,
        *,
        status: str | None,
        task_id: str | None,
        cursor: str | None,
        max_results: int | None,
        all_pages: bool,
    ) -> None:
        def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
            _cursor_alone(cursor, status=status, task_id=task_id)
            service = getattr(ctx.get_client(warnings=warnings), service_name)
            rows, next_cursor = collect_pages(
                (lambda: service.list_merges(cursor=cursor))
                if cursor is not None
                else (
                    lambda: service.list_merges(
                        status=status, task_id=task_id, limit=page_limit(max_results)
                    )
                ),
                lambda c: service.list_merges(cursor=c),
                model_row,
                max_results=max_results,
                all_pages=all_pages,
                warnings=warnings,
            )
            modifiers = {k: v for k, v in (("status", status), ("taskId", task_id)) if v}
            return CommandOutput(
                data={"merges": rows},
                context=CommandContext(name=ls_name, inputs={}, modifiers=modifiers),
                pagination=pagination(next_cursor),
                api_called=True,
            )

        run_command(ctx, command=ls_name, fn=fn)

    ls.help = (
        f"List past {entity} merges, newest first (id, status, taskId, startedAt, primary and "
        f"duplicate ids, completedAt, errorMessage). Needs the Manage duplicates permission and "
        f"the organization admin role."
    )

    @category("read")
    @group.command(name="get", cls=RichCommand)
    @click.argument("merge_id", type=int)
    @output_options
    @click.pass_obj
    def get(ctx: CLIContext, *, merge_id: int) -> None:
        def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
            service = getattr(ctx.get_client(warnings=warnings), service_name)
            state = service.get_merge_state(merge_id)
            return CommandOutput(
                data={"merge": model_row(state)},
                context=CommandContext(name=get_name, inputs={"mergeId": merge_id}, modifiers={}),
                api_called=True,
            )

        run_command(ctx, command=get_name, fn=fn)

    get.help = f"Show one past {entity} merge (ids from `{entity} merge-history ls`)."


_merge_history_group("company", company_group)
_merge_history_group("person", person_group)


# ---------------------------------------------------------------------------
# relationships (company / person)
# ---------------------------------------------------------------------------


def _relationships_command(entity: str, parent: Any) -> None:
    name = f"{entity} relationships"
    service_name = "companies" if entity == "company" else "persons"
    resolve = _resolve_company_selector if entity == "company" else _resolve_person_selector

    @category("read")
    @parent.command(name="relationships", cls=RichCommand)
    @click.argument("selector", type=str)
    @click.option(
        "--min-score",
        type=click.FloatRange(0.0, 1.0),
        default=None,
        help="Only relationships at least this strong (0.0-1.0).",
    )
    @click.option(
        "--order",
        type=click.Choice(["desc", "asc"]),
        default="desc",
        show_default=True,
        help="Strongest first (desc) or weakest first (asc).",
    )
    @_paging_options
    @output_options
    @click.pass_obj
    @apply_mcp_limits()
    def command(
        ctx: CLIContext,
        *,
        selector: str,
        min_score: float | None,
        order: str,
        cursor: str | None,
        max_results: int | None,
        all_pages: bool,
    ) -> None:
        def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
            _cursor_alone(cursor, min_score=min_score, order=None if order == "desc" else order)
            client = ctx.get_client(warnings=warnings)
            entity_id, resolved = resolve(client=client, selector=selector, cache=ctx.session_cache)
            service = getattr(client, service_name)
            rows, next_cursor = collect_pages(
                (lambda: service.list_relationships(entity_id, cursor=cursor))
                if cursor is not None
                else (
                    lambda: service.list_relationships(
                        entity_id,
                        min_score=min_score,
                        order=order,
                        limit=page_limit(max_results),
                    )
                ),
                lambda c: service.list_relationships(entity_id, cursor=c),
                relationship_row,
                max_results=max_results,
                all_pages=all_pages,
                warnings=warnings,
            )
            modifiers: dict[str, object] = {}
            if min_score is not None:
                modifiers["minScore"] = min_score
            if order != "desc":
                modifiers["order"] = order
            return CommandOutput(
                data={"relationships": rows},
                context=CommandContext(
                    name=name,
                    inputs={f"{entity}Id": int(entity_id)},
                    modifiers=modifiers,
                ),
                pagination=pagination(next_cursor),
                resolved=resolved,
                api_called=True,
            )

        run_command(ctx, command=name, fn=fn)

    who = "this company's people" if entity == "company" else "this person"
    command.help = (
        f"Relationships between your team and {who}, strongest first: "
        "person1, person2, interactionScore (0.0-1.0) and linkedInConnectedOn. LinkedIn-only "
        "relationships have score 0, so any --min-score above 0 drops them. Needs Affinity API "
        "version 2026-07-15 or newer."
    )


_relationships_command("company", company_group)
_relationships_command("person", person_group)


# ---------------------------------------------------------------------------
# task ls (merge tasks)
# ---------------------------------------------------------------------------


@category("read")
@task_group.command(name="ls", cls=RichCommand)
@click.option(
    "--kind",
    type=click.Choice(["company-merge", "person-merge"]),
    required=True,
    help="Which tasks to list.",
)
@click.option("--status", type=click.Choice(_MERGE_STATUSES), default=None, help="Status.")
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def task_ls(
    ctx: CLIContext,
    *,
    kind: str,
    status: str | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List company or person merge tasks, newest first.

    Each task has id, status and resultsSummary. Needs the Manage duplicates permission and the
    organization admin role. For one task use `xaffinity task get <taskUrl>`.

    Example:

    - `xaffinity task ls --kind company-merge --status failed`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _cursor_alone(cursor, status=status)
        tasks = ctx.get_client(warnings=warnings).tasks
        entity: Literal["company", "person"] = "company" if kind == "company-merge" else "person"
        rows, next_cursor = collect_pages(
            (lambda: tasks.list_merge_tasks(entity, cursor=cursor))
            if cursor is not None
            else (
                lambda: tasks.list_merge_tasks(entity, status=status, limit=page_limit(max_results))
            ),
            lambda c: tasks.list_merge_tasks(entity, cursor=c),
            model_row,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        modifiers: dict[str, object] = {"kind": kind}
        if status:
            modifiers["status"] = status
        return CommandOutput(
            data={"tasks": rows},
            context=CommandContext(name="task ls", inputs={}, modifiers=modifiers),
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="task ls", fn=fn)


# ---------------------------------------------------------------------------
# note feed / note replies (V2)
# ---------------------------------------------------------------------------


@category("read")
@note_group.command(name="feed", cls=RichCommand)
@click.option("--created-after", type=str, default=None, help="Created at or after.")
@click.option("--created-before", type=str, default=None, help="Created before.")
@click.option("--updated-after", type=str, default=None, help="Changed at or after.")
@click.option("--creator-id", type=int, default=None, help="Only notes by this person.")
@click.option(
    "--with-attached",
    is_flag=True,
    help="Add reply counts and the attached company/person/opportunity ids (with totals).",
)
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def note_feed(
    ctx: CLIContext,
    *,
    created_after: str | None,
    created_before: str | None,
    updated_after: str | None,
    creator_id: int | None,
    with_attached: bool,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List notes across the organization, newest first (V2).

    Rows: id, type (entities, interaction, ai-notetaker), creator, createdAt, updatedAt, content
    (HTML), mentionedPersonIds, interactionId, transcriptId; --with-attached adds repliesCount and
    companyIds/personIds/opportunityIds with totals. Replies are not included (`note replies`).
    For one person, company or opportunity use `note ls`. Times filter whole seconds.

    Examples:

    - `xaffinity note feed --created-after -7d --max-results 50`

    - `xaffinity note feed --creator-id 123 --with-attached`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _cursor_alone(
            cursor,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            creator_id=creator_id,
            with_attached=with_attached or None,
        )
        notes = ctx.get_client(warnings=warnings).notes
        times = {
            "created_after": _when(created_after, "--created-after"),
            "created_before": _when(created_before, "--created-before"),
            "updated_after": _when(updated_after, "--updated-after"),
        }
        rows, next_cursor = collect_pages(
            (lambda: notes.list_v2(cursor=cursor))
            if cursor is not None
            else (
                lambda: notes.list_v2(
                    **times,
                    creator_id=creator_id,
                    includes=True if with_attached else None,
                    limit=page_limit(max_results),
                )
            ),
            lambda c: notes.list_v2(cursor=c),
            note_row,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        modifiers: dict[str, object] = {k: v.isoformat() for k, v in times.items() if v}
        if creator_id is not None:
            modifiers["creatorId"] = creator_id
        if with_attached:
            modifiers["withAttached"] = True
        return CommandOutput(
            data={"notes": rows},
            context=CommandContext(name="note feed", inputs={}, modifiers=modifiers),
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="note feed", fn=fn)


@category("read")
@note_group.command(name="replies", cls=RichCommand)
@click.argument("note_id", type=int)
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def note_replies(
    ctx: CLIContext,
    *,
    note_id: int,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List the replies to a note (V2): id, type, creator, content, parentId, createdAt.

    Example:

    - `xaffinity note replies 12345`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        notes = ctx.get_client(warnings=warnings).notes
        rows, next_cursor = collect_pages(
            (lambda: notes.list_replies(NoteId(note_id), cursor=cursor))
            if cursor is not None
            else (lambda: notes.list_replies(NoteId(note_id), limit=page_limit(max_results))),
            lambda c: notes.list_replies(NoteId(note_id), cursor=c),
            lambda n: note_row(n, reply=True),
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        return CommandOutput(
            data={"replies": rows},
            context=CommandContext(name="note replies", inputs={"noteId": note_id}, modifiers={}),
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="note replies", fn=fn)
