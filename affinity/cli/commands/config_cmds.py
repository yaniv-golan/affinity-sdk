from __future__ import annotations

import getpass
import os
import re
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..click_compat import RichCommand, RichGroup, click
from ..config import config_init_template
from ..context import CLIContext
from ..decorators import category
from ..errors import CLIError
from ..options import output_options
from ..runner import CommandOutput, run_command


@click.group(name="config", cls=RichGroup)
def config_group() -> None:
    """Configuration and profiles."""


@category("local")
@config_group.command(name="path", cls=RichCommand)
@output_options
@click.pass_obj
def config_path(ctx: CLIContext) -> None:
    """Show the path to the configuration file."""

    def fn(_: CLIContext, _warnings: list[str]) -> CommandOutput:
        path = ctx.paths.config_path
        return CommandOutput(data={"path": str(path), "exists": path.exists()}, api_called=False)

    run_command(ctx, command="config path", fn=fn)


@category("local")
@config_group.command(name="init", cls=RichCommand)
@click.option("--force", is_flag=True, help="Overwrite existing config file.")
@output_options
@click.pass_obj
def config_init(ctx: CLIContext, *, force: bool) -> None:
    """Create a new configuration file with template."""

    def fn(_: CLIContext, _warnings: list[str]) -> CommandOutput:
        path = ctx.paths.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        overwritten = False
        if path.exists():
            if not force:
                raise CLIError(
                    f"Config already exists: {path} (use --force to overwrite)",
                    exit_code=2,
                    error_type="usage_error",
                )
            overwritten = True

        path.write_text(config_init_template(), encoding="utf-8")
        if os.name == "posix":
            with suppress(OSError):
                path.chmod(0o600)
        return CommandOutput(
            data={"path": str(path), "created": True, "overwritten": overwritten},
            api_called=False,
        )

    run_command(ctx, command="config init", fn=fn)


# API key format validation - most Affinity keys are alphanumeric with some punctuation
_API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.]+$")


def _validate_api_key_format(api_key: str) -> bool:
    """Validate API key contains only expected characters."""
    return bool(_API_KEY_PATTERN.match(api_key)) and 10 <= len(api_key) <= 200


def _find_existing_key(ctx: CLIContext) -> tuple[bool, str | None]:
    """
    Check all sources for an existing API key.

    Returns ``(found, source)`` where source is one of ``"environment"``,
    ``"dotenv"``, ``"file"``, ``"command"``, ``"config"``, or ``None``.

    Order mirrors :meth:`affinity.cli.context.CLIContext.resolve_api_key`'s
    resolution chain so check-key's verdict matches what the auth layer
    will actually use at runtime.
    """
    # Check environment variable
    env_key = os.getenv("AFFINITY_API_KEY", "").strip()
    if env_key:
        return True, "environment"

    # Check .env file in current directory (without requiring --dotenv flag)
    # This allows check-key to discover keys even if user forgot --dotenv
    dotenv_path = ctx.env_file
    if dotenv_path.exists():
        try:
            content = dotenv_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                stripped = line.strip()
                # Match AFFINITY_API_KEY=<non-empty-value>
                if stripped.startswith("AFFINITY_API_KEY="):
                    value = stripped[len("AFFINITY_API_KEY=") :].strip()
                    # Handle quoted values
                    if (value.startswith('"') and value.endswith('"')) or (
                        value.startswith("'") and value.endswith("'")
                    ):
                        value = value[1:-1]
                    if value:  # Non-empty value
                        return True, "dotenv"
        except OSError:
            pass

    # Check AFFINITY_API_KEY_FILE — env var pointing at a file containing the key.
    # Verify the file exists and has non-empty content; permission warnings are
    # surfaced by the auth resolver at runtime, not here.
    file_path_env = os.getenv("AFFINITY_API_KEY_FILE", "").strip()
    if file_path_env:
        try:
            file_path = Path(file_path_env).expanduser()
            if file_path.is_file() and file_path.read_text(encoding="utf-8").strip():
                return True, "file"
        except OSError:
            pass

    # Check AFFINITY_API_KEY_COMMAND — env var with a shell command whose stdout
    # is the key.  We deliberately do NOT run the command here:
    #   - it can be expensive (1Password CLI, vault, network round-trips),
    #   - check-key is called eagerly by the xaffinity-cli-usage skill,
    #   - the auth resolver runs the command at session-init time and surfaces
    #     any failure there.
    # Treat env-var-set as evidence the user has chosen this resolution path.
    cmd_env = os.getenv("AFFINITY_API_KEY_COMMAND", "").strip()
    if cmd_env:
        return True, "command"

    # Check config.toml - only in [default] section for consistency with _store_in_config
    config_path = ctx.paths.config_path
    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
            # Parse section-aware: only look in [default] section
            in_default = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped == "[default]":
                    in_default = True
                elif stripped.startswith("[") and stripped.endswith("]"):
                    in_default = False
                elif in_default and re.match(r'^api_key\s*=\s*"[^"]+', stripped):
                    # Found non-empty api_key in [default] section
                    return True, "config"
        except OSError:
            pass

    return False, None


@category("local")
@config_group.command(name="check-key", cls=RichCommand)
@output_options
@click.pass_obj
def check_key(ctx: CLIContext) -> None:
    """
    Check if an API key is configured.

    Exit codes:
        0: Key found (configured)
        1: Key not found (not configured) - this is NOT an error

    This follows the pattern of `git diff --exit-code` where non-zero exit
    indicates a specific condition (difference/missing), not an error.

    Does not validate the key against the API - only checks if one exists.

    Examples:
        xaffinity config check-key
        xaffinity config check-key --json
        xaffinity config check-key && echo "Key exists"
    """
    # For human-readable output, bypass run_command to avoid the "OK" box
    if ctx.output != "json":
        key_found, source = _find_existing_key(ctx)
        if key_found:
            click.echo(f"✓ API key configured (source: {source})")
        else:
            click.echo("✗ No API key configured")
        raise click.exceptions.Exit(0 if key_found else 1)

    # For JSON output, use the normal flow
    def fn(_ctx: CLIContext, _warnings: list[str]) -> CommandOutput:
        key_found, source = _find_existing_key(ctx)

        # Build the recommended command pattern based on key source
        pattern: str | None = None
        if key_found:
            if source == "dotenv":
                pattern = "xaffinity --dotenv --readonly <command> --json"
            else:
                pattern = "xaffinity --readonly <command> --json"

        return CommandOutput(
            data={
                "configured": key_found,
                "source": source,  # "environment" | "dotenv" | "file" | "command" | "config" | None
                "pattern": pattern,  # Recommended command pattern to use
            },
            api_called=False,
            exit_code=0 if key_found else 1,
        )

    run_command(ctx, command="config check-key", fn=fn)


def _validate_key(api_key: str, warnings: list[str]) -> bool:
    """
    Validate API key by calling whoami endpoint.

    Uses lazy import of httpx - while httpx is a core dependency,
    keeping the import inside the function avoids loading it for
    commands that don't need validation (like --no-validate).
    """
    import httpx

    try:
        # Use V1 whoami endpoint for validation (simpler auth)
        response = httpx.get(
            "https://api.affinity.co/auth/whoami",
            auth=("", api_key),
            timeout=10.0,
        )
        if response.status_code == 401:
            warnings.append("API key was rejected (401 Unauthorized)")
            return False
        return response.status_code == 200
    except httpx.RequestError as e:
        warnings.append(f"Network error during validation: {e}")
        return False


def _store_in_dotenv(api_key: str, *, warnings: list[str]) -> CommandOutput:
    """Store API key in .env file in current directory."""
    env_path = Path(".env")
    gitignore_path = Path(".gitignore")

    # Read existing .env content
    lines: list[str] = []
    key_line_index: int | None = None

    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        for i, line in enumerate(lines):
            # Match AFFINITY_API_KEY= at start of line (ignore comments)
            stripped = line.strip()
            if stripped.startswith("AFFINITY_API_KEY="):
                key_line_index = i
                break

    # Update or append
    new_line = f"AFFINITY_API_KEY={api_key}"
    if key_line_index is not None:
        lines[key_line_index] = new_line
    else:
        # Add blank line separator if file has content
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Affinity API key")
        lines.append(new_line)

    # Write .env
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Ensure .env is in .gitignore
    env_existed_before = key_line_index is not None  # Had AFFINITY_API_KEY before
    gitignore_updated = _ensure_gitignore(gitignore_path)
    if gitignore_updated:
        warnings.append(f"Added .env to {gitignore_path}")
        # Warn about potential git history exposure
        if env_existed_before:
            warnings.append(
                "Warning: .env was not in .gitignore before. If it was previously committed, "
                "secrets may still be in git history. Consider running: git rm --cached .env"
            )

    return CommandOutput(
        data={
            "key_stored": True,
            "scope": "project",
            "path": str(env_path.absolute()),
            "gitignore_updated": gitignore_updated,
        },
        api_called=False,
    )


def _store_in_config(ctx: CLIContext, api_key: str) -> CommandOutput:
    """Store API key in user config.toml."""
    config_path = ctx.paths.config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Escape special characters for TOML string (Bug #21)
    # TOML basic strings use backslash escapes for: \\ \" \n \r \t
    escaped_key = (
        api_key.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )

    # Read or create config
    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")
        # Simple TOML manipulation - find [default] section and update/add api_key
        # For robustness, we use basic string manipulation rather than full TOML parsing
        # to avoid adding toml as a required dependency
        lines = content.splitlines()
        in_default = False
        key_line_index: int | None = None
        default_section_index: int | None = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "[default]":
                in_default = True
                default_section_index = i
            elif stripped.startswith("[") and stripped.endswith("]"):
                in_default = False
            # More precise matching: api_key followed by whitespace or =
            # Avoids matching api_key_backup, api_keys, etc.
            elif in_default and re.match(r"^api_key\s*=", stripped):
                key_line_index = i
                break

        if key_line_index is not None:
            # Update existing key
            lines[key_line_index] = f'api_key = "{escaped_key}"'
        elif default_section_index is not None:
            # Add key after [default] section header
            lines.insert(default_section_index + 1, f'api_key = "{escaped_key}"')
        else:
            # No [default] section - add it
            if lines and lines[-1].strip():
                lines.append("")
            lines.append("[default]")
            lines.append(f'api_key = "{escaped_key}"')

        new_content = "\n".join(lines) + "\n"
    else:
        # Create new config file
        new_content = f'[default]\napi_key = "{escaped_key}"\n'

    config_path.write_text(new_content, encoding="utf-8")

    # Set restrictive permissions on Unix
    if os.name == "posix":
        with suppress(OSError):
            config_path.chmod(0o600)

    return CommandOutput(
        data={
            "key_stored": True,
            "scope": "user",
            "path": str(config_path),
        },
        api_called=False,
    )


def _ensure_gitignore(gitignore_path: Path) -> bool:
    """Ensure .env is in .gitignore. Returns True if file was modified."""
    patterns_to_check = [".env", "*.env", ".env*"]

    if gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8")
        # Check if any pattern already covers .env
        for line in content.splitlines():
            stripped = line.strip()
            if stripped in patterns_to_check or stripped == ".env":
                return False  # Already covered

        # Append .env
        with gitignore_path.open("a", encoding="utf-8") as f:
            if not content.endswith("\n"):
                f.write("\n")
            f.write("\n# Affinity API key\n.env\n")
        return True
    else:
        # Create .gitignore with .env
        gitignore_path.write_text("# Affinity API key\n.env\n", encoding="utf-8")
        return True


@category("local")
@config_group.command(name="setup-key", cls=RichCommand)
@click.option(
    "--scope",
    type=click.Choice(["project", "user"], case_sensitive=False),
    default=None,
    help="Where to store: 'project' (.env) or 'user' (config.toml). Interactive if omitted.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing API key without prompting.",
)
@click.option(
    "--validate/--no-validate",
    default=True,
    help="Test the key against the API after storing (default: validate).",
)
@output_options
@click.pass_obj
def setup_key(ctx: CLIContext, *, scope: str | None, force: bool, validate: bool) -> None:
    """
    Securely configure your Affinity API key.

    This command prompts for your API key with hidden input (not echoed to screen)
    and stores it in your chosen location. The key is never passed as a command-line
    argument or logged.

    Get your API key from Affinity:
    https://support.affinity.co/s/article/How-to-Create-and-Manage-API-Keys

    Storage options:
    - project: Stores in .env file in current directory (auto-added to .gitignore)
    - user: Stores in user config file (chmod 600 on Unix)

    Examples:
        xaffinity config setup-key
        xaffinity config setup-key --scope project
        xaffinity config setup-key --scope user --force
        xaffinity config setup-key --no-validate
    """

    def fn(_ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        # Helper to print only for human output
        human_output = ctx.output != "json"
        console = Console(file=sys.stderr, force_terminal=None) if human_output else None

        def echo(msg: str = "", style: str | None = None) -> None:
            if console:
                console.print(msg, style=style, highlight=False)

        # Check for existing key using full resolution chain
        key_found, source = _find_existing_key(ctx)
        if key_found and not force:
            # Key exists - confirm overwrite
            echo(f"An API key is already configured [dim](source: {source})[/dim].")
            if not click.confirm("Do you want to configure a new key?", default=False):
                # For human output, show clean message and exit
                if human_output:
                    echo("Keeping existing key.", style="dim")
                    raise click.exceptions.Exit(0)
                return CommandOutput(
                    data={"key_stored": False, "reason": "existing_key_kept"},
                    api_called=False,
                )

        # Get the API key securely
        echo()
        echo("[bold]Enter your Affinity API key.[/bold]")
        echo(
            "Get your key from: [link=https://support.affinity.co/s/article/How-to-Create-and-Manage-API-Keys]"
            "https://support.affinity.co/s/article/How-to-Create-and-Manage-API-Keys[/link]"
        )
        echo()
        echo("[dim](Input is hidden - nothing will appear as you type)[/dim]")
        api_key = getpass.getpass(prompt="API Key: " if human_output else "").strip()
        if not api_key:
            raise CLIError("No API key provided.", exit_code=2, error_type="usage_error")

        # Validate API key format
        if not _validate_api_key_format(api_key):
            raise CLIError(
                "Invalid API key format. Keys should be 10-200 characters, "
                "containing only letters, numbers, underscores, hyphens, colons, or dots.",
                exit_code=2,
                error_type="validation_error",
            )

        # Determine scope (simplified prompt - just 1 or 2)
        chosen_scope = scope
        if chosen_scope is None:
            echo()
            echo("[bold]Where should the key be stored?[/bold]")
            echo("  [cyan]1[/cyan]  project — .env in current directory [dim](this project)[/dim]")
            echo("  [cyan]2[/cyan]  user    — User config file [dim](all projects)[/dim]")
            choice = click.prompt("Choice", type=click.Choice(["1", "2"]))
            chosen_scope = "project" if choice == "1" else "user"

        # Store the key
        try:
            if chosen_scope == "project":
                result = _store_in_dotenv(api_key, warnings=warnings)
            else:
                result = _store_in_config(ctx, api_key)
        except PermissionError as e:
            raise CLIError(
                f"Permission denied writing to file: {e}. Check directory permissions.",
                exit_code=1,
                error_type="permission_error",
            ) from e
        except OSError as e:
            raise CLIError(
                f"Failed to write configuration: {e}",
                exit_code=1,
                error_type="io_error",
            ) from e

        # Validate key if requested
        validated = False
        if validate:
            if console:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                    transient=True,
                ) as progress:
                    progress.add_task("Validating key against Affinity API...", total=None)
                    validated = _validate_key(api_key, warnings)
            else:
                validated = _validate_key(api_key, warnings)
            # Need to create a new CommandOutput with validated field
            # result.data is always set by _store_in_dotenv/_store_in_config
            assert result.data is not None
            result = CommandOutput(
                data={**result.data, "validated": validated},
                api_called=False,
            )
            if validated:
                echo("[green]✓ Key validated successfully[/green]")
            else:
                warnings.append("Key stored but validation failed - check key is correct")

        # Show usage hint based on scope
        echo()
        if chosen_scope == "project":
            echo("Key stored. To use it, run commands with [bold]--dotenv[/bold] flag:")
            echo("  [dim]xaffinity --dotenv whoami[/dim]")
        else:
            echo("Key stored in user config. Test with:")
            echo("  [dim]xaffinity whoami[/dim]")

        # Clear key reference (minimal security benefit but good practice)
        del api_key

        return result

    # For human output, bypass run_command to avoid rendering the data dict
    # (we already printed our own messages above)
    if ctx.output != "json":
        warnings: list[str] = []
        try:
            result = fn(ctx, warnings)
        except CLIError as e:
            click.echo(f"Error: {e.message}", err=True)
            raise click.exceptions.Exit(e.exit_code) from e
        # Emit any warnings that were collected
        if warnings and not ctx.quiet:
            for w in warnings:
                click.echo(f"Warning: {w}", err=True)
        raise click.exceptions.Exit(result.exit_code)

    run_command(ctx, command="config setup-key", fn=fn)


def _time_ago(dt: datetime) -> str:
    """Format a datetime as a human-readable relative time string."""
    now = datetime.now(timezone.utc)
    aware_dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    delta = now - aware_dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


@category("local")
@config_group.command(name="update-check", cls=RichCommand)
@click.option("--enable/--disable", "enable", default=None, help="Enable/disable update checks.")
@click.option("--now", is_flag=True, help="Check for updates immediately.")
@click.option("--status", is_flag=True, help="Show update check status.")
@click.option("--background", is_flag=True, help="Trigger background check (for MCP/automation).")
@output_options
@click.pass_obj
def config_update_check(
    ctx: CLIContext, *, enable: bool | None, now: bool, status: bool, background: bool
) -> None:
    """
    Configure automatic update checking.

    By default, xaffinity checks for updates once per day in interactive
    sessions and displays a notification if a new version is available.

    This check never blocks command execution and is suppressed when:
    - Using --quiet or --output json
    - Running in CI/CD (CI environment variable set)
    - Not attached to a terminal
    - Config has update_check = false

    Examples:
        xaffinity config update-check                # Show current settings
        xaffinity config update-check --status       # Show update status
        xaffinity config update-check --now          # Check for updates now
        xaffinity config update-check --background   # Trigger background check
        xaffinity config update-check --enable       # Enable update checks
        xaffinity config update-check --disable      # Disable update checks
    """
    # Enforce mutual exclusion between flags
    action_flags = [now, status, background]
    config_flags = [enable is True, enable is False] if enable is not None else []
    if sum(action_flags) > 1:
        raise click.UsageError("--now, --status, and --background are mutually exclusive")
    if sum(config_flags) > 1:
        raise click.UsageError("--enable and --disable are mutually exclusive")
    if any(action_flags) and enable is not None:
        raise click.UsageError("Cannot combine action flags with --enable/--disable")

    import affinity

    from ..update_check import (
        UpdateInfo,
        check_pypi_version,
        get_cached_update_info,
        get_upgrade_command,
        is_update_available,
        save_update_info,
        trigger_background_update_check,
    )

    # Handle --background: spawn background worker and exit immediately
    if background:
        state_dir = ctx.paths.state_dir
        try:
            trigger_background_update_check(state_dir, current_version=affinity.__version__)
            # Silent success - no output for --background
            return
        except Exception as e:
            # Exit with code 1 on failure
            raise click.exceptions.Exit(1) from e

    # Human-readable output — bypass run_command for all non-JSON paths
    if ctx.output != "json":
        state_dir = ctx.paths.state_dir
        cache_path = state_dir / "update_check.json"

        # --now: check PyPI immediately
        if now:
            latest = check_pypi_version()
            current = affinity.__version__
            if latest:
                update_avail = is_update_available(current, latest)
                info = UpdateInfo(
                    current_version=current,
                    latest_version=latest,
                    checked_at=datetime.now(timezone.utc),
                    update_available=update_avail,
                )
                save_update_info(cache_path, info)
                click.echo(f"Current version: {current}")
                if update_avail:
                    click.echo(f"Latest version: {latest}")
                    cmd = get_upgrade_command()
                    click.echo(f"Status: update available \u2014 run `{cmd}`")
                else:
                    click.echo("Status: up to date")
            else:
                click.echo("Status: could not reach PyPI \u2014 try again later")
            raise click.exceptions.Exit(0)

        # --status: show cached info
        if status:
            cached = get_cached_update_info(cache_path)
            if cached is None:
                click.echo("No update check cache.")
                click.echo("Run `xaffinity config update-check --now` to check.")
                raise click.exceptions.Exit(0)
            click.echo(f"Current version: {cached.current_version}")
            if cached.update_available and cached.latest_version:
                click.echo(f"Latest version: {cached.latest_version}")
            ts_str = cached.checked_at.strftime("%Y-%m-%d %H:%M UTC")
            click.echo(f"Last checked: {_time_ago(cached.checked_at)} ({ts_str})")
            if cached.is_stale():
                click.echo("Cache: stale")
            if cached.update_available:
                cmd = get_upgrade_command()
                click.echo(f"Status: update available \u2014 run `{cmd}`")
            else:
                click.echo("Status: up to date")
            raise click.exceptions.Exit(0)

        # --enable/--disable: show instructions
        if enable is not None:
            if enable:
                click.echo(
                    "To enable update checks, ensure update_check = true"
                    f" in {ctx.paths.config_path}"
                    " or remove XAFFINITY_NO_UPDATE_CHECK env var."
                )
            else:
                click.echo(
                    "To disable update checks, set update_check = false"
                    f" in {ctx.paths.config_path}"
                    " or set XAFFINITY_NO_UPDATE_CHECK=1."
                )
            raise click.exceptions.Exit(0)

        # Default (no flags): show current settings with inline check
        enabled = ctx.update_check_enabled
        if not enabled:
            click.echo("Update checks: disabled")
            raise click.exceptions.Exit(0)

        mode = ctx.update_notify_mode
        click.echo(f"Update checks: enabled ({mode})")

        cached = get_cached_update_info(cache_path)

        if cached is None or cached.is_stale():
            # User is explicitly asking — check now instead of deferring
            latest = check_pypi_version()
            if latest:
                current = affinity.__version__
                update_avail = is_update_available(current, latest)
                info = UpdateInfo(
                    current_version=current,
                    latest_version=latest,
                    checked_at=datetime.now(timezone.utc),
                    update_available=update_avail,
                )
                save_update_info(cache_path, info)
                click.echo(f"Current version: {current}")
                if update_avail:
                    click.echo(f"Latest version: {latest}")
                    cmd = get_upgrade_command()
                    click.echo(f"Status: update available \u2014 run `{cmd}`")
                else:
                    click.echo("Status: up to date")
            else:
                click.echo(
                    "Status: could not reach PyPI"
                    " \u2014 try `xaffinity config update-check --now` later"
                )
            raise click.exceptions.Exit(0)

        click.echo(f"Current version: {cached.current_version}")
        if cached.update_available and cached.latest_version:
            click.echo(f"Latest version: {cached.latest_version}")

        ts_str = cached.checked_at.strftime("%Y-%m-%d %H:%M UTC")
        click.echo(f"Last checked: {_time_ago(cached.checked_at)} ({ts_str})")

        if cached.update_available:
            cmd = get_upgrade_command()
            click.echo(f"Status: update available \u2014 run `{cmd}`")
        else:
            click.echo("Status: up to date")
        raise click.exceptions.Exit(0)

    def fn(_ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        state_dir = ctx.paths.state_dir
        cache_path = state_dir / "update_check.json"

        result_data: dict[str, object] = {
            "update_check_enabled": ctx.update_check_enabled,
            "update_notify_mode": ctx.update_notify_mode,
        }

        # Handle --now: check for updates immediately
        if now:
            latest = check_pypi_version()
            current = affinity.__version__
            if latest:
                update_avail = is_update_available(current, latest)
                # Save to cache
                info = UpdateInfo(
                    current_version=current,
                    latest_version=latest,
                    checked_at=datetime.now(timezone.utc),
                    update_available=update_avail,
                )
                save_update_info(cache_path, info)
                result_data["current_version"] = current
                result_data["latest_version"] = latest
                result_data["update_available"] = update_avail
                if update_avail:
                    result_data["upgrade_command"] = get_upgrade_command()
            else:
                result_data["error"] = "Failed to check PyPI"
                warnings.append("Could not reach PyPI to check for updates")
            return CommandOutput(data=result_data, api_called=False)

        # Handle --status: show cached update info
        if status:
            # Always include state_dir for MCP throttle file alignment
            result_data["state_dir"] = str(state_dir)
            cached = get_cached_update_info(cache_path)
            if cached:
                result_data["current_version"] = cached.current_version
                result_data["latest_version"] = cached.latest_version
                result_data["update_available"] = cached.update_available
                result_data["checked_at"] = cached.checked_at.isoformat()
                result_data["cache_stale"] = cached.is_stale()
                if cached.last_notified_at:
                    result_data["last_notified_at"] = cached.last_notified_at.isoformat()
                if cached.update_available and cached.latest_version:
                    result_data["upgrade_command"] = get_upgrade_command()
            else:
                result_data["cache_exists"] = False
                result_data["cache_stale"] = True  # No cache means stale
                result_data["message"] = "No update check cache. Run with --now to check."
            return CommandOutput(data=result_data, api_called=False)

        # Handle --enable/--disable: update config
        # Note: This would require modifying the config file, which is complex
        # For now, we document the manual approach
        if enable is not None:
            if enable:
                result_data["action"] = "enable"
                result_data["message"] = (
                    "To enable update checks, ensure update_check = true in your config file "
                    f"({ctx.paths.config_path}) or remove XAFFINITY_NO_UPDATE_CHECK env var."
                )
            else:
                result_data["action"] = "disable"
                result_data["message"] = (
                    "To disable update checks, set update_check = false in your config file "
                    f"({ctx.paths.config_path}) or set XAFFINITY_NO_UPDATE_CHECK=1."
                )
            return CommandOutput(data=result_data, api_called=False)

        # Default: show current settings
        result_data["config_path"] = str(ctx.paths.config_path)
        result_data["state_dir"] = str(state_dir)
        result_data["env_var_set"] = bool(os.environ.get("XAFFINITY_NO_UPDATE_CHECK"))

        # Also show cache status if it exists
        cached = get_cached_update_info(cache_path)
        if cached:
            result_data["last_check"] = cached.checked_at.isoformat()
            result_data["update_available"] = cached.update_available
            if cached.update_available and cached.latest_version:
                result_data["latest_version"] = cached.latest_version

        return CommandOutput(data=result_data, api_called=False)

    run_command(ctx, command="config update-check", fn=fn)
