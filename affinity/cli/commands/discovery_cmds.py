"""`company duplicates`, `person duplicates`, `company connections` (V2, beta)."""

from __future__ import annotations

from typing import Any

from affinity.models.discovery import (
    CompanyDuplicateSuggestion,
    CoworkerConnectionGroup,
    DuplicateCompanyProfile,
    InvestorExecutiveConnectionGroup,
    PersonDuplicateSuggestion,
)
from affinity.models.entities import Person
from affinity.models.relationships_v2 import RelationshipPerson

from ..click_compat import RichCommand, click
from ..context import CLIContext
from ..decorators import category
from ..mcp_limits import apply_mcp_limits
from ..options import output_options
from ..results import CommandContext
from ..runner import CommandOutput, run_command
from ._org_reads import collect_pages, page_limit, pagination
from .company_cmds import _resolve_company_selector, company_group
from .org_read_cmds import _cursor_alone, _paging_options
from .person_cmds import person_group

_ORDER = click.Choice(["name", "-name", "createdAt", "-createdAt"])


def _name(first: str | None, last: str | None) -> str | None:
    return f"{first or ''} {last or ''}".strip() or None


def _company(c: DuplicateCompanyProfile) -> dict[str, object]:
    return {"id": int(c.id), "name": c.name, "domain": c.domain}


def _person(p: Person) -> dict[str, object]:
    return {"id": int(p.id), "name": _name(p.first_name, p.last_name), "email": p.primary_email}


def company_suggestion_row(s: CompanyDuplicateSuggestion) -> dict[str, object]:
    return {
        "suggestionId": s.id,
        "matchCriteria": s.match_criteria,
        "primary": _company(s.primary),
        "duplicate": _company(s.duplicates[0]) if s.duplicates else None,
    }


def person_suggestion_row(s: PersonDuplicateSuggestion) -> dict[str, object]:
    return {
        "suggestionId": s.id,
        "matchCriteria": s.match_criteria,
        "primary": _person(s.primary),
        "duplicate": _person(s.duplicates[0]) if s.duplicates else None,
    }


def _source(p: RelationshipPerson) -> dict[str, object]:
    return {
        "personId": int(p.id),
        "name": _name(p.first_name, p.last_name),
        "email": p.primary_email_address,
    }


def connection_row(
    g: CoworkerConnectionGroup | InvestorExecutiveConnectionGroup,
) -> dict[str, object]:
    target = {
        "name": g.target.full_name,
        "title": g.target.title,
        "linkedinUrl": g.target.linkedin_url,
        "companyId": int(g.target.current_company.id),
    }
    via: list[dict[str, object]] = []
    if isinstance(g, CoworkerConnectionGroup):
        for c in g.connections:
            via.append(
                {
                    **_source(c.source),
                    "sharedEmployer": c.shared_employer.name,
                    "from": c.overlap_start.isoformat(),
                    "to": c.overlap_end.isoformat(),
                }
            )
    else:
        for deal in g.connections:
            via.append(
                {
                    **_source(deal.source),
                    "investorFirm": deal.investor.company.name,
                    "investorTitle": deal.investor.title,
                    "round": deal.funding_round.series,
                    "roundDate": deal.funding_round.date.isoformat(),
                    "executiveTitle": deal.executive.title,
                    "portfolioCompany": deal.executive.company.name,
                }
            )
    return {"target": target, "via": via}


def _duplicates_command(entity: str, parent: Any, matches: list[str]) -> None:
    command = f"{entity} duplicates"
    service_name = "companies" if entity == "company" else "persons"
    row = company_suggestion_row if entity == "company" else person_suggestion_row

    @category("read")
    @parent.command(name="duplicates", cls=RichCommand)
    @click.option("--term", default=None, help="Narrow by name (2+ characters).")
    @click.option(
        "--match", "match", type=click.Choice(matches), default=None, help="Match criterion."
    )
    @click.option("--sort", type=_ORDER, default=None, help="Order ('-' for descending).")
    @_paging_options
    @output_options
    @click.pass_obj
    @apply_mcp_limits()
    def duplicates(
        ctx: CLIContext,
        *,
        term: str | None,
        match: str | None,
        sort: str | None,
        cursor: str | None,
        max_results: int | None,
        all_pages: bool,
    ) -> None:
        def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
            _cursor_alone(cursor, term=term, match=match, sort=sort)
            service = getattr(ctx.get_client(warnings=warnings), service_name)
            rows, next_cursor = collect_pages(
                (lambda: service.list_duplicate_suggestions(cursor=cursor))
                if cursor is not None
                else (
                    lambda: service.list_duplicate_suggestions(
                        term=term,
                        match_criteria=match,
                        order_by=sort,
                        limit=page_limit(max_results),
                    )
                ),
                lambda c: service.list_duplicate_suggestions(cursor=c),
                row,
                max_results=max_results,
                all_pages=all_pages,
                warnings=warnings,
            )
            modifiers = {k: v for k, v in (("term", term), ("match", match), ("sort", sort)) if v}
            return CommandOutput(
                data={"suggestions": rows},
                context=CommandContext(name=command, inputs={}, modifiers=modifiers),
                pagination=pagination(next_cursor),
                api_called=True,
            )

        run_command(ctx, command=command, fn=fn)

    confidence = (
        "domain-match (high), domain-redirect (medium), name (low)"
        if entity == "company"
        else "intelligent (medium: email matching across sources), name (low)"
    )
    duplicates.help = (
        f"{entity.capitalize()} pairs Affinity thinks are duplicates, not yet acted on (V2, "
        f"beta; API 2026-07-15+). Match criteria: {confidence}. Merge one with "
        f"`{entity} merge <primary> <duplicate>`."
    )


_duplicates_command("company", company_group, ["domain-match", "domain-redirect", "name"])
_duplicates_command("person", person_group, ["intelligent", "name"])


@category("read")
@company_group.command(name="connections", cls=RichCommand)
@click.argument("selector", type=str)
@click.option(
    "--type",
    "kind",
    type=click.Choice(["coworkers", "investor-executive"]),
    default="coworkers",
    show_default=True,
    help="coworkers: worked together before; investor-executive: an investor you know backed "
    "a company the target ran.",
)
@_paging_options
@output_options
@click.pass_obj
@apply_mcp_limits()
def company_connections(
    ctx: CLIContext,
    *,
    selector: str,
    kind: str,
    cursor: str | None,
    max_results: int | None,
    all_pages: bool,
) -> None:
    """Warm intros into a company: people there today whom someone you know may know (V2, beta).

    One row per target, strongest first, with `via`: the people you know and why. Targets
    aren't in your Affinity data (name, title, LinkedIn only).
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        _cursor_alone(cursor)
        client = ctx.get_client(warnings=warnings)
        company_id, resolved = _resolve_company_selector(
            client=client, selector=selector, cache=ctx.session_cache
        )
        method = (
            client.companies.list_coworker_connections
            if kind == "coworkers"
            else client.companies.list_investor_executive_connections
        )
        limit = min(max_results, 50) if max_results is not None else None
        rows, next_cursor = collect_pages(
            (lambda: method(company_id, cursor=cursor))
            if cursor is not None
            else (lambda: method(company_id, limit=limit)),
            lambda c: method(company_id, cursor=c),
            connection_row,
            max_results=max_results,
            all_pages=all_pages,
            warnings=warnings,
        )
        return CommandOutput(
            data={"connections": rows},
            context=CommandContext(
                name="company connections",
                inputs={"selector": selector},
                modifiers={"type": kind},
            ),
            resolved=resolved,
            pagination=pagination(next_cursor),
            api_called=True,
        )

    run_command(ctx, command="company connections", fn=fn)
