"""
Main Affinity API client.

Provides a unified interface to all Affinity API functionality.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import pathlib
import warnings
from typing import Any, Literal, cast

import httpx

from .clients.http import (
    AsyncHTTPClient,
    ClientConfig,
    HTTPClient,
)
from .hooks import AnyEventHook, ErrorHook, RequestHook, ResponseHook
from .models.secondary import WhoAmI
from .models.types import V1_BASE_URL, V2_BASE_URL
from .policies import Policies, WritePolicy
from .services.companies import AsyncCompanyService, CompanyService
from .services.lists import AsyncListService, ListService
from .services.opportunities import AsyncOpportunityService, OpportunityService
from .services.persons import AsyncPersonService, PersonService
from .services.rate_limits import AsyncRateLimitService, RateLimitService
from .services.tasks import AsyncTaskService, TaskService
from .services.v1_only import (
    AsyncAuthService,
    AsyncEntityFileService,
    AsyncFieldService,
    AsyncFieldValueChangesService,
    AsyncFieldValueService,
    AsyncInteractionService,
    AsyncNoteService,
    AsyncRelationshipStrengthService,
    AsyncReminderService,
    AsyncWebhookService,
    AuthService,
    EntityFileService,
    FieldService,
    FieldValueChangesService,
    FieldValueService,
    InteractionService,
    NoteService,
    RelationshipStrengthService,
    ReminderService,
    WebhookService,
)

_DEFAULT_API_KEY_ENV_VAR = "AFFINITY_API_KEY"


def _maybe_load_dotenv(
    *,
    load_dotenv: bool,
    dotenv_path: str | os.PathLike[str] | None,
    override: bool,
) -> None:
    if not load_dotenv:
        return

    if importlib.util.find_spec("dotenv") is None:
        raise ImportError(
            "Optional .env support requires python-dotenv; install `affinity-sdk[dotenv]` "
            "or `python-dotenv`."
        )

    dotenv_module = cast(Any, importlib.import_module("dotenv"))
    dotenv_module.load_dotenv(dotenv_path=dotenv_path, override=override)


def maybe_load_dotenv(
    *,
    load_dotenv: bool,
    dotenv_path: str | os.PathLike[str] | None = None,
    override: bool = False,
) -> None:
    """
    Optionally load a `.env` file.

    This is a public wrapper for the SDK's internal dotenv loader. It is used by
    `Affinity.from_env(...)` and can be reused by integrations (like the CLI)
    that want consistent behavior and error messaging.
    """
    _maybe_load_dotenv(load_dotenv=load_dotenv, dotenv_path=dotenv_path, override=override)


def _api_key_from_env(
    *,
    env_var: str,
    load_dotenv: bool,
    dotenv_path: str | os.PathLike[str] | None,
    dotenv_override: bool,
) -> str:
    from affinity._internal.keyfile import read_key_command, read_key_file  # noqa: PLC0415

    _maybe_load_dotenv(load_dotenv=load_dotenv, dotenv_path=dotenv_path, override=dotenv_override)

    # Step 1: AFFINITY_API_KEY (or custom env_var) — non-empty string wins immediately.
    api_key = os.getenv(env_var, "").strip()
    if api_key:
        return api_key

    # Step 2: AFFINITY_API_KEY_FILE — path to a file containing the key
    # (Docker secrets, k8s mounted Secrets, Hashicorp Vault agent, etc.).
    file_env = os.environ.get("AFFINITY_API_KEY_FILE", "").strip()
    if file_env:
        return read_key_file(pathlib.Path(file_env).expanduser())

    # Step 3: AFFINITY_API_KEY_COMMAND — command whose stdout is the key
    # (git credential helpers, gpg --passphrase-cmd, pass, op, vault, etc.).
    cmd_env = os.environ.get("AFFINITY_API_KEY_COMMAND", "").strip()
    if cmd_env:
        return read_key_command(cmd_env)

    raise ValueError(
        f"Missing API key: set `{env_var}`, `AFFINITY_API_KEY_FILE`, "
        f"`AFFINITY_API_KEY_COMMAND`, or initialize the client with `api_key=...`."
    )


class Affinity:
    """
    Synchronous Affinity API client.

    Provides access to all Affinity API functionality with a clean,
    Pythonic interface. Uses V2 API where available, falls back to V1
    for operations not yet supported in V2.

    Example:
        ```python
        from affinity import Affinity

        # Initialize with API key
        client = Affinity(api_key="your-api-key")

        # Use as context manager for automatic cleanup
        with Affinity(api_key="your-api-key") as client:
            # Get all companies
            for company in client.companies.all():
                print(company.name)

            # Get a specific person with field data
            person = client.persons.get(
                PersonId(12345),
                field_types=["enriched", "global"]
            )

            # Add a company to a list
            entries = client.lists.entries(ListId(789))
            entry = entries.add_company(CompanyId(456))

            # Update field values
            entries.update_field_value(
                entry.id,
                FieldId(101),
                "New value"
            )
        ```

    Attributes:
        companies: Company (organization) operations
        persons: Person (contact) operations
        lists: List operations
        notes: Note operations
        reminders: Reminder operations
        webhooks: Webhook subscription operations
        interactions: Interaction (email, meeting, etc.) operations
        fields: Custom field operations
        field_values: Field value operations
        field_value_changes: Field value change history operations
        files: Entity file operations
        relationships: Relationship strength queries
        auth: Authentication and rate limit info
    """

    def __init__(
        self,
        api_key: str,
        *,
        v1_base_url: str = V1_BASE_URL,
        v2_base_url: str = V2_BASE_URL,
        v1_auth_mode: Literal["bearer", "basic"] = "bearer",
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        enable_beta_endpoints: bool = False,
        allow_insecure_download_redirects: bool = False,
        expected_v2_version: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        enable_cache: bool = False,
        cache_ttl: float = 300.0,
        log_requests: bool = False,
        on_request: RequestHook | None = None,
        on_response: ResponseHook | None = None,
        on_error: ErrorHook | None = None,
        on_event: AnyEventHook | None = None,
        hook_error_policy: Literal["swallow", "raise"] = "swallow",
        policies: Policies | None = None,
    ):
        """
        Initialize the Affinity client.

        Args:
            api_key: Your Affinity API key
            v1_base_url: V1 API base URL (default: https://api.affinity.co)
            v2_base_url: V2 API base URL (default: https://api.affinity.co/v2)
            v1_auth_mode: Auth mode for V1 API ("bearer" or "basic")
            transport: Optional `httpx` transport (advanced; useful for mocking in tests)
            async_transport: Optional async `httpx` transport (advanced; useful for mocking in
                tests)
            enable_beta_endpoints: Enable beta V2 endpoints
            allow_insecure_download_redirects: Allow `http://` redirects for file downloads.
                Not recommended; prefer HTTPS-only downloads.
            expected_v2_version: Expected V2 API version for diagnostics (e.g.,
                "2024-01-01"). Used to detect version compatibility issues.
                See TR-015.
            timeout: Request timeout in seconds
            max_retries: Maximum retries for rate-limited requests
            enable_cache: Enable response caching for field metadata
            cache_ttl: Cache TTL in seconds
            log_requests: Log all HTTP requests (for debugging)
            on_request: Hook called before each request (DX-008)
            on_response: Hook called after each response (DX-008)
            on_error: Hook called when a request raises (DX-008)
            on_event: Event hook called for request/response lifecycle events (DX-008)
            hook_error_policy: What to do if hooks raise ("swallow" or "raise")
            policies: Client policies (e.g., disable writes)
        """
        config = ClientConfig(
            api_key=api_key,
            v1_base_url=v1_base_url,
            v2_base_url=v2_base_url,
            v1_auth_mode=v1_auth_mode,
            transport=transport,
            async_transport=async_transport,
            enable_beta_endpoints=enable_beta_endpoints,
            allow_insecure_download_redirects=allow_insecure_download_redirects,
            expected_v2_version=expected_v2_version,
            timeout=timeout,
            max_retries=max_retries,
            enable_cache=enable_cache,
            cache_ttl=cache_ttl,
            log_requests=log_requests,
            on_request=on_request,
            on_response=on_response,
            on_error=on_error,
            on_event=on_event,
            hook_error_policy=hook_error_policy,
            policies=policies or Policies(),
        )
        self._http = HTTPClient(config)

        # Resource management tracking
        self._closed = False
        self._entered_context = False

        # Initialize services
        self._companies: CompanyService | None = None
        self._persons: PersonService | None = None
        self._lists: ListService | None = None
        self._opportunities: OpportunityService | None = None
        self._tasks: TaskService | None = None
        self._notes: NoteService | None = None
        self._reminders: ReminderService | None = None
        self._webhooks: WebhookService | None = None
        self._interactions: InteractionService | None = None
        self._fields: FieldService | None = None
        self._field_values: FieldValueService | None = None
        self._field_value_changes: FieldValueChangesService | None = None
        self._files: EntityFileService | None = None
        self._relationships: RelationshipStrengthService | None = None
        self._auth: AuthService | None = None
        self._rate_limits: RateLimitService | None = None

    @classmethod
    def from_env(
        cls,
        *,
        env_var: str = _DEFAULT_API_KEY_ENV_VAR,
        load_dotenv: bool = False,
        dotenv_path: str | os.PathLike[str] | None = None,
        dotenv_override: bool = False,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        policies: Policies | None = None,
        on_event: AnyEventHook | None = None,
        hook_error_policy: Literal["swallow", "raise"] = "swallow",
        **kwargs: Any,
    ) -> Affinity:
        """
        Create a client using an API key from the environment.

        By default, reads `AFFINITY_API_KEY`. For local development, you can optionally
        load a `.env` file (requires `python-dotenv`).
        """
        api_key = _api_key_from_env(
            env_var=env_var,
            load_dotenv=load_dotenv,
            dotenv_path=dotenv_path,
            dotenv_override=dotenv_override,
        )
        return cls(
            api_key=api_key,
            transport=transport,
            async_transport=async_transport,
            policies=policies,
            on_event=on_event,
            hook_error_policy=hook_error_policy,
            **kwargs,
        )

    @classmethod
    def read_only(
        cls,
        api_key: str,
        **kwargs: Any,
    ) -> Affinity:
        """
        Create a read-only client that blocks all write operations.

        This is a convenience factory equivalent to:
            Affinity(api_key, policies=Policies(write=WritePolicy.DENY))

        For env-based key resolution, use read_only_from_env() instead.

        Args:
            api_key: Affinity API key (required).
            **kwargs: Additional arguments passed to Affinity constructor
                (e.g., v1_base_url, v2_base_url, timeout).

        Returns:
            Affinity client configured to reject write operations.

        Example:
            >>> with Affinity.read_only("your-key") as client:
            ...     companies = client.companies.list()
            ...     client.companies.create(...)  # Raises WriteNotAllowedError
        """
        if "policies" in kwargs:
            raise ValueError(
                "Cannot specify 'policies' with read_only(); "
                "use Affinity() constructor directly for custom policies"
            )
        return cls(
            api_key=api_key,
            policies=Policies(write=WritePolicy.DENY),
            **kwargs,
        )

    @classmethod
    def read_only_from_env(
        cls,
        *,
        env_var: str = _DEFAULT_API_KEY_ENV_VAR,
        **kwargs: Any,
    ) -> Affinity:
        """
        Create a read-only client using API key from environment.

        Equivalent to Affinity.from_env() with WritePolicy.DENY.

        Args:
            env_var: Environment variable name (default: AFFINITY_API_KEY).
            **kwargs: Additional arguments passed to from_env().

        Example:
            >>> with Affinity.read_only_from_env() as client:
            ...     companies = client.companies.list()
        """
        if "policies" in kwargs:
            raise ValueError(
                "Cannot specify 'policies' with read_only_from_env(); "
                "use Affinity.from_env() directly for custom policies"
            )
        return cls.from_env(
            env_var=env_var,
            policies=Policies(write=WritePolicy.DENY),
            **kwargs,
        )

    def __enter__(self) -> Affinity:
        self._entered_context = True
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client and release resources."""
        if not self._closed:
            self._http.close()
            self._closed = True

    def __del__(self) -> None:
        """Warn if client was not properly closed."""
        # Use getattr to handle case where __init__ failed before setting _closed
        if not getattr(self, "_closed", True) and not getattr(self, "_entered_context", True):
            warnings.warn(
                "Affinity client was not closed. "
                "Use 'with Affinity.from_env() as client:' "
                "or call client.close() when done.",
                ResourceWarning,
                stacklevel=2,
            )
            # Still close to prevent actual resource leaks
            with contextlib.suppress(Exception):
                self.close()

    # =========================================================================
    # Service Properties (lazy initialization)
    # =========================================================================

    @property
    def companies(self) -> CompanyService:
        """Company (organization) operations."""
        if self._companies is None:
            self._companies = CompanyService(self._http)
        return self._companies

    @property
    def persons(self) -> PersonService:
        """Person (contact) operations."""
        if self._persons is None:
            self._persons = PersonService(self._http)
        return self._persons

    @property
    def lists(self) -> ListService:
        """List operations."""
        if self._lists is None:
            self._lists = ListService(self._http)
        return self._lists

    @property
    def opportunities(self) -> OpportunityService:
        """Opportunity operations."""
        if self._opportunities is None:
            self._opportunities = OpportunityService(self._http)
        return self._opportunities

    @property
    def tasks(self) -> TaskService:
        """Long-running task operations (polling, waiting)."""
        if self._tasks is None:
            self._tasks = TaskService(self._http)
        return self._tasks

    @property
    def notes(self) -> NoteService:
        """Note operations."""
        if self._notes is None:
            self._notes = NoteService(self._http)
        return self._notes

    @property
    def reminders(self) -> ReminderService:
        """Reminder operations."""
        if self._reminders is None:
            self._reminders = ReminderService(self._http)
        return self._reminders

    @property
    def webhooks(self) -> WebhookService:
        """Webhook subscription operations."""
        if self._webhooks is None:
            self._webhooks = WebhookService(self._http)
        return self._webhooks

    @property
    def interactions(self) -> InteractionService:
        """Interaction operations."""
        if self._interactions is None:
            self._interactions = InteractionService(self._http)
        return self._interactions

    @property
    def fields(self) -> FieldService:
        """Custom field operations."""
        if self._fields is None:
            self._fields = FieldService(self._http)
        return self._fields

    @property
    def field_values(self) -> FieldValueService:
        """Field value operations."""
        if self._field_values is None:
            self._field_values = FieldValueService(self._http)
        return self._field_values

    @property
    def field_value_changes(self) -> FieldValueChangesService:
        """Field value change history queries."""
        if self._field_value_changes is None:
            self._field_value_changes = FieldValueChangesService(self._http)
        return self._field_value_changes

    @property
    def files(self) -> EntityFileService:
        """Entity file operations."""
        if self._files is None:
            self._files = EntityFileService(self._http)
        return self._files

    @property
    def relationships(self) -> RelationshipStrengthService:
        """Relationship strength queries."""
        if self._relationships is None:
            self._relationships = RelationshipStrengthService(self._http)
        return self._relationships

    @property
    def auth(self) -> AuthService:
        """Authentication info."""
        if self._auth is None:
            self._auth = AuthService(self._http)
        return self._auth

    @property
    def rate_limits(self) -> RateLimitService:
        """Unified rate limit information (version-agnostic)."""
        if self._rate_limits is None:
            self._rate_limits = RateLimitService(self._http)
        return self._rate_limits

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def clear_cache(self) -> None:
        """Clear the response cache."""
        if self._http.cache:
            self._http.cache.clear()

    def whoami(self) -> WhoAmI:
        """Convenience wrapper for `client.auth.whoami()`."""
        return self.auth.whoami()

    # Note: dict-style `rate_limit_state` is intentionally not part of the public API.


# =============================================================================
# Async Client (same interface, async methods)
# =============================================================================

# The async client mirrors the sync client interface for core services (TR-009).


class AsyncAffinity:
    """
    Asynchronous Affinity API client.

    Same interface as Affinity but with async/await support.

    Example:
        ```python
        async with AsyncAffinity(api_key="your-key") as client:
            async for company in client.companies.all():
                print(company.name)
        ```
    """

    def __init__(
        self,
        api_key: str,
        *,
        v1_base_url: str = V1_BASE_URL,
        v2_base_url: str = V2_BASE_URL,
        v1_auth_mode: Literal["bearer", "basic"] = "bearer",
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        enable_beta_endpoints: bool = False,
        allow_insecure_download_redirects: bool = False,
        expected_v2_version: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        enable_cache: bool = False,
        cache_ttl: float = 300.0,
        log_requests: bool = False,
        on_request: RequestHook | None = None,
        on_response: ResponseHook | None = None,
        on_error: ErrorHook | None = None,
        on_event: AnyEventHook | None = None,
        hook_error_policy: Literal["swallow", "raise"] = "swallow",
        policies: Policies | None = None,
    ):
        """
        Initialize the async Affinity client.

        Args:
            api_key: Your Affinity API key
            v1_base_url: V1 API base URL (default: https://api.affinity.co)
            v2_base_url: V2 API base URL (default: https://api.affinity.co/v2)
            v1_auth_mode: Auth mode for V1 API ("bearer" or "basic")
            transport: Optional `httpx` transport (advanced; useful for mocking in tests)
            async_transport: Optional async `httpx` transport (advanced; useful for mocking in
                tests)
            enable_beta_endpoints: Enable beta V2 endpoints
            allow_insecure_download_redirects: Allow `http://` redirects for file downloads.
                Not recommended; prefer HTTPS-only downloads.
            expected_v2_version: Expected V2 API version for diagnostics (e.g.,
                "2024-01-01"). Used to detect version compatibility issues.
                See TR-015.
            timeout: Request timeout in seconds
            max_retries: Maximum retries for rate-limited requests
            enable_cache: Enable response caching for field metadata
            cache_ttl: Cache TTL in seconds
            log_requests: Log all HTTP requests (for debugging)
            on_request: Hook called before each request (DX-008)
            on_response: Hook called after each response (DX-008)
            on_error: Hook called when a request raises (DX-008)
            on_event: Event hook called for request/response lifecycle events (DX-008)
            hook_error_policy: What to do if hooks raise ("swallow" or "raise")
        """
        config = ClientConfig(
            api_key=api_key,
            v1_base_url=v1_base_url,
            v2_base_url=v2_base_url,
            v1_auth_mode=v1_auth_mode,
            transport=transport,
            async_transport=async_transport,
            enable_beta_endpoints=enable_beta_endpoints,
            allow_insecure_download_redirects=allow_insecure_download_redirects,
            expected_v2_version=expected_v2_version,
            timeout=timeout,
            max_retries=max_retries,
            enable_cache=enable_cache,
            cache_ttl=cache_ttl,
            log_requests=log_requests,
            on_request=on_request,
            on_response=on_response,
            on_error=on_error,
            on_event=on_event,
            hook_error_policy=hook_error_policy,
            policies=policies or Policies(),
        )
        self._http = AsyncHTTPClient(config)

        # Resource management tracking
        self._closed = False
        self._entered_context = False

        self._companies: AsyncCompanyService | None = None
        self._persons: AsyncPersonService | None = None
        self._opportunities: AsyncOpportunityService | None = None
        self._lists: AsyncListService | None = None
        self._tasks: AsyncTaskService | None = None
        self._notes: AsyncNoteService | None = None
        self._reminders: AsyncReminderService | None = None
        self._webhooks: AsyncWebhookService | None = None
        self._interactions: AsyncInteractionService | None = None
        self._fields: AsyncFieldService | None = None
        self._field_values: AsyncFieldValueService | None = None
        self._field_value_changes: AsyncFieldValueChangesService | None = None
        self._files: AsyncEntityFileService | None = None
        self._relationships: AsyncRelationshipStrengthService | None = None
        self._auth: AsyncAuthService | None = None
        self._rate_limits: AsyncRateLimitService | None = None

    @classmethod
    def from_env(
        cls,
        *,
        env_var: str = _DEFAULT_API_KEY_ENV_VAR,
        load_dotenv: bool = False,
        dotenv_path: str | os.PathLike[str] | None = None,
        dotenv_override: bool = False,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        policies: Policies | None = None,
        on_event: AnyEventHook | None = None,
        hook_error_policy: Literal["swallow", "raise"] = "swallow",
        **kwargs: Any,
    ) -> AsyncAffinity:
        """
        Create an async client using an API key from the environment.

        By default, reads `AFFINITY_API_KEY`. For local development, you can optionally
        load a `.env` file (requires `python-dotenv`).
        """
        api_key = _api_key_from_env(
            env_var=env_var,
            load_dotenv=load_dotenv,
            dotenv_path=dotenv_path,
            dotenv_override=dotenv_override,
        )
        return cls(
            api_key=api_key,
            transport=transport,
            async_transport=async_transport,
            policies=policies,
            on_event=on_event,
            hook_error_policy=hook_error_policy,
            **kwargs,
        )

    @classmethod
    def read_only(
        cls,
        api_key: str,
        **kwargs: Any,
    ) -> AsyncAffinity:
        """
        Create a read-only async client that blocks all write operations.

        See Affinity.read_only() for details.
        """
        if "policies" in kwargs:
            raise ValueError(
                "Cannot specify 'policies' with read_only(); "
                "use AsyncAffinity() constructor directly for custom policies"
            )
        return cls(
            api_key=api_key,
            policies=Policies(write=WritePolicy.DENY),
            **kwargs,
        )

    @classmethod
    def read_only_from_env(
        cls,
        *,
        env_var: str = _DEFAULT_API_KEY_ENV_VAR,
        **kwargs: Any,
    ) -> AsyncAffinity:
        """
        Create a read-only async client using API key from environment.

        See Affinity.read_only_from_env() for details.
        """
        if "policies" in kwargs:
            raise ValueError(
                "Cannot specify 'policies' with read_only_from_env(); "
                "use AsyncAffinity.from_env() directly for custom policies"
            )
        return cls.from_env(
            env_var=env_var,
            policies=Policies(write=WritePolicy.DENY),
            **kwargs,
        )

    @property
    def companies(self) -> AsyncCompanyService:
        """Company (organization) operations."""
        if self._companies is None:
            self._companies = AsyncCompanyService(self._http)
        return self._companies

    @property
    def persons(self) -> AsyncPersonService:
        """Person (contact) operations."""
        if self._persons is None:
            self._persons = AsyncPersonService(self._http)
        return self._persons

    @property
    def opportunities(self) -> AsyncOpportunityService:
        """Opportunity operations."""
        if self._opportunities is None:
            self._opportunities = AsyncOpportunityService(self._http)
        return self._opportunities

    @property
    def lists(self) -> AsyncListService:
        """List and list entry operations."""
        if self._lists is None:
            self._lists = AsyncListService(self._http)
        return self._lists

    @property
    def tasks(self) -> AsyncTaskService:
        """Long-running task operations (polling, waiting)."""
        if self._tasks is None:
            self._tasks = AsyncTaskService(self._http)
        return self._tasks

    @property
    def notes(self) -> AsyncNoteService:
        """Note operations."""
        if self._notes is None:
            self._notes = AsyncNoteService(self._http)
        return self._notes

    @property
    def reminders(self) -> AsyncReminderService:
        """Reminder operations."""
        if self._reminders is None:
            self._reminders = AsyncReminderService(self._http)
        return self._reminders

    @property
    def webhooks(self) -> AsyncWebhookService:
        """Webhook subscription operations."""
        if self._webhooks is None:
            self._webhooks = AsyncWebhookService(self._http)
        return self._webhooks

    @property
    def interactions(self) -> AsyncInteractionService:
        """Interaction operations."""
        if self._interactions is None:
            self._interactions = AsyncInteractionService(self._http)
        return self._interactions

    @property
    def fields(self) -> AsyncFieldService:
        """Custom field operations."""
        if self._fields is None:
            self._fields = AsyncFieldService(self._http)
        return self._fields

    @property
    def field_values(self) -> AsyncFieldValueService:
        """Field value operations."""
        if self._field_values is None:
            self._field_values = AsyncFieldValueService(self._http)
        return self._field_values

    @property
    def field_value_changes(self) -> AsyncFieldValueChangesService:
        """Field value change history queries."""
        if self._field_value_changes is None:
            self._field_value_changes = AsyncFieldValueChangesService(self._http)
        return self._field_value_changes

    @property
    def files(self) -> AsyncEntityFileService:
        """Entity file operations."""
        if self._files is None:
            self._files = AsyncEntityFileService(self._http)
        return self._files

    @property
    def relationships(self) -> AsyncRelationshipStrengthService:
        """Relationship strength queries."""
        if self._relationships is None:
            self._relationships = AsyncRelationshipStrengthService(self._http)
        return self._relationships

    @property
    def auth(self) -> AsyncAuthService:
        """Authentication info."""
        if self._auth is None:
            self._auth = AsyncAuthService(self._http)
        return self._auth

    @property
    def rate_limits(self) -> AsyncRateLimitService:
        """Unified rate limit information (version-agnostic)."""
        if self._rate_limits is None:
            self._rate_limits = AsyncRateLimitService(self._http)
        return self._rate_limits

    async def __aenter__(self) -> AsyncAffinity:
        """Enter an async context and return this client."""
        self._entered_context = True
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit the async context and close the underlying HTTP client."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        if not self._closed:
            await self._http.close()
            self._closed = True

    def __del__(self) -> None:
        """Warn if client was not properly closed.

        NOTE: We intentionally do NOT attempt async cleanup here.
        asyncio.create_task() in __del__ is unsafe - the task may be garbage
        collected before completion, leading to resource leaks or errors.
        Users must use context managers or call close() explicitly.
        """
        # Use getattr to handle case where __init__ failed before setting _closed
        if not getattr(self, "_closed", True) and not getattr(self, "_entered_context", True):
            warnings.warn(
                "AsyncAffinity client was not closed. "
                "Use 'async with AsyncAffinity.from_env() as client:' "
                "or call await client.close() when done.",
                ResourceWarning,
                stacklevel=2,
            )

    def clear_cache(self) -> None:
        """Clear the response cache."""
        if self._http.cache:
            self._http.cache.clear()

    async def whoami(self) -> WhoAmI:
        """Convenience wrapper for `client.auth.whoami()`."""
        return await self.auth.whoami()
