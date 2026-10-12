"""
Models for the organization reads: teams and users (V2).

- ``GET /v2/teams``, ``GET /v2/teams/{id}`` -> :class:`Team`
- ``GET /v2/users``, ``GET /v2/users/{id}`` -> :class:`UserAccount`
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .entities import AffinityModel, ListSummary
from .secondary import User
from .types import ISODatetime, OpenStrEnum, UserId


class UserStatus(OpenStrEnum):
    """Whether a user's account is in use."""

    ACTIVE = "active"
    INVITED = "invited"  # invited, hasn't accepted yet
    DEACTIVATED = "deactivated"


class UserAccount(AffinityModel):
    """An internal user of the organization (V2 ``UserData``).

    A user's id is also their person id: ``persons.get(PersonId(user.id))`` works.
    ``emails`` and ``role`` are filled only for callers with the "Manage Users" permission.
    """

    id: UserId
    first_name: str = Field(alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    primary_email: str | None = Field(None, alias="primaryEmailAddress")
    emails: list[str] | None = Field(None, alias="emailAddresses")
    photo_url: str | None = Field(None, alias="photoUrl")
    status: UserStatus
    role: str | None = None


class TeamMember(AffinityModel):
    """A member of a team (from ``Team.members``)."""

    # The membership's id (not the user's)
    id: int
    user: User
    added_at: ISODatetime = Field(alias="addedAt")
    # Only with the "Manage Teams" permission and team-based privacy enabled
    privacy_type: str | None = Field(None, alias="privacyType")


class Team(AffinityModel):
    """A team (V2).

    ``members`` and ``accessible_lists`` (the first ones, up to 10, with ``*_total``) are filled
    only when requested with ``includes``; otherwise they are ``None``. ``privacy_type`` needs
    the "Manage Teams" permission and team privacy settings; otherwise it is ``None``.
    """

    id: int
    name: str
    created_at: ISODatetime | None = Field(None, alias="createdAt")
    privacy_type: str | None = Field(None, alias="privacyType")

    members: list[TeamMember] | None = None
    members_total: int | None = None
    accessible_lists: list[ListSummary] | None = None
    accessible_lists_total: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_previews(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for preview, key in (
            ("membersPreview", "members"),
            ("accessibleListsPreview", "accessible_lists"),
        ):
            block = data.pop(preview, None)
            if isinstance(block, dict):
                data[key] = block.get("data", [])
                data[f"{key}_total"] = block.get("totalCount")
        return data
