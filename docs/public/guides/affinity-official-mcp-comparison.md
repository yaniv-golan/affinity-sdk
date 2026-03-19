# Affinity SDK vs. Affinity's Official MCP Server

> Last updated: March 2026

In March 2026, Affinity launched an official MCP (Model Context Protocol) Server in beta. This document compares it with the `affinity-sdk` project to help you choose the right tool — or use both.

## TL;DR

This project is a **full-stack Affinity developer platform**: a strongly-typed Python SDK, a feature-rich CLI, a stdio MCP server with guided workflows, and Claude Code plugins with skills. Affinity's official MCP is an **officially-supported stdio server** (self-hosted in beta; hosted version coming soon) with 22 tools focused on relationship intelligence, pipeline queries, meetings, and notes. They are complementary — this project covers the full API surface with comprehensive read/write operations, structured queries, and developer tooling; Affinity's MCP provides an officially-supported, easy-to-install experience for common CRM lookups and note capture.

## What this project includes

This repository ships four integrated components:

| Component | Description |
|---|---|
| **Python SDK** | Full-coverage, strongly-typed client (Pydantic V2, typed IDs, sync + async, autopagination, rate limit handling, caching) |
| **CLI (`xaffinity`)** | Command-line tool with structured queries, filtering, aggregations, multiple output formats (table, JSON, JSONL, CSV, markdown, TOON), and scripting support |
| **MCP Server (`xaffinity-mcp`)** | Stdio-based MCP server with 7 native tools, 8 guided workflow prompts, CLI gateway pattern, and configurable read-only/write modes |
| **Claude Code Plugins** | Three plugins — SDK skills, CLI skills with hooks, and MCP workflow/query skills — that teach Claude best practices for Affinity development |

## Comparison

| | This Project | Affinity Official MCP (Beta) |
|---|---|---|
| **Components** | SDK + CLI + MCP server + Claude Code plugins | MCP server |
| **MCP transport** | stdio (local) | stdio (self-hosted, beta); hosted version coming soon |
| **MCP tools** | 7 lean tools that expose the **entire CLI command surface** (50+ commands) via a gateway pattern — intentionally minimal to reduce context window overhead and improve model accuracy per [Anthropic best practices](https://www.anthropic.com/engineering/writing-tools-for-agents) | 22 dedicated tools (20 read, 2 write) covering a subset of the API |
| **Guided workflows** | 8 MCP prompts (prepare-briefing, pipeline-review, warm-intro, change-status, interaction-brief, log-call, log-message, log-interaction-and-update-workflow) | Conversational queries |
| **API coverage** | Full V1 + V2 surface (all entities, fields, CRUD, files, webhooks, reminders, interactions, relationship strengths) | Subset: persons, companies, opportunities, lists, notes, meetings |
| **Read operations** | Companies, persons, lists, list entries, field values, notes, reminders, interactions, files, relationship strengths, webhooks, rate limits, opportunities, tasks | Persons, companies, opportunities, lists, list entries, fields, notes, meetings, semantic search, current user |
| **Write operations** | Full CRUD: create/update/delete entities, field values, notes, reminders, webhooks, file uploads, interaction logging | Note creation, feedback submission |
| **Type safety** | Typed IDs (`PersonId`, `CompanyId`, etc.), Pydantic V2 models, comprehensive enums | N/A (natural language) |
| **Query language** | Structured JSON queries with filtering, includes, aggregations | Natural language |
| **CLI** | Full-featured (`xaffinity`) with scripting and 6 output formats (table, JSON, JSONL, CSV, markdown, TOON) | N/A |
| **Pagination** | Automatic iterator support | Handled internally |
| **Rate limiting** | Auto-retry with exponential backoff, observability | Handled internally |
| **Async support** | Full sync + async clients | N/A |
| **Caching** | Optional field metadata caching | N/A |
| **Error handling** | Typed exception hierarchy | N/A |
| **Safety controls** | Configurable read-only mode, destructive command confirmation | N/A |
| **Extensibility** | Build anything — custom MCP servers, scripts, pipelines, plugins | Use as-is |
| **MCP runtime** | Bash 3.2+ (macOS default) — no Python or package manager needed for the MCP server itself | Python + uv (`uvx`) |
| **Setup** | `pip install affinity-sdk` / MCPB bundle / manual config | `uvx affinity-mcp` or `claude mcp add affinity-mcp` |
| **Code required** | SDK/CLI: yes (Python). MCP server: no (natural language) | No |
| **Client compatibility** | Claude Desktop, ChatGPT Desktop, Cursor, Windsurf, VS Code + Copilot, Zed, JetBrains, any stdio MCP client | Claude Desktop, Claude CLI, GitHub Copilot (VS Code), Gemini CLI, any MCP-compatible client |
| **Maintained by** | Community ([@yaniv-golan](https://github.com/yaniv-golan)) | Affinity (official) |

## When to use this project

- You need **full API coverage**, including write operations beyond note creation
- You need the **MCP server** with guided workflow prompts (prepare-briefing, pipeline-review, warm-intro, deal updates)
- You want the **CLI gateway** pattern that exposes the entire CLI surface through MCP
- You're building **custom integrations**, data pipelines, or automation scripts with the SDK
- You want **type safety** and compile-time checks against ID mixing
- You need **structured queries** with filtering, includes, and aggregations
- You need **file operations** (upload, download, streaming)
- You need **webhook, reminder, or interaction management**
- You use **Claude Code** and want skills that teach Claude correct SDK/CLI patterns
- You need **safety controls** like read-only mode and destructive command confirmation

## When to use Affinity's Official MCP

- You want an **officially-supported** MCP server with easy `uvx` installation
- Your use case is primarily **read-oriented queries** — "who haven't we contacted recently?", "summarize this pipeline", "what meetings do we have with X?"
- You want to quickly **create or retrieve notes** from a chat interface
- You want **semantic search** for finding companies by natural-language description
- You prefer a **managed hosted endpoint** (coming soon) with no local setup

## Using them together

The two serve different layers. A common pattern:

- Use the **official MCP** for quick conversational lookups and note capture
- Use this project's **MCP server** when you need guided workflows, write operations, or the full CLI surface
- Use the **SDK** for batch operations, data migrations, custom reporting, and bespoke tooling
- Use the **Claude Code plugins** when developing against the Affinity API

## Links

- [This project](https://github.com/yaniv-golan/affinity-sdk)
- [MCP Server docs](https://yaniv-golan.github.io/affinity-sdk/latest/mcp/)
- [Claude Code Plugins docs](https://yaniv-golan.github.io/affinity-sdk/latest/guides/claude-code-plugins/)
- [Affinity Official MCP Docs](https://developer.affinity.co/pages/mcp/introduction)
- [Affinity Official MCP Announcement](https://www.affinity.co/blog/campfire-icymi)
- [Affinity API V2 Documentation](https://api-docs.affinity.co/reference/getting-started-with-your-api)
- [Affinity API V1 Documentation](https://api-docs.affinity.co/reference)
