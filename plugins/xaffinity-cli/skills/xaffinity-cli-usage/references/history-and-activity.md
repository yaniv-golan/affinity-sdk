# Field history, org-wide activity, transcripts, relationships, merges, dropdown options

Read this before answering "who changed X / what changed since Y", "what happened across the
org this week", "who knows people at company Z", or before changing a dropdown's options. All of
these are reads except `field options create|update|delete`.

## Field change history

| Need | Command | Read |
|------|---------|------|
| One field on one entity | `field history field-123 --company-id 456` (or `--person-id`, `--opportunity-id`, `--list-entry-id`) | `.data.fieldValueChanges[]` |
| One field, every entity | `field history field-123 --changed-after -30d --order asc` (no selector needs `--changed-after` or `--max-results`) | same |
| Any fields, any entities (delta sync, audit by changer) | `field changes --changed-after -7d --max-results 100` (`--field-id`, `--list-entry-id` repeatable; `--changer-id`, `--action-type add\|update\|delete`) | same, + `nextCursor` in `.meta.pagination` |
| One field across a whole list (pipeline analysis) | `field history-bulk field-123 --list-id "Pipeline" --all --dry-run`, then without `--dry-run` | same |

- Action names differ: `field history` / `history-bulk` say `create/update/delete`, `field changes`
  says `add/update/delete`.
- `--max-results` on `field history` is the **most recent** N (`--order asc`: the oldest N).
- Sort events by `changedAt` **parsed as a time** (string order misplaces same-second events), then
  by `id`. Each row's `value` is the value at that point; derive transitions from consecutive rows.
- `history-bulk --all` on a field of that list with 100+ entries reads the field's history in a few
  calls (dry run: `"strategy": "field"`); otherwise one call per entry. Check the dry run's
  `estimatedApiCalls` before a big run.

## Org-wide activity (not tied to one entity)

```bash
xaffinity --readonly interaction feed --type email --after -7d --max-results 50 --json   # .data.interactions[]
xaffinity --readonly interaction feed --type meeting --after 2025-06-01 --before 2025-07-01 --json
xaffinity --readonly note feed --created-after -7d --max-results 50 --json              # .data.notes[]
xaffinity --readonly note replies 12345 --json                                          # .data.replies[]
```

- `interaction feed --type email|meeting|call|chat-message` lists only what the key's user may
  see; an email subject they may not see is `"********"`. Rows hold up to 10 participants plus a
  total (`toTotal`, `attendeesTotal`, …). For one person/company/opportunity keep using
  `interaction ls` (`references/interactions.md`).
- `note feed` rows: `content` (HTML), `creator`, `mentionedPersonIds`, `interactionId`,
  `transcriptId`; `--with-attached` adds `repliesCount` and company/person/opportunity ids.
  For one entity's notes use `note ls --company-id|--person-id|--opportunity-id`.
- Neither has a sort option beyond newest-first notes; for "since X" use `--after` /
  `--created-after` / `--updated-after` (`--updated-after` skips items never changed).
- This is sensitive data: summarise, don't dump.

## Transcripts (AI Notetaker)

```bash
xaffinity --readonly transcript ls --created-after -30d --max-results 20 --json   # .data.transcripts[] (metadata only)
xaffinity --readonly transcript get 123 --json                                     # .data.transcript (note + first fragments)
xaffinity --readonly transcript get 123 --all --json                               # every fragment
```

Fragments have `content`, `speaker`, `startTimestamp`, `endTimestamp`; `fragmentsTotal` says how
many exist. Only transcripts the key's user may see.

## Relationships and merge history

```bash
xaffinity --readonly company relationships domain:acme.com --min-score 0.3 --max-results 20 --json  # .data.relationships[]
xaffinity --readonly person relationships 456 --json
xaffinity --readonly company merge-history ls --status failed --json      # .data.merges[] (admin + Manage duplicates)
xaffinity --readonly task ls --kind company-merge --json                  # .data.tasks[]
```

Relationship rows: `person1`, `person2` (`personId`, `name`, `email`), `interactionScore` (0-1,
strongest first), `linkedInConnectedOn`. LinkedIn-only relationships have score 0, so any
`--min-score` above 0 drops them. Needs Affinity API version 2026-07-15+.

## Dropdown options

```bash
xaffinity --readonly field options ls field-123 --list-id "Pipeline" --json      # .data.options[]
xaffinity --readonly field options ls field-456 --entity-type company --json     # global field
xaffinity field options create field-123 --list-id "Pipeline" --text "Due diligence"
xaffinity field options update field-123 4567 --list-id "Pipeline" --text "Closed - won"
xaffinity field options delete field-123 4567 --list-id "Pipeline" --yes          # DESTRUCTIVE
```

- Options: `id`, `text`, `type` (`dropdown`, `ranked-dropdown`, `status-dropdown`), `rank`,
  `color`, and for status fields `statusCategory` / `winRate`.
- Only list fields' options can be changed. `create` takes the type from the existing options;
  status fields need `--status-category open|won|lost|on-hold`.
- **`field options delete` clears the field on every entry that had the option** — follow the
  double-confirmation rule, and show the option's text and how it's used first.
- Renaming (`update --text`) changes what every entry with that option shows.
