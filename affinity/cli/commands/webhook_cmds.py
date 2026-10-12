"""`webhook ls/get/create/update/delete` (V2 webhooks, Affinity API 2026-07-15+, beta).

create, update and delete ask for confirmation (or --yes): a webhook sends CRM events to an
outside URL until it is deleted.
"""

from __future__ import annotations

from typing import Any

from affinity.models.secondary import Webhook, WebhookCreate, WebhookUpdate
from affinity.types import WebhookEvent, WebhookId, WebhookStatus

from ..click_compat import RichCommand, RichGroup, click
from ..confirm import run_destructive
from ..context import CLIContext
from ..decorators import category, destructive
from ..errors import CLIError
from ..mcp_limits import apply_mcp_limits
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandFn, CommandOutput, run_command
from ._org_reads import collect_pages, page_limit, pagination
from .org_read_cmds import _cursor_alone, _paging_options, _when

_EVENTS = [e.value for e in WebhookEvent]
_SENDS_DATA_OUT = "sends CRM events to an outside URL"


@click.group(name="webhook", cls=RichGroup)
def webhook_group() -> None:
    """Webhooks: CRM events delivered to a URL (V2, beta; API version 2026-07-15+).

    Without permission to manage all webhooks, you see and change only the webhooks you created.
    """


def webhook_row(hook: Webhook) -> dict[str, object]:
    return {
        "id": int(hook.id),
        "url": hook.url,
        "status": str(hook.status),
        "subscriptions": [str(e) for e in hook.subscriptions],
        # An empty subscription list means every event type
        "allEvents": not hook.subscriptions,
        "creatorId": int(hook.creator.id) if hook.creator else None,
        "createdAt": hook.created_at,
        "updatedAt": hook.updated_at,
    }


def _events_option(func: Any) -> Any:
    for decorator in reversed(
        [
            click.option(
                "--event",
                "events",
                multiple=True,
                type=click.Choice(_EVENTS),
                help="An event to receive (repeatable), e.g. list_entry.created.",
            ),
            click.option(
                "--all-events",
                is_flag=True,
                help="Receive every event type in the organization.",
            ),
        ]
    ):
        func = decorator(func)
    return func


def _subscriptions(
    events: tuple[str, ...], all_events: bool, *, required: bool
) -> tuple[list[str] | None, str | None]:
    """(subscriptions or None, usage error or None)."""
    if events and all_events:
        return None, "Pass --event (one or more) or --all-events, not both."
    if all_events:
        return [], None
    if events:
        return list(dict.fromkeys(events)), None
    if required:
        return None, (
            "Name the events with --event (repeatable), or pass --all-events to receive every "
            "event type."
        )
    return None, None


def _describe(subscriptions: list[str]) -> str:
    return "EVERY event" if not subscriptions else ", ".join(subscriptions)


@category("read")
@webhook_group.command(name="ls", cls=RichCommand)
@click.option("--id", "ids", type=int, multiple=True, help="Only these webhook ids (repeatable).")
@click.option("--created-after", type=str, default=None, help="Created at or after.")
@click.option("--created-before", type=str, default=None, help="Created before.")
@click.option("--updated-after", type=str, default=None, help="Changed at or after.")
@click.option("--updated-before", type=str, default=None, help="Changed before.")
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def webhook_ls(
    ctx: CLIContext,
    *,
    ids: tuple[int, ...],
    created_after: str | None,
    created_before: str | None,
    updated_after: str | None,
    updated_before: str | None,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """List webhooks, ordered by id (url, status, subscriptions, allEvents, creatorId).

    Without permission to manage all webhooks, only your own are listed. Dates are ISO
    dates/datetimes or relative (e.g. -7d). Without --all or --max-results, one page is returned.

    Example:

    - `xaffinity webhook ls --all`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _cursor_alone(
            cursor,
            ids=ids or None,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
        )
        service = ctx.get_client(warnings=warnings).webhooks
        rows, next_cursor = collect_pages(
            (lambda: service.list(cursor=cursor))
            if cursor is not None
            else (
                lambda: service.list(
                    ids=[WebhookId(i) for i in ids] or None,
                    created_after=_when(created_after, "--created-after"),
                    created_before=_when(created_before, "--created-before"),
                    updated_after=_when(updated_after, "--updated-after"),
                    updated_before=_when(updated_before, "--updated-before"),
                    limit=page_limit(max_results),
                )
            ),
            lambda c: service.list(cursor=c),
            webhook_row,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        modifiers = {
            k: v
            for k, v in (
                ("ids", list(ids) or None),
                ("createdAfter", created_after),
                ("createdBefore", created_before),
                ("updatedAfter", updated_after),
                ("updatedBefore", updated_before),
            )
            if v
        }
        return CommandOutput(
            data={"webhooks": rows},
            context=CommandContext(name="webhook ls", inputs={}, modifiers=modifiers),
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="webhook ls", fn=fn)


@category("read")
@webhook_group.command(name="get", cls=RichCommand)
@click.argument("webhook_id", type=int)
@output_options
@click.pass_obj
def webhook_get(ctx: CLIContext, webhook_id: int) -> None:
    """Show one webhook (not found when it is someone else's and you can't manage all)."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        hook = ctx.get_client(warnings=warnings).webhooks.get(WebhookId(webhook_id))
        return CommandOutput(
            data={"webhook": webhook_row(hook)},
            context=CommandContext(
                name="webhook get", inputs={"webhookId": webhook_id}, modifiers={}
            ),
            api_called=True,
        )

    run_command(ctx, command="webhook get", fn=fn)


@category("write")
@destructive
@webhook_group.command(name="create", cls=RichCommand)
@click.option("--url", required=True, help="Where events are delivered (unique in the org).")
@_events_option
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def webhook_create(
    ctx: CLIContext, *, url: str, events: tuple[str, ...], all_events: bool, yes: bool
) -> None:
    """Create a webhook that sends the chosen events to URL. Asks for confirmation.

    Affinity sends a test delivery to the URL first; an unreachable URL, one with credentials
    in it, or one another webhook already uses is rejected. Name the events with --event;
    --all-events sends every change in the organization. Scripts pass --yes.

    Example:

    - `xaffinity webhook create --url https://hooks.example.com/affinity --event list_entry.created --yes`
    """  # noqa: E501
    subscriptions, error = _subscriptions(events, all_events, required=True)
    if error is not None or subscriptions is None:
        _usage_error(ctx, "webhook create", error or "")
        return

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        hook = ctx.get_client(warnings=warnings).webhooks.create(
            WebhookCreate.model_validate({"url": url, "subscriptions": subscriptions})
        )
        return CommandOutput(
            data={"webhook": webhook_row(hook)},
            context=CommandContext(
                name="webhook create",
                inputs={},
                modifiers={"url": url, "subscriptions": subscriptions},
            ),
            api_called=True,
        )

    _run_checked(
        ctx,
        command="webhook create",
        yes=yes,
        prompt=f"Create a webhook sending {_describe(subscriptions)} to {url}?",
        fn=fn,
    )


@category("write")
@destructive
@webhook_group.command(name="update", cls=RichCommand)
@click.argument("webhook_id", type=int)
@click.option("--url", default=None, help="New delivery URL.")
@_events_option
@click.option(
    "--status",
    type=click.Choice([s.value for s in WebhookStatus]),
    default=None,
    help="disabled pauses delivery; active resumes it.",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def webhook_update(
    ctx: CLIContext,
    *,
    webhook_id: int,
    url: str | None,
    events: tuple[str, ...],
    all_events: bool,
    status: str | None,
    yes: bool,
) -> None:
    """Change a webhook's URL, events or status; only what you pass. Asks for confirmation.

    --event replaces the whole event list; --all-events subscribes to every event. A new URL,
    or --status active, triggers a test delivery first. Prints the webhook as read back.

    Example:

    - `xaffinity webhook update 12 --status disabled --yes`
    """
    subscriptions, error = _subscriptions(events, all_events, required=False)
    if error is not None:
        _usage_error(ctx, "webhook update", error)
        return
    changes: dict[str, object] = {
        k: v
        for k, v in (("url", url), ("subscriptions", subscriptions), ("status", status))
        if v is not None
    }
    if not changes:
        _usage_error(
            ctx,
            "webhook update",
            "Nothing to change: pass --url, --event/--all-events or --status.",
        )
        return

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        hook = ctx.get_client(warnings=warnings).webhooks.update(
            WebhookId(webhook_id), WebhookUpdate.model_validate(changes)
        )
        return CommandOutput(
            data={"webhook": webhook_row(hook)},
            context=CommandContext(
                name="webhook update", inputs={"webhookId": webhook_id}, modifiers=changes
            ),
            api_called=True,
        )

    parts = []
    if url is not None:
        parts.append(f"URL {url}")
    if subscriptions is not None:
        parts.append(f"events: {_describe(subscriptions)}")
    if status is not None:
        parts.append(f"status {status}")
    _run_checked(
        ctx,
        command="webhook update",
        yes=yes,
        prompt=f"Change webhook {webhook_id} ({'; '.join(parts)})?",
        fn=fn,
    )


@category("write")
@destructive
@webhook_group.command(name="delete", cls=RichCommand)
@click.argument("webhook_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@output_options
@click.pass_obj
def webhook_delete(ctx: CLIContext, webhook_id: int, yes: bool) -> None:
    """Delete a webhook (yours, or any if you can manage all webhooks). Asks for confirmation."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        ctx.get_client(warnings=warnings).webhooks.delete(WebhookId(webhook_id))
        return CommandOutput(
            data={"success": True},
            context=CommandContext(
                name="webhook delete", inputs={"webhookId": webhook_id}, modifiers={}
            ),
            api_called=True,
        )

    run_destructive(
        ctx, command="webhook delete", yes=yes, prompt=f"Delete webhook {webhook_id}?", fn=fn
    )


def _run_checked(ctx: CLIContext, *, command: str, yes: bool, prompt: str, fn: CommandFn) -> None:
    run_destructive(ctx, command=command, yes=yes, prompt=prompt, fn=fn, why=_SENDS_DATA_OUT)


def _usage_error(ctx: CLIContext, command: str, message: str) -> None:
    """Report a usage error in the normal output envelope, before any prompt."""

    def fail(_ctx: CLIContext, _warnings: list[str]) -> CommandOutput:
        raise CLIError(message, error_type="usage_error", exit_code=2)

    run_command(ctx, command=command, fn=fail)
