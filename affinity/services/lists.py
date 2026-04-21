"""
Lists and List Entries service.

Provides operations for managing lists (spreadsheets) and their entries (rows).
"""

from __future__ import annotations

import builtins
import re
import time
import warnings
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ..compare import normalize_value
from ..exceptions import AffinityError, FilterParseError, NotFoundError, ValidationError
from ..filters import FilterExpression
from ..filters import parse as parse_filter
from ..models.entities import (
    AffinityList,
    FieldMetadata,
    FieldValues,
    ListCreate,
    ListEntry,
    ListEntryWithEntity,
    SavedView,
)
from ..models.pagination import (
    AsyncPageIterator,
    BatchOperationResponse,
    FilterStats,
    PageIterator,
    PaginatedResponse,
    PaginationInfo,
)
from ..models.types import (
    AnyFieldId,
    CompanyId,
    FieldType,
    FieldValueType,
    ListEntryId,
    ListId,
    ListType,
    OpportunityId,
    PersonId,
    SavedViewId,
)

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient


_LIST_SAVED_VIEWS_CURSOR_RE = re.compile(r"/lists/(?P<list_id>\d+)/saved-views(?:/|$)")

_SIZE_CACHE_TTL = 300  # 5 minutes

T = TypeVar("T", bound=BaseModel)


def _parse_filter_with_hint(filter_string: str) -> FilterExpression:
    """Parse a filter string, adding helpful hints on failure.

    Raises:
        FilterParseError: If the filter cannot be parsed, with a hint about
            quoting multi-word values.
    """
    try:
        return parse_filter(filter_string)
    except ValueError as e:
        # Add hint about quoting for common issues
        hint = (
            f"Invalid filter: {e}\n"
            "Hint: Multi-word values must be quoted. "
            "Example: --filter 'Status=\"Intro Meeting\"'"
        )
        raise FilterParseError(hint) from e


def _safe_model_validate(model: type[T], payload: Any, *, context: str | None = None) -> T:
    label = context or getattr(model, "__name__", "response")
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        raise AffinityError(
            f"Invalid API response while parsing {label}.",
            response_body=payload,
        ) from exc


def _saved_views_list_id_from_cursor(cursor: str) -> int | None:
    try:
        path = urlsplit(cursor).path or ""
    except Exception:
        return None
    m = _LIST_SAVED_VIEWS_CURSOR_RE.search(path)
    if not m:
        return None
    try:
        return int(m.group("list_id"))
    except ValueError:
        return None


class ListService:
    """
    Service for managing lists.

    Lists are spreadsheet-like collections of people, companies, or opportunities.
    """

    def __init__(self, client: HTTPClient):
        self._client = client
        self._resolve_cache: dict[tuple[str, ListType | None], AffinityList | None] = {}
        self._size_cache: dict[ListId, tuple[float, int]] = {}  # (timestamp, size)

    def entries(self, list_id: ListId) -> ListEntryService:
        """
        Get a ListEntryService for a specific list.

        This is the explicit path for retrieving "full row" data via list entries.
        """
        return ListEntryService(self._client, list_id)

    # =========================================================================
    # List Operations (V2 for read, V1 for write)
    # =========================================================================

    def list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[AffinityList]:
        """
        Get all lists accessible to you.

        Args:
            limit: Maximum results per page.
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).

        Returns:
            Paginated list of lists (without field metadata)
        """
        if cursor is not None:
            if limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = self._client.get_url(cursor)
        else:
            if limit is not None and limit <= 0:
                raise ValueError("'limit' must be > 0")
            params: dict[str, Any] = {}
            if limit is not None:
                params["limit"] = limit
            data = self._client.get("/lists", params=params or None)

        return PaginatedResponse[AffinityList](
            data=[
                _safe_model_validate(AffinityList, list_item) for list_item in data.get("data", [])
            ],
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    def get_first(self) -> AffinityList | None:
        """Get the first list, or None if no lists exist."""
        page = self.list(limit=1)
        return page.data[0] if page.data else None

    def pages(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Iterator[PaginatedResponse[AffinityList]]:
        """
        Iterate list pages (not items), yielding `PaginatedResponse[AffinityList]`.

        This is useful for ETL scripts that want checkpoint/resume via `page.next_cursor`.
        """
        if cursor is not None and limit is not None:
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        page = self.list(limit=limit) if cursor is None else self.list(cursor=cursor)
        while True:
            yield page
            if not page.has_next:
                return
            next_cursor = page.next_cursor
            if next_cursor is None or next_cursor == requested_cursor:
                return
            requested_cursor = next_cursor
            page = self.list(cursor=next_cursor)

    def all(self) -> Iterator[AffinityList]:
        """Iterate through all accessible lists."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[AffinityList]:
            if next_url:
                data = self._client.get_url(next_url)
                return PaginatedResponse[AffinityList](
                    data=[
                        _safe_model_validate(AffinityList, list_item)
                        for list_item in data.get("data", [])
                    ],
                    pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
                )
            return self.list()

        return PageIterator(fetch_page)

    def iter(self) -> Iterator[AffinityList]:
        """
        Auto-paginate all lists.

        Alias for `all()` (FR-006 public contract).
        """
        return self.all()

    def get(self, list_id: ListId) -> AffinityList:
        """
        Get a single list by ID.

        Includes field metadata for the list.

        Note: Uses V1 API because V2's listSize field is undocumented and
        returns incorrect values (often 0 for non-empty lists).
        """
        data = self._client.get(f"/lists/{list_id}", v1=True)
        return _safe_model_validate(AffinityList, data)

    def get_size(self, list_id: ListId, *, force: bool = False) -> int:
        """
        Get accurate list size. Uses V1 API, cached for 5 minutes.

        Args:
            list_id: The list ID.
            force: If True, bypass cache and fetch fresh value from API.

        Note: The V2 API's listSize field is unreliable (often returns 0 for
        non-empty lists). This method uses the V1 API which returns accurate values.
        """
        if not force and list_id in self._size_cache:
            cached_at, size = self._size_cache[list_id]
            if time.monotonic() - cached_at < _SIZE_CACHE_TTL:
                return size

        lst = self.get(list_id)
        size = lst._list_size_hint
        self._size_cache[list_id] = (time.monotonic(), size)
        return size

    def resolve(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> AffinityList | None:
        """
        Find a single list by name (optionally filtered by type).

        Notes:
        - This iterates list pages client-side (the API does not expose a list-search endpoint).
        - Results are cached in-memory on this service instance. If you call this frequently,
          reuse the client, or persist the resolved `ListId` in your own configuration.

        If multiple matches exist, returns the first match in server-provided order.
        """
        key = (name.lower(), list_type)
        if key in self._resolve_cache:
            return self._resolve_cache[key]

        for item in self.all():
            if item.name.lower() == name.lower() and (list_type is None or item.type == list_type):
                self._resolve_cache[key] = item
                return item

        self._resolve_cache[key] = None
        return None

    def resolve_all(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> builtins.list[AffinityList]:
        """
        Find all lists matching a name (optionally filtered by type).

        Notes:
        - This iterates list pages client-side (the API does not expose a list-search endpoint).
        - Unlike `resolve()`, this does not cache results.
        """
        matches: builtins.list[AffinityList] = []
        name_lower = name.lower()
        for item in self.all():
            if item.name.lower() != name_lower:
                continue
            if list_type is not None and item.type != list_type:
                continue
            matches.append(item)
        return matches

    def create(self, data: ListCreate) -> AffinityList:
        """
        Create a new list.

        Uses V1 API.
        """
        payload = data.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        if not data.additional_permissions:
            payload.pop("additional_permissions", None)

        result = self._client.post("/lists", json=payload, v1=True)

        # Invalidate cache
        if self._client.cache:
            self._client.cache.invalidate_prefix("list")
        self._resolve_cache.clear()

        return _safe_model_validate(AffinityList, result)

    # =========================================================================
    # Field Operations
    # =========================================================================

    def get_fields(
        self,
        list_id: ListId,
        *,
        field_types: Sequence[FieldType] | None = None,
    ) -> builtins.list[FieldMetadata]:
        """
        Get fields (columns) for a list.

        Includes list-specific, global, enriched, and relationship intelligence fields.
        Cached for performance.
        """
        params: dict[str, Any] = {}
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = self._client.get(
            f"/lists/{list_id}/fields",
            params=params or None,
            cache_key=f"list_{list_id}_fields:{','.join(field_types or [])}",
            cache_ttl=300,
        )

        return [_safe_model_validate(FieldMetadata, f) for f in data.get("data", [])]

    # =========================================================================
    # Saved View Operations
    # =========================================================================

    def get_saved_views(
        self,
        list_id: ListId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[SavedView]:
        """
        Get saved views for a list.

        Args:
            list_id: List id for the initial request.
            limit: Maximum results per page.
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).
        """
        if cursor is not None:
            if limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            cursor_list_id = _saved_views_list_id_from_cursor(cursor)
            if cursor_list_id is not None and int(list_id) != cursor_list_id:
                raise ValueError(
                    f"Cursor does not match list_id: cursor is for list {cursor_list_id}, "
                    f"requested list_id is {int(list_id)}"
                )
            data = self._client.get_url(cursor)
        else:
            if limit is not None and limit <= 0:
                raise ValueError("'limit' must be > 0")
            params: dict[str, Any] = {}
            if limit is not None:
                params["limit"] = limit
            data = self._client.get(f"/lists/{list_id}/saved-views", params=params or None)

        return PaginatedResponse[SavedView](
            data=[_safe_model_validate(SavedView, v) for v in data.get("data", [])],
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    def saved_views_pages(
        self,
        list_id: ListId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Iterator[PaginatedResponse[SavedView]]:
        """Iterate saved view pages, yielding `PaginatedResponse[SavedView]`."""
        if cursor is not None and limit is not None:
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        page = (
            self.get_saved_views(list_id, limit=limit)
            if cursor is None
            else self.get_saved_views(list_id, cursor=cursor)
        )
        while True:
            yield page
            if not page.has_next:
                return
            next_cursor = page.next_cursor
            if next_cursor is None or next_cursor == requested_cursor:
                return
            requested_cursor = next_cursor
            page = self.get_saved_views(list_id, cursor=next_cursor)

    def saved_views_all(self, list_id: ListId) -> Iterator[SavedView]:
        """Iterate all saved views for a list."""
        for page in self.saved_views_pages(list_id):
            yield from page.data

    def get_saved_view(self, list_id: ListId, view_id: SavedViewId) -> SavedView:
        """Get a single saved view."""
        data = self._client.get(f"/lists/{list_id}/saved-views/{view_id}")
        return _safe_model_validate(SavedView, data)


def _entry_to_filter_dict(entry: ListEntryWithEntity) -> dict[str, Any]:
    """
    Convert a ListEntryWithEntity to a dict for client-side filter matching.

    Extracts field values by name from the entity's fields_raw (V2 API format).
    This allows FilterExpression.matches() to evaluate against field values.

    Uses normalize_value() from compare.py to extract text values from
    dropdown dicts and multi-select arrays - single source of truth for normalization.
    """
    result: dict[str, Any] = {}

    # Extract field values from entity.fields_raw (V2 API format)
    if entry.entity is not None:
        fields_raw = getattr(entry.entity, "fields_raw", None)
        if isinstance(fields_raw, builtins.list):
            for field_obj in fields_raw:
                if isinstance(field_obj, dict):
                    field_name = field_obj.get("name")
                    if field_name:
                        value_wrapper = field_obj.get("value")
                        if isinstance(value_wrapper, dict):
                            data = value_wrapper.get("data")
                            # Use normalize_value() to extract text from dropdowns/multi-select
                            result[field_name] = normalize_value(data)
                        else:
                            result[field_name] = value_wrapper

    # Also add basic entity properties for filtering
    if entry.entity is not None:
        if hasattr(entry.entity, "name"):
            result["name"] = entry.entity.name
        if hasattr(entry.entity, "domain"):
            result["domain"] = entry.entity.domain
        if hasattr(entry.entity, "primary_email"):
            result["primary_email"] = entry.entity.primary_email

    # Row-level metadata keys — same shape the CLI exposes to agents via JSON/CSV
    # output. Adding these here means a filter like `entityName =~ "Acme"` matches
    # the row-level value instead of silently missing because the key was absent.
    # These four reads mirror _entry_to_row in list_cmds.py:1949-1954 exactly —
    # if the CLI output keys ever diverge from these, this dict is stale.
    result["listEntryId"] = int(entry.id)
    if entry.entity is not None:
        result["entityId"] = int(entry.entity.id)
        entity_name = getattr(entry.entity, "name", None)
        if entity_name is None and hasattr(entry.entity, "full_name"):
            entity_name = entry.entity.full_name
        result["entityName"] = entity_name
    result["entityType"] = entry.type

    return result


# Warning message for client-side filtering
_CLIENT_SIDE_FILTER_WARNING = (
    "The Affinity V2 API does not support server-side filtering on list entries. "
    "Filtering is being applied client-side after fetching data. "
    "For large lists, consider using saved views instead (--saved-view)."
)


class ListEntryService:
    """
    Service for managing list entries (rows).

    List entries connect entities (people, companies, opportunities) to lists
    and hold list-specific field values.
    """

    def __init__(self, client: HTTPClient, list_id: ListId):
        self._client = client
        self._list_id = list_id

    def _all_entity_list_entries_v2(self, path: str) -> builtins.list[ListEntry]:
        """
        Fetch all list entries for a single entity across all lists (V2 API).

        Used for list membership helpers to avoid enumerating an entire list.
        """
        # Retry on 404 to handle V1→V2 propagation delay after recent writes
        last_error: NotFoundError | None = None
        for attempt in range(3):
            try:
                entries: builtins.list[ListEntry] = []
                data = self._client.get(path)

                while True:
                    entries.extend(
                        _safe_model_validate(ListEntry, item) for item in data.get("data", [])
                    )
                    pagination = _safe_model_validate(PaginationInfo, data.get("pagination", {}))
                    if not pagination.next_cursor:
                        break
                    data = self._client.get_url(pagination.next_cursor)
                return entries
            except NotFoundError as e:
                last_error = e
                if attempt < 2:  # Don't sleep after last attempt
                    time.sleep(0.5 * (attempt + 1))  # 0.5s, 1s backoff
        raise last_error  # type: ignore[misc]

        return entries

    # =========================================================================
    # Read Operations (V2 API)
    # =========================================================================

    def list(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListEntryWithEntity]:
        """
        Get list entries with entity data and field values.

        Args:
            field_ids: Specific field IDs to include
            field_types: Field types to include
            filter: Filter expression (applied client-side; API doesn't support it)
            limit: Maximum results per page
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).

        Returns:
            Paginated list entries with entity data

        Note:
            The Affinity V2 API does not support server-side filtering on list entries.
            When a filter is provided, it is applied client-side after fetching data.
            For large lists, consider using saved views for server-side filtering.
        """
        # Parse filter expression if provided
        filter_expr: FilterExpression | None = None
        if filter is not None:
            if isinstance(filter, str):
                # Treat whitespace-only strings as no filter
                stripped = filter.strip()
                if stripped:
                    filter_expr = _parse_filter_with_hint(stripped)
            else:
                filter_expr = filter
            # Emit warning about client-side filtering
            warnings.warn(_CLIENT_SIDE_FILTER_WARNING, UserWarning, stacklevel=2)

        if cursor is not None:
            if field_ids or field_types or filter_expr is not None or limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = self._client.get_url(cursor)
        else:
            if limit is not None and limit <= 0:
                raise ValueError("'limit' must be > 0")
            params: dict[str, Any] = {}
            if field_ids:
                params["fieldIds"] = [str(field_id) for field_id in field_ids]
            if field_types:
                params["fieldTypes"] = [field_type.value for field_type in field_types]
            # NOTE: filter is NOT sent to API - it doesn't support filtering
            if limit is not None:
                params["limit"] = limit

            data = self._client.get(
                f"/lists/{self._list_id}/list-entries",
                params=params or None,
            )

        # Parse entries
        entries = [_safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])]

        # Apply client-side filtering if filter was provided
        if filter_expr is not None:
            entries = [e for e in entries if filter_expr.matches(_entry_to_filter_dict(e))]

        return PaginatedResponse[ListEntryWithEntity](
            data=entries,
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    def get_first(
        self,
        *,
        filter: str | FilterExpression | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
    ) -> ListEntryWithEntity | None:
        """
        Get the first list entry matching the filter, or None.

        WARNING: List entry filtering is applied client-side (the V2 API
        silently ignores filter parameters). This means the method may
        fetch multiple pages before finding a match. If no entry matches,
        **every page in the list is fetched** before returning None.
        On large lists (25K+ entries), this can result in hundreds of
        API calls. A UserWarning is emitted when filter is used.

        For efficient server-side filtering, use saved views via CLI.
        """
        return next(
            self.iter(filter=filter, field_ids=field_ids, field_types=field_types),
            None,
        )

    def pages(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        progress_callback: Callable[[FilterStats], None] | None = None,
    ) -> Iterator[PaginatedResponse[ListEntryWithEntity]]:
        """Iterate list-entry pages, yielding `PaginatedResponse[ListEntryWithEntity]`.

        Use ``pages()`` when you need page-level control for batch processing,
        cursor-based resumption, or progress tracking on unfiltered queries.
        Use ``iter()`` for most cases, especially with filters.

        Args:
            progress_callback: Optional callback called after each physical page
                fetch during filtered queries. Receives FilterStats with current
                scanned/matched counts for real-time progress updates.

        Note:
            Filtering is applied client-side (Affinity V2 API does not support
            server-side filtering on list entries). When a filter is provided,
            pages are "virtualized" - the method fetches physical pages internally
            and accumulates filtered results until a full virtual page is ready.
            This ensures consistent page sizes and fast time-to-first-results.
        """
        if cursor is not None and (
            field_ids
            or field_types
            or filter is not None
            or limit is not None
            or progress_callback is not None
        ):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )

        # Parse filter once for all pages (since list() with cursor can't accept filter)
        filter_expr: FilterExpression | None = None
        if filter is not None:
            if isinstance(filter, str):
                stripped = filter.strip()
                if stripped:
                    filter_expr = _parse_filter_with_hint(stripped)
            else:
                filter_expr = filter

        # No filter: use simple pagination (original behavior)
        if filter_expr is None:
            requested_cursor = cursor
            page = (
                self.list(field_ids=field_ids, field_types=field_types, limit=limit)
                if cursor is None
                else self.list(cursor=cursor)
            )
            while True:
                yield page
                if not page.has_next:
                    return
                next_cursor = page.next_cursor
                if next_cursor is None or next_cursor == requested_cursor:
                    return
                requested_cursor = next_cursor
                page = self.list(cursor=next_cursor)
            return

        # With filter: use virtualized pagination for consistent page sizes
        # and fast time-to-first-results
        virtual_page_size = limit if limit is not None else 100
        buffer: list[ListEntryWithEntity] = []
        physical_cursor: str | None = None
        has_more_physical = True

        # Track filter stats for progress reporting
        total_scanned = 0
        total_matched = 0

        # Fetch first physical page WITHOUT filter so we can track accurate counts
        first_page = self.list(field_ids=field_ids, field_types=field_types, limit=limit)
        # Track scanned count (before filtering)
        total_scanned += len(first_page.data)
        # Apply filter manually to first page (same as subsequent pages)
        filtered_first = [
            e for e in first_page.data if filter_expr.matches(_entry_to_filter_dict(e))
        ]
        total_matched += len(filtered_first)
        buffer.extend(filtered_first)
        physical_cursor = first_page.next_cursor
        has_more_physical = first_page.has_next and physical_cursor is not None

        # Report initial progress after first page fetch
        if progress_callback is not None:
            progress_callback(FilterStats(scanned=total_scanned, matched=total_matched))

        while True:
            # Yield virtual page when buffer is full or no more data
            if len(buffer) >= virtual_page_size or not has_more_physical:
                if not buffer and not has_more_physical:
                    return  # No more data
                # Slice off one virtual page
                page_data = buffer[:virtual_page_size]
                buffer = buffer[virtual_page_size:]
                # has_next is true if we have more buffered or more physical pages
                has_next = len(buffer) > 0 or has_more_physical
                virtual_page = PaginatedResponse[ListEntryWithEntity](
                    data=page_data,
                    pagination=PaginationInfo(
                        next_cursor=None,  # Virtual pages don't support cursor resumption
                        prev_cursor=None,
                    ),
                )
                # Override has_next since we know better than the pagination info
                virtual_page._has_next_override = has_next
                # Add filter stats for progress tracking
                virtual_page._filter_stats = FilterStats(
                    scanned=total_scanned, matched=total_matched
                )
                yield virtual_page
                if not has_next:
                    return
                continue

            # Need more data - fetch next physical page
            if not has_more_physical:
                continue  # Will yield remaining buffer above

            physical_page = self.list(cursor=physical_cursor)
            # Track scanned count (before filtering)
            total_scanned += len(physical_page.data)
            # Apply filter manually to subsequent pages
            filtered_data = [
                e for e in physical_page.data if filter_expr.matches(_entry_to_filter_dict(e))
            ]
            total_matched += len(filtered_data)
            buffer.extend(filtered_data)
            physical_cursor = physical_page.next_cursor
            has_more_physical = physical_page.has_next and physical_cursor is not None

            # Report progress after each physical page fetch
            if progress_callback is not None:
                progress_callback(FilterStats(scanned=total_scanned, matched=total_matched))

    def all(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
    ) -> Iterator[ListEntryWithEntity]:
        """
        Iterate through all list entries with automatic pagination.

        Note:
            The Affinity V2 API does not support server-side filtering on list entries.
            When a filter is provided, it is applied client-side after fetching all data.
            For large lists, consider using saved views for server-side filtering.
        """
        # Parse filter once for all pages
        filter_expr: FilterExpression | None = None
        if filter is not None:
            if isinstance(filter, str):
                # Treat whitespace-only strings as no filter
                stripped = filter.strip()
                if stripped:
                    filter_expr = _parse_filter_with_hint(stripped)
            else:
                filter_expr = filter
            # Emit warning once for the entire iteration
            warnings.warn(_CLIENT_SIDE_FILTER_WARNING, UserWarning, stacklevel=2)

        def fetch_page(next_url: str | None) -> PaginatedResponse[ListEntryWithEntity]:
            if next_url:
                data = self._client.get_url(next_url)
                entries = [
                    _safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])
                ]
                return PaginatedResponse[ListEntryWithEntity](
                    data=entries,
                    pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
                )
            # First page - don't pass filter to list() to avoid duplicate warnings
            return self.list(
                field_ids=field_ids,
                field_types=field_types,
            )

        # Iterate through all pages, applying filter if provided
        for entry in PageIterator(fetch_page):
            if filter_expr is None or filter_expr.matches(_entry_to_filter_dict(entry)):
                yield entry

    def iter(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
    ) -> Iterator[ListEntryWithEntity]:
        """
        Auto-paginate all list entries.

        Alias for `all()` (FR-006 public contract).
        """
        return self.all(field_ids=field_ids, field_types=field_types, filter=filter)

    def get(self, entry_id: ListEntryId) -> ListEntryWithEntity:
        """Get a single list entry by ID."""
        data = self._client.get(f"/lists/{self._list_id}/list-entries/{entry_id}")
        return _safe_model_validate(ListEntryWithEntity, data)

    def from_saved_view(
        self,
        view_id: SavedViewId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
    ) -> PaginatedResponse[ListEntryWithEntity]:
        """
        Get list entries from a saved view.

        Args:
            view_id: The saved view ID
            field_ids: Specific field IDs to include in the response
            field_types: Field types to include in the response
            limit: Maximum results per page

        Returns:
            Paginated list entries with entity data and field values

        Note:
            The saved view's filters are applied server-side.
            Field values are returned in entity.fields_raw as an array.
        """
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]
        if limit:
            params["limit"] = limit

        data = self._client.get(
            f"/lists/{self._list_id}/saved-views/{view_id}/list-entries",
            params=params or None,
        )

        return PaginatedResponse[ListEntryWithEntity](
            data=[_safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])],
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    # =========================================================================
    # Write Operations (V1 API for create/delete, V2 for field updates)
    # =========================================================================

    # -------------------------------------------------------------------------
    # Membership helpers (V2 for read, V1 for insert)
    # -------------------------------------------------------------------------

    def find_person(self, person_id: PersonId) -> ListEntry | None:
        """
        Return the first list entry for this person on this list (or None).

        Notes:
        - Affinity lists can contain duplicates. This returns the first match in
          the server-provided order. Use `find_all_person()` to surface all matches.
        """
        entries = self.find_all_person(person_id)
        return entries[0] if entries else None

    def find_all_person(self, person_id: PersonId) -> builtins.list[ListEntry]:
        """
        Return all list entries for this person on this list (may be empty).

        Affinity allows the same entity to appear multiple times on a list.
        """
        all_entries = self._all_entity_list_entries_v2(f"/persons/{person_id}/list-entries")
        return [entry for entry in all_entries if entry.list_id == self._list_id]

    def ensure_person(
        self,
        person_id: PersonId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """
        Ensure a person is on this list (idempotent by default).

        Returns:
            The first existing list entry if present; otherwise creates a new one.

        Notes:
        - This method performs an existence check to avoid accidental duplicates.
          To intentionally create duplicates, call `add_person()` directly.
        """
        existing = self.find_person(person_id)
        if existing is not None:
            return existing
        return self.add_person(person_id, creator_id=creator_id)

    def find_company(self, company_id: CompanyId) -> ListEntry | None:
        """
        Return the first list entry for this company on this list (or None).

        Notes:
        - Affinity lists can contain duplicates. This returns the first match in
          the server-provided order. Use `find_all_company()` to surface all matches.
        """
        entries = self.find_all_company(company_id)
        return entries[0] if entries else None

    def find_all_company(self, company_id: CompanyId) -> builtins.list[ListEntry]:
        """
        Return all list entries for this company on this list (may be empty).

        Affinity allows the same entity to appear multiple times on a list.
        """
        all_entries = self._all_entity_list_entries_v2(f"/companies/{company_id}/list-entries")
        return [entry for entry in all_entries if entry.list_id == self._list_id]

    def ensure_company(
        self,
        company_id: CompanyId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """
        Ensure a company is on this list (idempotent by default).

        Returns:
            The first existing list entry if present; otherwise creates a new one.

        Notes:
        - This method performs an existence check to avoid accidental duplicates.
          To intentionally create duplicates, call `add_company()` directly.
        """
        existing = self.find_company(company_id)
        if existing is not None:
            return existing
        return self.add_company(company_id, creator_id=creator_id)

    def add_person(
        self,
        person_id: PersonId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add a person to this list."""
        return self._create_entry(int(person_id), creator_id)

    def add_company(
        self,
        company_id: CompanyId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add a company to this list."""
        return self._create_entry(int(company_id), creator_id)

    def add_opportunity(
        self,
        opportunity_id: OpportunityId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add an opportunity to this list."""
        return self._create_entry(int(opportunity_id), creator_id)

    def _create_entry(
        self,
        entity_id: int,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Internal method to create a list entry.

        Note: The entity type must match the list type:
        - Person lists only accept person IDs via add_person()
        - Company lists only accept company IDs via add_company()
        - Opportunity lists only accept opportunity IDs via add_opportunity()
        """
        payload: dict[str, Any] = {"entity_id": entity_id}
        if creator_id:
            payload["creator_id"] = creator_id

        try:
            result = self._client.post(
                f"/lists/{self._list_id}/list-entries",
                json=payload,
                v1=True,
            )
        except ValidationError as e:
            # 422 + param=entity_id means entity type doesn't match list type
            if e.status_code == 422 and e.param == "entity_id":
                raise ValidationError(
                    f"Cannot add entity {entity_id} to list {self._list_id}. "
                    f"The entity type must match the list type "
                    f"(person lists accept persons, company lists accept companies, "
                    f"opportunity lists accept opportunities).",
                    param=e.param,
                    status_code=e.status_code,
                    response_body=e.response_body,
                    diagnostics=e.diagnostics,
                ) from e
            raise

        return _safe_model_validate(ListEntry, result)

    def delete(self, entry_id: ListEntryId) -> bool:
        """
        Remove a list entry (row) from the list.

        Note: This only removes the entry from the list, not the entity itself.
        """
        result = self._client.delete(
            f"/lists/{self._list_id}/list-entries/{entry_id}",
            v1=True,
        )
        return bool(result.get("success", False))

    # =========================================================================
    # Field Value Operations (V2 API)
    # =========================================================================

    def get_field_values(
        self,
        entry_id: ListEntryId,
        *,
        ids: Sequence[str | AnyFieldId] | None = None,
        types: Sequence[FieldType] | None = None,
    ) -> FieldValues:
        """Get field values for a list entry.

        Args:
            entry_id: The list entry ID.
            ids: Optional field IDs to filter (server-side).
            types: Optional field types to filter (server-side).

        Returns:
            FieldValues container with full field objects.
            Use .get_value(field_id) to extract unwrapped values.
        """
        params: dict[str, Any] = {}
        if ids:
            params["ids"] = [str(fid) for fid in ids]
        if types:
            params["types"] = [ft.value for ft in types]
        data = self._client.get(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields",
            params=params or None,
        )
        # V2 API returns {"data": [{field}, ...], "pagination": {...}}
        # FieldValues._coerce_from_api converts list -> dict keyed by field ID,
        # preserving full field objects (id, name, type, value).
        fields_list = data.get("data", [])
        return _safe_model_validate(FieldValues, fields_list)

    def get_field_value(
        self,
        entry_id: ListEntryId,
        field_id: AnyFieldId,
    ) -> Any:
        """
        Get a single field value.

        Returns the unwrapped value data. V2 API returns values in nested format
        like {"type": "text", "data": "value"} - this method extracts just the "data" part.
        """
        data = self._client.get(f"/lists/{self._list_id}/list-entries/{entry_id}/fields/{field_id}")
        value = data.get("value")
        # V2 API returns nested {"type": "...", "data": ...} - extract the data
        if isinstance(value, dict) and "data" in value:
            return value["data"]
        return value

    def update_field_value(
        self,
        entry_id: ListEntryId,
        field_id: AnyFieldId,
        value: Any,
        value_type: FieldValueType | str | None = None,
    ) -> FieldValues:
        """
        Update a single field value on a list entry.

        Args:
            entry_id: The list entry
            field_id: The field to update
            value: New value (type depends on field type)
            value_type: The field value type (e.g., FieldValueType.TEXT).
                Required by the V2 API. If not provided, attempts to infer
                from the value (str→text, int/float→number, datetime→datetime).

        Returns:
            Updated field value data
        """
        # Determine the type string for the API
        if value_type is not None:
            type_str = value_type.value if isinstance(value_type, FieldValueType) else value_type
        elif isinstance(value, str):
            type_str = "text"
        elif isinstance(value, (int, float)):
            type_str = "number"
        else:
            # Default to text for unknown types
            type_str = "text"

        result = self._client.post(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields/{field_id}",
            json={"value": {"type": type_str, "data": value}},
        )
        return _safe_model_validate(FieldValues, result)

    def batch_update_fields(
        self,
        entry_id: ListEntryId,
        updates: dict[AnyFieldId, Any],
    ) -> BatchOperationResponse:
        """
        Update multiple field values at once.

        More efficient than individual updates for multiple fields.

        Args:
            entry_id: The list entry
            updates: Dict mapping field IDs to new values. Values are auto-typed
                (str→text, int/float→number, otherwise→text).

        Returns:
            Batch operation response with success/failure per field
        """

        def infer_type(value: Any) -> str:
            if isinstance(value, str):
                return "text"
            elif isinstance(value, (int, float)):
                return "number"
            return "text"

        update_items = [
            {
                "id": str(field_id),
                "value": {"type": infer_type(value), "data": value},
            }
            for field_id, value in updates.items()
        ]

        result = self._client.patch(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields",
            json={"operation": "update-fields", "updates": update_items},
        )

        return _safe_model_validate(BatchOperationResponse, result)


class AsyncListService:
    """Async list operations (TR-009)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client
        self._resolve_cache: dict[tuple[str, ListType | None], AffinityList | None] = {}
        self._size_cache: dict[ListId, tuple[float, int]] = {}  # (timestamp, size)

    def entries(self, list_id: ListId) -> AsyncListEntryService:
        """
        Get an AsyncListEntryService for a specific list.

        This is the explicit path for retrieving "full row" data via list entries.
        """
        return AsyncListEntryService(self._client, list_id)

    async def list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[AffinityList]:
        """
        Get all lists accessible to you.

        Args:
            limit: Maximum results per page.
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).

        Returns:
            Paginated list of lists (without field metadata)
        """
        if cursor is not None:
            if limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = await self._client.get_url(cursor)
        else:
            if limit is not None and limit <= 0:
                raise ValueError("'limit' must be > 0")
            params: dict[str, Any] = {}
            if limit is not None:
                params["limit"] = limit
            data = await self._client.get("/lists", params=params or None)
        return PaginatedResponse[AffinityList](
            data=[
                _safe_model_validate(AffinityList, list_item) for list_item in data.get("data", [])
            ],
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    async def get_first(self) -> AffinityList | None:
        """Get the first list, or None if no lists exist."""
        page = await self.list(limit=1)
        return page.data[0] if page.data else None

    async def pages(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[AffinityList]]:
        """
        Iterate list pages (not items), yielding `PaginatedResponse[AffinityList]`.

        This is useful for ETL scripts that want checkpoint/resume via `page.next_cursor`.
        """
        if cursor is not None and limit is not None:
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        page = await self.list(limit=limit) if cursor is None else await self.list(cursor=cursor)
        while True:
            yield page
            if not page.has_next:
                return
            next_cursor = page.next_cursor
            if next_cursor is None or next_cursor == requested_cursor:
                return
            requested_cursor = next_cursor
            page = await self.list(cursor=next_cursor)

    def all(self) -> AsyncIterator[AffinityList]:
        """Iterate through all accessible lists."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[AffinityList]:
            if next_url:
                data = await self._client.get_url(next_url)
                return PaginatedResponse[AffinityList](
                    data=[
                        _safe_model_validate(AffinityList, list_item)
                        for list_item in data.get("data", [])
                    ],
                    pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
                )
            return await self.list()

        return AsyncPageIterator(fetch_page)

    def iter(self) -> AsyncIterator[AffinityList]:
        """
        Auto-paginate all lists.

        Alias for `all()` (FR-006 public contract).
        """
        return self.all()

    # =========================================================================
    # Saved View Operations
    # =========================================================================

    async def get_saved_views(
        self,
        list_id: ListId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[SavedView]:
        """
        Get saved views for a list.

        Args:
            list_id: List id for the initial request.
            limit: Maximum results per page.
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).
        """
        if cursor is not None:
            if limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            cursor_list_id = _saved_views_list_id_from_cursor(cursor)
            if cursor_list_id is not None and int(list_id) != cursor_list_id:
                raise ValueError(
                    f"Cursor does not match list_id: cursor is for list {cursor_list_id}, "
                    f"requested list_id is {int(list_id)}"
                )
            data = await self._client.get_url(cursor)
        else:
            if limit is not None and limit <= 0:
                raise ValueError("'limit' must be > 0")
            params: dict[str, Any] = {}
            if limit is not None:
                params["limit"] = limit
            data = await self._client.get(f"/lists/{list_id}/saved-views", params=params or None)

        return PaginatedResponse[SavedView](
            data=[_safe_model_validate(SavedView, v) for v in data.get("data", [])],
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    async def saved_views_pages(
        self,
        list_id: ListId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[SavedView]]:
        """Iterate saved view pages, yielding `PaginatedResponse[SavedView]`."""
        if cursor is not None and limit is not None:
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        page = (
            await self.get_saved_views(list_id, limit=limit)
            if cursor is None
            else await self.get_saved_views(list_id, cursor=cursor)
        )
        while True:
            yield page
            if not page.has_next:
                return
            next_cursor = page.next_cursor
            if next_cursor is None or next_cursor == requested_cursor:
                return
            requested_cursor = next_cursor
            page = await self.get_saved_views(list_id, cursor=next_cursor)

    async def saved_views_all(self, list_id: ListId) -> AsyncIterator[SavedView]:
        """Iterate all saved views for a list."""
        async for page in self.saved_views_pages(list_id):
            for item in page.data:
                yield item

    async def get_saved_view(self, list_id: ListId, view_id: SavedViewId) -> SavedView:
        """Get a single saved view."""
        data = await self._client.get(f"/lists/{list_id}/saved-views/{view_id}")
        return _safe_model_validate(SavedView, data)

    async def get(self, list_id: ListId) -> AffinityList:
        """
        Get a single list by ID.

        Includes field metadata for the list.

        Note: Uses V1 API because V2's listSize field is undocumented and
        returns incorrect values (often 0 for non-empty lists).
        """
        data = await self._client.get(f"/lists/{list_id}", v1=True)
        return _safe_model_validate(AffinityList, data)

    async def get_size(self, list_id: ListId, *, force: bool = False) -> int:
        """
        Get accurate list size. Uses V1 API, cached for 5 minutes.

        Args:
            list_id: The list ID.
            force: If True, bypass cache and fetch fresh value from API.

        Note: The V2 API's listSize field is unreliable (often returns 0 for
        non-empty lists). This method uses the V1 API which returns accurate values.
        """
        if not force and list_id in self._size_cache:
            cached_at, size = self._size_cache[list_id]
            if time.monotonic() - cached_at < _SIZE_CACHE_TTL:
                return size

        lst = await self.get(list_id)
        size = lst._list_size_hint
        self._size_cache[list_id] = (time.monotonic(), size)
        return size

    async def resolve(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> AffinityList | None:
        """
        Find a single list by name (optionally filtered by type).

        Notes:
        - This iterates list pages client-side (the API does not expose a list-search endpoint).
        - Results are cached in-memory on this service instance. If you call this frequently,
          reuse the client, or persist the resolved `ListId` in your own configuration.

        If multiple matches exist, returns the first match in server-provided order.
        """
        key = (name.lower(), list_type)
        if key in self._resolve_cache:
            return self._resolve_cache[key]

        async for item in self.all():
            if item.name.lower() == name.lower() and (list_type is None or item.type == list_type):
                self._resolve_cache[key] = item
                return item

        self._resolve_cache[key] = None
        return None

    async def resolve_all(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> builtins.list[AffinityList]:
        """
        Find all lists matching a name (optionally filtered by type).

        Notes:
        - This iterates list pages client-side (the API does not expose a list-search endpoint).
        - Unlike `resolve()`, this does not cache results.
        """
        matches: builtins.list[AffinityList] = []
        name_lower = name.lower()
        async for item in self.all():
            if item.name.lower() != name_lower:
                continue
            if list_type is not None and item.type != list_type:
                continue
            matches.append(item)
        return matches

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    async def create(self, data: ListCreate) -> AffinityList:
        """
        Create a new list.

        Uses V1 API.
        """
        payload = data.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        if not data.additional_permissions:
            payload.pop("additional_permissions", None)

        result = await self._client.post("/lists", json=payload, v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("list")
        self._resolve_cache.clear()

        return _safe_model_validate(AffinityList, result)

    # =========================================================================
    # Field Operations
    # =========================================================================

    async def get_fields(
        self,
        list_id: ListId,
        *,
        field_types: Sequence[FieldType] | None = None,
    ) -> builtins.list[FieldMetadata]:
        """
        Get fields (columns) for a list.

        Includes list-specific, global, enriched, and relationship intelligence fields.
        Cached for performance.
        """
        params: dict[str, Any] = {}
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = await self._client.get(
            f"/lists/{list_id}/fields",
            params=params or None,
            cache_key=f"list_{list_id}_fields:{','.join(field_types or [])}",
            cache_ttl=300,
        )

        return [_safe_model_validate(FieldMetadata, f) for f in data.get("data", [])]


class AsyncListEntryService:
    """Async list entry operations (TR-009)."""

    def __init__(self, client: AsyncHTTPClient, list_id: ListId):
        self._client = client
        self._list_id = list_id

    async def _all_entity_list_entries_v2(self, path: str) -> builtins.list[ListEntry]:
        """
        Fetch all list entries for a single entity across all lists (V2 API).

        Used for list membership helpers to avoid enumerating an entire list.
        """
        entries: builtins.list[ListEntry] = []
        data = await self._client.get(path)

        while True:
            entries.extend(_safe_model_validate(ListEntry, item) for item in data.get("data", []))
            pagination = _safe_model_validate(PaginationInfo, data.get("pagination", {}))
            if not pagination.next_cursor:
                break
            data = await self._client.get_url(pagination.next_cursor)

        return entries

    async def list(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListEntryWithEntity]:
        """
        Get list entries with entity data and field values.

        Args:
            field_ids: Specific field IDs to include
            field_types: Field types to include
            filter: Filter expression (applied client-side; API doesn't support it)
            limit: Maximum results per page
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).

        Returns:
            Paginated list entries with entity data

        Note:
            The Affinity V2 API does not support server-side filtering on list entries.
            When a filter is provided, it is applied client-side after fetching data.
            For large lists, consider using saved views for server-side filtering.
        """
        # Parse filter expression if provided
        filter_expr: FilterExpression | None = None
        if filter is not None:
            if isinstance(filter, str):
                # Treat whitespace-only strings as no filter
                stripped = filter.strip()
                if stripped:
                    filter_expr = _parse_filter_with_hint(stripped)
            else:
                filter_expr = filter
            # Emit warning about client-side filtering
            warnings.warn(_CLIENT_SIDE_FILTER_WARNING, UserWarning, stacklevel=2)

        if cursor is not None:
            if field_ids or field_types or filter_expr is not None or limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = await self._client.get_url(cursor)
        else:
            if limit is not None and limit <= 0:
                raise ValueError("'limit' must be > 0")
            params: dict[str, Any] = {}
            if field_ids:
                params["fieldIds"] = [str(field_id) for field_id in field_ids]
            if field_types:
                params["fieldTypes"] = [field_type.value for field_type in field_types]
            # NOTE: filter is NOT sent to API - it doesn't support filtering
            if limit is not None:
                params["limit"] = limit

            data = await self._client.get(
                f"/lists/{self._list_id}/list-entries",
                params=params or None,
            )

        # Parse entries
        entries = [_safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])]

        # Apply client-side filtering if filter was provided
        if filter_expr is not None:
            entries = [e for e in entries if filter_expr.matches(_entry_to_filter_dict(e))]

        return PaginatedResponse[ListEntryWithEntity](
            data=entries,
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    async def get_first(
        self,
        *,
        filter: str | FilterExpression | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
    ) -> ListEntryWithEntity | None:
        """
        Get the first list entry matching the filter, or None.

        See ListEntryService.get_first() for details and caveats about
        client-side filtering on list entries.
        """
        async for entry in self.iter(filter=filter, field_ids=field_ids, field_types=field_types):
            return entry
        return None

    async def pages(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        progress_callback: Callable[[FilterStats], None] | None = None,
    ) -> AsyncIterator[PaginatedResponse[ListEntryWithEntity]]:
        """Iterate list-entry pages, yielding `PaginatedResponse[ListEntryWithEntity]`.

        Use ``pages()`` when you need page-level control for batch processing,
        cursor-based resumption, or progress tracking on unfiltered queries.
        Use ``iter()`` for most cases, especially with filters.

        Args:
            progress_callback: Optional callback called after each physical page
                fetch during filtered queries. Receives FilterStats with current
                scanned/matched counts for real-time progress updates.

        Note:
            Filtering is applied client-side (Affinity V2 API does not support
            server-side filtering on list entries). When a filter is provided,
            pages are "virtualized" - the method fetches physical pages internally
            and accumulates filtered results until a full virtual page is ready.
            This ensures consistent page sizes and fast time-to-first-results.
        """
        if cursor is not None and (
            field_ids
            or field_types
            or filter is not None
            or limit is not None
            or progress_callback is not None
        ):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )

        # Parse filter once for all pages (since list() with cursor can't accept filter)
        filter_expr: FilterExpression | None = None
        if filter is not None:
            if isinstance(filter, str):
                stripped = filter.strip()
                if stripped:
                    filter_expr = _parse_filter_with_hint(stripped)
            else:
                filter_expr = filter

        # No filter: use simple pagination (original behavior)
        if filter_expr is None:
            requested_cursor = cursor
            page = (
                await self.list(field_ids=field_ids, field_types=field_types, limit=limit)
                if cursor is None
                else await self.list(cursor=cursor)
            )
            while True:
                yield page
                if not page.has_next:
                    return
                next_cursor = page.next_cursor
                if next_cursor is None or next_cursor == requested_cursor:
                    return
                requested_cursor = next_cursor
                page = await self.list(cursor=next_cursor)
            return

        # With filter: use virtualized pagination for consistent page sizes
        # and fast time-to-first-results
        virtual_page_size = limit if limit is not None else 100
        buffer: list[ListEntryWithEntity] = []
        physical_cursor: str | None = None
        has_more_physical = True

        # Track filter stats for progress reporting
        total_scanned = 0
        total_matched = 0

        # Fetch first physical page WITHOUT filter so we can track accurate counts
        first_page = await self.list(field_ids=field_ids, field_types=field_types, limit=limit)
        # Track scanned count (before filtering)
        total_scanned += len(first_page.data)
        # Apply filter manually to first page (same as subsequent pages)
        filtered_first = [
            e for e in first_page.data if filter_expr.matches(_entry_to_filter_dict(e))
        ]
        total_matched += len(filtered_first)
        buffer.extend(filtered_first)
        physical_cursor = first_page.next_cursor
        has_more_physical = first_page.has_next and physical_cursor is not None

        # Report initial progress after first page fetch
        if progress_callback is not None:
            progress_callback(FilterStats(scanned=total_scanned, matched=total_matched))

        while True:
            # Yield virtual page when buffer is full or no more data
            if len(buffer) >= virtual_page_size or not has_more_physical:
                if not buffer and not has_more_physical:
                    return  # No more data
                # Slice off one virtual page
                page_data = buffer[:virtual_page_size]
                buffer = buffer[virtual_page_size:]
                # has_next is true if we have more buffered or more physical pages
                has_next = len(buffer) > 0 or has_more_physical
                virtual_page = PaginatedResponse[ListEntryWithEntity](
                    data=page_data,
                    pagination=PaginationInfo(
                        next_cursor=None,  # Virtual pages don't support cursor resumption
                        prev_cursor=None,
                    ),
                )
                # Override has_next since we know better than the pagination info
                virtual_page._has_next_override = has_next
                # Add filter stats for progress tracking
                virtual_page._filter_stats = FilterStats(
                    scanned=total_scanned, matched=total_matched
                )
                yield virtual_page
                if not has_next:
                    return
                continue

            # Need more data - fetch next physical page
            if not has_more_physical:
                continue  # Will yield remaining buffer above

            physical_page = await self.list(cursor=physical_cursor)
            # Track scanned count (before filtering)
            total_scanned += len(physical_page.data)
            # Apply filter manually to subsequent pages
            filtered_data = [
                e for e in physical_page.data if filter_expr.matches(_entry_to_filter_dict(e))
            ]
            total_matched += len(filtered_data)
            buffer.extend(filtered_data)
            physical_cursor = physical_page.next_cursor
            has_more_physical = physical_page.has_next and physical_cursor is not None

            # Report progress after each physical page fetch
            if progress_callback is not None:
                progress_callback(FilterStats(scanned=total_scanned, matched=total_matched))

    async def all(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
    ) -> AsyncIterator[ListEntryWithEntity]:
        """
        Iterate through all list entries with automatic pagination.

        Note:
            The Affinity V2 API does not support server-side filtering on list entries.
            When a filter is provided, it is applied client-side after fetching all data.
            For large lists, consider using saved views for server-side filtering.
        """
        # Parse filter once for all pages
        filter_expr: FilterExpression | None = None
        if filter is not None:
            if isinstance(filter, str):
                # Treat whitespace-only strings as no filter
                stripped = filter.strip()
                if stripped:
                    filter_expr = _parse_filter_with_hint(stripped)
            else:
                filter_expr = filter
            # Emit warning once for the entire iteration
            warnings.warn(_CLIENT_SIDE_FILTER_WARNING, UserWarning, stacklevel=2)

        async def fetch_page(next_url: str | None) -> PaginatedResponse[ListEntryWithEntity]:
            if next_url:
                data = await self._client.get_url(next_url)
                entries = [
                    _safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])
                ]
                return PaginatedResponse[ListEntryWithEntity](
                    data=entries,
                    pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
                )
            # First page - don't pass filter to list() to avoid duplicate warnings
            return await self.list(
                field_ids=field_ids,
                field_types=field_types,
            )

        # Iterate through all pages, applying filter if provided
        async for entry in AsyncPageIterator(fetch_page):
            if filter_expr is None or filter_expr.matches(_entry_to_filter_dict(entry)):
                yield entry

    async def iter(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
    ) -> AsyncIterator[ListEntryWithEntity]:
        """
        Auto-paginate all list entries.

        Alias for `all()` (FR-006 public contract).
        """
        async for entry in self.all(field_ids=field_ids, field_types=field_types, filter=filter):
            yield entry

    async def get(self, entry_id: ListEntryId) -> ListEntryWithEntity:
        """Get a single list entry by ID."""
        data = await self._client.get(f"/lists/{self._list_id}/list-entries/{entry_id}")
        return _safe_model_validate(ListEntryWithEntity, data)

    # -------------------------------------------------------------------------
    # Membership helpers (V2 for read only)
    # -------------------------------------------------------------------------

    async def find_person(self, person_id: PersonId) -> ListEntry | None:
        """
        Return the first list entry for this person on this list (or None).

        Notes:
        - Affinity lists can contain duplicates. This returns the first match in
          the server-provided order. Use `find_all_person()` to surface all matches.
        """
        entries = await self.find_all_person(person_id)
        return entries[0] if entries else None

    async def find_all_person(self, person_id: PersonId) -> builtins.list[ListEntry]:
        """
        Return all list entries for this person on this list (may be empty).

        Affinity allows the same entity to appear multiple times on a list.
        """
        all_entries = await self._all_entity_list_entries_v2(f"/persons/{person_id}/list-entries")
        return [entry for entry in all_entries if entry.list_id == self._list_id]

    async def find_company(self, company_id: CompanyId) -> ListEntry | None:
        """
        Return the first list entry for this company on this list (or None).

        Notes:
        - Affinity lists can contain duplicates. This returns the first match in
          the server-provided order. Use `find_all_company()` to surface all matches.
        """
        entries = await self.find_all_company(company_id)
        return entries[0] if entries else None

    async def find_all_company(self, company_id: CompanyId) -> builtins.list[ListEntry]:
        """
        Return all list entries for this company on this list (may be empty).

        Affinity allows the same entity to appear multiple times on a list.
        """
        all_entries = await self._all_entity_list_entries_v2(
            f"/companies/{company_id}/list-entries"
        )
        return [entry for entry in all_entries if entry.list_id == self._list_id]

    async def ensure_person(
        self,
        person_id: PersonId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """
        Ensure a person is on this list (idempotent by default).

        Returns:
            The first existing list entry if present; otherwise creates a new one.

        Notes:
        - This method performs an existence check to avoid accidental duplicates.
          To intentionally create duplicates, call `add_person()` directly.
        """
        existing = await self.find_person(person_id)
        if existing is not None:
            return existing
        return await self.add_person(person_id, creator_id=creator_id)

    async def ensure_company(
        self,
        company_id: CompanyId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """
        Ensure a company is on this list (idempotent by default).

        Returns:
            The first existing list entry if present; otherwise creates a new one.

        Notes:
        - This method performs an existence check to avoid accidental duplicates.
          To intentionally create duplicates, call `add_company()` directly.
        """
        existing = await self.find_company(company_id)
        if existing is not None:
            return existing
        return await self.add_company(company_id, creator_id=creator_id)

    async def add_person(
        self,
        person_id: PersonId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add a person to this list."""
        return await self._create_entry(int(person_id), creator_id)

    async def add_company(
        self,
        company_id: CompanyId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add a company to this list."""
        return await self._create_entry(int(company_id), creator_id)

    async def add_opportunity(
        self,
        opportunity_id: OpportunityId,
        *,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add an opportunity to this list."""
        return await self._create_entry(int(opportunity_id), creator_id)

    async def _create_entry(
        self,
        entity_id: int,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Internal method to create a list entry.

        Note: The entity type must match the list type:
        - Person lists only accept person IDs via add_person()
        - Company lists only accept company IDs via add_company()
        - Opportunity lists only accept opportunity IDs via add_opportunity()
        """
        payload: dict[str, Any] = {"entity_id": entity_id}
        if creator_id:
            payload["creator_id"] = creator_id

        try:
            result = await self._client.post(
                f"/lists/{self._list_id}/list-entries",
                json=payload,
                v1=True,
            )
        except ValidationError as e:
            # 422 + param=entity_id means entity type doesn't match list type
            if e.status_code == 422 and e.param == "entity_id":
                raise ValidationError(
                    f"Cannot add entity {entity_id} to list {self._list_id}. "
                    f"The entity type must match the list type "
                    f"(person lists accept persons, company lists accept companies, "
                    f"opportunity lists accept opportunities).",
                    param=e.param,
                    status_code=e.status_code,
                    response_body=e.response_body,
                    diagnostics=e.diagnostics,
                ) from e
            raise

        return _safe_model_validate(ListEntry, result)

    async def delete(self, entry_id: ListEntryId) -> bool:
        """
        Remove a list entry (row) from the list.

        Note: This only removes the entry from the list, not the entity itself.
        """
        result = await self._client.delete(
            f"/lists/{self._list_id}/list-entries/{entry_id}",
            v1=True,
        )
        return bool(result.get("success", False))

    # =========================================================================
    # Field Value Operations (V2 API)
    # =========================================================================

    async def get_field_values(
        self,
        entry_id: ListEntryId,
        *,
        ids: Sequence[str | AnyFieldId] | None = None,
        types: Sequence[FieldType] | None = None,
    ) -> FieldValues:
        """Get field values for a list entry.

        Args:
            entry_id: The list entry ID.
            ids: Optional field IDs to filter (server-side).
            types: Optional field types to filter (server-side).

        Returns:
            FieldValues container with full field objects.
            Use .get_value(field_id) to extract unwrapped values.
        """
        params: dict[str, Any] = {}
        if ids:
            params["ids"] = [str(fid) for fid in ids]
        if types:
            params["types"] = [ft.value for ft in types]
        data = await self._client.get(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields",
            params=params or None,
        )
        # V2 API returns {"data": [{field}, ...], "pagination": {...}}
        # FieldValues._coerce_from_api converts list -> dict keyed by field ID,
        # preserving full field objects (id, name, type, value).
        fields_list = data.get("data", [])
        return _safe_model_validate(FieldValues, fields_list)

    async def get_field_value(
        self,
        entry_id: ListEntryId,
        field_id: AnyFieldId,
    ) -> Any:
        """
        Get a single field value.

        Returns the unwrapped value data. V2 API returns values in nested format
        like {"type": "text", "data": "value"} - this method extracts just the "data" part.
        """
        data = await self._client.get(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields/{field_id}"
        )
        value = data.get("value")
        # V2 API returns nested {"type": "...", "data": ...} - extract the data
        if isinstance(value, dict) and "data" in value:
            return value["data"]
        return value

    async def update_field_value(
        self,
        entry_id: ListEntryId,
        field_id: AnyFieldId,
        value: Any,
    ) -> FieldValues:
        """
        Update a single field value on a list entry.

        Args:
            entry_id: The list entry
            field_id: The field to update
            value: New value (type depends on field type)

        Returns:
            Updated field value data
        """
        result = await self._client.post(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields/{field_id}",
            json={"value": value},
        )
        return _safe_model_validate(FieldValues, result)

    async def batch_update_fields(
        self,
        entry_id: ListEntryId,
        updates: dict[AnyFieldId, Any],
    ) -> BatchOperationResponse:
        """
        Update multiple field values at once.

        More efficient than individual updates for multiple fields.

        Args:
            entry_id: The list entry
            updates: Dict mapping field IDs to new values. Values are auto-typed
                (str→text, int/float→number, otherwise→text).

        Returns:
            Batch operation response with success/failure per field
        """

        def infer_type(value: Any) -> str:
            if isinstance(value, str):
                return "text"
            elif isinstance(value, (int, float)):
                return "number"
            return "text"

        update_items = [
            {
                "id": str(field_id),
                "value": {"type": infer_type(value), "data": value},
            }
            for field_id, value in updates.items()
        ]

        result = await self._client.patch(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields",
            json={"operation": "update-fields", "updates": update_items},
        )

        return _safe_model_validate(BatchOperationResponse, result)
