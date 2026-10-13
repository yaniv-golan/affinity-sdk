# CSV Export Guide

This guide shows you how to export data from the Affinity CLI to CSV format for use in spreadsheets, data analysis tools, or other applications.

## Quick Start

Export data to CSV using the `--csv` flag with shell redirection:

```bash
# Export all people to CSV
xaffinity person ls --all --csv > people.csv

# Export all companies to CSV
xaffinity company ls --all --csv > companies.csv

# Export all opportunities to CSV
xaffinity opportunity ls --all --csv > opportunities.csv

# Export list entries with custom fields
xaffinity list export 12345 --all --csv > entries.csv
```

## Excel Compatibility

If you're opening CSV files in Microsoft Excel, use the `--csv-bom` flag to ensure proper character encoding:

```bash
xaffinity person ls --all --csv --csv-bom > people.csv
```

This adds a UTF-8 Byte Order Mark (BOM) to the file, which helps Excel correctly display special characters, accents, and non-English text.

## Commands with Built-in CSV Support

| Command | CSV Flag | Example |
|---------|----------|---------|
| `person ls` | `--csv` | `xaffinity person ls --all --csv > people.csv` |
| `company ls` | `--csv` | `xaffinity company ls --all --csv > companies.csv` |
| `opportunity ls` | `--csv` | `xaffinity opportunity ls --all --csv > opps.csv` |
| `list export` | `--csv` | `xaffinity list export 12345 --all --csv > entries.csv` |

**Note:** The `--csv` flag outputs CSV to stdout. Use shell redirection (`>`) to save to a file, or pipe (`|`) to other tools.

## CSV Column Reference

### person ls

The `person ls` command exports these columns:

- **id** - Person ID
- **name** - Full name (first + last)
- **primaryEmail** - Primary email address
- **emails** - All email addresses (semicolon-separated)

Example output:
```csv
id,name,primaryEmail,emails
123,Alice Smith,alice@example.com,alice@example.com
456,Bob Jones,bob@company.com,bob@company.com; bjones@company.com
```

### company ls

The `company ls` command exports these columns:

- **id** - Company ID
- **name** - Company name
- **domain** - Primary domain
- **domains** - All domains (semicolon-separated)

Example output:
```csv
id,name,domain,domains
100,Acme Corp,acme.com,acme.com
101,Beta Inc,beta.com,beta.com; beta.co
```

### opportunity ls

The `opportunity ls` command exports these columns:

- **id** - Opportunity ID
- **name** - Opportunity name
- **listId** - List ID the opportunity belongs to

Example output:
```csv
id,name,listId
10,Series A,41780
11,Seed Round,41780
```

### list export

The `list export` command is the most powerful CSV export option. It includes:

- Entity ID and name
- All custom field values
- List entry metadata

See `xaffinity list export --help` for details.

## Advanced: Using jq for Custom CSV Exports

For commands without built-in `--csv` support, you can use `jq` to convert JSON output to CSV format.

### Basic Pattern

```bash
xaffinity <command> --json --all | \
  jq -r '.data.<entity>[] | [.field1, .field2, .field3] | @csv' > output.csv
```

The `-r` flag is crucial - it outputs raw strings instead of JSON-quoted values.

### Examples

**Export list entries with field values:**
```bash
xaffinity list export "My List" --json | \
  jq -r '.data.entries[] | [.id, .fields["Status"], .fields["Owner"]] | @csv'
```

**Export notes:**
```bash
xaffinity note ls --person-id 123 --json --all | \
  jq -r '.data[] | [.id, .content, .createdAt] | @csv'
```

**Export interactions:**
```bash
# Use --days for relative date range (auto-chunks if > 1 year)
xaffinity interaction ls --type meeting --person-id 123 --days 365 --json | \
  jq -r '.data[] | [.id, .date, .type] | @csv'

# Or use built-in --csv flag
xaffinity interaction ls --type meeting --person-id 123 --days 365 --csv
```

**Add headers manually:**
```bash
xaffinity person ls --json --all | \
  jq -r '["ID","Name","Email"],
         (.data.persons[] | [.id, .name, .primaryEmail]) | @csv'
```

**Handle arrays (join with semicolons):**
```bash
xaffinity person ls --json --all | \
  jq -r '.data.persons[] | [.id, .name, (.emails | join("; "))] | @csv'
```

**Extract from nested structures:**
```bash
xaffinity person get 123 --json | \
  jq -r '.data.person.fields[] | [.fieldId, .value, .listEntryId] | @csv'
```

## JSON Data Structure Reference

Most CLI commands return data in this structure:

```json
{
  "data": {
    "<entity-plural>": [ ... ]
  }
}
```

Some commands (`note ls`, `interaction ls`) return data as a direct array:

```json
{
  "data": [ ... ]
}
```

Entity paths for jq:
- **persons**: `.data.persons[]`
- **companies**: `.data.companies[]`
- **opportunities**: `.data.opportunities[]`
- **fieldValues**: `.data.fieldValues[]`
- **fieldValueChanges**: `.data.fieldValueChanges[]`
- **notes**: `.data[]` (direct array)
- **interactions**: `.data[]` (direct array)
- **tasks**: `.data.tasks[]`

## Troubleshooting

### Empty Output

Make sure you're accessing the correct JSON path:

```bash
# Wrong - missing .data
xaffinity person ls --json | jq '.persons'

# Correct
xaffinity person ls --json | jq '.data.persons'
```

### CSV shows JSON strings

Use the `-r` flag with jq:

```bash
# Wrong - produces "[1,\"Alice\"]"
xaffinity person ls --json | jq '.data.persons[] | [.id, .name] | @csv'

# Correct - produces "1,Alice"
xaffinity person ls --json | jq -r '.data.persons[] | [.id, .name] | @csv'
```

### Special characters broken in Excel

Use the `--csv-bom` flag:

```bash
xaffinity person ls --all --csv --csv-bom > people.csv
```

### Empty CSV file has no headers

This is expected behavior when there are no results. The CLI cannot determine column names without data. If you need headers even for empty results, use `list export` which has a known schema.

## Tips and Best Practices

### 1. Use --all for complete exports

For large datasets, use `--all` to fetch all pages:

```bash
# Fetch all pages
xaffinity person ls --all --csv > people.csv

# Or limit results
xaffinity person ls --max-results 100 --csv > people.csv
```

### 2. Combine with filters

**Filtering on custom fields (recommended):**

When filtering on fields, use `--filter`. Affinity's search applies the conditions on fields before sending the data, and the whole filter is checked again locally:

```bash
# Efficient: Affinity filters on the field
xaffinity person ls --filter 'Department = "Sales"' --all --csv > sales-people.csv
```

You can also combine `--filter` with jq for additional client-side processing:

```bash
# Filter server-side, then process with jq
xaffinity person ls --filter 'Department = "Sales"' --json --all | \
  jq -r '.data.persons[] | [.id, .name, .primaryEmail] | @csv'
```

**Filtering on built-in properties:**

Built-in properties like `type`, `firstName` or `primaryEmail` are checked locally. On `person ls` and `company ls` a `--filter` with only such conditions is refused (it would mean reading every record); combine them with a field condition, use `--query TERM` for a name or email search, or use jq:

```bash
# Less efficient: Client-side filtering on built-in 'type' property
# (downloads all data, then filters locally)
xaffinity person ls --json --all | \
  jq -r '.data.persons[] | select(.type == "internal") | [.id, .name] | @csv'
```

**Combining both approaches:**

For complex scenarios, combine a field filter (applied by Affinity) with built-in property filtering in jq:

```bash
# Filter on custom field server-side, then filter on type client-side
xaffinity person ls --filter 'Department = "Sales"' --json --all | \
  jq -r '.data.persons[] | select(.type == "internal") | [.id, .name] | @csv'
```

### 3. Save queries as scripts

Create reusable export scripts:

```bash
#!/bin/bash
# export-pipeline.sh

xaffinity person ls --all --csv --csv-bom > people.csv
xaffinity company ls --all --csv --csv-bom > companies.csv
xaffinity opportunity ls --all --csv --csv-bom > opportunities.csv

echo "Export complete!"
```

### 4. Schedule regular exports

Use cron or task scheduler for automated exports:

```bash
# Daily export at 2 AM
0 2 * * * /path/to/export-pipeline.sh
```

### 5. Handle large datasets

For very large exports, monitor progress:

```bash
# The CLI will show API call counts for large exports
xaffinity list export 12345 --all --csv > large-export.csv
```

### 6. Composable with UNIX tools

Since CSV goes to stdout, you can pipe to other tools:

```bash
# Count rows
xaffinity person ls --all --csv | wc -l

# Preview first 10 rows
xaffinity person ls --all --csv | head -10

# Filter with awk
xaffinity list export 123 --all --csv | awk -F',' '$3 > 1000'
```

## Getting Help

- Run `xaffinity <command> --help` to see all available options
- Check `xaffinity --version` to ensure you have the latest version
- Report issues at https://github.com/yaniv-golan/affinity-sdk/issues

## Related Documentation

- [CLI Scripting Guide](../cli/scripting.md) - JSON output and automation
- [CLI Commands Reference](../cli/commands.md) - Complete command documentation
- [Field Values Guide](./field-values.md) - Working with custom fields
- [Filtering Guide](./filtering.md) - Filtering with Affinity's search and locally
