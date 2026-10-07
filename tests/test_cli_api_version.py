"""CLI support for pinning the Affinity V2 API version (--api-version et al.)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from affinity.api_versions import AFFINITY_API_VERSION_HEADER
from affinity.cli.commands import config_cmds
from affinity.cli.commands.config_cmds import (
    _probe_key_default_api_version as real_probe_key_default_api_version,
)
from affinity.cli.config import LoadedConfig, ProfileConfig, load_config
from affinity.cli.context import CLIContext
from affinity.cli.errors import CLIError
from affinity.cli.main import cli
from affinity.cli.session_cache import SessionCache, SessionCacheConfig
from affinity.models.entities import AffinityList

V2 = "https://api.affinity.co/v2"

WHOAMI = {
    "tenant": {"id": 1, "name": "T", "subdomain": "t"},
    "user": {"id": 2, "firstName": "A", "lastName": "B", "emailAddress": "a@b.co"},
    "grant": {"type": "api-key", "scopes": ["api"], "createdAt": "2024-01-01T00:00:00Z"},
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AFFINITY_API_VERSION", "AFFINITY_PROFILE", "AFFINITY_SESSION_CACHE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AFFINITY_API_KEY", "test-key")


def _use_profile_config(monkeypatch: pytest.MonkeyPatch, **profile: Any) -> None:
    loaded = LoadedConfig(default=ProfileConfig(**profile), profiles={})
    monkeypatch.setattr(CLIContext, "load_config", lambda _self: loaded)


def _ctx(api_version: str | None = None) -> CLIContext:
    return CLIContext(
        output="json",
        quiet=True,
        verbosity=0,
        pager=False,
        progress="never",
        profile=None,
        dotenv=False,
        env_file=Path(".env"),
        api_key_file=None,
        api_key_stdin=False,
        timeout=None,
        max_retries=0,
        readonly=False,
        trace=False,
        log_file=None,
        enable_log_file=False,
        enable_beta_endpoints=False,
        api_version=api_version,
    )


def _handler(seen: list[httpx.Request], *, key_default: str = "2024-01-01") -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        requested = request.headers.get(AFFINITY_API_VERSION_HEADER)
        echo = requested or key_default
        if str(request.url) == f"{V2}/auth/whoami":
            return httpx.Response(200, json=WHOAMI, headers={AFFINITY_API_VERSION_HEADER: echo})
        if requested == "2027-01-01":
            return httpx.Response(
                400,
                json={
                    "errors": [
                        {
                            "code": "validation",
                            "param": "X-Affinity-Api-Version",
                            "message": "The provided version: '2027-01-01' is not valid.",
                        }
                    ]
                },
                headers={AFFINITY_API_VERSION_HEADER: key_default},
            )
        return httpx.Response(404, json={"message": "nope"})

    return handler


@pytest.fixture
def mock_transport(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Route the CLI's real get_client() (incl. api version plumbing) to a mock transport."""
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(seen))
    original_init = httpx.Client.__init__

    def patched_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched_init)
    return seen


# ---------------------------------------------------------------------------
# Precedence / validation
# ---------------------------------------------------------------------------


class TestResolution:
    def test_default_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch)
        assert _ctx().resolve_client_settings(warnings=[]).affinity_api_version is None

    def test_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch, api_version="2026-07-15")
        assert _ctx().resolve_api_version(warnings=[]) == "2026-07-15"

    def test_env_beats_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch, api_version="2026-07-15")
        monkeypatch.setenv("AFFINITY_API_VERSION", " 2026-09-17 ")
        assert _ctx().resolve_api_version(warnings=[]) == "2026-09-17"

    def test_flag_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch, api_version="2026-07-15")
        monkeypatch.setenv("AFFINITY_API_VERSION", "2026-09-17")
        assert _ctx("current").resolve_api_version(warnings=[]) == "current"

    def test_flag_auto_overrides_env_and_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch, api_version="2026-07-15")
        monkeypatch.setenv("AFFINITY_API_VERSION", "2026-09-17")
        assert _ctx("auto").resolve_api_version(warnings=[]) is None

    def test_empty_env_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # MCPB passes "" for an unset optional user_config field.
        _use_profile_config(monkeypatch, api_version="2026-07-15")
        monkeypatch.setenv("AFFINITY_API_VERSION", "")
        assert _ctx().resolve_api_version(warnings=[]) == "2026-07-15"

    def test_invalid_env_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch)
        monkeypatch.setenv("AFFINITY_API_VERSION", "garbage")
        with pytest.raises(CLIError) as exc_info:
            _ctx().resolve_api_version(warnings=[])
        assert exc_info.value.exit_code == 2
        assert "AFFINITY_API_VERSION" in exc_info.value.message

    def test_unknown_date_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch)
        warnings: list[str] = []
        assert _ctx("2027-01-01").resolve_api_version(warnings=warnings) == "2027-01-01"
        assert any("2027-01-01" in w for w in warnings)

    def test_profile_config_parses_api_version(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            '[default]\napi_version = "2026-07-15"\n[profiles.new]\napi_version = "2026-09-17"\n'
        )
        loaded = load_config(path)
        assert loaded.default.api_version == "2026-07-15"
        assert loaded.profiles["new"].api_version == "2026-09-17"

    def test_invalid_flag_is_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["--api-version", "garbage", "--json", "whoami"])
        assert result.exit_code == 2
        assert "api-version" in result.output


# ---------------------------------------------------------------------------
# End-to-end through the CLI
# ---------------------------------------------------------------------------


class TestCommands:
    def test_whoami_meta_and_key_default_without_pin(
        self, monkeypatch: pytest.MonkeyPatch, mock_transport: list[httpx.Request]
    ) -> None:
        _use_profile_config(monkeypatch)
        result = CliRunner().invoke(cli, ["--no-cache", "--json", "whoami"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["meta"]["affinityApiVersion"] == "2024-01-01"
        assert payload["data"]["keyDefaultApiVersion"] == "2024-01-01"
        assert len(mock_transport) == 1
        assert AFFINITY_API_VERSION_HEADER not in mock_transport[0].headers

    def test_whoami_pinned_reports_both(
        self, monkeypatch: pytest.MonkeyPatch, mock_transport: list[httpx.Request]
    ) -> None:
        _use_profile_config(monkeypatch)
        result = CliRunner().invoke(
            cli, ["--no-cache", "--api-version", "2026-09-17", "--json", "whoami"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["meta"]["affinityApiVersion"] == "2026-09-17"
        assert payload["data"]["keyDefaultApiVersion"] == "2024-01-01"
        assert payload["warnings"] == []
        assert mock_transport[0].headers.get(AFFINITY_API_VERSION_HEADER) == "2026-09-17"
        assert AFFINITY_API_VERSION_HEADER not in mock_transport[1].headers

    def test_env_pins_version(
        self, monkeypatch: pytest.MonkeyPatch, mock_transport: list[httpx.Request]
    ) -> None:
        _use_profile_config(monkeypatch)
        monkeypatch.setenv("AFFINITY_API_VERSION", "2026-07-15")
        result = CliRunner().invoke(cli, ["--no-cache", "--json", "whoami"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["meta"]["affinityApiVersion"] == "2026-07-15"
        assert mock_transport[0].headers.get(AFFINITY_API_VERSION_HEADER) == "2026-07-15"

    @pytest.mark.usefixtures("mock_transport")
    def test_rejected_version_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch)
        # The mock rejects 2027-01-01 on every V2 route except whoami.
        result = CliRunner().invoke(
            cli, ["--no-cache", "--api-version", "2027-01-01", "--json", "company", "get", "5"]
        )
        payload = json.loads(result.output)
        assert result.exit_code == 2, result.output
        assert payload["ok"] is False
        assert payload["error"]["type"] == "api_version_error"
        assert "2027-01-01" in payload["error"]["message"]
        assert payload["error"]["details"]["affinityApiVersion"] == "2027-01-01"

    def test_meta_lists_multiple_versions_with_warning(self) -> None:
        from affinity.cli.runner import api_version_meta

        ctx = _ctx()
        ctx.note_api_versions({"2026-09-17", "2024-01-01"})
        warnings: list[str] = []
        assert api_version_meta(ctx, warnings) == ["2024-01-01", "2026-09-17"]
        assert len(warnings) == 1 and "more than one" in warnings[0]


# ---------------------------------------------------------------------------
# check-key
# ---------------------------------------------------------------------------


class TestCheckKey:
    def test_check_key_reports_key_default(
        self, monkeypatch: pytest.MonkeyPatch, mock_transport: list[httpx.Request]
    ) -> None:
        _use_profile_config(monkeypatch)
        monkeypatch.setattr(
            config_cmds, "_probe_key_default_api_version", real_probe_key_default_api_version
        )
        result = CliRunner().invoke(
            cli, ["--api-version", "2026-09-17", "--json", "config", "check-key"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["data"]["configured"] is True
        assert payload["data"]["keyDefaultApiVersion"] == "2024-01-01"
        assert AFFINITY_API_VERSION_HEADER not in mock_transport[0].headers

    def test_check_key_probe_is_best_effort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_profile_config(monkeypatch)

        def boom(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline")

        transport = httpx.MockTransport(boom)
        original_init = httpx.Client.__init__

        def patched_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)
        monkeypatch.setattr(
            config_cmds, "_probe_key_default_api_version", real_probe_key_default_api_version
        )
        result = CliRunner().invoke(cli, ["--json", "config", "check-key"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["keyDefaultApiVersion"] is None

    def test_check_key_never_runs_key_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _ctx()
        assert real_probe_key_default_api_version(ctx, "command") is None

        # Even when check-key found another source first, the probe must not resolve the key
        # through AFFINITY_API_KEY_COMMAND (resolve_api_key checks it before the config file).
        def no_command(_cmd: str) -> str:
            raise AssertionError("key command must not run")

        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
        monkeypatch.setenv("AFFINITY_API_KEY_COMMAND", "echo secret")
        monkeypatch.setattr("affinity._internal.keyfile.read_key_command", no_command)
        assert real_probe_key_default_api_version(ctx, "dotenv") is None


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------


class TestSessionCache:
    def _cache(self, tmp_path: Path, api_version: str | None) -> SessionCache:
        config = SessionCacheConfig()
        config.cache_dir = tmp_path
        config.enabled = True
        config.set_tenant_hash("same-key", api_version=api_version)
        return SessionCache(config)

    def test_isolated_per_version(self, tmp_path: Path) -> None:
        default = self._cache(tmp_path, None)
        pinned = self._cache(tmp_path, "2026-09-17")
        assert default.config.tenant_hash != pinned.config.tenant_hash

        lst = AffinityList.model_validate(
            {"id": 1, "name": "L", "type": 0, "public": False, "ownerId": 1, "listSize": 0}
        )
        default.set("list_1", lst)
        assert default.get("list_1", AffinityList) is not None
        assert pinned.get("list_1", AffinityList) is None

    def test_echo_stored_and_replayed(self, tmp_path: Path) -> None:
        writer = self._cache(tmp_path, "2026-09-17")
        writer.version_provider = lambda: "2026-09-17"
        lst = AffinityList.model_validate(
            {"id": 1, "name": "L", "type": 0, "public": False, "ownerId": 1, "listSize": 0}
        )
        writer.set("list_1", lst)
        writer.set("lists", [lst])

        reader = self._cache(tmp_path, "2026-09-17")
        assert reader.get("list_1", AffinityList) is not None
        assert reader.get_list("lists", AffinityList) is not None
        assert reader.api_versions_seen == {"2026-09-17"}

    def test_cli_context_reports_session_cache_versions(self, tmp_path: Path) -> None:
        ctx = _ctx()
        cache = self._cache(tmp_path, None)
        cache.api_versions_seen.add("2024-01-01")
        ctx._session_cache = cache
        assert ctx.api_versions_seen() == ["2024-01-01"]
