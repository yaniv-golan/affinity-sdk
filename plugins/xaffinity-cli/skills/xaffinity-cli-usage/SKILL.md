---
name: xaffinity-cli-usage
description: >
  Runs the xaffinity CLI in bash to read and manage Affinity CRM data: look up companies and
  people, see which lists/pipelines a company is on and its list field values (Status, Owner),
  export lists to JSON/CSV, filter pipelines, dedup-check before adding to a list, and create
  notes, interactions and list entries. Use when the user mentions xaffinity, asks for Affinity
  CLI commands, bash scripts, flags or CSV exports, or needs Affinity CRM data and no Affinity
  MCP tools are available. Not for pipeline-history analysis (pipeline-history skill) or
  structured MCP queries (query-language skill) when those skills are available.
---

# xaffinity CLI Usage

## REQUIRED FIRST STEP: Verify API Key

Before anything else, run:

```bash
xaffinity config check-key --json
```

- **`"configured": true`** — use the `pattern` field from the output for ALL subsequent commands:
  `"xaffinity --dotenv --readonly <command> --json"` means add `--dotenv`; otherwise no `--dotenv`.
- **`"configured": false`** — stop and help the user set up. Read `references/setup.md` (key
  resolution order, `xaffinity config setup-key` — interactive, the user must run it — and the
  Cowork `.env` / `AFFINITY_API_KEY_FILE` workaround).

## Read JSON from the right key

`--json` emits **one** JSON object (not NDJSON): `{"ok", "data", "warnings", "meta", ...}`. The
shape of `data` differs by command — reading the wrong key gives `null`, which looks like real
"empty" data. Use this table:

| Command | Read |
|---------|------|
| `list export` | `.data.rows[]` — `listEntryId`, `entityId`, `entityName`, + field values by name |
| `person ls` / `company ls` | `.data.persons[]` / `.data.companies[]` |
| `person get` / `company get` / `opportunity get` | `.data.person` / `.data.company` / `.data.opportunity` |
| …with `--expand list-entries` / `lists` / `persons` | `.data.listEntries` / `.data.lists` / `.data.persons` — **next to** the entity, not inside it |
| `list entry field … --get` | `.data.fields` |
| `interaction ls` / `note ls` / `<entity> files ls` | `.data[]` — a **bare array** |
| `note create` / `interaction create` | `.data.note` / `.data.interaction` |

When in doubt, look before you extract:
`... --json | jq '.data | if type == "object" then keys else "array of \(length)" end'`.

**`null` / missing does not mean empty.** A plain `company get` / `person get` / `opportunity get`
is a cheap lookup that fetches **no field values and no list entries**: `fields: {"requested":
false}` and a missing `listEntries` key mean *not fetched*. `meta.notRequested` names what was
skipped and the flag that fetches it. Never tell the user a record "has no lists" or "empty fields"
— and never offer to write values — without having fetched them.

## Common pitfalls (READ THIS FIRST)

**1. Never redirect stderr to `/dev/null`.** Every CLI safety signal — "results truncated",
"unknown option", "--filter client-side" — lives on stderr. Dropping stderr is how you ship a
silent-zero-match result and call it a duplicate check.

**2. `--filter` on `list export` requires `--all`, `--max-results`, or `--first-page-only`.** The
CLI errors on the unscoped case (v1.13+). Filtering is client-side; for large lists prefer
`--saved-view` (server-side) or `--company-id` / `--person-id` (entity-scoped, cheap). Details:
`references/filtering.md`.

**3. Duplicate checks: use `--company-id` / `--person-id`, not `--filter`.**
`xaffinity list export "Pipeline" --company-id 555 --json` returns 0 or more rows for that exact
company, with its list field values. Zero rows + the emitted warning is the "not on list" signal.

**4. Check `meta.truncated` on every JSON response.** If `true`, the answer is incomplete;
`meta.truncationReason` names the cause (currently `firstPageOnly`).

**5. Side-effecting commands: capture, then parse.** A pipeline like
`xaffinity note create --content "..." --company-id 123 | python3 -c "json.loads(...)"` runs the
create first, then parses. If you forgot `--json`, the parser crashes and the pipeline exits 1 —
but the note was already created, and a "retry" creates a duplicate. Capture first:

```bash
out=$(xaffinity --json note create --content "..." --company-id 123)
note_id=$(printf '%s' "$out" | jq -r '.data.note.id')
```

The CLI also warns on stderr when identical content from the same author arrives within 5
minutes, but that's a safety net — capture-then-parse is the fix.

**6. Person/company field values require numeric IDs, not names.**
`xaffinity list entry field "Pipeline" 999 --set Owner "Jane Doe"` aborts with an "Invalid entity
ID" error before any write. Resolve names to IDs first, and check the ID is not `null`:

```bash
owner_id=$(xaffinity --readonly --json person ls --query "Jane Doe" --max-results 5 \
  | jq -r '.data.persons[0].id')
[ "$owner_id" != "null" ] || { echo "no match for Jane Doe" >&2; exit 1; }
xaffinity list entry field "Pipeline" 999 --set Owner "$owner_id"
```

Since CLI 1.15.0 every `--set` value is validated before any API call, so a bad person ID no longer
leaves earlier `--set Status=...` writes committed. The CLI still does not resolve names for you.

## Reading One Company's / Person's Lists and List Fields

```bash
# Which lists is this company on, with each entry's list fields (Status, Owner, ...)
xaffinity --readonly company get 314557093 --expand list-entries --json | jq '.data.listEntries'

# Only one list (--list implies --expand list-entries)
xaffinity --readonly company get 314557093 --list "Dealflow" --json | jq '.data.listEntries'

# Rows for that company on a known list (same values, list-export row shape)
xaffinity --readonly list export "Dealflow" --company-id 314557093 --json | jq '.data.rows'
```

Read `.data.listEntries`, not `.data.company.listEntries`.

## IMPORTANT: Write Operations Require Explicit User Request

**Always use `--readonly` unless the user explicitly requests writes.** Writes include creating,
updating, or deleting notes, interactions, reminders, list entries, field values, persons,
companies and opportunities. Before writing over an existing field value, read it first and show
the user the current value. Write-side gotchas (duplicate refusal on create, read-only and
ambiguous fields, global companies, file uploads): `references/files-and-writes.md`.

## Destructive Commands Require Double Confirmation

Before executing ANY delete command:

1. **Look up the entity first** to show the user what will be deleted
2. **Ask the user in your response**, showing the entity details and requesting confirmation
3. **Wait for the user's next message** — do NOT proceed until they explicitly confirm
4. **Only after the user confirms**, run the delete with `--yes`

```
User: "Delete person 123"
You: xaffinity --readonly person get 123 --json
You: "This will permanently delete John Smith (ID: 123, email: john@example.com).
      Type 'yes' to confirm deletion."
[Stop here and wait for user's response]

User: "yes"
You: xaffinity person delete 123 --yes
```

**Destructive commands**: `person delete`, `company delete`, `opportunity delete`, `note delete`,
`reminder delete`, `field delete`, `list entry delete`, `interaction delete`

The `--yes` flag bypasses the CLI's interactive prompt; the confirmation must come from the user in
the conversation.

## Critical Patterns

| Pattern | Purpose |
|---------|---------|
| `--readonly` | Prevent accidental data modification (ALWAYS use unless writing) |
| `--json` | Structured, parseable output (ALWAYS use for commands you will parse) |
| `--max-results N` | **Limit results (ALWAYS use on list/search commands)**. Aliases: `--limit`, `-n` |
| `--yes` | Skip confirmation on delete commands (use after the user confirms) |
| `--help` | Discover command options (USE THIS, don't guess flags) |

**Always limit results.** Use `--max-results` on every `ls`, `list export`, `interaction ls`, and
`note ls`. Start small (10-50), increase only if needed — unbounded queries can return hundreds of
KB and make many API calls.

**Extract only what you need** with `jq` once you know the shape (see the table above):

```bash
# A person's ID for a follow-up command
xaffinity --readonly person get email:alice@example.com --json | jq -r '.data.person.id'

# Just the fields you need to answer the user
xaffinity --readonly person get 123 --json \
  | jq '.data.person | {id, firstName, lastName, primaryEmailAddress}'

# Entity names from a list export
xaffinity --readonly list export "Pipeline" --max-results 20 --json \
  | jq '[.data.rows[] | {entityName, entityId}]'
```

## Multi-Source Tasks: Use a Script

When a task needs data from **2 or more** CLI commands (e.g., person details + interactions + list
entries), write a **single bash script** that prints only the summary, instead of running commands
one by one — each separate command dumps its full JSON into the conversation. A single command is
fine for simple lookups, single writes and quick searches. Worked example (company → interactions
summary): `references/interactions.md`.

For complex joins across 3+ sources, conditional logic, pagination over large datasets, or SDK
features like `F` filters or `FieldResolver`, write a Python script using the Affinity SDK (the SDK
skill has patterns).

## Selectors: Use Names, Not Just IDs

Most commands accept names, emails, or domains directly — no ID lookup needed:

```bash
xaffinity --readonly person get email:alice@example.com --json
xaffinity --readonly company get domain:acme.com --json
xaffinity --readonly list export "My Pipeline" --max-results 20 --json
xaffinity --readonly person get 12345 --json      # IDs also work
```

## Common Commands

```bash
# Search entities (always limit results)
xaffinity --readonly person ls --query "John Smith" --max-results 10 --json
xaffinity --readonly company ls --query "Acme" --max-results 10 --json

# All lists; entries of one list
xaffinity --readonly list ls --json
xaffinity --readonly list export "Pipeline" --max-results 20 --json

# Export to CSV
xaffinity --readonly person ls --all --csv --csv-bom > contacts.csv
xaffinity --readonly list export "Pipeline" --all --csv --csv-bom > output.csv
```

Searching and filtering (`--query` vs `--filter`, saved views, operators): `references/filtering.md`.
Interactions (types, date ranges, creating them): `references/interactions.md`.

## List Entry Fields

```bash
# Read specific fields (resolved person/company objects, matching list export format)
xaffinity --readonly list entry field "Pipeline" 12345 --get Owner --get Status --json

# Set a field value (requires write permission)
xaffinity list entry field "Pipeline" 12345 --set Status "Active"
```

`--get` and `--set` are mutually exclusive. `--get` returns resolved objects for person/company
reference fields (`firstName`, `lastName`, `primaryEmailAddress`) and full dropdown option data
(`text`, `color`).

## Expand/Include (N+1 Warning)

`--expand` on `list export` triggers **one additional API call per record**. Use `--max-results`
to control cost.

```bash
# Safe: 20 records = ~21 API calls
xaffinity --readonly list export "Pipeline" --expand persons --max-results 20 --json

# DANGEROUS: --expand with --all on a large list (500 entries = 501+ API calls, ~10 minutes)
# xaffinity list export "Pipeline" --expand persons --all  # DON'T do this blindly
```

**Practical limits:** <=100 records is safe. 200 records ~5 min. 400+ records may hit timeouts.

## Query Command (Advanced)

For aggregation/groupBy, cross-entity filtering, nested AND/OR/NOT, or a cost preview, use
`xaffinity query`. **Always `--dry-run` first** for queries with include/expand/quantifiers. Full
reference (JSON structure, operators, aggregation, examples): `references/query-guide.md`.

| Need | Use |
|------|-----|
| Simple search by name/email | `person ls --query` or `person get email:...` |
| Export list entries | `list export "ListName"` |
| Server-side filtered list | `list export --saved-view "ViewName"` |
| Aggregate/group data, filter by related entities | `query` |
| Preview API cost first | `query --dry-run` |

## Gotchas & Workarounds

- **Smart fields ("Last Meeting", "Next Meeting") are UI-only.** Use `--with-interaction-dates` on
  **get** commands (not `ls`): `xaffinity --readonly company get domain:acme.com --with-interaction-dates --json`.
- **Internal meetings are NOT in interactions** (only meetings with external contacts) — use notes;
  see `references/interactions.md`.
- **Opportunities are bound to one list.** You cannot search opportunities globally; use
  `list export` on their list. `opportunity get` needs `--details` for field values.
- **Progress output goes to stderr**, so JSON on stdout stays clean. Use `--quiet` / `-q` to
  suppress progress — but don't discard stderr (pitfall 1).

## Quick Reference

| Task | Command |
|------|---------|
| Find person by email | `person get email:user@example.com --json` |
| Find company by domain | `company get domain:acme.com --json` |
| Company's lists + list field values | `company get <id> --expand list-entries --json` |
| Is company on list X? (dedup) | `list export "X" --company-id <id> --json` |
| Search people | `person ls --query "name" --max-results 10 --json` |
| Recent interactions | `interaction ls --type all --company-id ID --days 90 --max-results 50 --json` |
| Export list (bounded) | `list export "ListName" --max-results 100 --json` |
| Export list (full CSV) | `list export "ListName" --all --csv --csv-bom > out.csv` |
| List with server filter | `list export "ListName" --saved-view "ViewName" --max-results 50 --json` |
| List entity files | `company files ls "domain:acme.com" --max-results 20 --json` |
| Aggregate/group data | `query --dry-run --file query.json --json` (preview cost first) |
| Get command help | `xaffinity <command> --help` (USE THIS — don't guess flags) |

**Remember:** prefix all commands with `xaffinity --readonly` (and `--dotenv` if `check-key` says
so). Install: `pip install "affinity-sdk[cli]"`. Docs: https://yaniv-golan.github.io/affinity-sdk/latest/
