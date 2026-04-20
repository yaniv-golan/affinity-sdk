"""
Person service.

Provides operations for managing persons (contacts) in Affinity.
Uses V2 API for reading, V1 API for writing.
"""

from __future__ import annotations

import asyncio
import builtins
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal

from ..exceptions import (
    AffinityError,
    BetaEndpointDisabledError,
    DuplicateEntityError,
    NotFoundError,
)
from ..models.entities import (
    FieldMetadata,
    FieldValue,
    ListEntry,
    ListSummary,
    Person,
    PersonCreate,
    PersonUpdate,
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


def _person_matches(person: Person, *, email: str | None, name: str | None) -> bool:
    if email:
        email_lower = email.lower()
        if person.primary_email and person.primary_email.lower() == email_lower:
            return True
        if person.emails:
            for addr in person.emails:
                if addr.lower() == email_lower:
                    return True
    if name:
        name_lower = name.lower()
        full_name = f"{person.first_name or ''} {person.last_name or ''}".strip()
        if full_name.lower() == name_lower:
            return True
    return False


# V1 → V2 key mapping for person responses
_V1_TO_V2_KEYS = {
    "first_name": "firstName",
    "last_name": "lastName",
    "primary_email_address": "primaryEmailAddress",
    "email_addresses": "emailAddresses",
    "organization_ids": "organizationIds",
    "opportunity_ids": "opportunityIds",
    "current_organization_ids": "currentOrganizationIds",
    "list_entry_id": "listEntryId",
    "interaction_dates": "interactionDates",
    "created_at": "createdAt",
    "updated_at": "updatedAt",
}


def _normalize_v1_person_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize V1 person response to match V2 schema.

    V1 uses snake_case (first_name), V2 uses camelCase (firstName).
    The Person model uses aliases, so we need consistent key names.

    Implementation notes:
    - Maps snake_case keys to camelCase as needed
    - Strips unknown keys to avoid Pydantic strict mode errors
    - Handles nested field_values entries appropriately
    - V1 may include field values for deleted fields; these are preserved
    """
    result: dict[str, Any] = {}

    for key, value in data.items():
        # Skip field_values - handled separately
        if key == "field_values":
            continue

        # Map known V1 keys to V2
        if key in _V1_TO_V2_KEYS:
            result[_V1_TO_V2_KEYS[key]] = value
        else:
            # Keep as-is (id, type, emails, etc. are same in both)
            result[key] = value

    return result


class PersonService:
    """
    Service for managing persons (contacts).

    Uses V2 API for efficient reading with field selection,
    V1 API for create/update/delete operations.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    # =========================================================================
    # Read Operations (V2 API)
    # =========================================================================

    def list(
        self,
        *,
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **_unsupported: Any,
    ) -> PaginatedResponse[Person]:
        """
        Get a page of persons.

        Args:
            ids: Specific person IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include
            limit: Maximum number of results
            cursor: Cursor to resume pagination (opaque; obtained from prior responses)

        Returns:
            Paginated response with persons

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        validate_entity_field_types(field_types, endpoint="person")
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
            data = self._client.get("/persons", params=params or None)

        return PaginatedResponse[Person](
            data=[Person.model_validate(p) for p in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    def get_first(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> Person | None:
        """
        Get the first person, or None if no results.

        This is a convenience method equivalent to:
            page = client.persons.list(limit=1)
            return page.data[0] if page.data else None

        Args:
            field_ids: Specific field IDs to include
            field_types: Field types to include

        Returns:
            First Person, or None if no results.

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
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
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **_unsupported: Any,
    ) -> Iterator[PaginatedResponse[Person]]:
        """
        Iterate person pages (not items), yielding `PaginatedResponse[Person]`.

        Useful for ETL scripts that need checkpoint/resume via `page.next_cursor`.

        Args:
            ids: Specific person IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include
            limit: Maximum results per page
            cursor: Cursor to resume pagination

        Yields:
            PaginatedResponse[Person] for each page

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
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
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> Iterator[Person]:
        """
        Iterate through all persons with automatic pagination.

        Args:
            ids: Specific person IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include
            field_types: Field types to include

        Yields:
            Person objects

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")

        def fetch_page(next_url: str | None) -> PaginatedResponse[Person]:
            if next_url:
                data = self._client.get_url(next_url)
                return PaginatedResponse[Person](
                    data=[Person.model_validate(p) for p in data.get("data", [])],
                    pagination=PaginationInfo.model_validate(data.get("pagination", {})),
                )
            return self.list(
                ids=ids,
                field_ids=field_ids,
                field_types=field_types,
            )

        return PageIterator(fetch_page)

    def iter(
        self,
        *,
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> Iterator[Person]:
        """
        Auto-paginate all persons.

        Alias for `all()` (FR-006 public contract).

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        return self.all(ids=ids, field_ids=field_ids, field_types=field_types)

    def get(
        self,
        person_id: PersonId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        include_field_values: bool = False,
        retries: int = 0,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Person:
        """
        Get a single person by ID.

        Args:
            person_id: The person ID
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            include_field_values: If True, fetch embedded field values. This saves
                one API call when you need both person info and field values.
                Cannot be combined with field_ids/field_types.
            retries: Number of retries on 404 NotFoundError. Default is 0 (fail fast).
                Set to 2-3 if calling immediately after create() to handle eventual
                consistency lag.
            with_interaction_dates: Include interaction date summaries (last/next
                meeting dates, email dates).
            with_interaction_persons: Include person IDs for each interaction.
                Only applies when with_interaction_dates=True.

        Returns:
            Person object with requested field data.
            When include_field_values=True, the Person will have a `field_values`
            attribute containing the list of FieldValue objects.
            When with_interaction_dates=True, the Person will have interaction_dates
            and interactions populated.

        Raises:
            NotFoundError: If person does not exist after all retries.
            ValueError: If include_field_values is combined with field_ids/field_types.

        Note:
            When combining with_interaction_dates with field_ids/field_types,
            two API calls are made internally and the results are merged.
        """
        return self._get_with_retry(
            person_id,
            field_ids=field_ids,
            field_types=field_types,
            include_field_values=include_field_values,
            retries=retries,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
        )

    def _get_with_retry(
        self,
        person_id: PersonId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        include_field_values: bool = False,
        retries: int = 0,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Person:
        """Internal: get with retry logic."""
        last_error: NotFoundError | None = None
        attempts = retries + 1  # retries=0 means 1 attempt

        for attempt in range(attempts):
            try:
                return self._get_impl(
                    person_id,
                    field_ids=field_ids,
                    field_types=field_types,
                    include_field_values=include_field_values,
                    with_interaction_dates=with_interaction_dates,
                    with_interaction_persons=with_interaction_persons,
                )
            except NotFoundError as e:
                last_error = e
                if attempt < attempts - 1:  # Don't sleep after last attempt
                    time.sleep(0.5 * (attempt + 1))  # 0.5s, 1s, 1.5s backoff

        # V1 fallback: If V2 returned 404, try V1 API (handles V1→V2 sync delays)
        # Skip if already using V1 path (include_field_values or with_interaction_dates)
        if last_error is not None and not include_field_values and not with_interaction_dates:
            try:
                v1_data = self._client.get(f"/persons/{person_id}", v1=True)
                normalized = _normalize_v1_person_response(v1_data)
                return Person.model_validate(normalized)
            except NotFoundError:
                pass  # V1 also failed, raise original V2 error

        raise last_error  # type: ignore[misc]

    def _get_impl(
        self,
        person_id: PersonId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        include_field_values: bool = False,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Person:
        """Internal: actual get implementation."""
        has_field_filters = field_ids is not None or field_types is not None

        # include_field_values returns embedded field values, which is incompatible
        # with field_ids/field_types filtering (different data structures)
        if include_field_values and has_field_filters:
            raise ValueError(
                "Cannot combine 'include_field_values' with 'field_ids' or 'field_types'. "
                "Use include_field_values alone for embedded field values, or use "
                "field_ids/field_types alone for filtered fields."
            )

        # Path 1: include_field_values (V1 API, may include interaction dates)
        if include_field_values:
            v1_params: dict[str, Any] = {}
            if with_interaction_dates:
                v1_params["with_interaction_dates"] = True
            if with_interaction_persons:
                v1_params["with_interaction_persons"] = True

            data = self._client.get(
                f"/persons/{person_id}",
                params=v1_params or None,
                v1=True,
            )

            field_values_data = data.get("field_values", [])
            # V1 embedded field_values don't include entityId; add it before validation
            field_values = [
                FieldValue.model_validate({**fv, "entityId": int(person_id)})
                for fv in field_values_data
            ]
            normalized = _normalize_v1_person_response(data)
            person = Person.model_validate(normalized)
            object.__setattr__(person, "field_values", field_values)
            return person

        # Path 2: with_interaction_dates (may need merge if field filters present)
        if with_interaction_dates:
            v1_params = {"with_interaction_dates": True}
            if with_interaction_persons:
                v1_params["with_interaction_persons"] = True

            interaction_data = self._client.get(
                f"/persons/{person_id}",
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
                    f"/persons/{person_id}",
                    params=v2_params,
                )

                # Merge: filtered fields + interaction data
                filtered_data["interaction_dates"] = interaction_data.get("interaction_dates")
                filtered_data["interactions"] = interaction_data.get("interactions")
                return Person.model_validate(filtered_data)

            # No field filtering, normalize and return V1 data
            normalized = _normalize_v1_person_response(interaction_data)
            return Person.model_validate(normalized)

        # Path 3: Standard path (supports field filtering)
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = self._client.get(
            f"/persons/{person_id}",
            params=params or None,
        )
        return Person.model_validate(data)

    def get_list_entries(
        self,
        person_id: PersonId,
    ) -> PaginatedResponse[ListEntry]:
        """Get all list entries for a person across all lists."""
        data = self._client.get(f"/persons/{person_id}/list-entries")

        return PaginatedResponse[ListEntry](
            data=[ListEntry.model_validate(e) for e in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    def get_lists(
        self,
        person_id: PersonId,
    ) -> PaginatedResponse[ListSummary]:
        """Get all lists that contain this person."""
        data = self._client.get(f"/persons/{person_id}/lists")

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
        Get metadata about person fields.

        Cached for performance.
        """
        params: dict[str, Any] = {}
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = self._client.get(
            "/persons/fields",
            params=params or None,
            cache_key=f"person_fields:{','.join(field_types or [])}",
            cache_ttl=300,
        )

        return [FieldMetadata.model_validate(f) for f in data.get("data", [])]

    # =========================================================================
    # Associations (V1 API)
    # =========================================================================

    def get_associated_company_ids(
        self,
        person_id: PersonId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[CompanyId]:
        """
        Get associated company IDs for a person.

        V1-only: V2 does not expose person -> company associations directly.
        Uses GET `/persons/{id}` (V1) and returns `organization_ids`.

        Args:
            person_id: The person ID
            max_results: Maximum number of company IDs to return

        Returns:
            List of CompanyId values associated with this person

        Note:
            The Person model already has `company_ids` populated from V1's
            `organizationIds` field. This method provides API parity with
            `CompanyService.get_associated_person_ids()`.
        """
        data = self._client.get(f"/persons/{person_id}", v1=True)
        # Defensive: handle potential {"person": {...}} wrapper
        # (consistent with CompanyService.get_associated_person_ids pattern)
        person = data.get("person") if isinstance(data, dict) else None
        source = person if isinstance(person, dict) else data
        org_ids = None
        if isinstance(source, dict):
            org_ids = source.get("organization_ids") or source.get("organizationIds")

        if not isinstance(org_ids, list):
            return []

        ids = [CompanyId(int(cid)) for cid in org_ids if cid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    def get_associated_company_ids_batch(
        self,
        person_ids: Sequence[PersonId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[PersonId, builtins.list[CompanyId]]:
        """
        Get company associations for multiple persons.

        Makes one V1 API call per person.

        Args:
            person_ids: Sequence of person IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping person_id -> list of company_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.

        Example:
            associations = client.persons.get_associated_company_ids_batch(person_ids)
            all_company_ids = set()
            for company_ids in associations.values():
                all_company_ids.update(company_ids)
        """
        result: dict[PersonId, builtins.list[CompanyId]] = {}
        for person_id in person_ids:
            try:
                result[person_id] = self.get_associated_company_ids(person_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this person
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for person {person_id}: {e}"
                    ) from e
                # skip: continue without this person
        return result

    def get_associated_opportunity_ids(
        self,
        person_id: PersonId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[OpportunityId]:
        """
        Get associated opportunity IDs for a person.

        V1-only: V2 does not expose person -> opportunity associations directly.
        Uses GET `/persons/{id}` (V1) and returns `opportunity_ids`.

        Args:
            person_id: The person ID
            max_results: Maximum number of opportunity IDs to return

        Returns:
            List of OpportunityId values associated with this person

        Note:
            The Person model already has `opportunity_ids` populated from V1's
            `opportunityIds` field. This method provides API parity with
            `OpportunityService.get_associated_person_ids()`.
        """
        data = self._client.get(f"/persons/{person_id}", v1=True)
        # Defensive: handle potential {"person": {...}} wrapper
        person = data.get("person") if isinstance(data, dict) else None
        source = person if isinstance(person, dict) else data
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
        person_ids: Sequence[PersonId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[PersonId, builtins.list[OpportunityId]]:
        """
        Get opportunity associations for multiple persons.

        Makes one V1 API call per person.

        Args:
            person_ids: Sequence of person IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping person_id -> list of opportunity_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        result: dict[PersonId, builtins.list[OpportunityId]] = {}
        for person_id in person_ids:
            try:
                result[person_id] = self.get_associated_opportunity_ids(person_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this person
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for person {person_id}: {e}"
                    ) from e
                # skip: continue without this person
        return result

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
    ) -> PaginatedResponse[Person]:
        """
        Search for persons by name or email.

        Uses V1 API for search functionality.

        Args:
            term: Search term (name or email)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Pagination token

        Returns:
            PaginatedResponse[Person] with matching persons and pagination info
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

        data = self._client.get("/persons", params=params, v1=True)
        items = [Person.model_validate(p) for p in data.get("persons", [])]
        return PaginatedResponse[Person](
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
    ) -> Iterator[PaginatedResponse[Person]]:
        """
        Iterate V1 person-search result pages.

        Useful for scripts that need checkpoint/resume via `next_page_token`.

        Args:
            term: Search term (name or email)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            PaginatedResponse[Person] for each page
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
    ) -> Iterator[Person]:
        """
        Iterate all V1 person-search results with automatic pagination.

        Args:
            term: Search term (name or email)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            Person objects matching the search term
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
        email: str | None = None,
        name: str | None = None,
    ) -> Person | None:
        """
        Find a single person by email or name.

        This is a convenience helper that searches and returns the first exact match,
        or None if not found. Uses V1 search internally.

        Args:
            email: Email address to search for
            name: Person name to search for (first + last)

        Returns:
            The matching Person, or None if not found

        Raises:
            ValueError: If neither email nor name is provided

        Note:
            This auto-paginates V1 search results until a match is found.
            If multiple matches are found, returns the first one. For full
            disambiguation, use resolve_all() or search() directly.
        """
        if not email and not name:
            raise ValueError("Must provide either email or name")

        term = email or name or ""
        for page in self.search_pages(term, page_size=10):
            for person in page.data:
                if _person_matches(person, email=email, name=name):
                    return person

        return None

    def resolve_all(
        self,
        *,
        email: str | None = None,
        name: str | None = None,
    ) -> builtins.list[Person]:
        """
        Find all persons matching an email or name.

        Notes:
        - This auto-paginates V1 search results to collect exact matches.
        - Unlike resolve(), this returns every match in server-provided order.
        """
        if not email and not name:
            raise ValueError("Must provide either email or name")

        term = email or name or ""
        matches: builtins.list[Person] = []
        for page in self.search_pages(term, page_size=10):
            for person in page.data:
                if _person_matches(person, email=email, name=name):
                    matches.append(person)
        return matches

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    _DEDUP_PAGE_CAP = 3  # 3 pages x 500 = 1500 fuzzy results scanned per search term

    def create(self, data: PersonCreate, *, if_not_exists: bool = True) -> Person:
        """
        Create a new person.

        Args:
            data: Person creation data
            if_not_exists: If True (default), check for an existing person with
                the same primary email (case-insensitive exact) — falling back
                to exact first+last name match when no email is provided.
                Uses V1 search_pages(term) with page_size=500, capped at
                _DEDUP_PAGE_CAP pages. Set to False to skip the check and
                create unconditionally.

        Returns:
            Created person

        Raises:
            DuplicateEntityError: When if_not_exists=True and an exact-email
                OR exact full-name match already exists.

        Note:
            Creates use V1 API, while reads use V2 API. Due to eventual consistency
            between V1 and V2, a `get()` call immediately after `create()` may return
            404 NotFoundError. If you need to read immediately after creation, either:
            - Use the Person object returned by this method (it contains the created data)
            - Add a short delay (100-500ms) before calling get()
            - Implement retry logic in your application
        """
        if if_not_exists:
            existing = self._find_exact_duplicate(
                first_name=data.first_name,
                last_name=data.last_name,
                emails=list(data.emails or []),
            )
            if existing is not None:
                raise DuplicateEntityError(
                    f"Person with email or name already exists (id={existing.id})",
                    entity_type="person",
                    existing_id=int(existing.id),
                    existing_name=f"{existing.first_name or ''} {existing.last_name or ''}".strip(),
                )

        payload = data.model_dump(by_alias=True, mode="json")
        if not data.company_ids:
            payload.pop("organization_ids", None)

        result = self._client.post("/persons", json=payload, v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("person")

        return Person.model_validate(result)

    def _find_exact_duplicate(
        self,
        *,
        first_name: str | None,
        last_name: str | None,
        emails: builtins.list[str],
    ) -> Person | None:
        """Search V1 for an exact-email or exact full-name match. Returns None if no match.

        Email takes priority — exact case-insensitive match against any entry in the
        person's emails list or primary_email. Falls back to exact first+last name match
        when no emails are provided in the create payload.

        Iterates at most `_DEDUP_PAGE_CAP` pages per search term (1500 fuzzy results).
        """
        emails_lower = {e.strip().lower() for e in emails if e}
        fn_lower = (first_name or "").strip().lower()
        ln_lower = (last_name or "").strip().lower()

        search_terms = list(emails) if emails else [f"{first_name or ''} {last_name or ''}".strip()]
        seen_ids: set[int] = set()

        for term in search_terms:
            if not term:
                continue
            for page_num, page in enumerate(self.search_pages(term, page_size=500)):
                for person in page.data:
                    pid = int(person.id)
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)

                    person_emails = {(e or "").strip().lower() for e in (person.emails or [])}
                    if person.primary_email:
                        person_emails.add(person.primary_email.strip().lower())

                    if emails_lower and emails_lower & person_emails:
                        return person

                    if (
                        not emails_lower
                        and fn_lower
                        and ln_lower
                        and (person.first_name or "").lower() == fn_lower
                        and (person.last_name or "").lower() == ln_lower
                    ):
                        return person

                if page_num + 1 >= self._DEDUP_PAGE_CAP:
                    break
                if not page.next_page_token:
                    break

        return None

    def update(
        self,
        person_id: PersonId,
        data: PersonUpdate,
    ) -> Person:
        """
        Update an existing person.

        Note: To add emails/organizations, include existing values plus new ones.
        """
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )

        result = self._client.put(
            f"/persons/{person_id}",
            json=payload,
            v1=True,
        )

        if self._client.cache:
            self._client.cache.invalidate_prefix("person")

        return Person.model_validate(result)

    def delete(self, person_id: PersonId) -> bool:
        """Delete a person (also deletes associated field values)."""
        result = self._client.delete(f"/persons/{person_id}", v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("person")

        return bool(result.get("success", False))

    # =========================================================================
    # Merge Operations (V2 BETA)
    # =========================================================================

    def merge(
        self,
        primary_id: PersonId,
        duplicate_id: PersonId,
    ) -> str:
        """
        Merge a duplicate person into a primary person.

        Returns a task URL to check merge status.
        """
        if not self._client.enable_beta_endpoints:
            raise BetaEndpointDisabledError(
                "Person merge is a beta endpoint; set enable_beta_endpoints=True to use it."
            )
        result = self._client.post(
            "/person-merges",
            json={
                "primaryPersonId": int(primary_id),
                "duplicatePersonId": int(duplicate_id),
            },
        )
        return str(result.get("taskUrl", ""))

    def get_merge_status(self, task_id: str) -> MergeTask:
        """Check the status of a merge operation."""
        data = self._client.get(f"/tasks/person-merges/{task_id}")
        return MergeTask.model_validate(data)


class AsyncPersonService:
    """Async version of PersonService."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **_unsupported: Any,
    ) -> PaginatedResponse[Person]:
        """
        Get a page of persons.

        Args:
            ids: Specific person IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include
            limit: Maximum number of results
            cursor: Cursor to resume pagination (opaque; obtained from prior responses)

        Returns:
            Paginated response with persons

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        validate_entity_field_types(field_types, endpoint="person")
        if cursor is not None:
            if any(p is not None for p in (ids, field_ids, field_types, limit)):
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
            if limit:
                params["limit"] = limit
            data = await self._client.get("/persons", params=params or None)

        return PaginatedResponse[Person](
            data=[Person.model_validate(p) for p in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    async def get_first(
        self,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> Person | None:
        """
        Get the first person, or None if no results.

        See PersonService.get_first() for details.

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        page = await self.list(
            field_ids=field_ids,
            field_types=field_types,
            limit=1,
        )
        return page.data[0] if page.data else None

    async def batch_get(
        self,
        person_ids: Sequence[PersonId],
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        include_field_values: bool = False,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
        max_concurrent: int = 10,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[PersonId, Person]:
        """
        Fetch multiple persons with controlled concurrency.

        Makes individual get() calls with bounded concurrency.

        Args:
            person_ids: Person IDs to fetch
            field_ids: Specific field IDs to include
            field_types: Field types to include
            include_field_values: If True, fetch embedded field values
            with_interaction_dates: Include interaction date summaries
            with_interaction_persons: Include person IDs for interactions
            max_concurrent: Maximum concurrent API calls (default: 10)
            on_error: How to handle AffinityError exceptions:
                - "raise": Raise on first AffinityError (default)
                - "skip": Skip failed IDs, return partial results

        Returns:
            Dict mapping person_id -> Person for successfully fetched persons.

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        if not person_ids:
            return {}
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        unique_ids = list(dict.fromkeys(person_ids))
        results: dict[PersonId, Person] = {}

        async def fetch_one(pid: PersonId) -> tuple[PersonId, Person | None]:
            try:
                person = await self.get(
                    pid,
                    field_ids=field_ids,
                    field_types=field_types,
                    include_field_values=include_field_values,
                    with_interaction_dates=with_interaction_dates,
                    with_interaction_persons=with_interaction_persons,
                )
                return (pid, person)
            except AffinityError:
                if on_error == "raise":
                    raise
                return (pid, None)

        for i in range(0, len(unique_ids), max_concurrent):
            chunk = unique_ids[i : i + max_concurrent]
            tasks = [asyncio.create_task(fetch_one(pid)) for pid in chunk]
            try:
                for coro in asyncio.as_completed(tasks):
                    pid, person = await coro
                    if person is not None:
                        results[pid] = person
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        return results

    def pages(
        self,
        *,
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **_unsupported: Any,
    ) -> AsyncIterator[PaginatedResponse[Person]]:
        """
        Iterate person pages (not items), yielding `PaginatedResponse[Person]`.

        Useful for ETL scripts that need checkpoint/resume via `page.next_cursor`.

        Args:
            ids: Specific person IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include in response
            field_types: Field types to include
            limit: Maximum results per page
            cursor: Cursor to resume pagination

        Returns:
            AsyncIterator of PaginatedResponse[Person]

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        return self._pages_iter(
            ids=ids,
            field_ids=field_ids,
            field_types=field_types,
            limit=limit,
            cursor=cursor,
        )

    async def _pages_iter(
        self,
        *,
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[PaginatedResponse[Person]]:
        other_params = (ids, field_ids, field_types, limit)
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
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> AsyncIterator[Person]:
        """
        Iterate through all persons with automatic pagination.

        Args:
            ids: Specific person IDs to fetch (batch lookup)
            field_ids: Specific field IDs to include
            field_types: Field types to include

        Yields:
            Person objects

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")

        async def fetch_page(next_url: str | None) -> PaginatedResponse[Person]:
            if next_url:
                data = await self._client.get_url(next_url)
                return PaginatedResponse[Person](
                    data=[Person.model_validate(p) for p in data.get("data", [])],
                    pagination=PaginationInfo.model_validate(data.get("pagination", {})),
                )
            return await self.list(ids=ids, field_ids=field_ids, field_types=field_types)

        return AsyncPageIterator(fetch_page)

    def iter(
        self,
        *,
        ids: Sequence[PersonId] | None = None,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        **_unsupported: Any,
    ) -> AsyncIterator[Person]:
        """
        Auto-paginate all persons.

        Alias for `all()` (FR-006 public contract).

        Note:
            The V2 /persons endpoint silently ignores any ``filter=`` parameter —
            passing one would return unfiltered results without warning. This method
            therefore rejects ``filter=`` with a ``ValueError``. To search persons
            by name or email use ``client.persons.search_pages(term)``. To filter
            by list-specific fields use ``client.lists.entries(list_id).list(filter=...)``.
        """
        if "filter" in _unsupported:
            raise ValueError(
                "V2 /persons does not support server-side filter. "
                "To search by name/email use client.persons.search_pages(term). "
                "To filter by list-specific fields use "
                "client.lists.entries(list_id).list(filter=...)."
            )
        if _unsupported:
            raise TypeError(f"Unexpected keyword arguments: {list(_unsupported)}")
        return self.all(ids=ids, field_ids=field_ids, field_types=field_types)

    async def get(
        self,
        person_id: PersonId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        include_field_values: bool = False,
        retries: int = 0,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Person:
        """
        Get a single person by ID.

        Args:
            person_id: The person ID
            field_ids: Specific field IDs to include in response
            field_types: Field types to include (e.g., ["enriched", "global"])
            include_field_values: If True, fetch embedded field values. This saves
                one API call when you need both person info and field values.
                Cannot be combined with field_ids/field_types.
            retries: Number of retries on 404 NotFoundError. Default is 0 (fail fast).
                Set to 2-3 if calling immediately after create() to handle eventual
                consistency lag.
            with_interaction_dates: Include interaction date summaries (last/next
                meeting dates, email dates).
            with_interaction_persons: Include person IDs for each interaction.
                Only applies when with_interaction_dates=True.

        Returns:
            Person object with requested field data.
            When include_field_values=True, the Person will have a `field_values`
            attribute containing the list of FieldValue objects.
            When with_interaction_dates=True, the Person will have interaction_dates
            and interactions populated.

        Raises:
            NotFoundError: If person does not exist after all retries.
            ValueError: If include_field_values is combined with field_ids/field_types.

        Note:
            When combining with_interaction_dates with field_ids/field_types,
            two API calls are made internally and the results are merged.
        """
        return await self._get_with_retry(
            person_id,
            field_ids=field_ids,
            field_types=field_types,
            include_field_values=include_field_values,
            retries=retries,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
        )

    async def _get_with_retry(
        self,
        person_id: PersonId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        include_field_values: bool = False,
        retries: int = 0,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Person:
        """Internal: get with retry logic."""
        last_error: NotFoundError | None = None
        attempts = retries + 1  # retries=0 means 1 attempt

        for attempt in range(attempts):
            try:
                return await self._get_impl(
                    person_id,
                    field_ids=field_ids,
                    field_types=field_types,
                    include_field_values=include_field_values,
                    with_interaction_dates=with_interaction_dates,
                    with_interaction_persons=with_interaction_persons,
                )
            except NotFoundError as e:
                last_error = e
                if attempt < attempts - 1:  # Don't sleep after last attempt
                    await asyncio.sleep(0.5 * (attempt + 1))  # 0.5s, 1s, 1.5s backoff

        # V1 fallback: If V2 returned 404, try V1 API (handles V1→V2 sync delays)
        # Skip if already using V1 path (include_field_values or with_interaction_dates)
        if last_error is not None and not include_field_values and not with_interaction_dates:
            try:
                v1_data = await self._client.get(f"/persons/{person_id}", v1=True)
                normalized = _normalize_v1_person_response(v1_data)
                return Person.model_validate(normalized)
            except NotFoundError:
                pass  # V1 also failed, raise original V2 error

        raise last_error  # type: ignore[misc]

    async def _get_impl(
        self,
        person_id: PersonId,
        *,
        field_ids: Sequence[AnyFieldId] | None = None,
        field_types: Sequence[FieldType] | None = None,
        include_field_values: bool = False,
        with_interaction_dates: bool = False,
        with_interaction_persons: bool = False,
    ) -> Person:
        """Internal: actual get implementation."""
        has_field_filters = field_ids is not None or field_types is not None

        # include_field_values returns embedded field values, which is incompatible
        # with field_ids/field_types filtering (different data structures)
        if include_field_values and has_field_filters:
            raise ValueError(
                "Cannot combine 'include_field_values' with 'field_ids' or 'field_types'. "
                "Use include_field_values alone for embedded field values, or use "
                "field_ids/field_types alone for filtered fields."
            )

        # Path 1: include_field_values (V1 API, may include interaction dates)
        if include_field_values:
            v1_params: dict[str, Any] = {}
            if with_interaction_dates:
                v1_params["with_interaction_dates"] = True
            if with_interaction_persons:
                v1_params["with_interaction_persons"] = True

            data = await self._client.get(
                f"/persons/{person_id}",
                params=v1_params or None,
                v1=True,
            )

            field_values_data = data.get("field_values", [])
            # V1 embedded field_values don't include entityId; add it before validation
            field_values = [
                FieldValue.model_validate({**fv, "entityId": int(person_id)})
                for fv in field_values_data
            ]
            normalized = _normalize_v1_person_response(data)
            person = Person.model_validate(normalized)
            object.__setattr__(person, "field_values", field_values)
            return person

        # Path 2: with_interaction_dates (may need merge if field filters present)
        if with_interaction_dates:
            v1_params = {"with_interaction_dates": True}
            if with_interaction_persons:
                v1_params["with_interaction_persons"] = True

            interaction_data = await self._client.get(
                f"/persons/{person_id}",
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
                    f"/persons/{person_id}",
                    params=v2_params,
                )

                # Merge: filtered fields + interaction data
                filtered_data["interaction_dates"] = interaction_data.get("interaction_dates")
                filtered_data["interactions"] = interaction_data.get("interactions")
                return Person.model_validate(filtered_data)

            # No field filtering, normalize and return V1 data
            normalized = _normalize_v1_person_response(interaction_data)
            return Person.model_validate(normalized)

        # Path 3: Standard path (supports field filtering)
        params: dict[str, Any] = {}
        if field_ids:
            params["fieldIds"] = [str(field_id) for field_id in field_ids]
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = await self._client.get(f"/persons/{person_id}", params=params or None)
        return Person.model_validate(data)

    async def get_list_entries(
        self,
        person_id: PersonId,
    ) -> PaginatedResponse[ListEntry]:
        """Get all list entries for a person across all lists."""
        data = await self._client.get(f"/persons/{person_id}/list-entries")

        return PaginatedResponse[ListEntry](
            data=[ListEntry.model_validate(e) for e in data.get("data", [])],
            pagination=PaginationInfo.model_validate(data.get("pagination", {})),
        )

    async def get_lists(
        self,
        person_id: PersonId,
    ) -> PaginatedResponse[ListSummary]:
        """Get all lists that contain this person."""
        data = await self._client.get(f"/persons/{person_id}/lists")

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
        Get metadata about person fields.

        Cached for performance.
        """
        params: dict[str, Any] = {}
        if field_types:
            params["fieldTypes"] = [field_type.value for field_type in field_types]

        data = await self._client.get(
            "/persons/fields",
            params=params or None,
            cache_key=f"person_fields:{','.join(field_types or [])}",
            cache_ttl=300,
        )

        return [FieldMetadata.model_validate(f) for f in data.get("data", [])]

    # =========================================================================
    # Associations (V1 API)
    # =========================================================================

    async def get_associated_company_ids(
        self,
        person_id: PersonId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[CompanyId]:
        """
        Get associated company IDs for a person.

        V1-only: V2 does not expose person -> company associations directly.
        Uses GET `/persons/{id}` (V1) and returns `organization_ids`.

        Args:
            person_id: The person ID
            max_results: Maximum number of company IDs to return

        Returns:
            List of CompanyId values associated with this person

        Note:
            The Person model already has `company_ids` populated from V1's
            `organizationIds` field. This method provides API parity with
            `CompanyService.get_associated_person_ids()`.
        """
        data = await self._client.get(f"/persons/{person_id}", v1=True)
        # Defensive: handle potential {"person": {...}} wrapper
        # (consistent with CompanyService.get_associated_person_ids pattern)
        person = data.get("person") if isinstance(data, dict) else None
        source = person if isinstance(person, dict) else data
        org_ids = None
        if isinstance(source, dict):
            org_ids = source.get("organization_ids") or source.get("organizationIds")

        if not isinstance(org_ids, list):
            return []

        ids = [CompanyId(int(cid)) for cid in org_ids if cid is not None]
        if max_results is not None and max_results >= 0:
            return ids[:max_results]
        return ids

    async def get_associated_company_ids_batch(
        self,
        person_ids: Sequence[PersonId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[PersonId, builtins.list[CompanyId]]:
        """
        Get company associations for multiple persons.

        Makes one V1 API call per person.

        Args:
            person_ids: Sequence of person IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping person_id -> list of company_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        result: dict[PersonId, builtins.list[CompanyId]] = {}
        for person_id in person_ids:
            try:
                result[person_id] = await self.get_associated_company_ids(person_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this person
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for person {person_id}: {e}"
                    ) from e
                # skip: continue without this person
        return result

    async def get_associated_opportunity_ids(
        self,
        person_id: PersonId,
        *,
        max_results: int | None = None,
    ) -> builtins.list[OpportunityId]:
        """
        Get associated opportunity IDs for a person.

        V1-only: V2 does not expose person -> opportunity associations directly.
        Uses GET `/persons/{id}` (V1) and returns `opportunity_ids`.

        Args:
            person_id: The person ID
            max_results: Maximum number of opportunity IDs to return

        Returns:
            List of OpportunityId values associated with this person

        Note:
            The Person model already has `opportunity_ids` populated from V1's
            `opportunityIds` field. This method provides API parity with
            `OpportunityService.get_associated_person_ids()`.
        """
        data = await self._client.get(f"/persons/{person_id}", v1=True)
        # Defensive: handle potential {"person": {...}} wrapper
        person = data.get("person") if isinstance(data, dict) else None
        source = person if isinstance(person, dict) else data
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
        person_ids: Sequence[PersonId],
        *,
        on_error: Literal["raise", "skip"] = "raise",
    ) -> dict[PersonId, builtins.list[OpportunityId]]:
        """
        Get opportunity associations for multiple persons.

        Makes one V1 API call per person.

        Args:
            person_ids: Sequence of person IDs to fetch
            on_error: How to handle errors - "raise" (default) or "skip" failed IDs

        Returns:
            Dict mapping person_id -> list of opportunity_ids

        Raises:
            AffinityError: If on_error="raise" and any fetch fails.
        """
        result: dict[PersonId, builtins.list[OpportunityId]] = {}
        for person_id in person_ids:
            try:
                result[person_id] = await self.get_associated_opportunity_ids(person_id)
            except AffinityError:
                if on_error == "raise":
                    raise
                # skip: continue without this person
            except Exception as e:
                if on_error == "raise":
                    raise AffinityError(
                        f"Failed to get associations for person {person_id}: {e}"
                    ) from e
                # skip: continue without this person
        return result

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
    ) -> PaginatedResponse[Person]:
        """
        Search for persons by name or email.

        Uses V1 API for search functionality.
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

        data = await self._client.get("/persons", params=params, v1=True)
        items = [Person.model_validate(p) for p in data.get("persons", [])]
        return PaginatedResponse[Person](
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
    ) -> AsyncIterator[PaginatedResponse[Person]]:
        """
        Iterate V1 person-search result pages.

        Useful for scripts that need checkpoint/resume via `next_page_token`.

        Args:
            term: Search term (name or email)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            PaginatedResponse[Person] for each page
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
    ) -> AsyncIterator[Person]:
        """
        Iterate all V1 person-search results with automatic pagination.

        Args:
            term: Search term (name or email)
            with_interaction_dates: Include interaction date data
            with_interaction_persons: Include persons for interactions
            with_opportunities: Include associated opportunity IDs
            page_size: Results per page (max 500)
            page_token: Resume from this pagination token

        Yields:
            Person objects matching the search term
        """
        async for page in self.search_pages(
            term,
            with_interaction_dates=with_interaction_dates,
            with_interaction_persons=with_interaction_persons,
            with_opportunities=with_opportunities,
            page_size=page_size,
            page_token=page_token,
        ):
            for person in page.data:
                yield person

    async def resolve(
        self,
        *,
        email: str | None = None,
        name: str | None = None,
    ) -> Person | None:
        """
        Find a single person by email or name.

        This is a convenience helper that searches and returns the first exact match,
        or None if not found. Uses V1 search internally.
        """
        if not email and not name:
            raise ValueError("Must provide either email or name")

        term = email or name or ""
        async for page in self.search_pages(term, page_size=10):
            for person in page.data:
                if _person_matches(person, email=email, name=name):
                    return person

        return None

    async def resolve_all(
        self,
        *,
        email: str | None = None,
        name: str | None = None,
    ) -> builtins.list[Person]:
        """
        Find all persons matching an email or name.

        Notes:
        - This auto-paginates V1 search results to collect exact matches.
        - Unlike resolve(), this returns every match in server-provided order.
        """
        if not email and not name:
            raise ValueError("Must provide either email or name")

        term = email or name or ""
        matches: builtins.list[Person] = []
        async for page in self.search_pages(term, page_size=10):
            for person in page.data:
                if _person_matches(person, email=email, name=name):
                    matches.append(person)
        return matches

    # =========================================================================
    # Write Operations (V1 API)
    # =========================================================================

    _DEDUP_PAGE_CAP = 3  # 3 pages x 500 = 1500 fuzzy results scanned per search term

    async def create(self, data: PersonCreate, *, if_not_exists: bool = True) -> Person:
        """
        Create a new person.

        Args:
            data: Person creation data
            if_not_exists: If True (default), check for an existing person with
                the same primary email (case-insensitive exact) — falling back
                to exact first+last name match when no email is provided.
                Uses V1 search_pages(term) with page_size=500, capped at
                _DEDUP_PAGE_CAP pages. Set to False to skip the check and
                create unconditionally.

        Returns:
            Created person

        Raises:
            DuplicateEntityError: When if_not_exists=True and an exact-email
                OR exact full-name match already exists.

        Note:
            Creates use V1 API, while reads use V2 API. Due to eventual consistency
            between V1 and V2, a `get()` call immediately after `create()` may return
            404 NotFoundError. If you need to read immediately after creation, either:
            - Use the Person object returned by this method (it contains the created data)
            - Add a short delay (100-500ms) before calling get()
            - Implement retry logic in your application
        """
        if if_not_exists:
            existing = await self._find_exact_duplicate(
                first_name=data.first_name,
                last_name=data.last_name,
                emails=list(data.emails or []),
            )
            if existing is not None:
                raise DuplicateEntityError(
                    f"Person with email or name already exists (id={existing.id})",
                    entity_type="person",
                    existing_id=int(existing.id),
                    existing_name=f"{existing.first_name or ''} {existing.last_name or ''}".strip(),
                )

        payload = data.model_dump(by_alias=True, mode="json")
        if not data.company_ids:
            payload.pop("organization_ids", None)

        result = await self._client.post("/persons", json=payload, v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("person")

        return Person.model_validate(result)

    async def _find_exact_duplicate(
        self,
        *,
        first_name: str | None,
        last_name: str | None,
        emails: builtins.list[str],
    ) -> Person | None:
        """Search V1 for an exact-email or exact full-name match. Returns None if no match.

        Email takes priority — exact case-insensitive match against any entry in the
        person's emails list or primary_email. Falls back to exact first+last name match
        when no emails are provided in the create payload.

        Iterates at most `_DEDUP_PAGE_CAP` pages per search term (1500 fuzzy results).
        """
        emails_lower = {e.strip().lower() for e in emails if e}
        fn_lower = (first_name or "").strip().lower()
        ln_lower = (last_name or "").strip().lower()

        search_terms = list(emails) if emails else [f"{first_name or ''} {last_name or ''}".strip()]
        seen_ids: set[int] = set()

        for term in search_terms:
            if not term:
                continue
            page_num = 0
            async for page in self.search_pages(term, page_size=500):
                for person in page.data:
                    pid = int(person.id)
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)

                    person_emails = {(e or "").strip().lower() for e in (person.emails or [])}
                    if person.primary_email:
                        person_emails.add(person.primary_email.strip().lower())

                    if emails_lower and emails_lower & person_emails:
                        return person

                    if (
                        not emails_lower
                        and fn_lower
                        and ln_lower
                        and (person.first_name or "").lower() == fn_lower
                        and (person.last_name or "").lower() == ln_lower
                    ):
                        return person

                page_num += 1
                if page_num >= self._DEDUP_PAGE_CAP:
                    break
                if not page.next_page_token:
                    break

        return None

    async def update(
        self,
        person_id: PersonId,
        data: PersonUpdate,
    ) -> Person:
        """
        Update an existing person.

        Note: To add emails/organizations, include existing values plus new ones.
        """
        payload = data.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
            exclude_none=True,
        )

        result = await self._client.put(
            f"/persons/{person_id}",
            json=payload,
            v1=True,
        )

        if self._client.cache:
            self._client.cache.invalidate_prefix("person")

        return Person.model_validate(result)

    async def delete(self, person_id: PersonId) -> bool:
        """Delete a person (also deletes associated field values)."""
        result = await self._client.delete(f"/persons/{person_id}", v1=True)

        if self._client.cache:
            self._client.cache.invalidate_prefix("person")

        return bool(result.get("success", False))

    # =========================================================================
    # Merge Operations (V2 BETA)
    # =========================================================================

    async def merge(
        self,
        primary_id: PersonId,
        duplicate_id: PersonId,
    ) -> str:
        """
        Merge a duplicate person into a primary person.

        Returns a task URL to check merge status.
        """
        if not self._client.enable_beta_endpoints:
            raise BetaEndpointDisabledError(
                "Person merge is a beta endpoint; set enable_beta_endpoints=True to use it."
            )
        result = await self._client.post(
            "/person-merges",
            json={
                "primaryPersonId": int(primary_id),
                "duplicatePersonId": int(duplicate_id),
            },
        )
        return str(result.get("taskUrl", ""))

    async def get_merge_status(self, task_id: str) -> MergeTask:
        """Check the status of a merge operation."""
        data = await self._client.get(f"/tasks/person-merges/{task_id}")
        return MergeTask.model_validate(data)
