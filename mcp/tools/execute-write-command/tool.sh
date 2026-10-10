#!/usr/bin/env bash
# tools/execute-write-command/tool.sh - Execute a write CLI command (create, update, delete)
set -euo pipefail

source "${MCP_SDK:?}/tool-sdk.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/cli-gateway.sh"

# Validate registry (required for CLI Gateway tools)
if ! validate_registry; then
    # validate_registry already emitted mcp_result_error with details
    exit 0
fi

# Debug: Log args state to help diagnose intermittent "Command is required" errors
# (See issue: args_json was sometimes empty despite valid request)
args_raw="$(mcp_args_raw)"
args_len="${#args_raw}"
xaffinity_log_debug "execute-write-command" "args_len=$args_len"

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
        --hint "Pass command as a string, e.g. command: \"entry field set\"" \
        --data "$(jq_tool -n --argjson len "$args_len" --arg preview "$args_preview" '{argsLength: $len, argsPreview: $preview}')"
    exit 0
fi
argv_json="$(mcp_args_get '.argv // []')"
confirm="$(mcp_args_get '.confirm // false')"
dry_run="$(mcp_args_get '.dryRun // false')"

# Log tool invocation
xaffinity_log_debug "execute-write-command" "command='$command' confirm=$confirm dryRun=$dry_run"

# Track start time for latency metrics (macOS doesn't support %3N, fall back to seconds)
_get_time_ms() { local t; t=$(date +%s%3N 2>/dev/null); [[ "$t" =~ ^[0-9]+$ ]] && echo "$t" || echo "$(($(date +%s) * 1000))"; }
start_time_ms=$(_get_time_ms)

# Validate command is in registry with category=write
validate_command "$command" "write" || exit 0

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

# Block destructive commands entirely if policy disables them
if xaffinity_flag_enabled "${AFFINITY_MCP_DISABLE_DESTRUCTIVE:-}" && is_destructive "$command"; then
    mcp_error "destructive_disabled" "Destructive commands are disabled by policy (AFFINITY_MCP_DISABLE_DESTRUCTIVE=1)" \
        --hint "Contact your administrator to enable destructive operations"
    exit 0
fi

# Handle destructive operations with layered confirmation
if is_destructive "$command"; then
    # Check if --yes already in argv (user shouldn't provide it directly)
    # Note: ${argv[@]+...} syntax for Bash 3.2 compatibility with empty arrays
    has_yes=false
    for arg in ${argv[@]+"${argv[@]}"}; do
        [[ "$arg" == "--yes" || "$arg" == "-y" ]] && has_yes=true && break
    done
    if [[ "$has_yes" == "true" ]]; then
        mcp_error "validation_error" "--yes flag not allowed in argv; use confirm parameter instead" \
            --hint 'Remove --yes from argv and add "confirm": true to your request'
        exit 0
    fi

    # Verify command supports --yes flag before we try to append it
    supports_yes=$(jq_tool -r --arg cmd "$command" \
        '.commands[] | select(.name == $cmd) | .parameters["--yes"] // empty' \
        "$REGISTRY_FILE")
    if [[ -z "$supports_yes" ]]; then
        mcp_error "internal_error" "Destructive command $command does not support --yes flag; registry may be out of sync" \
            --hint "Report this issue - the command registry needs to be regenerated"
        exit 0
    fi

    if [[ "$confirm" == "true" ]]; then
        argv+=("--yes")
    elif [[ "${MCP_ELICIT_SUPPORTED:-0}" == "1" ]]; then
        response=$(mcp_elicit_confirm "Confirm: $command - This action cannot be undone.")
        action=$(printf '%s' "$response" | jq_tool -r '.action // "error"')
        case "$action" in
        accept)
            argv+=("--yes")
            ;;
        decline | cancel)
            # The user said no (or closed the dialog): not an error.
            mcp_result_success "$(jq_tool -n -c --arg a "$action" \
                '{result: null, cancelled: true, reason: (if $a == "decline" then "declined by the user" else "cancelled by the user" end)}')"
            exit 0
            ;;
        *)
            # No answer arrived (timeout or a client that dropped the request): say so, rather
            # than reporting it as the user's decision. Nothing was changed.
            mcp_error "confirmation_unavailable" "No confirmation was received for $command; nothing was changed" \
                --hint 'Retry, or pass "confirm": true if the user has already confirmed this action'
            exit 0
            ;;
        esac
    else
        # Build example showing how to confirm
        mcp_error "confirmation_required" "Destructive command requires confirm=true" \
            --hint 'Add "confirm": true to your request to proceed' \
            --data "$(jq_tool -n --arg cmd "$command" --argjson argv "$argv_json" '{example: {command: $cmd, argv: $argv, confirm: true}}')"
        exit 0
    fi
fi

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
    xaffinity_log_debug "execute-write-command" "Using progress forwarding for $command"
fi

# Report initial progress (only if not using CLI progress, to avoid duplicate 0%)
if [[ "$supports_progress" != "true" ]]; then
    mcp_progress 0 "Executing: ${command}"
fi

# Execute CLI (no retry for write commands to avoid duplicate side effects)
set +e
if [[ "$supports_progress" == "true" ]]; then
    # Use progress-aware execution with --stderr-file to capture CLI errors (mcp-bash 0.9.11+)
    run_xaffinity_with_progress --stderr-file "$stderr_file" "${cmd_args[@]:1}" >"$stdout_file"
    exit_code=$?
else
    # Standard execution
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
xaffinity_log_debug "execute-write-command" "exit_code=$exit_code output_bytes=${#stdout_content} latency_ms=$latency_ms"
log_metric "cli_command_latency_ms" "$latency_ms" "command=$command" "status=$([[ $exit_code -eq 0 ]] && echo 'success' || echo 'error')" "category=write"
log_metric "cli_command_output_bytes" "${#stdout_content}" "command=$command"

# Report completion progress (skip if CLI already emitted via progress forwarding)
if [[ "$supports_progress" != "true" ]]; then
    mcp_progress 100 "Complete"
fi

if [[ $exit_code -eq 0 ]]; then
    # Validate stdout is valid JSON before using --argjson
    if mcp_is_valid_json "$stdout_content"; then
        # Use stdin piping to avoid "Argument list too long" error with large outputs
        mcp_result_success "$(printf '%s' "$stdout_content" | jq_tool --argjson cmd "$cmd_json" \
              '{result: ., executed: $cmd}')"
    else
        # Use temp files to avoid "Argument list too long" error with large outputs
        printf '%s' "$stdout_content" > "$stdout_file"
        mcp_result_error "$(jq_tool -n --rawfile stdout "$stdout_file" \
              --argjson cmd "$cmd_json" \
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
