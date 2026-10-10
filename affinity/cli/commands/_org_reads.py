"""Shared helpers for the org-wide read commands (feed, merge history, relationships)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from affinity.models.entities import PersonSummary
from affinity.models.interactions_v2 import (
    CallV2,
    ChatMessageV2,
    EmailV2,
    InteractionAttendee,
    MeetingV2,
)
from affinity.models.pagination import PaginatedResponse
from affinity.models.relationships_v2 import Relationship, RelationshipPerson

from ..errors import CLIError
from ..serialization import serialize_model_for_cli

# Rows keep the first few participants and the total, so org-wide lists stay small
MAX_PARTICIPANTS = 10


def _name(first: str | None, last: str | None) -> str | None:
    return f"{first or ''} {last or ''}".strip() or None


def _person(person: PersonSummary | None) -> dict[str, object] | None:
    if person is None:
        return None
    return {
        "personId": int(person.id),
        "name": _name(person.first_name, person.last_name),
        "email": person.primary_email,
    }


def _attendee(attendee: InteractionAttendee | None) -> dict[str, object] | None:
    if attendee is None:
        return None
    person = attendee.person
    return {
        "email": attendee.email_address,
        "personId": int(person.id) if person else None,
        "name": _name(person.first_name, person.last_name) if person else None,
    }


def _attendees(items: list[InteractionAttendee]) -> list[dict[str, object] | None]:
    return [_attendee(a) for a in items[:MAX_PARTICIPANTS]]


def interaction_row(item: EmailV2 | MeetingV2 | CallV2 | ChatMessageV2) -> dict[str, object]:
    """One flattened row of the interaction feed."""
    if isinstance(item, EmailV2):
        return {
            "id": item.id,
            "type": "email",
            "sentAt": item.sent_at,
            "direction": item.direction,
            "loggingType": item.logging_type,
            "subject": item.subject,
            "from": _attendee(item.from_),
            "to": _attendees(item.to),
            "toTotal": item.to_total,
            "cc": _attendees(item.cc),
            "ccTotal": item.cc_total,
            "createdAt": item.created_at,
            "updatedAt": item.updated_at,
        }
    if isinstance(item, ChatMessageV2):
        return {
            "id": item.id,
            "type": "chat-message",
            "sentAt": item.sent_at,
            "direction": item.direction,
            "loggingType": item.logging_type,
            "creator": _person(item.creator),
            "participants": [_person(p) for p in item.participants[:MAX_PARTICIPANTS]],
            "participantsTotal": item.participants_total,
            "createdAt": item.created_at,
            "updatedAt": item.updated_at,
        }
    row: dict[str, object] = {
        "id": item.id,
        "type": "meeting" if isinstance(item, MeetingV2) else "call",
        "title": item.title,
        "startTime": item.start_time,
        "endTime": item.end_time,
        "allDay": item.all_day,
        "loggingType": item.logging_type,
        "creator": _attendee(item.creator),
    }
    if isinstance(item, MeetingV2):
        row["organizer"] = _attendee(item.organizer)
    row.update(
        {
            "attendees": _attendees(item.attendees),
            "attendeesTotal": item.attendees_total,
            "createdAt": item.created_at,
            "updatedAt": item.updated_at,
        }
    )
    return row


def _relationship_person(person: RelationshipPerson) -> dict[str, object]:
    return {
        "personId": person.id,
        "name": _name(person.first_name, person.last_name),
        "email": person.primary_email_address,
    }


def relationship_row(item: Relationship) -> dict[str, object]:
    return {
        "person1": _relationship_person(item.person1),
        "person2": _relationship_person(item.person2),
        "interactionScore": item.interaction_score,
        "linkedInConnectedOn": item.linkedin_connected_on,
    }


def model_row(item: Any) -> dict[str, object]:
    return serialize_model_for_cli(item)


def collect_pages(
    first: Callable[[], PaginatedResponse[Any]],
    follow: Callable[[str], PaginatedResponse[Any]],
    row: Callable[[Any], dict[str, object]],
    *,
    max_results: int | None,
    all_pages: bool,
    warnings: list[str],
) -> tuple[list[dict[str, object]], str | None]:
    """Rows from one page, N rows, or every page; and the cursor to resume from (if any)."""
    if max_results is not None and max_results < 1:
        raise CLIError("--max-results must be at least 1.", error_type="usage_error", exit_code=2)
    page = first()
    rows: list[dict[str, object]] = []
    while True:
        rows.extend(row(item) for item in page.data)
        next_cursor = page.next_cursor
        if max_results is not None and len(rows) >= max_results:
            if len(rows) > max_results:
                # Stopped inside a page: its cursor would skip the rest of it
                rows = rows[:max_results]
                next_cursor = None
                warnings.append("Results limited by --max-results. Use --all to fetch all results.")
            return rows, next_cursor
        if not next_cursor or not (all_pages or max_results is not None):
            return rows, next_cursor
        page = follow(next_cursor)


def page_limit(max_results: int | None) -> int | None:
    return min(max_results, 100) if max_results is not None else None


def pagination(next_cursor: str | None) -> dict[str, Any] | None:
    return {"nextCursor": next_cursor, "prevCursor": None} if next_cursor else None
