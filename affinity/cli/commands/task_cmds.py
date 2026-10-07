from __future__ import annotations

from affinity.models.secondary import MergeTask

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..decorators import category
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli


@click.group(name="task", cls=RichGroup)
def task_group() -> None:
    """Poll async tasks (e.g., merges).

    Some operations like 'company merge' and 'person merge' run asynchronously
    and return a task URL. Use these commands to check status or wait for completion.

    Example workflow:

        xaffinity company merge 123 456 --json

        # Returns {"taskUrl": "https://api.affinity.co/v2/tasks/..."}

        xaffinity task wait "https://api.affinity.co/v2/tasks/..."
    """


def _task_payload(task: MergeTask) -> dict[str, object]:
    return serialize_model_for_cli(task)


@category("read")
@task_group.command(name="get", cls=RichCommand)
@click.argument("task_url", type=str)
@output_options
@click.pass_obj
def task_get(ctx: CLIContext, task_url: str) -> None:
    """Get current status of an async task.

    Returns task status (pending, in-progress, success, failed) without waiting.
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        task = client.tasks.get(task_url)
        payload = _task_payload(task)

        cmd_context = CommandContext(
            name="task get",
            inputs={"taskUrl": task_url},
            modifiers={},
        )

        return CommandOutput(data={"task": payload}, context=cmd_context, api_called=True)

    run_command(ctx, command="task get", fn=fn)


@category("read")
@task_group.command(name="wait", cls=RichCommand)
@click.argument("task_url", type=str)
@click.option(
    "--timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Maximum seconds to wait for task completion.",
)
@click.option(
    "--poll-interval",
    type=float,
    default=2.0,
    show_default=True,
    help="Initial polling interval in seconds.",
)
@click.option(
    "--max-poll-interval",
    type=float,
    default=30.0,
    show_default=True,
    help="Maximum polling interval in seconds.",
)
@output_options
@click.pass_obj
def task_wait(
    ctx: CLIContext,
    task_url: str,
    *,
    timeout: float,
    poll_interval: float,
    max_poll_interval: float,
) -> None:
    """Wait for an async task to complete.

    Polls the task URL with exponential backoff until it reaches 'success' or 'failed'.
    Returns the final task status. Raises an error if the task fails or times out.
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        task = client.tasks.wait(
            task_url,
            timeout=timeout,
            poll_interval=poll_interval,
            max_poll_interval=max_poll_interval,
        )
        payload = _task_payload(task)

        # Build CommandContext - only include non-default modifiers
        ctx_modifiers: dict[str, object] = {}
        if timeout != 300.0:
            ctx_modifiers["timeout"] = timeout
        if poll_interval != 2.0:
            ctx_modifiers["pollInterval"] = poll_interval
        if max_poll_interval != 30.0:
            ctx_modifiers["maxPollInterval"] = max_poll_interval

        cmd_context = CommandContext(
            name="task wait",
            inputs={"taskUrl": task_url},
            modifiers=ctx_modifiers,
        )

        return CommandOutput(data={"task": payload}, context=cmd_context, api_called=True)

    run_command(ctx, command="task wait", fn=fn)
