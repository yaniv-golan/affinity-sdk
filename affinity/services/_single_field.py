"""One field on one company or person (V2), mixed into the company and person services:
read its value, replace it, and page through the items of a multi-value field."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from ..models.pagination import AsyncPageIterator, PageIterator, PaginatedResponse
from ..models.types import AnyFieldId, FieldValueType
from ._field_updates import _check_multi_value_size, _field_value_payload
from ._org_reads import check_cursor_alone, check_limit, page_of_raw

FIELD_VALUE_ITEMS_MIN_API_VERSION = "2026-09-17"


def unwrap(value: Any) -> Any:
    """V2 values come as ``{"type": ..., "data": ...}``: return the data."""
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def items_params(limit: int | None) -> list[tuple[str, Any]] | None:
    check_limit(limit)
    return [("limit", limit)] if limit is not None else None


class SingleFieldMixin:
    """``get_field_value``, ``update_field_value``, ``list/iter_field_value_items`` (sync)."""

    _client: Any
    _entity_path: str  # "/companies" or "/persons"

    def get_field_value(self, entity_id: int, field_id: AnyFieldId | str) -> Any:
        """One field's value on this entity (V2), unwrapped: text, a number, a dict, a list of
        items for a multi-value field, or ``None`` when empty."""
        data = self._client.get(f"{self._entity_path}/{int(entity_id)}/fields/{field_id}")
        return unwrap(data.get("value"))

    def update_field_value(
        self,
        entity_id: int,
        field_id: AnyFieldId | str,
        value: Any,
        value_type: FieldValueType | str | None = None,
    ) -> None:
        """Replace one field's value on this entity (V2). ``value_type`` is inferred for text,
        numbers and datetimes; pass it for lists (multi-value fields) and ``None`` (clear).
        To change several fields at once, use :meth:`batch_update_fields` (one request)."""
        payload = _field_value_payload(value, value_type)
        _check_multi_value_size(field_id, payload)
        self._client.post(
            f"{self._entity_path}/{int(entity_id)}/fields/{field_id}", json={"value": payload}
        )

    def list_field_value_items(
        self,
        entity_id: int,
        field_id: AnyFieldId | str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[Any]:
        """One page of the items in a field's value (V2, beta; API 2026-09-17+), for multi-value
        fields with many items. Items are raw: strings for text, dicts for companies, persons,
        dropdowns and locations. ``limit`` is 1-100."""
        check_cursor_alone(cursor, limit)
        if cursor is not None:
            return page_of_raw(
                self._client.get_url(cursor, min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION)
            )
        return page_of_raw(
            self._client.get(
                f"{self._entity_path}/{int(entity_id)}/fields/{field_id}/values",
                params=items_params(limit),
                min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION,
            )
        )

    def iter_field_value_items(self, entity_id: int, field_id: AnyFieldId | str) -> Iterator[Any]:
        """Every item in a field's value (all pages); see :meth:`list_field_value_items`."""
        return PageIterator(
            lambda c: (
                self.list_field_value_items(entity_id, field_id, cursor=c)
                if c
                else self.list_field_value_items(entity_id, field_id, limit=100)
            )
        )


class AsyncSingleFieldMixin:
    """Async ``get_field_value``, ``update_field_value``, ``list/iter_field_value_items``."""

    _client: Any
    _entity_path: str

    async def get_field_value(self, entity_id: int, field_id: AnyFieldId | str) -> Any:
        """One field's value; see the sync method."""
        data = await self._client.get(f"{self._entity_path}/{int(entity_id)}/fields/{field_id}")
        return unwrap(data.get("value"))

    async def update_field_value(
        self,
        entity_id: int,
        field_id: AnyFieldId | str,
        value: Any,
        value_type: FieldValueType | str | None = None,
    ) -> None:
        """Replace one field's value; see the sync method."""
        payload = _field_value_payload(value, value_type)
        _check_multi_value_size(field_id, payload)
        await self._client.post(
            f"{self._entity_path}/{int(entity_id)}/fields/{field_id}", json={"value": payload}
        )

    async def list_field_value_items(
        self,
        entity_id: int,
        field_id: AnyFieldId | str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedResponse[Any]:
        """One page of a field value's items; see the sync method."""
        check_cursor_alone(cursor, limit)
        if cursor is not None:
            return page_of_raw(
                await self._client.get_url(
                    cursor, min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION
                )
            )
        return page_of_raw(
            await self._client.get(
                f"{self._entity_path}/{int(entity_id)}/fields/{field_id}/values",
                params=items_params(limit),
                min_api_version=FIELD_VALUE_ITEMS_MIN_API_VERSION,
            )
        )

    def iter_field_value_items(
        self, entity_id: int, field_id: AnyFieldId | str
    ) -> AsyncIterator[Any]:
        """Every item in a field's value (all pages)."""

        async def fetch(c: str | None) -> PaginatedResponse[Any]:
            if c:
                return await self.list_field_value_items(entity_id, field_id, cursor=c)
            return await self.list_field_value_items(entity_id, field_id, limit=100)

        return AsyncPageIterator(fetch)
