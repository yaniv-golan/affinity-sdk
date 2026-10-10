#!/usr/bin/env bash
# Suggestions for the {entityType} variable of xaffinity://field-catalogs/{entityType} (mcp-bash resource template completion).
exec bash "${MCPBASH_PROJECT_ROOT}/completions/list-name.sh" --args entityType --extra company,person,opportunity
