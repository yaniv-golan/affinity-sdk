"""Server-side filtering for companies and persons (``company ls --filter``,
``person ls --filter``, ``query`` on companies/persons).

These endpoints have no other way to filter on field values: without Affinity's search a field
filter would mean reading Affinity's whole company directory (about 11.9 million records). So
a filter here must go to the server at least in part; the whole filter still runs locally on
what comes back.

Conditions on Affinity's enriched fields are sent as they are ("as-is"): search reads a few
records' enriched values as empty, so such records can be missed, and the result says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from affinity.compare import normalize_value
from affinity.criteria import SearchCriteria
from affinity.services._search import SEARCH_INCLUDES

from .query.models import WhereClause
from .query.pushdown import fields_index, split_where

# Record attributes (not fields) a company or person filter can test.
COMPANY_KEYS = frozenset({"id", "name", "domain", "domains"})
PERSON_KEYS = frozenset(
    {"id", "name", "firstName", "lastName", "primaryEmail", "primaryEmailAddress", "emails"}
)


@dataclass
class EntitySearch:
    """The search body for a WHERE clause on companies or persons, and the split notes."""

    body: dict[str, Any] | None
    field_ids: list[str] = field(default_factory=list)  # fields the local re-check reads
    pushed: list[str] = field(default_factory=list)
    local: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.body is not None and not self.local


def plan_entity_search(service: Any, where: WhereClause) -> EntitySearch:
    """Split ``where`` for ``service`` (``client.companies`` or ``client.persons``)."""
    fields = service.get_fields(includes=list(SEARCH_INCLUDES))
    options: dict[str, list[Any]] = {}

    def options_for(meta: Any) -> list[Any]:
        key = str(meta.id)
        if key not in options:
            options[key] = list(service.get_field_dropdown_options(meta.id))
        return options[key]

    split = split_where(where, fields, options_for, enriched="as-is")
    index = fields_index(fields)
    field_ids = sorted(
        {
            str(meta.id)
            for path in _paths(where)
            if (meta := index.get(path) or index.get(path.lower())) is not None
        }
    )
    body = (
        SearchCriteria(where=split.where, term=split.term, term_fields=split.term_fields).to_body()
        if split.server_side
        else None
    )
    return EntitySearch(
        body=body,
        field_ids=field_ids,
        pushed=split.pushed,
        local=split.local,
        caveats=split.caveats,
        errors=split.errors,
    )


def _paths(where: WhereClause) -> set[str]:
    out: set[str] = set()
    if where.path and where.path.startswith("fields."):
        out.add(where.path.removeprefix("fields."))
    for child in [*(where.and_ or []), *(where.or_ or [])]:
        out |= _paths(child)
    if where.not_ is not None:
        out |= _paths(where.not_)
    return out


def entity_filter_dict(entity: Any) -> dict[str, Any]:
    """What a ``--filter`` on companies or persons sees: field values by name (dropdowns as
    text, persons and companies as names) plus the record's own attributes."""
    result: dict[str, Any] = {}
    for item in getattr(entity, "fields_raw", None) or []:
        if isinstance(item, dict) and item.get("name"):
            value = item.get("value")
            data = value.get("data") if isinstance(value, dict) else value
            result[str(item["name"])] = normalize_value(data)
    result["id"] = int(entity.id)
    if hasattr(entity, "domain"):
        result["name"] = entity.name
        result["domain"] = entity.domain
        result["domains"] = list(entity.domains or [])
    else:
        first, last = entity.first_name, entity.last_name
        result["name"] = " ".join(p for p in (first, last) if p) or None
        result["firstName"] = first
        result["lastName"] = last
        result["primaryEmail"] = entity.primary_email
        result["emails"] = list(entity.emails or [])
    return result


def search_rows(
    service: Any,
    body: dict[str, Any],
    filter_text: str,
    *,
    row: Any,
    field_ids: list[str],
    page_size: int | None,
    cursor_url: str | None,
    max_results: int | None,
    all_pages: bool,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Rows (``row(record)``) from search pages that pass the whole ``filter_text``.

    Returns ``(rows, next cursor, stopped mid-page)``; the next cursor carries the search
    (see :func:`affinity.cli.list_search.encode_cursor`).
    """
    from affinity.filters import parse as parse_filter

    from .list_search import encode_cursor

    local = parse_filter(filter_text)
    page = (
        service.find_body(body, cursor=cursor_url)
        if cursor_url is not None
        else service.find_body(body, field_ids=field_ids or None, limit=page_size)
    )
    rows: list[dict[str, Any]] = []
    while True:
        token = encode_cursor(page.next_cursor, body, filter_text) if page.next_cursor else None
        matching = [r for r in page.data if local.matches(entity_filter_dict(r))]
        for idx, record in enumerate(matching):
            rows.append(row(record))
            if max_results is not None and len(rows) >= max_results:
                mid_page = idx < len(matching) - 1
                return rows, None if mid_page else token, mid_page
        if token is None or (not all_pages and max_results is None):
            return rows, token, False
        page = service.find_body(body, cursor=page.next_cursor)


def filtered_ls(
    service: Any,
    *,
    entity: str,
    filter_expr: str | None,
    cursor: str | None,
    row_keys: frozenset[str],
    row: Any,
    field_ids: list[str],
    page_size: int | None,
    max_results: int | None,
    all_pages: bool,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], str | None]:
    """``company ls`` / ``person ls`` with ``--filter`` (or a search ``--cursor``): rows and the
    next cursor. Raises ``CLIError`` for a filter Affinity's search can't apply at all."""
    from affinity.filters import parse as parse_filter

    from .errors import CLIError
    from .list_search import decode_cursor
    from .query.filter_text import where_from_filter

    decoded = decode_cursor(cursor) if cursor else None
    if decoded is not None:
        url, body, filter_text = decoded
        rows, token, mid = search_rows(
            service,
            body,
            filter_text,
            row=row,
            field_ids=[],
            page_size=None,
            cursor_url=url,
            max_results=max_results,
            all_pages=all_pages,
        )
        if mid:
            warnings.append("Results limited by --max-results. Use --all to fetch all results.")
        return rows, token
    assert filter_expr is not None
    try:
        where = where_from_filter(parse_filter(filter_expr), row_keys)
    except ValueError as e:
        raise CLIError(
            f"Invalid --filter: {e}",
            exit_code=2,
            error_type="usage_error",
            hint="Use 'Field = value', 'Field =~ text', 'Field > 5', 'Field = *' (has a value); "
            "combine with '&' and '|'.",
        ) from e

    def nothing_for_the_server(local: list[str]) -> CLIError:
        return CLIError(
            f"Affinity's search can't apply any part of this --filter, and reading every "
            f"{entity} isn't practical (Affinity's company directory has about 12 million).",
            exit_code=2,
            error_type="unsupported_filter",
            hint=f'Filter on {entity} fields (Field = value), or search names with --query "TERM".',
            details={"rejected_filter": filter_expr, "local": local},
        )

    if not _paths(where):
        raise nothing_for_the_server([])  # no field condition: known before any API call
    plan = plan_entity_search(service, where)
    details = {"rejected_filter": filter_expr, "local": plan.local}
    if plan.errors:
        raise CLIError(
            "; ".join(plan.errors), exit_code=2, error_type="usage_error", details=details
        )
    if plan.body is None:
        raise nothing_for_the_server(plan.local)
    if not plan.complete and not (all_pages or max_results is not None):
        raise CLIError(
            "Part of this --filter runs locally on fetched pages, so without --all or "
            "--max-results the result would silently be only first-page matches.",
            exit_code=2,
            error_type="usage_error",
            hint="details.local names the conditions Affinity's search can't apply.",
            details=details,
        )
    warnings.extend(plan.caveats)
    if plan.local:
        warnings.append(
            "Affinity filtered part of --filter; the rest is checked locally: "
            + "; ".join(plan.local)
        )
    rows, token, mid = search_rows(
        service,
        plan.body,
        filter_expr,
        row=row,
        field_ids=sorted({*plan.field_ids, *field_ids}),
        page_size=page_size,
        cursor_url=None,
        max_results=max_results,
        all_pages=all_pages,
    )
    if mid:
        warnings.append("Results limited by --max-results. Use --all to fetch all results.")
    return rows, token
