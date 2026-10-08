#!/usr/bin/env bash
# resources/saved-views/saved-views.sh - Return saved views for a list
# Called by the xaffinity.sh provider with a list ID or name as argument (already URL-decoded).
set -euo pipefail

source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"

listRef="${1:-}"
if [[ -z "${listRef}" ]]; then
    echo "Usage: saved-views.sh <listId|listName>" >&2
    exit 4
fi

# The argument is data: it goes to the CLI as one argument and never into the jq program.
output="$(xaffinity_resource_cli "${listRef}" list get "${listRef}")" || exit $?

jq_tool -c '
    {
        listId: .data.list.id,
        savedViews: (.data.savedViews // [] | map({id, name, type})),
        note: "Saved view names only. Filter criteria are not available via API. Use --saved-view with exact name, or --filter for field-based filtering."
    }
' <<<"${output}"
