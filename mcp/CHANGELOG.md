# MCP Server Changelog

All notable changes to the xaffinity MCP server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.20.1] - 2026-04-19

### Highlights

Registry and data-model guidance now document that enriched fields (Phone Number, Source of Introduction, Industry, Location, etc.) are writable through `person field --set` / `company field --set` by field name or field ID, and call out ambiguous names (like company "Industry", which exists under multiple enrichment providers) so the LLM reaches for a specific field ID instead of retrying. No MCP server code change; guidance-only release paired with CLI 1.11.0.

### Changed
- `person field` and `company field` `whenToUse` text now documents enriched-field writability and ambiguity handling
- Data-model guide documents enriched field writability and `EnrichedFieldNotWritableError` for derived-only fields like "Current Organization"

## [1.20.0] - 2026-04-04

### Highlights

MCP server now vendors the mcp-bash runtime (v1.2.0) directly in the repository. No network download required at startup — the server is fully self-contained. CI validates vendored runtime integrity via SHA-256 verification.

### Changed
- Vendored mcp-bash v1.2.0 runtime into `.mcp-bash/` directory (committed to repo)
- Updated launcher `find_framework()` to prioritize `.mcp-bash/` vendored path
- CI workflows use `mcp-bash vendor --verify` instead of downloading framework
- Removed `install` subcommand from `xaffinity-mcp.sh` (no longer needed)
- Removed `framework` section from `server.d/requirements.json`

### Removed
- `scripts/install-framework.sh` — replaced by vendored runtime

## [1.19.0] - 2026-02-21

### Highlights

New pipeline history analysis skill guides multi-step workflows for deal stage transitions, funnel conversion, and time-in-stage metrics. Also adds `field history-bulk` to the command registry for LLM discoverability.

### Added
- Pipeline history analysis skill (`pipeline-history`) with 5-step workflow: identify status field, export current state, dry-run estimate, fetch history, analyze transitions
- `field history-bulk` command in MCP command registry with `whenToUse` guidance, examples, and `requiresDryRun` behavior
- Bulk field change history section in data model resource

### Changed
- Updated `field history` registry entry to reference `field history-bulk` for bulk operations
- Updated CLI compatibility to require CLI 1.7.0 (for `field history-bulk` and `--list` alias)

## [1.18.1] - 2026-02-15

### Highlights

Improved LLM guidance quality: query language skill rewritten with progressive disclosure for better context efficiency, and workflow skill descriptions updated to Anthropic's recommended formula.

### Changed
- Rewrote `query-language` SKILL.md (861→293 lines) with progressive disclosure; extracted detail to reference files (`filter-operators.md`, `quantifiers.md`, `include-expand.md`, `output-formats.md`)
- Updated `affinity-mcp-workflows` skill description following Anthropic skills guide formula

## [1.18.0] - 2026-02-04

### Highlights

The Debug Mode toggle in Claude Desktop Extensions settings now works correctly. Also upgrades to mcp-bash 1.1.1, which includes zombie process mitigation and MCP Apps support.

### Fixed
- **Debug Mode UI toggle now works**: The "Debug Mode" toggle in Claude Desktop Extensions settings now correctly enables debug logging. Previously, the boolean value `true` was not recognized by the mcp-bash framework. Upgraded to mcp-bash 1.1.1 which normalizes boolean values (`true`/`1` → `debug`, `false`/`0` → `info`).

### Changed
- Upgraded mcp-bash framework to 1.1.1 (major version jump includes zombie process mitigation, MCP Apps support, and boolean log level normalization).
- Fixed incorrect comment in `xaffinity-mcp.sh` that claimed `env.sh` is sourced in bundle mode (it is not).

## [1.17.1] - 2026-02-03

### Highlights

Query tool timeout parameter is now detected correctly when nested inside the `query` object. Requires CLI 1.0.3 for incremental progress during large include operations.

### Fixed
- **Query tool timeout parameter detection**: The `timeout` parameter is now detected when placed inside the `query` object instead of at the top level. Previously, `{"query": {..., "timeout": 300}}` was silently ignored, falling back to auto-calculated timeout (minimum 30s). The tool now uses the nested value with a warning, and auto-converts milliseconds to seconds if value > 1000.

### Changed
- Requires CLI 1.0.3 (for incremental progress during N+1 include operations).

## [1.17.0] - 2026-01-31

### Highlights

MCP-only users now get automatic update notifications when a new CLI version is available. Checks are non-blocking and throttled to once per 24 hours. Requires CLI 1.0.0.

### Added
- **Update notifications for MCP-only users**: The MCP server now checks for CLI updates at startup and displays warnings when a new version is available. This helps users who only use the MCP server (via Claude Desktop or Claude Code) stay up to date. Update checks are non-blocking, throttled to 24 hours, and respect user opt-out (`XAFFINITY_NO_UPDATE_CHECK=1` or config file). Uses new CLI `--background` flag for background checks.

### Changed
- Requires CLI 1.0.0 (for `--background` flag support).

## [1.16.4] - 2026-01-27

### Fixed
- **MCPB API key not passed to tools**: Added `AFFINITY_API_KEY` to the `MCPBASH_TOOL_ENV_ALLOWLIST` in `env.sh`. The mcp-bash framework filters environment variables for security, and `AFFINITY_API_KEY` was missing from the allowlist, causing the API key configured in Claude Desktop to be silently dropped before reaching CLI commands.

## [1.16.3] - 2026-01-27

### Fixed
- **CLI detection for macOS Python framework**: Added `/Library/Frameworks/Python.framework/Versions/*/bin/` to CLI detection paths. Users who installed the CLI via `pip install` with python.org Python installer were getting "xaffinity: command not found" errors because this path wasn't checked.

## [1.16.2] - 2026-01-27

### Added
- Docs: "API Key" configuration section in README explaining how MCPB config interacts with CLI's API key resolution priority. Documents that Claude Desktop sets `AFFINITY_API_KEY` env var (priority 3), which overrides config file but not explicit CLI flags.
- Docs: "Verify Installation" troubleshooting section with two-layer health check: (1) CLI layer (`xaffinity --version`, `AFFINITY_API_KEY=... xaffinity whoami`), (2) MCP layer (`mcp-bash run-tool`). Includes platform-specific paths for locating MCPB installations.

### Changed
- Requires CLI 0.16.1 (version alignment).

## [1.16.1] - 2026-01-26

### Fixed
- **Query tool stdin reliability**: Changed query tool to use temp file (`--file`) instead of stdin pipeline. Fixes "Invalid JSON from stdin" errors that occurred in Cowork VM environments where multi-hop stdin piping was unreliable.

## [1.16.0] - 2026-01-25

### Added
- **JSON format cursor pagination**: The `query` tool now supports cursor-based pagination for JSON format output. Previously, JSON truncation happened at the MCP layer (post-CLI) which could not emit cursors, causing `truncated: true` responses without a `nextCursor`. Now all formats (toon, markdown, json, jsonl, csv) use unified CLI-level truncation with cursor support.

### Changed
- **Unified truncation flow**: All output formats now pass `--max-output-bytes` to CLI. CLI performs semantic truncation (removes array items while preserving structure) and emits cursors when output is truncated. MCP layer simplified to pass through CLI output.
- **Improved LLM guidance**: Updated `tool.meta.json` and `data-model.md` documentation to clarify cursor behavior and prevent cursor fabrication.

### Fixed
- **Cursor availability for JSON format**: Fixed issue where JSON format responses could have `truncated: true` without a `nextCursor`, leading LLMs to fabricate invalid cursors.

## [1.15.0] - 2026-01-25

### Changed
- **mcp-bash framework 0.14.0**: Upgraded from 0.13.0. Key improvements:
  - **Zombie process mitigation**: MCP servers now automatically exit on idle timeout (default 1 hour) and when parent process dies (orphan detection). Prevents zombie server accumulation in Claude Desktop/Cowork. Configurable via `MCPBASH_IDLE_TIMEOUT`, `MCPBASH_IDLE_TIMEOUT_ENABLED`, `MCPBASH_ORPHAN_CHECK_ENABLED`.
  - **Progress-aware timeout fix**: Registry generation now correctly copies `progressExtendsTimeout` and `maxTimeoutSecs` fields from `tool.meta.json`. Previously these settings were silently ignored, causing tools like `query` and `execute-read-command` to fall back to fixed countdown mode. Timeout extension now works as configured.
  - **Debug file fix**: The `server.d/.debug` marker file now properly enables debug-level MCP log notifications (previously only set env var but didn't sync log level).
  - **Log notification reliability**: Log notifications emitted during handler execution no longer corrupt JSON-RPC responses (deferred to queue file and flushed after handler completes).

## [1.14.1] - 2026-01-24

### Fixed
- **Bash 3.2 compatibility (associative arrays)**: Replaced `local -A provided_flags` in `cli-gateway.sh` with string-based tracking. macOS ships with Bash 3.2.57 which doesn't support associative arrays (`local -A`). This caused `execute-read-command` and `execute-write-command` to fail with "local: -A: invalid option" when any flags were passed in argv.
- **Bash 3.2 compatibility (empty arrays)**: Fixed empty array expansion in `execute-read-command` and `execute-write-command` tools. With `set -u` (strict mode), `${argv[@]}` fails as "unbound variable" when argv is empty in Bash 3.2. Now uses `${argv[@]+"${argv[@]}"}` pattern and `[[ ${#argv[@]} -gt 0 ]]` guards.
- **Resource scripts CLI path**: Fixed "xaffinity: command not found" errors in `saved-views`, `field-catalogs`, and `workflow-config` resource scripts. These scripts now source `common.sh` and use `${XAFFINITY_CLI:-xaffinity}` for CLI invocation, matching the pattern used by tools.

### Added
- **Debug logging for query pipeline**: Added `xaffinity_log_debug` tracing in `query/tool.sh` and `common.sh` to help diagnose Issue 3 (empty stdin to CLI) if it recurs. Enable with `XAFFINITY_MCP_DEBUG=1` or `MCPBASH_LOG_LEVEL=debug`.

## [1.14.0] - 2026-01-23

### Added
- **`files read` commands**: `company files read`, `person files read`, `opportunity files read` now discoverable via MCP. Returns base64-encoded file content with chunking support (`--offset`, `--limit`). Use this instead of `get-file-url` when WebFetch is blocked (Claude Desktop/Cowork).

### Changed
- **Updated data-model documentation**: "Reading Files" section now presents both options (`files read` vs `get-file-url`) with comparison table showing when to use each approach.
- **Updated `files ls` commands**: Now recommend `files read` for Claude Desktop/Cowork environments where presigned URLs are blocked.
- **Updated mcp-bash framework to v0.13.0**: Brings `timeoutHint` support for actionable timeout messages and improved activity detection (any pattern match extends timeout, not just progress fields).
- **Added `timeoutHint` to timeout-prone tools**: `query`, `execute-read-command`, `execute-write-command`, and `get-entity-dossier` now provide actionable guidance when tools timeout, helping LLMs adjust their approach (e.g., "Use dryRun=true first", "Reduce batch size").


### Fixed
- **Claude Desktop/Cowork file access**: The `files read` command provides a workaround for the domain sandbox restriction that blocks `userfiles.affinity.co`. Content is returned inline as base64, bypassing WebFetch.

## [1.13.0] - 2026-01-22

### Added
- **New `get-file-url` tool**: Get presigned download URLs for files attached to companies, persons, or opportunities. Returns URL valid for 60 seconds with file metadata (name, size, contentType). Use with WebFetch to read file content.
- **File listing commands**: `company files ls`, `person files ls`, `opportunity files ls` now discoverable via MCP with guidance to use `get-file-url` for content access.

### Changed
- **Hidden `files download` from MCP**: The `files download` command (renamed from `dump`) is hidden from MCP discovery since it downloads to local filesystem which LLMs cannot access. Use `get-file-url` + WebFetch instead.
- **Updated `files ls` commands**: Added `mcpNote` guiding users to use `get-file-url` tool with file IDs from `data[].id` to get presigned URLs.
- **Data model documentation**: Added "Reading Files" section to `xaffinity://data-model` resource explaining the 3-step workflow: `files ls` → `get-file-url` → WebFetch.
- **CLI compatibility**: Updated `CLI_MIN_VERSION` from 0.13.0 to 0.14.0. Required for `files ls`, `file-url`, and `files download --file-id` commands.

### Fixed
- **CLI Gateway nargs validation**: Fixed argument validation for options with `nargs > 1` (e.g., `entry field set --set FIELD VALUE`). Previously, `validate_argv` only consumed one value for such options, causing "Too many arguments" errors when the second value was incorrectly treated as a positional argument. Now correctly consumes the number of values specified by `nargs` in the registry schema.
- **Claude Cowork compatibility**: Fixed "xaffinity: command not found" errors when MCP server is spawned with minimal PATH that excludes version manager shims (pyenv, asdf, mise, pipx). Added runtime CLI detection using mcp-bash recommended pattern (`lib/cli-detect.sh`). Detection searches common shim locations at tool execution time when `$HOME` is available. Users can override with `XAFFINITY_CLI=/path/to/xaffinity`.
- **Policy error messages**: Improved error messages when tools are blocked by policy - now explains whether tool is missing from allowlist or blocked by read-only mode.
- **Bash 3.x compatibility**: Fixed `apply_limit_cap` usage comment to use while-loop pattern instead of `mapfile` (not available in macOS default Bash 3.2).

### Known Issues
- **Claude Desktop domain sandbox**: The `get-file-url` tool returns valid presigned URLs, but Claude Desktop's WebFetch cannot access `userfiles.affinity.co` due to domain sandbox restrictions. This affects ALL Claude Desktop users - neither "Additional allowed domains" nor "All domains" settings work around this limitation ([#19087](https://github.com/anthropics/claude-code/issues/19087), [#11897](https://github.com/anthropics/claude-code/issues/11897)). **Workaround**: Use `files read` command (returns content inline as base64), copy URL to browser, or use CLI directly with `files download --file-id`.

## [1.12.1] - 2026-01-21

### Fixed
- **execute-read-command**: Fixed `list export` truncation path (`.data.rows` instead of incorrect `.data.listEntries`)
- **Resources**: Skill files (workflows-guide, query-guide) now bundled correctly via `MCPB_INCLUDE`
- **Error handling**: Resources (`me.sh`, `me-person-id.sh`) now capture and include actual CLI errors instead of generic messages
- **Error handling**: Tools (`get-entity-dossier`) now use graceful degradation with logged warnings instead of failing silently
- **Resource context**: `run_xaffinity_readonly` now works in resource scripts by falling back to direct execution when `mcp_with_retry` is unavailable

### Changed
- **Documentation**: Added performance warnings for `fields.*` in data-model and query-guide resources

## [1.12.0] - 2026-01-21

### Added
- **New resources for Claude Desktop users**: Added two new MCP resources that expose Claude Code skill content to all MCP clients:
  - `xaffinity://workflows-guide` - MCP tools, prompts, and workflow patterns (reads from `.claude-plugin/skills/affinity-mcp-workflows/SKILL.md`)
  - `xaffinity://query-guide` - Complete query language reference with all operators, aggregations, and advanced filtering (reads from `.claude-plugin/skills/query-language/SKILL.md`)
  - Resources dynamically read from skill files (single source of truth) and strip YAML frontmatter
- **Cross-references in data-model resource**: The `xaffinity://data-model` resource now points to `query-guide` and `workflows-guide` for detailed reference material
- **Tool description updates**: `query` and `execute-read-command` tools now reference the new resources in their descriptions

### Changed
- **mcp-bash framework 0.12.0**: Upgraded from 0.11.0. Timeout errors now use `isError: true` format instead of JSON-RPC `-32603` errors. This ensures MCP clients (Claude Desktop, etc.) display the full timeout message with structured metadata (`type`, `reason`, `timeoutSecs`, `exitCode`) instead of a generic "Tool execution failed" message.
- **Dynamic timeout extension for expand/include queries**: Tools with progress reporting (`query`, `execute-read-command`) now use `progressExtendsTimeout` to dynamically extend timeouts as long as the CLI emits progress. This allows large expand/include queries (~400 records) to complete without timeout errors.
  - `query` tool: 60s watchdog / 600s ceiling (supports ~430 records with expand)
  - `execute-read-command` tool: 120s watchdog / 300s ceiling (supports ~215 records)
  - Watchdog resets on each progress message (~0.65s intervals during expand loops)
  - Queries without progress still timeout at the watchdog interval (stuck detection)
- **Streamlined data-model resource**: Trimmed redundant content that's now available in `query-guide` (multi-select filtering details, full filter operator list)
- **CLI compatibility**: Updated `CLI_MIN_VERSION` from 0.12.0 to 0.13.0. Required for accurate `listSize` values in `list get` output and dry-run estimates (V2 API bug returned 0 for non-empty lists; CLI 0.13.0 uses V1 API).

## [1.11.0] - 2026-01-20

### Added
- **Resumable query responses**: The `query` tool now supports cursor-based pagination for truncated responses:
  - New `cursor` input parameter: Pass the `nextCursor` from a truncated response to get the next chunk
  - `nextCursor` field in response: When output is truncated, includes an opaque cursor for resumption
  - `_cursorMode` hint: Indicates cursor mode ("streaming" or "full-fetch") for debugging
  - **Streaming mode** (simple queries): O(1) resumption via stored API cursor - no re-fetching of previous pages
  - **Full-fetch mode** (queries with orderBy/aggregate): Results cached to disk, zero API calls on resume
  - Cursors expire after 1 hour and require identical query + format for validation

### Changed
- **CLI compatibility**: Updated `CLI_MIN_VERSION` from 0.11.0 to 0.12.0 for cursor-based pagination support

## [1.10.0] - 2026-01-20

### Added
- **Full scan protection**: MCP gateway now enforces pagination limits on all `ls` and `export` commands to prevent runaway API usage:
  - Default limit: 1000 records (auto-injected if no `--max-results` specified)
  - Maximum limit: 10000 records (higher values are capped with warning)
  - `--all` flag is blocked with a clear error message suggesting `--max-results` or cursor pagination
  - To fetch more than 10000 records, use cursor pagination with `--cursor`

### Fixed
- **Session cache not being used by MCP tools**: The `execute-read-command`, `execute-write-command`, and `query` tools were not passing `--session-cache` to the CLI, missing out on cross-invocation caching of list name resolutions and field metadata. Now correctly passes the flag before the subcommand (required by CLI option parsing).

### Changed
- **CLI compatibility**: Updated `CLI_MIN_VERSION` from 0.10.0 to 0.11.0 to ensure users get Person entity detection fixes (`entityName` now correctly computed for Person list entries, formatters display person names instead of "object (N keys)").
- **Improved `field history` discoverability**: LLMs were failing to discover the `field history` command. Updated guidance in multiple locations:
  - Added "history", "audit", "track changes" to `discover-commands` common queries
  - Updated `whenToUse` in mcp-commands.json with business context (audit who changed what, track status changes)
  - Added `field history` example to `execute-read-command` tool description
  - Added "Audit field changes" section to `data-model.md` with use cases
  - Improved SKILL.md description to emphasize audit/tracking use cases

## [1.9.6] - 2026-01-19

### Changed
- **CLI compatibility**: Updated `CLI_MIN_VERSION` from 0.9.13 to 0.10.0 to ensure users get 40 bug fixes from the SDK audit (thread safety, stream cleanup, validation improvements).

## [1.9.5] - 2026-01-19

### Fixed
- **`interaction ls` and `note ls` truncation bug**: Fixed `_get_array_path()` to correctly return `.data` for these commands which output data as a direct array (changed in SDK v0.9.2), not `.data.interactions` or `.data.notes`. This was causing `invalid_path` errors when the MCP tool tried to truncate responses.

### Changed
- **Prescriptive TOON format guidance**: Added "Mistake 3: Using JSON format for bulk queries" to `data-model.md`. This makes the guidance prescriptive rather than just descriptive, reducing wasted API calls from truncation.
- **Updated CSV export documentation**: Fixed outdated jq paths in `docs/public/guides/csv-export.md` for `note ls` and `interaction ls` commands.

## [1.9.4] - 2026-01-19

### Changed
- **Smarter JSON truncation**: Upgraded mcp-bash framework from 0.10.0 to 0.11.0. Tools now use explicit `--array-path` for truncation instead of heuristics:
  - `query` tool: truncates `.data` array directly
  - `execute-read-command`: dynamically determines array path based on command (e.g., `.data.companies` for `company ls`)
- **TOON format guidance**: Added documentation in `data-model.md` explaining TOON format parsing with the official `toon_format` Python library, including file-based processing patterns.

## [1.9.3] - 2026-01-19

### Fixed
- **`--json` flag handling**: Gateway tools (`execute-read-command`, `execute-write-command`) now silently filter out `--json` if passed in `argv`. Previously, passing `--json` returned an error requiring a retry. The tools always append `--json` automatically, so filtering is idempotent.

### Changed
- **LLM guidance updates**: Added notes to `data-model.md`, `execute-read-command`, and `query` tool descriptions clarifying that JSON output is automatic (do not pass `--json`) and that `expand` fields are automatically included in `select` output.
- **`discover-commands` text format**: Now shows "one-of" required parameter groups with grouped notation, e.g., `(--person-id|--company-id|--opportunity-id):i!` for commands like `field history` and `interaction ls` that require exactly one entity selector. Parameters in these groups are excluded from the regular parameter list to avoid duplication.
- **`relatedCommands` improvements**: Fixed and expanded related command references in `mcp-commands.json` for better command discovery. Fixed `execute-read-command` description referencing nonexistent `saved-view ls` (saved views are shown by `list get`).

## [1.9.2] - 2026-01-19

### Changed
- **MCP command registry architecture**: Refactored to explicit opt-in model. New CLI commands are no longer auto-exposed to MCP. Commands must be explicitly listed in `mcp-commands.json` to be discoverable.
  - Source of truth: `mcp/.registry/mcp-commands.json` (62 commands whitelisted)
  - Generated output: `mcp/.registry/commands.generated.json` (includes `_generated` metadata)
  - Generator: `tools/generate_mcp_command_registry.py`
  - Excluded: `completion`, `config init`, `session *`, `version`, `list entry *` (aliases)
- **Query command rich metadata**: Enhanced `query` command guidance in registry with comprehensive self-contained documentation:
  - Full syntax reference (structure, entities, operators, compound, quantifiers, relative dates)
  - Critical notes (listEntries filter requirement, include vs expand, maxRecords for quantifiers, multi-select fields)
  - Include/expand references with parameterized syntax
  - Field path patterns (custom fields, wildcards, array access)
  - Output format recommendations (toon for efficiency, markdown for LLM analysis)
  - 9 diverse examples covering filtering, includes, expands, aggregations, quantifiers

## [1.9.1] - 2026-01-18

### Changed
- **`execute-read-command` tool guidance**: Updated description to clarify when to use `query` tool (bulk data with TOON format support) vs `execute-read-command` (CLI commands without query equivalent like `field ls`, `field history`, `interaction ls`)

## [1.9.0] - 2026-01-18

### Fixed
- **"Command is required" intermittent error**: Upgraded mcp-bash framework from 0.9.13 to 0.10.0, which fixes a critical bug where complex filter arguments with escaped quotes (e.g., `--filter 'Status in ["New", "Intro Meeting"]'`) would intermittently fail with "Command is required" error despite arguments being received. The bug was caused by TSV double-escaping corrupting JSON payloads during argument extraction.
- **Removed dead `format` parameter from `execute-read-command`**: The `format` parameter was documented in tool.meta.json but never implemented - the tool always outputs JSON. Removed the parameter to avoid confusion. (The `query` tool's `format` parameter works correctly.)
- **`execute-read-command` timeout too short**: Increased default timeout from 30 to 120 seconds. Complex queries and large exports were timing out before completion.

### Added
- **User-configurable settings (MCPB)**: Added `user-config.json` schema for implementing apps (Claude Desktop, etc.) to collect and pass configuration:
  - `api_key`: Affinity API key (required, masked in UI)
  - `read_only`: Restrict to read-only operations
  - `disable_destructive`: Block destructive commands entirely
  - `session_cache_ttl`: API response cache lifetime (0-3600 seconds)
  - `debug_mode`: Enable verbose logging
- **Registry/marketplace metadata**: Added MCPB manifest fields for registry listing:
  - License (MIT), keywords, homepage, documentation, and support URLs

### Changed
- **mcp-bash framework 0.10.0**: Updated from 0.9.13; fixes TSV parsing vulnerability in `tools/call` handler. No changes required to tool code - fix is internal to framework.
- **LLM-actionable error hints**: Adopted `mcp_error --hint` SDK helper across all tools and validation code. Errors now include actionable guidance for LLM self-correction:
  - `query` tool: Format validation, missing "from" field
  - `execute-read-command` / `execute-write-command`: Command required, argv validation, reserved flags, cancellation
  - `execute-write-command`: Destructive disabled, confirmation required, --yes flag errors
  - `discover-commands`: Invalid category
  - `cli-gateway.sh`: Registry errors, command not found (with "Did you mean" suggestions), unknown flags, type validation, missing required flags

## [1.8.8] - 2026-01-17

### Added
- **Query tool: TOON format default**: Query tool now defaults to TOON format for ~40% token reduction. Use `format: "json"` for JSON output.
- **Query tool: Format parameter**: The `format` parameter now works correctly. Supports `toon`, `markdown`, `json`, `jsonl`, `csv`.
- **Query tool: Include inline expansion**: Included relationships now display inline by default with display names (e.g., company names instead of IDs).
- **Query tool: Interaction dates expansion**: Support for `expand: ["interactionDates"]` to enrich records with last/next meeting, email dates.

### Changed
- **CLI minimum version**: Now requires CLI 0.9.11+ (was 0.9.9)

### Changed (Breaking)
- **Query tool default format**: Changed from `json` to `toon`. Existing integrations expecting JSON should explicitly set `format: "json"`.

### Fixed
- **Query tool format parameter**: Now correctly honors the `format` parameter instead of always using JSON.

## [1.8.7] - 2026-01-14

### Added
- **Query tool: Advanced relationship filtering**: Support for `all`, `none`, `exists` quantifiers and `_count` pseudo-field for filtering based on related entities. (CLI feature from 0.9.9)

## [1.8.6] - 2026-01-14

### Changed
- **Gateway tools diagnostic errors**: `execute-read-command` and `execute-write-command` now return diagnostic info when "Command is required" error occurs, including `argsLength` and `argsPreview` to help debug intermittent argument passing issues

### Fixed
- **Query tool always returned execution plan**: Fixed bash boolean handling bug where `${dry_run:+--dry-run}` always expanded because the string `"false"` is non-empty. Query tool now correctly executes queries instead of always returning dry-run plans. (Bug introduced in 1.8.4)

## [1.8.5] - 2026-01-13

### Changed
- **mcp-bash framework 0.9.13**: Updated from 0.9.10; gateway tools now use `mcp_extract_cli_error` helper for extracting error messages from structured JSON CLI output (checks `.error` as string, `.message` with status flags, `.errors[0].message`)
- **LLM guidance for `field history`**: Added `whenToUse` and examples to registry metadata clarifying that exactly one entity selector is required (`--person-id`, `--company-id`, `--opportunity-id`, or `--list-entry-id`)
- **LLM guidance for multi-word filters**: Updated SKILL.md and registry examples to show proper quoting for multi-word field names (e.g., `--filter '"Team Member"=~"LB"'`)

### Fixed
- **Gateway tool error capture**: `execute-read-command` and `execute-write-command` now properly capture CLI error messages when using progress forwarding (was capturing helper's stderr instead of CLI's)
- **Field catalogs jq path**: Fixed list name resolution in `field-catalogs` resource (was using wrong jq path `.data[]` instead of `.data.lists[]`)
- **Error message extraction**: Gateway tools now extract `.error.message` from CLI JSON responses when commands fail (CLI outputs structured errors to stdout with `--json`, not stderr)

## [1.8.4] - 2026-01-12

### Added
- **Field catalogs by list name**: `xaffinity://field-catalogs/{listName}` now accepts list names in addition to numeric IDs, matching the `query` tool's `listName` filter support
- **Real-time query progress**: `query` tool now forwards detailed CLI progress to MCP clients (step descriptions, record counts, completion status). Previously only reported 0%/100%.

### Changed
- **mcp-bash framework 0.9.11**: Updated from 0.9.10; adds `--stderr-file` option to `mcp_run_with_progress` for capturing non-progress stderr (enables detailed error reporting with progress forwarding)
- **Progress helper enhancements**: `run_xaffinity_with_progress` now supports `--stdin` (for query tool) and `--stderr-file` (for error capture)

## [1.8.3] - 2026-01-12

### Fixed
- **Query fetch ordering**: Fixed critical bugs where limits were applied during fetch instead of after filter/sort/aggregate:
  - With filters: Now correctly finds matching records regardless of their position
  - With sort + limit: Now returns actual top N records instead of random N sorted
  - With aggregate: Now computes accurate counts/sums on complete dataset

### Changed
- **CLI minimum version**: Now requires CLI 0.9.8+ (was 0.9.6)

## [1.8.2] - 2026-01-12

### Added
- **listEntries field aliases**: Query tool now supports intuitive field names for listEntries:
  - `listEntryId` - list entry ID (alias for `id`)
  - `entityId` - entity ID (alias for `entity.id`)
  - `entityName` - entity name (alias for `entity.name`)
  - `entityType` - entity type (alias for `type`)
- **Available Select Fields**: Added documentation table in SKILL.md listing all available select fields for listEntries
- **Null values in projection**: Explicitly selected fields now appear in output even when null

### Changed
- **CLI minimum version**: Now requires CLI 0.9.6+ (was 0.9.2)

## [1.8.1] - 2026-01-12

### Changed
- **Query examples**: Use `and`/`or`/`not` instead of `and_`/`or_`/`not_` in all query tool documentation (both forms work, but the cleaner alias is now preferred)

## [1.8.0] - 2026-01-12

### Added
- **Output formats**: New `format` parameter for `query` and `execute-read-command` tools
  - Supported formats: `json` (default), `jsonl`, `markdown`, `toon`, `csv`
  - `markdown`: Best for LLM comprehension when analyzing/summarizing data
  - `toon`: 30-60% fewer tokens than JSON, best for large datasets
  - `jsonl`: One JSON object per line, best for streaming workflows
- **SKILL.md**: Added "Output Formats" section with format comparison table and recommendations
- **SKILL.md**: Added format parameter documentation to MCP workflows guide

## [1.7.6] - 2026-01-11

### Fixed
- **query tool**: Now properly passes `--json` and `--quiet` flags to CLI for correct output formatting
- **macOS compatibility**: Removed Linux-specific `timeout` command usage that caused failures on macOS

## [1.7.5] - 2026-01-11

### Added
- **query tool**: New MCP tool for executing structured JSON queries against Affinity data

### Fixed
- **query tool discovery**: Added `query` to tool allowlist (files existed but tool was not discoverable)

## [1.7.4] - 2026-01-10

### Changed
- **SKILL.md**: Added guidance that `--filter` only works on list-defined fields (not `entityId`/`entityType`/`listEntryId`)
- **SKILL.md**: Added alternative approaches for finding specific entities in lists
- **Registry**: Enhanced `list export` `whenToUse` with filter field limitations
- **env.sh**: `MCPBASH_DEBUG_PAYLOADS=1` now auto-enabled in debug mode for payload logging

## [1.7.3] - 2026-01-10

### Changed
- **SKILL.md**: Added explicit filter quoting guidance - multi-word values MUST be quoted (e.g., `--filter 'Status="Intro Meeting"'`)
- **Registry**: `list export` command now includes examples showing correct filter quoting syntax
- **Registry**: Enhanced `whenToUse` guidance for filter-related commands

## [1.7.2] - 2026-01-10

### Fixed
- **Registry**: `interaction ls --type` now correctly marked as `multiple: true`
- **Prompts**: Updated `warm-intro` and `interaction-brief` to use `--type all` syntax

## [1.7.1] - 2026-01-10

### Changed
- **CLI Gateway**: Now accepts option aliases (e.g., `--limit` for `--max-results`)
- **Registry**: Option aliases now included in command registry

### Fixed
- **LLM compatibility**: Commands using `--limit` (common LLM pattern) now work correctly

## [1.7.0] - 2026-01-10

### Changed
- **CLI 0.8.0 required**: Updated minimum CLI version from 0.6.0 to 0.8.0
- **Updated prompts**: `change-status` and `log-interaction-and-update-workflow` now use `entry field` command
- **Updated tool descriptions**: `execute-write-command` examples updated for `entry field` syntax
- **Updated SKILL.md**: Command references updated for unified `entry field` command

### Compatibility
- **BREAKING**: Requires CLI 0.8.0+ (previous MCP versions worked with CLI 0.6.0+)

## [1.6.0] - 2026-01-08

### Added
- **Parameterized MCP resources**: Three new resources with URI templates for dynamic data access
  - `xaffinity://saved-views/{listId}`: Returns saved views available for a specific list
  - `xaffinity://workflow-config/{listId}`: Returns workflow configuration including status field options and saved views
  - `xaffinity://field-catalogs/{entityType}`: Returns field schema for lists (by ID) or global entity types (person/company/opportunity)
- **Session caching for field ls**: CLI `field ls` command now uses session cache when `AFFINITY_SESSION_CACHE` is set, reducing redundant API calls

### Changed
- **mcp-bash framework 0.9.10**: Updated from 0.9.5; fixes validator for `uriTemplate`, bundle completeness (require.sh, handler_helpers.sh, progress-passthrough.sh), and registry corruption for parameterized resources
- **xaffinity provider**: Now handles parameterized URIs by extracting path segments and passing to resource scripts
- **env.sh allowlist**: Added `AFFINITY_SESSION_CACHE` and `AFFINITY_SESSION_CACHE_TTL` to tool environment passthrough

## [1.5.1] - 2026-01-08

### Changed
- **mcp-bash framework 0.9.5**: Updated from 0.9.4 for native debug file detection and timeout fixes
- **Simplified env.sh**: Removed custom debug file detection; now uses native `server.d/.debug` (mcp-bash 0.9.5+)
- **Updated DEBUGGING.md**: Simplified to use native mcp-bash debug approach

### Fixed
- **set -e timeout bug**: Framework 0.9.5 fixes premature exit in `with_timeout` when grep finds no match

## [1.5.0] - 2026-01-07

### Added
- **xaffinity://data-model resource**: Conceptual guide to Affinity's data model (entities vs lists vs list entries)
- **relatedCommands**: CLI registry now includes related command suggestions for key commands
- **whenToUse**: CLI registry now includes usage guidance to help LLMs choose the right command
- **commands-metadata.json**: Manual metadata file for enriching auto-generated registry

### Changed
- **Registry generator**: Now merges manual metadata with CLI-generated data
- **Tool descriptions**: Enriched execute-read-command, execute-write-command, discover-commands, read-xaffinity-resource with domain context
- **xaffinity provider**: Added .md file support for static markdown resources

## [1.4.0] - 2026-01-06

### Removed
- **find-lists tool**: Thin wrapper, use `execute-read-command` with `list ls` instead
- **get-status-timeline tool**: Thin wrapper, use `execute-read-command` with `field-value-changes ls` instead

## [1.3.0] - 2026-01-06

### Removed
- **get-workflow-view tool**: Not useful for LLM agents (returns bulk data exceeding context limits). Use `get-list-workflow-config` for saved views, then `execute-read-command` with `list export --saved-view` for filtered results.

### Fixed
- **Large output handling**: Fixed "Argument list too long" error when tools process large outputs (>128KB)
  - `execute-read-command`: Use `--rawfile` for error output paths
  - `execute-write-command`: Use stdin piping and `--rawfile` for large outputs

## [1.2.3] - 2026-01-06

### Added
- **Debug mode**: Single flag `XAFFINITY_MCP_DEBUG=1` enables debug logging across all components
- **Debug file toggle**: XDG-compliant `~/.config/xaffinity-mcp/debug` file persists across reinstalls
- **Version banner**: Debug mode shows version info at startup (mcp, cli, mcp-bash versions)
- **Component prefixes**: All log messages include component and version: `[xaffinity:tool:1.2.3]`
- **Debugging guide**: New `docs/DEBUGGING.md` with troubleshooting instructions
- **Framework lockfile**: `mcp-bash.lock` pins framework version and commit hash (replaces `FRAMEWORK_VERSION`)

### Changed
- Logging functions now include version in prefix for easier debugging
- Debug cascade propagates to `MCPBASH_LOG_LEVEL=debug` and `XAFFINITY_DEBUG=true`
- Debug file uses XDG config location (`~/.config/xaffinity-mcp/debug`) instead of installation directory
- Requires mcp-bash >= 0.9.3 (provides `MCPBASH_FRAMEWORK_VERSION`, client identity logging, `mcp_run_with_progress`)
- CLI commands registry moved from `server.d/registry/` to `.registry/` (uses `MCPB_INCLUDE` for bundling)

## [1.2.2] - 2026-01-06

### Fixed
- **get-list-workflow-config**: Fixed list name/type extraction (was extracting from wrong JSON path `.name` instead of `.list.name`)
- **get-workflow-view**: Fixed data extraction using `.data.rows` instead of `.data.entries`, and correct field mapping for CLI output
- **execute-read-command**: Fixed empty argv causing spurious empty argument in command (empty `printf '%s\0'` output)

## [1.2.1] - 2026-01-06

### Fixed
- **CLI Gateway grep compatibility**: Fixed `grep` treating `--filter` and other flags as grep options (now uses `grep --`)
- **macOS date compatibility**: Fixed `date +%s%3N` failing on macOS (doesn't support milliseconds), falls back to seconds
- **Complete jq→jq_tool migration**: Fixed remaining bare `jq` calls in `mcp_emit_json` and lib scripts

## [1.2.0] - 2026-01-06

### Added
- **CLI Gateway tools exposed**: `discover-commands` and `execute-read-command` now available in MCP tool allowlist
- **CLI Gateway tools in full-access mode**: `execute-write-command` available when not in read-only mode

### Fixed
- **JSON processor compatibility**: Replaced all bare `jq` calls with `jq_tool` wrapper for gojq/bundle compatibility
- **Registry bundling**: Moved CLI commands registry from `.registry/` to `server.d/registry/` (now included in MCPB bundle)
- **Silent validation failures**: Improved error handling in CLI Gateway tools when registry not found

### Changed
- Registry generator scripts now output to `mcp/server.d/registry/commands.json`
- `lib/common.sh`: Registry path lookup now checks bundled location first, falls back to `.registry/`

## [1.1.1] - 2026-01-06

### Changed
- **Skill**: Added CLI Gateway tools documentation (discover-commands, execute-read-command, execute-write-command).
- **Skill**: Added destructive command confirmation flow (look up, ask, wait, execute with `confirm: true`).
- **Skill**: Clarified conversation-based confirmation works with all MCP clients regardless of elicitation support.

## [1.1.0] - 2026-01-05

### Added
- **CLI Gateway tools**: 3 new tools enabling full CLI access with minimal token overhead
  - `discover-commands`: Search CLI commands by keyword, returns compact text or JSON format
  - `execute-read-command`: Execute read-only CLI commands with retry and truncation support
  - `execute-write-command`: Execute write CLI commands with destructive command confirmation
- **Pre-generated command registry**: `mcp/.registry/commands.json` for zero-latency discovery
  - Registry validated at startup and in CI
  - Generator script: `tools/generate_cli_commands_registry.py` (requires CLI `--help --json` support)
- **CLI Gateway validation library**: `lib/cli-gateway.sh` with shared validation functions
  - `validate_registry()`: Verify registry exists and has valid structure
  - `validate_command()`: Check command exists in registry with correct category
  - `validate_argv()`: Validate arguments against per-command schema
  - `is_destructive()`: Check if command is destructive (from registry metadata)
  - `find_similar_command()`: Fuzzy matching for "Did you mean" suggestions on typos
- **Proactive output limiting**: Auto-inject `--limit` for commands that support it
- **CI validation**: Registry structure validation in GitHub Actions
- **API key health check**: Warns at startup if API key is not configured or invalid
- **Policy enforcement**: Runtime policy controls via environment variables
  - `AFFINITY_MCP_READ_ONLY=1`: Restrict to read-only operations
  - `AFFINITY_MCP_DISABLE_DESTRUCTIVE=1`: Block destructive commands entirely
- **Metrics logging**: `log_metric()` helper for structured metrics output
- **Post-execution cancellation**: Check `mcp_is_cancelled` after CLI execution

### Changed
- `lib/common.sh`: Added `jq_tool` wrapper, `REGISTRY_FILE` constant, and `log_metric()` helper
- `server.d/policy.sh`: Added CLI Gateway tools to read/write tool lists
- `server.d/env.sh`: Environment passthrough for policy variables via `MCPBASH_TOOL_ENV_ALLOWLIST`
- `confirmation_required` error now includes `example` field showing how to confirm
- `command_not_found` error now includes "Did you mean" hint for similar commands

### CLI Prerequisites (Implemented)
- CLI now supports `--help --json` for machine-readable help output
- All destructive commands (`*delete`) now support `--yes` flag for non-interactive execution
- Registry generated from live CLI via `tools/generate_cli_commands_registry.py`

## [1.0.5] - 2026-01-03

### Fixed
- **Typed argument helpers**: Fixed syntax for `mcp_args_bool` and `mcp_args_int` - require `--default`, `--min`, `--max` keyword arguments
- **Progress reporting**: Fixed `((current_step++))` failing with `set -e` when counter is 0 - use pre-increment `((++current_step))` instead
- **get-workflow-view**: Fixed CLI command (`list-entry export` → `list export`), positional arg for list ID, and `--saved-view` flag

## [1.0.4] - 2026-01-03

### Added
- **Progress reporting**: Long-running tools now report progress via mcp-bash SDK
  - `get-entity-dossier`: Reports progress for each data collection step
  - `get-relationship-insights`: Reports progress for connection analysis
  - `find-entities`: Reports progress for parallel search operations
  - Supports client cancellation via `mcp_is_cancelled` checks
- **Tool annotations**: All tools now include MCP 2025-03-26 annotations
  - `readOnlyHint`: Distinguishes read vs write operations
  - `destructiveHint`: Write tools marked as non-destructive (updates, not deletes)
  - `openWorldHint`: All tools interact with external Affinity API
  - `idempotentHint`: Status/field update tools are idempotent
- **Health checks**: Added `server.d/health-checks.sh` for startup validation
  - Verifies `xaffinity` CLI is available
- **Typed argument helpers**: Tools now use mcp-bash typed argument helpers
  - `mcp_args_bool` for boolean parameters with proper defaults
  - `mcp_args_int` for integer parameters with min/max validation
- **JSON tool compatibility**: All tools now use `MCPBASH_JSON_TOOL_BIN` (jq or gojq)
- **Automatic retry**: CLI calls use `mcp_with_retry` for transient failure handling (3 attempts, exponential backoff)
- **Debug mode**: Comprehensive logging for debugging MCP tool invocations
  - Set `MCPBASH_LOG_LEVEL=debug` or `XAFFINITY_DEBUG=true` to enable
  - Logs CLI command execution with exit codes and output sizes
  - Logs tool invocation parameters and completion stats
  - Auto-enables `MCPBASH_TOOL_STDERR_CAPTURE` in debug mode
- **lib/common.sh**: Added `xaffinity_log_*` helpers wrapping mcp-bash SDK logging
- **server.d/env.sh**: Documented debug mode configuration with examples

### Changed
- Tools now use structured logging via mcp-bash SDK (`mcp_log_debug`, `mcp_log_info`, etc.)
- CLI wrapper functions log command execution in debug mode (args redacted for security)
- Multi-step tools now use `mcp_progress` for visibility into operation status

## [1.0.3] - 2026-01-03

### Fixed
- **get-entity-dossier**: Fixed `relationship-strength get` (doesn't exist) → `relationship-strength ls --external-id`
- **get-entity-dossier**: Fixed entity data extraction path (`.data` → `.data.person`/`.data.company`/`.data.opportunity`)
- **get-entity-dossier**: Fixed interaction fetching - now queries all types (Affinity API limitation)
- **get-relationship-insights**: Fixed relationship-strength command usage
- **get-interactions**: Now queries all interaction types (email, meeting, call, chat-message) when no type specified, due to Affinity API limitation
- **get-interactions**: Fixed null participant handling in jq transformation
- **lib/common.sh**: Fixed `--quiet` flag positioning (must be global option before subcommand)

### Added
- Test harness using `mcp-bash run-tool` with dry-run validation and live API tests
- `.env.test` configuration pattern for private test data (gitignored)

## [1.0.2] - 2026-01-03

### Added
- **MCPB bundle support**: One-click installation via `.mcpb` bundles for Claude Desktop and other MCPB-compatible clients
- New `make mcpb` target to build MCPB bundles using mcp-bash-framework v0.9.0
- `mcpb.conf` configuration file for bundle metadata

### Changed
- Upgraded to mcp-bash-framework v0.9.0 (from 0.8.4)
- Updated Makefile with separate targets for MCPB bundles and Claude Code plugin ZIP

## [1.0.1] - 2026-01-03

### Changed
- ZIP-based plugin distribution for Claude Code compatibility
- Added COMPATIBILITY file for CLI version requirements
- Added FRAMEWORK_VERSION file for mcp-bash-framework version tracking
- Runtime CLI version validation on server startup

### Fixed
- Plugin bundle now includes all required MCP server files

## [1.0.0] - 2025-01-03

### Added
- Initial stable release of xaffinity MCP server
- Complete tool suite for Affinity CRM operations:
  - `find-entities`: Search for persons, organizations, and opportunities
  - `get-entity-details`: Retrieve detailed entity information with field values
  - `get-list-entries`: Query list entries with filtering and pagination
  - `export-list`: Export list data to CSV format
  - `workflow-analyze-entries`: Analyze list entries for workflow automation
  - `workflow-update-field`: Update field values on list entries
- Workflow prompts for guided CRM operations
- Session caching for improved performance
- Readonly mode support for safe operations

### CLI Compatibility
- Requires xaffinity CLI >= 0.6.0, < 1.0.0
- Uses JSON output format with `.data` wrapper
- Depends on `--session-cache`, `--readonly`, and `--output json` flags
