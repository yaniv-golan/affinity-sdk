"""SDK tests: person create/delete and opportunity update/delete on the V2 API, with the
documented V1 exceptions (a person created with companies; opportunity association changes)."""

from __future__ import annotations

import json
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.exceptions import ApiVersionTooOldError
from affinity.models import OpportunityUpdate, PersonCreate
from affinity.types import CompanyId, OpportunityId, PersonId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
V1 = "https://api.affinity.co"
PERSON_V2 = {
    "id": 31,
    "firstName": "Dana",
    "lastName": None,
    "primaryEmailAddress": "dana@acme.co",
    "emailAddresses": ["dana@acme.co", "d@home.co"],
    "type": "external",
}
PERSON_V1 = {
    "id": 32,
    "first_name": "Dana",
    "last_name": "Levi",
    "primary_email": "dana@acme.co",
    "emails": ["dana@acme.co"],
    "organization_ids": [7],
    "type": 0,
}
OPP_V2 = {"id": 41, "name": "Series A", "listId": 9, "isRestricted": False, "isRedacted": False}
OPP_V1 = {
    "id": 41,
    "name": "Series A",
    "list_entries": [],
    "person_ids": [5],
    "organization_ids": [7],
}


def _body(route: respx.Route) -> Any:
    return json.loads(route.calls[0].request.content)


def _version(route: respx.Route) -> str | None:
    return route.calls[0].request.headers.get("x-affinity-api-version")


@respx.mock
def test_person_create_on_v2() -> None:
    route = respx.post(f"{V2}/persons").mock(return_value=Response(201, json=PERSON_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        person = client.persons.create(
            PersonCreate(first_name="Dana", last_name=None, emails=["dana@acme.co", "d@home.co"]),
            if_not_exists=False,
        )
    assert person.id == 31
    assert _body(route) == {
        "firstName": "Dana",
        "lastName": None,
        "primaryEmailAddress": "dana@acme.co",
        "emailAddresses": ["dana@acme.co", "d@home.co"],
    }
    assert _version(route) == "2026-07-15"


@respx.mock
def test_person_create_without_email() -> None:
    route = respx.post(f"{V2}/persons").mock(return_value=Response(201, json=PERSON_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        client.persons.create(PersonCreate(first_name="A", last_name="B"), if_not_exists=False)
    assert _body(route) == {
        "firstName": "A",
        "lastName": "B",
        "primaryEmailAddress": None,
        "emailAddresses": [],
    }


@respx.mock
def test_person_create_with_companies_stays_on_v1() -> None:
    route = respx.post(f"{V1}/persons").mock(return_value=Response(200, json=PERSON_V1))
    with Affinity(api_key="k", max_retries=0) as client:
        person = client.persons.create(
            PersonCreate(
                first_name="Dana", last_name="Levi", emails=["dana@acme.co"], company_ids=[7]
            ),
            if_not_exists=False,
        )
    assert person.id == 32
    assert _body(route)["organization_ids"] == [7]


@respx.mock
def test_person_delete_on_v2() -> None:
    route = respx.delete(f"{V2}/persons/31").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.persons.delete(PersonId(31)) is True
    assert _version(route) == "2026-07-15"


def test_person_create_refused_when_pinned_old() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.persons.create(PersonCreate(first_name="A", last_name="B"), if_not_exists=False)


@respx.mock
def test_opportunity_rename_on_v2_then_read_back() -> None:
    update = respx.post(f"{V2}/opportunities/41").mock(return_value=Response(204))
    get = respx.get(f"{V2}/opportunities/41").mock(return_value=Response(200, json=OPP_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        opp = client.opportunities.update(OpportunityId(41), OpportunityUpdate(name="Series A"))
    assert _body(update) == {"name": "Series A"}
    assert _version(update) == "2026-09-17"
    assert get.called
    assert opp.name == "Series A"


@respx.mock
def test_opportunity_association_change_stays_one_v1_request() -> None:
    v1 = respx.put(f"{V1}/opportunities/41").mock(return_value=Response(200, json=OPP_V1))
    with Affinity(api_key="k", max_retries=0) as client:
        opp = client.opportunities.update(
            OpportunityId(41),
            OpportunityUpdate(
                name="Series A", person_ids=[PersonId(5)], company_ids=[CompanyId(7)]
            ),
        )
    assert _body(v1) == {"name": "Series A", "person_ids": [5], "organization_ids": [7]}
    assert opp.person_ids == [5]


def test_opportunity_empty_update_is_refused() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="nothing to update"),
    ):
        client.opportunities.update(OpportunityId(41), OpportunityUpdate())


@respx.mock
def test_opportunity_delete_on_v2() -> None:
    route = respx.delete(f"{V2}/opportunities/41").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.opportunities.delete(OpportunityId(41)) is True
    assert _version(route) == "2026-09-17"


@respx.mock
async def test_async_person_and_opportunity_writes() -> None:
    create = respx.post(f"{V2}/persons").mock(return_value=Response(201, json=PERSON_V2))
    v1_create = respx.post(f"{V1}/persons").mock(return_value=Response(200, json=PERSON_V1))
    pdel = respx.delete(f"{V2}/persons/31").mock(return_value=Response(204))
    rename = respx.post(f"{V2}/opportunities/41").mock(return_value=Response(204))
    respx.get(f"{V2}/opportunities/41").mock(return_value=Response(200, json=OPP_V2))
    assoc = respx.put(f"{V1}/opportunities/41").mock(return_value=Response(200, json=OPP_V1))
    odel = respx.delete(f"{V2}/opportunities/41").mock(return_value=Response(204))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        await client.persons.create(
            PersonCreate(first_name="A", last_name="B"), if_not_exists=False
        )
        await client.persons.create(
            PersonCreate(first_name="A", last_name="B", company_ids=[7]), if_not_exists=False
        )
        assert await client.persons.delete(PersonId(31)) is True
        await client.opportunities.update(OpportunityId(41), OpportunityUpdate(name="X"))
        await client.opportunities.update(
            OpportunityId(41), OpportunityUpdate(person_ids=[PersonId(5)])
        )
        assert await client.opportunities.delete(OpportunityId(41)) is True
    for route in (create, v1_create, pdel, rename, assoc, odel):
        assert route.called
    assert _body(assoc) == {"person_ids": [5]}


@respx.mock
def test_person_create_without_email_sends_an_empty_email_list() -> None:
    route = respx.post(f"{V2}/persons").mock(return_value=Response(201, json=PERSON_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        client.persons.create(PersonCreate(first_name="A", last_name="B"), if_not_exists=False)
    assert _body(route)["emailAddresses"] == []


def test_person_create_with_companies_needs_a_last_name() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="last name"),
    ):
        client.persons.create(
            PersonCreate(first_name="A", last_name=None, company_ids=[7]), if_not_exists=False
        )


@respx.mock
def test_opportunity_rename_read_back_sends_no_version() -> None:
    respx.post(f"{V2}/opportunities/41").mock(return_value=Response(204))
    get = respx.get(f"{V2}/opportunities/41").mock(return_value=Response(200, json=OPP_V2))
    with Affinity(api_key="k", max_retries=0) as client:
        client.opportunities.update(OpportunityId(41), OpportunityUpdate(name="N"))
    assert "x-affinity-api-version" not in get.calls[0].request.headers
