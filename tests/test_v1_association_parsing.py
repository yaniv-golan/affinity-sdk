"""V1 responses send organization_ids; Person and Opportunity read them into company_ids
(they were silently empty before)."""

from __future__ import annotations

from affinity.models import Opportunity, Person


def test_person_reads_v1_organization_ids() -> None:
    person = Person.model_validate(
        {"id": 1, "first_name": "A", "last_name": "B", "organization_ids": [7], "type": 0}
    )
    assert person.company_ids == [7]
    assert person.model_dump(by_alias=True)["organizationIds"] == [7]


def test_opportunity_reads_v1_organization_ids() -> None:
    opp = Opportunity.model_validate(
        {"id": 1, "name": "x", "organization_ids": [7], "person_ids": [5]}
    )
    assert opp.company_ids == [7] and opp.person_ids == [5]


def test_v2_and_field_name_forms_still_work() -> None:
    assert Person.model_validate(
        {"id": 1, "firstName": "A", "organizationIds": [8], "type": "external"}
    ).company_ids == [8]
    assert Opportunity.model_validate({"id": 1, "name": "x", "company_ids": [9]}).company_ids == [9]
