"""Tests for affinity.cli.help_json — JSON help output generator."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

from click import Argument, Choice, Option
from click.testing import CliRunner

from affinity.cli.help_json import (
    MissingCategoryError,
    _classify_command,
    _extract_command,
    _extract_option,
    _extract_positional,
    _get_param_type,
    _parse_examples,
)
from affinity.cli.main import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_command(
    name: str = "test-cmd",
    *,
    category: str | None = "read",
    destructive: bool = False,
    progress_capable: bool = False,
    help_text: str = "A test command.",
) -> MagicMock:
    """Build a minimal mock Click command with decorator metadata."""
    cmd = MagicMock(spec=["name", "help", "params"])
    cmd.name = name
    cmd.help = help_text
    cmd.params = []
    if category is not None:
        cmd.category = category
    # Simulate missing attribute
    elif hasattr(cmd, "category"):
        delattr(cmd, "category")
    cmd.destructive = destructive
    cmd.progress_capable = progress_capable
    return cmd


# ---------------------------------------------------------------------------
# _classify_command
# ---------------------------------------------------------------------------


class TestClassifyCommand:
    def test_returns_category(self) -> None:
        cmd = _make_command(category="write")
        cat, dest, prog = _classify_command(cmd, "test")
        assert cat == "write"
        assert dest is False
        assert prog is False

    def test_destructive_flag(self) -> None:
        cmd = _make_command(destructive=True)
        _, dest, _ = _classify_command(cmd, "test")
        assert dest is True

    def test_progress_capable_flag(self) -> None:
        cmd = _make_command(progress_capable=True)
        _, _, prog = _classify_command(cmd, "test")
        assert prog is True

    def test_missing_category_raises(self) -> None:
        cmd = MagicMock()
        # Ensure no category attribute
        del cmd.category
        with pytest.raises(MissingCategoryError, match="missing @category"):
            _classify_command(cmd, "some cmd")


# ---------------------------------------------------------------------------
# _get_param_type
# ---------------------------------------------------------------------------


class TestGetParamType:
    def test_flag_option(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = True
        opt.type = MagicMock(name="BOOL")
        assert _get_param_type(opt) == "flag"

    def test_int_type(self) -> None:
        from click import INT

        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = INT
        assert _get_param_type(opt) == "int"

    def test_int_range_type(self) -> None:
        from click import IntRange

        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = IntRange(1, None)
        assert _get_param_type(opt) == "int"

    def test_bool_type(self) -> None:
        from click import BOOL

        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = BOOL
        assert _get_param_type(opt) == "bool"

    def test_choice_type(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = Choice(["a", "b"])
        assert _get_param_type(opt) == "string"

    def test_default_string(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = MagicMock()
        opt.type.name = "TEXT"
        assert _get_param_type(opt) == "string"


# ---------------------------------------------------------------------------
# _extract_option
# ---------------------------------------------------------------------------


class TestExtractOption:
    def test_basic_option(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = MagicMock()
        opt.type.name = "TEXT"
        opt.required = True
        opt.help = "A helpful description"
        opt.multiple = False
        opt.nargs = 1
        opt.opts = ["--name"]
        result = _extract_option(opt, ["--name"])
        assert result["type"] == "string"
        assert result["required"] is True
        assert result["help"] == "A helpful description"
        assert "multiple" not in result
        assert "nargs" not in result

    def test_with_choices(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = Choice(["json", "csv", "toon"])
        opt.required = False
        opt.help = "Output format"
        opt.multiple = False
        opt.nargs = 1
        opt.opts = ["--output"]
        result = _extract_option(opt, ["--output"])
        assert result["choices"] == ["json", "csv", "toon"]

    def test_multiple_flag(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = MagicMock()
        opt.type.name = "TEXT"
        opt.required = False
        opt.help = None
        opt.multiple = True
        opt.nargs = 1
        opt.opts = ["--tag"]
        result = _extract_option(opt, ["--tag"])
        assert result["multiple"] is True

    def test_nargs_greater_than_one(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = False
        opt.type = MagicMock()
        opt.type.name = "TEXT"
        opt.required = False
        opt.help = None
        opt.multiple = False
        opt.nargs = 2
        opt.opts = ["--set"]
        result = _extract_option(opt, ["--set"])
        assert result["nargs"] == 2

    def test_aliases(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = True
        opt.type = MagicMock()
        opt.required = False
        opt.help = "Verbose"
        opt.multiple = False
        opt.nargs = 1
        opt.opts = ["-v", "--verbose"]
        result = _extract_option(opt, ["-v", "--verbose"])
        assert result["aliases"] == ["-v"]

    def test_no_help_text(self) -> None:
        opt = MagicMock(spec=Option)
        opt.is_flag = True
        opt.type = MagicMock()
        opt.required = False
        opt.help = None
        opt.multiple = False
        opt.nargs = 1
        opt.opts = ["--flag"]
        result = _extract_option(opt, ["--flag"])
        assert "help" not in result


# ---------------------------------------------------------------------------
# _extract_positional
# ---------------------------------------------------------------------------


class TestExtractPositional:
    def test_basic(self) -> None:
        arg = MagicMock(spec=Argument)
        arg.name = "entity_id"
        arg.required = True
        arg.type = MagicMock()
        arg.type.name = "INT"
        result = _extract_positional(arg)
        assert result["name"] == "ENTITY_ID"
        assert result["required"] is True

    def test_no_name_fallback(self) -> None:
        arg = MagicMock(spec=Argument)
        arg.name = None
        arg.required = False
        arg.type = MagicMock()
        arg.type.name = "TEXT"
        result = _extract_positional(arg)
        assert result["name"] == "ARG"


# ---------------------------------------------------------------------------
# _parse_examples
# ---------------------------------------------------------------------------


class TestParseExamples:
    def test_parses_examples_with_prefix(self) -> None:
        docstring = (
            "List all persons.\n\n"
            "Examples:\n"
            "  - `xaffinity person ls`\n"
            "  - `xaffinity person ls --all`\n"
        )
        result = _parse_examples(docstring)
        assert result == ["person ls", "person ls --all"]

    def test_parses_examples_without_prefix(self) -> None:
        docstring = "Do a thing.\n\nExamples:\n  - `some-other-tool run`\n"
        result = _parse_examples(docstring)
        assert result == ["some-other-tool run"]

    def test_empty_when_no_section(self) -> None:
        docstring = "A simple command with no examples."
        assert _parse_examples(docstring) == []

    def test_empty_docstring(self) -> None:
        assert _parse_examples("") == []

    def test_stops_at_next_section(self) -> None:
        docstring = (
            "Do thing.\n\n"
            "Examples:\n"
            "  - `xaffinity do thing`\n"
            "Notes:\n"
            "  - `xaffinity should not match`\n"
        )
        result = _parse_examples(docstring)
        assert result == ["do thing"]


# ---------------------------------------------------------------------------
# _extract_command
# ---------------------------------------------------------------------------


class TestExtractCommand:
    def test_skips_root_command_with_no_name(self) -> None:
        cmd = MagicMock()
        cmd.name = None
        cmd.help = "Root"
        cmd.params = []
        cmd.category = "local"
        # Not a Group — use spec to prevent isinstance match
        cmd.__class__ = type("FakeCommand", (), {})
        result = _extract_command(cmd, prefix="")
        assert result == []

    def test_extracts_single_command(self) -> None:
        import click as _click

        @_click.command("greet")
        @_click.option("--name", help="Name to greet")
        def greet(name: str) -> None:
            """Say hello.

            Examples:
              - `xaffinity greet --name World`
            """

        greet.category = "local"  # type: ignore[attr-defined]
        greet.destructive = False  # type: ignore[attr-defined]
        greet.progress_capable = False  # type: ignore[attr-defined]

        result = _extract_command(greet)
        assert len(result) == 1
        entry = result[0]
        assert entry["name"] == "greet"
        assert entry["description"] == "Say hello."
        assert entry["category"] == "local"
        assert "--name" in entry["parameters"]
        assert entry["examples"] == ["greet --name World"]

    def test_recurses_into_group(self) -> None:
        import click as _click

        @_click.group("parent")
        def parent() -> None:
            """Parent group."""

        @parent.command("child")
        def child() -> None:
            """Child command."""

        child.category = "read"  # type: ignore[attr-defined]
        child.destructive = False  # type: ignore[attr-defined]
        child.progress_capable = False  # type: ignore[attr-defined]

        result = _extract_command(parent)
        assert len(result) == 1
        assert result[0]["name"] == "parent child"


# ---------------------------------------------------------------------------
# Integration: --help --json on real CLI
# ---------------------------------------------------------------------------


class TestHelpJsonIntegration:
    def test_help_json_flag_produces_valid_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "commands" in payload
        assert isinstance(payload["commands"], list)
        assert len(payload["commands"]) > 0

    def test_help_json_contains_expected_commands(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help", "--json"])
        payload = json.loads(result.output)
        names = {c["name"] for c in payload["commands"]}
        for expected in ("person ls", "company ls", "list ls"):
            assert expected in names, f"{expected} not in {names}"

    def test_help_json_command_structure(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help", "--json"])
        payload = json.loads(result.output)
        required_keys = {
            "name",
            "description",
            "category",
            "destructive",
            "progressCapable",
            "parameters",
            "positionals",
        }
        for cmd in payload["commands"]:
            missing = required_keys - set(cmd.keys())
            assert not missing, f"Command {cmd['name']} missing keys: {missing}"

    def test_help_json_commands_sorted(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help", "--json"])
        payload = json.loads(result.output)
        names = [c["name"] for c in payload["commands"]]
        assert names == sorted(names)
