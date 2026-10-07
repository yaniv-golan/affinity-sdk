"""Multi-value field limits: Affinity's V2 API returns and accepts at most 100 values per
multi-value field (``data.maxItems: 100`` plus an optional ``totalCount`` on reads)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.models.entities import Company, FieldValues
from affinity.models.types import FieldValueType, ListEntryId, ListId
from affinity.services.lists import AsyncListEntryService, ListEntryService


def _company(fields: list[dict[str, Any]]) -> Company:
    return Company.model_validate({"id": 1, "name": "Acme", "fields": fields})


def _multi(field_id: str, n: int, total: int | None, value_type: str = "company-multi") -> dict:
    value: dict[str, Any] = {"type": value_type, "data": [{"id": i} for i in range(n)]}
    if total is not None:
        value["totalCount"] = total
    return {"id": field_id, "name": field_id.title(), "value": value}


class TestFieldValuesTruncation:
    def test_truncated_when_total_exceeds_returned(self) -> None:
        fields = _company([_multi("field-1", 100, 250)]).fields
        assert fields.total_count("field-1") == 250
        assert fields.is_truncated("field-1") is True
        assert fields.truncated_fields() == {"field-1": (100, 250)}

    def test_not_truncated_when_total_matches(self) -> None:
        fields = _company([_multi("field-1", 3, 3)]).fields
        assert fields.total_count("field-1") == 3
        assert fields.is_truncated("field-1") is False
        assert fields.truncated_fields() == {}

    def test_total_count_absent(self) -> None:
        fields = _company([_multi("field-1", 100, None)]).fields
        assert fields.total_count("field-1") is None
        assert fields.is_truncated("field-1") is False

    def test_null_and_scalar_data(self) -> None:
        fields = _company(
            [
                {
                    "id": "field-1",
                    "value": {"type": "company-multi", "data": None, "totalCount": 0},
                },
                {"id": "field-2", "value": {"type": "text", "data": "x"}},
                {"id": "field-3", "value": None},
            ]
        ).fields
        for fid in ("field-1", "field-2", "field-3", "field-404"):
            assert fields.is_truncated(fid) is False
        assert fields.truncated_fields() == {}

    def test_get_value_unchanged(self) -> None:
        fields = _company([_multi("field-1", 2, 250)]).fields
        assert fields.get_value("field-1") == [{"id": 0}, {"id": 1}]

    def test_empty_container(self) -> None:
        assert FieldValues().truncated_fields() == {}


def _http(handler: Any) -> HTTPClient:
    return HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
    )


def _recording_handler() -> tuple[list[httpx.Request], Any]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"results": []}, request=request)
        return httpx.Response(200, json={"data": []}, request=request)

    return seen, handler


class TestWriteGuards:
    @pytest.mark.parametrize(
        "value_type",
        [
            "company-multi",
            "person-multi",
            "number-multi",
            "filterable-text-multi",
            "location-multi",
        ],
    )
    def test_update_over_cap_raises_before_request(self, value_type: str) -> None:
        seen, handler = _recording_handler()
        http = _http(handler)
        try:
            entries = ListEntryService(http, ListId(10))
            with pytest.raises(ValueError, match="at most 100"):
                entries.update_field_value(
                    ListEntryId(5), "field-1", list(range(101)), value_type=value_type
                )
        finally:
            http.close()
        assert seen == []

    def test_update_at_cap_is_sent(self) -> None:
        seen, handler = _recording_handler()
        http = _http(handler)
        try:
            entries = ListEntryService(http, ListId(10))
            entries.update_field_value(
                ListEntryId(5), "field-1", list(range(100)), value_type=FieldValueType.NUMBER_MULTI
            )
        finally:
            http.close()
        assert len(seen) == 1
        assert len(json.loads(seen[0].content)["value"]["data"]) == 100

    def test_dropdown_multi_is_not_capped_client_side(self) -> None:
        """The write schema for dropdown-multi declares no maxItems."""
        seen, handler = _recording_handler()
        http = _http(handler)
        try:
            entries = ListEntryService(http, ListId(10))
            entries.update_field_value(
                ListEntryId(5),
                "field-1",
                [{"dropdownOptionId": i} for i in range(101)],
                value_type="dropdown-multi",
            )
        finally:
            http.close()
        assert len(seen) == 1

    def test_batch_over_100_updates_raises(self) -> None:
        seen, handler = _recording_handler()
        http = _http(handler)
        try:
            entries = ListEntryService(http, ListId(10))
            with pytest.raises(ValueError, match="at most 100"):
                entries.batch_update_fields(ListEntryId(5), {f"field-{i}": "x" for i in range(101)})
        finally:
            http.close()
        assert seen == []

    def test_batch_list_value_raises(self) -> None:
        """A list cannot be typed by inference; it used to be sent as an invalid 'text' value."""
        seen, handler = _recording_handler()
        http = _http(handler)
        try:
            entries = ListEntryService(http, ListId(10))
            with pytest.raises(ValueError, match="update_field_value"):
                entries.batch_update_fields(ListEntryId(5), {"field-1": [1, 2]})
        finally:
            http.close()
        assert seen == []


def test_get_field_values_follows_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "cursor=p2" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "field-2", "value": {"type": "text", "data": "b"}}],
                    "pagination": {"nextUrl": None},
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "data": [{"id": "field-1", "value": {"type": "text", "data": "a"}}],
                "pagination": {
                    "nextUrl": "https://v2.example/v2/lists/10/list-entries/5/fields?cursor=p2"
                },
            },
            request=request,
        )

    http = _http(handler)
    try:
        fv = ListEntryService(http, ListId(10)).get_field_values(ListEntryId(5))
    finally:
        http.close()
    assert fv.get_value("field-1") == "a"
    assert fv.get_value("field-2") == "b"


@pytest.mark.asyncio
async def test_async_guards_and_pagination() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "cursor=p2" in str(request.url):
            return httpx.Response(
                200,
                json={"data": [{"id": "field-2", "value": None}], "pagination": {"nextUrl": None}},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "data": [{"id": "field-1", "value": None}],
                "pagination": {
                    "nextUrl": "https://v2.example/v2/lists/10/list-entries/5/fields?cursor=p2"
                },
            },
            request=request,
        )

    client = AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url="https://v2.example/v2",
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
        )
    )
    try:
        entries = AsyncListEntryService(client, ListId(10))
        with pytest.raises(ValueError, match="at most 100"):
            await entries.update_field_value(
                ListEntryId(5), "field-1", list(range(101)), value_type="person-multi"
            )
        with pytest.raises(ValueError, match="at most 100"):
            await entries.batch_update_fields(ListEntryId(5), {f"field-{i}": 1 for i in range(101)})
        with pytest.raises(ValueError, match="update_field_value"):
            await entries.batch_update_fields(ListEntryId(5), {"field-1": [1]})
        assert seen == []
        fv = await entries.get_field_values(ListEntryId(5))
        assert set(fv.data) == {"field-1", "field-2"}
    finally:
        await client.close()
