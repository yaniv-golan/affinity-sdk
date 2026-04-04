#!/usr/bin/env bash
# xaffinity-mcp.sh - Main launcher for xaffinity MCP Server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MCPBASH_PROJECT_ROOT="${SCRIPT_DIR}"

# Source shared environment setup (debug mode, session cache, etc.)
# NOTE: In bundle mode, run-server.sh does NOT source env.sh.
# This file is sourced only when xaffinity-mcp.sh is the entry point:
# - Development mode (running xaffinity-mcp.sh directly)
# - Provides backward-compat boolean→debug normalization for older mcp-bash
# shellcheck source=server.d/env.sh
source "${SCRIPT_DIR}/server.d/env.sh"

# Set XAFFINITY_MCP_DEBUG based on MCPBASH_LOG_LEVEL (set by env.sh)
if [[ "${MCPBASH_LOG_LEVEL:-}" == "debug" ]]; then
    export XAFFINITY_MCP_DEBUG=1
fi

# Cache version at startup (for debug banner and logging)
export XAFFINITY_MCP_VERSION=$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo "unknown")

# Framework version - source from lockfile, allow env override
# shellcheck source=mcp-bash.lock
source "${SCRIPT_DIR}/mcp-bash.lock"
FRAMEWORK_VERSION="${MCPBASH_VERSION:-unknown}"

# Framework location precedence:
# 1. Vendored: ${SCRIPT_DIR}/.mcp-bash/bin/mcp-bash (committed to repo)
# 2. MCPBASH_HOME env override
# 3. XDG default: ${XDG_DATA_HOME:-$HOME/.local/share}/mcp-bash
# 4. Fallback: ${HOME}/.local/bin/mcp-bash

find_framework() {
    if [[ -x "${SCRIPT_DIR}/.mcp-bash/bin/mcp-bash" ]]; then
        echo "${SCRIPT_DIR}/.mcp-bash/bin/mcp-bash"
    elif [[ -n "${MCPBASH_HOME:-}" && -x "${MCPBASH_HOME}/bin/mcp-bash" ]]; then
        echo "${MCPBASH_HOME}/bin/mcp-bash"
    elif [[ -x "${XDG_DATA_HOME:-$HOME/.local/share}/mcp-bash/bin/mcp-bash" ]]; then
        echo "${XDG_DATA_HOME:-$HOME/.local/share}/mcp-bash/bin/mcp-bash"
    elif [[ -x "${HOME}/.local/bin/mcp-bash" ]]; then
        echo "${HOME}/.local/bin/mcp-bash"
    fi
}

# Minimal JSON processor for pre-framework startup checks
# Tries: jq (system), gojq (bundled), gojq (system)
_json() {
    if command -v jq >/dev/null 2>&1; then
        jq "$@"
    elif [[ -x "${SCRIPT_DIR}/.mcp-bash/bin/gojq" ]]; then
        "${SCRIPT_DIR}/.mcp-bash/bin/gojq" "$@"
    elif command -v gojq >/dev/null 2>&1; then
        gojq "$@"
    else
        echo "Error: No JSON processor found (jq or gojq required)" >&2
        return 1
    fi
}

# Handle special commands
case "${1:-}" in
    doctor)
        # Run diagnostics (pass --fix to auto-repair)
        shift
        FRAMEWORK=$(find_framework)
        if [[ -n "$FRAMEWORK" ]]; then
            exec "$FRAMEWORK" doctor "$@"
        else
            echo "Framework not installed. Run: $0 install" >&2
            exit 1
        fi
        ;;
    validate)
        # Validate server configuration
        shift
        FRAMEWORK=$(find_framework)
        exec "$FRAMEWORK" validate --project-root "${SCRIPT_DIR}" "$@"
        ;;
esac

# Source CLI compatibility requirements and verify CLI version
source "${SCRIPT_DIR}/COMPATIBILITY"

CLI_VERSION=$(xaffinity version --output json 2>/dev/null | _json -r '.data.version // empty') || true

if [[ -z "$CLI_VERSION" ]]; then
    echo "Error: Could not detect xaffinity CLI version." >&2
    echo "Ensure xaffinity is installed: pip install affinity-sdk" >&2
    exit 1
fi

# Version comparison function (portable: works on macOS and Linux)
# Returns 0 (success) if v1 >= v2
version_gte() {
    local v1="$1" v2="$2"
    # v1 >= v2 means v2 should come first (or equal) when sorted
    [ "$v2" = "$(printf '%s\n%s' "$v1" "$v2" | sort -V | head -1)" ] 2>/dev/null && return 0
    # Fallback for systems without sort -V (some minimal containers, old BSD)
    local IFS='.'
    local i v1_parts=($v1) v2_parts=($v2)
    for ((i=0; i<${#v2_parts[@]}; i++)); do
        [[ ${v1_parts[i]:-0} -lt ${v2_parts[i]:-0} ]] && return 1
        [[ ${v1_parts[i]:-0} -gt ${v2_parts[i]:-0} ]] && return 0
    done
    return 0
}

if ! version_gte "$CLI_VERSION" "$CLI_MIN_VERSION"; then
    echo "Error: xaffinity CLI version $CLI_VERSION is too old." >&2
    echo "MCP server requires CLI >= $CLI_MIN_VERSION" >&2
    echo "Run: pip install --upgrade affinity-sdk" >&2
    exit 1
fi

# Check API key configuration using xaffinity config check-key
# This detects keychain, dotenv, or env var configuration and returns the CLI pattern to use
check_key_output=$(xaffinity config check-key --json 2>/dev/null) || {
    echo "Error: Affinity API key not configured." >&2
    echo "" >&2
    echo "Run this command to set up your API key:" >&2
    echo "  xaffinity config setup-key" >&2
    echo "" >&2
    echo "Get your API key from: Affinity → Settings → API → Generate New Key" >&2
    exit 1
}

# Parse check-key output (data is nested under .data in JSON response)
configured=$(echo "$check_key_output" | _json -r '.data.configured // false')
if [[ "$configured" != "true" ]]; then
    echo "Error: Affinity API key not configured." >&2
    echo "" >&2
    echo "Run this command to set up your API key:" >&2
    echo "  xaffinity config setup-key" >&2
    echo "" >&2
    echo "Get your API key from: Affinity → Settings → API → Generate New Key" >&2
    exit 1
fi

# Extract the CLI pattern from check-key output
# Example patterns:
#   "xaffinity --dotenv --readonly <command> --json"  (dotenv mode)
#   "xaffinity --readonly <command> --json"           (keychain mode)
# Export for tool scripts to use when invoking xaffinity
export XAFFINITY_CLI_PATTERN=$(echo "$check_key_output" | _json -r '.data.pattern')
# Export CLI version for progress capability checks
export XAFFINITY_CLI_VERSION="$CLI_VERSION"

# Find and run framework
FRAMEWORK=$(find_framework)
if [[ -z "$FRAMEWORK" ]]; then
    echo "MCP Bash Framework not found. Expected vendored runtime at .mcp-bash/" >&2
    echo "Run: cd $(dirname "$0") && mcp-bash vendor" >&2
    exit 1
fi

# Tool allowlist (read-only vs full access)
# CLI Gateway tools provide dynamic command discovery and execution
AFFINITY_MCP_TOOLS_CLI_GATEWAY="discover-commands execute-read-command"
AFFINITY_MCP_TOOLS_READONLY="get-entity-dossier read-xaffinity-resource query ${AFFINITY_MCP_TOOLS_CLI_GATEWAY}"
AFFINITY_MCP_TOOLS_ALL="${AFFINITY_MCP_TOOLS_READONLY} execute-write-command"

if [[ "${AFFINITY_MCP_READ_ONLY:-}" == "1" ]]; then
    export MCPBASH_TOOL_ALLOWLIST="${AFFINITY_MCP_TOOLS_READONLY}"
else
    export MCPBASH_TOOL_ALLOWLIST="${AFFINITY_MCP_TOOLS_ALL}"
fi

# Print debug banner if debug mode enabled
if [[ "${XAFFINITY_MCP_DEBUG:-}" == "1" ]]; then
    echo "[xaffinity-mcp:${XAFFINITY_MCP_VERSION}] Debug mode enabled" >&2
    echo "[xaffinity-mcp:${XAFFINITY_MCP_VERSION}] Versions: mcp=${XAFFINITY_MCP_VERSION} cli=${CLI_VERSION} mcp-bash=v${MCPBASH_VERSION}" >&2
    echo "[xaffinity-mcp:${XAFFINITY_MCP_VERSION}] Process: pid=$$ started=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)" >&2
fi

exec "$FRAMEWORK" --project-root "${SCRIPT_DIR}" "$@"
