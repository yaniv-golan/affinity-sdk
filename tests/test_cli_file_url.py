"""Tests for file-url CLI command and get_download_url SDK method."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
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
from affinity.clients.http import ClientConfig, HTTPClient
from affinity.services.v1_only import EntityFileService, PresignedUrl
from affinity.types import FileId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)


# Sample file metadata returned by the V1 API
SAMPLE_FILE_METADATA = {
    "id": 7777777,
    "name": "Pitch Deck 2025.pdf",
    "size": 5826929,
    "content_type": "application/pdf",
    "person_id": None,
    "organization_id": 555555555,
    "opportunity_id": None,
    "uploader_id": 11111114,
    "created_at": "2025-09-16T12:53:13.339Z",
}

# Presigned URL with typical AWS S3 parameters
PRESIGNED_URL = "https://userfiles.affinity.co/abc123?X-Amz-Expires=60&X-Amz-Signature=xyz"


class TestFileUrlCLICommand:
    """Tests for the file-url CLI command."""

    def test_file_url_returns_presigned_url(self, respx_mock: respx.MockRouter) -> None:
        """file-url command returns presigned URL with metadata."""
        # V2 can't answer (404), so the V1 metadata + redirect path is used
        respx_mock.get("https://api.affinity.co/v2/files/7777777").mock(
            return_value=Response(404, json={"errors": []})
        )
        # Mock the file metadata endpoint
        respx_mock.get("https://api.affinity.co/entity-files/7777777").mock(
            return_value=Response(200, json=SAMPLE_FILE_METADATA)
        )
        # Mock the download endpoint to return a redirect
        respx_mock.get("https://api.affinity.co/entity-files/download/7777777").mock(
            return_value=Response(
                307,
                headers={"Location": PRESIGNED_URL},
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "file-url", "7777777"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["ok"] is True
        assert payload["command"]["name"] == "file-url"
        assert payload["command"]["inputs"]["fileId"] == 7777777
        assert payload["data"]["fileId"] == 7777777
        assert payload["data"]["name"] == "Pitch Deck 2025.pdf"
        assert payload["data"]["size"] == 5826929
        assert payload["data"]["contentType"] == "application/pdf"
        assert payload["data"]["url"] == PRESIGNED_URL
        assert payload["data"]["expiresIn"] == 60
        assert "expiresAt" in payload["data"]
        # Should have a warning about expiration
        assert any("expires" in w.lower() for w in payload.get("warnings", []))

    def test_file_url_with_null_content_type(self, respx_mock: respx.MockRouter) -> None:
        """file-url handles files with null content type."""
        file_meta = {**SAMPLE_FILE_METADATA, "content_type": None}
        respx_mock.get("https://api.affinity.co/v2/files/7777777").mock(
            return_value=Response(404, json={"errors": []})
        )
        respx_mock.get("https://api.affinity.co/entity-files/7777777").mock(
            return_value=Response(200, json=file_meta)
        )
        respx_mock.get("https://api.affinity.co/entity-files/download/7777777").mock(
            return_value=Response(307, headers={"Location": PRESIGNED_URL})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "file-url", "7777777"],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0
        payload = json.loads(result.output.strip())
        assert payload["data"]["contentType"] is None


class TestGetDownloadUrlSDK:
    """Tests for the get_download_url SDK method."""

    def test_get_download_url_returns_presigned_url(self) -> None:
        """get_download_url returns PresignedUrl with all fields."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            url_str = str(request.url)

            # File metadata request
            if "/entity-files/7777777" in url_str and "download" not in url_str:
                return httpx.Response(200, json=SAMPLE_FILE_METADATA, request=request)

            # Download redirect request
            if "/entity-files/download/7777777" in url_str:
                return httpx.Response(
                    307,
                    headers={"Location": PRESIGNED_URL},
                    request=request,
                )

            return httpx.Response(404, json={"message": "not found"}, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test-key",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        try:
            files = EntityFileService(http)
            result = files.get_download_url(FileId(7777777))

            assert isinstance(result, PresignedUrl)
            assert result.url == PRESIGNED_URL
            assert result.file_id == 7777777
            assert result.name == "Pitch Deck 2025.pdf"
            assert result.size == 5826929
            assert result.content_type == "application/pdf"
            assert result.expires_in == 60
            assert isinstance(result.expires_at, datetime)
            # Expiration should be roughly 60 seconds from now
            now = datetime.now(timezone.utc)
            assert result.expires_at > now
            assert result.expires_at < now + timedelta(seconds=120)
        finally:
            http.close()

    def test_get_download_url_parses_custom_expiry(self) -> None:
        """get_download_url correctly parses X-Amz-Expires from URL."""
        custom_url = "https://userfiles.affinity.co/abc?X-Amz-Expires=300&X-Amz-Signature=xyz"

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/entity-files/7777777" in url_str and "download" not in url_str:
                return httpx.Response(200, json=SAMPLE_FILE_METADATA, request=request)
            if "/entity-files/download/7777777" in url_str:
                return httpx.Response(307, headers={"Location": custom_url}, request=request)
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test-key",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        try:
            files = EntityFileService(http)
            result = files.get_download_url(FileId(7777777))
            assert result.expires_in == 300
        finally:
            http.close()

    def test_get_download_url_defaults_expiry_when_missing(self) -> None:
        """get_download_url defaults to 60s when X-Amz-Expires is missing."""
        url_without_expiry = "https://userfiles.affinity.co/abc?X-Amz-Signature=xyz"

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/entity-files/7777777" in url_str and "download" not in url_str:
                return httpx.Response(200, json=SAMPLE_FILE_METADATA, request=request)
            if "/entity-files/download/7777777" in url_str:
                return httpx.Response(
                    307, headers={"Location": url_without_expiry}, request=request
                )
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test-key",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        try:
            files = EntityFileService(http)
            result = files.get_download_url(FileId(7777777))
            assert result.expires_in == 60  # Default
        finally:
            http.close()

    def test_get_download_url_raises_on_no_redirect(self) -> None:
        """get_download_url raises AffinityError when no redirect is returned."""
        from affinity.exceptions import AffinityError

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/entity-files/7777777" in url_str and "download" not in url_str:
                return httpx.Response(200, json=SAMPLE_FILE_METADATA, request=request)
            if "/entity-files/download/7777777" in url_str:
                # Return 200 instead of redirect
                return httpx.Response(200, content=b"file content", request=request)
            return httpx.Response(404, request=request)

        http = HTTPClient(
            ClientConfig(
                api_key="test-key",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                transport=httpx.MockTransport(handler),
            )
        )
        try:
            files = EntityFileService(http)
            with pytest.raises(AffinityError, match="no redirect returned"):
                files.get_download_url(FileId(7777777))
        finally:
            http.close()


class TestGetDownloadUrlAsync:
    """Tests for the async get_download_url SDK method."""

    @pytest.mark.asyncio
    async def test_async_get_download_url_returns_presigned_url(self) -> None:
        """Async get_download_url returns PresignedUrl with all fields."""
        from affinity.clients.http import AsyncHTTPClient
        from affinity.services.v1_only import AsyncEntityFileService

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/entity-files/7777777" in url_str and "download" not in url_str:
                return httpx.Response(200, json=SAMPLE_FILE_METADATA, request=request)
            if "/entity-files/download/7777777" in url_str:
                return httpx.Response(307, headers={"Location": PRESIGNED_URL}, request=request)
            return httpx.Response(404, request=request)

        http = AsyncHTTPClient(
            ClientConfig(
                api_key="test-key",
                v1_base_url="https://v1.example",
                v2_base_url="https://v2.example/v2",
                max_retries=0,
                async_transport=httpx.MockTransport(handler),
            )
        )
        try:
            files = AsyncEntityFileService(http)
            result = await files.get_download_url(FileId(7777777))

            assert isinstance(result, PresignedUrl)
            assert result.url == PRESIGNED_URL
            assert result.file_id == 7777777
            assert result.name == "Pitch Deck 2025.pdf"
            assert result.size == 5826929
            assert result.content_type == "application/pdf"
            assert result.expires_in == 60
        finally:
            await http.close()
