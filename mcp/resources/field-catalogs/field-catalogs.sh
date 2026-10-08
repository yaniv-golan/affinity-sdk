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

if [[ ! "${entityType}" =~ ^(company|companies|person|persons|people|opportunity|opportunities)$ ]]; then
    # A list id or name (URL-decoded by the provider). `field ls --list-id` resolves a name across
    # all lists (case-insensitive, ambiguity reported) and returns the fields in the same call.
    fields_output="$(xaffinity_resource_cli "${entityType}" field ls --list-id "${entityType}")" || exit $?
    listId="$(jq_tool -r '.command.modifiers.listId' <<<"${fields_output}")"

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
