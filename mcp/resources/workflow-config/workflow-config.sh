#!/usr/bin/env bash
# resources/workflow-config/workflow-config.sh - Return workflow configuration for a list
# Includes status field options and saved views
set -euo pipefail

source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"

listRef="${1:-}"
if [[ -z "${listRef}" ]]; then
    echo "Usage: workflow-config.sh <listId|listName>" >&2
    exit 4
fi

# List details (resolves a name) and saved views, then the fields of the resolved list.
list_output="$(xaffinity_resource_cli "${listRef}" list get "${listRef}")" || exit $?
listId="$(jq_tool -r '.data.list.id' <<<"${list_output}")"
fields_output="$(xaffinity_resource_cli "${listRef}" field ls --list-id "${listId}")" || exit $?

# Extract list info, saved views, and status-like fields (dropdowns)
jq_tool -c --argjson fields "$(jq_tool -c '.data.fields // []' <<<"${fields_output}")" '
    .data.list as $list |
    .data.savedViews as $savedViews |
    {
        listId: $list.id,
        listName: $list.name,
        listType: $list.type,
        savedViews: ($savedViews // [] | map({id, name, type})),
        statusFields: (
            $fields
            | map(select(.valueType == "dropdown" or .valueType == "ranked-dropdown"
                or .valueType == "status-dropdown" or .valueType == "status"))
            | map({
                id: .id,
                name: .name,
                valueType: .valueType,
                options: (.dropdownOptions // [])
            })
        ),
        note: "Use saved view names with --saved-view, or filter by status field values with --filter '\''FieldName=\"Value\"'\''"
    }
' <<<"${list_output}"
