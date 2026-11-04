"""Unit tests: which part of a query WHERE clause goes to Affinity's search."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from affinity.cli.query.models import WhereClause
from affinity.cli.query.pushdown import (
    ALL_MAPPINGS,
    ENTITY_VERIFIED,
    ID_MAX,
    REFERENCE_MAPPINGS,
    VERIFIED,
    split_where,
)
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
            "is-not-equal-to",
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


def test_shared_names_stay_local_and_enriched_fields_include_empty() -> None:
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

    def split(where: dict[str, Any], **kw: Any) -> Any:
        return split_where(
            WhereClause.model_validate(where),
            [*FIELDS, twin, enriched],
            lambda meta: OPTIONS.get(str(meta.id), []),
            verified=ALL_MAPPINGS,
            **kw,
        )

    shared = split({"path": "fields.Status", "op": "eq", "value": "Won"})
    assert shared.where is None and "two fields share this name" in shared.local[0]
    year = {"path": "fields.Year Founded", "op": "eq", "value": 2001}
    # Search reads some enriched values as empty, so empty ones come back too
    either = _body(split(year).where)
    assert either["operator"] == "or"
    assert [f["operator"] for f in either["filters"]] == ["is-equal-to", "is-empty"]
    local = split(year, enriched="local")
    assert local.where is None and "enriched" in local.local[0]


def _named(where: dict[str, Any], verified: frozenset[tuple[str, str]] = ALL_MAPPINGS) -> Any:
    return _split(where, verified)


def test_starts_word_on_a_person_field_is_the_search_term() -> None:
    split = _named(
        {
            "and": [
                {"path": "fields.Owners", "op": "starts_word", "value": "Jan"},
                {"path": "fields.Owners", "op": "starts_word", "value": "Doe"},
                {"path": "fields.Status", "op": "eq", "value": "Lead"},
            ]
        }
    )
    assert (split.term, split.term_fields) == ("Jan", ("field-5",))
    assert len(_body(split.where)["filters"]) == 1  # the dropdown
    assert any("one name search per query" in r for r in split.local)


def test_starts_word_in_an_or_or_too_short_stays_local() -> None:
    nested = _named(
        {
            "or": [
                {"path": "fields.Owners", "op": "starts_word", "value": "Jan"},
                {"path": "fields.Status", "op": "eq", "value": "Lead"},
            ]
        }
    )
    assert nested.term is None and nested.where is None
    short = _named({"path": "fields.Owners", "op": "starts_word", "value": "Ja"})
    assert short.term is None
    unverified = _named(
        {"path": "fields.Owners", "op": "starts_word", "value": "Jan"}, verified=frozenset()
    )
    assert unverified.term is None


def test_contains_on_a_person_field_is_an_error() -> None:
    split = _named({"path": "fields.Owners", "op": "contains", "value": "an"})
    assert split.errors and "starts_word" in split.errors[0]


def test_starts_word_on_text_and_dropdowns() -> None:
    assert (
        _only(_named({"path": "fields.Notes", "op": "starts_word", "value": "Acme"}))["operator"]
        == "contains"
    )
    lost = _only(_named({"path": "fields.Status", "op": "starts_word", "value": "lo"}))
    assert lost["value"] == [{"dropdownOptionId": 14}]


@pytest.mark.parametrize("term", ["- -", "Jean-Luc", "O'Brien", "Ja"])
def test_only_plain_name_terms_go_to_the_server(term: str) -> None:
    split = _named({"path": "fields.Owners", "op": "starts_word", "value": term})
    assert split.term is None


def test_verified_name_search_and_empty_checks() -> None:
    from affinity.cli.query.pushdown import VERIFIED

    assert {("person-multi", "term"), ("person-multi", "is-not-empty")} <= VERIFIED
    assert ("location", "is-empty") not in VERIFIED  # never exercised live


MULTI = _field("field-10", "Tags", "dropdown-multi", ["has-any-of", "has-all-of", "has-none-of"])
MULTI_OPTIONS = [
    DropdownOption(id=21, text="AI"),
    DropdownOption(id=22, text="Fintech"),
    DropdownOption(id=23, text="Health"),
    DropdownOption(id=24, text="Health"),
]


def _multi(where: dict[str, Any]) -> Any:
    split = split_where(
        WhereClause.model_validate(where),
        [*FIELDS, MULTI],
        lambda meta: MULTI_OPTIONS if str(meta.id) == "field-10" else OPTIONS.get(str(meta.id), []),
        verified=ALL_MAPPINGS,
    )
    return _only(split) if split.where is not None else split


@pytest.mark.parametrize(
    ("where", "operator", "ids"),
    [
        ({"path": "fields.Tags", "op": "eq", "value": "AI"}, "has-any-of", [21]),
        (
            {"path": "fields.Tags", "op": "has_any", "value": ["AI", "Fintech"]},
            "has-any-of",
            [21, 22],
        ),
        ({"path": "fields.Tags", "op": "contains", "value": "tech"}, "has-any-of", [22]),
        (
            {"path": "fields.Tags", "op": "has_all", "value": ["AI", "Fintech"]},
            "has-all-of",
            [21, 22],
        ),
        ({"path": "fields.Tags", "op": "neq", "value": "Health"}, "has-none-of", [23, 24]),
    ],
)
def test_multi_select_dropdowns(where: dict[str, Any], operator: str, ids: list[int]) -> None:
    sent = _multi(where)
    assert sent["operator"] == operator
    assert [v["dropdownOptionId"] for v in sent["value"]] == ids


def test_has_all_with_a_shared_option_text_stays_local() -> None:
    # "Health" names two options: has-all-of both would ask for more than the filter does
    split = _multi({"path": "fields.Tags", "op": "has_all", "value": ["Health"]})
    assert split.where is None


def test_number_not_equal() -> None:
    assert _only(_split({"path": "fields.Amount", "op": "neq", "value": 5})) == {
        "valueType": "number",
        "fieldId": "field-3",
        "operator": "is-not-equal-to",
        "value": 5,
    }


# --- person and company fields: ids go to the search (D1, D4, D6, D7) ----------------------------

REF_FIELDS = [
    _field("field-11", "Owner", "person", ["is-any-of", "is-none-of", "is-empty", "is-not-empty"]),
    _field("field-12", "Team", "person-multi", ["has-any-of", "has-all-of", "has-none-of"]),
    _field("field-13", "Account", "company", ["is-any-of", "is-none-of", "is-empty"]),
    _field("field-14", "Portfolio", "company-multi", ["has-any-of", "has-all-of", "has-none-of"]),
    _field("field-15", "Mentor", "person", ["is-empty", "is-not-empty"]),  # takes no is-any-of
]
ENRICHED_COMPANY = FieldMetadata.model_validate(
    {
        "id": "affinity-data-current-organization",
        "name": "Current Organization",
        "type": "enriched",
        "valueType": "company",
        "enrichmentSource": "affinity-data",
        "filterability": {
            "filterableFieldType": "field-only",
            "operators": [{"id": "is-any-of", "name": "is", "numberOfValuesRequired": "multi"}],
        },
    }
)
OWNER_ID = 12345
NAMES_REASON = "names are matched locally (use the id to filter on Affinity's side)"


def _ref(
    where: dict[str, Any], verified: frozenset[tuple[str, str]] = ALL_MAPPINGS, **kw: Any
) -> Any:
    return split_where(
        WhereClause.model_validate(where),
        [*FIELDS, *REF_FIELDS, ENRICHED_COMPANY],
        lambda meta: OPTIONS.get(str(meta.id), []),
        verified=verified,
        **kw,
    )


def _cond(path: str, op: str, value: Any) -> dict[str, Any]:
    return {"path": f"fields.{path}", "op": op, "value": value}


def _ids_sent(split: Any) -> tuple[str, list[int]]:
    (cond,) = split.where.items
    assert isinstance(cond, Condition)
    return cond.operator, list(cond.value)


def test_person_eq_id_is_pushed() -> None:
    split = _ref(_cond("Owner", "eq", OWNER_ID))
    assert split.where == FilterGroup(
        "and", (Condition("field-11", "is-any-of", [OWNER_ID], "person"),)
    )
    assert split.pushed == [f"fields.Owner eq {OWNER_ID}"]
    assert split.local == [] and split.complete
    text = _ref(_cond("Owner", "eq", str(OWNER_ID)))
    assert _ids_sent(text) == ("is-any-of", [OWNER_ID])
    assert text.pushed == [f"fields.Owner eq '{OWNER_ID}'"]


def test_body_wraps_ids() -> None:
    split = _ref(_cond("Owner", "eq", OWNER_ID))
    assert _only(split)["value"] == [{"id": OWNER_ID}]


def test_null_stays_local() -> None:
    split = _ref(_cond("Owner", "eq", None))
    assert split.where is None and "no server mapping for person eq" in split.local[0]
    either = _ref({"or": [_cond("Owner", "eq", None), _cond("Owner", "eq", 5)]})
    assert either.where is None
    assert either.local == ["an OR with a branch the server can't answer"]
    assert _ref(_cond("Team", "in", [None])).where is None
    assert _ref(_cond("Team", "in", [5, None])).where is None


def test_in_dedupes_before_counting() -> None:
    assert _ids_sent(_ref(_cond("Owner", "in", [5] * 150))) == ("is-any-of", [5])
    assert _ids_sent(_ref(_cond("Owner", "in", [5, 5, 6]))) == ("is-any-of", [5, 6])


def test_more_than_100_distinct_ids_stay_local() -> None:
    assert _ids_sent(_ref(_cond("Owner", "in", list(range(1, 101)))))[1] == list(range(1, 101))
    split = _ref(_cond("Owner", "in", list(range(1, 102))))
    assert split.where is None
    assert split.local[0].endswith(": needs 1 to 100 values on the server")


def test_largest_id_is_sent() -> None:
    assert _ids_sent(_ref(_cond("Owner", "eq", ID_MAX))) == ("is-any-of", [ID_MAX])


def test_digit_strings_are_ids() -> None:
    assert _ids_sent(_ref(_cond("Owner", "eq", f" {OWNER_ID} "))) == ("is-any-of", [OWNER_ID])
    assert _ids_sent(_ref(_cond("Owner", "eq", "012"))) == ("is-any-of", [12])


@pytest.mark.parametrize("value", [0, -5, ID_MAX + 1, "99999999999999999999"])
def test_ids_out_of_range_stay_local(value: Any) -> None:
    split = _ref(_cond("Owner", "eq", value))
    assert split.where is None and split.local[0].endswith(": not a valid record id")


@pytest.mark.parametrize("value", [True, 1.0, "12a", "\u0663", "9" * 5000, "-5", "Jan"])
def test_values_that_are_not_ids_stay_local(value: Any) -> None:
    split = _ref(_cond("Owner", "eq", value))
    assert split.where is None and split.local[0].endswith(f": {NAMES_REASON}")


def test_shape_is_checked_before_range() -> None:
    split = _ref(_cond("Owner", "in", [0, "Jan"]))
    assert split.local[0].endswith(f": {NAMES_REASON}")
    assert _ref(_cond("Owner", "in", [0, 5])).local[0].endswith(": not a valid record id")


def test_names_stay_local() -> None:
    assert _ref(_cond("Owner", "eq", "Jane Doe")).where is None
    mixed = _ref(_cond("Owner", "in", [1, "Jane Doe"]))
    assert mixed.where is None and mixed.local[0].endswith(f": {NAMES_REASON}")


def test_empty_list_reason() -> None:
    split = _ref(_cond("Owner", "in", []))
    assert split.where is None and "needs 1 to 100 values" in split.local[0]


def test_single_field_list_eq_and_has_stay_local() -> None:
    for op, value in (("eq", [5, 6]), ("has_any", [5]), ("has_all", [5]), ("in", 5)):
        split = _ref(_cond("Owner", op, value))
        assert split.where is None, op
        assert f"no server mapping for person {op}" in split.local[0]


def test_negations_stay_local() -> None:
    assert _ref(_cond("Owner", "neq", 5)).where is None
    assert _ref({"not": _cond("Owner", "eq", 5)}).where is None
    negated = _ref({"not": _cond("Owner", "in", [5])})
    assert negated.where is None and "stays local" in negated.local[0]


@pytest.mark.parametrize(
    ("op", "value", "operator", "ids"),
    [
        ("eq", 5, "has-any-of", [5]),
        ("in", [5, 6], "has-any-of", [5, 6]),
        ("has_any", [5, 6], "has-any-of", [5, 6]),
        ("has_all", [5, 6], "has-all-of", [5, 6]),
        ("eq", [5, 6], "has-all-of", [5, 6]),
    ],
)
def test_multi_ops(op: str, value: Any, operator: str, ids: list[int]) -> None:
    assert _ids_sent(_ref(_cond("Team", op, value))) == (operator, ids)


def test_multi_in_with_a_scalar_stays_local() -> None:
    for op in ("in", "has_any", "has_all"):
        assert _ref(_cond("Team", op, 5)).where is None, op
    assert _ref(_cond("Team", "neq", 5)).where is None


def test_company_fields() -> None:
    assert _ids_sent(_ref(_cond("Account", "eq", 7))) == ("is-any-of", [7])
    assert _ids_sent(_ref(_cond("Portfolio", "in", [7, 8]))) == ("has-any-of", [7, 8])


@pytest.mark.parametrize("enriched", ["local", "or-empty", "as-is"])
def test_enriched_reference_stays_local(enriched: str) -> None:
    split = _ref(_cond("Current Organization", "eq", 7), enriched=enriched)
    assert split.where is None
    assert split.local[0].endswith(": Affinity enriched fields stay local")


def test_entity_verified_keeps_references_local() -> None:
    assert not ENTITY_VERIFIED & REFERENCE_MAPPINGS
    split = _ref(_cond("Owner", "eq", OWNER_ID), verified=ENTITY_VERIFIED)
    assert split.where is None and "person is-any-of not verified" in split.local[0]
    # No "use the id" advice on the endpoints that never take these conditions
    named = _ref(_cond("Owner", "eq", "Jane Doe"), verified=ENTITY_VERIFIED)
    assert "not verified against the API yet" in named.local[0]
    assert "use the id" not in named.local[0]


def test_or_of_ids_pushes_and_mixed_or_stays_local() -> None:
    both = _ref({"or": [_cond("Owner", "eq", 1), _cond("Owner", "eq", 2)]})
    assert _body(both.where)["operator"] == "or" and both.complete
    mixed = _ref({"or": [_cond("Owner", "eq", 1), _cond("Owner", "eq", "Jane Doe")]})
    assert mixed.where is None and mixed.pushed == []


def test_unverified_pair_stays_local() -> None:
    split = _ref(_cond("Owner", "eq", 5), verified=frozenset())
    assert split.where is None and "person is-any-of not verified" in split.local[0]


def test_field_without_operator_stays_local() -> None:
    split = _ref(_cond("Mentor", "eq", 5))
    assert split.where is None and "the field doesn't take is-any-of" in split.local[0]


def test_reference_pairs_are_emittable_and_verified_matches_the_record() -> None:
    assert REFERENCE_MAPPINGS <= ALL_MAPPINGS
    assert len(REFERENCE_MAPPINGS) == 6
    # The golden run of 2026-11-04 (see the VERIFIED comment) passed every pair except the
    # single-company one, which no list it ran on could exercise
    assert REFERENCE_MAPPINGS - {("company", "is-any-of")} == REFERENCE_MAPPINGS & VERIFIED
    assert VERIFIED - REFERENCE_MAPPINGS == ENTITY_VERIFIED


def test_digit_term_stays_local() -> None:
    owners = "fields.Owners"
    split = _named({"path": owners, "op": "starts_word", "value": "360"})
    assert split.term is None and split.local == [
        f"{owners} starts_word '360': a number isn't sent as a name search"
    ]
    spaced = _named({"path": owners, "op": "starts_word", "value": "3 6 0"})
    assert spaced.term is None and "a number isn't sent" in spaced.local[0]
    as_int = _named({"path": owners, "op": "starts_word", "value": 360})
    assert as_int.term is None and "one name search per query" in as_int.local[0]
    for value in ("- -", "\u0663\u0663\u0663"):
        other = _named({"path": owners, "op": "starts_word", "value": value})
        assert other.term is None and "one name search per query" in other.local[0]
    for value in ("Jan", "Jan 3", "3M Co"):
        assert _named({"path": owners, "op": "starts_word", "value": value}).term == value
