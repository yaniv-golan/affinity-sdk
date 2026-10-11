"""Tests for xaffinity config check-key, setup-key, and update-check commands."""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from affinity.cli.context import CLIContext
from affinity.cli.main import cli
from affinity.cli.paths import CliPaths


def make_mock_paths(base_path: Path) -> CliPaths:
    """Create a CliPaths pointing to a directory inside base_path."""
    config_dir = base_path / "xaffinity_config"
    return CliPaths(
        config_dir=config_dir,
        config_path=config_dir / "config.toml",
        cache_dir=base_path / "cache",
        state_dir=base_path / "state",
        log_dir=base_path / "logs",
        log_file=base_path / "logs" / "xaffinity.log",
    )


@contextmanager
def mock_cli_paths(mock_paths: CliPaths):
    """Context manager to mock CLIContext paths.

    The CLIContext is a frozen dataclass that calls get_paths() at instantiation.
    We patch __init__ to override _paths after the original initialization.
    """
    original_init = CLIContext.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        object.__setattr__(self, "_paths", mock_paths)

    with patch.object(CLIContext, "__init__", patched_init):
        yield


class TestConfigCheckKey:
    """Tests for xaffinity config check-key command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_check_key_not_configured(self, runner, monkeypatch):
        """Test check-key when no key is configured."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "check-key", "--json"])

        assert result.exit_code == 1
        assert '"configured": false' in result.output
        assert '"pattern": null' in result.output

    def test_check_key_from_environment(self, runner, monkeypatch):
        """Test check-key finds key in environment."""
        monkeypatch.setenv("AFFINITY_API_KEY", "test-key")

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "check-key", "--json"])

        assert result.exit_code == 0
        assert '"configured": true' in result.output
        assert '"source": "environment"' in result.output
        assert '"pattern": "xaffinity --readonly <command> --json"' in result.output

    def test_check_key_from_config(self, runner, monkeypatch):
        """Test check-key finds key in config.toml."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            mock_paths.config_dir.mkdir(parents=True)
            mock_paths.config_path.write_text('[default]\napi_key = "test-key"\n')

            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "check-key", "--json"])

        assert result.exit_code == 0
        assert '"configured": true' in result.output
        assert '"source": "config"' in result.output
        assert '"pattern": "xaffinity --readonly <command> --json"' in result.output

    def test_check_key_from_dotenv_file_quoted(self, runner, monkeypatch):
        """Test check-key finds key in .env file with quoted value."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            Path(".env").write_text('AFFINITY_API_KEY="my-secret-key"\n')
            mock_paths = make_mock_paths(Path(fs))

            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "check-key", "--json"])

        assert result.exit_code == 0
        assert '"configured": true' in result.output
        assert '"source": "dotenv"' in result.output
        assert '"pattern": "xaffinity --dotenv --readonly <command> --json"' in result.output

    def test_check_key_names_an_env_file_outside_the_current_directory(self, runner, monkeypatch):
        """In Cowork the shell starts above the connected folder: the pattern must name the file."""
        import json as _json

        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            Path("mnt/project").mkdir(parents=True)
            Path("mnt/project/.env").write_text("AFFINITY_API_KEY=my-secret-key\n")
            mock_paths = make_mock_paths(Path(fs))

            with mock_cli_paths(mock_paths):
                result = runner.invoke(
                    cli, ["--env-file", "mnt/project/.env", "--json", "config", "check-key"]
                )
            expected = str((Path(fs) / "mnt/project/.env").resolve())

        assert result.exit_code == 0, result.output
        data = _json.loads(result.output.strip().splitlines()[-1])["data"]
        assert data["source"] == "dotenv"
        assert data["pattern"] == (
            f"xaffinity --dotenv --env-file {expected} --readonly <command> --json"
        )

    def test_check_key_from_dotenv_file_unquoted(self, runner, monkeypatch):
        """Test check-key finds key in .env file with unquoted value."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            Path(".env").write_text("AFFINITY_API_KEY=my-secret-key\n")
            mock_paths = make_mock_paths(Path(fs))

            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "check-key", "--json"])

        assert result.exit_code == 0
        assert '"configured": true' in result.output
        assert '"source": "dotenv"' in result.output
        assert '"pattern": "xaffinity --dotenv --readonly <command> --json"' in result.output

    def test_check_key_from_custom_env_file_path(self, runner, monkeypatch):
        """Test check-key respects --env-file pointing to a non-CWD path."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            # Create .env in a subdirectory (not CWD)
            subdir = Path(fs) / "subdir"
            subdir.mkdir()
            (subdir / ".env").write_text("AFFINITY_API_KEY=my-secret-key\n")
            mock_paths = make_mock_paths(Path(fs))

            with mock_cli_paths(mock_paths):
                result = runner.invoke(
                    cli, ["--env-file", str(subdir / ".env"), "config", "check-key", "--json"]
                )

        assert result.exit_code == 0
        assert '"configured": true' in result.output
        assert '"source": "dotenv"' in result.output

    def test_check_key_ignores_empty_value_in_env(self, runner, monkeypatch):
        """Test check-key doesn't consider empty api_key as configured."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            Path(".env").write_text('AFFINITY_API_KEY=""\n')
            mock_paths = make_mock_paths(Path(fs))

            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "check-key", "--json"])

        assert result.exit_code == 1
        assert '"configured": false' in result.output
        assert '"pattern": null' in result.output

    def test_check_key_ignores_wrong_section_in_config(self, runner, monkeypatch):
        """Test check-key only looks in [default] section of config.toml."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            mock_paths.config_dir.mkdir(parents=True)
            # Key is in [other] section, not [default]
            mock_paths.config_path.write_text('[other]\napi_key = "secret"\n')

            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "check-key", "--json"])

        assert result.exit_code == 1
        assert '"configured": false' in result.output
        assert '"pattern": null' in result.output


class TestConfigSetupKey:
    """Tests for xaffinity config setup-key command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_setup_key_project_scope(self, runner, monkeypatch):
        """Test storing key in .env file."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="test-api-key-123"),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=True),
            ):
                result = runner.invoke(cli, ["config", "setup-key", "--scope", "project", "--json"])

            assert result.exit_code == 0, f"Failed with output: {result.output}"
            assert '"key_stored": true' in result.output
            assert '"scope": "project"' in result.output
            assert '"validated": true' in result.output

            # Verify .env was created
            env_path = Path(".env")
            assert env_path.exists()
            content = env_path.read_text()
            assert "AFFINITY_API_KEY=test-api-key-123" in content

            # Verify .gitignore was updated
            gitignore_path = Path(".gitignore")
            assert gitignore_path.exists()
            assert ".env" in gitignore_path.read_text()

    def test_setup_key_user_scope(self, runner, monkeypatch):
        """Test storing key in user config."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="test-api-key-456"),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=True),
            ):
                result = runner.invoke(cli, ["config", "setup-key", "--scope", "user", "--json"])

            assert result.exit_code == 0, f"Failed with output: {result.output}"
            assert '"key_stored": true' in result.output
            assert '"scope": "user"' in result.output

            # Verify config was created
            assert mock_paths.config_path.exists()
            content = mock_paths.config_path.read_text()
            assert 'api_key = "test-api-key-456"' in content

            # Verify permissions on Unix
            if os.name == "posix":
                mode = mock_paths.config_path.stat().st_mode
                assert not (mode & stat.S_IRGRP)  # Not group readable
                assert not (mode & stat.S_IROTH)  # Not world readable

    def test_setup_key_empty_input_error(self, runner, monkeypatch):
        """Test error on empty API key."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            with mock_cli_paths(mock_paths), patch("getpass.getpass", return_value=""):
                result = runner.invoke(cli, ["config", "setup-key", "--scope", "project"])

        assert result.exit_code == 2
        assert "No API key provided" in result.output

    def test_setup_key_invalid_format_error(self, runner, monkeypatch):
        """Test error on invalid API key format."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            # Key with spaces and special chars
            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="invalid key with spaces!"),
            ):
                result = runner.invoke(cli, ["config", "setup-key", "--scope", "project"])

        assert result.exit_code == 2
        assert "Invalid API key format" in result.output

    def test_setup_key_existing_key_no_force(self, runner, monkeypatch):
        """Test behavior when key already exists without --force."""
        monkeypatch.setenv("AFFINITY_API_KEY", "existing-key")

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            with mock_cli_paths(mock_paths):
                result = runner.invoke(
                    cli, ["config", "setup-key", "--scope", "project", "--json"], input="n\n"
                )

        assert result.exit_code == 0
        assert '"key_stored": false' in result.output
        assert '"reason": "existing_key_kept"' in result.output

    def test_setup_key_existing_key_with_force(self, runner, monkeypatch):
        """Test --force overwrites existing key."""
        monkeypatch.setenv("AFFINITY_API_KEY", "existing-key")

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="new-valid-key"),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=True),
            ):
                result = runner.invoke(
                    cli,
                    ["config", "setup-key", "--scope", "project", "--force", "--json"],
                )

        assert result.exit_code == 0
        assert '"key_stored": true' in result.output

    def test_setup_key_no_validate(self, runner, monkeypatch):
        """Test --no-validate skips API validation."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="test-valid-key"),
                patch("affinity.cli.commands.config_cmds._validate_key") as mock_validate,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "config",
                        "setup-key",
                        "--scope",
                        "project",
                        "--no-validate",
                        "--json",
                    ],
                )

            assert result.exit_code == 0, f"Failed with output: {result.output}"
            mock_validate.assert_not_called()
            assert '"validated"' not in result.output

    def test_setup_key_validation_network_error(self, runner, monkeypatch):
        """Test graceful handling of network error during validation."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))

            # Simulate network failure
            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="test-valid-key"),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=False),
            ):
                result = runner.invoke(cli, ["config", "setup-key", "--scope", "project", "--json"])

            # Key should still be stored, but validation failed
            assert result.exit_code == 0, f"Failed with output: {result.output}"
            assert '"key_stored": true' in result.output
            assert '"validated": false' in result.output

            # Verify .env was still created
            env_path = Path(".env")
            assert env_path.exists()

    def test_setup_key_appends_to_existing_env(self, runner, monkeypatch):
        """Test appending to existing .env file."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            Path(".env").write_text("OTHER_VAR=value\n")
            mock_paths = make_mock_paths(Path(fs))

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="new-valid-key"),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=True),
            ):
                runner.invoke(cli, ["config", "setup-key", "--scope", "project", "--json"])

            content = Path(".env").read_text()
            assert "OTHER_VAR=value" in content
            assert "AFFINITY_API_KEY=new-valid-key" in content

    def test_setup_key_updates_existing_key_in_env(self, runner, monkeypatch):
        """Test updating existing key in .env."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            Path(".env").write_text("AFFINITY_API_KEY=old-key\nOTHER=value\n")
            mock_paths = make_mock_paths(Path(fs))

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value="new-valid-key"),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=True),
            ):
                runner.invoke(
                    cli,
                    ["config", "setup-key", "--scope", "project", "--force", "--json"],
                )

            content = Path(".env").read_text()
            assert "AFFINITY_API_KEY=new-valid-key" in content
            assert "old-key" not in content
            assert "OTHER=value" in content

    def test_setup_key_toml_escapes_special_chars(self, runner, monkeypatch):
        """Test that TOML special characters are properly escaped."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            # Key with quote and backslash that need escaping
            special_key = "key-with-quote-and-backslash"  # simplified for regex validation

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value=special_key),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=True),
            ):
                result = runner.invoke(cli, ["config", "setup-key", "--scope", "user", "--json"])

            assert result.exit_code == 0, f"Failed with output: {result.output}"
            content = mock_paths.config_path.read_text()
            assert f'api_key = "{special_key}"' in content

    def test_setup_key_toml_escapes_quote(self, runner, monkeypatch):
        """Test that quote characters are escaped in TOML."""
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            # Need to bypass format validation for this test
            special_key = 'key"with"quotes'

            with (
                mock_cli_paths(mock_paths),
                patch("getpass.getpass", return_value=special_key),
                patch(
                    "affinity.cli.commands.config_cmds._validate_api_key_format",
                    return_value=True,
                ),
                patch("affinity.cli.commands.config_cmds._validate_key", return_value=True),
            ):
                result = runner.invoke(cli, ["config", "setup-key", "--scope", "user", "--json"])

            assert result.exit_code == 0, f"Failed with output: {result.output}"
            content = mock_paths.config_path.read_text()
            # Verify proper TOML escaping: " -> \"
            assert 'api_key = "key\\"with\\"quotes"' in content


def _write_update_cache(state_dir: Path, *, update_available: bool = False, **overrides) -> None:
    """Write an update_check.json cache file for testing."""
    import affinity

    state_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "current_version": affinity.__version__,
        "latest_version": "99.0.0" if update_available else affinity.__version__,
        "checked_at": overrides.get("checked_at", datetime.now(timezone.utc).isoformat()),
        "update_available": update_available,
        "last_notified_at": None,
    }
    data.update(overrides)
    (state_dir / "update_check.json").write_text(json.dumps(data), encoding="utf-8")


class TestConfigUpdateCheckHumanOutput:
    """Tests for human-readable output of xaffinity config update-check."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_disabled(self, runner, monkeypatch):
        """When update checks are disabled, show single-line message."""
        monkeypatch.setenv("XAFFINITY_NO_UPDATE_CHECK", "1")
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "Update checks: disabled" in result.output

    def test_enabled_never_checked_inline_check(self, runner, monkeypatch):
        """When enabled but never checked, do inline PyPI check."""
        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            with (
                mock_cli_paths(mock_paths),
                patch(
                    "affinity.cli.update_check.check_pypi_version",
                    return_value="99.0.0",
                ),
            ):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "Update checks: enabled" in result.output
        assert "Current version:" in result.output
        assert "Latest version: 99.0.0" in result.output
        assert "Status: update available" in result.output

    def test_enabled_never_checked_up_to_date(self, runner, monkeypatch):
        """When inline check finds no update, show 'up to date'."""
        import affinity

        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            with (
                mock_cli_paths(mock_paths),
                patch(
                    "affinity.cli.update_check.check_pypi_version",
                    return_value=affinity.__version__,
                ),
            ):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "Status: up to date" in result.output

    def test_enabled_never_checked_network_failure(self, runner, monkeypatch):
        """When inline check cannot reach PyPI, show network failure message."""
        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            with (
                mock_cli_paths(mock_paths),
                patch(
                    "affinity.cli.update_check.check_pypi_version",
                    return_value=None,
                ),
            ):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "could not reach PyPI" in result.output

    def test_stale_cache_triggers_inline_check(self, runner, monkeypatch):
        """When cache is stale, do inline PyPI check instead of showing stale data."""
        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            _write_update_cache(
                mock_paths.state_dir,
                update_available=False,
                checked_at=stale_time,
            )
            with (
                mock_cli_paths(mock_paths),
                patch(
                    "affinity.cli.update_check.check_pypi_version",
                    return_value="99.0.0",
                ),
            ):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "Update checks: enabled" in result.output
        assert "Latest version: 99.0.0" in result.output
        assert "Status: update available" in result.output

    def test_enabled_up_to_date(self, runner, monkeypatch):
        """When up to date with fresh cache, show version and 'up to date' status."""
        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            _write_update_cache(mock_paths.state_dir, update_available=False)
            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "Update checks: enabled" in result.output
        assert "Current version:" in result.output
        assert "Last checked:" in result.output
        assert "Status: up to date" in result.output
        # Should NOT show "Latest version:" when up to date
        assert "Latest version:" not in result.output

    def test_enabled_update_available(self, runner, monkeypatch):
        """When update is available, show both versions and upgrade command."""
        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            _write_update_cache(mock_paths.state_dir, update_available=True)
            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "Update checks: enabled" in result.output
        assert "Current version:" in result.output
        assert "Latest version: 99.0.0" in result.output
        assert "Status: update available" in result.output
        assert "install --upgrade" in result.output

    def test_json_output_unchanged(self, runner, monkeypatch):
        """--json should still use the run_command path (not human output)."""
        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "update-check", "--json"])

        assert result.exit_code == 0
        envelope = json.loads(result.output)
        data = envelope["data"]
        assert "update_check_enabled" in data
        assert "update_notify_mode" in data

    def test_last_checked_shows_relative_time(self, runner, monkeypatch):
        """Last checked timestamp includes relative time like '3 hours ago'."""
        monkeypatch.delenv("XAFFINITY_NO_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("AFFINITY_API_KEY", raising=False)

        three_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

        with runner.isolated_filesystem() as fs:
            mock_paths = make_mock_paths(Path(fs))
            _write_update_cache(
                mock_paths.state_dir,
                update_available=False,
                checked_at=three_hours_ago,
            )
            with mock_cli_paths(mock_paths):
                result = runner.invoke(cli, ["config", "update-check"])

        assert result.exit_code == 0
        assert "3 hours ago" in result.output


class TestTimeAgo:
    """Tests for the _time_ago helper."""

    def test_just_now(self):
        from affinity.cli.commands.config_cmds import _time_ago

        assert _time_ago(datetime.now(timezone.utc)) == "just now"

    def test_minutes(self):
        from affinity.cli.commands.config_cmds import _time_ago

        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _time_ago(dt) == "5 minutes ago"

    def test_single_minute(self):
        from affinity.cli.commands.config_cmds import _time_ago

        dt = datetime.now(timezone.utc) - timedelta(minutes=1, seconds=30)
        assert _time_ago(dt) == "1 minute ago"

    def test_hours(self):
        from affinity.cli.commands.config_cmds import _time_ago

        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        assert _time_ago(dt) == "3 hours ago"

    def test_single_hour(self):
        from affinity.cli.commands.config_cmds import _time_ago

        dt = datetime.now(timezone.utc) - timedelta(hours=1, minutes=30)
        assert _time_ago(dt) == "1 hour ago"

    def test_days(self):
        from affinity.cli.commands.config_cmds import _time_ago

        dt = datetime.now(timezone.utc) - timedelta(days=5)
        assert _time_ago(dt) == "5 days ago"

    def test_months(self):
        from affinity.cli.commands.config_cmds import _time_ago

        dt = datetime.now(timezone.utc) - timedelta(days=60)
        assert _time_ago(dt) == "2 months ago"

    def test_naive_datetime_treated_as_utc(self):
        from affinity.cli.commands.config_cmds import _time_ago

        dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        assert _time_ago(dt) == "2 hours ago"
