import inspect

from affinity.services.opportunities import AsyncOpportunityService, OpportunityService


def test_opportunities_list_has_no_filter_param_sync():
    sig = inspect.signature(OpportunityService.list)
    assert "filter" not in sig.parameters, (
        "opportunities.list() must not accept filter — V2 /opportunities ignores it. "
        "See docs/internal/affinity_api_docs_v2.md line 13917."
    )


def test_opportunities_list_has_no_filter_param_async():
    sig = inspect.signature(AsyncOpportunityService.list)
    assert "filter" not in sig.parameters, "async opportunities.list() must not accept filter."
