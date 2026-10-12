"""V2 search (beta), mixed into the company, person and list entry services: ``find``,
``iter_find`` and ``find_body``.

Affinity filters and sorts on its side. The next page is the same POST body sent to the
``nextUrl`` (a cursor alone is rejected), so a ``cursor`` is always used together with the
criteria that produced it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from ..criteria import Condition, FilterGroup, SearchCriteria, Sort
from ..exceptions import AuthorizationError
from ..models.pagination import AsyncPageIterator, PageIterator, PaginatedResponse
from ..models.types import AnyFieldId, FieldType
from ._org_reads import check_limit, page_of

if TYPE_CHECKING:
    from ..models.entities import FieldMetadata

T = TypeVar("T", bound=BaseModel)
SEARCH_INCLUDES = ("filterability", "sortability")
_PERMISSION_HINT = (
    " Search needs the 'Export All Organizations directory' permission (companies and "
    "persons) or 'Export data from Lists' (list entries)."
)


def criteria_of(
    where: Condition | FilterGroup | SearchCriteria | None,
    sort: Sequence[Sort] | Sort | None,
    term: str | None,
    term_fields: Sequence[AnyFieldId | str] | None,
) -> SearchCriteria:
    if isinstance(where, SearchCriteria):
        if sort is not None or term is not None or term_fields is not None:
            raise ValueError("Pass either a SearchCriteria or where/sort/term, not both")
        return where
    sorts = (sort,) if isinstance(sort, Sort) else tuple(sort or ())
    return SearchCriteria(
        where=where, sort=sorts, term=term, term_fields=tuple(str(f) for f in term_fields or ())
    )


def search_params(
    *,
    field_ids: Sequence[AnyFieldId | str] | None,
    field_types: Sequence[FieldType | str] | None,
    limit: int | None,
    total_count: bool,
) -> list[tuple[str, Any]]:
    check_limit(limit)
    params: list[tuple[str, Any]] = [("fieldIds", str(f)) for f in field_ids or ()]
    params += [("fieldTypes", getattr(t, "value", t)) for t in field_types or ()]
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    return params


def check_cursor_page(
    cursor: str | None,
    *,
    field_ids: Any,
    field_types: Any,
    limit: int | None,
    total_count: bool,
) -> None:
    if cursor is not None and (field_ids or field_types or limit is not None or total_count):
        raise ValueError(
            "With 'cursor', pass only the search criteria: field_ids, field_types, limit and "
            "total_count are carried by the cursor"
        )


def with_permission_hint(exc: AuthorizationError) -> AuthorizationError:
    return AuthorizationError(
        exc.message + _PERMISSION_HINT,
        status_code=exc.status_code,
        response_body=exc.response_body,
        diagnostics=exc.diagnostics,
    )


def fields_by_id(fields: Sequence[FieldMetadata]) -> dict[str, FieldMetadata]:
    return {str(f.id): f for f in fields}


class SearchMixin(Generic[T]):
    """``find``, ``iter_find``, ``find_body`` (sync)."""

    _client: Any
    _search_model: type[T]

    def _search_path(self) -> str:
        raise NotImplementedError

    def _search_fields(self) -> Sequence[FieldMetadata]:
        raise NotImplementedError

    def _criteria_body(self, criteria: SearchCriteria) -> dict[str, Any]:
        fields = fields_by_id(self._search_fields()) if criteria.needs_fields() else None
        return criteria.to_body(fields)

    def find_body(
        self,
        body: Mapping[str, Any],
        *,
        field_ids: Sequence[AnyFieldId | str] | None = None,
        field_types: Sequence[FieldType | str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[T]:
        """One page of results for a prebuilt search body (the API's ``SearchCriteria`` JSON).
        For the next page pass the same ``body`` and the page's ``next_cursor``."""
        check_cursor_page(
            cursor,
            field_ids=field_ids,
            field_types=field_types,
            limit=limit,
            total_count=total_count,
        )
        try:
            if cursor is not None:
                data = self._client.post_url(cursor, json=dict(body), read_only=True)
            else:
                data = self._client.post(
                    self._search_path(),
                    json=dict(body),
                    params=search_params(
                        field_ids=field_ids,
                        field_types=field_types,
                        limit=limit,
                        total_count=total_count,
                    ),
                    read_only=True,
                )
        except AuthorizationError as e:
            raise with_permission_hint(e) from e
        return page_of(self._search_model, data)

    def find(
        self,
        where: Condition | FilterGroup | SearchCriteria | None = None,
        *,
        sort: Sequence[Sort] | Sort | None = None,
        term: str | None = None,
        term_fields: Sequence[AnyFieldId | str] | None = None,
        field_ids: Sequence[AnyFieldId | str] | None = None,
        field_types: Sequence[FieldType | str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[T]:
        """One page of records matching ``where`` (filtered and sorted by Affinity; V2 search,
        beta). See :mod:`affinity.criteria`.

        ``term``: a keyword (3+ characters) matched against names, domains and emails, and
        against ``term_fields``. ``field_ids`` / ``field_types``: field values to return.
        ``limit``: 1-100. For the next page call ``find`` again with the same criteria and
        ``cursor=page.next_cursor``.

        Raises ``AuthorizationError`` without the search permission ('Export All Organizations
        directory' for companies and persons, 'Export data from Lists' for list entries).
        """
        criteria = criteria_of(where, sort, term, term_fields)
        return self.find_body(
            self._criteria_body(criteria),
            field_ids=field_ids,
            field_types=field_types,
            limit=limit,
            total_count=total_count,
            cursor=cursor,
        )

    def iter_find(
        self,
        where: Condition | FilterGroup | SearchCriteria | None = None,
        *,
        sort: Sequence[Sort] | Sort | None = None,
        term: str | None = None,
        term_fields: Sequence[AnyFieldId | str] | None = None,
        field_ids: Sequence[AnyFieldId | str] | None = None,
        field_types: Sequence[FieldType | str] | None = None,
    ) -> Iterator[T]:
        """Every record matching ``where`` (all pages); see :meth:`find`."""
        body = self._criteria_body(criteria_of(where, sort, term, term_fields))
        return PageIterator(
            lambda c: (
                self.find_body(body, cursor=c)
                if c
                else self.find_body(body, field_ids=field_ids, field_types=field_types, limit=100)
            )
        )


class AsyncSearchMixin(Generic[T]):
    """Async ``find``, ``iter_find``, ``find_body``."""

    _client: Any
    _search_model: type[T]

    def _search_path(self) -> str:
        raise NotImplementedError

    async def _search_fields(self) -> Sequence[FieldMetadata]:
        raise NotImplementedError

    async def _criteria_body(self, criteria: SearchCriteria) -> dict[str, Any]:
        fields = fields_by_id(await self._search_fields()) if criteria.needs_fields() else None
        return criteria.to_body(fields)

    async def find_body(
        self,
        body: Mapping[str, Any],
        *,
        field_ids: Sequence[AnyFieldId | str] | None = None,
        field_types: Sequence[FieldType | str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[T]:
        """One page for a prebuilt search body; see the sync method."""
        check_cursor_page(
            cursor,
            field_ids=field_ids,
            field_types=field_types,
            limit=limit,
            total_count=total_count,
        )
        try:
            if cursor is not None:
                data = await self._client.post_url(cursor, json=dict(body), read_only=True)
            else:
                data = await self._client.post(
                    self._search_path(),
                    json=dict(body),
                    params=search_params(
                        field_ids=field_ids,
                        field_types=field_types,
                        limit=limit,
                        total_count=total_count,
                    ),
                    read_only=True,
                )
        except AuthorizationError as e:
            raise with_permission_hint(e) from e
        return page_of(self._search_model, data)

    async def find(
        self,
        where: Condition | FilterGroup | SearchCriteria | None = None,
        *,
        sort: Sequence[Sort] | Sort | None = None,
        term: str | None = None,
        term_fields: Sequence[AnyFieldId | str] | None = None,
        field_ids: Sequence[AnyFieldId | str] | None = None,
        field_types: Sequence[FieldType | str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[T]:
        """One page of matching records; see the sync method."""
        criteria = criteria_of(where, sort, term, term_fields)
        return await self.find_body(
            await self._criteria_body(criteria),
            field_ids=field_ids,
            field_types=field_types,
            limit=limit,
            total_count=total_count,
            cursor=cursor,
        )

    def iter_find(
        self,
        where: Condition | FilterGroup | SearchCriteria | None = None,
        *,
        sort: Sequence[Sort] | Sort | None = None,
        term: str | None = None,
        term_fields: Sequence[AnyFieldId | str] | None = None,
        field_ids: Sequence[AnyFieldId | str] | None = None,
        field_types: Sequence[FieldType | str] | None = None,
    ) -> AsyncIterator[T]:
        """Every matching record (all pages)."""
        criteria = criteria_of(where, sort, term, term_fields)
        body: dict[str, Any] | None = None

        async def fetch(c: str | None) -> PaginatedResponse[T]:
            nonlocal body
            if body is None:
                body = await self._criteria_body(criteria)
            if c:
                return await self.find_body(body, cursor=c)
            return await self.find_body(
                body, field_ids=field_ids, field_types=field_types, limit=100
            )

        return AsyncPageIterator(fetch)
