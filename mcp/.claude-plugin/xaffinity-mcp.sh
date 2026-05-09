#!/usr/bin/env bash
# xaffinity-mcp.sh - Wrapper that extracts and runs the MCP server
#
# This script is part of the Claude Code plugin distribution.
# On first run (or version change), it extracts the bundled MCP server from the ZIP archive.
# Subsequent runs execute the extracted server directly.
#
# IMPORTANT: MCP Protocol Compliance
# - This wrapper MUST NOT write anything to stdout (only JSON-RPC allowed)
# - All status/error messages go to stderr (>&2)
# - The real MCP server handles stdout after exec

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="${SCRIPT_DIR}/.mcp-extracted"
ZIP_FILE="${SCRIPT_DIR}/xaffinity-mcp-plugin.zip"
BUNDLE_VERSION_FILE="${SCRIPT_DIR}/VERSION"
EXTRACTED_VERSION_FILE="${MCP_DIR}/VERSION"

# Determine if extraction is needed:
# 1. MCP server doesn't exist, OR
# 2. Version mismatch (bundle updated)
needs_extraction() {
    # Check if MCP server exists and is executable
    if [[ ! -x "${MCP_DIR}/xaffinity-mcp.sh" ]]; then
        return 0  # Needs extraction
    fi

    # Check version match
    local bundle_version=""
    local extracted_version=""

    if [[ -f "${BUNDLE_VERSION_FILE}" ]]; then
        bundle_version="$(cat "${BUNDLE_VERSION_FILE}" 2>/dev/null || echo "")"
    fi

    if [[ -f "${EXTRACTED_VERSION_FILE}" ]]; then
        extracted_version="$(cat "${EXTRACTED_VERSION_FILE}" 2>/dev/null || echo "")"
    fi

    # If versions don't match (or bundle version exists but extracted doesn't), re-extract
    if [[ -n "${bundle_version}" && "${bundle_version}" != "${extracted_version}" ]]; then
        return 0  # Needs extraction
    fi

    return 1  # No extraction needed
}

# Extract if needed
if needs_extraction; then
    if [[ ! -f "${ZIP_FILE}" ]]; then
        echo "Error: MCP server bundle not found: ${ZIP_FILE}" >&2
        echo "In Claude Code, run: /plugin install affinity-crm-mcp-unofficial@xaffinity" >&2
        echo "Or manually rebuild: cd mcp && make plugin" >&2
        exit 1
    fi

    # Check for unzip command
    if ! command -v unzip &>/dev/null; then
        echo "Error: 'unzip' command not found." >&2
        echo "Please install unzip:" >&2
        echo "  macOS: brew install unzip (usually pre-installed)" >&2
        echo "  Linux: apt install unzip" >&2
        echo "  Windows: Use Git Bash or WSL" >&2
        exit 1
    fi

    # Status message
    if [[ -d "${MCP_DIR}" ]]; then
        echo "Updating xaffinity MCP server..." >&2
    else
        echo "Extracting xaffinity MCP server..." >&2
    fi

    # Atomic extraction: extract to temp dir, then move
    # This prevents partial extraction if interrupted
    TEMP_DIR="$(mktemp -d)"
    cleanup() { rm -rf "${TEMP_DIR:-}" 2>/dev/null || true; }
    trap cleanup EXIT INT TERM

    unzip -q "${ZIP_FILE}" -d "${TEMP_DIR}"

    # Ensure scripts are executable before moving
    chmod +x "${TEMP_DIR}/xaffinity-mcp.sh" "${TEMP_DIR}/xaffinity-mcp-env.sh" 2>/dev/null || true

    # Atomic swap: remove old, move new
    rm -rf "${MCP_DIR}"
    mv "${TEMP_DIR}" "${MCP_DIR}"

    # Clear all traps since move succeeded
    trap - EXIT INT TERM

    echo "Extraction complete." >&2
fi

# Verify extraction is complete before exec
if [[ ! -x "${MCP_DIR}/xaffinity-mcp.sh" ]]; then
    echo "Error: MCP server not found after extraction." >&2
    echo "Try removing the extraction directory and reinstalling:" >&2
    echo "  rm -rf '${MCP_DIR}'" >&2
    echo "  /plugin install affinity-crm-mcp-unofficial@xaffinity" >&2
    exit 1
fi

# Execute the real MCP server
exec "${MCP_DIR}/xaffinity-mcp.sh" "$@"
