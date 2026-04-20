"""Unit tests for DuplicateEntityError."""

from __future__ import annotations

from affinity.exceptions import AffinityError, DuplicateEntityError


def test_duplicate_entity_error_carries_existing_id():
    err = DuplicateEntityError(
        "Company 'Elssway' already exists",
        entity_type="company",
        existing_id=282327760,
        existing_name="Elssway",
        existing_domain="elssway.com",
        existing_is_global=False,
    )
    assert isinstance(err, AffinityError)
    assert err.entity_type == "company"
    assert err.existing_id == 282327760
    assert err.existing_name == "Elssway"
    assert err.existing_domain == "elssway.com"
    assert err.existing_is_global is False
    assert "Elssway" in str(err)


def test_duplicate_entity_error_minimal():
    err = DuplicateEntityError(
        "Person duplicate",
        entity_type="person",
        existing_id=12345,
    )
    assert err.existing_name is None
    assert err.existing_domain is None
    assert err.existing_is_global is False


def test_duplicate_entity_error_flags_global_match():
    err = DuplicateEntityError(
        "Global match for 'Stripe' (id=9999)",
        entity_type="company",
        existing_id=9999,
        existing_name="Stripe",
        existing_domain="stripe.com",
        existing_is_global=True,
    )
    assert err.existing_is_global is True
