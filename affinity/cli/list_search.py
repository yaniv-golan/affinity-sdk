"""Server-side filtering for ``list export --filter`` (Affinity's list entry search).

The ``--filter`` text becomes a query WHERE clause (:mod:`.query.filter_text`) and goes through
the same split as ``query`` (:mod:`.query.pushdown`): the part Affinity's search can answer is
sent, and the whole filter still runs locally on what comes back.

A search's next page needs the same request body, so the cursor this command prints carries
it and the filter (``xsearch2:``, see :mod:`affinity._filtering.search`; 2.0.2's ``xsearch1:``
cursors still work). Passing that cursor back resumes the filtered read on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from affinity._filtering.search import CURSOR_PREFIXES, SearchCursor, dropdown_fields, plan_filter
from affinity._filtering.search import decode_cursor as sdk_decode_cursor
from affinity._filtering.search import encode_cursor as sdk_encode_cursor
from affinity._filtering.search import is_filter_cursor as sdk_is_filter_cursor
from affinity.filters import FilterExpression
from affinity.filters import parse as parse_filter
from affinity.types import ListId

from .errors import CLIError

SEARCH_CURSOR_PREFIXES = CURSOR_PREFIXES


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
    """Split ``filter_text`` for the list's fields (cached per client, shared with the SDK).
    Raises ``CLIError`` for a filter the query language refuses (e.g. ``contains`` on a person
    field)."""
    expr = parse_filter(filter_text)
    entries = client.lists.entries(list_id)
    fields = entries._search_fields()
    options = {str(m.id): entries._options(m) for m in dropdown_fields(expr, fields)}
    plan = plan_filter(expr, fields, lambda m: options.get(str(m.id), []))
    if plan.errors:
        raise CLIError(
            "; ".join(plan.errors),
            exit_code=2,
            error_type="usage_error",
            details={"rejected_filter": filter_text},
        )
    return ListSearch(body=plan.body, pushed=plan.pushed, local=plan.local)


def is_filter_cursor(cursor: str | None) -> bool:
    """True for a filtered-read cursor (``xsearch2:`` / ``xsearch1:``)."""
    return sdk_is_filter_cursor(cursor)


def encode_cursor(next_url: str, body: dict[str, Any], expr: FilterExpression) -> str:
    """A cursor for the next search page (carries the body and the filter)."""
    return sdk_encode_cursor("search", next_url, body, expr)


def decode_cursor(cursor: str, *, path_suffixes: list[str]) -> SearchCursor | None:
    """The search cursor in ``cursor`` (``xsearch2:`` or 2.0.2's ``xsearch1:``), or ``None``
    for a plain page cursor. A damaged cursor, one for other records, or one from an SDK read
    that filtered locally raises ``CLIError``."""
    try:
        decoded = sdk_decode_cursor(cursor, path_suffixes=path_suffixes)
    except ValueError as e:
        raise CLIError(
            f"This --cursor can't be used here: {e}", exit_code=2, error_type="usage_error"
        ) from e
    if decoded is not None and decoded.kind != "search":
        raise CLIError(
            "This --cursor comes from an SDK read that filtered locally; run the command "
            "again without it.",
            exit_code=2,
            error_type="usage_error",
        )
    return decoded
