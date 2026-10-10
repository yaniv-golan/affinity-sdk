"""Models for V2 meeting transcripts (AI Notetaker)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .pagination import AffinityModel
from .secondary import NoteV2
from .types import ISODatetime


class TranscriptFragment(AffinityModel):
    """One piece of dialogue. Timestamps are offsets from the start, e.g. ``"00:00:06"``."""

    content: str
    speaker: str | None = None
    start_timestamp: str | None = Field(None, alias="startTimestamp")
    end_timestamp: str | None = Field(None, alias="endTimestamp")


class Transcript(AffinityModel):
    """A meeting transcript.

    ``note`` is the AI Notetaker note it belongs to (with ``interaction``, the meeting).
    ``fragments_preview`` holds the first fragments (only from ``transcripts.get()``; the list
    endpoint returns metadata only) and ``fragments_total`` how many there are in all; use
    ``transcripts.fragments()`` / ``iter_fragments()`` for the rest.
    """

    id: int
    created_at: ISODatetime = Field(alias="createdAt")
    language_code: str | None = Field(None, alias="languageCode")
    note: NoteV2 | None = None
    fragments_preview: list[TranscriptFragment] = Field(default_factory=list)
    fragments_total: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_preview(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("fragmentsPreview"), dict):
            preview = data["fragmentsPreview"]
            data = {
                **data,
                "fragments_preview": preview.get("data") or [],
                "fragments_total": preview.get("totalCount"),
            }
            data.pop("fragmentsPreview", None)
        return data
