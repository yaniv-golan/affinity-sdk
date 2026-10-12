"""SDK tests for webhooks on the V2 API (`/v2/webhooks`, Affinity API 2026-07-15, beta)."""

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
from affinity.exceptions import ApiVersionTooOldError, NotFoundError
from affinity.models import PersonRef, Webhook, WebhookCreate, WebhookUpdate
from affinity.types import PersonId, WebhookEvent, WebhookId, WebhookStatus

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
VERSION = "2026-07-15"
HOOK = {
    "id": 7,
    "url": "https://hooks.example.org/affinity",
    "subscriptions": ["list_entry.created", "field_value.updated"],
    "status": "active",
    "creator": {"id": 5},
    "createdAt": "2026-08-01T10:00:00Z",
    "updatedAt": None,
}


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"data": items, "pagination": {"nextUrl": next_url, "prevUrl": None}}


def _version(route: respx.Route, i: int = 0) -> str:
    return str(route.calls[i].request.headers.get("x-affinity-api-version"))


def test_webhook_model_reads_the_v2_shape() -> None:
    hook = Webhook.model_validate(HOOK)
    assert hook.id == WebhookId(7)
    assert hook.url == "https://hooks.example.org/affinity"
    assert hook.subscriptions == [WebhookEvent.LIST_ENTRY_CREATED, WebhookEvent.FIELD_VALUE_UPDATED]
    assert hook.status == WebhookStatus.ACTIVE
    assert hook.creator == PersonRef(id=PersonId(5))
    assert hook.created_at == datetime(2026, 8, 1, 10, tzinfo=timezone.utc)
    assert hook.updated_at is None
    assert Webhook.model_validate({**HOOK, "creator": None}).creator is None


def test_unknown_event_and_status_are_kept() -> None:
    hook = Webhook.model_validate({**HOOK, "subscriptions": ["new.event"], "status": "paused"})
    assert hook.subscriptions == ["new.event"]
    assert hook.status == "paused"


def test_create_requires_subscriptions() -> None:
    with pytest.raises(ValidationError):
        WebhookCreate(url="https://hooks.example.org/a")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("model", "kwargs", "field"),
    [
        (WebhookCreate, {"webhook_url": "https://x", "subscriptions": []}, "webhook_url"),
        (WebhookUpdate, {"webhook_url": "https://x"}, "webhook_url"),
        (WebhookUpdate, {"disabled": True}, "disabled"),
    ],
)
def test_v1_field_names_fail_instead_of_being_ignored(
    model: type[Any], kwargs: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        model(**kwargs)


@respx.mock
def test_list_sends_filters_and_version() -> None:
    route = respx.get(f"{V2}/webhooks").mock(return_value=Response(200, json=_page([HOOK])))
    with Affinity(api_key="k", max_retries=0) as client:
        page = client.webhooks.list(
            ids=[WebhookId(1), WebhookId(2)],
            created_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_before=datetime(2026, 2, 1, 0, 0, 0, 500000, tzinfo=timezone.utc),
            limit=50,
            total_count=True,
        )
    assert [h.id for h in page.data] == [7]
    params = route.calls[0].request.url.params
    assert params["filter"] == (
        "(id=1 | id=2) & createdAt>=2026-01-01T00:00:00Z & updatedAt<2026-02-01T00:00:01Z"
    )
    assert params["limit"] == "50"
    assert params["totalCount"] == "true"
    assert _version(route) == VERSION


@respx.mock
def test_list_without_filters_sends_no_query() -> None:
    route = respx.get(f"{V2}/webhooks").mock(return_value=Response(200, json=_page([])))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.webhooks.list().data == []
    assert not route.calls[0].request.url.params


def test_list_rejects_bad_limit_and_cursor_with_filters() -> None:
    with Affinity(api_key="k", max_retries=0) as client:
        with pytest.raises(ValueError, match="limit"):
            client.webhooks.list(limit=101)
        with pytest.raises(ValueError, match="cursor"):
            client.webhooks.list(cursor="https://api.affinity.co/v2/webhooks?cursor=a", limit=5)


@respx.mock
def test_iter_follows_pages_with_the_same_version() -> None:
    url = f"{V2}/webhooks"
    second = respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{**HOOK, "id": 8}]))
    )
    first = respx.get(url).mock(return_value=Response(200, json=_page([HOOK], f"{url}?cursor=p2")))
    with Affinity(api_key="k", max_retries=0) as client:
        ids = [h.id for h in client.webhooks.iter()]
    assert ids == [7, 8]
    assert _version(first) == VERSION
    assert _version(second) == VERSION


@respx.mock
def test_get_create_update_delete() -> None:
    get = respx.get(f"{V2}/webhooks/7").mock(return_value=Response(200, json=HOOK))
    create = respx.post(f"{V2}/webhooks").mock(return_value=Response(201, json=HOOK))
    update = respx.post(f"{V2}/webhooks/7").mock(return_value=Response(204))
    delete = respx.delete(f"{V2}/webhooks/7").mock(return_value=Response(204))
    with Affinity(api_key="k", max_retries=0) as client:
        assert client.webhooks.get(WebhookId(7)).id == 7
        created = client.webhooks.create(
            WebhookCreate(
                url="https://hooks.example.org/affinity",
                subscriptions=[WebhookEvent.LIST_ENTRY_CREATED],
            )
        )
        updated = client.webhooks.update(WebhookId(7), WebhookUpdate(status=WebhookStatus.DISABLED))
        assert client.webhooks.delete(WebhookId(7)) is None
    assert created.id == 7
    assert json.loads(create.calls[0].request.content) == {
        "url": "https://hooks.example.org/affinity",
        "subscriptions": ["list_entry.created"],
    }
    # update sends only the fields that were set, then reads the webhook back
    assert json.loads(update.calls[0].request.content) == {"status": "disabled"}
    assert updated.id == 7
    assert get.call_count == 2
    for route in (get, create, update, delete):
        assert _version(route) == VERSION


@respx.mock
def test_empty_subscriptions_are_sent_as_all_events() -> None:
    create = respx.post(f"{V2}/webhooks").mock(return_value=Response(201, json=HOOK))
    update = respx.post(f"{V2}/webhooks/7").mock(return_value=Response(204))
    respx.get(f"{V2}/webhooks/7").mock(return_value=Response(200, json=HOOK))
    with Affinity(api_key="k", max_retries=0) as client:
        client.webhooks.create(WebhookCreate(url="https://hooks.example.org/a", subscriptions=[]))
        client.webhooks.update(WebhookId(7), WebhookUpdate(subscriptions=[]))
    assert json.loads(create.calls[0].request.content)["subscriptions"] == []
    assert json.loads(update.calls[0].request.content) == {"subscriptions": []}


def test_update_needs_at_least_one_field() -> None:
    with (
        Affinity(api_key="k", max_retries=0) as client,
        pytest.raises(ValueError, match="nothing to update"),
    ):
        client.webhooks.update(WebhookId(7), WebhookUpdate())


@respx.mock
def test_get_not_visible_is_not_found() -> None:
    respx.get(f"{V2}/webhooks/9").mock(
        return_value=Response(404, json={"errors": [{"code": "not-found", "message": "nope"}]})
    )
    with Affinity(api_key="k", max_retries=0) as client, pytest.raises(NotFoundError):
        client.webhooks.get(WebhookId(9))


def test_pinned_older_version_is_refused() -> None:
    with (
        Affinity(api_key="k", max_retries=0, affinity_api_version="2024-01-01") as client,
        pytest.raises(ApiVersionTooOldError),
    ):
        client.webhooks.list()


@respx.mock
async def test_async_service() -> None:
    url = f"{V2}/webhooks"
    respx.get(url, params={"cursor": "p2"}).mock(
        return_value=Response(200, json=_page([{**HOOK, "id": 8}]))
    )
    listing = respx.get(url).mock(
        return_value=Response(200, json=_page([HOOK], f"{url}?cursor=p2"))
    )
    get = respx.get(f"{V2}/webhooks/7").mock(return_value=Response(200, json=HOOK))
    create = respx.post(url).mock(return_value=Response(201, json=HOOK))
    update = respx.post(f"{V2}/webhooks/7").mock(return_value=Response(204))
    delete = respx.delete(f"{V2}/webhooks/7").mock(return_value=Response(204))
    async with AsyncAffinity(api_key="k", max_retries=0) as client:
        page = await client.webhooks.list(limit=1)
        ids = [h.id async for h in client.webhooks.iter()]
        assert (await client.webhooks.get(WebhookId(7))).status == WebhookStatus.ACTIVE
        await client.webhooks.create(
            WebhookCreate(url="https://hooks.example.org/a", subscriptions=[])
        )
        updated = await client.webhooks.update(
            WebhookId(7), WebhookUpdate(url="https://hooks.example.org/b")
        )
        assert await client.webhooks.delete(WebhookId(7)) is None
    assert page.data[0].id == 7
    assert ids == [7, 8]
    assert updated.id == 7
    assert json.loads(update.calls[0].request.content) == {"url": "https://hooks.example.org/b"}
    for route in (listing, get, create, update, delete):
        assert _version(route) == VERSION
