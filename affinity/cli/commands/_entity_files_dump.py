from __future__ import annotations

import asyncio
import json
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict

from affinity import AsyncAffinity
from affinity.models.rate_limit_snapshot import RateLimitSnapshot
from affinity.models.secondary import EntityFile
from affinity.types import FileId

from ..context import CLIContext
from ..csv_utils import sanitize_filename
from ..errors import CLIError
from ..progress import ProgressManager, ProgressSettings
from ..results import CommandContext
from ..runner import CommandOutput, run_command


def download_single_file(
    *,
    ctx: CLIContext,
    entity_type: str,
    entity_id: int,
    file_id: int,
    out_path: str | None,
    overwrite: bool,
) -> None:
    """Download a single file by ID.

    Args:
        ctx: CLI context
        entity_type: Entity type (company, person, opportunity)
        entity_id: Entity ID
        file_id: File ID to download
        out_path: Output path (file path)
        overwrite: Whether to overwrite existing files
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)

        # Get file metadata first to know the filename
        file_meta = client.files.get(FileId(file_id))
        filename = file_meta.name

        # Determine output path
        dest = Path(out_path) if out_path else Path.cwd() / filename

        # Check if file exists
        if dest.exists() and not overwrite:
            raise CLIError(
                f"File already exists: {dest}. Use --overwrite to replace.",
                error_type="usage_error",
            )

        # Download the file
        dest.parent.mkdir(parents=True, exist_ok=True)
        client.files.download_to(FileId(file_id), dest, overwrite=overwrite)

        cmd_context = CommandContext(
            name=f"{entity_type} files download",
            inputs={f"{entity_type}Id": entity_id, "fileId": file_id},
            modifiers={"out": str(dest)} if out_path else {},
        )

        return CommandOutput(
            data={
                "fileId": int(file_id),
                "name": filename,
                "size": file_meta.size,
                "out": str(dest),
            },
            context=cmd_context,
            warnings=warnings,
            api_called=True,
            rate_limit=client.rate_limits.snapshot(),
        )

    run_command(ctx, command=f"{entity_type} files download", fn=fn)


class ManifestFile(TypedDict):
    fileId: int
    name: str
    contentType: str | None
    size: int
    createdAt: str
    uploaderId: int
    path: str


async def dump_entity_files_bundle(
    *,
    ctx: CLIContext,
    warnings: list[str],
    out_dir: str | None,
    overwrite: bool,
    concurrency: int,
    page_size: int,
    max_files: int | None,
    default_dirname: str,
    manifest_entity: dict[str, Any],
    files_list_kwargs: dict[str, Any],
    context: CommandContext | None = None,
) -> CommandOutput:
    """
    Download all files for a single entity into a folder bundle with a manifest.

    Notes:
    - Uses a bounded worker pool (avoids spawning one task per file).
    - Uses the same resolved client settings as sync commands (env/profile/flags).
    """
    settings = ctx.resolve_client_settings(warnings=warnings)

    entity_dir = Path(out_dir) if out_dir is not None else (Path.cwd() / default_dirname)
    files_dir = entity_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    workers = max(1, int(concurrency))
    queue: asyncio.Queue[EntityFile | None] = asyncio.Queue(maxsize=workers * 2)

    manifest_files: list[ManifestFile] = []
    rate_limit_snapshot: RateLimitSnapshot | None = None
    task_lock = asyncio.Lock()
    skipped_existing = 0
    downloaded = 0
    used_filenames: set[str] = set()

    async with AsyncAffinity(
        api_key=settings.api_key,
        v1_base_url=settings.v1_base_url,
        v2_base_url=settings.v2_base_url,
        timeout=settings.timeout,
        log_requests=settings.log_requests,
        max_retries=settings.max_retries,
        on_request=settings.on_request,
        on_response=settings.on_response,
        on_error=settings.on_error,
        policies=settings.policies,
        affinity_api_version=settings.affinity_api_version,
    ) as async_client:

        async def producer() -> None:
            token: str | None = None
            produced = 0
            while True:
                resp = await async_client.files.list(
                    **files_list_kwargs,
                    page_size=page_size,
                    page_token=token,
                )
                for f in resp.data:
                    await queue.put(f)
                    produced += 1
                    if max_files is not None and produced >= max_files:
                        token = None
                        break
                if max_files is not None and produced >= max_files:
                    break
                if not resp.next_cursor:
                    break
                token = resp.next_cursor

            for _ in range(workers):
                await queue.put(None)

        with ProgressManager(settings=ProgressSettings(mode=ctx.progress, quiet=ctx.quiet)) as pm:

            async def worker() -> None:
                nonlocal skipped_existing
                nonlocal downloaded
                while True:
                    f = await queue.get()
                    if f is None:
                        return

                    def choose_filename(name: str, file_id: int) -> str:
                        candidate = sanitize_filename(name) or str(file_id)
                        if candidate not in used_filenames:
                            return candidate

                        base = PurePosixPath(candidate)
                        stem = base.stem or "file"
                        suffix = base.suffix
                        disambiguated = f"{stem}__{file_id}{suffix}"
                        if disambiguated not in used_filenames:
                            return disambiguated

                        i = 2
                        while True:
                            alt = f"{stem}__{file_id}__{i}{suffix}"
                            if alt not in used_filenames:
                                return alt
                            i += 1

                    filename = choose_filename(f.name, int(f.id))
                    used_filenames.add(filename)
                    dest = files_dir / filename
                    if dest.exists() and not overwrite:
                        existing_size = dest.stat().st_size
                        if f.size and existing_size != int(f.size):
                            raise CLIError(
                                (
                                    "Refusing to skip existing file with size mismatch: "
                                    f"{dest} (expected {int(f.size)} bytes, got {existing_size}); "
                                    "use --overwrite to re-download."
                                ),
                                error_type="usage_error",
                            )
                        skipped_existing += 1
                        manifest_files.append(
                            {
                                "fileId": int(f.id),
                                "name": f.name,
                                "contentType": f.content_type,
                                "size": f.size,
                                "createdAt": f.created_at.isoformat(),
                                "uploaderId": int(f.uploader_id),
                                "path": str(dest.relative_to(entity_dir)),
                            }
                        )
                        continue
                    async with task_lock:
                        _task_id, cb = pm.task(
                            description=f"download {f.name}",
                            total_bytes=int(f.size) if f.size else None,
                        )
                    await async_client.files.download_to(
                        f.id,
                        dest,
                        overwrite=overwrite,
                        on_progress=cb,
                        timeout=settings.timeout,
                    )
                    downloaded += 1
                    manifest_files.append(
                        {
                            "fileId": int(f.id),
                            "name": f.name,
                            "contentType": f.content_type,
                            "size": f.size,
                            "createdAt": f.created_at.isoformat(),
                            "uploaderId": int(f.uploader_id),
                            "path": str(dest.relative_to(entity_dir)),
                        }
                    )

            await asyncio.gather(
                producer(),
                *(worker() for _ in range(workers)),
            )

        if skipped_existing and not overwrite:
            warnings.append(
                f"Skipped {skipped_existing} existing file(s); use --overwrite to re-download."
            )

        manifest = {
            "entity": manifest_entity,
            "files": sorted(manifest_files, key=lambda x: x["fileId"]),
        }
        (entity_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        rate_limit_snapshot = async_client.rate_limits.snapshot()

    data = {
        "out": str(entity_dir),
        "filesDownloaded": downloaded,
        "filesSkippedExisting": skipped_existing,
        "filesTotal": len(manifest_files),
        "manifest": str((entity_dir / "manifest.json").relative_to(entity_dir)),
    }
    return CommandOutput(
        data=data,
        context=context,
        warnings=warnings,
        api_called=True,
        rate_limit=rate_limit_snapshot,
    )
