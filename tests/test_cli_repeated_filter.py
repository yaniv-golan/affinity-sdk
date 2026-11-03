"""CLI tests: a repeated --filter (or --expand-filter) ANDs its values."""

from __future__ import annotations

import json

import click
import pytest

pytest.importorskip("rich_click")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner

from affinity.cli.main import cli
from affinity.cli.options import join_filters_callback
from affinity.filters import parse

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

ENV = {"AFFINITY_API_KEY": "test-key"}


def _join(*values: str) -> str | None:
    option = click.Option(["--filter"], multiple=True)
    ctx = click.Context(click.Command("x"))
    return join_filters_callback(ctx, option, values)


def test_join_none_one_many() -> None:
    assert _join() is None
    assert _join('Status = "Won"') == 'Status = "Won"'
    assert _join(" A = 1 ") == " A = 1 "  # a single value is passed on unchanged
    assert _join("A = 1", "B = 2") == "(A = 1) & (B = 2)"


def test_or_inside_one_value_keeps_precedence() -> None:
    joined = _join("a = 1 | b = 2", "c = 3")
    assert joined is not None
    assert str(parse(joined)) == str(parse("(a = 1 | b = 2) & c = 3"))


def test_a_value_cannot_close_the_wrapper() -> None:
    with pytest.raises(click.BadParameter, match="value 1"):
        _join("a = 1) | (b = 2", "c = 3")


def test_an_invalid_value_keeps_its_own_error() -> None:
    with pytest.raises(click.BadParameter, match="value 2") as info:
        _join('Status = "Won"', "Status = Intro Meeting")
    assert "quoted" in str(info.value)


def test_blank_among_repeats() -> None:
    with pytest.raises(click.BadParameter, match="value 2 is empty"):
        _join("Deals > 5", " ")


def test_cli_usage_error_envelope(respx_mock: respx.MockRouter) -> None:
    result = CliRunner().invoke(
        cli, ["--json", "company", "ls", "--filter", "Deals > 5", "--filter", " "], env=ENV
    )
    assert result.exit_code == 2
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["error"]["type"] == "usage_error"
    assert not respx_mock.calls


@pytest.mark.parametrize(
    ("path", "param"),
    [
        (("list", "export"), "filter_expr"),
        (("list", "export"), "expand_filter"),
        (("company", "ls"), "filter_expr"),
        (("person", "ls"), "filter_expr"),
    ],
)
def test_help_says_repeatable(path: tuple[str, str], param: str) -> None:
    group = cli.commands[path[0]]
    assert isinstance(group, click.Group)
    command = group.commands[path[1]]
    option = next(p for p in command.params if p.name == param)
    assert isinstance(option, click.Option)
    assert option.multiple
    assert "ANDed" in (option.help or "")
