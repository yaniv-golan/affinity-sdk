#!/usr/bin/env bash
# tools/query/tool.sh - Execute a structured JSON query against Affinity data
set -euo pipefail

source "${MCP_SDK:?}/tool-sdk.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"

# Parse arguments using mcp-bash SDK
query_json="$(mcp_args_require '.query' 'Query is required')"

# Debug: Trace query_json (enable with XAFFINITY_MCP_DEBUG=1 or MCPBASH_LOG_LEVEL=debug)
xaffinity_log_debug "query" "query_json length: ${#query_json}"
xaffinity_log_debug "query" "query_json first 200 chars: ${query_json:0:200}"

# Write query to temp file (avoids stdin pipeline issues in some environments like Cowork VMs)
query_file=$(mktemp)
trap 'rm -f "$query_file"' EXIT
printf '%s' "$query_json" > "$query_file"

dry_run="$(mcp_args_get '.dryRun // false')"
max_records="$(mcp_args_int '.maxRecords' --default 1000)"
user_timeout_secs="$(mcp_args_int '.timeout' --default 0)"  # 0 = auto-calculate
max_output_bytes="$(mcp_args_int '.maxOutputBytes' --default 50000)"
format="$(mcp_args_get '.format // "toon"')"
cursor="$(mcp_args_get '.cursor // ""')"

# Check for common mistake: timeout inside query object instead of top-level
# If user put timeout inside query and not at top level, use it with a warning
if [[ $user_timeout_secs -eq 0 ]]; then
    nested_timeout=$(jq_tool -r '.timeout // 0' "$query_file" 2>/dev/null || echo "0")
    if [[ "$nested_timeout" =~ ^[0-9]+$ ]] && [[ $nested_timeout -gt 0 ]]; then
        # Convert milliseconds to seconds if value looks like ms (> 1000)
        if [[ $nested_timeout -gt 1000 ]]; then
            user_timeout_secs=$((nested_timeout / 1000))
            mcp_log_warn "query" "timeout was inside query object (should be top-level). Interpreted ${nested_timeout}ms as ${user_timeout_secs}s."
        else
            user_timeout_secs=$nested_timeout
            mcp_log_warn "query" "timeout was inside query object - move it to top level (peer to 'query', not nested inside it)."
        fi
    fi
fi

# Validate format parameter
case "$format" in
  toon|markdown|json|jsonl|csv) ;;
  *) mcp_error "validation_error" "format must be toon, markdown, json, jsonl, or csv" \
       --hint "Use toon (default, token-efficient) or json (full structure)"; exit 0 ;;
esac

# Track start time for latency metrics
_get_time_ms() { local t; t=$(date +%s%3N 2>/dev/null); [[ "$t" =~ ^[0-9]+$ ]] && echo "$t" || echo "$(($(date +%s) * 1000))"; }
start_time_ms=$(_get_time_ms)

# Validate query has required 'from' field
if ! jq_tool -e '.from' "$query_file" >/dev/null 2>&1; then
    mcp_error "validation_error" 'Query must have a "from" field specifying the entity type' \
        --hint "Valid types: persons, companies, opportunities, listEntries, interactions, notes"
    exit 0
fi

# Old CLI: run anyway; results carry a warning (see xaffinity_note_cli_version)
xaffinity_note_cli_version

# Cap max_records at 10000 for safety
if [[ $max_records -gt 10000 ]]; then
    max_records=10000
fi

# Calculate dynamic timeout based on estimated API calls
# - Run a quick dry-run to get estimatedApiCalls
# - Calculate timeout as ~2 seconds per API call (generous for rate limits)
# - User-specified timeout overrides if larger
# - Minimum 30 seconds for simple queries
calc_dynamic_timeout() {
    local min_timeout=30
    local per_call_secs=2

    # Quick dry-run to get estimate (suppress warnings, stderr)
    local dry_output
    local session_cache_opt=""
    [[ -n "${AFFINITY_SESSION_CACHE:-}" ]] && session_cache_opt="--session-cache ${AFFINITY_SESSION_CACHE}"
    dry_output=$("${XAFFINITY_CLI:-xaffinity}" query --file "$query_file" --dry-run --max-records "$max_records" --output json $session_cache_opt 2>/dev/null) || return 1

    # Parse estimatedApiCalls from dry-run output
    local estimated_calls
    estimated_calls=$(printf '%s' "$dry_output" | jq_tool -r '.execution.estimatedApiCalls // 1')

    # Calculate timeout: max(min_timeout, estimated_calls * per_call_secs)
    local calc_timeout=$((estimated_calls * per_call_secs))
    [[ $calc_timeout -lt $min_timeout ]] && calc_timeout=$min_timeout

    echo "$calc_timeout"
}

# Determine effective timeout
if [[ $user_timeout_secs -gt 0 ]]; then
    # User specified timeout - use it
    timeout_secs=$user_timeout_secs
elif [[ "$dry_run" == "true" ]]; then
    # Dry-run mode - short timeout is fine
    timeout_secs=30
else
    # Calculate dynamic timeout based on estimated API calls
    # Emit progress to extend MCP timeout during planning phase
    mcp_progress 0 "Planning query..."
    if dynamic_timeout=$(calc_dynamic_timeout 2>/dev/null); then
        timeout_secs=$dynamic_timeout
    else
        # Fallback if dry-run fails
        timeout_secs=120
    fi
fi

# Log tool invocation (timeout_mode: user=specified, auto=calculated, dryrun=fixed)
timeout_mode=$([[ $user_timeout_secs -gt 0 ]] && echo "user" || ([[ "$dry_run" == "true" ]] && echo "dryrun" || echo "auto"))
xaffinity_log_debug "query" "dryRun=$dry_run maxRecords=$max_records timeout=$timeout_secs (${timeout_mode})"

# Create temp files for stdout/stderr capture
stdout_file=$(mktemp)
stderr_file=$(mktemp)
trap 'rm -f "$query_file" "$stdout_file" "$stderr_file"' EXIT

# Build command for transparency logging (actual execution uses run_xaffinity_with_progress)
declare -a cmd_display=("xaffinity" "query" "--file" "<query.json>" "--max-records" "$max_records" "--timeout" "$timeout_secs" "--output" "$format")
[[ "$dry_run" == "true" ]] && cmd_display+=("--dry-run")
# CLI handles truncation via --max-output-bytes for all formats including JSON
cmd_display+=("--max-output-bytes" "$max_output_bytes")
# Pass cursor to CLI if provided
[[ -n "$cursor" ]] && cmd_display+=("--cursor" "$cursor")

# Check for cancellation before execution
if mcp_is_cancelled; then
    mcp_error "cancelled" "Operation cancelled by client"
    exit 0
fi

# Execute CLI with progress forwarding
# - Uses run_xaffinity_with_progress to forward NDJSON progress to MCP clients
# - CLI emits step-by-step progress (fetch, filter, aggregate) when stderr is not a TTY
# - CLI emits cursor to stderr as NDJSON {"type": "cursor", ...} when truncated
# - --file reads query from temp file (more reliable than stdin in VM environments)
# - --stderr-file captures non-progress stderr for error reporting (mcp-bash 0.9.11+)
# - --max-output-bytes for all formats (CLI handles truncation, returns exit code 100 if truncated)
# - --session-cache enables cross-invocation reuse of list/field metadata (must come before subcommand)
set +e
run_xaffinity_with_progress --stderr-file "$stderr_file" \
    $([[ -n "${AFFINITY_SESSION_CACHE:-}" ]] && echo "--session-cache" "${AFFINITY_SESSION_CACHE}") \
    query --file "$query_file" --max-records "$max_records" --timeout "$timeout_secs" --output "$format" \
    $([[ "$dry_run" == "true" ]] && echo "--dry-run") \
    --max-output-bytes "$max_output_bytes" \
    $([[ -n "$cursor" ]] && echo "--cursor" "$cursor") >"$stdout_file"
exit_code=$?
set -e

# Handle truncation exit code (100 = success but output was truncated)
was_truncated=false
if [[ $exit_code -eq 100 ]]; then
    was_truncated=true
    exit_code=0  # Treat as success for MCP response
fi

stdout_content=$(cat "$stdout_file")

# Extract cursor from stderr using type field (consistent with progress pattern)
# CLI emits: {"type": "cursor", "cursor": "eyJ...", "mode": "streaming"}
next_cursor=""
cursor_mode=""
cursor_line=$(jq_tool -c 'select(.type == "cursor")' "$stderr_file" 2>/dev/null | head -1 || true)
if [[ -n "$cursor_line" ]]; then
    next_cursor=$(echo "$cursor_line" | jq_tool -r '.cursor // empty')
    cursor_mode=$(echo "$cursor_line" | jq_tool -r '.mode // empty')
fi

# Build executed command for transparency (without the actual query for brevity)
cmd_json=$(jq_tool -n --args '$ARGS.positional' -- "${cmd_display[@]}")

# Check for cancellation after execution
if mcp_is_cancelled; then
    mcp_error "cancelled" "Operation cancelled by client"
    exit 0
fi

# Calculate latency
end_time_ms=$(_get_time_ms)
latency_ms=$((end_time_ms - start_time_ms))

# Log result and metrics
xaffinity_log_debug "query" "exit_code=$exit_code output_bytes=${#stdout_content} latency_ms=$latency_ms"
log_metric "query_latency_ms" "$latency_ms" "dryRun=$dry_run" "status=$([[ $exit_code -eq 0 ]] && echo 'success' || echo 'error')"
log_metric "query_output_bytes" "${#stdout_content}" "dryRun=$dry_run"

# Note: CLI emits 100% progress via NDJSON when query completes (forwarded by run_xaffinity_with_progress)

if [[ $exit_code -eq 0 ]]; then
    # Format-specific response handling
    if [[ "$format" == "json" ]]; then
        # JSON format: CLI handles truncation, MCP just passes through and adds metadata
        if mcp_is_valid_json "$stdout_content"; then
            # Build response based on truncation state
            if [[ "$was_truncated" == "true" ]]; then
                # Truncated: add truncated flag and cursor if present
                if [[ -n "$next_cursor" ]]; then
                    xaffinity_result_success "$(printf '%s' "$stdout_content" | jq_tool \
                        --argjson cmd "$cmd_json" \
                        --arg cursor "$next_cursor" \
                        --arg mode "$cursor_mode" \
                        '. + {executed: $cmd, truncated: true, nextCursor: $cursor, _cursorMode: $mode}')"
                else
                    xaffinity_result_success "$(printf '%s' "$stdout_content" | jq_tool \
                        --argjson cmd "$cmd_json" \
                        '. + {executed: $cmd, truncated: true}')"
                fi
            else
                # Not truncated: just add executed command (NO truncated: false)
                xaffinity_result_success "$(printf '%s' "$stdout_content" | jq_tool \
                    --argjson cmd "$cmd_json" \
                    '. + {executed: $cmd}')"
            fi
        else
            # Invalid JSON - shouldn't happen for --output json
            printf '%s' "$stdout_content" > "$stdout_file"
            xaffinity_result_error "$(jq_tool -n --rawfile stdout "$stdout_file" --argjson cmd "$cmd_json" \
                '{type: "invalid_json_output", message: "CLI returned non-JSON output", output: $stdout, executed: $cmd}')"
        fi
    else
        # Non-JSON formats (toon, markdown, csv, jsonl): wrap text in MCP response structure
        # CLI already handled truncation via --max-output-bytes (exit code 100 if truncated)
        # Use jq -Rs to safely escape the text content as a JSON string
        printf '%s' "$stdout_content" > "$stdout_file"

        if [[ "$was_truncated" == "true" ]]; then
            # Add nextCursor if present (for resumable truncated responses)
            if [[ -n "$next_cursor" ]]; then
                xaffinity_result_success "$(jq_tool -n --rawfile text "$stdout_file" --argjson cmd "$cmd_json" \
                    --arg cursor "$next_cursor" --arg mode "$cursor_mode" \
                    '{content: [{type: "text", text: $text}], truncated: true, nextCursor: $cursor, _cursorMode: $mode, executed: $cmd}')"
            else
                xaffinity_result_success "$(jq_tool -n --rawfile text "$stdout_file" --argjson cmd "$cmd_json" \
                    '{content: [{type: "text", text: $text}], truncated: true, executed: $cmd}')"
            fi
        else
            xaffinity_result_success "$(jq_tool -n --rawfile text "$stdout_file" --argjson cmd "$cmd_json" \
                '{content: [{type: "text", text: $text}], truncated: false, executed: $cmd}')"
        fi
    fi
else
    # Error handling - same for all formats. The CLI's error is JSON on stdout; stderr carries
    # progress lines, so it is only the fallback.
    stderr_text=$(jq_tool -R -r 'select((fromjson? | type) != "object")' "$stderr_file" 2>/dev/null || true)
    # CLI exited with error: its message, type and hint (JSON on stdout), else stderr / exit code
    printf '%s' "$stdout_content" > "$stdout_file"
    cli_error=$(xaffinity_cli_error_json "$stdout_content" "$stderr_text" "$exit_code")
    xaffinity_result_error "$(jq_tool -n --argjson err "$cli_error" --rawfile stdout "$stdout_file" \
          --argjson cmd "$cmd_json" --argjson code "$exit_code" \
          '{type: "cli_error"} + $err + {message: ($err.message + (if $err.hint then " Hint: " + $err.hint else "" end)), output: $stdout, exitCode: $code, executed: $cmd}')"
fi
