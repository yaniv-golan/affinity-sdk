# Services by question

Read this when a task goes beyond get/list/search of companies, persons, opportunities and lists:
change history, org-wide activity, notes and replies, meeting transcripts, who-knows-whom, merges,
dropdown options, or file uploads. Sync names shown; `AsyncAffinity` has the same methods
(`async for` over `iter_*`, `await` the rest). `list_*` / `list` return one page
(`.data`, `.next_cursor`); `iter_*` / `iter` follow every page.

## Contents
- Who changed what (field history)
- Org-wide activity (emails, meetings, calls, chats, notes)
- Notes on one entity, replies
- Meeting transcripts
- Who on the team knows whom (relationships)
- Merges
- Field values of one company / person
- Dropdown options
- File uploads

## Who changed what (field history)

```python
from datetime import datetime, timedelta, timezone
from affinity.types import CompanyId, FieldId

since = datetime.now(timezone.utc) - timedelta(days=7)

# One field on one entity (V1): exactly one of person_id / company_id / opportunity_id /
# list_entry_id, or none with changed_after
changes = client.field_value_changes.list(FieldId("field-123"), company_id=CompanyId(456))

# One field across every entity, oldest first, keyset-paged (V1)
for ch in client.field_value_changes.iter_all(FieldId("field-123"), changed_after=since):
    print(ch.changed_at, ch.list_entry_id, ch.value)

# Any fields, any entities (V2): filter by changer, fields, list entries, time, action
for ch in client.field_value_changes.iter_global(changed_after=since, changer_id=42):
    print(ch.changed_at, ch.field, ch.entity, ch.action_type, ch.value)
```

- V1 (`list`, `iter_all`) actions are `create` / `update` / `delete`; V2 (`iter_global`) uses
  `add` / `update` / `delete`.
- Sort by `changed_at` as a datetime, then `id`. Each row's `value` is the value at that point.

## Org-wide activity

Only what the API key's user may see; hidden email subjects arrive as `"********"`. For one
person/company/opportunity use `client.interactions.iter(type=..., person_id=...)` instead.

```python
for m in client.interactions.iter_meetings(after=since):         # MeetingV2
    print(m.start_time, m.title, m.attendees_total)
for e in client.interactions.iter_emails(after=since):           # EmailV2: subject, from_, to
    ...
# also iter_calls(), iter_chat_messages(); list_* for one page; before=, created_after=,
# updated_after= filters

for n in client.notes.iter_v2(created_after=since, includes=True):   # NoteV2
    print(n.created_at, n.creator, n.replies_count, n.companies)
```

`includes=True` adds `replies_count` and the attached companies / persons / opportunities
(previews with `*_total` counts). `creator` is `None` on AI-notetaker notes; those carry
`interaction` and `transcript_id`.

## Notes on one entity, replies

```python
from affinity.types import CompanyId, NoteId

for n in client.companies.iter_notes(CompanyId(456), created_after=since):  # NoteV2
    ...
# persons.iter_notes(PersonId), opportunities.iter_notes(OpportunityId); list_notes for a page
note = client.notes.get_v2(NoteId(1), includes=["repliesCount"])
for r in client.notes.iter_replies(NoteId(1)):                    # NoteV2 with .parent
    ...
```

`client.notes.list()` / `create()` (V1) remain the way to read V1-shaped notes and to write.

## Meeting transcripts (AI Notetaker)

```python
for t in client.transcripts.iter(created_after=since):           # metadata only
    print(t.id, t.created_at, t.fragments_total)
t = client.transcripts.get(123)            # note + fragments_preview (first fragments only)
for f in client.transcripts.iter_fragments(123):                 # the whole dialogue, in order
    print(f.speaker, f.start_timestamp, f.content)
```

Transcript text is sensitive: summarise, don't dump it.

## Who on the team knows whom

```python
from affinity.types import CompanyId

for r in client.companies.iter_relationships(CompanyId(456), min_score=0.3):
    print(r.person1.first_name, "->", r.person2.first_name, r.interaction_score)
# persons.iter_relationships(PersonId(...)); list_relationships for one page; order="asc"
```

Rows are strongest first: `person1`, `person2` (`id`, `first_name`, `last_name`,
`primary_email_address`), `interaction_score` (0-1), `linkedin_connected_on`. LinkedIn-only
relationships score 0, so any `min_score` above 0 drops them. Needs API version 2026-07-15
(see "API versions" in SKILL.md).

## Merges

```python
page = client.companies.list_merges(status="failed")   # CompanyMergeState, newest first
state = client.companies.get_merge_state(page.data[0].id)
tasks = client.tasks.list_merge_tasks("company")         # batches of merges; or "person"
# persons.list_merges / get_merge_state; iter_merges / iter_merge_tasks for every page
```

Merge history needs an admin key with the "Manage duplicates" permission.

## Field values of one company / person

```python
values = client.companies.get_field_values(CompanyId(456), ids=["field-123"])
# persons.get_field_values(PersonId(...), ids=[...]); keyed by field id
```

## Dropdown options

```python
from affinity.types import FieldId, ListId

opts = client.lists.get_field_dropdown_options(ListId(1), FieldId("field-123"),
                                               with_status_types=True)
for o in opts:
    print(o.id, o.text, o.type, o.rank, o.color, o.status_category)
# Global (company/person) fields: client.companies.get_field_dropdown_options(field_id)

# Writes (list fields only; explicit user approval):
client.lists.create_field_dropdown_option(ListId(1), FieldId("field-123"),
                                          option_type="dropdown", text="Due diligence")
client.lists.update_field_dropdown_option(ListId(1), FieldId("field-123"), 4567,
                                          text="Closed - won")
client.lists.delete_field_dropdown_option(ListId(1), FieldId("field-123"), 4567)
```

- `option_type` must match the field's existing options (`dropdown`, `ranked-dropdown`,
  `status-dropdown`); status options also need `status_category` (`open`, `won`, `lost`,
  `on-hold`).
- **Deleting an option clears the field on every entry that had it, and cannot be undone.**
  Show the user the option and how many entries use it, and get a second confirmation.
- Renaming changes what every entry with that option shows.

## File uploads

```python
from affinity.types import CompanyId

files = client.files.upload_path_returning_files("./deck.pdf", company_id=CompanyId(456))
print(files[0].id)    # the created EntityFile(s); no follow-up list call needed
```
