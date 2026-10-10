"""
Opportunity service.

Opportunities can be retrieved via v2 endpoints, but full "row" data (fields)
is available via list entries.
"""

from __future__ import annotations

import asyncio
import builtins
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from ..exceptions import AffinityError, NotFoundError
from ..models.entities import (
    Company,
    Opportunity,
    OpportunityCreate,
    OpportunityUpdate,
    Person,
)
from ..models.pagination import (
    AsyncPageIterator,
    PageIterator,
    PaginatedResponse,
    PaginationInfo,
)
from ..models.secondary import NoteV2
from ..models.types import CompanyId, ListId, OpportunityId, PersonId
from ._org_reads import check_cursor_alone, note_params, page_of
from .lists import AsyncListEntryService, ListEntryService

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient


class OpportunityAssociations(NamedTuple):
    """Person and company associations for an opportunity."""

    person_ids: builtins.list[PersonId]
    company_ids: builtins.list[CompanyId]


class OpportunityService:
    """
    Service for managing opportunities.

    Notes:
    - V2 opportunity endpoints may return partial representations (e.g. name and
      listId only). The SDK does not perform hidden follow-up calls to "complete"
      an opportunity.
    - For full opportunity row data (including list fields), use list entries
      explicitly via `client.lists.entries(list_id)`.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    # =========================================================================
    # Read Operations (V2 API by default)
    # =========================================================================

    def get(self, opportunity_id: OpportunityId, *, retries: int = 0) -> Opportunity:
        """
        Get a single opportunity by ID.

        Args:
            opportunity_id: The opportunity ID
            retries: Number of retries on 404 NotFoundError. Default is 0 (fail fast).
                Set to 2-3 if calling immediately after create() to handle V1→V2
                eventual consistency lag.

        Returns:
            The opportunity representation returned by v2 (may be partial).

        Raises:
            NotFoundError: If opportunity does not exist after all retries.
        """
        last_error: NotFoundError | None = None
        attempts = retries + 1  # retries=0 means 1 attempt

        for attempt in range(attempts):
            try:
                data = self._client.get(f"/opportunities/{opportunity_id}")
                return Opportunity.model_validate(data)
            except NotFoundError as e:
                last_error = e
                if attempt < attempts - 1:  # Don't sleep after last attempt
                    time.sleep(0.5 * (attempt + 1))  # 0.5s, 1s, 1.5s backoff

        raise last_error  # type: ignore[misc]

    def get_details(self, opportunity_id: OpportunityId) -> Opportunity:
        """
        Get a single opportunity by ID with a more complete representation.

        Includes association IDs and (when present) list entries, which are not
        always included in the default `get()` response.

        See Also:
            - :meth:`get_associated_person_ids`: Get just person IDs (single API call)
            - :meth:`get_associated_people`: Get full Person objects
            - :meth:`get_associated_company_ids`: Get just company IDs (single API call)
            - :meth:`get_associated_companies`: Get full Company objects
            - :meth:`get_associations`: Get both person and company IDs in one call
        """
        # Uses the v1 endpoint because it returns a fuller payload (including
        # association IDs and, when present, list entries).
        data = self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        return Opportunity.model_validate(data)

    def list(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[Opportunity]:
        """
        List all opportunities.

        Args:
            ids: Specific opportunity IDs to fetch (batch lookup)
            limit: Maximum number of results per page
            cursor: Cursor to resume pagination (opaque; obtained from prior responses)

        Returns the v2 opportunity representation (which may be partial).
        For full opportunity row data, use list entries explicitly.
        """
        if cursor is not None:
            if ids is not None or limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = self._client.get_url(cursor)
        else:
            params: dict[str, Any] = {}
            if ids:
                params["ids"] = [int(id_) for id_ in ids]
            if limit is not None:
                params["limit"] = limit
            data = self._client.get("/opportunities", params=params or None)

        return PaginatedResponse[Opportunity](
            data=[Opportunity.model_validate(item) for item in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    def get_first(self) -> Opportunity | None:
        """
        Get an opportunity, or None if none exist.

        NOTE: With no filter or ordering guarantee, the returned opportunity
        is arbitrary (determined by API default ordering). Primarily useful
        for "does any opportunity exist?" checks.
        """
        page = self.list(limit=1)
        return page.data[0] if page.data else None

    def pages(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Iterator[PaginatedResponse[Opportunity]]:
        """
        Iterate opportunity pages (not items), yielding `PaginatedResponse[Opportunity]`.

        This is useful for ETL scripts that want checkpoint/resume via `page.next_cursor`.

        Args:
            ids: Specific opportunity IDs to fetch (batch lookup)
            limit: Maximum results per page
            cursor: Cursor to resume pagination
        """
        other_params = (ids, limit)
        if cursor is not None and any(p is not None for p in other_params):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        page = self.list(cursor=cursor) if cursor is not None else self.list(ids=ids, limit=limit)
        while True:
            yield page
            if not page.has_next:
                return
            next_cursor = page.next_cursor
            if next_cursor is None or next_cursor == requested_cursor:
                return
            requested_cursor = next_cursor
            page = self.list(cursor=next_cursor)

    def all(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
    ) -> Iterator[Opportunity]:
        """
        Iterate through all opportunities with automatic pagination.

        Args:
            ids: Specific opportunity IDs to fetch (batch lookup)
        """

        def fetch_page(next_url: str | None) -> PaginatedResponse[Opportunity]:
            if next_url:
                data = self._client.get_url(next_url)
                return PaginatedResponse[Opportunity](
                    data=[Opportunity.model_validate(item) for item in data.get("data", [])],
                    pagination=PaginationInfo.model_validate(data.get("pagination", {})),
                )
            return self.list(ids=ids)

        return PageIterator(fetch_page)

    def iter(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
    ) -> Iterator[Opportunity]:
        """
        Auto-paginate all opportunities.

        Alias for `all()` (FR-006 public contract).
        """
        return self.all(ids=ids)

    # =========================================================================
    # Search (V1 API)
    # =========================================================================

    def search(
        self,
        term: str | None = None,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Opportunity]:
        """
        Search for opportunities by name.

        Uses V1 API for search functionality.

        Args:
            term: Search term (matches opportunity name). If None, returns all.
            page_size: Results per page (max 500)
            page_token: Pagination token

        Returns:
            PaginatedResponse with opportunities and next_page_token
        """
        params: dict[str, Any] = {}
        if term:
            params["term"] = term
        if page_size:
            params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token

        data = self._client.get("/opportunities", params=params, v1=True)
        items = [Opportunity.model_validate(o) for o in data.get("opportunities", [])]
        return PaginatedResponse[Opportunity](
            data=items,
            next_page_token=data.get("next_page_token"),
        )

    def search_pages(
        self,
        term: str | None = None,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> Iterator[PaginatedResponse[Opportunity]]:
        """
        Iterate V1 opportunity-search result pages.

        Useful for scripts that need checkpoint/resume via `next_page_token`.

        Args:
            term: Search term (matches opportunity name). If None, returns all.
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            PaginatedResponse[Opportunity] for each page
        """
        requested_token = page_token
        page = self.search(term, page_size=page_size, page_token=page_token)
        while True:
            yield page
            next_token = page.next_page_token
            if not next_token or next_token == requested_token:
                return
            requested_token = next_token
            page = self.search(term, page_size=page_size, page_token=next_token)

    def search_all(
        self,
        term: str | None = None,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> Iterator[Opportunity]:
        """
        Iterate all V1 opportunity-search results with automatic pagination.

        Args:
            term: Search term (matches opportunity name). If None, returns all.
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            Each Opportunity individually
        """
        for page in self.search_pages(term, page_size=page_size, page_token=page_token):
            yield from page.data

    def resolve(
        self,
        *,
        name: str,
        list_id: ListId,
        limit: int | None = None,
    ) -> Opportunity | None:
        """
        Find a single opportunity by exact name within a specific list.

        Notes:
        - Opportunities are list-scoped; a list id is required.
        - This iterates list-entry pages client-side (no dedicated search endpoint).
        - If multiple matches exist, returns the first match in server-provided order.
        """
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty")
        name_lower = name.lower()

        entries = ListEntryService(self._client, list_id)
        for page in entries.pages(limit=limit):
            for entry in page.data:
                entity = entry.entity
                if isinstance(entity, Opportunity) and entity.name.lower() == name_lower:
                    return entity
        return None

    def resolve_all(
        self,
        *,
        name: str,
        list_id: ListId,
        limit: int | None = None,
    ) -> builtins.list[Opportunity]:
        """
        Find all opportunities matching a name within a specific list.

        Notes:
        - Opportunities are list-scoped; a list id is required.
        - This iterates list-entry pages client-side (no dedicated search endpoint).
        """
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty")
        name_lower = name.lower()
        matches: builtins.list[Opportunity] = []

        entries = ListEntryService(self._client, list_id)
        for page in entries.pages(limit=limit):
            for entry in page.data:
                entity = entry.entity
                if isinstance(entity, Opportunity) and entity.name.lower() == name_lower:
                    matches.append(entity)
        return matches

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    def create(self, data: OpportunityCreate) -> Opportunity:
        """
        Create a new opportunity.

        The opportunity will be added to the specified list.

        Args:
            data: Opportunity creation data including list_id and name

        Returns:
            The created opportunity
        """
        payload = data.model_dump(by_alias=True, mode="json", exclude_none=True)
        if not data.person_ids:
            payload.pop("person_ids", None)
        if not data.company_ids:
            payload.pop("organization_ids", None)

        result = self._client.post("/opportunities", json=payload, v1=True)
        return Opportunity.model_validate(result)

    def update(self, opportunity_id: OpportunityId, data: OpportunityUpdate) -> Opportunity:
        """
        Update an existing opportunity.

        Note: When provided, `person_ids` and `company_ids` replace the existing
        values. To add or remove associations safely, pass the full desired arrays.
        """
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )

        # Uses the v1 endpoint; its PUT semantics replace association arrays.
        result = self._client.put(f"/opportunities/{opportunity_id}", json=payload, v1=True)
        return Opportunity.model_validate(result)

    def delete(self, opportunity_id: OpportunityId) -> bool:
        """
        Delete an opportunity.

        This removes the opportunity and all associated list entries.

        Args:
            opportunity_id: The opportunity to delete

        Returns:
            True if successful
        """
        result = self._client.delete(f"/opportunities/{opportunity_id}", v1=True)
        return bool(result.get("success", False))

    # =========================================================================
    # Association Methods (V1 API)
    # =========================================================================

    def get_associated_person_ids(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[PersonId]:
        """
        Get associated person IDs for an opportunity.

        V1-only: V2 does not expose opportunity -> person associations.
        Uses GET `/opportunities/{id}` (V1) and returns `person_ids`.

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of person IDs to return

        Returns:
            List of PersonId values associated with this opportunity

        See Also:
            - :meth:`get_associated_people`: Returns full Person objects
            - :meth:`get_associations`: Get both person and company IDs in one call
        """
        data = self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        # Defensive: V1 returns directly (not wrapped), but handle potential wrapper
        # for consistency with CompanyService pattern that handles "organization" wrapper
        opportunity = data.get("opportunity") if isinstance(data, dict) else None
        source = opportunity if isinstance(opportunity, dict) else data
        person_ids = None
        if isinstance(source, dict):
            person_ids = source.get("person_ids") or source.get("personIds")

        if not isinstance(person_ids, list):
            return []

        ids = [PersonId(int(pid)) for pid in person_ids if pid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    def get_associated_people(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[Person]:
        """
        Get Person objects associated with an opportunity.

        Uses V2 batch lookup for efficiency (1 API call per 100 persons
        instead of 1 per person).

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of people to return

        Returns:
            List of Person objects associated with this opportunity
        """
        person_ids = self.get_associated_person_ids(opportunity_id, max_results=max_results)
        if not person_ids:
            return []

        # Use V2 batch lookup: GET /persons?ids=1&ids=2&ids=3
        # Note: person_ids is already truncated by get_associated_person_ids if max_results set
        params: dict[str, Any] = {"ids": [int(pid) for pid in person_ids]}

        people: builtins.list[Person] = []
        data = self._client.get("/persons", params=params)  # V2 batch
        for item in data.get("data", []):
            people.append(Person.model_validate(item))

        # Handle pagination if needed (>100 persons)
        # Note: max_results check is defensive - person_ids was already truncated above
        pagination = data.get("pagination", {})
        next_url = pagination.get("nextUrl")
        while next_url and (max_results is None or len(people) < max_results):
            data = self._client.get_url(next_url)
            for item in data.get("data", []):
                people.append(Person.model_validate(item))
            next_url = data.get("pagination", {}).get("nextUrl")

        if max_results:
            return people[:max_results]
        return people

    def get_associated_company_ids(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[CompanyId]:
        """
        Get associated company IDs for an opportunity.

        V1-only: V2 does not expose opportunity -> company associations.
        Uses GET `/opportunities/{id}` (V1) and returns `organization_ids`.

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of company IDs to return

        Returns:
            List of CompanyId values associated with this opportunity

        See Also:
            - :meth:`get_associated_companies`: Returns full Company objects
            - :meth:`get_associations`: Get both person and company IDs in one call
        """
        data = self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        # Defensive: V1 returns directly (not wrapped), but handle potential wrapper
        # for consistency with CompanyService pattern that handles "organization" wrapper
        opportunity = data.get("opportunity") if isinstance(data, dict) else None
        source = opportunity if isinstance(opportunity, dict) else data
        company_ids = None
        if isinstance(source, dict):
            company_ids = source.get("organization_ids") or source.get("organizationIds")

        if not isinstance(company_ids, list):
            return []

        ids = [CompanyId(int(cid)) for cid in company_ids if cid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    def get_associated_companies(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[Company]:
        """
        Get Company objects associated with an opportunity.

        Uses V2 batch lookup for efficiency (1 API call per 100 companies
        instead of 1 per company).

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of companies to return

        Returns:
            List of Company objects associated with this opportunity
        """
        company_ids = self.get_associated_company_ids(opportunity_id, max_results=max_results)
        if not company_ids:
            return []

        # Use V2 batch lookup: GET /companies?ids=1&ids=2&ids=3
        # Note: company_ids is already truncated by get_associated_company_ids if max_results set
        params: dict[str, Any] = {"ids": [int(cid) for cid in company_ids]}

        companies: builtins.list[Company] = []
        data = self._client.get("/companies", params=params)  # V2 batch
        for item in data.get("data", []):
            companies.append(Company.model_validate(item))

        # Handle pagination if needed (>100 companies)
        # Note: max_results check is defensive - company_ids was already truncated above
        pagination = data.get("pagination", {})
        next_url = pagination.get("nextUrl")
        while next_url and (max_results is None or len(companies) < max_results):
            data = self._client.get_url(next_url)
            for item in data.get("data", []):
                companies.append(Company.model_validate(item))
            next_url = data.get("pagination", {}).get("nextUrl")

        if max_results:
            return companies[:max_results]
        return companies

    def get_associations(
        self,
        opportunity_id: OpportunityId,
    ) -> OpportunityAssociations:
        """
        Get both person and company associations in a single V1 call.

        Use this when you need both types of associations to avoid
        duplicate API calls from separate get_associated_*_ids() calls.

        Args:
            opportunity_id: The opportunity ID

        Returns:
            OpportunityAssociations named tuple with person_ids and company_ids

        Example:
            assoc = client.opportunities.get_associations(opp_id)
            print(assoc.person_ids)   # IDE autocomplete works
            print(assoc.company_ids)  # IDE autocomplete works
        """
        data = self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        # Defensive: V1 returns directly (not wrapped), but handle potential wrapper
        opportunity = data.get("opportunity") if isinstance(data, dict) else None
        source = opportunity if isinstance(opportunity, dict) else data

        person_ids: builtins.list[PersonId] = []
        company_ids: builtins.list[CompanyId] = []

        if isinstance(source, dict):
            raw_person_ids = source.get("person_ids") or source.get("personIds")
            raw_company_ids = source.get("organization_ids") or source.get("organizationIds")

            if isinstance(raw_person_ids, list):
                person_ids = [PersonId(int(pid)) for pid in raw_person_ids if pid is not None]
            if isinstance(raw_company_ids, list):
                company_ids = [CompanyId(int(cid)) for cid in raw_company_ids if cid is not None]

        return OpportunityAssociations(person_ids=person_ids, company_ids=company_ids)

    def get_associated_person_ids_batch(
        self,
        opportunity_ids: Sequence[OpportunityId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[OpportunityId, builtins.list[PersonId]]:
        """
        Get person associations for multiple opportunities.

        Makes one V1 API call per opportunity. Useful for iterating list entries
        where V2 returns empty person_ids.

        Args:
            opportunity_ids: Sequence of opportunity IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping opportunity_id -> list of person_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails. The exception
                includes the failing opportunity_id in its context.

        Example:
            # Get all persons from an opportunity list
            opp_ids = [entry.entity.id for entry in client.lists.entries(list_id).all()]
            associations = client.opportunities.get_associated_person_ids_batch(opp_ids)
            all_person_ids = set()
            for person_ids in associations.values():
                all_person_ids.update(person_ids)
        """
        result: dict[OpportunityId, builtins.list[PersonId]] = {}
        for opp_id in opportunity_ids:
            try:
                result[opp_id] = self.get_associated_person_ids(opp_id)
            except AffinityError:
                # Re-raise SDK errors directly - they already have good context
                if on_error == "raise":
                    raise
                # skip: continue without this opportunity
            except Exception as e:
                if on_error == "raise":
                    # Wrap non-SDK errors with context, preserving chain
                    raise AffinityError(
                        f"Failed to get associations for opportunity {opp_id}: {e}"
                    ) from e
                # skip: continue without this opportunity
        return result

    def get_associated_company_ids_batch(
        self,
        opportunity_ids: Sequence[OpportunityId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[OpportunityId, builtins.list[CompanyId]]:
        """
        Get company associations for multiple opportunities.

        Makes one V1 API call per opportunity. Useful for iterating list entries
        where V2 returns empty company_ids.

        Args:
            opportunity_ids: Sequence of opportunity IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping opportunity_id -> list of company_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails. The exception
                includes the failing opportunity_id in its context.

        Example:
            # Get all companies from an opportunity list
            opp_ids = [entry.entity.id for entry in client.lists.entries(list_id).all()]
            associations = client.opportunities.get_associated_company_ids_batch(opp_ids)
            all_company_ids = set()
            for company_ids in associations.values():
                all_company_ids.update(company_ids)
        """
        result: dict[OpportunityId, builtins.list[CompanyId]] = {}
        for opp_id in opportunity_ids:
            try:
                result[opp_id] = self.get_associated_company_ids(opp_id)
            except AffinityError:
                # Re-raise SDK errors directly - they already have good context
                if on_error == "raise":
                    raise
                # skip: continue without this opportunity
            except Exception as e:
                if on_error == "raise":
                    # Wrap non-SDK errors with context, preserving chain
                    raise AffinityError(
                        f"Failed to get company associations for opportunity {opp_id}: {e}"
                    ) from e
                # skip: continue without this opportunity
        return result

    # =========================================================================
    # Notes (V2)
    # =========================================================================

    def list_notes(
        self,
        opportunity_id: OpportunityId,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[NoteV2]:
        """One page of this opportunity's notes as V2 notes (``NoteV2``; replies not included).

        Filters: ``creator_id``, ``created_after`` (inclusive) / ``created_before`` (exclusive),
        ``updated_after``, rounded outward to whole seconds. ``limit`` is 1-100.
        Affinity may refuse opportunity notes (403 → ``AuthorizationError``).
        """
        check_cursor_alone(
            cursor, creator_id, created_after, created_before, updated_after, limit, total_count
        )
        if cursor is not None:
            return page_of(NoteV2, self._client.get_url(cursor, min_api_version=None))
        params = note_params(
            creator_id=creator_id,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            NoteV2,
            self._client.get(
                f"/opportunities/{int(opportunity_id)}/notes",
                params=params or None,
                min_api_version=None,
            ),
        )

    def iter_notes(
        self,
        opportunity_id: OpportunityId,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[NoteV2]:
        """All of this opportunity's notes (every page); see :meth:`list_notes`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[NoteV2]:
            if next_url:
                return self.list_notes(opportunity_id, cursor=next_url)
            return self.list_notes(
                opportunity_id,
                creator_id=creator_id,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                limit=limit,
            )

        return PageIterator(fetch_page)


class AsyncOpportunityService:
    """Async version of OpportunityService (TR-009)."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def get(self, opportunity_id: OpportunityId, *, retries: int = 0) -> Opportunity:
        """
        Get a single opportunity by ID.

        Args:
            opportunity_id: The opportunity ID
            retries: Number of retries on 404 NotFoundError. Default is 0 (fail fast).
                Set to 2-3 if calling immediately after create() to handle V1→V2
                eventual consistency lag.

        Returns:
            The opportunity representation returned by v2 (may be partial).

        Raises:
            NotFoundError: If opportunity does not exist after all retries.
        """
        last_error: NotFoundError | None = None
        attempts = retries + 1  # retries=0 means 1 attempt

        for attempt in range(attempts):
            try:
                data = await self._client.get(f"/opportunities/{opportunity_id}")
                return Opportunity.model_validate(data)
            except NotFoundError as e:
                last_error = e
                if attempt < attempts - 1:  # Don't sleep after last attempt
                    await asyncio.sleep(0.5 * (attempt + 1))  # 0.5s, 1s, 1.5s backoff

        raise last_error  # type: ignore[misc]

    async def get_details(self, opportunity_id: OpportunityId) -> Opportunity:
        """
        Get a single opportunity by ID with a more complete representation.

        Includes association IDs and (when present) list entries, which are not
        always included in the default `get()` response.

        See Also:
            - :meth:`get_associated_person_ids`: Get just person IDs (single API call)
            - :meth:`get_associated_people`: Get full Person objects
            - :meth:`get_associated_company_ids`: Get just company IDs (single API call)
            - :meth:`get_associated_companies`: Get full Company objects
            - :meth:`get_associations`: Get both person and company IDs in one call
        """
        # Uses the v1 endpoint because it returns a fuller payload (including
        # association IDs and, when present, list entries).
        data = await self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        return Opportunity.model_validate(data)

    async def list(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[Opportunity]:
        """
        List all opportunities.

        Args:
            ids: Specific opportunity IDs to fetch (batch lookup)
            limit: Maximum number of results per page
            cursor: Cursor to resume pagination (opaque; obtained from prior responses)

        Returns the v2 opportunity representation (which may be partial).
        For full opportunity row data, use list entries explicitly.
        """
        if cursor is not None:
            if ids is not None or limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = await self._client.get_url(cursor)
        else:
            params: dict[str, Any] = {}
            if ids:
                params["ids"] = [int(id_) for id_ in ids]
            if limit is not None:
                params["limit"] = limit
            data = await self._client.get("/opportunities", params=params or None)

        return PaginatedResponse[Opportunity](
            data=[Opportunity.model_validate(item) for item in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    async def get_first(self) -> Opportunity | None:
        """
        Get an opportunity, or None if none exist.

        See OpportunityService.get_first() for details.
        """
        page = await self.list(limit=1)
        return page.data[0] if page.data else None

    async def batch_get(
        self,
        opportunity_ids: Sequence[OpportunityId],
        *,
        max_concurrent: int = 10,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[OpportunityId, Opportunity]:
        """
        Fetch multiple opportunities with controlled concurrency.

        Makes individual get() calls with bounded concurrency.

        Args:
            opportunity_ids: Opportunity IDs to fetch
            max_concurrent: Maximum concurrent API calls (default: 10)
            on_error: How to handle AffinityError exceptions:
                - "raise": Raise on first AffinityError (default)
                - "skip": Skip failed IDs, return partial results

        Returns:
            Dict mapping opportunity_id -> Opportunity for successfully fetched items.

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        if not opportunity_ids:
            return {}
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        unique_ids = list(dict.fromkeys(opportunity_ids))
        results: dict[OpportunityId, Opportunity] = {}

        async def fetch_one(oid: OpportunityId) -> tuple[OpportunityId, Opportunity | None]:
            try:
                opp = await self.get(oid)
                return (oid, opp)
            except AffinityError:
                if on_error == "raise":
                    raise
                return (oid, None)

        for i in range(0, len(unique_ids), max_concurrent):
            chunk = unique_ids[i : i + max_concurrent]
            tasks = [asyncio.create_task(fetch_one(oid)) for oid in chunk]
            try:
                for coro in asyncio.as_completed(tasks):
                    oid, opp = await coro
                    if opp is not None:
                        results[oid] = opp
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        return results

    async def pages(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[Opportunity]]:
        """
        Iterate opportunity pages (not items), yielding `PaginatedResponse[Opportunity]`.

        This is useful for ETL scripts that want checkpoint/resume via `page.next_cursor`.

        Args:
            ids: Specific opportunity IDs to fetch (batch lookup)
            limit: Maximum results per page
            cursor: Cursor to resume pagination
        """
        other_params = (ids, limit)
        if cursor is not None and any(p is not None for p in other_params):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        page = (
            await self.list(cursor=cursor)
            if cursor is not None
            else await self.list(ids=ids, limit=limit)
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

    def all(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
    ) -> AsyncIterator[Opportunity]:
        """
        Iterate through all opportunities with automatic pagination.

        Args:
            ids: Specific opportunity IDs to fetch (batch lookup)
        """

        async def fetch_page(next_url: str | None) -> PaginatedResponse[Opportunity]:
            if next_url:
                data = await self._client.get_url(next_url)
                return PaginatedResponse[Opportunity](
                    data=[Opportunity.model_validate(item) for item in data.get("data", [])],
                    pagination=PaginationInfo.model_validate(data.get("pagination", {})),
                )
            return await self.list(ids=ids)

        return AsyncPageIterator(fetch_page)

    def iter(
        self,
        *,
        ids: Sequence[OpportunityId] | None = None,
    ) -> AsyncIterator[Opportunity]:
        """
        Auto-paginate all opportunities.

        Alias for `all()` (FR-006 public contract).
        """
        return self.all(ids=ids)

    # =========================================================================
    # Search (V1 API)
    # =========================================================================

    async def search(
        self,
        term: str | None = None,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Opportunity]:
        """
        Search for opportunities by name.

        Uses V1 API for search functionality.

        Args:
            term: Search term (matches opportunity name). If None, returns all.
            page_size: Results per page (max 500)
            page_token: Pagination token

        Returns:
            PaginatedResponse with opportunities and next_page_token
        """
        params: dict[str, Any] = {}
        if term:
            params["term"] = term
        if page_size:
            params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token

        data = await self._client.get("/opportunities", params=params, v1=True)
        items = [Opportunity.model_validate(o) for o in data.get("opportunities", [])]
        return PaginatedResponse[Opportunity](
            data=items,
            next_page_token=data.get("next_page_token"),
        )

    async def search_pages(
        self,
        term: str | None = None,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[Opportunity]]:
        """
        Iterate V1 opportunity-search result pages.

        Useful for scripts that need checkpoint/resume via `next_page_token`.

        Args:
            term: Search term (matches opportunity name). If None, returns all.
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            PaginatedResponse[Opportunity] for each page
        """
        requested_token = page_token
        page = await self.search(term, page_size=page_size, page_token=page_token)
        while True:
            yield page
            next_token = page.next_page_token
            if not next_token or next_token == requested_token:
                return
            requested_token = next_token
            page = await self.search(term, page_size=page_size, page_token=next_token)

    async def search_all(
        self,
        term: str | None = None,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AsyncIterator[Opportunity]:
        """
        Iterate all V1 opportunity-search results with automatic pagination.

        Args:
            term: Search term (matches opportunity name). If None, returns all.
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            Each Opportunity individually
        """
        async for page in self.search_pages(term, page_size=page_size, page_token=page_token):
            for opp in page.data:
                yield opp

    async def resolve(
        self,
        *,
        name: str,
        list_id: ListId,
        limit: int | None = None,
    ) -> Opportunity | None:
        """
        Find a single opportunity by exact name within a specific list.

        Notes:
        - Opportunities are list-scoped; a list id is required.
        - This iterates list-entry pages client-side (no dedicated search endpoint).
        - If multiple matches exist, returns the first match in server-provided order.
        """
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty")
        name_lower = name.lower()

        entries = AsyncListEntryService(self._client, list_id)
        async for page in entries.pages(limit=limit):
            for entry in page.data:
                entity = entry.entity
                if isinstance(entity, Opportunity) and entity.name.lower() == name_lower:
                    return entity
        return None

    async def resolve_all(
        self,
        *,
        name: str,
        list_id: ListId,
        limit: int | None = None,
    ) -> builtins.list[Opportunity]:
        """
        Find all opportunities matching a name within a specific list.

        Notes:
        - Opportunities are list-scoped; a list id is required.
        - This iterates list-entry pages client-side (no dedicated search endpoint).
        """
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty")
        name_lower = name.lower()
        matches: builtins.list[Opportunity] = []

        entries = AsyncListEntryService(self._client, list_id)
        async for page in entries.pages(limit=limit):
            for entry in page.data:
                entity = entry.entity
                if isinstance(entity, Opportunity) and entity.name.lower() == name_lower:
                    matches.append(entity)
        return matches

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    async def create(self, data: OpportunityCreate) -> Opportunity:
        """
        Create a new opportunity.

        The opportunity will be added to the specified list.

        Args:
            data: Opportunity creation data including list_id and name

        Returns:
            The created opportunity
        """
        payload = data.model_dump(by_alias=True, mode="json", exclude_none=True)
        if not data.person_ids:
            payload.pop("person_ids", None)
        if not data.company_ids:
            payload.pop("organization_ids", None)

        result = await self._client.post("/opportunities", json=payload, v1=True)
        return Opportunity.model_validate(result)

    async def update(self, opportunity_id: OpportunityId, data: OpportunityUpdate) -> Opportunity:
        """
        Update an existing opportunity.

        Note: When provided, `person_ids` and `company_ids` replace the existing
        values. To add or remove associations safely, pass the full desired arrays.
        """
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )

        # Uses the v1 endpoint; its PUT semantics replace association arrays.
        result = await self._client.put(f"/opportunities/{opportunity_id}", json=payload, v1=True)
        return Opportunity.model_validate(result)

    async def delete(self, opportunity_id: OpportunityId) -> bool:
        """
        Delete an opportunity.

        This removes the opportunity and all associated list entries.

        Args:
            opportunity_id: The opportunity to delete

        Returns:
            True if successful
        """
        result = await self._client.delete(f"/opportunities/{opportunity_id}", v1=True)
        return bool(result.get("success", False))

    # =========================================================================
    # Association Methods (V1 API)
    # =========================================================================

    async def get_associated_person_ids(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[PersonId]:
        """
        Get associated person IDs for an opportunity.

        V1-only: V2 does not expose opportunity -> person associations.
        Uses GET `/opportunities/{id}` (V1) and returns `person_ids`.

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of person IDs to return

        Returns:
            List of PersonId values associated with this opportunity

        See Also:
            - :meth:`get_associated_people`: Returns full Person objects
            - :meth:`get_associations`: Get both person and company IDs in one call
        """
        data = await self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        # Defensive: V1 returns directly (not wrapped), but handle potential wrapper
        opportunity = data.get("opportunity") if isinstance(data, dict) else None
        source = opportunity if isinstance(opportunity, dict) else data
        person_ids = None
        if isinstance(source, dict):
            person_ids = source.get("person_ids") or source.get("personIds")

        if not isinstance(person_ids, list):
            return []

        ids = [PersonId(int(pid)) for pid in person_ids if pid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    async def get_associated_people(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[Person]:
        """
        Get Person objects associated with an opportunity.

        Uses V2 batch lookup for efficiency (1 API call per 100 persons
        instead of 1 per person).

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of people to return

        Returns:
            List of Person objects associated with this opportunity
        """
        person_ids = await self.get_associated_person_ids(opportunity_id, max_results=max_results)
        if not person_ids:
            return []

        # Use V2 batch lookup: GET /persons?ids=1&ids=2&ids=3
        # Note: person_ids is already truncated by get_associated_person_ids if max_results set
        params: dict[str, Any] = {"ids": [int(pid) for pid in person_ids]}

        people: builtins.list[Person] = []
        data = await self._client.get("/persons", params=params)  # V2 batch
        for item in data.get("data", []):
            people.append(Person.model_validate(item))

        # Handle pagination if needed (>100 persons)
        # Note: max_results check is defensive - person_ids was already truncated above
        pagination = data.get("pagination", {})
        next_url = pagination.get("nextUrl")
        while next_url and (max_results is None or len(people) < max_results):
            data = await self._client.get_url(next_url)
            for item in data.get("data", []):
                people.append(Person.model_validate(item))
            next_url = data.get("pagination", {}).get("nextUrl")

        if max_results:
            return people[:max_results]
        return people

    async def get_associated_company_ids(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[CompanyId]:
        """
        Get associated company IDs for an opportunity.

        V1-only: V2 does not expose opportunity -> company associations.
        Uses GET `/opportunities/{id}` (V1) and returns `organization_ids`.

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of company IDs to return

        Returns:
            List of CompanyId values associated with this opportunity

        See Also:
            - :meth:`get_associated_companies`: Returns full Company objects
            - :meth:`get_associations`: Get both person and company IDs in one call
        """
        data = await self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        # Defensive: V1 returns directly (not wrapped), but handle potential wrapper
        opportunity = data.get("opportunity") if isinstance(data, dict) else None
        source = opportunity if isinstance(opportunity, dict) else data
        company_ids = None
        if isinstance(source, dict):
            company_ids = source.get("organization_ids") or source.get("organizationIds")

        if not isinstance(company_ids, list):
            return []

        ids = [CompanyId(int(cid)) for cid in company_ids if cid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    async def get_associated_companies(
        self,
        opportunity_id: OpportunityId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[Company]:
        """
        Get Company objects associated with an opportunity.

        Uses V2 batch lookup for efficiency (1 API call per 100 companies
        instead of 1 per company).

        Args:
            opportunity_id: The opportunity ID
            max_results: Maximum number of companies to return

        Returns:
            List of Company objects associated with this opportunity
        """
        company_ids = await self.get_associated_company_ids(opportunity_id, max_results=max_results)
        if not company_ids:
            return []

        # Use V2 batch lookup: GET /companies?ids=1&ids=2&ids=3
        # Note: company_ids is already truncated by get_associated_company_ids if max_results set
        params: dict[str, Any] = {"ids": [int(cid) for cid in company_ids]}

        companies: builtins.list[Company] = []
        data = await self._client.get("/companies", params=params)  # V2 batch
        for item in data.get("data", []):
            companies.append(Company.model_validate(item))

        # Handle pagination if needed (>100 companies)
        # Note: max_results check is defensive - company_ids was already truncated above
        pagination = data.get("pagination", {})
        next_url = pagination.get("nextUrl")
        while next_url and (max_results is None or len(companies) < max_results):
            data = await self._client.get_url(next_url)
            for item in data.get("data", []):
                companies.append(Company.model_validate(item))
            next_url = data.get("pagination", {}).get("nextUrl")

        if max_results:
            return companies[:max_results]
        return companies

    async def get_associations(
        self,
        opportunity_id: OpportunityId,
    ) -> OpportunityAssociations:
        """
        Get both person and company associations in a single V1 call.

        Use this when you need both types of associations to avoid
        duplicate API calls from separate get_associated_*_ids() calls.

        Args:
            opportunity_id: The opportunity ID

        Returns:
            OpportunityAssociations named tuple with person_ids and company_ids

        Example:
            assoc = await client.opportunities.get_associations(opp_id)
            print(assoc.person_ids)   # IDE autocomplete works
            print(assoc.company_ids)  # IDE autocomplete works
        """
        data = await self._client.get(f"/opportunities/{opportunity_id}", v1=True)
        # Defensive: V1 returns directly (not wrapped), but handle potential wrapper
        opportunity = data.get("opportunity") if isinstance(data, dict) else None
        source = opportunity if isinstance(opportunity, dict) else data

        person_ids: builtins.list[PersonId] = []
        company_ids: builtins.list[CompanyId] = []

        if isinstance(source, dict):
            raw_person_ids = source.get("person_ids") or source.get("personIds")
            raw_company_ids = source.get("organization_ids") or source.get("organizationIds")

            if isinstance(raw_person_ids, list):
                person_ids = [PersonId(int(pid)) for pid in raw_person_ids if pid is not None]
            if isinstance(raw_company_ids, list):
                company_ids = [CompanyId(int(cid)) for cid in raw_company_ids if cid is not None]

        return OpportunityAssociations(person_ids=person_ids, company_ids=company_ids)

    async def get_associated_person_ids_batch(
        self,
        opportunity_ids: Sequence[OpportunityId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[OpportunityId, builtins.list[PersonId]]:
        """
        Get person associations for multiple opportunities.

        Makes one V1 API call per opportunity. Useful for iterating list entries
        where V2 returns empty person_ids.

        Args:
            opportunity_ids: Sequence of opportunity IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping opportunity_id -> list of person_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails. The exception
                includes the failing opportunity_id in its context.

        Example:
            # Get all persons from an opportunity list
            opp_ids = [entry.entity.id async for entry in client.lists.entries(list_id).all()]
            associations = await client.opportunities.get_associated_person_ids_batch(opp_ids)
            all_person_ids = set()
            for person_ids in associations.values():
                all_person_ids.update(person_ids)
        """
        result: dict[OpportunityId, builtins.list[PersonId]] = {}
        for opp_id in opportunity_ids:
            try:
                result[opp_id] = await self.get_associated_person_ids(opp_id)
            except AffinityError:
                # Re-raise SDK errors directly - they already have good context
                if on_error == "raise":
                    raise
                # skip: continue without this opportunity
            except Exception as e:
                if on_error == "raise":
                    # Wrap non-SDK errors with context, preserving chain
                    raise AffinityError(
                        f"Failed to get associations for opportunity {opp_id}: {e}"
                    ) from e
                # skip: continue without this opportunity
        return result

    async def get_associated_company_ids_batch(
        self,
        opportunity_ids: Sequence[OpportunityId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[OpportunityId, builtins.list[CompanyId]]:
        """
        Get company associations for multiple opportunities.

        Makes one V1 API call per opportunity. Useful for iterating list entries
        where V2 returns empty company_ids.

        Args:
            opportunity_ids: Sequence of opportunity IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping opportunity_id -> list of company_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails. The exception
                includes the failing opportunity_id in its context.

        Example:
            # Get all companies from an opportunity list
            opp_ids = [entry.entity.id async for entry in client.lists.entries(list_id).all()]
            associations = await client.opportunities.get_associated_company_ids_batch(opp_ids)
            all_company_ids = set()
            for company_ids in associations.values():
                all_company_ids.update(company_ids)
        """
        result: dict[OpportunityId, builtins.list[CompanyId]] = {}
        for opp_id in opportunity_ids:
            try:
                result[opp_id] = await self.get_associated_company_ids(opp_id)
            except AffinityError:
                # Re-raise SDK errors directly - they already have good context
                if on_error == "raise":
                    raise
                # skip: continue without this opportunity
            except Exception as e:
                if on_error == "raise":
                    # Wrap non-SDK errors with context, preserving chain
                    raise AffinityError(
                        f"Failed to get company associations for opportunity {opp_id}: {e}"
                    ) from e
                # skip: continue without this opportunity
        return result

    # =========================================================================
    # Notes (V2)
    # =========================================================================

    async def list_notes(
        self,
        opportunity_id: OpportunityId,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[NoteV2]:
        """One page of this opportunity's notes as V2 notes (``NoteV2``; replies not included).

        Filters: ``creator_id``, ``created_after`` (inclusive) / ``created_before`` (exclusive),
        ``updated_after``, rounded outward to whole seconds. ``limit`` is 1-100.
        Affinity may refuse opportunity notes (403 → ``AuthorizationError``).
        """
        check_cursor_alone(
            cursor, creator_id, created_after, created_before, updated_after, limit, total_count
        )
        if cursor is not None:
            return page_of(NoteV2, await self._client.get_url(cursor, min_api_version=None))
        params = note_params(
            creator_id=creator_id,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            NoteV2,
            await self._client.get(
                f"/opportunities/{int(opportunity_id)}/notes",
                params=params or None,
                min_api_version=None,
            ),
        )

    def iter_notes(
        self,
        opportunity_id: OpportunityId,
        *,
        creator_id: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[NoteV2]:
        """All of this opportunity's notes (every page); see :meth:`list_notes`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[NoteV2]:
            if next_url:
                return await self.list_notes(opportunity_id, cursor=next_url)
            return await self.list_notes(
                opportunity_id,
                creator_id=creator_id,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)
