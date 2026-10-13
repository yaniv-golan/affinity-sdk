"""Utilities for transforming and formatting interaction date data.

Supports `--expand interactions` for list export command.
This module provides:
- Transform functions to convert raw API data to a consistent shape
- CSV column definitions and flattening for export
- Person name resolution caching
- Unreplied message detection (email and chat)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from affinity.exceptions import MergedEntityError, NotFoundError

from .async_utils import gather_or_raise

if TYPE_CHECKING:
    from affinity import Affinity, AsyncAffinity
    from affinity.models.entities import InteractionDates, Interactions

logger = logging.getLogger(__name__)

# The only errors a lookup reads as a fallback ("Person <id>", no unreplied message): the
# record is gone (deleted, or merged into another). Any other error propagates; a fallback
# would pass a failed lookup off as data.
LOOKUP_GONE_ERRORS: tuple[type[Exception], ...] = (NotFoundError, MergedEntityError)

# =============================================================================
# CSV Column Definitions
# =============================================================================

# Column names for flat CSV mode (order matters - must match flatten_interactions_for_csv)
INTERACTION_CSV_COLUMNS: list[str] = [
    "lastMeetingDate",
    "lastMeetingDaysSince",
    "lastMeetingTeamMembers",
    "nextMeetingDate",
    "nextMeetingDaysUntil",
    "nextMeetingTeamMembers",
    "lastEmailDate",
    "lastEmailDaysSince",
    "lastInteractionDate",
    "lastInteractionDaysSince",
]

# Additional columns when --check-unreplied is used
UNREPLIED_CSV_COLUMNS: list[str] = [
    "unrepliedDate",
    "unrepliedDaysSince",
    "unrepliedType",
    "unrepliedSubject",
]


# =============================================================================
# Transform Functions
# =============================================================================


def transform_interaction_data(
    interaction_dates: InteractionDates | None,
    interactions: Interactions | None,
    *,
    client: Affinity | None = None,
    person_name_cache: dict[int, str] | None = None,
) -> dict[str, Any] | None:
    """Transform interaction data into a structured dict.

    Combines data from both `interaction_dates` (date values) and `interactions`
    (detailed data with person_ids) into a unified structure suitable for
    JSON output and CSV flattening.

    Args:
        interaction_dates: Parsed InteractionDates object from entity
        interactions: Interactions model with person IDs
        client: Optional Affinity client for resolving person IDs to names
        person_name_cache: Optional cache dict for resolved person names.
            Will be mutated to store resolved names. Thread-safe under CPython GIL.

    Returns:
        Transformed dict with structured interaction data, or None if no data.

    Example output:
        {
            "lastMeeting": {
                "date": "2026-01-10T10:00:00Z",
                "daysSince": 7,
                "teamMemberIds": [1, 2],
                "teamMemberNames": ["John Doe", "Jane Smith"],
            },
            "nextMeeting": { ... },
            "lastEmail": { ... },
            "lastInteraction": { ... },
        }
    """
    if interaction_dates is None:
        return None

    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {}

    # Last meeting (last_event)
    if interaction_dates.last_event_date:
        meeting_data: dict[str, Any] = {
            "date": _format_datetime(interaction_dates.last_event_date),
            "daysSince": _days_since(interaction_dates.last_event_date, now),
        }
        # Add team member data from interactions
        if interactions and interactions.last_event:
            person_ids = interactions.last_event.person_ids
            meeting_data["teamMemberIds"] = person_ids
            if client and person_ids:
                meeting_data["teamMemberNames"] = _resolve_person_names(
                    client, person_ids, person_name_cache
                )
        result["lastMeeting"] = meeting_data

    # Next meeting (next_event)
    if interaction_dates.next_event_date:
        meeting_data = {
            "date": _format_datetime(interaction_dates.next_event_date),
            "daysUntil": _days_until(interaction_dates.next_event_date, now),
        }
        if interactions and interactions.next_event:
            person_ids = interactions.next_event.person_ids
            meeting_data["teamMemberIds"] = person_ids
            if client and person_ids:
                meeting_data["teamMemberNames"] = _resolve_person_names(
                    client, person_ids, person_name_cache
                )
        result["nextMeeting"] = meeting_data

    # Last email
    if interaction_dates.last_email_date:
        email_data: dict[str, Any] = {
            "date": _format_datetime(interaction_dates.last_email_date),
            "daysSince": _days_since(interaction_dates.last_email_date, now),
        }
        if interactions and interactions.last_email:
            person_ids = interactions.last_email.person_ids
            email_data["teamMemberIds"] = person_ids
            if client and person_ids:
                email_data["teamMemberNames"] = _resolve_person_names(
                    client, person_ids, person_name_cache
                )
        result["lastEmail"] = email_data

    # Last interaction (any type)
    if interaction_dates.last_interaction_date:
        result["lastInteraction"] = {
            "date": _format_datetime(interaction_dates.last_interaction_date),
            "daysSince": _days_since(interaction_dates.last_interaction_date, now),
        }

    return result if result else None


def flatten_interactions_for_csv(interactions: dict[str, Any] | None) -> dict[str, str]:
    """Flatten nested interaction data for CSV columns.

    Returns dict with all INTERACTION_CSV_COLUMNS keys (empty strings if no data).

    Args:
        interactions: Transformed interaction data from transform_interaction_data()

    Returns:
        Dict with string values for each CSV column.
    """
    # Initialize all columns to empty string
    result: dict[str, str] = dict.fromkeys(INTERACTION_CSV_COLUMNS, "")

    if not interactions:
        return result

    # Last meeting
    if "lastMeeting" in interactions:
        last_meeting = interactions["lastMeeting"]
        result["lastMeetingDate"] = last_meeting.get("date", "")
        days_since = last_meeting.get("daysSince")
        result["lastMeetingDaysSince"] = str(days_since) if days_since is not None else ""
        team_names = last_meeting.get("teamMemberNames", [])
        result["lastMeetingTeamMembers"] = ", ".join(team_names) if team_names else ""

    # Next meeting
    if "nextMeeting" in interactions:
        next_meeting = interactions["nextMeeting"]
        result["nextMeetingDate"] = next_meeting.get("date", "")
        days_until = next_meeting.get("daysUntil")
        result["nextMeetingDaysUntil"] = str(days_until) if days_until is not None else ""
        team_names = next_meeting.get("teamMemberNames", [])
        result["nextMeetingTeamMembers"] = ", ".join(team_names) if team_names else ""

    # Last email
    if "lastEmail" in interactions:
        last_email = interactions["lastEmail"]
        result["lastEmailDate"] = last_email.get("date", "")
        days_since = last_email.get("daysSince")
        result["lastEmailDaysSince"] = str(days_since) if days_since is not None else ""

    # Last interaction
    if "lastInteraction" in interactions:
        last_interaction = interactions["lastInteraction"]
        result["lastInteractionDate"] = last_interaction.get("date", "")
        days_since = last_interaction.get("daysSince")
        result["lastInteractionDaysSince"] = str(days_since) if days_since is not None else ""

    return result


# =============================================================================
# Helper Functions
# =============================================================================


def _format_datetime(dt: datetime | None) -> str:
    """Format datetime as ISO 8601 string."""
    if dt is None:
        return ""
    return dt.isoformat()


def _days_since(dt: datetime, now: datetime) -> int:
    """Calculate days since a datetime (positive if in past)."""
    diff = now - dt
    return max(0, diff.days)


def _days_until(dt: datetime, now: datetime) -> int:
    """Calculate days until a datetime (positive if in future)."""
    diff = dt - now
    return max(0, diff.days)


def _resolve_person_names(
    client: Affinity,
    person_ids: list[int],
    cache: dict[int, str] | None = None,
) -> list[str]:
    """Resolve person IDs to names, using cache when available.

    Args:
        client: Affinity client for API calls
        person_ids: List of person IDs to resolve
        cache: Optional dict cache (mutated in place). Thread-safe under CPython GIL.

    Returns:
        List of person names in same order as input IDs.
        Uses "Person {id}" for a person that is gone (LOOKUP_GONE_ERRORS); other errors
        propagate.
    """
    if cache is None:
        cache = {}

    names: list[str] = []
    for pid in person_ids:
        if pid in cache:
            names.append(cache[pid])
            continue

        try:
            # Fetch person name from API
            from affinity.types import PersonId

            person = client.persons.get(PersonId(pid))
            name = person.full_name or f"Person {pid}"
            cache[pid] = name
            names.append(name)
        except LOOKUP_GONE_ERRORS:
            # Cache the fallback to avoid repeated lookups
            cache[pid] = f"Person {pid}"
            names.append(f"Person {pid}")

    return names


async def _resolve_person_names_async(
    client: AsyncAffinity,
    person_ids: list[int],
    cache: dict[int, str] | None = None,
    *,
    person_semaphore: asyncio.Semaphore | None = None,
) -> list[str]:
    """Resolve person IDs to names asynchronously, using cache when available.

    Person fetches run in parallel with bounded concurrency via a SHARED semaphore.

    Args:
        client: AsyncAffinity client for async API calls
        person_ids: List of person IDs to resolve
        cache: Optional dict cache (mutated in place). Thread-safe under CPython GIL.
        person_semaphore: Optional SHARED semaphore for bounded concurrent fetches.
            IMPORTANT: Pass the same semaphore across all calls to limit total
            concurrent person API calls. Creating a new semaphore per call defeats
            the bounded concurrency purpose.

    Returns:
        List of person names in same order as input IDs.
        Uses "Person {id}" for a person that is gone (LOOKUP_GONE_ERRORS); other errors
        propagate.
    """
    if cache is None:
        cache = {}

    # NOTE: Benign race possible - two tasks may both see same ID as uncached
    # before either updates cache. Result: duplicate fetch, correct final state.
    uncached_ids = [pid for pid in person_ids if pid not in cache]

    if uncached_ids:
        # Use SHARED semaphore from caller, or create local fallback (for backwards compat)
        sem = person_semaphore or asyncio.Semaphore(10)

        async def fetch_person(pid: int) -> None:
            async with sem:
                try:
                    from affinity.types import PersonId

                    person = await client.persons.get(PersonId(pid))
                    name = person.full_name or f"Person {pid}"
                    cache[pid] = name
                except LOOKUP_GONE_ERRORS:
                    # Cache the fallback to avoid repeated lookups
                    cache[pid] = f"Person {pid}"

        # PERF: Parallelize person fetches with bounded concurrency
        await gather_or_raise(*[fetch_person(pid) for pid in uncached_ids])

    # Return names in original order
    return [cache.get(pid, f"Person {pid}") for pid in person_ids]


# PERF: section_iteration_boundary
async def resolve_interaction_names_async(
    client: AsyncAffinity,
    interaction_data: dict[str, Any] | None,
    cache: dict[int, str] | None = None,
    *,
    person_semaphore: asyncio.Semaphore | None = None,
) -> None:
    """Resolve teamMemberNames in transformed interaction data asynchronously.

    Mutates the interaction_data dict in place, adding teamMemberNames
    to any section that has teamMemberIds.

    Sections (lastMeeting, nextMeeting, lastEmail) are resolved in parallel.
    Person fetches within each section use a SHARED semaphore for bounded concurrency.

    Args:
        client: AsyncAffinity client for async API calls
        interaction_data: Transformed interaction data from transform_interaction_data()
        cache: Optional dict cache for person names (mutated in place)
        person_semaphore: Optional SHARED semaphore for bounded person resolution.
            If not provided, a local semaphore is created (not recommended for
            multi-record expansion - pass shared semaphore from caller).
    """
    if interaction_data is None:
        return

    if cache is None:
        cache = {}

    async def resolve_section(section_key: str) -> None:
        """Resolve person names for a single section."""
        section = interaction_data.get(section_key)
        if section and "teamMemberIds" in section:
            person_ids = section["teamMemberIds"]
            if person_ids:
                section["teamMemberNames"] = await _resolve_person_names_async(
                    client, person_ids, cache, person_semaphore=person_semaphore
                )

    # PERF: Parallelize over sections (lastMeeting, nextMeeting, lastEmail)
    await gather_or_raise(
        *[resolve_section(key) for key in ("lastMeeting", "nextMeeting", "lastEmail")]
    )


# =============================================================================
# Unreplied Message Detection (Email and Chat)
# =============================================================================

# Interaction types that have INCOMING/OUTGOING direction semantics
# MEETING and CALL don't have direction - they're synchronous events
from affinity.models.types import InteractionType

DIRECTIONAL_TYPES: frozenset[InteractionType] = frozenset(
    {InteractionType.EMAIL, InteractionType.CHAT_MESSAGE}
)

# User-friendly type names for output
_TYPE_NAMES: dict[InteractionType, str] = {
    InteractionType.EMAIL: "email",
    InteractionType.CHAT_MESSAGE: "chat",
}


def check_unreplied(
    client: Affinity,
    entity_type: str | int,
    entity_id: int,
    *,
    interaction_types: list[InteractionType] | None = None,
    lookback_days: int = 30,
) -> dict[str, Any] | None:
    """Check for unreplied incoming messages for an entity.

    Supports person, company, and opportunity entity types.
    Also handles V1 integer entityType formats (0=person, 1=company).

    Cross-type reply detection: if they emailed and you replied via chat
    (or vice versa), that counts as a reply.

    Args:
        client: Affinity client for API calls
        entity_type: "company", "person", "opportunity" (or V1 integers 0, 1)
        entity_id: The entity ID
        interaction_types: Types to check for unreplied messages.
            Default: [EMAIL, CHAT_MESSAGE] (both).
            Only EMAIL and CHAT_MESSAGE are valid (have direction).
        lookback_days: Number of days to look back (default 30)

    Returns:
        Dict with unreplied message info if found, None otherwise (also when the entity is
        gone: LOOKUP_GONE_ERRORS). Any other error propagates.
        Example: {
            "date": "2026-01-10T10:00:00Z",
            "daysSince": 5,
            "type": "email",
            "subject": "Following up on our conversation",  # None for chat
        }
    """
    from affinity.models.types import InteractionDirection
    from affinity.types import CompanyId, OpportunityId, PersonId

    # Default to checking both email and chat
    if interaction_types is None:
        interaction_types = [InteractionType.EMAIL, InteractionType.CHAT_MESSAGE]

    # Filter to only directional types
    types_to_check = frozenset(interaction_types) & DIRECTIONAL_TYPES
    if not types_to_check:
        return None

    try:
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(days=lookback_days)

        # Build entity-specific filter kwargs
        # Handle V1 (integer) and V2 (string) entityType formats
        iter_kwargs: dict[str, Any] = {
            "start_time": start_time,
            "end_time": now,
        }

        if entity_type in ("company", 1, "organization"):
            iter_kwargs["company_id"] = CompanyId(entity_id)
        elif entity_type in ("person", 0):
            iter_kwargs["person_id"] = PersonId(entity_id)
        elif entity_type == "opportunity":
            iter_kwargs["opportunity_id"] = OpportunityId(entity_id)
        else:
            logger.debug(f"Unsupported entity type for unreplied check: {entity_type}")
            return None

        # Fetch interactions per directional type (V1 API requires type parameter)
        # We make separate calls for EMAIL and CHAT_MESSAGE, then combine results
        directional: list[Any] = []
        for itype in types_to_check:
            type_kwargs = {**iter_kwargs, "type": itype}
            directional.extend(client.interactions.iter(**type_kwargs))

        if not directional:
            return None

        # Sort by date descending (most recent first)
        directional.sort(key=lambda i: i.date, reverse=True)

        # Find the most recent incoming message matching the requested types
        last_incoming = None
        for interaction in directional:
            if (
                interaction.direction == InteractionDirection.INCOMING
                and interaction.type in types_to_check
            ):
                last_incoming = interaction
                break

        if not last_incoming:
            return None

        # Check if there's an outgoing message of ANY directional type after the incoming
        # (cross-type reply: email replied via chat or vice versa counts as replied)
        has_reply = any(
            i.direction == InteractionDirection.OUTGOING and i.date > last_incoming.date
            for i in directional
        )

        if has_reply:
            return None

        # Return unreplied message info
        return {
            "date": _format_datetime(last_incoming.date),
            "daysSince": _days_since(last_incoming.date, now),
            "type": _TYPE_NAMES.get(last_incoming.type, "unknown"),
            "subject": getattr(last_incoming, "subject", None),  # None for chat
        }

    except LOOKUP_GONE_ERRORS as e:
        logger.warning(f"Failed to check unreplied for {entity_type} {entity_id}: {e}")
        return None


async def async_check_unreplied(
    client: AsyncAffinity,
    entity_type: str | int,
    entity_id: int,
    *,
    interaction_types: list[InteractionType] | None = None,
    lookback_days: int = 30,
) -> dict[str, Any] | None:
    """Async version: Check for unreplied incoming messages for an entity.

    Supports person, company, and opportunity entity types.
    Also handles V1 integer entityType formats (0=person, 1=company).

    Cross-type reply detection: if they emailed and you replied via chat
    (or vice versa), that counts as a reply.

    Args:
        client: AsyncAffinity client for async API calls
        entity_type: "company", "person", "opportunity" (or V1 integers 0, 1)
        entity_id: The entity ID
        interaction_types: Types to check for unreplied messages.
            Default: [EMAIL, CHAT_MESSAGE] (both).
            Only EMAIL and CHAT_MESSAGE are valid (have direction).
        lookback_days: Number of days to look back (default 30)

    Returns:
        Dict with unreplied message info if found, None otherwise (also when the entity is
        gone: LOOKUP_GONE_ERRORS). Any other error propagates.
        Example: {
            "date": "2026-01-10T10:00:00Z",
            "daysSince": 5,
            "type": "email",
            "subject": "Following up on our conversation",  # None for chat
        }
    """
    from affinity.models.types import InteractionDirection
    from affinity.types import CompanyId, OpportunityId, PersonId

    # Default to checking both email and chat
    if interaction_types is None:
        interaction_types = [InteractionType.EMAIL, InteractionType.CHAT_MESSAGE]

    # Filter to only directional types
    types_to_check = frozenset(interaction_types) & DIRECTIONAL_TYPES
    if not types_to_check:
        return None

    try:
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(days=lookback_days)

        # Build entity-specific filter kwargs
        # Handle V1 (integer) and V2 (string) entityType formats
        iter_kwargs: dict[str, Any] = {
            "start_time": start_time,
            "end_time": now,
        }

        if entity_type in ("company", 1, "organization"):
            iter_kwargs["company_id"] = CompanyId(entity_id)
        elif entity_type in ("person", 0):
            iter_kwargs["person_id"] = PersonId(entity_id)
        elif entity_type == "opportunity":
            iter_kwargs["opportunity_id"] = OpportunityId(entity_id)
        else:
            logger.debug(f"Unsupported entity type for unreplied check: {entity_type}")
            return None

        # Fetch interactions per directional type in parallel (V1 API requires type parameter)
        async def _fetch_type(itype: InteractionType) -> list[Any]:
            result = []
            async for interaction in client.interactions.iter(**iter_kwargs, type=itype):
                result.append(interaction)
            return result

        type_results = await gather_or_raise(*[_fetch_type(itype) for itype in types_to_check])
        directional = [i for sublist in type_results for i in sublist]

        if not directional:
            return None

        # Sort by date descending (most recent first)
        directional.sort(key=lambda i: i.date, reverse=True)

        # Find the most recent incoming message matching the requested types
        last_incoming = None
        for interaction in directional:
            if (
                interaction.direction == InteractionDirection.INCOMING
                and interaction.type in types_to_check
            ):
                last_incoming = interaction
                break

        if not last_incoming:
            return None

        # Check if there's an outgoing message of ANY directional type after the incoming
        # (cross-type reply: email replied via chat or vice versa counts as replied)
        has_reply = any(
            i.direction == InteractionDirection.OUTGOING and i.date > last_incoming.date
            for i in directional
        )

        if has_reply:
            return None

        # Return unreplied message info
        return {
            "date": _format_datetime(last_incoming.date),
            "daysSince": _days_since(last_incoming.date, now),
            "type": _TYPE_NAMES.get(last_incoming.type, "unknown"),
            "subject": getattr(last_incoming, "subject", None),  # None for chat
        }

    except LOOKUP_GONE_ERRORS as e:
        logger.warning(f"Failed to check unreplied for {entity_type} {entity_id}: {e}")
        return None


def flatten_unreplied_for_csv(unreplied: dict[str, Any] | None) -> dict[str, str]:
    """Flatten unreplied message data for CSV columns.

    Returns dict with all UNREPLIED_CSV_COLUMNS keys (empty strings if no data).

    Args:
        unreplied: Unreplied message data from check_unreplied()

    Returns:
        Dict with string values for each CSV column.
    """
    result: dict[str, str] = dict.fromkeys(UNREPLIED_CSV_COLUMNS, "")

    if not unreplied:
        return result

    result["unrepliedDate"] = unreplied.get("date", "")
    days_since = unreplied.get("daysSince")
    result["unrepliedDaysSince"] = str(days_since) if days_since is not None else ""
    result["unrepliedType"] = unreplied.get("type", "")
    result["unrepliedSubject"] = unreplied.get("subject") or ""

    return result
