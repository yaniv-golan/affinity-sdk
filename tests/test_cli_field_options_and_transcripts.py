"""CLI tests: `field options ls/create/update/delete` and `transcript ls/get`."""

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
OPTS = f"{V2}/lists/9/fields/field-5/dropdown-options"
RANKED = [
    {"id": 1, "text": "Lead", "type": "ranked-dropdown", "rank": 0, "color": "gray"},
    {"id": 2, "text": "Won", "type": "ranked-dropdown", "rank": 3, "color": "green"},
]


@pytest.fixture(autouse=True)
def _list_9(respx_mock: respx.MockRouter) -> None:
    """`--list-id 9` resolves the list first (V1 GET /lists/9)."""
    respx_mock.get("https://api.affinity.co/lists/9").mock(
        return_value=Response(
            200,
            json={
                "id": 9,
                "name": "Pipeline",
                "type": 8,
                "public": False,
                "owner_id": 1,
                "creator_id": 1,
                "list_size": 10,
            },
        )
    )


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _run(args: list[str], **kwargs: Any) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV, **kwargs)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


# --- field options --------------------------------------------------------------------------------


def test_options_ls_for_a_list_field(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(OPTS).mock(return_value=Response(200, json=_page(RANKED)))
    code, out = _run(["field", "options", "ls", "field-5", "--list-id", "9"])
    assert code == 0, out
    assert [o["text"] for o in out["data"]["options"]] == ["Lead", "Won"]
    assert out["data"]["options"][1]["type"] == "ranked-dropdown"


def test_options_ls_for_a_global_company_field(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/companies/fields/field-7/dropdown-options").mock(
        return_value=Response(200, json=_page([{"id": 5, "text": "Seed", "type": "dropdown"}]))
    )
    code, out = _run(["field", "options", "ls", "field-7", "--entity-type", "company"])
    assert code == 0, out
    assert out["data"]["options"][0]["id"] == 5


def test_options_ls_needs_exactly_one_scope() -> None:
    code, out = _run(["field", "options", "ls", "field-5"])
    assert code == 2 and out["error"]["type"] == "usage_error"


def test_options_create_takes_type_rank_and_color_from_the_field(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(OPTS).mock(return_value=Response(200, json=_page(RANKED)))
    created = respx_mock.post(OPTS).mock(
        return_value=Response(
            201,
            json={"id": 3, "text": "DD", "type": "ranked-dropdown", "rank": 4, "color": "white"},
        )
    )
    code, out = _run(["field", "options", "create", "field-5", "--list-id", "9", "--text", "DD"])
    assert code == 0, out
    assert json.loads(created.calls[0].request.content) == {
        "type": "ranked-dropdown",
        "text": "DD",
        "rank": 4,
        "color": "white",
    }
    assert out["data"]["option"]["id"] == 3


def test_options_create_without_options_needs_type(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(OPTS).mock(return_value=Response(200, json=_page([])))
    code, out = _run(["field", "options", "create", "field-5", "--list-id", "9", "--text", "X"])
    assert code == 2 and "--type" in out["error"]["message"]


def test_options_create_rejects_status_values_on_a_ranked_field(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(OPTS).mock(return_value=Response(200, json=_page(RANKED)))
    code, out = _run(
        [
            "field",
            "options",
            "create",
            "field-5",
            "--list-id",
            "9",
            "--text",
            "X",
            "--status-category",
            "won",
        ]
    )
    assert code == 2 and "status-dropdown" in out["error"]["message"]


def test_options_update_sends_only_given_values(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{OPTS}/2").mock(return_value=Response(204))
    respx_mock.get(f"{OPTS}/2").mock(
        return_value=Response(200, json={**RANKED[1], "text": "Closed won"})
    )
    code, out = _run(
        ["field", "options", "update", "field-5", "2", "--list-id", "9", "--text", "Closed won"]
    )
    assert code == 0, out
    assert json.loads(route.calls[0].request.content) == {"text": "Closed won"}
    assert out["data"]["option"]["text"] == "Closed won"


def test_options_delete_needs_confirmation_without_a_terminal(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{OPTS}/2").mock(return_value=Response(200, json=RANKED[1]))
    deleted = respx_mock.delete(f"{OPTS}/2").mock(return_value=Response(204))
    code, out = _run(["field", "options", "delete", "field-5", "2", "--list-id", "9"])
    assert code == 2 and "--yes" in out["error"]["hint"]
    assert not deleted.calls


def test_options_delete_prompt_names_the_option_and_the_data_loss(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{OPTS}/2").mock(return_value=Response(200, json=RANKED[1]))
    deleted = respx_mock.delete(f"{OPTS}/2").mock(return_value=Response(204))
    result = CliRunner().invoke(
        cli,
        ["--json", "field", "options", "delete", "field-5", "2", "--list-id", "9"],
        env=ENV,
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert 'Delete option 2 "Won" of field-5?' in result.output
    assert "cannot be recovered" in result.output
    assert len(deleted.calls) == 1


def test_options_delete_with_yes(respx_mock: respx.MockRouter) -> None:
    deleted = respx_mock.delete(f"{OPTS}/2").mock(return_value=Response(204))
    code, out = _run(["field", "options", "delete", "field-5", "2", "--list-id", "9", "--yes"])
    assert code == 0 and out["data"] == {"success": True}
    assert deleted.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"


def test_options_delete_is_destructive_in_json_help() -> None:
    result = CliRunner().invoke(cli, ["--help", "--json"])
    commands = {c["name"]: c for c in json.loads(result.output)["commands"]}
    assert commands["field options delete"]["destructive"] is True
    assert commands["field options create"]["destructive"] is False
    assert commands["transcript get"]["category"] == "read"


# --- transcripts ----------------------------------------------------------------------------------

NOTE = {
    "id": 77,
    "type": "ai-notetaker",
    "content": {"html": "<p>Summary</p>"},
    "creator": {
        "id": 5,
        "firstName": "Ann",
        "lastName": "Lee",
        "primaryEmailAddress": None,
        "type": "internal",
    },
    "createdAt": "2025-06-01T10:00:00Z",
    "interaction": {"id": 900, "type": "meeting"},
    "transcriptId": 11,
}
BASE = {"id": 11, "createdAt": "2025-06-01T10:00:00Z", "languageCode": "en", "note": NOTE}
FRAGS = [
    {
        "content": f"line {i}",
        "speaker": "Ann",
        "startTimestamp": "00:00:0{i}",
        "endTimestamp": "00:00:0{i}",
    }
    for i in range(3)
]


def test_transcript_ls_projects_the_note(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{V2}/transcripts").mock(
        return_value=Response(200, json=_page([BASE], f"{V2}/transcripts?cursor=n"))
    )
    code, out = _run(["transcript", "ls", "--created-after", "2025-06-01T00:00:00Z"])
    assert code == 0, out
    assert route.calls[0].request.url.params["filter"] == "createdAt>=2025-06-01T00:00:00Z"
    row = out["data"]["transcripts"][0]
    assert row["note"] == {
        "noteId": 77,
        "type": "ai-notetaker",
        "interactionId": 900,
        "transcriptId": 11,
    }
    assert out["meta"]["pagination"]["nextCursor"] == f"{V2}/transcripts?cursor=n"


def test_transcript_ls_with_note_includes_it(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/transcripts").mock(return_value=Response(200, json=_page([BASE])))
    code, out = _run(["transcript", "ls", "--with-note"])
    assert code == 0, out
    assert out["data"]["transcripts"][0]["note"]["content"]["html"] == "<p>Summary</p>"


def test_transcript_get_preview_and_all(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/transcripts/11").mock(
        return_value=Response(
            200, json={**BASE, "fragmentsPreview": {"data": FRAGS[:1], "totalCount": 3}}
        )
    )
    respx_mock.get(f"{V2}/transcripts/11/fragments", params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page(FRAGS[2:]))
    )
    respx_mock.get(f"{V2}/transcripts/11/fragments").mock(
        return_value=Response(
            200, json=_page(FRAGS[:2], f"{V2}/transcripts/11/fragments?cursor=p2")
        )
    )
    code, out = _run(["transcript", "get", "11"])
    assert code == 0, out
    assert [f["content"] for f in out["data"]["transcript"]["fragments"]] == ["line 0"]
    assert out["data"]["transcript"]["fragmentsTotal"] == 3
    assert any("Use --all" in w for w in out["warnings"])

    code, out = _run(["transcript", "get", "11", "--all"])
    assert code == 0, out
    assert [f["content"] for f in out["data"]["transcript"]["fragments"]] == [
        "line 0",
        "line 1",
        "line 2",
    ]
    assert out["data"]["transcript"]["note"]["transcriptId"] == 11
