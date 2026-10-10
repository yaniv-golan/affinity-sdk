from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import affinity

from .click_compat import RichGroup, click
from .context import CLIContext, OutputFormat
from .logging import configure_logging, restore_logging
from .paths import get_paths

if TYPE_CHECKING:
    pass


# CI environment variables that indicate non-interactive environment
_CI_ENV_VARS = (
    "CI",  # Generic CI indicator (GitHub Actions, GitLab CI, etc.)
    "GITHUB_ACTIONS",  # GitHub Actions
    "GITLAB_CI",  # GitLab CI
    "JENKINS_URL",  # Jenkins
    "CIRCLECI",  # CircleCI
    "BUILDKITE",  # Buildkite
    "TRAVIS",  # Travis CI
    "TF_BUILD",  # Azure Pipelines
    "AZURE_PIPELINES",  # Azure Pipelines (alternative)
    "CODEBUILD_BUILD_ID",  # AWS CodeBuild
    "TEAMCITY_VERSION",  # TeamCity
)


def _should_check_for_updates(click_ctx: click.Context, *, no_update_check: bool = False) -> bool:
    """Determine if update check should run."""
    ctx: CLIContext | None = click_ctx.obj

    # Skip during shell completion (resilient_parsing=True means Click is parsing
    # for tab completion, not actual execution)
    if click_ctx.resilient_parsing:
        return False

    # Honor explicit --no-update-check flag
    if no_update_check:
        return False

    # Never check if quiet mode
    if ctx and ctx.quiet:
        return False

    # Never check for JSON output (likely automated)
    if ctx and ctx.output == "json":
        return False

    # Never check in CI environments
    if any(os.environ.get(var) for var in _CI_ENV_VARS):
        return False

    # Honor explicit opt-out environment variable
    if os.environ.get("XAFFINITY_NO_UPDATE_CHECK"):
        return False

    # Check user preference (default: enabled)
    if ctx and not ctx.update_check_enabled:
        return False

    # Check update_notify mode
    if ctx:
        mode = ctx.update_notify_mode
        if mode == "never":
            return False
        if mode == "always":
            return True
        # mode == "interactive" (default): check TTY
        return sys.stderr.isatty()

    # No context - default to interactive check
    return sys.stderr.isatty()


def _run_update_check_on_exit(state_dir: Path) -> None:
    """Run update check after command completion. Accepts Path, not context."""
    try:
        from .update_check import check_for_update_interactive

        check_for_update_interactive(state_dir)
    except Exception:
        pass  # Never crash on update check failure


def _json_requested_in_args(args: list[str]) -> bool:
    """Check if --json or --output json appears in raw CLI args."""
    if "--json" in args:
        return True
    for i, arg in enumerate(args):
        if arg == "--output" and i + 1 < len(args) and args[i + 1] == "json":
            return True
        if arg == "--output=json":
            return True
    return False


def _emit_click_error_as_json(exc: click.ClickException) -> None:
    """Emit a JSON error envelope for a Click-level exception."""
    import time

    from .context import build_result
    from .results import CommandContext, ErrorInfo
    from .runner import _emit_json

    error_type = "usage_error" if isinstance(exc, click.UsageError) else "error"
    msg = exc.format_message()
    hint = "Run 'xaffinity -h' to see available commands." if "No such command" in msg else None

    result = build_result(
        ok=False,
        command=CommandContext(name="xaffinity"),
        started_at=time.time(),
        data=None,
        warnings=[],
        profile=None,
        rate_limit=None,
        error=ErrorInfo(type=error_type, message=msg, hint=hint),
    )
    _emit_json(result)


class _RootGroupMixin:
    """Mixin that adds --help --json support to the root CLI group."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Override to support --help --json for machine-readable output."""
        # Check if --json flag is present in args
        if "--json" in sys.argv:
            from .help_json import emit_help_json_and_exit

            emit_help_json_and_exit(ctx)

        # Standard help output
        super().format_help(ctx, formatter)  # type: ignore[misc]

    def main(self, *args: Any, **kwargs: Any) -> Any:
        """Override to handle --help --json and JSON error envelopes."""
        # Resolve effective args (explicit args from caller, or sys.argv)
        raw_args: list[str] | None = args[0] if args else kwargs.get("args")
        effective_args = list(raw_args) if raw_args is not None else sys.argv[1:]

        # Handle --help --json early (existing logic)
        if ("--help" in effective_args or "-h" in effective_args) and "--json" in effective_args:
            with self.make_context("xaffinity", []) as ctx:  # type: ignore[attr-defined]
                from .help_json import emit_help_json_and_exit

                emit_help_json_and_exit(ctx)

        # When JSON is requested, force standalone_mode=False so Click re-raises
        # ClickException and Abort instead of printing plain text + sys.exit().
        if _json_requested_in_args(effective_args):
            caller_standalone = kwargs.get("standalone_mode", True)
            kwargs["standalone_mode"] = False
            try:
                rv = super().main(*args, **kwargs)  # type: ignore[misc]
                if caller_standalone:
                    raise SystemExit(0 if rv is None else rv)
                return rv
            except click.ClickException as exc:
                if caller_standalone:
                    _emit_click_error_as_json(exc)
                    raise SystemExit(exc.exit_code) from exc
                raise
            except click.Abort as abort:
                if caller_standalone:
                    raise SystemExit(1) from abort
                raise

        return super().main(*args, **kwargs)  # type: ignore[misc]


# Create RootGroup by mixing in the JSON help behavior with RichGroup
# This approach satisfies mypy since the base class is determined at import time
RootGroup: type[click.Group] = type("RootGroup", (_RootGroupMixin, RichGroup), {})


@click.group(
    name="xaffinity",
    invoke_without_command=True,
    cls=RootGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--output",
    type=click.Choice(["table", "json"]),
    default=None,
    help="Output format (table or json).",
)
@click.option("--json", "json_flag", is_flag=True, help="Alias for --output json.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress non-essential stderr output.")
@click.option("-v", "verbose", count=True, help="Increase verbosity (-v, -vv).")
@click.option("--pager/--no-pager", default=None, help="Page table / long output when interactive.")
@click.option(
    "--all-columns",
    is_flag=True,
    help="Show all table columns (disable auto-limiting based on terminal width).",
)
@click.option(
    "--max-columns",
    type=int,
    default=None,
    help="Limit table output to N columns (default: auto based on terminal width).",
)
@click.option(
    "--progress/--no-progress",
    default=None,
    help="Force enable/disable progress bars (stderr).",
)
@click.option("--profile", type=str, default=None, help="Config profile name.")
@click.option("--dotenv/--no-dotenv", default=False, help="Opt-in .env loading.")
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False),
    default=".env",
    help="Path to .env file (used with --dotenv).",
)
@click.option(
    "--api-key-file",
    type=str,
    default=None,
    help="Read API key from file (or '-' for stdin).",
)
@click.option("--api-key-stdin", is_flag=True, help="Alias for --api-key-file -.")
@click.option(
    "--api-version",
    "api_version",
    type=str,
    default=None,
    metavar="VERSION",
    help=(
        "Affinity V2 API version, e.g. 2026-09-17, or 'current' (newest). "
        "Default: your API key's default version (also: AFFINITY_API_VERSION, "
        "profile api_version)."
    ),
)
@click.option("--timeout", type=float, default=None, help="Per-request timeout in seconds.")
@click.option(
    "--max-retries",
    type=int,
    default=3,
    show_default=True,
    help="Maximum retries for rate-limited requests.",
)
@click.option(
    "--beta",
    is_flag=True,
    help="Enable beta V2 endpoints. No current command requires it (merges are GA).",
)
@click.option(
    "--readonly",
    is_flag=True,
    help="Disallow write operations (safety guard; affects all SDK calls).",
)
@click.option(
    "--trace",
    is_flag=True,
    help="Trace request/response/error events to stderr (safe redaction).",
)
@click.option(
    "--log-file", type=click.Path(dir_okay=False), default=None, help="Override log file path."
)
@click.option("--no-log-file", is_flag=True, help="Disable file logging explicitly.")
@click.option(
    "--session-cache",
    type=click.Path(file_okay=False),
    default=None,
    help="Enable session caching using the specified directory.",
)
@click.option("--no-cache", is_flag=True, help="Disable session caching.")
@click.option("--no-update-check", is_flag=True, help="Disable update check for this invocation.")
@click.version_option(version=affinity.__version__, prog_name="xaffinity")
@click.pass_context
def cli(
    click_ctx: click.Context,
    *,
    output: str | None,
    json_flag: bool,
    quiet: bool,
    verbose: int,
    pager: bool | None,
    all_columns: bool,
    max_columns: int | None,
    progress: bool | None,
    profile: str | None,
    dotenv: bool,
    env_file: str,
    api_key_file: str | None,
    api_key_stdin: bool,
    api_version: str | None,
    timeout: float | None,
    max_retries: int,
    beta: bool,
    readonly: bool,
    trace: bool,
    log_file: str | None,
    no_log_file: bool,
    session_cache: str | None,
    no_cache: bool,
    no_update_check: bool,
) -> None:
    # Validate numeric options (Bug #33, #34)
    if timeout is not None and timeout <= 0:
        raise click.BadParameter("must be positive", param_hint="'--timeout'")
    if max_columns is not None and max_columns <= 0:
        raise click.BadParameter("must be positive", param_hint="'--max-columns'")
    if api_version is not None:
        from affinity.api_versions import normalize_affinity_api_version
        from affinity.exceptions import ConfigurationError

        try:
            normalize_affinity_api_version(api_version)
        except ConfigurationError as exc:
            raise click.BadParameter(exc.message, param_hint="'--api-version'") from exc
    # The CLI reports an unknown API version in its own warnings list (see
    # CLIContext.resolve_api_version); drop the SDK's duplicate Python warning.
    warnings.filterwarnings("ignore", message="Unknown Affinity API version")
    # Detect whether --env-file was explicitly provided vs default
    env_file_is_explicit = False
    get_source = getattr(click_ctx, "get_parameter_source", None)
    if callable(get_source):
        source_enum = getattr(click.core, "ParameterSource", None)
        default_source = getattr(source_enum, "DEFAULT", None) if source_enum else None
        env_file_src = get_source("env_file")
        if env_file_src not in (None, default_source):
            env_file_is_explicit = True

    # If user explicitly provided --env-file, implicitly enable dotenv.
    if env_file_is_explicit:
        dotenv = True

    # When dotenv is active with default --env-file, search upward for .env
    if dotenv and not env_file_is_explicit:
        from dotenv import find_dotenv  # type: ignore[import-not-found]

        found = find_dotenv(usecwd=True)
        if found:
            env_file = found

    # Validate env file exists when dotenv is enabled (Bug #40)
    if dotenv and not Path(env_file).exists():
        raise click.BadParameter(f"file not found: {env_file}", param_hint="'--env-file'")

    if click_ctx.invoked_subcommand is None:
        # No args: show help; no network calls.
        click.echo(click_ctx.get_help())
        raise click.exceptions.Exit(0)

    # Detect global-level conflict: --json and --output are mutually exclusive
    if json_flag and output is not None:
        raise click.UsageError("--json and --output are mutually exclusive")

    # Resolve output format and track source
    out: OutputFormat | None = None
    output_source: str | None = None
    if json_flag:
        out = "json"
        output_source = "--json"
    elif output is not None:
        # output comes from Click's Choice validator, so it's a valid OutputFormat
        out = cast(OutputFormat, output)
        output_source = f"--output {output}"

    progress_mode: Literal["auto", "always", "never"] = "auto"
    if progress is True:
        progress_mode = "always"
    if progress is False:
        progress_mode = "never"
    if trace and progress is None:
        progress_mode = "never"

    paths = get_paths()
    effective_log_file = Path(log_file) if log_file else paths.log_file
    enable_log_file = not no_log_file

    # Set session cache environment variable if --session-cache flag is passed
    # This ensures SessionCacheConfig picks up the value via its standard environment check
    if session_cache:
        os.environ["AFFINITY_SESSION_CACHE"] = session_cache

    click_ctx.obj = CLIContext(
        output=out,
        quiet=quiet,
        verbosity=verbose,
        pager=pager,
        progress=progress_mode,
        profile=profile,
        dotenv=dotenv,
        env_file=Path(env_file),
        api_key_file=api_key_file,
        api_key_stdin=api_key_stdin,
        timeout=timeout,
        max_retries=max_retries,
        enable_beta_endpoints=beta,
        readonly=readonly,
        trace=trace,
        log_file=effective_log_file,
        enable_log_file=enable_log_file,
        all_columns=all_columns,
        max_columns=max_columns,
        api_version=api_version,
        _paths=paths,
        _output_source=output_source,
    )

    # Set no_cache flag on context
    if no_cache:
        click_ctx.obj._no_cache = True

    click_ctx.call_on_close(click_ctx.obj.close)

    previous_logging = configure_logging(
        verbosity=verbose,
        log_file=effective_log_file,
        enable_file=enable_log_file,
        api_key_for_redaction=None,
    )
    click_ctx.call_on_close(lambda: restore_logging(previous_logging))

    # Register update check to run after command completes
    if _should_check_for_updates(click_ctx, no_update_check=no_update_check):
        state_dir = paths.state_dir
        # Use lambda to capture state_dir value, not context reference
        # (context object may be invalidated by the time cleanup runs)
        click_ctx.call_on_close(lambda sd=state_dir: _run_update_check_on_exit(state_dir=sd))


# Register commands
from .commands.company_cmds import company_group as _company_group
from .commands.completion_cmd import completion_cmd as _completion_cmd
from .commands.config_cmds import config_group as _config_group
from .commands.entry_cmds import entry_group as _entry_group
from .commands.field_cmds import field_group as _field_group
from .commands.file_cmds import file_group as _file_group
from .commands.file_url_cmd import file_url_cmd as _file_url_cmd
from .commands.interaction_cmds import interaction_group as _interaction_group
from .commands.list_cmds import list_group as _list_group
from .commands.note_cmds import note_group as _note_group
from .commands.opportunity_cmds import opportunity_group as _opportunity_group
from .commands.person_cmds import person_group as _person_group
from .commands.query_cmd import query_cmd as _query_cmd
from .commands.relationship_strength_cmds import (
    relationship_strength_group as _relationship_strength_group,
)
from .commands.reminder_cmds import reminder_group as _reminder_group
from .commands.resolve_url_cmd import resolve_url_cmd as _resolve_url_cmd
from .commands.session_cmds import session_group as _session_group
from .commands.task_cmds import task_group as _task_group
from .commands.transcript_cmds import transcript_group as _transcript_group
from .commands.version_cmd import version_cmd as _version_cmd
from .commands.whoami_cmd import whoami_cmd as _whoami_cmd

cli.add_command(_completion_cmd)
cli.add_command(_version_cmd)
cli.add_command(_config_group)
cli.add_command(_whoami_cmd)
cli.add_command(_file_group)
cli.add_command(_transcript_group)

# Org-wide V2 reads attach commands to the interaction/company/person/task groups
from .commands import org_read_cmds as _org_read_cmds  # noqa: F401

cli.add_command(_file_url_cmd)
cli.add_command(_resolve_url_cmd)
cli.add_command(_person_group)
cli.add_command(_company_group)
cli.add_command(_opportunity_group)
cli.add_command(_list_group)
cli.add_command(_entry_group)
cli.add_command(_note_group)
cli.add_command(_reminder_group)
cli.add_command(_interaction_group)
cli.add_command(_field_group)
cli.add_command(_relationship_strength_group)
cli.add_command(_session_group)
cli.add_command(_task_group)
cli.add_command(_query_cmd)
