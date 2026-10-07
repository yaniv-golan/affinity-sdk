from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from affinity.cli.commands._entity_files_dump import dump_entity_files_bundle
from affinity.models.secondary import EntityFile
from affinity.policies import Policies


@dataclass(frozen=True)
class _FakeFilesListResponse:
    data: list[EntityFile]
    next_page_token: str | None = None
    next_cursor: str | None = None


class _FakeFilesService:
    def __init__(self, files: list[EntityFile]) -> None:
        self._files = files
        self.download_to_calls: list[tuple[int, Path]] = []

    async def list(self, **_: object) -> _FakeFilesListResponse:
        return _FakeFilesListResponse(data=self._files, next_page_token=None)

    async def download_to(self, file_id: object, dest: object, **_: object) -> None:
        self.download_to_calls.append((int(file_id), Path(dest)))


class _FakeRateLimits:
    def snapshot(self) -> None:
        return None


class _FakeAsyncAffinity:
    def __init__(self, *_: object, **__: object) -> None:
        raise RuntimeError("tests should monkeypatch _FakeAsyncAffinity using with_files(...)")

    @classmethod
    def with_files(cls, files: list[EntityFile]) -> type[_FakeAsyncAffinity]:
        fake_files = _FakeFilesService(files)

        class _Instance(_FakeAsyncAffinity):
            files = fake_files
            rate_limits = _FakeRateLimits()

            def __init__(self, *_: object, **__: object) -> None:
                return

            async def __aenter__(self) -> _Instance:
                return self

            async def __aexit__(self, *_: object) -> None:
                return

        return _Instance


def test_dump_entity_files_bundle_skips_existing_files(monkeypatch: object, tmp_path: Path) -> None:
    existing = tmp_path / "bundle" / "files" / "a.txt"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"abc")

    files = [
        EntityFile.model_validate(
            {
                "id": 1,
                "name": "a.txt",
                "size": 3,
                "contentType": "text/plain",
                "uploaderId": 1,
                "createdAt": "2020-01-01T00:00:00Z",
            }
        )
    ]

    monkeypatch.setattr(
        "affinity.cli.commands._entity_files_dump.AsyncAffinity",
        _FakeAsyncAffinity.with_files(files),
    )

    ctx = SimpleNamespace(
        progress="never",
        quiet=True,
        resolve_client_settings=lambda **_: SimpleNamespace(
            api_key="x",
            v1_base_url="http://v1",
            v2_base_url="http://v2",
            timeout=1.0,
            log_requests=False,
            max_retries=0,
            on_request=None,
            on_response=None,
            on_error=None,
            policies=Policies(),
            affinity_api_version=None,
        ),
    )

    warnings: list[str] = []
    out = asyncio.run(
        dump_entity_files_bundle(
            ctx=ctx,
            warnings=warnings,
            out_dir=str(tmp_path / "bundle"),
            overwrite=False,
            concurrency=1,
            page_size=200,
            max_files=None,
            default_dirname="unused",
            manifest_entity={"type": "company", "companyId": 1},
            files_list_kwargs={"company_id": 1},
        )
    )

    assert out.data == {
        "out": str(tmp_path / "bundle"),
        "filesDownloaded": 0,
        "filesSkippedExisting": 1,
        "filesTotal": 1,
        "manifest": "manifest.json",
    }
    assert out.warnings and "Skipped 1 existing file(s)" in out.warnings[0]

    manifest_path = tmp_path / "bundle" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["files"][0]["path"] == "files/a.txt"
