# Affinity SDK vs. Affinity's Official MCP Server

> Last updated: October 2026. Affinity's MCP server changes quickly; check the
> [official MCP docs](https://developer.affinity.co/pages/mcp/introduction) for its current tool list.

Affinity runs an official MCP (Model Context Protocol) server that lets AI assistants read and
update your Affinity data. This page compares it with the `affinity-sdk` project to help you pick
the right tool — or use both.

## TL;DR

- **Affinity's official MCP** is a hosted server (`https://mcp.affinity.co/mcp`) that you connect
  to from Claude, ChatGPT, Copilot, Notion, Gemini CLI or any MCP client, signing in with OAuth.
  It reads and writes most CRM records and is the easiest way to work with Affinity **in a chat**.
- **This project** is a developer toolkit: a typed Python SDK, the `xaffinity` CLI, a local MCP
  server that runs the CLI, and Claude Code plugins. Use it when you work with Affinity **in
  code, scripts or a terminal**: bulk updates, exports, data pipelines, webhooks, structured
  queries and aggregations, or a local MCP server that uses your own API key.

## What this project includes

| Component | Description |
|---|---|
| **Python SDK** | Strongly-typed client (Pydantic V2, typed IDs, sync + async, auto-pagination, rate-limit handling, caching) |
| **CLI (`xaffinity`)** | Command-line tool with structured queries, filtering, aggregations, multiple output formats (table, JSON, JSONL, CSV, markdown, TOON), and scripting support |
| **MCP Server (`xaffinity-mcp`)** | Local stdio MCP server with 7 tools that expose the whole CLI (50+ commands), 8 guided workflow prompts, and read-only/write modes |
| **Claude Code Plugins** | Three plugins — SDK skills, CLI skills with hooks, and MCP workflow/query skills — that teach Claude to use Affinity correctly |

## Comparison

| | This Project | Affinity Official MCP |
|---|---|---|
| **What it is** | SDK + CLI + local MCP server + Claude Code plugins | MCP server |
| **Where it runs** | On your machine (stdio) | Hosted by Affinity; a local `uvx affinity-mcp` option also exists |
| **Sign-in** | Affinity API key | OAuth (hosted), or an API key |
| **Plan requirement** | A plan with Affinity API access | Affinity Scale, Advanced or Enterprise |
| **MCP tools** | 7 lean tools that run any CLI command, kept small to save context | About 75 dedicated tools, plus built-in skills (meeting prep, warm intro, market map, data migration, event setup) |
| **Reads** | Companies, persons, opportunities, lists, list entries, saved views, field values and history, notes, reminders, interactions, files, relationship strengths, webhooks, rate limits, merge tasks | Companies, persons, opportunities, lists, list entries, saved views, fields and field-value history, notes, files, meetings, transcripts, relationship strengths, coworker and investor connections, reminders, users, semantic company search |
| **Writes** | Create/update/delete entities, field values, list entries, notes, reminders, webhooks, interactions; file upload; merges | Create/update/delete persons, companies, opportunities, notes, reminders; merges; lists, list entries, fields and dropdown options; field-value upserts; interactions; file upload |
| **Bulk work and export** | CLI and SDK: CSV/JSONL export, scripted bulk updates, structured queries with aggregations | One conversation at a time |
| **Webhooks** | Yes | No |
| **Guided workflows** | 8 MCP prompts (prepare-briefing, pipeline-review, warm-intro, change-status, interaction-brief, log-call, log-message, log-interaction-and-update-workflow) | Built-in skills (see above) |
| **Safety controls** | CLI `--readonly` mode, MCP read-only mode (`AFFINITY_MCP_READ_ONLY=1`), confirmation for destructive commands | Read-only OAuth scope, admin control per client, confirmation before deletes and merges |
| **For developers** | Typed IDs and models, async client, typed exceptions, field-metadata caching | — |
| **Clients** | Any stdio MCP client (Claude Desktop, Claude Code, Cursor, Windsurf, VS Code, Zed, JetBrains, …) | Claude (Desktop, Web, Code), ChatGPT, Copilot (Studio, CLI, VS Code), Notion agents, Gemini CLI, any MCP client |
| **Setup** | `pip install affinity-sdk` / MCPB bundle / Claude Code plugin | Add the hosted server in your client and sign in |
| **Maintained by** | Community ([@yaniv-golan](https://github.com/yaniv-golan)) | Affinity (official) |

## When to use this project

- You are writing **code or scripts** against Affinity (Python SDK, async, typed models)
- You need **bulk operations**, exports (CSV/JSONL) or data pipelines
- You need **structured queries** with filtering, includes and aggregations
- You manage **webhooks**
- You want a **local MCP server** that uses your own API key and needs no hosted service
- You use **Claude Code** and want skills that teach Claude correct SDK/CLI patterns

## When to use Affinity's Official MCP

- You want to work with Affinity **conversationally** in Claude, ChatGPT, Copilot, Notion or Gemini
- You want an **officially supported**, hosted server with OAuth sign-in and no local install
- You want **semantic search**, transcripts, or relationship and connection lookups in chat
- You want Affinity's built-in skills (meeting prep, warm intros, market maps)

## Using them together

- Use the **official MCP** for everyday chat work: lookups, meeting prep, notes and record updates
- Use the **CLI or SDK** for bulk updates, exports, migrations, reporting and automation
- Use this project's **MCP server** when you want an agent to have the full CLI with your own API key
- Use the **Claude Code plugins** when developing against the Affinity API

## Links

- [This project](https://github.com/yaniv-golan/affinity-sdk)
- [MCP Server docs](https://yaniv-golan.github.io/affinity-sdk/latest/mcp/)
- [Claude Code Plugins docs](https://yaniv-golan.github.io/affinity-sdk/latest/guides/claude-code-plugins/)
- [Affinity Official MCP Docs](https://developer.affinity.co/pages/mcp/introduction)
- [Affinity API V2 Documentation](https://api-docs.affinity.co/reference/getting-started-with-your-api)
- [Affinity API V1 Documentation](https://api-docs.affinity.co/reference)
