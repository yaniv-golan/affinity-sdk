"""Cursor-based pagination for resumable queries.

Provides cursor encoding/decoding, validation, and cache management for
resuming truncated query results. This module is CLI-only and NOT part
of the public SDK API.

Two resumption modes:
- Streaming: Uses Affinity API's cursor for O(1) resumption (simple queries)
- Full-fetch: Uses disk cache for queries requiring sorting/aggregation
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from .models import Query

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Cursor format version (bump if cursor structure changes)
CURSOR_VERSION = 1

# Cursor TTL in seconds (1 hour)
CURSOR_TTL_SECONDS = 3600

# Cache directory name (inside system temp)
CACHE_DIR_NAME = "xaffinity_cache"

# Cache limits
MAX_CACHE_FILE_BYTES = 100 * 1024 * 1024  # 100MB per file
MAX_CACHE_DIR_BYTES = 500 * 1024 * 1024  # 500MB total
CACHE_EVICTION_TARGET_BYTES = 400 * 1024 * 1024  # 80% threshold after eviction

# Fields to include in query hash (user-specified query parameters)
HASH_FIELDS = frozenset(
    {
        "from",
        "select",
        "where",
        "include",
        "expand",
        "orderBy",
        "groupBy",
        "aggregate",
        "having",
        "limit",
        "subqueries",
    }
)

# Generate session ID once per CLI invocation (PID + UUID for uniqueness)
_SESSION_ID: str | None = None


def _get_session_id() -> str:
    """Get unique session identifier for this CLI process."""
    global _SESSION_ID  # noqa: PLW0603
    if _SESSION_ID is None:
        _SESSION_ID = f"{os.getpid()}_{uuid.uuid4().hex[:8]}"
    return _SESSION_ID


# =============================================================================
# Cursor Payload Model
# =============================================================================


class CursorPayload(BaseModel):
    """Cursor payload for resumable query pagination.

    Contains all information needed to resume a truncated query response.
    Serialized as base64-encoded JSON for transport.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Version for forward compatibility
    v: int = Field(default=CURSOR_VERSION, description="Cursor format version")

    # Query fingerprint (24 chars of SHA-256)
    qh: str = Field(description="Query hash for validation")

    # Position tracking
    skip: int = Field(default=0, ge=0, description="Records to skip")
    last_id: int | None = Field(
        default=None, alias="lastId", description="Last record ID for anchor-based resumption"
    )

    # Timestamp for TTL
    ts: int = Field(description="Creation timestamp (Unix ms)")

    # Mode determines resumption strategy
    mode: Literal["streaming", "full-fetch"] = Field(description="Execution mode")

    # Streaming mode: store Affinity API cursor
    api_cursor: str | None = Field(
        default=None, alias="apiCursor", description="Affinity API cursor for streaming mode"
    )

    # Full-fetch mode: store cache file path and hash
    cache_file: str | None = Field(
        default=None, alias="cacheFile", description="Cache file path for full-fetch mode"
    )
    cache_hash: str | None = Field(
        default=None, alias="cacheHash", description="SHA-256 of cache file for integrity"
    )

    # Optional: total count for progress tracking
    total: int | None = Field(default=None, description="Total record count (if known)")

    @field_validator("qh")
    @classmethod
    def validate_query_hash(cls, v: str) -> str:
        """Validate query hash is 24 hex characters."""
        if len(v) != 24 or not all(c in "0123456789abcdef" for c in v):
            raise ValueError("Query hash must be 24 hex characters")
        return v


# =============================================================================
# Exceptions
# =============================================================================


class InvalidCursor(Exception):
    """Raised when cursor validation fails."""

    pass


class CursorExpired(InvalidCursor):
    """Raised when cursor has expired."""

    pass


class CursorQueryMismatch(InvalidCursor):
    """Raised when cursor doesn't match current query."""

    pass


# =============================================================================
# Query Hashing
# =============================================================================


def hash_query(query: Query, output_format: str, *, max_records: int | None = None) -> str:
    """Generate deterministic hash of query for cursor validation.

    Only includes user-specified query fields, not internal state.
    Format is included because truncation boundaries are format-dependent.

    Args:
        query: The Query object to hash
        output_format: Output format (toon, json, markdown, etc.)
        max_records: --max-records of the run (it decides which rows the result holds);
            None leaves it out of the hash

    Returns:
        24-character hex hash (96 bits)
    """
    # Get query dict with aliases (from_ -> from, order_by -> orderBy, etc.)
    full_dump = query.model_dump(mode="json", by_alias=True, exclude_none=True)

    # Only include user-specified fields
    user_fields = {k: v for k, v in full_dump.items() if k in HASH_FIELDS}

    # Include format in hash (changing format invalidates cursor)
    user_fields["_format"] = output_format
    if max_records is not None:
        user_fields["_maxRecords"] = max_records

    # Sort keys for deterministic ordering
    canonical = json.dumps(user_fields, sort_keys=True, separators=(",", ":"))

    # SHA-256, truncated to 24 chars (96 bits)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


# =============================================================================
# Cursor Encoding/Decoding
# =============================================================================


def encode_cursor(payload: CursorPayload) -> str:
    """Encode cursor payload to base64 string.

    Args:
        payload: CursorPayload to encode

    Returns:
        Base64-encoded JSON string
    """
    json_bytes = payload.model_dump_json(by_alias=True, exclude_none=True).encode()
    return base64.urlsafe_b64encode(json_bytes).decode()


def decode_cursor(cursor_str: str) -> CursorPayload:
    """Decode cursor string to CursorPayload.

    Args:
        cursor_str: Base64-encoded cursor string

    Returns:
        CursorPayload object

    Raises:
        InvalidCursor: If cursor is malformed
    """
    try:
        json_bytes = base64.urlsafe_b64decode(cursor_str)
        data = json.loads(json_bytes)
        return CursorPayload.model_validate(data)
    except Exception as e:
        raise InvalidCursor(f"Invalid cursor format: {e}") from e


# =============================================================================
# Cursor Validation
# =============================================================================


def validate_cursor(
    cursor: CursorPayload,
    query: Query,
    output_format: str,
    *,
    max_records: int | None = None,
) -> None:
    """Validate cursor against current query.

    Args:
        cursor: Decoded cursor payload
        query: Current query to validate against
        output_format: Current output format
        max_records: Current --max-records (as passed to hash_query)

    Raises:
        InvalidCursor: If cursor is invalid
        CursorExpired: If cursor has expired
        CursorQueryMismatch: If cursor doesn't match query
    """
    # 1. Version check
    if cursor.v != CURSOR_VERSION:
        raise InvalidCursor(f"Unsupported cursor version {cursor.v} (expected {CURSOR_VERSION})")

    # 2. Query hash check (ensures same query + format)
    expected_hash = hash_query(query, output_format, max_records=max_records)
    if cursor.qh != expected_hash:
        raise CursorQueryMismatch(
            "Query or format does not match cursor (or --max-records changed). "
            "Cursors can only be used with the IDENTICAL query object, output format and "
            "--max-records. Changing any field (including 'from', 'where', 'orderBy', 'limit', "
            "format or --max-records) invalidates the cursor. To continue, re-run the original "
            "query to get a fresh cursor."
        )

    # 3. Expiration check (cursors valid for 1 hour)
    now_ms = int(time.time() * 1000)
    if now_ms - cursor.ts > CURSOR_TTL_SECONDS * 1000:
        raise CursorExpired(
            f"Cursor has expired (valid for {CURSOR_TTL_SECONDS // 60} minutes). "
            "Re-run the original query to get a fresh cursor."
        )

    # 4. Skip bounds check
    if cursor.skip < 0:
        raise InvalidCursor(f"Invalid skip value: {cursor.skip}")

    # 5. Mode-specific validation
    if cursor.mode == "full-fetch" and cursor.cache_file:
        _validate_cache_path(cursor.cache_file)


def _validate_cache_path(cache_file: str) -> None:
    """Validate cache file path is within expected directory.

    Args:
        cache_file: Path to cache file

    Raises:
        InvalidCursor: If path is invalid or outside cache directory
    """
    cache_path = Path(cache_file)
    expected_dir = get_cache_dir().resolve()

    # Security: validate path is within expected directory
    try:
        resolved = cache_path.resolve()
        if resolved.parent != expected_dir:
            raise InvalidCursor("Invalid cache path: outside cache directory")
    except (ValueError, OSError) as e:
        raise InvalidCursor(f"Invalid cache path: {e}") from e

    # Validate filename pattern
    if not cache_path.name.startswith("xaff_"):
        raise InvalidCursor("Invalid cache filename")


# =============================================================================
# Cache Management
# =============================================================================


def get_cache_dir() -> Path:
    """Get cross-platform cache directory.

    Returns:
        Path to cache directory (created if needed)
    """
    cache_dir = Path(tempfile.gettempdir()) / CACHE_DIR_NAME
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


def generate_cache_filename(query_hash: str) -> str:
    """Generate unique cache filename.

    Format: xaff_{query_hash}_{pid}_{uuid8}_{timestamp}.json

    Args:
        query_hash: 24-char query hash

    Returns:
        Unique filename for cache file
    """
    session_id = _get_session_id()
    timestamp = int(time.time() * 1000)
    return f"xaff_{query_hash}_{session_id}_{timestamp}.json"


def write_cache(
    data: list[dict[str, Any]],
    query_hash: str,
    *,
    extra: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Write query results to cache file.

    Args:
        data: Query result data to cache
        query_hash: Query hash for filename
        extra: What else a resumed page needs (included records, warnings, truncated),
            stored next to the rows and returned by read_cache_payload()

    Returns:
        Tuple of (cache_file_path, cache_content_hash)

    Raises:
        InvalidCursor: If data exceeds size limits
    """
    # Serialize data
    payload: dict[str, Any] = {**(extra or {}), "data": data}
    json_content = json.dumps(payload, separators=(",", ":"), default=str)
    content_bytes = json_content.encode()

    # Check single file limit
    if len(content_bytes) > MAX_CACHE_FILE_BYTES:
        raise InvalidCursor(
            f"Query result too large to cache ({len(content_bytes) / 1024 / 1024:.1f}MB > "
            f"{MAX_CACHE_FILE_BYTES / 1024 / 1024:.0f}MB limit). "
            "Consider adding a 'limit' to your query."
        )

    # Run cleanup before write
    cleanup_cache()

    # Check total directory size
    cache_dir = get_cache_dir()
    current_size = sum(f.stat().st_size for f in cache_dir.glob("xaff_*.json") if f.is_file())

    if current_size + len(content_bytes) > MAX_CACHE_DIR_BYTES:
        # Evict old files until under target
        _evict_cache_files(len(content_bytes))

    # Write file with restrictive permissions (0600)
    filename = generate_cache_filename(query_hash)
    cache_path = cache_dir / filename

    # Use atomic write pattern (write to temp, then rename)
    temp_path = cache_path.with_suffix(".tmp")
    try:
        temp_path.write_bytes(content_bytes)
        temp_path.chmod(0o600)
        temp_path.rename(cache_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    # Compute content hash
    content_hash = hashlib.sha256(content_bytes).hexdigest()

    return str(cache_path), content_hash


def read_cache(cursor: CursorPayload) -> list[dict[str, Any]] | None:
    """Read cached rows with integrity check.

    Args:
        cursor: Cursor with cache file info

    Returns:
        Cached data list, or None if cache is invalid/expired
    """
    payload = read_cache_payload(cursor)
    return payload["data"] if payload is not None else None


def read_cache_payload(cursor: CursorPayload) -> dict[str, Any] | None:
    """Read a cache file with integrity check: ``{"data": rows, ...}`` plus the ``extra``
    given to write_cache(). None if the cache is invalid or expired."""
    if not cursor.cache_file or not cursor.cache_hash:
        return None

    cache_path = Path(cursor.cache_file)

    try:
        # Age check FIRST (cheap stat() before expensive read+hash)
        file_age = time.time() - cache_path.stat().st_mtime
        if file_age > CURSOR_TTL_SECONDS:
            logger.warning("Cache file expired (%.0fs old), will re-execute", file_age)
            cache_path.unlink(missing_ok=True)
            return None

        # Read content
        content_bytes = cache_path.read_bytes()

        # Integrity check (only after confirming file is fresh)
        actual_hash = hashlib.sha256(content_bytes).hexdigest()
        if actual_hash != cursor.cache_hash:
            logger.warning("Cache integrity check failed, will re-execute")
            return None

        loaded = json.loads(content_bytes)
        if isinstance(loaded, list):  # Written before the cache held more than the rows
            return {"data": loaded}
        if isinstance(loaded, dict) and isinstance(loaded.get("data"), list):
            return loaded
        logger.warning("Cache content not recognized, will re-execute")
        return None

    except FileNotFoundError:
        logger.warning("Cache file not found, will re-execute")
        return None
    except (PermissionError, json.JSONDecodeError) as e:
        logger.warning("Cache read failed: %s, will re-execute", e)
        return None


def delete_cache(cache_file: str) -> None:
    """Delete cache file after pagination complete.

    Args:
        cache_file: Path to cache file to delete
    """
    try:
        Path(cache_file).unlink(missing_ok=True)
    except Exception as e:
        logger.debug("Failed to delete cache file: %s", e)


def cleanup_cache() -> None:
    """Clean up expired cache files.

    Called on CLI startup and before cache writes.
    Deletes files older than CURSOR_TTL_SECONDS.
    """
    cache_dir = get_cache_dir()
    now = time.time()

    for cache_file in cache_dir.glob("xaff_*.json"):
        try:
            age = now - cache_file.stat().st_mtime
            if age > CURSOR_TTL_SECONDS:
                cache_file.unlink(missing_ok=True)
                logger.debug("Deleted expired cache: %s", cache_file.name)
        except Exception as e:
            logger.debug("Failed to clean up cache file %s: %s", cache_file.name, e)


def _evict_cache_files(needed_bytes: int) -> None:
    """Evict old cache files using LRU policy.

    Args:
        needed_bytes: Bytes needed for new cache file
    """
    cache_dir = get_cache_dir()

    # Get all cache files sorted by mtime (oldest first)
    cache_files = sorted(
        [f for f in cache_dir.glob("xaff_*.json") if f.is_file()],
        key=lambda f: f.stat().st_mtime,
    )

    current_size = sum(f.stat().st_size for f in cache_files)
    target_size = CACHE_EVICTION_TARGET_BYTES - needed_bytes

    for cache_file in cache_files:
        if current_size <= target_size:
            break
        try:
            file_size = cache_file.stat().st_size
            cache_file.unlink(missing_ok=True)
            current_size -= file_size
            logger.debug(
                "Evicted cache file: %s (%.1fMB)", cache_file.name, file_size / 1024 / 1024
            )
        except Exception as e:
            logger.debug("Failed to evict cache file %s: %s", cache_file.name, e)


# =============================================================================
# Cursor Creation Helpers
# =============================================================================


def create_streaming_cursor(
    query: Query,
    output_format: str,
    skip: int,
    *,
    api_cursor: str | None = None,
    last_id: int | None = None,
    total: int | None = None,
    max_records: int | None = None,
) -> CursorPayload:
    """Create cursor for streaming mode resumption.

    Args:
        query: The query being executed
        output_format: Output format
        skip: Records already returned
        api_cursor: Affinity API cursor for efficient resumption
        last_id: Last record ID for anchor-based fallback
        total: Total record count if known
        max_records: --max-records of the run (part of the query hash)

    Returns:
        CursorPayload for streaming mode
    """
    return CursorPayload(
        v=CURSOR_VERSION,
        qh=hash_query(query, output_format, max_records=max_records),
        skip=skip,
        last_id=last_id,
        ts=int(time.time() * 1000),
        mode="streaming",
        api_cursor=api_cursor,
        total=total,
    )


def find_resume_position(
    records: list[dict[str, Any]],
    cursor: CursorPayload,
) -> tuple[int, list[str]]:
    """Find position to resume from, handling data changes.

    Uses lastId as the anchor point when available. If lastId is not found
    (record was deleted), falls back to skip-based positioning.

    Args:
        records: Current result set
        cursor: Cursor with skip and lastId

    Returns:
        Tuple of (resume_position, warnings).
        Position is the index to start from (i.e., records[position:]).
        Warnings are messages about data changes.
    """
    warnings: list[str] = []

    # 1. Try to find lastId in current result set
    if cursor.last_id is not None:
        for i, record in enumerate(records):
            record_id = record.get("id") or record.get("listEntryId")
            if record_id == cursor.last_id:
                # Found anchor - resume from next record
                return i + 1, warnings

        # lastId not found (deleted) - warn and use skip as fallback
        warnings.append(
            f"Record id={cursor.last_id} not found (may have been deleted). "
            "Using skip-based positioning which may cause duplicates or gaps."
        )

    # 2. Use skip as fallback
    if cursor.skip < len(records):
        return cursor.skip, warnings

    # 3. skip exceeds current total - return empty (end of data)
    return len(records), warnings


def create_full_fetch_cursor(
    query: Query,
    output_format: str,
    skip: int,
    cache_file: str,
    cache_hash: str,
    *,
    last_id: int | None = None,
    total: int | None = None,
    max_records: int | None = None,
) -> CursorPayload:
    """Create cursor for full-fetch mode resumption.

    Args:
        query: The query being executed
        output_format: Output format
        skip: Records already returned
        cache_file: Path to cache file
        cache_hash: SHA-256 of cache content
        last_id: Last record ID for anchor-based fallback
        total: Total record count
        max_records: --max-records of the run (part of the query hash)

    Returns:
        CursorPayload for full-fetch mode
    """
    return CursorPayload(
        v=CURSOR_VERSION,
        qh=hash_query(query, output_format, max_records=max_records),
        skip=skip,
        last_id=last_id,
        ts=int(time.time() * 1000),
        mode="full-fetch",
        cache_file=cache_file,
        cache_hash=cache_hash,
        total=total,
    )
