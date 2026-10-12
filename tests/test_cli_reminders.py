"""CLI tests: `reminder ls/get/create/update/delete` on the V2 API (V1 for a type change)."""

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
V1 = "https://api.affinity.co"
ONE_TIME = {
    "id": 11,
    "type": "one-time",
    "status": "active",
    "content": "Call back",
    "dueDate": "2026-11-01T09:00:00Z",
    "recurrence": None,
    "creator": {"id": 1},
    "owner": {"id": 2},
    "completer": None,
    "person": {"id": 5},
    "company": None,
    "opportunity": None,
    "createdAt": "2026-10-01T09:00:00Z",
    "updatedAt": None,
    "completedAt": None,
}
RECURRING = {
    **ONE_TIME,
    "id": 12,
    "type": "recurring",
    "recurrence": {"periodDays": 30, "resetTrigger": "event"},
    "person": None,
    "company": {"id": 7},
}
ROW = {
    "id": 11,
    "type": "one-time",
    "status": "active",
    "content": "Call back",
    "dueDate": "2026-11-01T09:00:00Z",
    "resetType": None,
    "reminderDays": None,
    "ownerId": 2,
    "creatorId": 1,
    "completerId": None,
    "personId": 5,
    "companyId": None,
    "opportunityId": None,
    "createdAt": "2026-10-01T09:00:00Z",
    "updatedAt": None,
    "completedAt": None,
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _run(args: list[str], **kwargs: Any) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV, **kwargs)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def test_ls_rows_filters_and_cursor(respx_mock: respx.MockRouter) -> None:
    url = f"{V2}/reminders"
    route = respx_mock.get(url).mock(
        return_value=Response(200, json=_page([ONE_TIME, RECURRING], f"{url}?cursor=abc"))
    )
    code, out = _run(
        ["reminder", "ls", "--person-id", "5", "--status", "active", "--reset-type", "meeting"]
    )
    assert code == 0, out
    assert out["data"]["reminders"][0] == ROW
    assert out["data"]["reminders"][1]["resetType"] == "event"
    assert out["data"]["reminders"][1]["reminderDays"] == 30
    assert out["data"]["reminders"][1]["companyId"] == 7
    assert out["meta"]["pagination"]["reminders"]["nextCursor"] == f"{url}?cursor=abc"
    assert route.calls[0].request.url.params["filter"] == (
        "person.id=5 & resetTrigger=event & status=active"
    )


def test_ls_page_size_is_capped_at_100() -> None:
    code, out = _run(["reminder", "ls", "--page-size", "500"])
    assert code == 2
    assert "100" in out["error"]["message"]


def test_ls_all_follows_pages(respx_mock: respx.MockRouter) -> None:
    url = f"{V2}/reminders"
    respx_mock.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([RECURRING]))
    )
    respx_mock.get(url).mock(return_value=Response(200, json=_page([ONE_TIME], f"{url}?cursor=p2")))
    code, out = _run(["reminder", "ls", "--all"])
    assert code == 0, out
    assert [r["id"] for r in out["data"]["reminders"]] == [11, 12]


def test_get(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    code, out = _run(["reminder", "get", "11"])
    assert code == 0, out
    assert out["data"]["reminder"] == ROW


def test_create_on_v2(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{V2}/reminders").mock(return_value=Response(201, json=ONE_TIME))
    code, out = _run(
        [
            "reminder",
            "create",
            "--type",
            "one-time",
            "--owner-id",
            "2",
            "--person-id",
            "5",
            "--due-date",
            "2026-11-01T09:00:00Z",
            "--content",
            "Call back",
        ]
    )
    assert code == 0, out
    assert json.loads(route.calls[0].request.content) == {
        "type": "one-time",
        "owner": {"id": 2},
        "entity": {"type": "person", "id": 5},
        "content": "Call back",
        "dueDate": "2026-11-01T09:00:00Z",
    }
    assert out["data"]["reminder"]["id"] == 11


def test_create_without_entity_is_a_usage_error() -> None:
    code, out = _run(
        [
            "reminder",
            "create",
            "--type",
            "recurring",
            "--owner-id",
            "2",
            "--reset-type",
            "email",
            "--reminder-days",
            "30",
        ]
    )
    assert code == 2
    assert "person, company or opportunity" in out["error"]["message"]


def test_create_one_time_without_due_date_is_a_usage_error() -> None:
    code, out = _run(
        ["reminder", "create", "--type", "one-time", "--owner-id", "2", "--person-id", "5"]
    )
    assert code == 2
    assert "due" in out["error"]["message"]


def test_update_completed_sends_only_completion(respx_mock: respx.MockRouter) -> None:
    update = respx_mock.post(f"{V2}/reminders/11").mock(return_value=Response(204))
    respx_mock.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    code, out = _run(["reminder", "update", "11", "--completed"])
    assert code == 0, out
    assert list(json.loads(update.calls[0].request.content)) == ["completedAt"]


def test_update_clear_content(respx_mock: respx.MockRouter) -> None:
    update = respx_mock.post(f"{V2}/reminders/11").mock(return_value=Response(204))
    respx_mock.get(f"{V2}/reminders/11").mock(return_value=Response(200, json=ONE_TIME))
    code, out = _run(["reminder", "update", "11", "--clear-content"])
    assert code == 0, out
    assert json.loads(update.calls[0].request.content) == {"content": None}


def test_update_with_nothing_is_a_usage_error() -> None:
    code, out = _run(["reminder", "update", "11"])
    assert code == 2
    assert "nothing to update" in out["error"]["message"].lower()


def test_update_type_goes_through_v1(respx_mock: respx.MockRouter) -> None:
    v1 = respx_mock.put(f"{V1}/reminders/11").mock(
        return_value=Response(
            200,
            json={
                "id": 11,
                "type": 0,
                "status": 1,
                "due_date": "2026-11-01T09:00:00Z",
                "owner": {"id": 2},
                "created_at": "2026-10-01T09:00:00Z",
            },
        )
    )
    code, out = _run(
        ["reminder", "update", "11", "--type", "one-time", "--due-date", "2026-11-01T09:00:00Z"]
    )
    assert code == 0, out
    assert json.loads(v1.calls[0].request.content) == {
        "type": 0,
        "due_date": "2026-11-01T09:00:00Z",
    }
    assert out["data"]["reminder"]["type"] == "one-time"


def test_delete(respx_mock: respx.MockRouter) -> None:
    deleted = respx_mock.delete(f"{V2}/reminders/11").mock(return_value=Response(204))
    code, out = _run(["reminder", "delete", "11", "--yes"])
    assert code == 0 and out["data"] == {"success": True}
    assert deleted.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"
