from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar, cast

from .click_compat import click
from .context import CLIContext, OutputFormat
from .errors import CLIError

F = TypeVar("F", bound=Callable[..., object])


def _get_existing_source(obj: CLIContext) -> str:
    """Get description of what set the current output value."""
    if obj._output_source:
        return obj._output_source
    if obj.output:
        return f"--output {obj.output}"
    return "unknown"


def _set_output(ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    """Callback for --output option. Tracks source and detects conflicts."""
    if value is None:
        return value
    obj = ctx.obj
    if isinstance(obj, CLIContext):
        source = f"--output {value}"
        if obj.output is not None and obj.output != value:
            obj._output_format_conflict = (source, _get_existing_source(obj))
        else:
            obj.output = cast(OutputFormat, value)
            obj._output_source = source
    return value


def _set_json(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
    """Callback for --json flag. Tracks source and detects conflicts."""
    if not value:
        return value
    obj = ctx.obj
    if isinstance(obj, CLIContext):
        if obj.output is not None and obj.output != "json":
            obj._output_format_conflict = ("--json", _get_existing_source(obj))
        else:
            obj.output = "json"
            obj._output_source = "--json"
    return value


def _set_csv(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
    """Callback for --csv flag. Tracks source and detects conflicts."""
    if not value:
        return value
    obj = ctx.obj
    if isinstance(obj, CLIContext):
        if obj.output is not None and obj.output != "csv":
            obj._output_format_conflict = ("--csv", _get_existing_source(obj))
        else:
            obj.output = "csv"
            obj._output_source = "--csv"
    return value


def _validate_output(ctx: CLIContext) -> None:
    """Check for output conflicts. Raises CLIError."""
    if ctx._output_format_conflict is not None:
        requested, existing = ctx._output_format_conflict
        raise CLIError(
            f"{requested} and {existing} are mutually exclusive",
            exit_code=2,
            error_type="usage_error",
        )


def _auto_validate_output(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrapper that validates output options before command execution.

    Uses Click's current context to get CLIContext since this wrapper runs
    before @click.pass_obj injects ctx into the function arguments.

    When validation fails, emits error in appropriate format (JSON/text) before exiting.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Get CLIContext from Click's current context
        click_ctx = click.get_current_context(silent=True)
        if click_ctx is not None and isinstance(click_ctx.obj, CLIContext):
            cli_ctx = click_ctx.obj
            if cli_ctx._output_format_conflict is not None:
                requested, existing = cli_ctx._output_format_conflict
                err = CLIError(
                    f"{requested} and {existing} are mutually exclusive",
                    exit_code=2,
                    error_type="usage_error",
                )
                # Emit error in appropriate format
                _emit_validation_error(cli_ctx, err)
                raise click.exceptions.Exit(2)
        return fn(*args, **kwargs)

    return wrapper


def _emit_validation_error(ctx: CLIContext, err: CLIError) -> None:
    """Emit a validation error in the appropriate format (JSON or text)."""
    import json
    import sys
    import time

    from .context import build_result, error_info_for_exception, normalize_exception
    from .results import CommandContext

    normalized = normalize_exception(err, verbosity=ctx.verbosity)

    # Build error result
    result = build_result(
        ok=False,
        command=CommandContext(name="validation"),
        started_at=time.time(),
        data=None,
        artifacts=None,
        warnings=[],
        profile=ctx.profile,
        rate_limit=None,
        error=error_info_for_exception(normalized, verbosity=ctx.verbosity),
    )

    # Output in appropriate format
    if ctx.output == "json" or ctx.output is None:
        # JSON format for --json flag or when output not set
        payload = result.model_dump(by_alias=True, mode="json")
        meta = payload.get("meta")
        if isinstance(meta, dict) and meta.get("rateLimit") is None:
            meta.pop("rateLimit", None)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        # Text format for table/other formats
        sys.stderr.write(f"Error: {err.message}\n")
        if err.hint:
            sys.stderr.write(f"Hint: {err.hint}\n")


def csv_suboption_callback(ctx: click.Context, param: click.Parameter, value: Any) -> Any:
    """Callback for CSV-related sub-options (--csv-bom, --csv-header, --csv-mode).

    Auto-enables CSV output format when any sub-option is used without explicit format.
    Detects conflicts if a non-CSV format was already specified.

    Works with both boolean flags and value options. For options with defaults
    (like --csv-header), only triggers if explicitly provided by user.
    """
    # Skip if not provided (False for flags, None for value options)
    if value is None or value is False:
        return value

    # For options with defaults, check if explicitly provided using ParameterSource
    get_source = getattr(ctx, "get_parameter_source", None)
    if callable(get_source) and param.name:
        source_enum = getattr(click.core, "ParameterSource", None)
        default_source = getattr(source_enum, "DEFAULT", None) if source_enum else None
        actual_source = get_source(param.name)
        # If using default value, don't auto-enable CSV
        if actual_source == default_source:
            return value

    obj = ctx.obj
    if isinstance(obj, CLIContext):
        flag_name = f"--{param.name.replace('_', '-')}" if param.name else "--csv-option"
        if obj.output is None:
            obj.output = "csv"
            obj._output_source = flag_name
        elif obj.output != "csv":
            obj._output_format_conflict = (flag_name, _get_existing_source(obj))
    return value


def output_options(fn: F) -> F:
    """Add output format options to a command (no CSV alias).

    Adds --output/-o and --json flags. Note: --csv is NOT included here.
    Use csv_output_options for commands that support CSV output.
    """
    fn = click.option(
        "--json",
        is_flag=True,
        help="Alias for --output json.",
        callback=_set_json,
        expose_value=False,
    )(fn)
    fn = click.option(
        "--output",
        "-o",
        type=click.Choice(["table", "json", "jsonl", "markdown", "toon", "csv"]),
        default=None,
        help="Output format (default: table). Pass --json explicitly when piping to a JSON parser.",
        callback=_set_output,
        expose_value=False,
    )(fn)
    return fn


def csv_output_options(fn: F) -> F:
    """Add output format options with --csv alias. Use for commands that support CSV.

    This decorator automatically validates output options - no manual validation needed.
    Conflicting flags like --csv --json will raise CLIError before command execution.
    """
    fn = _auto_validate_output(fn)  # type: ignore[assignment]
    fn = click.option(
        "--csv",
        is_flag=True,
        help="Alias for --output csv.",
        callback=_set_csv,
        expose_value=False,
    )(fn)
    fn = output_options(fn)
    return fn
