"""Pre-validation + no-op-short-circuit tests for `field --set/--append`.

Covers CLI-SET-PHASE-ATOMICITY:

- Client-side-validatable failures (bad person id, unknown dropdown option,
  etc.) abort BEFORE any API write. No partial commit on retries.
- No-op short-circuit: if the new value already matches existing, neither
  DELETE nor POST is issued (clean audit log on agent retries).

Each test name reads as a single sentence describing the invariant.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

try:
    import respx
except ModuleNotFoundError:  # pragma: no cover
    respx = None  # type: ignore[assignment]

from click.testing import CliRunner
from httpx import Response

from affinity.cli.main import cli

if respx is None:  # pragma: no cover
    pytest.skip("respx is not installed", allow_module_level=True)


LIST_ID = 67890
ENTRY_ID = 123

LIST_RESPONSE = {
    "id": LIST_ID,
    "name": "Portfolio",
    "type": 0,
    "isPublic": False,
    "ownerId": 100,
    "creatorId": 100,
}

FIELDS_RESPONSE = [
    {
        "id": "field-100",
        "name": "Status",
        "valueType": "dropdown",
        "allowsMultiple": False,
        "dropdownOptions": [
            {"id": 200, "text": "Active"},
            {"id": 201, "text": "Closed"},
        ],
    },
    {
        "id": "field-101",
        "name": "Priority",
        "valueType": "dropdown",
        "allowsMultiple": False,
        "dropdownOptions": [
            {"id": 300, "text": "High"},
            {"id": 301, "text": "Low"},
        ],
    },
    {
        "id": "field-103",
        "name": "Owner",
        "valueType": "person",
        "allowsMultiple": False,
    },
    {
        "id": "field-104",
        "name": "Investors",
        "valueType": "person",
        "allowsMultiple": True,
    },
    {
        "id": "field-102",
        "name": "Tags",
        "valueType": "dropdown",
        "allowsMultiple": True,
        "dropdownOptions": [
            {"id": 400, "text": "A"},
            {"id": 401, "text": "B"},
            {"id": 402, "text": "C"},
        ],
    },
]

FIELDS_RESPONSE_V1 = [
    {
        "id": "field-100",
        "name": "Status",
        "value_type": 2,
        "allows_multiple": False,
        "dropdown_options": [
            {"id": 200, "text": "Active"},
            {"id": 201, "text": "Closed"},
        ],
    },
    {
        "id": "field-101",
        "name": "Priority",
        "value_type": 2,
        "allows_multiple": False,
        "dropdown_options": [
            {"id": 300, "text": "High"},
            {"id": 301, "text": "Low"},
        ],
    },
    {
        "id": "field-103",
        "name": "Owner",
        "value_type": 0,
        "allows_multiple": False,
    },
    {
        "id": "field-104",
        "name": "Investors",
        "value_type": 0,
        "allows_multiple": True,
    },
    {
        "id": "field-102",
        "name": "Tags",
        "value_type": 2,
        "allows_multiple": True,
        "dropdown_options": [
            {"id": 400, "text": "A"},
            {"id": 401, "text": "B"},
            {"id": 402, "text": "C"},
        ],
    },
]


def _setup_list_mocks(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.affinity.co/v2/lists").mock(
        return_value=Response(200, json={"data": [LIST_RESPONSE], "pagination": {}})
    )
    respx_mock.get(f"https://api.affinity.co/lists/{LIST_ID}").mock(
        return_value=Response(
            200,
            json={
                "id": LIST_ID,
                "name": "Portfolio",
                "type": 0,
                "public": False,
                "owner_id": 100,
                "creator_id": 100,
                "list_size": 100,
            },
        )
    )
    respx_mock.get(f"https://api.affinity.co/v2/lists/{LIST_ID}/fields").mock(
        return_value=Response(200, json={"data": FIELDS_RESPONSE, "pagination": {}})
    )
    respx_mock.get("https://api.affinity.co/fields").mock(
        return_value=Response(200, json={"data": FIELDS_RESPONSE_V1})
    )


def _existing_status_active(respx_mock: respx.MockRouter) -> None:
    """Existing field-100 = Active (id 200)."""
    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 999,
                    "fieldId": "field-100",
                    "entityId": 224925,
                    "value": {"id": 200, "text": "Active"},
                }
            ],
        )
    )


# ============================================================================
# Pre-validation: invalid person id aborts before any write
# ============================================================================


@pytest.mark.req("CLI-SET-PHASE-ATOMICITY")
def test_entry_field_invalid_person_aborts_before_any_write(
    respx_mock: respx.MockRouter,
) -> None:
    """--set Status=Active --set Owner="Jane Doe" → exit 2, no DELETE/POST."""
    _setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    delete_route = respx_mock.delete(url__regex=r"https://api\.affinity\.co/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    post_route = respx_mock.post(
        url__regex=r"https://api\.affinity\.co/v2/lists/\d+/list-entries/\d+/fields/.+"
    ).mock(return_value=Response(200, json={}))

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--set",
            "Owner",
            "Jane Doe",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2, result.output
    assert not delete_route.called
    assert not post_route.called
    # Hint mentions the right escape hatch.
    assert "person ls" in result.output or "person ls" in (result.stderr or "")


@pytest.mark.req("CLI-SET-PHASE-ATOMICITY")
def test_entry_field_invalid_person_reverse_order_also_aborts(
    respx_mock: respx.MockRouter,
) -> None:
    """Pre-validation is order-independent: Owner first, Status second still aborts."""
    _setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    delete_route = respx_mock.delete(url__regex=r".*/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    post_route = respx_mock.post(url__regex=r".*/list-entries/\d+/fields/.+").mock(
        return_value=Response(200, json={})
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Owner",
            "Jane Doe",
            "--set",
            "Status",
            "Active",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2, result.output
    assert not delete_route.called
    assert not post_route.called


@pytest.mark.req("CLI-SET-PHASE-ATOMICITY")
def test_entry_field_invalid_person_in_append_aborts(
    respx_mock: respx.MockRouter,
) -> None:
    """--set Status=Active --append Investors "Some Name" → aborts before any write."""
    _setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    delete_route = respx_mock.delete(url__regex=r".*/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    post_route = respx_mock.post(url__regex=r".*/list-entries/\d+/fields/.+").mock(
        return_value=Response(200, json={})
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
            "--append",
            "Investors",
            "Some Name",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2, result.output
    assert not delete_route.called
    assert not post_route.called


@pytest.mark.req("CLI-SET-PHASE-ATOMICITY")
def test_entry_field_setjson_invalid_value_aborts(
    respx_mock: respx.MockRouter,
) -> None:
    """--set-json {"Owner": "Jane Doe"} flows through pre-validation."""
    _setup_list_mocks(respx_mock)
    respx_mock.get("https://api.affinity.co/field-values").mock(return_value=Response(200, json=[]))

    delete_route = respx_mock.delete(url__regex=r".*/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    post_route = respx_mock.post(url__regex=r".*/list-entries/\d+/fields/.+").mock(
        return_value=Response(200, json={})
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set-json",
            json.dumps({"Owner": "Jane Doe"}),
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 2, result.output
    assert not delete_route.called
    assert not post_route.called


# ============================================================================
# No-op short-circuit
# ============================================================================


@pytest.mark.req("CLI-SET-PHASE-ATOMICITY")
def test_entry_field_noop_set_skips_writes(respx_mock: respx.MockRouter) -> None:
    """--set Status=Active on entry that already has Status=Active → no DELETE/POST."""
    _setup_list_mocks(respx_mock)
    _existing_status_active(respx_mock)

    delete_route = respx_mock.delete(url__regex=r".*/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    post_route = respx_mock.post(url__regex=r".*/list-entries/\d+/fields/.+").mock(
        return_value=Response(200, json={})
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert not delete_route.called
    assert not post_route.called


@pytest.mark.req("CLI-SET-PHASE-ATOMICITY")
def test_entry_field_partial_noop(respx_mock: respx.MockRouter) -> None:
    """One field is a no-op, the other writes — only the writing field hits the API."""
    _setup_list_mocks(respx_mock)
    _existing_status_active(respx_mock)

    delete_route = respx_mock.delete(url__regex=r".*/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    status_post = respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-100"
    ).mock(
        return_value=Response(
            200,
            json={
                "id": 1001,
                "fieldId": "field-100",
                "entityId": 224925,
                "value": {"id": 200, "text": "Active"},
            },
        )
    )
    priority_post = respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-101"
    ).mock(
        return_value=Response(
            200,
            json={
                "id": 1002,
                "fieldId": "field-101",
                "entityId": 224925,
                "value": {"id": 300, "text": "High"},
            },
        )
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Status",
            "Active",  # already Active → no-op
            "--set",
            "Priority",
            "High",  # new value → write
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert not delete_route.called  # nothing to delete for Priority (no existing)
    assert not status_post.called
    assert priority_post.called


@pytest.mark.req("CLI-SET-PHASE-ATOMICITY")
def test_entry_field_set_multi_subset_writes(respx_mock: respx.MockRouter) -> None:
    """--set Tags A when existing is [A,B,C] IS a write (REPLACE drops B/C)."""
    _setup_list_mocks(respx_mock)

    respx_mock.get("https://api.affinity.co/field-values").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "fieldId": "field-102",
                    "entityId": 224925,
                    "value": {"id": 400, "text": "A"},
                },
                {
                    "id": 2,
                    "fieldId": "field-102",
                    "entityId": 224925,
                    "value": {"id": 401, "text": "B"},
                },
                {
                    "id": 3,
                    "fieldId": "field-102",
                    "entityId": 224925,
                    "value": {"id": 402, "text": "C"},
                },
            ],
        )
    )

    delete_route = respx_mock.delete(url__regex=r".*/field-values/\d+").mock(
        return_value=Response(200, json={"success": True})
    )
    post_route = respx_mock.post(
        f"https://api.affinity.co/v2/lists/{LIST_ID}/list-entries/{ENTRY_ID}/fields/field-102"
    ).mock(
        return_value=Response(
            200,
            json={
                "id": 9,
                "fieldId": "field-102",
                "entityId": 224925,
                "value": [{"id": 400, "text": "A"}],
            },
        )
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "entry",
            "field",
            "Portfolio",
            str(ENTRY_ID),
            "--set",
            "Tags",
            "A",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    # All three existing rows must be removed; the new value is a write.
    assert delete_route.call_count == 3
    assert post_route.called


# ============================================================================
# Note dedup warning
# ============================================================================


def _setup_note_dedup_mocks(
    respx_mock: respx.MockRouter,
    *,
    user_id: int = 42,
    existing_note: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a dict with refs to the registered routes so tests can assert on them."""
    whoami_route = respx_mock.get("https://api.affinity.co/v2/auth/whoami").mock(
        return_value=Response(
            200,
            json={
                "tenant": {"id": 1, "name": "Acme", "subdomain": "acme"},
                "user": {
                    "id": user_id,
                    "firstName": "Test",
                    "lastName": "User",
                    "emailAddress": "test@example.com",
                    "type": 0,
                    "role": 0,
                },
                "grant": {
                    "type": "api_key",
                    "scopes": [],
                    "createdAt": "2024-01-01T00:00:00Z",
                },
            },
        )
    )
    notes_list_payload: dict[str, Any] = {"notes": [], "next_page_token": None}
    if existing_note is not None:
        notes_list_payload["notes"] = [existing_note]
    notes_list_route = respx_mock.get("https://api.affinity.co/notes").mock(
        return_value=Response(200, json=notes_list_payload)
    )
    notes_create_route = respx_mock.post("https://api.affinity.co/notes").mock(
        return_value=Response(
            200,
            json={
                "id": 999,
                "creator_id": user_id,
                "content": "Hello",
                "type": 0,
                "person_ids": [3],
                "organization_ids": [],
                "opportunity_ids": [],
                "created_at": "2024-01-01T00:00:00Z",
            },
        )
    )
    return {
        "whoami": whoami_route,
        "notes_list": notes_list_route,
        "notes_create": notes_create_route,
    }


@pytest.mark.req("CLI-NOTE-DUPLICATE-WARN")
def test_note_create_warns_on_duplicate(respx_mock: respx.MockRouter) -> None:
    """A recent identical note by the same author triggers a warning, still creates."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    routes = _setup_note_dedup_mocks(
        respx_mock,
        existing_note={
            "id": 555,
            "creator_id": 42,
            "content": "Hello world",
            "type": 0,
            "personIds": [3],
            "organizationIds": [],
            "opportunityIds": [],
            "createdAt": now,
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "note",
            "create",
            "--content",
            "Hello world",
            "--person-id",
            "3",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert routes["whoami"].called
    assert routes["notes_list"].called
    assert routes["notes_create"].called  # note is still created
    payload = json.loads(result.output.strip())
    warnings_list = payload.get("warnings", [])
    assert any("Possible duplicate" in w for w in warnings_list), warnings_list


@pytest.mark.req("CLI-NOTE-DUPLICATE-WARN")
def test_note_create_dedup_uses_explicit_creator_id(
    respx_mock: respx.MockRouter,
) -> None:
    """When --creator-id is provided, whoami is NOT called; lookup uses the override."""
    routes = _setup_note_dedup_mocks(respx_mock)

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "note",
            "create",
            "--content",
            "Hello",
            "--person-id",
            "3",
            "--creator-id",
            "99",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert not routes["whoami"].called
    # notes.list was called scoped to creator_id=99.
    assert routes["notes_list"].called
    req = routes["notes_list"].calls[0].request
    assert "creator_id=99" in str(req.url)


@pytest.mark.req("CLI-NOTE-DUPLICATE-WARN")
def test_note_create_skip_duplicate_check_flag(
    respx_mock: respx.MockRouter,
) -> None:
    """--skip-duplicate-check disables both whoami and notes.list reads."""
    routes = _setup_note_dedup_mocks(respx_mock)

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "note",
            "create",
            "--content",
            "Hello",
            "--person-id",
            "3",
            "--skip-duplicate-check",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert not routes["whoami"].called
    assert not routes["notes_list"].called
    assert routes["notes_create"].called


@pytest.mark.req("CLI-NOTE-DUPLICATE-WARN")
def test_note_create_dedup_outside_window_does_not_warn(
    respx_mock: respx.MockRouter,
) -> None:
    """A duplicate from 10 minutes ago is outside the 5-minute window → no warning."""
    routes = _setup_note_dedup_mocks(
        respx_mock,
        existing_note={
            "id": 555,
            "creator_id": 42,
            "content": "Hello world",
            "type": 0,
            "personIds": [3],
            "organizationIds": [],
            "opportunityIds": [],
            "createdAt": "2020-01-01T00:00:00Z",  # ancient
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "note",
            "create",
            "--content",
            "Hello world",
            "--person-id",
            "3",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    assert routes["notes_create"].called
    payload = json.loads(result.output.strip())
    warnings_list = payload.get("warnings", [])
    assert not any("Possible duplicate" in w for w in warnings_list)
