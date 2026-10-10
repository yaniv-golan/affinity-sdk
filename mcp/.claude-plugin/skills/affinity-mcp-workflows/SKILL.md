---
name: affinity-mcp-workflows
description: >
  Primary skill for Affinity CRM data access when MCP tools are available
  (discover-commands, execute-read-command, execute-write-command, query,
  get-entity-dossier). Prefer this over direct CLI when MCP tools are present.
when_to_use: >
  Use for entity lookup, pipeline review, meeting prep, warm intros, interaction logging,
  or when user mentions "pipeline", "deals", "relationship strength", "prepare briefing",
  or wants to log calls/meetings.
---

# Affinity MCP Workflows

## Prerequisites

The MCP server requires the xaffinity CLI to be installed:

```bash
pip install "affinity-sdk[cli]"
```

The CLI must be configured with an API key before the MCP server will work.

## ⚠️ REQUIRED WORKFLOW - Do Not Skip Steps

**You MUST complete steps 1-2 before running ANY queries or commands.**

Skipping these steps leads to incorrect or inefficient queries because:
- `discover-commands` has each command's exact flags, limits and examples; this skill shows the
  shape of a call, not every flag
- The data model has nuances (e.g., `list export` vs `company ls`) that you'll miss

### Mandatory Pre-Flight Checklist

**Before proceeding to execute any commands:**

1. ✅ Read `xaffinity://data-model` using `read-xaffinity-resource`
2. ✅ Run `discover-commands` for your specific task
3. ✅ State what you learned from each step before continuing

**Example:**
```
"I read the data-model resource and learned that list entries have custom fields
accessed via fields.<Name>. I ran discover-commands for 'interaction' and found
that interaction ls supports --type all to fetch all types in one call, and
--days to limit the time range. Now I'll proceed with..."
```

## IMPORTANT: Write Operations Only After Explicit User Request

**Only use tools or prompts that modify CRM data when the user explicitly asks to do so.**

Write operations include:
- **Tools**: `execute-write-command`
- **Prompts**: `log-interaction-and-update-workflow`, `change-status`, `log-call`, `log-message`

Read-only operations (search, lookup, briefings) can be used proactively to help the user. But never create, update, or delete CRM records unless the user specifically requests it.

## Full Scan Protection

The MCP gateway caps how much a command may fetch:

- Without `--max-results` a list command returns its first page and a `nextCursor`. Pass the
  number you need, up to the command's max (`discover-commands` → `limitConfig`, e.g.
  `note feed` 200); above the max is refused.
- `--all` is refused, except `field history-bulk --all --strategy field` (dry run first). To get
  more, pass `--cursor` with the previous `nextCursor`.
- Large results are trimmed to fit; the result says how many rows were kept, and drops
  `nextCursor` (it would skip the trimmed rows): re-run with a smaller `--max-results`.

## Available Tools

### CLI Gateway (Primary Interface)

The CLI Gateway provides full access to the xaffinity CLI:

| Tool | Use Case |
|------|----------|
| `discover-commands` | Search CLI commands by keyword (e.g., "create person", "export list") |
| `execute-read-command` | Execute read-only CLI commands (get, search, list, export) |
| `execute-write-command` | **(write)** Execute write CLI commands (create, update, delete) |

**Usage pattern:**

1. **Discover** the right command: `discover-commands(query: "create person", category: "write")`
2. **Execute** it: `execute-write-command(command: "person create", argv: ["--first-name", "John", "--last-name", "Doe"])`

### Utility Tools

| Tool | Use Case |
|------|----------|
| `get-entity-dossier` | Entity info in one call (details, strongest relationships, recent interactions and notes, list memberships) |
| `read-xaffinity-resource` | Access dynamic resources via `xaffinity://` URIs |

### Destructive Commands

Commands that cannot be undone (delete, merge) require double confirmation. Some clients show a
confirmation dialog; Claude Desktop does not, so this conversation step is the only check there:

1. **Look up the entity first** using `execute-read-command` to show what will be deleted
2. **Call without `confirm`.** The server refuses with `confirmation_required` (or shows the user
   a dialog, in clients that have one). A `confirm: true` the server didn't ask for is ignored.
3. **Ask the user in your response** by showing them the entity details and requesting confirmation
4. **Wait for user's next message** - do NOT proceed until they explicitly confirm
5. **Only after user confirms** call again, same command and argv, with `confirm: true`
   (within 15 minutes; after that, or for a different target, the server asks again)

Example flow:
```
User: "Delete person 123"
You: execute-read-command(command: "person get", argv: ["123"])
You: execute-write-command(command: "person delete", argv: ["123"])
     -> confirmation_required
You: "This will permanently delete John Smith (ID: 123, email: john@example.com).
      Type 'yes' to confirm deletion."
[Stop here and wait for user's response]

User: "yes"
You: execute-write-command(command: "person delete", argv: ["123"], confirm: true)
```

## Query vs CLI Commands: When to Use What

**Use `query` tool for:**
- Any operation needing **relationships** (persons at a company, companies for a person)
- Any operation needing **computed data** (interaction dates, unreplied messages)
- **Pipeline analysis** with aggregations or groupBy
- **Complex filtering** with AND/OR conditions
- **List entry operations** that need associated entities

**Use individual CLI commands for:**
- **Simple lookups**: `person get 123`, `company get 456`
- **Quick searches**: `person ls --query "John"`, `company ls --query "Acme"`
- **Content search**: `note search "pricing"`, `file search "pitch deck"` (text inside notes
  and files), `company search "AI infrastructure startups in Berlin"` (natural-language
  company search, up to 100 ranked results)
- **Metadata**: `list ls`, `field ls --list-id <id>`
- **Write operations**: All creates, updates, deletes

### Query Examples (Preferred for Complex Operations)

⚠️ **STOP: Did you complete the pre-flight checklist?** Run `discover-commands` for the exact flags.

⚠️ **For queries with `expand` or `include`, ALWAYS use `dryRun: true` first** to see estimated API calls. These cause N+1 API calls (one per record) and can be slow or timeout.

```json
// STEP 1: Preview any expand/include query with dryRun first
{"query": {"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "expand": ["interactionDates"], "limit": 100}, "dryRun": true}

// STEP 2: If API calls look reasonable (<200), run without dryRun
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "expand": ["interactionDates"], "limit": 100}

// Pipeline with field values and unreplied email detection
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "select": ["entityName", "fields.Status", "fields.Owner"], "expand": ["unreplied"]}

// Persons with their companies and interaction history summary
{"from": "persons", "where": {"path": "email", "op": "contains", "value": "@acme.com"}, "include": ["companies"], "expand": ["interactionDates"]}

// Pipeline summary by status (aggregation) - no expand, no dryRun needed
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "groupBy": "fields.Status", "aggregate": {"count": {"count": true}}}

// List entries with associated persons and interactions (parameterized include)
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "include": [{"interactions": {"limit": 50, "days": 180}}, "persons"]}
```

## Common CLI Commands

⚠️ **Reminder:** Run `discover-commands` first. The commands below show the shape; it has every flag.

Use `discover-commands` to find commands, then `execute-read-command` or `execute-write-command` to run them.

### Search & Lookup (Simple Operations)

| Command | Use Case |
|---------|----------|
| `person ls --query "..."` | Quick search persons by name/email |
| `company ls --query "..."` | Quick search companies by name/domain |
| `company search "..."` | Find companies matching a description (semantic, ranked, ≤100) |
| `note search "..." [--company-id X]` | Find notes by their text |
| `file search "..." [--company-id X]` | Find files by their contents (then `file-url <fileId>`) |
| `list ls` | List Affinity lists (`--query NAME` matches part of the name) |
| `field ls --list-id <id>` | Get field definitions (options: `field options ls`) |

**Note:** For list exports needing relationships or computed data, use `query` instead of `list export`.

### Entity Details

| Command | Use Case |
|---------|----------|
| `person get <id>` | Get person details |
| `company get <id>` | Get company details |
| `opportunity get <id>` | Get opportunity details |
| `person relationships <id>` / `company relationships <id or domain:x.com>` | Who on the team knows them, strongest first (`--min-score 0.3`) |
| `interaction ls --person-id <id> --type all --days 365` | One entity's interactions (or one type: email, meeting, call, chat-message) |
| `field history <field-id> --person-id <id>` | Who changed one field on one entity, and when (or `--company-id`, `--opportunity-id`, `--list-entry-id`) |

### Org-wide activity and history

| Command | Use Case |
|---------|----------|
| `interaction feed --type email --after -7d` | Emails (or `meeting`, `call`, `chat-message`) across the org, not tied to one entity |
| `note feed --created-after -7d` | Recent notes across the org (`--with-attached` adds the companies/persons/deals and reply counts) |
| `note replies <noteId>` | Replies to a note |
| `transcript ls --created-after -30d` / `transcript get <id>` | AI Notetaker meeting transcripts (`get --max-results N` for more fragments) |
| `field history <field-id> --changed-after -30d` | One field's changes on every entity |
| `field changes --changed-after -7d` | Any field on any entity (`--changer-id`, `--field-id`, `--list-entry-id`) |
| `field history-bulk <field-id> --list-id <list> --all --strategy field --dry-run` | One field across a whole list (stage transitions) — see the pipeline-history skill |
| `company merge-history ls` / `task ls --kind company-merge` | Which merges ran or failed (admin key) |
| `field options ls <field-id> --list-id <list>` | A dropdown or status field's options (ids, text, rank, status category) |

Only what the API key's user may see. Notes and transcripts are sensitive: summarise them.

### Write Operations

| Command | Use Case |
|---------|----------|
| `interaction create --type call --person-id <id>` | Log a call/meeting/email |
| `note create --person-id <id> --content "..."` | Add a note |
| `entry field "<list>" <entryId> --get <field>` | Read field values (returns resolved person/company objects) |
| `entry field "<list>" <entryId> --set <field> <value>` | Update a field value |
| `person create --first-name "..." --last-name "..."` | Create a person |
| `field options create\|update <field-id> --list-id <list> ...` | Add or rename a dropdown option |
| `field options delete <field-id> <optionId> --list-id <list>` | **Destructive**: clears the field on every entry with that option (double confirmation) |

## MCP Prompts (Guided Workflows)

These prompts provide guided multi-step workflows. Suggest them when appropriate.

**Note**: Prompts marked with (write) modify CRM data - only use when user explicitly requests.

| Prompt | Type | When to Suggest |
|--------|------|-----------------|
| `prepare-briefing` | read-only | User has upcoming meeting, needs context on a person/company |
| `pipeline-review` | read-only | User wants weekly/monthly pipeline review |
| `warm-intro` | read-only | User wants to find introduction path to someone |
| `interaction-brief` | read-only | Get interaction history summary for an entity |
| `log-interaction-and-update-workflow` | **write** | User explicitly asks to log a call/meeting and update pipeline |
| `change-status` | **write** | User explicitly asks to move a deal to new stage |
| `log-call` | **write** | User explicitly asks to log a phone call |
| `log-message` | **write** | User explicitly asks to log a chat/text message |

### How to Invoke Prompts

Prompts are invoked with arguments. Example:
- `prepare-briefing(entityName: "John Smith", meetingType: "demo")`
- `warm-intro(targetName: "Jane Doe", context: "partnership discussion")`
- `log-interaction-and-update-workflow(personName: "Alice", interactionType: "call", summary: "Discussed pricing")`

## Resources

Access dynamic data via `xaffinity://` URIs using `read-xaffinity-resource`:

| URI | Returns |
|-----|---------|
| `xaffinity://me` | Current authenticated user details |
| `xaffinity://me/person-id` | Current user's person ID in Affinity |
| `xaffinity://interaction-enums` | Valid interaction types and directions |
| `xaffinity://saved-views/{listId}` | Saved views available for a list |
| `xaffinity://field-catalogs/{listId}` | Field definitions for a list |
| `xaffinity://workflow-config/{listId}` | Workflow configuration for a list |

## Common Workflow Patterns

Combine the tools above to handle multi-step tasks:

- **Before a meeting**: `get-entity-dossier` for full context, or `prepare-briefing` prompt
- **After a call**: `execute-write-command` to log interaction, `query` to find list entry, `entry field` to update status — or `log-interaction-and-update-workflow` prompt
- **Finding warm intros**: `person ls` / `company ls` → `person relationships` /
  `company relationships`, or `warm-intro` prompt
- **"What happened this week?"**: `interaction feed --type meeting --after -7d`, `note feed
  --created-after -7d`
- **"Who changed X / what did Y change?"**: `field history` (one field), `field changes`
  (`--changer-id`)
- **"What was said in the meeting?"**: `transcript ls`, then `transcript get <id>`
- **Pipeline review**: `query` with aggregation + expand, or `pipeline-review` prompt
- **"What did we discuss about X?"**: `note search "X"` (add `--company-id` to scope it), then
  `note get <noteId>` for the full note

⚠️ Complete the pre-flight checklist before using any pattern.

## Tips

- **Entity types**: `person`, `company`, `opportunity`
- **Interaction types**: `call`, `meeting`, `email`, `chat-message` (or `chat`)
- **Dossier**: `get-entity-dossier` returns details, the team's strongest relationships, recent interactions and notes, and list memberships in one call
- **Use names directly**: Most commands accept names instead of IDs (e.g., `person ls --query "John"`)
- **Finding entities in a list**: Use `query` with filters:
  ```json
  {"from": "listEntries", "where": {"and": [{"path": "listName", "op": "eq", "value": "Dealflow"}, {"path": "entityName", "op": "contains", "value": "Acme"}]}}
  ```
- **Output formats**: The `format` parameter controls result format:
  - `toon` (default for query): 40% fewer tokens, best for bulk queries
  - `markdown`: Best for LLM comprehension when analyzing data
  - `json`: Full structure with envelope - supports cursor pagination for large results
  - `csv`: For spreadsheet export
  - All formats support cursor pagination when results are truncated (use `nextCursor` to resume)

## Troubleshooting

If tools aren't working or returning unexpected results:

### Enable Debug Mode

```bash
# Enable (persistent, works with any MCP client)
mkdir -p ~/.config/xaffinity-mcp && touch ~/.config/xaffinity-mcp/debug

# Restart the MCP client (Claude Desktop: Cmd+Q, reopen)

# Disable when done
rm ~/.config/xaffinity-mcp/debug
```

### View Logs

**Claude Desktop**: `tail -f ~/Library/Logs/Claude/mcp-server-*.log`

Debug logs show component prefixes like `[xaffinity:tool:1.2.3]` to identify which component produced each message.

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Tools show old behavior after update | Cached MCP server process | Fully quit and restart Claude Desktop |
| API key errors | Key not configured | Run `xaffinity config setup-key`, or set `AFFINITY_API_KEY` / `AFFINITY_API_KEY_FILE` / `AFFINITY_API_KEY_COMMAND` env var |
| CLI version errors | Outdated CLI | Run `pip install --upgrade "affinity-sdk[cli]"`

### Field values: hidden is not empty

With Affinity API version 2026-07-15 or newer, fields on restricted opportunities your API key
can't manage come back masked (`type: "hidden"`, empty value). The CLI warns ("hidden by Affinity").
Treat them as unknown, never as empty, and don't overwrite them.
