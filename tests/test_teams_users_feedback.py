"""SDK tests: teams, users, feedback, a single meeting, and a note's attachments (V2)."""

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
from affinity.models import Team, UserAccount, UserStatus
from affinity.types import NoteId, UserId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
USER = {
    "id": 4,
    "firstName": "Ann",
    "lastName": "Lee",
    "primaryEmailAddress": "ann@x.co",
    "photoUrl": None,
    "status": "active",
}
TEAM = {
    "id": 42,
    "name": "Investments",
    "createdAt": "2024-03-01T15:22:00Z",
    "membersPreview": {
        "data": [
            {
                "id": 1001,
                "addedAt": "2024-03-02T09:00:00Z",
                "user": {"id": 4, "firstName": "Ann", "lastName": "Lee", "emailAddress": "a@x.co"},
            }
        ],
        "totalCount": 12,
    },
    "accessibleListsPreview": {
        "data": [
            {
                "id": 9,
                "name": "Dealflow",
                "type": "company",
                "isPublic": False,
                "ownerId": 4,
                "creatorId": 4,
                "createdAt": "2026-01-01T00:00:00Z",
            }
        ],
        "totalCount": 3,
    },
}
MEETING = {
    "id": 77,
    "type": "meeting",
    "title": "Sync",
    "allDay": False,
    "startTime": "2026-10-01T09:00:00Z",
    "endTime": "2026-10-01T09:30:00Z",
    "attendees": [],
    "createdAt": "2026-09-30T09:00:00Z",
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _version(route: respx.Route) -> str | None:
    return route.calls[0].request.headers.get("x-affinity-api-version")


def test_team_flattens_previews() -> None:
    team = Team.model_validate(TEAM)
    assert team.members_total == 12 and team.members is not None
    assert team.members[0].user.id == 4
    assert team.accessible_lists_total == 3 and team.accessible_lists is not None
    assert team.accessible_lists[0].name == "Dealflow"
    plain = Team.model_validate({"id": 1, "name": "x"})
    assert plain.members is None and plain.members_total is None


def test_user_account_model() -> None:
    user = UserAccount.model_validate(USER)
    assert user.status == UserStatus.ACTIVE and user.emails is None and user.role is None
    assert UserAccount.model_validate({**USER, "status": "suspended"}).status == "suspended"


@respx.mock
def test_teams_list_filters_and_version() -> None:
    route = respx.get(f"{V2}/teams").mock(return_value=Response(200, json=_page([TEAM])))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.teams.list(
            ids=[1, 2],
            name_contains='In "vest',
            order_by="-createdAt",
            includes=True,
            limit=10,
            total_count=True,
        )
    assert page.data[0].id == 42
    params = route.calls[0].request.url.params
    assert params["filter"] == '(id=1 | id=2) & name=~"In \\"vest"'
    assert params["orderBy"] == "-createdAt"
    assert params.get_list("includes") == ["membersPreview", "accessibleListsPreview"]
    assert params["limit"] == "10" and params["totalCount"] == "true"
    assert _version(route) == "2026-09-17"


def test_teams_list_rejects_bad_arguments() -> None:
    with Affinity(api_key="k", max_retries=0) as client:
        with pytest.raises(ValueError, match="order_by"):
            client.teams.list(order_by="size")
        with pytest.raises(ValueError, match="includes"):
            client.teams.list(includes=["everything"])


def test_teams_need_2026_09_17() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2026-07-15") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.teams.list()


@respx.mock
def test_teams_get_and_iter() -> None:
    url = f"{V2}/teams"
    get = respx.get(f"{url}/42").mock(return_value=Response(200, json=TEAM))
    respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{"id": 43, "name": "B"}]))
    )
    respx.get(url).mock(return_value=Response(200, json=_page([TEAM], f"{url}?cursor=p2")))
    with Affinity(api_key="k", max_retries=0) as client:
        team = client.teams.get(42, includes=["membersPreview"])
        ids = [t.id for t in client.teams.iter(name="Investments")]
    assert team.members_total == 12
    assert get.calls[0].request.url.params.get_list("includes") == ["membersPreview"]
    assert ids == [42, 43]


@respx.mock
def test_users_list_get_iter() -> None:
    url = f"{V2}/users"
    listing = respx.get(url).mock(return_value=Response(200, json=_page([USER])))
    get = respx.get(f"{url}/4").mock(return_value=Response(200, json=USER))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.users.list(term="ann", ids=[UserId(4)], status=UserStatus.ACTIVE, limit=5)
        user = client.users.get(UserId(4))
        all_users = list(client.users.iter())
    params = listing.calls[0].request.url.params
    assert params["term"] == "ann" and params["filter"] == "id=4 & status=active"
    assert page.data[0].first_name == "Ann" and user.id == 4 and len(all_users) == 1
    assert _version(get) is None


@respx.mock
def test_feedback_send() -> None:
    route = respx.post(f"{V2}/feedback").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.feedback.send("api", "Note edits", "Please allow ...") is None
    assert json.loads(route.calls[0].request.content) == {
        "type": "api",
        "subject": "Note edits",
        "body": "Please allow ...",
    }


@pytest.mark.parametrize(
    ("kind", "subject", "body", "match"),
    [("bug", "s", "b", "kind"), ("api", "", "b", "subject"), ("api", "s", "x" * 10001, "body")],
)
def test_feedback_is_checked_before_sending(kind: str, subject: str, body: str, match: str) -> None:
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match=match):
        client.feedback.send(kind, subject, body)  # type: ignore[arg-type]


@respx.mock
def test_get_meeting() -> None:
    route = respx.get(f"{V2}/meetings/77").mock(return_value=Response(200, json=MEETING))
    with Affinity(api_key="k", max_retries=0) as client:
        meeting = client.interactions.get_meeting(77)
    assert meeting.id == 77 and meeting.title == "Sync"
    assert _version(route) == "2026-07-15"


@respx.mock
def test_note_attachments() -> None:
    companies = respx.get(f"{V2}/notes/5/attached-companies").mock(
        return_value=Response(200, json=_page([{"id": 7, "name": "Acme", "domain": "acme.com"}]))
    )
    persons = respx.get(f"{V2}/notes/5/attached-persons").mock(
        return_value=Response(200, json=_page([]))
    )
    opps = respx.get(f"{V2}/notes/5/attached-opportunities").mock(
        return_value=Response(200, json=_page([{"id": 3, "name": "Deal", "listId": 9}]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.notes.list_attached_companies(NoteId(5), limit=20, total_count=True)
        people = list(client.notes.iter_attached_persons(NoteId(5)))
        deals = client.notes.list_attached_opportunities(NoteId(5))
    assert page.data[0].name == "Acme" and people == [] and deals.data[0].id == 3
    assert companies.calls[0].request.url.params["totalCount"] == "true"
    assert persons.calls[0].request.url.params["limit"] == "100"
    assert opps.called


@respx.mock
async def test_async_services() -> None:
    respx.get(f"{V2}/teams").mock(return_value=Response(200, json=_page([TEAM])))
    respx.get(f"{V2}/teams/42").mock(return_value=Response(200, json=TEAM))
    respx.get(f"{V2}/users").mock(return_value=Response(200, json=_page([USER])))
    respx.get(f"{V2}/users/4").mock(return_value=Response(200, json=USER))
    fb = respx.post(f"{V2}/feedback").mock(return_value=Response(204))
    respx.get(f"{V2}/meetings/77").mock(return_value=Response(200, json=MEETING))
    respx.get(f"{V2}/notes/5/attached-companies").mock(return_value=Response(200, json=_page([])))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        assert [t.id async for t in client.teams.iter()] == [42]
        assert (await client.teams.get(42)).name == "Investments"
        assert [u.id async for u in client.users.iter()] == [4]
        assert (await client.users.get(UserId(4))).id == 4
        await client.feedback.send("mcp", "s", "b")
        assert (await client.interactions.get_meeting(77)).id == 77
        assert [c async for c in client.notes.iter_attached_companies(NoteId(5))] == []
    assert fb.called


@respx.mock
def test_team_members_and_accessible_lists_page() -> None:
    members_url = f"{V2}/teams/42/members"
    respx.get(members_url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{**TEAM["membersPreview"]["data"][0], "id": 1002}]))
    )
    first = respx.get(members_url).mock(
        return_value=Response(
            200, json=_page(TEAM["membersPreview"]["data"], f"{members_url}?cursor=p2")
        )
    )
    lists = respx.get(f"{V2}/teams/42/accessible-lists").mock(
        return_value=Response(200, json=_page(TEAM["accessibleListsPreview"]["data"]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        members = list(client.teams.iter_members(42))
        page = client.teams.list_accessible_lists(42, limit=5, total_count=True)
    assert [m.id for m in members] == [1001, 1002]
    assert members[0].user.id == 4
    assert page.data[0].name == "Dealflow" and page.data[0].is_public is False
    assert _version(first) == "2026-09-17"
    assert lists.calls[0].request.url.params["limit"] == "5"
    assert lists.calls[0].request.url.params["totalCount"] == "true"


@respx.mock
async def test_async_team_members_and_accessible_lists() -> None:
    respx.get(f"{V2}/teams/42/members").mock(
        return_value=Response(200, json=_page(TEAM["membersPreview"]["data"]))
    )
    respx.get(f"{V2}/teams/42/accessible-lists").mock(
        return_value=Response(200, json=_page(TEAM["accessibleListsPreview"]["data"]))
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        assert [m.id async for m in client.teams.iter_members(42)] == [1001]
        assert (await client.teams.list_accessible_lists(42)).data[0].id == 9
