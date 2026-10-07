"""The CLI's proactive throttle must read the per-minute quota header Affinity actually sends.

It used to read `X-RateLimit-Remaining`, which Affinity never sends (it sends
`x-ratelimit-limit-user-remaining`), and `ResponseInfo.headers` is a plain dict with lowercase
keys, so the lookup always returned None and the throttle never fired.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from affinity.cli.query.executor import RateLimitedExecutor, user_rate_limit_remaining


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"x-ratelimit-limit-user-remaining": "7"}, 7),  # what ResponseInfo.headers holds
        ({"X-Ratelimit-Limit-User-Remaining": "7"}, 7),  # any casing
        ({"x-ratelimit-limit-user-remaining": "  12 "}, 12),
        ({}, None),
        ({"x-ratelimit-limit-user-remaining": ""}, None),
        ({"x-ratelimit-limit-user-remaining": "abc"}, None),
        ({"X-RateLimit-Remaining": "3"}, None),  # header Affinity never sends
    ],
)
def test_user_rate_limit_remaining(headers: dict[str, str], expected: int | None) -> None:
    assert user_rate_limit_remaining(headers) == expected


def test_low_remaining_quota_triggers_proactive_delay() -> None:
    limiter = RateLimitedExecutor()
    limiter.on_response(200, user_rate_limit_remaining({"x-ratelimit-limit-user-remaining": "5"}))
    assert limiter._delay_until > 0


@pytest.mark.parametrize(
    "module", ["affinity/cli/commands/query_cmd.py", "affinity/cli/commands/field_cmds.py"]
)
def test_commands_do_not_read_nonexistent_header(module: str) -> None:
    source = (Path(__file__).resolve().parents[1] / module).read_text(encoding="utf-8")
    assert "X-RateLimit-Remaining" not in source
    assert "user_rate_limit_remaining" in source
