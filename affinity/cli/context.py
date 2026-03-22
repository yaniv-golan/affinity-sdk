from __future__ import annotations

import errno
import os
import re
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.parse import urlsplit as _urlsplit_for_qs

from affinity import Affinity
from affinity.client import maybe_load_dotenv
from affinity.exceptions import (
    AffinityError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    MergedEntityError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ServerError,
    UnsafeUrlError,
    UnsupportedOperationError,
    ValidationError,
    WriteNotAllowedError,
)
from affinity.exceptions import (
    TimeoutError as AffinityTimeoutError,
)
from affinity.hooks import (
    ErrorHook,
    EventHook,
    HookEvent,
    RequestHook,
    RequestInfo,
    RequestRetrying,
    ResponseHook,
    ResponseInfo,
)
from affinity.hooks import (
    ErrorInfo as HookErrorInfo,
)
from affinity.models.types import V1_BASE_URL, V2_BASE_URL
from affinity.policies import Policies, WritePolicy

from .click_compat import click
from .config import LoadedConfig, ProfileConfig, config_file_permission_warnings, load_config
from .errors import CLIError
from .logging import set_redaction_api_key
from .paths import CliPaths, get_paths
from .results import CommandContext, CommandMeta, CommandResult, ErrorInfo, ResultSummary
from .session_cache import SessionCache, SessionCacheConfig

OutputFormat = Literal["table", "json", "jsonl", "markdown", "toon", "csv"]

_CLI_CACHE_ENABLED = True
_CLI_CACHE_TTL_SECONDS = 300.0


def _strip_url_query_and_fragment(url: str) -> str:
    """
    Keep scheme/host/path but drop query/fragment to reduce accidental leakage of PII/filters.
    """
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return url


@dataclass(frozen=True, slots=True)
class ClientSettings:
    api_key: str
    timeout: float
    v1_base_url: str
    v2_base_url: str
    enable_beta_endpoints: bool
    log_requests: bool
    max_retries: int
    policies: Policies
    on_request: RequestHook | None
    on_response: ResponseHook | None
    on_error: ErrorHook | None
    on_event: EventHook | None


@dataclass
class CLIContext:
    output: OutputFormat | None
    quiet: bool
    verbosity: int
    pager: bool | None
    progress: Literal["auto", "always", "never"]
    profile: str | None
    dotenv: bool
    env_file: Path
    api_key_file: str | None
    api_key_stdin: bool
    timeout: float | None
    max_retries: int
    readonly: bool
    trace: bool
    log_file: Path | None
    enable_log_file: bool
    enable_beta_endpoints: bool
    all_columns: bool = False  # Show all columns in table output
    max_columns: int | None = None  # Override auto-calculated max columns

    _paths: CliPaths = field(default_factory=get_paths)
    _output_source: str | None = field(
        default=None
    )  # Which flag set output (e.g., "--json", "--output csv")
    _output_format_conflict: tuple[str, str] | None = field(
        default=None
    )  # (requested_desc, existing_desc)
    _loaded_config: LoadedConfig | None = None
    _client: Affinity | None = None
    _session_cache_config: SessionCacheConfig = field(default_factory=SessionCacheConfig)
    _session_cache: SessionCache | None = None
    _no_cache: bool = False

    def load_dotenv_if_requested(self) -> None:
        try:
            maybe_load_dotenv(
                load_dotenv=self.dotenv,
                dotenv_path=self.env_file,
                override=False,
            )
        except ImportError as exc:
            raise CLIError(
                "Optional .env support requires python-dotenv; install `affinity-sdk[cli]`.",
                exit_code=2,
                error_type="usage_error",
            ) from exc

    @property
    def paths(self) -> CliPaths:
        return self._paths

    def _config_path(self) -> Path:
        return self.paths.config_path

    def load_config(self) -> LoadedConfig:
        if self._loaded_config is None:
            self._loaded_config = load_config(self._config_path())
        return self._loaded_config

    def _effective_profile(self) -> str:
        return self.profile or os.getenv("AFFINITY_PROFILE") or "default"

    def _profile_config(self) -> ProfileConfig:
        cfg = self.load_config()
        if self._effective_profile() == "default":
            return cfg.default
        return cfg.profiles.get(self._effective_profile(), ProfileConfig())

    @property
    def update_check_enabled(self) -> bool:
        """Check if update checking is enabled based on config and environment."""
        # Environment variable takes precedence
        if os.environ.get("XAFFINITY_NO_UPDATE_CHECK"):
            return False

        # Then check profile config
        prof = self._profile_config()
        return prof.update_check

    @property
    def update_notify_mode(self) -> str:
        """Get update notification mode from config or environment.

        Returns:
            "interactive" (default), "always", or "never"
        """
        # Environment variable takes precedence
        env_mode = os.environ.get("XAFFINITY_UPDATE_NOTIFY")
        if env_mode in ("interactive", "always", "never"):
            return env_mode

        # Then check profile config
        prof = self._profile_config()
        return prof.update_notify

    def resolve_api_key(self, *, warnings: list[str]) -> str:
        if self.api_key_stdin:
            raw = sys.stdin.read()
            key = raw.strip()
            if not key:
                raise CLIError(
                    "Empty API key provided via stdin.", exit_code=2, error_type="usage_error"
                )
            return key

        if self.api_key_file is not None:
            if self.api_key_file == "-":
                raw = sys.stdin.read()
                key = raw.strip()
                if not key:
                    raise CLIError(
                        "Empty API key provided via stdin.", exit_code=2, error_type="usage_error"
                    )
                return key
            path = Path(self.api_key_file)
            # Check file permissions (Bug #17)
            warnings.extend(config_file_permission_warnings(path))
            key = path.read_text(encoding="utf-8").strip()
            if not key:
                raise CLIError(f"Empty API key file: {path}", exit_code=2, error_type="usage_error")
            return key

        env_key = os.getenv("AFFINITY_API_KEY", "").strip()
        if env_key:
            return env_key

        prof = self._profile_config()
        if prof.api_key:
            warnings.extend(config_file_permission_warnings(self._config_path()))
            return prof.api_key.strip()

        raise CLIError(
            (
                "Missing API key. Set AFFINITY_API_KEY, use --api-key-file/--api-key-stdin, "
                "or configure profiles."
            ),
            exit_code=2,
            error_type="usage_error",
        )

    def resolve_client_settings(self, *, warnings: list[str]) -> ClientSettings:
        self.load_dotenv_if_requested()
        api_key = self.resolve_api_key(warnings=warnings)
        set_redaction_api_key(api_key)

        prof = self._profile_config()
        timeout = self.timeout if self.timeout is not None else prof.timeout_seconds
        if timeout is None:
            timeout = 30.0
        if self.max_retries < 0:
            raise CLIError("--max-retries must be >= 0.", exit_code=2, error_type="usage_error")

        v1_base_url = os.getenv("AFFINITY_V1_BASE_URL") or prof.v1_base_url or V1_BASE_URL
        v2_base_url = os.getenv("AFFINITY_V2_BASE_URL") or prof.v2_base_url or V2_BASE_URL

        def _write_stderr(line: str) -> None:
            sys.stderr.write(line + "\n")
            with suppress(Exception):
                sys.stderr.flush()

        on_request: RequestHook | None = None
        on_response: ResponseHook | None = None
        on_error: ErrorHook | None = None
        if self.trace:

            def _on_request(req: RequestInfo) -> None:
                method = req.method
                url = _strip_url_query_and_fragment(req.url)
                _write_stderr(f"trace -> {method} {url}")

            def _on_response(res: ResponseInfo) -> None:
                status = str(res.status_code)
                url = _strip_url_query_and_fragment(res.request.url)
                extra = []
                extra.append(f"elapsedMs={int(res.elapsed_ms)}")
                if res.cache_hit:
                    extra.append("cacheHit=true")
                suffix = (" " + " ".join(extra)) if extra else ""
                _write_stderr(f"trace <- {status} {url}{suffix}")

            def _on_error(err: HookErrorInfo) -> None:
                url = _strip_url_query_and_fragment(err.request.url)
                exc_name = type(err.error).__name__
                _write_stderr(f"trace !! {exc_name} {url}")

            on_request = _on_request
            on_response = _on_response
            on_error = _on_error

        # Rate limit visibility - always show retrying messages (not just with --trace)
        def _on_event(event: HookEvent) -> None:
            if isinstance(event, RequestRetrying):
                wait_int = int(event.wait_seconds)
                _write_stderr(f"Rate limited (429) - retrying in {wait_int}s...")

        on_event: EventHook = _on_event

        policies = Policies(write=WritePolicy.DENY) if self.readonly else Policies()

        return ClientSettings(
            api_key=api_key,
            timeout=timeout,
            v1_base_url=v1_base_url,
            v2_base_url=v2_base_url,
            enable_beta_endpoints=self.enable_beta_endpoints,
            log_requests=self.verbosity >= 2,
            max_retries=self.max_retries,
            policies=policies,
            on_request=on_request,
            on_response=on_response,
            on_error=on_error,
            on_event=on_event,
        )

    @property
    def session_cache(self) -> SessionCache:
        """Get or create session cache."""
        if self._no_cache:
            # Return a disabled cache instance
            config = SessionCacheConfig()
            config.enabled = False
            return SessionCache(config, trace=self.trace)
        if self._session_cache is None:
            self._session_cache = SessionCache(self._session_cache_config, trace=self.trace)
        return self._session_cache

    def init_session_cache(self, settings: ClientSettings) -> None:
        """Initialize session cache with tenant hash from resolved settings.

        Called after client settings are resolved but before client creation.
        Uses settings.api_key (public) rather than client internals.
        """
        if self._session_cache_config.enabled and not self._no_cache:
            self._session_cache_config.set_tenant_hash(settings.api_key)

    def get_client(self, *, warnings: list[str]) -> Affinity:
        if self._client is not None:
            return self._client

        settings = self.resolve_client_settings(warnings=warnings)
        self.init_session_cache(settings)

        self._client = Affinity(
            api_key=settings.api_key,
            v1_base_url=settings.v1_base_url,
            v2_base_url=settings.v2_base_url,
            timeout=settings.timeout,
            log_requests=settings.log_requests,
            max_retries=settings.max_retries,
            enable_beta_endpoints=settings.enable_beta_endpoints,
            enable_cache=_CLI_CACHE_ENABLED,
            cache_ttl=_CLI_CACHE_TTL_SECONDS,
            on_request=settings.on_request,
            on_response=settings.on_response,
            on_error=settings.on_error,
            on_event=settings.on_event,
            policies=settings.policies,
        )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def exit_code_for_exception(exc: Exception) -> int:
    if isinstance(exc, CLIError):
        return exc.exit_code
    if isinstance(exc, (AuthenticationError, AuthorizationError)):
        return 3
    if isinstance(exc, MergedEntityError):
        return 4
    if isinstance(exc, NotFoundError):
        return 4
    if isinstance(exc, (RateLimitError, ServerError)):
        return 5
    if isinstance(exc, AffinityError):
        return 1
    return 1


def _hint_for_validation_message(message: str) -> str | None:
    """Return a specific hint if the error message matches a known pattern."""
    msg_lower = message.lower()

    # Date range exceeded for interactions
    if "date range" in msg_lower and ("1 year" in msg_lower or "within" in msg_lower):
        return (
            "The Affinity API limits interaction queries to 1 year. "
            "Split your query into multiple 1-year ranges."
        )

    # Interaction create: missing internal/external person IDs
    if "person_ids" in msg_lower and ("internal" in msg_lower or "external" in msg_lower):
        return (
            "Interactions require at least one internal person (workspace user) and one "
            "external person (contact). Use `xaffinity whoami` to find your person ID, "
            "or pass --include-me to auto-include yourself."
        )

    return None


def normalize_exception(exc: Exception, *, verbosity: int = 0) -> CLIError:
    if isinstance(exc, CLIError):
        return exc

    if isinstance(exc, FileExistsError):
        path = str(exc.filename) if getattr(exc, "filename", None) else str(exc)
        return CLIError(
            f"Destination already exists: {path}",
            error_type="file_exists",
            exit_code=2,
            hint="Re-run with --overwrite or choose a different --out directory.",
            details={"path": path},
            cause=exc,
        )

    if isinstance(exc, PermissionError):
        path = str(exc.filename) if getattr(exc, "filename", None) else "file"
        return CLIError(
            f"Permission denied: {path}",
            error_type="permission_denied",
            exit_code=2,
            hint="Check file permissions or choose a different output location.",
            details={"path": path},
            cause=exc,
        )

    if isinstance(exc, IsADirectoryError):
        path = str(exc.filename) if getattr(exc, "filename", None) else str(exc)
        return CLIError(
            f"Expected a file path but got a directory: {path}",
            error_type="io_error",
            exit_code=2,
            details={"path": path},
            cause=exc,
        )

    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
        path = str(getattr(exc, "filename", "") or "")
        suffix = f": {path}" if path else ""
        return CLIError(
            f"No space left on device{suffix}",
            error_type="disk_full",
            exit_code=2,
            hint="Free disk space or choose a different output directory.",
            details={"path": path} if path else None,
            cause=exc,
        )

    if isinstance(exc, WriteNotAllowedError):
        details: dict[str, Any] = {}
        if getattr(exc, "method", None):
            details["method"] = exc.method
        if getattr(exc, "url", None):
            details["url"] = _strip_url_query_and_fragment(exc.url)
        return CLIError(
            "Write operation blocked by policy (--readonly).",
            error_type="write_not_allowed",
            exit_code=2,
            hint="Re-run without --readonly to allow writes.",
            details=details or None,
            cause=exc,
        )

    if isinstance(exc, RateLimitError):
        hint = "Wait and retry, or reduce request frequency."
        if getattr(exc, "retry_after", None):
            hint = f"Retry after {exc.retry_after} seconds."
        return CLIError(
            str(exc),
            error_type="rate_limited",
            exit_code=5,
            hint=hint,
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, MergedEntityError):
        details = _details_for_affinity_error(exc, verbosity=verbosity) or {}
        details["sourceId"] = exc.source_id
        details["targetId"] = exc.target_id
        if exc.entity_type:
            details["entityType"] = exc.entity_type
        entity_label = exc.entity_type or "Entity"
        return CLIError(
            str(exc),
            error_type="entity_merged",
            exit_code=4,
            hint=(
                f"{entity_label} {exc.source_id} was merged into {exc.target_id}. "
                f"Use the target ID instead."
            ),
            details=details,
            cause=exc,
        )

    if isinstance(exc, ValidationError):
        sanitized_params = _sanitized_request_params_from_diagnostics(exc) or {}

        message = getattr(exc, "message", None)
        if not isinstance(message, str) or not message:
            message = str(exc) or "Request validation failed."

        field_name: str | None = getattr(exc, "param", None)
        if not field_name:
            match = re.search(r"\bField\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:", message)
            if match:
                field_name = match.group(1)
        if not field_name and len(sanitized_params) == 1:
            field_name = next(iter(sanitized_params.keys()))

        # Check for specific error patterns in the message first
        pattern_hint = _hint_for_validation_message(message)
        if pattern_hint is not None:
            hint = pattern_hint
        else:
            hint = "Check command arguments and retry."
            if "organization_id" in sanitized_params or "company_id" in sanitized_params:
                company_id = sanitized_params.get("organization_id") or sanitized_params.get(
                    "company_id"
                )
                if isinstance(company_id, int):
                    hint = (
                        "Verify the company id exists and you have access "
                        f"(company_id={company_id})."
                    )
            elif "person_id" in sanitized_params:
                person_id = sanitized_params.get("person_id")
                if isinstance(person_id, int):
                    hint = (
                        f"Verify the person id exists and you have access (person_id={person_id})."
                    )
            elif "opportunity_id" in sanitized_params:
                opportunity_id = sanitized_params.get("opportunity_id")
                if isinstance(opportunity_id, int):
                    hint = (
                        "Verify the opportunity id exists and you have access "
                        f"(opportunity_id={opportunity_id})."
                    )
            elif field_name:
                hint = f"Check the value for `{field_name}` and retry."
            elif sanitized_params:
                bits = ", ".join(f"{k}={v}" for k, v in sorted(sanitized_params.items()))
                hint = f"Check parameter values ({bits}) and retry."

        details = _details_for_affinity_error(exc, verbosity=verbosity) or {}
        if sanitized_params:
            details.setdefault("params", sanitized_params)
        if field_name:
            details.setdefault("param", field_name)

        if message == "Unknown error":
            diagnostics = getattr(exc, "diagnostics", None)
            snippet = getattr(diagnostics, "response_body_snippet", None) if diagnostics else None
            if isinstance(snippet, str) and snippet.strip() and snippet.strip() not in {"{}", "[]"}:
                message = snippet.strip()
            else:
                message = str(exc)

        return CLIError(
            message,
            error_type="validation_error",
            exit_code=2,
            hint=hint,
            details=details or None,
            cause=exc,
        )

    if isinstance(exc, AuthenticationError):
        return CLIError(
            str(exc),
            error_type="auth_error",
            exit_code=3,
            hint="Run 'xaffinity config check-key' or 'xaffinity config setup-key' to configure.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, AuthorizationError):
        return CLIError(
            str(exc),
            error_type="forbidden",
            exit_code=3,
            hint="Check that your API key has access to this resource.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, NotFoundError):
        return CLIError(
            str(exc),
            error_type="not_found",
            exit_code=4,
            hint="If this resource was just created, wait a moment and retry (V1→V2 sync delay).",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, ConflictError):
        return CLIError(
            str(exc),
            error_type="conflict",
            exit_code=1,
            hint="The resource already exists or was modified. Check for duplicates.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, UnsafeUrlError):
        return CLIError(
            str(exc),
            error_type="unsafe_url",
            exit_code=1,
            hint="The server returned a URL that failed security validation.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, UnsupportedOperationError):
        return CLIError(
            str(exc),
            error_type="unsupported_operation",
            exit_code=1,
            hint="This operation is not available for the current API version or configuration.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, (ServerError,)):
        return CLIError(
            str(exc),
            error_type="server_error",
            exit_code=5,
            hint="Retry later; if the issue persists, contact Affinity support.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, NetworkError):
        return CLIError(
            str(exc),
            error_type="network_error",
            exit_code=1,
            hint="Check your network connection and retry.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, AffinityTimeoutError):
        return CLIError(
            str(exc),
            error_type="timeout",
            exit_code=1,
            hint="Increase --timeout and retry, or narrow the request.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, ConfigurationError):
        return CLIError(
            str(exc),
            error_type="config_error",
            exit_code=2,
            hint="Check configuration (API key, base URLs) and retry.",
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, AffinityError):
        return CLIError(
            str(exc),
            error_type="api_error",
            exit_code=1,
            details=_details_for_affinity_error(exc, verbosity=verbosity),
            cause=exc,
        )

    if isinstance(exc, click.UsageError):
        return CLIError(
            exc.format_message(),
            error_type="usage_error",
            exit_code=2,
            cause=exc,
        )

    if isinstance(exc, click.ClickException):
        return CLIError(
            exc.format_message(),
            error_type="error",
            exit_code=getattr(exc, "exit_code", 1),
            cause=exc,
        )

    return CLIError(
        str(exc) or exc.__class__.__name__,
        error_type="internal_error",
        exit_code=1,
        cause=exc,
    )


def _details_for_affinity_error(exc: AffinityError, *, verbosity: int) -> dict[str, Any] | None:
    details: dict[str, Any] = {}
    if exc.status_code is not None:
        details["statusCode"] = exc.status_code
    diagnostics = getattr(exc, "diagnostics", None)
    if diagnostics is not None:
        if getattr(diagnostics, "method", None):
            details["method"] = diagnostics.method
        if getattr(diagnostics, "url", None):
            details["url"] = _strip_url_query_and_fragment(diagnostics.url)
        if getattr(diagnostics, "api_version", None):
            details["apiVersion"] = diagnostics.api_version
        if getattr(diagnostics, "request_id", None):
            details["requestId"] = diagnostics.request_id
        if verbosity >= 2:
            if getattr(diagnostics, "response_headers", None):
                details["responseHeaders"] = diagnostics.response_headers
            if getattr(diagnostics, "response_body_snippet", None):
                details["responseBodySnippet"] = diagnostics.response_body_snippet
    return details or None


def _sanitized_request_params_from_diagnostics(exc: AffinityError) -> dict[str, Any] | None:
    diagnostics = getattr(exc, "diagnostics", None)
    if diagnostics is None:
        return None

    allow = {
        "organization_id",
        "person_id",
        "opportunity_id",
        "list_id",
        "list_entry_id",
        "page_size",
        "page_token",
    }

    raw_params = getattr(diagnostics, "request_params", None)
    if isinstance(raw_params, dict) and raw_params:
        sanitized_from_params: dict[str, Any] = {}
        for k, v in raw_params.items():
            if k not in allow:
                continue
            if isinstance(v, list) and v:
                v = v[0]
            if isinstance(v, int):
                sanitized_from_params[k] = v
            elif isinstance(v, str) and v.isdigit():
                sanitized_from_params[k] = int(v)
        if sanitized_from_params:
            if "page_token" in sanitized_from_params:
                sanitized_from_params["cursor"] = sanitized_from_params.pop("page_token")
            return sanitized_from_params

    if not getattr(diagnostics, "url", None):
        return None

    try:
        parts = _urlsplit_for_qs(diagnostics.url)
        qs = parse_qs(parts.query, keep_blank_values=False)
    except Exception:
        return None

    sanitized: dict[str, Any] = {}
    for k, values in qs.items():
        if k not in allow or not values:
            continue
        v = values[0]
        if isinstance(v, str) and v.isdigit():
            sanitized[k] = int(v)
        else:
            # Avoid leaking free-text search terms or other potential PII.
            continue
    if "page_token" in sanitized:
        sanitized["cursor"] = sanitized.pop("page_token")
    return sanitized or None


def error_info_for_exception(exc: Exception, *, verbosity: int = 0) -> ErrorInfo:
    normalized = normalize_exception(exc, verbosity=verbosity)
    details = normalized.details
    if verbosity >= 2 and normalized.cause is not None:
        extra: dict[str, Any] = {}
        if details and isinstance(details, dict):
            extra.update(details)
        extra.setdefault("causeType", type(normalized.cause).__name__)
        msg = str(normalized.cause)
        if msg:
            extra.setdefault("causeMessage", msg)
        details = extra
    return ErrorInfo(
        type=normalized.error_type,
        message=normalized.message,
        hint=normalized.hint,
        docs_url=normalized.docs_url,
        details=details,
    )


def build_result(
    *,
    ok: bool,
    command: CommandContext,
    started_at: float,
    data: Any | None,
    artifacts: list[Any] | None = None,
    warnings: list[str],
    profile: str | None,
    rate_limit: Any | None,
    pagination: dict[str, Any] | None = None,
    resolved: dict[str, Any] | None = None,
    columns: list[dict[str, Any]] | None = None,
    summary: ResultSummary | None = None,
    error: ErrorInfo | None = None,
) -> CommandResult:
    duration_ms = int(max(0.0, (time.time() - started_at) * 1000))
    meta = CommandMeta(
        duration_ms=duration_ms,
        profile=profile,
        pagination=pagination,
        resolved=resolved,
        columns=columns,
        rate_limit=rate_limit,
        summary=summary,
    )
    return CommandResult(
        ok=ok,
        command=command,
        data=data,
        artifacts=artifacts or [],
        warnings=warnings,
        meta=meta,
        error=error,
    )
