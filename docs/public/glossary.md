# Glossary

## Entity Types

- **Person**: A contact in Affinity (individual). Has properties like name, email addresses, phone numbers.
- **Company**: An organization in Affinity. Has properties like name, domain(s), and associated persons.
- **Opportunity**: A deal or pipeline item. Always belongs to a specific list.
- **List**: A collection that can contain persons, companies, or opportunities. Lists have custom fields and workflow configurations.
- **List Entry**: A record in a list linking an entity (person, company, or opportunity) to that list with field values.

## API Generations (V1 vs V2)

- **V1 API**: Legacy Affinity endpoints at `https://api.affinity.co`. Used for writes and some read operations.
- **V2 API**: Newer Affinity endpoints at `https://api.affinity.co/v2`. Used for most reads with better performance.

The SDK automatically routes requests to the appropriate API version.

## V2 API Version

V2 has dated versions (for example `2024-01-01`). In the Affinity dashboard, your API key has a "Default API Version" setting that selects the V2 version used for your requests.

## Beta Endpoints

Some V2 endpoints are opt-in and require `enable_beta_endpoints=True` in the SDK. None currently do: company and person merges were beta but became generally available in 2026 and no longer need it.

## Typed IDs

The SDK uses typed ID classes to reduce accidental mixups:

- `PersonId` - Person identifier
- `CompanyId` - Company identifier
- `OpportunityId` - Opportunity identifier
- `ListId` - List identifier
- `ListEntryId` - List entry identifier
- `FieldId` - Field definition identifier
- `FieldValueId` - Field value identifier
- `NoteId` - Note identifier
- `InteractionId` - Interaction identifier
- `FileId` - File attachment identifier

## Fields and Field Values

- **Field (metadata)**: A custom field definition on a list or entity type. Has a name, type, and configuration.
- **Field Value**: The actual data stored in a field for a specific entity or list entry.
- **`fields.requested`**: Boolean indicating whether field data was requested and returned by the API. If `False`, fields were not fetched.

## Interactions

Records of communication or meetings with entities:

- `email` - Email correspondence
- `meeting` - Calendar meetings
- `call` - Phone calls
- `chat_message` - Chat/text messages
- `in_person` - In-person meetings

## Relationship Strength

A score (0-5) indicating how strong the relationship is between your organization and a person/company, based on interaction frequency and recency.

**Note:** The SDK uses `client.relationships` service, while the CLI uses `xaffinity relationship-strength` command (with hyphen).

## Eventual Consistency (V1→V2)

After creating or updating an entity via V1, there's a brief delay (typically 100-500ms) before the change appears in V2 reads. See [Errors & retries](./guides/errors-and-retries.md#v1v2-eventual-consistency).

## Session Caching (CLI)

A CLI feature that shares metadata (field definitions, list configurations) across multiple commands in a pipeline, reducing redundant API calls.

## PageIterator

The object returned by `all()` and `iter()` methods. Supports:

- Direct iteration (`for item in iterator`)
- `pages()` method for page-by-page access with progress callbacks
- `all()` method to collect all items into a list

## Policies

Client-level configuration that controls SDK behavior:

- **WritePolicy**: `ALLOW` (default) or `DENY` to prevent write operations
- **ExternalHookPolicy**: Controls how external URLs appear in hook events (`REDACT`, `SUPPRESS`, `EMIT_UNSAFE`)

## Exception Types

The SDK raises specific exception types for different error conditions:

- **AffinityError**: Base class for all SDK errors
- **AuthenticationError**: Invalid or missing API key (401)
- **PermissionError**: Insufficient permissions for the operation (403)
- **NotFoundError**: Requested resource does not exist (404)
- **RateLimitError**: API rate limit exceeded (429); includes `retry_after` attribute
- **ValidationError**: Invalid input data or request parameters
- **BetaEndpointDisabledError**: Beta endpoint used without `enable_beta_endpoints=True`
- **VersionCompatibilityError**: API version mismatch between client and server

See [Errors & retries](./guides/errors-and-retries.md) for handling strategies.
