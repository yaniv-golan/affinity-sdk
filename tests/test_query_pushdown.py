"""Unit tests: which part of a query WHERE clause goes to Affinity's search."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from affinity.cli.query.models import WhereClause
from affinity.cli.query.pushdown import ALL_MAPPINGS, VERIFIED, split_where
from affinity.criteria import Condition, FilterGroup, SearchCriteria
from affinity.models.entities import DropdownOption, FieldMetadata


def _field(fid: str, name: str, value_type: str, ops: list[str] | None) -> FieldMetadata:
    filterability = (
        None
        if ops is None
        else {
            "filterableFieldType": "field-only",
            "operators": [{"id": o, "name": o, "numberOfValuesRequired": "one"} for o in ops],
        }
    )
    return FieldMetadata.model_validate(
        {
            "id": fid,
            "name": name,
            "type": "list",
            "valueType": value_type,
            "enrichmentSource": None,
            "filterability": filterability,
        }
    )


DROPDOWN_OPS = ["is-any-of", "is-none-of", "is-empty", "is-not-empty"]
FIELDS = [
    _field("field-1", "Status", "ranked-dropdown", DROPDOWN_OPS),
    _field(
        "field-2", "Notes", "text", ["contains", "does-not-contain", "is-empty", "is-not-empty"]
    ),
    _field(
        "field-3",
        "Amount",
        "number",
        [
            "is-equal-to",
            "is-greater-than",
            "is-greater-than-or-equal-to",
            "is-less-than",
            "is-less-than-or-equal-to",
            "is-between",
            "is-not-empty",
        ],
    ),
    _field("field-4", "Close", "datetime", ["is-after", "is-before", "is-between", "is-not-empty"]),
    _field("field-5", "Owners", "person-multi", ["has-any-of", "is-not-empty", "is-empty"]),
    _field("field-6", "Last email", "interaction", None),
    _field("field-7", "Size", "filterable-text", ["is-any-of", "is-none-of", "is-not-empty"]),
]
OPTIONS = {
    "field-1": [
        DropdownOption(id=11, text="Lead"),
        DropdownOption(id=12, text="Won"),
        DropdownOption(id=13, text="Won"),  # two options can share a text
        DropdownOption(id=14, text="Lost"),
    ]
}


def _split(where: dict[str, Any], verified: frozenset[tuple[str, str]] = ALL_MAPPINGS) -> Any:
    return split_where(
        WhereClause.model_validate(where),
        FIELDS,
        lambda meta: OPTIONS.get(str(meta.id), []),
        verified=verified,
    )


def _body(where: Condition | FilterGroup | None) -> dict[str, Any]:
    return SearchCriteria(where=where).to_body()["filters"]


def _only(split: Any) -> dict[str, Any]:
    filters = _body(split.where)["filters"]
    assert len(filters) == 1
    return dict(filters[0])


def test_dropdown_text_becomes_every_option_id_with_that_text() -> None:
    split = _split({"path": "fields.Status", "op": "eq", "value": "Won"})
    assert _only(split) == {
        "valueType": "ranked-dropdown",
        "fieldId": "field-1",
        "operator": "is-any-of",
        "value": [{"dropdownOptionId": 12}, {"dropdownOptionId": 13}],
    }
    assert split.complete


def test_dropdown_contains_and_in_match_option_texts() -> None:
    contains = _only(_split({"path": "fields.status", "op": "contains", "value": "o"}))
    assert [v["dropdownOptionId"] for v in contains["value"]] == [12, 13, 14]
    within = _only(_split({"path": "fields.Status", "op": "in", "value": ["Lead", "Lost"]}))
    assert [v["dropdownOptionId"] for v in within["value"]] == [11, 14]


def test_dropdown_not_equal_is_none_of_and_unknown_text_stays_local() -> None:
    assert _only(_split({"path": "fields.Status", "op": "neq", "value": "Won"}))["operator"] == (
        "is-none-of"
    )
    assert _only(_split({"not": {"path": "fields.Status", "op": "eq", "value": "Lead"}}))[
        "value"
    ] == [{"dropdownOptionId": 11}]
    unknown = _split({"path": "fields.Status", "op": "eq", "value": "Nope"})
    assert unknown.where is None and "no dropdown option matches" in unknown.local[0]


@pytest.mark.parametrize("op", ["eq", "contains", "starts_with", "ends_with"])
def test_every_text_test_is_sent_as_contains(op: str) -> None:
    assert _only(_split({"path": "fields.Notes", "op": op, "value": "Acme"})) == {
        "valueType": "text",
        "fieldId": "field-2",
        "operator": "contains",
        "value": "Acme",
    }


def test_numbers_accept_text_values_from_filter_strings() -> None:
    assert _only(_split({"path": "fields.Amount", "op": "gte", "value": "1000000"}))["value"] == (
        1000000
    )
    assert _only(_split({"path": "fields.Amount", "op": "between", "value": [1, 2.5]}))[
        "value"
    ] == [1, 2.5]
    assert _split({"path": "fields.Amount", "op": "gt", "value": "lots"}).where is None


def test_dates_are_widened_to_whole_days() -> None:
    after = _only(_split({"path": "fields.Close", "op": "gt", "value": "2026-03-10"}))
    assert after == {
        "valueType": "date",
        "fieldId": "field-4",
        "operator": "is-after",
        "value": "2026-03-08",
    }
    before = _only(_split({"path": "fields.Close", "op": "lte", "value": "2026-03-10T15:00:00"}))
    assert before["operator"] == "is-before" and before["value"] == "2026-03-12"
    on = _only(_split({"path": "fields.Close", "op": "eq", "value": "2026-03-10"}))
    assert on["value"] == ["2026-03-09", "2026-03-11"]


def test_relative_dates_become_calendar_days() -> None:
    split = _only(_split({"path": "fields.Close", "op": "gte", "value": "-30d"}))
    assert split["operator"] == "is-after"
    assert date.fromisoformat(split["value"]) < date.today()


def test_and_sends_what_it_can_and_keeps_the_rest_local() -> None:
    split = _split(
        {
            "and": [
                {"path": "fields.Status", "op": "eq", "value": "Lead"},
                {"path": "entityName", "op": "contains", "value": "Acme"},
                {"path": "fields.Last email", "op": "is_not_null"},
            ]
        }
    )
    assert len(_body(split.where)["filters"]) == 1
    assert not split.complete and len(split.local) == 2


def test_or_goes_whole_or_not_at_all() -> None:
    both = _split(
        {
            "or": [
                {"path": "fields.Status", "op": "eq", "value": "Lead"},
                {"path": "fields.Amount", "op": "gt", "value": 5},
            ]
        }
    )
    assert _body(both.where)["operator"] == "or"
    partial = _split(
        {
            "or": [
                {"path": "fields.Status", "op": "eq", "value": "Lead"},
                {"path": "entityName", "op": "eq", "value": "Acme"},
            ]
        }
    )
    assert partial.where is None and partial.pushed == []


@pytest.mark.parametrize(
    "where",
    [
        {"path": "fields.Status", "op": "is_null"},
        {"path": "fields.Owners", "op": "is_not_null"},  # [] is "not null" locally
        {"path": "fields.Last email", "op": "eq", "value": "x"},
        {"path": "fields.Unknown", "op": "eq", "value": "x"},
        {"path": "fields.Notes", "op": "neq", "value": "x"},
        {"not": {"path": "fields.Amount", "op": "gt", "value": 5}},
        {"all": {"path": "persons", "where": {"path": "name", "op": "eq", "value": "x"}}},
        {"path": "fields.Status", "op": "contains_any", "value": ["a"]},
    ],
)
def test_conditions_that_stay_local(where: dict[str, Any]) -> None:
    split = _split(where)
    assert split.where is None and split.local


def test_unverified_mappings_stay_local() -> None:
    split = _split({"path": "fields.Amount", "op": "gt", "value": 5}, verified=frozenset())
    assert split.where is None and "not verified" in split.local[0]


def test_verified_is_a_subset_of_what_can_be_emitted() -> None:
    assert VERIFIED <= ALL_MAPPINGS


def test_shared_names_and_enriched_fields_stay_local() -> None:
    twin = _field("field-9", "Status", "number", ["is-equal-to"])
    enriched = FieldMetadata.model_validate(
        {
            "id": "affinity-data-year-founded",
            "name": "Year Founded",
            "type": "enriched",
            "valueType": "number",
            "enrichmentSource": "affinity-data",
            "filterability": {
                "filterableFieldType": "field-only",
                "operators": [{"id": "is-equal-to", "name": "=", "numberOfValuesRequired": "one"}],
            },
        }
    )
    for where, reason in (
        ({"path": "fields.Status", "op": "eq", "value": "Won"}, "two fields share this name"),
        ({"path": "fields.Year Founded", "op": "eq", "value": 2001}, "enriched"),
    ):
        split = split_where(
            WhereClause.model_validate(where),
            [*FIELDS, twin, enriched],
            lambda meta: OPTIONS.get(str(meta.id), []),
            verified=ALL_MAPPINGS,
        )
        assert split.where is None and reason in split.local[0]
