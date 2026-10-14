"""
Lists and List Entries service.

Provides operations for managing lists (spreadsheets) and their entries (rows).
"""

from __future__ import annotations

import builtins
import re
import time
import warnings
import weakref
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast
from urllib.parse import urlsplit

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from .._filtering.search import (
    CursorKind,
    FilterPlan,
    SearchCache,
    SearchCursor,
    decode_cursor,
    dropdown_fields,
    encode_cursor,
    plan_filter,
    reject_raw_filters,
)
from ..compare import normalize_value
from ..exceptions import (
    AffinityError,
    AuthorizationError,
    ClientSideFilterWarning,
    FilterParseError,
    NotFoundError,
    UnsupportedOperationError,
    ValidationError,
)
from ..filters import FilterExpression
from ..filters import parse as parse_filter
from ..models.entities import (
    AffinityList,
    DropdownOption,
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
    _CursorGuard,
)
from ..models.secondary import FieldValueChangeV2
from ..models.types import (
    AnyFieldId,
    CompanyId,
    FieldType,
    FieldValueType,
    ListEntryId,
    ListId,
    ListType,
    PersonId,
    SavedViewId,
)
from ._dropdown_options import (
    DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
    DropdownOptionColorName,
    DropdownOptionType,
    DropdownStatusCategory,
    create_body,
    option_path,
    options_path,
    parse_options,
    update_body,
)
from ._field_listing import build_fields_query
from ._field_updates import (
    _batch_update_items,
    _check_multi_value_size,
    _field_value_payload,
)
from ._org_reads import check_cursor_alone, check_positive_limit, page_of_raw
from ._search import SEARCH_INCLUDES, AsyncSearchMixin, SearchMixin
from ._single_field import FIELD_VALUE_ITEMS_MIN_API_VERSION, items_params
from .v1_only import _fvc_v2_page, _fvc_v2_params

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient


_LIST_SAVED_VIEWS_CURSOR_RE = re.compile(r"/lists/(?P<list_id>\d+)/saved-views(?:/|$)")

_SIZE_CACHE_TTL = 300  # 5 minutes

# get_size() results per HTTP client, so entry writes made through any service on that client
# (list entries, opportunities.create) can drop the list's cached size.
_SIZE_CACHES: weakref.WeakKeyDictionary[Any, dict[int, tuple[float, int]]] = (
    weakref.WeakKeyDictionary()
)


def _size_cache_for(client: Any) -> dict[int, tuple[float, int]]:
    try:
        return _SIZE_CACHES.setdefault(client, {})
    except TypeError:  # not weak-referenceable: a private cache, as before
        return {}


def invalidate_list_size(client: Any, list_id: int) -> None:
    """Forget the cached ``get_size`` of a list after a row was added to or removed from it."""
    try:
        cache = _SIZE_CACHES.get(client)
    except TypeError:
        return
    if cache:
        cache.pop(int(list_id), None)


def invalidate_all_list_sizes(client: Any) -> None:
    """Forget every cached ``get_size`` on this client: for writes that remove rows from lists
    the SDK can't name without another request (deleting an opportunity, person or company)."""
    try:
        cache = _SIZE_CACHES.get(client)
    except TypeError:
        return
    if cache:
        cache.clear()


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


def _exact_name_matches(
    items: Iterable[AffinityList], name: str, list_type: ListType | None
) -> builtins.list[AffinityList]:
    """Lists whose name equals ``name`` case-insensitively (and of ``list_type``, if given)."""
    name_lower = name.lower()
    return [
        item
        for item in items
        if item.name.lower() == name_lower and (list_type is None or item.type == list_type)
    ]


_LIST_TYPE_NAMES = {
    ListType.PERSON: "person",
    ListType.COMPANY: "company",
    ListType.OPPORTUNITY: "opportunity",
}


def _uses_v1_create(data: ListCreate) -> bool:
    # V2 can't set an owner or extra permissions at create (documented V1 exception)
    return data.owner_id is not None or bool(data.additional_permissions)


def _list_create_body(data: ListCreate) -> dict[str, Any]:
    return {
        "name": data.name,
        "type": _LIST_TYPE_NAMES[ListType(data.type)],
        "isPublic": data.is_public,
    }


def _v1_list_create_payload(data: ListCreate) -> dict[str, Any]:
    payload = data.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    if not data.additional_permissions:
        payload.pop("additional_permissions", None)
    return payload


class ListService:
    """
    Service for managing lists.

    Lists are spreadsheet-like collections of people, companies, or opportunities.
    """

    def __init__(self, client: HTTPClient):
        self._client = client
        self._resolve_cache: dict[tuple[str, ListType | None], AffinityList | None] = {}
        # (timestamp, size), shared by every list service on this client
        self._size_cache: dict[int, tuple[float, int]] = _size_cache_for(client)
        # Fields, dropdown options and refused searches for filtered reads, per list
        self._search_cache = SearchCache()

    def entries(self, list_id: ListId) -> ListEntryService:
        """
        Get a ListEntryService for a specific list.

        This is the explicit path for retrieving "full row" data via list entries.
        """
        return ListEntryService(self._client, list_id, self._search_cache)

    # =========================================================================
    # List Operations (V2 for read, V1 for write)
    # =========================================================================

    def list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        term: str | None = None,
    ) -> PaginatedResponse[AffinityList]:
        """
        Get all lists accessible to you.

        Args:
            limit: Maximum results per page.
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).
            term: Only lists whose name contains this text (case-insensitive substring,
                matched by Affinity). Kept across pages by the cursor.

        Returns:
            Paginated list of lists (without field metadata)
        """
        term = term or None
        if cursor is not None:
            if limit is not None or term is not None:
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
            if term is not None:
                params["term"] = term
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
        term: str | None = None,
    ) -> Iterator[PaginatedResponse[AffinityList]]:
        """
        Iterate list pages (not items), yielding `PaginatedResponse[AffinityList]`.

        This is useful for ETL scripts that want checkpoint/resume via `page.next_cursor`.
        ``term`` keeps only lists whose name contains it (case-insensitive, matched by Affinity).
        """
        term = term or None
        if cursor is not None and (limit is not None or term is not None):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        guard = _CursorGuard(cursor)
        page = self.list(limit=limit, term=term) if cursor is None else self.list(cursor=cursor)
        while True:
            yield page
            # Stop at the last page, or when paging can't progress (see _CursorGuard).
            if guard.last_page(requested_cursor, page):
                return
            requested_cursor = page.next_cursor
            page = self.list(cursor=requested_cursor)

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
        Get a single list by ID (V2).

        ``fields`` is not filled: use :meth:`get_fields`. For the number of entries use
        :meth:`get_size`.
        """
        data = self._client.get(f"/lists/{int(list_id)}")
        return _safe_model_validate(AffinityList, data)

    def get_size(self, list_id: ListId, *, force: bool = False) -> int:
        """
        Get accurate list size. Uses V1 API, cached for 5 minutes.

        Adding or removing a row through this client (``entries(list_id).add_*`` / ``delete``,
        ``opportunities.create``) drops the cached size; changes made elsewhere show after the
        cache expires, or with ``force=True``.

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

        # V2 has no list size: read it from V1
        data = self._client.get(f"/lists/{int(list_id)}", v1=True)
        size = _safe_model_validate(AffinityList, data)._list_size_hint
        self._size_cache[list_id] = (time.monotonic(), size)
        return size

    def resolve(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> AffinityList | None:
        """
        Find a single list by name (case-insensitive exact match; optionally filtered by type).

        Notes:
        - Affinity narrows the lists by name first (``term``); the exact comparison is made
          here. If that finds nothing, every list is checked (see `resolve_all()`).
        - Matches are cached in-memory on this service instance (a miss is not). If you call
          this frequently, reuse the client, or persist the resolved `ListId` in your own
          configuration.

        If multiple matches exist, returns the first match in server-provided order.
        """
        key = (name.lower(), list_type)
        if key in self._resolve_cache:
            return self._resolve_cache[key]

        matches = self.resolve_all(name=name, list_type=list_type)
        found = matches[0] if matches else None
        if found is not None:  # a miss isn't cached: the list may be created later
            self._resolve_cache[key] = found
        return found

    def resolve_all(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> builtins.list[AffinityList]:
        """
        Find all lists matching a name (case-insensitive exact match; optionally by type).

        Notes:
        - For an ASCII name, Affinity first narrows the lists by name (``term``, a
          case-insensitive substring match) and the exact comparison is made here. If that
          yields no match, every list is checked, so a difference in how Affinity folds case
          or whitespace can't hide a list. Non-ASCII names always check every list.
        - Unlike `resolve()`, this does not cache results.
        """
        term = name.strip()
        if term and term.isascii():
            candidates = (item for page in self.pages(term=term) for item in page.data)
            matches = _exact_name_matches(candidates, name, list_type)
            if matches:
                return matches
        return _exact_name_matches(self.all(), name, list_type)

    def create(self, data: ListCreate) -> AffinityList:
        """
        Create a list (V2); the caller becomes its creator and owner.

        A public list needs the "Share accessible Lists globally" permission. V2 can't set
        ``owner_id`` or ``additional_permissions``: with either, the list is created through V1.
        """
        if _uses_v1_create(data):
            result = self._client.post("/lists", json=_v1_list_create_payload(data), v1=True)
        else:
            result = self._client.post("/lists", json=_list_create_body(data))

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
        filter: str | None = None,
        includes: Sequence[str] | None = None,
    ) -> builtins.list[FieldMetadata]:
        """
        Get fields (columns) for a list.

        Includes list-specific, global, enriched, and relationship intelligence fields.
        Cached for performance.

        Args:
            filter: Filter on the field name: ``name="Location"`` (exact) or ``name=~Loc``
                (substring); both case-sensitive. Combine clauses with ``|``. Only ``name``
                is filterable; anything else is rejected with 400.
            includes: Extra properties per field: ``"filterability"``, ``"sortability"``
                (read them via ``is_filterable`` / ``is_sortable``).
        """
        params, cache_key = build_fields_query(
            f"list_{list_id}_fields:",
            filter=filter,
            includes=includes,
        )

        data = self._client.get_all_pages(
            f"/lists/{list_id}/fields",
            params=params or None,
            cache_key=cache_key,
            cache_ttl=300,
        )

        return [_safe_model_validate(FieldMetadata, f) for f in data.get("data", [])]

    # =========================================================================
    # Dropdown options of list fields
    # =========================================================================

    def get_field_dropdown_options(
        self, list_id: ListId, field_id: AnyFieldId, *, with_status_types: bool = False
    ) -> builtins.list[DropdownOption]:
        """All options of a list field's dropdown, ranked-dropdown or status field (every page).

        Older API versions report status options as ``ranked-dropdown`` without
        ``status_category``. ``with_status_types=True`` reads with API version
        {DROPDOWN_OPTION_WRITES_MIN_API_VERSION} (as the option writes do) to get the real
        ``status-dropdown`` type; a client pinned to an older version then raises
        ``ApiVersionTooOldError``.
        """
        data = self._client.get_all_pages(
            options_path(field_id, list_id=list_id, entity=None),
            params={"limit": 100},
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION if with_status_types else None,
        )
        return parse_options(data)

    def get_field_dropdown_option(
        self, list_id: ListId, field_id: AnyFieldId, option_id: int
    ) -> DropdownOption:
        """One option of a list field."""
        return DropdownOption.model_validate(
            self._client.get(option_path(list_id, field_id, option_id))
        )

    def create_field_dropdown_option(
        self,
        list_id: ListId,
        field_id: AnyFieldId,
        *,
        option_type: DropdownOptionType,
        text: str,
        rank: int | None = None,
        color: DropdownOptionColorName | None = None,
        status_category: DropdownStatusCategory | None = None,
        win_rate: int | None = None,
    ) -> DropdownOption:
        """Add an option to a list field.

        ``option_type`` must match the field: ``"dropdown"`` (text only), ``"ranked-dropdown"``
        (``rank`` and ``color`` required) or ``"status-dropdown"`` (``rank``, ``color`` and
        ``status_category`` required; ``win_rate`` 0-100 only for ``"open"``). Needs Affinity API
        version {DROPDOWN_OPTION_WRITES_MIN_API_VERSION} (sent for this call).
        """
        body = create_body(
            option_type=option_type,
            text=text,
            rank=rank,
            color=color,
            status_category=status_category,
            win_rate=win_rate,
        )
        data = self._client.post(
            options_path(field_id, list_id=list_id, entity=None),
            json=body,
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
        )
        return DropdownOption.model_validate(data)

    def update_field_dropdown_option(
        self,
        list_id: ListId,
        field_id: AnyFieldId,
        option_id: int,
        *,
        text: str | None = None,
        rank: int | None = None,
        color: DropdownOptionColorName | None = None,
        status_category: DropdownStatusCategory | None = None,
        win_rate: int | None = None,
    ) -> DropdownOption:
        """Change an option of a list field (only what you pass).

        A ``dropdown`` option can change its text; ``ranked-dropdown`` also rank and color;
        ``status-dropdown`` also status category and win rate. Renaming changes the value shown
        on every entry that has this option. Returns the option as read back afterwards.
        """
        body = update_body(
            text=text,
            rank=rank,
            color=color,
            status_category=status_category,
            win_rate=win_rate,
        )
        self._client.post(
            option_path(list_id, field_id, option_id),
            json=body,
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
        )
        # The API answers 204 with no body: read the option back
        return DropdownOption.model_validate(
            self._client.get(
                option_path(list_id, field_id, option_id),
                min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
            )
        )

    def delete_field_dropdown_option(
        self, list_id: ListId, field_id: AnyFieldId, option_id: int
    ) -> bool:
        """Delete an option of a list field. **Cannot be undone.**

        Affinity also clears this field on every list entry currently set to the option; those
        values cannot be recovered.
        """
        self._client.delete(
            option_path(list_id, field_id, option_id),
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
        )
        return True

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
        guard = _CursorGuard(cursor)
        page = (
            self.get_saved_views(list_id, limit=limit)
            if cursor is None
            else self.get_saved_views(list_id, cursor=cursor)
        )
        while True:
            yield page
            # Stop at the last page, or when paging can't progress (see _CursorGuard).
            if guard.last_page(requested_cursor, page):
                return
            requested_cursor = page.next_cursor
            page = self.get_saved_views(list_id, cursor=requested_cursor)

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

    # Extract field values from entity.fields_raw (V2 API format), by name and by field id
    # (ids written after names: a field literally named like another field's id loses)
    if entry.entity is not None:
        fields_raw = getattr(entry.entity, "fields_raw", None)
        if isinstance(fields_raw, builtins.list):
            by_id: dict[str, Any] = {}
            for field_obj in fields_raw:
                if isinstance(field_obj, dict):
                    field_name = field_obj.get("name")
                    value_wrapper = field_obj.get("value")
                    if isinstance(value_wrapper, dict):
                        # Use normalize_value() to extract text from dropdowns/multi-select
                        value = normalize_value(value_wrapper.get("data"))
                    else:
                        value = value_wrapper
                    if field_name:
                        result[field_name] = value
                    if field_obj.get("id") is not None:
                        by_id[str(field_obj["id"])] = value
            result.update(by_id)

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


def _fetch_field_ids(
    field_ids: Sequence[AnyFieldId] | None, plan: FilterPlan | None
) -> builtins.list[AnyFieldId] | None:
    """The requested fields plus the ones the filter tests (its local re-check reads them)."""
    ids: builtins.list[AnyFieldId] = [*(field_ids or [])]
    for fid in plan.field_ids if plan is not None else []:
        if fid not in [str(i) for i in ids]:
            ids.append(cast(AnyFieldId, fid))
    return ids or None


def _note_refusal(cache: SearchCache, scope: str, error: Exception) -> str:
    """Remember a refused search (403, 404, unsupported) for a while; a 400 is usually about
    this filter (or stale cached fields), so it only drops the cached fields."""
    if isinstance(error, ValidationError):
        cache.drop_scope(scope)
    else:
        cache.put(("refused", scope), True)
    return f"Affinity's search refused the filter ({error})"


def _warn_client_side(reason: str) -> None:
    warnings.warn(
        f"Filtering list entries locally: every entry is read and checked ({reason}).",
        ClientSideFilterWarning,
        stacklevel=4,
    )


def _filtered_page(
    page: PaginatedResponse[ListEntryWithEntity],
    expr: FilterExpression,
    kind: CursorKind,
    body: dict[str, Any] | None,
) -> tuple[builtins.list[ListEntryWithEntity], int, str | None]:
    matched = [e for e in page.data if expr.matches(_entry_to_filter_dict(e))]
    token = encode_cursor(kind, page.next_cursor, body, expr) if page.next_cursor else None
    return matched, len(page.data), token


def _page_with_cursor(
    entries: builtins.list[ListEntryWithEntity], token: str | None
) -> PaginatedResponse[ListEntryWithEntity]:
    return PaginatedResponse[ListEntryWithEntity](
        data=entries, pagination=PaginationInfo(next_cursor=token, prev_cursor=None)
    )


def _parse_entry_filter(filter: str | FilterExpression | None) -> FilterExpression | None:
    """The filter to apply (``None`` for no filter or a blank string); RawFilter refused."""
    if filter is None:
        return None
    if isinstance(filter, str):
        stripped = filter.strip()
        if not stripped:
            return None
        expr = _parse_filter_with_hint(stripped)
    else:
        expr = filter
    reject_raw_filters(expr)
    return expr


class ListEntryService(SearchMixin[ListEntryWithEntity]):
    """
    Service for managing list entries (rows).

    List entries connect entities (people, companies, opportunities) to lists
    and hold list-specific field values.
    """

    _search_model = ListEntryWithEntity

    def __init__(
        self, client: HTTPClient, list_id: ListId, cache: SearchCache | None = None
    ) -> None:
        self._client = client
        self._list_id = list_id
        self._cache = cache if cache is not None else SearchCache()

    def _search_path(self) -> str:
        return f"/lists/{self._list_id}/list-entries/search"

    def _search_fields(self) -> builtins.list[FieldMetadata]:
        key = ("fields", str(self._list_id))
        cached = self._cache.get(key)
        if cached is None:
            cached = ListService(self._client).get_fields(self._list_id, includes=SEARCH_INCLUDES)
            self._cache.put(key, cached)
        return list(cached)

    def _options(self, meta: FieldMetadata) -> builtins.list[DropdownOption]:
        key = ("options", str(self._list_id), str(meta.id))
        cached = self._cache.get(key)
        if cached is None:
            cached = ListService(self._client).get_field_dropdown_options(self._list_id, meta.id)
            self._cache.put(key, cached)
        return list(cached)

    def _plan(self, expr: FilterExpression) -> FilterPlan | None:
        """The split of ``expr`` for this list; ``None`` when the fields can't be read (the
        filter then runs locally, as before)."""
        try:
            fields = self._search_fields()
            options = {str(m.id): self._options(m) for m in dropdown_fields(expr, fields)}
        except (AuthorizationError, NotFoundError, ValidationError):
            return None
        return plan_filter(expr, fields, lambda m: options.get(str(m.id), []))

    def _filter_pages(
        self,
        expr: FilterExpression,
        *,
        field_ids: Sequence[AnyFieldId] | None,
        field_types: Sequence[FieldType] | None,
        limit: int | None,
        server_filter: bool,
    ) -> Iterator[tuple[builtins.list[ListEntryWithEntity], int, str | None]]:
        """``(matching entries, entries read, cursor to the next page)`` per page read:
        through Affinity's search when part of ``expr`` can go there, else every entry."""
        plan = self._plan(expr)
        fetch_ids = _fetch_field_ids(field_ids, plan)
        refused = self._cache.get(("refused", str(self._list_id)))
        reason = "no part of the filter can go to Affinity's search"
        if server_filter and plan is not None and plan.body is not None and not refused:
            try:
                page = self.find_body(
                    plan.body,
                    field_ids=fetch_ids,
                    field_types=field_types,
                    limit=min(limit or 100, 100),
                )
            except (AuthorizationError, NotFoundError, ValidationError,
                    UnsupportedOperationError) as e:  # fmt: skip
                reason = _note_refusal(self._cache, str(self._list_id), e)
            else:
                guard = _CursorGuard(None)
                requested: str | None = None
                while True:
                    yield _filtered_page(page, expr, "search", plan.body)
                    if not page.next_cursor or guard.last_page(requested, page):
                        return
                    requested = page.next_cursor
                    page = self.find_body(plan.body, cursor=requested)
        elif refused:
            reason = "Affinity's search was refused for this list"
        elif not server_filter:
            reason = "server_filter=False"
        _warn_client_side(reason)
        page = self.list(field_ids=fetch_ids, field_types=field_types, limit=limit)
        guard = _CursorGuard(None)
        requested = None
        while True:
            yield _filtered_page(page, expr, "local", None)
            if not page.next_cursor or guard.last_page(requested, page):
                return
            requested = page.next_cursor
            page = self.list(cursor=requested)

    def _resume(self, cursor: SearchCursor) -> PaginatedResponse[ListEntryWithEntity]:
        """One page of a filtered read, from its cursor."""
        if cursor.kind == "search":
            assert cursor.body is not None
            page = self.find_body(cursor.body, cursor=cursor.url)
        else:
            page = self.list(cursor=cursor.url)
        matched, _, token = _filtered_page(page, cursor.filter, cursor.kind, cursor.body)
        return _page_with_cursor(matched, token)

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
        server_filter: bool = True,
    ) -> PaginatedResponse[ListEntryWithEntity]:
        """
        One page of list entries with entity data and field values.

        Args:
            field_ids: Specific field IDs to include
            field_types: Field types to include
            filter: Only entries matching this filter. The part Affinity's search can answer is
                applied on its side; the whole filter is then checked locally. The page holds
                the matches of **one page read** (up to ``limit`` entries read): it can have
                fewer than ``limit`` matches, or none, while more pages exist. Follow
                ``next_cursor`` (it carries the filter), or use :meth:`iter` for every match.
            limit: Maximum entries per page read (filtered reads through the search: 1-100)
            cursor: Cursor from a prior page (a plain page URL, or a filtered-read cursor)
            server_filter: Let Affinity's search apply what it can of ``filter`` (default);
                ``False`` reads every entry and filters locally.

        Returns:
            One page of list entries
        """
        expr = _parse_entry_filter(filter)
        if cursor is not None:
            if field_ids or field_types or expr is not None or limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            resumed = decode_cursor(cursor, path_suffixes=self._cursor_paths())
            if resumed is not None:
                return self._resume(resumed)
            data = self._client.get_url(cursor)
            entries = [_safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])]
            return PaginatedResponse[ListEntryWithEntity](
                data=entries,
                pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
            )
        if limit is not None and limit <= 0:
            raise ValueError("'limit' must be > 0")
        if expr is not None:
            matched, _, token = next(
                self._filter_pages(
                    expr,
                    field_ids=field_ids,
                    field_types=field_types,
                    limit=limit,
                    server_filter=server_filter,
                )
            )
            return _page_with_cursor(matched, token)
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]
        if limit is not None:
            params["limit"] = limit
        data = self._client.get(
            f"/lists/{self._list_id}/list-entries",
            params=params or None,
        )
        entries = [_safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])]
        return PaginatedResponse[ListEntryWithEntity](
            data=entries,
            pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
        )

    def _cursor_paths(self) -> builtins.list[str]:
        return [
            f"/lists/{self._list_id}/list-entries",
            f"/lists/{self._list_id}/list-entries/search",
        ]

    def get_first(
        self,
        *,
        filter: str | FilterExpression | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        server_filter: bool = True,
    ) -> ListEntryWithEntity | None:
        """
        Get the first list entry matching the filter, or None.

        The part of the filter Affinity's search can answer is applied on its side. When none
        can (or ``server_filter=False``), entries are read page by page until one matches: if
        none does, **every page is read**. With several matches, which one comes first can
        differ between Affinity's search and a plain read.
        """
        return next(
            self.iter(
                filter=filter,
                field_ids=field_ids,
                field_types=field_types,
                server_filter=server_filter,
            ),
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
        server_filter: bool = True,
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
            With a filter, the part Affinity's search can answer is applied on its side (only
            matching entries are read); the whole filter is checked locally. Pages are
            "virtual": filtered results are buffered until a full page of ``limit`` matches
            is ready (``next_cursor`` is ``None``; use :meth:`list` for cursor paging).
            ``server_filter=False`` reads every entry and filters locally.
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

        filter_expr = _parse_entry_filter(filter)

        # No filter: use simple pagination (original behavior)
        if filter_expr is None:
            requested_cursor = cursor
            guard = _CursorGuard(cursor)
            page = (
                self.list(field_ids=field_ids, field_types=field_types, limit=limit)
                if cursor is None
                else self.list(cursor=cursor)
            )
            while True:
                yield page
                # Stop at the last page, or when paging can't progress (see _CursorGuard).
                if guard.last_page(requested_cursor, page):
                    return
                requested_cursor = page.next_cursor
                page = self.list(cursor=requested_cursor)
            return

        # With filter: virtual pages of `limit` matches over the filtered page reads
        virtual_page_size = limit if limit is not None else 100
        source = self._filter_pages(
            filter_expr,
            field_ids=field_ids,
            field_types=field_types,
            limit=limit,
            server_filter=server_filter,
        )
        buffer: list[ListEntryWithEntity] = []
        stats = FilterStats()
        has_more_physical = True

        def read_more() -> None:
            nonlocal has_more_physical
            try:
                matched, scanned, token = next(source)
            except StopIteration:
                has_more_physical = False
                return
            stats.scanned += scanned
            stats.matched += len(matched)
            buffer.extend(matched)
            has_more_physical = token is not None
            if progress_callback is not None:
                progress_callback(FilterStats(scanned=stats.scanned, matched=stats.matched))

        read_more()
        while True:
            if len(buffer) >= virtual_page_size or not has_more_physical:
                if not buffer and not has_more_physical:
                    return
                page_data = buffer[:virtual_page_size]
                buffer = buffer[virtual_page_size:]
                has_next = len(buffer) > 0 or has_more_physical
                virtual_page = PaginatedResponse[ListEntryWithEntity](
                    data=page_data,
                    pagination=PaginationInfo(next_cursor=None, prev_cursor=None),
                )
                virtual_page._has_next_override = has_next
                virtual_page._filter_stats = FilterStats(
                    scanned=stats.scanned, matched=stats.matched
                )
                yield virtual_page
                if not has_next:
                    return
                continue
            read_more()

    def all(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        server_filter: bool = True,
    ) -> Iterator[ListEntryWithEntity]:
        """
        Iterate through all list entries with automatic pagination.

        Note:
            With a filter, the part Affinity's search can answer is applied on its side; the
            whole filter is checked locally. ``server_filter=False`` reads every entry.
        """
        filter_expr = _parse_entry_filter(filter)
        if filter_expr is not None:
            for matched, _, _ in self._filter_pages(
                filter_expr,
                field_ids=field_ids,
                field_types=field_types,
                limit=None,
                server_filter=server_filter,
            ):
                yield from matched
            return

        def fetch_page(next_url: str | None) -> PaginatedResponse[ListEntryWithEntity]:
            if next_url:
                return self.list(cursor=next_url)
            return self.list(field_ids=field_ids, field_types=field_types)

        yield from PageIterator(fetch_page)

    def iter(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        server_filter: bool = True,
    ) -> Iterator[ListEntryWithEntity]:
        """
        Auto-paginate all list entries.

        Alias for `all()` (FR-006 public contract).
        """
        return self.all(
            field_ids=field_ids, field_types=field_types, filter=filter, server_filter=server_filter
        )

    def get(
        self,
        entry_id: ListEntryId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
    ) -> ListEntryWithEntity:
        """
        Get a single list entry by ID.

        Args:
            entry_id: The list entry ID
            field_ids: Specific field IDs to include in the response
            field_types: Field types to include in the response

        Note:
            The V2 API omits field values unless ``field_ids`` or ``field_types``
            is given. Values are returned in entity.fields_raw as an array.
        """
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]
        data = self._client.get(
            f"/lists/{self._list_id}/list-entries/{entry_id}",
            params=params or None,
        )
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
        check_positive_limit(limit)
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]
        if limit is not None:
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

    def _create_entry(
        self,
        entity_id: int,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add a person or company to this list (V1).

        The entity type must match the list type. Opportunities can't be added this way:
        create them on their list with ``opportunities.create(OpportunityCreate(list_id=...))``.
        (V2 documents ``POST /v2/lists/{id}/list-entries``, but Affinity didn't serve it when
        tested, 2026-10-12.)
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
                    f"(person lists accept persons, company lists accept companies).",
                    param=e.param,
                    status_code=e.status_code,
                    response_body=e.response_body,
                    diagnostics=e.diagnostics,
                ) from e
            raise

        invalidate_list_size(self._client, self._list_id)
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
        invalidate_list_size(self._client, self._list_id)
        return bool(result.get("success", True))

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
        # Paged (default page size 20): read every page, or callers silently miss fields.
        data = self._client.get_all_pages(
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
                from the value (str→text, int/float→number, datetime/date→datetime);
                pass it explicitly for multi-value fields.

        Returns:
            Updated field value data
        """
        payload = _field_value_payload(value, value_type)
        _check_multi_value_size(field_id, payload)
        result = self._client.post(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields/{field_id}",
            json={"value": payload},
        )
        return _safe_model_validate(FieldValues, result)

    def list_field_value_items(
        self,
        entry_id: ListEntryId,
        field_id: AnyFieldId | str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[Any]:
        """One page of the items in a field's value on this entry (V2, beta; API 2026-09-17+),
        for multi-value fields with many items. Items are raw. ``limit`` is 1-100."""
        check_cursor_alone(cursor, limit)
        if cursor is not None:
            return page_of_raw(
                self._client.get_url(cursor, min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION)
            )
        return page_of_raw(
            self._client.get(
                f"/lists/{int(self._list_id)}/list-entries/{int(entry_id)}/fields/{field_id}/values",
                params=items_params(limit),
                min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION,
            )
        )

    def iter_field_value_items(
        self, entry_id: ListEntryId, field_id: AnyFieldId | str
    ) -> Iterator[Any]:
        """Every item in a field's value on this entry (all pages)."""
        return PageIterator(
            lambda c: (
                self.list_field_value_items(entry_id, field_id, cursor=c)
                if c
                else self.list_field_value_items(entry_id, field_id, limit=100)
            )
        )

    def list_field_changes(
        self,
        entry_id: ListEntryId,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[FieldValueChangeV2]:
        """One page of this entry's field value changes, oldest first (V2).

        Covers the list's own fields. ``changed_after`` is inclusive and ``changed_before``
        exclusive (whole seconds). ``limit`` is 1-100.
        """
        check_cursor_alone(
            cursor, field_id, changer_id, changed_after, changed_before, action_type, limit
        )
        if cursor is not None:
            return _fvc_v2_page(self._client.get_url(cursor))
        params = _fvc_v2_params(
            field_id=field_id,
            list_entry_id=None,
            changer_id=changer_id,
            changed_after=changed_after,
            changed_before=changed_before,
            action_type=action_type,
            order="asc",
            limit=limit,
        )
        return _fvc_v2_page(
            self._client.get(
                f"/lists/{int(self._list_id)}/list-entries/{int(entry_id)}/field-value-changes",
                params=params or None,
            )
        )

    def iter_field_changes(
        self,
        entry_id: ListEntryId,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
    ) -> Iterator[FieldValueChangeV2]:
        """Every field value change of this entry (all pages); see :meth:`list_field_changes`."""
        return PageIterator(
            lambda c: (
                self.list_field_changes(entry_id, cursor=c)
                if c
                else self.list_field_changes(
                    entry_id,
                    field_id=field_id,
                    changer_id=changer_id,
                    changed_after=changed_after,
                    changed_before=changed_before,
                    action_type=action_type,
                    limit=100,
                )
            )
        )

    def batch_update_fields(
        self,
        entry_id: ListEntryId,
        updates: Mapping[AnyFieldId | str, Any],
        *,
        value_types: Mapping[AnyFieldId | str, FieldValueType | str] | None = None,
    ) -> BatchOperationResponse:
        """
        Update multiple field values in one request.

        Affinity applies the whole batch or none of it: if any update is rejected, no field
        changes. At most 100 updates per call.

        Args:
            entry_id: The list entry
            updates: Mapping of field IDs to new values. Values are auto-typed
                (str→text, int/float→number, datetime/date→datetime, otherwise→text)
                unless the field has an entry in ``value_types``.
            value_types: Optional field ID → value type (e.g. ``"dropdown-multi"``). A typed
                field may take a list (multi-value fields) or ``None`` (clears the field).

        Returns:
            The batch response (``operation``); Affinity returns no per-field results.
        """

        update_items = _batch_update_items(updates, value_types)

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
        # (timestamp, size), shared by every list service on this client
        self._size_cache: dict[int, tuple[float, int]] = _size_cache_for(client)
        # Fields, dropdown options and refused searches for filtered reads, per list
        self._search_cache = SearchCache()

    def entries(self, list_id: ListId) -> AsyncListEntryService:
        """
        Get an AsyncListEntryService for a specific list.

        This is the explicit path for retrieving "full row" data via list entries.
        """
        return AsyncListEntryService(self._client, list_id, self._search_cache)

    async def list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        term: str | None = None,
    ) -> PaginatedResponse[AffinityList]:
        """
        Get all lists accessible to you.

        Args:
            limit: Maximum results per page.
            cursor: Cursor to resume pagination (opaque; obtained from prior responses).
            term: Only lists whose name contains this text (case-insensitive substring,
                matched by Affinity). Kept across pages by the cursor.

        Returns:
            Paginated list of lists (without field metadata)
        """
        term = term or None
        if cursor is not None:
            if limit is not None or term is not None:
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
            if term is not None:
                params["term"] = term
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
        term: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[AffinityList]]:
        """
        Iterate list pages (not items), yielding `PaginatedResponse[AffinityList]`.

        This is useful for ETL scripts that want checkpoint/resume via `page.next_cursor`.
        ``term`` keeps only lists whose name contains it (case-insensitive, matched by Affinity).
        """
        term = term or None
        if cursor is not None and (limit is not None or term is not None):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        guard = _CursorGuard(cursor)
        page = (
            await self.list(limit=limit, term=term)
            if cursor is None
            else await self.list(cursor=cursor)
        )
        while True:
            yield page
            # Stop at the last page, or when paging can't progress (see _CursorGuard).
            if guard.last_page(requested_cursor, page):
                return
            requested_cursor = page.next_cursor
            page = await self.list(cursor=requested_cursor)

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
        guard = _CursorGuard(cursor)
        page = (
            await self.get_saved_views(list_id, limit=limit)
            if cursor is None
            else await self.get_saved_views(list_id, cursor=cursor)
        )
        while True:
            yield page
            # Stop at the last page, or when paging can't progress (see _CursorGuard).
            if guard.last_page(requested_cursor, page):
                return
            requested_cursor = page.next_cursor
            page = await self.get_saved_views(list_id, cursor=requested_cursor)

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
        Get a single list by ID (V2).

        ``fields`` is not filled: use :meth:`get_fields`. For the number of entries use
        :meth:`get_size`.
        """
        data = await self._client.get(f"/lists/{int(list_id)}")
        return _safe_model_validate(AffinityList, data)

    async def get_size(self, list_id: ListId, *, force: bool = False) -> int:
        """
        Get accurate list size. Uses V1 API, cached for 5 minutes.

        Adding or removing a row through this client (``entries(list_id).add_*`` / ``delete``,
        ``opportunities.create``) drops the cached size; changes made elsewhere show after the
        cache expires, or with ``force=True``.

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

        # V2 has no list size: read it from V1
        data = await self._client.get(f"/lists/{int(list_id)}", v1=True)
        size = _safe_model_validate(AffinityList, data)._list_size_hint
        self._size_cache[list_id] = (time.monotonic(), size)
        return size

    async def resolve(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> AffinityList | None:
        """
        Find a single list by name (case-insensitive exact match; optionally filtered by type).

        Notes:
        - Affinity narrows the lists by name first (``term``); the exact comparison is made
          here. If that finds nothing, every list is checked (see `resolve_all()`).
        - Matches are cached in-memory on this service instance (a miss is not). If you call
          this frequently, reuse the client, or persist the resolved `ListId` in your own
          configuration.

        If multiple matches exist, returns the first match in server-provided order.
        """
        key = (name.lower(), list_type)
        if key in self._resolve_cache:
            return self._resolve_cache[key]

        matches = await self.resolve_all(name=name, list_type=list_type)
        found = matches[0] if matches else None
        if found is not None:  # a miss isn't cached: the list may be created later
            self._resolve_cache[key] = found
        return found

    async def resolve_all(
        self,
        *,
        name: str,
        list_type: ListType | None = None,
    ) -> builtins.list[AffinityList]:
        """
        Find all lists matching a name (case-insensitive exact match; optionally by type).

        Notes:
        - For an ASCII name, Affinity first narrows the lists by name (``term``, a
          case-insensitive substring match) and the exact comparison is made here. If that
          yields no match, every list is checked, so a difference in how Affinity folds case
          or whitespace can't hide a list. Non-ASCII names always check every list.
        - Unlike `resolve()`, this does not cache results.
        """
        term = name.strip()
        if term and term.isascii():
            candidates = [item async for page in self.pages(term=term) for item in page.data]
            matches = _exact_name_matches(candidates, name, list_type)
            if matches:
                return matches
        return _exact_name_matches([item async for item in self.all()], name, list_type)

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    async def create(self, data: ListCreate) -> AffinityList:
        """
        Create a list (V2); the caller becomes its creator and owner.

        A public list needs the "Share accessible Lists globally" permission. V2 can't set
        ``owner_id`` or ``additional_permissions``: with either, the list is created through V1.
        """
        if _uses_v1_create(data):
            result = await self._client.post("/lists", json=_v1_list_create_payload(data), v1=True)
        else:
            result = await self._client.post("/lists", json=_list_create_body(data))

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
        filter: str | None = None,
        includes: Sequence[str] | None = None,
    ) -> builtins.list[FieldMetadata]:
        """
        Get fields (columns) for a list.

        Includes list-specific, global, enriched, and relationship intelligence fields.
        Cached for performance.

        Args:
            filter: Filter on the field name: ``name="Location"`` (exact) or ``name=~Loc``
                (substring); both case-sensitive. Combine clauses with ``|``. Only ``name``
                is filterable; anything else is rejected with 400.
            includes: Extra properties per field: ``"filterability"``, ``"sortability"``
                (read them via ``is_filterable`` / ``is_sortable``).
        """
        params, cache_key = build_fields_query(
            f"list_{list_id}_fields:",
            filter=filter,
            includes=includes,
        )

        data = await self._client.get_all_pages(
            f"/lists/{list_id}/fields",
            params=params or None,
            cache_key=cache_key,
            cache_ttl=300,
        )

        return [_safe_model_validate(FieldMetadata, f) for f in data.get("data", [])]

    # =========================================================================
    # Dropdown options of list fields
    # =========================================================================

    async def get_field_dropdown_options(
        self, list_id: ListId, field_id: AnyFieldId, *, with_status_types: bool = False
    ) -> builtins.list[DropdownOption]:
        """All options of a list field's dropdown, ranked-dropdown or status field (every page).

        Older API versions report status options as ``ranked-dropdown`` without
        ``status_category``. ``with_status_types=True`` reads with API version
        {DROPDOWN_OPTION_WRITES_MIN_API_VERSION} (as the option writes do) to get the real
        ``status-dropdown`` type; a client pinned to an older version then raises
        ``ApiVersionTooOldError``.
        """
        data = await self._client.get_all_pages(
            options_path(field_id, list_id=list_id, entity=None),
            params={"limit": 100},
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION if with_status_types else None,
        )
        return parse_options(data)

    async def get_field_dropdown_option(
        self, list_id: ListId, field_id: AnyFieldId, option_id: int
    ) -> DropdownOption:
        """One option of a list field."""
        return DropdownOption.model_validate(
            await self._client.get(option_path(list_id, field_id, option_id))
        )

    async def create_field_dropdown_option(
        self,
        list_id: ListId,
        field_id: AnyFieldId,
        *,
        option_type: DropdownOptionType,
        text: str,
        rank: int | None = None,
        color: DropdownOptionColorName | None = None,
        status_category: DropdownStatusCategory | None = None,
        win_rate: int | None = None,
    ) -> DropdownOption:
        """Add an option to a list field.

        ``option_type`` must match the field: ``"dropdown"`` (text only), ``"ranked-dropdown"``
        (``rank`` and ``color`` required) or ``"status-dropdown"`` (``rank``, ``color`` and
        ``status_category`` required; ``win_rate`` 0-100 only for ``"open"``). Needs Affinity API
        version {DROPDOWN_OPTION_WRITES_MIN_API_VERSION} (sent for this call).
        """
        body = create_body(
            option_type=option_type,
            text=text,
            rank=rank,
            color=color,
            status_category=status_category,
            win_rate=win_rate,
        )
        data = await self._client.post(
            options_path(field_id, list_id=list_id, entity=None),
            json=body,
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
        )
        return DropdownOption.model_validate(data)

    async def update_field_dropdown_option(
        self,
        list_id: ListId,
        field_id: AnyFieldId,
        option_id: int,
        *,
        text: str | None = None,
        rank: int | None = None,
        color: DropdownOptionColorName | None = None,
        status_category: DropdownStatusCategory | None = None,
        win_rate: int | None = None,
    ) -> DropdownOption:
        """Change an option of a list field (only what you pass).

        A ``dropdown`` option can change its text; ``ranked-dropdown`` also rank and color;
        ``status-dropdown`` also status category and win rate. Renaming changes the value shown
        on every entry that has this option. Returns the option as read back afterwards.
        """
        body = update_body(
            text=text,
            rank=rank,
            color=color,
            status_category=status_category,
            win_rate=win_rate,
        )
        await self._client.post(
            option_path(list_id, field_id, option_id),
            json=body,
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
        )
        # The API answers 204 with no body: read the option back
        return DropdownOption.model_validate(
            await self._client.get(
                option_path(list_id, field_id, option_id),
                min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
            )
        )

    async def delete_field_dropdown_option(
        self, list_id: ListId, field_id: AnyFieldId, option_id: int
    ) -> bool:
        """Delete an option of a list field. **Cannot be undone.**

        Affinity also clears this field on every list entry currently set to the option; those
        values cannot be recovered.
        """
        await self._client.delete(
            option_path(list_id, field_id, option_id),
            min_api_version=DROPDOWN_OPTION_WRITES_MIN_API_VERSION,
        )
        return True


class AsyncListEntryService(AsyncSearchMixin[ListEntryWithEntity]):
    """Async list entry operations (TR-009)."""

    _search_model = ListEntryWithEntity

    def __init__(
        self, client: AsyncHTTPClient, list_id: ListId, cache: SearchCache | None = None
    ) -> None:
        self._client = client
        self._list_id = list_id
        self._cache = cache if cache is not None else SearchCache()

    def _search_path(self) -> str:
        return f"/lists/{self._list_id}/list-entries/search"

    def _cursor_paths(self) -> builtins.list[str]:
        return [
            f"/lists/{self._list_id}/list-entries",
            f"/lists/{self._list_id}/list-entries/search",
        ]

    async def _search_fields(self) -> builtins.list[FieldMetadata]:
        key = ("fields", str(self._list_id))
        cached = self._cache.get(key)
        if cached is None:
            cached = await AsyncListService(self._client).get_fields(
                self._list_id, includes=SEARCH_INCLUDES
            )
            self._cache.put(key, cached)
        return list(cached)

    async def _options(self, meta: FieldMetadata) -> builtins.list[DropdownOption]:
        key = ("options", str(self._list_id), str(meta.id))
        cached = self._cache.get(key)
        if cached is None:
            cached = await AsyncListService(self._client).get_field_dropdown_options(
                self._list_id, meta.id
            )
            self._cache.put(key, cached)
        return list(cached)

    async def _plan(self, expr: FilterExpression) -> FilterPlan | None:
        """See :meth:`ListEntryService._plan`."""
        try:
            fields = await self._search_fields()
            options = {str(m.id): await self._options(m) for m in dropdown_fields(expr, fields)}
        except (AuthorizationError, NotFoundError, ValidationError):
            return None
        return plan_filter(expr, fields, lambda m: options.get(str(m.id), []))

    async def _filter_pages(
        self,
        expr: FilterExpression,
        *,
        field_ids: Sequence[AnyFieldId] | None,
        field_types: Sequence[FieldType] | None,
        limit: int | None,
        server_filter: bool,
    ) -> AsyncGenerator[tuple[builtins.list[ListEntryWithEntity], int, str | None], None]:
        """See :meth:`ListEntryService._filter_pages`."""
        plan = await self._plan(expr)
        fetch_ids = _fetch_field_ids(field_ids, plan)
        refused = self._cache.get(("refused", str(self._list_id)))
        reason = "no part of the filter can go to Affinity's search"
        if server_filter and plan is not None and plan.body is not None and not refused:
            try:
                page = await self.find_body(
                    plan.body,
                    field_ids=fetch_ids,
                    field_types=field_types,
                    limit=min(limit or 100, 100),
                )
            except (AuthorizationError, NotFoundError, ValidationError,
                    UnsupportedOperationError) as e:  # fmt: skip
                reason = _note_refusal(self._cache, str(self._list_id), e)
            else:
                guard = _CursorGuard(None)
                requested: str | None = None
                while True:
                    yield _filtered_page(page, expr, "search", plan.body)
                    if not page.next_cursor or guard.last_page(requested, page):
                        return
                    requested = page.next_cursor
                    page = await self.find_body(plan.body, cursor=requested)
        elif refused:
            reason = "Affinity's search was refused for this list"
        elif not server_filter:
            reason = "server_filter=False"
        _warn_client_side(reason)
        page = await self.list(field_ids=fetch_ids, field_types=field_types, limit=limit)
        guard = _CursorGuard(None)
        requested = None
        while True:
            yield _filtered_page(page, expr, "local", None)
            if not page.next_cursor or guard.last_page(requested, page):
                return
            requested = page.next_cursor
            page = await self.list(cursor=requested)

    async def _resume(self, cursor: SearchCursor) -> PaginatedResponse[ListEntryWithEntity]:
        if cursor.kind == "search":
            assert cursor.body is not None
            page = await self.find_body(cursor.body, cursor=cursor.url)
        else:
            page = await self.list(cursor=cursor.url)
        matched, _, token = _filtered_page(page, cursor.filter, cursor.kind, cursor.body)
        return _page_with_cursor(matched, token)

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
        server_filter: bool = True,
    ) -> PaginatedResponse[ListEntryWithEntity]:
        """One page of list entries; see :meth:`ListEntryService.list`."""
        expr = _parse_entry_filter(filter)
        if cursor is not None:
            if field_ids or field_types or expr is not None or limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            resumed = decode_cursor(cursor, path_suffixes=self._cursor_paths())
            if resumed is not None:
                return await self._resume(resumed)
            data = await self._client.get_url(cursor)
            entries = [_safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])]
            return PaginatedResponse[ListEntryWithEntity](
                data=entries,
                pagination=_safe_model_validate(PaginationInfo, data.get("pagination", {})),
            )
        if limit is not None and limit <= 0:
            raise ValueError("'limit' must be > 0")
        if expr is not None:
            source = self._filter_pages(
                expr,
                field_ids=field_ids,
                field_types=field_types,
                limit=limit,
                server_filter=server_filter,
            )
            matched, _, token = await source.__anext__()
            await source.aclose()
            return _page_with_cursor(matched, token)
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]
        if limit is not None:
            params["limit"] = limit
        data = await self._client.get(
            f"/lists/{self._list_id}/list-entries",
            params=params or None,
        )
        entries = [_safe_model_validate(ListEntryWithEntity, e) for e in data.get("data", [])]
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
        server_filter: bool = True,
    ) -> ListEntryWithEntity | None:
        """The first list entry matching the filter, or None; see
        :meth:`ListEntryService.get_first`."""
        async for entry in self.iter(
            filter=filter, field_ids=field_ids, field_types=field_types, server_filter=server_filter
        ):
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
        server_filter: bool = True,
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
            See :meth:`ListEntryService.pages` (filters go to Affinity's search where they
            can; virtual pages; ``server_filter=False`` reads every entry).
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

        filter_expr = _parse_entry_filter(filter)

        # No filter: use simple pagination (original behavior)
        if filter_expr is None:
            requested_cursor = cursor
            guard = _CursorGuard(cursor)
            page = (
                await self.list(field_ids=field_ids, field_types=field_types, limit=limit)
                if cursor is None
                else await self.list(cursor=cursor)
            )
            while True:
                yield page
                # Stop at the last page, or when paging can't progress (see _CursorGuard).
                if guard.last_page(requested_cursor, page):
                    return
                requested_cursor = page.next_cursor
                page = await self.list(cursor=requested_cursor)
            return

        # With filter: virtual pages of `limit` matches over the filtered page reads
        virtual_page_size = limit if limit is not None else 100
        source = self._filter_pages(
            filter_expr,
            field_ids=field_ids,
            field_types=field_types,
            limit=limit,
            server_filter=server_filter,
        )
        buffer: list[ListEntryWithEntity] = []
        stats = FilterStats()
        has_more_physical = True

        async def read_more() -> None:
            nonlocal has_more_physical
            try:
                matched, scanned, token = await source.__anext__()
            except StopAsyncIteration:
                has_more_physical = False
                return
            stats.scanned += scanned
            stats.matched += len(matched)
            buffer.extend(matched)
            has_more_physical = token is not None
            if progress_callback is not None:
                progress_callback(FilterStats(scanned=stats.scanned, matched=stats.matched))

        await read_more()
        while True:
            if len(buffer) >= virtual_page_size or not has_more_physical:
                if not buffer and not has_more_physical:
                    return
                page_data = buffer[:virtual_page_size]
                buffer = buffer[virtual_page_size:]
                has_next = len(buffer) > 0 or has_more_physical
                virtual_page = PaginatedResponse[ListEntryWithEntity](
                    data=page_data,
                    pagination=PaginationInfo(next_cursor=None, prev_cursor=None),
                )
                virtual_page._has_next_override = has_next
                virtual_page._filter_stats = FilterStats(
                    scanned=stats.scanned, matched=stats.matched
                )
                yield virtual_page
                if not has_next:
                    return
                continue
            await read_more()

    async def all(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        server_filter: bool = True,
    ) -> AsyncIterator[ListEntryWithEntity]:
        """Every list entry (matching ``filter``); see :meth:`ListEntryService.all`."""
        filter_expr = _parse_entry_filter(filter)
        if filter_expr is not None:
            async for matched, _, _ in self._filter_pages(
                filter_expr,
                field_ids=field_ids,
                field_types=field_types,
                limit=None,
                server_filter=server_filter,
            ):
                for entry in matched:
                    yield entry
            return

        async def fetch_page(next_url: str | None) -> PaginatedResponse[ListEntryWithEntity]:
            if next_url:
                return await self.list(cursor=next_url)
            return await self.list(field_ids=field_ids, field_types=field_types)

        async for entry in AsyncPageIterator(fetch_page):
            yield entry

    async def iter(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        server_filter: bool = True,
    ) -> AsyncIterator[ListEntryWithEntity]:
        """
        Auto-paginate all list entries.

        Alias for `all()` (FR-006 public contract).
        """
        async for entry in self.all(
            field_ids=field_ids, field_types=field_types, filter=filter, server_filter=server_filter
        ):
            yield entry

    async def get(
        self,
        entry_id: ListEntryId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
    ) -> ListEntryWithEntity:
        """
        Get a single list entry by ID.

        Args:
            entry_id: The list entry ID
            field_ids: Specific field IDs to include in the response
            field_types: Field types to include in the response

        Note:
            The V2 API omits field values unless ``field_ids`` or ``field_types``
            is given. Values are returned in entity.fields_raw as an array.
        """
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]
        data = await self._client.get(
            f"/lists/{self._list_id}/list-entries/{entry_id}",
            params=params or None,
        )
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

    async def _create_entry(
        self,
        entity_id: int,
        creator_id: int | None = None,
    ) -> ListEntry:
        """Add a person or company to this list (V1).

        The entity type must match the list type. Opportunities can't be added this way:
        create them on their list with ``opportunities.create(OpportunityCreate(list_id=...))``.
        (V2 documents ``POST /v2/lists/{id}/list-entries``, but Affinity didn't serve it when
        tested, 2026-10-12.)
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
                    f"(person lists accept persons, company lists accept companies).",
                    param=e.param,
                    status_code=e.status_code,
                    response_body=e.response_body,
                    diagnostics=e.diagnostics,
                ) from e
            raise

        invalidate_list_size(self._client, self._list_id)
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
        invalidate_list_size(self._client, self._list_id)
        return bool(result.get("success", True))

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
        # Paged (default page size 20): read every page, or callers silently miss fields.
        data = await self._client.get_all_pages(
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
        value_type: FieldValueType | str | None = None,
    ) -> FieldValues:
        """
        Update a single field value on a list entry.

        Args:
            entry_id: The list entry
            field_id: The field to update
            value: New value (type depends on field type)
            value_type: The field value type (e.g., FieldValueType.TEXT).
                Required by the V2 API. If not provided, inferred from the value
                (str->text, int/float->number, datetime/date->datetime); pass it
                explicitly for multi-value fields.

        Returns:
            Updated field value data
        """
        payload = _field_value_payload(value, value_type)
        _check_multi_value_size(field_id, payload)
        result = await self._client.post(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields/{field_id}",
            json={"value": payload},
        )
        return _safe_model_validate(FieldValues, result)

    async def list_field_value_items(
        self,
        entry_id: ListEntryId,
        field_id: AnyFieldId | str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[Any]:
        """One page of the items in a field's value on this entry (V2, beta; API 2026-09-17+),
        for multi-value fields with many items. Items are raw. ``limit`` is 1-100."""
        check_cursor_alone(cursor, limit)
        if cursor is not None:
            return page_of_raw(
                await self._client.get_url(
                    cursor, min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION
                )
            )
        return page_of_raw(
            await self._client.get(
                f"/lists/{int(self._list_id)}/list-entries/{int(entry_id)}/fields/{field_id}/values",
                params=items_params(limit),
                min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION,
            )
        )

    def iter_field_value_items(
        self, entry_id: ListEntryId, field_id: AnyFieldId | str
    ) -> AsyncIterator[Any]:
        """Every item in a field's value on this entry (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[Any]:
            if c:
                return await self.list_field_value_items(entry_id, field_id, cursor=c)
            return await self.list_field_value_items(entry_id, field_id, limit=100)

        return AsyncPageIterator(fetch)

    async def list_field_changes(
        self,
        entry_id: ListEntryId,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[FieldValueChangeV2]:
        """One page of this entry's field value changes, oldest first (V2).

        Covers the list's own fields. ``changed_after`` is inclusive and ``changed_before``
        exclusive (whole seconds). ``limit`` is 1-100.
        """
        check_cursor_alone(
            cursor, field_id, changer_id, changed_after, changed_before, action_type, limit
        )
        if cursor is not None:
            return _fvc_v2_page(await self._client.get_url(cursor))
        params = _fvc_v2_params(
            field_id=field_id,
            list_entry_id=None,
            changer_id=changer_id,
            changed_after=changed_after,
            changed_before=changed_before,
            action_type=action_type,
            order="asc",
            limit=limit,
        )
        return _fvc_v2_page(
            await self._client.get(
                f"/lists/{int(self._list_id)}/list-entries/{int(entry_id)}/field-value-changes",
                params=params or None,
            )
        )

    def iter_field_changes(
        self,
        entry_id: ListEntryId,
        *,
        field_id: AnyFieldId | Sequence[AnyFieldId] | None = None,
        changer_id: int | None = None,
        changed_after: datetime | None = None,
        changed_before: datetime | None = None,
        action_type: Literal["add", "update", "delete"] | None = None,
    ) -> AsyncIterator[FieldValueChangeV2]:
        """Every field value change of this entry (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[FieldValueChangeV2]:
            if c:
                return await self.list_field_changes(entry_id, cursor=c)
            return await self.list_field_changes(
                entry_id,
                field_id=field_id,
                changer_id=changer_id,
                changed_after=changed_after,
                changed_before=changed_before,
                action_type=action_type,
                limit=100,
            )

        return AsyncPageIterator(fetch)

    async def batch_update_fields(
        self,
        entry_id: ListEntryId,
        updates: Mapping[AnyFieldId | str, Any],
        *,
        value_types: Mapping[AnyFieldId | str, FieldValueType | str] | None = None,
    ) -> BatchOperationResponse:
        """
        Update multiple field values in one request.

        Affinity applies the whole batch or none of it: if any update is rejected, no field
        changes. At most 100 updates per call.

        Args:
            entry_id: The list entry
            updates: Mapping of field IDs to new values. Values are auto-typed
                (str→text, int/float→number, datetime/date→datetime, otherwise→text)
                unless the field has an entry in ``value_types``.
            value_types: Optional field ID → value type (e.g. ``"dropdown-multi"``). A typed
                field may take a list (multi-value fields) or ``None`` (clears the field).

        Returns:
            The batch response (``operation``); Affinity returns no per-field results.
        """

        update_items = _batch_update_items(updates, value_types)

        result = await self._client.patch(
            f"/lists/{self._list_id}/list-entries/{entry_id}/fields",
            json={"operation": "update-fields", "updates": update_items},
        )

        return _safe_model_validate(BatchOperationResponse, result)
