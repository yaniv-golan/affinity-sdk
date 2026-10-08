"""Check every V2 request the test suite sends against Affinity's OpenAPI specs.

``tests/conftest.py`` wraps ``httpx.Client.send`` / ``httpx.AsyncClient.send`` for every test and
hands each request to :func:`check_request`. A request whose path starts with ``/v2/`` must match
an operation (method + path template) of the spec version it asks for, and every query parameter
must be declared on that operation. Affinity silently drops unknown query parameters, so an
undeclared one is a no-op bug (``fieldTypes`` on ``get_fields`` once was).

Which spec applies:
- ``X-Affinity-Api-Version`` naming a known version: that version's spec.
- ``current`` or an unknown date: the newest spec.
- no header: the API key's default applies, which can be any version, so the operation must
  exist in every spec and the parameter must be declared in every spec.

Only requests sent in tests are checked; operations no test sends are not covered. V1 requests
(no ``/v2/`` prefix) and request bodies are not checked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import httpx

from affinity.api_versions import AFFINITY_API_VERSION_HEADER, KNOWN_AFFINITY_API_VERSIONS

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "tools" / "openapi_snapshots"


@dataclass(frozen=True)
class Operation:
    method: str
    template: str
    pattern: re.Pattern[str]
    query: Mapping[str, frozenset[str]]  # version -> declared query parameter names


def _path_pattern(template: str, params: Mapping[str, Any]) -> re.Pattern[str]:
    """``/v2/lists/{listId}`` -> regex; integer path parameters must be digits."""
    out = "^"
    for literal, name in re.findall(r"([^{]*)(?:\{([^}]+)\})?", template):
        out += re.escape(literal)
        if name:
            schema = params.get(f"path:{name}", {}).get("schema", {})
            out += r"-?\d+" if schema.get("type") == "integer" else r"[^/]+"
    return re.compile(out + "$")


def load_operations(
    snapshots: Mapping[str, Mapping[str, Any]],
) -> list[Operation]:
    """Operations of every version, keyed by (method, template), from snapshot digests."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for version, snapshot in snapshots.items():
        for key, op in snapshot["digest"]["operations"].items():
            method, template = key.split(" ", 1)
            entry = by_key.setdefault((method, template), {"params": op["parameters"], "query": {}})
            entry["query"][version] = frozenset(
                name.removeprefix("query:")
                for name in op["parameters"]
                if name.startswith("query:")
            )
    return [
        Operation(method, template, _path_pattern(template, entry["params"]), entry["query"])
        for (method, template), entry in sorted(by_key.items())
    ]


@cache
def committed_operations() -> tuple[tuple[str, ...], tuple[Operation, ...]]:
    snapshots = {p.stem: json.loads(p.read_text()) for p in sorted(SNAPSHOT_DIR.glob("*.json"))}
    if sorted(snapshots) != sorted(KNOWN_AFFINITY_API_VERSIONS):
        raise RuntimeError(
            f"tools/openapi_snapshots has {sorted(snapshots)}, but the SDK knows "
            f"{sorted(KNOWN_AFFINITY_API_VERSIONS)}"
        )
    return tuple(sorted(snapshots)), tuple(load_operations(snapshots))


def match(method: str, path: str, operations: tuple[Operation, ...]) -> Operation | None:
    found = [op for op in operations if op.method == method and op.pattern.match(path)]
    if len(found) > 1:
        raise RuntimeError(f"{method} {path} matches {[op.template for op in found]}")
    return found[0] if found else None


def versions_for(header: str | None, versions: tuple[str, ...]) -> tuple[str, ...]:
    if not header:
        return versions
    if header in versions:
        return (header,)
    return (versions[-1],)  # "current" or a date the SDK doesn't know: the newest spec


def check(
    method: str,
    raw_path: str,
    query_names: set[str],
    header: str | None,
    *,
    versions: tuple[str, ...],
    operations: tuple[Operation, ...],
) -> tuple[bool, list[str]]:
    """Returns ``(is_unknown_operation, problems)`` for one V2 request."""
    op = match(method, raw_path, operations)
    if op is None:
        return True, [f"{method} {raw_path}: no such operation in the spec"]
    problems: list[str] = []
    wanted = versions_for(header, versions)
    missing = [v for v in wanted if v not in op.query]
    if missing:
        how = f"pinned to {header}" if header else "sent without a version header"
        problems.append(
            f"{method} {op.template} ({how}): the operation does not exist in {', '.join(missing)}"
        )
    for name in sorted(query_names):
        undeclared = [v for v in wanted if v in op.query and name not in op.query[v]]
        if undeclared:
            problems.append(
                f"{method} {op.template}: query parameter {name!r} is not declared in "
                f"{', '.join(undeclared)}"
            )
    return False, problems


def check_request(request: httpx.Request) -> tuple[bool, list[str]] | None:
    """Check one request; None when it is not a V2 API request."""
    raw_path = request.url.raw_path.decode("ascii").split("?", 1)[0]
    if not raw_path.startswith("/v2/"):
        return None
    versions, operations = committed_operations()
    return check(
        request.method,
        raw_path,
        set(request.url.params.keys()),
        request.headers.get(AFFINITY_API_VERSION_HEADER),
        versions=versions,
        operations=operations,
    )
