"""Tests for unreplied message detection functionality.

Tests the check_unreplied function and --check-unreplied CLI flag.
Supports both email and chat message types.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from affinity.cli.interaction_utils import (
    UNREPLIED_CSV_COLUMNS,
    check_unreplied,
    flatten_unreplied_for_csv,
)
from affinity.models.types import InteractionDirection, InteractionType


class TestCheckUnreplied:
    """Tests for check_unreplied function."""

    def test_returns_none_when_no_messages(self) -> None:
        """Should return None when entity has no interactions."""
        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        assert result is None

    def test_returns_none_when_no_incoming_emails(self) -> None:
        """Should return None when all emails are outgoing."""
        mock_email = MagicMock()
        mock_email.date = datetime.now(timezone.utc) - timedelta(days=1)
        mock_email.direction = InteractionDirection.OUTGOING
        mock_email.type = InteractionType.EMAIL
        mock_email.subject = "Re: Hello"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([mock_email])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        assert result is None

    def test_returns_none_when_incoming_has_reply(self) -> None:
        """Should return None when last incoming email has a reply."""
        # Incoming email 2 days ago
        incoming = MagicMock()
        incoming.date = datetime.now(timezone.utc) - timedelta(days=2)
        incoming.direction = InteractionDirection.INCOMING
        incoming.type = InteractionType.EMAIL
        incoming.subject = "Question"

        # Outgoing reply 1 day ago
        reply = MagicMock()
        reply.date = datetime.now(timezone.utc) - timedelta(days=1)
        reply.direction = InteractionDirection.OUTGOING
        reply.type = InteractionType.EMAIL
        reply.subject = "Re: Question"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming, reply])

        result = check_unreplied(
            client=mock_client,
            entity_type="person",
            entity_id=456,
            lookback_days=30,
        )

        assert result is None

    def test_returns_unreplied_email_when_no_reply(self) -> None:
        """Should return unreplied email info when no reply exists."""
        # Incoming email 2 days ago - no reply
        incoming = MagicMock()
        email_date = datetime.now(timezone.utc) - timedelta(days=2)
        incoming.date = email_date
        incoming.direction = InteractionDirection.INCOMING
        incoming.type = InteractionType.EMAIL
        incoming.subject = "Urgent: Need response"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        assert result is not None
        assert "date" in result
        assert result["daysSince"] == 2
        assert result["type"] == "email"
        assert result["subject"] == "Urgent: Need response"

    def test_returns_unreplied_chat_when_no_reply(self) -> None:
        """Should return unreplied chat info when no reply exists."""
        # Incoming chat 1 day ago - no reply
        incoming = MagicMock()
        chat_date = datetime.now(timezone.utc) - timedelta(days=1)
        incoming.date = chat_date
        incoming.direction = InteractionDirection.INCOMING
        incoming.type = InteractionType.CHAT_MESSAGE
        # Chat messages don't have subject attribute
        del incoming.subject

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        assert result is not None
        assert "date" in result
        assert result["daysSince"] == 1
        assert result["type"] == "chat"
        assert result["subject"] is None

    def test_cross_type_reply_email_to_chat(self) -> None:
        """Email replied via chat should count as replied."""
        # Incoming email 2 days ago
        incoming_email = MagicMock()
        incoming_email.date = datetime.now(timezone.utc) - timedelta(days=2)
        incoming_email.direction = InteractionDirection.INCOMING
        incoming_email.type = InteractionType.EMAIL
        incoming_email.subject = "Question via email"

        # Outgoing chat reply 1 day ago
        reply_chat = MagicMock()
        reply_chat.date = datetime.now(timezone.utc) - timedelta(days=1)
        reply_chat.direction = InteractionDirection.OUTGOING
        reply_chat.type = InteractionType.CHAT_MESSAGE

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming_email, reply_chat])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        # Should be None because there's a reply (even though different type)
        assert result is None

    def test_cross_type_reply_chat_to_email(self) -> None:
        """Chat replied via email should count as replied."""
        # Incoming chat 2 days ago
        incoming_chat = MagicMock()
        incoming_chat.date = datetime.now(timezone.utc) - timedelta(days=2)
        incoming_chat.direction = InteractionDirection.INCOMING
        incoming_chat.type = InteractionType.CHAT_MESSAGE

        # Outgoing email reply 1 day ago
        reply_email = MagicMock()
        reply_email.date = datetime.now(timezone.utc) - timedelta(days=1)
        reply_email.direction = InteractionDirection.OUTGOING
        reply_email.type = InteractionType.EMAIL
        reply_email.subject = "Re: Chat message"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming_chat, reply_email])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        # Should be None because there's a reply (even though different type)
        assert result is None

    def test_filters_by_interaction_types_email_only(self) -> None:
        """Should only check email when types=[EMAIL]."""
        # Incoming chat 1 day ago (more recent)
        incoming_chat = MagicMock()
        incoming_chat.date = datetime.now(timezone.utc) - timedelta(days=1)
        incoming_chat.direction = InteractionDirection.INCOMING
        incoming_chat.type = InteractionType.CHAT_MESSAGE

        # Incoming email 2 days ago
        incoming_email = MagicMock()
        incoming_email.date = datetime.now(timezone.utc) - timedelta(days=2)
        incoming_email.direction = InteractionDirection.INCOMING
        incoming_email.type = InteractionType.EMAIL
        incoming_email.subject = "Email subject"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming_chat, incoming_email])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            interaction_types=[InteractionType.EMAIL],
            lookback_days=30,
        )

        # Should return the email, not the chat
        assert result is not None
        assert result["type"] == "email"
        assert result["subject"] == "Email subject"

    def test_filters_by_interaction_types_chat_only(self) -> None:
        """Should only check chat when types=[CHAT_MESSAGE]."""
        # Incoming email 1 day ago (more recent)
        incoming_email = MagicMock()
        incoming_email.date = datetime.now(timezone.utc) - timedelta(days=1)
        incoming_email.direction = InteractionDirection.INCOMING
        incoming_email.type = InteractionType.EMAIL
        incoming_email.subject = "Email subject"

        # Incoming chat 2 days ago
        incoming_chat = MagicMock()
        incoming_chat.date = datetime.now(timezone.utc) - timedelta(days=2)
        incoming_chat.direction = InteractionDirection.INCOMING
        incoming_chat.type = InteractionType.CHAT_MESSAGE

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming_email, incoming_chat])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            interaction_types=[InteractionType.CHAT_MESSAGE],
            lookback_days=30,
        )

        # Should return the chat, not the email
        assert result is not None
        assert result["type"] == "chat"

    def test_ignores_meeting_and_call_types(self) -> None:
        """Should ignore MEETING and CALL types (no direction semantics)."""
        # Meeting interaction
        meeting = MagicMock()
        meeting.date = datetime.now(timezone.utc) - timedelta(days=1)
        meeting.type = InteractionType.MEETING
        meeting.direction = None

        # Call interaction
        call = MagicMock()
        call.date = datetime.now(timezone.utc) - timedelta(days=2)
        call.type = InteractionType.CALL
        call.direction = None

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([meeting, call])

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        # Should return None (no directional interactions)
        assert result is None

    def test_finds_most_recent_unreplied_incoming(self) -> None:
        """Should find the most recent incoming message without a reply."""
        # Older incoming (3 days ago)
        older_incoming = MagicMock()
        older_incoming.date = datetime.now(timezone.utc) - timedelta(days=3)
        older_incoming.direction = InteractionDirection.INCOMING
        older_incoming.type = InteractionType.EMAIL
        older_incoming.subject = "Old email"

        # Newer incoming (1 day ago) - most recent
        newer_incoming = MagicMock()
        newer_incoming.date = datetime.now(timezone.utc) - timedelta(days=1)
        newer_incoming.direction = InteractionDirection.INCOMING
        newer_incoming.type = InteractionType.EMAIL
        newer_incoming.subject = "New email"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([older_incoming, newer_incoming])

        result = check_unreplied(
            client=mock_client,
            entity_type="person",
            entity_id=789,
            lookback_days=30,
        )

        assert result is not None
        assert result["daysSince"] == 1
        assert result["subject"] == "New email"

    def test_supports_opportunity_entity_type(self) -> None:
        """Should support opportunity entity type."""
        incoming = MagicMock()
        incoming.date = datetime.now(timezone.utc) - timedelta(days=1)
        incoming.direction = InteractionDirection.INCOMING
        incoming.type = InteractionType.EMAIL
        incoming.subject = "Opportunity inquiry"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming])

        result = check_unreplied(
            client=mock_client,
            entity_type="opportunity",
            entity_id=123,
            lookback_days=30,
        )

        assert result is not None
        assert result["subject"] == "Opportunity inquiry"

    def test_supports_v1_integer_entity_types(self) -> None:
        """Should support V1 integer entity types (0=person, 1=company)."""
        incoming = MagicMock()
        incoming.date = datetime.now(timezone.utc) - timedelta(days=1)
        incoming.direction = InteractionDirection.INCOMING
        incoming.type = InteractionType.EMAIL
        incoming.subject = "Test"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming])

        # Test V1 company (1)
        result = check_unreplied(
            client=mock_client,
            entity_type=1,
            entity_id=123,
            lookback_days=30,
        )
        assert result is not None

        # Test V1 person (0)
        mock_client.interactions.iter.return_value = iter([incoming])
        result = check_unreplied(
            client=mock_client,
            entity_type=0,
            entity_id=456,
            lookback_days=30,
        )
        assert result is not None

    def test_supports_organization_alias(self) -> None:
        """Should support 'organization' as alias for 'company'."""
        incoming = MagicMock()
        incoming.date = datetime.now(timezone.utc) - timedelta(days=1)
        incoming.direction = InteractionDirection.INCOMING
        incoming.type = InteractionType.EMAIL
        incoming.subject = "Test"

        mock_client = MagicMock()
        mock_client.interactions.iter.return_value = iter([incoming])

        result = check_unreplied(
            client=mock_client,
            entity_type="organization",
            entity_id=123,
            lookback_days=30,
        )

        assert result is not None

    def test_handles_unsupported_entity_type(self) -> None:
        """Should return None for unsupported entity types."""
        mock_client = MagicMock()

        result = check_unreplied(
            client=mock_client,
            entity_type="unknown_type",
            entity_id=123,
            lookback_days=30,
        )

        assert result is None

    def test_gone_entity_reads_as_nothing_unreplied(self) -> None:
        """Should return None when the entity is gone."""
        from affinity.exceptions import NotFoundError

        mock_client = MagicMock()
        mock_client.interactions.iter.side_effect = NotFoundError("Company not found")

        result = check_unreplied(
            client=mock_client,
            entity_type="company",
            entity_id=123,
            lookback_days=30,
        )

        assert result is None

    def test_other_api_errors_propagate(self) -> None:
        """Any error but a gone entity propagates (None would read as "nothing unreplied")."""
        from affinity.exceptions import ValidationError

        mock_client = MagicMock()
        mock_client.interactions.iter.side_effect = ValidationError("Bad request")

        with pytest.raises(ValidationError):
            check_unreplied(
                client=mock_client,
                entity_type="company",
                entity_id=123,
                lookback_days=30,
            )


class TestFlattenUnrepliedForCsv:
    """Tests for flatten_unreplied_for_csv function."""

    def test_returns_all_columns_empty_when_none(self) -> None:
        """Should return all columns with empty strings when input is None."""
        result = flatten_unreplied_for_csv(None)

        assert len(result) == len(UNREPLIED_CSV_COLUMNS)
        for col in UNREPLIED_CSV_COLUMNS:
            assert col in result
            assert result[col] == ""

    def test_returns_all_columns_empty_when_empty_dict(self) -> None:
        """Should return all columns with empty strings for empty dict."""
        result = flatten_unreplied_for_csv({})

        assert len(result) == len(UNREPLIED_CSV_COLUMNS)
        for col in UNREPLIED_CSV_COLUMNS:
            assert result[col] == ""

    def test_flattens_unreplied_email_data(self) -> None:
        """Should flatten unreplied email data correctly."""
        unreplied_data = {
            "date": "2026-01-10T10:00:00+00:00",
            "daysSince": 5,
            "type": "email",
            "subject": "Need response",
        }

        result = flatten_unreplied_for_csv(unreplied_data)

        assert result["unrepliedDate"] == "2026-01-10T10:00:00+00:00"
        assert result["unrepliedDaysSince"] == "5"
        assert result["unrepliedType"] == "email"
        assert result["unrepliedSubject"] == "Need response"

    def test_flattens_unreplied_chat_data(self) -> None:
        """Should flatten unreplied chat data correctly (no subject)."""
        unreplied_data = {
            "date": "2026-01-10T10:00:00+00:00",
            "daysSince": 3,
            "type": "chat",
            "subject": None,
        }

        result = flatten_unreplied_for_csv(unreplied_data)

        assert result["unrepliedDate"] == "2026-01-10T10:00:00+00:00"
        assert result["unrepliedDaysSince"] == "3"
        assert result["unrepliedType"] == "chat"
        assert result["unrepliedSubject"] == ""

    def test_handles_missing_subject(self) -> None:
        """Should handle missing subject gracefully."""
        unreplied_data = {
            "date": "2026-01-10T10:00:00+00:00",
            "daysSince": 5,
            "type": "email",
            "subject": None,
        }

        result = flatten_unreplied_for_csv(unreplied_data)

        assert result["unrepliedSubject"] == ""

    def test_handles_days_since_zero(self) -> None:
        """Should handle daysSince=0 correctly."""
        unreplied_data = {
            "date": "2026-01-17T10:00:00+00:00",
            "daysSince": 0,
            "type": "email",
            "subject": "Today's email",
        }

        result = flatten_unreplied_for_csv(unreplied_data)

        assert result["unrepliedDaysSince"] == "0"


class TestUnrepliedCsvColumns:
    """Tests for UNREPLIED_CSV_COLUMNS constant."""

    def test_has_expected_columns(self) -> None:
        """Should have all expected columns."""
        expected = [
            "unrepliedDate",
            "unrepliedDaysSince",
            "unrepliedType",
            "unrepliedSubject",
        ]
        assert expected == UNREPLIED_CSV_COLUMNS

    def test_has_four_columns(self) -> None:
        """Should have exactly 4 columns (includes type)."""
        assert len(UNREPLIED_CSV_COLUMNS) == 4


class TestParseUnrepliedTypes:
    """Tests for _parse_unreplied_types CLI helper function."""

    def test_parses_email_only(self) -> None:
        """Should parse 'email' to [EMAIL]."""
        from affinity.cli.commands.list_cmds import _parse_unreplied_types

        result = _parse_unreplied_types("email")
        assert result == [InteractionType.EMAIL]

    def test_parses_chat_only(self) -> None:
        """Should parse 'chat' to [CHAT_MESSAGE]."""
        from affinity.cli.commands.list_cmds import _parse_unreplied_types

        result = _parse_unreplied_types("chat")
        assert result == [InteractionType.CHAT_MESSAGE]

    def test_parses_email_and_chat(self) -> None:
        """Should parse 'email,chat' to [EMAIL, CHAT_MESSAGE]."""
        from affinity.cli.commands.list_cmds import _parse_unreplied_types

        result = _parse_unreplied_types("email,chat")
        assert InteractionType.EMAIL in result
        assert InteractionType.CHAT_MESSAGE in result
        assert len(result) == 2

    def test_parses_all_shorthand(self) -> None:
        """Should parse 'all' to [EMAIL, CHAT_MESSAGE]."""
        from affinity.cli.commands.list_cmds import _parse_unreplied_types

        result = _parse_unreplied_types("all")
        assert InteractionType.EMAIL in result
        assert InteractionType.CHAT_MESSAGE in result
        assert len(result) == 2

    def test_rejects_meeting_type(self) -> None:
        """Should raise CLIError for 'meeting' (no direction)."""
        import pytest

        from affinity.cli.commands.list_cmds import _parse_unreplied_types
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError) as exc_info:
            _parse_unreplied_types("meeting")

        assert "Invalid unreplied types" in str(exc_info.value)
        assert "meeting" in str(exc_info.value)

    def test_rejects_call_type(self) -> None:
        """Should raise CLIError for 'call' (no direction)."""
        import pytest

        from affinity.cli.commands.list_cmds import _parse_unreplied_types
        from affinity.cli.errors import CLIError

        with pytest.raises(CLIError) as exc_info:
            _parse_unreplied_types("call")

        assert "Invalid unreplied types" in str(exc_info.value)

    def test_handles_whitespace(self) -> None:
        """Should handle whitespace around types."""
        from affinity.cli.commands.list_cmds import _parse_unreplied_types

        result = _parse_unreplied_types("  email  ,  chat  ")
        assert InteractionType.EMAIL in result
        assert InteractionType.CHAT_MESSAGE in result

    def test_handles_case_insensitive(self) -> None:
        """Should be case-insensitive."""
        from affinity.cli.commands.list_cmds import _parse_unreplied_types

        result = _parse_unreplied_types("EMAIL,CHAT")
        assert InteractionType.EMAIL in result
        assert InteractionType.CHAT_MESSAGE in result
