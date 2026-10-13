"""Server-side filtering for ``list export --filter`` (Affinity's list entry search).

The ``--filter`` text becomes a query WHERE clause (:mod:`.query.filter_text`) and goes through
the same split as ``query`` (:mod:`.query.pushdown`): the part Affinity's search can answer is
sent, and the whole filter still runs locally on what comes back.

A search's next page needs the same request body, so the cursor this command prints carries
it: ``xsearch1:`` + base64 of ``{"u": next page URL, "b": body, "f": filter text}``. Passing
that cursor back resumes the filtered export on its own.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from affinity.criteria import SearchCriteria
from affinity.filters import parse as parse_filter
from affinity.services._search import SEARCH_INCLUDES
from affinity.types import ListId

from .errors import CLIError
from .query.filter_text import where_from_filter
from .query.pushdown import split_where

SEARCH_CURSOR_PREFIX = "xsearch1:"


@dataclass
class ListSearch:
    """The search body for a filter (``None``: nothing for the server) and the split notes."""

    body: dict[str, Any] | None
    pushed: list[str] = field(default_factory=list)
    local: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every condition went to the server: page one is the first page of matches."""
        return self.body is not None and not self.local


def plan_list_search(client: Any, list_id: ListId, filter_text: str) -> ListSearch:
    """Split ``filter_text`` for the list's fields. Raises ``CLIError`` for a filter the query
    language refuses (e.g. ``contains`` on a person field)."""
    where = where_from_filter(parse_filter(filter_text))
    fields = client.lists.get_fields(list_id, includes=list(SEARCH_INCLUDES))
    options: dict[str, list[Any]] = {}

    def options_for(meta: Any) -> list[Any]:
        key = str(meta.id)
        if key not in options:
            options[key] = list(client.lists.get_field_dropdown_options(list_id, meta.id))
        return options[key]

    split = split_where(where, fields, options_for)
    if split.errors:
        raise CLIError(
            "; ".join(split.errors),
            exit_code=2,
            error_type="usage_error",
            details={"rejected_filter": filter_text},
        )
    body = (
        SearchCriteria(where=split.where, term=split.term, term_fields=split.term_fields).to_body()
        if split.server_side
        else None
    )
    return ListSearch(body=body, pushed=split.pushed, local=split.local)


def encode_cursor(next_url: str, body: dict[str, Any], filter_text: str) -> str:
    raw = json.dumps({"u": next_url, "b": body, "f": filter_text}, separators=(",", ":"))
    return SEARCH_CURSOR_PREFIX + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, dict[str, Any], str] | None:
    """``(next page URL, body, filter text)`` for a search cursor; ``None`` for other cursors."""
    if not cursor.startswith(SEARCH_CURSOR_PREFIX):
        return None
    payload = cursor.removeprefix(SEARCH_CURSOR_PREFIX)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return str(data["u"]), dict(data["b"]), str(data["f"])
    except (ValueError, KeyError, TypeError) as e:
        raise CLIError(
            "This --cursor is damaged: run the export again without it.",
            exit_code=2,
            error_type="usage_error",
        ) from e
