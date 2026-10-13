# Migrating to 2.0

SDK 2.0 moves every operation Affinity's V2 API can do onto V2. Most code keeps working; this
page lists what changes and what to do. The [API versions & routing](api-versions-and-routing.md)
guide lists the few cases that still use V1, and the [API coverage](../reference/api-coverage.md)
page shows every operation.

## Before you upgrade

- **API version pins.** Many moved operations need a newer Affinity API version (webhooks,
  reminder get/delete and the rate-limit endpoint need `2026-07-15`; reminder update and the V2
  file reads need `2026-09-17`). The SDK sends the version
  each operation needs, unless you pinned one (`affinity_api_version=`, CLI `--api-version`,
  `AFFINITY_API_VERSION`, the MCP bundle's "Affinity API Version" setting). A pin older than the
  operation needs raises `ApiVersionTooOldError` before anything is sent: raise or remove it.
- **Permissions.** On V2, only a reminder's creator or owner may change it (the SDK retries
  through V1 when V2 refuses), and a public list needs "Share accessible Lists globally".

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

`notes.get()` returns `NoteV2` (`includes=True` adds the attached companies, persons and
opportunities); `notes.list_v2()`/`iter_v2()`/`get_v2()` are now `notes.feed()`/`iter_feed()`/
`get()`. `notes.list()` (one entity's notes) and CLI `note ls` still use V1, which shows notes
stored as HTML in a markdown-like form. Changing the text of a note that @mentions people is refused by the API.

## Lists

| 1.x | 2.0 |
|---|---|
| `lists.get().fields` | `None`: use `lists.get_fields()` |
| `lists.create()` | V2 (you become the owner); with `owner_id`/`additional_permissions`, V1 |
| `entries(list_id).add_opportunity()` | removed: create the opportunity on its list (`opportunities.create(OpportunityCreate(list_id=…))`) |

Person create/delete, opportunity rename/delete and adding people or companies to a list are
unchanged (V1): Affinity's V2 spec documents them, but the API didn't serve them when tested (2026-10-12).
`Person.company_ids` and `Opportunity.company_ids` are now filled from V1 responses.

## Files and rate limits

`files.get/list/iter` are unchanged (V1). `files.get_download_url()` uses V2 when it can (one
request for the details and a URL valid 60 seconds) and V1 otherwise. New:
`files.feed()`/`iter_feed()` (every file you can see, newest first, as `FileV2`). `rate_limits.refresh()` reads the V2 endpoint; `RateLimitSnapshot` is unchanged.

## CLI and MCP

- `reminder ls --page-size` is at most 100; `reminder update --clear-content`; `--reset-type
  event` (`meeting` still accepted).
- `note create --type` is `plain-text` or `html`; `note update --type`.
- `list get <id>` no longer has `.data.list.fields` (use `.data.fields`).
- The MCP server needs CLI 2.0; it adds the webhook commands, and `webhook create`/`update`/`delete`
  need the user's confirmation.

## Filters in 2.0.2

From 2.0.2, filters mean what Affinity's own search means, so a filter gives the same answer
whether Affinity or the CLI applies it. This applies to `query`, every `--filter`, and Python
code that uses `affinity.filters` (`F`, filter strings, `lists.entries(...).list(filter=...)`).

- **Empty multi-value fields count as empty.** `is_null` (`field != *`) now matches a field with
  no items, and `is_not_null` (`field = *`) no longer does.
- **Person and company values compare by name** ("Jane Doe", "Acme") in `--filter` and
  `affinity.filters`, as they already did in `query`.
- **`contains` on person and company fields is refused** in `query`: use `starts_word`, which
  matches a word in the name that starts with the text ("Jan" finds "Jane Doe").
- **`query`: a field name two list fields share is refused.** Name the field by id instead,
  for example `fields.affinity-data-year-founded`.

These now work where they were refused before: `company ls --filter` and `person ls --filter`
(on company and person fields), and `query` on companies and persons with a condition on their
fields. See [Filtering](filtering.md) for what goes to Affinity's search.
