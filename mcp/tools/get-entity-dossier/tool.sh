#!/usr/bin/env bash
# tools/get-entity-dossier/tool.sh - Get comprehensive dossier for an entity
set -euo pipefail

source "${MCP_SDK:?}/tool-sdk.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/entity-types.sh"

# Helper: run CLI command with error logging (graceful degradation)
# Usage: _run_cli_graceful <default_value> <description> <command...>
# Returns default_value on failure, logs warning with actual error
_run_cli_graceful() {
    local default_value="$1"
    local description="$2"
    shift 2

    local stderr_file
    stderr_file=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f '$stderr_file'" RETURN

    local result
    if result=$("$@" 2>"$stderr_file"); then
        echo "$result"
    else
        local cli_error
        cli_error=$(cat "$stderr_file" 2>/dev/null | head -c 200 || echo "unknown error")
        xaffinity_log_warn "get-entity-dossier" "$description failed: $cli_error"
        echo "$default_value"
    fi
}

# Extract arguments
entity_json="$(mcp_args_get '.entity // null')"
entity_type="$(mcp_args_get '.entityType // null')"
entity_id="$(mcp_args_get '.entityId // null')"
include_interactions="$(mcp_args_bool '.includeInteractions' --default true)"
include_notes="$(mcp_args_bool '.includeNotes' --default true)"
include_lists="$(mcp_args_bool '.includeLists' --default true)"

# Parse entity reference
if [[ "$entity_json" != "null" ]]; then
    entity_type=$(echo "$entity_json" | jq_tool -r '.type')
    entity_id=$(echo "$entity_json" | jq_tool -r '.id')
elif [[ "$entity_id" == "null" || -z "$entity_id" ]]; then
    xaffinity_log_error "get-entity-dossier" "missing required entity reference"
    mcp_fail_invalid_args "Either entity object or entityId/entityType is required"
fi

validate_entity_type "$entity_type" || mcp_fail_invalid_args "Invalid entity type: $entity_type"

# Log tool invocation (entity_id is logged as it's needed for debugging)
xaffinity_log_debug "get-entity-dossier" "type=$entity_type id=$entity_id interactions=$include_interactions notes=$include_notes lists=$include_lists"

# Calculate total steps for progress
total_steps=2  # entity details + relationship strength
[[ "$include_interactions" == "true" ]] && ((++total_steps))
[[ "$include_notes" == "true" ]] && ((++total_steps))
[[ "$include_lists" == "true" ]] && ((++total_steps))
current_step=0

# Old CLI: run anyway; results carry a warning (see xaffinity_note_cli_version)
xaffinity_note_cli_version

# Fetch entity details
mcp_progress 0 "Fetching $entity_type details" "$total_steps"
cli_args=(--output json --quiet)
[[ -n "${AFFINITY_SESSION_CACHE:-}" ]] && cli_args+=(--session-cache "$AFFINITY_SESSION_CACHE")

# The entity itself must load: on failure, report the CLI's error instead of an empty dossier
# (an auth failure used to look like a successful dossier with no data).
entity_err=$(mktemp)
entity_exit=0
entity_raw=$(run_xaffinity_readonly "$entity_type" get "$entity_id" "${cli_args[@]}" 2>"$entity_err") || entity_exit=$?
if [[ $entity_exit -ne 0 ]]; then
    cli_error=$(xaffinity_cli_error_json "$entity_raw" "$(cat "$entity_err")" "$entity_exit")
    rm -f "$entity_err"
    xaffinity_result_error "$(jq_tool -n --argjson err "$cli_error" --argjson code "$entity_exit" \
        --arg what "$entity_type get $entity_id" '{type: "cli_error"} + $err + {message: ($err.message + (if $err.hint then " Hint: " + $err.hint else "" end)), exitCode: $code, failed: $what}')"
    exit 0
fi
rm -f "$entity_err"
entity_data=$(jq_tool -c --arg t "$entity_type" '.data[$t] // {}' <<<"$entity_raw")
((++current_step))

# Check for cancellation
if mcp_is_cancelled; then
    mcp_fail -32001 "Operation cancelled"
fi

# Get relationship strength if person
mcp_progress "$current_step" "Getting relationship strength" "$total_steps"
relationship_data="null"
if [[ "$entity_type" == "person" ]]; then
    relationship_data=$(_run_cli_graceful '{"data":{"relationshipStrengths":[]}}' "relationship-strength for $entity_id" run_xaffinity_readonly relationship-strength ls --external-id "$entity_id" "${cli_args[@]}" | jq_tool -c '.data.relationshipStrengths[0] // null')
fi
((++current_step))

# Get interactions if requested (Affinity API requires type, so query all types)
interactions="[]"
if [[ "$include_interactions" == "true" ]]; then
    if mcp_is_cancelled; then
        mcp_fail -32001 "Operation cancelled"
    fi
    mcp_progress "$current_step" "Fetching interactions" "$total_steps"

    # Use --type all to fetch all interaction types in one call (sorted by date descending)
    result=$(_run_cli_graceful '{"data":[]}' "interactions for $entity_type $entity_id" run_xaffinity_readonly interaction ls --"$entity_type"-id "$entity_id" --type all --days 365 --max-results 10 "${cli_args[@]}")
    interactions=$(echo "$result" | jq_tool -c '.data // []')
    ((++current_step))
fi

# Get notes if requested
notes="[]"
if [[ "$include_notes" == "true" ]]; then
    if mcp_is_cancelled; then
        mcp_fail -32001 "Operation cancelled"
    fi
    mcp_progress "$current_step" "Fetching notes" "$total_steps"
    notes=$(_run_cli_graceful '{"data":[]}' "notes for $entity_type $entity_id" run_xaffinity_readonly note ls --"$entity_type"-id "$entity_id" --max-results 10 "${cli_args[@]}" | jq_tool -c '.data // []')
    ((++current_step))
fi

# Get list memberships if requested
lists="[]"
if [[ "$include_lists" == "true" ]]; then
    if mcp_is_cancelled; then
        mcp_fail -32001 "Operation cancelled"
    fi
    mcp_progress "$current_step" "Fetching list memberships" "$total_steps"
    lists=$(_run_cli_graceful '{"data":{"entries":[]}}' "list entries for $entity_type $entity_id" run_xaffinity_readonly list-entry ls --"$entity_type"-id "$entity_id" "${cli_args[@]}" | jq_tool -c '.data.entries // []')
    ((++current_step))
fi

# Count collected data for logging
interactions_count=$(echo "$interactions" | jq_tool 'length')
notes_count=$(echo "$notes" | jq_tool 'length')
lists_count=$(echo "$lists" | jq_tool 'length')

xaffinity_log_debug "get-entity-dossier" "collected interactions=$interactions_count notes=$notes_count lists=$lists_count"

mcp_progress "$total_steps" "Building dossier" "$total_steps"

# Build dossier
xaffinity_emit_json "$(jq_tool -n \
    --arg entityType "$entity_type" \
    --argjson entityId "$entity_id" \
    --argjson entity "$entity_data" \
    --argjson relationship "$relationship_data" \
    --argjson interactions "$interactions" \
    --argjson notes "$notes" \
    --argjson lists "$lists" \
    '{
        entity: {type: $entityType, id: $entityId},
        details: $entity,
        relationshipStrength: $relationship,
        recentInteractions: $interactions,
        recentNotes: $notes,
        listMemberships: $lists
    }'
)"
