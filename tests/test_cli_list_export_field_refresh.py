"""list export --field: a field missing from stale V1 metadata is found after one refresh."""

from __future__ import annotations

import pytest

from affinity.cli.commands.list_cmds import _select_fields_refreshing
from affinity.cli.errors import CLIError
from affinity.models.entities import FieldMetadata


def _meta(fid: str, name: str) -> FieldMetadata:
    return FieldMetadata.model_validate({"id": fid, "name": name, "valueType": "text"})


STALE = [_meta("field-100", "Status")]
FRESH = [*STALE, _meta("field-200", "Close Date")]


def test_known_field_needs_no_refresh() -> None:
    calls: list[int] = []
    selected, meta = _select_fields_refreshing(
        fields=("Status",), field_meta=STALE, refresh=lambda: calls.append(1) or FRESH
    )
    assert selected == ["field-100"]
    assert meta is STALE
    assert calls == []


@pytest.mark.parametrize("spec", ["Close Date", "field-200"])
def test_field_missing_from_stale_metadata_found_after_refresh(spec: str) -> None:
    selected, meta = _select_fields_refreshing(
        fields=("Status", spec), field_meta=STALE, refresh=lambda: FRESH
    )
    assert selected == ["field-100", "field-200"]
    assert meta is FRESH


def test_unknown_field_still_errors_after_refresh() -> None:
    with pytest.raises(CLIError, match="Unknown field"):
        _select_fields_refreshing(fields=("Nope",), field_meta=STALE, refresh=lambda: FRESH)
