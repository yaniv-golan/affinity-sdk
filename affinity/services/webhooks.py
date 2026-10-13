"""
Webhook service (V2 API: ``/v2/webhooks``, Affinity API 2026-07-15 and later, beta).

Users who may manage all webhooks see every webhook in the organization; everyone else sees,
changes and deletes only the webhooks they created. Creating a webhook, changing its ``url``, or
re-activating it sends a test delivery to the URL first; an unreachable URL is rejected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..models.pagination import AsyncPageIterator, PageIterator, PaginatedResponse
from ..models.secondary import Webhook, WebhookCreate, WebhookUpdate
from ..models.types import WebhookId
from ._org_reads import check_cursor_alone, check_limit, empty_page, no_ids, page_of
from ._v2_filters import v2_filter_datetime

if TYPE_CHECKING:
    from ..clients.http import AsyncHTTPClient, HTTPClient

WEBHOOKS_MIN_API_VERSION = "2026-07-15"


def webhook_params(
    *,
    ids: Sequence[int] | None,
    created_after: datetime | None,
    created_before: datetime | None,
    updated_after: datetime | None,
    updated_before: datetime | None,
    limit: int | None,
    total_count: bool,
) -> list[tuple[str, Any]]:
    """Query for ``GET /v2/webhooks`` (datetimes in whole seconds, rounded outward)."""
    check_limit(limit)
    clauses = []
    if ids:
        either = " | ".join(f"id={int(i)}" for i in ids)
        clauses.append(f"({either})" if len(ids) > 1 else either)
    for name, after, before in (
        ("createdAt", created_after, created_before),
        ("updatedAt", updated_after, updated_before),
    ):
        if after is not None:
            clauses.append(f"{name}>={v2_filter_datetime(after, round_up=False)}")
        if before is not None:
            clauses.append(f"{name}<{v2_filter_datetime(before, round_up=True)}")
    params: list[tuple[str, Any]] = []
    if clauses:
        params.append(("filter", " & ".join(clauses)))
    if limit is not None:
        params.append(("limit", limit))
    if total_count:
        params.append(("totalCount", "true"))
    return params


def create_body(data: WebhookCreate) -> dict[str, Any]:
    # subscriptions is always sent: [] means every event type
    return data.model_dump(by_alias=True, mode="json")


def update_body(data: WebhookUpdate) -> dict[str, Any]:
    body = data.model_dump(by_alias=True, mode="json", exclude_unset=True, exclude_none=True)
    if not body:
        raise ValueError("WebhookUpdate has nothing to update: set url, subscriptions or status")
    return body


class WebhookService:
    """Webhooks (V2, beta). Every call sends Affinity API version 2026-07-15 unless pinned newer."""

    def __init__(self, client: HTTPClient):
        self._client = client

    def list(
        self,
        *,
        ids: Sequence[WebhookId] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Webhook]:
        """One page of webhooks, ordered by id.

        Without permission to manage all webhooks, only the caller's own webhooks are listed.
        ``created_after``/``updated_after`` are inclusive, ``created_before``/``updated_before``
        exclusive. ``limit`` is 1-100. Pass ``cursor`` (``pagination.next_cursor``) alone for the
        next page; :meth:`iter` follows every page.
        """
        check_cursor_alone(
            cursor,
            ids,
            created_after,
            created_before,
            updated_after,
            updated_before,
            limit,
            total_count,
        )
        if cursor is not None:
            return page_of(
                Webhook, self._client.get_url(cursor, min_api_version=WEBHOOKS_MIN_API_VERSION)
            )
        params = webhook_params(
            ids=ids,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
            total_count=total_count,
        )
        if no_ids(ids):
            return empty_page(Webhook, total_count=total_count)
        return page_of(
            Webhook,
            self._client.get(
                "/webhooks", params=params or None, min_api_version=WEBHOOKS_MIN_API_VERSION
            ),
        )

    def iter(
        self,
        *,
        ids: Sequence[WebhookId] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[Webhook]:
        """All webhooks matching the filters (every page); see :meth:`list`."""

        def fetch_page(next_url: str | None) -> PaginatedResponse[Webhook]:
            if next_url:
                return self.list(cursor=next_url)
            return self.list(
                ids=ids,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                updated_before=updated_before,
                limit=limit,
            )

        return PageIterator(fetch_page)

    def get(self, webhook_id: WebhookId) -> Webhook:
        """One webhook. ``NotFoundError`` when it does not exist or is not visible to the caller."""
        return Webhook.model_validate(
            self._client.get(
                f"/webhooks/{int(webhook_id)}", min_api_version=WEBHOOKS_MIN_API_VERSION
            )
        )

    def create(self, data: WebhookCreate) -> Webhook:
        """Create a webhook; the caller becomes its creator.

        Affinity first sends a test delivery to ``data.url``. A URL that is unreachable, holds
        credentials, or is already used by another webhook raises ``ValidationError``; reaching
        the organization's webhook limit raises one too. ``subscriptions=[]`` = every event type.
        """
        return Webhook.model_validate(
            self._client.post(
                "/webhooks", json=create_body(data), min_api_version=WEBHOOKS_MIN_API_VERSION
            )
        )

    def update(self, webhook_id: WebhookId, data: WebhookUpdate) -> Webhook:
        """Change a webhook (only the fields set in ``data``) and return it as read back.

        Two requests: the update answers with no body, so the webhook is fetched afterwards.
        Changing ``url`` on an active webhook, or setting ``status`` to active, sends a test
        delivery first. A webhook you didn't create, without permission to manage all webhooks,
        is not visible to you (``NotFoundError``).
        """
        self._client.post(
            f"/webhooks/{int(webhook_id)}",
            json=update_body(data),
            min_api_version=WEBHOOKS_MIN_API_VERSION,
        )
        return self.get(webhook_id)

    def delete(self, webhook_id: WebhookId) -> None:
        """Delete a webhook. One you didn't create, without permission to manage all webhooks, is
        not visible to you (``NotFoundError``)."""
        self._client.delete(
            f"/webhooks/{int(webhook_id)}", min_api_version=WEBHOOKS_MIN_API_VERSION
        )


class AsyncWebhookService:
    """Async webhooks (V2, beta); same behavior as :class:`WebhookService`."""

    def __init__(self, client: AsyncHTTPClient):
        self._client = client

    async def list(
        self,
        *,
        ids: Sequence[WebhookId] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
        total_count: bool = False,
        cursor: str | None = None,
    ) -> PaginatedResponse[Webhook]:
        """One page of webhooks; see :meth:`WebhookService.list`."""
        check_cursor_alone(
            cursor,
            ids,
            created_after,
            created_before,
            updated_after,
            updated_before,
            limit,
            total_count,
        )
        if cursor is not None:
            return page_of(
                Webhook,
                await self._client.get_url(cursor, min_api_version=WEBHOOKS_MIN_API_VERSION),
            )
        params = webhook_params(
            ids=ids,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
            total_count=total_count,
        )
        if no_ids(ids):
            return empty_page(Webhook, total_count=total_count)
        return page_of(
            Webhook,
            await self._client.get(
                "/webhooks", params=params or None, min_api_version=WEBHOOKS_MIN_API_VERSION
            ),
        )

    def iter(
        self,
        *,
        ids: Sequence[WebhookId] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Webhook]:
        """All webhooks matching the filters (every page); see :meth:`list`."""

        async def fetch_page(next_url: str | None) -> PaginatedResponse[Webhook]:
            if next_url:
                return await self.list(cursor=next_url)
            return await self.list(
                ids=ids,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                updated_before=updated_before,
                limit=limit,
            )

        return AsyncPageIterator(fetch_page)

    async def get(self, webhook_id: WebhookId) -> Webhook:
        """One webhook; see :meth:`WebhookService.get`."""
        return Webhook.model_validate(
            await self._client.get(
                f"/webhooks/{int(webhook_id)}", min_api_version=WEBHOOKS_MIN_API_VERSION
            )
        )

    async def create(self, data: WebhookCreate) -> Webhook:
        """Create a webhook; see :meth:`WebhookService.create`."""
        return Webhook.model_validate(
            await self._client.post(
                "/webhooks", json=create_body(data), min_api_version=WEBHOOKS_MIN_API_VERSION
            )
        )

    async def update(self, webhook_id: WebhookId, data: WebhookUpdate) -> Webhook:
        """Change a webhook and return it as read back (two requests); see
        :meth:`WebhookService.update`."""
        await self._client.post(
            f"/webhooks/{int(webhook_id)}",
            json=update_body(data),
            min_api_version=WEBHOOKS_MIN_API_VERSION,
        )
        return await self.get(webhook_id)

    async def delete(self, webhook_id: WebhookId) -> None:
        """Delete a webhook; see :meth:`WebhookService.delete`."""
        await self._client.delete(
            f"/webhooks/{int(webhook_id)}", min_api_version=WEBHOOKS_MIN_API_VERSION
        )
