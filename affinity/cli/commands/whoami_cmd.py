from __future__ import annotations

from ..click_compat import RichCommand, click
from ..context import CLIContext
from ..decorators import category
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ..serialization import serialize_model_for_cli


@category("read")
@click.command(name="whoami", cls=RichCommand)
@output_options
@click.pass_obj
def whoami_cmd(ctx: CLIContext) -> None:
    """Show current authenticated user information."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        who = client.whoami()
        # The key's default Affinity V2 API version: whoami itself is an unversioned V2
        # call unless a version is pinned, in which case one extra unversioned call asks.
        if client.affinity_api_version is None:
            key_default = client._http.last_affinity_api_version
        else:
            key_default = client.get_key_default_api_version()

        cmd_context = CommandContext(
            name="whoami",
            inputs={},
            modifiers={},
        )

        data = serialize_model_for_cli(who)
        if isinstance(data, dict):
            data["keyDefaultApiVersion"] = key_default

        return CommandOutput(
            data=data,
            context=cmd_context,
            warnings=warnings,
            api_called=True,
        )

    run_command(ctx, command="whoami", fn=fn)
