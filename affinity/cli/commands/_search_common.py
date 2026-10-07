"""Shared helpers for the search commands (`note search`, `file search`, `company search`).

The search endpoints cap results at 100 per request and have no pagination, so these
commands deliberately do NOT use ``@apply_mcp_limits`` (it injects a default of 1000);
they clamp ``--max-results`` to 100 themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from affinity.services.search import LIMIT_MAX

from ..click_compat import click
from ..context import CLIContext
from ..errors import CLIError

F = TypeVar("F", bound=Callable[..., Any])

# Table cells show this much of a preview; JSON and the other formats get it in full.
TABLE_PREVIEW_CHARS = 200


def max_results_option(default_hint: int) -> Callable[[F], F]:
    """``--max-results/--limit/-n`` (1+), clamped to the API maximum of 100."""
    return click.option(
        "--max-results",
        "--limit",
        "-n",
        "max_results",
        type=click.IntRange(1, None),
        default=None,
        help=f"Maximum results (1-{LIMIT_MAX}; API default {default_hint}). "
        f"Values above {LIMIT_MAX} are clamped.",
    )


def clamp_limit(max_results: int | None, warnings: list[str]) -> int | None:
    if max_results is not None and max_results > LIMIT_MAX:
        warnings.append(
            f"--max-results {max_results} exceeds the search maximum; using {LIMIT_MAX}."
        )
        return LIMIT_MAX
    return max_results


def usage_error(message: str, **details: Any) -> CLIError:
    return CLIError(
        message,
        exit_code=2,
        error_type="usage_error",
        details=details or None,
    )


def check_sdk_args(build: Callable[[], object]) -> None:
    """Run an SDK body builder up front so argument errors exit 2 before any API call."""
    try:
        build()
    except ValueError as e:
        raise usage_error(str(e)) from e


def is_table_output(ctx: CLIContext) -> bool:
    return (ctx.output or "table") == "table"


def trim_preview(text: str | None, ctx: CLIContext) -> str | None:
    """Collapse whitespace and trim to ``TABLE_PREVIEW_CHARS`` for table output only."""
    if text is None or not is_table_output(ctx):
        return text
    flat = " ".join(text.split())
    if len(flat) <= TABLE_PREVIEW_CHARS:
        return flat
    return flat[: TABLE_PREVIEW_CHARS - 1].rstrip() + "…"
