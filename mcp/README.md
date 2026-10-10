# xaffinity MCP Server

An MCP (Model Context Protocol) server for Affinity CRM, built with the [MCP Bash Framework](https://github.com/yaniv-golan/mcp-bash-framework).

## Features

- **Entity Management** - Search and lookup persons, companies, and opportunities
- **Workflow Management** - View and update pipeline status, manage list entries
- **Relationship Intelligence** - Get relationship strength scores and find warm intro paths
- **Interaction Logging** - Log calls, meetings, emails, and messages
- **Session Caching** - Efficient caching to minimize API calls

## Quick Start

```bash
# 1. Install the xaffinity CLI
pipx install "affinity-sdk[cli]"

# 2. (Optional) Configure your API key - Claude Desktop will prompt if skipped
xaffinity config setup-key

# 3. Install the MCP server (choose one)
#    - Claude Desktop: Download and double-click xaffinity-mcp-*.mcpb
#    - Other clients: See "Manual Installation" below
#    - Claude Code: /plugin marketplace add yaniv-golan/affinity-sdk
```

## Prerequisites

### For Claude Desktop / MCPB Installation (Recommended)

The `.mcpb` bundle is self-contained — it includes the MCP Bash Framework and gojq. You only need:

| Requirement | How to Install | Verify |
|-------------|----------------|--------|
| Python 3.9+ | [python.org](https://python.org) or your package manager | `python --version` |
| xaffinity CLI | `pipx install "affinity-sdk[cli]"` | `xaffinity --version` |
| Affinity API key | `xaffinity config setup-key` (or let Claude Desktop prompt) | `xaffinity config check-key` |

**Keep the CLI and the extension in step.** They are installed separately. When you upgrade one,
upgrade the other: download the latest `.mcpb` after `pipx upgrade affinity-sdk`, and the other way
round. Since MCP 1.25.2, with a CLI older than the extension needs, changes are refused ("This MCP
server needs the xaffinity CLI … or later to make changes") and reads still run with a warning that
results may be incomplete. An older extension with a newer CLI can fail without saying why (for
example, merges with CLI 1.22.0 need extension 1.25.1 or later). Claude Desktop never updates an
extension installed from a `.mcpb` file: install each new release yourself.

### For Manual Installation

Manual installation requires additional dependencies:

| Requirement | How to Install | Verify |
|-------------|----------------|--------|
| Bash 3.2+ | Included on macOS/Linux; Windows: use WSL or Git Bash | `bash --version` |
| Python 3.9+ | [python.org](https://python.org) or your package manager | `python --version` |
| jq or gojq | See [Installing jq/gojq](#installing-jqgojq) below | `jq --version` or `gojq --version` |
| xaffinity CLI | `pipx install "affinity-sdk[cli]"` | `xaffinity --version` |
| Affinity API key | `xaffinity config setup-key` | `xaffinity config check-key` |

#### Installing jq/gojq

Either [jq](https://jqlang.org/) or [gojq](https://github.com/itchyny/gojq) works — the server auto-detects which is available.

**Which to choose:**
- **jq** (recommended) — The original, widely available, full regex support
- **gojq** — Pure Go implementation, also supports YAML, better error messages

**Install jq:**

| Platform | Command |
|----------|---------|
| macOS | `brew install jq` |
| Debian/Ubuntu | `sudo apt-get install jq` |
| Fedora | `sudo dnf install jq` |
| Arch Linux | `sudo pacman -S jq` |
| Windows | `winget install jqlang.jq` or `choco install jq` |

**Install gojq (alternative):**

| Platform | Command |
|----------|---------|
| macOS | `brew install gojq` |
| Any (Go required) | `go install github.com/itchyny/gojq/cmd/gojq@latest` |
| Binary download | [GitHub Releases](https://github.com/itchyny/gojq/releases) |

## Installation

### Option 1: Claude Desktop (One-Click)

The `.mcpb` bundle is fully self-contained — it includes the MCP framework and JSON processor.

1. Install the xaffinity CLI: `pipx install "affinity-sdk[cli]"`
2. *(Optional)* Configure your API key: `xaffinity config setup-key`
   - If skipped, Claude Desktop will prompt for your API key during MCPB installation
3. Download `xaffinity-mcp-*.mcpb` from the [latest release](https://github.com/yaniv-golan/affinity-sdk/releases/latest)
4. Double-click the file or drag it into Claude Desktop

> **Note:** MCPB bundles currently only work with Claude Desktop. Other clients require [manual installation](#option-3-manual-installation).

### Option 2: Claude Code

```bash
/plugin marketplace add yaniv-golan/affinity-sdk
/plugin install affinity-crm-mcp-unofficial@xaffinity
```

### Option 3: Manual Installation

For other MCP clients or development. Requires [additional prerequisites](#for-manual-installation).

1. Download `xaffinity-mcp-plugin.zip` from the [latest release](https://github.com/yaniv-golan/affinity-sdk/releases/latest)
2. Extract and configure your MCP client (see [Usage](#usage) below)
3. The MCP Bash Framework runtime is vendored in `.mcp-bash/` — no separate install needed to run the server.
4. Optional — validate your configuration. `validate` and `doctor` are developer tools that need the
   full [mcp-bash](https://github.com/yaniv-golan/mcp-bash-framework) install (the vendored copy is
   runtime-only); the command prints the install line for the pinned version if it is missing:
   ```bash
   ./xaffinity-mcp.sh validate
   ```

## Troubleshooting

### Verify Installation

Before debugging, verify each layer works in order:

**Step 1: CLI Installation**

First, verify the CLI is installed and can reach the Affinity API. See [CLI: Verify Installation](https://yaniv-golan.github.io/affinity-sdk/latest/cli/#verify-installation).

```bash
# Check CLI is installed
xaffinity --version

# Check API connectivity (set your API key - same one entered in Claude Desktop)
AFFINITY_API_KEY="your-key-here" xaffinity whoami --json
```

If `whoami` hangs, check network connectivity (firewall, proxy, VPN).

**Step 2: MCP Server**

Once the CLI works, test the MCP server end-to-end.

First, locate your MCP server directory:

```bash
# MCPB installation (macOS)
cd ~/Library/Application\ Support/Claude/Claude\ Extensions/local.mcpb.yaniv-golan.xaffinity-mcp/server

# Or find it automatically
cd "$(find ~ -name 'xaffinity-mcp.sh' -type f 2>/dev/null | head -1 | xargs dirname)"
```

<details>
<summary>Other platforms/installations</summary>

| Installation | Path |
|--------------|------|
| MCPB (Windows) | `%APPDATA%\Claude\Claude Extensions\local.mcpb.yaniv-golan.xaffinity-mcp\server\` |
| Manual install | Your chosen extraction location |
| Development | `<repo>/mcp/` |

</details>

**For MCPB installations**: If Step 1 passes, your setup is complete. The MCP server shells out to CLI, so if CLI works with your API key, the MCP server will too. Restart Claude Desktop and test the tools there.

**For manual installations**: You can also test the MCP layer directly:

```bash
AFFINITY_API_KEY="your-key-here" \
  mcp-bash run-tool execute-read-command \
  --args '{"command":"whoami"}'
```

**Expected**: JSON response with your user information.

If Step 1 works but Step 2 fails, the issue is MCP-specific. Run diagnostics (these need the full
mcp-bash install, not just the vendored runtime — see step 4 of manual installation):

```bash
./xaffinity-mcp.sh doctor
./xaffinity-mcp.sh validate
```

### Common Issues

| Error | Cause | Solution |
|-------|-------|----------|
| "Could not detect xaffinity CLI" | CLI not installed | `pip install "affinity-sdk[cli]"` |
| "older than this MCP server needs" / "This MCP server needs the xaffinity CLI X or later" | Outdated CLI | `pipx upgrade affinity-sdk` or `pip install --upgrade "affinity-sdk[cli]"` |
| "API key not configured" | Missing credentials | `xaffinity config setup-key` |
| "No JSON processor found" | jq/gojq not installed (manual install only) | See [Installing jq/gojq](#installing-jqgojq) |
| "Framework not found" | Vendored `.mcp-bash/` directory missing | `cd mcp && mcp-bash vendor` |

For detailed debugging, see [docs/DEBUGGING.md](docs/DEBUGGING.md).

## Usage

### With Claude Code

The server is automatically available through the Claude plugin at `.claude-plugin/mcp.json`.

### With Other MCP Clients

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "xaffinity": {
      "command": "/path/to/affinity-sdk/mcp/xaffinity-mcp.sh"
    }
  }
}
```

## Tools

### CLI Gateway (Primary Interface)

The CLI Gateway provides full access to the xaffinity CLI with minimal token overhead:

| Tool | Description |
|------|-------------|
| `discover-commands` | Search CLI commands by keyword (e.g., "create person", "delete note") |
| `execute-read-command` | Execute read-only CLI commands (get, search, list) |
| `execute-write-command` | Execute write CLI commands (create, update, delete) |

### Query Tool

| Tool | Description |
|------|-------------|
| `query` | Execute structured JSON queries with filters, joins, and aggregations |

For complex data analysis (filtering, grouping, aggregations), use the `query` tool instead of individual CLI commands. See the [Query Language Reference](https://yaniv-golan.github.io/affinity-sdk/latest/reference/query-language/) for full documentation.

### Utility Tools

| Tool | Description |
|------|-------------|
| `get-entity-dossier` | Get comprehensive info for an entity (aggregates 5+ CLI calls) |
| `read-xaffinity-resource` | Read static MCP resources (data-model, etc.) |

#### CLI Gateway Usage

1. **Discover** the right command:
   ```json
   {"query": "add person to list", "category": "write"}
   ```
   Returns compact text format:
   ```
   # cmd|cat|params (s=str i=int b=bool f=flag !=req *=multi)
   list entry add|w|LIST:s! --person-id:i --company-id:i
   ```

2. **Execute** the command:
   ```json
   {"command": "list entry add", "argv": ["Pipeline", "--person-id", "123"]}
   ```

#### Output Formats

Control the format of command results for optimal token efficiency:

```json
{"command": "person ls", "format": "markdown"}
```

| Format | Token Efficiency | Best For |
|--------|-----------------|----------|
| `json` | Low | Programmatic use (default) |
| `markdown` | Medium-High | **LLM comprehension** - best for analysis tasks |
| `toon` | **High (~40% fewer)** | Large datasets, batch operations |
| `csv` | Medium | Spreadsheet export |
| `jsonl` | Medium | Streaming workflows |

**Recommendations:**
- Use `markdown` when you need to analyze or summarize data (LLMs read tables well)
- Use `toon` for large exports to minimize tokens (30-60% smaller than JSON)
- Use `json` when you need full structure with pagination/metadata

#### Destructive Commands

Commands that cannot be undone (delete, merge) need the user's confirmation:

- In clients that support confirmation dialogs (MCP elicitation), the server asks you directly.
- **Claude Desktop extensions get no dialog.** The tool refuses the first call and tells Claude to
  ask you in the conversation; Claude retries with `confirm: true` only after you agree:

```json
{"command": "person delete", "argv": ["456"], "confirm": true}
```

The tool appends `--yes` for the CLI. Since MCP 1.27.0 `confirm: true` counts only after the
server refused that exact command (or showed its dialog) in the last 15 minutes, once: if Claude
sets it on its first call, the call is refused and Claude is told to ask you. Keep Claude
Desktop's tool-approval prompt on for `execute-write-command` as a second check.

**Using Affinity's own connector too?** If both this extension and Affinity's claude.ai
connector are connected, Claude may pick either one. Say "use the xaffinity tools" when you
mean this one.

## Prompts

| Prompt | Description |
|--------|-------------|
| `prepare-briefing` | Prepare for a meeting with comprehensive context |
| `log-interaction-and-update-workflow` | Log interaction and update pipeline |
| `pipeline-review` | Review a workflow pipeline |
| `change-status` | Change workflow status with documentation |
| `warm-intro` | Find warm introduction paths |
| `log-call` | Quick log a phone call |
| `log-message` | Quick log a chat/text message |
| `interaction-brief` | Get interaction history summary |

## Configuration

### API Key

The MCP server uses the xaffinity CLI, which resolves API keys in this order (highest priority first):

| Priority | Source | Description |
|----------|--------|-------------|
| 1 | `--api-key-stdin` | CLI flag (reads from stdin) |
| 2 | `--api-key-file` | CLI flag (reads from file) |
| 3 | `AFFINITY_API_KEY` env var | **← Claude Desktop sets this** |
| 4 | `.env` file | Requires `--dotenv` flag |
| 5 | Config file | `~/.config/xaffinity/config.toml` |

**Claude Desktop / MCPB**: When you enter an API key in the Claude Desktop configuration UI, it's passed to the MCP server as the `AFFINITY_API_KEY` environment variable (priority 3). This means:

- It **overrides** any key in your config file (priority 5)
- It **does not override** explicit CLI flags like `--api-key-file` (priorities 1-2)
- If you have a key configured via `xaffinity config setup-key` AND enter one in Claude Desktop, the **Claude Desktop key wins**

**Recommendation**: Use ONE of these methods:
- **Claude Desktop only**: Enter your key in the MCPB configuration UI (simplest)
- **CLI + MCP**: Run `xaffinity config setup-key` and leave the Claude Desktop field empty
- **Environment variable**: Set `AFFINITY_API_KEY` in your shell profile

### Read-Only Mode

Set `AFFINITY_MCP_READ_ONLY=1` (or `true`) to block all write tools. In the Claude Desktop
bundle this is the **Read-Only Mode** setting.

```bash
AFFINITY_MCP_READ_ONLY=1 ./xaffinity-mcp.sh
```

### Disable Destructive Commands

Set `AFFINITY_MCP_DISABLE_DESTRUCTIVE=1` (or `true`) to allow writes but block commands that
cannot be undone (delete, merge), even with confirmation. In the bundle this is **Disable
Destructive Commands**.

```bash
AFFINITY_MCP_DISABLE_DESTRUCTIVE=1 ./xaffinity-mcp.sh
```

> **Check that a setting took effect:** ask Claude to run a *dry-run* `person delete 1`. With
> read-only on, it is refused with "Writes are off: the server is in read-only mode"; with
> destructive commands disabled, it returns `destructive_disabled`. Nothing is changed either way.

### Cache TTL

Adjust cache duration (default 10 minutes):

```bash
AFFINITY_SESSION_CACHE_TTL=300 ./xaffinity-mcp.sh
```

### Affinity API version

By default every request uses your API key's default Affinity V2 API version. To pin one
(e.g. to get the newest response shapes), set `AFFINITY_API_VERSION` (or the **Affinity API
Version** field in Claude Desktop's extension settings):

```bash
AFFINITY_API_VERSION=2026-09-17 ./xaffinity-mcp.sh
```

Accepted values: `2024-01-01`, `2026-07-15`, `2026-09-17`, or `current` (always the newest).
`AFFINITY_PROFILE` is also passed through, so a profile from the xaffinity config file
(including its `api_version`) can be selected. Tool results report the version that answered
in `meta.affinityApiVersion`. See the [API versions guide](https://yaniv-golan.github.io/affinity-sdk/latest/guides/api-versions/).

### Update Notifications

The MCP server checks for CLI updates at startup and displays a warning if a new version is available. This helps MCP-only users (who don't run CLI commands directly) stay up to date.

Update checks are:
- **Non-blocking**: The check runs in the background and doesn't delay server startup
- **Throttled**: Background checks are limited to once per 24 hours
- **Respectful**: Honors user opt-out via `XAFFINITY_NO_UPDATE_CHECK=1` or config file

To disable update notifications:

```bash
# Via environment variable
XAFFINITY_NO_UPDATE_CHECK=1 ./xaffinity-mcp.sh

# Or via CLI config
xaffinity config update-check --disable
```

## Development

### Run Diagnostics

```bash
./xaffinity-mcp.sh doctor
```

### Debug Mode

Enable debug logging for troubleshooting. Debug mode shows version info at startup and adds component prefixes to all log messages.

```bash
# Persistent debug mode (recommended - survives reinstalls)
mkdir -p ~/.config/xaffinity-mcp && touch ~/.config/xaffinity-mcp/debug

# Session-only via environment variable
XAFFINITY_MCP_DEBUG=1 ./xaffinity-mcp.sh

# Disable persistent debug mode
rm ~/.config/xaffinity-mcp/debug
```

When debug mode is enabled, logs show:
```
[xaffinity-mcp:1.2.3] Debug mode enabled
[xaffinity-mcp:1.2.3] Versions: mcp=1.2.3 cli=0.6.9 mcp-bash=v0.9.3
[xaffinity-mcp:1.2.3] Process: pid=12345 started=2026-01-06T10:30:00-08:00
```

#### Log Locations

| Context | Log Location |
|---------|--------------|
| Claude Desktop (macOS) | `~/Library/Logs/Claude/mcp-server-xaffinity MCP.log` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\Logs\mcp-server-xaffinity MCP.log` |
| CLI standalone | stderr (console) |

#### Advanced Debug Options

| Variable | Description |
|----------|-------------|
| `XAFFINITY_MCP_DEBUG=1` | Enable debug mode (cascades to all components) |
| `MCPBASH_LOG_VERBOSE=true` | Show paths in logs (exposes file paths) |
| `MCPBASH_TRACE_TOOLS=true` | Enable shell tracing (`set -x`) for tools |

See [docs/DEBUGGING.md](docs/DEBUGGING.md) for full debugging guide.

### Claude Code Plugin

The MCP server is also available as a Claude Code plugin, distributed via the repository's own marketplace (`.claude-plugin/marketplace.json`). For standalone MCP server usage with other clients, see the main [MCP documentation](https://yaniv-golan.github.io/affinity-sdk/latest/mcp/).

The plugin files must be assembled before publishing:

#### Build the plugin

```bash
make plugin
```

This copies the MCP server files into `.claude-plugin/`:
- `xaffinity-mcp.sh`, `xaffinity-mcp-env.sh`
- `tools/`, `prompts/`, `resources/`, `lib/`
- `completions/`, `providers/`, `scripts/`, `server.d/`

#### Clean build artifacts

```bash
make clean
```

#### Plugin structure

```
.claude-plugin/
├── plugin.json          # Plugin manifest (checked in)
├── skills/              # Claude Code skills (checked in)
├── xaffinity-mcp.sh     # MCP server (copied by make)
├── tools/               # MCP tools (copied by make)
├── prompts/             # MCP prompts (copied by make)
└── ...                  # Other MCP files (copied by make)
```

See [CONTRIBUTING.md](../CONTRIBUTING.md#mcp-plugin-development) for release instructions.

## License

See the main repository license.
