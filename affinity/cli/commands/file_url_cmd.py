"""
file-url command: Get presigned download URL for a file.

This command is primarily used by the MCP get-file-url tool.
"""

from __future__ import annotations

from affinity.types import FileId

from ..click_compat import RichCommand, click
from ..context import CLIContext
from ..decorators import category
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command


@category("read")
@click.command(name="file-url", cls=RichCommand)
@click.argument("file_id", type=int)
@output_options
@click.pass_obj
def file_url_cmd(ctx: CLIContext, file_id: int) -> None:
    """Get presigned download URL for a file.

    Returns a presigned URL that can be used to download the file
    without authentication. The URL is valid for approximately 60 seconds.

    To find a file by its contents, use `file search`.

    Example:
        xaffinity file-url 9192757
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        presigned = client.files.get_download_url(FileId(file_id))

        cmd_context = CommandContext(
            name="file-url",
            inputs={"fileId": file_id},
            modifiers={},
        )

        data = {
            "fileId": presigned.file_id,
            "name": presigned.name,
            "size": presigned.size,
            "contentType": presigned.content_type,
            "url": presigned.url,
            "expiresIn": presigned.expires_in,
            "expiresAt": presigned.expires_at.isoformat(),
        }

        warnings.append(f"Presigned URL expires in {presigned.expires_in} seconds")

        return CommandOutput(
            data=data,
            context=cmd_context,
            warnings=warnings,
            api_called=True,
            rate_limit=client.rate_limits.snapshot(),
        )

    run_command(ctx, command="file-url", fn=fn)
