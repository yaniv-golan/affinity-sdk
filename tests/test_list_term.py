"""Name filter on lists: SDK ``term=`` and CLI ``list ls --query``."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

pytest.importorskip("rich_click")

from click.testing import CliRunner

from affinity.cli.main import cli
from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.services.lists import AsyncListService, ListService

V2 = "https://v2.example/v2"


def _list(list_id: int, name: str) -> dict[str, Any]:
    return {"id": list_id, "name": name, "type": 0, "public": True, "ownerId": 1}


def _paged_handler(seen: list[httpx.Request]) -> Any:
    """Two pages; the nextUrl carries the term, as Affinity's does."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("cursor") == "p2":
            body = {"data": [_list(2, "Deals 2024")], "pagination": {"nextUrl": None}}
        else:
            term = request.url.params.get("term")
            next_url = f"{V2}/lists?cursor=p2&limit=1" + (f"&term={term}" if term else "")
            body = {"data": [_list(1, "Deals")], "pagination": {"nextUrl": next_url}}
        return httpx.Response(200, json=body, request=request)

    return handler


def _http(seen: list[httpx.Request]) -> HTTPClient:
    return HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url="https://v1.example",
            v2_base_url=V2,
            max_retries=0,
            transport=httpx.MockTransport(_paged_handler(seen)),
        )
    )


class TestSdk:
    def test_list_and_pages_send_term_and_follow_it(self) -> None:
        seen: list[httpx.Request] = []
        with _http(seen) as http:
            names = [
                lst.name for page in ListService(http).pages(term="deals") for lst in page.data
            ]
        assert names == ["Deals", "Deals 2024"]
        assert seen[0].url.params.get("term") == "deals"
        assert seen[1].url.params.get("term") == "deals"

    def test_empty_term_is_no_term(self) -> None:
        seen: list[httpx.Request] = []
        with _http(seen) as http:
            ListService(http).list(term="")
        assert "term" not in seen[0].url.params

    @pytest.mark.parametrize("method", ["list", "pages"])
    def test_term_with_cursor_is_rejected(self, method: str) -> None:
        with _http([]) as http:
            svc = ListService(http)
            with pytest.raises(ValueError, match="cursor"):
                result = getattr(svc, method)(cursor=f"{V2}/lists?cursor=p2", term="x")
                next(iter(result))

    def test_async_pages_send_term(self) -> None:
        async def run() -> list[str]:
            seen: list[httpx.Request] = []
            http = AsyncHTTPClient(
                ClientConfig(
                    api_key="k",
                    v1_base_url="https://v1.example",
                    v2_base_url=V2,
                    max_retries=0,
                    async_transport=httpx.MockTransport(_paged_handler(seen)),
                )
            )
            try:
                svc = AsyncListService(http)
                names = [lst.name async for page in svc.pages(term="deals") for lst in page.data]
                with pytest.raises(ValueError, match="cursor"):
                    await svc.list(cursor=f"{V2}/lists?cursor=p2", term="x")
            finally:
                await http.close()
            assert all(r.url.params.get("term") == "deals" for r in seen)
            return names

        assert asyncio.run(run()) == ["Deals", "Deals 2024"]


class TestCli:
    def _run(self, args: list[str], seen: list[httpx.Request], monkeypatch: Any) -> Any:
        import affinity.clients.http as http_module

        original = http_module.HTTPClient.__init__

        def init(self: Any, config: ClientConfig) -> None:
            config.transport = httpx.MockTransport(_paged_handler(seen))
            config.v2_base_url = V2
            original(self, config)

        monkeypatch.setattr(http_module.HTTPClient, "__init__", init)
        return CliRunner().invoke(
            cli, ["--json", "list", "ls", *args], env={"AFFINITY_API_KEY": "test-key"}
        )

    def test_query_is_sent_as_term_and_recorded(self, monkeypatch: Any) -> None:
        seen: list[httpx.Request] = []
        result = self._run(["--query", "  deals ", "--all"], seen, monkeypatch)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert [row["name"] for row in payload["data"]["lists"]] == ["Deals", "Deals 2024"]
        assert payload["command"]["modifiers"]["query"] == "deals"
        assert all(r.url.params.get("term") == "deals" for r in seen)

    def test_blank_query_is_a_usage_error(self, monkeypatch: Any) -> None:
        result = self._run(["--query", "  "], [], monkeypatch)
        assert result.exit_code == 2
        assert "blank" in result.output

    def test_query_with_cursor_is_a_usage_error(self, monkeypatch: Any) -> None:
        result = self._run(["-q", "deals", "--cursor", f"{V2}/lists?cursor=p2"], [], monkeypatch)
        assert result.exit_code == 2
        assert "drop --query" in result.output
