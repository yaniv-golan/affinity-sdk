"""Every V2 request body the SDK builds matches the pinned request schema.

Each write is sent to a mocked API; the captured JSON body is checked against the operation's
request schema in every committed snapshot (tools/openapi_snapshots/) that has the operation.
The check is strict: unknown keys fail (Affinity's request bodies are closed), as do missing
required keys, wrong types and values outside an enum. Catches renamed keys, nulls where none
are allowed, and drift between API versions, offline.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from httpx import Response

from affinity import Affinity
from affinity.models import (
    ListCreate,
    NoteCreate,
    NoteUpdate,
    OpportunityUpdate,
    PersonCreate,
    ReminderCreate,
    ReminderUpdate,
    WebhookCreate,
    WebhookUpdate,
)
from affinity.types import (
    CompanyId,
    ListId,
    ListType,
    NoteId,
    NoteType,
    OpportunityId,
    PersonId,
    ReminderIdType,
    ReminderResetType,
    ReminderType,
    UserId,
    WebhookEvent,
    WebhookId,
    WebhookStatus,
)

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)

SNAPSHOTS = Path(__file__).resolve().parents[1] / "tools" / "openapi_snapshots"
V2 = "https://api.affinity.co/v2"
_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def _digests() -> dict[str, dict[str, Any]]:
    return {
        p.stem: json.loads(p.read_text())["digest"] for p in sorted(SNAPSHOTS.glob("[0-9]*.json"))
    }


def _resolve(schemas: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = schemas[node["$ref"].rsplit("/", 1)[-1]]
    return node


def _properties(schemas: dict[str, Any], node: dict[str, Any]) -> dict[str, Any] | None:
    node = _resolve(schemas, node)
    if "properties" in node or "allOf" in node:
        props = dict(node.get("properties", {}))
        for part in node.get("allOf", []):
            props.update(_properties(schemas, part) or {})
        return props
    return None


def problems(schemas: dict[str, Any], node: dict[str, Any], value: Any, at: str = "$") -> list[str]:
    """Why ``value`` doesn't match ``node`` (empty when it does)."""
    node = _resolve(schemas, node)
    for key in ("oneOf", "anyOf"):
        if key in node:
            branches = [problems(schemas, b, value, at) for b in node[key]]
            if any(not b for b in branches):
                return []
            return [f"{at}: matches no {key} branch: " + " | ".join(b[0] for b in branches)]
    out: list[str] = []
    for part in node.get("allOf", []):
        out += [p for p in problems(schemas, part, value, at) if "unknown key" not in p]
    types = node.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_TYPES[t](value) for t in types):
            return [f"{at}: {value!r} is not {'/'.join(types)}"]
    if "const" in node and value != node["const"]:
        out.append(f"{at}: {value!r} != const {node['const']!r}")
    if "enum" in node and value not in node["enum"]:
        out.append(f"{at}: {value!r} not in {node['enum']}")
    if isinstance(value, dict):
        props = _properties(schemas, node)
        required = set(node.get("required", []))
        for part in node.get("allOf", []):
            required |= set(_resolve(schemas, part).get("required", []))
        out += [f"{at}: missing required key {k!r}" for k in sorted(required - set(value))]
        if props is not None:
            for k, v in value.items():
                if k not in props:
                    out.append(f"{at}: unknown key {k!r}")
                else:
                    out += problems(schemas, props[k], v, f"{at}.{k}")
    if isinstance(value, list) and "items" in node:
        for i, item in enumerate(value):
            out += problems(schemas, node["items"], item, f"{at}[{i}]")
    return out


DUE = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
# (operation, path to mock, SDK call)
CASES: list[tuple[str, str, Callable[[Affinity], Any]]] = [
    (
        "POST /v2/webhooks",
        "/webhooks",
        lambda c: c.webhooks.create(
            WebhookCreate(url="https://h.example.org/a", subscriptions=[WebhookEvent.LIST_CREATED])
        ),
    ),
    (
        "POST /v2/webhooks/{webhookId}",
        "/webhooks/7",
        lambda c: c.webhooks.update(
            WebhookId(7),
            WebhookUpdate(
                url="https://h.example.org/b", subscriptions=[], status=WebhookStatus.DISABLED
            ),
        ),
    ),
    (
        "POST /v2/reminders",
        "/reminders",
        lambda c: c.reminders.create(
            ReminderCreate(
                owner_id=UserId(2),
                type=ReminderType.ONE_TIME,
                content="x",
                due_date=DUE,
                person_id=PersonId(5),
            )
        ),
    ),
    (
        "POST /v2/reminders",
        "/reminders",
        lambda c: c.reminders.create(
            ReminderCreate(
                owner_id=UserId(2),
                type=ReminderType.RECURRING,
                reset_type=ReminderResetType.EVENT,
                reminder_days=30,
                opportunity_id=OpportunityId(3),
            )
        ),
    ),
    (
        "POST /v2/reminders/{reminderId}",
        "/reminders/11",
        lambda c: c.reminders.update(
            ReminderIdType(11),
            ReminderUpdate(
                owner_id=UserId(3), content="y", due_date=DUE, reminder_days=7, is_completed=True
            ),
        ),
    ),
    (
        "POST /v2/reminders/{reminderId}",
        "/reminders/11",
        lambda c: c.reminders.update(
            ReminderIdType(11), ReminderUpdate(clear_content=True, is_completed=False)
        ),
    ),
    (
        "POST /v2/notes",
        "/notes",
        lambda c: c.notes.create(
            NoteCreate(
                content="a <b>",
                person_ids=[PersonId(1)],
                company_ids=[CompanyId(2)],
                opportunity_ids=[OpportunityId(3)],
                creator_id=UserId(4),
                created_at=DUE,
            )
        ),
    ),
    (
        "POST /v2/notes",
        "/notes",
        lambda c: c.notes.create(
            NoteCreate(content="<p>r</p>", type=NoteType.HTML, parent_id=NoteId(9))
        ),
    ),
    (
        "POST /v2/notes/{noteId}",
        "/notes/21",
        lambda c: c.notes.update(
            NoteId(21), NoteUpdate(content="z", person_ids=[PersonId(1)], company_ids=[])
        ),
    ),
    (
        "POST /v2/persons",
        "/persons",
        lambda c: c.persons.create(
            PersonCreate(first_name="A", last_name=None, emails=["a@x.co", "b@x.co"]),
            if_not_exists=False,
        ),
    ),
    (
        "POST /v2/persons",
        "/persons",
        lambda c: c.persons.create(
            PersonCreate(first_name="A", last_name="B"), if_not_exists=False
        ),
    ),
    (
        "POST /v2/opportunities/{opportunityId}",
        "/opportunities/41",
        lambda c: c.opportunities.update(OpportunityId(41), OpportunityUpdate(name="N")),
    ),
    (
        "POST /v2/lists",
        "/lists",
        lambda c: c.lists.create(ListCreate(name="L", type=ListType.OPPORTUNITY, is_public=True)),
    ),
    (
        "POST /v2/lists/{listId}/list-entries",
        "/lists/9/list-entries",
        lambda c: c.lists.entries(ListId(9)).add_company(CompanyId(7), creator_id=4),
    ),
]


def _send(path: str, call: Callable[[Affinity], Any]) -> Any:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{V2}{path}").mock(return_value=Response(500))
        # the 500 stops the call after the request is captured
        with Affinity(api_key="k", max_retries=0) as client, contextlib.suppress(Exception):
            call(client)
        assert route.called, f"no request to {path}"
        return json.loads(route.calls[0].request.content)


@pytest.mark.parametrize(
    ("op", "path", "call"), CASES, ids=[f"{c[0]}#{i}" for i, c in enumerate(CASES)]
)
def test_request_body_matches_every_pinned_schema(
    op: str, path: str, call: Callable[[Affinity], Any]
) -> None:
    body = _send(path, call)
    checked = 0
    for version, digest in _digests().items():
        operation = digest["operations"].get(op)
        if operation is None:
            continue
        schema = operation["requestBody"]["content"]["application/json"]
        found = problems(digest["schemas"], schema, body)
        assert not found, f"{op} body {body} vs {version}: {found}"
        checked += 1
    assert checked, f"{op} is in no pinned snapshot"


def test_the_checker_catches_mistakes() -> None:
    digest = _digests()["2026-09-17"]
    schema = digest["operations"]["POST /v2/webhooks"]["requestBody"]["content"]["application/json"]
    schemas = digest["schemas"]
    assert problems(schemas, schema, {"url": "https://x", "subscriptions": []}) == []
    assert any("missing required" in p for p in problems(schemas, schema, {"url": "https://x"}))
    assert any(
        "unknown key" in p
        for p in problems(schemas, schema, {"url": "x", "subscriptions": [], "webhook_url": "x"})
    )
    assert any(
        "not in" in p for p in problems(schemas, schema, {"url": "x", "subscriptions": ["nope"]})
    )
