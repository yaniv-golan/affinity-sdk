from __future__ import annotations

import json

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")

import click
from click.testing import CliRunner

from affinity.cli.config import LoadedConfig, ProfileConfig
from affinity.cli.context import error_info_for_exception, normalize_exception
from affinity.cli.main import cli
from affinity.cli.render import RenderSettings, render_result
from affinity.cli.results import CommandContext, CommandMeta, CommandResult, ErrorInfo
from affinity.exceptions import ErrorDiagnostics, ValidationError


def test_resolve_url_parsed_before_api_key_required() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["resolve-url", "not-a-url"], env={"AFFINITY_API_KEY": ""})
    assert result.exit_code == 2
    assert "URL must start with http:// or https://" in result.output


def test_missing_api_key_error_does_not_print_help_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that missing API key error doesn't include unhelpful --help hint.

    Mocks load_config to return empty config, preventing fallback to real config file.
    """
    # Return empty config with no API key
    empty_config = LoadedConfig(default=ProfileConfig(), profiles={})
    # Must patch in context module where load_config is imported
    monkeypatch.setattr("affinity.cli.context.load_config", lambda _path: empty_config)

    runner = CliRunner()
    result = runner.invoke(cli, ["whoami"], env={"AFFINITY_API_KEY": ""})
    assert result.exit_code == 2
    assert "Missing Affinity API key." in result.output
    assert "Hint: run `affinity whoami --help`" not in result.output


def test_ambiguous_resolution_renders_match_table(capsys: pytest.CaptureFixture[str]) -> None:
    result = CommandResult(
        ok=False,
        command=CommandContext(name="list export"),
        data=None,
        artifacts=[],
        warnings=[],
        meta=CommandMeta(duration_ms=0, profile=None, resolved=None, pagination=None, columns=None),
        error=ErrorInfo(
            type="ambiguous_resolution",
            message='Ambiguous list name: "Pipeline" (2 matches)',
            details={
                "selector": "Pipeline",
                "matches": [
                    {"listId": 1, "name": "Pipeline", "type": "opportunity"},
                    {"listId": 2, "name": "Pipeline", "type": "opportunity"},
                ],
            },
        ),
    )
    render_result(
        result,
        settings=RenderSettings(output="table", quiet=False, verbosity=0, pager=False),
    )
    captured = capsys.readouterr()
    assert "Ambiguous:" in captured.err
    assert "listId" in captured.err
    assert "Pipeline" in captured.err


def test_normalize_exception_file_exists_adds_actionable_hint() -> None:
    exc = FileExistsError(17, "File exists", "/tmp/out.csv")
    normalized = normalize_exception(exc)
    assert normalized.error_type == "file_exists"
    assert normalized.exit_code == 2
    assert normalized.hint is not None
    assert "--overwrite" in normalized.hint


def test_error_info_includes_hint_and_renders(capsys: pytest.CaptureFixture[str]) -> None:
    exc = FileExistsError(17, "File exists", "/tmp/out.csv")
    info = error_info_for_exception(exc)
    assert info.type == "file_exists"
    assert info.hint is not None

    result = CommandResult(
        ok=False,
        command=CommandContext(name="list export"),
        data=None,
        artifacts=[],
        warnings=[],
        meta=CommandMeta(duration_ms=0, profile=None, resolved=None, pagination=None, columns=None),
        error=info,
    )
    render_result(
        result,
        settings=RenderSettings(output="table", quiet=False, verbosity=0, pager=False),
    )
    captured = capsys.readouterr()
    assert "File exists:" in captured.err
    assert "Hint:" in captured.err


def test_validation_error_normalization_includes_sanitized_params() -> None:
    exc = ValidationError(
        "Field organization_id: expected 2249254 to be a valid id",
        status_code=422,
        diagnostics=ErrorDiagnostics(
            method="GET",
            url="https://api.affinity.co/entity-files",
            request_params={"organization_id": "2249254", "term": "secret"},
        ),
    )
    normalized = normalize_exception(exc, verbosity=0)
    assert normalized.error_type == "validation_error"
    assert normalized.exit_code == 2
    assert normalized.hint is not None
    assert "company_id=2249254" in normalized.hint
    assert normalized.details is not None
    assert normalized.details.get("params") == {"organization_id": 2249254}


@pytest.mark.req("SDK-CLI-JSON-ERROR-ENVELOPE")
class TestClickErrorJsonEnvelope:
    """Click-level errors emit JSON envelope when --json is active."""

    def test_no_such_command_json(self) -> None:
        """Bad command with --json emits JSON error envelope."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "badcommand"])
        assert result.exit_code == 2
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert "No such command" in payload["error"]["message"]
        assert payload["error"]["type"] == "usage_error"
        assert payload["error"]["hint"] is not None

    def test_no_such_command_no_json_unchanged(self) -> None:
        """Without --json, Click's default error behavior is preserved."""
        runner = CliRunner()
        result = runner.invoke(cli, ["badcommand"])
        assert result.exit_code == 2
        assert "No such command" in result.output

    def test_output_json_flag_also_works(self) -> None:
        """--output json triggers JSON error envelope too."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--output", "json", "badcommand"])
        assert result.exit_code == 2
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert payload["error"]["type"] == "usage_error"

    def test_parse_error_json(self) -> None:
        """Root-level parse errors (make_context) also emit JSON envelope."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "--no-such-option", "list", "ls"])
        assert result.exit_code == 2
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False

    def test_json_envelope_command_name_is_xaffinity(self) -> None:
        """Command name in error envelope is 'xaffinity' (not misidentified option value)."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--output", "json", "badcommand"])
        payload = json.loads(result.output.strip())
        assert payload["command"]["name"] == "xaffinity"

    def test_abort_in_json_mode_exits_cleanly(self) -> None:
        """Ctrl+C (Abort) in JSON mode exits with code 1, no traceback."""
        from unittest.mock import patch

        runner = CliRunner()
        with patch.object(cli, "invoke", side_effect=click.Abort()):
            result = runner.invoke(cli, ["--json", "list", "ls"])
        assert result.exit_code == 1
        assert "Traceback" not in (result.output or "")

    def test_normalize_click_usage_error(self) -> None:
        """normalize_exception handles click.UsageError with correct type and exit code."""
        exc = click.UsageError("--foo and --bar are mutually exclusive")
        normalized = normalize_exception(exc)
        assert normalized.error_type == "usage_error"
        assert normalized.exit_code == 2

    def test_standalone_mode_false_no_json_output(self) -> None:
        """Non-standalone callers get re-raised exceptions, not JSON on stdout."""
        with pytest.raises(click.UsageError):
            cli.main(["--json", "badcommand"], standalone_mode=False)

    def test_run_command_normalizes_click_usage_error(self) -> None:
        """click.UsageError inside a command handler flows through run_command's
        normalize -> build_result -> emit_result pipeline, producing usage_error."""
        from unittest.mock import MagicMock, patch

        from affinity.cli.runner import run_command

        mock_ctx = MagicMock()
        mock_ctx.output = "json"
        mock_ctx.verbosity = 0
        mock_ctx.quiet = True
        mock_ctx.profile = None
        mock_ctx._client = None

        def failing_fn(_ctx, _warnings):
            raise click.UsageError("--foo requires a value")

        with (
            patch("affinity.cli.runner._emit_json") as mock_emit,
            pytest.raises(click.exceptions.Exit) as exc_info,
        ):
            run_command(mock_ctx, command="test-cmd", fn=failing_fn)

        assert exc_info.value.exit_code == 2
        emitted = mock_emit.call_args[0][0]
        assert emitted.ok is False
        assert emitted.error.type == "usage_error"
        assert emitted.error.message == "--foo requires a value"
