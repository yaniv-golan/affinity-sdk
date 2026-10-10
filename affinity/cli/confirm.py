"""Confirmation for commands that cannot be undone (delete, merge)."""

from __future__ import annotations

import sys

from .click_compat import click
from .context import CLIContext
from .errors import CLIError
from .runner import CommandFn, CommandOutput, run_command


def run_destructive(
    ctx: CLIContext, *, command: str, yes: bool, prompt: str, fn: CommandFn
) -> None:
    """Run ``fn`` after confirmation: ``--yes``, or "y" to the prompt (typed or piped).

    The prompt goes to stderr so stdout stays clean. With no answer at all (end of input, e.g. a
    script or an MCP server without a terminal), fail with a usage error (exit 2) in the normal
    output envelope instead of aborting silently. "n" aborts as before. The API is never called
    without confirmation.
    """
    if not yes:
        if sys.stdin.isatty():
            click.confirm(prompt, abort=True, err=True)
        else:
            # Read the piped answer ourselves: click's prompt treats end of input differently
            # across versions (8.1 returns "", 8.2+ aborts), and end of input must not look
            # like "n".
            click.echo(f"{prompt} [y/N]: ", err=True, nl=False)
            answer = sys.stdin.readline()
            click.echo(err=True)
            if not answer:

                def refuse(_ctx: CLIContext, _warnings: list[str]) -> CommandOutput:
                    raise CLIError(
                        f"{command} cannot be undone and needs confirmation; nothing was changed.",
                        exit_code=2,
                        error_type="usage_error",
                        hint="Re-run with --yes once the action is confirmed.",
                    )

                run_command(ctx, command=command, fn=refuse)
                return
            if answer.strip().lower() not in ("y", "yes"):
                raise click.Abort
    run_command(ctx, command=command, fn=fn)
