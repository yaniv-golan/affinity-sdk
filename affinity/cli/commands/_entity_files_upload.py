"""Shared helpers for `<entity> files upload` commands."""

from __future__ import annotations

from datetime import datetime

from affinity.models.secondary import EntityFile


def uploaded_file_ref(created: list[EntityFile]) -> dict[str, object]:
    """
    `fileId`/`createdAt` for one upload result row.

    Both are None when the API returned no created-file record. `createdAt` is
    also None when the record came back without a usable timestamp (the SDK
    returns such entries as partial records rather than failing the upload).
    """
    if not created:
        return {"fileId": None, "createdAt": None}
    first = created[0]
    created_at = first.created_at
    return {
        "fileId": int(first.id),
        "createdAt": created_at.isoformat() if isinstance(created_at, datetime) else None,
    }
