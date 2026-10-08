"""Tests for tests/spec_guard.py (the per-request OpenAPI check every test runs under)."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest

from tests import spec_guard

VERSIONS = ("2024-01-01", "2026-07-15")


def _snapshot(operations: dict[str, list[str]], **path_types: str) -> dict[str, Any]:
    def params(names: list[str], template: str) -> dict[str, Any]:
        out: dict[str, Any] = {f"query:{n}": {"schema": {}} for n in names}
        for name in re.findall(r"\{([^}]+)\}", template):
            out[f"path:{name}"] = {"schema": {"type": path_types.get(name, "integer")}}
        return out

    return {
        "digest": {
            "operations": {
                key: {"parameters": params(names, key.split(" ", 1)[1])}
                for key, names in operations.items()
            }
        }
    }


@pytest.fixture
def operations() -> tuple[spec_guard.Operation, ...]:
    old = _snapshot(
        {
            "GET /v2/companies": ["cursor", "limit"],
            "GET /v2/companies/{companyId}": [],
            "GET /v2/companies/fields": ["filter"],
        }
    )
    new = _snapshot(
        {
            "GET /v2/companies": ["cursor", "limit", "term"],
            "GET /v2/companies/{companyId}": [],
            "GET /v2/companies/fields": ["filter"],
            "GET /v2/rate-limit": [],
            "GET /v2/tasks/company-merges/{taskId}": [],
        },
        taskId="string",
    )
    return tuple(spec_guard.load_operations({VERSIONS[0]: old, VERSIONS[1]: new}))


def _check(
    ops: tuple[spec_guard.Operation, ...],
    method: str,
    path: str,
    query: set[str] | None = None,
    header: str | None = None,
) -> tuple[bool, list[str]]:
    return spec_guard.check(method, path, query or set(), header, versions=VERSIONS, operations=ops)


def test_integer_path_parameters_keep_literal_paths_apart(operations: Any) -> None:
    assert spec_guard.match("GET", "/v2/companies/fields", operations).template == (
        "/v2/companies/fields"
    )
    assert spec_guard.match("GET", "/v2/companies/42", operations).template == (
        "/v2/companies/{companyId}"
    )
    assert spec_guard.match("GET", "/v2/tasks/company-merges/abc", operations) is not None
    assert spec_guard.match("POST", "/v2/companies/42", operations) is None


def test_unknown_operation(operations: Any) -> None:
    unknown, problems = _check(operations, "GET", "/v2/widgets")
    assert unknown and "no such operation" in problems[0]


def test_declared_params_pass(operations: Any) -> None:
    assert _check(operations, "GET", "/v2/companies", {"cursor", "limit"}) == (False, [])


def test_param_declared_only_in_newer_version(operations: Any) -> None:
    # No header: the key's default may be the old version, which drops `term`.
    _, problems = _check(operations, "GET", "/v2/companies", {"term"})
    assert problems == ["GET /v2/companies: query parameter 'term' is not declared in 2024-01-01"]
    assert _check(operations, "GET", "/v2/companies", {"term"}, "2026-07-15") == (False, [])


def test_version_gated_operation(operations: Any) -> None:
    _, problems = _check(operations, "GET", "/v2/rate-limit")
    assert "sent without a version header" in problems[0] and "2024-01-01" in problems[0]
    _, problems = _check(operations, "GET", "/v2/rate-limit", header="2024-01-01")
    assert "pinned to 2024-01-01" in problems[0]
    assert _check(operations, "GET", "/v2/rate-limit", header="2026-07-15") == (False, [])


def test_current_and_unknown_dates_use_the_newest_spec(operations: Any) -> None:
    assert _check(operations, "GET", "/v2/rate-limit", header="current") == (False, [])
    assert _check(operations, "GET", "/v2/rate-limit", header="2027-01-01") == (False, [])


def test_v1_and_non_api_paths_are_not_checked() -> None:
    assert spec_guard.check_request(httpx.Request("GET", "https://api.affinity.co/lists")) is None
    request = httpx.Request("GET", "https://files.example/x/v2/y")
    assert spec_guard.check_request(request) is None


def test_every_committed_template_matches_exactly_itself() -> None:
    _versions, ops = spec_guard.committed_operations()
    for op in ops:
        path = re.sub(r"\{[^}]+\}", "1", op.template)
        if not op.pattern.match(path):
            path = re.sub(r"\{[^}]+\}", "x", op.template)
        assert spec_guard.match(op.method, path, ops) is op, op.template


def test_committed_snapshots_are_the_sdk_known_versions() -> None:
    versions, _ops = spec_guard.committed_operations()
    from affinity.api_versions import KNOWN_AFFINITY_API_VERSIONS

    assert list(versions) == sorted(KNOWN_AFFINITY_API_VERSIONS)
