"""
Shared implementation for `files read` commands.

This module provides file content reading with chunking support for
company, person, and opportunity files commands.
"""

from __future__ import annotations

import base64
import re

import httpx

from affinity.types import FileId

from ..context import CLIContext
from ..errors import CLIError
from ..results import CommandContext
from ..runner import CommandOutput, run_command


def parse_size(size_str: str) -> int:
    """Parse a size string like '1MB', '500KB', or '1048576' into bytes.

    Args:
        size_str: Size string (e.g., '1MB', '500KB', '1048576', '1M', '500K')

    Returns:
        Size in bytes

    Raises:
        CLIError: If the size string is invalid
    """
    size_str = size_str.strip().upper()

    # Try parsing as plain integer first
    if size_str.isdigit():
        return int(size_str)

    # Match patterns like "1MB", "500KB", "1M", "500K", "1.5MB"
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(K|KB|M|MB|G|GB)?$", size_str)
    if not match:
        raise CLIError(
            f"Invalid size format: '{size_str}'",
            exit_code=2,
            error_type="usage_error",
            hint="Use formats like '1MB', '500KB', '1048576', or '1.5M'",
        )

    value = float(match.group(1))
    unit = match.group(2) or ""

    multipliers = {
        "": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024 * 1024,
        "MB": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
    }

    return int(value * multipliers[unit])


def read_file_content(
    *,
    ctx: CLIContext,
    entity_type: str,
    entity_id: int,
    file_id: int,
    offset: int,
    limit: int,
) -> None:
    """Read file content with chunking support.

    Args:
        ctx: CLI context
        entity_type: Entity type (company, person, opportunity)
        entity_id: Entity ID (for command context only)
        file_id: File ID to read
        offset: Byte offset to start reading
        limit: Maximum bytes to read
    """

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)

        # Name, size, type and a signed URL: one V2 request (or V1's two where V2 can't answer)
        presigned = client.files.get_download_url(FileId(file_id))
        file_size = presigned.size or 0

        # Validate offset
        if offset < 0:
            raise CLIError(
                f"Offset cannot be negative: {offset}",
                exit_code=2,
                error_type="usage_error",
            )

        if offset >= file_size > 0:
            raise CLIError(
                f"Offset {offset} exceeds file size {file_size}",
                exit_code=2,
                error_type="usage_error",
                hint=f"File size is {file_size} bytes. Use --offset 0 to start from beginning.",
            )

        # Calculate actual bytes to read
        remaining = file_size - offset
        bytes_to_read = min(limit, remaining)
        end_byte = offset + bytes_to_read - 1

        # Fetch with Range header
        headers = {"Range": f"bytes={offset}-{end_byte}"}

        try:
            # Use a fresh httpx client for the S3 request (no auth needed)
            with httpx.Client(timeout=60.0) as http_client:
                response = http_client.get(presigned.url, headers=headers)
                response.raise_for_status()
                content_bytes = response.content
        except httpx.HTTPStatusError as e:
            # S3 returns 416 if range is unsatisfiable
            if e.response.status_code == 416:
                raise CLIError(
                    f"Range not satisfiable: bytes={offset}-{end_byte}",
                    exit_code=1,
                    error_type="api_error",
                    hint=f"File size is {file_size} bytes.",
                ) from None
            raise CLIError(
                f"Failed to fetch file content: {e}",
                exit_code=1,
                error_type="api_error",
            ) from None
        except httpx.RequestError as e:
            raise CLIError(
                f"Network error fetching file: {e}",
                exit_code=1,
                error_type="network_error",
            ) from None

        # Encode content as base64
        content_b64 = base64.b64encode(content_bytes).decode("ascii")

        # Calculate hasMore and nextOffset
        actual_length = len(content_bytes)
        next_offset = offset + actual_length
        has_more = next_offset < file_size

        cmd_context = CommandContext(
            name=f"{entity_type} files read",
            inputs={f"{entity_type}Id": entity_id, "fileId": file_id},
            modifiers={"offset": offset, "limit": limit} if offset > 0 else {"limit": limit},
        )

        data = {
            "fileId": int(file_id),
            "name": presigned.name,
            "size": file_size,
            "contentType": presigned.content_type,
            "offset": offset,
            "length": actual_length,
            "hasMore": has_more,
            "nextOffset": next_offset if has_more else None,
            "encoding": "base64",
            "content": content_b64,
        }

        return CommandOutput(
            data=data,
            context=cmd_context,
            warnings=warnings,
            api_called=True,
            rate_limit=client.rate_limits.snapshot(),
        )

    run_command(ctx, command=f"{entity_type} files read", fn=fn)
