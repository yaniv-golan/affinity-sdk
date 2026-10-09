#!/usr/bin/env bash
# server.d/policy.sh - Tool execution policies for xaffinity MCP Server

# Read-only tools (safe for any context)
# Includes CLI Gateway read tools: discover-commands, execute-read-command
AFFINITY_MCP_TOOLS_READONLY="get-entity-dossier get-file-url read-xaffinity-resource query discover-commands execute-read-command"

# Write tools (require full access)
# Includes CLI Gateway write tool: execute-write-command
AFFINITY_MCP_TOOLS_WRITE="execute-write-command"

# All tools
AFFINITY_MCP_TOOLS_ALL="${AFFINITY_MCP_TOOLS_READONLY} ${AFFINITY_MCP_TOOLS_WRITE}"

# Policy check function called by the framework
# Sets _MCP_TOOLS_ERROR_MESSAGE on failure for clear error reporting
mcp_tools_policy_check() {
    local tool_name="$1"

    # If read-only mode is enabled, only allow read-only tools. The MCPB bundle passes the
    # setting as "true"/"false" (env.sh, which maps it to 1, isn't sourced there).
    local read_only
    read_only="$(printf '%s' "${AFFINITY_MCP_READ_ONLY:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${read_only}" == "1" || "${read_only}" == "true" ]]; then
        case " ${AFFINITY_MCP_TOOLS_READONLY} " in
            *" ${tool_name} "*) return 0 ;;
            *)
                _MCP_TOOLS_ERROR_CODE=-32602
                _MCP_TOOLS_ERROR_MESSAGE="Tool '${tool_name}' is a write tool but server is in read-only mode (AFFINITY_MCP_READ_ONLY=1)"
                return 1
                ;;
        esac
    fi

    # Full access mode - allow all tools
    case " ${AFFINITY_MCP_TOOLS_ALL} " in
        *" ${tool_name} "*) return 0 ;;
        *)
            _MCP_TOOLS_ERROR_CODE=-32602
            _MCP_TOOLS_ERROR_MESSAGE="Tool '${tool_name}' not in server allowlist. Add to AFFINITY_MCP_TOOLS_READONLY or AFFINITY_MCP_TOOLS_WRITE in server.d/policy.sh"
            return 1
            ;;
    esac
}
