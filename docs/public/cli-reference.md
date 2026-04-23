# CLI Output Reference

This document describes the output formats available from Affinity CLI commands.

## Output Formats

The CLI supports multiple output formats via the `--output` / `-o` flag:

| Format | Flag | Availability | Description |
|--------|------|--------------|-------------|
| `table` | default | Global | Rich terminal tables (default for interactive use) |
| `json` | `--json` or `-o json` | Global | Full JSON with envelope (`ok`, `data`, `meta`, `error`) |
| `jsonl` | `-o jsonl` | Per-command | JSON Lines (one object per line, data only) |
| `markdown` | `-o markdown` | Per-command | GitHub-flavored markdown tables (data only) |
| `toon` | `-o toon` | Per-command | Token-Optimized Object Notation (30-60% fewer tokens) |
| `csv` | `--csv` or `-o csv` | Per-command | Comma-separated values (data only) |

**Note:** `table` and `json` are available on all commands. Other formats (`jsonl`, `markdown`, `toon`, `csv`) are available on specific commands that support them (primarily list/export commands).

**For LLM/MCP use**: Use `markdown` for best comprehension or `toon` for large datasets.

**For scripts**: Use `--json` for full structured output with error handling.

**Example:**
```bash
xaffinity person ls --query "Alice" --output markdown
xaffinity query --query '{"from": "companies", "limit": 10}' -o toon
```

## Table of Contents

- [Standard Response Format](#standard-response-format)
- [Resolved Metadata](#resolved-metadata)
- [Pagination Metadata](#pagination-metadata)
- [Rate Limit Metadata](#rate-limit-metadata)
- [Error Responses](#error-responses)
- [Query Command Output](#query-command-output)

## Standard Response Format

All CLI commands support the `--json` flag for machine-readable output. The response follows a consistent structure:

```json
{
  "ok": true,
  "command": {"name": "person get", "inputs": {"personSelector": "12345"}, "modifiers": {}, "resolved": null},
  "data": {
    "person": {
      "id": 12345,
      "firstName": "John",
      "lastName": "Doe",
      "emailAddresses": ["john@example.com"]
    }
  },
  "meta": {
    "durationMs": 234,
    "resolved": {},
    "pagination": null,
    "rateLimit": {
      "limit": 300,
      "remaining": 299,
      "reset": 1609459200
    }
  }
}
```

### Top-Level Fields

- **ok** (boolean): `true` if the command succeeded, `false` if it failed
- **command** (object): Command metadata with `name` (string), `inputs` (object), `modifiers` (object), and `resolved` (object|null)
- **data** (object): The command's result data (structure varies by command)
- **meta** (object): Metadata about the command execution

### Meta Object

The `meta` object contains:

- **durationMs** (number): How long the command took to execute (in milliseconds)
- **resolved** (object): Information about how CLI inputs were resolved to API parameters
- **pagination** (object | null): Pagination metadata for list commands
- **rateLimit** (object): API rate limit information

## Resolved Metadata

The `meta.resolved` field contains information about how CLI inputs were resolved to API parameters. The structure varies by command type based on what inputs need resolution.

### Entity Commands

For person, company, and opportunity commands, the resolved metadata includes information about how entity selectors were resolved:

```json
{
  "resolved": {
    "person": {
      "input": "john@example.com",
      "personId": 12345,
      "source": "email",
      "canonicalUrl": "https://app.affinity.co/persons/12345"
    },
    "fieldSelection": {
      "fieldIds": ["field-123", "field-456"],
      "fieldTypes": ["global"]
    },
    "expand": ["lists", "interactions"]
  }
}
```

#### Entity Selector Resolution

The entity object (e.g., `person`, `company`, `opportunity`) contains:

- **input** (string): The original selector you provided
- **{entityType}Id** (number): The resolved entity ID
- **source** (string): How the selector was resolved
  - `"id"`: Direct numeric ID
  - `"url"`: Affinity URL
  - `"email"`: Email address (persons only)
  - `"name"`: Name search (persons only)
  - `"domain"`: Domain search (companies only)
- **canonicalUrl** (string, optional): The canonical Affinity URL for the entity

#### Field Selection

When field selection options are used (e.g., `--field`), the `fieldSelection` object contains:

- **fieldIds** (array of strings): Specific field IDs requested
- **fieldTypes** (array of strings): Field types requested (e.g., `"global"`, `"list"`, `"enriched"`, `"relationship-intelligence"`)

#### Expansion

When `--expand` is used, the `expand` array contains the expansions requested (e.g., `["lists", "interactions"]`).

### List Commands

For list-related commands, the resolved metadata includes list and saved view resolution:

```json
{
  "resolved": {
    "list": {
      "input": "Sales Pipeline",
      "listId": 789,
      "source": "name"
    },
    "savedView": {
      "input": "My Active Deals",
      "savedViewId": 456,
      "name": "My Active Deals"
    }
  }
}
```

#### List Resolution

The `list` object contains:

- **input** (string): The original list selector
- **listId** (number): The resolved list ID
- **source** (string): How the list was resolved (`"id"`, `"url"`, or `"name"`)

#### Saved View Resolution

When a saved view is specified, the `savedView` object contains:

- **input** (string): The original saved view selector
- **savedViewId** (number): The resolved saved view ID
- **name** (string): The name of the saved view

### Opportunity Commands

Opportunity commands may include additional resolution metadata:

```json
{
  "resolved": {
    "opportunity": {
      "input": "OPP-123",
      "opportunityId": 12345,
      "source": "id"
    },
    "list": {
      "input": "789",
      "listId": 789,
      "source": "id"
    }
  }
}
```

## Pagination Metadata

For commands that return collections, pagination metadata is namespaced by collection type:

```json
{
  "data": {
    "persons": [
      {"id": 1, "firstName": "Alice"},
      {"id": 2, "firstName": "Bob"}
    ]
  },
  "meta": {
    "pagination": {
      "persons": {
        "nextCursor": "eyJpZCI6MTIzfQ==",
        "prevCursor": null
      }
    }
  }
}
```

### Pagination Object Structure

The pagination key always matches the data key (`persons`, `companies`, `opportunities`, `rows`, etc.). Each pagination object contains:

- **nextCursor** (string | null): Cursor for the next page, or `null` if this is the last page
- **prevCursor** (string | null): Cursor for the previous page, or `null` if this is the first page

### Using Pagination Cursors

To fetch the next page, use the `--cursor` option:

```bash
xaffinity person ls --query "Alice" --json
# Get nextCursor from response
xaffinity person ls --query "Alice" --cursor "eyJpZCI6MTIzfQ==" --json
```

## Rate Limit Metadata

All API responses include rate limit information:

```json
{
  "meta": {
    "rateLimit": {
      "limit": 300,
      "remaining": 299,
      "reset": 1609459200
    }
  }
}
```

### Rate Limit Fields

- **limit** (number): Total number of requests allowed per time window
- **remaining** (number): Number of requests remaining in the current window
- **reset** (number): Unix timestamp when the rate limit window resets

## Error Responses

When a command fails, the response structure changes:

```json
{
  "ok": false,
  "command": {"name": "person get", "inputs": {"personSelector": "99999"}, "modifiers": {}, "resolved": null},
  "error": {
    "type": "api_error",
    "message": "Person not found",
    "statusCode": 404,
    "details": {
      "personId": 99999
    }
  },
  "meta": {
    "durationMs": 123
  }
}
```

### Error Object

The `error` object contains:

- **type** (string): Error category
  - `"api_error"`: API returned an error
  - `"usage_error"`: Invalid command usage
  - `"validation_error"`: Input validation failed
  - `"network_error"`: Network/connection issue
- **message** (string): Human-readable error description
- **statusCode** (number, optional): HTTP status code for API errors
- **details** (object, optional): Additional error context

## Examples

### Person Get by Email

Command:
```bash
xaffinity person get email:john@example.com --json
```

Response:
```json
{
  "ok": true,
  "command": "person get",
  "data": {
    "person": {
      "id": 12345,
      "firstName": "John",
      "lastName": "Doe",
      "emailAddresses": ["john@example.com"]
    }
  },
  "meta": {
    "durationMs": 156,
    "resolved": {
      "person": {
        "input": "email:john@example.com",
        "personId": 12345,
        "source": "email",
        "canonicalUrl": "https://app.affinity.co/persons/12345"
      }
    },
    "pagination": null,
    "rateLimit": {
      "limit": 300,
      "remaining": 298,
      "reset": 1609459200
    }
  }
}
```

### Person List with Query and Pagination

Command:
```bash
xaffinity person ls --query "Alice" --json
```

Response:
```json
{
  "ok": true,
  "command": "person ls",
  "data": {
    "persons": [
      {
        "id": 1,
        "firstName": "Alice",
        "lastName": "Smith",
        "emailAddresses": ["alice@example.com"]
      },
      {
        "id": 2,
        "firstName": "Alice",
        "lastName": "Jones",
        "emailAddresses": ["ajones@example.com"]
      }
    ]
  },
  "meta": {
    "durationMs": 234,
    "resolved": {},
    "pagination": {
      "persons": {
        "nextCursor": "eyJpZCI6Mn0=",
        "prevCursor": null
      }
    },
    "rateLimit": {
      "limit": 300,
      "remaining": 297,
      "reset": 1609459200
    }
  }
}
```

### List Export with Saved View

Command:
```bash
xaffinity list export "Sales Pipeline" --saved-view "Active Deals" --json
```

Response:
```json
{
  "ok": true,
  "command": {"name": "list export", "inputs": {"listSelector": "Sales Pipeline"}, "modifiers": {"savedView": "Active Deals"}, "resolved": null},
  "data": {
    "entries": [
      {
        "id": 101,
        "listId": 789,
        "entityId": 12345
      }
    ]
  },
  "meta": {
    "durationMs": 189,
    "resolved": {
      "list": {
        "input": "Sales Pipeline",
        "listId": 789,
        "source": "name"
      },
      "savedView": {
        "input": "Active Deals",
        "savedViewId": 456,
        "name": "Active Deals"
      }
    },
    "pagination": {
      "entries": {
        "nextCursor": null,
        "prevCursor": null
      }
    },
    "rateLimit": {
      "limit": 300,
      "remaining": 296,
      "reset": 1609459200
    }
  }
}
```

## Field Naming Conventions

All field names in JSON output use camelCase to match the Affinity API conventions:

- `firstName` (not `first_name`)
- `emailAddresses` (not `email_addresses`)
- `nextCursor` (not `next_cursor`)

This applies to both the `data` section and all metadata fields.

## JSON Output vs Table Output

Important differences between `--json` and table output:

1. **Completeness**: JSON output includes all fields from the API response, while table output may filter or format fields for readability
2. **Filter Flags**: Flags like `--list-entry-field` and `--field` that control table formatting are ignored in JSON mode
3. **Consistency**: JSON structure is stable and suitable for programmatic parsing
4. **Metadata**: JSON includes full metadata (resolved, pagination, rate limits) that isn't shown in tables

When writing scripts or integrations, always use `--json` for reliable, complete output.

## TypeScript Type Definitions

For TypeScript users, here are type definitions for the CLI output structure:

```typescript
// Standard response wrapper
interface CLIResponse<T = unknown> {
  ok: boolean;
  command: string;
  data?: T;
  error?: CLIError;
  meta: CLIMeta;
}

// Error object (when ok = false)
interface CLIError {
  type: 'api_error' | 'usage_error' | 'validation_error' | 'network_error';
  message: string;
  statusCode?: number;
  details?: Record<string, unknown>;
}

// Metadata object
interface CLIMeta {
  durationMs: number;
  resolved: ResolvedMetadata;
  pagination: PaginationMetadata | null;
  rateLimit: RateLimitInfo;
}

// Resolved metadata (structure varies by command)
interface ResolvedMetadata {
  person?: EntityResolution;
  company?: EntityResolution;
  opportunity?: EntityResolution;
  list?: ListResolution;
  savedView?: SavedViewResolution;
  fieldSelection?: FieldSelection;
  expand?: string[];
}

interface EntityResolution {
  input: string;
  personId?: number;
  companyId?: number;
  opportunityId?: number;
  source: 'id' | 'url' | 'email' | 'name' | 'domain';
  canonicalUrl?: string;
}

interface ListResolution {
  input: string;
  listId: number;
  source: 'id' | 'url' | 'name';
}

interface SavedViewResolution {
  input: string;
  savedViewId: number;
  name: string;
}

interface FieldSelection {
  fieldIds?: string[];
  fieldTypes?: string[];
}

// Pagination (key matches data collection name)
type PaginationMetadata = {
  [collectionName: string]: {
    nextCursor: string | null;
    prevCursor: string | null;
  };
};

// Rate limit info
interface RateLimitInfo {
  limit: number;
  remaining: number;
  reset: number;
}

// Example usage:
interface PersonGetResponse extends CLIResponse<{ person: Person }> {
  data: {
    person: Person;
  };
}

interface PersonListResponse extends CLIResponse<{ persons: Person[] }> {
  data: {
    persons: Person[];
  };
}
```

## Query Command Output

The `xaffinity query` command uses a different output format optimized for complex queries with includes and aggregations:

```json
{
  "data": [
    {"id": 1, "firstName": "Alice", "lastName": "Smith"},
    {"id": 2, "firstName": "Bob", "lastName": "Jones"}
  ],
  "included": {
    "companies": [
      {"id": 100, "name": "Acme Corp"},
      {"id": 101, "name": "TechCo"}
    ]
  },
  "meta": {
    "executionTime": 2.34,
    "recordsFetched": 2,
    "apiCalls": 3
  },
  "pagination": {
    "nextCursor": "eyJpZCI6Mn0="
  }
}
```

### Query Output Fields

- **data** (array): Query results as an array of records
- **included** (object, optional): Related entities fetched via `include`, keyed by entity type
- **meta** (object, optional): Execution metadata (requires `--include-meta`)
- **pagination** (object, optional): Pagination cursors for continuing the query

Note: Query output does not include `ok`, `command`, or `resolved` fields. Use `--include-meta` to include execution metadata.

## Related Documentation

- [CLI Commands Reference](cli/commands.md) - Complete command documentation
- [CLI Scripting Guide](cli/scripting.md) - Working with JSON output and pagination
- [CSV Export Guide](guides/csv-export.md) - Exporting data to CSV files
- [Query Language Reference](reference/query-language.md) - Complete query syntax
