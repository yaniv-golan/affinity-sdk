# Files, entity writes, and write-side gotchas

Read this before uploading/downloading files or creating/updating persons, companies, or fields.
All writes require an explicit user request (see SKILL.md).

## File commands

Files can be listed, downloaded, read, and uploaded for **companies**, **persons**, and
**opportunities**, as nested subcommands under `<entity> files`:

```bash
# List files attached to an entity (JSON: .data is a bare array)
xaffinity --readonly company files ls "domain:acme.com" --max-results 20 --json
xaffinity --readonly person files ls "email:alice@example.com" --max-results 20 --json
xaffinity --readonly opportunity files ls 12345 --max-results 20 --json

# Download files
xaffinity --readonly company files download "domain:acme.com" --output-dir ./downloads

# Read file content (with chunking support for large files)
xaffinity --readonly company files read "domain:acme.com" --file-id 67890 --json

# Upload files (write operation — requires explicit user request)
xaffinity company files upload 12345 --file ./document.pdf --json
```

Upload takes a numeric entity id and one `--file` per file. Each `data.uploads[]` row has
`fileId` (and `createdAt`) of the new file — use it with `files read --file-id` without a
follow-up `files ls`. `fileId` is `null` if the API didn't return the created file
(CLI <= 1.18.x never included it).

## `company create` / `person create` refuse duplicates by default

Since CLI 1.12.0, create refuses if an exact name/domain (companies) or email/full-name (persons) match exists:

- Exit code: 6
- Error type: `duplicate_exists`
- Payload: `error.details.existing.companyId` (or `personId`) — use this ID instead of creating a duplicate.
- For companies, `error.details.existing.isGlobal == true` indicates a global Affinity directory
  record — the hint points to `list entry add --company-id <id>` instead of creating a tenant-scoped copy.
- Pass `--allow-duplicate` to force-create when you genuinely want a distinct record with the same name.

## Field writes

- **`company field` / `person field` writes are all-or-nothing** (CLI 1.21.0+): every
  `--set`/`--set-json`/`--unset` of one command goes in one request; if Affinity rejects one value,
  nothing changes. JSON output: `created` (`{fieldId, name, value}`) and `cleared`. They use
  Affinity API version 2026-07-15+ (sent automatically; a pin to 2024-01-01 exits 2).
- **Enriched fields are writable** ("Phone Number", "Source of Introduction", "Current
  Organization", "Current Job Title", "Industry", "Location", "Description", etc.) with
  `person field --set` / `company field --set`, by field name or field ID. Not settable during
  `person create`; set them afterwards. Not writable: interaction fields and enriched dropdowns.
- **Ambiguous names:** on companies the same concept can exist under several enrichment providers
  (e.g. "Industry" for both the built-in enricher and Dealroom). The CLI raises `AmbiguousFieldError`
  with a table of candidate field IDs — copy one into the command.
- **Global organizations are read-only:** companies with `global: true` cannot be modified.
- **`--set` replaces, `--append` adds.** `--set` replaces a field's whole value (a rejected write
  leaves the old value). `--append` only works on multi-value fields; on a single-value field it
  exits 2 — use `--set`. Numbers are plain (`--set Amount 5`); locations are JSON objects
  (`--set HQ '{"city": "Paris", "country": "France"}'`). Requires CLI 1.18.0+: earlier versions
  could leave a field empty when a write failed.
- **One command = one all-or-nothing update** (CLI 1.19.0+, `list entry field` and
  `opportunity field`): all `--set`/`--set-json`/`--unset` succeed together or none applies. To
  update several fields consistently, put them in one command.
- **Hidden is not empty.** A field reported "hidden by Affinity" (restricted opportunity, API
  version 2026-07-15+) is masked: treat it as unknown and don't overwrite it.
