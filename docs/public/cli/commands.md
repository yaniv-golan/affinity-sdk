# Commands

## No-network commands

These commands never call the Affinity API:

- `xaffinity --help`
- `xaffinity completion bash|zsh|fish`
- `xaffinity version` (also `xaffinity --version`)
- `xaffinity config path`
- `xaffinity config init`
- `xaffinity config check-key` (reads files, but never calls API)

## Configuration

### `xaffinity config path`

Show the path to the configuration file.

```bash
xaffinity config path
```

### `xaffinity config init`

Create a new configuration file with template.

```bash
xaffinity config init
xaffinity config init --force  # Overwrite existing
```

### `xaffinity config check-key`

Check if an API key is configured. Returns exit code 0 if key found, 1 if not found.

This command checks (in order):
1. `AFFINITY_API_KEY` environment variable
2. `.env` file in current directory
3. User config file (`config.toml`)

```bash
xaffinity config check-key
xaffinity config check-key --json
xaffinity config check-key && echo "Key exists"
```

The `--json` output includes:
- `configured`: boolean indicating if a key was found
- `source`: where the key was found (`"environment"`, `"dotenv"`, `"config"`, or `null`)

### `xaffinity config setup-key`

Securely configure your Affinity API key. Prompts for the key with hidden input (not echoed to screen).

Options:

- `--scope [project|user]`: Where to store the key
  - `project`: `.env` file in current directory (auto-added to `.gitignore`)
  - `user`: User config file (`config.toml`, with `chmod 600` on Unix)
- `--force`: Overwrite existing key without confirmation
- `--validate/--no-validate`: Test key against API after storing (default: validate)

```bash
# Interactive setup (prompts for scope)
xaffinity config setup-key

# Store in current project's .env file
xaffinity config setup-key --scope project

# Store in user config (works across all projects)
xaffinity config setup-key --scope user

# Overwrite existing key
xaffinity config setup-key --force

# Skip API validation
xaffinity config setup-key --no-validate
```

Get your API key from [Affinity API Settings](https://support.affinity.co/s/article/How-to-Create-and-Manage-API-Keys).

## Global options

These options can be used with any command:

- `--json` / `--output json`: emit machine-readable `CommandResult` JSON to stdout.
- `--help --json`: emit machine-readable command documentation (see [Scripting: Machine-Readable Help](scripting.md#machine-readable-help)).
- `--trace`: emit request/response/error trace lines to stderr (safe redaction). Recommended with `--no-progress` for long-running commands.
- `--beta`: enable beta endpoints (required for merge commands).
- `--session-cache <dir>`: enable session caching using the specified directory.
- `--no-cache`: disable session caching for this command.

## Pipeline Optimization

When running multiple CLI commands in a pipeline, you can enable session caching to avoid redundant API calls for metadata like field definitions and list resolution.

### Using session commands

```bash
# Start a session (creates temp cache directory)
export AFFINITY_SESSION_CACHE=$(xaffinity session start)

# Run your commands - metadata is cached across invocations
xaffinity list export "My List" --json > entries.json
xaffinity field ls --list-id 12345
xaffinity person get 12345

# Check session status
xaffinity session status

# End session (cleanup)
xaffinity session end
unset AFFINITY_SESSION_CACHE
```

### Best practice for scripts (with automatic cleanup)

```bash
#!/bin/bash
export AFFINITY_SESSION_CACHE=$(xaffinity session start) || exit 1
trap 'xaffinity session end; unset AFFINITY_SESSION_CACHE' EXIT

# Your commands here - cleanup happens automatically on exit, error, or Ctrl+C
xaffinity list export "My List" | jq '.entries[]' | xaffinity person get
```

### Quick one-liner pattern (subshell)

For simple pipelines, use a subshell - the session dir is cleaned up by TTL:

```bash
(
  export AFFINITY_SESSION_CACHE=$(xaffinity session start)
  xaffinity list export "My List" | xaffinity person get
)
```

### Cache behavior

- Cache is scoped to your API key (multi-tenant safe)
- Default TTL is 10 minutes (configurable via `AFFINITY_SESSION_CACHE_TTL`)
- Cache is file-based in the specified directory
- `session end` is idempotent - safe to call multiple times

### Environment variables

| Variable | Description |
|----------|-------------|
| `AFFINITY_SESSION_CACHE` | Directory for session cache files |
| `AFFINITY_SESSION_CACHE_TTL` | Cache TTL in seconds (default: 600) |

### Session commands

| Command | Description |
|---------|-------------|
| `xaffinity session start` | Create session cache, output path |
| `xaffinity session end` | Clean up session cache directory |
| `xaffinity session status` | Show cache stats (entries, size, age) |

### Debugging

Use `--trace` to see cache hit/miss information:

```bash
xaffinity --trace list export "My List"
# trace #+ cache hit: list_fields_12345
# trace #- cache miss: saved_views_12345
```

Use `--no-cache` to bypass session cache:

```bash
xaffinity --no-cache list export "My List"
```

## Identity

### `xaffinity whoami`

Validates credentials and prints tenant/user context.

```bash
xaffinity whoami
xaffinity whoami --json | jq
```

## URL resolution

### `xaffinity resolve-url <url>`

Parses an Affinity UI URL (including tenant hosts like `https://<tenant>.affinity.co/...` or `https://<tenant>.affinity.com/...`) and validates it by fetching the referenced object.

```bash
xaffinity resolve-url "https://app.affinity.co/companies/263169568"
xaffinity resolve-url "https://mydomain.affinity.com/companies/263169568" --json
```

## Query

### `xaffinity query`

Execute structured JSON queries against Affinity data. Supports complex filtering, aggregations, grouping, and including related entities.

```bash
# Simple query
xaffinity query --query '{"from": "persons", "limit": 10}'

# Filter with conditions
xaffinity query --query '{"from": "persons", "where": {"path": "email", "op": "contains", "value": "@acme.com"}}'

# Include related entities
xaffinity query --query '{"from": "persons", "include": ["companies"], "limit": 50}'

# Aggregation
xaffinity query --query '{"from": "opportunities", "groupBy": "status", "aggregate": {"count": {"count": true}}}'

# Dry-run to preview execution plan
xaffinity query --query '{"from": "persons", "include": ["companies"]}' --dry-run

# From file
xaffinity query --file query.json
```

**Key options:**

- `--query <json>`: Inline JSON query string
- `--file <path>`: Read query from JSON file
- `--dry-run`: Show execution plan without running (estimated API calls)
- `--dry-run-verbose`: Detailed plan with step breakdown
- `--max-records <n>`: Safety limit (default: 10000)
- `--timeout <secs>`: Query timeout (default: 300)
- `--csv` / `--json`: Output format

See [Query Language Reference](../reference/query-language.md) and [Query Guide](../guides/query-command.md) for full documentation.

## People

### `xaffinity person ls`

List persons. Supports field selection, filter expressions, and free-text search.

Options:

- `--query <term>` / `-q <term>`: free-text search (name or email)
- `--field <id-or-name>` (repeatable): field ID or name to include
- `--field-type <type>` (repeatable): field type to include (global, enriched, relationship-intelligence)
- `--filter <expression>`: filter expression (custom fields only)
- `--page-size`, `--cursor`, `--max-results`, `--all`: pagination options
- `--csv`: output as CSV (to stdout)
- `--csv-bom`: add UTF-8 BOM for Excel compatibility

**Note:** `--filter` only works with custom fields. To filter on built-in properties like `type`, `firstName`, etc., use `--json` output with `jq`.

**Note:** `--query` can be combined with `--field` and `--field-type` to search with field data.

```bash
xaffinity person ls
xaffinity person ls --page-size 50
xaffinity person ls --field-type enriched --all
xaffinity person ls --filter 'Email =~ "@acme.com"'
xaffinity person ls --query "alice@example.com"
xaffinity person ls --query "Alice" --field-type enriched --all
xaffinity person ls --all --csv > people.csv
xaffinity person ls --all --csv --csv-bom > people.csv
```

See the [CSV Export Guide](../guides/csv-export.md) for more details.

### `xaffinity person get <personSelector>`

Fetch a person by id, UI URL (including tenant hosts), or a resolver selector.

Examples:

```bash
xaffinity person get 26229794
xaffinity person get "https://mydomain.affinity.com/persons/26229794"
xaffinity person get email:alice@example.com
xaffinity person get 'name:"Alice Smith"'
```

Field selection:

- `--all-fields`: include all supported (non-list-specific) fields.
- `--field <id-or-exact-name>` (repeatable)
- `--field-type <type>` (repeatable)
- `--no-fields`: skip fields entirely.

Expansions:

- `--expand lists`: include lists the person is on (auto-paginates up to a safe cap; use `--max-results` / `--all` to adjust).
- `--expand list-entries`: include list entries for the person (first page by default; use `--max-results` / `--all` to fetch more).
- `--list <id-or-exact-name>`: filter list entries to a specific list (requires `--expand list-entries`).
- `--list-entry-field <id-or-exact-name>` (repeatable): project list-entry fields into columns (requires `--expand list-entries`). Field names are only allowed with `--list`.
- `--show-list-entry-fields`: render per-list-entry Fields tables in human output (requires `--expand list-entries` and `--max-results <= 3`). Mutually exclusive with `--list-entry-field`.
- `--list-entry-fields-scope list-only|all`: control which fields appear in list-entry tables (human output only).

Interaction dates (V1 API):

- `--with-interaction-dates`: include interaction date summaries (last/next meeting, email dates). Uses V1 API.
- `--with-interaction-persons`: include person IDs involved in each interaction (requires `--with-interaction-dates`).

```bash
xaffinity person get 26229794 --all-fields --expand lists
xaffinity person get 26229794 --expand list-entries --list "Dealflow" --max-results 200
xaffinity person get 26229794 --expand list-entries --list "Dealflow" --list-entry-field Stage --list-entry-field Amount
xaffinity person get 26229794 --expand list-entries --max-results 1 --show-list-entry-fields
xaffinity person get 26229794 --expand list-entries --max-results 1 --show-list-entry-fields --list-entry-fields-scope all
xaffinity person get 26229794 --all-fields --expand lists --json | jq '.data.person.name'

# Include interaction dates (last meeting, next meeting, email dates)
xaffinity person get 26229794 --with-interaction-dates --json
xaffinity person get 26229794 --with-interaction-dates --with-interaction-persons --json
```

### `xaffinity person create`

```bash
xaffinity person create --first-name Ada --last-name Lovelace --email ada@example.com
xaffinity person create --first-name Ada --last-name Lovelace --company-id 224925494
```

### `xaffinity person update <personId>`

```bash
xaffinity person update 26229794 --email ada@example.com --email ada@work.com
xaffinity person update 26229794 --first-name Ada --last-name Byron
```

### `xaffinity person delete <personId>`

```bash
xaffinity person delete 26229794
```

### `xaffinity person merge <primaryId> <duplicateId>`

```bash
xaffinity --beta person merge 111 222
```

### `xaffinity person files dump <personId>`

Downloads all files attached to a person into a folder bundle with a `manifest.json`.

```bash
xaffinity person files dump 12345 --out ./bundle
```

### `xaffinity person files upload <personId>`

Uploads one or more files to a person.

```bash
xaffinity person files upload 12345 --file doc.pdf
xaffinity person files upload 12345 --file a.pdf --file b.pdf
```

## Companies

### `xaffinity company ls`

List companies. Supports field selection, filter expressions, and free-text search.

Options:

- `--query <term>` / `-q <term>`: free-text search (name or domain)
- `--field <id-or-name>` (repeatable): field ID or name to include
- `--field-type <type>` (repeatable): field type to include (global, enriched, relationship-intelligence)
- `--filter <expression>`: filter expression (custom fields only)
- `--page-size`, `--cursor`, `--max-results`, `--all`: pagination options
- `--csv`: output as CSV (to stdout)
- `--csv-bom`: add UTF-8 BOM for Excel compatibility

**Note:** `--filter` only works with custom fields. To filter on built-in properties like `name`, `domain`, etc., use `--json` output with `jq`.

**Note:** `--query` can be combined with `--field` and `--field-type` to search with field data.

```bash
xaffinity company ls
xaffinity company ls --page-size 50
xaffinity company ls --field-type enriched --all
xaffinity company ls --filter 'Industry = "Software"'
xaffinity company ls --query "example.com"
xaffinity company ls --query "Example" --field-type enriched --all
xaffinity company ls --all --csv > companies.csv
xaffinity company ls --all --csv --csv-bom > companies.csv
```

See the [CSV Export Guide](../guides/csv-export.md) for more details.

### `xaffinity company get <companySelector>`

Fetch a company by id, UI URL (including tenant hosts), or a resolver selector.

Examples:

```bash
xaffinity company get 224925494
xaffinity company get "https://mydomain.affinity.com/companies/224925494"
xaffinity company get domain:wellybox.com
xaffinity company get 'name:"WellyBox"'
```

Field selection:

- `--all-fields`: include all supported (non-list-specific) fields.
- `--field <id-or-exact-name>` (repeatable)
- `--field-type <type>` (repeatable)
- `--no-fields`: skip fields entirely.

Expansions:

- `--expand lists`: include lists the company is on (auto-paginates up to a safe cap; use `--max-results` / `--all` to adjust).
- `--expand list-entries`: include list entries for the company (first page by default; use `--max-results` / `--all` to fetch more).
- `--expand persons`: include people associated with the company (use `--max-results` / `--all` to control volume).
- `--list <id-or-exact-name>`: filter list entries to a specific list (requires `--expand list-entries`).
- `--list-entry-field <id-or-exact-name>` (repeatable): project list-entry fields into columns (requires `--expand list-entries`). Field names are only allowed with `--list`.
- `--show-list-entry-fields`: render per-list-entry Fields tables in human output (requires `--expand list-entries` and `--max-results <= 3`). Mutually exclusive with `--list-entry-field`.
- `--list-entry-fields-scope list-only|all`: control which fields appear in list-entry tables (human output only).

Interaction dates (V1 API):

- `--with-interaction-dates`: include interaction date summaries (last/next meeting, email dates). Uses V1 API.
- `--with-interaction-persons`: include person IDs involved in each interaction (requires `--with-interaction-dates`).

```bash
xaffinity company get 224925494 --all-fields --expand lists
xaffinity company get 224925494 --expand list-entries --list "Dealflow" --max-results 200
xaffinity company get 224925494 --expand list-entries --list "Dealflow" --list-entry-field Stage --list-entry-field Amount
xaffinity company get 224925494 --expand list-entries --max-results 1 --show-list-entry-fields
xaffinity company get 224925494 --expand list-entries --max-results 1 --show-list-entry-fields --list-entry-fields-scope all
xaffinity company get 224925494 --expand persons --max-results 50
xaffinity company get 224925494 --all-fields --expand lists --json | jq '.data.company.name'

# Include interaction dates (last meeting, next meeting, email dates)
xaffinity company get 224925494 --with-interaction-dates --json
xaffinity company get 224925494 --with-interaction-dates --with-interaction-persons --json
```

### `xaffinity company create`

```bash
xaffinity company create --name "Acme Corp" --domain acme.com
xaffinity company create --name "Acme Corp" --person-id 26229794
```

### `xaffinity company update <companyId>`

```bash
xaffinity company update 224925494 --domain acme.com
xaffinity company update 224925494 --person-id 26229794 --person-id 26229795
```

### `xaffinity company delete <companyId>`

```bash
xaffinity company delete 224925494
```

### `xaffinity company merge <primaryId> <duplicateId>`

```bash
xaffinity --beta company merge 111 222
```

### `xaffinity company files dump <companyId>`

```bash
xaffinity company files dump 9876 --out ./bundle
```

Notes:
- Saved files use the original filename when possible; if multiple files share the same name, the CLI disambiguates by appending the file id.

### `xaffinity company files upload <companyId>`

Uploads one or more files to a company.

```bash
xaffinity company files upload 9876 --file doc.pdf
xaffinity company files upload 9876 --file a.pdf --file b.pdf
```

## Opportunities

### `xaffinity opportunity ls`

List opportunities.

Options:

- `--page-size`, `--cursor`, `--max-results`, `--all`: pagination options
- `--csv`: output as CSV (to stdout)
- `--csv-bom`: add UTF-8 BOM for Excel compatibility

```bash
xaffinity opportunity ls
xaffinity opportunity ls --page-size 200 --all --json
xaffinity opportunity ls --all --csv > opportunities.csv
xaffinity opportunity ls --all --csv --csv-bom > opportunities.csv
```

See the [CSV Export Guide](../guides/csv-export.md) for more details.

### `xaffinity opportunity get <opportunitySelector>`

Fetch an opportunity by id or UI URL (including tenant hosts).

```bash
xaffinity opportunity get 123
xaffinity opportunity get "https://mydomain.affinity.com/opportunities/123"
xaffinity opportunity get 123 --details
```

Notes:
- `--details` fetches a fuller payload with associations and list entries.

### `xaffinity opportunity create`

Create a new opportunity.

```bash
xaffinity opportunity create --name "Series A" --list "Dealflow"
xaffinity opportunity create --name "Series A" --list 123 --person-id 1 --company-id 2
```

### `xaffinity opportunity update <opportunityId>`

Update an opportunity (replaces association arrays when provided).

```bash
xaffinity opportunity update 123 --name "Series A (Closed)"
xaffinity opportunity update 123 --person-id 1 --person-id 2
```

### `xaffinity opportunity delete <opportunityId>`

```bash
xaffinity opportunity delete 123
```

### `xaffinity opportunity files upload <opportunityId>`

Uploads one or more files to an opportunity.

```bash
xaffinity opportunity files upload 123 --file doc.pdf
xaffinity opportunity files upload 123 --file a.pdf --file b.pdf
```

## Lists

### `xaffinity list ls`

```bash
xaffinity list ls
xaffinity list ls --all --json
```

### `xaffinity list create`

```bash
xaffinity list create --name "Dealflow" --type opportunity --private
xaffinity list create --name "People" --type person --public --owner-id 42
```

### `xaffinity list get <list>`

Accepts a list ID or an exact list name.

The Fields table includes a `valueType` column (e.g., `dropdown-multi`, `ranked-dropdown`).

```bash
xaffinity list get 123
xaffinity list get "My Pipeline" --json
```

### `xaffinity list export <list>`

Exports list entries with selected fields. This is the most powerful CSV export command, supporting custom fields and complex filtering.

Options:

- `--csv`: output as CSV (to stdout)
- `--csv-bom`: add UTF-8 BOM for Excel compatibility
- `--field <id-or-name>` (repeatable): include specific fields
- `--saved-view <name>`: use a saved view's field selection (server-side filtering)
- `--filter <expression>`: filter expression (client-side; requires a scope flag — see below)
- `--company-id <id>` (repeatable): entity-scoped export — return only rows where the entry is for this company
- `--person-id <id>` (repeatable): entity-scoped export — return only rows where the entry is for this person
- `--first-page-only`: fetch a single page of results; output includes `meta.truncated=true` and `meta.truncationReason="firstPageOnly"` when there are more pages
- `--expand <type>` (repeatable): expand associated entities or interaction data
  - `persons`, `companies`, `opportunities`: Expand related entities
  - `interactions`: Add interaction date summaries (last/next meeting, email dates)
- `--check-unreplied`: Check for unreplied incoming messages (email/chat) for each list entry
- `--unreplied-types <types>`: Comma-separated types to check: email, chat, all (default: email,chat)
- `--unreplied-lookback-days <days>`: Lookback period for unreplied message detection (default: 30)

**`--filter` requires a scope flag.** Since v1.13, `list export --filter <expr>` must be combined with one of `--all`, `--max-results <N>`, or `--first-page-only`; otherwise the command exits with code 2. Filtering is client-side (all matching pages are downloaded and filtered locally), so an unscoped `--filter` could silently read the entire list. For large lists prefer `--saved-view` (server-side) or the entity-scoped `--company-id` / `--person-id` forms.

**Truncation envelope.** JSON output includes a `meta` object with `truncated` (boolean) and, when `truncated=true`, a `truncationReason` string naming the cause (currently: `firstPageOnly`). Always check this before treating a response as complete.

```bash
xaffinity list export 123 --csv > out.csv
xaffinity list export "My Pipeline" --saved-view "Board" --csv > out.csv
xaffinity list export 123 --field Stage --field Amount --filter '"Stage" = "Active"' --max-results 500 --csv > out.csv
xaffinity list export 123 --csv --csv-bom > out.csv

# Entity-scoped export (dedup-on-create pattern)
xaffinity list export "Pipeline" --company-id 555 --json
xaffinity list export "Pipeline" --person-id 12345 --json

# Single-page probe — useful when you only need the first batch
xaffinity list export "Pipeline" --first-page-only --json

# Include interaction dates (last meeting, next meeting, email dates)
xaffinity list export "Dealflow" --expand interactions --json
xaffinity list export "Dealflow" --expand interactions --csv > pipeline.csv

# Check for unreplied incoming messages (email + chat)
xaffinity list export "Pipeline" --check-unreplied --json
xaffinity list export "Pipeline" --check-unreplied --unreplied-lookback-days 60 --csv > unreplied.csv

# Check email-only unreplied messages
xaffinity list export "Pipeline" --check-unreplied --unreplied-types email --json
```

See the [CSV Export Guide](../guides/csv-export.md) for more details.

### List Entry Commands

**Shorthand:** All `list entry` commands are also available as top-level `entry` commands:
```bash
xaffinity entry get 123 456          # Same as: xaffinity list entry get 123 456
xaffinity entry field 123 456 ...    # Same as: xaffinity list entry field 123 456 ...
```

### `xaffinity list entry add <list>`

```bash
xaffinity list entry add 123 --person-id 26229794
xaffinity list entry add "Dealflow" --company-id 224925494
```

### `xaffinity list entry delete <list> <entryId>`

```bash
xaffinity list entry delete 123 98765
```

### `xaffinity list entry field <list> <entryId>`

Unified command for getting, setting, appending, and unsetting field values.

```bash
# Set a single field
xaffinity list entry field "Portfolio" 123 --set Status "Active"

# Set multiple fields
xaffinity list entry field "Portfolio" 123 --set Status "Active" --set Priority "High"

# Append to a multi-value field (e.g., tags)
xaffinity list entry field "Portfolio" 123 --append Tags "Priority"

# Unset a field (remove all values)
xaffinity list entry field "Portfolio" 123 --unset OldField

# Unset a specific value from a multi-value field
xaffinity list entry field "Portfolio" 123 --unset-value Tags "OldTag"

# Batch set via JSON
xaffinity list entry field "Portfolio" 123 --set-json '{"Status": "Active", "Priority": "High"}'

# Get specific field values
xaffinity list entry field "Portfolio" 123 --get Status --get Priority

# Swap tags (append new, remove old)
xaffinity list entry field "Portfolio" 123 --append Tags "NewTag" --unset-value Tags "OldTag"
```

**Notes:**
- Field names are resolved case-insensitively
- Field IDs (`field-123`) can be used directly
- `--set` replaces all existing values; use `--append` to add to multi-value fields
- `--get` is exclusive with write operations
- `--get` returns resolved objects for person/company reference fields (with `firstName`, `lastName`, `primaryEmailAddress`, etc.) and full dropdown option data (with `text`, `color`). Output format matches `list export`.
- Operation order: `--set`/`--set-json` → `--append` → `--unset`/`--unset-value`

## Notes

### `xaffinity note ls`

```bash
xaffinity note ls
xaffinity note ls --person-id 123 --json
```

### `xaffinity note get <noteId>`

```bash
xaffinity note get 9876
```

### `xaffinity note create`

```bash
xaffinity note create --content "Met with the team" --person-id 123
xaffinity note create --content "<p>Meeting notes</p>" --type html --company-id 456
```

### `xaffinity note update <noteId>`

```bash
xaffinity note update 9876 --content "Updated note content"
```

### `xaffinity note delete <noteId>`

```bash
xaffinity note delete 9876
```

## Reminders

### `xaffinity reminder ls`

The `--due-after` and `--due-before` options accept the same date formats as `--due-date`:
- **ISO-8601**: `2025-01-15`, `2025-01-15T09:00:00Z`
- **Relative**: `+7d`, `+2w`, `+1m`, `+1y`
- **Keywords**: `now`, `today`, `tomorrow`, `yesterday`

```bash
xaffinity reminder ls
xaffinity reminder ls --owner-id 42 --status active --json
xaffinity reminder ls --due-after today --due-before +7d
```

### `xaffinity reminder get <reminderId>`

```bash
xaffinity reminder get 12345
```

### `xaffinity reminder create`

The `--due-date` option accepts multiple formats:
- **ISO-8601**: `2025-01-15`, `2025-01-15T09:00:00Z`
- **Relative**: `+7d` (7 days), `+2w` (2 weeks), `+1m` (1 month), `+1y` (1 year)
- **Keywords**: `now`, `today`, `tomorrow`, `yesterday`

```bash
xaffinity reminder create --owner-id 42 --type one-time --due-date +7d --person-id 123
xaffinity reminder create --owner-id 42 --type one-time --due-date tomorrow --person-id 123
xaffinity reminder create --owner-id 42 --type one-time --due-date 2025-01-15T09:00:00Z --person-id 123
xaffinity reminder create --owner-id 42 --type recurring --reset-type interaction --reminder-days 3 --company-id 456
```

### `xaffinity reminder update <reminderId>`

```bash
xaffinity reminder update 12345 --content "Follow up after demo"
xaffinity reminder update 12345 --completed
```

### `xaffinity reminder delete <reminderId>`

```bash
xaffinity reminder delete 12345
```

## Interactions

### `xaffinity interaction ls`

List interactions for an entity. Requires `--type`, one entity selector (`--person-id`, `--company-id`, or `--opportunity-id`), and a date range (`--days` or `--after`). Auto-chunks date ranges > 1 year into API-compatible segments.

```bash
xaffinity interaction ls --type email --person-id 123 --days 30
xaffinity interaction ls --type meeting --person-id 123 --after 2025-01-01 --before 2025-02-01
xaffinity interaction ls --type call --company-id 456 --days 365 --json
```

### `xaffinity interaction get <interactionId>`

```bash
xaffinity interaction get 2468 --type meeting
```

### `xaffinity interaction create`

```bash
xaffinity interaction create --type meeting --person-id 123 --content "Met to discuss roadmap" --date 2025-01-10T14:00:00Z
xaffinity interaction create --type email --person-id 123 --content "Intro email" --date 2025-01-05T09:15:00Z --direction outgoing
```

### `xaffinity interaction update <interactionId>`

```bash
xaffinity interaction update 2468 --type meeting --content "Updated meeting notes"
```

### `xaffinity interaction delete <interactionId>`

```bash
xaffinity interaction delete 2468 --type meeting
```

## Fields

### `xaffinity field ls`

```bash
xaffinity field ls --entity-type company
xaffinity field ls --list-id 123 --json
```

### `xaffinity field create`

```bash
xaffinity field create --name "Stage" --entity-type opportunity --value-type dropdown --list-specific
```

### `xaffinity field delete <fieldId>`

```bash
xaffinity field delete field-123
```

### `xaffinity field history`

Show field value change history for a specific field on an entity.

```
xaffinity field history FIELD_ID [OPTIONS]
```

Arguments:

- `FIELD_ID` (required): Field ID (e.g., `field-123`). Use `field ls --list-id LIST` to find IDs.

Options:

- `--person-id <id>`: Filter by person
- `--company-id <id>`: Filter by company
- `--opportunity-id <id>`: Filter by opportunity
- `--list-entry-id <id>`: Filter by list entry
- `--action-type <type>`: Filter by action (`create`, `update`, `delete`)
- `--max-results <n>`: Limit number of results

Exactly one entity selector is required.

```bash
xaffinity field history field-123 --person-id 456
xaffinity field history field-123 --company-id 789 --action-type update
xaffinity --json field history field-123 --list-entry-id 101 --max-results 20
```

## Relationship Strengths

### `xaffinity relationship-strength get`

```bash
xaffinity relationship-strength get --external-id 26229794
xaffinity relationship-strength get --external-id 26229794 --internal-id 42
```

## Tasks

### `xaffinity task get <taskUrl>`

```bash
xaffinity task get https://api.affinity.co/tasks/person-merges/123
```

### `xaffinity task wait <taskUrl>`

```bash
xaffinity task wait https://api.affinity.co/tasks/person-merges/123 --timeout 120
```
