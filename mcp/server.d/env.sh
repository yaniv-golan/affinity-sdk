#!/usr/bin/env bash
# server.d/env.sh - Environment setup for xaffinity MCP Server
#
# Sourcing behavior: env.sh is expected to be sourced once at MCP server startup.
# The update check function uses _XAFFINITY_UPDATE_CHECK_RAN guard to handle
# the case where env.sh might be re-sourced within the same process.
#
# ==============================================================================
# Tool Environment Passthrough
# ==============================================================================
# Allow environment variables to be passed through to tool scripts.
# By default, mcp-bash only passes MCP*/MCPBASH* variables for security.
# We use "allowlist" mode to pass additional variables.
#
# Allowlist variables:
#   AFFINITY_API_KEY                 - Affinity API key (from MCPB user config)
#   XAFFINITY_CLI                    - Path override for xaffinity CLI (user-set)
#   AFFINITY_MCP_READ_ONLY=1         - Restrict to read-only operations
#   AFFINITY_MCP_DISABLE_DESTRUCTIVE=1 - Block destructive commands entirely
#   XAFFINITY_DEBUG                  - Enable debug logging
#   AFFINITY_TRACE                   - Enable CLI command tracing
#   AFFINITY_SESSION_CACHE           - Cache directory (auto-configured below)
#   AFFINITY_SESSION_CACHE_TTL       - Cache TTL in seconds (default: 600)
#   XAFFINITY_CLI_PATTERN            - CLI invocation pattern from check-key (includes --dotenv if needed)
#   XAFFINITY_CLI_VERSION            - CLI version for feature detection
#   AFFINITY_API_VERSION             - Affinity V2 API version to pin (e.g. 2026-09-17; default: key default)
#   AFFINITY_PROFILE                 - xaffinity config profile to use (default: [default])
#
# Note: XAFFINITY_CLI allows users to override the CLI path. The actual CLI
# detection happens at runtime in lib/common.sh (not here) because env.sh
# variables don't reliably pass through to tool subprocesses in all contexts.

export MCPBASH_TOOL_ENV_MODE="allowlist"
export MCPBASH_TOOL_ENV_ALLOWLIST="AFFINITY_API_KEY,XAFFINITY_CLI,XAFFINITY_CLI_PATTERN,XAFFINITY_CLI_VERSION,AFFINITY_MCP_READ_ONLY,AFFINITY_MCP_DISABLE_DESTRUCTIVE,XAFFINITY_DEBUG,AFFINITY_TRACE,AFFINITY_SESSION_CACHE,AFFINITY_SESSION_CACHE_TTL,AFFINITY_API_VERSION,AFFINITY_PROFILE"

# ==============================================================================
# Debug Mode Configuration
# ==============================================================================
# Enable debug logging by creating server.d/.debug file (mcp-bash 0.9.5+ native):
#
#   touch server.d/.debug   # Enable debug logging
#   rm server.d/.debug      # Disable debug logging
#
# Environment variable MCPBASH_LOG_LEVEL=debug takes precedence if set.
# See mcp-bash docs/DEBUGGING.md for details.
# ==============================================================================

# ==============================================================================
# Boolean Translation for user_config
# ==============================================================================
# user_config passes "true"/"false" strings, but existing code checks for "1"
# Translate for backwards compatibility with env-var based configuration

if [[ "${AFFINITY_MCP_READ_ONLY:-}" == "true" ]]; then
    export AFFINITY_MCP_READ_ONLY="1"
elif [[ "${AFFINITY_MCP_READ_ONLY:-}" == "false" ]]; then
    unset AFFINITY_MCP_READ_ONLY
fi

if [[ "${AFFINITY_MCP_DISABLE_DESTRUCTIVE:-}" == "true" ]]; then
    export AFFINITY_MCP_DISABLE_DESTRUCTIVE="1"
elif [[ "${AFFINITY_MCP_DISABLE_DESTRUCTIVE:-}" == "false" ]]; then
    unset AFFINITY_MCP_DISABLE_DESTRUCTIVE
fi

# ==============================================================================
# Session Cache Configuration
# ==============================================================================

# Create session cache on server startup
if [[ -z "${AFFINITY_SESSION_CACHE:-}" ]]; then
    export AFFINITY_SESSION_CACHE="${TMPDIR:-/tmp}/xaffinity-mcp-session-$$"
    mkdir -p "${AFFINITY_SESSION_CACHE}"
    chmod 700 "${AFFINITY_SESSION_CACHE}"
fi

# Default cache TTL (10 minutes for MCP context)
export AFFINITY_SESSION_CACHE_TTL="${AFFINITY_SESSION_CACHE_TTL:-600}"

# ==============================================================================
# Debug Mode Auto-Configuration
# ==============================================================================
# When MCPBASH_LOG_LEVEL=debug (or "true" from user_config), enable xaffinity debug features

# Translate boolean "true" from user_config to "debug" level
if [[ "${MCPBASH_LOG_LEVEL:-}" == "true" ]]; then
    export MCPBASH_LOG_LEVEL="debug"
fi

if [[ "${MCPBASH_LOG_LEVEL:-info}" == "debug" ]]; then
    # Enable xaffinity-specific debug logging
    export XAFFINITY_DEBUG="${XAFFINITY_DEBUG:-true}"

    # Enable CLI command tracing
    export AFFINITY_TRACE="1"

    # Capture tool stderr for debugging (mcp-bash feature)
    export MCPBASH_TOOL_STDERR_CAPTURE="${MCPBASH_TOOL_STDERR_CAPTURE:-true}"

    # Increase stderr tail limit for more context in errors
    export MCPBASH_TOOL_STDERR_TAIL_LIMIT="${MCPBASH_TOOL_STDERR_TAIL_LIMIT:-8192}"

    # Log raw argument payloads for debugging parsing issues
    export MCPBASH_DEBUG_PAYLOADS="${MCPBASH_DEBUG_PAYLOADS:-1}"
fi

# ==============================================================================
# Debug Log Directory (optional)
# ==============================================================================
# If XAFFINITY_DEBUG_LOG_DIR is set, write debug logs to files for analysis

if [[ -n "${XAFFINITY_DEBUG_LOG_DIR:-}" ]]; then
    mkdir -p "${XAFFINITY_DEBUG_LOG_DIR}" 2>/dev/null || true
    export XAFFINITY_DEBUG_LOG_FILE="${XAFFINITY_DEBUG_LOG_DIR}/xaffinity-mcp-$(date +%Y%m%d-%H%M%S).log"
fi

# ==============================================================================
# CLI Update Check (non-blocking, informational only)
# ==============================================================================
# IMPORTANT: This block uses if/fi structure to avoid early returns that would
# skip other env.sh setup. Never use `return` here.

_xaffinity_update_check() {
    # DEFENSIVE GUARD: Prevent repeated execution if env.sh is re-sourced within same process.
    # NOTE: This guard only works within a single shell process. If mcp-bash spawns a new
    # process per tool call (which Milestone 0 should rule out), warnings would repeat.
    # The throttle file provides cross-process protection for background spawns.
    if [[ "${_XAFFINITY_UPDATE_CHECK_RAN:-}" == "1" ]]; then
        return 0
    fi
    export _XAFFINITY_UPDATE_CHECK_RAN=1

    # Respect user opt-out via environment variable
    if [[ "${XAFFINITY_NO_UPDATE_CHECK:-}" == "1" ]]; then
        return 0
    fi

    # Detect JSON tool (needed for parsing update info)
    local json_tool="${MCPBASH_JSON_TOOL:-}"
    if [[ -z "$json_tool" ]]; then
        if command -v jq &>/dev/null; then
            json_tool="jq"
        elif command -v gojq &>/dev/null; then
            json_tool="gojq"
        fi
    fi

    # No JSON tool: skip entirely to respect potential config opt-out
    if [[ -z "$json_tool" ]]; then
        return 0
    fi

    # Verify xaffinity CLI is available (may not be on PATH if env.sh runs early)
    if ! command -v xaffinity &>/dev/null; then
        return 0  # CLI not available yet, skip silently
    fi

    # ALWAYS read cache and show warnings (not throttled)
    # IMPORTANT: --json is a root-level option, must come before subcommand
    local update_info cache_stale="" update_enabled=""
    update_info=$(xaffinity --json config update-check --status 2>/dev/null) || true

    if [[ -n "$update_info" ]]; then
        # Check if user has disabled updates via config file
        # NOTE: CLI always includes update_check_enabled and update_notify_mode in --status output.
        # Use 'if . == null' pattern because jq's '//' treats false as falsy
        update_enabled=$("$json_tool" -r '.data.update_check_enabled | if . == null then true else . end' <<< "$update_info")
        if [[ "$update_enabled" != "true" ]]; then
            return 0  # Respect config opt-out (no warnings, no spawn)
        fi

        # Check notify mode (never = suppress warnings)
        local notify_mode
        notify_mode=$("$json_tool" -r '.data.update_notify_mode // "interactive"' <<< "$update_info")

        local update_available
        update_available=$("$json_tool" -r '.data.update_available | if . == null then false else . end' <<< "$update_info")
        cache_stale=$("$json_tool" -r '.data.cache_stale | if . == null then true else . end' <<< "$update_info")

        # Show warning on EVERY startup if update available and notify mode allows it
        # This is NOT throttled - users see the warning each time they restart
        if [[ "$update_available" == "true" ]] && [[ "$notify_mode" != "never" ]]; then
            local current latest upgrade_cmd
            current=$("$json_tool" -r '.data.current_version // "unknown"' <<< "$update_info")
            latest=$("$json_tool" -r '.data.latest_version // "unknown"' <<< "$update_info")
            upgrade_cmd=$("$json_tool" -r '.data.upgrade_command // "pip install --upgrade affinity-sdk[cli]"' <<< "$update_info")
            # Use echo to stderr since mcp_log_warn may not be available in env.sh
            echo "[xaffinity] CLI update available: $current → $latest" >&2
            echo "[xaffinity] Run: $upgrade_cmd" >&2
        fi
    else
        # CLI call failed or returned empty - cannot verify opt-out status.
        # Mark cache as stale but leave update_enabled empty to prevent spawning
        # without knowing user preferences. This is the safe default.
        cache_stale="true"
    fi

    # THROTTLE only applies to background spawn, not to warnings above
    # Check if we should spawn a background refresh
    if [[ "$cache_stale" != "true" ]] || [[ "$update_enabled" != "true" ]]; then
        return 0  # Nothing to refresh or user disabled
    fi

    # Use CLI's state directory for throttle file to stay aligned with CLI's cache.
    # The CLI stores update_check.json in its state_dir; we store our throttle timestamp
    # in the same location to avoid mismatched paths if user customizes CLI paths.
    # NOTE: state_dir is added to --status output as part of this implementation.
    local throttle_dir
    local cli_state_dir
    cli_state_dir=$("$json_tool" -r '.data.state_dir // empty' <<< "$update_info" 2>/dev/null)
    if [[ -n "$cli_state_dir" ]]; then
        throttle_dir="$cli_state_dir"
    else
        # Fallback to standard XDG path (matches CLI default)
        throttle_dir="${XDG_STATE_HOME:-$HOME/.local/state}/xaffinity"
    fi
    local throttle_file="${throttle_dir}/.mcp_update_check_timestamp"
    mkdir -p "$throttle_dir" 2>/dev/null || true

    local do_spawn=0
    if [[ -f "$throttle_file" ]]; then
        local last_spawn now
        last_spawn=$(cat "$throttle_file" 2>/dev/null || echo 0)
        now=$(date +%s)
        # 24-hour throttle to match CLI worker's check interval
        if (( now - last_spawn >= 86400 )); then
            do_spawn=1
        fi
    else
        do_spawn=1
    fi

    # Spawn background refresh if throttle allows
    if [[ "$do_spawn" == "1" ]]; then
        if xaffinity config update-check --background 2>/dev/null; then
            date +%s > "$throttle_file" 2>/dev/null || true
        fi
    fi
}

# Call the function (contained scope, no early returns leak out)
# Note: Don't suppress stderr here - warnings need to be visible to users
_xaffinity_update_check || true
unset -f _xaffinity_update_check
