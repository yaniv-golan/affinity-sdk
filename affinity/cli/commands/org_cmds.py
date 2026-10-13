"""`team ls/get`, `user ls/get`, `meeting get`, `note attached`, `feedback send` (V2)."""

from __future__ import annotations

from typing import Any

from affinity.models.entities import ListSummary
from affinity.models.org import Team, TeamMember, UserAccount
from affinity.types import NoteId, UserId

from ..click_compat import RichCommand, RichGroup, click
from ..confirm import run_destructive
from ..context import CLIContext
from ..decorators import category, destructive
from ..mcp_limits import apply_mcp_limits
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli
from ._org_reads import collect_pages, page_limit, pagination
from .note_cmds import note_group
from .org_read_cmds import _cursor_alone, _paging_options


def _name(first: str | None, last: str | None) -> str | None:
    return f"{first or ''} {last or ''}".strip() or None


def team_row(team: Team) -> dict[str, object]:
    row: dict[str, object] = {
        "id": team.id,
        "name": team.name,
        "createdAt": team.created_at,
    }
    if team.privacy_type is not None:
        row["privacyType"] = team.privacy_type
    if team.members is not None:
        row["members"] = [
            {
                "userId": int(m.user.id),
                "name": _name(m.user.first_name, m.user.last_name),
                "email": m.user.email,
            }
            for m in team.members
        ]
        row["membersTotal"] = team.members_total
    if team.accessible_lists is not None:
        row["accessibleLists"] = [{"id": int(x.id), "name": x.name} for x in team.accessible_lists]
        row["accessibleListsTotal"] = team.accessible_lists_total
    return row


def user_row(user: UserAccount) -> dict[str, object]:
    return {
        "id": int(user.id),
        "name": _name(user.first_name, user.last_name),
        "email": user.primary_email,
        "status": str(user.status),
        "role": user.role,
        "emails": user.emails,
    }


def _team_includes(with_members: bool, with_lists: bool) -> list[str] | None:
    values = []
    if with_members:
        values.append("membersPreview")
    if with_lists:
        values.append("accessibleListsPreview")
    return values or None


def _team_preview_options(func: Any) -> Any:
    for decorator in reversed(
        [
            click.option(
                "--with-members", is_flag=True, help="Add the first 10 members and the total."
            ),
            click.option(
                "--with-lists",
                is_flag=True,
                help="Add the first 10 lists the team can access and the total.",
            ),
        ]
    ):
        func = decorator(func)
    return func


# --- team ---------------------------------------------------------------------------------------


@click.group(name="team", cls=RichGroup)
def team_group() -> None:
    """Teams (V2, beta; Affinity API 2026-09-17+)."""


@category("read")
@team_group.command(name="ls", cls=RichCommand)
@click.option("--name", default=None, help="Exact team name.")
@click.option("--name-contains", default=None, help="Part of the team name (case-insensitive).")
@click.option(
    "--sort",
    type=click.Choice(["name", "-name", "createdAt", "-createdAt"]),
    default=None,
    help="Order (default name; '-' for descending).",
)
@_team_preview_options
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def team_ls(
    ctx: CLIContext,
    *,
    name: str | None,
    name_contains: str | None,
    sort: str | None,
    with_members: bool,
    with_lists: bool,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List the teams you can see (id, name, createdAt; members/lists with the flags)."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        includes = _team_includes(with_members, with_lists)
        _cursor_alone(cursor, name=name, name_contains=name_contains, sort=sort, includes=includes)
        teams = ctx.get_client(warnings=warnings).teams
        rows, next_cursor = collect_pages(
            (lambda: teams.list(cursor=cursor))
            if cursor is not None
            else (
                lambda: teams.list(
                    name=name,
                    name_contains=name_contains,
                    order_by=sort,
                    includes=includes,
                    limit=page_limit(max_results),
                )
            ),
            lambda c: teams.list(cursor=c),
            team_row,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        modifiers = {
            k: v for k, v in (("name", name), ("nameContains", name_contains), ("sort", sort)) if v
        }
        return CommandOutput(
            data={"teams": rows},
            context=CommandContext(name="team ls", inputs={}, modifiers=modifiers),
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="team ls", fn=fn)


@category("read")
@team_group.command(name="get", cls=RichCommand)
@click.argument("team_id", type=int)
@_team_preview_options
@output_options
@click.pass_obj
def team_get(ctx: CLIContext, team_id: int, *, with_members: bool, with_lists: bool) -> None:
    """Show one team."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        team = ctx.get_client(warnings=warnings).teams.get(
            team_id, includes=_team_includes(with_members, with_lists)
        )
        return CommandOutput(
            data={"team": team_row(team)},
            context=CommandContext(name="team get", inputs={"teamId": team_id}, modifiers={}),
            api_called=True,
        )

    run_command(ctx, command="team get", fn=fn)


def _team_pages_command(name: str, method: str, key: str, row: Any, doc: str) -> None:
    command = f"team {name}"

    @category("read")
    @team_group.command(name=name, cls=RichCommand)
    @click.argument("team_id", type=int)
    @_paging_options
    @output_options
    @click.pass_obj
    @apply_mcp_limits()
    def cmd(
        ctx: CLIContext,
        team_id: int,
        *,
        cursor: str | None,
        max_results: int | None,
        all_pages: bool,
    ) -> None:
        def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
            _cursor_alone(cursor)
            service = ctx.get_client(warnings=warnings).teams
            fetch = getattr(service, method)
            rows, next_cursor = collect_pages(
                (lambda: fetch(team_id, cursor=cursor))
                if cursor is not None
                else (lambda: fetch(team_id, limit=page_limit(max_results))),
                lambda c: fetch(team_id, cursor=c),
                row,
                max_results=max_results,
                all_pages=all_pages,
                warnings=warnings,
            )
            return CommandOutput(
                data={key: rows},
                context=CommandContext(name=command, inputs={"teamId": team_id}, modifiers={}),
                pagination=pagination(next_cursor),
                api_called=True,
            )

        run_command(ctx, command=command, fn=fn)

    cmd.help = doc


def _member_row(member: TeamMember) -> dict[str, object]:
    return {
        "userId": int(member.user.id),
        "name": _name(member.user.first_name, member.user.last_name),
        "email": member.user.email,
        "addedAt": member.added_at,
    }


def _list_row(lst: ListSummary) -> dict[str, object]:
    return {"id": int(lst.id), "name": lst.name, "type": lst.type, "isPublic": lst.is_public}


_team_pages_command(
    "members",
    "list_members",
    "members",
    _member_row,
    "Every member of a team (V2, beta; Affinity API 2026-09-17+).",
)
_team_pages_command(
    "lists",
    "list_accessible_lists",
    "lists",
    _list_row,
    "The lists a team has access to (V2, beta; Affinity API 2026-09-17+).",
)


# --- user ---------------------------------------------------------------------------------------


@click.group(name="user", cls=RichGroup)
def user_group() -> None:
    """The organization's internal users (V2, beta). A user's id is also their person id."""


@category("read")
@user_group.command(name="ls", cls=RichCommand)
@click.option("--term", default=None, help="Match first name, last name or primary email.")
@click.option(
    "--status",
    type=click.Choice(["active", "invited", "deactivated"]),
    default=None,
    help="Account status.",
)
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def user_ls(
    ctx: CLIContext,
    *,
    term: str | None,
    status: str | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List the organization's users (id, name, email, status). `role` and `emails` need the
    Manage Users permission; otherwise they are null."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _cursor_alone(cursor, term=term, status=status)
        users = ctx.get_client(warnings=warnings).users
        rows, next_cursor = collect_pages(
            (lambda: users.list(cursor=cursor))
            if cursor is not None
            else (lambda: users.list(term=term, status=status, limit=page_limit(max_results))),
            lambda c: users.list(cursor=c),
            user_row,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        modifiers = {k: v for k, v in (("term", term), ("status", status)) if v}
        return CommandOutput(
            data={"users": rows},
            context=CommandContext(name="user ls", inputs={}, modifiers=modifiers),
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="user ls", fn=fn)


@category("read")
@user_group.command(name="get", cls=RichCommand)
@click.argument("user_id", type=int)
@output_options
@click.pass_obj
def user_get(ctx: CLIContext, user_id: int) -> None:
    """Show one user (the id is also the user's person id)."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        user = ctx.get_client(warnings=warnings).users.get(UserId(user_id))
        return CommandOutput(
            data={"user": user_row(user)},
            context=CommandContext(name="user get", inputs={"userId": user_id}, modifiers={}),
            api_called=True,
        )

    run_command(ctx, command="user get", fn=fn)


# --- meeting ------------------------------------------------------------------------------------


@click.group(name="meeting", cls=RichGroup)
def meeting_group() -> None:
    """Meetings (V2). For many meetings use `interaction feed --type meeting`."""


@category("read")
@meeting_group.command(name="get", cls=RichCommand)
@click.argument("meeting_id", type=int)
@output_options
@click.pass_obj
def meeting_get(ctx: CLIContext, meeting_id: int) -> None:
    """Show one meeting: title, times, organizer, creator and attendees (V2, API 2026-07-15+)."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        meeting = ctx.get_client(warnings=warnings).interactions.get_meeting(meeting_id)
        return CommandOutput(
            data={"meeting": serialize_model_for_cli(meeting)},
            context=CommandContext(
                name="meeting get", inputs={"meetingId": meeting_id}, modifiers={}
            ),
            api_called=True,
        )

    run_command(ctx, command="meeting get", fn=fn)


# --- note attached ------------------------------------------------------------------------------


@category("read")
@note_group.command(name="attached", cls=RichCommand)
@click.argument("note_id", type=int)
@output_options
@click.pass_obj
def note_attached(ctx: CLIContext, note_id: int) -> None:
    """Every company, person and opportunity a note is attached to (V2, all pages)."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        notes = ctx.get_client(warnings=warnings).notes
        nid = NoteId(note_id)
        data = {
            "companies": [
                {"id": int(c.id), "name": c.name, "domain": c.domain}
                for c in notes.iter_attached_companies(nid)
            ],
            "persons": [
                {
                    "id": int(p.id),
                    "name": _name(p.first_name, p.last_name),
                    "email": p.primary_email,
                }
                for p in notes.iter_attached_persons(nid)
            ],
            "opportunities": [
                {"id": int(o.id), "name": o.name, "listId": o.list_id}
                for o in notes.iter_attached_opportunities(nid)
            ],
        }
        return CommandOutput(
            data=data,
            context=CommandContext(name="note attached", inputs={"noteId": note_id}, modifiers={}),
            api_called=True,
        )

    run_command(ctx, command="note attached", fn=fn)


# --- feedback -----------------------------------------------------------------------------------


@click.group(name="feedback", cls=RichGroup)
def feedback_group() -> None:
    """Feedback to Affinity's product team."""


@category("write")
@destructive
@feedback_group.command(name="send", cls=RichCommand)
@click.option(
    "--type",
    "kind",
    type=click.Choice(["api", "mcp", "other"]),
    required=True,
    help="What it is about: the API, the MCP server, or something else.",
)
@click.option("--subject", required=True, help="Subject (up to 255 characters).")
@click.option("--body", required=True, help="The feedback (up to 10,000 characters).")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def feedback_send(ctx: CLIContext, *, kind: str, subject: str, body: str, yes: bool) -> None:
    """Send feedback to Affinity's product team. Asks for confirmation (--yes in scripts):
    the text leaves your organization."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        ctx.get_client(warnings=warnings).feedback.send(kind, subject, body)  # type: ignore[arg-type]
        return CommandOutput(
            data={"sent": True},
            context=CommandContext(
                name="feedback send", inputs={}, modifiers={"type": kind, "subject": subject}
            ),
            api_called=True,
        )

    run_destructive(
        ctx,
        command="feedback send",
        yes=yes,
        prompt=f'Send {kind} feedback "{subject}" to Affinity?',
        fn=fn,
        why="sends a message to Affinity's product team",
    )
