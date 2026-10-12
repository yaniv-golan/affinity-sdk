# Migrating to 2.0

SDK 2.0 moves every operation Affinity's V2 API can do onto V2. Most code keeps working; this
page lists what changes and what to do. The [API versions & routing](api-versions-and-routing.md)
guide lists the few cases that still use V1, and the [API coverage](../reference/api-coverage.md)
page shows every operation.

## Before you upgrade

- **API version pins.** Many moved operations need a newer Affinity API version (webhooks,
  reminder get/delete and person create/delete need `2026-07-15`; reminder update, opportunity
  rename/delete, adding to a list and V2 file reads need `2026-09-17`). The SDK sends the version
  each operation needs, unless you pinned one (`affinity_api_version=`, CLI `--api-version`,
  `AFFINITY_API_VERSION`, the MCP bundle's "Affinity API Version" setting). A pin older than the
  operation needs raises `ApiVersionTooOldError` before anything is sent: raise or remove it.
- **Permissions.** On V2, only a reminder's creator or owner may change it (the SDK retries
  through V1 when V2 refuses), opportunity rename/delete needs "Export data from Lists" and
  manage access, and a public list needs "Share accessible Lists globally".

## Webhooks

| 1.x | 2.0 |
|---|---|
| `WebhookSubscription` | `Webhook` |
| `.webhook_url` | `.url` |
| `.disabled` | `.status` (`WebhookStatus.ACTIVE` / `DISABLED`) |
| `.created_by` | `.creator.id` (`creator` may be `None`) |
| `WebhookCreate(webhook_url=…)` | `WebhookCreate(url=…, subscriptions=[…])`: `subscriptions` is required, `[]` = every event |
| `WebhookUpdate(disabled=True)` | `WebhookUpdate(status=WebhookStatus.DISABLED)` |
| `webhooks.list() -> list` | one page; `webhooks.iter()` for all |
| `update()` | returns the webhook as read back (two requests) |
| `delete() -> bool` | `-> None` |

Without permission to manage all webhooks, you see and change only your own. 1.x field names
raise `ValidationError` instead of being ignored. The CLI has `xaffinity webhook
ls|get|create|update|delete`; create, update and delete ask for confirmation.

## Reminders

| 1.x | 2.0 |
|---|---|
| `ReminderType.ONE_TIME == 0` (int enums) | string values (`"one-time"`); member names unchanged, the 1.x integers are still accepted |
| `ReminderResetType.MEETING` | alias of `EVENT` (`"event"`) |
| `Reminder.creator/owner/completer/person` (names, emails) | `PersonRef` (id only) |
| `Reminder.company/opportunity` (dicts) | `CompanyRef` / `OpportunityRef` |
| `Reminder.reset_type/reminder_days` | still there (read-only), from `Reminder.recurrence` |
| `list(page_size=, page_token=)` | `list(limit=, cursor=)` (1-100), soonest due first; `iter()` for all |
| `ReminderUpdate(content=None)` cleared nothing | still nothing; `clear_content=True` removes the content |
| a reminder without a person, company or opportunity | `ValueError` (both APIs require one) |

`update()` returns the reminder as read back; `list_batch()` is on the sync client too.

## Notes

| 1.x | 2.0 |
|---|---|
| `notes.create() / update() -> Note` | `-> NoteV2` (`content.html`, `creator`) |
| plain text stored as plain text | stored as HTML; the SDK escapes and wraps plain text, so it shows as typed |
| `NoteUpdate(content=…)` kept the note's type | `NoteUpdate.type` says how `content` is written (plain text unless `HTML`) |
| `type=HTML` with any tags | `<b>`/`<i>` become `<strong>`/`<em>`; tags Affinity rejects raise `ValueError` naming the allowed ones |
| `parent_id` with entity ids (ids ignored) | `ValidationError` |

`note get`/`note ls` and `notes.list/get` still use V1, which shows notes stored as HTML in a
markdown-like form. Changing the text of a note that @mentions people is refused by the API.

## Persons, opportunities, lists

| 1.x | 2.0 |
|---|---|
| `persons.create()` | V2 (first email is primary; `last_name` accepts `None`); with `company_ids`, V1 as before (needs a last name) |
| `opportunities.update(name=…)` | V2; the result's `person_ids`/`company_ids` are empty (use `get_details()`) |
| `opportunities.update(person_ids=/company_ids=)` | V1, unchanged |
| `OpportunityUpdate()` with nothing set | `ValueError` |
| `lists.get().fields` | `None`: use `lists.get_fields()` |
| `lists.create()` | V2 (you become the owner); with `owner_id`/`additional_permissions`, V1 |
| `entries(list_id).add_opportunity()` | removed: create the opportunity on its list (`opportunities.create(OpportunityCreate(list_id=…))`) |

## Files and rate limits

`files.get/list/iter` are unchanged (V1). New: `files.get_v2()` (with a download URL that
expires after 60 seconds) and `files.list_v2()`/`iter_v2()` (every file you can see, newest
first). `rate_limits.refresh()` reads the V2 endpoint; `RateLimitSnapshot` is unchanged.

## CLI and MCP

- `reminder ls --page-size` is at most 100; `reminder update --clear-content`; `--reset-type
  event` (`meeting` still accepted).
- `note create --type` is `plain-text` or `html`; `note update --type`.
- `opportunity update --name` alone no longer prints `personIds`/`organizationIds`.
- `list get <id>` no longer has `.data.list.fields` (use `.data.fields`).
- The MCP server needs CLI 2.0; it adds the webhook commands, and `webhook create`/`update`/`delete`
  need the user's confirmation.
