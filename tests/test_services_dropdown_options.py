"""SDK tests for V2 dropdown options: reads on lists/companies/persons, list option writes."""

from __future__ import annotations

import json
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity
from affinity.exceptions import ApiVersionTooOldError, WriteNotAllowedError
from affinity.policies import Policies, WritePolicy
from affinity.types import ListId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
LIST_OPTS = f"{V2}/lists/9/fields/field-5/dropdown-options"
STATUS = {
    "id": 3,
    "text": "Won",
    "type": "status-dropdown",
    "rank": 2,
    "color": "green",
    "statusCategory": "won",
    "winRate": None,
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


@respx.mock
def test_list_field_options_read_every_page_without_a_version_header() -> None:
    next_url = f"{LIST_OPTS}?cursor=p2"
    respx.get(LIST_OPTS, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([STATUS]))
    )
    first = respx.get(LIST_OPTS).mock(
        return_value=Response(
            200, json=_page([{"id": 1, "text": "Lead", "type": "dropdown"}], next_url)
        )
    )
    with Affinity(api_key="k", max_retries=0) as client:
        options = client.lists.get_field_dropdown_options(ListId(9), "field-5")
    assert [o.text for o in options] == ["Lead", "Won"]
    won = options[1]
    assert won.type == "status-dropdown" and won.status_category == "won" and won.win_rate is None
    assert won.color == "green"
    # Reads send no per-operation version: they must keep working for pinned 2024-01-01 clients
    assert "x-affinity-api-version" not in {k.lower() for k in first.calls[0].request.headers}


@respx.mock
def test_status_types_are_read_with_the_write_version_on_every_page() -> None:
    next_url = f"{LIST_OPTS}?cursor=p2"
    second = respx.get(LIST_OPTS, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([STATUS]))
    )
    first = respx.get(LIST_OPTS).mock(
        return_value=Response(200, json=_page([{"id": 1, "text": "Lead"}], next_url))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        client.lists.get_field_dropdown_options(ListId(9), "field-5", with_status_types=True)
    assert first.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"
    assert second.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"


@respx.mock
def test_reads_work_for_a_client_pinned_to_2024_01_01() -> None:
    respx.get(f"{V2}/companies/fields/field-7/dropdown-options").mock(
        return_value=Response(200, json=_page([{"id": 1, "text": "A"}]))
    )
    respx.get(f"{V2}/persons/fields/field-8/dropdown-options").mock(
        return_value=Response(200, json=_page([{"id": 2, "text": "B"}]))
    )
    with Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client:
        assert [o.id for o in client.companies.get_field_dropdown_options("field-7")] == [1]
        assert [o.id for o in client.persons.get_field_dropdown_options("field-8")] == [2]


@respx.mock
def test_create_sends_the_typed_body_and_the_write_version() -> None:
    route = respx.post(LIST_OPTS).mock(return_value=Response(201, json=STATUS))
    with Affinity(api_key="k", max_retries=0) as client:
        option = client.lists.create_field_dropdown_option(
            ListId(9),
            "field-5",
            option_type="status-dropdown",
            text="Won",
            rank=2,
            color="green",
            status_category="won",
        )
    request = route.calls[0].request
    assert json.loads(request.content) == {
        "type": "status-dropdown",
        "text": "Won",
        "rank": 2,
        "color": "green",
        "statusCategory": "won",
    }
    assert request.headers["x-affinity-api-version"] == "2026-07-15"
    assert option.id == 3


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"option_type": "multi", "text": "x"}, "'option_type'"),
        ({"option_type": "dropdown", "text": ""}, "1-255"),
        ({"option_type": "dropdown", "text": "x", "rank": 1}, "only text"),
        ({"option_type": "ranked-dropdown", "text": "x", "rank": 1}, "needs 'rank' and 'color'"),
        (
            {"option_type": "ranked-dropdown", "text": "x", "rank": 1, "color": "pink"},
            "'color'",
        ),
        (
            {"option_type": "status-dropdown", "text": "x", "rank": 1, "color": "red"},
            "needs 'status_category'",
        ),
        (
            {
                "option_type": "ranked-dropdown",
                "text": "x",
                "rank": 1,
                "color": "red",
                "status_category": "won",
            },
            "status-dropdown' options only",
        ),
        (
            {
                "option_type": "status-dropdown",
                "text": "x",
                "rank": 1,
                "color": "red",
                "status_category": "open",
                "win_rate": 101,
            },
            "between 0 and 100",
        ),
    ],
)
def test_create_checks_the_body(kwargs: dict[str, Any], message: str) -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match=message):
        client.lists.create_field_dropdown_option(ListId(9), "field-5", **kwargs)


@respx.mock
def test_update_sends_only_what_changed() -> None:
    route = respx.post(f"{LIST_OPTS}/3").mock(return_value=Response(204))
    respx.get(f"{LIST_OPTS}/3").mock(
        return_value=Response(200, json={**STATUS, "text": "Closed won"})
    )
    with Affinity(api_key="k", max_retries=0) as client:
        updated = client.lists.update_field_dropdown_option(
            ListId(9), "field-5", 3, text="Closed won"
        )
        assert updated.text == "Closed won"  # read back: the update itself answers 204
        with pytest.raises(ValueError, match="Nothing to update"):
            client.lists.update_field_dropdown_option(ListId(9), "field-5", 3)
    assert json.loads(route.calls[0].request.content) == {"text": "Closed won"}


@respx.mock
def test_delete_and_get_one() -> None:
    respx.get(f"{LIST_OPTS}/3").mock(return_value=Response(200, json=STATUS))
    deleted = respx.delete(f"{LIST_OPTS}/3").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.lists.get_field_dropdown_option(ListId(9), "field-5", 3).text == "Won"
        assert client.lists.delete_field_dropdown_option(ListId(9), "field-5", 3) is True
    assert deleted.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"


def test_writes_refuse_a_client_pinned_below_2026_07_15() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.lists.delete_field_dropdown_option(ListId(9), "field-5", 3)


def test_writes_are_blocked_by_write_policy_deny() -> None:
    with (
        Affinity(api_key="k", max_retries=0, policies=Policies(write=WritePolicy.DENY)) as client,
        pytest.raises(WriteNotAllowedError),
    ):
        client.lists.update_field_dropdown_option(ListId(9), "field-5", 3, text="x")


@respx.mock
async def test_async_read_and_create() -> None:
    respx.get(LIST_OPTS).mock(return_value=Response(200, json=_page([STATUS])))
    respx.post(LIST_OPTS).mock(return_value=Response(201, json={"id": 4, "text": "New"}))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        options = await client.lists.get_field_dropdown_options(ListId(9), "field-5")
        created = await client.lists.create_field_dropdown_option(
            ListId(9), "field-5", option_type="dropdown", text="New"
        )
    assert [o.id for o in options] == [3]
    assert created.id == 4
