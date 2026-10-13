"""CLI command fixes: confirmation refusals under --json, list ls --type paging warning,
field history-bulk failure handling, and interaction-date expansion errors."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

import respx
from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli
from affinity.exceptions import AuthenticationError, AuthorizationError

ENV = {"AFFINITY_API_KEY": "test-key"}


def _last_json(output: str) -> dict:  # type: ignore[type-arg]
    # click < 8.2's CliRunner mixes the stderr prompt into stdout: the JSON is the last line.
    return json.loads(output.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# "n" to a confirmation prompt
# ---------------------------------------------------------------------------


def test_piped_no_under_json_prints_an_envelope_and_exits_1(
    respx_mock: respx.MockRouter,
) -> None:
    result = CliRunner().invoke(cli, ["--json", "person", "delete", "12345"], input="n\n", env=ENV)
    assert result.exit_code == 1
    payload = _last_json(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "aborted"
    assert "nothing was changed" in payload["error"]["message"]
    assert not respx_mock.calls


def test_piped_no_with_trailing_json_flag_prints_an_envelope(
    respx_mock: respx.MockRouter,
) -> None:
    result = CliRunner().invoke(cli, ["person", "delete", "12345", "--json"], input="n\n", env=ENV)
    assert result.exit_code == 1
    assert _last_json(result.stdout)["error"]["type"] == "aborted"
    assert not respx_mock.calls


def test_piped_no_without_json_aborts_as_before(respx_mock: respx.MockRouter) -> None:
    result = CliRunner().invoke(cli, ["person", "delete", "12345"], input="n\n", env=ENV)
    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert '"ok"' not in result.output
    assert not respx_mock.calls


def test_tty_no_under_json_prints_an_envelope(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import affinity.cli.confirm as confirm_mod

    monkeypatch.setattr(
        confirm_mod, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    )
    monkeypatch.setattr(confirm_mod.click, "confirm", lambda *_a, **_k: False)
    result = CliRunner().invoke(cli, ["--json", "person", "delete", "12345"], env=ENV)
    assert result.exit_code == 1
    assert _last_json(result.stdout)["error"]["type"] == "aborted"
    assert not respx_mock.calls


# ---------------------------------------------------------------------------
# list ls --type: empty first page with more pages
# ---------------------------------------------------------------------------


def _lists_page(types: list[int], next_url: str | None) -> Response:
    return Response(
        200,
        json={
            "data": [
                {
                    "id": 100 + i,
                    "name": f"List {i}",
                    "type": t,
                    "isPublic": False,
                    "ownerId": 1,
                    "creatorId": 1,
                }
                for i, t in enumerate(types)
            ],
            "pagination": {"nextUrl": next_url},
        },
    )


def test_list_ls_type_warns_when_first_page_has_none_but_more_pages_exist(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://api.affinity.co/v2/lists").mock(
        return_value=_lists_page([0, 0], "https://api.affinity.co/v2/lists?cursor=abc")
    )
    result = CliRunner().invoke(cli, ["--json", "list", "ls", "--type", "opportunity"], env=ENV)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["lists"] == []
    assert payload["meta"]["pagination"]["lists"]["nextCursor"]
    assert any("later pages" in w and "--all" in w for w in payload["warnings"])


def test_list_ls_type_no_warning_without_more_pages_or_with_matches(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get("https://api.affinity.co/v2/lists")
    route.mock(return_value=_lists_page([0], None))
    result = CliRunner().invoke(cli, ["--json", "list", "ls", "--type", "opportunity"], env=ENV)
    assert result.exit_code == 0, result.output
    assert not any("later pages" in w for w in json.loads(result.stdout)["warnings"])

    route.mock(return_value=_lists_page([0, 8], "https://api.affinity.co/v2/lists?cursor=abc"))
    result = CliRunner().invoke(cli, ["--json", "list", "ls", "--type", "opportunity"], env=ENV)
    payload = json.loads(result.stdout)
    assert len(payload["data"]["lists"]) == 1
    assert not any("later pages" in w for w in payload["warnings"])


# ---------------------------------------------------------------------------
# field history-bulk failures
# ---------------------------------------------------------------------------


def _change() -> object:
    from affinity.models.entities import FieldValueChange

    return FieldValueChange.model_validate(
        {
            "id": 501,
            "fieldId": "field-123",
            "entityId": 100,
            "listEntryId": 10,
            "actionType": 2,
            "value": "Won",
            "changedAt": "2024-06-01T12:00:00Z",
            "changer": {"id": 1, "type": 0, "firstName": "Jane", "lastName": "Doe"},
        }
    )


def _bulk(monkeypatch: pytest.MonkeyPatch, fail: dict[int, Exception]) -> object:
    from affinity.services.v1_only import AsyncFieldValueChangesService

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        eid = int(list_entry_id)
        if eid in fail:
            raise fail[eid]
        return [_change()]

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)
    return CliRunner().invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-entry-ids", "10,20,30"],
        env=ENV,
    )


def test_history_bulk_all_failed_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    boom = Exception("500 Server Error")
    result = _bulk(monkeypatch, {10: boom, 20: boom, 30: boom})
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "api_error"
    assert "All 3" in payload["error"]["message"]
    assert any("0 of 3" in w for w in payload["warnings"])


def test_history_bulk_partial_failure_exits_0_and_marks_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _bulk(monkeypatch, {20: Exception("404 Not Found")})
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["data"]["fieldValueChanges"]) == 2
    assert payload["meta"]["truncated"] is True
    assert payload["meta"]["truncationReason"] == "partialFailure"
    assert any("Entry 20" in w for w in payload["warnings"])


def test_history_bulk_no_failure_is_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _bulk(monkeypatch, {})
    assert result.exit_code == 0, result.output
    assert "truncated" not in json.loads(result.stdout)["meta"]


@pytest.mark.parametrize("exc_cls", [AuthenticationError, AuthorizationError])
def test_history_bulk_auth_error_fails_immediately(
    monkeypatch: pytest.MonkeyPatch, exc_cls: type[Exception]
) -> None:
    result = _bulk(monkeypatch, {20: exc_cls("denied", status_code=401)})
    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert not any("Entry 20" in w for w in payload["warnings"])


# ---------------------------------------------------------------------------
# list export --expand interactions: --expand-on-error
# ---------------------------------------------------------------------------


def _interaction_client(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.companies.get.side_effect = exc
    client.persons.get.side_effect = exc
    return client


def test_interaction_dates_raise_mode_reraises() -> None:
    from affinity.cli.commands.list_cmds import _fetch_interaction_dates

    warnings: list[str] = []
    with pytest.raises(RuntimeError):
        _fetch_interaction_dates(
            _interaction_client(RuntimeError("boom")),
            "company",
            12345,
            on_error="raise",
            warnings=warnings,
        )
    assert warnings == []


def test_interaction_dates_skip_mode_warns_and_returns_none() -> None:
    from affinity.cli.commands.list_cmds import _fetch_interaction_dates

    warnings: list[str] = []
    result = _fetch_interaction_dates(
        _interaction_client(RuntimeError("boom")),
        "person",
        12345,
        on_error="skip",
        warnings=warnings,
    )
    assert result is None
    assert len(warnings) == 1
    assert "person 12345" in warnings[0] and "boom" in warnings[0]


@pytest.mark.parametrize("exc_cls", [AuthenticationError, AuthorizationError])
def test_interaction_dates_auth_error_reraises_even_in_skip_mode(
    exc_cls: type[Exception],
) -> None:
    from affinity.cli.commands.list_cmds import _fetch_associations
    from affinity.models.types import ListType

    client = _interaction_client(exc_cls("denied", status_code=403))
    client.companies.get_associated_person_ids.return_value = []
    warnings: list[str] = []
    with pytest.raises(exc_cls):
        _fetch_associations(
            client,
            ListType.COMPANY,
            12345,
            expand_set={"interactions"},
            max_results=None,
            on_error="skip",
            warnings=warnings,
        )
    assert warnings == []


# --- 23: a delete Affinity reports as failed does not exit 0 -----------------------------------


@pytest.mark.parametrize(
    ("body", "exit_code", "success"),
    [
        ({"success": True}, 0, True),
        ({"success": False}, 1, None),
        ({}, 0, True),  # an empty body (e.g. 204) is a success
    ],
)
def test_delete_exit_code_follows_reported_success(
    respx_mock: respx.MockRouter, body: dict, exit_code: int, success: bool | None
) -> None:
    respx_mock.delete("https://api.affinity.co/organizations/123").mock(
        return_value=Response(200, json=body)
    )
    result = CliRunner().invoke(
        cli,
        ["--json", "company", "delete", "123", "--yes"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.output)
    if success is None:
        assert payload["ok"] is False
        assert payload["error"]["type"] == "api_error"
    else:
        assert payload["data"]["success"] is success
