"""SDK and CLI tests for org-wide V2 reads: interaction feed, merge history, relationships."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
from affinity.types import CompanyId, PersonId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
T0 = datetime(2025, 6, 1, tzinfo=timezone.utc)
PERSON = {
    "id": 7,
    "firstName": "Ann",
    "lastName": "Lee",
    "primaryEmailAddress": "a@x.co",
    "type": "internal",
}
EMAIL = {
    "id": 1,
    "sentAt": "2025-06-02T10:00:00Z",
    "loggingType": "automated",
    "direction": "sent",
    "subject": "Hello",
    "createdAt": "2025-06-02T10:00:00Z",
    "updatedAt": None,
    "from": {"emailAddress": "a@x.co", "person": PERSON},
    "toParticipantsPreview": {
        "data": [{"emailAddress": f"p{i}@y.co", "person": None} for i in range(12)],
        "totalCount": 40,
    },
    "ccParticipantsPreview": {"data": [], "totalCount": 0},
}
MEETING = {
    "id": 2,
    "loggingType": "automated",
    "title": "Sync",
    "startTime": "2025-06-03T09:00:00Z",
    "endTime": None,
    "allDay": False,
    "creator": None,
    "organizer": {"emailAddress": "o@x.co", "person": None},
    "createdAt": "2025-06-03T09:00:00Z",
    "updatedAt": None,
    "attendeesPreview": {"data": [{"emailAddress": "a@x.co", "person": PERSON}], "totalCount": 1},
}
CALL = {**{k: v for k, v in MEETING.items() if k != "organizer"}, "id": 3}
CHAT = {
    "id": 4,
    "sentAt": "2025-06-04T09:00:00Z",
    "loggingType": "manual",
    "direction": "received",
    "creator": PERSON,
    "createdAt": "2025-06-04T09:00:00Z",
    "updatedAt": "2025-06-05T09:00:00Z",
    "participantsPreview": {"data": [PERSON], "totalCount": 1},
}
MERGE = {
    "id": 9,
    "status": "success",
    "taskId": "123e4567-e89b-12d3-a456-426614174000",
    "startedAt": "2025-06-01T00:00:00Z",
    "primaryCompanyId": 10,
    "duplicateCompanyId": 11,
    "completedAt": "2025-06-01T00:01:00Z",
    "errorMessage": None,
}
REL = {
    "person1": {"id": 1, "firstName": "Ann", "lastName": "Lee", "primaryEmailAddress": None},
    "person2": {"id": 2, "firstName": "Bo", "lastName": None, "primaryEmailAddress": "b@z.co"},
    "interactionScore": 0.82,
    "linkedIn": {"connectedOn": "2024-01-01"},
}


def _page(items: list[dict[str, Any]], next_url: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None, **extra}}


def _params(route: Any, i: int = 0) -> dict[str, str]:
    return dict(route.calls[i].request.url.params)


# --- SDK: interactions --------------------------------------------------------------------------


@respx.mock
def test_each_type_filters_its_own_time_field() -> None:
    routes = {
        "emails": respx.get(f"{V2}/emails").mock(return_value=Response(200, json=_page([EMAIL]))),
        "meetings": respx.get(f"{V2}/meetings").mock(
            return_value=Response(200, json=_page([MEETING]))
        ),
        "calls": respx.get(f"{V2}/calls").mock(return_value=Response(200, json=_page([CALL]))),
        "chat_messages": respx.get(f"{V2}/chat-messages").mock(
            return_value=Response(200, json=_page([CHAT]))
        ),
    }
    with Affinity(api_key="k", max_retries=0) as client:
        for name in routes:
            getattr(client.interactions, f"list_{name}")(after=T0, limit=5)
    assert _params(routes["emails"])["filter"] == "sentAt>=2025-06-01T00:00:00Z"
    assert _params(routes["chat_messages"])["filter"] == "sentAt>=2025-06-01T00:00:00Z"
    # The spec's calls example says sentAt; calls and meetings filter on startTime
    assert _params(routes["calls"])["filter"] == "startTime>=2025-06-01T00:00:00Z"
    assert _params(routes["meetings"])["filter"] == "startTime>=2025-06-01T00:00:00Z"


@respx.mock
def test_window_and_sync_filters_combine() -> None:
    route = respx.get(f"{V2}/emails").mock(return_value=Response(200, json=_page([])))
    with Affinity(api_key="k", max_retries=0) as client:
        client.interactions.list_emails(
            after=T0,
            before=datetime(2025, 6, 2, 0, 0, 0, 500, tzinfo=timezone.utc),
            created_after=T0,
            updated_after=T0,
        )
    assert _params(route)["filter"] == (
        "sentAt>=2025-06-01T00:00:00Z & sentAt<2025-06-02T00:00:01Z"
        " & createdAt>=2025-06-01T00:00:00Z & updatedAt>=2025-06-01T00:00:00Z"
    )


@respx.mock
def test_models_flatten_previews() -> None:
    respx.get(f"{V2}/emails").mock(return_value=Response(200, json=_page([EMAIL])))
    respx.get(f"{V2}/meetings").mock(return_value=Response(200, json=_page([MEETING])))
    respx.get(f"{V2}/chat-messages").mock(return_value=Response(200, json=_page([CHAT])))
    with Affinity(api_key="k", max_retries=0) as client:
        email = client.interactions.list_emails().data[0]
        meeting = client.interactions.list_meetings().data[0]
        chat = client.interactions.list_chat_messages().data[0]
    assert email.from_ is not None and email.from_.person is not None
    assert len(email.to) == 12 and email.to_total == 40 and email.cc_total == 0
    assert meeting.organizer is not None and meeting.attendees_total == 1
    assert chat.participants[0].first_name == "Ann" and chat.updated_at is not None


@respx.mock
async def test_async_iter_follows_pages() -> None:
    next_url = f"{V2}/calls?cursor=p2"
    respx.get(f"{V2}/calls", params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{**CALL, "id": 5}]))
    )
    respx.get(f"{V2}/calls").mock(return_value=Response(200, json=_page([CALL], next_url)))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        ids = [c.id async for c in client.interactions.iter_calls()]
    assert ids == [3, 5]


# --- SDK: merges and tasks ----------------------------------------------------------------------


@respx.mock
def test_merge_history_filters_and_task_url() -> None:
    route = respx.get(f"{V2}/company-merges").mock(return_value=Response(200, json=_page([MERGE])))
    respx.get(f"{V2}/company-merges/9").mock(return_value=Response(200, json=MERGE))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.companies.list_merges(
            status="success",
            task_id="https://api.affinity.co/v2/tasks/company-merges/123e4567-e89b-12d3-a456-426614174000",
        )
        state = client.companies.get_merge_state(9)
        with pytest.raises(ValueError, match="'status'"):
            client.companies.list_merges(status="done")
    assert _params(route)["filter"] == (
        "status=success & taskId=123e4567-e89b-12d3-a456-426614174000"
    )
    assert page.data[0].duplicate_company_id == 11
    assert state.status == "success"


@respx.mock
def test_person_merges_and_merge_tasks() -> None:
    person_merge = {k: v for k, v in MERGE.items() if "Company" not in k}
    person_merge |= {"primaryPersonId": 20, "duplicatePersonId": 21}
    respx.get(f"{V2}/person-merges").mock(return_value=Response(200, json=_page([person_merge])))
    tasks = respx.get(f"{V2}/tasks/person-merges").mock(
        return_value=Response(200, json=_page([{"id": MERGE["taskId"], "status": "failed"}]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.persons.list_merges().data[0].primary_person_id == PersonId(20)
        assert client.tasks.list_merge_tasks("person", status="failed").data[0].status == "failed"
    assert _params(tasks)["filter"] == "status=failed"


# --- SDK: relationships -------------------------------------------------------------------------


@respx.mock
def test_relationships_send_the_version_on_every_page() -> None:
    url = f"{V2}/companies/10/relationships"
    second = respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([REL]))
    )
    first = respx.get(url).mock(return_value=Response(200, json=_page([REL], f"{url}?cursor=p2")))
    with Affinity(api_key="k", max_retries=0) as client:
        rows = list(client.companies.iter_relationships(CompanyId(10), min_score=0.5))
    assert len(rows) == 2
    assert _params(first) == {"filter": "interactionScore>=0.5"}
    assert first.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"
    assert second.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"
    assert rows[0].linkedin_connected_on is not None and rows[0].interaction_score == 0.82


@respx.mock
def test_relationship_order_score_and_total() -> None:
    route = respx.get(f"{V2}/persons/2/relationships").mock(
        return_value=Response(200, json=_page([REL], None, totalCount=1))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.persons.list_relationships(
            PersonId(2), min_score=0.00001, order="asc", limit=10, total_count=True
        )
        with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
            client.persons.list_relationships(PersonId(2), min_score=1.5)
    assert _params(route) == {
        "filter": "interactionScore>=0.00001",
        "orderBy": "interactionScore",
        "limit": "10",
        "totalCount": "true",
    }
    assert page.total_count == 1


def test_relationships_refuse_a_client_pinned_to_2024_01_01() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.companies.list_relationships(CompanyId(10))


# --- CLI ---------------------------------------------------------------------------------------


def _run(args: list[str]) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def test_feed_email_rows_cap_participants(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/emails").mock(
        return_value=Response(200, json=_page([EMAIL], f"{V2}/emails?cursor=n"))
    )
    code, out = _run(["interaction", "feed", "--type", "email", "--after", "2025-06-01T00:00:00Z"])
    assert code == 0, out
    row = out["data"]["interactions"][0]
    assert row["type"] == "email" and row["subject"] == "Hello"
    assert row["from"] == {"email": "a@x.co", "personId": 7, "name": "Ann Lee"}
    assert len(row["to"]) == 10 and row["toTotal"] == 40
    assert out["meta"]["pagination"]["nextCursor"] == f"{V2}/emails?cursor=n"


def test_feed_chat_alias(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/chat-messages").mock(return_value=Response(200, json=_page([CHAT])))
    code, out = _run(["interaction", "feed", "--type", "chat"])
    assert code == 0, out
    assert out["data"]["interactions"][0]["type"] == "chat-message"


def test_feed_cursor_cannot_take_filters() -> None:
    code, out = _run(
        ["interaction", "feed", "--type", "email", "--cursor", "https://x", "--after", "-1d"]
    )
    assert code == 2 and "--cursor" in out["error"]["message"]


def test_merge_history_and_task_ls(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/company-merges").mock(return_value=Response(200, json=_page([MERGE])))
    respx_mock.get(f"{V2}/tasks/company-merges").mock(
        return_value=Response(200, json=_page([{"id": MERGE["taskId"], "status": "success"}]))
    )
    code, out = _run(["company", "merge-history", "ls", "--status", "success"])
    assert code == 0, out
    assert out["data"]["merges"][0]["duplicateCompanyId"] == 11
    code, out = _run(["task", "ls", "--kind", "company-merge"])
    assert code == 0, out
    assert out["data"]["tasks"][0]["status"] == "success"


def test_company_relationships(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/companies/10/relationships").mock(
        return_value=Response(200, json=_page([REL]))
    )
    code, out = _run(["company", "relationships", "10", "--min-score", "0.5"])
    assert code == 0, out
    row = out["data"]["relationships"][0]
    assert row["person2"] == {"personId": 2, "name": "Bo", "email": "b@z.co"}
    assert row["interactionScore"] == 0.82 and row["linkedInConnectedOn"] == "2024-01-01"
