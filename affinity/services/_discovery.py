"""Duplicate suggestions and inferred connections (V2, beta), mixed into the company and person
services.

Duplicate suggestions need Affinity API 2026-07-15. Inferred connections answer "who do I
know at this company?": targets work at the given company today.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from ..models.discovery import (
    CompanyDuplicateSuggestion,
    CoworkerConnectionGroup,
    InvestorExecutiveConnectionGroup,
    PersonDuplicateSuggestion,
)
from ..models.pagination import (
    AsyncPageIterator,
    PageIterator,
    PaginatedResponse,
    PaginationInfoWithTotal,
)
from ..models.types import CompanyId
from ._org_reads import check_cursor_alone, check_limit, page_of

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient

T = TypeVar("T")
DUPLICATES_MIN_API_VERSION = "2026-07-15"
DUPLICATE_ORDER = ("name", "-name", "createdAt", "-createdAt")
CompanyMatch = Literal["domain-match", "domain-redirect", "name"]
PersonMatch = Literal["intelligent", "name"]


def duplicate_params(
    *,
    term: str | None,
    match_criteria: str | None,
    order_by: str | None,
    limit: int | None,
    total_count: bool,
) -> list[tuple[str, Any]]:
    check_limit(limit)
    if term is not None and len(term) < 2:
        raise ValueError("term needs at least 2 characters")
    if order_by is not None and order_by not in DUPLICATE_ORDER:
        raise ValueError(f"order_by must be one of {', '.join(DUPLICATE_ORDER)}")
    params: list[tuple[str, Any]] = []
    if term:
        params.append(("term", term))
    if match_criteria:
        params.append(("filter", f"matchCriteria={match_criteria}"))
    if order_by:
        params.append(("orderBy", order_by))
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    return params


def connection_params(
    company_id: int, limit: int | None, total_count: bool
) -> list[tuple[str, Any]]:
    if limit is not None and not 1 <= limit <= 50:
        raise ValueError("'limit' must be between 1 and 50")
    params: list[tuple[str, Any]] = [("filter", f"target.currentCompany.id={int(company_id)}")]
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    return params


def groups_page(parse: Callable[[dict[str, Any]], T], data: dict[str, Any]) -> PaginatedResponse[T]:
    return PaginatedResponse[Any](
        data=[parse(item) for item in data.get("data", [])],
        pagination=PaginationInfoWithTotal.model_validate(data.get("pagination", {})),
    )


class _Base:
    _client: Any


# --- sync ---------------------------------------------------------------------------------------


class _DuplicateSuggestions(_Base):
    _duplicates_path: str
    _duplicate_model: type[Any]

    def _list_duplicates(
        self,
        term: str | None,
        match_criteria: str | None,
        order_by: str | None,
        limit: int | None,
        total_count: bool,
        cursor: str | None,
    ) -> PaginatedResponse[Any]:
        check_cursor_alone(cursor, term, match_criteria, order_by, limit, total_count)
        client: HTTPClient = self._client
        if cursor is not None:
            return page_of(
                self._duplicate_model,
                client.get_url(cursor, min_api_version=DUPLICATES_MIN_API_VERSION),
            )
        params = duplicate_params(
            term=term,
            match_criteria=match_criteria,
            order_by=order_by,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            self._duplicate_model,
            client.get(
                self._duplicates_path,
                params=params or None,
                min_api_version=DUPLICATES_MIN_API_VERSION,
            ),
        )


class CompanyDiscoveryMixin(_DuplicateSuggestions):
    """Company duplicate suggestions and inferred connections (sync)."""

    _duplicates_path = "/duplicates/company-suggestions"
    _duplicate_model = CompanyDuplicateSuggestion

    def list_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: CompanyMatch | None = None,
        order_by: str | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[CompanyDuplicateSuggestion]:
        """One page of company pairs Affinity thinks are duplicates (V2, API 2026-07-15+).

        Only pairs nobody has acted on yet. ``term`` (2+ characters) narrows by name;
        ``match_criteria``: ``domain-match``, ``domain-redirect`` or ``name``; ``order_by``:
        ``name``, ``-name``, ``createdAt``, ``-createdAt``. Merge one with :meth:`merge`.
        """
        return self._list_duplicates(term, match_criteria, order_by, limit, total_count, cursor)

    def iter_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: CompanyMatch | None = None,
        order_by: str | None = None,
    ) -> Iterator[CompanyDuplicateSuggestion]:
        """Every company duplicate suggestion (all pages); see
        :meth:`list_duplicate_suggestions`."""
        return PageIterator(
            lambda c: (
                self.list_duplicate_suggestions(cursor=c)
                if c
                else self.list_duplicate_suggestions(
                    term=term, match_criteria=match_criteria, order_by=order_by, limit=100
                )
            )
        )

    def list_coworker_connections(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[CoworkerConnectionGroup]:
        """People at this company today whom someone you know worked with before (V2, beta).

        One group per target person, strongest first, each with the people you know and the
        shared employer and dates. ``limit`` is 1-50 targets per page.
        """
        check_cursor_alone(cursor, limit, total_count)
        client: HTTPClient = self._client
        if cursor is not None:
            return groups_page(CoworkerConnectionGroup.from_api, client.get_url(cursor))
        return groups_page(
            CoworkerConnectionGroup.from_api,
            client.get(
                "/inferred-connections/coworkers",
                params=connection_params(company_id, limit, total_count),
            ),
        )

    def iter_coworker_connections(self, company_id: CompanyId) -> Iterator[CoworkerConnectionGroup]:
        """Every coworker connection group for this company (all pages)."""
        return PageIterator(
            lambda c: (
                self.list_coworker_connections(company_id, cursor=c)
                if c
                else self.list_coworker_connections(company_id, limit=50)
            )
        )

    def list_investor_executive_connections(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[InvestorExecutiveConnectionGroup]:
        """Executives at this company today who ran a company that an investor you know backed
        (V2, beta). One group per target, strongest first, with the funding round and roles.
        ``limit`` is 1-50 targets per page."""
        check_cursor_alone(cursor, limit, total_count)
        client: HTTPClient = self._client
        if cursor is not None:
            return groups_page(InvestorExecutiveConnectionGroup.from_api, client.get_url(cursor))
        return groups_page(
            InvestorExecutiveConnectionGroup.from_api,
            client.get(
                "/inferred-connections/investor-executive-connections",
                params=connection_params(company_id, limit, total_count),
            ),
        )

    def iter_investor_executive_connections(
        self, company_id: CompanyId
    ) -> Iterator[InvestorExecutiveConnectionGroup]:
        """Every investor-executive connection group for this company (all pages)."""
        return PageIterator(
            lambda c: (
                self.list_investor_executive_connections(company_id, cursor=c)
                if c
                else self.list_investor_executive_connections(company_id, limit=50)
            )
        )


class PersonDiscoveryMixin(_DuplicateSuggestions):
    """Person duplicate suggestions (sync)."""

    _duplicates_path = "/duplicates/person-suggestions"
    _duplicate_model = PersonDuplicateSuggestion

    def list_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: PersonMatch | None = None,
        order_by: str | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[PersonDuplicateSuggestion]:
        """One page of person pairs Affinity thinks are duplicates (V2, API 2026-07-15+).

        Only pairs nobody has acted on yet. ``match_criteria``: ``intelligent`` or ``name``.
        Merge one with :meth:`merge`. See ``companies.list_duplicate_suggestions`` for the rest.
        """
        return self._list_duplicates(term, match_criteria, order_by, limit, total_count, cursor)

    def iter_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: PersonMatch | None = None,
        order_by: str | None = None,
    ) -> Iterator[PersonDuplicateSuggestion]:
        """Every person duplicate suggestion (all pages)."""
        return PageIterator(
            lambda c: (
                self.list_duplicate_suggestions(cursor=c)
                if c
                else self.list_duplicate_suggestions(
                    term=term, match_criteria=match_criteria, order_by=order_by, limit=100
                )
            )
        )


# --- async --------------------------------------------------------------------------------------


class _AsyncDuplicateSuggestions(_Base):
    _duplicates_path: str
    _duplicate_model: type[Any]

    async def _list_duplicates(
        self,
        term: str | None,
        match_criteria: str | None,
        order_by: str | None,
        limit: int | None,
        total_count: bool,
        cursor: str | None,
    ) -> PaginatedResponse[Any]:
        check_cursor_alone(cursor, term, match_criteria, order_by, limit, total_count)
        client: AsyncHTTPClient = self._client
        if cursor is not None:
            return page_of(
                self._duplicate_model,
                await client.get_url(cursor, min_api_version=DUPLICATES_MIN_API_VERSION),
            )
        params = duplicate_params(
            term=term,
            match_criteria=match_criteria,
            order_by=order_by,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            self._duplicate_model,
            await client.get(
                self._duplicates_path,
                params=params or None,
                min_api_version=DUPLICATES_MIN_API_VERSION,
            ),
        )


class AsyncCompanyDiscoveryMixin(_AsyncDuplicateSuggestions):
    """Company duplicate suggestions and inferred connections (async)."""

    _duplicates_path = "/duplicates/company-suggestions"
    _duplicate_model = CompanyDuplicateSuggestion

    async def list_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: CompanyMatch | None = None,
        order_by: str | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[CompanyDuplicateSuggestion]:
        """One page of company duplicate suggestions; see the sync method."""
        return await self._list_duplicates(
            term, match_criteria, order_by, limit, total_count, cursor
        )

    def iter_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: CompanyMatch | None = None,
        order_by: str | None = None,
    ) -> AsyncIterator[CompanyDuplicateSuggestion]:
        """Every company duplicate suggestion (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[CompanyDuplicateSuggestion]:
            if c:
                return await self.list_duplicate_suggestions(cursor=c)
            return await self.list_duplicate_suggestions(
                term=term, match_criteria=match_criteria, order_by=order_by, limit=100
            )

        return AsyncPageIterator(fetch)

    async def list_coworker_connections(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[CoworkerConnectionGroup]:
        """Coworker connections into this company; see the sync method."""
        check_cursor_alone(cursor, limit, total_count)
        client: AsyncHTTPClient = self._client
        if cursor is not None:
            return groups_page(CoworkerConnectionGroup.from_api, await client.get_url(cursor))
        return groups_page(
            CoworkerConnectionGroup.from_api,
            await client.get(
                "/inferred-connections/coworkers",
                params=connection_params(company_id, limit, total_count),
            ),
        )

    def iter_coworker_connections(
        self, company_id: CompanyId
    ) -> AsyncIterator[CoworkerConnectionGroup]:
        """Every coworker connection group for this company (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[CoworkerConnectionGroup]:
            if c:
                return await self.list_coworker_connections(company_id, cursor=c)
            return await self.list_coworker_connections(company_id, limit=50)

        return AsyncPageIterator(fetch)

    async def list_investor_executive_connections(
        self,
        company_id: CompanyId,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[InvestorExecutiveConnectionGroup]:
        """Investor-executive connections into this company; see the sync method."""
        check_cursor_alone(cursor, limit, total_count)
        client: AsyncHTTPClient = self._client
        if cursor is not None:
            return groups_page(
                InvestorExecutiveConnectionGroup.from_api, await client.get_url(cursor)
            )
        return groups_page(
            InvestorExecutiveConnectionGroup.from_api,
            await client.get(
                "/inferred-connections/investor-executive-connections",
                params=connection_params(company_id, limit, total_count),
            ),
        )

    def iter_investor_executive_connections(
        self, company_id: CompanyId
    ) -> AsyncIterator[InvestorExecutiveConnectionGroup]:
        """Every investor-executive connection group for this company (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[InvestorExecutiveConnectionGroup]:
            if c:
                return await self.list_investor_executive_connections(company_id, cursor=c)
            return await self.list_investor_executive_connections(company_id, limit=50)

        return AsyncPageIterator(fetch)


class AsyncPersonDiscoveryMixin(_AsyncDuplicateSuggestions):
    """Person duplicate suggestions (async)."""

    _duplicates_path = "/duplicates/person-suggestions"
    _duplicate_model = PersonDuplicateSuggestion

    async def list_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: PersonMatch | None = None,
        order_by: str | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[PersonDuplicateSuggestion]:
        """One page of person duplicate suggestions; see the sync method."""
        return await self._list_duplicates(
            term, match_criteria, order_by, limit, total_count, cursor
        )

    def iter_duplicate_suggestions(
        self,
        *,
        term: str | None = None,
        match_criteria: PersonMatch | None = None,
        order_by: str | None = None,
    ) -> AsyncIterator[PersonDuplicateSuggestion]:
        """Every person duplicate suggestion (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[PersonDuplicateSuggestion]:
            if c:
                return await self.list_duplicate_suggestions(cursor=c)
            return await self.list_duplicate_suggestions(
                term=term, match_criteria=match_criteria, order_by=order_by, limit=100
            )

        return AsyncPageIterator(fetch)
