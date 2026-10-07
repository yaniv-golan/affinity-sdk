"""
Affinity V2 API versions (the ``X-Affinity-Api-Version`` request header).

Affinity versions its V2 API by date. Every API key has a *default* version (chosen in the
Affinity dashboard); a request can override it with the ``X-Affinity-Api-Version`` header, and
every V2 response echoes the version that answered it. V1 endpoints are not versioned.

The SDK sends no header unless you pin a version (``Affinity(affinity_api_version=...)``,
CLI ``--api-version``), so by default your key's default version applies.
"""

from __future__ import annotations

import re
from datetime import date

from .exceptions import ConfigurationError

AFFINITY_API_VERSION_HEADER = "X-Affinity-Api-Version"

#: V2 API versions known to (and tested with) this SDK release, oldest first.
KNOWN_AFFINITY_API_VERSIONS: tuple[str, ...] = ("2024-01-01", "2026-07-15", "2026-09-17")

#: Sentinel accepted by Affinity: always the newest version.
CURRENT_AFFINITY_API_VERSION = "current"

# Values that mean "send no header" (use the API key's default version).
_KEY_DEFAULT_ALIASES = frozenset({"", "auto", "key-default", "default"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_affinity_api_version(value: str | None) -> tuple[str | None, str | None]:
    """
    Validate and normalize a configured Affinity API version.

    Returns ``(version, warning)``:

    - ``version`` is the header value to send, or ``None`` to send no header (the API key's
      default version applies). ``None``, ``""``, ``"auto"``, ``"key-default"`` and
      ``"default"`` all mean "no header".
    - ``warning`` is a message for a well-formed date this SDK release does not know
      (Affinity decides whether it is valid), else ``None``.

    Raises:
        ConfigurationError: the value is neither ``current`` nor a ``YYYY-MM-DD`` date.
    """
    if value is None:
        return None, None
    if not isinstance(value, str):
        raise ConfigurationError(
            f"Invalid Affinity API version {value!r}: expected a string such as "
            f"'{KNOWN_AFFINITY_API_VERSIONS[-1]}' or 'current'."
        )
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in _KEY_DEFAULT_ALIASES:
        return None, None
    if lowered == CURRENT_AFFINITY_API_VERSION:
        return CURRENT_AFFINITY_API_VERSION, None
    if _DATE_RE.match(stripped):
        try:
            date.fromisoformat(stripped)
        except ValueError:
            pass
        else:
            if stripped in KNOWN_AFFINITY_API_VERSIONS:
                return stripped, None
            return stripped, (
                f"Unknown Affinity API version '{stripped}' (this SDK knows "
                f"{', '.join(KNOWN_AFFINITY_API_VERSIONS)}); sending it anyway - "
                "Affinity rejects versions it does not support."
            )
    raise ConfigurationError(
        f"Invalid Affinity API version {value!r}: expected 'current', a date such as "
        f"{', '.join(KNOWN_AFFINITY_API_VERSIONS)}, or 'auto' for the API key's default."
    )


__all__ = [
    "AFFINITY_API_VERSION_HEADER",
    "CURRENT_AFFINITY_API_VERSION",
    "KNOWN_AFFINITY_API_VERSIONS",
    "normalize_affinity_api_version",
]
