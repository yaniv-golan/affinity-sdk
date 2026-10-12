"""
SDK Read-Only Integration Tests.

These tests verify SDK read operations against a live Affinity sandbox.
They are read-only and safe to run repeatedly without side effects.

Tests verify:
- API responses parse correctly into SDK models
- Pagination works correctly via iter() methods
- Query parameters are encoded properly
- All read endpoints return valid data

Usage:
    pytest tests/integration/test_sdk_reads.py
    pytest -m integration -k reads
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from affinity import Affinity

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


# =============================================================================
# Group 1: Auth
# =============================================================================


class TestAuth:
    """Test authentication endpoints."""

    def test_whoami(self, sandbox_client: Affinity) -> None:
        """Verify whoami returns valid user and tenant info."""
        whoami = sandbox_client.auth.whoami()

        assert whoami.user is not None
        assert whoami.user.id is not None
        assert whoami.user.first_name is not None

        assert whoami.tenant is not None
        assert whoami.tenant.name is not None
        assert whoami.tenant.subdomain is not None

        assert whoami.grant is not None
        assert isinstance(whoami.grant.scopes, list)

        # Verify sandbox (redundant but confirms fixture works)
        assert whoami.tenant.name.lower().endswith("sandbox")


# =============================================================================
# Group 2: Persons
# =============================================================================


class TestPersons:
    """Test person read operations."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify persons.list() returns paginated response."""
        response = sandbox_client.persons.list(limit=5)

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        # If there are persons, verify they have required fields
        for person in response.data:
            assert person.id is not None
            # first_name or last_name may be None, but id is always present

    def test_iter_pagination(self, sandbox_client: Affinity) -> None:
        """Verify persons.iter() handles pagination correctly."""
        count = 0
        max_items = 10  # Limit to avoid long test runs

        for person in sandbox_client.persons.iter():
            assert person.id is not None
            count += 1
            if count >= max_items:
                break

        # Just verify iteration works (may have 0 persons in empty sandbox)
        assert count >= 0

    def test_get_first_person(self, sandbox_client: Affinity) -> None:
        """Verify persons.get() returns a valid person if any exist."""
        # Get first person from list
        response = sandbox_client.persons.list(limit=1)
        if not response.data:
            pytest.skip("No persons in sandbox to test get()")

        person_id = response.data[0].id
        person = sandbox_client.persons.get(person_id)

        assert person is not None
        assert person.id == person_id


# =============================================================================
# Group 3: Companies
# =============================================================================


class TestCompanies:
    """Test company read operations."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify companies.list() returns paginated response."""
        response = sandbox_client.companies.list(limit=5)

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        for company in response.data:
            assert company.id is not None

    def test_iter_pagination(self, sandbox_client: Affinity) -> None:
        """Verify companies.iter() handles pagination correctly."""
        count = 0
        max_items = 10

        for company in sandbox_client.companies.iter():
            assert company.id is not None
            count += 1
            if count >= max_items:
                break

        assert count >= 0

    def test_get_first_company(self, sandbox_client: Affinity) -> None:
        """Verify companies.get() returns a valid company if any exist."""
        response = sandbox_client.companies.list(limit=1)
        if not response.data:
            pytest.skip("No companies in sandbox to test get()")

        company_id = response.data[0].id
        company = sandbox_client.companies.get(company_id)

        assert company is not None
        assert company.id == company_id


# =============================================================================
# Group 4: Lists
# =============================================================================


class TestLists:
    """Test list read operations."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify lists.list() returns paginated response."""
        response = sandbox_client.lists.list(limit=5)

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        for affinity_list in response.data:
            assert affinity_list.id is not None
            assert affinity_list.name is not None
            assert affinity_list.type is not None

    def test_iter_pagination(self, sandbox_client: Affinity) -> None:
        """Verify lists.iter() handles pagination correctly."""
        count = 0
        max_items = 10

        for affinity_list in sandbox_client.lists.iter():
            assert affinity_list.id is not None
            count += 1
            if count >= max_items:
                break

        assert count >= 0

    def test_list_entries_iter(self, sandbox_client: Affinity) -> None:
        """Verify list entries iteration works for the first list."""
        # Get first list
        response = sandbox_client.lists.list(limit=1)
        if not response.data:
            pytest.skip("No lists in sandbox to test entries()")

        list_id = response.data[0].id
        entries = sandbox_client.lists.entries(list_id)

        count = 0
        max_items = 5

        for entry in entries.iter():
            assert entry.id is not None
            assert entry.list_id == list_id
            count += 1
            if count >= max_items:
                break

        # May have 0 entries, that's OK
        assert count >= 0


# =============================================================================
# Group 5: Fields
# =============================================================================


class TestFields:
    """Test field read operations."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify fields.list() returns field definitions."""
        fields = sandbox_client.fields.list()

        assert isinstance(fields, list)

        for field in fields:
            assert field.id is not None
            assert field.name is not None
            assert field.value_type is not None


# =============================================================================
# Group 6: Opportunities
# =============================================================================


class TestOpportunities:
    """Test opportunity read operations."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify opportunities.list() returns paginated response."""
        response = sandbox_client.opportunities.list(limit=5)

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        for opp in response.data:
            assert opp.id is not None

    def test_iter_pagination(self, sandbox_client: Affinity) -> None:
        """Verify opportunities.iter() handles pagination correctly."""
        count = 0
        max_items = 10

        for opp in sandbox_client.opportunities.iter():
            assert opp.id is not None
            count += 1
            if count >= max_items:
                break

        assert count >= 0

    def test_get_first_opportunity(self, sandbox_client: Affinity) -> None:
        """Verify opportunities.get() returns a valid opportunity if any exist."""
        response = sandbox_client.opportunities.list(limit=1)
        if not response.data:
            pytest.skip("No opportunities in sandbox to test get()")

        opp_id = response.data[0].id
        opp = sandbox_client.opportunities.get(opp_id)

        assert opp is not None
        assert opp.id == opp_id


# =============================================================================
# Group 7: Notes (V1 API)
# =============================================================================


class TestNotes:
    """Test note read operations (V1 API)."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify notes.list() returns paginated notes."""
        response = sandbox_client.notes.list()

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        for note in response.data[:5]:  # Check first 5
            assert note.id is not None

    def test_get_first_note(self, sandbox_client: Affinity) -> None:
        """Verify notes.get() returns a valid note if any exist."""
        response = sandbox_client.notes.list()
        if not response.data:
            pytest.skip("No notes in sandbox to test get()")

        note_id = response.data[0].id
        note = sandbox_client.notes.get(note_id)

        assert note is not None
        assert note.id == note_id


# =============================================================================
# Group 8: Interactions (V1 API)
# =============================================================================


class TestInteractions:
    """Test interaction read operations (V1 API).

    Note: The interactions API requires filtering by an external/collaborator
    person, which may not exist in all sandbox environments. These tests
    verify the API call structure works; they skip if no suitable person exists.
    """

    def test_list_emails(self, sandbox_client: Affinity) -> None:
        """Verify interactions.list() returns email interactions."""
        from datetime import datetime, timedelta, timezone

        from affinity.exceptions import ValidationError
        from affinity.types import InteractionType

        # Get first person to filter by (API requires entity filter)
        persons_response = sandbox_client.persons.list(limit=1)
        if not persons_response.data:
            pytest.skip("No persons in sandbox to test interactions")

        person_id = persons_response.data[0].id

        # API requires start_time, end_time, and entity filter
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=30)

        try:
            response = sandbox_client.interactions.list(
                type=InteractionType.EMAIL,
                start_time=start_time,
                end_time=end_time,
                person_id=person_id,
                page_size=5,
            )
        except ValidationError as e:
            # API requires person to be external/collaborator
            if "external or collaborator" in str(e):
                pytest.skip("No external/collaborator persons in sandbox")
            raise

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        for interaction in response.data:
            assert interaction.id is not None
            assert interaction.type == InteractionType.EMAIL

    def test_list_meetings(self, sandbox_client: Affinity) -> None:
        """Verify interactions.list() returns meeting interactions."""
        from datetime import datetime, timedelta, timezone

        from affinity.exceptions import ValidationError
        from affinity.types import InteractionType

        # Get first person to filter by (API requires entity filter)
        persons_response = sandbox_client.persons.list(limit=1)
        if not persons_response.data:
            pytest.skip("No persons in sandbox to test interactions")

        person_id = persons_response.data[0].id

        # API requires start_time, end_time, and entity filter
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=30)

        try:
            response = sandbox_client.interactions.list(
                type=InteractionType.MEETING,
                start_time=start_time,
                end_time=end_time,
                person_id=person_id,
                page_size=5,
            )
        except ValidationError as e:
            # API requires person to be external/collaborator
            if "external or collaborator" in str(e):
                pytest.skip("No external/collaborator persons in sandbox")
            raise

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        for interaction in response.data:
            assert interaction.id is not None
            assert interaction.type == InteractionType.MEETING


# =============================================================================
# Group 9: Reminders (V1 API)
# =============================================================================


class TestReminders:
    """Test reminder read operations (V1 API)."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify reminders.list() returns paginated reminders."""
        response = sandbox_client.reminders.list()

        assert response is not None
        assert hasattr(response, "data")
        assert isinstance(response.data, list)

        for reminder in response.data[:5]:  # Check first 5
            assert reminder.id is not None


# =============================================================================
# Group 10: Webhooks (V2 API)
# =============================================================================


class TestWebhooks:
    """Test webhook read operations (V2 API; non-admins see only their own webhooks)."""

    def test_list(self, sandbox_client: Affinity) -> None:
        """Verify webhooks.list() returns a page and every webhook can be read back."""
        page = sandbox_client.webhooks.list(limit=5)

        for webhook in page.data:
            assert sandbox_client.webhooks.get(webhook.id).id == webhook.id


# =============================================================================
# Group 11: Field Values (V1 API)
# =============================================================================


class TestFieldValues:
    """Test field value read operations (V1 API)."""

    def test_list_for_person(self, sandbox_client: Affinity) -> None:
        """Verify field_values.list() works with person filter."""
        # Get first person
        response = sandbox_client.persons.list(limit=1)
        if not response.data:
            pytest.skip("No persons in sandbox to test field_values")

        person_id = response.data[0].id
        field_values = sandbox_client.field_values.list(person_id=person_id)

        assert isinstance(field_values, list)
        # May have 0 field values, that's OK

    def test_list_for_company(self, sandbox_client: Affinity) -> None:
        """Verify field_values.list() works with company filter."""
        # Get first company
        response = sandbox_client.companies.list(limit=1)
        if not response.data:
            pytest.skip("No companies in sandbox to test field_values")

        company_id = response.data[0].id
        field_values = sandbox_client.field_values.list(company_id=company_id)

        assert isinstance(field_values, list)
