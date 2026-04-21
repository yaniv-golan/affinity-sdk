# Affinity Data Model

> **MCP Note**: When using commands via MCP tools, output format (JSON) is handled automatically. Do not include `--json` in arguments.

**Read Before Querying:**
- `xaffinity://query-guide` - **Read this first** for query performance tips, field selection best practices, and operator reference
- `xaffinity://workflows-guide` - **Read for complex tasks** covering common patterns, error handling, and when to use query vs CLI

## Core Concepts

### Companies and Persons (Global Entities)
**Companies** and **Persons** exist globally in your CRM, independent of any list.

- **Commands**: `company ls`, `person ls`, `company get`, `person get`
- **Filters**: Core fields (name, domain, email)
- **Use case**: Search or retrieve ANY company/person in your CRM
- Can be added to multiple lists

### Opportunities (List-Scoped Entities)
**Opportunities** are special - they ONLY exist within a specific list (a pipeline).

- **Commands**: `opportunity ls`, `opportunity get`
- Each opportunity belongs to exactly ONE list
- Opportunities have **associations** to Persons and Companies
- **Important**: V2 API returns partial data. To get associations:
  ```
  opportunity get <id> --expand persons --expand companies
  ```

### Lists (Collections with Custom Fields)
**Lists** are pipelines/collections that organize entities.

- List types: Person lists, Company lists, Opportunity lists
- Each list has **custom Fields** (columns) defined by your team
- **Commands**: `list ls` (find lists), `list get` (list details)
- **Use case**: Find which lists exist and what fields they have

### List Entries (Entity + List Membership)
When an entity is added to a list, it becomes a **List Entry** with field values.

- Entries have **Field Values** specific to that list's custom fields
- **Commands**: `list export` (get entries), `list-entry get` (single entry)
- **Filters**: Based on list-specific field values (Status, Stage, etc.)
- **Use case**: Get entities from a specific list, filtered by list fields
- **Note**: Companies/Persons can be on multiple lists; Opportunities are on exactly one

### Checking List Membership

To check if a company/person is in a specific list, use `--expand list-entries`:

```bash
company get 12345 --expand list-entries
person get john@example.com --expand list-entries
```

Response includes `data.listEntries` array with all lists the entity belongs to. Check if non-empty to verify membership. Use `--list "Dealflow"` to filter to a specific list. For batch checks, use `query` with a `companyId IN [...]` filter.

### Current User Context

To check who is authenticated and get tenant information:

```bash
whoami                    # Returns user info, tenant, and API key scope
```

**MCP Resources**: `xaffinity://me` (full user details), `xaffinity://me/person-id` (just person ID, cached)

## Selectors: Names Work Directly

Most commands accept **names, IDs, or emails** as selectors - no need to look up IDs first.

```bash
list export Dealflow --filter "Status=New"
company get "Acme Corp"
person get john@example.com
opportunity get "Big Deal Q1"
```

## Filtering List Entries

### --filter (Direct Field Filtering)
```bash
list export Dealflow --filter 'Status="New"'
```
Use when you know the field name and value.

### --saved-view (Pre-Configured Views)
```bash
list export Dealflow --saved-view "Active Pipeline"
```
Uses a named view from Affinity UI (server-side filtering). Caveat: Cannot query what filters a saved view applies.

### Decision Flow
1. Get workflow config: `xaffinity://workflow-config/{listId}` (returns status options + saved views in one call)
2. If a saved view name clearly matches your intent (e.g., "Due Diligence" for DD stage) → use it
3. If no matching saved view, or you need specific field filtering → use `--filter`
4. When in doubt, use `--filter` - it's explicit and predictable

### Common Mistake: Confusing Status Values with Saved View Names
```bash
# ✗ WRONG - "New" is a Status field value, not a saved view name
list export Dealflow --saved-view "New"

# ✓ CORRECT - Filter by the Status field
list export Dealflow --filter 'Status="New"'
```

---

## Efficient Patterns (One-Shot)

### Query list entries with filter
```bash
list export Dealflow --filter "Status=New"
```

### Get list entries with specific field values
By default, `list export` only returns basic columns (listEntryId, entityType, entityId, entityName).
**To get custom field values** like Owner, Team Member, Status, use `--field` for each field:
```bash
list export Dealflow --field "Team Member" --field "Owner" --filter 'Status="New"'
```

**Tip:** `--saved-view` can be combined with `--field` to get server-side filtering (from the saved view) with explicit field selection.

### Query tool field selection
When using the `query` tool with listEntries, custom field values are **auto-fetched** when referenced in `groupBy`, `aggregate`, or `where` clauses:
```json
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "groupBy": "fields.Status", "aggregate": {"count": {"count": true}}}
```

**Best practice: Select only the fields you need:**
```json
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "select": ["entityName", "fields.Status", "fields.Owner"]}
```

⚠️ **Avoid `fields.*` for lists with many custom fields** - it fetches ALL field values which can be slow (60+ seconds for lists with 50+ fields). Only use `fields.*` when you genuinely need every field.

### Query tool expand (interaction dates)
Use `expand: ["interactionDates"]` to add last/next meeting dates and email activity to each record:
```json
{"from": "listEntries", "where": {"path": "listName", "op": "eq", "value": "Dealflow"}, "expand": ["interactionDates"], "limit": 50}
```
Unlike `include` (which fetches related entities separately), `expand` merges computed data directly into each record.

### Expand/Include Practical Limits

Both `expand` and `include` trigger N+1 API calls (one per record). MCP tools support dynamic timeout extension for these operations, but there are practical limits:

| Records | Estimated Time | MCP Result |
|---------|----------------|------------|
| ≤100 | ~2 minutes | ✅ Completes normally |
| ~200 | ~5 minutes | ✅ Completes with progress |
| ~400 | ~9 minutes | ✅ Near ceiling |
| 430+ | 10+ minutes | ⚠️ May hit 10-minute ceiling |

**Recommendations:**
- For ≤100 records: Use freely
- For 100-400 records: Works but takes time; consider if you need all records
- For 400+ records: Batch into smaller queries or use CLI directly (not via MCP)

### Multi-select field filtering
Multi-select dropdown fields (like "Team Member") return arrays. Use `eq` for membership check, `has_any`/`has_all` for multiple values:
```json
{"from": "listEntries", "where": {"and": [{"path": "listName", "op": "eq", "value": "Dealflow"}, {"path": "fields.Team Member", "op": "eq", "value": "LB"}]}}
```
See `xaffinity://query-guide` for all multi-select operators.

### Get interactions for a company or person
```bash
interaction ls --type all --company-id 12345                                   # All interactions ever with company
interaction ls --type email --type meeting --company-id 12345 --days 90        # Emails and meetings, last 90 days
interaction ls --type meeting --company-id 12345 --days 90 --max-results 10    # Recent meetings with company
interaction ls --type email --person-id 67890 --max-results 5                  # Most recent emails with person
```

### Get interaction date summaries
```bash
company get 12345 --with-interaction-dates
person get 67890 --with-interaction-dates
```

For bulk interaction dates, use `query` with `expand: ["interactionDates"]` (see above).

Returns:
- `lastMeeting.date`, `lastMeeting.daysSince`, `lastMeeting.teamMembers`
- `nextMeeting.date`, `nextMeeting.daysUntil`, `nextMeeting.teamMembers`
- `lastEmail.date`, `lastEmail.daysSince`
- `lastInteraction.date`, `lastInteraction.daysSince`

### Create an interaction
Interactions require at least one internal person (workspace user) and one external person (contact). Use `--include-me` to auto-include the current user.
```bash
# Log a meeting (--include-me auto-adds your person ID as internal)
interaction create --type meeting --person-id 67890 --include-me \
  --content "Discussed Q3 goals" --date 2025-06-15T14:00:00Z

# Log an email with explicit person IDs
interaction create --type email --person-id 12345 --person-id 67890 \
  --content "Follow-up on proposal" --date 2025-06-15T10:00:00Z --direction outgoing
```
**Important:** `user.id` from `whoami` IS a person ID — workspace users are persons with type `internal`. You can use any `creator_id` (a user ID) directly with `person get` or `person ls` to resolve the user's name. No special user-listing API is needed.

**Limitation:** The API only supports adding person IDs as participants. The UI's "Also add to... search for an entity" feature (which associates an interaction with a company or opportunity) has no API equivalent.

### Find unreplied messages
```bash
list export Dealflow --check-unreplied                     # Find unreplied incoming messages (email/chat)
list export Dealflow --check-unreplied --unreplied-types email  # Email only
list export Dealflow --check-unreplied --unreplied-lookback-days 60  # Custom lookback
```

### Search companies globally
```bash
company ls --query "Acme"
```

### See list fields and dropdown options
```bash
field ls --list-id Dealflow                    # Returns all fields with dropdown options
```
The response includes `dropdownOptions` array for dropdown/ranked-dropdown fields with `id`, `text`, `rank`, `color`.

Or use the resource: `xaffinity://field-catalogs/{listId}` for field schema with descriptions.

### Audit field changes (who changed what, when)
```bash
field history field-123456 --person-id 789           # See change history for a field on a person
field history field-123456 --company-id 456          # Field history on a company
field history field-123456 --list-entry-id 999       # Field history on a list entry
```

Use `field history` to:
- Track who changed a deal's status and when
- Audit field value modifications over time
- Investigate when a field was last updated

**Note**: Requires the field ID (from `field ls`) and exactly one entity selector (`--person-id`, `--company-id`, `--opportunity-id`, or `--list-entry-id`).

### Bulk field change history (pipeline analysis)
For pipeline stage analysis, funnel conversion, or time-in-stage metrics across a whole list, use `field history-bulk`:

```bash
# Step 1: Find the status field ID
field ls --list-id Dealflow                          # Look for a dropdown field like "Status"

# Step 2: Estimate API cost (ALWAYS do this first)
field history-bulk field-358027 --list-id Dealflow --dry-run

# Step 3: Fetch history (bounded)
field history-bulk field-358027 --list-id Dealflow --max-results 50    # Sample N entries
field history-bulk field-358027 --list-id Dealflow --all               # All entries (can be thousands of API calls)
field history-bulk field-358027 --list-entry-ids 100,200,300           # Specific entries only
field history-bulk field-358027 --list-id Dealflow --max-results 100 --action-type update  # Only stage changes
```

Each row has: `id`, `fieldId`, `entityId`, `listEntryId`, `entityName`, `actionType` (`create`/`update`/`delete`), `value`, `changedAt`, `changerName`, `changer`.

**Reconstructing transitions:** Sort events per entity by `changedAt`. Each row's `value` is the value AT that point — compare consecutive rows to derive old→new transitions.

**Important:**
- Each list entry = 1 API call. A list with 9,000 entries = 9,000 API calls with `--all`.
- Always `--dry-run` first to see `estimatedApiCalls`.
- With `--list-entry-ids`, `entityName` is null — join with `list export` data if names are needed.
- Partial failures (e.g., deleted entries) are reported as warnings without blocking other entries.

## Common Mistakes

### Mistake 1: Looking up IDs unnecessarily
```bash
# ✗ WRONG - unnecessary steps
list ls                                        # Step 1: find ID
list export 41780 --filter "Status=New"        # Step 2: use ID

# ✓ RIGHT - use name directly
list export Dealflow --filter "Status=New"     # One step!
```

### Mistake 2: Using wrong command for list fields
```bash
# ✗ WRONG - Status is a LIST field, and --filter is not supported on companies/persons/opportunities
company ls --filter "Status=New"

# ✓ RIGHT - use list export for list-specific fields
list export Dealflow --filter "Status=New"
```

### Mistake 3: Trying to set "Current Organization" via API
"Current Organization" is a derived/system-managed field — it cannot be set or updated directly. It is driven by enrichment data and email domain. "Current Job Title" can be updated after person creation using `field update`, but neither field can be set during `person create`.

### Enriched Field Writes
Most enriched fields — "Phone Number", "Source of Introduction", "Industry", "Location", "Description", etc. — **are** writable via `person field --set` / `company field --set` / `field update`. Pass the field name ("Phone Number") or its field ID and the CLI handles the rest.

Some names are ambiguous because the same concept exists under multiple enrichment providers (e.g. company "Industry" exists for both the built-in enricher and the Dealroom enricher). The CLI surfaces this as an `AmbiguousFieldError` with a table of candidate IDs — pass the specific field ID to disambiguate.

A small number of enriched fields are **derived and cannot be written** — the most important is "Current Organization" on persons, which is computed from email domain. Attempting to set a derived-only field raises `EnrichedFieldNotWritableError` (exit code 2) with a clear message; no silent no-op.

### Duplicate checks (dedup on create)

Prefer entity-scoped queries over `--filter`:

    xaffinity list export "Pipeline" --company-id 555 --json

Returns 0 rows + `warnings: ["Not on this list: company_ids=[555]"]` if
the company is not on the list. No pagination ambiguity — the answer is
unambiguous from one call.

### Agent pitfalls (abbreviated; canonical list in the CLI skill)

- `--json` emits a single JSON object, not NDJSON. Parse the whole blob and read `data.rows`.
- Never redirect stderr to `/dev/null` — truncation, unknown-option, and client-side-filter warnings all go there.
- `list export --filter` requires a scope flag (`--all`, `--max-results`, or `--first-page-only`) since v1.13. Unscoped invocations exit 2.
- For duplicate checks, use `list export --company-id <id>` / `--person-id <id>` instead of `--filter`. Entity-scoped, cheap, unambiguous.
- Check `meta.truncated` on every JSON response; `meta.truncationReason` names the cause (currently `firstPageOnly`).

### Output Format Recommendations

When using the `query` tool, prefer **TOON format** (the default) for bulk data retrieval:

| Format | Best For | Token Efficiency |
|--------|----------|------------------|
| toon | Bulk data retrieval (default) | ~40% fewer tokens |
| markdown | LLM analysis and comprehension | Good |
| json | Programmatic parsing, nested structures | Standard |
| csv/jsonl | Export, downstream processing | N/A |

TOON is the default. Use `format: "json"` only when programmatically parsing nested structures outside of Claude.

## Full Scan Protection

The MCP gateway protects against expensive unbounded scans:

| Behavior | Details |
|----------|---------|
| Default limit | 1000 records (auto-injected) |
| Maximum limit | 10000 records (higher values capped) |
| `--all` flag | **Blocked** with error message |

**To fetch more than 10000 records:**
Use cursor pagination:
```bash
# First request
list export Dealflow --max-results 10000
# Returns: {"nextCursor": "abc123", ...}

# Subsequent requests
list export Dealflow --cursor abc123 --max-results 10000
```


---

## Async Operations (Merges)

Some operations run asynchronously and return a **task URL** instead of completing immediately.

### Merge Operations (Beta)
Merge duplicate companies or persons into a primary record:
```bash
company merge 123 456    # Merge company 456 into company 123
person merge 789 101     # Merge person 101 into person 789
```

These return a `taskUrl` that you can poll for completion:
```json
{"survivingId": 123, "mergedId": 456, "taskUrl": "https://api.affinity.co/v2/tasks/..."}
```

### Waiting for Task Completion
```bash
task wait "https://api.affinity.co/v2/tasks/abc123"              # Wait up to 5 min (default)
task wait "https://api.affinity.co/v2/tasks/abc123" --timeout 60 # Wait up to 60 seconds
task get "https://api.affinity.co/v2/tasks/abc123"               # Check status without waiting
```

Task statuses: `pending`, `in_progress`, `success`, `failed`

### Accessing a Merged Entity
If you `company get` or `person get` an entity that was previously merged, the API returns exit code 4 with `error.type: "entity_merged"`. The JSON error includes `error.details.targetId` — the surviving entity's ID. Use that ID to fetch the correct record:
```bash
# If company 292479388 was merged into 301128758:
company get 301128758   # Use the targetId from the error
```

---

## Write Operations

Use `execute-write-command` for mutations (create, update, delete). Common patterns:

### Reading List Entry Fields
To read specific field values from a list entry:
```bash
entry field "Dealflow" 12345 --get "Owner" --get "Status"
```

Returns resolved objects for person/company reference fields (with `firstName`, `lastName`, `primaryEmailAddress`, etc.) and full dropdown option data (with `text`, `color`, `rank`). Output format matches `list export`.

### Updating List Entry Fields
To set a field value on a list entry:
```bash
entry field "Dealflow" 12345 --set "Status" "New Value"
```

Field names can be found via `field ls "Dealflow"`. For dropdown fields, use the option text (not ID).

### Notes
Notes attach to persons, companies, or opportunities:
```bash
note create --person-id 12345 --content "Call summary: discussed Q2 plans"
note create --company-id 67890 --content "Site visit notes"
note ls --company-id 67890                    # List notes for an entity
```

### Entity Dossier (Comprehensive View)
For complete entity information (all fields, list memberships, interactions, notes) in one call, use the **`get-entity-dossier`** MCP tool instead of multiple commands:
- Returns aggregated data for a person, company, or opportunity
- More efficient than separate `get`, `interaction ls`, `note ls` calls
- Ideal for "tell me everything about X" requests

---

## Reading Files

Files can be attached to companies, persons, and opportunities.

### Step 1: List files to get file IDs
```bash
company files ls 306016520
person files ls 67890
opportunity files ls 98765
```

This returns file metadata including `id` in `data[].id`.

### Step 2: Choose how to read file content

Two options are available - choose based on your environment:

| Method | Use When | Pros | Cons |
|--------|----------|------|------|
| `files read` | Claude Desktop, Claude Cowork, or any sandboxed environment | Works everywhere, supports chunking | Base64 encoding adds ~33% overhead |
| `get-file-url` | Claude Code, API clients, browsers | Direct URL access, no encoding overhead | Blocked in Claude Desktop/Cowork |

### Option A: `files read` (Recommended for MCP)

Returns base64-encoded content inline. Works in all environments including Claude Desktop/Cowork.

```bash
company files read 306016520 --file-id 9192757
person files read 67890 --file-id 9192758
opportunity files read 98765 --file-id 9192759
```

Response includes `data.content` (base64), `data.hasMore`, `data.nextOffset` for chunking.

**Chunking:** Default 1MB per request. For larger files:

```bash
# First chunk (offset=0, default)
company files read 123 --file-id 456

# Next chunk (use nextOffset from previous response)
company files read 123 --file-id 456 --offset 1048576

# Custom chunk size
company files read 123 --file-id 456 --limit 500KB
```

**Reassembling:** Loop with `--offset`, decode base64, write chunks until `hasMore` is false.

### Option B: `get-file-url` (Presigned URL)

Returns a presigned S3 URL valid for 60 seconds. More efficient for direct downloads but **blocked in Claude Desktop/Cowork**.

```bash
get-file-url fileId=9192757
```

Returns presigned `url` (60s expiry). **⚠️ Blocked in Claude Desktop/Cowork** - use `files read` instead.

---

## Filter Syntax

CLI commands use `--filter 'field op "value"'` syntax:
```bash
--filter 'name =~ "Acme"'           # contains
--filter "Status=Active"            # equals
--filter 'email =$ "@acme.com"'     # ends with
--filter 'Status in ["New", "Active"]'  # in list
```

Common operators: `=` `!=` `=~` (contains) `=^` (starts) `=$` (ends) `>` `<` `>=` `<=`

For the `query` tool, use JSON operators (`eq`, `contains`, `in`, etc.) - see `xaffinity://query-guide` for complete reference.

### Filter limitations (critical)

Global-entity list commands do **not** support server-side filtering:

- `company ls --filter` → raises `unsupported_filter`
- `person ls --filter` → raises `unsupported_filter`
- `opportunity ls` → no filter support (opportunities are list-scoped; use `list export` instead)
- `list export <LIST> --filter` → supported via client-side filtering, with a warning

**Use instead:**
- **Name/domain/email search on global entities:** `company ls --query TERM` / `person ls --query TERM` (fuzzy search).
- **List-specific field filters:** `list export <LIST> --filter '...'` (client-side, warned).
- **Saved-view-backed filters on large lists:** `list export <LIST> --saved-view "<VIEW>"` (server-side efficiency).

The `query` tool rejects `where` clauses on `companies`, `persons`, and `opportunities` (these are global entities with no server-side filter support). Use `listEntries` with a `listId` filter for list-scoped queries.

### `company create` / `person create` duplicate protection

As of CLI 0.7.0, `company create` and `person create` refuse to create a duplicate by default. The duplicate key is:
- **Companies:** exact (case-insensitive) name match OR exact domain match.
- **Persons:** exact email match (primary or any), else exact first-name + last-name match.

On conflict, exit code 6 with `error.type == "duplicate_exists"` and `error.details.existing` containing the existing ID. Pass `--allow-duplicate` to bypass. For companies, global Affinity directory matches include a targeted hint to add via `list entry add --company-id <id>` instead of creating a tenant-scoped copy.

## Query vs Filter

- `--filter`: Structured filtering with operators (preferred, supported on `list export` and some list-scoped reads)
- `--query`: Free-text search (required for `company ls` / `person ls`; fuzzy search)

Use `--filter` for precise matching on list entries, `--query` for fuzzy text search on global companies/persons.

## Output Formats

### TOON (Token-Oriented Object Notation)

Some commands may output data in **TOON format** - a structured format specifically designed for LLM consumption with reduced token costs.

**Do NOT manually parse TOON.** Use the official library:
```python
from toon_format import decode  # pip install git+https://github.com/toon-format/toon-python.git
data = decode(toon_string)
```

---

## Handling Truncated Responses

Large query results may be truncated to fit within output limits (~50KB default). The response will include `truncated: true` when this happens.

### Cursor Pagination (All Formats Supported)

All output formats (toon, markdown, json, jsonl, csv) support cursor-based pagination for large result sets.

**Important**: The presence or absence of `nextCursor` tells you what action to take:

| Response | Meaning | Action |
|----------|---------|--------|
| `truncated: true` + `nextCursor: "..."` | Output truncated mid-stream; more fetched data available | Pass cursor to continue from truncation point |
| `truncated: true` + **NO** `nextCursor` | Rare edge case - envelope too large to fit any data | Reduce `include`/`expand` or increase `maxOutputBytes` |

**Critical**: Never fabricate a cursor. The cursor format is opaque and cryptographically validated - any made-up cursor will fail validation.

### Resuming with Cursor

When `nextCursor` is provided, call query again with **identical** `query` and `format`, plus `cursor` set to `nextCursor`. Changing query/format invalidates the cursor.

If truncated without cursor (rare): reduce `include`/`expand` or increase `maxOutputBytes`.

### Example
```python
result = await query(query={"from": "persons", "limit": 1000}, format="toon")
if result.get("truncated") and result.get("nextCursor"):
    result2 = await query(query={"from": "persons", "limit": 1000}, format="toon", cursor=result["nextCursor"])
```

### Two Types of Cursors

| Cursor Type | Location | Purpose |
|-------------|----------|---------|
| **Truncation cursor** | Top-level `nextCursor` | Resume after `maxOutputBytes` truncation |
| **API pagination cursor** | `meta.pagination.rows.nextCursor` | Affinity API's native pagination (used internally) |

The `query` tool's `nextCursor` is for **output size truncation**, not record limits.

### Cursor Behavior

- Expire after 1 hour
- Pass back unchanged - never fabricate or modify (cryptographically validated)
- Mode-specific: streaming (simple queries) vs full-fetch (orderBy/aggregate)
