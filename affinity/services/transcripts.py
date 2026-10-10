"""Meeting transcripts (V2): list, get, fragments."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..models.pagination import (
    AsyncPageIterator,
    PageIterator,
    PaginatedResponse,
    PaginationInfoWithTotal,
)
from ..models.transcripts import Transcript, TranscriptFragment
from ._v2_filters import v2_filter_datetime

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient


def _list_params(
    *,
    created_after: datetime | None,
    created_before: datetime | None,
    limit: int | None,
    total_count: bool,
) -> dict[str, Any]:
    if limit is not None and not 1 <= limit <= 100:
        raise ValueError("'limit' must be between 1 and 100")
    clauses = []
    if created_after is not None:
        clauses.append(f"createdAt>={v2_filter_datetime(created_after, round_up=False)}")
    if created_before is not None:
        clauses.append(f"createdAt<{v2_filter_datetime(created_before, round_up=True)}")
    params: dict[str, Any] = {}
    if clauses:
        params["filter"] = " & ".join(clauses)
    if limit is not None:
        params["limit"] = limit
    if total_count:
        params["totalCount"] = "true"
    return params


def _fragment_params(limit: int | None, total_count: bool) -> dict[str, Any]:
    if limit is not None and not 1 <= limit <= 100:
        raise ValueError("'limit' must be between 1 and 100")
    params: dict[str, Any] = {}
    if limit is not None:
        params["limit"] = limit
    if total_count:
        params["totalCount"] = "true"
    return params


def _transcript_page(data: dict[str, Any]) -> PaginatedResponse[Transcript]:
    return PaginatedResponse[Transcript](
        data=[Transcript.model_validate(item) for item in data.get("data", [])],
        pagination=PaginationInfoWithTotal.model_validate(data.get("pagination", {})),
    )


def _fragment_page(data: dict[str, Any]) -> PaginatedResponse[TranscriptFragment]:
    return PaginatedResponse[TranscriptFragment](
        data=[TranscriptFragment.model_validate(item) for item in data.get("data", [])],
        pagination=PaginationInfoWithTotal.model_validate(data.get("pagination", {})),
    )


def _check_cursor_alone(cursor: str | None, *others: Any) -> None:
    if cursor is not None and any(o not in (None, False) for o in others):
        raise ValueError(
            "Cannot combine 'cursor' with other parameters; cursor encodes all query context."
        )


class TranscriptService:
    """Meeting transcripts made by Affinity's AI Notetaker (V2).

    Only transcripts the API key's user is allowed to see are returned. Transcript content is
    meeting dialogue: handle it like the meeting itself.
    """

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Transcript]:
        """One page of transcripts, metadata only (no fragments).

        ``created_after`` is inclusive and ``created_before`` exclusive, rounded outward to
        whole seconds. ``limit`` is 1-100 (API default 20). ``total_count=True`` asks for the
        number of matching transcripts (``page.total_count``). ``cursor`` (a previous page's
        ``next_cursor``) can't be combined with other arguments.
        """
        _check_cursor_alone(cursor, created_after, created_before, limit, total_count)
        if cursor is not None:
            return _transcript_page(self._client.get_url(cursor))
        params = _list_params(
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            total_count=total_count,
        )
        return _transcript_page(self._client.get("/transcripts", params=params or None))

    def iter(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[Transcript]:
        """Iterate all transcripts (every page; ``limit`` is the page size)."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[Transcript]:
            if next_url:
                return self.list(cursor=next_url)
            return self.list(
                created_after=created_after, created_before=created_before, limit=limit
            )

        return PageIterator(fetch_page)

    def get(self, transcript_id: int) -> Transcript:
        """One transcript with its note and a preview of its first fragments."""
        return Transcript.model_validate(self._client.get(f"/transcripts/{int(transcript_id)}"))

    def fragments(
        self,
        transcript_id: int,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[TranscriptFragment]:
        """One page of a transcript's dialogue fragments, in order."""
        _check_cursor_alone(cursor, limit, total_count)
        if cursor is not None:
            return _fragment_page(self._client.get_url(cursor))
        params = _fragment_params(limit, total_count)
        return _fragment_page(
            self._client.get(f"/transcripts/{int(transcript_id)}/fragments", params=params or None)
        )

    def iter_fragments(
        self, transcript_id: int, *, limit: int | None = None
    ) -> Iterator[TranscriptFragment]:
        """Iterate all of a transcript's fragments (every page)."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[TranscriptFragment]:
            if next_url:
                return self.fragments(transcript_id, cursor=next_url)
            return self.fragments(transcript_id, limit=limit)

        return PageIterator(fetch_page)


class AsyncTranscriptService:
    """Async meeting transcripts (V2). See :class:`TranscriptService`."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Transcript]:
        """One page of transcripts, metadata only. See :meth:`TranscriptService.list`."""
        _check_cursor_alone(cursor, created_after, created_before, limit, total_count)
        if cursor is not None:
            return _transcript_page(await self._client.get_url(cursor))
        params = _list_params(
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            total_count=total_count,
        )
        return _transcript_page(await self._client.get("/transcripts", params=params or None))

    def iter(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Transcript]:
        """Iterate all transcripts (every page)."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[Transcript]:
            if next_url:
                return await self.list(cursor=next_url)
            return await self.list(
                created_after=created_after, created_before=created_before, limit=limit
            )

        return AsyncPageIterator(fetch_page)

    async def get(self, transcript_id: int) -> Transcript:
        """One transcript with its note and a preview of its first fragments."""
        return Transcript.model_validate(
            await self._client.get(f"/transcripts/{int(transcript_id)}")
        )

    async def fragments(
        self,
        transcript_id: int,
        *,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[TranscriptFragment]:
        """One page of a transcript's dialogue fragments, in order."""
        _check_cursor_alone(cursor, limit, total_count)
        if cursor is not None:
            return _fragment_page(await self._client.get_url(cursor))
        params = _fragment_params(limit, total_count)
        return _fragment_page(
            await self._client.get(
                f"/transcripts/{int(transcript_id)}/fragments", params=params or None
            )
        )

    def iter_fragments(
        self, transcript_id: int, *, limit: int | None = None
    ) -> AsyncIterator[TranscriptFragment]:
        """Iterate all of a transcript's fragments (every page)."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[TranscriptFragment]:
            if next_url:
                return await self.fragments(transcript_id, cursor=next_url)
            return await self.fragments(transcript_id, limit=limit)

        return AsyncPageIterator(fetch_page)
