"""SDK tests for note writes on the V2 API (`POST /v2/notes`, `POST/DELETE /v2/notes/{id}`).

V2 stores every note as HTML: plain text is escaped and wrapped by the SDK; HTML is sent as is
after `<b>`/`<i>` become `<strong>`/`<em>`, and what the API documents as failing is refused
before sending, with the allowed tags named."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response
from pydantic import ValidationError

from affinity import Affinity, AsyncAffinity
from affinity.exceptions import ValidationError as AffinityValidationError
from affinity.models import NoteCreate, NoteUpdate, NoteV2
from affinity.services._note_html import note_html, plain_text_to_html
from affinity.types import CompanyId, NoteId, NoteType, OpportunityId, PersonId, UserId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
NOTE = {
    "id": 21,
    "type": "entities",
    "content": {"html": "<p>Hi</p>"},
    "creator": {
        "id": 4,
        "firstName": "Ann",
        "lastName": "Lee",
        "primaryEmailAddress": "a@x.co",
        "type": "internal",
    },
    "mentions": [],
    "createdAt": "2026-10-01T09:00:00Z",
    "updatedAt": None,
}


def _body(route: respx.Route, i: int = 0) -> Any:
    return json.loads(route.calls[i].request.content)


# --- content conversion -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("Hello", "<p>Hello</p>"),
        ("a < b & c > d", "<p>a &lt; b &amp; c &gt; d</p>"),
        ("line 1\nline 2", "<p>line 1<br>line 2</p>"),
        ("para 1\n\npara 2\r\n\r\npara 3", "<p>para 1</p><p>para 2</p><p>para 3</p>"),
        ("<b>not bold</b>", "<p>&lt;b&gt;not bold&lt;/b&gt;</p>"),
        ("snake_case_name", "<p>snake_case_name</p>"),
        ("", "<p></p>"),
    ],
)
def test_plain_text_becomes_escaped_html(text: str, html: str) -> None:
    assert plain_text_to_html(text) == html


def test_html_b_and_i_become_strong_and_em() -> None:
    assert note_html("<p><b>Bold</b> and <I>it</I></p>", NoteType.HTML) == (
        "<p><strong>Bold</strong> and <em>it</em></p>"
    )


@pytest.mark.parametrize(
    ("html", "problem"),
    [
        ('<p style="color:red">x</p>', "style"),
        ('<p class="x">x</p>', "class"),
        ('<img src="https://x.co/a.png">', "<img>"),
        ("<pre>x</pre>", "<pre>"),
        ("<blockquote>x</blockquote>", "<blockquote>"),
        ('<span data-type="note-mention">Jo</span>', "<span>"),
        ('<a href="javascript:alert(1)">x</a>', "href"),
        ('<a href="https://x.co" title="t">x</a>', "title"),
    ],
)
def test_html_the_api_rejects_is_refused_before_sending(html: str, problem: str) -> None:
    with pytest.raises(ValueError, match="Allowed") as exc:
        note_html(html, NoteType.HTML)
    assert problem in str(exc.value)


def test_allowed_html_passes_through() -> None:
    html = (
        '<p>Top items:</p><ul><li><strong>Send pricing</strong></li><li><a href="mailto:j@a.co">'
        "Jane</a></li></ul><p><u>u</u><em>e</em><br><span>s</span></p><ol><li>1</li></ol>"
    )
    assert note_html(html, NoteType.HTML) == html


@pytest.mark.parametrize("kind", [NoteType.AI_NOTETAKER, NoteType.EMAIL_DERIVED])
def test_system_note_types_cannot_be_created(kind: NoteType) -> None:
    with pytest.raises(ValueError, match="plain text or HTML"):
        note_html("x", kind)


# --- create -------------------------------------------------------------------------------------


@respx.mock
def test_create_entities_note() -> None:
    route = respx.post(f"{V2}/notes").mock(return_value=Response(201, json=NOTE))
    with Affinity(api_key="k", max_retries=0) as client:
        note = client.notes.create(
            NoteCreate(
                content="Met Dana <CEO>",
                person_ids=[PersonId(1), PersonId(2)],
                company_ids=[CompanyId(3)],
                opportunity_ids=[OpportunityId(4)],
                creator_id=UserId(9),
                created_at=datetime(2025, 1, 15, 9, 30, 0, 400000, tzinfo=timezone.utc),
            )
        )
    assert isinstance(note, NoteV2)
    assert note.id == 21
    assert _body(route) == {
        "type": "entities",
        "content": {"html": "<p>Met Dana &lt;CEO&gt;</p>"},
        "persons": [{"id": 1}, {"id": 2}],
        "companies": [{"id": 3}],
        "opportunities": [{"id": 4}],
        "creator": {"id": 9},
        "createdAt": "2025-01-15T09:30:00Z",
    }
    assert "x-affinity-api-version" not in route.calls[0].request.headers


@respx.mock
def test_create_html_note() -> None:
    route = respx.post(f"{V2}/notes").mock(return_value=Response(201, json=NOTE))
    with Affinity(api_key="k", max_retries=0) as client:
        client.notes.create(
            NoteCreate(content="<p><b>Summary</b></p>", type=NoteType.HTML, company_ids=[3])
        )
    assert _body(route) == {
        "type": "entities",
        "content": {"html": "<p><strong>Summary</strong></p>"},
        "companies": [{"id": 3}],
    }


@respx.mock
def test_create_reply() -> None:
    route = respx.post(f"{V2}/notes").mock(
        return_value=Response(201, json={**NOTE, "type": "user-reply", "parent": {"id": 20}})
    )
    with Affinity(api_key="k", max_retries=0) as client:
        reply = client.notes.create(NoteCreate(content="Agreed", parent_id=NoteId(20)))
    assert _body(route) == {
        "type": "user-reply",
        "content": {"html": "<p>Agreed</p>"},
        "parent": {"id": 20},
    }
    assert reply.parent == {"id": 20}


def test_reply_with_entities_is_refused() -> None:
    with pytest.raises(ValidationError, match="reply"):
        NoteCreate(content="x", parent_id=NoteId(20), person_ids=[PersonId(1)])


def test_note_needs_an_entity_or_a_parent() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="at least one"),
    ):
        client.notes.create(NoteCreate(content="x"))


# --- update / delete ------------------------------------------------------------------------------


@respx.mock
def test_update_content_then_read_back_with_attachments() -> None:
    update = respx.post(f"{V2}/notes/21").mock(return_value=Response(204))
    get = respx.get(f"{V2}/notes/21").mock(return_value=Response(200, json=NOTE))
    with Affinity(api_key="k", max_retries=0) as client:
        note = client.notes.update(NoteId(21), NoteUpdate(content="New\ntext"))
    assert _body(update) == {"content": {"html": "<p>New<br>text</p>"}}
    assert note.id == 21
    assert "includes" in str(get.calls[0].request.url)


@respx.mock
def test_update_html_and_replace_or_clear_attachments() -> None:
    update = respx.post(f"{V2}/notes/21").mock(return_value=Response(204))
    respx.get(f"{V2}/notes/21").mock(return_value=Response(200, json=NOTE))
    with Affinity(api_key="k", max_retries=0) as client:
        client.notes.update(
            NoteId(21),
            NoteUpdate(
                content="<p><i>x</i></p>",
                type=NoteType.HTML,
                person_ids=[PersonId(7)],
                company_ids=[],
            ),
        )
    assert _body(update) == {
        "content": {"html": "<p><em>x</em></p>"},
        "persons": [{"id": 7}],
        "companies": [],
    }


@respx.mock
def test_update_attachments_only() -> None:
    update = respx.post(f"{V2}/notes/21").mock(return_value=Response(204))
    respx.get(f"{V2}/notes/21").mock(return_value=Response(200, json=NOTE))
    with Affinity(api_key="k", max_retries=0) as client:
        client.notes.update(NoteId(21), NoteUpdate(opportunity_ids=[OpportunityId(4)]))
    assert _body(update) == {"opportunities": [{"id": 4}]}


def test_empty_update_is_refused() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="nothing to update"),
    ):
        client.notes.update(NoteId(21), NoteUpdate())


@respx.mock
def test_delete_returns_true() -> None:
    route = respx.delete(f"{V2}/notes/21").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.notes.delete(NoteId(21)) is True
    assert route.called


@respx.mock
async def test_async_writes() -> None:
    create = respx.post(f"{V2}/notes").mock(return_value=Response(201, json=NOTE))
    update = respx.post(f"{V2}/notes/21").mock(return_value=Response(204))
    respx.get(f"{V2}/notes/21").mock(return_value=Response(200, json=NOTE))
    delete = respx.delete(f"{V2}/notes/21").mock(return_value=Response(204))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        note = await client.notes.create(NoteCreate(content="a_b", person_ids=[PersonId(1)]))
        await client.notes.update(NoteId(21), NoteUpdate(content="c"))
        assert await client.notes.delete(NoteId(21)) is True
    assert isinstance(note, NoteV2)
    assert _body(create)["content"] == {"html": "<p>a_b</p>"}
    assert _body(update) == {"content": {"html": "<p>c</p>"}}
    assert delete.called


# --- notes with @mentions: V2 can't change their content -----------------------------------------

BAD_REQUEST = Response(400, json={"errors": [{"code": "bad-request", "message": "Bad request"}]})
MENTIONED = {
    **NOTE,
    "mentions": [{"type": "person", "person": {**NOTE["creator"], "id": 8}}],
}


@respx.mock
def test_update_of_a_note_with_mentions_explains_the_refusal() -> None:
    respx.post(f"{V2}/notes/21").mock(return_value=BAD_REQUEST)
    respx.get(f"{V2}/notes/21").mock(return_value=Response(200, json=MENTIONED))
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(AffinityValidationError, match="@mentions") as exc,
    ):
        client.notes.update(NoteId(21), NoteUpdate(content="typo fixed"))
    assert exc.value.param == "content"


@respx.mock
def test_other_bad_requests_are_raised_unchanged() -> None:
    respx.post(f"{V2}/notes/21").mock(return_value=BAD_REQUEST)
    respx.get(f"{V2}/notes/21").mock(return_value=Response(200, json=NOTE))
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(AffinityValidationError) as exc,
    ):
        client.notes.update(NoteId(21), NoteUpdate(content="x"))
    assert "@mentions" not in str(exc.value)


@respx.mock
async def test_async_update_with_mentions_explains_the_refusal() -> None:
    respx.post(f"{V2}/notes/21").mock(return_value=BAD_REQUEST)
    respx.get(f"{V2}/notes/21").mock(return_value=Response(200, json=MENTIONED))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        with pytest.raises(AffinityValidationError, match="@mentions"):
            await client.notes.update(NoteId(21), NoteUpdate(content="x"))
