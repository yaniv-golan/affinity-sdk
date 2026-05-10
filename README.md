# Affinity Python SDK

[![CI](https://github.com/yaniv-golan/affinity-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/yaniv-golan/affinity-sdk/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/yaniv-golan/affinity-sdk/branch/main/graph/badge.svg)](https://codecov.io/gh/yaniv-golan/affinity-sdk)
[![PyPI version](https://img.shields.io/pypi/v/affinity-sdk.svg)](https://pypi.org/project/affinity-sdk/)
[![Python versions](https://img.shields.io/pypi/pyversions/affinity-sdk.svg)](https://pypi.org/project/affinity-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Typed](https://img.shields.io/badge/typed-mypy-blue.svg)](https://mypy-lang.org/)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-orange.svg)](https://docs.pydantic.dev/)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue.svg)](https://yaniv-golan.github.io/affinity-sdk/latest/)
[![MCP](https://img.shields.io/badge/MCP-server-green?logo=modelcontextprotocol)](https://yaniv-golan.github.io/affinity-sdk/latest/mcp/)
[![MCP Bash Framework](https://img.shields.io/badge/MCP-MCP_Bash_Framework-green?logo=modelcontextprotocol)](https://github.com/yaniv-golan/mcp-bash-framework)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-plugins-blueviolet.svg)](https://yaniv-golan.github.io/affinity-sdk/latest/guides/claude-code-plugins/)
[![Built with Skill Creator Plus](https://img.shields.io/badge/Built_with-Skill_Creator_Plus-4ecdc4?style=flat-square)](https://github.com/yaniv-golan/skill-creator-plus)

A modern, strongly-typed Python wrapper for the [Affinity CRM API](https://api-docs.affinity.co/).

Disclaimer: This is an unofficial community project and is not affiliated with, endorsed by, or sponsored by Affinity. “Affinity” and related marks are trademarks of their respective owners. Use of the Affinity API is subject to Affinity’s Terms of Service.

Maintainer: GitHub: `yaniv-golan`

Documentation: https://yaniv-golan.github.io/affinity-sdk/latest/

## Affinity's Official MCP Server

As of March 2026, Affinity has released an [official MCP Server (beta)](https://developer.affinity.co/pages/mcp/introduction) for conversational, natural-language access to your CRM data via AI chat clients. It covers relationship intelligence queries, pipeline summaries, meeting activity, and note capture.

This SDK serves a different purpose — it's a full-coverage, strongly-typed Python client for the Affinity API, supporting the complete read/write surface (companies, persons, lists, field values, notes, reminders, webhooks, files, and more). Use it when you need programmatic control, write operations, type safety, or want to build custom integrations and tooling.

For a detailed comparison, see [Affinity SDK vs. Official MCP](https://yaniv-golan.github.io/affinity-sdk/latest/guides/affinity-official-mcp-comparison/).

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage Examples](#usage-examples)
- [Type System](#type-system)
- [API Coverage](#api-coverage)
- [Authentication](#authentication)
- [Configuration](#configuration)
- [Error Handling](#error-handling)
- [Async Support](#async-support)
- [Development](#development)

## Features

- **Complete API coverage** - Full V1 + V2 support with smart routing
- **CLI included** - Scriptable command-line interface for automation
- **Strong typing** - Full Pydantic V2 models with typed ID classes
- **No magic numbers** - Comprehensive enums for all API constants
- **Automatic pagination** - Iterator support for seamless pagination
- **Rate limit handling** - Automatic retry with exponential backoff
- **Response caching** - Optional caching for field metadata
- **Both sync and async** - Full support for both patterns

### AI Integrations

- **Claude Code plugins** - SDK and CLI knowledge for AI-assisted development
- **MCP Server** - Connect desktop AI tools to Affinity

## Installation

```bash
pip install affinity-sdk
```

Requires Python 3.10+.

Optional (local dev): load `.env` automatically:

```bash
pip install "affinity-sdk[dotenv]"
```

Optional: install the CLI:

```bash
pipx install "affinity-sdk[cli]"
```

The CLI includes a powerful `query` command for structured data extraction with filtering, aggregations, and relationship includes. Output formats include JSON, CSV, markdown, and TOON (token-optimized for LLMs).

CLI docs: https://yaniv-golan.github.io/affinity-sdk/latest/cli/

### MCP Server

Connect desktop AI tools to Affinity CRM.

**Claude Desktop** (easiest - MCPB bundle):

1. Install CLI: `pipx install "affinity-sdk[cli]"`
2. *(Optional)* Pre-configure API key: `xaffinity config setup-key`
   - If skipped, Claude Desktop will prompt for your API key during MCPB install
3. Download the `.mcpb` bundle from [GitHub Releases](https://github.com/yaniv-golan/affinity-sdk/releases)
4. Double-click to install (or drag to Claude Desktop)

**Other clients** (Cursor, Windsurf, VS Code + Copilot, Zed, etc.):

These require manual configuration. See the [MCP Server docs](https://yaniv-golan.github.io/affinity-sdk/latest/mcp/) for step-by-step instructions.

MCP docs: https://yaniv-golan.github.io/affinity-sdk/latest/mcp/

### Claude Code Plugins

If you use [Claude Code](https://docs.anthropic.com/en/docs/claude-code), install plugins for SDK/CLI knowledge:

```bash
/plugin marketplace add yaniv-golan/affinity-sdk
/plugin install affinity-crm-sdk-unofficial@xaffinity   # SDK patterns
/plugin install affinity-crm-cli-xaffinity-unofficial@xaffinity   # CLI patterns + hooks
```

Plugin docs: https://yaniv-golan.github.io/affinity-sdk/latest/guides/claude-code-plugins/

## Documentation

- [Full documentation](https://yaniv-golan.github.io/affinity-sdk/latest/)
- [MCP Server](https://yaniv-golan.github.io/affinity-sdk/latest/mcp/)
- [CLI Reference](https://yaniv-golan.github.io/affinity-sdk/latest/cli/)
- [API Reference](https://yaniv-golan.github.io/affinity-sdk/latest/reference/client/)

## Quick Start

```python
from affinity import Affinity
from affinity.types import FieldType, PersonId

# Recommended: read the API key from the environment (AFFINITY_API_KEY)
client = Affinity.from_env()

# If you use a local `.env` file (requires `affinity-sdk[dotenv]`)
# client = Affinity.from_env(load_dotenv=True)

# Or pass it explicitly
# client = Affinity(api_key="your-api-key")

# Or use as a context manager
with Affinity.from_env() as client:
    # List all companies
    for company in client.companies.all():
        print(f"{company.name} ({company.domain})")

    # Get a person with enriched data
    person = client.persons.get(
        PersonId(12345),
        field_types=[FieldType.ENRICHED, FieldType.GLOBAL]
    )
    print(f"{person.first_name} {person.last_name}: {person.primary_email}")
```

## Usage Examples

### Working with Companies

```python
from affinity import Affinity, F
from affinity.models import CompanyCreate
from affinity.types import CompanyId, FieldType

with Affinity(api_key="your-key") as client:
    # List companies with filtering (V2 API)
    companies = client.companies.list(
        filter=F.field("domain").contains("acme"),
        field_types=[FieldType.ENRICHED],
    )

    # Iterate through all companies with automatic pagination
    for company in client.companies.all():
        print(f"{company.name}: {company.fields}")

    # Get a specific company
    company = client.companies.get(CompanyId(123))

    # Create a company (uses V1 API)
    new_company = client.companies.create(
        CompanyCreate(
            name="Acme Corp",
            domain="acme.com",
        )
    )

    # Search by name, domain, or email
    results = client.companies.search("acme.com")

    # Get list entries for a company
    entries = client.companies.get_list_entries(CompanyId(123))
```

### Working with Persons

```python
from affinity import Affinity
from affinity.models import PersonCreate
from affinity.types import PersonType

with Affinity(api_key="your-key") as client:
    # Get all internal team members
    for person in client.persons.all():
        if person.type == PersonType.INTERNAL:
            print(f"{person.first_name} {person.last_name}")

    # Create a contact
    person = client.persons.create(
        PersonCreate(
            first_name="Jane",
            last_name="Doe",
            emails=["jane@example.com"],
        )
    )

    # Search by email
    results = client.persons.search("jane@example.com")
```

### Working with Lists

```python
from affinity import Affinity, FieldResolver, ResolveMode
from affinity.models import ListCreate
from affinity.types import CompanyId, FieldId, FieldType, ListId, ListType

with Affinity(api_key="your-key") as client:
    # Get all lists
    for lst in client.lists.all():
        print(f"{lst.name} ({lst.type.name})")

    # Get a specific list with field metadata
    pipeline = client.lists.get(ListId(123))
    print(f"Fields: {[f.name for f in pipeline.fields]}")

    # Create a new list
    new_list = client.lists.create(
        ListCreate(
            name="Q1 Pipeline",
            type=ListType.OPPORTUNITY,
            is_public=True,
        )
    )

    # Work with list entries
    entries = client.lists.entries(ListId(123))

    # List entries with field data
    for entry in entries.all(field_types=[FieldType.LIST]):
        print(f"{entry.entity.name}: {entry.fields}")

    # Look up field values by name (instead of raw field IDs)
    # See docs/public/guides/performance.md for details
    resolver = FieldResolver(pipeline.fields)
    for entry in entries.all(field_types=[FieldType.LIST]):
        status = resolver.get(entry, "Status", resolve=ResolveMode.TEXT)
        print(f"{entry.entity.name}: {status}")

    # Add a company to the list
    entry = entries.add_company(CompanyId(456))

    # Update field values
    entries.update_field_value(
        entry.id,
        FieldId(101),
        "In Progress"
    )

    # Batch update multiple fields
    entries.batch_update_fields(
        entry.id,
        {
            FieldId(101): "Closed Won",
            FieldId(102): 100000,
            FieldId(103): "2024-03-15",
        }
    )

    # Use saved views
    views = client.lists.get_saved_views(ListId(123))
    for view in views.data:
        results = entries.from_saved_view(view.id)
```

### Notes

```python
from affinity import Affinity
from affinity.models import NoteCreate, NoteUpdate
from affinity.types import NoteType, PersonId

with Affinity(api_key="your-key") as client:
    # Create a note
    note = client.notes.create(
        NoteCreate(
            content="<p>Great meeting!</p>",
            type=NoteType.HTML,
            person_ids=[PersonId(123)],
        )
    )

    # Get notes for a person
    result = client.notes.list(person_id=PersonId(123))
    for note_item in result.data:
        print(note_item.content)

    # Update a note
    client.notes.update(note.id, NoteUpdate(content="Updated content"))

    # Delete a note
    client.notes.delete(note.id)
```

### Reminders

```python
from datetime import datetime, timedelta
from affinity import Affinity
from affinity.models import ReminderCreate
from affinity.types import PersonId, ReminderResetType, ReminderType, UserId

with Affinity(api_key="your-key") as client:
    # Get current user
    me = client.whoami()

    # Create a follow-up reminder
    reminder = client.reminders.create(
        ReminderCreate(
            owner_id=UserId(me.user.id),
            type=ReminderType.ONE_TIME,
            content="Follow up on proposal",
            due_date=datetime.now() + timedelta(days=7),
            person_id=PersonId(123),
        )
    )

    # Create a recurring reminder
    recurring = client.reminders.create(
        ReminderCreate(
            owner_id=UserId(me.user.id),
            type=ReminderType.RECURRING,
            reset_type=ReminderResetType.INTERACTION,
            reminder_days=30,
            content="Monthly check-in",
            person_id=PersonId(123),
        )
    )
```

### Files

```python
from affinity import Affinity
from affinity.types import FileId, PersonId

with Affinity(api_key="your-key") as client:
    # Download into memory (bytes)
    content = client.files.download(FileId(123))

    # Stream download (for progress bars / piping / large files)
    for chunk in client.files.download_stream(
        FileId(123),
        chunk_size=64_000,
        timeout=60.0,          # per-call request timeout override (seconds)
        deadline_seconds=300,  # total time budget (includes retries/backoff)
    ):
        ...

    # Download to disk
    saved_path = client.files.download_to(
        FileId(123),
        "report.pdf",
        overwrite=False,
        deadline_seconds=300,
    )

    # Upload (multipart form data)
    client.files.upload(
        files={"file": ("report.pdf", b"hello", "application/pdf")},
        person_id=PersonId(123),
    )

    # Upload from disk / bytes (ergonomic helpers)
    client.files.upload_path("report.pdf", person_id=PersonId(123))
    client.files.upload_bytes(b"hello", "report.txt", person_id=PersonId(123))

    # Iterate all files attached to an entity
    for f in client.files.all(person_id=PersonId(123)):
        print(f.name, f.size)
```

### Webhooks

```python
from affinity import Affinity
from affinity.models import WebhookCreate, WebhookUpdate
from affinity.types import WebhookEvent

with Affinity(api_key="your-key") as client:
    # Create a webhook subscription
    webhook = client.webhooks.create(
        WebhookCreate(
            webhook_url="https://your-server.com/webhook",
            subscriptions=[
                WebhookEvent.LIST_ENTRY_CREATED,
                WebhookEvent.LIST_ENTRY_DELETED,
                WebhookEvent.FIELD_VALUE_UPDATED,
            ],
        )
    )

    # List all webhooks (max 3 per instance)
    webhooks = client.webhooks.list()

    # Disable a webhook
    client.webhooks.update(
        webhook.id,
        WebhookUpdate(disabled=True)
    )
```

### Rate Limits

```python
from affinity import Affinity

with Affinity(api_key="your-key") as client:
    # Fetch/observe current rate limits now (one request)
    limits = client.rate_limits.refresh()
    print(f"API key per minute: {limits.api_key_per_minute.remaining}/{limits.api_key_per_minute.limit}")
    print(f"Org monthly: {limits.org_monthly.remaining}/{limits.org_monthly.limit}")

    # Best-effort snapshot derived from tracked response headers (no network)
    snapshot = client.rate_limits.snapshot()
    print(f"Snapshot source: {snapshot.source}")
```

## Type System

The SDK uses strongly-typed ID classes (int/str subclasses) to prevent accidental mixing:

```python
from affinity.types import PersonId, CompanyId, ListId

# These are different types - IDE and type checker will catch mixing
person_id = PersonId(123)
company_id = CompanyId(456)

# This would be a type error:
# client.persons.get(company_id)  # Wrong type!
```

All magic numbers are replaced with enums:

```python
from affinity.types import (
    ListType,        # PERSON, ORGANIZATION, OPPORTUNITY
    PersonType,      # INTERNAL, EXTERNAL, COLLABORATOR
    FieldValueType,  # "text", "number", "datetime", "dropdown-multi", etc.
    InteractionType, # EMAIL, MEETING, CALL, CHAT
    # ... and more
)
```

## API Coverage

| Feature | V2 | V1 | SDK |
|---------|:--:|:--:|:---:|
| Companies (read) | ✅ | ✅ | V2 |
| Companies (write) | ❌ | ✅ | V1 |
| Persons (read) | ✅ | ✅ | V2 |
| Persons (write) | ❌ | ✅ | V1 |
| Lists (read) | ✅ | ✅ | V2 |
| Lists (write) | ❌ | ✅ | V1 |
| List Entries (read) | ✅ | ✅ | V2 |
| List Entries (write) | ❌ | ✅ | V1 |
| Field Values (read) | ✅ | ✅ | V2 |
| Field Values (write) | ✅ | ✅ | V2 |
| Notes | Read-only | ✅ | V1 |
| Reminders | ❌ | ✅ | V1 |
| Webhooks | ❌ | ✅ | V1 |
| Interactions | Read-only | ✅ | V1 |
| Entity Files | ❌ | ✅ | V1 |
| Relationship Strengths | ❌ | ✅ | V1 |

## Authentication

The SDK resolves the API key through the following chain (first non-empty value wins):

1. **Explicit constructor arg** — `Affinity(api_key="…")` or `--api-key` CLI flag
2. **`AFFINITY_API_KEY`** — standard environment variable
3. **`AFFINITY_API_KEY_FILE`** — path to a file containing the key (12-factor / Docker secrets convention)
4. **`AFFINITY_API_KEY_COMMAND`** — shell command whose stdout is the key (credential-helper convention)
5. **`--api-key-file <path>`** or **`--api-key-stdin`** — CLI flags
6. **`xaffinity config setup-key`** — saved to the system keychain

Empty string is treated as unset at every step (safe against stale `export AFFINITY_API_KEY=` lines).

### AFFINITY_API_KEY_FILE — file-based secrets

Set this env var to the path of a file containing your API key. Used by Docker secrets,
Kubernetes mounted Secrets, and Hashicorp Vault agent sidecars.

```bash
# Docker
docker run -e AFFINITY_API_KEY_FILE=/run/secrets/affinity_api_key …

# Kubernetes — mount a Secret as a file and set the env var
# (see your k8s Secret docs for creating the Secret object)
env:
  - name: AFFINITY_API_KEY_FILE
    value: /etc/secrets/affinity-api-key
```

On Posix systems, a `UserWarning` is emitted if the file is group- or world-readable
(mode `0644` or looser). Use `chmod 600` to silence it.

### AFFINITY_API_KEY_COMMAND — command-based secrets

Set this env var to a shell command. The SDK runs it at startup and uses its stdout as the key.
Follows the same convention as `git credential.helper`, `gpg --passphrase-program`, and similar tools.

```bash
# 1Password CLI
export AFFINITY_API_KEY_COMMAND="op read op://Personal/Affinity/credential"

# macOS Keychain
export AFFINITY_API_KEY_COMMAND="security find-generic-password -a affinity -w"

# pass (Unix password manager)
export AFFINITY_API_KEY_COMMAND="pass show affinity/api-key"

# HashiCorp Vault
export AFFINITY_API_KEY_COMMAND="vault kv get -field=api_key secret/affinity"
```

The default timeout is 30 seconds; override with `AFFINITY_API_KEY_COMMAND_TIMEOUT=<seconds>`.
A non-zero exit code or empty stdout raises an error (stderr is included, capped at 500 chars).

> **Note on `.env` files.** When `load_dotenv=True` / `--dotenv` is used, a `.env`
> file containing `AFFINITY_API_KEY=…` will silently take precedence over a
> shell-set `AFFINITY_API_KEY_FILE` or `AFFINITY_API_KEY_COMMAND` (because the
> resolver checks `AFFINITY_API_KEY` at step 2, before the file/command paths).
> Don't mix dotenv with `_FILE`/`_COMMAND` unless you want this precedence. See
> [Authentication caveats](docs/public/guides/authentication.md#caveats).

## Configuration

```python
from affinity import Affinity

client = Affinity(
    api_key="your-api-key",

    # Timeouts and retries
    timeout=30.0,           # Request timeout (seconds)
    max_retries=3,          # Retries for rate-limited requests

    # Caching
    enable_cache=True,      # Cache field metadata
    cache_ttl=300.0,        # Cache TTL (seconds)

    # Debugging
    log_requests=False,     # Log all HTTP requests

    # Hooks (DX-008)
    # on_event=lambda event: print(event.type),
    # on_request=lambda req: print(req.method, req.url),
    # on_response=lambda resp: print(resp.status_code, resp.request.url),
)
```

## Error Handling

The SDK provides a comprehensive exception hierarchy:

```python
from affinity import (
    Affinity,
    AffinityError,
    AuthenticationError,
    MergedEntityError,
    RateLimitError,
    NotFoundError,
    ValidationError,
)

try:
    with Affinity(api_key="your-key") as client:
        person = client.persons.get(PersonId(99999999))
except AuthenticationError:
    print("Invalid API key")
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after}s")
except NotFoundError:
    print("Person not found")
except MergedEntityError as e:
    print(f"Entity {e.source_id} was merged into {e.target_id}")
except ValidationError as e:
    print(f"Invalid request: {e.message}")
except AffinityError as e:
    print(f"API error: {e}")
```

## Async Support

```python
import asyncio
from affinity import AsyncAffinity

async def main():
    async with AsyncAffinity(api_key="your-key") as client:
        # Async operations
        companies = await client.companies.list()
        async for company in client.companies.all():
            print(company.name)

asyncio.run(main())
```

Async support mirrors the sync client surface area (including V1-only services like notes/reminders/webhooks/files).

See `docs/public/guides/sync-vs-async.md` for more details.

If you don't use `async with`, make sure to `await client.close()` (e.g., in a `finally`) to avoid leaking connections.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Optional: live API smoke tests (requires a real API key)
AFFINITY_API_KEY="..." pytest -m integration -q

# Type checking
mypy affinity

# Linting
ruff check affinity
ruff format affinity
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Please read our contributing guidelines first.

## Links

- Repository: https://github.com/yaniv-golan/affinity-sdk
- Issues: https://github.com/yaniv-golan/affinity-sdk/issues
- [Affinity API V2 Documentation](https://api-docs.affinity.co/reference/getting-started-with-your-api)
- [Affinity API V1 Documentation](https://api-docs.affinity.co/reference)

---

Built with [Skill Creator Plus](https://github.com/yaniv-golan/skill-creator-plus).
