"""
Models for duplicate suggestions and inferred connections (V2, beta).

- ``GET /v2/duplicates/company-suggestions`` -> :class:`CompanyDuplicateSuggestion`
- ``GET /v2/duplicates/person-suggestions`` -> :class:`PersonDuplicateSuggestion`
- ``GET /v2/inferred-connections/coworkers`` -> :class:`CoworkerConnectionGroup`
- ``GET /v2/inferred-connections/investor-executive-connections`` ->
  :class:`InvestorExecutiveConnectionGroup`
"""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from .entities import AffinityModel, Person
from .relationships_v2 import RelationshipPerson
from .types import CompanyId


class DuplicateCompanyProfile(AffinityModel):
    """A company in a duplicate suggestion."""

    id: CompanyId
    name: str
    domain: str | None = None
    domains: list[str] = Field(default_factory=list)
    is_enriched: bool = Field(False, alias="isEnriched")
    is_global: bool = Field(False, alias="isGlobal")


class CompanyDuplicateSuggestion(AffinityModel):
    """Two company profiles Affinity thinks are the same company.

    Merge ``duplicates[0]`` into ``primary`` with ``companies.merge(primary.id, duplicate.id)``.
    ``match_criteria``: ``domain-match`` (high confidence), ``domain-redirect`` (medium),
    ``name`` (low).
    """

    id: str
    primary: DuplicateCompanyProfile = Field(alias="primaryProfile")
    # Always one today
    duplicates: list[DuplicateCompanyProfile] = Field(alias="duplicateProfiles")
    match_criteria: str = Field(alias="matchCriteria")


class PersonDuplicateSuggestion(AffinityModel):
    """Two person profiles Affinity thinks are the same person.

    Merge ``duplicates[0]`` into ``primary`` with ``persons.merge(primary.id, duplicate.id)``.
    ``match_criteria``: ``intelligent`` (medium confidence: email matching across sources),
    ``name`` (low).
    """

    id: str
    primary: Person = Field(alias="primaryProfile")
    duplicates: list[Person] = Field(alias="duplicateProfiles")
    match_criteria: str = Field(alias="matchCriteria")


class ConnectionCompany(AffinityModel):
    """A company in an inferred connection."""

    id: CompanyId
    name: str | None = None
    domain: str | None = None


class ConnectionTarget(AffinityModel):
    """Someone outside your Affinity data you'd like to know.

    ``full_name`` is always set when known; API 2024-01-01 sends ``first_name``/``last_name``
    instead (later versions send only the full name).
    """

    full_name: str | None = Field(None, alias="fullName")
    first_name: str | None = Field(None, alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    title: str | None = None
    linkedin_url: str | None = Field(None, alias="linkedinUrl")
    # Where they work today (not necessarily where the connection comes from)
    current_company: ConnectionCompany = Field(alias="currentCompany")

    @model_validator(mode="after")
    def _full_name(self) -> ConnectionTarget:
        if self.full_name is None and (self.first_name or self.last_name):
            self.full_name = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return self


class CoworkerConnection(AffinityModel):
    """Someone you know (``source``) who worked with the target at ``shared_employer``."""

    source: RelationshipPerson
    shared_employer: ConnectionCompany
    overlap_start: date
    overlap_end: date

    @classmethod
    def from_api(cls, data: dict[str, object]) -> CoworkerConnection:
        inference = data.get("inference") or {}
        assert isinstance(inference, dict)
        return cls.model_validate(
            {
                "source": data.get("source"),
                "shared_employer": inference.get("sharedEmployer"),
                "overlap_start": inference.get("overlapStartDate"),
                "overlap_end": inference.get("overlapEndDate"),
            }
        )


class DealRole(AffinityModel):
    """A person's role in a deal."""

    title: str | None = None
    company: ConnectionCompany
    start_date: date | None = Field(None, alias="startDate")
    # None while they're still in the role
    end_date: date | None = Field(None, alias="endDate")


class FundingRound(AffinityModel):
    """The funding round behind an investor-executive connection."""

    date: date
    series: str | None = None
    amount: float | None = None
    currency: str | None = Field(None, alias="currencyCode")


class InvestorExecutiveConnection(AffinityModel):
    """Someone you know (``source``, the investor) who invested in a company where the target
    was an executive."""

    source: RelationshipPerson
    funding_round: FundingRound = Field(alias="fundingEvent")
    investor: DealRole
    executive: DealRole

    @classmethod
    def from_api(cls, data: dict[str, object]) -> InvestorExecutiveConnection:
        deal = data.get("dealContext") or {}
        assert isinstance(deal, dict)
        return cls.model_validate({"source": data.get("source"), **deal})


class CoworkerConnectionGroup(AffinityModel):
    """A target and the people you know who worked with them, strongest first."""

    target: ConnectionTarget
    connections: list[CoworkerConnection]

    @classmethod
    def from_api(cls, data: dict[str, object]) -> CoworkerConnectionGroup:
        raw = data.get("connections") or []
        assert isinstance(raw, list)
        return cls(
            target=ConnectionTarget.model_validate(data.get("target")),
            connections=[CoworkerConnection.from_api(c) for c in raw],
        )


class InvestorExecutiveConnectionGroup(AffinityModel):
    """A target (an executive) and the investors you know who backed their company."""

    target: ConnectionTarget
    connections: list[InvestorExecutiveConnection]

    @classmethod
    def from_api(cls, data: dict[str, object]) -> InvestorExecutiveConnectionGroup:
        raw = data.get("connections") or []
        assert isinstance(raw, list)
        return cls(
            target=ConnectionTarget.model_validate(data.get("target")),
            connections=[InvestorExecutiveConnection.from_api(c) for c in raw],
        )
