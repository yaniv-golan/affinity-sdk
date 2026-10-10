# Interactions (emails, meetings, calls, chats)

Read this before running `interaction ls` or `interaction create`.

## Listing interactions

Interactions require `--type` and exactly one entity ID (`--person-id`, `--company-id`, or `--opportunity-id`).
For emails, meetings, calls or chats **across the organization** (not one entity), use
`interaction feed --type email|meeting|call|chat-message` (see `history-and-activity.md`).

**Valid types:** `email`, `meeting`, `call`, `chat`, `chat-message`, `all`

**Date range:** defaults to **all time** if not specified. Use `--days` or `--after`/`--before` to limit.

```bash
# Recent interactions (recommended: use --days and --max-results)
xaffinity --readonly interaction ls --type all --company-id 123 \
  --days 90 --max-results 50 --json

# Specific date range (max 1 year per API call; auto-chunked for larger ranges)
xaffinity --readonly interaction ls --type email --person-id 456 \
  --after 2025-01-01 --before 2025-12-31 --max-results 100 --json

# --days and --after are mutually exclusive
# Dates without timezone suffix are interpreted as local time; use Z for UTC:
#   --after 2025-01-01T00:00:00Z
```

**JSON shape:** `interaction ls --json` returns `data` as a **bare array** of interactions —
read `.data[]`, not `.data.interactions`.

```bash
# Example: "Summarize my interactions with Acme in Q1"
CID=$(xaffinity --readonly company get domain:acme.com --json | jq -r '.data.company.id')

xaffinity --readonly interaction ls --type all --company-id "$CID" \
  --after 2025-01-01T00:00:00Z --before 2025-03-31T23:59:59Z \
  --max-results 200 --json \
  | jq '{
    company: "Acme",
    total: (.data | length),
    by_type: (.data | group_by(.type) | map({type: .[0].type, count: length}))
  }'
```

**WARNING:** Without `--days` or `--after`, the CLI fetches ALL interactions since 2010. Multi-year
ranges are auto-chunked into 365-day API calls. `--days 3650` = ~10 API calls per type. **Always use
`--days` or `--max-results` to bound the query.**

## Creating interactions (write — requires explicit user request)

Interactions require **both internal AND external** person IDs:

- **Internal**: a workspace user (team member). Find yours with `xaffinity whoami`.
- **External**: a contact (non-team-member person in your CRM).

```bash
# Create a meeting — use --include-me to auto-add your person ID
xaffinity interaction create --type meeting \
  --person-id EXTERNAL_CONTACT_ID --include-me \
  --content "Discussed partnership" --date 2025-06-15T14:00:00Z --json

# Without --include-me, specify all person IDs explicitly
xaffinity interaction create --type email \
  --person-id YOUR_PERSON_ID --person-id CONTACT_ID \
  --content "Follow-up email" --date 2025-06-15T14:00:00Z --json
```

**Common error:** forgetting to include an internal person ID causes a validation error. Use
`--include-me` to avoid this.

## Limits

- **Internal meetings are NOT in interactions.** The interactions API only shows meetings with
  **external** contacts. Workaround: `xaffinity --readonly note ls --person-id 123 --max-results 20 --json`
  and filter for `isMeeting: true` (note `ls` also returns a bare array in `.data`).
- **Interactions cannot be linked to companies/opportunities.** The UI's "Also add to..." feature has
  no API equivalent; the API only accepts person IDs as participants.
