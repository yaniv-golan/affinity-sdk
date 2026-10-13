"""SDK tests: lists.entries(id).list/pages/iter/get_first(filter=...) through Affinity's search."""

from __future__ import annotations

import base64
import json
import warnings
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity, ClientSideFilterWarning, F
from affinity.filters import AndExpression, RawFilter
from affinity.types import ListId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENTRIES = f"{V2}/lists/9/list-entries"
SEARCH = f"{ENTRIES}/search"
OPS = ["is-any-of", "is-none-of", "is-empty", "is-not-empty"]
FIELDS = [
    {
        "id": "field-1",
        "name": "Status",
        "type": "list",
        "valueType": "ranked-dropdown",
        "enrichmentSource": None,
        "filterability": {
            "filterableFieldType": "field-only",
            "operators": [{"id": o, "name": o, "numberOfValuesRequired": "multi"} for o in OPS],
        },
        "sortability": None,
    }
]
OPTIONS = [{"id": 11, "text": "Lead", "rank": 1}, {"id": 12, "text": "Won", "rank": 2}]


def _entry(entry_id: int, status: str, name: str = "Co") -> dict[str, Any]:
    option = 12 if status == "Won" else 11
    return {
        "id": entry_id,
        "listId": 9,
        "type": "company",
        "createdAt": "2026-01-01T00:00:00Z",
        "creatorId": 1,
        "entity": {
            "id": 100 + entry_id,
            "name": f"{name} {entry_id}",
            "domain": None,
            "domains": [],
            "isGlobal": False,
            "fields": [
                {
                    "id": "field-1",
                    "name": "Status",
                    "type": "list",
                    "value": {
                        "type": "ranked-dropdown",
                        "data": {"dropdownOptionId": option, "text": status, "rank": 1},
                    },
                }
            ],
        },
    }


def _page(items: list[Any], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _metadata(router: respx.MockRouter) -> respx.Route:
    fields = router.get(f"{V2}/lists/9/fields").mock(return_value=Response(200, json=_page(FIELDS)))
    router.get(url__regex=r".*/lists/9/fields/field-1/dropdown-options.*").mock(
        return_value=Response(200, json=_page(OPTIONS))
    )
    return fields


WON = 'Status = "Won"'


@respx.mock
def test_list_returns_one_search_page_rechecked_with_a_resumable_cursor() -> None:
    _metadata(respx.mock)
    second = respx.post(SEARCH, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([_entry(3, "Won")]))
    )
    first = respx.post(SEARCH).mock(
        return_value=Response(
            201, json=_page([_entry(1, "Won"), _entry(2, "Lead")], f"{SEARCH}?cursor=p2")
        )
    )
    with Affinity(api_key="k", max_retries=0) as client, warnings.catch_warnings():
        warnings.simplefilter("error", ClientSideFilterWarning)  # nothing runs locally
        entries = client.lists.entries(ListId(9))
        page = entries.list(filter=WON, limit=10)
        assert [e.id for e in page.data] == [1]  # the local re-check removed entry 2
        assert page.next_cursor is not None and page.next_cursor.startswith("xsearch2:")
        nxt = entries.list(cursor=page.next_cursor)
    assert [e.id for e in nxt.data] == [3] and nxt.next_cursor is None
    body = json.loads(first.calls[0].request.content)
    assert body["filters"]["filters"][0]["value"] == [{"dropdownOptionId": 12}]
    assert json.loads(second.calls[0].request.content) == body
    assert first.calls[0].request.url.params.get_list("fieldIds") == ["field-1"]
    assert first.calls[0].request.url.params["limit"] == "10"


@respx.mock
def test_a_local_filter_reads_one_plain_page_per_list_call() -> None:
    _metadata(respx.mock)
    search = respx.post(SEARCH)
    respx.get(ENTRIES, params={"cursor": "g2"}).mock(
        return_value=Response(200, json=_page([_entry(3, "Lead", "Acme")]))
    )
    respx.get(ENTRIES).mock(
        return_value=Response(
            200,
            json=_page(
                [_entry(1, "Lead", "Bolt"), _entry(2, "Lead", "Acme")], f"{ENTRIES}?cursor=g2"
            ),
        )
    )
    with Affinity(api_key="k", max_retries=0) as client:
        entries = client.lists.entries(ListId(9))
        with pytest.warns(ClientSideFilterWarning):
            page = entries.list(filter='entityName =~ "Acme"')
        assert [e.id for e in page.data] == [2]  # one page read, not a scan
        assert page.next_cursor is not None  # more may match: follow the cursor
        nxt = entries.list(cursor=page.next_cursor)
    assert [e.id for e in nxt.data] == [3]
    assert not search.called


@respx.mock
def test_iter_and_pages_go_through_the_search() -> None:
    _metadata(respx.mock)
    respx.post(SEARCH, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([_entry(3, "Won")]))
    )
    respx.post(SEARCH).mock(
        return_value=Response(
            201, json=_page([_entry(1, "Won"), _entry(2, "Lead")], f"{SEARCH}?cursor=p2")
        )
    )
    seen: list[tuple[int, int]] = []
    with Affinity(api_key="k", max_retries=0) as client:
        entries = client.lists.entries(ListId(9))
        assert [e.id for e in entries.iter(filter=F.field("Status").equals("Won"))] == [1, 3]
        pages = list(
            entries.pages(
                filter=WON, limit=1, progress_callback=lambda s: seen.append((s.scanned, s.matched))
            )
        )
        assert entries.get_first(filter=WON) is not None
    assert [[e.id for e in p.data] for p in pages] == [[1], [3]]
    assert seen == [(2, 1), (3, 2)]


@respx.mock
def test_a_refused_search_falls_back_and_is_remembered() -> None:
    _metadata(respx.mock)
    search = respx.post(SEARCH).mock(
        return_value=Response(403, json={"errors": [{"code": "forbidden", "message": "No"}]})
    )
    respx.get(ENTRIES).mock(
        return_value=Response(200, json=_page([_entry(1, "Won"), _entry(2, "Lead")]))
    )
    with Affinity(api_key="k", max_retries=0) as client:
        with pytest.warns(ClientSideFilterWarning, match="refused"):
            first = client.lists.entries(ListId(9)).list(filter=WON)
        with pytest.warns(ClientSideFilterWarning, match="refused for this list"):
            again = client.lists.entries(ListId(9)).list(filter=WON)
    assert [e.id for e in first.data] == [1] and [e.id for e in again.data] == [1]
    assert search.call_count == 1  # the refusal was remembered


@respx.mock
def test_a_400_falls_back_for_that_call_only() -> None:
    fields = _metadata(respx.mock)
    search = respx.post(SEARCH).mock(
        return_value=Response(400, json={"errors": [{"code": "validation", "message": "bad"}]})
    )
    respx.get(ENTRIES).mock(return_value=Response(200, json=_page([_entry(1, "Won")])))
    with Affinity(api_key="k", max_retries=0) as client:
        for _ in range(2):
            with pytest.warns(ClientSideFilterWarning):
                assert [e.id for e in client.lists.entries(ListId(9)).list(filter=WON).data] == [1]
    assert search.call_count == 2  # not remembered
    assert fields.call_count == 2  # the cached fields were dropped after the 400


@respx.mock
def test_fields_are_cached_per_client_and_server_filter_false_skips_the_search() -> None:
    fields = _metadata(respx.mock)
    search = respx.post(SEARCH).mock(return_value=Response(201, json=_page([_entry(1, "Won")])))
    respx.get(ENTRIES).mock(return_value=Response(200, json=_page([_entry(1, "Won")])))
    with Affinity(api_key="k", max_retries=0) as client:
        client.lists.entries(ListId(9)).list(filter=WON)
        client.lists.entries(ListId(9)).list(filter=WON)
        with pytest.warns(ClientSideFilterWarning, match="server_filter=False"):
            client.lists.entries(ListId(9)).list(filter=WON, server_filter=False)
    assert fields.call_count == 1
    assert search.call_count == 2


def test_raw_filters_are_refused_anywhere_in_the_tree() -> None:
    nested = AndExpression(F.field("Status").equals("Won"), RawFilter("x"))
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match="raw"):
        client.lists.entries(ListId(9)).list(filter=nested)


def test_a_cursor_for_another_list_is_refused() -> None:
    payload = {"k": "local", "u": f"{V2}/lists/8/list-entries?cursor=x", "b": None,
               "f": {"cmp": ["Status", "=", {"t": "value", "v": "Won"}]}}  # fmt: skip
    token = "xsearch2:" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError, match="list"):
        client.lists.entries(ListId(9)).list(cursor=token)


@respx.mock
def test_2_0_2_cursors_still_resume() -> None:
    respx.post(SEARCH, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([_entry(3, "Won"), _entry(4, "Lead")]))
    )
    payload = {"u": f"{SEARCH}?cursor=p2", "b": {"filters": {}}, "f": WON}
    token = "xsearch1:" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.lists.entries(ListId(9)).list(cursor=token)
    assert [e.id for e in page.data] == [3]


def test_filter_by_field_id_matches_locally() -> None:
    from affinity.models.entities import ListEntryWithEntity
    from affinity.services.lists import _entry_to_filter_dict

    entry = ListEntryWithEntity.model_validate(_entry(1, "Won"))
    assert F.field("field-1").equals("Won").matches(_entry_to_filter_dict(entry))


@respx.mock
async def test_async() -> None:
    _metadata(respx.mock)
    respx.post(SEARCH, params={"cursor": "p2"}).mock(
        return_value=Response(201, json=_page([_entry(3, "Won")]))
    )
    respx.post(SEARCH).mock(
        return_value=Response(
            201, json=_page([_entry(1, "Won"), _entry(2, "Lead")], f"{SEARCH}?cursor=p2")
        )
    )
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        entries = client.lists.entries(ListId(9))
        page = await entries.list(filter=WON)
        assert [e.id for e in page.data] == [1]
        assert page.next_cursor is not None
        assert [e.id for e in (await entries.list(cursor=page.next_cursor)).data] == [3]
        assert [e.id async for e in entries.iter(filter=WON)] == [1, 3]
        pages = [p async for p in entries.pages(filter=WON, limit=1)]
        assert [[e.id for e in p.data] for p in pages] == [[1], [3]]
        first = await entries.get_first(filter=WON)
        assert first is not None and first.id == 1
