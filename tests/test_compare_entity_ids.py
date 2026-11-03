"""Person and company field values match by name, or by id when the filter value is a number."""

from __future__ import annotations

import copy
import json
import pickle
from typing import Any

import pytest

from affinity.compare import EntityName, compare_values, normalize_value


def _person(pid: int, first: str | None, last: str | None = None) -> dict[str, Any]:
    return {"id": pid, "firstName": first, "lastName": last, "type": "internal"}


YANIV = normalize_value(_person(26229794, "Yaniv", "Golan"))


def test_person_matches_its_id() -> None:
    assert YANIV == "Yaniv Golan"
    assert compare_values(YANIV, "26229794", "eq")
    assert compare_values(YANIV, 26229794, "eq")
    assert compare_values(YANIV, "Yaniv Golan", "eq")
    assert not compare_values(YANIV, "1", "eq")
    assert not compare_values(YANIV, "26229794", "neq")
    assert compare_values(YANIV, "1", "neq")


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
    assert compare_values(YANIV, ["Nobody", "26229794"], "in")
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
    assert compare_values(YANIV, "golan", "contains")
    assert compare_values(YANIV, "Gol", "starts_word")
    assert compare_values(YANIV, "yan", "starts_with")
    assert json.dumps(YANIV) == '"Yaniv Golan"'
    assert type(str(YANIV)) is str
    # Dropdown options carry an id too, but match by text only
    option = normalize_value({"dropdownOptionId": 3, "id": 3, "text": "High"})
    assert type(option) is str and not compare_values(option, 3, "eq")


@pytest.mark.parametrize("how", ["copy", "deepcopy", *(f"pickle{p}" for p in range(6))])
def test_copy_and_pickle_keep_the_id(how: str) -> None:
    if how == "copy":
        out = copy.copy(YANIV)
    elif how == "deepcopy":
        out = copy.deepcopy(YANIV)
    else:
        out = pickle.loads(pickle.dumps(YANIV, protocol=int(how[-1])))
    assert out == YANIV
    assert isinstance(out, EntityName)
    assert out.entity_id == 26229794
