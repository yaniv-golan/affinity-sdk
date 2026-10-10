"""Tests for MCP registry generator limit config logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.generate_mcp_command_registry import add_limit_config, get_param_with_aliases


class TestGetParamWithAliases:
    def test_finds_canonical_flag(self) -> None:
        params = {
            "--max-results": {"type": "int", "aliases": ["--limit", "-n"]},
        }
        result = get_param_with_aliases(params, "--max-results")
        assert result == ("--max-results", ["--max-results", "--limit", "-n"])

    def test_finds_flag_by_alias(self) -> None:
        params = {
            "--max-results": {"type": "int", "aliases": ["--limit", "-n"]},
        }
        result = get_param_with_aliases(params, "--limit")
        assert result == ("--max-results", ["--max-results", "--limit", "-n"])

    def test_finds_flag_by_short_alias(self) -> None:
        params = {
            "--max-results": {"type": "int", "aliases": ["--limit", "-n"]},
        }
        result = get_param_with_aliases(params, "-n")
        assert result == ("--max-results", ["--max-results", "--limit", "-n"])

    def test_returns_none_for_missing_flag(self) -> None:
        params = {
            "--other": {"type": "flag"},
        }
        assert get_param_with_aliases(params, "--max-results") is None

    def test_handles_param_without_aliases(self) -> None:
        params = {
            "--max-results": {"type": "int"},
        }
        result = get_param_with_aliases(params, "--max-results")
        assert result == ("--max-results", ["--max-results"])


class TestAddLimitConfig:
    def test_adds_limit_config_with_all_flag(self) -> None:
        cmd: dict[str, object] = {
            "name": "list export",
            "parameters": {
                "--max-results": {"type": "int", "aliases": ["--limit", "-n"]},
                "--all": {"type": "flag", "aliases": ["-A"]},
            },
        }
        add_limit_config(cmd)

        assert "limitConfig" in cmd
        limit_config = cmd["limitConfig"]
        assert isinstance(limit_config, dict)
        assert limit_config["flag"] == "--max-results"
        assert limit_config["unboundedFlag"] == "--all"
        assert limit_config["unboundedFlagAliases"] == ["--all", "-A"]
        assert limit_config["default"] == 1000
        assert limit_config["max"] == 10000

    def test_adds_limit_config_without_all_flag(self) -> None:
        cmd: dict[str, object] = {
            "name": "interaction ls",
            "parameters": {
                "--max-results": {"type": "int", "aliases": ["--limit", "-n"]},
            },
        }
        add_limit_config(cmd)

        assert "limitConfig" in cmd
        limit_config = cmd["limitConfig"]
        assert isinstance(limit_config, dict)
        assert "unboundedFlag" not in limit_config
        assert "unboundedFlagAliases" not in limit_config
        assert limit_config["flag"] == "--max-results"

    def test_no_limit_config_without_limit_param(self) -> None:
        cmd: dict[str, object] = {
            "name": "person get",
            "parameters": {
                "--expand": {"type": "string"},
            },
        }
        add_limit_config(cmd)

        assert "limitConfig" not in cmd

    def test_falls_back_to_limit_flag(self) -> None:
        """If --max-results not present, should check for --limit."""
        cmd: dict[str, object] = {
            "name": "some cmd",
            "parameters": {
                "--limit": {"type": "int"},
            },
        }
        add_limit_config(cmd)

        assert "limitConfig" in cmd
        limit_config = cmd["limitConfig"]
        assert isinstance(limit_config, dict)
        assert limit_config["flag"] == "--limit"

    def test_handles_empty_parameters(self) -> None:
        cmd: dict[str, object] = {
            "name": "some cmd",
            "parameters": {},
        }
        add_limit_config(cmd)

        assert "limitConfig" not in cmd

    def test_handles_missing_parameters(self) -> None:
        cmd: dict[str, object] = {
            "name": "some cmd",
        }
        add_limit_config(cmd)

        assert "limitConfig" not in cmd


class TestConfiguredLimitConfig:
    """`limitConfig` from mcp-commands.json: values kept, flags from the CLI, `false` = none."""

    def _cmd(self, **extra: object) -> dict[str, object]:
        return {
            "name": "note search",
            "parameters": {"--max-results": {"type": "int", "aliases": ["-n"]}},
            **extra,
        }

    def test_configured_values_are_kept_and_flags_come_from_the_cli(self) -> None:
        cmd = self._cmd(limitConfig={"default": 20, "max": 100, "flag": "--wrong"})
        add_limit_config(cmd)
        assert cmd["limitConfig"] == {
            "flag": "--max-results",
            "flagAliases": ["--max-results", "-n"],
            "default": 20,
            "max": 100,
        }

    def test_partial_config_gets_the_other_default(self) -> None:
        cmd = self._cmd(limitConfig={"max": 100})
        add_limit_config(cmd)
        assert cmd["limitConfig"]["default"] == 1000  # type: ignore[index]
        assert cmd["limitConfig"]["max"] == 100  # type: ignore[index]

    def test_false_means_no_limit_config(self) -> None:
        cmd: dict[str, object] = {
            "name": "company files read",
            "parameters": {"--limit": {"type": "string"}},
            "limitConfig": False,
        }
        add_limit_config(cmd)
        assert "limitConfig" not in cmd

    def test_configured_without_a_limit_flag_is_an_error(self) -> None:
        cmd: dict[str, object] = {"name": "x", "parameters": {}, "limitConfig": {"max": 5}}
        with pytest.raises(ValueError, match="no --max-results"):
            add_limit_config(cmd)


def test_search_limits_in_the_registry_match_the_sdk() -> None:
    from affinity.services.search import (
        FILE_SEARCH_DEFAULT_LIMIT,
        LIMIT_MAX,
        NOTE_SEARCH_DEFAULT_LIMIT,
        SEMANTIC_SEARCH_DEFAULT_LIMIT,
    )

    registry = json.loads(
        (Path(__file__).resolve().parents[1] / "mcp/.registry/commands.generated.json").read_text()
    )
    limits = {c["name"]: c.get("limitConfig") for c in registry["commands"]}
    expected = {
        "note search": NOTE_SEARCH_DEFAULT_LIMIT,
        "file search": FILE_SEARCH_DEFAULT_LIMIT,
        "company search": SEMANTIC_SEARCH_DEFAULT_LIMIT,
    }
    for name, default in expected.items():
        assert limits[name]["default"] == default, name
        assert limits[name]["max"] == LIMIT_MAX, name


def test_registry_is_generated_from_the_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generator runs the CLI from this repo's source, not the `xaffinity` on PATH, and
    stamps the pyproject version: an editable install's version is fixed at install time, so
    checking it failed every commit after a version bump until a reinstall."""
    from tools import generate_mcp_command_registry as gen

    monkeypatch.setenv("PATH", str(tmp_path))  # no xaffinity anywhere
    output = tmp_path / "commands.generated.json"
    repo = Path(gen.__file__).resolve().parents[1]
    gen.generate_registry(repo / "mcp" / ".registry" / "mcp-commands.json", output)

    data = json.loads(output.read_text())
    assert data["cliVersion"] == gen.get_pyproject_version()
    assert data["commands"]
    committed = json.loads((repo / "mcp" / ".registry" / "commands.generated.json").read_text())
    assert data["commands"] == committed["commands"]
