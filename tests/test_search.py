"""SDK tests for V2 search: models, client-side validation, read-only POST transport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity, AsyncAffinity, WriteNotAllowedError
from affinity.clients.http import AsyncHTTPClient, ClientConfig, HTTPClient
from affinity.exceptions import AffinityError
from affinity.models.search import (
    FileSearchResult,
    NoteSearchResult,
    SemanticCompany,
    SemanticSearchResult,
)
from affinity.policies import Policies, WritePolicy
from affinity.types import NoteKind

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"

NOTE_HITS = {
    "data": [
        {"note": {"id": 11, "kind": "note"}, "preview": "We discussed pricing."},
        {"note": {"id": 12, "kind": "meeting-ai-summary"}, "preview": "Summary text"},
    ]
}
FILE_HITS = {
    "data": [
        {"file": {"id": 21, "name": "deck.pdf"}, "pageNumber": 3, "preview": "ARR $12M"},
        {"file": {"id": 22, "name": "notes.txt"}, "pageNumber": None, "preview": "plain"},
    ]
}
SEMANTIC = {
    "explanation": "Companies building AI infrastructure.",
    "entityType": "companies",
    "data": [
        {
            "id": 31,
            "name": "Acme AI",
            "domain": "acme.ai",
            "domains": ["acme.ai"],
            "isGlobal": True,
            "score": "0.85",
        },
        {
            "id": 32,
            "name": "No Domain Inc",
            "domain": None,
            "domains": [],
            "isGlobal": False,
            "score": "n/a",
        },
    ],
}


def _body(route: respx.Route, index: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[index].request.content)


# =============================================================================
# Models
# =============================================================================


class TestModels:
    def test_note_result_parses_kind_and_preview(self) -> None:
        hit = NoteSearchResult.model_validate(NOTE_HITS["data"][1])
        assert int(hit.note.id) == 12
        assert hit.note.kind == NoteKind.MEETING_AI_SUMMARY
        assert hit.preview == "Summary text"

    def test_note_kind_is_open(self) -> None:
        hit = NoteSearchResult.model_validate({"note": {"id": 1, "kind": "future-kind"}})
        assert str(hit.note.kind) == "future-kind"
        assert hit.preview == ""

    def test_note_kind_members_match_spec(self) -> None:
        assert {k.value for k in NoteKind} == {
            "note",
            "meeting-note",
            "email-note",
            "ai-summary",
            "meeting-ai-summary",
            "chat-message-note",
        }

    def test_file_result_nullable_page_number(self) -> None:
        with_page = FileSearchResult.model_validate(FILE_HITS["data"][0])
        without = FileSearchResult.model_validate(FILE_HITS["data"][1])
        assert with_page.page_number == 3
        assert with_page.file.name == "deck.pdf"
        assert without.page_number is None

    def test_semantic_result_score_raw_and_lenient_float(self) -> None:
        result = SemanticSearchResult.model_validate(SEMANTIC)
        assert result.explanation == "Companies building AI infrastructure."
        assert result.entity_type == "companies"
        first, second = result.data
        assert first.score == "0.85"
        assert first.score_float == pytest.approx(0.85)
        assert first.is_global is True
        assert second.domain is None
        assert second.score == "n/a"
        assert second.score_float is None

    def test_semantic_company_missing_score(self) -> None:
        company = SemanticCompany.model_validate({"id": 1, "name": "x"})
        assert company.score is None
        assert company.score_float is None


# =============================================================================
# Client-side validation (no request is sent)
# =============================================================================


@pytest.fixture
def client() -> Any:
    c = Affinity(api_key="k", max_retries=0)
    yield c
    c.close()


class TestValidation:
    @pytest.mark.parametrize("prompt", ["", "  ", "ab", "x" * 501])
    def test_keyword_prompt_bounds(self, client: Affinity, prompt: str) -> None:
        with pytest.raises(ValueError, match="prompt"):
            client.notes.search(prompt)
        with pytest.raises(ValueError, match="prompt"):
            client.files.search(prompt)

    @pytest.mark.parametrize("prompt", ["", "x" * 501])
    def test_semantic_prompt_bounds(self, client: Affinity, prompt: str) -> None:
        with pytest.raises(ValueError, match="prompt"):
            client.companies.semantic_search(prompt)

    @pytest.mark.parametrize("limit", [0, 101, -1])
    def test_limit_bounds(self, client: Affinity, limit: int) -> None:
        with pytest.raises(ValueError, match="limit"):
            client.notes.search("pricing", limit=limit)
        with pytest.raises(ValueError, match="limit"):
            client.files.search("pricing", limit=limit)
        with pytest.raises(ValueError, match="limit"):
            client.companies.semantic_search("fintech", limit=limit)

    def test_company_and_ids_are_exclusive(self, client: Affinity) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            client.notes.search("pricing", company_id=1, note_ids=[2])
        with pytest.raises(ValueError, match="mutually exclusive"):
            client.files.search("pricing", company_id=1, file_ids=[2])

    def test_ids_at_most_100(self, client: Affinity) -> None:
        with pytest.raises(ValueError, match="at most 100"):
            client.notes.search("pricing", note_ids=list(range(1, 102)))
        with pytest.raises(ValueError, match="at most 100"):
            client.companies.semantic_search("fintech", list_ids=list(range(1, 102)))

    def test_empty_ids_rejected(self, client: Affinity) -> None:
        with pytest.raises(ValueError, match="empty"):
            client.files.search("pricing", file_ids=[])


# =============================================================================
# Requests
# =============================================================================


class TestRequests:
    def test_note_search_body_and_results(
        self, respx_mock: respx.MockRouter, client: Affinity
    ) -> None:
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(201, json=NOTE_HITS)
        )
        hits = client.notes.search("pricing", company_id=5, limit=3)
        assert _body(route) == {"prompt": "pricing", "companyId": 5, "limit": 3}
        assert [int(h.note.id) for h in hits] == [11, 12]

    def test_note_search_default_body_has_no_limit(
        self, respx_mock: respx.MockRouter, client: Affinity
    ) -> None:
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(201, json={"data": []})
        )
        assert client.notes.search("pricing", note_ids=[1, 2]) == []
        assert _body(route) == {"prompt": "pricing", "noteIds": [1, 2]}

    def test_file_search_body_and_results(
        self, respx_mock: respx.MockRouter, client: Affinity
    ) -> None:
        route = respx_mock.post(f"{V2}/files/search").mock(
            return_value=Response(201, json=FILE_HITS)
        )
        hits = client.files.search("pitch deck", file_ids=[21, 22])
        assert _body(route) == {"prompt": "pitch deck", "fileIds": [21, 22]}
        assert [h.page_number for h in hits] == [3, None]

    def test_semantic_search_body_and_results(
        self, respx_mock: respx.MockRouter, client: Affinity
    ) -> None:
        route = respx_mock.post(f"{V2}/semantic-search").mock(
            return_value=Response(201, json=SEMANTIC)
        )
        result = client.companies.semantic_search("AI infra", list_ids=[7], limit=10)
        assert _body(route) == {
            "prompt": "AI infra",
            "entityType": "companies",
            "listIds": [7],
            "limit": 10,
        }
        assert [int(c.id) for c in result.data] == [31, 32]

    @pytest.mark.asyncio
    async def test_async_search_methods(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(f"{V2}/notes/search").mock(return_value=Response(201, json=NOTE_HITS))
        respx_mock.post(f"{V2}/files/search").mock(return_value=Response(201, json=FILE_HITS))
        respx_mock.post(f"{V2}/semantic-search").mock(return_value=Response(201, json=SEMANTIC))
        async with AsyncAffinity(api_key="k", max_retries=0) as client:
            notes = await client.notes.search("pricing")
            files = await client.files.search("pitch deck", company_id=3)
            companies = await client.companies.semantic_search("AI infra")
        assert len(notes) == 2
        assert files[0].file.name == "deck.pdf"
        assert companies.explanation == SEMANTIC["explanation"]

    @pytest.mark.asyncio
    async def test_async_validation(self) -> None:
        async with AsyncAffinity(api_key="k", max_retries=0) as client:
            with pytest.raises(ValueError):
                await client.notes.search("ab")
            with pytest.raises(ValueError):
                await client.files.search("pricing", company_id=1, file_ids=[1])
            with pytest.raises(ValueError):
                await client.companies.semantic_search("x", limit=101)


# =============================================================================
# Read-only POST: write policy and retries
# =============================================================================


class TestReadOnlyPost:
    def test_search_allowed_under_write_deny(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(f"{V2}/notes/search").mock(return_value=Response(201, json=NOTE_HITS))
        respx_mock.post(f"{V2}/files/search").mock(return_value=Response(201, json=FILE_HITS))
        respx_mock.post(f"{V2}/semantic-search").mock(return_value=Response(201, json=SEMANTIC))
        client = Affinity(api_key="k", max_retries=0, policies=Policies(write=WritePolicy.DENY))
        try:
            assert len(client.notes.search("pricing")) == 2
            assert len(client.files.search("pitch deck")) == 2
            assert len(client.companies.semantic_search("AI infra").data) == 2
        finally:
            client.close()

    def test_plain_post_still_denied(self) -> None:
        client = HTTPClient(
            ClientConfig(api_key="k", max_retries=0, policies=Policies(write=WritePolicy.DENY))
        )
        try:
            with pytest.raises(WriteNotAllowedError):
                client.post("/notes/search", json={"prompt": "pricing"})
        finally:
            client.close()

    @pytest.mark.asyncio
    async def test_async_search_allowed_under_write_deny(
        self, respx_mock: respx.MockRouter
    ) -> None:
        respx_mock.post(f"{V2}/notes/search").mock(return_value=Response(201, json=NOTE_HITS))
        async with AsyncAffinity(
            api_key="k", max_retries=0, policies=Policies(write=WritePolicy.DENY)
        ) as client:
            assert len(await client.notes.search("pricing")) == 2

    def test_read_only_post_retried_on_429(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("affinity.clients.http.time.sleep", sleeps.append)
        route = respx_mock.post(f"{V2}/notes/search").mock(
            side_effect=[
                Response(429, json={"message": "rate limit"}, headers={"Retry-After": "2"}),
                Response(201, json=NOTE_HITS),
            ]
        )
        client = HTTPClient(ClientConfig(api_key="k", max_retries=2, retry_delay=0.01))
        try:
            data = client.post("/notes/search", json={"prompt": "pricing"}, read_only=True)
        finally:
            client.close()
        assert route.call_count == 2
        assert 2.0 in sleeps
        assert len(data["data"]) == 2

    def test_read_only_post_retried_on_500(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _s: None)
        route = respx_mock.post(f"{V2}/semantic-search").mock(
            side_effect=[Response(503, json={"message": "busy"}), Response(201, json=SEMANTIC)]
        )
        client = HTTPClient(ClientConfig(api_key="k", max_retries=1, retry_delay=0.01))
        try:
            client.post("/semantic-search", json={"prompt": "x"}, read_only=True)
        finally:
            client.close()
        assert route.call_count == 2

    def test_plain_post_not_retried_on_429(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _s: None)
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(429, json={"message": "rate limit"}, headers={"Retry-After": "1"})
        )
        client = HTTPClient(ClientConfig(api_key="k", max_retries=2, retry_delay=0.01))
        try:
            with pytest.raises(AffinityError):
                client.post("/notes/search", json={"prompt": "pricing"})
        finally:
            client.close()
        assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_async_read_only_post_retried_on_429(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        async def fake_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("affinity.clients.http.asyncio.sleep", fake_sleep)
        route = respx_mock.post(f"{V2}/files/search").mock(
            side_effect=[
                Response(429, json={"message": "rate limit"}, headers={"Retry-After": "1"}),
                Response(201, json=FILE_HITS),
            ]
        )
        client = AsyncHTTPClient(ClientConfig(api_key="k", max_retries=2, retry_delay=0.01))
        try:
            data = await client.post("/files/search", json={"prompt": "deck"}, read_only=True)
        finally:
            await client.close()
        assert route.call_count == 2
        assert len(data["data"]) == 2

    def test_transport_error_retried_for_read_only_post(
        self, respx_mock: respx.MockRouter, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr("affinity.clients.http.time.sleep", lambda _s: None)
        route = respx_mock.post(f"{V2}/notes/search").mock(
            side_effect=[httpx.ConnectError("boom"), Response(201, json=NOTE_HITS)]
        )
        client = HTTPClient(ClientConfig(api_key="k", max_retries=1, retry_delay=0.01))
        try:
            client.post("/notes/search", json={"prompt": "pricing"}, read_only=True)
        finally:
            client.close()
        assert route.call_count == 2
