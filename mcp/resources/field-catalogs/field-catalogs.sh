#!/usr/bin/env bash
# resources/field-catalogs/field-catalogs.sh - Return field catalog for an entity type or list
# entityType can be: a listId (numeric), list name, "company", "person", or "opportunity"
set -euo pipefail

source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"

entityType="${1:-}"
if [[ -z "${entityType}" ]]; then
    echo "Usage: field-catalogs.sh <entityType|listId|listName>" >&2
    exit 4
fi

# Clients that expand {entityType} percent-encode it ("Deal%20Pipeline").
entityType="$(printf '%b' "${entityType//%/\\x}")"

if [[ ! "${entityType}" =~ ^(company|companies|person|persons|people|opportunity|opportunities)$ ]]; then
    # A list id or name. `field ls --list-id` resolves a name across all lists (case-insensitive,
    # ambiguity reported) and returns the list's fields in the same call.
    fields_output="$(run_xaffinity_readonly field ls --list-id "${entityType}" --output json --quiet \
        ${AFFINITY_SESSION_CACHE:+--session-cache "$AFFINITY_SESSION_CACHE"} 2>/dev/null)" || true
    error_type="$(jq_tool -r '.error.type // empty' <<<"${fields_output}" 2>/dev/null || true)"
    listId="$(jq_tool -r '.command.modifiers.listId // empty' <<<"${fields_output}" 2>/dev/null || true)"
    if [[ -n "${error_type}" || -z "${listId}" ]]; then
        case "${error_type}" in
            not_found)
                echo "Unknown entity type or list name: ${entityType}. Use a list ID (numeric), list name, 'company', 'person', or 'opportunity'." >&2
                exit 4
                ;;
            ambiguous_resolution)
                candidates="$(jq_tool -r '[.error.details.matches[]? | "\(.name) (\(.listId))"] | join(", ")' <<<"${fields_output}" 2>/dev/null || true)"
                echo "List name '${entityType}' matches several lists: ${candidates}. Use the list ID." >&2
                exit 4
                ;;
            *)
                message="$(jq_tool -r '.error.message // empty' <<<"${fields_output}" 2>/dev/null || true)"
                echo "Failed to get fields for list ${entityType}: ${message:-${fields_output:0:200}}" >&2
                exit 5
                ;;
        esac
    fi

    jq_tool -c --arg listId "${listId}" '
        {
            entityType: "list",
            listId: ($listId | tonumber),
            fields: (.data.fields // [] | map({
                id: .id,
                name: .name,
                valueType: .valueType,
                enrichmentSource: .enrichmentSource,
                dropdownOptions: (if .dropdownOptions then .dropdownOptions else null end)
            }) | map(if .dropdownOptions == null then del(.dropdownOptions) else . end)),
            note: "Use field names in --filter expressions: --filter '\''FieldName=\"Value\"'\''"
        }
    ' <<<"${fields_output}"
else
    # Global entity type - return fixed schema info
    case "${entityType}" in
        company|companies)
            jq_tool -n '{
                entityType: "company",
                fields: [
                    {name: "id", type: "integer", description: "Unique company ID"},
                    {name: "name", type: "string", description: "Company name"},
                    {name: "domain", type: "string", description: "Company domain/website"},
                    {name: "domains", type: "array", description: "All associated domains"},
                    {name: "global", type: "boolean", description: "Whether company is global (not list-specific)"}
                ],
                note: "Global company fields are fixed. List-specific fields are on list entries - use field-catalogs/{listId} for those."
            }'
            ;;
        person|persons|people)
            jq_tool -n '{
                entityType: "person",
                fields: [
                    {name: "id", type: "integer", description: "Unique person ID"},
                    {name: "firstName", type: "string", description: "First name"},
                    {name: "lastName", type: "string", description: "Last name"},
                    {name: "primaryEmail", type: "string", description: "Primary email address"},
                    {name: "emails", type: "array", description: "All email addresses"}
                ],
                note: "Global person fields are fixed. List-specific fields are on list entries - use field-catalogs/{listId} for those."
            }'
            ;;
        opportunity|opportunities)
            jq_tool -n '{
                entityType: "opportunity",
                note: "Opportunities are list-specific. Use field-catalogs/{listId} or field-catalogs/{listName} with a pipeline list to see opportunity fields."
            }'
            ;;
    esac
fi
