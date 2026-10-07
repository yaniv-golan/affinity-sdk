"""CLI tests for `note search`, `file search` and `company search`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover - optional dev dependency
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

V2 = "https://api.affinity.co/v2"
ENV = {"AFFINITY_API_KEY": "test-key"}
MCP_ENV = {**ENV, "AFFINITY_MCP_MAX_LIMIT": "10000", "AFFINITY_MCP_DEFAULT_LIMIT": "1000"}

LONG_PREVIEW = "pricing " * 100  # 800 chars

NOTE_HITS = {
    "data": [
        {"note": {"id": 11, "kind": "note"}, "preview": LONG_PREVIEW},
        {"note": {"id": 12, "kind": "ai-summary"}, "preview": "short"},
    ]
}
FILE_HITS = {
    "data": [
        {"file": {"id": 21, "name": "deck.pdf"}, "pageNumber": 3, "preview": "ARR $12M"},
        {"file": {"id": 22, "name": "notes.txt"}, "pageNumber": None, "preview": LONG_PREVIEW},
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
        }
    ],
}


def _run(args: list[str], env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(cli, args, env=env or ENV)


def _json(result: Any) -> dict[str, Any]:
    return json.loads(result.output.strip().splitlines()[-1])


def _body(route: respx.Route) -> dict[str, Any]:
    return json.loads(route.calls[-1].request.content)


def _list_json(list_id: int, list_type: int) -> dict[str, Any]:
    return {
        "id": list_id,
        "name": f"List {list_id}",
        "type": list_type,
        "public": False,
        "owner_id": 1,
        "creator_id": 1,
    }


# =============================================================================
# note search
# =============================================================================


class TestNoteSearch:
    def test_json_full_preview_and_body(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(201, json=NOTE_HITS)
        )
        result = _run(["--json", "note", "search", "pricing", "-n", "5"])
        assert result.exit_code == 0, result.output
        payload = _json(result)
        assert payload["command"]["name"] == "note search"
        assert payload["data"][0] == {"noteId": 11, "kind": "note", "preview": LONG_PREVIEW}
        assert payload["data"][1]["kind"] == "ai-summary"
        assert "explanation" not in payload["meta"]
        assert _body(route) == {"prompt": "pricing", "limit": 5}

    def test_table_trims_preview(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(f"{V2}/notes/search").mock(return_value=Response(201, json=NOTE_HITS))
        result = _run(["note", "search", "pricing"])
        assert result.exit_code == 0, result.output
        assert "pricing pricing" in result.output
        assert LONG_PREVIEW.strip() not in result.output

    def test_readonly_allowed(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(f"{V2}/notes/search").mock(return_value=Response(201, json=NOTE_HITS))
        result = _run(["--readonly", "--json", "note", "search", "pricing"])
        assert result.exit_code == 0, result.output
        assert len(_json(result)["data"]) == 2

    def test_company_id_numeric_and_note_ids(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(201, json={"data": []})
        )
        result = _run(["--json", "note", "search", "pricing", "--company-id", "77"])
        assert result.exit_code == 0, result.output
        assert _body(route) == {"prompt": "pricing", "companyId": 77}
        assert _json(result)["meta"]["resolved"]["company"]["companyId"] == 77

        result = _run(["--json", "note", "search", "pricing", "--note-id", "1", "--note-id", "2"])
        assert result.exit_code == 0, result.output
        assert _body(route) == {"prompt": "pricing", "noteIds": [1, 2]}

    def test_company_id_accepts_url_selector(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(201, json={"data": []})
        )
        result = _run(
            [
                "--json",
                "note",
                "search",
                "pricing",
                "--company-id",
                "https://acme.affinity.co/companies/88",
            ]
        )
        assert result.exit_code == 0, result.output
        assert _body(route)["companyId"] == 88

    def test_company_and_note_ids_exclusive(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/notes/search")
        result = _run(
            ["--json", "note", "search", "pricing", "--company-id", "1", "--note-id", "2"]
        )
        assert result.exit_code == 2
        assert _json(result)["error"]["type"] == "usage_error"
        assert route.call_count == 0

    def test_short_prompt_is_usage_error(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/notes/search")
        result = _run(["--json", "note", "search", "ab"])
        assert result.exit_code == 2
        assert "at least 3" in _json(result)["error"]["message"]
        assert route.call_count == 0

    def test_limit_clamped_to_100_with_warning(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(201, json={"data": []})
        )
        result = _run(["--json", "note", "search", "pricing", "--max-results", "500"])
        assert result.exit_code == 0, result.output
        assert _body(route)["limit"] == 100
        assert any("100" in w for w in _json(result)["warnings"])

    def test_mcp_limits_not_injected(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/notes/search").mock(
            return_value=Response(201, json={"data": []})
        )
        result = _run(["--json", "note", "search", "pricing"], env=MCP_ENV)
        assert result.exit_code == 0, result.output
        assert "limit" not in _body(route)


# =============================================================================
# file search
# =============================================================================


class TestFileSearch:
    def test_json_rows(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/files/search").mock(
            return_value=Response(201, json=FILE_HITS)
        )
        result = _run(["--json", "file", "search", "pitch deck", "--company-id", "5"])
        assert result.exit_code == 0, result.output
        payload = _json(result)
        assert payload["command"]["name"] == "file search"
        assert payload["data"][0] == {
            "fileId": 21,
            "name": "deck.pdf",
            "pageNumber": 3,
            "preview": "ARR $12M",
        }
        assert payload["data"][1]["pageNumber"] is None
        assert payload["data"][1]["preview"] == LONG_PREVIEW
        assert _body(route) == {"prompt": "pitch deck", "companyId": 5}

    def test_readonly_and_file_ids(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/files/search").mock(
            return_value=Response(201, json=FILE_HITS)
        )
        result = _run(["--readonly", "--json", "file", "search", "deck", "--file-id", "21"])
        assert result.exit_code == 0, result.output
        assert _body(route) == {"prompt": "deck", "fileIds": [21]}

    def test_company_and_file_ids_exclusive(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/files/search")
        result = _run(["--json", "file", "search", "deck", "--company-id", "1", "--file-id", "2"])
        assert result.exit_code == 2
        assert route.call_count == 0

    def test_mcp_limits_not_injected(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/files/search").mock(
            return_value=Response(201, json={"data": []})
        )
        result = _run(["--json", "file", "search", "deck", "-n", "7"], env=MCP_ENV)
        assert result.exit_code == 0, result.output
        assert _body(route)["limit"] == 7

    def test_help_cross_references(self) -> None:
        group_help = _run(["file", "--help"])
        assert "file-url" in group_help.output
        url_help = _run(["file-url", "--help"])
        assert "file search" in url_help.output


# =============================================================================
# company search
# =============================================================================


class TestCompanySearch:
    def test_json_explanation_in_meta(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/semantic-search").mock(
            return_value=Response(201, json=SEMANTIC)
        )
        result = _run(["--json", "company", "search", "AI infrastructure startups"])
        assert result.exit_code == 0, result.output
        payload = _json(result)
        assert payload["meta"]["explanation"] == SEMANTIC["explanation"]
        assert payload["data"] == [
            {
                "id": 31,
                "name": "Acme AI",
                "domain": "acme.ai",
                "domains": ["acme.ai"],
                "isGlobal": True,
                "score": 0.85,
            }
        ]
        assert _body(route) == {"prompt": "AI infrastructure startups", "entityType": "companies"}

    def test_table_shows_explanation(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(f"{V2}/semantic-search").mock(return_value=Response(201, json=SEMANTIC))
        result = _run(["company", "search", "AI infrastructure startups"])
        assert result.exit_code == 0, result.output
        assert "Companies building AI infrastructure." in result.output
        assert "Acme AI" in result.output

    def test_readonly_allowed(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(f"{V2}/semantic-search").mock(return_value=Response(201, json=SEMANTIC))
        result = _run(["--readonly", "--json", "company", "search", "AI infra"])
        assert result.exit_code == 0, result.output

    def test_list_filter_company_list(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.get(f"{V2}/lists/42").mock(return_value=Response(200, json=_list_json(42, 1)))
        respx_mock.get("https://api.affinity.co/lists/42").mock(
            return_value=Response(200, json=_list_json(42, 1))
        )
        route = respx_mock.post(f"{V2}/semantic-search").mock(
            return_value=Response(201, json=SEMANTIC)
        )
        result = _run(["--json", "company", "search", "AI infra", "--list", "42", "-n", "200"])
        assert result.exit_code == 0, result.output
        assert _body(route) == {
            "prompt": "AI infra",
            "entityType": "companies",
            "listIds": [42],
            "limit": 100,
        }

    def test_list_filter_rejects_non_company_list(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.get(f"{V2}/lists/43").mock(return_value=Response(200, json=_list_json(43, 0)))
        respx_mock.get("https://api.affinity.co/lists/43").mock(
            return_value=Response(200, json=_list_json(43, 0))
        )
        route = respx_mock.post(f"{V2}/semantic-search")
        result = _run(["--json", "company", "search", "AI infra", "--list", "43"])
        assert result.exit_code == 2
        assert "not a company list" in _json(result)["error"]["message"]
        assert route.call_count == 0

    def test_mcp_limits_not_injected(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(f"{V2}/semantic-search").mock(
            return_value=Response(201, json=SEMANTIC)
        )
        result = _run(["--json", "company", "search", "AI infra"], env=MCP_ENV)
        assert result.exit_code == 0, result.output
        assert "limit" not in _body(route)

    def test_other_commands_have_no_explanation_key(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(f"{V2}/notes/search").mock(return_value=Response(201, json={"data": []}))
        result = _run(["--json", "note", "search", "pricing"])
        assert "explanation" not in _json(result)["meta"]


# =============================================================================
# Registration
# =============================================================================


def test_search_commands_are_read_category_without_mcp_limits() -> None:
    from affinity.cli.commands.company_cmds import company_search
    from affinity.cli.commands.file_cmds import file_search
    from affinity.cli.commands.note_cmds import note_search

    for cmd in (note_search, file_search, company_search):
        assert getattr(cmd, "category", None) == "read"
        # apply_mcp_limits would inject a default limit of 1000 (> the API max of 100).
        fn: Any = cmd.callback
        while fn is not None:
            assert "apply_mcp_limits" not in fn.__qualname__
            fn = getattr(fn, "__wrapped__", None)


def test_search_commands_in_mcp_registry() -> None:
    registry_path = Path(__file__).resolve().parent.parent / "mcp/.registry/mcp-commands.json"
    commands = json.loads(registry_path.read_text())["commands"]
    for name in ("note search", "file search", "company search"):
        entry = commands[name]
        assert entry["whenToUse"]
        assert entry["relatedCommands"]
        assert entry["examples"]
        assert "mcpBehavior" not in entry
    assert "company ls" in commands["company search"]["whenToUse"]
