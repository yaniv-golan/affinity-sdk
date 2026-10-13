"""Review fixes, SDK and CLI commands: id iterables, list-size cache on deletes, bool field
writes from the CLI, opportunity expansion errors, history-bulk exit codes, field delete
cache invalidation, and --unset-value delete results."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.exceptions import (
    AuthorizationError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from affinity.models.types import CompanyId, ListId, OpportunityId, PersonId
from affinity.services.companies import AsyncCompanyService, CompanyService
from affinity.services.lists import AsyncListService, ListService
from affinity.services.opportunities import AsyncOpportunityService, OpportunityService
from affinity.services.org import AsyncTeamService, AsyncUserService, TeamService, UserService
from affinity.services.persons import AsyncPersonService, PersonService
from affinity.services.reminders import AsyncReminderService, ReminderService
from affinity.services.webhooks import AsyncWebhookService, WebhookService

V1 = "https://v1.example"
V2 = "https://v2.example/v2"

Handler = Callable[[httpx.Request], httpx.Response]


def _sync(handler: Handler) -> HTTPClient:
    return HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )


def _async(handler: Handler) -> AsyncHTTPClient:
    return AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )


def _recorder() -> tuple[list[httpx.Request], Handler]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    return seen, handler


# --- 1. ids given as a generator ----------------------------------------------------------------


def _gen(*values: int) -> Any:
    return (v for v in values)


def _sent_ids(request: httpx.Request) -> list[str]:
    """The ids a request filters on: ``ids=`` params, or a V2 ``filter=(id=1 | id=2)``."""
    ids = request.url.params.get_list("ids")
    if ids:
        return ids
    filt = request.url.params.get("filter", "")
    return [part.strip(" ()")[3:] for part in filt.split("|") if part.strip(" ()")]


def test_list_accepts_an_ids_generator() -> None:
    seen, handler = _recorder()
    http = _sync(handler)
    try:
        calls = [
            lambda ids: CompanyService(http).list(ids=ids),
            lambda ids: PersonService(http).list(ids=ids),
            lambda ids: OpportunityService(http).list(ids=ids),
            lambda ids: WebhookService(http).list(ids=ids),
            lambda ids: TeamService(http).list(ids=ids),
            lambda ids: UserService(http).list(ids=ids),
            lambda ids: ReminderService(http).list(ids=ids),
        ]
        for call in calls:
            seen.clear()
            call(_gen(1, 2))
            assert len(seen) == 1
            assert _sent_ids(seen[0]) == ["1", "2"]
            # an empty generator is an empty id filter: no request
            seen.clear()
            assert call(_gen()).data == []
            assert seen == []
        # iter/all pass the generator through to the first page
        seen.clear()
        list(CompanyService(http).iter(ids=_gen(3)))
        assert _sent_ids(seen[0]) == ["3"]
    finally:
        http.close()


async def test_async_list_accepts_an_ids_generator() -> None:
    seen, handler = _recorder()
    http = _async(handler)
    try:
        calls = [
            lambda ids: AsyncCompanyService(http).list(ids=ids),
            lambda ids: AsyncPersonService(http).list(ids=ids),
            lambda ids: AsyncOpportunityService(http).list(ids=ids),
            lambda ids: AsyncWebhookService(http).list(ids=ids),
            lambda ids: AsyncTeamService(http).list(ids=ids),
            lambda ids: AsyncUserService(http).list(ids=ids),
            lambda ids: AsyncReminderService(http).list(ids=ids),
        ]
        for call in calls:
            seen.clear()
            await call(_gen(1, 2))
            assert len(seen) == 1
            assert _sent_ids(seen[0]) == ["1", "2"]
            seen.clear()
            assert (await call(_gen())).data == []
            assert seen == []
    finally:
        await http.close()


# --- 2. list-size cache dropped by entity deletes ------------------------------------------------


def _size_handler() -> Handler:
    size_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal size_reads
        if request.method == "GET" and request.url.path == "/lists/100":
            size_reads += 1
            return httpx.Response(
                200,
                json={
                    "id": 100,
                    "name": "Deals",
                    "type": 8,
                    "public": False,
                    "owner_id": 1,
                    "list_size": size_reads,
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={})

    return handler


@pytest.mark.parametrize(
    "delete",
    [
        lambda http: OpportunityService(http).delete(OpportunityId(9)),
        lambda http: PersonService(http).delete(PersonId(12345)),
        lambda http: CompanyService(http).delete(CompanyId(12345)),
    ],
)
def test_entity_delete_drops_cached_list_sizes(delete: Callable[[HTTPClient], Any]) -> None:
    http = _sync(_size_handler())
    try:
        lists = ListService(http)
        assert lists.get_size(ListId(100)) == 1
        assert lists.get_size(ListId(100)) == 1  # cached
        delete(http)
        assert lists.get_size(ListId(100)) == 2
    finally:
        http.close()


async def test_async_entity_deletes_drop_cached_list_sizes() -> None:
    http = _async(_size_handler())
    try:
        lists = AsyncListService(http)
        assert await lists.get_size(ListId(100)) == 1
        assert await lists.get_size(ListId(100)) == 1
        await AsyncOpportunityService(http).delete(OpportunityId(9))
        assert await lists.get_size(ListId(100)) == 2
        await AsyncPersonService(http).delete(PersonId(12345))
        assert await lists.get_size(ListId(100)) == 3
        await AsyncCompanyService(http).delete(CompanyId(12345))
        assert await lists.get_size(ListId(100)) == 4
    finally:
        await http.close()


# --- CLI ---------------------------------------------------------------------------------------

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

import respx
from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

ENV = {"AFFINITY_API_KEY": "test-key"}
LIST_ID = 67890
ENTRY_ID = 123


def _setup_list(respx_mock: respx.MockRouter) -> None:
    from tests.field_write_mocks import mock_list_field_writes

    list_v2 = {
        "id": LIST_ID,
        "name": "Portfolio",
        "type": 0,
        "isPublic": False,
        "ownerId": 100,
        "creatorId": 100,
    }
    respx_mock.get("https://api.affinity.co/v2/lists").mock(
        return_value=Response(200, json={"data": [list_v2], "pagination": {}})
    )
    respx_mock.get(f"https://api.affinity.co/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={
                "id": LIST_ID,
                "name": "Portfolio",
                "type": 0,
                "public": False,
                "owner_id": 100,
                "creator_id": 100,
                "list_size": 1,
            },
        )
    )
    fields_v2 = [
        {"id": "field-100", "name": "Status", "valueType": "text", "allowsMultiple": False},
        {"id": "field-102", "name": "Tags", "valueType": "text", "allowsMultiple": True},
    ]
    fields_v1 = [
        {"id": "field-100", "name": "Status", "value_type": 6, "allows_multiple": False},
        {"id": "field-102", "name": "Tags", "value_type": 6, "allows_multiple": True},
    ]
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(200, json={"data": fields_v2, "pagination": {}})
    )
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(200, json={"data": fields_v1})
    )
    mock_list_field_writes(respx_mock, LIST_ID, fields_v1)


# --- 3. bool in --set-json -----------------------------------------------------------------------


def test_set_json_bool_is_a_validation_error_before_any_write(
    respx_mock: respx.MockRouter,
) -> None:
    _setup_list(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set-json",
            '{"Status": true}',
        ],
        env=ENV,
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["type"] == "validation_error"
    assert "boolean" in payload["error"]["message"]
    writes = [c for c in respx_mock.calls if c.request.method in ("POST", "PATCH", "DELETE")]
    assert writes == []


def test_pre_validate_rejects_bool_for_entity_fields() -> None:
    from affinity.cli.errors import CLIError
    from affinity.cli.field_utils import pre_validate_set_operations

    resolver = MagicMock()
    resolver.get_field_name.return_value = "Status"
    with pytest.raises(CLIError) as info:
        pre_validate_set_operations(resolver, [("field-100", False)])
    assert info.value.exit_code == 2
    assert info.value.error_type == "validation_error"
    resolver.resolve_field_value.assert_not_called()


# --- 4. list export: opportunity expansion errors ------------------------------------------------


def _opps_client(exc: Exception) -> MagicMock:
    client = MagicMock()
    entry = SimpleNamespace(entity=SimpleNamespace(id=777, name="Acme Corp deal"))
    ok_entry = SimpleNamespace(entity=SimpleNamespace(id=778, name="Other deal"))
    client.lists.entries.return_value.pages.return_value = [SimpleNamespace(data=[entry, ok_entry])]

    def get_associations(opp_id: Any) -> Any:
        if int(opp_id) == 777:
            raise exc
        return SimpleNamespace(person_ids=[], company_ids=[12345])

    client.opportunities.get_associations.side_effect = get_associations
    return client


def _fetch_opps(client: MagicMock, on_error: str, warnings: list[str]) -> list[dict[str, Any]]:
    from affinity.cli.commands.list_cmds import _fetch_entity_opportunities

    return _fetch_entity_opportunities(
        client,
        "company",
        CompanyId(12345),
        opps_list_id=ListId(500),
        max_results=None,
        on_error=on_error,
        warnings=warnings,
    )


def test_entity_opportunities_raise_mode_reraises() -> None:
    with pytest.raises(ServerError):
        _fetch_opps(_opps_client(ServerError("boom", status_code=500)), "raise", [])


def test_entity_opportunities_skip_mode_warns_and_continues() -> None:
    warnings: list[str] = []
    opps = _fetch_opps(_opps_client(ServerError("boom", status_code=500)), "skip", warnings)
    assert [o["id"] for o in opps] == [778]
    assert len(warnings) == 1
    assert "777" in warnings[0]


def test_entity_opportunities_not_found_is_empty_without_warning() -> None:
    for mode in ("raise", "skip"):
        warnings: list[str] = []
        opps = _fetch_opps(_opps_client(NotFoundError("gone", status_code=404)), mode, warnings)
        assert [o["id"] for o in opps] == [778]
        assert warnings == []


def test_entity_opportunities_auth_error_reraises_in_skip_mode() -> None:
    with pytest.raises(AuthorizationError):
        _fetch_opps(_opps_client(AuthorizationError("denied", status_code=403)), "skip", [])


def _unreplied(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, on_error: str, warnings: list[str]
) -> Any:
    import affinity.cli.interaction_utils as iu
    from affinity.cli.commands.list_cmds import _fetch_associations
    from affinity.models.types import ListType

    def boom(**_kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr(iu, "check_unreplied", boom)
    client = MagicMock()
    client.companies.get_associated_person_ids.return_value = [PersonId(1)]
    client.companies.get_associated_people.return_value = [MagicMock(id=1, full_name="Jane Doe")]
    return _fetch_associations(
        client,
        ListType.COMPANY,
        12345,
        expand_set={"persons"},
        max_results=None,
        on_error=on_error,
        warnings=warnings,
        check_unreplied=True,
    )


def test_unreplied_failure_skip_mode_keeps_the_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    result = _unreplied(monkeypatch, ServerError("boom", status_code=500), "skip", warnings)
    assert result is not None
    persons, _companies, _opps, _interactions, unreplied = result
    assert len(persons) == 1
    assert unreplied is None
    assert len(warnings) == 1
    assert "unreplied" in warnings[0]


def test_unreplied_failure_raise_mode_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ServerError):
        _unreplied(monkeypatch, ServerError("boom", status_code=500), "raise", [])


def test_unreplied_auth_error_reraises_in_skip_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    with pytest.raises(AuthorizationError):
        _unreplied(monkeypatch, AuthorizationError("denied", status_code=403), "skip", warnings)
    assert warnings == []


# --- 5. field history-bulk: all failed keeps the error's exit code -------------------------------


def _bulk(monkeypatch: pytest.MonkeyPatch, fail: dict[int, Exception]) -> Any:
    from affinity.services.v1_only import AsyncFieldValueChangesService

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
        raise fail[int(list_entry_id)]

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)
    return CliRunner().invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-entry-ids", "10,20,30"],
        env=ENV,
    )


@pytest.mark.parametrize(
    ("errors", "exit_code"),
    [
        ([RateLimitError("429", status_code=429)] * 3, 5),
        (
            [
                RateLimitError("429", status_code=429),
                ServerError("503", status_code=503),
                ServerError("500", status_code=500),
            ],
            5,
        ),
        ([NotFoundError("404", status_code=404)] * 3, 4),
        (
            [
                NotFoundError("404", status_code=404),
                ServerError("500", status_code=500),
                ServerError("500", status_code=500),
            ],
            1,
        ),
    ],
)
def test_history_bulk_all_failed_keeps_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, errors: list[Exception], exit_code: int
) -> None:
    result = _bulk(monkeypatch, dict(zip([10, 20, 30], errors, strict=True)))
    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "All 3 history fetches failed" in payload["error"]["message"]
    if exit_code == 1:
        assert payload["error"]["type"] == "api_error"


# --- 6. field delete: caches dropped only after a confirmed delete -------------------------------


@pytest.mark.parametrize(("body", "exit_code"), [({"success": False}, 1), ({"success": True}, 0)])
def test_field_delete_invalidates_caches_only_on_success(
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    exit_code: int,
) -> None:
    from affinity.cli.session_cache import SessionCache

    dropped: list[str] = []
    monkeypatch.setattr(
        SessionCache, "invalidate_prefix", lambda _self, prefix: dropped.append(prefix)
    )
    respx_mock.delete("https://api.affinity.co/fields/123").mock(
        return_value=Response(200, json=body)
    )
    result = CliRunner().invoke(cli, ["--json", "field", "delete", "field-123", "--yes"], env=ENV)
    assert result.exit_code == exit_code, result.output
    assert bool(dropped) is (exit_code == 0)


# --- 7. --unset-value: success:false is an error -------------------------------------------------


@pytest.mark.parametrize(("body", "exit_code"), [({"success": False}, 1), ({"success": True}, 0)])
def test_unset_value_follows_the_delete_result(
    respx_mock: respx.MockRouter, body: dict[str, Any], exit_code: int
) -> None:
    _setup_list(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-102", "entityId": 12345, "value": "Tag1"}],
        )
    )
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json=body)
    )
    result = CliRunner().invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--unset-value", "Tags", "Tag1"],
        env=ENV,
    )
    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    if exit_code:
        assert payload["error"]["type"] == "api_error"
    else:
        assert payload["data"]["deleted"] == 1


def test_unset_value_failure_after_a_set_is_a_partial_write(respx_mock: respx.MockRouter) -> None:
    _setup_list(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[{"id": 500, "fieldId": "field-102", "entityId": 12345, "value": "Tag1"}],
        )
    )
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": False})
    )
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--unset-value",
            "Tags",
            "Tag1",
        ],
        env=ENV,
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["type"] == "partial_write"
    assert "did not remove" in payload["error"]["message"]


# --- 8. bool anywhere in a --set-json value ------------------------------------------------------


def test_set_json_bool_inside_a_list_is_a_validation_error(respx_mock: respx.MockRouter) -> None:
    _setup_list(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))
    result = CliRunner().invoke(
        cli,
        ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), "--set-json", '{"Tags": [true]}'],
        env=ENV,
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["type"] == "validation_error"
    assert "boolean" in payload["error"]["message"]
    writes = [c for c in respx_mock.calls if c.request.method in ("POST", "PATCH", "DELETE")]
    assert writes == []


@pytest.mark.parametrize(
    "value",
    [["Acme Corp", False], {"city": True}, [{"city": "Paris", "country": True}], [[True]]],
)
def test_pre_validate_rejects_nested_bools(value: Any) -> None:
    from affinity.cli.errors import CLIError
    from affinity.cli.field_utils import pre_validate_set_operations

    resolver = MagicMock()
    resolver.get_field_name.return_value = "Tags"
    with pytest.raises(CLIError) as info:
        pre_validate_set_operations(resolver, [("field-102", value)])
    assert info.value.exit_code == 2
    assert info.value.error_type == "validation_error"
    assert info.value.details["failures"][0]["reason"] == "Affinity has no boolean field type"
    resolver.resolve_field_value.assert_not_called()


def test_text_fields_still_stringify_numbers() -> None:
    from affinity.cli.field_utils import FieldResolver
    from affinity.models.entities import FieldMetadata

    fields = [
        FieldMetadata.model_validate(
            {"id": "field-100", "name": "Status", "valueType": "text", "allowsMultiple": False}
        ),
        FieldMetadata.model_validate(
            {
                "id": "field-102",
                "name": "Tags",
                "valueType": "filterable-text-multi",
                "allowsMultiple": True,
            }
        ),
    ]
    resolver = FieldResolver(fields)
    assert resolver.resolve_field_value("field-100", 5) == ("5", "text")
    assert resolver.resolve_field_value("field-102", [1, "a"]) == (
        ["1", "a"],
        "filterable-text-multi",
    )


# --- 9. --unset-value: a failure reports what the command already changed ------------------------


def _two_tags(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[
                {"id": 500, "fieldId": "field-102", "entityId": 12345, "value": "Tag1"},
                {"id": 501, "fieldId": "field-102", "entityId": 12345, "value": "Tag2"},
            ],
        )
    )


def _unset_two(extra: list[str] | None = None) -> Any:
    args = ["--json", "entry", "field", "Portfolio", str(ENTRY_ID), *(extra or [])]
    args += ["--unset-value", "Tags", "Tag1", "--unset-value", "Tags", "Tag2"]
    return CliRunner().invoke(cli, args, env=ENV)


def test_unset_value_failure_after_an_earlier_delete_is_a_partial_write(
    respx_mock: respx.MockRouter,
) -> None:
    _setup_list(respx_mock)
    _two_tags(respx_mock)
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.delete("https://api.affinity.co/field-values/501").mock(
        return_value=Response(200, json={"success": False})
    )
    result = _unset_two()
    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "partial_write"
    assert "already removed 'Tag1' from Tags" in error["message"]
    assert "did not remove value 'Tag2'" in error["message"]
    assert error["details"]["removed"] == [{"field": "Tags", "value": "Tag1"}]
    assert error["details"]["written"] == []


def test_unset_value_delete_error_after_an_earlier_delete_is_a_partial_write(
    respx_mock: respx.MockRouter,
) -> None:
    _setup_list(respx_mock)
    _two_tags(respx_mock)
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(200, json={"success": True})
    )
    respx_mock.delete("https://api.affinity.co/field-values/501").mock(
        return_value=Response(404, json={"message": "Not found"})
    )
    result = _unset_two()
    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "partial_write"
    assert error["details"]["removed"] == [{"field": "Tags", "value": "Tag1"}]


def test_unset_value_delete_error_after_a_set_is_a_partial_write(
    respx_mock: respx.MockRouter,
) -> None:
    _setup_list(respx_mock)
    _two_tags(respx_mock)
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(404, json={"message": "Not found"})
    )
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--unset-value",
            "Tags",
            "Tag1",
        ],
        env=ENV,
    )
    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "partial_write"
    assert "Status were already updated" in error["message"]
    assert error["details"]["written"] == ["Status"]
    assert "removed" not in error["details"]


def test_unset_value_delete_error_with_nothing_changed_is_not_a_partial_write(
    respx_mock: respx.MockRouter,
) -> None:
    _setup_list(respx_mock)
    _two_tags(respx_mock)
    respx_mock.delete("https://api.affinity.co/field-values/500").mock(
        return_value=Response(404, json={"message": "Not found"})
    )
    result = _unset_two()
    assert result.exit_code != 0, result.output
    assert json.loads(result.stdout)["error"]["type"] != "partial_write"


def test_execute_append_phase_reports_written_field_ids() -> None:
    from affinity.cli.field_utils import FieldResolver, execute_append_phase
    from affinity.models.entities import FieldMetadata

    resolver = FieldResolver(
        [
            FieldMetadata.model_validate(
                {"id": "field-103", "name": "Scores", "valueType": "number-multi"}
            )
        ]
    )
    entries = MagicMock()
    entries.update_field_value.return_value = {"id": "field-103", "value": [1, 5]}
    written: list[str] = []
    execute_append_phase(
        entries=entries,
        list_entry_id=ENTRY_ID,
        append_ops=[("field-103", "5")],
        existing_values_serialized=[
            {"id": 1, "fieldId": "field-103", "entityId": 12345, "value": 1}
        ],
        resolver=resolver,
        written_ids=written,
    )
    assert written == ["field-103"]


def test_append_cli_error_after_a_set_is_a_partial_write(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from affinity.cli import field_utils
    from affinity.cli.errors import CLIError

    _setup_list(respx_mock)
    _two_tags(respx_mock)
    # Tags as a multi-value text field, so --append passes validation and reaches the phase
    multi_tags = {"id": "field-102", "name": "Tags", "valueType": "filterable-text-multi"}
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": "field-100", "name": "Status", "valueType": "text"},
                    {**multi_tags, "allowsMultiple": True},
                ],
                "pagination": {},
            },
        )
    )

    def refuse(**_kwargs: Any) -> Any:
        raise CLIError("Tags would hold too many values", error_type="usage_error", exit_code=2)

    monkeypatch.setattr(field_utils, "execute_append_phase", refuse)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--append",
            "Tags",
            "Tag3",
        ],
        env=ENV,
    )
    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "partial_write"
    assert "Status were already updated" in error["message"]
    assert "too many values" in error["message"]
