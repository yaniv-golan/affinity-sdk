"""CLI tests: `webhook ls/get/create/update/delete` (V2 webhooks)."""

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
HOOK = {
    "id": 7,
    "url": "https://hooks.example.org/affinity",
    "subscriptions": ["list_entry.created"],
    "status": "active",
    "creator": {"id": 5},
    "createdAt": "2026-08-01T10:00:00Z",
    "updatedAt": None,
}
ROW = {
    "id": 7,
    "url": "https://hooks.example.org/affinity",
    "status": "active",
    "subscriptions": ["list_entry.created"],
    "allEvents": False,
    "creatorId": 5,
    "createdAt": "2026-08-01T10:00:00Z",
    "updatedAt": None,
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _run(args: list[str], **kwargs: Any) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV, **kwargs)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def test_ls_rows_filters_and_cursor(respx_mock: respx.MockRouter) -> None:
    url = f"{V2}/webhooks"
    route = respx_mock.get(url).mock(
        return_value=Response(200, json=_page([HOOK, {**HOOK, "id": 8, "subscriptions": []}]))
    )
    code, out = _run(
        ["webhook", "ls", "--created-after", "2026-01-01T00:00:00Z", "--id", "7", "--id", "8"]
    )
    assert code == 0, out
    assert out["data"]["webhooks"][0] == ROW
    assert out["data"]["webhooks"][1]["allEvents"] is True
    params = route.calls[0].request.url.params
    assert params["filter"] == "(id=7 | id=8) & createdAt>=2026-01-01T00:00:00Z"
    assert route.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"


def test_ls_cursor_cannot_be_combined_with_filters() -> None:
    code, out = _run(["webhook", "ls", "--cursor", f"{V2}/webhooks?cursor=x", "--id", "7"])
    assert code == 2
    assert "--cursor" in out["error"]["message"]


def test_get(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/webhooks/7").mock(return_value=Response(200, json=HOOK))
    code, out = _run(["webhook", "get", "7"])
    assert code == 0, out
    assert out["data"]["webhook"] == ROW


def test_create_needs_confirmation_without_a_terminal(respx_mock: respx.MockRouter) -> None:
    created = respx_mock.post(f"{V2}/webhooks").mock(return_value=Response(201, json=HOOK))
    code, out = _run(
        ["webhook", "create", "--url", "https://hooks.example.org/a", "--event", "note.created"]
    )
    assert code == 2 and "--yes" in out["error"]["hint"]
    assert "outside" in out["error"]["message"]
    assert not created.calls


def test_create_prompt_names_url_and_events(respx_mock: respx.MockRouter) -> None:
    created = respx_mock.post(f"{V2}/webhooks").mock(return_value=Response(201, json=HOOK))
    result = CliRunner().invoke(
        cli,
        ["--json", "webhook", "create", "--url", "https://hooks.example.org/a", "--all-events"],
        env=ENV,
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert "https://hooks.example.org/a" in result.output
    assert "EVERY event" in result.output
    assert json.loads(created.calls[0].request.content) == {
        "url": "https://hooks.example.org/a",
        "subscriptions": [],
    }


def test_create_with_yes(respx_mock: respx.MockRouter) -> None:
    created = respx_mock.post(f"{V2}/webhooks").mock(return_value=Response(201, json=HOOK))
    code, out = _run(
        [
            "webhook",
            "create",
            "--url",
            "https://hooks.example.org/a",
            "--event",
            "list_entry.created",
            "--event",
            "note.created",
            "--yes",
        ]
    )
    assert code == 0, out
    assert out["data"]["webhook"] == ROW
    assert json.loads(created.calls[0].request.content)["subscriptions"] == [
        "list_entry.created",
        "note.created",
    ]


@pytest.mark.parametrize(
    "extra",
    [
        [],  # neither --event nor --all-events
        ["--event", "note.created", "--all-events"],
    ],
)
def test_create_needs_exactly_one_of_event_or_all_events(extra: list[str]) -> None:
    code, out = _run(["webhook", "create", "--url", "https://h.example.org", *extra, "--yes"])
    assert code == 2
    assert "--all-events" in out["error"]["message"]


def test_unknown_event_is_a_usage_error() -> None:
    result = CliRunner().invoke(
        cli,
        ["--json", "webhook", "create", "--url", "https://h.example.org", "--event", "nope"],
        env=ENV,
    )
    assert result.exit_code == 2


def test_update_sends_only_given_fields_and_reads_back(respx_mock: respx.MockRouter) -> None:
    update = respx_mock.post(f"{V2}/webhooks/7").mock(return_value=Response(204))
    respx_mock.get(f"{V2}/webhooks/7").mock(
        return_value=Response(200, json={**HOOK, "status": "disabled"})
    )
    code, out = _run(["webhook", "update", "7", "--status", "disabled", "--yes"])
    assert code == 0, out
    assert json.loads(update.calls[0].request.content) == {"status": "disabled"}
    assert out["data"]["webhook"]["status"] == "disabled"


def test_update_with_nothing_to_change_is_a_usage_error() -> None:
    code, out = _run(["webhook", "update", "7", "--yes"])
    assert code == 2
    assert "--status" in out["error"]["message"]


def test_update_needs_confirmation_without_a_terminal(respx_mock: respx.MockRouter) -> None:
    update = respx_mock.post(f"{V2}/webhooks/7").mock(return_value=Response(204))
    code, out = _run(["webhook", "update", "7", "--url", "https://hooks.example.org/b"])
    assert code == 2 and "--yes" in out["error"]["hint"]
    assert not update.calls


def test_delete(respx_mock: respx.MockRouter) -> None:
    deleted = respx_mock.delete(f"{V2}/webhooks/7").mock(return_value=Response(204))
    code, out = _run(["webhook", "delete", "7", "--yes"])
    assert code == 0 and out["data"] == {"success": True}
    assert len(deleted.calls) == 1


def test_readonly_blocks_writes(respx_mock: respx.MockRouter) -> None:
    deleted = respx_mock.delete(f"{V2}/webhooks/7").mock(return_value=Response(204))
    result = CliRunner().invoke(
        cli, ["--json", "--readonly", "webhook", "delete", "7", "--yes"], env=ENV
    )
    assert result.exit_code != 0
    assert not deleted.calls


def test_writes_are_destructive_in_json_help() -> None:
    result = CliRunner().invoke(cli, ["--help", "--json"])
    commands = {c["name"]: c for c in json.loads(result.output)["commands"]}
    for name in ("webhook create", "webhook update", "webhook delete"):
        assert commands[name]["destructive"] is True, name
        assert commands[name]["category"] == "write", name
    for name in ("webhook ls", "webhook get"):
        assert commands[name]["category"] == "read", name
