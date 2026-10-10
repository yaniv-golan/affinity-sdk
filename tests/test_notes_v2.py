"""SDK and CLI tests for V2 note reads (additive; V1 `note ls/get` unchanged)."""

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
from affinity.exceptions import ApiVersionTooOldError, AuthorizationError
from affinity.types import CompanyId, NoteId, OpportunityId, PersonId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
CREATOR = {
    "id": 5,
    "firstName": "Ann",
    "lastName": "Lee",
    "primaryEmailAddress": "a@x.co",
    "type": "internal",
}
BASE = {
    "content": {"html": "<p>Hi <b>Bo</b></p>"},
    "creator": CREATOR,
    "mentions": [
        {
            "type": "person",
            "person": {
                "id": 9,
                "firstName": "Bo",
                "lastName": None,
                "primaryEmailAddress": None,
                "type": "external",
            },
        }
    ],
    "createdAt": "2025-06-01T10:00:00Z",
    "updatedAt": None,
}
ENTITIES = {
    **BASE,
    "id": 1,
    "type": "entities",
    "repliesCount": 2,
    "companiesPreview": {
        "data": [{"id": 10, "name": "Acme", "domain": "acme.com"}],
        "totalCount": 3,
    },
    "personsPreview": {"data": [], "totalCount": 0},
    "opportunitiesPreview": {"data": [{"id": 30, "name": "Deal"}], "totalCount": 1},
}
AI = {
    **BASE,
    "id": 2,
    "type": "ai-notetaker",
    "creator": None,
    "interaction": {"id": 900, "type": "meeting"},
    "transcriptId": 77,
}
REPLY = {**BASE, "id": 3, "type": "user-reply", "parent": {"id": 1}}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


@respx.mock
def test_list_v2_filters_includes_and_variants() -> None:
    route = respx.get(f"{V2}/notes").mock(return_value=Response(200, json=_page([ENTITIES, AI])))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.notes.list_v2(
            creator_id=5,
            created_after=datetime(2025, 6, 1, tzinfo=timezone.utc),
            includes=True,
            limit=50,
        )
    url = route.calls[0].request.url
    assert url.params["filter"] == "creator.id=5 & createdAt>=2025-06-01T00:00:00Z"
    assert url.params.get_list("includes") == [
        "companiesPreview",
        "personsPreview",
        "opportunitiesPreview",
        "repliesCount",
    ]
    entities, ai = page.data
    assert entities.replies_count == 2 and entities.companies_total == 3
    assert entities.companies is not None and entities.companies[0].id == 10
    assert entities.persons == [] and entities.opportunities is not None
    assert ai.creator is None and ai.transcript_id == 77 and ai.companies is None


def test_unknown_includes_are_refused() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="includes"),
    ):
        client.notes.list_v2(includes=["comments"])


@respx.mock
def test_get_v2_and_replies() -> None:
    get = respx.get(f"{V2}/notes/1").mock(return_value=Response(200, json=ENTITIES))
    respx.get(f"{V2}/notes/1/replies").mock(return_value=Response(200, json=_page([REPLY])))
    with Affinity(api_key="k", max_retries=0) as client:
        note = client.notes.get_v2(NoteId(1), includes=["repliesCount"])
        replies = list(client.notes.iter_replies(NoteId(1)))
    assert get.calls[0].request.url.params.get_list("includes") == ["repliesCount"]
    assert note.replies_count == 2
    assert replies[0].parent == {"id": 1}


@respx.mock
def test_entity_notes_and_company_version_on_every_page() -> None:
    url = f"{V2}/companies/10/notes"
    second = respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([AI]))
    )
    first = respx.get(url).mock(
        return_value=Response(200, json=_page([ENTITIES], f"{url}?cursor=p2"))
    )
    person = respx.get(f"{V2}/persons/5/notes").mock(return_value=Response(200, json=_page([])))
    opp = respx.get(f"{V2}/opportunities/30/notes").mock(return_value=Response(403, json={}))
    with Affinity(api_key="k", max_retries=0) as client:
        ids = [n.id for n in client.companies.iter_notes(CompanyId(10))]
        client.persons.list_notes(PersonId(5), updated_after=datetime(2025, 1, 1))
        with pytest.raises(AuthorizationError):
            client.opportunities.list_notes(OpportunityId(30))
    assert ids == [1, 2]
    assert first.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"
    assert second.calls[0].request.headers["x-affinity-api-version"] == "2026-07-15"
    assert "x-affinity-api-version" not in {k.lower() for k in person.calls[0].request.headers}
    assert person.calls[0].request.url.params["filter"] == "updatedAt>=2025-01-01T00:00:00Z"
    assert opp.called


def test_company_notes_refuse_a_client_pinned_to_2024_01_01() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.companies.list_notes(CompanyId(10))


@respx.mock
async def test_async_list_v2_and_entity_notes() -> None:
    respx.get(f"{V2}/notes").mock(return_value=Response(200, json=_page([ENTITIES])))
    respx.get(f"{V2}/persons/5/notes").mock(return_value=Response(200, json=_page([AI])))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        notes = [n async for n in client.notes.iter_v2()]
        person = await client.persons.list_notes(PersonId(5))
    assert [n.id for n in notes] == [1] and person.data[0].type == "ai-notetaker"


def test_transcript_note_json_does_not_grow_empty_previews() -> None:
    from affinity.cli.serialization import serialize_model_for_cli
    from affinity.models.secondary import NoteV2

    out = serialize_model_for_cli(NoteV2.model_validate(AI))
    assert not {"companies", "persons", "opportunities", "repliesCount"} & set(out)


# --- CLI ---------------------------------------------------------------------------------------


def _run(args: list[str]) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", *args], env=ENV)
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


def test_note_feed_rows(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{V2}/notes").mock(
        return_value=Response(200, json=_page([ENTITIES, AI], f"{V2}/notes?cursor=n"))
    )
    code, out = _run(["note", "feed", "--with-attached", "--creator-id", "5"])
    assert code == 0, out
    assert route.calls[0].request.url.params.get_list("includes")
    first, second = out["data"]["notes"]
    assert first["content"] == "<p>Hi <b>Bo</b></p>"
    assert first["creator"] == {"personId": 5, "name": "Ann Lee", "email": "a@x.co"}
    assert first["mentionedPersonIds"] == [9]
    assert first["companyIds"] == [10] and first["companiesTotal"] == 3
    assert first["repliesCount"] == 2 and "parentId" not in first
    assert second["creator"] is None and second["transcriptId"] == 77
    assert second["interactionId"] == 900
    assert out["meta"]["pagination"]["nextCursor"] == f"{V2}/notes?cursor=n"


def test_note_feed_without_attached_has_no_preview_keys(respx_mock: respx.MockRouter) -> None:
    plain = {k: v for k, v in ENTITIES.items() if not k.endswith("Preview") and k != "repliesCount"}
    respx_mock.get(f"{V2}/notes").mock(return_value=Response(200, json=_page([plain])))
    code, out = _run(["note", "feed"])
    assert code == 0, out
    assert "companyIds" not in out["data"]["notes"][0]


def test_note_replies(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{V2}/notes/1/replies").mock(return_value=Response(200, json=_page([REPLY])))
    code, out = _run(["note", "replies", "1"])
    assert code == 0, out
    assert out["data"]["replies"][0]["parentId"] == 1
