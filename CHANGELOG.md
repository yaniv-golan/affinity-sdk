# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.10.0] - 2026-04-05

### Highlights

You can now resolve field names to `FieldMetadata` (including `FieldId`) using `FieldResolver.find_field()`, which is what you need for write operations like `update_field_value()`. Unlike `get()` which silently picks the first match for ambiguous names, `find_field()` raises `AmbiguousFieldError` to prevent accidentally updating the wrong field. Skill descriptions across all three plugins have been rewritten for clearer triggering boundaries when multiple plugins are installed.

### Added
- `FieldResolver.find_field(name)` method — resolves field names to `FieldMetadata` for write operations
- `AmbiguousFieldError` exception — raised when `find_field()` encounters ambiguous field names; includes candidate IDs and disambiguation hints

### Changed
- **Plugin skills:** Clarified triggering boundaries across MCP, CLI, and SDK plugins — MCP workflows skill is now explicitly primary when MCP tools are available, CLI skill defers to MCP for general CRM tasks
- **Plugin skills:** Pipeline-history skill rewritten with MCP tool examples (was raw bash), query-language skill no longer depends on workflows-guide resource
- **Plugin skills:** SDK skill now documents `FieldResolver.find_field()` patterns for read vs. write field resolution

## [1.9.0] - 2026-03-22

### Highlights

Accessing a company or person that was merged now raises a dedicated `MergedEntityError` (or `CompanyMergedError`/`PersonMergedError`) instead of a generic `ValidationError`. The exception provides structured `source_id` and `target_id` attributes, so you can programmatically follow merges without regex-parsing error messages. The CLI also surfaces this as a distinct `entity_merged` error type with structured JSON details.

### Added
- `MergedEntityError`, `CompanyMergedError`, `PersonMergedError` exception classes — raised on 422 responses indicating an entity was merged into another
- CLI: `entity_merged` error type with `sourceId`, `targetId`, `entityType` in JSON error details (exit code 4)

## [1.8.2] - 2026-03-19

### Highlights

Added a comparison guide documenting how this project differs from Affinity's official MCP Server (beta), covering API coverage, tool architecture, deployment options, and when to use each.

### Added
- Comparison guide: [Affinity SDK vs. Official MCP](docs/public/guides/affinity-official-mcp-comparison.md)
- README section linking to Affinity's official MCP and the comparison guide

## [1.8.1] - 2026-03-16

### Highlights

`InteractionDirection`, `InteractionLoggingType`, and `ListRole` are now available from `affinity.types` — previously they could only be imported from the internal `affinity.models.types` path. The `Interaction` model now also captures the `logging_type` field from the API instead of silently dropping it.

### Fixed
- Re-export `InteractionDirection`, `InteractionLoggingType`, and `ListRole` from `affinity.types` — these were the only public enums missing from the stable import path
- Add `logging_type` field to `Interaction` model — the API returns this field but it was silently discarded

## [1.8.0] - 2026-03-10

### Highlights

`entry field --get` now returns resolved person and company objects (with `firstName`, `lastName`, `primaryEmailAddress`, etc.) instead of raw integer IDs. This matches the behavior of `list export` and provides a consistent, AI-agent-friendly experience. Under the hood, the command switched from V1 to V2 API.

### CLI Plugin 1.6.1

#### Added
- Skill: documented file commands (`company files`, `person files`, `opportunity files`) with `ls`, `download`, `read`, `upload` subcommands — prevents unnecessary fallback to raw v1 API calls
- Skill: added file commands to Quick Reference table

### Changed
- **Breaking:** `entry field --get` output for person/company reference fields now returns resolved objects instead of raw integer IDs. Scripts that parse the integer ID should update to read from the resolved object.
- `entry field --get` now uses the V2 API (`/v2/lists/{listId}/list-entries/{entryId}/fields`) instead of V1 `field_values.list()`

### Fixed
- `ListEntryService.get_field_values()` (sync and async) now correctly parses V2 API list response format — previously returned empty results due to dict/list type mismatch
- Added `types` to `REPEATABLE_QUERY_PARAMS` so the V2 fields endpoint `types` filter parameter is correctly encoded
- `FieldValues._coerce_from_api` no longer double-wraps dicts that are already in `{requested, data}` format — fixes `fields.*` returning null after round-trip serialization
- Query executor now warns (via `ctx.warnings`) when field metadata fetch fails instead of silently returning null for all `fields.*` values
- Query executor now warns when field name resolution fails in `where` clauses instead of silently skipping resolution
- Multi-parent fetch path now normalizes list entry fields — `fields.*` in `select` works correctly when query spans multiple parent lists

## [1.7.3] - 2026-03-08

### Highlights

`UserId` is now a subtype of `PersonId`, reflecting that workspace users are internal persons in Affinity's data model. Code that receives a `UserId` (e.g., `Note.creator_id`) can now pass it directly to any API accepting `PersonId` — no casting required.

### Changed
- `UserId` now extends `PersonId` instead of `IntId` — `isinstance(UserId(1), PersonId)` is now `True`. This is an intentional runtime behavior change: workspace users are internal persons in Affinity's data model. Code that used `isinstance` to distinguish users from persons should use `person.type == PersonType.INTERNAL` instead.
- Simplified internal casts that previously converted `UserId` → `int` → `PersonId`

### Documentation
- data-model.md: clarified that user IDs and person IDs share the same ID space

## [1.7.2] - 2026-03-03

### Highlights

The xaffinity CLI plugin's SessionStart hook no longer runs `pip install` on every container start. Installation is deferred to first actual use via a self-installing wrapper, cutting session startup from ~30s to <1s in ephemeral environments like Cowork. Skill descriptions now display correctly in the Claude Code UI. Also documents two Affinity API limitations discovered via support: interaction entity association and enriched field constraints.

### CLI Plugin 1.6.0

#### Added
- **Lazy install**: SessionStart hook now drops a lightweight self-installing wrapper instead of running `pip install` unconditionally. The wrapper defers installation to first actual xaffinity use, with mkdir-based locking for concurrent safety, a marker file for install failure detection, and a 45s wait timeout for concurrent invocations.
- **PreToolUse hook**: Install-failure detection via `$HOME/.xaffinity-install-status` marker — blocks commands with a clear error when pip install failed, instead of cryptic failures.
- **PreToolUse hook**: Lazy session cache start — `xaffinity session start` is triggered on first xaffinity command instead of at session start, with `CLAUDE_ENV_FILE` persistence.

#### Changed
- SessionStart timeout reduced from 60s to 10s (hook is now lightweight)
- PreToolUse Bash timeout increased from 10s to 60s (accommodates pip install via wrapper on first use)
- SessionStart status message updated to "Preparing Affinity CLI environment..."

#### Fixed
- Skill YAML frontmatter: replaced multi-line `>` scalar with single-line description (Claude Code couldn't parse folded scalars, showing `>` instead of the description)
- Skill: updated session cache documentation to reflect lazy initialization

#### Documentation
- Skill: documented that interactions cannot be associated with companies/opportunities via API (UI's "Also add to" feature has no API equivalent)
- Skill: documented that "Current Organization" is read-only via API and "Current Job Title" requires a separate `field update` after person creation

### SDK Plugin 1.5.5

#### Fixed
- Skill YAML frontmatter: replaced multi-line `>` scalar with single-line description (same parsing fix as CLI plugin)

### Documentation
- Data model: documented interaction entity association limitation and enriched field constraints (Current Organization, Current Job Title)

## [1.7.1] - 2026-03-01

### Highlights

Plugin skills now guide agents to consolidate multi-source CRM queries into single scripts instead of running separate commands that dump raw JSON into the conversation. The CLI plugin also auto-starts session caching at session begin, so metadata is shared across all commands without manual setup.

### CLI Plugin 1.5.6

#### Added
- Skill: "Multi-Source Tasks" section guiding agents to write consolidated bash scripts (with session cache + jq) instead of running separate CLI commands that dump raw JSON into context
- Skill: "Extract only what you need" guidance with jq examples for single-command output filtering
- Skill: session cache fallback instruction for environments without automatic setup
- SessionStart hook: automatic session cache initialization (`xaffinity session start`) with `CLAUDE_ENV_FILE` persistence for Cowork

### SDK Plugin 1.5.4

#### Added
- Skill: "Multi-Source Tasks: Output Only the Summary" section guiding agents to print concise summaries instead of raw `model_dump_json()` output when combining multiple data sources

## [1.7.0] - 2026-02-21

### Highlights

New `field history-bulk` command fetches field change history across an entire list in one shot — useful for pipeline stage analysis, funnel conversion, and time-in-stage metrics. Also fixes `--dotenv` to search upward for `.env` files (matching standard dotenv behavior) and hardens the PreToolUse hook with a three-tier API key check.

### Added
- **CLI**: `field history-bulk` command for batch field change history across lists. Supports `--list-id` (with `--all`/`--max-results` bounding), `--list-entry-ids` for specific entries, `--action-type` filtering, `--dry-run` for API cost estimation, and concurrent fetching via `XAFFINITY_CONCURRENCY` env var. Partial failures are reported as warnings without blocking successful entries.
- **CLI**: `--list` alias for `--list-id` on `field ls` and `field history-bulk` commands (LLM-friendly shorthand).
- **MCP Plugin**: Pipeline history analysis skill (`pipeline-history`) with 5-step workflow: identify status field, export current state, dry-run estimate, fetch history, analyze transitions.

### Fixed
- **CLI**: `--dotenv` now searches upward for `.env` files using `find_dotenv(usecwd=True)`, matching standard python-dotenv behavior. Previously only checked the current working directory, which failed in Cowork VM sessions where the working directory differs from the `.env` location.
- **CLI Plugin**: PreToolUse hook (`pre-xaffinity.sh`) now uses three-tier API key detection: (1) `AFFINITY_API_KEY` env var, (2) `--dotenv` check-key, (3) plain check-key (config.toml). Previously only checked the env var, blocking commands when the key was configured via `.env` or config file.

### Documentation
- Added `list export --json` output structure documentation to data model resource (`field` key details, `--field`/`--field-type` requirement).
- Added `field history-bulk` to MCP command registry for LLM discoverability.
- Added field change history section to data model resource.

### CLI Plugin 1.5.5

#### Fixed
- PreToolUse hook: three-tier API key detection (env var → dotenv → config.toml)

### MCP Plugin 1.19.0

#### Added
- Pipeline history analysis skill for deal stage transition workflows

## [1.6.2] - 2026-02-17

### Highlights

Update notifications now work reliably. Previously, having multiple Python environments (e.g., dev virtualenv + system install) could silently poison the update cache, and the first `config update-check` run always said "never checked" instead of just checking.

### Fixed
- **CLI**: `config update-check` now checks PyPI inline when cache is missing or stale, instead of showing "never checked"
- **CLI**: Background update worker now receives version from spawning process, fixing version mismatch when multiple Python environments share a cache
- **CLI**: Smarter cache invalidation — upgrading no longer discards the cache; it adapts it

## [1.6.1] - 2026-02-16

### Highlights

`config check-key --env-file <path>` now correctly reads the specified file instead of only looking in the current directory. Fixes key discovery in Cowork VM sessions where the `.env` is on a mounted path.

### Fixed
- **CLI**: `config check-key` ignored `--env-file` flag — always checked `CWD/.env` instead of the user-provided path

## [1.6.0] - 2026-02-16

### Highlights

New `--include-me` flag for `interaction create` auto-includes your person ID. Also fixes field writes for person/company multi-value fields -- entity IDs are now wrapped correctly for the V2 API, and `--append` properly merges instead of replacing.

### Added
- **CLI**: `interaction create --include-me` flag to auto-include current user's person ID via whoami
- **CLI**: Enhanced validation error hint for interaction person_ids constraint (internal/external requirement)

### Changed
- Added interaction create guidance to CLI plugin skill
- Added interaction create examples to MCP data model resource

### Fixed
- **CLI**: Person/company field writes (`--set`, `--append`) now correctly wrap entity IDs in `{"id": <int>}` for the V2 API. Previously sent raw integers, causing `validation_error: value at /value/data is not null`.
- **CLI**: `--append` on `person-multi`/`company-multi` fields now merges with existing values instead of silently replacing them (same fix already existed for `dropdown-multi`).
- **CLI**: Repeated `--append` on the same multi-value field (e.g., `--append Tags A --append Tags B`) now aggregates into a single write, preventing the second write from overwriting the first.
- **CLI**: V1 API `allows_multiple` promotion now applies to `person` and `company` field types (previously only `dropdown` was promoted to its `-multi` variant).
- MCP command registry: corrected `--participants` to `--person-id` for `interaction create`

## Plugin Releases — 2026-02-15

Plugin versions are now independent from the SDK version (see `VERSIONING.md`).

### CLI Plugin 1.5.3

#### Changed
- Plugin versions are now independent from SDK version

#### Fixed
- **Security**: SessionStart hook no longer exports `AFFINITY_API_KEY` to the environment. The key stays in `.env` and is read per-command via `--dotenv`, preventing the LLM from accessing it via `env` or `echo $AFFINITY_API_KEY`.

### SDK Plugin 1.5.3

#### Changed
- Rewrote `affinity-python-sdk` skill description following Anthropic skills guide formula

### MCP Plugin 1.18.1

#### Changed
- Rewrote `query-language` SKILL.md (861→293 lines) with progressive disclosure; extracted detail to 4 reference files
- Updated `affinity-mcp-workflows` skill description following guide formula

## [1.5.2] - 2026-02-14

### Highlights

Plugin improvements for Claude Code and Cowork: automatic environment setup via SessionStart hook, API key protection via `.env` read guard, and clearer plugin names in marketplace listings.

### Added
- CLI Plugin: SessionStart hook (`session-setup.sh`) for Cowork bootstrap — installs xaffinity, sets PATH, loads API key from `.env`
- CLI Plugin: PreToolUse/Read guard (`guard-env-read.sh`) blocks reading `.env` files to prevent API key exposure in conversation
- CLI Plugin: Query command reference (`references/query-guide.md`)
- MCP Plugin: Query language reference files (`references/filter-operators.md`, `quantifiers.md`, `include-expand.md`, `output-formats.md`)

### Changed
- Marketplace: Renamed plugins from generic "sdk"/"cli"/"mcp" to "Affinity CRM SDK (unofficial)", "Affinity CRM CLI (xaffinity, unofficial)", "Affinity CRM MCP (unofficial)" for clarity in plugin lists.
- CLI Plugin: Rewrote skill description following Anthropic skills guide formula
- MCP Plugin: Rewrote `query-language` SKILL.md (861→293 lines) with progressive disclosure; extracted detail to `references/`
- MCP Plugin: Updated `affinity-mcp-workflows` skill description following guide formula
- SDK Plugin: Updated `affinity-python-sdk` skill description following guide formula

### Removed
- CLI Plugin: Removed `/affinity-help` command (redundant — CLI skill auto-triggers on relevant prompts)

### Fixed
- CLI Plugin: `.env` parsing now handles quoted values and CRLF line endings
- CLI Plugin: SessionStart hook writes to `CLAUDE_ENV_FILE` are idempotent (no duplicate lines on re-run)

## [1.5.1] - 2026-02-12

### Highlights

Fixes writing to multi-select dropdown fields. `--set` and `--append` now correctly handle the array format required by the API, and `--append` properly merges with existing selections instead of replacing them.

### Fixed
- CLI: `--set` and `--append` now work correctly for dropdown-multi fields when V1 API returns `value_type="dropdown"` with `allows_multiple=True`. Previously, `resolve_dropdown_value` only checked `value_type` (not `allows_multiple`) to determine the payload format, sending `{"dropdownOptionId": ID}` instead of `[{"dropdownOptionId": ID}]`.
- CLI: `--append` for dropdown-multi fields now merges with existing values instead of replacing them. The V2 API replaces the entire option array on POST, so `--append` now reads existing selections, adds the new option (deduplicating), and sends the combined array.

## [1.5.0] - 2026-02-11

### Highlights

Interaction queries are now much more robust. Date ranges are validated up front, ranges over 1 year are auto-chunked seamlessly, and mixing naive/timezone-aware datetimes is caught with a clear error. **Breaking:** `InteractionService.list()` now requires `start_time`, `end_time`, and an entity ID -- callers already getting 422 errors will now get a clear SDK-level message instead.

### Changed
- **Breaking:** `InteractionService.list()` and `AsyncInteractionService.list()` now require `start_time`, `end_time`, and at least one entity ID (`person_id`, `company_id`, or `opportunity_id`). Previously these were optional, but the API always rejected calls without them (422). Callers that were passing `None` for these parameters were already getting API errors; this change surfaces the requirement at the SDK level with clear error messages.
- `InteractionService.list()` now validates date ranges: `start_time` must be before `end_time`, and the range must not exceed 365 days.

### Added
- SDK: `InteractionService.iter()` and `AsyncInteractionService.iter()` now automatically chunk date ranges exceeding 365 days. Large ranges are split into <=365-day chunks with synthetic cursors bridging them, making iteration seamless.
- SDK: `iter()` defaults `end_time` to `datetime.now(timezone.utc)` when not provided, so callers only need to specify `start_time`.
- SDK: Timezone consistency validation — mixing naive and timezone-aware datetimes raises `ValueError` with guidance instead of a raw `TypeError`.
- SDK: `_chunk_date_range()` utility for splitting date ranges into API-compatible chunks.
- CLI: Query executor now properly fetches interactions for `include` and `expand` operations. Previously, these paths called `interactions.list()` without required `type`/date parameters and silently returned empty results.

### Fixed
- SDK: Fixed falsy truthiness bugs in `InteractionService.list()` where `PersonId(0)`, `CompanyId(0)`, `page_size=0`, and empty `page_token=""` were incorrectly dropped. Changed `if x:` to `if x is not None:` for all optional parameters.

## [1.4.1] - 2026-02-11

### Highlights

CLI errors from invalid commands or options now produce proper JSON error envelopes when `--json` is active, fixing broken downstream JSON parsers.

### Fixed
- CLI: Click-level errors (unknown commands, invalid options) now emit a proper JSON error envelope (`{"ok": false, "error": {...}}`) when `--json` or `--output json` is active. Previously, stdout was empty and only plain text went to stderr, breaking downstream JSON parsers.
- CLI: `normalize_exception()` now handles `click.UsageError` (→ `usage_error`, exit code 2) and `click.ClickException` (→ `error`, exit code from exception) instead of misclassifying them as `internal_error`.

## [1.4.0] - 2026-02-10

### Highlights

`FieldResolver` now resolves all value types to human-readable text -- not just dropdowns, but also persons, companies, locations, and interactions. Dropdown fields return rich `DropdownOption` objects with `.text`, `.rank`, and `.color`. **Breaking:** `get_value()` returns `DropdownOption` instead of raw IDs, and `resolve_dropdowns` parameter is replaced by `ResolveMode`.

### Changed
- **Breaking:** `FieldValues.get_value()` now returns `DropdownOption` objects for dropdown/ranked-dropdown fields instead of raw `int` IDs. The `DropdownOption` has `.id`, `.text`, `.rank`, and `.color` attributes. This applies to all dropdown value types (`dropdown`, `ranked-dropdown`, `dropdown-multi`).
- **Breaking:** Removed `FieldType.LIST_SPECIFIC`. Use `FieldType.LIST` instead. The V2 API uses `"list"` uniformly; `"list-specific"` was never a valid V2 value.
- **Breaking:** `FieldResolver.get()` parameter `resolve_dropdowns: bool` replaced with `resolve: ResolveMode` (`ResolveMode.RAW` or `ResolveMode.TEXT`). `ResolveMode.TEXT` resolves dropdowns, persons, companies, and locations to human-readable strings.

### Added
- SDK: `ResolveMode` enum (`RAW`, `TEXT`) for controlling `FieldResolver` value resolution.
- SDK: `ResolveMode.TEXT` now resolves person, company, location, and interaction fields to human-readable strings (not just dropdowns).
- SDK: `FieldResolver` supports source-qualified field names (`"dealroom:Description"`) for disambiguating enrichment fields with the same display name. Ambiguous bare names emit a one-time warning at access time.
- SDK: `validate_entity_field_types()` raises `ValueError` when `FieldType.LIST` is passed to company/person endpoints (which only accept `ENRICHED`, `GLOBAL`, `RELATIONSHIP_INTELLIGENCE`).
- SDK: `DropdownOption.color` now accepts both `int` (V1) and `str` (V2) values.

### Fixed
- SDK: `FieldValues._extract_value()` now correctly recurses through `{"data": {...}}` envelopes for dropdown values, fixing ranked-dropdown and dropdown-multi extraction that previously returned the raw envelope dict.
- SDK: Dropdown text resolution now works without V1 field metadata. Previously, `FieldResolver` built a lookup table from `FieldMetadata.dropdown_options` (V1-only; always empty on V2). Now text is read directly from the `DropdownOption` object extracted from the field value.
- SDK: `async_check_unreplied()` replaced broken no-type `iter()` call with `asyncio.gather()` per-type pattern for parallel interaction fetching.
- CLI: `_extract_person_display_name` and `_extract_person_name` now delegate to shared `resolve_person()` utility.

### Documentation
- Added `FieldResolver` usage example to README "Working with Lists" section.
- Added `FieldResolver` mention to getting-started guide field gotchas, linking to performance guide.
- Replaced raw field count with `FieldResolver` usage in `examples/basic_usage.py`.
- Updated field-types-and-values guide: dropdown types now document `DropdownOption` return type.
- Added "List Entry Field Access" section to performance guide explaining `entry.entity.fields` delegation.

## [1.3.2] - 2026-02-08

### Highlights

Fixes writing to `dropdown-multi` fields via `--set` and `--append`. Previously all dropdown-multi writes failed with a type mismatch error.

### Fixed
- CLI: `--set` and `--append` now work for `dropdown-multi` fields. Previously, `resolve_dropdown_value()` only handled `dropdown` and `ranked-dropdown`, causing all dropdown-multi writes to fail with "Field value type should be dropdown-multi" errors. The fix resolves option text/IDs and wraps them in the array format required by the V2 API.

## [1.3.1] - 2026-02-06

### Highlights

`FieldResolver` now works correctly with list entry objects -- it auto-delegates to the inner entity when fields were fetched on the entity rather than the list entry directly.

### Fixed
- SDK: `FieldResolver.get()` and `get_by_id()` now automatically delegate to the inner entity when called with a `ListEntryWithEntity` whose fields were not directly requested. Previously warned and returned `None` even when `entry.entity.fields` were populated.
- SDK: `FieldResolver` duplicate field name warning now includes enrichment source (e.g., "dealroom" vs "affinity-data") instead of raw IDs, making enrichment provider collisions immediately clear.

## [1.3.0] - 2026-02-06

### Highlights

New `list_batch()` method on `AsyncEntityFileService` for fetching files across multiple entities concurrently with auto-pagination.

### Added
- SDK: `list_batch()` on `AsyncEntityFileService` for fetching files across multiple entities concurrently with auto-pagination. Accepts `person_ids`, `company_ids`, or `opportunity_ids` with `max_concurrent` and `on_error` ("raise"/"skip") parameters.

## [1.2.0] - 2026-02-06

### Highlights

New async batch methods for files and reminders: `batch_get()` on `AsyncEntityFileService` and `list_batch()` on `AsyncReminderService` for concurrent multi-entity operations. Also fixes falsy ID bugs across reminder and file services.

### Added
- SDK: `batch_get()` on `AsyncEntityFileService` for fetching file metadata concurrently with controlled concurrency. Supports `max_concurrent` and `on_error` ("raise"/"skip") parameters.
- SDK: `list_batch()` on `AsyncReminderService` for fetching reminders across multiple entities concurrently with auto-pagination. Accepts `person_ids`, `company_ids`, or `opportunity_ids` with common filters.

### Fixed
- SDK: Fixed truthiness bugs in `ReminderService.list()`, `AsyncReminderService.list()`, `EntityFileService.list()`, and `AsyncEntityFileService.list()` where `PersonId(0)`, `CompanyId(0)`, and other falsy values were silently dropped from query parameters. Changed all `if <param>:` guards to `if <param> is not None:`.
- SDK: Fixed incorrect `ReminderStatus` docstrings that referenced non-existent "SNOOZED" and "COMPLETE" values. The actual enum values are `COMPLETED`, `ACTIVE`, and `OVERDUE`.

## [1.1.0] - 2026-02-05

### Highlights

Major convenience release: `FieldResolver` for looking up field values by name instead of ID, `batch_get()` for concurrent entity fetching, `read_only()` factory for safe clients, and `get_first()` for quick single-entity lookups. Working with Affinity field data is now significantly easier.

### Added
- SDK: `CompanyService.get_many(company_ids)` convenience method for batch fetching multiple companies in a single API call. Alias for `list(ids=[...])` with better discoverability.
- SDK: `FieldValues.get(field_id)` convenience method for safer access to field values by ID. Returns the field value dict or `None` if not found.
- SDK: `Affinity.read_only()` and `Affinity.read_only_from_env()` factory methods (and async variants) for creating clients that block all write operations. Useful for read-only scripts and dashboards.
- SDK: `get_first()` convenience method on `CompanyService`, `PersonService`, `OpportunityService`, `ListService`, and `ListEntryService` (sync + async). Returns the first matching entity or `None`.
- SDK: `batch_get()` on `AsyncCompanyService`, `AsyncPersonService`, and `AsyncOpportunityService` for fetching multiple entities with controlled concurrency. Supports `max_concurrent` and `on_error` ("raise"/"skip") parameters.
- SDK: `FieldValues.get_value(field_id)` for extracting the unwrapped field value (e.g., returns `"Active"` instead of `{"data": "Active"}`). Handles text, dropdown, multi-value, and location fields.
- SDK: `FieldResolver` helper class for looking up field values by name instead of ID. Supports case-insensitive matching, batch extraction via `get_many()`, and dropdown option resolution.
- Docs: "Field Lookup Patterns" section in performance guide documenting `FieldResolver` usage, low-level access, and field metadata sources.

### Fixed
- SDK: `FieldService.list()` now caches results for 5 minutes (matching V2 field endpoints). Previously only V2 endpoints (`companies.get_fields()`, `persons.get_fields()`) were cached.
- SDK: `FieldService.list(list_id=ListId(0))` no longer skips the `list_id` parameter. The falsy ID guard (`if list_id:`) was changed to `if list_id is not None:`.

## [1.0.3] - 2026-02-03

### Highlights

Fixes MCP timeout failures during large query `include` operations by emitting incremental progress every 10 records instead of only at start/end.

### Fixed
- CLI: Query `include` operations now emit incremental progress every 10 records during N+1 API calls. Previously, progress was only emitted at the start and end of include steps, causing MCP timeout to expire during long-running N+1 operations (e.g., 100 records with `include: ["persons"]`). The fix changes from `asyncio.gather()` to `asyncio.as_completed()` for incremental progress emission.

## [1.0.2] - 2026-02-03

### Highlights

Fixes descending sort for string fields in queries (dates, names were silently returning ascending order) and corrects API call estimates for sorted queries that could cause premature timeouts.

### Fixed
- CLI: Query `orderBy` with descending direction now works correctly for string values (dates, names, etc.). Previously, descending sort on non-numeric fields silently returned results in ascending order because string values cannot be negated. The fix uses a comparison-inverting wrapper class for proper descending sort.
- CLI: Query dry run now correctly estimates API calls when `orderBy` is present. Previously, `estimatedApiCalls` used the `limit` value even though sorting requires fetching all records first. A query with `limit: 200` and `orderBy` on a 9000-record list showed 4 API calls instead of ~90. This caused dynamic timeout calculations to be too short.

## [1.0.1] - 2026-02-01

### Highlights

Fixes unreplied email/chat detection (`--check-unreplied` and `expand: ["unreplied"]`) which was silently failing, and adds required `type` parameter validation to `InteractionService` with clear error messages instead of cryptic 422s.

### Fixed
- SDK: `InteractionService.list()` and `iter()` now validate that `type` parameter is required, raising `ValueError` with clear guidance instead of failing with cryptic API 422 errors. The Affinity V1 API has always required this parameter.
- CLI: `--check-unreplied` and `expand: ["unreplied"]` now work correctly. Previously, all unreplied checks silently failed (returning null) due to missing `type` parameter in interaction API calls.
- CLI: Query `dryRun` JSON output now includes a warning when estimated API calls exceed 50, helping LLMs notice expensive operations.

## [1.0.0] - 2026-01-31

**First stable release.** The SDK and CLI APIs are now considered stable. Breaking changes will follow semantic versioning (major version bumps).

### Added
- CLI: Auto-update notifications. The CLI now checks PyPI daily (in background) and displays a notification when a new version is available. Notifications are suppressed in non-interactive environments (`--quiet`, `--output json`, CI, no TTY). Disable with `XAFFINITY_NO_UPDATE_CHECK=1` or `update_check = false` in config.
- CLI: `xaffinity config update-check --background` flag for non-blocking update checks. Used by MCP server to trigger background updates without waiting for results.

### Fixed
- MCP: Add `AFFINITY_API_KEY` to environment variable allowlist. API keys configured in Claude Desktop's MCP settings were being silently dropped, requiring manual config file creation as a workaround.
- MCP: Detect CLI in macOS Python framework path (`/Library/Frameworks/Python.framework/Versions/*/bin/`). Users who installed via `pip install` with python.org Python were getting "xaffinity: command not found" errors.

## [0.16.1] - 2026-01-27

### Added
- Docs: "Verify Installation" section in CLI documentation with step-by-step health check instructions (`xaffinity --version`, `AFFINITY_API_KEY=... xaffinity whoami`).

## [0.16.0] - 2026-01-26

### Changed (Breaking)
- SDK: `Person.interactions` and `Company.interactions` attribute type changed from `dict[str, Any]` to typed `Interactions` model. Migration: change dict access (`person.interactions["last_event"]["person_ids"]`) to attribute access (`person.interactions.last_event.person_ids`). The `Interactions` model provides typed access to `first_email`, `last_email`, `first_event`, `last_event`, `next_event`, `last_chat_message`, and `last_interaction` fields, each containing an `InteractionEvent` with `date` and `person_ids` attributes.

### Fixed
- SDK: `PersonService.get()` field values are now properly validated as `FieldValue` models.

## [0.15.1] - 2026-01-25

### Fixed
- CLI: `query --output json` now supports cursor-based pagination when output is truncated. Previously, JSON truncation happened at the MCP layer after CLI execution, so no cursor was emitted. Now JSON truncation happens in the CLI layer (same as other formats), enabling proper resumption via `--cursor`.

## [0.15.0] - 2026-01-23

### Added
- CLI: `company files read`, `person files read`, `opportunity files read` commands to read file content with chunking support. Returns base64-encoded content with metadata (`size`, `offset`, `length`, `hasMore`, `nextOffset`). Use `--offset` and `--limit` to fetch large files in chunks. Default chunk size is 1MB.

### Fixed
- CLI: `--env-file` now implicitly enables dotenv loading when an explicit file path is provided (not the default `.env`). Previously, using `--env-file .sandbox.env` without `--dotenv` would silently ignore the file.
- CLI: Fixed dropdown field value resolution in `entry field --set` and `--append` commands. The V2 API requires `{"dropdownOptionId": ID}` format, but the CLI was sending raw values. Now dropdown text (e.g., "In Progress") or numeric IDs are properly resolved to the V2 API format.
- CLI: List field metadata now fetched from V1 API to include `dropdown_options`, which V2 API omits.

## 0.14.0 - 2026-01-22

### Added
- CLI: `company files ls`, `person files ls`, `opportunity files ls` commands to list files attached to entities without downloading them. Supports pagination (`--page-size`, `--cursor`, `--max-results`, `--all`), selector resolution (ID, URL, name, domain, email), and MCP safety limits.
- CLI: `file-url` command to get presigned download URLs for files. Returns URL valid for 60 seconds along with file metadata (name, size, contentType). Useful for programmatic access and MCP workflows.
- CLI: `files download --file-id` option for single-file downloads. Downloads one file directly without creating a manifest.
- SDK: `FilesService.get_download_url(file_id)` method to get presigned download URLs without downloading content. Returns `PresignedUrl` with URL, file metadata, and expiration info.
- SDK: `PresignedUrl` dataclass exported from `affinity` package with fields: `url`, `file_id`, `name`, `size`, `content_type`, `expires_in`, `expires_at`.

### Changed
- CLI: `files dump` renamed to `files download` for clarity.

### Fixed
- SDK: `CompanyService.get()` and `PersonService.get()` now automatically fall back to V1 API when V2 returns 404. This handles V1→V2 eventual consistency issues where a search (`company ls --query`, `person ls --query`) finds an entity via V1, but a subsequent `get` fails because V2 hasn't synced yet. The fallback is transparent and requires no code changes.

### Known Issues
- MCP: `get-file-url` tool returns valid presigned URLs, but Claude Desktop's WebFetch cannot access `userfiles.affinity.co` due to domain sandbox restrictions. This affects all Claude Desktop users - neither "Additional allowed domains" nor "All domains" settings work around this limitation ([#19087](https://github.com/anthropics/claude-code/issues/19087), [#11897](https://github.com/anthropics/claude-code/issues/11897)). Workaround: Use `files read` command (returns content inline), copy URL to browser, or use CLI directly with `files download --file-id`.

## 0.13.1 - 2026-01-21

### Fixed
- CLI: Query progress now emits `progress: 0` in `on_step_start` events, enabling mcp-bash timeout extension for slow first-page fetches

## 0.13.0 - 2026-01-21

### Changed (Breaking)
- SDK: `AffinityList.list_size` field removed. The V2 API returns incorrect values (often 0 for non-empty lists). Use `client.lists.get_size(list_id)` instead, which fetches from the V1 API and caches for 5 minutes.

### Added
- SDK: `ListService.get_size(list_id)` and `AsyncListService.get_size(list_id)` methods to get accurate list size from the V1 API with automatic caching (5 minutes). Use `force=True` to bypass cache when fresh data is critical.

## 0.12.0 - 2026-01-20

### Added
- CLI: `query --cursor` option for resumable pagination when responses are truncated (exceed `--max-output-bytes`):
  - **Streaming mode** (simple queries): O(1) resumption via stored Affinity API cursor - no re-fetching of previous pages
  - **Full-fetch mode** (queries with orderBy/aggregate): Results cached to disk, zero API calls on resume
  - Cursor emitted to stderr as NDJSON `{"type": "cursor", "cursor": "...", "mode": "..."}` for MCP extraction
  - CLI exits with code 100 when truncated (cursor available)
  - Cache auto-cleanup on startup: LRU eviction (500MB limit), 1-hour TTL
  - Validation: query hash, format mismatch, cache tampering all detected with clear errors

## 0.11.0 - 2026-01-20

### Added
- CLI: Full scan protection when running via MCP gateway. Commands with pagination (`list export`, `person ls`, `company ls`, etc.) now enforce limits: default 1000 records, max 10000 records. The `--all` flag is blocked with a clear error message guiding users to use explicit `--max-results` or cursor pagination instead.

### Changed
- CLI: `--csv` is now an alias for `--output csv` (consistent with `--json` being an alias for `--output json`)
- CLI: CSV sub-options (`--csv-bom`, `--csv-header`, `--csv-mode`) auto-enable CSV output when no format is specified
- CLI: Error messages for output flag conflicts no longer have trailing periods (e.g., `--csv and --json are mutually exclusive` instead of ending with `.`)

### Removed
- **Breaking**: CLI: `--pretty` flag removed from `query` command (use `| jq .` for pretty JSON output)

### Fixed
- CLI: Query engine now correctly computes `entityName` for Person list entries. Previously, `entityName` was `null` for persons because the API returns `firstName`/`lastName` instead of `name`. Now uses the same display name logic as `list export`.
- CLI: Table/CSV formatters now correctly detect Person entities with real API types (`external`/`internal`) or no type field. Previously, formatters only checked for `type="person"` which the API never actually returns, causing Person data to display as "object (N keys)" instead of "John Smith (id=123)".

### Performance
- CLI: Session cache now caches person resolution (by email/name) and company resolution (by domain/name), reducing API calls when running multiple commands in a session pipeline.
- CLI: Person and company field resolution now uses session cache, avoiding redundant field definition fetches.

## 0.10.0 - 2026-01-19

### Fixed
- SDK: Removed unsafe `asyncio.create_task()` in `AsyncAffinity.__del__` that could cause tasks to be garbage collected before completion. Users must now use context managers or call `close()` explicitly.
- SDK: Added thread-safe locking to `SimpleCache` for concurrent access.
- SDK: Fixed async client initialization race condition with asyncio lock.
- SDK: Fixed stream context manager cleanup on errors in HTTP client.
- SDK: Fixed empty/whitespace content handling in JSON response parsing.
- SDK: Added error handling for partial file cleanup on download failures.
- SDK: Improved filename sanitization for server-provided filenames (null bytes, control chars).
- CLI: Added validation for `--timeout`, `--max-columns`, `--max-records`, `--max-output-bytes` to reject non-positive values.
- CLI: Added validation for `--env-file` to check file exists when `--dotenv` is enabled.
- CLI: Added file size limit (1MB) for config files to prevent memory exhaustion.
- CLI: Added file permission warnings for API key files (same as config files).
- CLI: Fixed TOML escaping to handle newlines, tabs, and carriage returns in API keys.
- CLI: Fixed JSON/TOML parse error handling with helpful error messages.
- CLI: Fixed type validation for list access in CSV/table output (verify first element is dict).
- CLI: Fixed exponential backoff calculation to cap exponent before computing power.
- CLI: Let `AuthenticationError`/`AuthorizationError` propagate in query executor instead of silent catch.
- CLI: Added logging to silent exception handlers in entity validation.

### Changed (Breaking)
- CLI: `--check-unreplied-emails` renamed to `--check-unreplied` with support for both email and chat messages.
  - New `--unreplied-types` flag: comma-separated list of types to check (`email`, `chat`, `all`). Default: `email,chat`
  - Output now includes `type` field ("email" or "chat") in addition to date, daysSince, and subject
  - Chat messages have `null` subject (no subject attribute)
  - Cross-type reply detection: email replied via chat (or vice versa) counts as "replied"
- CLI: Query `expand: ["unrepliedEmails"]` renamed to `expand: ["unreplied"]`
  - Same cross-type reply detection and multi-type support as CLI flag
- CSV columns renamed: `unrepliedEmailDate` → `unrepliedDate`, `unrepliedEmailDaysSince` → `unrepliedDaysSince`, `unrepliedEmailSubject` → `unrepliedSubject`, plus new `unrepliedType` column

## 0.9.14 - 2026-01-19

### Fixed
- CLI: Query `select` clause now automatically includes `expand` fields. Previously, using `select` with `expand` would filter out the expansion data (e.g., `interactionDates`, `unreplied`), requiring users to explicitly list expansions in `select`.

## 0.9.13 - 2026-01-19

### Added
- CLI: Query `include` for `listEntries` now supports `persons`, `companies`, `opportunities`, and `interactions`. Fetches related entities based on list entry entity type (e.g., company entries get associated persons).
- CLI: Query `expand: ["unrepliedEmails"]` now works with `listEntries`. Checks each list entry's underlying entity for unreplied incoming emails.
- CLI: Query extended include syntax with parameters:
  - `{include: {interactions: {limit: 50, days: 180}}}` - Limit and lookback control
  - `{include: {opportunities: {list: "Pipeline"}}}` - Scope to specific opportunity list
  - `{include: {persons: {where: {...}}}}` - Filter included entities

### Fixed
- CLI: Query TOON format now correctly flattens `fields.*` and `interactionDates` like markdown and CSV formats do. Previously, TOON was missing the `_apply_explicit_flattening()` call, causing nested fields to be truncated and interaction dates to be missing entirely.
- CLI: `list export --check-unreplied-emails` now works standalone without requiring `--expand`.
- CLI: `expand: ["interactionDates"]` now always produces 8 canonical columns regardless of data presence, ensuring consistent schema across all records.
- SDK: Improved error message when adding wrong entity type to a list (e.g., adding a company to a person list). Now provides clear guidance about list type requirements instead of exposing raw API validation error.

### Performance
- CLI: Query `expand: ["interactionDates"]` is significantly faster with parallel person resolution, bounded concurrent fetches (semaphore=10), and parallelized section resolution. Default concurrency increased from 5 to 15 (tunable via `XAFFINITY_QUERY_CONCURRENCY`).

## 0.9.12 - 2026-01-17

### Changed
- CLI: Query `listEntries` now normalizes reference field values to display strings:
  - Person fields: `{"firstName": "Jane", "lastName": "Doe"}` → `"Jane Doe"`
  - Company fields: `{"name": "Acme Corp", "id": 123}` → `"Acme Corp"`
  - Multi-person/company fields: Arrays of names instead of raw objects
  - Use `expand` or `include` for full entity data when needed
- CLI: Query output with explicit `select` on `fields.*` paths now flattens fields to top-level columns in table/CSV/markdown formats. JSON output preserves nested structure.

## 0.9.11 - 2026-01-17

### Added
- CLI: Query `include` clause now displays included relationships inline by default. Use `--include-style=separate` for separate tables or `--include-style=ids-only` for raw IDs. JSON output includes full `included` and `included_by_parent` mappings for correlation.
- CLI: Query `include` extended syntax for custom display fields: `include: {companies: {display: ["name", "domain"]}}`.
- SDK: `with_interaction_dates` and `with_interaction_persons` parameters for `CompanyService.get()` and `PersonService.get()`. When enabled, routes to V1 API to fetch interaction date summaries (last/next meeting, email dates, team member IDs).
- CLI: `--expand interactions` option for `list export` command. Adds interaction date summaries to each list entry (last meeting, next meeting, last email, last interaction with daysSince/daysUntil calculations and team member names). Supports both JSON and CSV output formats.
- CLI: `expand: ["interactionDates"]` support in `query` command. Enriches records with interaction date summaries directly on each record in `result.data`. Works with `persons`, `companies`, and `listEntries` queries.
- CLI: `--check-unreplied-emails` flag for `list export` command. Detects unreplied incoming emails for each list entry and adds date, daysSince, and subject to output. Use `--unreplied-lookback-days` to configure lookback period (default: 30 days).
- CLI: `--with-interaction-dates` and `--with-interaction-persons` flags for `company get` and `person get` commands. Fetches interaction date summaries directly in entity get operations.
- MCP: Query tool `format` parameter now functional (was previously ignored). Supports `toon`, `markdown`, `json`, `jsonl`, `csv`.
- CLI: `--max-output-bytes` option for `query` command. Enables format-aware truncation for MCP use, returning exit code 100 when truncated.
- CLI: `format_toon_envelope()` function for TOON output with full envelope (`data[N]{...}:`, `pagination:`, `included_*:` sections).
- SDK: Batch association methods for PersonService, CompanyService, and OpportunityService:
  - `get_associated_company_ids_batch(person_ids)` / `get_associated_opportunity_ids_batch(person_ids)`
  - `get_associated_person_ids_batch(company_ids)` / `get_associated_opportunity_ids_batch(company_ids)`
  - `get_associated_person_ids_batch(opportunity_ids)` / `get_associated_company_ids_batch(opportunity_ids)`
  - All return `dict[EntityId, list[AssocId]]` with `on_error="raise"|"skip"` parameter.
- SDK: `retries` parameter on `persons.get()`, `companies.get()`, and `opportunities.get()` methods. Enables automatic retry with exponential backoff on 404 errors to handle V1→V2 eventual consistency after create operations. Default is `retries=0` (fail fast).
- CLI: Reminder date options now accept relative dates and keywords in addition to ISO-8601:
  - `--due-date`: `+7d`, `+2w`, `+1m`, `+1y`, `today`, `tomorrow`, `yesterday`, `now`
  - `--due-after`, `--due-before`: Same formats for filtering in `reminder ls`
  - Example: `xaffinity reminder create --due-date +7d --type one-time --owner-id 123`
- CLI: Domain validation for `company create` and `company update` now provides helpful error messages:
  - Detects underscores (RFC 1035 violation) and suggests dash replacement
  - Detects URL prefixes and extracts domain
  - Example: `--domain test_company.com` → "Use 'test-company.com' instead"
- Docs: V1→V2 eventual consistency guide covering 404 after create and stale data after update scenarios.
- Tests: Integration test suite for SDK write operations (`tests/integration/`).

### Changed
- SDK: `OpportunityService.get_associated_people()` and `get_associated_companies()` now use V2 batch lookup instead of individual V1 fetches, reducing N+1 API calls (e.g., 50 people now fetched in 2 calls instead of 51).
- SDK: Query executor `_batch_fetch_by_ids()` now uses V2 batch lookup for persons and companies, improving query performance on relationship includes.
- CLI: Query `include` clause now fetches relationship IDs in parallel, then batch-fetches full records via V2 API, reducing API calls from N×M to N+1 for deduped lookups.

### Changed (Breaking)
- MCP: Query tool default format changed from `json` to `toon` for better token efficiency (~40% fewer tokens).
- CLI: TOON query output now includes full envelope structure instead of data-only format. The `data` prefix is added to the array header: `data[N]{fields}:` instead of `[N]{fields}:`.

### Fixed
- MCP: Query tool now honors the `format` parameter instead of always using JSON output.
- SDK: `ListEntryService.batch_update_fields()` now uses correct V2 API payload format. Previously failed with "Missing discriminator for property operation" error.

## 0.9.9 - 2026-01-14

### Added
- CLI: `query` command now supports advanced relationship filtering:
  - `all` quantifier: Filter where all related items match a condition (e.g., find persons where all their companies have ".com" domains)
  - `none` quantifier: Filter where no related items match a condition (e.g., find persons with no spam interactions)
  - `exists` subquery: Filter where at least one related item exists, optionally matching a condition (e.g., find persons who have email interactions)
  - `_count` pseudo-field: Filter by count of related items (e.g., `"path": "companies._count", "op": "gte", "value": 2`)
  - Available relationship paths: persons→companies/opportunities/interactions/notes/listEntries, companies→persons/opportunities/interactions/notes/listEntries, opportunities→persons/companies/interactions
  - Note: These features cause N+1 API calls to fetch relationship data; use `--dry-run` to preview

### Changed (Breaking)
- CLI: Renamed relationship `"people"` to `"persons"` for consistency with entity type names:
  - Query `include`: `{"from": "companies", "include": ["people"]}` → use `["persons"]`
  - CLI `--expand`: `xaffinity company get <id> --expand people` → use `--expand persons`
  - JSON output: `data.people` → `data.persons`

### Fixed
- CLI: Query engine no longer silently passes all records for `all`, `none`, and `_count` filters. Previously these were placeholder implementations that returned `True` for all records, causing incorrect query results. (Bug #15)

## 0.9.8 - 2026-01-12

### Fixed
- CLI: `query` command now correctly fetches all records before applying filter, sort, or aggregate operations. Previously, limits were applied during fetch which caused incorrect results:
  - With filters: Empty results when matching records were beyond the limit position
  - With sort + limit: Random N records sorted instead of actual top N
  - With aggregate: Inaccurate counts/sums computed on partial data

## 0.9.7 - 2026-01-12

### Fixed
- CI: Smoke test now correctly installs CLI extras before testing CLI import

## 0.9.6 - 2026-01-12

### Added
- CLI: `listEntries` queries now include convenience aliases: `listEntryId`, `entityId`, `entityName`, `entityType`. These intuitive field names work in both `select` and `where` clauses.
- CLI: `listEntries` records now always include a `fields` key (defaults to `{}` if no custom fields).

### Changed
- CLI: Query projection now includes null values for explicitly selected fields. Previously, `select: ["entityName", "fields.Status"]` would return `{}` if Status was null; now returns `{"entityName": "Acme", "fields": {"Status": null}}`.

## 0.9.5 - 2026-01-12

### Added
- CLI: New `--output`/`-o` option supporting multiple formats: `json`, `jsonl`, `markdown`, `toon`, `csv`, `table` (default).
  - `markdown`: GitHub-flavored markdown tables, best for LLM analysis and comprehension
  - `toon`: Token-Optimized Object Notation, 30-60% fewer tokens than JSON for large datasets
  - `jsonl`: JSON Lines format, one object per line for streaming workflows
  - Example: `xaffinity person ls --output markdown`, `xaffinity query -o toon`
  - Existing `--csv` and `--json` flags continue to work as before.

### Changed
- CLI: `to_cell()` now extracts "text" from dropdown/multi-select fields instead of JSON-serializing the full dict. This makes CSV and other tabular outputs human-readable for dropdown values.

### Fixed
- CLI: `query` command with `limit` now correctly returns results when combined with client-side filters (like `has_any` on multi-select fields). Previously, the limit was applied during fetch before filtering, causing empty results when the first N records didn't match the filter criteria.

## 0.9.4 - 2026-01-12

### Added
- CLI: `query` command now supports `has_any` and `has_all` operators for multi-select field filtering.
- SDK/CLI: Filter parser now supports V2 API comparison operators: `>`, `>=`, `<`, `<=` for numeric/date comparisons.
- SDK/CLI: Filter parser now supports word-based operator aliases for LLM/human clarity:
  - `contains`, `starts_with`, `ends_with` (string matching)
  - `gt`, `gte`, `lt`, `lte` (numeric/date comparisons)
  - `is null`, `is not null`, `is empty` (null/empty checks)
- SDK/CLI: Filter parser now supports collection bracket syntax `[A, B, C]` with operators:
  - `in [A, B]` - value is one of the listed values
  - `between [1, 10]` - value is in range (inclusive)
  - `has_any [A, B]` - array field contains any of the values
  - `has_all [A, B]` - array field contains all of the values
  - `contains_any [A, B]` - substring match for any term
  - `contains_all [A, B]` - substring match for all terms
  - `= [A, B]` - set equality (array has exactly these elements)
  - `=~ [A, B]` - V2 API collection contains (array contains all elements)

### Fixed
- CLI: `query` command now correctly filters on multi-select dropdown fields (like "Team Member"). The `eq` operator checks array membership for scalar values and set equality for array values. Previously, these queries returned 0 results due to strict equality comparison.
- SDK/CLI: `list export --filter` now correctly matches multi-select dropdown fields. The `=`, `!=`, and `=~` operators now handle array values properly. Also fixes extraction of text values from multi-select dropdown API responses.
- SDK/CLI: Fixed `=^` (starts_with) and `=$` (ends_with) operators which were broken due to tokenizer ordering issue.

### Improved
- SDK/CLI: Filter parser now provides helpful hints for common mistakes:
  - Multi-word field names: suggests quoting (`"Team Member"`)
  - Multi-word values: suggests quoting (`"Intro Meeting"`)
  - SQL keywords (`AND`, `OR`): suggests correct symbols (`&`, `|`)
  - Double equals (`==`): suggests single `=`

## 0.9.3 - 2026-01-11

### Changed
- CI: SDK releases now include MCPB bundle and plugin ZIP for convenience.
- CI: Enabled PyPI attestations via workflow_dispatch API trigger.

## 0.9.2 - 2026-01-11

### Fixed
- SDK: `AsyncListEntryService.pages()` now supports `progress_callback` parameter (sync/async parity fix).

### Changed
- **BREAKING**: CLI: `interaction ls` JSON output restructured for consistency:
  - `.data.interactions` → `.data` (direct array)
  - `.data.metadata.totalRows` → `.meta.summary.totalRows`
  - `.data.metadata.dateRange` → `.meta.summary.dateRange`
  - `.data.metadata.typeStats` → `.meta.summary.typeBreakdown`
- **BREAKING**: CLI: `note ls` JSON output restructured for consistency:
  - `.data.notes` → `.data` (direct array)
  - Pagination: `.data.notes.nextCursor` → `.meta.pagination.nextCursor`
- **BREAKING**: CLI: `query` JSON output (with `--include-meta`) restructured for consistency:
  - `.meta.recordCount` → `.meta.summary.totalRows`
  - Included entity counts now in `.meta.summary.includedCounts`
- CLI: Standardized `ResultSummary` footer rendering across all commands (displays row counts, date ranges, type breakdowns as compact footer text instead of tables).

## 0.9.1 - 2026-01-11

### Added
- CLI: `query` command now validates entity queryability and provides clear error messages for unsupported entities.
- CLI: `query` command resolves field names to IDs automatically (e.g., `"field": "Status"` works alongside `"fieldId": 123`).

### Fixed
- CLI: `query` for `listEntries` entity now correctly requires `listId` filter.
- CLI: `query` relationship definitions now correctly set `requires_n_plus_1` flag for proper query planning.

## 0.9.0 - 2026-01-11

### Added
- CLI: New `query` command for structured JSON queries with complex filtering, includes, aggregations, and sorting. Use `--dry-run` to preview execution plans. Supports entities: persons, companies, opportunities, listEntries, interactions, notes.
- MCP: New `query` tool for complex data queries via JSON query language. Supports filtering (AND/OR/NOT), includes (related entities), aggregations (count/sum/avg/min/max), groupBy, and sorting.
- CLI: `--limit` alias for `--max-results` on `company get`, `person get`, and `opportunity get` commands (consistency with `ls` commands).

### Changed
- CLI: `--list`, `--list-entry-field`, and `--show-list-entry-fields` now auto-imply `--expand list-entries` on `company get` and `person get` commands (improved DX).

## 0.8.6 - 2026-01-10

### Added
- SDK: `PersonService.get_associated_company_ids()` and `get_associated_opportunity_ids()` methods for symmetric association API.
- SDK: `CompanyService.get_associated_opportunity_ids()` method.

## 0.8.5 - 2026-01-10

### Fixed
- CLI/SDK: `FilterParseError` now raised when filter expressions fail to parse (previously silently ignored). Common cause: unquoted multi-word values like `--filter 'Status=Intro Meeting'` must be quoted: `--filter 'Status="Intro Meeting"'`.
- CLI: Pre-commit hook now validates installed CLI version matches `pyproject.toml` before regenerating MCP registry.

## 0.8.4 - 2026-01-10

### Added
- CLI: NDJSON progress output for `interaction ls` multi-type queries (MCP integration).

## 0.8.3 - 2026-01-10

### Added
- CLI: `--limit` alias for `--max-results` on all `ls` commands (LLM-friendly).
- CLI: Option aliases now included in `--help --json` output.

## 0.8.2 - 2026-01-10

_No user-facing changes. Version bump for PyPI release._

## 0.8.1 - 2026-01-10

_No user-facing changes. Version bump for PyPI release._

## 0.8.0 - 2026-01-10

### Changed
- CLI: Renamed `--json` to `--set-json` on `person field`, `company field`, `opportunity field` commands to avoid conflict with global `--json` output flag.
- **BREAKING**: CLI: `--csv FILE` is now `--csv` flag that outputs CSV to stdout. Use shell redirection: `--csv > file.csv`. Applies to `person ls`, `company ls`, `opportunity ls`, `list export`.
- CLI: `--csv` and `--json` are now mutually exclusive (error if both specified).
- CLI: `person ls --query` and `company ls --query` now support `--field` and `--field-type` options via hybrid V1→V2 fetch.
- **BREAKING**: CLI: `interaction ls` date filter parameters renamed for consistency:
  - `--start-time` → `--after`
  - `--end-time` → `--before`
  - Context metadata keys changed: `startTime`/`endTime` → `after`/`before`
  - Note: Interaction object fields (`startTime`/`endTime`) are unchanged
- **BREAKING**: CLI: `interaction ls` now requires `--type` (was optional but API required it).
- CLI: `interaction ls` date range now defaults to all-time when `--days` and `--after` are omitted.
- **BREAKING**: CLI: `interaction ls` removed `--cursor` and `--all` flags (auto-chunking replaces manual pagination).
- **BREAKING**: CLI: `interaction ls` output field renamed `modifiers.type` → `modifiers.types` (now always an array).
- **BREAKING**: CLI: `interaction ls` metadata `chunksProcessed` moved to `typeStats[type].chunksProcessed`.
- **BREAKING**: CLI: Naive datetime strings (without timezone) are now interpreted as **local time** instead of UTC. Use explicit `Z` suffix or offset for UTC. See [datetime-handling guide](https://yaniv-golan.github.io/affinity-sdk/latest/guides/datetime-handling/) for details.
- **BREAKING**: CLI: List entry field commands unified into single `entry field` command:
  - `entry set-field --field F --value V` → `entry field --set F V`
  - `entry set-field --field F --value-json '{...}'` → `entry field --set-json '{"F": ...}'`
  - `entry set-fields --updates-json '{...}'` → `entry field --set-json '{...}'`
  - `entry unset-field --field F` → `entry field --unset F`
  - `entry unset-field --field F --value V` → `entry field --unset-value F V`
  - `entry unset-field --field F --all-values` → `entry field --unset F`
  - Removed: `--field-id` option (field IDs can be passed as FIELD argument directly)
  - **Behavior change:** `--set` on multi-value field now replaces all values (use `--append` to add)

### Added
- CLI: `interaction ls --type` now accepts multiple types (e.g., `--type email --type meeting`).
- CLI: `interaction ls --type all` convenience option fetches all interaction types.
- CLI: `interaction ls` multi-type results sorted by date descending (types interleaved).
- CLI: `interaction ls` metadata includes `typeStats` with per-type counts and chunk info.
- CLI: `interaction ls` auto-chunking for date ranges > 1 year (transparently splits into API-compatible chunks).
- CLI: `interaction ls --days N` convenience flag for "last N days" queries.
- CLI: `interaction ls --csv` and `--csv-bom` flags for CSV export.
- CLI: `interaction ls` metadata in JSON output includes `dateRange`, `typeStats`, `totalRows`.
- SDK: `ListEntryService.from_saved_view()` now accepts `field_ids` and `field_types` parameters.
- CLI: `list export --saved-view` can now be combined with `--field` for server-side filtering with explicit field selection.
- SDK: `ids` parameter added to `PersonService`, `CompanyService`, and `OpportunityService` for batch fetching by ID.
- CLI: `entry field --get FIELD` for reading field values (new functionality).
- CLI: `entry field --append FIELD VALUE` for adding to multi-value fields without replacing.
- CLI: `entry field --unset-value FIELD VALUE` for removing specific value from multi-value field.

### Fixed
- SDK: `FieldValues` now properly parses field arrays from API responses (previously showed `requested=False`).

### Removed
- CLI: `person search` and `company search` commands. Use `person ls --query` and `company ls --query` instead.
- CLI: Removed `entry set-field`, `entry set-fields`, and `entry unset-field` commands (replaced by unified `entry field`).

## 0.7.0 - 2026-01-08

### Added
- CLI: Column limiting for wide table output - tables now auto-limit columns based on terminal width.
- CLI: `--all-columns` flag to show all columns regardless of terminal width.
- CLI: `--max-columns N` flag for fine control over column limits.
- CLI: Real-time filter scanning progress during `list export --filter` shows "Scanning X... (Y matches)".
- CLI: Export summary line after filtered operations (e.g., "Exported 35 rows (filtered from 9,340 scanned) in 2:15").
- CLI: `format_duration()` helper for human-readable time formatting.
- CLI: Rich pager now uses `styles=True` to preserve ANSI colors when paging.
- SDK: `FilterStats` dataclass for tracking scanned/matched counts during filtered pagination.
- SDK: `PaginatedResponse.filter_stats` property exposes filter statistics.

### Fixed
- CLI: JSON progress output no longer appears alongside Rich progress bar (mutual exclusivity enforced).
- SDK: Dropdown field filtering now extracts "text" property from dropdown dicts.

## 0.6.11 - 2026-01-07

### Added
- CLI: Parameter help text (`help`) now included in `--help --json` output.
- CLI: Click.Choice values (`choices`) now included in `--help --json` output.
- CLI: Examples from docstrings now parsed and included in `--help --json` output.

### Changed
- CLI: Improved `--filter` help text with full operator list (`= != =~ =^ =$ > < >= <=`).
- CLI: Improved `--query` help text to clarify V1 fuzzy search vs V2 structured filtering.

## 0.6.10 - 2026-01-06

### Added
- CLI: JSON progress output to stderr when not connected to a TTY (for MCP integration).
- CLI: `@progress_capable` decorator to mark commands supporting progress reporting.
- CLI: Rate-limited progress updates (0.65s interval) with guaranteed 100% completion emission.
- CLI: `progressCapable` field in `--help --json` output for registry generation.
- MCP: `run_xaffinity_with_progress` helper for progress-aware CLI execution.
- MCP: `command_supports_progress` helper to check registry for progress capability.
- MCP: Execute tools now forward CLI progress for `@progress_capable` commands.
- CLI: File upload commands (`person/company/opportunity files upload`) marked as `@progress_capable`.
- MCP: `PROGRESS_MIN_VERSION` in COMPATIBILITY for graceful degradation with older CLIs.
- MCP: `version_gte` helper for portable version comparison (macOS/Linux).
- MCP: CLI version check in `command_supports_progress` (disables progress for CLI < 0.6.10).
- MCP: `XAFFINITY_CLI_VERSION` exported for tool scripts to access CLI version.

### Changed
- MCP: Updated mcp-bash.lock to patched v0.9.3 (commit ee245a7) with progress passthrough fixes.

## 0.6.9 - 2026-01-06

### Changed
- CLI Plugin: Skill now documents destructive command confirmation flow (look up, ask, wait, execute with `--yes`).
- CLI Plugin: Skill lists all destructive commands requiring double confirmation.

## 0.6.8 - 2026-01-05

### Added
- CLI: `@category` and `@destructive` decorators for MCP registry generation.
- CLI: `--help --json` output for machine-readable command documentation.
- CLI: Commands now expose category (read/write/local) and destructive metadata.

## 0.6.7 - 2026-01-03

### Changed
- Docs: Updated all documentation links to use versioned `/latest/` URLs for reliable navigation.

## 0.6.6 - 2026-01-03

### Fixed
- SDK: Regex patterns in `lists.py` and `http.py` were double-escaped, matching literal `\d` instead of digits.

## 0.6.5 - 2026-01-02

_No user-facing changes. Version bump for PyPI release._

## 0.6.4 - 2026-01-02

_No user-facing changes. Version bump for PyPI release._

## 0.6.3 - 2026-01-02

_No user-facing changes. Version bump for PyPI release._

## 0.6.2 - 2026-01-02

### Fixed
- CLI: Added explicit `type=str` to Click arguments for Python 3.13 mypy compatibility.

## 0.6.1 - 2026-01-02

### Changed
- Docs: Separated MCP Server documentation from Claude Code plugins.

## 0.6.0 - 2026-01-02

### Added
- MCP: `read-xaffinity-resource` tool for clients with limited resource support.

### Changed
- Plugins: Restructured into 3-plugin marketplace architecture (affinity-sdk, xaffinity-cli, xaffinity-mcp).
- Docs: Restructured Claude integrations with consistent naming.

### Fixed
- CLI: Improved `setup-key` command UX with Rich styling.
- MCP: Source both `.zprofile` and `.zshrc` in environment wrapper.
- MCP: Parse JSON response correctly for `check-key` output.

## 0.5.1 - 2026-01-01

### Fixed
- Plugins: Consolidated plugin structure and fixed relative paths.

## 0.5.0 - 2026-01-01

### Added
- MCP: Initial xaffinity MCP server as separate Claude Code plugin.
- CLI: Top-level `entry` command group as shorthand for `list entry` (e.g., `xaffinity entry get` instead of `xaffinity list entry get`).
- CLI: `--query` / `-q` flag for `person ls`, `company ls`, and `opportunity ls` to enable free-text search (V1 API).
- CLI: `--company-id` and `--opportunity-id` options for `interaction ls`.
- CLI: `-A` short flag for `--all` on all paginated list commands.
- CLI: `-n` short flag for `--max-results` on all commands with result limits.
- CLI: `-s` short flag for `--page-size` on all pagination commands.
- CLI: `-t` short flag for `--type` on interaction commands.
- CLI: Structured `CommandContext` for all commands.
- SDK: `OpportunityService.search()`, `search_pages()`, `search_all()` methods for V1 opportunity search.
- SDK: Async versions of opportunity search methods in `AsyncOpportunityService`.
- SDK: `InteractionService.list()` now accepts `company_id` and `opportunity_id` parameters.

### Changed
- CLI: `list view` renamed to `list get` for consistency with other entity commands.
- CLI: `--completed/--not-completed` boolean flag pattern for `reminder update` (replaces separate flags).
- CLI: Removed API version mentions from help text (implementation detail).
- CLI: `interaction ls` now requires an entity ID (`--person-id`, `--company-id`, or `--opportunity-id`) and defaults to last 7 days with visible warning (API max: 1 year).
- CLI: Unified `person field`, `company field`, `opportunity field` commands replace `set-field`, `set-fields`, and `unset-field` commands. New syntax: `--set FIELD VALUE`, `--unset FIELD`, `--set-json '{...}'`, `--get FIELD`.
- CLI: Note content separated from metadata in table display.

### Removed
- CLI: `person set-field`, `person set-fields`, `person unset-field` commands (use `person field` instead).
- CLI: `company set-field`, `company set-fields`, `company unset-field` commands (use `company field` instead).
- CLI: `opportunity set-field`, `opportunity set-fields`, `opportunity unset-field` commands (use `opportunity field` instead).

### Fixed
- CLI: Help text formatting - added missing spaces in command examples (~78 instances).
- CLI: Improved `--cursor` help text explaining incompatibility with `--page-size`.
- CLI: Clarified `--csv` help text to indicate it writes to file while stdout format is unchanged.
- CLI: CommandContext validation and test isolation issues.

## 0.4.8 - 2025-12-31

### Added
- CLI: `xaffinity field history` for viewing field value change history.
- CLI: Session caching for pipeline optimization via `AFFINITY_SESSION_CACHE` environment variable.
- CLI: `session start/end/status` commands for managing session cache lifecycle.
- CLI: `--session-cache` and `--no-cache` global flags for cache control.
- CLI: Cache hit/miss visibility with `--trace` flag.
- CLI: `config check-key --json` now includes `pattern` field showing key source.
- SDK: Client-side filtering for list entries (V2 API does not support server-side filtering).

### Changed
- CLI: `--filter` on list entry commands now applies client-side with warning (V2 API limitation).
- CLI: Removed `--opportunity-id` from `list entry add` (opportunities are created atomically via `opportunity create --list-id`).

### Fixed
- SDK: Client-side filter parsing handles whitespace-only and unparseable filters gracefully.
- CLI: `--filter` on list entries now returns proper field values (V2 API format).

## 0.4.0 - 2025-12-30

### Added
- CLI: `config check-key` command to check if an API key is configured (checks environment, .env, and config.toml).
- CLI: `config setup-key` command for secure API key configuration with hidden input, validation, and automatic .gitignore management.
- CLI: `set-field`, `set-fields`, `unset-field` commands for person, company, opportunity, and list entry entities.
- CLI: `list entry get` command with field metadata display.
- CLI: Enhanced `--expand-filter` syntax with OR (`|`), AND (`&`), NOT (`!`), NULL checks (`=*`, `!=*`), and contains (`=~`).
- SDK: `list_entries` field added to `Person` model.
- SDK: Unified filter parser with `parse()` function and `matches()` method for client-side filter evaluation.

### Changed
- CLI: Authentication error hints now reference `config check-key` and `config setup-key` commands.
- CLI: Authentication documentation updated with Quick Setup section.

### Fixed
- CLI: Default `--page-size` reduced from 200 to 100 to match Affinity API limit.
- SDK: Async `merge()` parameter names corrected (`primaryCompanyId`/`duplicateCompanyId`).
- SDK: Cache invalidation added to async create/update/delete in `CompanyService`.

### Removed
- CLI: Deprecated `field-value` and `field-value-changes` command groups removed (use entity-specific field commands instead).
- CLI: Deprecated `update-field` and `batch-update` list entry commands removed (use `set-field`/`set-fields` instead).

## 0.3.0 - 2025-12-30

### Added
- CLI: `xaffinity list export --expand` for exporting list entries with entity field expansion (company/person/opportunity fields).
- CLI: `xaffinity field-value-changes ls` for viewing field value change history.
- CLI: `xaffinity company get` (id/URL/resolver selectors) with `--all-fields` and `--expand lists|list-entries|people`.
- CLI: `xaffinity person get` (id/URL/resolver selectors) with `--all-fields` and `--expand lists|list-entries`.
- CLI: `xaffinity person ls` and `xaffinity company ls` with search flags.
- CLI: `xaffinity opportunity` command group with `ls/get/create/update/delete`.
- CLI: `xaffinity note`, `xaffinity reminder`, and `xaffinity interaction` command groups.
- CLI: `xaffinity file upload` command for file uploads.
- CLI: Write/merge/field operations for list entries.
- CLI: `--max-results` and `--all` controls for pagination and expansions.
- CLI: Progress reporting for all paginated commands.
- CLI: Rate limit visibility via SDK event hook.
- CLI: `--trace` flag for debugging SDK requests.
- SDK: `client.files.download_stream_with_info(...)` exposes headers/filename/size alongside streamed bytes.
- SDK: v1-only company association helpers `get_associated_person_ids(...)` and `get_associated_people(...)`.
- SDK: List-scoped opportunity resolution helpers `resolve(...)` and `resolve_all(...)`.
- SDK: Async parity for company and person services.
- SDK: Async parity for V1-only services.
- SDK: Async list and list entry write helpers.
- SDK: Pagination support for person resolution in `PersonService` and `AsyncPersonService`.
- SDK: `client.clear_cache()` method for cache invalidation.
- SDK: Field value changes service with `client.field_value_changes`.
- SDK: Detailed exception handling for `ConflictError`, `UnsafeUrlError`, and `UnsupportedOperationError`.
- SDK: Webhook `sent_at` timestamp validation.
- SDK: Request pipeline with policies (read-only mode, transport injection).
- SDK: `on_error` hook for error observability.
- Inbound webhook parsing helpers: `parse_webhook(...)`, `dispatch_webhook(...)`, and `BodyRegistry`.
- Claude Code plugin for SDK/CLI documentation and guidance.

### Changed
- CLI: Enum fields now display human-readable names instead of integers (type, status, direction, actionType).
- CLI: Datetimes render in local time with timezone info in column headers.
- CLI: Human/table output renders dict-shaped results as sections/tables (no JSON-looking panels).
- CLI: `--json` output now uses section-keyed `data` and `meta.pagination`.
- CLI: List-entry fields tables default to list-only fields; use `--list-entry-fields-scope all` for full payloads.
- CLI: Domain columns are now linkified in table output.
- CLI: Output only pages when content would scroll.
- `FieldValueType` is now V2-first and string-based (e.g. `dropdown-multi`, `ranked-dropdown`, `interaction`).
- `ListEntry.entity` is now discriminated by `entity_type`.
- Rate limit API unified across sync and async clients.

### Fixed
- SDK: `ListService.get()` now uses V1 API to return correct `list_size`.
- CLI: JSON serialization now handles datetime objects correctly.
- Sync entity file download `deadline_seconds` handling.
- File downloads now use public services for company expansion pagination.

## 0.2.0 - 2025-12-17

### Added
- Initial public release.
- `client.files.download_stream(...)` and `client.files.download_to(...)` for chunked file downloads.
- `client.files.upload_path(...)` and `client.files.upload_bytes(...)` for ergonomic uploads.
- `client.files.all(...)` / `client.files.iter(...)` for auto-pagination over files.

### Changed
- File downloads now follow redirects without forwarding credentials and use the standard retry/diagnostics policy.
- `client.files.list(...)` and `client.files.upload(...)` now require exactly one of `person_id`, `organization_id`, or `opportunity_id` (per API contract).
