"""
Models for the V2 search endpoints.

- ``POST /v2/notes/search`` -> :class:`NoteSearchResult`
- ``POST /v2/files/search`` -> :class:`FileSearchResult`
- ``POST /v2/semantic-search`` -> :class:`SemanticSearchResult`
"""

from __future__ import annotations

from pydantic import Field

from .entities import AffinityModel
from .types import CompanyId, FileId, NoteId, NoteKind


class NoteRef(AffinityModel):
    """The note matched by a note search (``note`` in a result)."""

    id: NoteId
    # Open enum: an unknown kind is kept as-is instead of failing validation.
    kind: NoteKind | None = None


class NoteSearchResult(AffinityModel):
    """One note search hit: the note and its best-matching passage."""

    note: NoteRef
    # The matching passage (up to 2,000 characters; often the full note text).
    preview: str = ""


class FileRef(AffinityModel):
    """The file matched by a file search (``file`` in a result)."""

    id: FileId
    name: str = ""


class FileSearchResult(AffinityModel):
    """One file search hit: the file, the page of the passage, and the passage."""

    file: FileRef
    # Null for files without page structure.
    page_number: int | None = Field(None, alias="pageNumber")
    preview: str = ""


class SemanticCompany(AffinityModel):
    """A company returned by semantic search."""

    id: CompanyId
    name: str = ""
    domain: str | None = None
    domains: list[str] = Field(default_factory=list)
    is_global: bool = Field(False, alias="isGlobal")
    # Raw relevance score as sent by the API (a decimal string such as "0.85").
    score: str | None = None

    @property
    def score_float(self) -> float | None:
        """The score as a float, or None when missing or not a number."""
        if self.score is None:
            return None
        try:
            return float(self.score)
        except (TypeError, ValueError):
            return None


class SemanticSearchResult(AffinityModel):
    """Semantic search response: matches plus the API's explanation of the search."""

    explanation: str | None = None
    entity_type: str = Field("companies", alias="entityType")
    data: list[SemanticCompany] = Field(default_factory=list)
