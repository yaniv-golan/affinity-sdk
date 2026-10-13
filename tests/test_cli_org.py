"""CLI tests: team, user, meeting get, note attached, feedback send (V2)."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

ENV = {"AFFINITY_API_KEY": "test-key"}
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
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _run(args: list[str], **kwargs: Any) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV, **kwargs)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def test_team_ls_with_members(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{V2}/teams").mock(return_value=Response(200, json=_page([TEAM])))
    code, out = _run(["team", "ls", "--name-contains", "Inv", "--with-members"])
    assert code == 0, out
    row = out["data"]["teams"][0]
    assert row["id"] == 42 and row["name"] == "Investments"
    assert row["members"] == [{"userId": 4, "name": "Ann Lee", "email": "a@x.co"}]
    assert row["membersTotal"] == 12
    params = route.calls[0].request.url.params
    assert params["filter"] == 'name=~"Inv"'
    assert params.get_list("includes") == ["membersPreview"]


def test_team_get(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/teams/42").mock(return_value=Response(200, json=TEAM))
    code, out = _run(["team", "get", "42"])
    assert code == 0, out
    assert out["data"]["team"]["name"] == "Investments"


def test_user_ls_and_get(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{V2}/users").mock(return_value=Response(200, json=_page([USER])))
    respx_mock.get(f"{V2}/users/4").mock(return_value=Response(200, json=USER))
    code, out = _run(["user", "ls", "--term", "ann", "--status", "active"])
    assert code == 0, out
    assert out["data"]["users"][0] == {
        "id": 4,
        "name": "Ann Lee",
        "email": "ann@x.co",
        "status": "active",
        "role": None,
        "emails": None,
    }
    assert route.calls[0].request.url.params["filter"] == "status=active"
    code, out = _run(["user", "get", "4"])
    assert code == 0 and out["data"]["user"]["id"] == 4


def test_meeting_get(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/meetings/77").mock(
        return_value=Response(
            200,
            json={
                "id": 77,
                "type": "meeting",
                "title": "Sync",
                "allDay": False,
                "startTime": "2026-10-01T09:00:00Z",
                "endTime": "2026-10-01T09:30:00Z",
                "attendees": [],
                "createdAt": "2026-09-30T09:00:00Z",
            },
        )
    )
    code, out = _run(["meeting", "get", "77"])
    assert code == 0, out
    assert out["data"]["meeting"]["id"] == 77 and out["data"]["meeting"]["title"] == "Sync"


def test_note_attached(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/notes/5/attached-companies").mock(
        return_value=Response(200, json=_page([{"id": 7, "name": "Acme", "domain": "acme.com"}]))
    )
    respx_mock.get(f"{V2}/notes/5/attached-persons").mock(
        return_value=Response(200, json=_page([]))
    )
    respx_mock.get(f"{V2}/notes/5/attached-opportunities").mock(
        return_value=Response(200, json=_page([{"id": 3, "name": "Deal", "listId": 9}]))
    )
    code, out = _run(["note", "attached", "5"])
    assert code == 0, out
    assert out["data"] == {
        "companies": [{"id": 7, "name": "Acme", "domain": "acme.com"}],
        "persons": [],
        "opportunities": [{"id": 3, "name": "Deal", "listId": 9}],
    }


def test_feedback_send_needs_confirmation(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{V2}/feedback").mock(return_value=Response(204))
    code, out = _run(["feedback", "send", "--type", "api", "--subject", "s", "--body", "b"])
    assert code == 2 and "--yes" in out["error"]["hint"]
    assert not route.called
    code, out = _run(
        ["feedback", "send", "--type", "api", "--subject", "s", "--body", "b", "--yes"]
    )
    assert code == 0 and out["data"] == {"sent": True}
    assert json.loads(route.calls[0].request.content) == {
        "type": "api",
        "subject": "s",
        "body": "b",
    }


def test_new_commands_in_json_help() -> None:
    result = CliRunner().invoke(cli, ["--help", "--json"])
    commands = {c["name"]: c for c in json.loads(result.output)["commands"]}
    for name in ("team ls", "team get", "user ls", "user get", "meeting get", "note attached"):
        assert commands[name]["category"] == "read", name
    assert commands["feedback send"]["category"] == "write"
    assert commands["feedback send"]["destructive"] is True


def test_team_members_and_lists(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/teams/42/members").mock(
        return_value=Response(200, json=_page(TEAM["membersPreview"]["data"]))
    )
    respx_mock.get(f"{V2}/teams/42/accessible-lists").mock(
        return_value=Response(
            200,
            json=_page(
                [
                    {
                        "id": 9,
                        "name": "Dealflow",
                        "type": "company",
                        "isPublic": False,
                        "ownerId": 4,
                        "creatorId": 4,
                        "createdAt": "2026-01-01T00:00:00Z",
                    }
                ]
            ),
        )
    )
    code, out = _run(["team", "members", "42"])
    assert code == 0, out
    assert out["data"]["members"] == [
        {"userId": 4, "name": "Ann Lee", "email": "a@x.co", "addedAt": "2024-03-02T09:00:00Z"}
    ]
    code, out = _run(["team", "lists", "42"])
    assert code == 0, out
    assert out["data"]["lists"][0]["id"] == 9 and out["data"]["lists"][0]["isPublic"] is False
