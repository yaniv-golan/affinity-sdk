"""Note content for the V2 API, which stores every note as HTML (``content.html``).

Plain text is escaped and wrapped in paragraphs. HTML is sent as is, except that ``<b>``/``<i>``
become ``<strong>``/``<em>`` (V2 doesn't list them). What the API documents as failing is
refused here, before sending, with a message naming the allowed tags; anything else is left to
the API to judge.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from ..exceptions import ValidationError
from ..models.secondary import NoteCreate, NoteUpdate, NoteV2
from ..models.types import NoteType
from ._v2_filters import v2_body_datetime

ALLOWED = (
    "<p>, <br>, <strong>, <em>, <u>, <ol>, <ul>, <li>, <span> (no attributes), "
    "<a> (href only: http, https or mailto)"
)
# Tags and attributes the API documents as making the request fail
_RESTRICTED_TAGS = {
    "img",
    "script",
    "iframe",
    "style",
    "blockquote",
    "hr",
    "s",
    "pre",
    "code",
    "font",
}
_HREF_SCHEMES = {"http", "https", "mailto"}
_RENAMED = re.compile(r"<(/?)(b|i)(\s*/?)>", re.IGNORECASE)


def plain_text_to_html(text: str) -> str:
    """Escape plain text and wrap it: a blank line starts a paragraph, a newline is ``<br>``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    paragraphs = re.split(r"\n[ \t]*\n+", text) if text else [""]
    return "".join(
        "<p>" + html.escape(p.strip("\n"), quote=False).replace("\n", "<br>") + "</p>"
        for p in paragraphs
    )


class _Check(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _RESTRICTED_TAGS:
            self.problems.append(f"<{tag}> is not allowed")
        for name, value in attrs:
            if name in ("style", "class"):
                self.problems.append(f"the {name} attribute is not allowed (on <{tag}>)")
            elif tag == "span":
                self.problems.append(
                    f"<span> takes no attributes ({name}; mentions can't be written via the API)"
                )
            elif tag == "a" and name != "href":
                self.problems.append(f"<a> takes only href ({name})")
            elif tag == "a" and urlsplit(value or "").scheme.lower() not in _HREF_SCHEMES:
                self.problems.append(f"<a> href must be http, https or mailto ({value})")

    handle_startendtag = handle_starttag


def normalize_html(content: str) -> str:
    """``<b>``/``<i>`` → ``<strong>``/``<em>``; raise ``ValueError`` on what the API rejects."""
    content = _RENAMED.sub(
        lambda m: f"<{m.group(1)}{'strong' if m.group(2).lower() == 'b' else 'em'}{m.group(3)}>",
        content,
    )
    check = _Check()
    check.feed(content)
    check.close()
    if check.problems:
        unique = list(dict.fromkeys(check.problems))
        raise ValueError(
            f"Note HTML the API would reject: {'; '.join(unique)}. Allowed: {ALLOWED}."
        )
    return content


def note_html(content: str, kind: NoteType) -> str:
    """The ``content.html`` to send for ``content`` given as ``kind`` (plain text or HTML)."""
    if kind == NoteType.PLAIN_TEXT:
        return plain_text_to_html(content)
    if kind == NoteType.HTML:
        return normalize_html(content)
    raise ValueError(
        f"Notes are written as plain text or HTML; {NoteType(kind).name} notes are created by "
        "Affinity itself"
    )


def _refs(ids: Sequence[int]) -> list[dict[str, int]]:
    return [{"id": int(i)} for i in ids]


def note_create_body(data: NoteCreate) -> dict[str, Any]:
    """``POST /v2/notes`` body: a reply (``parent_id``) or a note attached to entities."""
    body: dict[str, Any] = {"content": {"html": note_html(data.content, data.type)}}
    if data.parent_id is not None:
        body = {"type": "user-reply", **body, "parent": {"id": int(data.parent_id)}}
    else:
        if not (data.person_ids or data.company_ids or data.opportunity_ids):
            raise ValueError(
                "A note needs at least one person, company or opportunity, or a parent_id"
            )
        body = {"type": "entities", **body}
        for key, ids in (
            ("persons", data.person_ids),
            ("companies", data.company_ids),
            ("opportunities", data.opportunity_ids),
        ):
            if ids:
                body[key] = _refs(ids)
    if data.creator_id is not None:
        body["creator"] = {"id": int(data.creator_id)}
    if data.created_at is not None:
        body["createdAt"] = v2_body_datetime(data.created_at)
    return body


def note_update_body(data: NoteUpdate) -> dict[str, Any]:
    """``POST /v2/notes/{id}`` body: only what is set (an empty list clears attachments)."""
    body: dict[str, Any] = {}
    if data.content is not None:
        body["content"] = {"html": note_html(data.content, data.type)}
    for key, ids in (
        ("persons", data.person_ids),
        ("companies", data.company_ids),
        ("opportunities", data.opportunity_ids),
    ):
        if ids is not None:
            body[key] = _refs(ids)
    if not body:
        raise ValueError("NoteUpdate has nothing to update: set content or attachments")
    return body


def mentions_error(exc: ValidationError) -> ValidationError:
    """The API's 400 for changing the text of a note that @mentions people, explained."""
    return ValidationError(
        "This note @mentions people, and the Affinity API can't change the text of such notes "
        "(it can't write mention tags). Edit it in Affinity, or change only its attachments.",
        param="content",
        status_code=exc.status_code,
        response_body=exc.response_body,
        diagnostics=exc.diagnostics,
    )


def has_mentions(note: NoteV2 | None) -> bool:
    return bool(note is not None and note.mentions)
