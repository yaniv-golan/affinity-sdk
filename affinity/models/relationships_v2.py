"""Models for V2 relationships (``GET /v2/{companies,persons}/{id}/relationships``)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field, model_validator

from .pagination import AffinityModel


class RelationshipPerson(AffinityModel):
    """One side of a relationship."""

    id: int
    first_name: str | None = Field(None, alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    primary_email_address: str | None = Field(None, alias="primaryEmailAddress")


class Relationship(AffinityModel):
    """How strongly two people are connected.

    ``interaction_score`` runs from 0.0 to 1.0 (higher is stronger); a relationship known only
    from LinkedIn has score 0 and ``linkedin_connected_on`` set.
    """

    person1: RelationshipPerson
    person2: RelationshipPerson
    interaction_score: float = Field(alias="interactionScore")
    linkedin_connected_on: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_linkedin(cls, data: Any) -> Any:
        if isinstance(data, dict) and "linkedIn" in data:
            linked = data.get("linkedIn")
            data = {**data, "linkedin_connected_on": (linked or {}).get("connectedOn")}
            data.pop("linkedIn", None)
        return data
