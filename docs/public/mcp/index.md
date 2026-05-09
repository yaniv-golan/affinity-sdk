# MCP Server

The xaffinity MCP server connects desktop AI tools to Affinity CRM.

## Compatible Clients

MCP (Model Context Protocol) is an open standard. This server works with:

- **Claude Desktop** (Anthropic)
- **ChatGPT Desktop** (OpenAI)
- **Cursor** (AI IDE)
- **Windsurf** (AI IDE)
- **Zed** (AI-native editor)
- **VS Code + GitHub Copilot**
- **Continue** (open-source AI assistant)
- **JetBrains IDEs** (via MCP support)
- Any desktop application supporting [MCP stdio transport](https://modelcontextprotocol.io/)

## Features

- **Entity Search** - Find persons, companies, opportunities
- **Query Language** - Complex queries with filtering, includes, and aggregations
- **Relationship Intelligence** - Strength scores, warm intro paths
- **Workflow Management** - Update pipeline status, manage list entries
- **Interaction Logging** - Log calls, meetings, emails
- **Meeting Prep** - Comprehensive briefings before meetings

---

## Installation Options

### Option 1: MCPB Bundle for Claude Desktop (Recommended)

The easiest installation method - download and double-click:

1. **Install the CLI first:**
   ```bash
   pipx install "affinity-sdk[cli]"
   ```

2. *(Optional)* **Pre-configure your API key:**
   ```bash
   xaffinity config setup-key
   ```
   If you skip this step, Claude Desktop will prompt for your API key during MCPB installation.

3. **Download** `xaffinity-mcp-X.Y.Z.mcpb` from [GitHub Releases](https://github.com/yaniv-golan/affinity-sdk/releases)

4. **Double-click** the file or drag it to Claude Desktop

The MCPB bundle is self-contained (includes MCP framework and JSON processor) but requires the CLI to be installed separately.

!!! note "MCPB support"
    MCPB bundles currently only work with **Claude Desktop**. Other clients require manual configuration (see below).

### Option 2: Manual Configuration

For **Cursor, Windsurf, VS Code + Copilot, Zed**, and other MCP clients:

**Prerequisites:**

1. Install the CLI (choose one):

```bash
# Recommended: isolated installation with pipx
pipx install "affinity-sdk[cli]"

# Alternative: install in a virtualenv
pip install "affinity-sdk[cli]"
```

!!! tip "Why pipx?"
    `pipx` installs CLI tools in isolated environments, avoiding dependency conflicts. See [pipx.pypa.io](https://pipx.pypa.io/) for installation.

2. Configure your API key:

```bash
xaffinity config setup-key
```

3. Verify configuration:

```bash
xaffinity config check-key
```

---

## Client Configuration

For manual installation, add the MCP server to your client's configuration.

!!! tip "Finding the MCP server path"
    If you installed the MCPB bundle, the server is at:
    ```
    ~/Library/Application Support/Claude/Claude Extensions/local.mcpb.yaniv-golan.xaffinity-mcp/server/xaffinity-mcp.sh
    ```

    If you cloned the repo, use the path to your clone:
    ```
    /path/to/your/affinity-sdk/mcp/xaffinity-mcp.sh
    ```

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "xaffinity": {
      "command": "/path/to/xaffinity-mcp.sh"
    }
  }
}
```

### Cursor / Windsurf

Add to your MCP configuration:

```json
{
  "mcpServers": {
    "xaffinity": {
      "command": "/path/to/xaffinity-mcp.sh"
    }
  }
}
```

### VS Code + GitHub Copilot

Add to your MCP settings:

```json
{
  "mcpServers": {
    "xaffinity": {
      "command": "/path/to/xaffinity-mcp.sh"
    }
  }
}
```

### Generic MCP Client

Any MCP client supporting stdio transport can connect using:

```json
{
  "mcpServers": {
    "xaffinity": {
      "command": "/path/to/xaffinity-mcp.sh"
    }
  }
}
```

Replace `/path/to/xaffinity-mcp.sh` with the actual path (see tip above).

---

## Available Tools (7)

The MCP server exposes 7 native tools. Most CRM operations are accessed through the CLI gateway tools, which provide access to the full `xaffinity` CLI.

### Native MCP Tools

| Tool | Description |
|------|-------------|
| `discover-commands` | Search CLI commands by keyword (e.g., "create person", "delete note") |
| `execute-read-command` | Execute read-only CLI commands (get, search, list, export) |
| `execute-write-command` | Execute write CLI commands (create, update, delete) |
| `query` | Execute structured JSON queries with filtering, includes, and aggregations |
| `get-entity-dossier` | Comprehensive entity info: details, relationship strength, interactions, notes, list memberships |
| `get-file-url` | Get presigned URL to access a file attachment |
| `read-xaffinity-resource` | Access dynamic resources via `xaffinity://` URIs |

### CLI Gateway Pattern

The `discover-commands`, `execute-read-command`, and `execute-write-command` tools provide access to the full xaffinity CLI:

1. **Discover** the right command:
   ```json
   {"query": "add person to list", "category": "write"}
   ```

2. **Execute** the command:
   ```json
   {"command": "list entry add", "argv": ["Pipeline", "--person-id", "123"]}
   ```

**Destructive commands** (delete operations) require explicit confirmation:
```json
{"command": "person delete", "argv": ["456"], "confirm": true}
```

### Common CLI Operations (via gateway)

These operations are available through `execute-read-command` and `execute-write-command`:

**Search & Lookup (read-only)**

| CLI Command | Description |
|-------------|-------------|
| `person ls`, `company ls` | Search persons, companies by name/email |
| `list ls` | Find Affinity lists by name |
| `list export <name>` | Export list entries with custom fields |
| `interaction ls` | Interaction history (calls, meetings, emails) for an entity |

**Workflow Management**

| CLI Command | Description |
|-------------|-------------|
| `list get <name>` | Get workflow config (statuses, fields, saved views) for a list |
| `entry field <list> <id>` | Update fields on a list entry **(write)** |

**Logging (write operations)**

| CLI Command | Description |
|-------------|-------------|
| `note create` | Add note to a person, company, or opportunity **(write)** |
| `interaction create` | Log call, meeting, email, or chat message **(write)** |

---

## Guided Workflows (8 Prompts)

MCP prompts provide guided multi-step workflows.

### Read-Only Prompts

| Prompt | Use Case |
|--------|----------|
| `prepare-briefing` | Before a meeting - get full context on a person/company |
| `pipeline-review` | Weekly/monthly pipeline review |
| `warm-intro` | Find introduction paths to someone |
| `interaction-brief` | Get interaction history summary for an entity |

### Write Prompts

| Prompt | Use Case |
|--------|----------|
| `log-interaction-and-update-workflow` | After a call/meeting - log and update pipeline |
| `change-status` | Move a deal to a new stage |
| `log-call` | Quick phone call logging |
| `log-message` | Quick chat/text message logging |

### Prompt Invocation

Prompts accept arguments:

```
prepare-briefing(entityName: "John Smith", meetingType: "demo")
warm-intro(targetName: "Jane Doe", context: "partnership discussion")
log-interaction-and-update-workflow(personName: "Alice", interactionType: "call", summary: "Discussed pricing")
```

---

## Resources

Access dynamic data via `xaffinity://` URIs using `read-xaffinity-resource`:

| URI | Returns |
|-----|---------|
| `xaffinity://data-model` | Conceptual guide to Affinity's data model (read first!) |
| `xaffinity://query-guide` | Complete query language reference |
| `xaffinity://workflows-guide` | Workflow patterns and best practices |
| `xaffinity://me` | Current authenticated user details |
| `xaffinity://me/person-id` | Current user's person ID in Affinity |
| `xaffinity://interaction-enums` | Valid interaction types and directions |
| `xaffinity://saved-views/{listId}` | Saved views available for a list |
| `xaffinity://field-catalogs/{listId}` | Field definitions for a list or entity type |
| `xaffinity://workflow-config/{listId}` | Workflow configuration for a list |

---

## Common Workflow Patterns

### Before a Meeting

1. `person ls` or `company ls` to locate the person/company
2. `get-entity-dossier` for full context (relationship strength, recent interactions, notes)
3. **Or use**: `prepare-briefing` prompt for a guided flow

### After a Call/Meeting

1. `interaction create` to record what happened
2. `entry field` to update pipeline fields if needed
3. **Or use**: `log-interaction-and-update-workflow` prompt

### Finding Warm Introductions

1. `person ls` to locate target person
2. `get-entity-dossier` includes relationship strength data
3. **Or use**: `warm-intro` prompt for guided flow

### Pipeline Review

1. `list ls` to locate the pipeline list
2. `query` tool with listEntries to fetch items with filters
3. **Or use**: `pipeline-review` prompt

### Updating Deal Status

1. `person ls` or `company ls` to find the entity
2. `list get <name>` to see available status options
3. `entry field` to update the status field
4. **Or use**: `change-status` prompt

---

## Configuration

### Read-Only Mode

Restrict to read-only tools:

```bash
AFFINITY_MCP_READ_ONLY=1 ./xaffinity-mcp.sh
```

### Disable Destructive Commands

Allow write operations but block delete commands via CLI Gateway:

```bash
AFFINITY_MCP_DISABLE_DESTRUCTIVE=1 ./xaffinity-mcp.sh
```

### Cache TTL

Adjust cache duration (default 10 minutes):

```bash
AFFINITY_SESSION_CACHE_TTL=300 ./xaffinity-mcp.sh
```

### Debug Mode

Enable comprehensive logging for troubleshooting:

```bash
# Full debug mode - enables all debug features
MCPBASH_LOG_LEVEL=debug ./xaffinity-mcp.sh

# Test a single tool with debug output
MCPBASH_LOG_LEVEL=debug mcp-bash run-tool find-entities --args '{"query":"acme"}' --verbose

# Enable shell tracing for deep debugging
MCPBASH_TRACE_TOOLS=true mcp-bash run-tool get-entity-dossier --args '{"entityType":"person","entityId":"12345"}'
```

| Variable | Description |
|----------|-------------|
| `MCPBASH_LOG_LEVEL=debug` | Enable mcp-bash framework debug logging |
| `XAFFINITY_DEBUG=true` | Enable xaffinity-specific debug logging |
| `MCPBASH_LOG_VERBOSE=true` | Show paths in logs (exposes file paths) |
| `MCPBASH_TRACE_TOOLS=true` | Enable shell tracing (`set -x`) for tools |

### Diagnostics

Run health check:

```bash
./xaffinity-mcp.sh doctor
```

---

## Tips

- **Entity types**: `person`, `company`, `opportunity`
- **Interaction types**: `call`, `meeting`, `email`, `chat_message`, `in_person`
- **Dossier is comprehensive**: `get-entity-dossier` returns relationship strength, interactions, notes, and list memberships in one call
- **Check workflow config**: Use `list get <name>` to discover valid status options and saved views before updating
- **Start with data-model**: Read `xaffinity://data-model` to understand when to use `company ls` vs `list export`

---

## Claude Code Installation

Using Claude Code? You can also install via the plugin marketplace:

```bash
/plugin marketplace add yaniv-golan/affinity-sdk
/plugin install affinity-crm-mcp-unofficial@xaffinity
```

This installs the MCP server automatically. See [Claude Code plugins](../guides/claude-code-plugins.md) for additional Claude-specific features.

---

## Discoverability

### MCPB Distribution

This server is distributed as an [MCPB bundle](https://github.com/modelcontextprotocol/mcpb) for one-click installation. Download from [GitHub Releases](https://github.com/yaniv-golan/affinity-sdk/releases).

### MCP Registry

This server is published on the [MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.yaniv-golan/xaffinity-mcp`. The registry entry is updated automatically on every MCP release via CI.

### Future: .well-known Discovery

The MCP protocol is adding standardized discovery via `/.well-known/mcp.json` endpoints ([SEP-1649](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1649)). This will allow clients to auto-discover server capabilities without connecting first.
