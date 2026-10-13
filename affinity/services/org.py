"""
Organization reads and feedback (V2): teams, users, and feedback to Affinity.

Teams need Affinity API 2026-09-17 (beta); users and feedback work under any version.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from pydantic import BaseModel

from ..models.entities import ListSummary
from ..models.org import Team, TeamMember, UserAccount, UserStatus
from ..models.pagination import AsyncPageIterator, PageIterator, PaginatedResponse
from ..models.types import UserId
from ._org_reads import check_cursor_alone, check_limit, page_of

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient

TEAMS_MIN_API_VERSION = "2026-09-17"
TEAM_INCLUDES = ("membersPreview", "accessibleListsPreview")
TEAM_ORDER = ("name", "-name", "createdAt", "-createdAt")
FeedbackType = Literal["api", "mcp", "other"]


def _either(field: str, ids: Sequence[int]) -> str:
    clause = " | ".join(f"{field}={int(i)}" for i in ids)
    return f"({clause})" if len(ids) > 1 else clause


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def team_includes(includes: bool | Sequence[str] | None) -> list[str]:
    """``True`` for both previews, or a list of them."""
    if not includes:
        return []
    values = list(TEAM_INCLUDES) if includes is True else list(includes)
    unknown = [v for v in values if v not in TEAM_INCLUDES]
    if unknown:
        raise ValueError(f"Unknown includes {unknown}; allowed: {', '.join(TEAM_INCLUDES)}")
    return values


def team_params(
    *,
    ids: Sequence[int] | None,
    name: str | None,
    name_contains: str | None,
    order_by: str | None,
    includes: bool | Sequence[str] | None,
    limit: int | None,
    total_count: bool,
) -> list[tuple[str, Any]]:
    check_limit(limit)
    clauses = []
    if ids:
        clauses.append(_either("id", ids))
    if name is not None:
        clauses.append(f"name={_quote(name)}")
    if name_contains is not None:
        clauses.append(f"name=~{_quote(name_contains)}")
    params: list[tuple[str, Any]] = []
    if clauses:
        params.append(("filter", " & ".join(clauses)))
    if order_by is not None:
        if order_by not in TEAM_ORDER:
            raise ValueError(f"order_by must be one of {', '.join(TEAM_ORDER)}")
        params.append(("orderBy", order_by))
    params.extend(("includes", v) for v in team_includes(includes))
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    return params


def user_params(
    *,
    term: str | None,
    ids: Sequence[int] | None,
    status: UserStatus | str | None,
    limit: int | None,
) -> list[tuple[str, Any]]:
    check_limit(limit)
    clauses = []
    if ids:
        clauses.append(_either("id", ids))
    if status is not None:
        clauses.append(f"status={UserStatus(status)}")
    params: list[tuple[str, Any]] = []
    if term:
        params.append(("term", term))
    if clauses:
        params.append(("filter", " & ".join(clauses)))
    if limit is not None:
        params.append(("limit", limit))
    return params


def feedback_body(kind: FeedbackType, subject: str, body: str) -> dict[str, str]:
    if kind not in ("api", "mcp", "other"):
        raise ValueError("kind must be 'api', 'mcp' or 'other'")
    if not 1 <= len(subject) <= 255:
        raise ValueError("subject must be 1-255 characters")
    if not 1 <= len(body) <= 10000:
        raise ValueError("body must be 1-10,000 characters")
    return {"type": kind, "subject": subject, "body": body}


# --- teams --------------------------------------------------------------------------------------


T = TypeVar("T", bound=BaseModel)


def team_page_params(limit: int | None, total_count: bool) -> list[tuple[str, Any]]:
    check_limit(limit)
    params: list[tuple[str, Any]] = []
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    return params


class TeamService:
    """Teams (V2, beta; Affinity API 2026-09-17+): the teams the caller may see."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        ids: Sequence[int] | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        order_by: str | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Team]:
        """One page of teams, by name unless ``order_by`` says otherwise.

        ``name`` matches exactly, ``name_contains`` anywhere (case-insensitive).
        ``order_by``: ``name``, ``-name``, ``createdAt`` or ``-createdAt``. ``includes=True``
        (or ``["membersPreview"]`` / ``["accessibleListsPreview"]``) fills the first members
        and accessible lists, with totals. ``limit`` is 1-100.
        """
        check_cursor_alone(cursor, ids, name, name_contains, order_by, includes, limit, total_count)
        if cursor is not None:
            return page_of(
                Team, self._client.get_url(cursor, min_api_version=TEAMS_MIN_API_VERSION)
            )
        params = team_params(
            ids=ids,
            name=name,
            name_contains=name_contains,
            order_by=order_by,
            includes=includes,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            Team,
            self._client.get(
                "/teams", params=params or None, min_api_version=TEAMS_MIN_API_VERSION
            ),
        )

    def iter(
        self,
        *,
        ids: Sequence[int] | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        order_by: str | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
    ) -> Iterator[Team]:
        """All teams matching the filters (every page); see :meth:`list`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[Team]:
            if next_url:
                return self.list(cursor=next_url)
            return self.list(
                ids=ids,
                name=name,
                name_contains=name_contains,
                order_by=order_by,
                includes=includes,
                limit=limit,
            )

        return PageIterator(fetch_page)

    def get(self, team_id: int, *, includes: bool | Sequence[str] | None = None) -> Team:
        """One team (``includes`` as in :meth:`list`)."""
        params = [("includes", v) for v in team_includes(includes)]
        return Team.model_validate(
            self._client.get(
                f"/teams/{int(team_id)}",
                params=params or None,
                min_api_version=TEAMS_MIN_API_VERSION,
            )
        )

    def _team_page(
        self,
        model: type[T],
        path: str,
        *,
        limit: int | None,
        total_count: bool,
        cursor: str | None,
    ) -> PaginatedResponse[T]:
        check_cursor_alone(cursor, limit, total_count)
        if cursor is not None:
            return page_of(
                model, self._client.get_url(cursor, min_api_version=TEAMS_MIN_API_VERSION)
            )
        return page_of(
            model,
            self._client.get(
                path,
                params=team_page_params(limit, total_count) or None,
                min_api_version=TEAMS_MIN_API_VERSION,
            ),
        )

    def list_members(
        self,
        team_id: int,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[TeamMember]:
        """One page of a team's members (V2, beta; API 2026-09-17+): every member, where
        ``get(includes=True)`` gives only the first ones. ``limit`` is 1-100."""
        return self._team_page(
            TeamMember,
            f"/teams/{int(team_id)}/members",
            limit=limit,
            total_count=total_count,
            cursor=cursor,
        )

    def iter_members(self, team_id: int) -> Iterator[TeamMember]:
        """Every member of a team (all pages); see :meth:`list_members`."""
        return PageIterator(
            lambda c: (
                self.list_members(team_id, cursor=c) if c else self.list_members(team_id, limit=100)
            )
        )

    def list_accessible_lists(
        self,
        team_id: int,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListSummary]:
        """One page of the lists a team has access to (V2, beta; API 2026-09-17+). ``limit`` is
        1-100."""
        return self._team_page(
            ListSummary,
            f"/teams/{int(team_id)}/accessible-lists",
            limit=limit,
            total_count=total_count,
            cursor=cursor,
        )

    def iter_accessible_lists(self, team_id: int) -> Iterator[ListSummary]:
        """Every list a team has access to (all pages); see :meth:`list_accessible_lists`."""
        return PageIterator(
            lambda c: (
                self.list_accessible_lists(team_id, cursor=c)
                if c
                else self.list_accessible_lists(team_id, limit=100)
            )
        )


class AsyncTeamService:
    """Async teams; see :class:`TeamService`."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        ids: Sequence[int] | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        order_by: str | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Team]:
        """One page of teams; see :meth:`TeamService.list`."""
        check_cursor_alone(cursor, ids, name, name_contains, order_by, includes, limit, total_count)
        if cursor is not None:
            return page_of(
                Team, await self._client.get_url(cursor, min_api_version=TEAMS_MIN_API_VERSION)
            )
        params = team_params(
            ids=ids,
            name=name,
            name_contains=name_contains,
            order_by=order_by,
            includes=includes,
            limit=limit,
            total_count=total_count,
        )
        return page_of(
            Team,
            await self._client.get(
                "/teams", params=params or None, min_api_version=TEAMS_MIN_API_VERSION
            ),
        )

    def iter(
        self,
        *,
        ids: Sequence[int] | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        order_by: str | None = None,
        includes: bool | Sequence[str] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Team]:
        """All teams matching the filters (every page); see :meth:`list`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[Team]:
            if next_url:
                return await self.list(cursor=next_url)
            return await self.list(
                ids=ids,
                name=name,
                name_contains=name_contains,
                order_by=order_by,
                includes=includes,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)

    async def get(self, team_id: int, *, includes: bool | Sequence[str] | None = None) -> Team:
        """One team; see :meth:`TeamService.get`."""
        params = [("includes", v) for v in team_includes(includes)]
        return Team.model_validate(
            await self._client.get(
                f"/teams/{int(team_id)}",
                params=params or None,
                min_api_version=TEAMS_MIN_API_VERSION,
            )
        )

    async def _team_page(
        self,
        model: type[T],
        path: str,
        *,
        limit: int | None,
        total_count: bool,
        cursor: str | None,
    ) -> PaginatedResponse[T]:
        check_cursor_alone(cursor, limit, total_count)
        if cursor is not None:
            return page_of(
                model, await self._client.get_url(cursor, min_api_version=TEAMS_MIN_API_VERSION)
            )
        return page_of(
            model,
            await self._client.get(
                path,
                params=team_page_params(limit, total_count) or None,
                min_api_version=TEAMS_MIN_API_VERSION,
            ),
        )

    async def list_members(
        self,
        team_id: int,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[TeamMember]:
        """One page of a team's members; see :meth:`TeamService.list_members`."""
        return await self._team_page(
            TeamMember,
            f"/teams/{int(team_id)}/members",
            limit=limit,
            total_count=total_count,
            cursor=cursor,
        )

    def iter_members(self, team_id: int) -> AsyncIterator[TeamMember]:
        """Every member of a team (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[TeamMember]:
            if c:
                return await self.list_members(team_id, cursor=c)
            return await self.list_members(team_id, limit=100)

        return AsyncPageIterator(fetch)

    async def list_accessible_lists(
        self,
        team_id: int,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[ListSummary]:
        """One page of a team's accessible lists; see
        :meth:`TeamService.list_accessible_lists`."""
        return await self._team_page(
            ListSummary,
            f"/teams/{int(team_id)}/accessible-lists",
            limit=limit,
            total_count=total_count,
            cursor=cursor,
        )

    def iter_accessible_lists(self, team_id: int) -> AsyncIterator[ListSummary]:
        """Every list a team has access to (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[ListSummary]:
            if c:
                return await self.list_accessible_lists(team_id, cursor=c)
            return await self.list_accessible_lists(team_id, limit=100)

        return AsyncPageIterator(fetch)


# --- users --------------------------------------------------------------------------------------


class UserService:
    """The organization's internal users (V2, beta)."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        term: str | None = None,
        ids: Sequence[UserId] | None = None,
        status: UserStatus | str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[UserAccount]:
        """One page of users. ``term`` matches first name, last name or primary email;
        ``status`` is active, invited or deactivated. ``limit`` is 1-100."""
        check_cursor_alone(cursor, term, ids, status, limit)
        if cursor is not None:
            return page_of(UserAccount, self._client.get_url(cursor))
        params = user_params(term=term, ids=ids, status=status, limit=limit)
        return page_of(UserAccount, self._client.get("/users", params=params or None))

    def iter(
        self,
        *,
        term: str | None = None,
        ids: Sequence[UserId] | None = None,
        status: UserStatus | str | None = None,
        limit: int | None = None,
    ) -> Iterator[UserAccount]:
        """All users matching the filters (every page); see :meth:`list`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[UserAccount]:
            if next_url:
                return self.list(cursor=next_url)
            return self.list(term=term, ids=ids, status=status, limit=limit)

        return PageIterator(fetch_page)

    def get(self, user_id: UserId) -> UserAccount:
        """One user (the id is also the user's person id)."""
        return UserAccount.model_validate(self._client.get(f"/users/{int(user_id)}"))


class AsyncUserService:
    """Async users; see :class:`UserService`."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        term: str | None = None,
        ids: Sequence[UserId] | None = None,
        status: UserStatus | str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[UserAccount]:
        """One page of users; see :meth:`UserService.list`."""
        check_cursor_alone(cursor, term, ids, status, limit)
        if cursor is not None:
            return page_of(UserAccount, await self._client.get_url(cursor))
        params = user_params(term=term, ids=ids, status=status, limit=limit)
        return page_of(UserAccount, await self._client.get("/users", params=params or None))

    def iter(
        self,
        *,
        term: str | None = None,
        ids: Sequence[UserId] | None = None,
        status: UserStatus | str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[UserAccount]:
        """All users matching the filters (every page); see :meth:`list`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[UserAccount]:
            if next_url:
                return await self.list(cursor=next_url)
            return await self.list(term=term, ids=ids, status=status, limit=limit)

        return AsyncPageIterator(fetch_page)

    async def get(self, user_id: UserId) -> UserAccount:
        """One user; see :meth:`UserService.get`."""
        return UserAccount.model_validate(await self._client.get(f"/users/{int(user_id)}"))


# --- feedback -----------------------------------------------------------------------------------


class FeedbackService:
    """Feedback to Affinity's product team about the API, the MCP server, or anything else."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def send(self, kind: FeedbackType, subject: str, body: str) -> None:
        """Send feedback to Affinity. ``kind`` is ``"api"``, ``"mcp"`` or ``"other"``; ``subject``
        up to 255 characters, ``body`` up to 10,000. It reaches Affinity's team: be specific."""
        self._client.post("/feedback", json=feedback_body(kind, subject, body))


class AsyncFeedbackService:
    """Async feedback; see :class:`FeedbackService`."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def send(self, kind: FeedbackType, subject: str, body: str) -> None:
        """Send feedback to Affinity; see :meth:`FeedbackService.send`."""
        await self._client.post("/feedback", json=feedback_body(kind, subject, body))
