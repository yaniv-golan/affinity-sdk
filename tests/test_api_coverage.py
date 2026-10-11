"""API coverage ledger (tools/api_coverage.json): every Affinity API operation has a row, and each
supported row's SDK call really requests that operation.

Each row's `call` is evaluated against `Affinity` and `AsyncAffinity` with every HTTP verb
replaced by a recorder that notes (method, path, API version) and stops the call, so no request
is sent and the check proves the endpoint the SDK uses, not just that a method exists."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from affinity import Affinity, AsyncAffinity
from affinity.clients.http import AsyncHTTPClient, HTTPClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import api_coverage

LEDGER = api_coverage.load_ledger()

# HTTP client method -> HTTP verb it sends
_VERBS = {
    "get": "GET",
    "get_all_pages": "GET",
    "get_url": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "upload_file": "POST",
    "download_file": "GET",
    "stream_download": "GET",
    "stream_download_with_info": "GET",
    "get_redirect_url": "GET",
}


class _Stop(Exception):
    """Raised by the recorder after the first request, before anything is sent."""


def _record(calls: list[tuple[str, str, str]], name: str, path: str, v1: bool) -> None:
    if path.startswith("http"):
        parts = urlsplit(path)
        path, v1 = parts.path, not parts.path.startswith("/v2")
    if path.startswith("/v2/"):
        path = path[3:]
    calls.append((_VERBS[name], path.split("?")[0], "v1" if v1 else "v2"))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []
    for name in _VERBS:
        for cls in (HTTPClient, AsyncHTTPClient):

            def fake(_self: Any, path: str, *_a: Any, _n: str = name, **kw: Any) -> Any:
                _record(calls, _n, path, bool(kw.get("v1")))
                raise _Stop

            if inspect.iscoroutinefunction(getattr(cls, name)):

                async def afake(_self: Any, path: str, *_a: Any, _n: str = name, **kw: Any) -> Any:
                    _record(calls, _n, path, bool(kw.get("v1")))
                    raise _Stop

                monkeypatch.setattr(cls, name, afake)
            elif inspect.isgeneratorfunction(getattr(cls, name)) or name == "stream_download":

                def gfake(_self: Any, path: str, *_a: Any, _n: str = name, **kw: Any) -> Any:
                    _record(calls, _n, path, bool(kw.get("v1")))
                    raise _Stop
                    yield b""  # pragma: no cover

                monkeypatch.setattr(cls, name, gfake)
            else:
                monkeypatch.setattr(cls, name, fake)
    return calls


def _drive(result: Any) -> None:
    """Run whatever the call returned far enough to send its first request."""
    if inspect.isawaitable(result):
        result = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(result)
    if hasattr(result, "__anext__"):
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(result.__anext__())
    elif hasattr(result, "__next__"):
        next(result)
    elif hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict, list)):
        next(iter(result))


def _invoke(client: Any, call: str) -> None:
    with contextlib.suppress(_Stop):
        _drive(eval(f"c.{call}", {"__builtins__": {}}, {"c": client, **api_coverage.call_names()}))


def _expected(op: str, api: str) -> tuple[str, str, str]:
    method, path = op.split(" ", 1)
    if path.startswith("/v2/"):
        path = path[3:]
    return method, re.sub(r"\{[^}]*\}", "{}", path), api


def _matches(got: tuple[str, str, str], want: tuple[str, str, str]) -> bool:
    pattern = "^" + re.escape(want[1]).replace(r"\{\}", r"[^/]+") + "$"
    return got[0] == want[0] and got[2] == want[2] and re.match(pattern, got[1]) is not None


SUPPORTED = [r for r in LEDGER if r["status"] == "supported"]


@pytest.mark.parametrize("row", SUPPORTED, ids=[f"{r['api']} {r['op']}" for r in SUPPORTED])
@pytest.mark.parametrize("flavor", ["sync", "async"])
def test_sdk_call_requests_the_operation(
    row: dict[str, Any], flavor: str, recorder: list[tuple[str, str, str]]
) -> None:
    call = row.get("async_call", row["call"]) if flavor == "async" else row["call"]
    if call is None:
        pytest.skip("no async form")
    cls = AsyncAffinity if flavor == "async" else Affinity
    client = cls(api_key="coverage-test-key")
    _invoke(client, call)
    assert recorder, f"{call} sent no request"
    want = _expected(row["op"], row["api"])
    assert _matches(recorder[0], want), f"{call} requested {recorder[0]}, ledger says {want}"


def test_ledger_matches_the_pinned_specs() -> None:
    problems = api_coverage.check_ledger(LEDGER)
    assert not problems, "\n".join(problems)


def test_docs_page_is_current() -> None:
    page = REPO / "docs" / "public" / "reference" / "api-coverage.md"
    assert page.read_text() == api_coverage.markdown(LEDGER), (
        "docs/public/reference/api-coverage.md is stale: run "
        "python tools/api_coverage.py --markdown docs/public/reference/api-coverage.md"
    )
