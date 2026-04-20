"""
Company (Organization) service.

Provides operations for managing companies/organizations in Affinity.
Uses V2 API for reading, V1 API for writing.
"""

from __future__ import annotations

import asyncio
import builtins
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal

from ..exceptions import AffinityError, BetaEndpointDisabledError, NotFoundError
from ..filters import FilterExpression
from ..models.entities import (
    Company,
    CompanyCreate,
    CompanyUpdate,
    FieldMetadata,
    ListEntry,
    ListSummary,
    Person,
)
from ..models.pagination import (
    AsyncPageIterator,
    PageIterator,
    PaginatedResponse,
    PaginationInfo,
)
from ..models.secondary import MergeTask
from ..models.types import (
    AnyFieldId,
    CompanyId,
    FieldType,
    OpportunityId,
    PersonId,
    validate_entity_field_types,
)

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient


class CompanyService:
    """
    Service for managing companies (organizations).

    Note: Companies are called Organizations in the V1 API. This service
    uses V2 terminology throughout but routes to V1 for create/update/delete.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    # =========================================================================
    # Read Operations (V2 API)
    # =========================================================================

    def list(
        self,
        *,
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **_unsupported: Any,
    ) -> PaginatedResponse[Company]:
        """
        Get a page of companies.

        Args:
            ids: Specific company IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            limit: Maximum number of results (API default: 100)
            cursor: Cursor to resume pagination (opaque; obtained from prior responses)

        Returns:
            Paginated response with companies

        Note:
            The V2 /companies endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search companies
            by name or domain use ``client.companies.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /companies does not support server-side filter. "
                "To search by name/domain use client.companies.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        validate_entity_field_types(field_types, endpoint="company")
        if cursor is not None:
            if any(p is not None for p in (ids, field_ids, field_types, limit)):
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
            if field_ids:
                params["fieldIds"] = [str(field_id) for field_id in field_ids]
            if field_types:
                params["fieldTypes"] = [field_type.value for field_type in field_types]
            if limit:
                params["limit"] = limit
            data = self._client.get("/companies", params=params or None)

        return PaginatedResponse[Company](
            data=[Company.model_validate(c) for c in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    def get_first(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> Company | None:
        """
        Get the first company, or None if no results.

        This is a convenience method equivalent to:
            page = client.companies.list(limit=1)
            return page.data[0] if page.data else None

        Args:
            field_ids: Specific field IDs to include
            field_types: Field types to include

        Returns:
            First Company, or None if no results.

        Note:
            The V2 /companies endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search companies
            by name or domain use ``client.companies.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /companies does not support server-side filter. "
                "To search by name/domain use client.companies.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        page = self.list(
            field_ids=field_ids,
            field_types=field_types,
            limit=1,
        )
        return page.data[0] if page.data else None

    def pages(
        self,
        *,
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **_unsupported: Any,
    ) -> Iterator[PaginatedResponse[Company]]:
        """
        Iterate company pages (not items), yielding `PaginatedResponse[Company]`.

        Useful for ETL scripts that need checkpoint/resume via `page.next_cursor`.

        Args:
            ids: Specific company IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            limit: Maximum results per page
            cursor: Cursor to resume pagination

        Yields:
            PaginatedResponse[Company] for each page
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /companies does not support server-side filter. "
                "To search by name/domain use client.companies.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        other_params = (ids, field_ids, field_types, limit)
        if cursor is not None and any(p is not None for p in other_params):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        page = (
            self.list(cursor=cursor)
            if cursor is not None
            else self.list(ids=ids, field_ids=field_ids, field_types=field_types, limit=limit)
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

    def all(
        self,
        *,
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> Iterator[Company]:
        """
        Iterate through all companies with automatic pagination.

        Args:
            ids: Specific company IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include
            field_types: Field types to include

        Yields:
            Company objects

        Note:
            The V2 /companies endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search companies
            by name or domain use ``client.companies.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /companies does not support server-side filter. "
                "To search by name/domain use client.companies.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")

        def fetch_page(next_url: str | None) -> PaginatedResponse[Company]:
            if next_url:
                data = self._client.get_url(next_url)
            else:
                return self.list(
                    ids=ids,
                    field_ids=field_ids,
                    field_types=field_types,
                )
            return PaginatedResponse[Company](
                data=[Company.model_validate(c) for c in data.get("data", [])],
                pagination=PaginationInfo.model_validate(data.get("pagination", {})),
            )

        return PageIterator(fetch_page)

    def iter(
        self,
        *,
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> Iterator[Company]:
        """
        Auto-paginate all companies.

        Alias for `all()` (FR-006 public contract).

        Note:
            The V2 /companies endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search companies
            by name or domain use ``client.companies.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /companies does not support server-side filter. "
                "To search by name/domain use client.companies.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        return self.all(ids=ids, field_ids=field_ids, field_types=field_types)

    def get(
        self,
        company_id: CompanyId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        retries: int = 0,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Company:
        """
        Get a single company by ID.

        Args:
            company_id: The company ID
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            retries: Number of retries on 404 NotFoundError. Default is 0 (fail fast).
                Set to 2-3 if calling immediately after create() to handle eventual
                consistency lag.
            with_interaction_dates: Include interaction date summaries (last/next
                meeting dates, email dates).
            with_interaction_persons: Include person IDs for each interaction.
                Only applies when with_interaction_dates=True.

        Returns:
            Company object with requested field data. When with_interaction_dates=True,
            the Company will have interaction_dates and interactions populated.

        Raises:
            NotFoundError: If company does not exist after all retries.

        Note:
            When combining with_interaction_dates with field_ids/field_types,
            two API calls are made internally and the results are merged.
        """
        last_error: NotFoundError | None = None
        attempts = retries + 1  # retries=0 means 1 attempt
        has_field_filters = field_ids is not None or field_types is not None

        for attempt in range(attempts):
            try:
                if with_interaction_dates:
                    # Fetch interaction data
                    v1_params: dict[str, Any] = {"with_interaction_dates": True}
                    if with_interaction_persons:
                        v1_params["with_interaction_persons"] = True
                    interaction_data = self._client.get(
                        f"/organizations/{company_id}",
                        params=v1_params,
                        v1=True,
                    )

                    # If field filtering is also requested, fetch filtered fields and merge
                    if has_field_filters:
                        v2_params: dict[str, Any] = {}
                        if field_ids:
                            v2_params["fieldIds"] = [str(fid) for fid in field_ids]
                        if field_types:
                            v2_params["fieldTypes"] = [ft.value for ft in field_types]

                        filtered_data = self._client.get(
                            f"/companies/{company_id}",
                            params=v2_params,
                        )

                        # Merge: filtered fields + interaction data
                        filtered_data["interaction_dates"] = interaction_data.get(
                            "interaction_dates"
                        )
                        filtered_data["interactions"] = interaction_data.get("interactions")
                        return Company.model_validate(filtered_data)

                    # No field filtering, return interaction data directly
                    return Company.model_validate(interaction_data)

                # Standard path - supports field filtering
                params: dict[str, Any] = {}
                if field_ids:
                    params["fieldIds"] = [str(field_id) for field_id in field_ids]
                if field_types:
                    params["fieldTypes"] = [field_type.value for field_type in field_types]

                data = self._client.get(
                    f"/companies/{company_id}",
                    params=params or None,
                )
                return Company.model_validate(data)
            except NotFoundError as e:
                last_error = e
                if attempt < attempts - 1:  # Don't sleep after last attempt
                    time.sleep(0.5 * (attempt + 1))  # 0.5s, 1s, 1.5s backoff

        # V1 fallback: If V2 returned 404, try V1 API (handles V1→V2 sync delays)
        # Skip if already using V1 path (with_interaction_dates=True)
        if last_error is not None and not with_interaction_dates:
            try:
                v1_data = self._client.get(f"/organizations/{company_id}", v1=True)
                return Company.model_validate(v1_data)
            except NotFoundError:
                pass  # V1 also failed, raise original V2 error

        raise last_error  # type: ignore[misc]

    def get_many(
        self,
        company_ids: Sequence[CompanyId],
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
    ) -> PaginatedResponse[Company]:
        """
        Fetch multiple companies by ID in a single API call.

        This is a convenience alias for ``list(ids=[...])``.

        Args:
            company_ids: Company IDs to fetch
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])

        Returns:
            Paginated response with companies

        Example:
            >>> companies = client.companies.get_many([CompanyId(1), CompanyId(2)])
            >>> for company in companies.data:
            ...     print(company.name)
        """
        return self.list(ids=company_ids, field_ids=field_ids, field_types=field_types)

    def get_associated_person_ids(
        self,
        company_id: CompanyId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[PersonId]:
        """
        Get associated person IDs for a company.

        V1-only exception: V2 does not expose company -> people associations.
        Uses GET `/organizations/{id}` and returns `person_ids` if present.
        """
        data = self._client.get(f"/organizations/{company_id}", v1=True)
        organization = data.get("organization") if isinstance(data, dict) else None
        source = organization if isinstance(organization, dict) else data
        person_ids = None
        if isinstance(source, dict):
            person_ids = source.get("person_ids") or source.get("personIds")

        if not isinstance(person_ids, list):
            return []

        ids = [PersonId(int(value)) for value in person_ids if value is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    def get_associated_person_ids_batch(
        self,
        company_ids: Sequence[CompanyId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[CompanyId, builtins.list[PersonId]]:
        """
        Get person associations for multiple companies.

        Makes one V1 API call per company.

        Args:
            company_ids: Sequence of company IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping company_id -> list of person_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        result: dict[CompanyId, builtins.list[PersonId]] = {}
        for company_id in company_ids:
            try:
                result[company_id] = self.get_associated_person_ids(company_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this company
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for company {company_id}: {e}"
                    ) from e
                # skip: continue without this company
        return result

    def get_associated_people(
        self,
        company_id: CompanyId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[Person]:
        """
        Get Person objects associated with a company.

        Uses V2 batch lookup for efficiency (1 API call per 100 persons
        instead of 1 per person).
        """
        person_ids = self.get_associated_person_ids(company_id, max_results=max_results)
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

    def get_associated_opportunity_ids(
        self,
        company_id: CompanyId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[OpportunityId]:
        """
        Get associated opportunity IDs for a company.

        V1-only: V2 does not expose company -> opportunity associations directly.
        Uses GET `/organizations/{id}` (V1) and returns `opportunity_ids`.

        Args:
            company_id: The company ID
            max_results: Maximum number of opportunity IDs to return

        Returns:
            List of OpportunityId values associated with this company
        """
        data = self._client.get(f"/organizations/{company_id}", v1=True)
        # Defensive: handle potential {"organization": {...}} wrapper
        organization = data.get("organization") if isinstance(data, dict) else None
        source = organization if isinstance(organization, dict) else data
        opp_ids = None
        if isinstance(source, dict):
            opp_ids = source.get("opportunity_ids") or source.get("opportunityIds")

        if not isinstance(opp_ids, list):
            return []

        ids = [OpportunityId(int(oid)) for oid in opp_ids if oid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    def get_associated_opportunity_ids_batch(
        self,
        company_ids: Sequence[CompanyId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[CompanyId, builtins.list[OpportunityId]]:
        """
        Get opportunity associations for multiple companies.

        Makes one V1 API call per company.

        Args:
            company_ids: Sequence of company IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping company_id -> list of opportunity_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        result: dict[CompanyId, builtins.list[OpportunityId]] = {}
        for company_id in company_ids:
            try:
                result[company_id] = self.get_associated_opportunity_ids(company_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this company
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for company {company_id}: {e}"
                    ) from e
                # skip: continue without this company
        return result

    def get_list_entries(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListEntry]:
        """
        Get all list entries for a company across all lists.

        Returns comprehensive field data for each list entry.
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
            params: dict[str, Any] = {}
            if limit:
                params["limit"] = limit
            data = self._client.get(
                f"/companies/{company_id}/list-entries",
                params=params or None,
            )

        return PaginatedResponse[ListEntry](
            data=[ListEntry.model_validate(e) for e in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    def get_lists(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListSummary]:
        """Get all lists that contain this company."""
        if cursor is not None:
            if limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = self._client.get_url(cursor)
        else:
            params: dict[str, Any] = {}
            if limit:
                params["limit"] = limit
            data = self._client.get(
                f"/companies/{company_id}/lists",
                params=params or None,
            )

        return PaginatedResponse[ListSummary](
            data=[ListSummary.model_validate(item) for item in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    def get_fields(
        self,
        *,
        field_types: Sequence[FieldType] | None = None,
    ) -> builtins.list[FieldMetadata]:
        """
        Get metadata about company fields.

        Cached for performance.
        """
        params: dict[str, Any] = {}
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = self._client.get(
            "/companies/fields",
            params=params or None,
            cache_key=(
                "company_fields:_all_"
                if field_types is None
                else f"company_fields:{','.join(field_types)}"
            ),
            cache_ttl=300,
        )

        return [FieldMetadata.model_validate(f) for f in data.get("data", [])]

    # =========================================================================
    # Search (V1 API)
    # =========================================================================

    def search(
        self,
        term: str,
        *,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        with_opportunities: bool = False,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Company]:
        """
        Search for companies by name or domain.

        Uses V1 API for search functionality not available in V2.

        Args:
            term: Search term (name or domain)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Pagination token

        Returns:
            Dict with 'organizations' and 'next_page_token'
        """
        params: dict[str, Any] = {"term": term}
        if with_interaction_dates:
            params["with_interaction_dates"] = True
        if with_interaction_persons:
            params["with_interaction_persons"] = True
        if with_opportunities:
            params["with_opportunities"] = True
        if page_size:
            params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token

        data = self._client.get("/organizations", params=params, v1=True)
        items = [Company.model_validate(o) for o in data.get("organizations", [])]
        return PaginatedResponse[Company](
            data=items,
            next_page_token=data.get("next_page_token"),
        )

    def search_pages(
        self,
        term: str,
        *,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        with_opportunities: bool = False,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> Iterator[PaginatedResponse[Company]]:
        """
        Iterate V1 company-search result pages.

        Useful for scripts that need checkpoint/resume via `next_page_token`.

        Args:
            term: Search term (name or domain)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            PaginatedResponse[Company] for each page
        """
        requested_token = page_token
        page = self.search(
            term,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
            with_opportunities=with_opportunities,
            page_size=page_size,
            page_token=page_token,
        )
        while True:
            yield page
            next_token = page.next_page_token
            if not next_token or next_token == requested_token:
                return
            requested_token = next_token
            page = self.search(
                term,
                with_interaction_dates=with_interaction_dates,
                with_interaction_persons=with_interaction_persons,
                with_opportunities=with_opportunities,
                page_size=page_size,
                page_token=next_token,
            )

    def search_all(
        self,
        term: str,
        *,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        with_opportunities: bool = False,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> Iterator[Company]:
        """
        Iterate all V1 company-search results with automatic pagination.

        Args:
            term: Search term (name or domain)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            Company objects matching the search term
        """
        for page in self.search_pages(
            term,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
            with_opportunities=with_opportunities,
            page_size=page_size,
            page_token=page_token,
        ):
            yield from page.data

    def resolve(
        self,
        *,
        domain: str | None = None,
        name: str | None = None,
    ) -> Company | None:
        """
        Find a single company by domain or name.

        This is a convenience helper that searches and returns the first exact match,
        or None if not found. Uses V1 search internally.

        Args:
            domain: Domain to search for (e.g., "acme.com")
            name: Company name to search for

        Returns:
            The matching Company, or None if not found

        Raises:
            ValueError: If neither domain nor name is provided

        Note:
            If multiple matches are found, returns the first one.
            For disambiguation, use search() directly.
        """
        if not domain and not name:
            raise ValueError("Must provide either domain or name")

        term = domain or name or ""
        result = self.search(term, page_size=10)

        for company in result.data:
            if domain and company.domain and company.domain.lower() == domain.lower():
                return company
            if name and company.name and company.name.lower() == name.lower():
                return company

        return None

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    def create(self, data: CompanyCreate) -> Company:
        """
        Create a new company.

        Args:
            data: Company creation data

        Returns:
            Created company

        Note:
            Creates use V1 API, while reads use V2 API. Due to eventual consistency
            between V1 and V2, a `get()` call immediately after `create()` may return
            404 NotFoundError. If you need to read immediately after creation, either:
            - Use the Company object returned by this method (it contains the created data)
            - Add a short delay (100-500ms) before calling get()
            - Implement retry logic in your application
        """
        payload = data.model_dump(by_alias=True, mode="json", exclude_none=True)
        if not data.person_ids:
            payload.pop("person_ids", None)

        result = self._client.post("/organizations", json=payload, v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("company")

        return Company.model_validate(result)

    def update(
        self,
        company_id: CompanyId,
        data: CompanyUpdate,
    ) -> Company:
        """
        Update an existing company.

        Note: Cannot update name/domain of global companies.
        """
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )

        result = self._client.put(
            f"/organizations/{company_id}",
            json=payload,
            v1=True,
        )

        if self._client.cache:
            self._client.cache.invalidate_prefix("company")

        return Company.model_validate(result)

    def delete(self, company_id: CompanyId) -> bool:
        """
        Delete a company.

        Note: Cannot delete global companies.
        """
        result = self._client.delete(f"/organizations/{company_id}", v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("company")

        return bool(result.get("success", False))

    # =========================================================================
    # Merge Operations (V2 BETA)
    # =========================================================================

    def merge(
        self,
        primary_id: CompanyId,
        duplicate_id: CompanyId,
    ) -> str:
        """
        Merge a duplicate company into a primary company.

        Returns a task URL to check merge status.
        """
        if not self._client.enable_beta_endpoints:
            raise BetaEndpointDisabledError(
                "Company merge is a beta endpoint; set enable_beta_endpoints=True to use it."
            )
        result = self._client.post(
            "/company-merges",
            json={
                "primaryCompanyId": int(primary_id),
                "duplicateCompanyId": int(duplicate_id),
            },
        )
        return str(result.get("taskUrl", ""))

    def get_merge_status(self, task_id: str) -> MergeTask:
        """Check the status of a merge operation."""
        data = self._client.get(f"/tasks/company-merges/{task_id}")
        return MergeTask.model_validate(data)


class AsyncCompanyService:
    """
    Async version of CompanyService.

    Mirrors sync behavior for V2 reads, V1 writes, and V1 search helpers.
    """

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[Company]:
        """
        Get a page of companies.

        Args:
            ids: Specific company IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            filter: V2 filter expression string, or a FilterExpression built via `affinity.F`
                (e.g., `F.field("domain").contains("acme")`)
            limit: Maximum number of results (API default: 100)
            cursor: Cursor to resume pagination (opaque; obtained from prior responses)

        Returns:
            Paginated response with companies
        """
        validate_entity_field_types(field_types, endpoint="company")
        if cursor is not None:
            if any(p is not None for p in (ids, field_ids, field_types, filter, limit)):
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
            if field_ids:
                params["fieldIds"] = [str(field_id) for field_id in field_ids]
            if field_types:
                params["fieldTypes"] = [field_type.value for field_type in field_types]
            if filter is not None:
                filter_text = str(filter).strip()
                if filter_text:
                    params["filter"] = filter_text
            if limit:
                params["limit"] = limit
            data = await self._client.get("/companies", params=params or None)

        return PaginatedResponse[Company](
            data=[Company.model_validate(c) for c in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    async def get_first(
        self,
        *,
        filter: str | FilterExpression | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
    ) -> Company | None:
        """
        Get the first company matching the filter, or None if no match.

        See CompanyService.get_first() for details.
        """
        page = await self.list(
            filter=filter,
            field_ids=field_ids,
            field_types=field_types,
            limit=1,
        )
        return page.data[0] if page.data else None

    async def batch_get(
        self,
        company_ids: Sequence[CompanyId],
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        max_concurrent: int = 10,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[CompanyId, Company]:
        """
        Fetch multiple companies with controlled concurrency.

        Unlike get_many() which uses the API's batch endpoint, this method
        makes individual get() calls with bounded concurrency. Use this when
        you need parameters not supported by the batch endpoint (e.g.,
        with_interaction_dates=True).

        Args:
            company_ids: Company IDs to fetch
            field_ids: Specific field IDs to include
            field_types: Field types to include
            with_interaction_dates: Include interaction date summaries
            with_interaction_persons: Include person IDs for interactions
            max_concurrent: Maximum concurrent API calls (default: 10)
            on_error: How to handle AffinityError exceptions:
                - "raise": Raise on first AffinityError (default)
                - "skip": Skip failed IDs, return partial results

        Returns:
            Dict mapping company_id -> Company for successfully fetched companies.

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        if not company_ids:
            return {}
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        unique_ids = list(dict.fromkeys(company_ids))
        results: dict[CompanyId, Company] = {}

        async def fetch_one(cid: CompanyId) -> tuple[CompanyId, Company | None]:
            try:
                company = await self.get(
                    cid,
                    field_ids=field_ids,
                    field_types=field_types,
                    with_interaction_dates=with_interaction_dates,
                    with_interaction_persons=with_interaction_persons,
                )
                return (cid, company)
            except AffinityError:
                if on_error == "raise":
                    raise
                return (cid, None)

        for i in range(0, len(unique_ids), max_concurrent):
            chunk = unique_ids[i : i + max_concurrent]
            tasks = [asyncio.create_task(fetch_one(cid)) for cid in chunk]
            try:
                for coro in asyncio.as_completed(tasks):
                    cid, company = await coro
                    if company is not None:
                        results[cid] = company
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
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[Company]]:
        """
        Iterate company pages (not items), yielding `PaginatedResponse[Company]`.

        Useful for ETL scripts that need checkpoint/resume via `page.next_cursor`.

        Args:
            ids: Specific company IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            filter: V2 filter expression string or FilterExpression
            limit: Maximum results per page
            cursor: Cursor to resume pagination

        Yields:
            PaginatedResponse[Company] for each page
        """
        other_params = (ids, field_ids, field_types, filter, limit)
        if cursor is not None and any(p is not None for p in other_params):
            raise ValueError(
                "Cannot combine 'cursor' with other parameters; cursor encodes all query context. "
                "Start a new pagination sequence without a cursor to change parameters."
            )
        requested_cursor = cursor
        if cursor is not None:
            page = await self.list(cursor=cursor)
        else:
            page = await self.list(
                ids=ids,
                field_ids=field_ids,
                field_types=field_types,
                filter=filter,
                limit=limit,
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
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
    ) -> AsyncIterator[Company]:
        """
        Iterate through all companies with automatic pagination.

        Args:
            ids: Specific company IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include
            field_types: Field types to include
            filter: V2 filter expression

        Yields:
            Company objects
        """

        async def fetch_page(next_url: str | None) -> PaginatedResponse[Company]:
            if next_url:
                data = await self._client.get_url(next_url)
                return PaginatedResponse[Company](
                    data=[Company.model_validate(c) for c in data.get("data", [])],
                    pagination=PaginationInfo.model_validate(data.get("pagination", {})),
                )
            return await self.list(
                ids=ids, field_ids=field_ids, field_types=field_types, filter=filter
            )

        return AsyncPageIterator(fetch_page)

    def iter(
        self,
        *,
        ids: Sequence[CompanyId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        filter: str | FilterExpression | None = None,
    ) -> AsyncIterator[Company]:
        """
        Auto-paginate all companies.

        Alias for `all()` (FR-006 public contract).
        """
        return self.all(ids=ids, field_ids=field_ids, field_types=field_types, filter=filter)

    async def get(
        self,
        company_id: CompanyId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        retries: int = 0,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Company:
        """
        Get a single company by ID.

        Args:
            company_id: The company ID
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            retries: Number of retries on 404 NotFoundError. Default is 0 (fail fast).
                Set to 2-3 if calling immediately after create() to handle eventual
                consistency lag.
            with_interaction_dates: Include interaction date summaries (last/next
                meeting dates, email dates).
            with_interaction_persons: Include person IDs for each interaction.
                Only applies when with_interaction_dates=True.

        Returns:
            Company object with requested field data. When with_interaction_dates=True,
            the Company will have interaction_dates and interactions populated.

        Raises:
            NotFoundError: If company does not exist after all retries.

        Note:
            When combining with_interaction_dates with field_ids/field_types,
            two API calls are made internally and the results are merged.
        """
        last_error: NotFoundError | None = None
        attempts = retries + 1  # retries=0 means 1 attempt
        has_field_filters = field_ids is not None or field_types is not None

        for attempt in range(attempts):
            try:
                if with_interaction_dates:
                    # Fetch interaction data
                    v1_params: dict[str, Any] = {"with_interaction_dates": True}
                    if with_interaction_persons:
                        v1_params["with_interaction_persons"] = True
                    interaction_data = await self._client.get(
                        f"/organizations/{company_id}",
                        params=v1_params,
                        v1=True,
                    )

                    # If field filtering is also requested, fetch filtered fields and merge
                    if has_field_filters:
                        v2_params: dict[str, Any] = {}
                        if field_ids:
                            v2_params["fieldIds"] = [str(fid) for fid in field_ids]
                        if field_types:
                            v2_params["fieldTypes"] = [ft.value for ft in field_types]

                        filtered_data = await self._client.get(
                            f"/companies/{company_id}",
                            params=v2_params,
                        )

                        # Merge: filtered fields + interaction data
                        filtered_data["interaction_dates"] = interaction_data.get(
                            "interaction_dates"
                        )
                        filtered_data["interactions"] = interaction_data.get("interactions")
                        return Company.model_validate(filtered_data)

                    # No field filtering, return interaction data directly
                    return Company.model_validate(interaction_data)

                # Standard path - supports field filtering
                params: dict[str, Any] = {}
                if field_ids:
                    params["fieldIds"] = [str(field_id) for field_id in field_ids]
                if field_types:
                    params["fieldTypes"] = [field_type.value for field_type in field_types]

                data = await self._client.get(f"/companies/{company_id}", params=params or None)
                return Company.model_validate(data)
            except NotFoundError as e:
                last_error = e
                if attempt < attempts - 1:  # Don't sleep after last attempt
                    await asyncio.sleep(0.5 * (attempt + 1))  # 0.5s, 1s, 1.5s backoff

        # V1 fallback: If V2 returned 404, try V1 API (handles V1→V2 sync delays)
        # Skip if already using V1 path (with_interaction_dates=True)
        if last_error is not None and not with_interaction_dates:
            try:
                v1_data = await self._client.get(f"/organizations/{company_id}", v1=True)
                return Company.model_validate(v1_data)
            except NotFoundError:
                pass  # V1 also failed, raise original V2 error

        raise last_error  # type: ignore[misc]

    async def get_many(
        self,
        company_ids: Sequence[CompanyId],
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
    ) -> PaginatedResponse[Company]:
        """
        Fetch multiple companies by ID in a single API call.

        This is a convenience alias for ``list(ids=[...])``.

        Args:
            company_ids: Company IDs to fetch
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])

        Returns:
            Paginated response with companies

        Example:
            >>> companies = await client.companies.get_many([CompanyId(1), CompanyId(2)])
            >>> for company in companies.data:
            ...     print(company.name)
        """
        return await self.list(ids=company_ids, field_ids=field_ids, field_types=field_types)

    async def get_list_entries(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListEntry]:
        """
        Get all list entries for a company across all lists.

        Returns comprehensive field data for each list entry.
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
            params: dict[str, Any] = {}
            if limit:
                params["limit"] = limit
            data = await self._client.get(
                f"/companies/{company_id}/list-entries",
                params=params or None,
            )

        return PaginatedResponse[ListEntry](
            data=[ListEntry.model_validate(e) for e in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    async def get_lists(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListSummary]:
        """Get all lists that contain this company."""
        if cursor is not None:
            if limit is not None:
                raise ValueError(
                    "Cannot combine 'cursor' with other parameters; cursor encodes all query "
                    "context. Start a new pagination sequence without a cursor to change "
                    "parameters."
                )
            data = await self._client.get_url(cursor)
        else:
            params: dict[str, Any] = {}
            if limit:
                params["limit"] = limit
            data = await self._client.get(
                f"/companies/{company_id}/lists",
                params=params or None,
            )

        return PaginatedResponse[ListSummary](
            data=[ListSummary.model_validate(item) for item in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    async def get_fields(
        self,
        *,
        field_types: Sequence[FieldType] | None = None,
    ) -> builtins.list[FieldMetadata]:
        """
        Get metadata about company fields.

        Cached for performance.
        """
        params: dict[str, Any] = {}
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = await self._client.get(
            "/companies/fields",
            params=params or None,
            cache_key=(
                "company_fields:_all_"
                if field_types is None
                else f"company_fields:{','.join(field_types)}"
            ),
            cache_ttl=300,
        )

        return [FieldMetadata.model_validate(f) for f in data.get("data", [])]

    # =========================================================================
    # Search (V1 API)
    # =========================================================================

    async def search(
        self,
        term: str,
        *,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        with_opportunities: bool = False,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> PaginatedResponse[Company]:
        """
        Search for companies by name or domain.

        Uses V1 API for search functionality not available in V2.
        """
        params: dict[str, Any] = {"term": term}
        if with_interaction_dates:
            params["with_interaction_dates"] = True
        if with_interaction_persons:
            params["with_interaction_persons"] = True
        if with_opportunities:
            params["with_opportunities"] = True
        if page_size:
            params["page_size"] = page_size
        if page_token:
            params["page_token"] = page_token

        data = await self._client.get("/organizations", params=params, v1=True)
        items = [Company.model_validate(o) for o in data.get("organizations", [])]
        return PaginatedResponse[Company](
            data=items,
            next_page_token=data.get("next_page_token"),
        )

    async def search_pages(
        self,
        term: str,
        *,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        with_opportunities: bool = False,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[Company]]:
        """
        Iterate V1 company-search result pages.

        Useful for scripts that need checkpoint/resume via `next_page_token`.

        Args:
            term: Search term (name or domain)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            PaginatedResponse[Company] for each page
        """
        requested_token = page_token
        page = await self.search(
            term,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
            with_opportunities=with_opportunities,
            page_size=page_size,
            page_token=page_token,
        )
        while True:
            yield page
            next_token = page.next_page_token
            if not next_token or next_token == requested_token:
                return
            requested_token = next_token
            page = await self.search(
                term,
                with_interaction_dates=with_interaction_dates,
                with_interaction_persons=with_interaction_persons,
                with_opportunities=with_opportunities,
                page_size=page_size,
                page_token=next_token,
            )

    async def search_all(
        self,
        term: str,
        *,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        with_opportunities: bool = False,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AsyncIterator[Company]:
        """
        Iterate all V1 company-search results with automatic pagination.

        Args:
            term: Search term (name or domain)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            Company objects matching the search term
        """
        async for page in self.search_pages(
            term,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
            with_opportunities=with_opportunities,
            page_size=page_size,
            page_token=page_token,
        ):
            for company in page.data:
                yield company

    async def resolve(
        self,
        *,
        domain: str | None = None,
        name: str | None = None,
    ) -> Company | None:
        """
        Find a single company by domain or name.

        This is a convenience helper that searches and returns the first exact match,
        or None if not found. Uses V1 search internally.
        """
        if not domain and not name:
            raise ValueError("Must provide either domain or name")

        term = domain or name or ""
        result = await self.search(term, page_size=10)

        for company in result.data:
            if domain and company.domain and company.domain.lower() == domain.lower():
                return company
            if name and company.name and company.name.lower() == name.lower():
                return company

        return None

    async def get_associated_person_ids(
        self,
        company_id: CompanyId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[PersonId]:
        """
        Get associated person IDs for a company.

        V1-only exception: V2 does not expose company -> people associations.
        Uses GET `/organizations/{id}` and returns `person_ids` if present.
        """
        data = await self._client.get(f"/organizations/{company_id}", v1=True)
        organization = data.get("organization") if isinstance(data, dict) else None
        source = organization if isinstance(organization, dict) else data
        person_ids = None
        if isinstance(source, dict):
            person_ids = source.get("person_ids") or source.get("personIds")

        if not isinstance(person_ids, list):
            return []

        ids = [PersonId(int(value)) for value in person_ids if value is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    async def get_associated_person_ids_batch(
        self,
        company_ids: Sequence[CompanyId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[CompanyId, builtins.list[PersonId]]:
        """
        Get person associations for multiple companies.

        Makes one V1 API call per company.

        Args:
            company_ids: Sequence of company IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping company_id -> list of person_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        result: dict[CompanyId, builtins.list[PersonId]] = {}
        for company_id in company_ids:
            try:
                result[company_id] = await self.get_associated_person_ids(company_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this company
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for company {company_id}: {e}"
                    ) from e
                # skip: continue without this company
        return result

    async def get_associated_people(
        self,
        company_id: CompanyId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[Person]:
        """
        Get Person objects associated with a company.

        Uses V2 batch lookup for efficiency (1 API call per 100 persons
        instead of 1 per person).
        """
        person_ids = await self.get_associated_person_ids(company_id, max_results=max_results)
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

    async def get_associated_opportunity_ids(
        self,
        company_id: CompanyId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[OpportunityId]:
        """
        Get associated opportunity IDs for a company.

        V1-only: V2 does not expose company -> opportunity associations directly.
        Uses GET `/organizations/{id}` (V1) and returns `opportunity_ids`.

        Args:
            company_id: The company ID
            max_results: Maximum number of opportunity IDs to return

        Returns:
            List of OpportunityId values associated with this company
        """
        data = await self._client.get(f"/organizations/{company_id}", v1=True)
        # Defensive: handle potential {"organization": {...}} wrapper
        organization = data.get("organization") if isinstance(data, dict) else None
        source = organization if isinstance(organization, dict) else data
        opp_ids = None
        if isinstance(source, dict):
            opp_ids = source.get("opportunity_ids") or source.get("opportunityIds")

        if not isinstance(opp_ids, list):
            return []

        ids = [OpportunityId(int(oid)) for oid in opp_ids if oid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    async def get_associated_opportunity_ids_batch(
        self,
        company_ids: Sequence[CompanyId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[CompanyId, builtins.list[OpportunityId]]:
        """
        Get opportunity associations for multiple companies.

        Makes one V1 API call per company.

        Args:
            company_ids: Sequence of company IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping company_id -> list of opportunity_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        result: dict[CompanyId, builtins.list[OpportunityId]] = {}
        for company_id in company_ids:
            try:
                result[company_id] = await self.get_associated_opportunity_ids(company_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this company
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for company {company_id}: {e}"
                    ) from e
                # skip: continue without this company
        return result

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    async def create(self, data: CompanyCreate) -> Company:
        """
        Create a new company.

        Uses V1 API.

        Note:
            Creates use V1 API, while reads use V2 API. Due to eventual consistency
            between V1 and V2, a `get()` call immediately after `create()` may return
            404 NotFoundError. If you need to read immediately after creation, either:
            - Use the Company object returned by this method (it contains the created data)
            - Add a short delay (100-500ms) before calling get()
            - Implement retry logic in your application
        """
        payload = data.model_dump(by_alias=True, mode="json", exclude_none=True)
        if not data.person_ids:
            payload.pop("person_ids", None)
        result = await self._client.post("/organizations", json=payload, v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("company")

        return Company.model_validate(result)

    async def update(self, company_id: CompanyId, data: CompanyUpdate) -> Company:
        """
        Update an existing company.

        Uses V1 API.
        """
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )
        result = await self._client.put(
            f"/organizations/{company_id}",
            json=payload,
            v1=True,
        )

        if self._client.cache:
            self._client.cache.invalidate_prefix("company")

        return Company.model_validate(result)

    async def delete(self, company_id: CompanyId) -> bool:
        """
        Delete a company.

        Uses V1 API.
        """
        result = await self._client.delete(f"/organizations/{company_id}", v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("company")

        return bool(result.get("success", False))

    # =========================================================================
    # Merge Operations (V2 BETA)
    # =========================================================================

    async def merge(
        self,
        primary_id: CompanyId,
        duplicate_id: CompanyId,
    ) -> str:
        """
        Merge a duplicate company into a primary company.

        Returns a task URL to check merge status.
        """
        if not self._client.enable_beta_endpoints:
            raise BetaEndpointDisabledError(
                "Company merge is a beta endpoint; set enable_beta_endpoints=True to use it."
            )
        result = await self._client.post(
            "/company-merges",
            json={
                "primaryCompanyId": int(primary_id),
                "duplicateCompanyId": int(duplicate_id),
            },
        )
        return str(result.get("taskUrl", ""))

    async def get_merge_status(self, task_id: str) -> MergeTask:
        """Check the status of a merge operation."""
        data = await self._client.get(f"/tasks/company-merges/{task_id}")
        return MergeTask.model_validate(data)
