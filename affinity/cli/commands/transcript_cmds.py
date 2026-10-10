"""Meeting transcript commands (V2, AI Notetaker)."""

from __future__ import annotations

from affinity.models.transcripts import Transcript

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..decorators import category
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli
from ._v1_parsing import parse_date_flexible


@click.group(name="transcript", cls=RichGroup)
def transcript_group() -> None:
    """Meeting transcripts from Affinity's AI Notetaker (only those your key's user may see)."""


def _note_summary(transcript: Transcript) -> dict[str, object] | None:
    note = transcript.note
    if note is None:
        return None
    interaction = note.interaction or {}
    return {
        "noteId": int(note.id),
        "type": note.type,
        "interactionId": interaction.get("id"),
        "transcriptId": note.transcript_id,
    }


def _transcript_payload(transcript: Transcript, *, with_note: bool) -> dict[str, object]:
    return {
        "id": transcript.id,
        "createdAt": transcript.created_at,
        "languageCode": transcript.language_code,
        "note": serialize_model_for_cli(transcript.note)
        if with_note and transcript.note
        else _note_summary(transcript),
    }


@category("read")
@transcript_group.command(name="ls", cls=RichCommand)
@click.option(
    "--created-after",
    type=str,
    default=None,
    help="Only transcripts created at or after this time (ISO date/datetime or relative).",
)
@click.option(
    "--created-before",
    type=str,
    default=None,
    help="Only transcripts created before this time (ISO date/datetime or relative).",
)
@click.option(
    "--with-note", is_flag=True, help="Include each transcript's whole AI Notetaker note."
)
@click.option("--cursor", type=str, default=None, help="Resume from a previous nextCursor.")
@click.option(
    "--max-results", "--limit", "-n", type=int, default=None, help="Stop after N results total."
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch all pages.")
@output_options
@click.pass_obj
@apply_mcp_limits()
def transcript_ls(
    ctx: CLIContext,
    *,
    created_after: str | None,
    created_before: str | None,
    with_note: bool,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List meeting transcripts (metadata, no dialogue).

    Each row has id, createdAt, languageCode and a short note reference (noteId, type,
    interactionId, transcriptId); --with-note includes the whole note. Use `transcript get`
    for the dialogue. Without --all or --max-results, one page is returned with a nextCursor.

    Examples:

    - `xaffinity transcript ls --created-after -7d`

    - `xaffinity transcript ls --max-results 50`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if cursor is not None and (created_after or created_before):
            raise CLIError(
                "--cursor can't be combined with --created-after/--created-before.",
                error_type="usage_error",
                exit_code=2,
            )
        if max_results is not None and max_results < 1:
            raise CLIError(
                "--max-results must be at least 1.", error_type="usage_error", exit_code=2
            )
        after = (
            parse_date_flexible(created_after, label="--created-after") if created_after else None
        )
        before = (
            parse_date_flexible(created_before, label="--created-before")
            if created_before
            else None
        )
        modifiers: dict[str, object] = {}
        if after is not None:
            modifiers["createdAfter"] = after.isoformat()
        if before is not None:
            modifiers["createdBefore"] = before.isoformat()
        if with_note:
            modifiers["withNote"] = True
        if cursor is not None:
            modifiers["cursor"] = cursor
        if max_results is not None:
            modifiers["maxResults"] = max_results
        if all_pages:
            modifiers["allPages"] = True

        service = ctx.get_client(warnings=warnings).transcripts
        page_limit = min(max_results, 100) if max_results is not None else None
        page = (
            service.list(cursor=cursor)
            if cursor is not None
            else service.list(created_after=after, created_before=before, limit=page_limit)
        )
        rows: list[dict[str, object]] = []
        while True:
            rows.extend(_transcript_payload(t, with_note=with_note) for t in page.data)
            next_cursor = page.next_cursor
            if max_results is not None and len(rows) >= max_results:
                if len(rows) > max_results:
                    rows = rows[:max_results]
                    next_cursor = None
                    warnings.append(
                        "Results limited by --max-results. Use --all to fetch all results."
                    )
                break
            if not next_cursor or not (all_pages or max_results is not None):
                break
            page = service.list(cursor=next_cursor)

        return CommandOutput(
            data={"transcripts": rows},
            context=CommandContext(name="transcript ls", inputs={}, modifiers=modifiers),
            pagination={"nextCursor": next_cursor, "prevCursor": None} if next_cursor else None,
            api_called=True,
        )

    run_command(ctx, command="transcript ls", fn=fn)


@category("read")
@transcript_group.command(name="get", cls=RichCommand)
@click.argument("transcript_id", type=int)
@click.option(
    "--max-results",
    "--limit",
    "-n",
    type=int,
    default=None,
    help="Fetch up to N dialogue fragments (default: the preview Affinity returns).",
)
@click.option("--all", "-A", "all_pages", is_flag=True, help="Fetch every fragment.")
@output_options
@click.pass_obj
@apply_mcp_limits()
def transcript_get(
    ctx: CLIContext, *, transcript_id: int, max_results: int | None, all_pages: bool
) -> None:
    """Show a transcript: its note and its dialogue fragments.

    By default the fragments are Affinity's preview (the first ones; fragmentsTotal says how
    many there are). --max-results N or --all fetch more, in order. Each fragment has content,
    speaker, startTimestamp and endTimestamp (offsets such as 00:00:06).

    Examples:

    - `xaffinity transcript get 123`

    - `xaffinity transcript get 123 --all`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if max_results is not None and max_results < 1:
            raise CLIError(
                "--max-results must be at least 1.", error_type="usage_error", exit_code=2
            )
        service = ctx.get_client(warnings=warnings).transcripts
        transcript = service.get(transcript_id)
        fragments = transcript.fragments_preview
        if all_pages or max_results is not None:
            fragments = []
            for fragment in service.iter_fragments(transcript_id, limit=100):
                fragments.append(fragment)
                if max_results is not None and len(fragments) >= max_results:
                    break
        total = transcript.fragments_total
        if total is not None and len(fragments) < total and not all_pages:
            warnings.append(
                f"Showing {len(fragments)} of {total} fragments. Use --all to fetch every one."
            )
        modifiers: dict[str, object] = {}
        if max_results is not None:
            modifiers["maxResults"] = max_results
        if all_pages:
            modifiers["allPages"] = True
        return CommandOutput(
            data={
                "transcript": {
                    "id": transcript.id,
                    "createdAt": transcript.created_at,
                    "languageCode": transcript.language_code,
                    "note": serialize_model_for_cli(transcript.note) if transcript.note else None,
                    "fragmentsTotal": total,
                    "fragments": [serialize_model_for_cli(f) for f in fragments],
                }
            },
            context=CommandContext(
                name="transcript get", inputs={"transcriptId": transcript_id}, modifiers=modifiers
            ),
            api_called=True,
        )

    run_command(ctx, command="transcript get", fn=fn)
