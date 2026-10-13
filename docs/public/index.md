# Affinity Python SDK

A modern, strongly-typed Python wrapper for the Affinity CRM API, with a scriptable CLI
(`xaffinity`), a local MCP server, and Claude Code plugins.

It covers companies, persons, opportunities, lists and list entries, field values, notes,
reminders, interactions, meetings, transcripts, files, relationship strengths, teams, users,
webhooks and more, using Affinity's V2 API wherever it can and V1 where it can't
([API coverage](reference/api-coverage.md)). Use it for code, scripts and terminal work: bulk
updates, exports, data pipelines, webhooks and integrations. Affinity also runs an official,
hosted MCP server for working in a chat; see
[how the two compare](guides/affinity-official-mcp-comparison.md).

Disclaimer: This is an unofficial community project and is not affiliated with, endorsed by, or sponsored by Affinity. “Affinity” and related marks are trademarks of their respective owners. Use of the Affinity API is subject to Affinity’s Terms of Service.

## Install

```bash
pip install affinity-sdk
```

Requires Python 3.10+.

## Quickstart

```python
from affinity import Affinity

with Affinity(api_key="your-api-key") as client:
    me = client.whoami()
    print(me.user.email)
```

## Next steps

- [Getting started](getting-started.md) - Authentication, first request, common patterns
- [Examples](examples.md)
- [CLI](cli/index.md)
- [AI Integrations](ai-integrations/index.md) - MCP Server & Claude Code plugins
- [Troubleshooting](troubleshooting.md)
- [API reference](reference/client.md)

## Guides

- [Authentication](guides/authentication.md) - API keys, env vars, context managers
- [Configuration](guides/configuration.md) - Client options, timeouts, caching, policies
- [Migrating to 2.0](guides/migrating-to-2.md) - What changed from 1.x
- [API versions](guides/api-versions.md) and [V1/V2 routing](guides/api-versions-and-routing.md)
- [Pagination](guides/pagination.md) - Iterating large result sets
- [Filtering](guides/filtering.md) - Filter query syntax
- [Field values](guides/field-values.md) - Reading and updating field values
- [Query command](guides/query-command.md) - Structured queries from the CLI
- [Webhooks](guides/webhooks.md) - Subscriptions and parsing events
- [Errors & retries](guides/errors-and-retries.md) - Exception handling
- [Rate limits](guides/rate-limits.md) - Managing API quotas
- [Datetime handling](guides/datetime-handling.md) - Timezone behavior
- [Sync vs async](guides/sync-vs-async.md) - Choosing the right client
