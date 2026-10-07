"""`file` command group: org-wide file operations (search).

Per-entity file listing, reading, download and upload live under
`company|person|opportunity files ...`; a presigned download URL is `file-url`.
"""

from __future__ import annotations

from typing import Any

from affinity.services.search import FILE_SEARCH_DEFAULT_LIMIT, build_keyword_search_body
from affinity.types import CompanyId

from ..click_compat import RichCommand, RichGroup, click
from ..context import CLIContext
from ..decorators import category
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ._search_common import (
    check_sdk_args,
    clamp_limit,
    max_results_option,
    trim_preview,
    usage_error,
)
from .company_cmds import _resolve_company_selector


@click.group(name="file", cls=RichGroup)
def file_group() -> None:
    """File commands (search file contents).

    For files on one record use `company|person|opportunity files ls/read/download`;
    for a download link use `file-url FILE_ID`.
    """


@category("read")
@file_group.command(name="search", cls=RichCommand)
@click.argument("prompt", type=str)
@click.option(
    "--company-id",
    "company",
    type=str,
    default=None,
    help="Only files on this company (id, URL, name:NAME or domain:DOMAIN).",
)
@click.option(
    "--file-id",
    "file_ids",
    type=int,
    multiple=True,
    help="Only search these files (repeatable, max 100). Exclusive with --company-id.",
)
@max_results_option(FILE_SEARCH_DEFAULT_LIMIT)
@output_options
@click.pass_obj
def file_search(
    ctx: CLIContext,
    prompt: str,
    *,
    company: str | None,
    file_ids: tuple[int, ...],
    max_results: int | None,
) -> None:
    """
    Search inside file contents by keyword, across all files (V2 file search).

    PROMPT is 3-500 characters. Results are ordered by relevance, one per file (at most
    100, no pagination), with the page number (when the file has pages) and the matching
    passage. Use `file-url <fileId>` or `company files read` to get the file itself.

    Examples:

    - `xaffinity file search "pitch deck"`
    - `xaffinity file search "revenue projections" --company-id 12345 -n 5`
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        if company is not None and file_ids:
            raise usage_error("--company-id and --file-id are mutually exclusive.")
        limit = clamp_limit(max_results, warnings)
        check_sdk_args(
            lambda: build_keyword_search_body(
                prompt,
                company_id=None,
                ids=list(file_ids) or None,
                ids_key="--file-id",
                limit=limit,
            )
        )

        client = ctx.get_client(warnings=warnings)
        resolved: dict[str, Any] | None = None
        company_id: CompanyId | None = None
        if company is not None:
            company_id, resolved = _resolve_company_selector(
                client=client, selector=company, cache=ctx.session_cache
            )

        hits = client.files.search(
            prompt,
            company_id=company_id,
            file_ids=list(file_ids) or None,
            limit=limit,
        )
        rows: list[dict[str, object]] = [
            {
                "fileId": int(hit.file.id),
                "name": hit.file.name,
                "pageNumber": hit.page_number,
                "preview": trim_preview(hit.preview, ctx),
            }
            for hit in hits
        ]

        modifiers: dict[str, object] = {}
        if company_id is not None:
            modifiers["companyId"] = int(company_id)
        if file_ids:
            modifiers["fileIds"] = list(file_ids)
        if limit is not None:
            modifiers["maxResults"] = limit

        return CommandOutput(
            data=rows,
            context=CommandContext(
                name="file search", inputs={"prompt": prompt}, modifiers=modifiers
            ),
            resolved=resolved,
            api_called=True,
        )

    run_command(ctx, command="file search", fn=fn)
