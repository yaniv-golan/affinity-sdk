"""CLI tests: `note create/update/delete` on the V2 API (output keys as in `note get`)."""

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
CREATOR = {
    "id": 2,
    "firstName": "Ann",
    "lastName": "Lee",
    "primaryEmailAddress": "a@x.co",
    "type": "internal",
}
NOTE = {
    "id": 10,
    "type": "entities",
    "content": {"html": "<p>Created</p>"},
    "creator": CREATOR,
    "mentions": [],
    "createdAt": "2024-01-01T00:00:00Z",
    "updatedAt": None,
}
KEYS = [
    "id",
    "type",
    "creatorId",
    "content",
    "personIds",
    "associatedPersonIds",
    "interactionPersonIds",
    "mentionedPersonIds",
    "companyIds",
    "opportunityIds",
    "interactionId",
    "interactionType",
    "isMeeting",
    "parentId",
    "createdAt",
    "updatedAt",
]


def _run(args: list[str], **kwargs: Any) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV, **kwargs)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def test_create_keeps_the_note_get_keys(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{V2}/notes").mock(return_value=Response(201, json=NOTE))
    code, out = _run(
        [
            "note",
            "create",
            "--content",
            "Created",
            "--person-id",
            "3",
            "--company-id",
            "4",
            "--skip-duplicate-check",
        ]
    )
    assert code == 0, out
    note = out["data"]["note"]
    assert list(note) == KEYS
    assert note["type"] == "html"
    assert note["creatorId"] == 2
    assert note["content"] == "<p>Created</p>"
    assert note["personIds"] == [3]
    assert note["companyIds"] == [4]
    assert note["opportunityIds"] == []
    assert json.loads(route.calls[0].request.content) == {
        "type": "entities",
        "content": {"html": "<p>Created</p>"},
        "persons": [{"id": 3}],
        "companies": [{"id": 4}],
    }


def test_create_reply(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{V2}/notes").mock(
        return_value=Response(201, json={**NOTE, "type": "user-reply", "parent": {"id": 9}})
    )
    code, out = _run(["note", "create", "--content", "ok", "--parent-id", "9"])
    assert code == 0, out
    assert out["data"]["note"]["parentId"] == 9


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--parent-id", "9", "--person-id", "3"], "reply"),
        (["--type", "html", "--person-id", "3"], "Allowed"),
    ],
)
def test_create_usage_errors(args: list[str], message: str) -> None:
    content = '<p style="x">a</p>' if "html" in args else "a"
    code, out = _run(["note", "create", "--content", content, "--skip-duplicate-check", *args])
    assert code == 2, out
    assert message in out["error"]["message"]


def test_create_type_choices_are_plain_text_or_html() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "note",
            "create",
            "--content",
            "x",
            "--person-id",
            "3",
            "--type",
            "ai-notetaker",
        ],
        env=ENV,
    )
    assert result.exit_code == 2


def test_update_html_and_read_back(respx_mock: respx.MockRouter) -> None:
    update = respx_mock.post(f"{V2}/notes/10").mock(return_value=Response(204))
    respx_mock.get(f"{V2}/notes/10").mock(
        return_value=Response(
            200,
            json={
                **NOTE,
                "content": {"html": "<p><strong>Updated</strong></p>"},
                "personsPreview": {"data": [{**CREATOR, "id": 3}], "totalCount": 1},
                "companiesPreview": {"data": [], "totalCount": 0},
                "opportunitiesPreview": {"data": [], "totalCount": 0},
                "repliesCount": 0,
            },
        )
    )
    code, out = _run(
        ["note", "update", "10", "--content", "<p><b>Updated</b></p>", "--type", "html"]
    )
    assert code == 0, out
    assert json.loads(update.calls[0].request.content) == {
        "content": {"html": "<p><strong>Updated</strong></p>"}
    }
    assert out["data"]["note"]["content"] == "<p><strong>Updated</strong></p>"
    assert out["data"]["note"]["personIds"] == [3]


def test_delete(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.delete(f"{V2}/notes/10").mock(return_value=Response(204))
    code, out = _run(["note", "delete", "10", "--yes"])
    assert code == 0 and out["data"] == {"success": True}
    assert route.called
