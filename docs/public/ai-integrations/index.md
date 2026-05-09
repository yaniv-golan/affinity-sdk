# AI Integrations

Connect AI tools to Affinity CRM for intelligent workflows, meeting preparation, and pipeline management.

## Choose Your Integration

| Integration | Best For | What It Provides |
|-------------|----------|------------------|
| [**MCP Server**](../mcp/index.md) | Any MCP-compatible AI tool | Tools for search, workflows, relationship intelligence |
| [**Claude Code Plugins**](../guides/claude-code-plugins.md) | Claude Code users | Skills that teach Claude SDK/CLI best practices |

## MCP Server

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server connects desktop AI applications to Affinity:

- **Claude Desktop**, **ChatGPT Desktop**, **Cursor**, **Windsurf**, **VS Code + Copilot**, and more
- 7 native tools for entity search, relationship insights, workflow management, and full CLI access via gateway
- 8 guided prompts for common workflows (meeting prep, pipeline review, warm intros)

[MCP Server documentation](../mcp/index.md){ .md-button }

## Claude Code Plugins & Skills

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugins provide **skills**—knowledge packages that teach Claude domain-specific patterns:

| Plugin | Skill | What Claude Learns |
|--------|-------|-------------------|
| `affinity-crm-sdk-unofficial@xaffinity` | affinity-python-sdk | Typed IDs, context managers, pagination, filtering gotchas |
| `affinity-crm-cli-xaffinity-unofficial@xaffinity` | xaffinity-cli-usage | `--readonly` default, `--json` output, API key verification |

[Claude Code Plugins documentation](../guides/claude-code-plugins.md){ .md-button }

## Quick Install

### MCP Server

**Claude Desktop** (easiest - MCPB bundle):

1. Install CLI: `pipx install "affinity-sdk[cli]"`
2. *(Optional)* Pre-configure API key: `xaffinity config setup-key` (Claude Desktop will prompt if skipped)
3. Download `.mcpb` from [GitHub Releases](https://github.com/yaniv-golan/affinity-sdk/releases) and double-click

**Other clients** (Cursor, Windsurf, VS Code, etc.):

```bash
pipx install "affinity-sdk[cli]"
xaffinity config setup-key
```

Then add to your MCP client's configuration:

```json
{
  "mcpServers": {
    "xaffinity": {
      "command": "/path/to/affinity-sdk/mcp/xaffinity-mcp.sh"
    }
  }
}
```

### Claude Code Plugins

```bash
/plugin marketplace add yaniv-golan/affinity-sdk
/plugin install affinity-crm-sdk-unofficial@xaffinity   # SDK patterns
/plugin install affinity-crm-cli-xaffinity-unofficial@xaffinity   # CLI patterns
/plugin install affinity-crm-mcp-unofficial@xaffinity   # MCP server (Claude Code only)
```

## When to Use What

| Scenario | Recommended |
|----------|-------------|
| Writing Python scripts with the SDK | SDK plugin (skills) |
| Running CLI commands | CLI plugin (skills) |
| Meeting prep, pipeline management, logging interactions | MCP Server |
| Using Claude Desktop, Cursor, or other MCP clients | MCP Server |
| Using Claude Code for development | Both plugins + optionally MCP |
