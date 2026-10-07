"""
V2 search: note keyword search, file keyword search and company semantic search.

The endpoints are ``POST`` but only read, so they are sent as read-only POSTs: allowed
under ``WritePolicy.DENY`` (CLI ``--readonly``) and retried like a GET on 429 / 5xx /
transport errors.

They are exposed on the existing services through the mixins below:

- ``client.notes.search(prompt, ...)`` -> ``list[NoteSearchResult]``
- ``client.files.search(prompt, ...)`` -> ``list[FileSearchResult]``
- ``client.companies.semantic_search(prompt, ...)`` -> ``SemanticSearchResult``

Arguments are checked client-side against the API's documented limits so mistakes fail
before a request is spent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..models.search import FileSearchResult, NoteSearchResult, SemanticSearchResult
from ..types import CompanyId, FileId, ListId, NoteId

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient

# API limits (V2 OpenAPI: notes/files KeywordSearchCriteria, SemanticSearchCriteria).
KEYWORD_PROMPT_MIN = 3
SEMANTIC_PROMPT_MIN = 1
PROMPT_MAX = 500
LIMIT_MIN = 1
LIMIT_MAX = 100
MAX_IDS = 100

# Server-side defaults when no limit is sent (documented, not sent).
NOTE_SEARCH_DEFAULT_LIMIT = 20
FILE_SEARCH_DEFAULT_LIMIT = 20
SEMANTIC_SEARCH_DEFAULT_LIMIT = 100

_NOTES_PATH = "/notes/search"
_FILES_PATH = "/files/search"
_SEMANTIC_PATH = "/semantic-search"


def _check_prompt(prompt: str, *, min_len: int) -> None:
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if len(prompt) < min_len:
        raise ValueError(f"prompt must be at least {min_len} characters (got {len(prompt)})")
    if len(prompt) > PROMPT_MAX:
        raise ValueError(f"prompt must be at most {PROMPT_MAX} characters (got {len(prompt)})")


def _check_limit(limit: int | None) -> None:
    if limit is None:
        return
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if not LIMIT_MIN <= limit <= LIMIT_MAX:
        raise ValueError(f"limit must be between {LIMIT_MIN} and {LIMIT_MAX} (got {limit})")


def _check_ids(ids: Sequence[int], *, name: str) -> list[int]:
    if isinstance(ids, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of integers")
    values = [int(i) for i in ids]
    if not values:
        raise ValueError(f"{name} must not be empty (omit it to search everything)")
    if len(values) > MAX_IDS:
        raise ValueError(f"{name} accepts at most {MAX_IDS} ids (got {len(values)})")
    if any(v < 1 for v in values):
        raise ValueError(f"{name} must contain positive ids")
    return values


def build_keyword_search_body(
    prompt: str,
    *,
    company_id: int | None,
    ids: Sequence[int] | None,
    ids_key: str,
    limit: int | None,
) -> dict[str, Any]:
    """Validate arguments and build the body for ``/notes/search`` or ``/files/search``.

    ``ids_key`` is ``"noteIds"`` or ``"fileIds"``. ``company_id`` and ``ids`` are mutually
    exclusive (the API rejects both together).
    """
    _check_prompt(prompt, min_len=KEYWORD_PROMPT_MIN)
    _check_limit(limit)
    if company_id is not None and ids is not None:
        raise ValueError(f"company_id and {ids_key} are mutually exclusive")
    body: dict[str, Any] = {"prompt": prompt}
    if company_id is not None:
        if int(company_id) < 1:
            raise ValueError("company_id must be a positive id")
        body["companyId"] = int(company_id)
    if ids is not None:
        body[ids_key] = _check_ids(ids, name=ids_key)
    if limit is not None:
        body["limit"] = limit
    return body


def build_semantic_search_body(
    prompt: str,
    *,
    list_ids: Sequence[int] | None,
    limit: int | None,
) -> dict[str, Any]:
    """Validate arguments and build the body for ``/semantic-search`` (companies only)."""
    _check_prompt(prompt, min_len=SEMANTIC_PROMPT_MIN)
    _check_limit(limit)
    body: dict[str, Any] = {"prompt": prompt, "entityType": "companies"}
    if list_ids is not None:
        body["listIds"] = _check_ids(list_ids, name="listIds")
    if limit is not None:
        body["limit"] = limit
    return body


def _note_results(payload: dict[str, Any]) -> list[NoteSearchResult]:
    return [NoteSearchResult.model_validate(item) for item in payload.get("data") or []]


def _file_results(payload: dict[str, Any]) -> list[FileSearchResult]:
    return [FileSearchResult.model_validate(item) for item in payload.get("data") or []]


# =============================================================================
# Sync mixins
# =============================================================================


class NoteSearchMixin:
    """Adds ``search`` (V2 keyword search) to the note service."""

    _client: HTTPClient

    def search(
        self,
        prompt: str,
        *,
        company_id: CompanyId | int | None = None,
        note_ids: Sequence[NoteId | int] | None = None,
        limit: int | None = None,
    ) -> list[NoteSearchResult]:
        """
        Search notes by keyword (``POST /v2/notes/search``).

        Args:
            prompt: Search text, 3-500 characters.
            company_id: Only notes associated with this company.
            note_ids: Only these notes (at most 100). Exclusive with ``company_id``.
            limit: Maximum results, 1-100 (API default 20).

        Returns:
            Hits ordered by relevance; each has ``note.id``, ``note.kind`` and ``preview``
            (the matching passage). Fetch the full note with ``notes.get(note.id)``.

        Raises:
            ValueError: An argument is outside the API's limits.
        """
        body = build_keyword_search_body(
            prompt, company_id=company_id, ids=note_ids, ids_key="noteIds", limit=limit
        )
        return _note_results(self._client.post(_NOTES_PATH, json=body, read_only=True))


class FileSearchMixin:
    """Adds ``search`` (V2 keyword search over file contents) to the file service."""

    _client: HTTPClient

    def search(
        self,
        prompt: str,
        *,
        company_id: CompanyId | int | None = None,
        file_ids: Sequence[FileId | int] | None = None,
        limit: int | None = None,
    ) -> list[FileSearchResult]:
        """
        Search file contents by keyword (``POST /v2/files/search``).

        Args:
            prompt: Search text, 3-500 characters.
            company_id: Only files associated with this company.
            file_ids: Only these files (at most 100). Exclusive with ``company_id``.
            limit: Maximum results, 1-100 (API default 20).

        Returns:
            Hits ordered by relevance, one per file; each has ``file.id``, ``file.name``,
            ``page_number`` (None for files without pages) and ``preview``.

        Raises:
            ValueError: An argument is outside the API's limits.
        """
        body = build_keyword_search_body(
            prompt, company_id=company_id, ids=file_ids, ids_key="fileIds", limit=limit
        )
        return _file_results(self._client.post(_FILES_PATH, json=body, read_only=True))


class CompanySemanticSearchMixin:
    """Adds ``semantic_search`` (V2 natural-language company search)."""

    _client: HTTPClient

    def semantic_search(
        self,
        prompt: str,
        *,
        list_ids: Sequence[ListId | int] | None = None,
        limit: int | None = None,
    ) -> SemanticSearchResult:
        """
        Find companies from a natural-language description (``POST /v2/semantic-search``).

        Unlike ``search`` (name / domain match), this interprets the prompt, for example
        "fintech companies in the US with over 50 employees".

        Args:
            prompt: Description, 1-500 characters.
            list_ids: Only companies on these company lists (at most 100).
            limit: Maximum results, 1-100 (API default 100).

        Returns:
            ``SemanticSearchResult`` with ``explanation`` (how the API read the prompt)
            and ``data`` (companies with a relevance ``score``).

        Raises:
            ValueError: An argument is outside the API's limits.
        """
        body = build_semantic_search_body(prompt, list_ids=list_ids, limit=limit)
        payload = self._client.post(_SEMANTIC_PATH, json=body, read_only=True)
        return SemanticSearchResult.model_validate(payload)


# =============================================================================
# Async mixins
# =============================================================================


class AsyncNoteSearchMixin:
    """Async ``search`` for the note service. See :meth:`NoteSearchMixin.search`."""

    _client: AsyncHTTPClient

    async def search(
        self,
        prompt: str,
        *,
        company_id: CompanyId | int | None = None,
        note_ids: Sequence[NoteId | int] | None = None,
        limit: int | None = None,
    ) -> list[NoteSearchResult]:
        """Search notes by keyword (``POST /v2/notes/search``)."""
        body = build_keyword_search_body(
            prompt, company_id=company_id, ids=note_ids, ids_key="noteIds", limit=limit
        )
        return _note_results(await self._client.post(_NOTES_PATH, json=body, read_only=True))


class AsyncFileSearchMixin:
    """Async ``search`` for the file service. See :meth:`FileSearchMixin.search`."""

    _client: AsyncHTTPClient

    async def search(
        self,
        prompt: str,
        *,
        company_id: CompanyId | int | None = None,
        file_ids: Sequence[FileId | int] | None = None,
        limit: int | None = None,
    ) -> list[FileSearchResult]:
        """Search file contents by keyword (``POST /v2/files/search``)."""
        body = build_keyword_search_body(
            prompt, company_id=company_id, ids=file_ids, ids_key="fileIds", limit=limit
        )
        return _file_results(await self._client.post(_FILES_PATH, json=body, read_only=True))


class AsyncCompanySemanticSearchMixin:
    """Async ``semantic_search``. See :meth:`CompanySemanticSearchMixin.semantic_search`."""

    _client: AsyncHTTPClient

    async def semantic_search(
        self,
        prompt: str,
        *,
        list_ids: Sequence[ListId | int] | None = None,
        limit: int | None = None,
    ) -> SemanticSearchResult:
        """Find companies from a natural-language description (``POST /v2/semantic-search``)."""
        body = build_semantic_search_body(prompt, list_ids=list_ids, limit=limit)
        payload = await self._client.post(_SEMANTIC_PATH, json=body, read_only=True)
        return SemanticSearchResult.model_validate(payload)
