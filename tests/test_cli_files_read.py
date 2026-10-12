"""Tests for files read CLI commands."""

from __future__ import annotations

import base64
import json

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

from affinity.cli.commands._entity_files_read import parse_size
from affinity.cli.errors import CLIError
from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)


# Sample file metadata returned by the V1 API
# Presigned URL for file download
PRESIGNED_URL = "https://userfiles.affinity.co/abc123?X-Amz-Expires=60&X-Amz-Signature=xyz"

# V2 file metadata (files.get)
SAMPLE_FILE_METADATA = {
    "id": 9192757,
    "name": "Pitch Deck 2025.pdf",
    "size": 5242880,  # 5MB
    "type": "application/pdf",
    "creator": {"id": 222321674},
    "createdAt": "2025-09-16T12:53:13Z",
    "updatedAt": None,
    "downloadUrl": PRESIGNED_URL,
}


class TestParseSize:
    """Tests for the parse_size function."""

    def test_parse_plain_integer(self) -> None:
        """parse_size handles plain integer strings."""
        assert parse_size("1048576") == 1048576
        assert parse_size("500") == 500
        assert parse_size("0") == 0

    def test_parse_kilobytes(self) -> None:
        """parse_size handles KB/K units."""
        assert parse_size("1KB") == 1024
        assert parse_size("1K") == 1024
        assert parse_size("500KB") == 500 * 1024
        assert parse_size("500K") == 500 * 1024

    def test_parse_megabytes(self) -> None:
        """parse_size handles MB/M units."""
        assert parse_size("1MB") == 1024 * 1024
        assert parse_size("1M") == 1024 * 1024
        assert parse_size("5MB") == 5 * 1024 * 1024
        assert parse_size("5M") == 5 * 1024 * 1024

    def test_parse_gigabytes(self) -> None:
        """parse_size handles GB/G units."""
        assert parse_size("1GB") == 1024 * 1024 * 1024
        assert parse_size("1G") == 1024 * 1024 * 1024
        assert parse_size("2GB") == 2 * 1024 * 1024 * 1024

    def test_parse_fractional(self) -> None:
        """parse_size handles fractional values."""
        assert parse_size("1.5MB") == int(1.5 * 1024 * 1024)
        assert parse_size("0.5GB") == int(0.5 * 1024 * 1024 * 1024)
        assert parse_size("2.5K") == int(2.5 * 1024)

    def test_parse_case_insensitive(self) -> None:
        """parse_size is case insensitive."""
        assert parse_size("1mb") == 1024 * 1024
        assert parse_size("1Mb") == 1024 * 1024
        assert parse_size("1kb") == 1024
        assert parse_size("1Kb") == 1024

    def test_parse_with_whitespace(self) -> None:
        """parse_size handles leading/trailing whitespace."""
        assert parse_size("  1MB  ") == 1024 * 1024
        assert parse_size(" 500KB ") == 500 * 1024

    def test_parse_invalid_format(self) -> None:
        """parse_size raises CLIError on invalid format."""
        with pytest.raises(CLIError, match="Invalid size format"):
            parse_size("invalid")

        with pytest.raises(CLIError, match="Invalid size format"):
            parse_size("1TB")  # TB not supported

        with pytest.raises(CLIError, match="Invalid size format"):
            parse_size("-1MB")  # Negative not supported

        with pytest.raises(CLIError, match="Invalid size format"):
            parse_size("MB")  # No number


class TestFilesReadCommand:
    """Tests for the files read CLI command."""

    def test_company_files_read_returns_content(self, respx_mock: respx.MockRouter) -> None:
        """company files read returns base64-encoded content with metadata."""
        file_content = b"Hello, World! This is test content."

        # Mock the file metadata endpoint
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        # Mock the download endpoint to return a redirect
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        # Mock the S3 presigned URL with Range header support
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=file_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "read", "12345", "--file-id", "9192757"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0, f"Output: {result.output}"
        payload = json.loads(result.output.strip())
        assert payload["ok"] is True
        assert payload["command"]["name"] == "company files read"
        assert payload["data"]["fileId"] == 9192757
        assert payload["data"]["name"] == "Pitch Deck 2025.pdf"
        assert payload["data"]["size"] == 5242880
        assert payload["data"]["contentType"] == "application/pdf"
        assert payload["data"]["encoding"] == "base64"
        assert payload["data"]["content"] == base64.b64encode(file_content).decode("ascii")

    def test_person_files_read_works(self, respx_mock: respx.MockRouter) -> None:
        """person files read command works."""
        file_content = b"Test content"
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=file_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "person", "files", "read", "67890", "--file-id", "9192757"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["command"]["name"] == "person files read"

    def test_opportunity_files_read_works(self, respx_mock: respx.MockRouter) -> None:
        """opportunity files read command works."""
        file_content = b"Test content"
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=file_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "opportunity", "files", "read", "98765", "--file-id", "9192757"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["command"]["name"] == "opportunity files read"

    def test_files_read_with_offset(self, respx_mock: respx.MockRouter) -> None:
        """files read with offset starts at correct position."""
        file_content = b"Content from offset"
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=file_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "read",
                "12345",
                "--file-id",
                "9192757",
                "--offset",
                "1048576",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["data"]["offset"] == 1048576

    def test_files_read_with_limit(self, respx_mock: respx.MockRouter) -> None:
        """files read with limit respects size limit."""
        file_content = b"Limited content"
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=file_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "read",
                "12345",
                "--file-id",
                "9192757",
                "--limit",
                "500KB",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        # Command context should show the parsed limit
        assert payload["command"]["modifiers"]["limit"] == 500 * 1024


class TestFilesReadChunking:
    """Tests for files read chunking behavior."""

    def test_has_more_true_when_content_remains(self, respx_mock: respx.MockRouter) -> None:
        """hasMore is true when more content is available."""
        # Create a 100-byte chunk from a 5MB file
        chunk_content = b"x" * 100
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=chunk_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "read",
                "12345",
                "--file-id",
                "9192757",
                "--limit",
                "100",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["data"]["hasMore"] is True
        assert payload["data"]["nextOffset"] == 100

    def test_has_more_false_at_end_of_file(self, respx_mock: respx.MockRouter) -> None:
        """hasMore is false at end of file, nextOffset is null."""
        # Small file that fits in one read
        small_file = {**SAMPLE_FILE_METADATA, "size": 100}
        chunk_content = b"x" * 100
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=small_file)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=chunk_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "read", "12345", "--file-id", "9192757"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["data"]["hasMore"] is False
        assert payload["data"]["nextOffset"] is None

    def test_next_offset_calculation(self, respx_mock: respx.MockRouter) -> None:
        """nextOffset is correctly calculated from offset + actual length."""
        chunk_content = b"x" * 500  # 500 bytes
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        respx_mock.get(PRESIGNED_URL).mock(return_value=Response(206, content=chunk_content))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "read",
                "12345",
                "--file-id",
                "9192757",
                "--offset",
                "1000",
                "--limit",
                "500",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["data"]["offset"] == 1000
        assert payload["data"]["length"] == 500
        assert payload["data"]["nextOffset"] == 1500  # 1000 + 500


class TestFilesReadErrors:
    """Tests for files read error handling."""

    def test_negative_offset_error(self, respx_mock: respx.MockRouter) -> None:
        """Negative offset raises error."""
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "read",
                "12345",
                "--file-id",
                "9192757",
                "--offset",
                "-100",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 2
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert "negative" in payload["error"]["message"].lower()

    def test_offset_exceeds_file_size_error(self, respx_mock: respx.MockRouter) -> None:
        """Offset >= file size raises error."""
        small_file = {**SAMPLE_FILE_METADATA, "size": 100}
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=small_file)
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "read",
                "12345",
                "--file-id",
                "9192757",
                "--offset",
                "100",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 2
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert "exceeds" in payload["error"]["message"].lower()

    def test_file_not_found_error(self, respx_mock: respx.MockRouter) -> None:
        """File not found raises error."""
        respx_mock.get("https://api.affinity.co/entity-files/9999999").mock(
            return_value=Response(
                404, json={"code": "NOT_FOUND", "message": "Entity file not found"}
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "read", "12345", "--file-id", "9999999"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False

    def test_invalid_limit_format_error(self) -> None:
        """Invalid limit format raises error."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "company",
                "files",
                "read",
                "12345",
                "--file-id",
                "9192757",
                "--limit",
                "invalid",
            ],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code != 0
        # The error is raised before the runner context, so check exception
        assert result.exception is not None
        assert "invalid size format" in str(result.exception).lower()

    def test_s3_range_not_satisfiable_error(self, respx_mock: respx.MockRouter) -> None:
        """S3 416 Range Not Satisfiable is handled gracefully."""
        respx_mock.get("https://api.affinity.co/v2/files/9192757").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/9192757").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )
        # S3 returns 416 when range is unsatisfiable
        respx_mock.get(PRESIGNED_URL).mock(
            return_value=Response(416, content=b"Range Not Satisfiable")
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "company", "files", "read", "12345", "--file-id", "9192757"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 1
        payload = json.loads(result.output.strip())
        assert payload["ok"] is False
        assert "range" in payload["error"]["message"].lower()
