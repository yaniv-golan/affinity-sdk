from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("rich_click")
pytest.importorskip("rich")
pytest.importorskip("platformdirs")

from click.testing import CliRunner

from affinity.cli.main import cli

# ---------------------------------------------------------------------------
# Validation tests (no API calls needed)
# ---------------------------------------------------------------------------


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_no_bound_rejected() -> None:
    """No --all/--max-results/--list-entry-ids with --list-id -> exit 2."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-id", "42"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    assert "bound" in result.output.lower() or "max-results" in result.output.lower()


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_list_id_and_list_entry_ids_mutual_exclusion() -> None:
    """--list-id and --list-entry-ids together -> exit 2."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "field",
            "history-bulk",
            "field-123",
            "--list-id",
            "42",
            "--list-entry-ids",
            "1,2,3",
            "--all",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower() or "cannot" in result.output.lower()


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_list_entry_ids_with_all_rejected() -> None:
    """--list-entry-ids + --all -> exit 2."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "field",
            "history-bulk",
            "field-123",
            "--list-entry-ids",
            "1,2,3",
            "--all",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 2
    assert "all" in result.output.lower()


# ---------------------------------------------------------------------------
# Functional tests (mock API)
# ---------------------------------------------------------------------------


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_list_entry_ids_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Query specific entries; entityName is null in output."""
    from affinity.models.entities import FieldValueChange

    # Build mock FieldValueChange objects matching API response format
    change_data = {
        "id": 501,
        "fieldId": "field-123",
        "entityId": 100,
        "listEntryId": 10,
        "actionType": 2,
        "value": "Won",
        "changedAt": "2024-06-01T12:00:00Z",
        "changer": {"id": 1, "type": 0, "firstName": "Alice", "lastName": "Smith"},
    }
    mock_change = FieldValueChange.model_validate(change_data)

    # Capture calls to track which entry IDs were queried
    calls: list[int] = []

    async def mock_list(
        self,  # noqa: ARG001
        field_id,  # noqa: ARG001
        *,
        list_entry_id=None,
        action_type=None,  # noqa: ARG001
        **kwargs,  # noqa: ARG001
    ):
        eid = int(list_entry_id)
        calls.append(eid)
        if eid == 10:
            return [mock_change]
        return []

    # Monkeypatch the AsyncFieldValueChangesService.list method
    from affinity.services.v1_only import AsyncFieldValueChangesService

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-entry-ids", "10,20"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    changes = payload["data"]["fieldValueChanges"]
    assert len(changes) == 1
    assert changes[0]["id"] == 501
    assert changes[0]["listEntryId"] == 10
    assert changes[0]["entityName"] is None
    assert changes[0]["actionType"] == "update"
    assert changes[0]["changerName"] == "Alice Smith"
    # Both entries should have been queried
    assert sorted(calls) == [10, 20]


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_dry_run() -> None:
    """Dry run reports estimated API calls without executing."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "field",
            "history-bulk",
            "field-123",
            "--list-entry-ids",
            "10,20,30",
            "--dry-run",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    dry = payload["data"]
    assert dry["dryRun"] is True
    assert dry["entries"] == 3
    assert dry["estimatedApiCalls"] == 3
    assert dry["fieldId"] == "field-123"


# ---------------------------------------------------------------------------
# Additional functional tests
# ---------------------------------------------------------------------------


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_max_results_limits_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """--max-results N with --list-id processes only N entries."""
    from affinity.cli.commands import field_cmds
    from affinity.services.lists import ListService
    from affinity.services.v1_only import AsyncFieldValueChangesService

    calls: list[int] = []

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        eid = int(list_entry_id)
        calls.append(eid)
        return []

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    # Mock list entry resolution to return 5 entries
    class FakeEntry:
        def __init__(self, eid):
            self.id = eid
            self.entity = None

    class FakeEntryService:
        def all(self):
            return [FakeEntry(i) for i in range(1, 6)]

    monkeypatch.setattr(ListService, "entries", lambda self, lid: FakeEntryService())  # noqa: ARG005

    # Also mock resolve_list_selector
    mock_resolved = MagicMock()
    mock_resolved.list.id = 42
    mock_resolved.list.name = "Pipeline"
    monkeypatch.setattr(field_cmds, "resolve_list_selector", lambda **kw: mock_resolved)  # noqa: ARG005

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-id", "42", "--max-results", "3"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    # Should have processed only 3 entries, not all 5
    assert len(calls) == 3


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_all_processes_all_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """--all processes all entries from the list."""
    from affinity.cli.commands import field_cmds
    from affinity.services.lists import ListService
    from affinity.services.v1_only import AsyncFieldValueChangesService

    calls: list[int] = []

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        calls.append(int(list_entry_id))
        return []

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    class FakeEntry:
        def __init__(self, eid):
            self.id = eid
            self.entity = None

    class FakeEntryService:
        def all(self):
            return [FakeEntry(i) for i in range(1, 6)]

    monkeypatch.setattr(ListService, "entries", lambda self, lid: FakeEntryService())  # noqa: ARG005

    mock_resolved = MagicMock()
    mock_resolved.list.id = 42
    mock_resolved.list.name = "Pipeline"
    monkeypatch.setattr(field_cmds, "resolve_list_selector", lambda **kw: mock_resolved)  # noqa: ARG005

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-id", "42", "--all"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 5  # All 5 entries processed


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_output_schema_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Output rows contain all fields from _field_value_change_payload() + entityName."""
    from affinity.models.entities import FieldValueChange
    from affinity.services.v1_only import AsyncFieldValueChangesService

    change = FieldValueChange.model_validate(
        {
            "id": 501,
            "fieldId": "field-123",
            "entityId": 100,
            "listEntryId": 10,
            "actionType": 2,
            "value": "Won",
            "changedAt": "2024-06-01T12:00:00Z",
            "changer": {"id": 1, "type": 0, "firstName": "Alice", "lastName": "Smith"},
        }
    )

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        return [change]

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-entry-ids", "10"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    row = payload["data"]["fieldValueChanges"][0]

    expected_keys = {
        "id",
        "fieldId",
        "entityId",
        "listEntryId",
        "actionType",
        "value",
        "changedAt",
        "changerName",
        "changer",
        "entityName",
    }
    assert set(row.keys()) == expected_keys


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_action_type_filter_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """--action-type is forwarded to the API call."""
    from affinity.services.v1_only import AsyncFieldValueChangesService
    from affinity.types import FieldValueChangeAction

    captured_action_types: list = []

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        captured_action_types.append(action_type)
        return []

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "field",
            "history-bulk",
            "field-123",
            "--list-entry-ids",
            "10",
            "--action-type",
            "update",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    assert len(captured_action_types) == 1
    assert captured_action_types[0] == FieldValueChangeAction.UPDATE


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_partial_failure_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """One entry failing doesn't block others; failure appears as warning."""
    from affinity.models.entities import FieldValueChange
    from affinity.services.v1_only import AsyncFieldValueChangesService

    change = FieldValueChange.model_validate(
        {
            "id": 501,
            "fieldId": "field-123",
            "entityId": 100,
            "listEntryId": 10,
            "actionType": 2,
            "value": "Won",
            "changedAt": "2024-06-01T12:00:00Z",
            "changer": {"id": 1, "type": 0, "firstName": "Alice", "lastName": "Smith"},
        }
    )

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        eid = int(list_entry_id)
        if eid == 20:
            raise Exception("404 Not Found")
        return [change]

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-entry-ids", "10,20"],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    # Entry 10 should succeed
    assert len(payload["data"]["fieldValueChanges"]) == 1
    # Warnings should contain failure info
    warnings = payload.get("warnings", [])
    assert any("20" in w for w in warnings)
    assert any("succeeded" in w.lower() for w in warnings)


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_list_entry_ids_ignores_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """--list-entry-ids + --max-results: --max-results is silently ignored."""
    from affinity.services.v1_only import AsyncFieldValueChangesService

    calls: list[int] = []

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        calls.append(int(list_entry_id))
        return []

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "field",
            "history-bulk",
            "field-123",
            "--list-entry-ids",
            "10,20,30",
            "--max-results",
            "1",
        ],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    assert result.exit_code == 0, result.output
    # All 3 entries should be processed despite --max-results 1
    assert sorted(calls) == [10, 20, 30]


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_concurrency_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """XAFFINITY_CONCURRENCY env var controls concurrency."""
    from affinity.cli.query.executor import RateLimitedExecutor
    from affinity.services.v1_only import AsyncFieldValueChangesService

    captured_concurrency: list[int] = []
    original_init = RateLimitedExecutor.__init__

    def tracking_init(self, concurrency=15):
        captured_concurrency.append(concurrency)
        original_init(self, concurrency)

    monkeypatch.setattr(RateLimitedExecutor, "__init__", tracking_init)

    async def mock_list(self, field_id, *, list_entry_id=None, action_type=None, **kwargs):  # noqa: ARG001
        return []

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", mock_list)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-entry-ids", "10"],
        env={"AFFINITY_API_KEY": "test-key", "XAFFINITY_CONCURRENCY": "5"},
    )
    assert result.exit_code == 0, result.output
    assert 5 in captured_concurrency


# ---------------------------------------------------------------------------
# Field-wide strategy (list fields, --all)
# ---------------------------------------------------------------------------


def _change(cid: int, entry: int | None, when: str) -> object:
    from affinity.models.entities import FieldValueChange

    return FieldValueChange.model_validate(
        {
            "id": cid,
            "field_id": 123,
            "entity_id": 900 + (entry or 0),
            "list_entry_id": entry,
            "action_type": 2,
            "value": "Won",
            "changed_at": when,
            "changer": None,
        }
    )


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    entries: int,
    field_type: str = "list",
    field_id: object = "field-123",
    iter_all: object = None,
) -> dict[str, list[object]]:
    from affinity.cli.commands import field_cmds
    from affinity.services.lists import ListService
    from affinity.services.v1_only import AsyncFieldValueChangesService, FieldValueChangesService

    seen: dict[str, list[object]] = {"per_entry": [], "field_wide": [], "get_fields": []}

    async def per_entry(self, field_id, *, list_entry_id=None, action_type=None, **kw):  # noqa: ARG001
        seen["per_entry"].append(int(list_entry_id))
        return [_change(5000 + int(list_entry_id), int(list_entry_id), "2025-01-02T00:00:00Z")]

    def field_wide(self, field_id, *, action_type=None, page_size=100, **kw):  # noqa: ARG001
        seen["field_wide"].append((str(field_id), action_type, page_size))
        if iter_all is not None:
            yield from iter_all()  # type: ignore[operator]
            return
        for eid in range(1, entries + 1):
            yield _change(5000 + eid, eid, "2025-01-02T00:00:00Z")
        yield _change(9999, 99999, "2025-01-03T00:00:00Z")  # entry no longer on the list

    monkeypatch.setattr(AsyncFieldValueChangesService, "list", per_entry)
    monkeypatch.setattr(FieldValueChangesService, "iter_all", field_wide)

    class FakeField:
        def __init__(self) -> None:
            self.id = field_id
            self.type = field_type

    def get_fields(self, list_id, **kw):  # noqa: ARG001
        seen["get_fields"].append(int(list_id))
        return [FakeField()]

    monkeypatch.setattr(ListService, "get_fields", get_fields)

    class FakeEntry:
        def __init__(self, eid: int) -> None:
            self.id = eid
            self.entity = None

    class FakeEntryService:
        def all(self):
            return [FakeEntry(i) for i in range(1, entries + 1)]

    monkeypatch.setattr(ListService, "entries", lambda self, lid: FakeEntryService())  # noqa: ARG005
    resolved = MagicMock()
    resolved.list.id = 42
    resolved.list.name = "Pipeline"
    monkeypatch.setattr(field_cmds, "resolve_list_selector", lambda **kw: resolved)  # noqa: ARG005
    return seen


def _bulk(*extra: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    result = CliRunner().invoke(
        cli,
        ["--json", "field", "history-bulk", "field-123", "--list-id", "42", *extra],
        env={"AFFINITY_API_KEY": "test-key"},
    )
    return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_big_list_with_a_list_field_reads_field_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _setup(monkeypatch, entries=120)
    code, out = _bulk("--all", "--action-type", "update")
    assert code == 0, out
    assert seen["per_entry"] == []
    assert seen["field_wide"] == [("field-123", 2, 500)]
    rows = out["data"]["fieldValueChanges"]
    assert len(rows) == 120  # the change on entry 99999 (not on the list) is dropped
    assert any("no longer on the list" in w for w in out["warnings"])
    assert out["command"]["modifiers"]["strategy"] == "field"
    assert rows == sorted(rows, key=lambda r: (r["changedAt"], r["id"]))


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_field_wide_rows_match_per_entry_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, entries=100)
    _, field = _bulk("--all", "--strategy", "field")
    _setup(monkeypatch, entries=100)
    _, entries = _bulk("--all", "--strategy", "entries")
    assert field["data"]["fieldValueChanges"] == entries["data"]["fieldValueChanges"]


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_small_list_global_field_and_max_results_stay_per_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _setup(monkeypatch, entries=20)
    assert _bulk("--all")[0] == 0
    assert seen["field_wide"] == [] and seen["get_fields"] == [] and len(seen["per_entry"]) == 20

    seen = _setup(monkeypatch, entries=150, field_type="global")
    code, out = _bulk("--all")
    assert code == 0 and seen["field_wide"] == [] and len(seen["per_entry"]) == 150
    assert out["command"]["modifiers"]["strategy"] == "entries"

    seen = _setup(monkeypatch, entries=150)
    assert _bulk("--max-results", "150")[0] == 0
    assert seen["get_fields"] == [] and seen["field_wide"] == []


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_field_ids_compare_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _setup(monkeypatch, entries=120, field_id=123)
    assert _bulk("--all")[0] == 0
    assert seen["field_wide"]


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_strategy_field_needs_list_all_and_a_list_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, entries=10)
    code, out = _bulk("--max-results", "5", "--strategy", "field")
    assert code == 2 and "--all" in out["error"]["message"]
    _setup(monkeypatch, entries=10, field_type="global")
    code, out = _bulk("--all", "--strategy", "field")
    assert code == 2 and "--strategy entries" in out["error"]["message"]


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_failure_before_any_data_falls_back_to_per_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    from affinity.exceptions import ValidationError

    def boom():
        raise ValidationError("bad", status_code=422)
        yield  # pragma: no cover

    seen = _setup(monkeypatch, entries=120, iter_all=boom)
    code, out = _bulk("--all")
    assert code == 0, out
    assert len(seen["per_entry"]) == 120
    assert any("fetching per entry instead" in w for w in out["warnings"])
    assert out["command"]["modifiers"]["strategy"] == "entries"


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_forced_field_never_falls_back_to_per_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """--strategy field is a promise of a few calls (MCP allows --all only with it)."""
    from affinity.exceptions import ValidationError

    def boom():
        raise ValidationError("bad", status_code=422)
        yield  # pragma: no cover

    seen = _setup(monkeypatch, entries=120, iter_all=boom)
    code, out = _bulk("--all", "--strategy", "field")
    assert code == 1, out
    assert seen["per_entry"] == []
    assert "--strategy entries" in out["error"]["hint"]


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_forced_field_when_the_fields_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from affinity.exceptions import ServerError
    from affinity.services.lists import ListService

    seen = _setup(monkeypatch, entries=120)

    def broken(self, list_id, **kw):  # noqa: ARG001
        raise ServerError("down", status_code=503)

    monkeypatch.setattr(ListService, "get_fields", broken)
    code, out = _bulk("--all", "--strategy", "field")
    assert code == 1, out
    assert seen["per_entry"] == [] and seen["field_wide"] == []
    assert "could not read" in out["error"]["message"].lower()


def _broken_get_fields(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    from affinity.services.lists import ListService

    def broken(self, list_id, **kw):  # noqa: ARG001
        raise exc

    monkeypatch.setattr(ListService, "get_fields", broken)


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_forced_field_dry_run_reports_the_fields_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from affinity.exceptions import ServerError

    _setup(monkeypatch, entries=120)
    _broken_get_fields(monkeypatch, ServerError("down", status_code=503))
    code, out = _bulk("--all", "--strategy", "field", "--dry-run")
    assert code == 1, out


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_auto_falls_back_when_the_fields_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from affinity.exceptions import ServerError

    seen = _setup(monkeypatch, entries=120)
    _broken_get_fields(monkeypatch, ServerError("down", status_code=503))
    code, out = _bulk("--all")
    assert code == 0, out
    assert len(seen["per_entry"]) == 120
    assert any("fetching per entry" in w for w in out["warnings"])


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_forced_field_auth_error_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from affinity.exceptions import AuthenticationError

    def denied():
        raise AuthenticationError("no", status_code=401)
        yield  # pragma: no cover

    seen = _setup(monkeypatch, entries=120, iter_all=denied)
    code, _ = _bulk("--all", "--strategy", "field")
    assert code != 0
    assert seen["per_entry"] == []


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_failure_after_data_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from affinity.exceptions import ServerError

    def partial():
        yield _change(1, 1, "2025-01-01T00:00:00Z")
        raise ServerError("down", status_code=503)

    seen = _setup(monkeypatch, entries=120, iter_all=partial)
    code, out = _bulk("--all")
    assert code == 1
    assert "--strategy entries" in out["error"]["hint"]
    assert seen["per_entry"] == []


@pytest.mark.req("CLI-FIELD-HISTORY-BULK")
def test_dry_run_reports_strategy_and_a_numeric_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _setup(monkeypatch, entries=120)
    code, out = _bulk("--all", "--dry-run")
    assert code == 0, out
    data = out["data"]
    assert data["strategy"] == "field" and data["estimatedApiCalls"] == 120
    assert "estimatedApiCallsNote" in data
    assert seen["field_wide"] == [] and seen["per_entry"] == []
