"""Person and company field values match by name; a number in the filter is the record's id."""

from __future__ import annotations

import copy
import json
import pickle
from typing import Any

import pytest

from affinity.compare import EntityName, compare_values, normalize_value


def _person(pid: int, first: str | None, last: str | None = None) -> dict[str, Any]:
    return {"id": pid, "firstName": first, "lastName": last, "type": "internal"}


JANE = normalize_value(_person(12345, "Jane", "Doe"))


def test_person_matches_its_id() -> None:
    assert JANE == "Jane Doe"
    assert compare_values(JANE, "12345", "eq")
    assert compare_values(JANE, 12345, "eq")
    assert compare_values(JANE, "Jane Doe", "eq")
    assert not compare_values(JANE, "1", "eq")
    assert not compare_values(JANE, "12345", "neq")
    assert compare_values(JANE, "1", "neq")


def test_company_and_list_references_match_their_id() -> None:
    company = normalize_value({"id": 7, "name": "Acme", "domain": "acme.com"})
    assert compare_values(company, "7", "eq")
    assert compare_values(company, "Acme", "eq")
    a_list = normalize_value(
        {"id": 41780, "name": "Dealflow", "type": "opportunity", "entityCount": 3}
    )
    assert compare_values(a_list, 41780, "eq")


def test_multi_person_by_id() -> None:
    team = normalize_value([_person(1, "A"), _person(2, "B")])
    assert compare_values(team, "2", "eq")
    assert not compare_values(team, "2", "neq")
    assert compare_values(team, "3", "neq")
    assert compare_values(team, ["1", "2"], "eq")
    assert compare_values(team, ["2", "1"], "eq")
    assert not compare_values(team, ["1"], "eq")
    assert compare_values(team, ["A", 2], "eq")
    assert not compare_values(team, ["1", "2"], "neq")
    assert compare_values(team, ["1"], "neq")


def test_in_and_has_by_id() -> None:
    assert compare_values(JANE, ["Nobody", "12345"], "in")
    team = normalize_value([_person(1, "A"), _person(2, "B")])
    assert compare_values(team, ["9", "2"], "has_any")
    assert compare_values(team, ["1", "2"], "has_all")
    assert not compare_values(team, ["1", "3"], "has_all")


@pytest.mark.parametrize("target", [True, 1.0, "12a", "²", "٣", "", "-1", "9" * 5000])
def test_targets_that_are_not_ids(target: Any) -> None:
    x = normalize_value(_person(1, "X"))
    assert not compare_values(x, target, "eq")


@pytest.mark.parametrize("target", [" 1 ", "01", 1])
def test_digit_targets_match_the_id(target: Any) -> None:
    assert compare_values(normalize_value(_person(1, "X")), target, "eq")


def test_nameless_reference_is_unchanged() -> None:
    assert normalize_value(_person(5, None, None)) is None
    nameless = normalize_value({"id": 8, "name": ""})
    assert nameless == "" and type(nameless) is str
    assert normalize_value({"id": 5}) == {"id": 5}


def test_name_operators_and_serialization_unchanged() -> None:
    assert compare_values(JANE, "doe", "contains")
    assert compare_values(JANE, "Do", "starts_word")
    assert compare_values(JANE, "jan", "starts_with")
    assert json.dumps(JANE) == '"Jane Doe"'
    assert type(str(JANE)) is str
    # Dropdown options carry an id too, but match by text only
    option = normalize_value({"dropdownOptionId": 3, "id": 3, "text": "High"})
    assert type(option) is str and not compare_values(option, 3, "eq")


@pytest.mark.parametrize("how", ["copy", "deepcopy", *(f"pickle{p}" for p in range(6))])
def test_copy_and_pickle_keep_the_id(how: str) -> None:
    if how == "copy":
        out = copy.copy(JANE)
    elif how == "deepcopy":
        out = copy.deepcopy(JANE)
    else:
        out = pickle.loads(pickle.dumps(JANE, protocol=int(how[-1])))
    assert out == JANE
    assert isinstance(out, EntityName)
    assert out.entity_id == 12345


# --- a number on a record reference is its id only (never a name made of digits) -----------------

DIGITS = EntityName("360", 77)


def test_a_number_never_matches_a_digit_name() -> None:
    assert not compare_values(DIGITS, 360, "eq")
    assert not compare_values(DIGITS, "360", "eq")
    assert compare_values(DIGITS, 77, "eq")
    assert compare_values(DIGITS, "77", "in") is False  # `in` needs a list
    assert compare_values(DIGITS, 360, "neq")
    assert not compare_values(DIGITS, 77, "neq")
    assert not compare_values(DIGITS, [360], "in")
    assert compare_values(DIGITS, ["77"], "in")


def test_a_number_never_matches_a_digit_name_in_a_multi_field() -> None:
    team = [EntityName("360", 77)]
    assert not compare_values(team, [360], "has_any")
    assert compare_values(team, [77], "has_all")
    assert compare_values(team, [77], "eq")
    assert not compare_values(team, [360], "eq")
    assert compare_values(team, [360], "neq")
    assert not compare_values(team, 360, "eq")
    assert compare_values(team, 77, "eq")


def test_a_name_still_matches_by_name_and_non_ids_compare_as_names() -> None:
    assert compare_values(EntityName("Acme", 7), "Acme", "eq")
    assert compare_values(EntityName("Pipeline", 9), 9, "eq")
    assert compare_values(EntityName("Pipeline", 9), "Pipeline", "eq")
    assert compare_values(EntityName("1.0", 3), "1.0", "eq")


def test_a_plain_str_reference_keeps_the_string_fallback() -> None:
    # No int id (so no EntityName): can't be told apart from a text field value
    assert compare_values("360", 360, "eq")


def test_the_sdk_filter_matches_ids_only() -> None:
    from affinity.filters import parse

    record = {"Company": {"id": 77, "name": "360", "domain": None}}
    assert not parse("Company = 360").matches(record)
    assert not parse('Company = "360"').matches(record)
    assert parse("Company = 77").matches(record)
    assert parse("Company != 360").matches(record)
    assert not parse('Company in ["360"]').matches(record)


def test_a_reference_without_an_int_id_keeps_the_string_fallback() -> None:
    from affinity.filters import parse

    # The API always sends int ids; a plain-str value can't be told from a text field value
    assert parse("Company = 360").matches({"Company": "360"})
