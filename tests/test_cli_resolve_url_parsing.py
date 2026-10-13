from __future__ import annotations

import pytest

pytest.importorskip("rich_click")

from affinity.cli.commands.resolve_url_cmd import _parse_affinity_url
from affinity.cli.errors import CLIError


def test_resolve_url_accepts_tenant_host() -> None:
    resolved = _parse_affinity_url("https://mydomain.affinity.co/companies/333333333")
    assert resolved.type == "company"
    assert resolved.company_id == 333333333


def test_resolve_url_accepts_tenant_host_affinity_dot_com() -> None:
    resolved = _parse_affinity_url("https://mydomain.affinity.com/companies/333333333")
    assert resolved.type == "company"
    assert resolved.company_id == 333333333


def test_resolve_url_rejects_non_affinity_host() -> None:
    with pytest.raises(CLIError):
        _parse_affinity_url("https://example.com/companies/1")


def test_resolve_url_rejects_unrecognized_path() -> None:
    with pytest.raises(CLIError):
        _parse_affinity_url("https://app.affinity.co/not-a-real-page/1")
