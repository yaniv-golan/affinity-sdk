"""Confirmation for commands that cannot be undone (delete, merge) or that send data out
(webhook create/update)."""

from __future__ import annotations

import sys

from .click_compat import click
from .context import CLIContext
from .errors import CLIError
from .runner import CommandFn, CommandOutput, run_command


def run_destructive(
    ctx: CLIContext,
    *,
    command: str,
    yes: bool,
    prompt: str,
    fn: CommandFn,
    why: str = "cannot be undone",
) -> None:
    """Run ``fn`` after confirmation: ``--yes``, or "y" to the prompt (typed or piped).

    The prompt goes to stderr so stdout stays clean. With no answer at all (end of input, e.g. a
    script or an MCP server without a terminal), fail with a usage error (exit 2) in the normal
    output envelope instead of aborting silently. "n" aborts with exit 1; under ``--json`` it
    also prints the error envelope (type ``aborted``) so a refusal is not an empty stdout. The
    API is never called without confirmation. A result of ``{"success": false}`` exits 1 as an
    ``api_error``; a missing key counts as success. ``why`` completes the refusal: "<command> <why>
    and needs confirmation".
    """
    if not yes:
        if sys.stdin.isatty():
            confirmed = click.confirm(prompt, err=True)
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
                        f"{command} {why} and needs confirmation; nothing was changed.",
                        exit_code=2,
                        error_type="usage_error",
                        hint="Re-run with --yes once the action is confirmed.",
                    )

                run_command(ctx, command=command, fn=refuse)
                return
            confirmed = answer.strip().lower() in ("y", "yes")
        if not confirmed:
            if ctx.output != "json":
                raise click.Abort

            def declined(_ctx: CLIContext, _warnings: list[str]) -> CommandOutput:
                raise CLIError(
                    f"{command} was not confirmed; nothing was changed.",
                    exit_code=1,
                    error_type="aborted",
                )

            run_command(ctx, command=command, fn=declined)
            return

    def checked(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        out = fn(ctx, warnings)
        # Affinity answers a delete it did not do with {"success": false}; don't exit 0 on it.
        if isinstance(out.data, dict) and out.data.get("success") is False:
            raise CLIError(
                f"Affinity reported that {command} failed; nothing was changed.",
                exit_code=1,
                error_type="api_error",
            )
        return out

    run_command(ctx, command=command, fn=checked)
