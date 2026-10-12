"""SDK and CLI tests: duplicate suggestions and inferred connections (V2, beta)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.cli.main import cli
from affinity.exceptions import ApiVersionTooOldError
from affinity.types import CompanyId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
CO = {"isEnriched": True, "isGlobal": False, "domains": ["acme.com"]}
COMPANY_SUGGESTION = {
    "id": "s1",
    "matchCriteria": "domain-match",
    "primaryProfile": {"id": 1, "name": "Acme", "domain": "acme.com", **CO},
    "duplicateProfiles": [{"id": 2, "name": "Acme Inc", "domain": "acme.com", **CO}],
}
PERSON = {"firstName": "Dana", "lastName": "Levi", "type": "external", "emailAddresses": []}
PERSON_SUGGESTION = {
    "id": "p1",
    "matchCriteria": "intelligent",
    "primaryProfile": {"id": 5, "primaryEmailAddress": "d@a.co", **PERSON},
    "duplicateProfiles": [{"id": 6, "primaryEmailAddress": "d@b.co", **PERSON}],
}
SOURCE = {"id": 9, "firstName": "Ann", "lastName": "Lee", "primaryEmailAddress": "ann@x.co"}
TARGET = {
    "fullName": "Bob Lee",
    "title": "CEO",
    "linkedinUrl": "https://linkedin.com/in/bob",
    "currentCompany": {"id": 7, "name": "Wix", "domain": "wix.com"},
}
COWORKER_GROUP = {
    "target": TARGET,
    "connections": [
        {
            "source": SOURCE,
            "inference": {
                "sharedEmployer": {"id": 8, "name": "Old Co", "domain": None},
                "overlapStartDate": "2018-03-01",
                "overlapEndDate": "2020-11-30",
            },
        }
    ],
}
ROLE = {"title": "Partner", "startDate": "2018-06-01", "endDate": None}
INVESTOR_GROUP = {
    "target": TARGET,
    "connections": [
        {
            "source": SOURCE,
            "dealContext": {
                "fundingEvent": {
                    "date": "2021-05-01",
                    "series": "Series A",
                    "amount": 15000000,
                    "currencyCode": "USD",
                },
                "investor": {**ROLE, "company": {"id": 3, "name": "Fund", "domain": None}},
                "executive": {
                    **ROLE,
                    "title": "CEO",
                    "company": {"id": 7, "name": "Wix", "domain": None},
                },
            },
            "inference": {
                "investingFirm": None,
                "portfolioCompany": {"id": 7, "name": "Wix", "domain": None},
            },
        }
    ],
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _run(args: list[str]) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


# --- SDK ----------------------------------------------------------------------------------------


@respx.mock
def test_company_duplicate_suggestions() -> None:
    route = respx.get(f"{V2}/duplicates/company-suggestions").mock(
        return_value=Response(200, json=_page([COMPANY_SUGGESTION]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.companies.list_duplicate_suggestions(
            term="ac", match_criteria="domain-match", order_by="-createdAt", limit=10
        )
    s = page.data[0]
    assert (s.primary.id, s.duplicates[0].id, s.match_criteria) == (1, 2, "domain-match")
    params = route.calls[0].request.url.params
    assert params["term"] == "ac" and params["filter"] == "matchCriteria=domain-match"
    assert params["orderBy"] == "-createdAt" and params["limit"] == "10"
    assert route.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"


@respx.mock
def test_person_duplicate_suggestions_iterate() -> None:
    url = f"{V2}/duplicates/person-suggestions"
    respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{**PERSON_SUGGESTION, "id": "p2"}]))
    )
    respx.get(url).mock(
        return_value=Response(200, json=_page([PERSON_SUGGESTION], f"{url}?cursor=p2"))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        ids = [s.id for s in client.persons.iter_duplicate_suggestions()]
    assert ids == ["p1", "p2"]


def test_duplicate_arguments_are_checked() -> None:
    with Affinity(api_key="k", max_retries=0) as client:
        with pytest.raises(ValueError, match="2 characters"):
            client.companies.list_duplicate_suggestions(term="a")
        with pytest.raises(ValueError, match="order_by"):
            client.persons.list_duplicate_suggestions(order_by="score")
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.companies.list_duplicate_suggestions()


@respx.mock
def test_coworker_connections() -> None:
    route = respx.get(f"{V2}/inferred-connections/coworkers").mock(
        return_value=Response(200, json=_page([COWORKER_GROUP]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.companies.list_coworker_connections(CompanyId(7), limit=50, total_count=True)
    group = page.data[0]
    conn = group.connections[0]
    assert group.target.full_name == "Bob Lee" and group.target.current_company.id == 7
    assert conn.source.id == 9 and conn.shared_employer.name == "Old Co"
    assert (conn.overlap_start, conn.overlap_end) == (date(2018, 3, 1), date(2020, 11, 30))
    params = route.calls[0].request.url.params
    assert params["filter"] == "target.currentCompany.id=7" and params["totalCount"] == "true"
    assert "x-affinity-api-version" not in route.calls[0].request.headers


@respx.mock
def test_investor_executive_connections() -> None:
    respx.get(f"{V2}/inferred-connections/investor-executive-connections").mock(
        return_value=Response(200, json=_page([INVESTOR_GROUP]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        groups = list(client.companies.iter_investor_executive_connections(CompanyId(7)))
    conn = groups[0].connections[0]
    assert conn.funding_round.series == "Series A" and conn.funding_round.amount == 15000000
    assert conn.investor.company.name == "Fund" and conn.executive.title == "CEO"
    assert conn.investor.end_date is None


def test_connection_limit_is_1_to_50() -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match="50"):
        client.companies.list_coworker_connections(CompanyId(7), limit=100)


@respx.mock
async def test_async() -> None:
    respx.get(f"{V2}/duplicates/company-suggestions").mock(
        return_value=Response(200, json=_page([COMPANY_SUGGESTION]))
    )
    respx.get(f"{V2}/duplicates/person-suggestions").mock(
        return_value=Response(200, json=_page([PERSON_SUGGESTION]))
    )
    respx.get(f"{V2}/inferred-connections/coworkers").mock(
        return_value=Response(200, json=_page([COWORKER_GROUP]))
    )
    respx.get(f"{V2}/inferred-connections/investor-executive-connections").mock(
        return_value=Response(200, json=_page([]))
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        assert [s.id async for s in client.companies.iter_duplicate_suggestions()] == ["s1"]
        assert (await client.persons.list_duplicate_suggestions()).data[0].id == "p1"
        groups = [g async for g in client.companies.iter_coworker_connections(CompanyId(7))]
        assert groups[0].connections[0].source.id == 9
        assert (await client.companies.list_investor_executive_connections(CompanyId(7))).data == []


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_company_duplicates(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/duplicates/company-suggestions").mock(
        return_value=Response(200, json=_page([COMPANY_SUGGESTION]))
    )
    code, out = _run(["company", "duplicates", "--match", "domain-match"])
    assert code == 0, out
    assert out["data"]["suggestions"][0] == {
        "suggestionId": "s1",
        "matchCriteria": "domain-match",
        "primary": {"id": 1, "name": "Acme", "domain": "acme.com"},
        "duplicate": {"id": 2, "name": "Acme Inc", "domain": "acme.com"},
    }


def test_cli_person_duplicates(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/duplicates/person-suggestions").mock(
        return_value=Response(200, json=_page([PERSON_SUGGESTION]))
    )
    code, out = _run(["person", "duplicates"])
    assert code == 0, out
    row = out["data"]["suggestions"][0]
    assert row["primary"] == {"id": 5, "name": "Dana Levi", "email": "d@a.co"}
    assert row["duplicate"]["id"] == 6


def test_cli_company_connections(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/inferred-connections/coworkers").mock(
        return_value=Response(200, json=_page([COWORKER_GROUP]))
    )
    code, out = _run(["company", "connections", "7"])
    assert code == 0, out
    row = out["data"]["connections"][0]
    assert row["target"] == {
        "name": "Bob Lee",
        "title": "CEO",
        "linkedinUrl": "https://linkedin.com/in/bob",
        "companyId": 7,
    }
    assert row["via"] == [
        {
            "personId": 9,
            "name": "Ann Lee",
            "email": "ann@x.co",
            "sharedEmployer": "Old Co",
            "from": "2018-03-01",
            "to": "2020-11-30",
        }
    ]


def test_cli_company_connections_investor(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/inferred-connections/investor-executive-connections").mock(
        return_value=Response(200, json=_page([INVESTOR_GROUP]))
    )
    code, out = _run(["company", "connections", "7", "--type", "investor-executive"])
    assert code == 0, out
    via = out["data"]["connections"][0]["via"][0]
    assert via["personId"] == 9 and via["round"] == "Series A" and via["investorFirm"] == "Fund"


def test_target_reads_the_2024_01_01_name_shape() -> None:
    from affinity.models import ConnectionTarget

    old = {k: v for k, v in TARGET.items() if k != "fullName"}
    target = ConnectionTarget.model_validate({**old, "firstName": "Bob", "lastName": "Lee"})
    assert target.full_name == "Bob Lee"
    assert (target.first_name, target.last_name) == ("Bob", "Lee")
    assert ConnectionTarget.model_validate(TARGET).full_name == "Bob Lee"
