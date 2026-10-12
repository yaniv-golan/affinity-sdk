"""
Inbound webhook parsing helpers.

The SDK manages webhooks via the V2 API (`client.webhooks`). This module
provides optional, framework-agnostic helpers for parsing inbound webhook payloads
sent by Affinity to your webhook URL.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Generic, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .exceptions import (
    WebhookInvalidJsonError,
    WebhookInvalidPayloadError,
    WebhookInvalidSentAtError,
    WebhookMissingKeyError,
)
from .types import WebhookEvent


class _WebhookModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        validate_assignment=True,
    )


class WebhookEnvelope(_WebhookModel):
    """
    Parsed webhook envelope.

    Affinity webhook requests use the envelope shape:
    - `type`: event string (e.g., "list_entry.created")
    - `body`: event-specific payload object
    - `sent_at`: unix epoch seconds (UTC)
    """

    type: WebhookEvent
    body: dict[str, Any] = Field(default_factory=dict)
    sent_at: datetime
    sent_at_epoch: int


class WebhookPerson(_WebhookModel):
    id: int
    type: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    primary_email: str | None = None
    emails: list[str] = Field(default_factory=list)
    company_ids: list[int] | None = Field(
        default=None,
        validation_alias=AliasChoices("organization_ids", "organizationIds"),
    )


class WebhookOrganization(_WebhookModel):
    id: int
    name: str | None = None
    domain: str | None = None
    domains: list[str] = Field(default_factory=list)
    crunchbase_uuid: str | None = None
    global_: bool | None = Field(None, alias="global")


class OrganizationMergedBody(_WebhookModel):
    changer: WebhookPerson | None = None
    removed_company: WebhookOrganization | None = None
    company: WebhookOrganization | None = None
    merged_at: str | None = None


class ListEntryCreatedBody(_WebhookModel):
    id: int
    list_id: int | None = None
    creator_id: int | None = None
    entity_id: int | None = None
    entity_type: int | None = None
    created_at: str | None = None
    entity: dict[str, Any] | None = None


class FieldValueUpdatedBody(_WebhookModel):
    id: int
    field_id: int | None = None
    list_entry_id: int | None = None
    entity_type: int | None = None
    value_type: int | None = None
    entity_id: int | None = None
    value: Any | None = None
    field: dict[str, Any] | None = None


TBody = TypeVar("TBody")
BodyParser = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class ParsedWebhook(Generic[TBody]):
    type: WebhookEvent
    body: TBody
    sent_at: datetime
    sent_at_epoch: int


def _parse_json_payload(payload: bytes | str) -> Any:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as e:
            raise WebhookInvalidJsonError("Webhook payload bytes are not valid UTF-8") from e
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise WebhookInvalidJsonError("Webhook payload is not valid JSON") from e


def _require_key(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise WebhookMissingKeyError(f"Webhook payload is missing required key: {key}", key=key)
    return data[key]


def _parse_sent_at_epoch(value: Any) -> int:
    if isinstance(value, bool):
        raise WebhookInvalidSentAtError("Webhook 'sent_at' must be an epoch seconds integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise WebhookInvalidSentAtError("Webhook 'sent_at' must be an integer epoch seconds")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    raise WebhookInvalidSentAtError("Webhook 'sent_at' must be an epoch seconds integer")


_DEFAULT_WEBHOOK_MAX_AGE_SECONDS = 300
_DEFAULT_WEBHOOK_FUTURE_SKEW_SECONDS = 120


def _normalize_now(now: datetime | None) -> datetime:
    """
    Return current UTC time, or normalize provided datetime to UTC.

    Handles naive datetimes defensively - callers may pass datetime objects
    from sources outside ISODatetime-validated models (e.g., tests, manual
    construction). This is intentional defense-in-depth.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _validate_sent_at(
    sent_at: datetime,
    *,
    now: datetime | None,
    max_age_seconds: int | None,
    max_future_skew_seconds: int | None,
) -> None:
    current = _normalize_now(now)
    if max_age_seconds is not None:
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")
        oldest = current - timedelta(seconds=max_age_seconds)
        if sent_at < oldest:
            raise WebhookInvalidSentAtError(
                "Webhook 'sent_at' is too old for the allowed replay window."
            )
    if max_future_skew_seconds is not None:
        if max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be >= 0")
        latest = current + timedelta(seconds=max_future_skew_seconds)
        if sent_at > latest:
            raise WebhookInvalidSentAtError(
                "Webhook 'sent_at' is too far in the future for the allowed clock skew."
            )


def parse_webhook(
    payload: bytes | str | Mapping[str, Any],
    *,
    max_age_seconds: int | None = _DEFAULT_WEBHOOK_MAX_AGE_SECONDS,
    max_future_skew_seconds: int | None = _DEFAULT_WEBHOOK_FUTURE_SKEW_SECONDS,
    now: datetime | None = None,
) -> WebhookEnvelope:
    """
    Parse an inbound webhook payload into a `WebhookEnvelope`.

    Args:
        payload: Raw request body as bytes/str, or an already-decoded dict.
        max_age_seconds: Reject payloads older than this many seconds (None disables).
        max_future_skew_seconds: Reject payloads too far in the future (None disables).
        now: Override the current time (UTC) for testing.

    Raises:
        WebhookInvalidJsonError: If payload is not valid JSON (bytes/str inputs).
        WebhookMissingKeyError: If required keys are missing.
        WebhookInvalidPayloadError: If the decoded payload isn't a JSON object.
        WebhookInvalidSentAtError: If `sent_at` is invalid or outside the allowed window.
    """

    data: Any = _parse_json_payload(payload) if isinstance(payload, (bytes, str)) else payload

    if not isinstance(data, Mapping):
        raise WebhookInvalidPayloadError("Webhook payload must be a JSON object at the top level")

    event_type = _require_key(data, "type")
    body = _require_key(data, "body")
    sent_at_raw = _require_key(data, "sent_at")

    if not isinstance(body, Mapping):
        raise WebhookInvalidPayloadError("Webhook 'body' must be a JSON object")

    sent_at_epoch = _parse_sent_at_epoch(sent_at_raw)
    sent_at = datetime.fromtimestamp(sent_at_epoch, tz=timezone.utc)
    _validate_sent_at(
        sent_at,
        now=now,
        max_age_seconds=max_age_seconds,
        max_future_skew_seconds=max_future_skew_seconds,
    )

    return WebhookEnvelope.model_validate(
        {
            "type": event_type,
            "body": dict(body),
            "sent_at": sent_at,
            "sent_at_epoch": sent_at_epoch,
        }
    )


def _coerce_handler(handler: type[BaseModel] | BodyParser) -> BodyParser:
    if isinstance(handler, type) and issubclass(handler, BaseModel):
        return handler.model_validate
    return handler


class BodyRegistry:
    """
    Registry mapping webhook event types to body parsers.

    Parsers should accept a JSON object (dict) and return either a Pydantic model
    or any other parsed representation.
    """

    def __init__(self, handlers: Mapping[WebhookEvent, type[BaseModel] | BodyParser] | None = None):
        self._handlers: dict[WebhookEvent, BodyParser] = {}
        if handlers:
            for event, handler in handlers.items():
                self.register(event, handler)

    def register(self, event: WebhookEvent | str, handler: type[BaseModel] | BodyParser) -> None:
        self._handlers[WebhookEvent(event)] = _coerce_handler(handler)

    def parse_body(self, event: WebhookEvent, body: dict[str, Any]) -> Any:
        parser = self._handlers.get(event)
        return parser(body) if parser else body


DEFAULT_BODY_REGISTRY = BodyRegistry(
    {
        WebhookEvent.ORGANIZATION_MERGED: OrganizationMergedBody,
        WebhookEvent.LIST_ENTRY_CREATED: ListEntryCreatedBody,
        WebhookEvent.FIELD_VALUE_UPDATED: FieldValueUpdatedBody,
        WebhookEvent.PERSON_CREATED: WebhookPerson,
        WebhookEvent.ORGANIZATION_CREATED: WebhookOrganization,
    }
)


def dispatch_webhook(
    envelope: WebhookEnvelope,
    *,
    registry: BodyRegistry = DEFAULT_BODY_REGISTRY,
) -> ParsedWebhook[BaseModel | dict[str, Any]]:
    """
    Parse the envelope body using a registry and return a `ParsedWebhook`.

    If the event type has no registered parser, the body remains a dict.
    """

    parsed_body = registry.parse_body(envelope.type, envelope.body)
    return ParsedWebhook(
        type=envelope.type,
        body=parsed_body,
        sent_at=envelope.sent_at,
        sent_at_epoch=envelope.sent_at_epoch,
    )
