#!/usr/bin/env bash
# tools/execute-read-command/tool.sh - Execute a read-only CLI command
set -euo pipefail

source "${MCP_SDK:?}/tool-sdk.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/cli-gateway.sh"

# Get the JSON array path for truncation based on command name
# CLI output structure varies:
#   Most commands: {"ok":true, "data": {"<plural>": [...]}, ...}
#   Some commands: {"ok":true, "data": [...], ...}  (direct array)
# Returns empty string if command doesn't have a known array to truncate
_get_array_path() {
    local cmd="$1"
    local entity action data_key

    # Parse command: "company ls", "list export", "list-entry ls", etc.
    read -r entity action <<< "$cmd"

    # Commands that return .data as direct array (changed in SDK v0.9.2)
    # See CHANGELOG.md and mcp/COMPATIBILITY for details
    case "$entity" in
        interaction|note)
            if [[ "$action" == "ls" ]]; then
                echo ".data"
                return
            fi
            ;;
    esac

    # Map entity to data key (plural form used in CLI JSON output)
    case "$entity" in
        company)            data_key="companies" ;;
        person)             data_key="persons" ;;
        opportunity)        data_key="opportunities" ;;
        list)
            # "list ls" vs "list export" vs "list entry ls"
            case "$action" in
                ls)         data_key="lists" ;;
                export)     data_key="rows" ;;  # CLI uses .data.rows, not .data.listEntries
                entry)      data_key="listEntries" ;;
                *)          data_key="" ;;
            esac
            ;;
        list-entry)         data_key="listEntries" ;;
        field)              data_key="fields" ;;
        field-value)        data_key="fieldValues" ;;
        field-value-changes) data_key="fieldValueChanges" ;;
        reminder)           data_key="reminders" ;;
        webhook)            data_key="webhooks" ;;
        saved-view)         data_key="savedViews" ;;
        entity-file)        data_key="entityFiles" ;;
        relationship-strength) data_key="relationshipStrengths" ;;
        *)                  data_key="" ;;
    esac

    # Only return path for ls/export commands (which return arrays)
    if [[ -n "$data_key" && ("$action" == "ls" || "$action" == "export" || "$entity" == "list-entry") ]]; then
        echo ".data.$data_key"
    fi
}

# Validate registry (required for CLI Gateway tools)
if ! validate_registry; then
    # validate_registry already emitted mcp_result_error with details
    exit 0
fi

# Debug: Log args state to help diagnose intermittent "Command is required" errors
# (See issue: args_json was sometimes empty despite valid request)
args_raw="$(mcp_args_raw)"
args_len="${#args_raw}"
xaffinity_log_debug "execute-read-command" "args_len=$args_len"

# Parse arguments using mcp-bash SDK
# Provide diagnostic info if command is missing
if ! command="$(mcp_args_get '.command')"; then
    mcp_error "validation_error" "Command is required" \
        --hint "Use discover-commands to find available CLI commands" \
        --data "$(jq_tool -n --argjson len "$args_len" '{argsLength: $len}')"
    exit 0
fi
if [[ -z "$command" || "$command" == "null" ]]; then
    # Extract first 200 chars of args for debugging (without secrets)
    args_preview="${args_raw:0:200}"
    mcp_error "validation_error" "Command is required (field missing or null)" \
        --hint "Pass command as a string, e.g. command: \"person get\"" \
        --data "$(jq_tool -n --argjson len "$args_len" --arg preview "$args_preview" '{argsLength: $len, argsPreview: $preview}')"
    exit 0
fi
argv_json="$(mcp_args_get '.argv // []')"
max_output_bytes="$(mcp_args_int '.maxOutputBytes' --default 50000)"
dry_run="$(mcp_args_get '.dryRun // false')"

# Log tool invocation
xaffinity_log_debug "execute-read-command" "command='$command' dryRun=$dry_run"

# Track start time for latency metrics (macOS doesn't support %3N, fall back to seconds)
_get_time_ms() { local t; t=$(date +%s%3N 2>/dev/null); [[ "$t" =~ ^[0-9]+$ ]] && echo "$t" || echo "$(($(date +%s) * 1000))"; }
start_time_ms=$(_get_time_ms)

# Validate command is in registry with category=read
validate_command "$command" "read" || exit 0

# Parse argv from JSON array
if [[ -z "$argv_json" ]]; then
    argv_json='[]'
fi
if ! printf '%s' "$argv_json" | jq_tool -e 'type == "array" and all(type == "string")' >/dev/null 2>&1; then
    mcp_error "validation_error" "argv must be an array of strings" \
        --hint 'Pass argv as: ["arg1", "--flag", "value"] or omit for commands without arguments'
    exit 0
fi

# Use NUL-delimited extraction to preserve newlines inside argument strings
# Note: Using while loop instead of mapfile for bash 3.x compatibility (macOS default)
argv=()
while IFS= read -r -d '' item; do
    argv+=("$item")
done < <(printf '%s' "$argv_json" | jq_tool -jr '.[] + "\u0000"')

# Silently filter out --json if passed (tool appends it automatically anyway)
# Note: ${argv[@]+...} syntax for Bash 3.2 compatibility with empty arrays
filtered_argv=()
for arg in ${argv[@]+"${argv[@]}"}; do
    [[ "$arg" != "--json" ]] && filtered_argv+=("$arg")
done
argv=("${filtered_argv[@]+"${filtered_argv[@]}"}")

# Validate argv against per-command schema
# Note: ${argv[@]+...} syntax for Bash 3.2 compatibility with empty arrays
validate_argv "$command" ${argv[@]+"${argv[@]}"} || exit 0

# Apply proactive limiting - inject/cap --limit for commands that support it
# Note: Using while loop instead of mapfile for bash 3.x compatibility (macOS default)
new_argv=()
while IFS= read -r -d '' item; do
    new_argv+=("$item")
done < <(apply_limit_cap "$command" ${argv[@]+"${argv[@]}"})
argv=("${new_argv[@]+"${new_argv[@]}"}")

# Build command array safely
# Note: --session-cache is a global option that must come BEFORE the subcommand
# Use XAFFINITY_CLI for full path (set by common.sh for Cowork compatibility)
declare -a cmd_args=("${XAFFINITY_CLI:-xaffinity}")
[[ -n "${AFFINITY_SESSION_CACHE:-}" ]] && cmd_args+=("--session-cache" "${AFFINITY_SESSION_CACHE}")
read -ra parts <<< "$command"
cmd_args+=("${parts[@]}")
# Note: ${argv[@]+...} syntax for Bash 3.2 compatibility with empty arrays
cmd_args+=(${argv[@]+"${argv[@]}"})
cmd_args+=("--json")

# Check for cancellation before execution
if mcp_is_cancelled; then
    mcp_error "cancelled" "Operation cancelled by client"
    exit 0
fi

# Dry run: return what would be executed
if [[ "$dry_run" == "true" ]]; then
    mcp_result_success "$(jq_tool -n --args '$ARGS.positional' -- "${cmd_args[@]}" | \
        jq_tool '{result: null, dryRun: true, command: .}')"
    exit 0
fi

# Execute and capture stdout/stderr separately
stdout_file=$(mktemp)
stderr_file=$(mktemp)
trap 'rm -f "$stdout_file" "$stderr_file"' EXIT

# Check if command supports progress and MCP progress is available
supports_progress=false
if command_supports_progress "$command" && [[ -n "${MCP_PROGRESS_STREAM:-}" ]]; then
    supports_progress=true
    xaffinity_log_debug "execute-read-command" "Using progress forwarding for $command"
fi

# Report initial progress (only if not using CLI progress, to avoid duplicate 0%)
if [[ "$supports_progress" != "true" ]]; then
    mcp_progress 0 "Executing: ${command}"
fi

# Execute CLI with retry for transient failures (read commands are safe to retry)
set +e
if [[ "$supports_progress" == "true" ]]; then
    # Use progress-aware execution with --stderr-file to capture CLI errors (mcp-bash 0.9.11+)
    run_xaffinity_with_progress --stderr-file "$stderr_file" "${cmd_args[@]:1}" >"$stdout_file"
    exit_code=$?
else
    # One call: the SDK retries transient HTTP failures itself; rerunning a permanent failure
    # (exit 3 auth, 4 not found) only concatenated the CLI's JSON errors.
    "${cmd_args[@]}" >"$stdout_file" 2>"$stderr_file"
    exit_code=$?
fi
set -e

stdout_content=$(cat "$stdout_file")
stderr_content=$(cat "$stderr_file")

# Build executed command array for transparency
cmd_json=$(jq_tool -n --args '$ARGS.positional' -- "${cmd_args[@]}")

# Check for cancellation after execution
if mcp_is_cancelled; then
    mcp_error "cancelled" "Operation cancelled by client"
    exit 0
fi

# Calculate latency
end_time_ms=$(_get_time_ms)
latency_ms=$((end_time_ms - start_time_ms))

# Log result and metrics
xaffinity_log_debug "execute-read-command" "exit_code=$exit_code output_bytes=${#stdout_content} latency_ms=$latency_ms"
log_metric "cli_command_latency_ms" "$latency_ms" "command=$command" "status=$([[ $exit_code -eq 0 ]] && echo 'success' || echo 'error')" "category=read"
log_metric "cli_command_output_bytes" "${#stdout_content}" "command=$command"

# Report completion progress (skip if CLI already emitted via progress forwarding)
if [[ "$supports_progress" != "true" ]]; then
    mcp_progress 100 "Complete"
fi

if [[ $exit_code -eq 0 ]]; then
    # Validate stdout is valid JSON before using --argjson
    if mcp_is_valid_json "$stdout_content"; then
        # Apply semantic truncation with command-specific array path
        array_path=$(_get_array_path "$command")
        truncate_args=("$stdout_content" "$max_output_bytes")
        [[ -n "$array_path" ]] && truncate_args+=(--array-path "$array_path")
        if truncated_result=$(mcp_json_truncate "${truncate_args[@]}"); then
            mcp_result_success "$(printf '%s' "$truncated_result" | jq_tool --argjson cmd "$cmd_json" '. + {executed: $cmd}')"
        else
            # Truncation failed (output too large, can't truncate safely)
            mcp_result_error "$(printf '%s' "$truncated_result" | jq_tool --argjson cmd "$cmd_json" '.error + {executed: $cmd}')"
        fi
    else
        # Use temp files to avoid "Argument list too long" error with large outputs
        printf '%s' "$stdout_content" > "$stdout_file"
        mcp_result_error "$(jq_tool -n --rawfile stdout "$stdout_file" --argjson cmd "$cmd_json" \
            '{type: "invalid_json_output", message: "CLI returned non-JSON output", output: $stdout, executed: $cmd}')"
    fi
else
    # CLI exited with error: its message, type and hint (JSON on stdout), else stderr / exit code
    printf '%s' "$stdout_content" > "$stdout_file"
    cli_error=$(xaffinity_cli_error_json "$stdout_content" "$stderr_content" "$exit_code")
    mcp_result_error "$(jq_tool -n --argjson err "$cli_error" --rawfile stdout "$stdout_file" \
          --argjson cmd "$cmd_json" --argjson code "$exit_code" \
          '{type: "cli_error"} + $err + {message: ($err.message + (if $err.hint then " Hint: " + $err.hint else "" end)), output: $stdout, exitCode: $code, executed: $cmd}')"
    exit 0
fi
