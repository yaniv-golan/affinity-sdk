#!/usr/bin/env python3
"""
SDK Write Capability Test Suite

This test suite verifies all SDK write capabilities against a live Affinity
sandbox environment. It creates, updates, and deletes entities, then cleans
up all test data after completion.

Usage:
    python tests/integration/test_sdk_writes.py [--include-beta] [--cleanup-orphans]

    API key is loaded from .sandbox.env file (AFFINITY_API_KEY=...).

Safety:
    - The suite ONLY runs against sandbox instances (tenant name must end with 'sandbox')
    - User must explicitly confirm before tests run
    - All test data is cleaned up after completion
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from affinity import Affinity
from affinity.exceptions import NotFoundError
from affinity.models import (
    CompanyCreate,
    CompanyUpdate,
    FieldCreate,
    FieldValueCreate,
    InteractionCreate,
    InteractionUpdate,
    ListCreate,
    NoteCreate,
    NoteUpdate,
    OpportunityCreate,
    OpportunityUpdate,
    PersonCreate,
    PersonUpdate,
    ReminderCreate,
    ReminderUpdate,
    WebhookCreate,
    WebhookUpdate,
)
from affinity.models.entities import AffinityList
from affinity.models.types import InteractionDirection
from affinity.types import (
    CompanyId,
    EntityType,
    FieldValueType,
    InteractionType,
    ListId,
    ListType,
    OpportunityId,
    PersonId,
    ReminderResetType,
    ReminderStatus,
    ReminderType,
    UserId,
    WebhookEvent,
    WebhookStatus,
)

# =============================================================================
# Configuration
# =============================================================================

TEST_PREFIX = "SDK_WRITE_TEST_"


@dataclass
class RunConfig:
    """Configuration for the test run, passed to all test functions."""

    timestamp: str
    marker: str  # f"{TEST_PREFIX}{timestamp}"
    owner_id: UserId
    beta_enabled: bool = False

    @classmethod
    def create(cls, owner_id: UserId, beta_enabled: bool = False) -> RunConfig:
        """Create a new RunConfig with current timestamp."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return cls(
            timestamp=timestamp,
            marker=f"{TEST_PREFIX}{timestamp}",
            owner_id=owner_id,
            beta_enabled=beta_enabled,
        )


@dataclass
class Fixtures:
    """
    Persistent test entities used across multiple test groups.

    These are created in Groups 2-3 and cleaned up at the end of the test run.
    Unlike CRUD tests that create/delete their own entities, these persist
    throughout the test run for use by Groups 4-7.
    """

    person_id: PersonId | None = None
    company_id: CompanyId | None = None
    opportunity_id: OpportunityId | None = None


@dataclass
class CleanupItem:
    """Item to be cleaned up after tests."""

    item_type: str
    item_id: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreatedLists:
    """Container for test lists of each type, created in Group 2."""

    person_list: AffinityList
    company_list: AffinityList
    opportunity_list: AffinityList


# =============================================================================
# Safety Guards
# =============================================================================


def verify_sandbox(client: Affinity) -> bool:
    """
    Verify the instance is a sandbox environment.

    Returns True only if the tenant name ends with 'sandbox' (case-insensitive).
    """
    whoami = client.auth.whoami()
    tenant_name = whoami.tenant.name.lower()

    print(f"Instance: {whoami.tenant.name}")
    print(f"Subdomain: {whoami.tenant.subdomain}")
    print(f"User: {whoami.user.first_name} {whoami.user.last_name}")
    print(f"API Key Scope: {whoami.grant.scopes}")

    is_sandbox = tenant_name.endswith("sandbox")

    if not is_sandbox:
        print("\n  WARNING: This does NOT appear to be a sandbox instance!")
        print(f"   Tenant name '{whoami.tenant.name}' does not end with 'sandbox'")
        return False

    print("\n  Confirmed: Running against sandbox environment")
    return True


def get_user_permission() -> bool:
    """Get explicit user permission to run write tests."""
    print("\n" + "=" * 60)
    print("SDK WRITE TEST SUITE")
    print("=" * 60)
    print("\nThis test suite will:")
    print("  - Create, update, and delete entities in Affinity")
    print("  - Create test lists, fields, notes, reminders, etc.")
    print("  - All test data will be cleaned up after completion")
    print("")

    response = input("Do you want to proceed? [y/N]: ").strip().lower()
    return response == "y"


# =============================================================================
# Group 1: Core Entity CRUD Verification
# =============================================================================


def verify_person_crud(client: Affinity, config: RunConfig) -> bool:
    """
    Test person create, read, update, delete.

    This is a self-contained CRUD verification test. It creates and deletes
    its own entity to verify the full lifecycle works correctly.

    Returns True if all operations succeeded.
    """
    # CREATE
    person = client.persons.create(
        PersonCreate(
            first_name=f"{config.marker}_John",
            last_name="Doe",
            emails=[f"test_{config.timestamp}@example-test-domain.com"],
        )
    )
    assert person.id is not None
    assert person.first_name == f"{config.marker}_John"
    print(f"    Created person: {person.id} ({person.first_name} {person.last_name})")

    # READ (use retries=3 for V1→V2 eventual consistency after create)
    fetched = client.persons.get(person.id, retries=3)
    assert fetched.id == person.id
    assert fetched.first_name == person.first_name
    print(f"    Read person: {fetched.id}")

    # UPDATE
    updated = client.persons.update(
        person.id,
        PersonUpdate(
            first_name=f"{config.marker}_Jane",
        ),
    )
    assert updated.first_name == f"{config.marker}_Jane"
    print(f"    Updated person: {updated.first_name}")

    # Note: Skip re-fetch verification after update due to V1→V2 eventual consistency.
    # The update() response already confirms the change was applied.

    # DELETE
    success = client.persons.delete(person.id)
    assert success is True
    print(f"    Deleted person: {person.id}")

    # Verify deletion (should raise NotFoundError)
    # Note: Need delay for V1→V2 eventual consistency - delete via V1 may not
    # propagate to V2 immediately
    time.sleep(1)
    try:
        client.persons.get(person.id)
        print("    Person should have been deleted")
        return False
    except NotFoundError:
        print("    Verified person deleted (404 as expected)")
        return True


def verify_company_crud(client: Affinity, config: RunConfig) -> bool:
    """
    Test company create, read, update, delete.

    This is a self-contained CRUD verification test.
    """
    # CREATE
    company = client.companies.create(
        CompanyCreate(
            name=f"{config.marker}_Acme Corp",
            domain=f"test-{config.timestamp.replace('_', '-')}.example.com",
        )
    )
    assert company.id is not None
    print(f"    Created company: {company.id} ({company.name})")

    # READ (use retries=3 for V1→V2 eventual consistency after create)
    fetched = client.companies.get(company.id, retries=3)
    assert fetched.id == company.id
    print(f"    Read company: {fetched.id}")

    # UPDATE
    updated = client.companies.update(
        company.id,
        CompanyUpdate(
            name=f"{config.marker}_Acme Industries",
        ),
    )
    assert updated.name == f"{config.marker}_Acme Industries"
    print(f"    Updated company: {updated.name}")

    # DELETE
    success = client.companies.delete(company.id)
    assert success is True
    print(f"    Deleted company: {company.id}")

    # Verify deletion (should raise NotFoundError)
    # Note: Need delay for V1→V2 eventual consistency
    time.sleep(1)
    try:
        client.companies.get(company.id)
        print("    Company should have been deleted")
        return False
    except NotFoundError:
        print("    Verified company deleted (404 as expected)")
        return True


# =============================================================================
# Group 2: List Creation + Persistent Fixtures
# =============================================================================


def verify_list_create_all_types(client: Affinity, config: RunConfig) -> CreatedLists:
    """
    Create test lists for each entity type.

    Note: Lists cannot be deleted via API, so these lists will remain.
    Each entity type requires its own list type for proper entry operations.
    """
    # PERSON LIST (for person entries)
    person_list = client.lists.create(
        ListCreate(
            name=f"{config.marker}_People",
            type=ListType.PERSON,
            is_public=False,
            owner_id=config.owner_id,
        )
    )
    assert person_list.id is not None
    assert person_list.type == ListType.PERSON
    print(f"    Created person list: {person_list.id} ({person_list.name})")

    # COMPANY LIST (for company entries)
    company_list = client.lists.create(
        ListCreate(
            name=f"{config.marker}_Companies",
            type=ListType.COMPANY,
            is_public=False,
            owner_id=config.owner_id,
        )
    )
    assert company_list.id is not None
    assert company_list.type == ListType.COMPANY
    print(f"    Created company list: {company_list.id} ({company_list.name})")

    # OPPORTUNITY LIST (for opportunities)
    opportunity_list = client.lists.create(
        ListCreate(
            name=f"{config.marker}_Pipeline",
            type=ListType.OPPORTUNITY,
            is_public=False,
            owner_id=config.owner_id,
        )
    )
    assert opportunity_list.id is not None
    assert opportunity_list.type == ListType.OPPORTUNITY
    print(f"    Created opportunity list: {opportunity_list.id} ({opportunity_list.name})")

    return CreatedLists(
        person_list=person_list,
        company_list=company_list,
        opportunity_list=opportunity_list,
    )


def create_persistent_fixtures(
    client: Affinity,
    config: RunConfig,
    cleanup_items: list[CleanupItem],
) -> Fixtures:
    """
    Create persistent test entities for use across Groups 4-7.

    Unlike CRUD tests that create/delete their own entities, these persist
    throughout the test run. They are tracked in cleanup_items for final cleanup.
    """
    fixtures = Fixtures()

    # Create persistent person
    person = client.persons.create(
        PersonCreate(
            first_name=f"{config.marker}_Fixture",
            last_name="Person",
            emails=[f"fixture_{config.timestamp}@example-test-domain.com"],
        )
    )
    cleanup_items.append(CleanupItem("person", person.id))
    fixtures.person_id = person.id
    print(f"    Created persistent person: {person.id}")

    # Create persistent company
    company = client.companies.create(
        CompanyCreate(
            name=f"{config.marker}_Fixture Corp",
            domain=f"fixture-{config.timestamp.replace('_', '-')}.example.com",
        )
    )
    cleanup_items.append(CleanupItem("company", company.id))
    fixtures.company_id = company.id
    print(f"    Created persistent company: {company.id}")

    return fixtures


def create_persistent_opportunity(
    client: Affinity,
    config: RunConfig,
    opportunity_list_id: ListId,
    cleanup_items: list[CleanupItem],
    fixtures: Fixtures,
) -> None:
    """
    Create a persistent opportunity for use in Groups 4-7.

    Called after opportunity list is created (Group 2.3) and after
    opportunity CRUD verification (Group 3.1-3.3).
    """
    opp = client.opportunities.create(
        OpportunityCreate(
            name=f"{config.marker}_Fixture Deal",
            list_id=opportunity_list_id,
        )
    )
    cleanup_items.append(CleanupItem("opportunity", opp.id))
    fixtures.opportunity_id = opp.id
    print(f"    Created persistent opportunity: {opp.id}")


# =============================================================================
# Group 2: List Entry Operations
# =============================================================================


def verify_person_list_entry_operations(
    client: Affinity,
    person_list_id: ListId,
    person_id: PersonId,
) -> bool:
    """Test person list entry add/ensure/delete operations."""
    entries = client.lists.entries(person_list_id)

    # ADD PERSON to person list
    entry = entries.add_person(person_id)
    assert entry.id is not None
    print(f"    Added person to person list: entry {entry.id}")

    # ENSURE PERSON (idempotent - should return existing)
    # Note: Need delay for V1→V2 eventual consistency, otherwise ensure_person
    # won't find the entry we just added and will create a duplicate
    time.sleep(1)
    entry_again = entries.ensure_person(person_id)
    assert entry_again.id == entry.id  # Same entry returned
    print(f"    ensure_person returned existing entry: {entry_again.id}")

    # DELETE entry
    success = entries.delete(entry.id)
    assert success is True
    print(f"    Deleted person entry: {entry.id}")

    return True


def verify_company_list_entry_operations(
    client: Affinity,
    company_list_id: ListId,
    company_id: CompanyId,
) -> bool:
    """Test company list entry add/ensure/delete operations."""
    entries = client.lists.entries(company_list_id)

    # ADD COMPANY to company list
    entry = entries.add_company(company_id)
    assert entry.id is not None
    print(f"    Added company to company list: entry {entry.id}")

    # ENSURE COMPANY (idempotent - should return existing)
    # Note: Need delay for V1→V2 eventual consistency
    time.sleep(1)
    entry_again = entries.ensure_company(company_id)
    assert entry_again.id == entry.id  # Same entry returned
    print(f"    ensure_company returned existing entry: {entry_again.id}")

    # DELETE entry
    success = entries.delete(entry.id)
    assert success is True
    print(f"    Deleted company entry: {entry.id}")

    return True


# =============================================================================
# Group 3: Opportunity CRUD
# =============================================================================


def verify_opportunity_crud(
    client: Affinity,
    config: RunConfig,
    opportunity_list_id: ListId,
) -> bool:
    """
    Test opportunity create, update, delete.

    This is a self-contained CRUD verification test. It creates and deletes
    its own entity. The persistent opportunity for Groups 4-7 is created
    separately via create_persistent_opportunity().

    Note: Opportunities are automatically added to their designated list on creation.
    Unlike persons/companies, opportunities are bound to a single list; they can't be added
    to a list entry-wise.
    """
    # CREATE (auto-adds to the opportunity list)
    opp = client.opportunities.create(
        OpportunityCreate(
            name=f"{config.marker}_Deal",
            list_id=opportunity_list_id,
        )
    )
    assert opp.id is not None
    assert opp.list_id == opportunity_list_id
    print(f"    Created opportunity: {opp.id} ({opp.name})")
    print(f"    Opportunity auto-added to list: {opp.list_id}")

    # VERIFY opportunity appears in list entries
    # Note: No find_opportunity() method exists because opportunities are bound to
    # their creating list. We verify by iterating through entries until we find ours.
    # Performance: For test lists with few entries this is fast. For large lists,
    # consider using list(entries.iter())[:100] or adding a limit parameter.
    # Note: Need delay for V1→V2 eventual consistency after create
    time.sleep(1)
    entries = client.lists.entries(opportunity_list_id)
    found_entry = None
    for entry in entries.iter():
        if entry.entity and entry.entity.id == opp.id:
            found_entry = entry
            break  # Exit early once found
    assert found_entry is not None
    print(f"    Verified opportunity in list entries: entry {found_entry.id}")

    # READ (use retries=3 for V1→V2 eventual consistency after create)
    fetched = client.opportunities.get(opp.id, retries=3)
    assert fetched.id == opp.id
    print(f"    Read opportunity: {fetched.id}")

    # UPDATE
    updated = client.opportunities.update(
        opp.id,
        OpportunityUpdate(
            name=f"{config.marker}_Big Deal",
        ),
    )
    assert updated.name == f"{config.marker}_Big Deal"
    print(f"    Updated opportunity: {updated.name}")

    # DELETE (also removes from list)
    success = client.opportunities.delete(opp.id)
    assert success is True
    print(f"    Deleted opportunity: {opp.id}")

    # Verify deletion (should raise NotFoundError)
    # Note: Need delay for V1→V2 eventual consistency
    time.sleep(1)
    try:
        client.opportunities.get(opp.id)
        print("    Opportunity should have been deleted")
        return False
    except NotFoundError:
        print("    Verified opportunity deleted (404 as expected)")
        return True


# =============================================================================
# Group 4: Field Operations
# =============================================================================


def verify_global_field_operations(
    client: Affinity,
    config: RunConfig,
    person_id: PersonId,
) -> bool:
    """Test global field create, field value CRUD using persistent person fixture."""
    # CREATE GLOBAL FIELD (text field on person entity type)
    field = client.fields.create(
        FieldCreate(
            name=f"{config.marker}_GlobalStatus",
            entity_type=EntityType.PERSON,
            value_type=FieldValueType.TEXT,
            is_list_specific=False,  # Global field
        )
    )
    assert field.id is not None
    assert field.list_id is None  # Global field has no list_id
    print(f"    Created global field: {field.id} ({field.name})")

    # CREATE FIELD VALUE (on the person entity)
    # entity_id is the ID of the person/company/opportunity this field value belongs to.
    # Typed IDs (PersonId, CompanyId, etc.) serialize to int automatically via Pydantic.
    fv = client.field_values.create(
        FieldValueCreate(
            field_id=field.id,
            entity_id=person_id,  # PersonId serializes to int for API
            value="Initial Value",
        )
    )
    assert fv.id is not None
    print(f"    Created field value: {fv.id}")

    # UPDATE FIELD VALUE
    updated_fv = client.field_values.update(fv.id, "Updated Value")
    assert updated_fv.value == "Updated Value"
    print(f"    Updated field value: {updated_fv.value}")

    # VERIFY via list
    # Note: Need delay for V1→V2 eventual consistency after update
    time.sleep(1)
    values = client.field_values.list(person_id=person_id)
    found = [v for v in values if v.id == fv.id]
    assert len(found) == 1
    assert found[0].value == "Updated Value"
    print("    Verified field value via list")

    # DELETE FIELD VALUE
    success = client.field_values.delete(fv.id)
    assert success is True
    print(f"    Deleted field value: {fv.id}")

    # DELETE FIELD
    success = client.fields.delete(field.id)
    assert success is True
    print(f"    Deleted global field: {field.id}")

    return True


def verify_list_specific_field_operations(
    client: Affinity,
    config: RunConfig,
    person_list_id: ListId,
    person_id: PersonId,
) -> bool:
    """
    Test list-specific field create and list entry field updates.

    List-specific fields are accessed via the list entries API, not the
    global field_values API.
    """
    # First, add a person to the list to get an entry
    entries = client.lists.entries(person_list_id)
    entry = entries.add_person(person_id)
    print(f"    Added person to list: entry {entry.id}")

    # CREATE LIST-SPECIFIC FIELD
    field = client.fields.create(
        FieldCreate(
            name=f"{config.marker}_ListStatus",
            entity_type=EntityType.PERSON,
            value_type=FieldValueType.TEXT,
            list_id=person_list_id,  # Ties field to this specific list
            is_list_specific=True,
        )
    )
    assert field.id is not None
    assert field.list_id == person_list_id  # List-specific field has list_id set
    print(f"    Created list-specific field: {field.id} ({field.name})")

    # UPDATE SINGLE FIELD via list entry API
    result = entries.update_field_value(entry.id, field.id, "New Status")
    assert result is not None
    print("    Updated list entry field value")

    # VERIFY via get_field_value
    field_value = entries.get_field_value(entry.id, field.id)
    assert field_value == "New Status"
    print(f"    Verified list entry field value: {field_value}")

    # BATCH UPDATE (update multiple fields at once)
    batch_result = entries.batch_update_fields(entry.id, {field.id: "Batch Updated Status"})
    assert batch_result is not None
    print("    Batch updated list entry fields")

    # Verify batch update
    field_value = entries.get_field_value(entry.id, field.id)
    assert field_value == "Batch Updated Status"
    print(f"    Verified batch update: {field_value}")

    # CLEANUP: Delete field, then entry
    success = client.fields.delete(field.id)
    assert success is True
    print(f"    Deleted list-specific field: {field.id}")

    success = entries.delete(entry.id)
    assert success is True
    print(f"    Deleted list entry: {entry.id}")

    return True


# =============================================================================
# Group 5: Notes and Reminders
# =============================================================================


def verify_note_on_person_crud(
    client: Affinity,
    config: RunConfig,
    person_id: PersonId,
) -> bool:
    """Test note create, update, delete on a person (Test 5.1)."""
    # CREATE
    note = client.notes.create(
        NoteCreate(
            content=f"{config.marker} Test note on person",
            person_ids=[person_id],
        )
    )
    assert note.id is not None
    print(f"    Created note on person: {note.id}")

    # READ
    fetched = client.notes.get(note.id)
    assert fetched.id == note.id
    print(f"    Read note: {fetched.id}")

    # UPDATE
    updated = client.notes.update(
        note.id,
        NoteUpdate(
            content=f"{config.marker} Updated note on person",
        ),
    )
    assert "Updated" in (updated.content.html or "")
    print("    Updated note")

    # DELETE
    success = client.notes.delete(note.id)
    assert success is True
    print(f"    Deleted note: {note.id}")

    return True


def verify_note_on_company_crud(
    client: Affinity,
    config: RunConfig,
    company_id: CompanyId,
) -> bool:
    """Test note create, update, delete on a company (Test 5.2)."""
    # CREATE
    note = client.notes.create(
        NoteCreate(
            content=f"{config.marker} Test note on company",
            company_ids=[company_id],
        )
    )
    assert note.id is not None
    print(f"    Created note on company: {note.id}")

    # READ
    fetched = client.notes.get(note.id)
    assert fetched.id == note.id
    print(f"    Read note: {fetched.id}")

    # UPDATE
    updated = client.notes.update(
        note.id,
        NoteUpdate(
            content=f"{config.marker} Updated note on company",
        ),
    )
    assert "Updated" in (updated.content.html or "")
    print("    Updated note")

    # DELETE
    success = client.notes.delete(note.id)
    assert success is True
    print(f"    Deleted note: {note.id}")

    return True


def verify_note_on_opportunity_crud(
    client: Affinity,
    config: RunConfig,
    opportunity_id: OpportunityId,
) -> bool:
    """Test note create, update, delete on an opportunity (Test 5.2b)."""
    # CREATE
    note = client.notes.create(
        NoteCreate(
            content=f"{config.marker} Test note on opportunity",
            opportunity_ids=[opportunity_id],
        )
    )
    assert note.id is not None
    print(f"    Created note on opportunity: {note.id}")

    # READ
    fetched = client.notes.get(note.id)
    assert fetched.id == note.id
    print(f"    Read note: {fetched.id}")

    # UPDATE
    updated = client.notes.update(
        note.id,
        NoteUpdate(
            content=f"{config.marker} Updated note on opportunity",
        ),
    )
    assert "Updated" in (updated.content.html or "")
    print("    Updated note")

    # DELETE
    success = client.notes.delete(note.id)
    assert success is True
    print(f"    Deleted note: {note.id}")

    return True


def verify_one_time_reminder_crud(
    client: Affinity,
    config: RunConfig,
    person_id: PersonId,
) -> bool:
    """Test one-time reminder create, update, delete (Test 5.3)."""
    # CREATE (one-time reminder)
    reminder = client.reminders.create(
        ReminderCreate(
            owner_id=config.owner_id,
            type=ReminderType.ONE_TIME,
            content=f"{config.marker} One-time follow up",
            due_date=datetime.now() + timedelta(days=7),
            person_id=person_id,
        )
    )
    assert reminder.id is not None
    assert reminder.type == ReminderType.ONE_TIME
    print(f"    Created one-time reminder: {reminder.id}")

    # READ
    fetched = client.reminders.get(reminder.id)
    assert fetched.id == reminder.id
    print(f"    Read reminder: {fetched.id}")

    # UPDATE
    updated = client.reminders.update(
        reminder.id,
        ReminderUpdate(
            content=f"{config.marker} Updated one-time follow up",
        ),
    )
    assert "Updated" in (updated.content or "")
    print("    Updated reminder")

    # COMPLETE, then reopen (V2: completedAt)
    completed = client.reminders.update(reminder.id, ReminderUpdate(is_completed=True))
    assert completed.status == ReminderStatus.COMPLETED
    reopened = client.reminders.update(reminder.id, ReminderUpdate(is_completed=False))
    assert reopened.completed_at is None
    print("    Completed and reopened reminder")

    # DELETE
    success = client.reminders.delete(reminder.id)
    assert success is True
    print(f"    Deleted reminder: {reminder.id}")

    return True


def verify_recurring_reminder_crud(
    client: Affinity,
    config: RunConfig,
    company_id: CompanyId,
) -> bool:
    """Test recurring reminder create, update, delete (Test 5.4)."""
    # CREATE (recurring reminder with interaction-based reset)
    reminder = client.reminders.create(
        ReminderCreate(
            owner_id=config.owner_id,
            type=ReminderType.RECURRING,
            content=f"{config.marker} Recurring check-in",
            reset_type=ReminderResetType.INTERACTION,  # Resets on any interaction
            reminder_days=30,  # Remind every 30 days
            company_id=company_id,
        )
    )
    assert reminder.id is not None
    assert reminder.type == ReminderType.RECURRING
    assert reminder.reset_type == ReminderResetType.INTERACTION
    print(f"    Created recurring reminder: {reminder.id}")

    # READ
    fetched = client.reminders.get(reminder.id)
    assert fetched.id == reminder.id
    assert fetched.reminder_days == 30
    print(f"    Read reminder: {fetched.id} (resets every {fetched.reminder_days} days)")

    # UPDATE (change reminder frequency)
    updated = client.reminders.update(
        reminder.id,
        ReminderUpdate(
            reminder_days=14,  # Change to bi-weekly
        ),
    )
    assert updated.reminder_days == 14
    print(f"    Updated reminder frequency to {updated.reminder_days} days")

    # DELETE
    success = client.reminders.delete(reminder.id)
    assert success is True
    print(f"    Deleted reminder: {reminder.id}")

    return True


# =============================================================================
# Group 6: Interactions
# =============================================================================


def verify_interaction_crud(
    client: Affinity,
    config: RunConfig,
    person_id: PersonId,
) -> bool:
    """Test interaction create, update, delete using persistent person fixture."""
    # CREATE (meeting type)
    # Note: Interactions require at least 1 internal person (user) and 1 external person.
    # config.owner_id is the authenticated user's person ID (internal).
    # person_id is the test person we created (external).
    interaction = client.interactions.create(
        InteractionCreate(
            type=InteractionType.MEETING,
            person_ids=[PersonId(config.owner_id), person_id],
            content=f"{config.marker} Intro meeting notes",
            date=datetime.now(),
            direction=InteractionDirection.OUTGOING,
        )
    )
    assert interaction.id is not None
    print(f"    Created interaction: {interaction.id}")

    # READ
    fetched = client.interactions.get(interaction.id, InteractionType.MEETING)
    assert fetched.id == interaction.id
    print(f"    Read interaction: {fetched.id}")

    # UPDATE
    # Note: The Interaction model doesn't have a 'content' field in its response -
    # content is stored differently (possibly as a note). We verify the update
    # succeeded by checking the returned interaction has the same ID.
    updated = client.interactions.update(
        interaction.id,
        InteractionType.MEETING,
        InteractionUpdate(content=f"{config.marker} Updated meeting notes"),
    )
    assert updated.id == interaction.id
    print("    Updated interaction")

    # DELETE
    success = client.interactions.delete(interaction.id, InteractionType.MEETING)
    assert success is True
    print(f"    Deleted interaction: {interaction.id}")

    return True


# =============================================================================
# Group 7: File Operations
# =============================================================================


def verify_file_upload_bytes(
    client: Affinity,
    config: RunConfig,
    person_id: PersonId,
) -> bool:
    """Test file upload via bytes using persistent person fixture."""

    # UPLOAD BYTES to person
    test_content = f"{config.marker} Test file content".encode()
    success = client.files.upload_bytes(
        data=test_content,
        filename=f"{config.marker}_bytes.txt",
        person_id=person_id,
    )
    assert success is True
    print("    Uploaded bytes file to person")

    # LIST FILES (verify upload)
    files = client.files.list(person_id=person_id)
    uploaded = [f for f in files.data if config.marker in f.name]
    assert len(uploaded) >= 1
    print(f"    Verified file in list: {uploaded[0].name}")

    # GET FILE METADATA
    file_info = client.files.get(uploaded[0].id)
    assert file_info.id == uploaded[0].id
    print(f"    Retrieved file metadata: {file_info.id}")

    return True


def verify_file_upload_path(
    client: Affinity,
    config: RunConfig,
    company_id: CompanyId,
) -> bool:
    """Test file upload from disk path using persistent company fixture."""

    # Create a temporary file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix=f"{config.marker}_path_",
        delete=False,
    ) as f:
        f.write(f"{config.marker} Test file from path")
        temp_path = Path(f.name)

    try:
        # UPLOAD PATH to company
        success = client.files.upload_path(
            path=temp_path,
            company_id=company_id,
        )
        assert success is True
        print("    Uploaded file from path to company")

        # VERIFY upload
        files = client.files.list(company_id=company_id)
        uploaded = [f for f in files.data if config.marker in f.name]
        assert len(uploaded) >= 1
        print(f"    Verified path upload in list: {uploaded[0].name}")

    finally:
        # Clean up temp file
        temp_path.unlink(missing_ok=True)

    return True


def verify_file_verification(
    client: Affinity,
    config: RunConfig,
    person_id: PersonId,
    company_id: CompanyId,
) -> bool:
    """Verify all uploaded files are retrievable."""

    # List person files
    person_files = client.files.list(person_id=person_id)
    test_person_files = [f for f in person_files.data if config.marker in f.name]
    print(f"    Found {len(test_person_files)} test files on person")

    # List company files
    company_files = client.files.list(company_id=company_id)
    test_company_files = [f for f in company_files.data if config.marker in f.name]
    print(f"    Found {len(test_company_files)} test files on company")

    # Note: No file delete API exists - files remain attached
    print("    Note: File delete not supported by API")

    return True


# =============================================================================
# Group 8: Webhooks
# =============================================================================


def verify_webhook_crud(client: Affinity) -> bool:
    """
    Test webhook create, get, update, delete (V2).

    Opt-in only: set AFFINITY_TEST_WEBHOOK_URL to an endpoint you control (Affinity sends a test
    delivery to it, and a webhook sends CRM events there while it exists). One narrow event,
    deleted in ``finally``. Without the variable the group is skipped.
    """
    url = os.environ.get("AFFINITY_TEST_WEBHOOK_URL", "").strip()
    if not url:
        print("    Skipping webhook test: AFFINITY_TEST_WEBHOOK_URL is not set (opt-in)")
        return True

    webhook = client.webhooks.create(
        WebhookCreate(url=url, subscriptions=[WebhookEvent.LIST_DELETED])
    )
    print(f"    Created webhook: {webhook.id}")
    try:
        assert webhook.subscriptions == [WebhookEvent.LIST_DELETED]
        fetched = client.webhooks.get(webhook.id)
        assert fetched.id == webhook.id
        print(f"    Read webhook: {fetched.id}")

        updated = client.webhooks.update(webhook.id, WebhookUpdate(status=WebhookStatus.DISABLED))
        assert updated.status == WebhookStatus.DISABLED
        print("    Updated webhook (disabled)")
    finally:
        client.webhooks.delete(webhook.id)
        print(f"    Deleted webhook: {webhook.id}")
    return True


# =============================================================================
# Group 9: Beta Merge Operations (Optional)
# =============================================================================


def poll_merge_status(
    get_status_fn,
    task_url: str,
    max_wait_seconds: float = 30.0,
) -> bool:
    """
    Poll a merge task until completion with exponential backoff.

    Args:
        get_status_fn: Function to get merge status (e.g., client.persons.get_merge_status)
        task_url: The task URL returned by the merge call
        max_wait_seconds: Maximum total time to wait for completion

    Returns:
        True if merge succeeded, False if failed or timed out
    """
    task_id = task_url.rsplit("/", maxsplit=1)[-1]
    wait_time = 0.5  # Start with 500ms
    elapsed = 0.0

    while elapsed < max_wait_seconds:
        status = get_status_fn(task_id)

        if status.status == "success":
            return True
        elif status.status == "failed":
            return False

        # Sleep and track elapsed time
        time.sleep(wait_time)
        elapsed += wait_time

        # Exponential backoff with cap at 2 seconds
        wait_time = min(wait_time * 1.5, 2.0)

    return False  # Timed out


def verify_person_merge(client: Affinity, config: RunConfig) -> bool:
    """
    Test person merge (BETA).

    Creates two persons, merges them, verifies the merge.
    Requires client created with enable_beta_endpoints=True.
    """
    if not config.beta_enabled:
        print("    Skipping person merge test: beta endpoints not enabled")
        return True

    # Create two persons to merge
    primary = client.persons.create(
        PersonCreate(
            first_name=f"{config.marker}_Primary",
            last_name="Person",
            emails=[f"primary_{config.timestamp}@test.example.com"],
        )
    )
    duplicate = client.persons.create(
        PersonCreate(
            first_name=f"{config.marker}_Duplicate",
            last_name="Person",
            emails=[f"duplicate_{config.timestamp}@test.example.com"],
        )
    )
    print(f"    Created primary person: {primary.id}")
    print(f"    Created duplicate person: {duplicate.id}")

    try:
        # MERGE
        task_url = client.persons.merge(primary.id, duplicate.id)
        print(f"    Initiated merge, task URL: {task_url}")

        # Poll for completion with exponential backoff
        success = poll_merge_status(
            client.persons.get_merge_status,
            task_url,
            max_wait_seconds=30,
        )

        if success:
            print("    Merge completed successfully")
        else:
            print("    Merge failed or timed out")
            return False

    finally:
        # Cleanup - attempt to delete both persons
        # If merge succeeded: primary exists (with merged data), duplicate is gone
        # If merge failed: both still exist and should be cleaned up
        for person in [primary, duplicate]:
            try:
                client.persons.delete(person.id)
                print(f"    Cleaned up person: {person.id}")
            except Exception:
                pass  # May already be deleted/merged

    return True


def verify_company_merge(client: Affinity, config: RunConfig) -> bool:
    """
    Test company merge (BETA).

    Creates two companies, merges them, verifies the merge.
    Requires client created with enable_beta_endpoints=True.
    """
    if not config.beta_enabled:
        print("    Skipping company merge test: beta endpoints not enabled")
        return True

    # Create two companies to merge
    primary = client.companies.create(
        CompanyCreate(
            name=f"{config.marker}_Primary Corp",
            domain=f"primary-{config.timestamp.replace('_', '-')}.test.example.com",
        )
    )
    duplicate = client.companies.create(
        CompanyCreate(
            name=f"{config.marker}_Duplicate Corp",
            domain=f"duplicate-{config.timestamp.replace('_', '-')}.test.example.com",
        )
    )
    print(f"    Created primary company: {primary.id}")
    print(f"    Created duplicate company: {duplicate.id}")

    try:
        # MERGE
        task_url = client.companies.merge(primary.id, duplicate.id)
        print(f"    Initiated merge, task URL: {task_url}")

        # Poll for completion with exponential backoff
        success = poll_merge_status(
            client.companies.get_merge_status,
            task_url,
            max_wait_seconds=30,
        )

        if success:
            print("    Merge completed successfully")
        else:
            print("    Merge failed or timed out")
            return False

    finally:
        # Cleanup - attempt to delete both companies
        # If merge succeeded: primary exists (with merged data), duplicate is gone
        # If merge failed: both still exist and should be cleaned up
        for company in [primary, duplicate]:
            try:
                client.companies.delete(company.id)
                print(f"    Cleaned up company: {company.id}")
            except Exception:
                pass  # May already be deleted/merged

    return True


# =============================================================================
# Cleanup Functions
# =============================================================================


def cleanup_test_data(client: Affinity, items: list[CleanupItem]) -> None:
    """
    Clean up all test data in reverse dependency order.

    Cleanup order (most dependent first -> least dependent last):

    1. Field values - depend on both fields AND entities
    2. Custom fields - may depend on lists (list-specific fields)
    3. Notes - attached to persons/companies/opportunities
    4. Reminders - attached to persons/companies/opportunities
    5. Interactions - attached to persons
    6. List entries - reference entities within lists
    7. Opportunities - entities (deleting removes from list)
    8. Companies - entities
    9. Persons - entities
    10. Webhooks - independent (no dependencies)

    Items are deleted in reverse order of how they were added to the cleanup list.
    Since items are tracked immediately after creation, reversing the list naturally
    deletes children before parents.

    Note: Lists cannot be deleted via API.
    Note: Files cannot be deleted via API.
    """
    print("\nCleaning up test data...")

    for item in reversed(items):
        try:
            if item.item_type == "field_value":
                client.field_values.delete(item.item_id)
            elif item.item_type == "field":
                client.fields.delete(item.item_id)
            elif item.item_type == "note":
                client.notes.delete(item.item_id)
            elif item.item_type == "reminder":
                client.reminders.delete(item.item_id)
            elif item.item_type == "interaction":
                interaction_type = item.metadata["type"]
                client.interactions.delete(item.item_id, interaction_type)
            elif item.item_type == "list_entry":
                list_id = item.metadata["list_id"]
                client.lists.entries(list_id).delete(item.item_id)
            elif item.item_type == "opportunity":
                client.opportunities.delete(item.item_id)
            elif item.item_type == "company":
                client.companies.delete(item.item_id)
            elif item.item_type == "person":
                client.persons.delete(item.item_id)
            elif item.item_type == "webhook":
                client.webhooks.delete(item.item_id)
            print(f"    Deleted {item.item_type}: {item.item_id}")
        except Exception as e:
            print(f"    Failed to delete {item.item_type} {item.item_id}: {e}")


# =============================================================================
# Orphan Detection and Cleanup
# =============================================================================


def find_orphan_test_data(client: Affinity, max_entities: int = 1000) -> dict[str, list[Any]]:
    """
    Find any existing test data from previous failed runs.

    Scans for entities with names/content matching TEST_PREFIX.

    WARNING: This scan iterates through entities without date filters. On production-size
    instances with many entities, this can be slow and expensive. Consider:
    - Running only on sandbox instances (which typically have fewer entities)
    - Adding a --limit flag to cap entities scanned
    - For production use, implementing date-based filtering if the API supports it

    Args:
        max_entities: Maximum entities to scan per type (default 1000). Set to 0 for unlimited.

    Returns:
        Dict mapping entity type to list of orphaned IDs (with metadata where needed)
    """
    orphans: dict[str, list[Any]] = {
        "persons": [],
        "companies": [],
        "opportunities": [],
        "fields": [],
        "notes": [],
        "reminders": [],
        "interactions": [],  # Includes type metadata for deletion
        "list_entries": [],  # Includes list_id metadata for deletion
        "lists": [],  # Note: Can't delete, but useful to know about
    }

    print("Scanning for orphan test data...")

    # Helper to limit iteration
    def limited_iter(iterator, limit):
        """Yield at most `limit` items from iterator. 0 means unlimited."""
        for i, item in enumerate(iterator):
            if limit and i >= limit:
                break
            yield item

    # Check persons
    for person in limited_iter(client.persons.iter(), max_entities):
        if person.first_name and TEST_PREFIX in person.first_name:
            orphans["persons"].append(person.id)

    # Check companies
    for company in limited_iter(client.companies.iter(), max_entities):
        if company.name and TEST_PREFIX in company.name:
            orphans["companies"].append(company.id)

    # Check opportunities and list entries (iterate through all lists)
    for affinity_list in client.lists.iter():
        # Check if this is a test list
        is_test_list = affinity_list.name and TEST_PREFIX in affinity_list.name
        if is_test_list:
            orphans["lists"].append(affinity_list.id)

        # Check entries in opportunity lists for orphan opportunities
        if affinity_list.type == ListType.OPPORTUNITY:
            for entry in client.lists.entries(affinity_list.id).iter():
                opp = client.opportunities.get(entry.entity_id)
                if opp.name and TEST_PREFIX in opp.name:
                    orphans["opportunities"].append(opp.id)

        # Check for orphan list entries in test lists
        # (entries that weren't cleaned up from failed test runs)
        if is_test_list:
            for entry in client.lists.entries(affinity_list.id).iter():
                orphans["list_entries"].append(
                    {
                        "id": entry.id,
                        "list_id": affinity_list.id,
                    }
                )

    # Check custom fields
    for fld in client.fields.list():
        if fld.name and TEST_PREFIX in fld.name:
            orphans["fields"].append(fld.id)

    # Check notes (uses list() with manual pagination - no iter() method)
    # Full scan can be expensive on large instances.
    page_token = None
    while True:
        response = client.notes.list(page_size=100, page_token=page_token)
        for note in response.data:
            if note.content and TEST_PREFIX in note.content:
                orphans["notes"].append(note.id)
        page_token = response.next_page_token
        if not page_token:
            break

    # Check reminders
    for reminder in client.reminders.iter(limit=100):
        if reminder.content and TEST_PREFIX in reminder.content:
            orphans["reminders"].append(reminder.id)

    # Check interactions (by type, uses list() with manual pagination)
    # We only check MEETING type - this is what the test suite creates.
    # Note: interactions.list() requires type, start_time, end_time, and one entity filter.
    # For orphan detection, we scan interactions on test persons/companies we found above.
    # This is limited but avoids scanning the entire interactions history.
    one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)
    now = datetime.now(timezone.utc)

    for person_id in orphans.get("persons", [])[:10]:  # Limit to first 10 orphan persons
        try:
            page_token = None
            while True:
                response = client.interactions.list(
                    type=InteractionType.MEETING,
                    person_id=person_id,
                    start_time=one_year_ago,
                    end_time=now,
                    page_token=page_token,
                )
                for interaction in response.data:
                    if interaction.content and TEST_PREFIX in interaction.content:
                        orphans["interactions"].append(
                            {
                                "id": interaction.id,
                                "type": InteractionType.MEETING,
                            }
                        )
                page_token = response.next_page_token
                if not page_token:
                    break
        except Exception:
            pass  # May fail if person doesn't exist anymore

    result = {k: v for k, v in orphans.items() if v}  # Only return non-empty
    if result:
        print(f"  Found orphans: {', '.join(f'{k}={len(v)}' for k, v in result.items())}")
    else:
        print("  No orphan test data found")
    return result


def cleanup_orphan_data(client: Affinity, orphans: dict[str, list[Any]]) -> None:
    """
    Delete orphan test data in dependency order.

    Order: interactions -> notes -> reminders -> list_entries -> fields ->
           opportunities -> companies -> persons
    Lists cannot be deleted via API.
    """
    # Interactions first (attached to persons)
    for item in orphans.get("interactions", []):
        try:
            client.interactions.delete(item["id"], item["type"])
            print(f"    Deleted orphan interaction: {item['id']}")
        except Exception as e:
            print(f"    Failed to delete interaction {item['id']}: {e}")

    # Notes (attached to entities)
    for note_id in orphans.get("notes", []):
        try:
            client.notes.delete(note_id)
            print(f"    Deleted orphan note: {note_id}")
        except Exception as e:
            print(f"    Failed to delete note {note_id}: {e}")

    # Reminders (attached to entities)
    for reminder_id in orphans.get("reminders", []):
        try:
            client.reminders.delete(reminder_id)
            print(f"    Deleted orphan reminder: {reminder_id}")
        except Exception as e:
            print(f"    Failed to delete reminder {reminder_id}: {e}")

    # List entries (before entities)
    for item in orphans.get("list_entries", []):
        try:
            client.lists.entries(item["list_id"]).delete(item["id"])
            print(f"    Deleted orphan list entry: {item['id']}")
        except Exception as e:
            print(f"    Failed to delete list entry {item['id']}: {e}")

    # Fields (may have values on entities)
    for field_id in orphans.get("fields", []):
        try:
            client.fields.delete(field_id)
            print(f"    Deleted orphan field: {field_id}")
        except Exception as e:
            print(f"    Failed to delete field {field_id}: {e}")

    # Opportunities (entities)
    for opp_id in orphans.get("opportunities", []):
        try:
            client.opportunities.delete(opp_id)
            print(f"    Deleted orphan opportunity: {opp_id}")
        except Exception as e:
            print(f"    Failed to delete opportunity {opp_id}: {e}")

    # Companies (entities)
    for company_id in orphans.get("companies", []):
        try:
            client.companies.delete(company_id)
            print(f"    Deleted orphan company: {company_id}")
        except Exception as e:
            print(f"    Failed to delete company {company_id}: {e}")

    # Persons (entities)
    for person_id in orphans.get("persons", []):
        try:
            client.persons.delete(person_id)
            print(f"    Deleted orphan person: {person_id}")
        except Exception as e:
            print(f"    Failed to delete person {person_id}: {e}")

    # Lists (informational only)
    if orphans.get("lists"):
        print(f"    Found {len(orphans['lists'])} orphan test lists (cannot delete via API)")


# =============================================================================
# Test Runner
# =============================================================================


def load_sandbox_env() -> str | None:
    """
    Load API key from .sandbox.env file.

    The .sandbox.env file should be in the project root and contain:
        AFFINITY_API_KEY=your_sandbox_api_key_here

    Returns the API key if found, None otherwise.
    """
    env_file = Path(".sandbox.env")
    if not env_file.exists():
        return None

    with env_file.open() as f:
        for line in f:
            line = line.strip()
            if line.startswith("AFFINITY_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def print_results(results: dict[str, bool]) -> None:
    """Print test results summary."""
    print("\n" + "=" * 50)
    print("RESULTS SUMMARY")
    print("=" * 50)
    passed = sum(1 for v in results.values() if v)
    failed = len(results) - passed
    print(f"Total tests: {len(results)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    if failed == 0:
        print("\nAll SDK write capabilities verified successfully!")
    else:
        print("\nFailed tests:")
        for name, result in results.items():
            if not result:
                print(f"  - {name}")


def main():
    parser = argparse.ArgumentParser(description="SDK Write Capability Test Suite")
    parser.add_argument("--include-beta", action="store_true", help="Include beta endpoint tests")
    parser.add_argument(
        "--cleanup-orphans", action="store_true", help="Find and clean up orphan test data first"
    )
    args = parser.parse_args()

    # Load API key from .sandbox.env
    api_key = load_sandbox_env()
    if not api_key:
        print("Error: Could not find API key in .sandbox.env file")
        print("   Create .sandbox.env with: AFFINITY_API_KEY=your_key")
        sys.exit(1)

    # Initialize client
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if args.include_beta:
        client_kwargs["enable_beta_endpoints"] = True

    with Affinity(**client_kwargs) as client:
        # Step 1: Verify sandbox
        if not verify_sandbox(client):
            print("\nAborting: Not a sandbox environment")
            sys.exit(1)

        # Step 2: Get user permission
        if not get_user_permission():
            print("\nAborting: User declined")
            sys.exit(0)

        # Step 3: Get current user ID and create RunConfig
        whoami = client.auth.whoami()
        config = RunConfig.create(
            owner_id=whoami.user.id,
            beta_enabled=args.include_beta,
        )
        print(f"\nTest config: marker={config.marker}, owner_id={config.owner_id}")

        # Step 3.5: Optionally clean up orphans from previous failed runs
        if args.cleanup_orphans:
            print("\n" + "=" * 50)
            print("Orphan Cleanup")
            print("=" * 50)
            orphans = find_orphan_test_data(client)
            if orphans:
                response = input("Delete orphan test data? [y/N]: ").strip().lower()
                if response == "y":
                    cleanup_orphan_data(client, orphans)

        # Step 4: Run tests
        results: dict[str, bool] = {}
        cleanup_items: list[CleanupItem] = []
        fixtures = Fixtures()

        try:
            # Group 1: CRUD Verification (self-contained)
            print("\n" + "=" * 50)
            print("Group 1: Person and Company CRUD Verification")
            print("=" * 50)
            results["verify_person_crud"] = verify_person_crud(client, config)
            results["verify_company_crud"] = verify_company_crud(client, config)

            # Group 2: List Creation + Persistent Fixtures
            print("\n" + "=" * 50)
            print("Group 2: Lists and Persistent Fixtures")
            print("=" * 50)
            test_lists = verify_list_create_all_types(client, config)
            # Create persistent fixtures for Groups 4-7
            fixtures = create_persistent_fixtures(client, config, cleanup_items)
            results["verify_person_list_entry_operations"] = verify_person_list_entry_operations(
                client, test_lists.person_list.id, fixtures.person_id
            )
            results["verify_company_list_entry_operations"] = verify_company_list_entry_operations(
                client, test_lists.company_list.id, fixtures.company_id
            )

            # Group 3: Opportunity CRUD + Persistent Fixture
            print("\n" + "=" * 50)
            print("Group 3: Opportunity CRUD")
            print("=" * 50)
            results["verify_opportunity_crud"] = verify_opportunity_crud(
                client, config, test_lists.opportunity_list.id
            )
            create_persistent_opportunity(
                client, config, test_lists.opportunity_list.id, cleanup_items, fixtures
            )

            # Group 4: Field Operations
            print("\n" + "=" * 50)
            print("Group 4: Field Operations")
            print("=" * 50)
            results["verify_global_field_operations"] = verify_global_field_operations(
                client, config, fixtures.person_id
            )
            results["verify_list_specific_field_operations"] = (
                verify_list_specific_field_operations(
                    client, config, test_lists.person_list.id, fixtures.person_id
                )
            )

            # Group 5: Notes and Reminders
            print("\n" + "=" * 50)
            print("Group 5: Notes and Reminders")
            print("=" * 50)
            results["verify_note_on_person_crud"] = verify_note_on_person_crud(
                client, config, fixtures.person_id
            )
            results["verify_note_on_company_crud"] = verify_note_on_company_crud(
                client, config, fixtures.company_id
            )
            results["verify_note_on_opportunity_crud"] = verify_note_on_opportunity_crud(
                client, config, fixtures.opportunity_id
            )
            results["verify_one_time_reminder_crud"] = verify_one_time_reminder_crud(
                client, config, fixtures.person_id
            )
            results["verify_recurring_reminder_crud"] = verify_recurring_reminder_crud(
                client, config, fixtures.company_id
            )

            # Group 6: Interactions
            print("\n" + "=" * 50)
            print("Group 6: Interactions")
            print("=" * 50)
            results["verify_interaction_crud"] = verify_interaction_crud(
                client, config, fixtures.person_id
            )

            # Group 7: File Operations
            print("\n" + "=" * 50)
            print("Group 7: File Operations")
            print("=" * 50)
            results["verify_file_upload_bytes"] = verify_file_upload_bytes(
                client, config, fixtures.person_id
            )
            results["verify_file_upload_path"] = verify_file_upload_path(
                client, config, fixtures.company_id
            )
            results["verify_file_verification"] = verify_file_verification(
                client, config, fixtures.person_id, fixtures.company_id
            )

            # Group 8: Webhooks
            print("\n" + "=" * 50)
            print("Group 8: Webhooks")
            print("=" * 50)
            results["verify_webhook_crud"] = verify_webhook_crud(client)

            # Group 9: Beta Features (optional)
            print("\n" + "=" * 50)
            print("Group 9: Beta Features")
            print("=" * 50)
            results["verify_person_merge"] = verify_person_merge(client, config)
            results["verify_company_merge"] = verify_company_merge(client, config)

        finally:
            # Cleanup
            print("\n" + "=" * 50)
            print("Cleanup")
            print("=" * 50)
            cleanup_test_data(client, cleanup_items)

        # Report results
        print_results(results)


if __name__ == "__main__":
    main()
