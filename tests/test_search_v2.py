"""SDK tests: V2 search (companies, persons, list entries) and the criteria builder."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.criteria import Condition, SearchCriteria, Sort, all_of, any_of, filter_value_type
from affinity.exceptions import AuthorizationError
from affinity.models.entities import FieldMetadata
from affinity.policies import Policies, WritePolicy
from affinity.types import ListId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
COMPANY = {
    "id": 7,
    "name": "Acme",
    "domain": "acme.com",
    "domains": ["acme.com"],
    "isGlobal": False,
}
ENTRY = {
    "id": 3,
    "listId": 9,
    "type": "company",
    "createdAt": "2026-01-01T00:00:00Z",
    "creatorId": 1,
    "entity": {**COMPANY, "fields": []},
}


def _op(op_id: str) -> dict[str, Any]:
    return {"id": op_id, "name": op_id, "numberOfValuesRequired": "one"}


def _field(fid: str, value_type: str, ops: list[str] | None, **extra: Any) -> dict[str, Any]:
    filterability = (
        None
        if ops is None
        else {"filterableFieldType": "field-only", "operators": [_op(o) for o in ops]}
    )
    return {
        "id": fid,
        "name": fid.title(),
        "type": "global",
        "valueType": value_type,
        "enrichmentSource": None,
        "filterability": filterability,
        "sortability": None,
        **extra,
    }


FIELDS = [
    _field("stage", "dropdown", ["is-any-of", "is-none-of", "is-empty", "is-not-empty"]),
    _field("funded", "datetime", ["is-after", "is-between", "is-within-the-last"]),
    _field("first-email", "interaction", None),
]


def _page(items: list[Any], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _meta() -> dict[str, FieldMetadata]:
    return {f["id"]: FieldMetadata.model_validate(f) for f in FIELDS}


# --- criteria -----------------------------------------------------------------------------------


def test_body_wraps_values_by_type() -> None:
    criteria = SearchCriteria(
        where=all_of(
            Condition("stage", "is-any-of", [3, 4]),
            any_of(
                Condition("funded", "is-after", date(2025, 1, 1)),
                Condition("funded", "is-within-the-last", (6, "month")),
            ),
            Condition("owner", "is-any-of", 5, value_type="person"),
            Condition("loc", "is-any-of", {"country": "Israel"}, value_type="location"),
            Condition("size", "is-between", (10, 50), value_type="number"),
            Condition("url", "contains", "acme", value_type="text"),
            Condition("emp", "is-any-of", ["11-50"], value_type="filterable-text"),
            Condition("stage", "is-empty"),
        ),
        sort=(Sort("size", "desc"),),
        term="acme",
    )
    body = criteria.to_body(_meta())
    group = body["filters"]
    assert group["operator"] == "and"
    stage, either, owner, loc, size, url, emp, empty = group["filters"]
    assert stage == {
        "valueType": "dropdown",
        "fieldId": "stage",
        "operator": "is-any-of",
        "value": [{"dropdownOptionId": 3}, {"dropdownOptionId": 4}],
    }
    assert either["operator"] == "or"
    assert either["filters"][0]["valueType"] == "date"  # datetime fields filter as date
    assert either["filters"][0]["value"] == "2025-01-01"
    assert either["filters"][1]["value"] == {"amount": [6], "unit": "month"}
    assert owner["value"] == [{"id": 5}]
    assert loc["value"] == [{"country": "Israel"}]
    assert size["value"] == [10, 50]
    assert url["value"] == "acme"
    assert emp["value"] == ["11-50"]
    assert "value" not in empty  # is-empty takes no value key (null is rejected)
    assert body["sorts"] == [{"fieldId": "size", "direction": "desc"}]
    assert body["search"] == {"term": "acme"}


def test_a_bare_condition_is_wrapped_in_a_group() -> None:
    body = SearchCriteria(where=Condition("x", "contains", "a", value_type="text")).to_body()
    assert body["filters"]["operator"] == "and" and len(body["filters"]["filters"]) == 1


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        (Condition("stage", "contains", "a"), "isn't allowed on field stage"),
        (Condition("first-email", "is-after", date(2025, 1, 1)), "can't be filtered"),
        (Condition("nope", "contains", "a"), "Unknown field"),
        (Condition("funded", "is-after", datetime(2025, 1, 1)), "whole days"),
        (Condition("stage", "is-empty", 3), "takes no value"),
        (Condition("stage", "is-any-of"), "needs a value"),
        (Condition("stage", "is-any-of", list(range(101))), "At most 100"),
        (Condition("funded", "is-between", ("2025-02-01", "2025-01-01")), "start <= end"),
    ],
)
def test_bad_conditions_are_refused(condition: Condition, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SearchCriteria(where=condition).to_body(_meta())


def test_criteria_limits() -> None:
    with pytest.raises(ValueError, match="3 characters"):
        SearchCriteria(term="ab")
    with pytest.raises(ValueError, match="At most 5 sorts"):
        SearchCriteria(sort=tuple(Sort(f"f{i}") for i in range(6)))
    with pytest.raises(ValueError, match="at most 50"):
        all_of(*[Condition("x", "is-empty", value_type="text")] * 51)
    with pytest.raises(ValueError, match="needs value_type"):
        SearchCriteria(where=Condition("x", "contains", "a")).to_body()


def test_filter_value_type() -> None:
    assert filter_value_type("datetime") == "date"
    assert filter_value_type("dropdown-multi") == "dropdown-multi"
    assert filter_value_type("interaction") is None


# --- services -----------------------------------------------------------------------------------


@respx.mock
def test_company_find_posts_a_read_only_search() -> None:
    fields = respx.get(f"{V2}/companies/fields").mock(
        return_value=Response(200, json=_page(FIELDS))
    )
    route = respx.post(f"{V2}/companies/search").mock(
        return_value=Response(201, json=_page([COMPANY]))
    )
    with Affinity(api_key="k", max_retries=0, policies=Policies(write=WritePolicy.DENY)) as client:
        page = client.companies.find(
            Condition("stage", "is-any-of", 3),
            sort=Sort("stage"),
            field_ids=["stage"],
            limit=10,
            total_count=True,
        )
    assert page.data[0].name == "Acme"
    assert fields.calls[0].request.url.params.get_list("includes") == [
        "filterability",
        "sortability",
    ]
    request = route.calls[0].request
    assert request.url.params.get_list("fieldIds") == ["stage"]
    assert request.url.params["limit"] == "10" and request.url.params["totalCount"] == "true"
    sent = json.loads(request.content)
    assert sent["filters"]["filters"][0]["value"] == [{"dropdownOptionId": 3}]
    assert sent["sorts"] == [{"fieldId": "stage", "direction": "asc"}]


@respx.mock
def test_value_types_given_skip_the_fields_request() -> None:
    fields = respx.get(f"{V2}/persons/fields")
    respx.post(f"{V2}/persons/search").mock(return_value=Response(201, json=_page([])))
    with Affinity(api_key="k", max_retries=0) as client:
        client.persons.find(Condition("x", "contains", "dan", value_type="text"))
        client.persons.find(term="dan")
    assert not fields.called


@respx.mock
def test_next_pages_resend_the_body_to_the_next_url() -> None:
    url = f"{V2}/companies/search"
    second = respx.post(url, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([{**COMPANY, "id": 8}]))
    )
    respx.post(url).mock(
        return_value=Response(201, json=_page([COMPANY], f"{url}?limit=100&cursor=p2"))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        ids = [c.id for c in client.companies.iter_find(term="acme")]
        first = client.companies.find(term="acme")
        nxt = client.companies.find(term="acme", cursor=first.next_cursor)
    assert ids == [7, 8] and [c.id for c in nxt.data] == [8]
    for call in second.calls:
        assert json.loads(call.request.content) == {"search": {"term": "acme"}}


def test_cursor_carries_paging_options() -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match="cursor"):
        client.companies.find(term="acme", cursor=f"{V2}/companies/search?cursor=x", limit=5)


@respx.mock
def test_list_entry_find() -> None:
    respx.get(f"{V2}/lists/9/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    route = respx.post(f"{V2}/lists/9/list-entries/search").mock(
        return_value=Response(201, json=_page([ENTRY]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.lists.entries(ListId(9)).find(Condition("stage", "is-not-empty"))
    assert page.data[0].id == 3 and page.data[0].entity.id == 7  # type: ignore[union-attr]
    assert json.loads(route.calls[0].request.content)["filters"]["filters"][0] == {
        "valueType": "dropdown",
        "fieldId": "stage",
        "operator": "is-not-empty",
    }


@respx.mock
def test_403_names_the_permission() -> None:
    respx.post(f"{V2}/companies/search").mock(
        return_value=Response(403, json={"errors": [{"code": "forbidden", "message": "No"}]})
    )
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(AuthorizationError, match="Export All Organizations directory"),
    ):
        client.companies.find(term="acme")


@respx.mock
async def test_async() -> None:
    respx.get(f"{V2}/companies/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    url = f"{V2}/companies/search"
    respx.post(url, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([{**COMPANY, "id": 8}]))
    )
    respx.post(url).mock(return_value=Response(201, json=_page([COMPANY], f"{url}?cursor=p2")))
    respx.post(f"{V2}/lists/9/list-entries/search").mock(
        return_value=Response(201, json=_page([ENTRY]))
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        page = await client.companies.find(Condition("stage", "is-any-of", [3]))
        assert page.data[0].id == 7
        assert [c.id async for c in client.companies.iter_find(term="acme")] == [7, 8]
        entries = client.lists.entries(ListId(9))
        assert (await entries.find(term="acme")).data[0].id == 3
