"""
Pagination, response wrappers, and utility models.

Provides type-safe access to paginated API responses.
"""

from __future__ import annotations

import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from ..exceptions import TooManyResultsError

T = TypeVar("T")

__all__ = [
    "FilterStats",
    "PaginationProgress",
    "PaginatedResponse",
    "PageIterator",
    "AsyncPageIterator",
]

# Default limit for .all() method to prevent OOM
_DEFAULT_LIMIT = 100_000


@dataclass
class PaginationProgress:
    """Progress information for pagination callbacks."""

    page_number: int
    """1-indexed page number."""

    items_in_page: int
    """Items in current page."""

    items_so_far: int
    """Cumulative items *including* just-yielded page."""

    has_next: bool
    """Whether more pages exist (matches Page.has_next)."""


class AffinityModel(BaseModel):
    """Base model with common configuration."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        use_enum_values=True,
    )


# =============================================================================
# Pagination Models
# =============================================================================


class PaginationInfo(AffinityModel):
    """V2 pagination info returned in responses."""

    next_cursor: str | None = Field(None, alias="nextUrl")
    prev_cursor: str | None = Field(None, alias="prevUrl")


class PaginationInfoWithTotal(PaginationInfo):
    """Pagination with total count (used by some endpoints)."""

    total_count: int = Field(0, alias="totalCount")


# =============================================================================
# Generic Paginated Response
# =============================================================================


@dataclass
class FilterStats:
    """Stats for client-side filtered pagination."""

    scanned: int = 0  # Total physical rows scanned so far
    matched: int = 0  # Total rows matching filter so far


class PaginatedResponse(AffinityModel, Generic[T]):
    """
    A paginated response from the API.

    Provides access to the current page of results and pagination info.
    Works with all Affinity API endpoints transparently.

    Attributes:
        data: List of items in the current page.
        has_next: Whether more pages are available.
        next_cursor: Cursor for fetching the next page (use this for pagination).

    Example:
        ```python
        page = client.companies.list(limit=100)
        while page.has_next:
            process(page.data)
            page = client.companies.list(limit=100, cursor=page.next_cursor)
        ```

    Tip:
        Always use ``next_cursor`` for pagination. Avoid accessing
        ``pagination.next_cursor`` or ``next_page_token`` directly.
    """

    data: list[T] = Field(default_factory=list)
    pagination: PaginationInfo = Field(default_factory=PaginationInfo)
    # Internal: V1 API compatibility field. Users should use next_cursor property.
    next_page_token: str | None = Field(None, alias="nextPageToken")
    _has_next_override: bool | None = PrivateAttr(default=None)
    _filter_stats: FilterStats | None = PrivateAttr(default=None)

    def __len__(self) -> int:
        """Number of items in current page."""
        return len(self.data)

    @property
    def has_next(self) -> bool:
        """Whether there are more pages."""
        if self._has_next_override is not None:
            return self._has_next_override
        return self.next_cursor is not None

    @property
    def next_cursor(self) -> str | None:
        """
        Cursor for the next page, if any.

        Returns the V2 pagination URL or V1 page token, whichever is present.
        Always use this property instead of accessing ``pagination.next_cursor``
        or ``next_page_token`` directly.
        """
        # Use explicit None check to preserve empty strings (which are valid cursors)
        if self.pagination.next_cursor is not None:
            return self.pagination.next_cursor
        return self.next_page_token

    @property
    def filter_stats(self) -> FilterStats | None:
        """Stats for client-side filtered queries (scanned/matched counts)."""
        return self._filter_stats


# =============================================================================
# Auto-paginating Iterator
# =============================================================================


class PageIterator(Generic[T]):
    """
    Synchronous iterator that automatically fetches all pages.

    Usage:
        for item in client.companies.all():
            print(item.name)
    """

    def __init__(
        self,
        fetch_page: Callable[[str | None], PaginatedResponse[T]],
        initial_cursor: str | None = None,
    ):
        self._fetch_page = fetch_page
        self._next_cursor = initial_cursor
        self._current_page: list[T] = []
        self._index = 0
        self._exhausted = False

    def __iter__(self) -> Iterator[T]:
        return self

    def __next__(self) -> T:
        while True:
            # If we have items in current page, return next
            if self._index < len(self._current_page):
                item = self._current_page[self._index]
                self._index += 1
                return item

            # Need to fetch next page
            if self._exhausted:
                raise StopIteration

            requested_url = self._next_cursor
            response = self._fetch_page(requested_url)
            self._current_page = list(response.data)
            self._next_cursor = response.next_cursor
            self._index = 0

            # Guard against pagination loops (no cursor progress).
            if response.has_next and response.next_cursor == requested_url:
                self._exhausted = True

            # Empty pages can still legitimately include nextUrl; keep paging
            # until we get data or the cursor is exhausted.
            if not self._current_page:
                if response.has_next and not self._exhausted:
                    continue
                self._exhausted = True
                raise StopIteration

            if not response.has_next:
                self._exhausted = True

    def pages(
        self,
        *,
        on_progress: Callable[[PaginationProgress], None] | None = None,
    ) -> Iterator[PaginatedResponse[T]]:
        """
        Iterate through pages (not individual items).

        Args:
            on_progress: Optional callback fired after fetching each page.
                Receives PaginationProgress with page_number, items_in_page,
                items_so_far, and has_next. Callbacks should be lightweight;
                heavy processing should happen outside the callback to avoid
                blocking iteration.

        Yields:
            PaginatedResponse objects for each page.

        Example:
            def report(p: PaginationProgress):
                print(f"Page {p.page_number}: {p.items_so_far} items so far")

            for page in client.persons.all().pages(on_progress=report):
                process(page.data)
        """
        page_number = 0
        items_so_far = 0

        while True:
            requested_url = self._next_cursor
            response = self._fetch_page(requested_url)
            self._next_cursor = response.next_cursor
            page_number += 1
            items_in_page = len(response.data)
            items_so_far += items_in_page

            # Guard against pagination loops
            if response.has_next and response.next_cursor == requested_url:
                if response.data:
                    if on_progress:
                        on_progress(
                            PaginationProgress(
                                page_number=page_number,
                                items_in_page=items_in_page,
                                items_so_far=items_so_far,
                                has_next=False,  # Loop detected, no more pages
                            )
                        )
                    yield response
                break

            if response.data:
                if on_progress:
                    on_progress(
                        PaginationProgress(
                            page_number=page_number,
                            items_in_page=items_in_page,
                            items_so_far=items_so_far,
                            has_next=response.has_next,
                        )
                    )
                yield response

            if not response.has_next:
                break

    def all(self, *, limit: int | None = _DEFAULT_LIMIT) -> list[T]:
        """
        Fetch all items across all pages into a list.

        Args:
            limit: Maximum items to fetch. Default 100,000. Set to None for unlimited.

        Returns:
            List of all items.

        Raises:
            TooManyResultsError: If results exceed limit.

        Note:
            The check occurs after extending results, so the final list may exceed
            limit by up to one page before the error is raised.

        Example:
            # Default - safe for most use cases
            persons = list(client.persons.all())  # Using iterator

            # Or use .all() method with limit check
            it = PageIterator(fetch_page)
            persons = it.all()  # Returns list, raises if > 100k

            # Explicit unlimited for large exports
            all_persons = it.all(limit=None)

            # Custom limit
            persons = it.all(limit=500_000)
        """
        results: list[T] = []

        for page in self.pages():
            results.extend(page.data)

            if limit is not None and len(results) > limit:
                raise TooManyResultsError(
                    f"Exceeded limit={limit:,} items. "
                    f"Use pages() for streaming, add a filter, or pass limit=None."
                )

        return results


class AsyncPageIterator(Generic[T]):
    """
    Asynchronous iterator that automatically fetches all pages.

    Usage:
        async for item in client.companies.all():
            print(item.name)
    """

    def __init__(
        self,
        fetch_page: Callable[[str | None], Awaitable[PaginatedResponse[T]]],
        initial_cursor: str | None = None,
    ):
        self._fetch_page = fetch_page
        self._next_cursor = initial_cursor
        self._current_page: list[T] = []
        self._index = 0
        self._exhausted = False

    def __aiter__(self) -> AsyncIterator[T]:
        return self

    async def __anext__(self) -> T:
        while True:
            # If we have items in current page, return next
            if self._index < len(self._current_page):
                item = self._current_page[self._index]
                self._index += 1
                return item

            # Need to fetch next page
            if self._exhausted:
                raise StopAsyncIteration

            requested_url = self._next_cursor
            response = await self._fetch_page(requested_url)
            self._current_page = list(response.data)
            self._next_cursor = response.next_cursor
            self._index = 0

            # Guard against pagination loops (no cursor progress).
            if response.has_next and response.next_cursor == requested_url:
                self._exhausted = True

            # Empty pages can still legitimately include nextUrl; keep paging
            # until we get data or the cursor is exhausted.
            if not self._current_page:
                if response.has_next and not self._exhausted:
                    continue
                self._exhausted = True
                raise StopAsyncIteration

            if not response.has_next:
                self._exhausted = True

    async def pages(
        self,
        *,
        on_progress: Callable[[PaginationProgress], None] | None = None,
    ) -> AsyncIterator[PaginatedResponse[T]]:
        """
        Iterate through pages (not individual items).

        Args:
            on_progress: Optional callback fired after fetching each page.
                Receives PaginationProgress with page_number, items_in_page,
                items_so_far, and has_next. Callbacks should be lightweight;
                heavy processing should happen outside the callback to avoid
                blocking iteration.

        Yields:
            PaginatedResponse objects for each page.

        Example:
            def report(p: PaginationProgress):
                print(f"Page {p.page_number}: {p.items_so_far} items so far")

            async for page in client.persons.all().pages(on_progress=report):
                process(page.data)
        """
        page_number = 0
        items_so_far = 0

        while True:
            requested_url = self._next_cursor
            response = await self._fetch_page(requested_url)
            self._next_cursor = response.next_cursor
            page_number += 1
            items_in_page = len(response.data)
            items_so_far += items_in_page

            # Guard against pagination loops
            if response.has_next and response.next_cursor == requested_url:
                if response.data:
                    if on_progress:
                        on_progress(
                            PaginationProgress(
                                page_number=page_number,
                                items_in_page=items_in_page,
                                items_so_far=items_so_far,
                                has_next=False,  # Loop detected, no more pages
                            )
                        )
                    yield response
                break

            if response.data:
                if on_progress:
                    on_progress(
                        PaginationProgress(
                            page_number=page_number,
                            items_in_page=items_in_page,
                            items_so_far=items_so_far,
                            has_next=response.has_next,
                        )
                    )
                yield response

            if not response.has_next:
                break

    async def all(self, *, limit: int | None = _DEFAULT_LIMIT) -> list[T]:
        """
        Fetch all items across all pages into a list.

        Args:
            limit: Maximum items to fetch. Default 100,000. Set to None for unlimited.

        Returns:
            List of all items.

        Raises:
            TooManyResultsError: If results exceed limit.

        Note:
            The check occurs after extending results, so the final list may exceed
            limit by up to one page before the error is raised.

        Example:
            # Default - safe for most use cases
            persons = [p async for p in client.persons.all()]  # Using async iterator

            # Or use .all() method with limit check
            it = AsyncPageIterator(fetch_page)
            persons = await it.all()  # Returns list, raises if > 100k

            # Explicit unlimited for large exports
            all_persons = await it.all(limit=None)

            # Custom limit
            persons = await it.all(limit=500_000)
        """
        results: list[T] = []

        async for page in self.pages():
            results.extend(page.data)

            if limit is not None and len(results) > limit:
                raise TooManyResultsError(
                    f"Exceeded limit={limit:,} items. "
                    f"Use pages() for streaming, add a filter, or pass limit=None."
                )

        return results


# =============================================================================
# V1 Pagination Response (DEPRECATED - use PaginatedResponse)
# =============================================================================

# V1PaginatedResponse is now a deprecated alias for PaginatedResponse.
# The unified PaginatedResponse handles both V1 (token-based) and V2 (cursor-based)
# pagination formats transparently.
#
# For backward compatibility, V1PaginatedResponse is still accessible but will
# emit a DeprecationWarning when imported.

_V1PaginatedResponse = PaginatedResponse  # Internal alias for direct access


def __getattr__(name: str) -> type:
    """Module-level getattr to provide deprecation warning for V1PaginatedResponse."""
    if name == "V1PaginatedResponse":
        warnings.warn(
            "V1PaginatedResponse is deprecated. Use PaginatedResponse instead, "
            "which now handles both V1 and V2 pagination formats.",
            DeprecationWarning,
            stacklevel=2,
        )
        return PaginatedResponse
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# Batch Operation Response (V2)
# =============================================================================


class BatchOperationResult(AffinityModel):
    """Result of a single operation in a batch."""

    field_id: str = Field(alias="fieldId")
    success: bool
    error: str | None = None


class BatchOperationResponse(AffinityModel):
    """Response from batch field operations.

    Affinity's ``update-fields`` PATCH returns ``{"operation": "update-fields"}`` on success and
    an error for the whole batch otherwise (all-or-nothing), so ``results`` is usually empty.
    """

    operation: str | None = None
    results: list[BatchOperationResult] = Field(default_factory=list)

    @property
    def all_successful(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def failures(self) -> list[BatchOperationResult]:
        return [r for r in self.results if not r.success]


# =============================================================================
# Success Response (V1 delete operations)
# =============================================================================


class SuccessResponse(AffinityModel):
    """Simple success response from V1 delete operations."""

    success: bool
