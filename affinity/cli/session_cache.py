"""Session cache for cross-invocation caching in CLI pipelines.

This module provides file-based session caching to avoid redundant API calls
when running multiple CLI commands in a pipeline. Enable by setting the
AFFINITY_SESSION_CACHE environment variable to a directory path.

Example:
    export AFFINITY_SESSION_CACHE=$(affinity session start)
    affinity list export "My List" | affinity person get
    affinity session end
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class SessionCacheConfig:
    """Configuration for session caching."""

    DEFAULT_TTL = 600  # 10 minutes - longer than in-memory default

    def __init__(self) -> None:
        self.cache_dir: Path | None = self._get_cache_dir()
        self.ttl: float = self._get_ttl()
        self.enabled: bool = self._init_cache_dir()
        self.tenant_hash: str | None = None

    def _get_ttl(self) -> float:
        """Get TTL from environment or use default."""
        ttl_str = os.environ.get("AFFINITY_SESSION_CACHE_TTL")
        if ttl_str:
            try:
                return float(ttl_str)
            except ValueError:
                pass
        return self.DEFAULT_TTL

    def _get_cache_dir(self) -> Path | None:
        """Get cache directory from environment."""
        cache_path = os.environ.get("AFFINITY_SESSION_CACHE")
        if not cache_path:
            return None
        return Path(cache_path)

    def _init_cache_dir(self) -> bool:
        """Initialize cache directory, creating if needed."""
        if self.cache_dir is None:
            return False

        # Check if path exists but is not a directory
        if self.cache_dir.exists() and not self.cache_dir.is_dir():
            print(
                f"Warning: AFFINITY_SESSION_CACHE '{self.cache_dir}' is not a directory",
                file=sys.stderr,
            )
            return False

        if not self.cache_dir.exists():
            # Auto-create directory with restricted permissions (owner-only)
            # to prevent other users from reading cached API responses
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError as e:
                # Warn but don't fail - caching is optional
                print(
                    f"Warning: Cannot create session cache directory '{self.cache_dir}': {e}",
                    file=sys.stderr,
                )
                return False

        # Cleanup stale .tmp files from interrupted writes
        self._cleanup_stale_tmp_files()
        return True

    def _cleanup_stale_tmp_files(self) -> None:
        """Remove orphaned .tmp files older than TTL."""
        if self.cache_dir is None:
            return
        try:
            for tmp in self.cache_dir.glob("*.tmp"):
                try:
                    if (time.time() - tmp.stat().st_mtime) > self.ttl:
                        tmp.unlink(missing_ok=True)
                except OSError:
                    pass  # Ignore errors on individual files
        except OSError:
            pass  # Ignore errors listing directory

    def set_tenant_hash(self, api_key: str, *, api_version: str | None = None) -> None:
        """Set tenant hash from API key + Affinity API version for cache isolation.

        Responses differ between Affinity V2 API versions, so entries written under one
        version (or the key's default) are never served to another.
        """
        material = f"{api_key}|apiver={api_version or 'default'}"
        self.tenant_hash = hashlib.sha256(material.encode()).hexdigest()[:12]


def _write_stderr(msg: str) -> None:
    """Write a message to stderr."""
    print(msg, file=sys.stderr)


class SessionCache:
    """File-based session cache for cross-invocation caching."""

    def __init__(self, config: SessionCacheConfig, *, trace: bool = False) -> None:
        self.config = config
        self.trace = trace
        # Returns the Affinity V2 API version that answered the latest request; stored
        # with each entry so a cache hit still reports which version produced the data.
        self.version_provider: Callable[[], str | None] | None = None
        # Versions recorded on entries served from this cache (CLI meta.affinityApiVersion).
        self.api_versions_seen: set[str] = set()

    def _note_entry_version(self, data: Any) -> None:
        version = data.get("affinityApiVersion") if isinstance(data, dict) else None
        if isinstance(version, str) and version:
            self.api_versions_seen.add(version)

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.config.tenant_hash is not None

    def _sanitize_key(self, key: str) -> str:
        """Sanitize cache key for safe filename usage.

        For long keys, appends a hash to prevent collisions from truncation.
        """
        # Replace non-word chars (except - and .) with underscore
        # \w = [a-zA-Z0-9_], so this keeps alphanumerics, underscore, dash, and dot
        safe_key = re.sub(r"[^\w\-.]", "_", key)
        # If key is long, truncate but add hash suffix to prevent collisions
        if len(safe_key) > 180:
            key_hash = hashlib.md5(key.encode()).hexdigest()[:8]
            return f"{safe_key[:180]}_{key_hash}"
        return safe_key

    def _cache_path(self, key: str) -> Path:
        """Get full path for a cache key."""
        if self.config.cache_dir is None:
            raise RuntimeError("Cache directory not configured")
        safe_key = self._sanitize_key(key)
        filename = f"{safe_key}_{self.config.tenant_hash}.json"
        return self.config.cache_dir / filename

    def _is_expired(self, path: Path) -> bool:
        """Check if cache file is expired using file mtime."""
        try:
            mtime = path.stat().st_mtime
            return (time.time() - mtime) > self.config.ttl
        except OSError:
            return True

    def get(self, key: str, model_class: type[T]) -> T | None:
        """Get cached value, deserializing to Pydantic model."""
        if not self.enabled:
            return None

        path = self._cache_path(key)
        if not path.exists():
            logger.debug("[CACHE] MISS: %s (session)", key)
            if self.trace:
                _write_stderr(f"trace #- cache miss: {key}")
            return None

        # Check TTL using file mtime (avoids parsing JSON just for expiration check)
        if self._is_expired(path):
            path.unlink(missing_ok=True)
            logger.debug("[CACHE] EXPIRED: %s (session)", key)
            if self.trace:
                _write_stderr(f"trace #- cache expired: {key}")
            return None

        try:
            data = json.loads(path.read_text())
            result = model_class.model_validate(data["value"])
            self._note_entry_version(data)
            logger.debug("[CACHE] HIT: %s (session)", key)
            if self.trace:
                _write_stderr(f"trace #+ cache hit: {key}")
            return result
        except (json.JSONDecodeError, KeyError, ValidationError) as e:
            logger.debug("[CACHE] CORRUPTED: %s - %s (session)", key, e)
            if self.trace:
                _write_stderr(f"trace #! cache corrupted: {key}")
            path.unlink(missing_ok=True)
            return None

    def get_list(self, key: str, model_class: type[T]) -> list[T] | None:
        """Get cached list of models."""
        if not self.enabled:
            return None

        path = self._cache_path(key)
        if not path.exists():
            logger.debug("[CACHE] MISS: %s (session)", key)
            if self.trace:
                _write_stderr(f"trace #- cache miss: {key}")
            return None

        if self._is_expired(path):
            path.unlink(missing_ok=True)
            logger.debug("[CACHE] EXPIRED: %s (session)", key)
            if self.trace:
                _write_stderr(f"trace #- cache expired: {key}")
            return None

        try:
            data = json.loads(path.read_text())
            result = [model_class.model_validate(item) for item in data["value"]]
            self._note_entry_version(data)
            logger.debug("[CACHE] HIT: %s (%d items) (session)", key, len(result))
            if self.trace:
                _write_stderr(f"trace #+ cache hit: {key} ({len(result)} items)")
            return result
        except (json.JSONDecodeError, KeyError, ValidationError) as e:
            logger.debug("[CACHE] CORRUPTED: %s - %s (session)", key, e)
            if self.trace:
                _write_stderr(f"trace #! cache corrupted: {key}")
            path.unlink(missing_ok=True)
            return None

    def set(self, key: str, value: BaseModel | Sequence[BaseModel]) -> None:
        """Cache a value (single model or list of models).

        Uses atomic write (write to temp file, then rename) to prevent
        corruption from concurrent access in parallel pipelines.
        """
        if not self.enabled:
            return

        path = self._cache_path(key)
        if isinstance(value, BaseModel):
            serialized: dict[str, Any] | list[dict[str, Any]] = value.model_dump(mode="json")
        else:
            serialized = [v.model_dump(mode="json") for v in value]

        data: dict[str, Any] = {"value": serialized}
        if self.version_provider is not None:
            try:
                version = self.version_provider()
            except Exception:
                version = None
            if version:
                data["affinityApiVersion"] = version

        try:
            # Atomic write: write to temp file, then rename
            # Using .json.tmp makes it clear which file this temp relates to
            tmp_path = path.with_name(f"{path.name}.tmp")
            tmp_path.write_text(json.dumps(data))  # Compact JSON for cache files
            tmp_path.replace(path)  # Atomic on POSIX
            logger.debug("[CACHE] SET: %s (session)", key)
            if self.trace:
                _write_stderr(f"trace #= cache set: {key}")
        except OSError as e:
            # Cache write failure shouldn't crash the command
            logger.debug("[CACHE] WRITE FAILED: %s - %s (session)", key, e)
            if self.trace:
                _write_stderr(f"trace #! cache write failed: {key}")

    def invalidate(self, key: str) -> None:
        """Remove a specific cache entry."""
        if not self.enabled:
            return
        path = self._cache_path(key)
        path.unlink(missing_ok=True)
        logger.debug("[CACHE] INVALIDATED: %s (session)", key)
        if self.trace:
            _write_stderr(f"trace #x cache invalidated: {key}")

    def invalidate_prefix(self, prefix: str) -> None:
        """Remove all cache entries matching a prefix."""
        if not self.enabled or not self.config.cache_dir:
            return
        safe_prefix = self._sanitize_key(prefix)
        count = 0
        for path in self.config.cache_dir.glob(f"{safe_prefix}*_{self.config.tenant_hash}.json"):
            path.unlink(missing_ok=True)
            count += 1
        if count:
            logger.debug("[CACHE] INVALIDATED %d entries with prefix: %s (session)", count, prefix)
            if self.trace:
                _write_stderr(f"trace #x cache invalidated {count} entries: {prefix}*")
