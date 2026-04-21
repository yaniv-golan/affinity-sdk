"""_entry_to_filter_dict must expose row-level keys CLI outputs."""

from __future__ import annotations

import pytest

from affinity.filters import F
from affinity.services.lists import _entry_to_filter_dict


def _make_entry_stub():
    """Build a minimal ListEntryWithEntity duck-type for filter tests.

    If this breaks because ListEntryWithEntity is a strict Pydantic model,
    replace with a model_validate() call on a JSON dict — mirror the pattern
    in tests/test_services_lists_additional_coverage.py.
    """
    from types import SimpleNamespace

    entity = SimpleNamespace(
        id=555,
        name="Fusion Mantle",
        fields_raw=[],
    )
    return SimpleNamespace(
        id=901,
        list_id=10,
        entity=entity,
        type="company",  # ListEntryWithEntity.type — same attribute _entry_to_row reads
    )


@pytest.mark.req("REQ-EXPORT-FILTER-DICT-001")
def test_filter_dict_includes_entity_name():
    entry = _make_entry_stub()
    d = _entry_to_filter_dict(entry)
    assert d["entityName"] == "Fusion Mantle"


@pytest.mark.req("REQ-EXPORT-FILTER-DICT-002")
def test_filter_dict_includes_entity_id_and_type():
    entry = _make_entry_stub()
    d = _entry_to_filter_dict(entry)
    assert d["entityId"] == 555
    assert d["entityType"] == "company"


@pytest.mark.req("REQ-EXPORT-FILTER-DICT-003")
def test_filter_dict_includes_list_entry_id():
    entry = _make_entry_stub()
    d = _entry_to_filter_dict(entry)
    assert d["listEntryId"] == 901


@pytest.mark.req("REQ-EXPORT-FILTER-DICT-004")
def test_filter_matches_entity_name_via_F():
    """End-to-end: `entityName =~ "Fusion"` matches the stub."""
    entry = _make_entry_stub()
    expr = F.field("entityName").contains("Fusion")
    assert expr.matches(_entry_to_filter_dict(entry)) is True
