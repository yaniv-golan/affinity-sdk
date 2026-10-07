# Webhooks

The SDK supports managing webhook subscriptions via the V1 API (`client.webhooks`). Receiving webhooks is handled by **your** web server/app.

## Supported Webhook Events

The SDK defines all webhook events in `WebhookEvent`. You can subscribe to any combination of these events:

| Event | Value | Description |
|-------|-------|-------------|
| `LIST_CREATED` | `list.created` | A new list was created |
| `LIST_UPDATED` | `list.updated` | A list's properties were modified |
| `LIST_DELETED` | `list.deleted` | A list was deleted |
| `LIST_ENTRY_CREATED` | `list_entry.created` | An entity was added to a list |
| `LIST_ENTRY_DELETED` | `list_entry.deleted` | An entity was removed from a list |
| `NOTE_CREATED` | `note.created` | A note was created on an entity |
| `NOTE_UPDATED` | `note.updated` | A note's content was modified |
| `NOTE_DELETED` | `note.deleted` | A note was deleted |
| `FIELD_CREATED` | `field.created` | A new field was created |
| `FIELD_UPDATED` | `field.updated` | A field's properties were modified |
| `FIELD_DELETED` | `field.deleted` | A field was deleted |
| `FIELD_VALUE_CREATED` | `field_value.created` | A field value was set on an entity |
| `FIELD_VALUE_UPDATED` | `field_value.updated` | A field value was modified |
| `FIELD_VALUE_DELETED` | `field_value.deleted` | A field value was removed |
| `PERSON_CREATED` | `person.created` | A new person was created |
| `PERSON_UPDATED` | `person.updated` | A person's properties were modified |
| `PERSON_DELETED` | `person.deleted` | A person was deleted |
| `ORGANIZATION_CREATED` | `organization.created` | A new company/organization was created |
| `ORGANIZATION_UPDATED` | `organization.updated` | A company's properties were modified |
| `ORGANIZATION_DELETED` | `organization.deleted` | A company was deleted |
| `ORGANIZATION_MERGED` | `organization.merged` | Two companies were merged |
| `OPPORTUNITY_CREATED` | `opportunity.created` | A new opportunity was created |
| `OPPORTUNITY_UPDATED` | `opportunity.updated` | An opportunity's properties were modified |
| `OPPORTUNITY_DELETED` | `opportunity.deleted` | An opportunity was deleted |
| `FILE_CREATED` | `file.created` | A file was uploaded to an entity |
| `FILE_DELETED` | `file.deleted` | A file was deleted |
| `SMART_INTERACTION_VALUE_UPDATED` | `smart_interaction_value.updated` | A smart interaction field value was updated (listed by the V2 API; Affinity does not document the payload) |
| `REMINDER_CREATED` | `reminder.created` | A reminder was created |
| `REMINDER_UPDATED` | `reminder.updated` | A reminder's properties were modified |
| `REMINDER_DELETED` | `reminder.deleted` | A reminder was deleted |

!!! tip "Forward Compatibility"
    `WebhookEvent` extends `OpenStrEnum`, meaning unknown event types from Affinity are preserved as strings rather than causing errors. This ensures the SDK gracefully handles any new events Affinity may add in the future.

## Create a subscription

```python
from affinity import Affinity
from affinity.models import WebhookCreate
from affinity.types import WebhookEvent

with Affinity.from_env() as client:
    webhook = client.webhooks.create(
        WebhookCreate(
            webhook_url="https://example.com/webhooks/affinity/<random-secret>",
            subscriptions=[
                WebhookEvent.FIELD_VALUE_UPDATED,
                WebhookEvent.LIST_ENTRY_CREATED,
            ],
        )
    )
    print(webhook.id, webhook.webhook_url)
```

!!! note "Notes"
    - Affinity limits webhook subscriptions (see `WebhookService` docs).
    - Affinity may attempt to contact your `webhook_url` during creation/updates; ensure your endpoint is reachable and responds quickly.

## Securing your webhook endpoint

!!! warning "No signature verification available"
    Affinity's V1 API does not provide cryptographic signature verification for webhook requests. There is no HMAC header, signing secret, or other mechanism to verify that requests originate from Affinity. You must rely on defense-in-depth practices to secure your endpoint.

Since webhook authenticity cannot be cryptographically verified, treat your webhook endpoint as a semi-public entry point and apply multiple layers of protection:

### Required: Secret URL path

Include a long, random, unguessable secret in your webhook URL path:

```
https://example.com/webhooks/affinity/a1b2c3d4e5f6g7h8i9j0...
```

- Generate at least 32 characters of cryptographically random data
- Reject any request where the path secret doesn't match
- Rotate the secret periodically and after any suspected exposure
- Never log the full URL or share it in plain text

### Required: HTTPS only

- Always use HTTPS for your webhook URL
- Terminate TLS at your load balancer or reverse proxy
- Reject HTTP requests at the application level as a fallback

### Required: Request validation

- **Method**: Only accept `POST` requests
- **Content-Type**: Require `application/json`
- **Body size**: Enforce a reasonable limit (e.g., 1MB)
- **JSON parsing**: Use strict parsing; reject malformed payloads

### Recommended: Replay protection

Use the `sent_at` field in the webhook payload to reject stale events:

```python
from affinity import parse_webhook
from affinity.exceptions import WebhookInvalidSentAtError

try:
    # Reject events older than 5 minutes (300 seconds)
    envelope = parse_webhook(raw_body, max_age_seconds=300)
except WebhookInvalidSentAtError:
    # Event is too old or too far in the future
    return Response(status=400)
```

For stronger replay protection, store a short-lived dedupe key:

```python
import hashlib

# Generate a dedupe key from event properties
dedupe_key = f"{envelope.type}:{envelope.sent_at_epoch}:{hashlib.sha256(raw_body).hexdigest()[:16]}"

# Check against a cache (Redis, memcached, etc.) with 5-10 minute TTL
if cache.exists(dedupe_key):
    return Response(status=200)  # Already processed, acknowledge silently
cache.set(dedupe_key, "1", ttl=600)
```

### Recommended: IP allowlisting

If Affinity provides stable egress IP ranges for your account:

- Configure your load balancer or WAF to only accept webhook traffic from those IPs
- Contact Affinity support to request their webhook delivery IP ranges

If IP ranges are not available:

- Restrict by geography or ASN where appropriate
- Alert on unexpected source IPs for investigation
- Apply rate limiting and bot protection at the edge

### Recommended: Fast response with async processing

- Respond with `2xx` immediately after basic validation
- Enqueue the actual processing for async handling
- Assume retries can happen (Affinity retries with exponential backoff for up to 10 hours)
- Make your processing idempotent using the dedupe key pattern above

### Recommended: Logging considerations

- Avoid logging raw webhook payloads (may contain PII)
- If logging is required, redact sensitive fields or use a PII-safe pipeline
- Log the event type, timestamp, and dedupe key for debugging

## Parse inbound payloads (optional)

The SDK includes small, framework-agnostic helpers to parse the webhook envelope and (optionally) dispatch to a typed body for a few common events.

```python
from affinity import dispatch_webhook, parse_webhook
from affinity.types import WebhookEvent

envelope = parse_webhook(raw_body_bytes)  # or raw str / dict
event = dispatch_webhook(envelope)  # typed for some events, dict otherwise

if event.type == WebhookEvent.LIST_ENTRY_CREATED:
    # event.body may be typed (or a dict, depending on the event)
    print(event.sent_at, event.body)
```

## Minimal receiver example (FastAPI)

```python
import hashlib
import secrets

from fastapi import FastAPI, HTTPException, Header, Request

from affinity import dispatch_webhook, parse_webhook
from affinity.exceptions import WebhookInvalidSentAtError, WebhookParseError

app = FastAPI()

# Generate with: secrets.token_urlsafe(32)
WEBHOOK_SECRET = "replace-with-a-long-random-string-at-least-32-chars"


@app.post("/webhooks/affinity/{secret}")
async def affinity_webhook(
    secret: str,
    request: Request,
    content_type: str = Header(default=""),
) -> dict[str, str]:
    # 1. Validate secret path
    if not secrets.compare_digest(secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=404)  # 404 to avoid confirming endpoint exists

    # 2. Validate content type
    if not content_type.startswith("application/json"):
        raise HTTPException(status_code=415, detail="unsupported media type")

    # 3. Read and validate body size
    raw = await request.body()
    if len(raw) > 1_000_000:  # 1MB limit
        raise HTTPException(status_code=413, detail="payload too large")

    # 4. Parse with replay protection (rejects events older than 5 minutes)
    try:
        envelope = parse_webhook(raw, max_age_seconds=300)
    except WebhookInvalidSentAtError:
        raise HTTPException(status_code=400, detail="stale event")
    except WebhookParseError:
        raise HTTPException(status_code=400, detail="invalid payload")

    # 5. Optional: Check dedupe key against cache here

    # 6. Dispatch to typed body (if registered) and process
    event = dispatch_webhook(envelope)

    # TODO: Enqueue for async processing instead of processing inline
    # process_webhook_event.delay(event)

    return {"ok": "true"}
```
