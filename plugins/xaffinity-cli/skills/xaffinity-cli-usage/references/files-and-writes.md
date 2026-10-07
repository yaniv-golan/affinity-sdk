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

- **"Current Organization" is read-only via API.** It is derived from enrichment data and email
  domain. "Current Job Title" can be updated after person creation using `field update`. Neither can
  be set during `person create`.
- **Most enriched fields are writable** ("Phone Number", "Source of Introduction", "Industry",
  "Location", "Description", etc.). `person field --set` / `company field --set` / `field update`
  accept the field name or its field ID.
- **Ambiguous names:** on companies the same concept can exist under several enrichment providers
  (e.g. "Industry" for both the built-in enricher and Dealroom). The CLI raises `AmbiguousFieldError`
  with a table of candidate field IDs — copy one into the command.
- **Derived-only enriched fields** like "Current Organization" raise `EnrichedFieldNotWritableError`
  (exit 2) with a clear message instead of silently no-op'ing.
- **Global organizations are read-only:** companies with `global: true` cannot be modified.
- **`--set` replaces, `--append` adds.** `--set` replaces a field's whole value (a rejected write
  leaves the old value). `--append` only works on multi-value fields; on a single-value field it
  exits 2 — use `--set`. Numbers are plain (`--set Amount 5`); locations are JSON objects
  (`--set HQ '{"city": "Paris", "country": "France"}'`). Requires CLI 1.18.0+: earlier versions
  could leave a field empty when a write failed.
