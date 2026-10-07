"""Uploads expose the created file records (V1 `POST /entity-files` -> `entity_files`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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

from affinity import Affinity, AsyncAffinity
from affinity.cli.main import cli
from affinity.models.secondary import EntityFile
from affinity.types import CompanyId, FileId, OpportunityId, PersonId

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

UPLOAD_URL = "https://api.affinity.co/entity-files"


def _file_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": 9192757,
        "name": "notes.txt",
        "size": 5,
        "person_id": None,
        "organization_id": 306016520,
        "opportunity_id": None,
        "uploader_id": 222321674,
        "created_at": "2026-10-08T12:53:13.339Z",
    }
    item.update(overrides)
    return item


NEW_SHAPE = {"success": True, "entity_files": [_file_item()]}
OLD_SHAPE = {"success": True}


# =============================================================================
# EntityFile.company_id
# =============================================================================


class TestEntityFileCompanyId:
    def test_company_id_from_v1_snake_case(self) -> None:
        f = EntityFile.model_validate(_file_item())
        assert f.company_id == CompanyId(306016520)

    def test_company_id_from_camel_case(self) -> None:
        payload = {
            "id": 1,
            "name": "a.txt",
            "size": 1,
            "organizationId": 7,
            "uploaderId": 2,
            "createdAt": "2026-10-08T12:53:13Z",
        }
        assert EntityFile.model_validate(payload).company_id == CompanyId(7)

    def test_company_id_serializes_as_organization_id(self) -> None:
        dumped = EntityFile.model_validate(_file_item()).model_dump(by_alias=True)
        assert dumped["organizationId"] == 306016520
        assert "organization_id" not in dumped


# =============================================================================
# SDK (sync)
# =============================================================================


class TestUploadReturningFilesSync:
    def test_upload_bytes_returns_created_files(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        with Affinity(api_key="k", max_retries=0) as client:
            files = client.files.upload_bytes_returning_files(
                b"hello", "notes.txt", company_id=CompanyId(306016520)
            )
        assert len(files) == 1
        f = files[0]
        assert isinstance(f, EntityFile)
        assert f.id == FileId(9192757)
        assert f.company_id == CompanyId(306016520)
        assert isinstance(f.created_at, datetime)

    def test_upload_path_returns_created_files_and_reports_progress(
        self, respx_mock: respx.MockRouter, tmp_path: Path
    ) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        p = tmp_path / "notes.txt"
        p.write_text("hello", encoding="utf-8")
        progress: list[tuple[int, int | None, str]] = []
        with Affinity(api_key="k", max_retries=0) as client:
            files = client.files.upload_path_returning_files(
                p,
                person_id=PersonId(1),
                on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
            )
        assert [int(f.id) for f in files] == [9192757]
        assert progress == [(0, 5, "upload"), (5, 5, "upload")]

    def test_upload_returns_created_files(self, respx_mock: respx.MockRouter) -> None:
        route = respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        with Affinity(api_key="k", max_retries=0) as client:
            files = client.files.upload_returning_files(
                files={"file": ("notes.txt", b"hello", "text/plain")},
                opportunity_id=OpportunityId(3),
            )
        assert [int(f.id) for f in files] == [9192757]
        assert b"opportunity_id" in route.calls.last.request.content

    def test_old_success_only_response_returns_empty_list(
        self, respx_mock: respx.MockRouter
    ) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=OLD_SHAPE))
        with Affinity(api_key="k", max_retries=0) as client:
            assert (
                client.files.upload_bytes_returning_files(b"x", "a.txt", person_id=PersonId(1))
                == []
            )

    def test_bool_methods_unchanged_with_new_shape(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        with Affinity(api_key="k", max_retries=0) as client:
            assert client.files.upload_bytes(b"x", "a.txt", person_id=PersonId(1)) is True
            assert (
                client.files.upload(
                    files={"file": ("a.txt", b"x", "text/plain")}, person_id=PersonId(1)
                )
                is True
            )

    def test_bool_method_still_reports_success_false(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json={"success": False}))
        with Affinity(api_key="k", max_retries=0) as client:
            assert client.files.upload_bytes(b"x", "a.txt", person_id=PersonId(1)) is False

    def test_lenient_parse_never_raises(self, respx_mock: respx.MockRouter) -> None:
        payload = {
            "success": True,
            "entity_files": [
                # Missing uploader_id/created_at: fails validation, id preserved.
                {"id": "11", "name": "partial.txt", "size": 3, "organization_id": 5},
                # Not a dict: skipped.
                "garbage",
                # No usable id: skipped.
                {"name": "no-id.txt"},
                {"id": "not-a-number"},
                # Valid.
                _file_item(id=12),
                # Bad size only: other fields still validated (created_at -> UTC).
                _file_item(id=13, size="big", created_at="2026-10-08T12:00:00+02:00"),
            ],
        }
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=payload))
        with Affinity(api_key="k", max_retries=0) as client:
            files = client.files.upload_bytes_returning_files(
                b"x", "a.txt", company_id=CompanyId(5)
            )
        assert [int(f.id) for f in files] == [11, 12, 13]
        bad_size = files[2]
        assert bad_size.size is None
        assert bad_size.uploader_id == 222321674
        assert bad_size.created_at == datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
        assert bad_size.created_at.tzinfo is not None
        partial = files[0]
        assert partial.name == "partial.txt"
        assert partial.size == 3
        assert partial.company_id == 5
        assert partial.uploader_id is None
        assert partial.created_at is None
        assert partial.content_type is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"success": True, "entity_files": "nope"},
            {"success": True, "entity_files": None},
            {"entity_files": {"id": 1}},
            {},
        ],
    )
    def test_unexpected_entity_files_shape_returns_empty(
        self, respx_mock: respx.MockRouter, payload: dict[str, Any]
    ) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=payload))
        with Affinity(api_key="k", max_retries=0) as client:
            assert (
                client.files.upload_bytes_returning_files(b"x", "a.txt", person_id=PersonId(1))
                == []
            )

    def test_requires_exactly_one_target(self) -> None:
        with Affinity(api_key="k", max_retries=0) as client, pytest.raises(ValueError):
            client.files.upload_bytes_returning_files(b"x", "a.txt")


# =============================================================================
# SDK (async)
# =============================================================================


class TestUploadReturningFilesAsync:
    async def test_upload_bytes_returns_created_files(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        async with AsyncAffinity(api_key="k", max_retries=0) as client:
            files = await client.files.upload_bytes_returning_files(
                b"hello", "notes.txt", company_id=CompanyId(306016520)
            )
        assert [int(f.id) for f in files] == [9192757]
        assert files[0].company_id == CompanyId(306016520)

    async def test_upload_path_returns_created_files(
        self, respx_mock: respx.MockRouter, tmp_path: Path
    ) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        p = tmp_path / "notes.txt"
        p.write_text("hello", encoding="utf-8")
        progress: list[tuple[int, int | None, str]] = []
        async with AsyncAffinity(api_key="k", max_retries=0) as client:
            files = await client.files.upload_path_returning_files(
                p,
                person_id=PersonId(1),
                on_progress=lambda done, total, *, phase: progress.append((done, total, phase)),
            )
        assert [int(f.id) for f in files] == [9192757]
        assert progress == [(0, 5, "upload"), (5, 5, "upload")]

    async def test_upload_returns_created_files(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        async with AsyncAffinity(api_key="k", max_retries=0) as client:
            files = await client.files.upload_returning_files(
                files={"file": ("notes.txt", b"hello", "text/plain")},
                person_id=PersonId(1),
            )
        assert [int(f.id) for f in files] == [9192757]

    async def test_old_shape_and_lenient(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(UPLOAD_URL).mock(
            side_effect=[
                Response(200, json=OLD_SHAPE),
                Response(200, json={"entity_files": [{"id": 4}, 1, {"x": 1}]}),
            ]
        )
        async with AsyncAffinity(api_key="k", max_retries=0) as client:
            assert (
                await client.files.upload_bytes_returning_files(
                    b"x", "a.txt", person_id=PersonId(1)
                )
                == []
            )
            files = await client.files.upload_bytes_returning_files(
                b"x", "a.txt", person_id=PersonId(1)
            )
        assert [int(f.id) for f in files] == [4]

    async def test_bool_methods_unchanged(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        async with AsyncAffinity(api_key="k", max_retries=0) as client:
            assert await client.files.upload_bytes(b"x", "a.txt", person_id=PersonId(1)) is True


# =============================================================================
# CLI
# =============================================================================


@pytest.mark.parametrize(
    ("entity", "entity_id", "form_field"),
    [
        ("company", "306016520", "organization_id"),
        ("person", "42", "person_id"),
        ("opportunity", "77", "opportunity_id"),
    ],
)
class TestCliFilesUpload:
    def _invoke(self, tmp_path: Path, entity: str, entity_id: str) -> dict[str, Any]:
        p = tmp_path / "notes.txt"
        p.write_text("hello", encoding="utf-8")
        result = CliRunner().invoke(
            cli,
            ["--json", entity, "files", "upload", entity_id, "--file", str(p)],
            env={"AFFINITY_API_KEY": "test-key"},
        )
        assert result.exit_code == 0, result.output
        # Progress events may precede the result envelope; it is the last line.
        payload: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
        return payload

    def test_rows_include_file_id(
        self,
        respx_mock: respx.MockRouter,
        tmp_path: Path,
        entity: str,
        entity_id: str,
        form_field: str,
    ) -> None:
        route = respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=NEW_SHAPE))
        payload = self._invoke(tmp_path, entity, entity_id)
        assert form_field.encode() in route.calls.last.request.content
        (row,) = payload["data"]["uploads"]
        assert row["fileId"] == 9192757
        assert row["createdAt"].startswith("2026-10-08T12:53:13")
        assert row["success"] is True
        assert row["filename"] == "notes.txt"
        assert row["size"] == 5

    def test_rows_have_null_file_id_on_old_response(
        self,
        respx_mock: respx.MockRouter,
        tmp_path: Path,
        entity: str,
        entity_id: str,
        form_field: str,
    ) -> None:
        route = respx_mock.post(UPLOAD_URL).mock(return_value=Response(200, json=OLD_SHAPE))
        payload = self._invoke(tmp_path, entity, entity_id)
        assert form_field.encode() in route.calls.last.request.content
        (row,) = payload["data"]["uploads"]
        assert row["fileId"] is None
        assert row["createdAt"] is None
        assert row["success"] is True
