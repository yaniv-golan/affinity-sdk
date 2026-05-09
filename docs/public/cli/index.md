# CLI

The SDK ships an optional `xaffinity` CLI that dogfoods the SDK. Install it as an extra so library-only users don't pay the dependency cost.

## Key Features

- **Query Language**: Complex queries with filtering, aggregations, and includes ([Query Guide](../guides/query-command.md))
- **CSV Export**: Export people, companies, opportunities, and list entries to CSV with `--csv` flag ([CSV Export Guide](../guides/csv-export.md))
- **Filtering**: Server-side filtering on custom fields with `--filter` ([Filtering Guide](../guides/filtering.md))
- **JSON Output**: All commands support `--json` for programmatic use ([Scripting Guide](scripting.md))
- **Datetime Handling**: Local time input, UTC output for JSON ([Datetime Guide](../guides/datetime-handling.md))
- **Pagination**: Fetch all pages with `--all`, control page size with `--page-size` (items per API call), or limit total results with `--max-results`
- **Name Resolution**: Use names instead of IDs for lists, fields, and entities
- **Session Caching**: Share metadata across pipeline commands with `session start/end` ([Pipeline Optimization](commands.md#pipeline-optimization))

See [Commands Reference](commands.md) for complete command documentation.

## AI Integration

### MCP Server

Connect desktop AI tools (Claude Desktop, Cursor, Windsurf, VS Code + Copilot) to Affinity.

**Important**: The CLI is a prerequisite for the MCP server. Install and configure the CLI first (see [Install](#install) and [Authentication](#authentication) below), then follow the [MCP Server](../mcp/index.md) setup guide.

### Claude Code

Using Claude Code? Install the CLI plugin for AI-assisted usage:

```bash
/plugin marketplace add yaniv-golan/affinity-sdk
/plugin install affinity-crm-cli-xaffinity-unofficial@xaffinity
```

This teaches Claude CLI patterns and provides hooks for API key protection and Cowork bootstrap. See [Claude Code plugins](../guides/claude-code-plugins.md) for all available plugins.

## Install

Recommended for end-users:

```bash
pipx install "affinity-sdk[cli]"
```

Or in a virtualenv:

```bash
pip install "affinity-sdk[cli]"
```

## Verify Installation

**Step 1**: Check the CLI is installed:

```bash
xaffinity --version
```

**Expected**: Version number (e.g., `0.15.0`)

**Step 2**: Verify API connectivity with your API key:

```bash
AFFINITY_API_KEY="your-key-here" xaffinity whoami
```

**Expected**: Your user information and tenant details

If `whoami` hangs, check network connectivity (firewall, proxy, VPN). If it returns an authentication error, verify your API key is correct (Settings → API Keys in Affinity).

Once verified, see [Authentication](#authentication) for persistent API key configuration options.

## Authentication

The CLI never makes "background" requests. It only calls the API for commands that require it.

### Quick Setup

Check if a key is already configured:

```bash
xaffinity config check-key
```

Set up a new key securely (hidden input, not echoed):

```bash
xaffinity config setup-key
```

See [config check-key](commands.md#xaffinity-config-check-key) and [config setup-key](commands.md#xaffinity-config-setup-key) for details.

### API Key Sources

The CLI checks these sources in order (highest precedence first):

1. `--api-key-stdin` flag (reads from stdin)
2. `--api-key-file PATH` (reads from file, or `-` for stdin)
3. `AFFINITY_API_KEY` environment variable
4. `.env` file in current directory (requires `--dotenv` flag, or use `--env-file <path>` which implicitly enables dotenv)
5. `api_key` in user config file (`~/.config/xaffinity/config.toml`)

### Reading from File or Stdin

For scripts or CI/CD pipelines, you can pass the API key via file or stdin:

```bash
# Read from a secrets file
xaffinity --api-key-file /run/secrets/affinity-key whoami

# Read from stdin (useful for piping from secret managers)
vault kv get -field=api_key secret/affinity | xaffinity --api-key-stdin whoami

# Equivalent: --api-key-file - reads from stdin
echo "$SECRET_KEY" | xaffinity --api-key-file - whoami
```

### Using .env Files

For project-specific keys, use `--dotenv` to load from `.env`:

```bash
xaffinity --dotenv whoami
xaffinity --dotenv --env-file ./dev.env whoami
```

The `config setup-key --scope project` command creates a `.env` file and adds it to `.gitignore` automatically.

## Output contract

- `--json` is supported on every command.
- In `--json` mode, JSON is written to **stdout**. Progress/logging go to **stderr**.
- Human/table output goes to **stdout**; diagnostics go to **stderr**.
- Commands build a single structured result and then render it as either JSON or table output (no “double implementations”).
- In `--json` mode, `data` is an object keyed by section name (even for single-section commands), and pagination tokens/URLs live in `meta.pagination.<section>`.
- If `--max-results` truncates results mid-page, the CLI may omit `meta.pagination.<section>` to avoid producing an unsafe resume token.

## Performance

The CLI enables SDK in-memory caching for cacheable metadata requests (e.g., field metadata) automatically.

For pipelines running multiple commands, use **session caching** to share metadata across invocations:

```bash
export AFFINITY_SESSION_CACHE=$(xaffinity session start)
xaffinity list export "My List" | xaffinity person get
xaffinity session end
```

See [Pipeline Optimization](commands.md#pipeline-optimization) for details.

### Query Command Tuning

For the `query` command, advanced users can tune concurrency:

| Variable | Default | Description |
|----------|---------|-------------|
| `XAFFINITY_QUERY_CONCURRENCY` | 15 | Max concurrent API calls for fetches/expansions |

Higher values speed up queries with `include` or `expand` but may trigger rate limits on smaller accounts.

## Update Notifications

The CLI checks for available updates once per day and displays a notification after command execution:

```
┌──────────────────────────────────────────────────────┐
│  Update available: 1.0.0 → 1.1.0                     │
│  Run: pip install --upgrade "affinity-sdk[cli]"      │
└──────────────────────────────────────────────────────┘
```

The upgrade command is auto-detected based on your installation method (pipx, uv, pip).

The check is non-blocking and never delays command execution. Notifications are automatically suppressed when:

- Using `--quiet` or `--output json`
- Running in CI/CD environments (`CI`, `GITHUB_ACTIONS`, etc.)
- Not attached to a terminal
- Using the `--no-update-check` flag

### Configuration

Disable update checks via config file (`~/.config/xaffinity/config.toml`):

```toml
[default]
update_check = false
```

Or via environment variable:

```bash
export XAFFINITY_NO_UPDATE_CHECK=1
```

Control notification behavior with `update_notify`:

```toml
[default]
update_notify = "interactive"  # "interactive" (default), "always", or "never"
```

### Manual Check

Check your update status (queries PyPI if no recent cache):

```bash
xaffinity config update-check
```

Force a fresh check regardless of cache age:

```bash
xaffinity config update-check --now
```

See detailed update status (JSON-friendly):

```bash
xaffinity config update-check --status
```

### Background Check (for MCP/Automation)

Trigger a non-blocking background update check (used by MCP server):

```bash
xaffinity config update-check --background
```

This spawns a background worker that checks for updates and caches the result.
It exits immediately with no output on success, exit code 1 on failure.

## Progress + quiet mode

- Long operations show progress bars/spinners on **stderr** when interactive.
- `-q/--quiet` disables progress and suppresses non-essential stderr output.

## Logging

The CLI writes logs to platform-standard locations (via `platformdirs`), with rotation and redaction.

Override with:

- `--log-file <path>`
- `--no-log-file`

## SDK controls

These flags expose useful SDK behaviors directly from the CLI:

- `--readonly`: disallow write operations (guard rail for scripts).
- `--max-retries N`: tune rate-limit retry behavior.
- `--trace`: trace request/response/error events to stderr (safe redaction).

## Advanced configuration (testing)

For testing against mock servers, these environment variables override API base URLs:

- `AFFINITY_V1_BASE_URL`: Override V1 API base URL (default: `https://api.affinity.co`)
- `AFFINITY_V2_BASE_URL`: Override V2 API base URL (default: `https://api.affinity.co/v2`)

These can also be set per-profile in the config file.

## Exit codes

- `0`: success
- `1`: general error
- `2`: usage/validation error (including ambiguous name resolution)
- `3`: auth/permission error (401/403)
- `4`: not found
- `5`: rate limited or temporary upstream failure (429/5xx after retries)
- `130`: interrupted (Ctrl+C)
- `143`: terminated (SIGTERM)
