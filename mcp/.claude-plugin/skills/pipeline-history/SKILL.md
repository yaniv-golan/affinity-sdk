---
name: pipeline-history
description: >
  Guides the multi-step workflow of exporting deals and fetching field change history
  via MCP tools to analyze pipeline progression over time.
when_to_use: >
  Use when analyzing deal pipeline history, stage transitions, funnel conversion,
  time-in-stage metrics, deal velocity, or stalled deals. Triggers on "pipeline funnel",
  "how long in stage", "deal progression", "stage duration", "deal history",
  "conversion rate by stage".
---

# Pipeline History Analysis

## When to Use

- Historical pipeline analysis (how deals progressed over time)
- Funnel conversion rates (what % of deals reached each stage)
- Stage duration analysis (how long deals stay in each stage)
- Deal progression timelines (when did a deal move from X to Y)

## Workflow

All commands use `execute-read-command`. Do not pass `--json` in argv — JSON output is automatic.

### Step 1: Identify the Status Field

```
execute-read-command(command: "field ls", argv: ["--list-id", "YOUR_LIST_NAME"])
```

Look for a dropdown field tracking deal stages (typically named "Status", "Stage", or "Pipeline Stage"). Note its `fieldId` (e.g., `field-260415`).

### Step 2: Export Current State

```
execute-read-command(command: "list export", argv: ["YOUR_LIST_NAME", "--field", "Status", "--max-results", "50"])
```

JSON output key is `data.rows`. Each row contains `listEntryId`, `entityId`, `entityName`, and current field values.

### Step 3: Estimate API Cost (REQUIRED)

```
execute-read-command(command: "field history-bulk", argv: ["<field-id>", "--list-id", "YOUR_LIST_NAME", "--dry-run"])
```

Check `estimatedApiCalls` before proceeding. Each list entry = 1 API call.

### Step 4: Fetch History

```
execute-read-command(command: "field history-bulk", argv: ["<field-id>", "--list-id", "YOUR_LIST_NAME", "--max-results", "50"])
```

For all entries (only after confirming cost via dry-run is acceptable):

```
execute-read-command(command: "field history-bulk", argv: ["<field-id>", "--list-id", "YOUR_LIST_NAME", "--all"])
```

### Step 5: Analyze

Each row has: `id`, `fieldId`, `entityId`, `listEntryId`, `entityName`, `actionType`, `value`, `changedAt`, `changerName`.

**Reconstruct transitions:** Sort events per entity by `changedAt`. Each row's `value` is the value AT that point. Compare consecutive rows to derive old→new transitions.

**Common analyses:**
- **Funnel conversion:** Count distinct entities that ever had `value` = each stage
- **Time-in-stage:** Duration between consecutive `changedAt` timestamps for the same entity
- **Stage transition matrix:** Group by (previous value → current value) pairs
- **Stalled deals:** Entities with no change event in the last N days

## Gotchas

- `actionType` meanings: `create` (initial value set), `update` (value changed), `delete` (value cleared)
- Each row has a single `value`, not `oldValue`/`newValue` — derive transitions by sorting
- `changedAt` is UTC
- Some deals may have no history (field set at creation, never changed — only a `create` event)
- With `--list-entry-ids`, `entityName` is `null` — join with `list export` data if names needed
- Always `--dry-run` first to estimate API calls on large lists
