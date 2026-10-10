"""Models for the org-wide V2 interaction lists: emails, meetings, calls, chat messages.

These are separate from the V1 :class:`~affinity.models.secondary.Interaction` used by
``interactions.list()`` (one entity at a time). Previews of attendees/participants are flattened
into a list plus the total count.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .entities import PersonSummary
from .pagination import AffinityModel
from .types import ISODatetime


class InteractionAttendee(AffinityModel):
    """A participant: an email address, and the Affinity person when one matches."""

    email_address: str | None = Field(None, alias="emailAddress")
    person: PersonSummary | None = None


def _flatten(data: Any, preview_key: str, items_key: str, total_key: str) -> Any:
    if isinstance(data, dict) and isinstance(data.get(preview_key), dict):
        preview = data[preview_key]
        data = {**data, items_key: preview.get("data") or [], total_key: preview.get("totalCount")}
        data.pop(preview_key, None)
    return data


class _Event(AffinityModel):
    id: int
    logging_type: str | None = Field(None, alias="loggingType")
    title: str | None = None
    start_time: ISODatetime = Field(alias="startTime")
    end_time: ISODatetime | None = Field(None, alias="endTime")
    all_day: bool = Field(False, alias="allDay")
    creator: InteractionAttendee | None = None
    created_at: ISODatetime = Field(alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")
    attendees: list[InteractionAttendee] = Field(default_factory=list)
    attendees_total: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_attendees(cls, data: Any) -> Any:
        return _flatten(data, "attendeesPreview", "attendees", "attendees_total")


class CallV2(_Event):
    """A call (V2 ``GET /v2/calls``)."""


class MeetingV2(_Event):
    """A meeting (V2 ``GET /v2/meetings``)."""

    organizer: InteractionAttendee | None = None


class EmailV2(AffinityModel):
    """An email (V2 ``GET /v2/emails``). A subject the user may not see is ``"********"``."""

    id: int
    sent_at: ISODatetime = Field(alias="sentAt")
    logging_type: str | None = Field(None, alias="loggingType")
    direction: str | None = None
    subject: str | None = None
    created_at: ISODatetime = Field(alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")
    from_: InteractionAttendee | None = Field(None, alias="from")
    to: list[InteractionAttendee] = Field(default_factory=list)
    to_total: int | None = None
    cc: list[InteractionAttendee] = Field(default_factory=list)
    cc_total: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_participants(cls, data: Any) -> Any:
        data = _flatten(data, "toParticipantsPreview", "to", "to_total")
        return _flatten(data, "ccParticipantsPreview", "cc", "cc_total")


class ChatMessageV2(AffinityModel):
    """A chat message (V2 ``GET /v2/chat-messages``)."""

    id: int
    sent_at: ISODatetime = Field(alias="sentAt")
    logging_type: str | None = Field(None, alias="loggingType")
    direction: str | None = None
    creator: PersonSummary | None = None
    created_at: ISODatetime = Field(alias="createdAt")
    updated_at: ISODatetime | None = Field(None, alias="updatedAt")
    participants: list[PersonSummary] = Field(default_factory=list)
    participants_total: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_participants(cls, data: Any) -> Any:
        return _flatten(data, "participantsPreview", "participants", "participants_total")
