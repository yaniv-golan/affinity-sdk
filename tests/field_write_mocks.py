"""respx helpers for CLI field-write tests.

`list entry field` reads dropdown options fresh from the V2 dropdown-options endpoint and sends
every --set/--set-json/--unset in one V2 PATCH; these helpers mock both for a list.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from affinity.models.types import FieldId


def _norm(field_id: Any) -> str:
    try:
        return str(FieldId(field_id))
    except (ValueError, TypeError):
        return str(field_id)


def mock_list_field_writes(respx_mock: Any, list_id: int, v1_fields: list[dict[str, Any]]) -> Any:
    """Mock dropdown options (from the V1 field fixtures) and the update-fields PATCH.

    Returns the PATCH route; ``patched(route)`` gives the updates sent.
    """
    options = {
        _norm(f["id"]): f.get("dropdown_options") or f.get("dropdownOptions") or []
        for f in v1_fields
    }

    def dropdown_options(request: httpx.Request) -> httpx.Response:
        m = re.search(r"/fields/([^/]+)/dropdown-options", request.url.path)
        items = options.get(m.group(1) if m else "", [])
        return httpx.Response(
            200,
            json={
                "data": [{"type": "dropdown", "id": o["id"], "text": o["text"]} for o in items],
                "pagination": {"nextUrl": None, "prevUrl": None},
            },
        )

    respx_mock.get(
        url__regex=rf"https://api\.affinity\.co/v2/lists/{list_id}/fields/[^/]+/dropdown-options"
    ).mock(side_effect=dropdown_options)
    return respx_mock.patch(
        url__regex=rf"https://api\.affinity\.co/v2/lists/{list_id}/list-entries/\d+/fields$"
    ).mock(return_value=httpx.Response(200, json={"operation": "update-fields"}))


def patched(route: Any) -> list[dict[str, Any]]:
    """All updates sent through a mocked update-fields PATCH route, in order."""
    out: list[dict[str, Any]] = []
    for call in route.calls:
        out.extend(json.loads(call.request.content)["updates"])
    return out
