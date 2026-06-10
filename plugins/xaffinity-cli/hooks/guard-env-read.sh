#!/usr/bin/env bash
set -euo pipefail

# Block reading .env files to prevent API key exposure in conversation.
# The API key is already available via env var or config.toml — no need to read .env.

input=$(cat)
file_path=$(echo "$input" | jq -r '
  if .tool_input | type == "object" then
    .tool_input.file_path // ""
  elif .tool_input | type == "string" then
    .tool_input
  else
    ""
  end
' 2>/dev/null || echo "")

# Check if the path points to a .env file
case "$file_path" in
  *.env|*.env.*|*/.env|*/.env.*)
    cat >&2 << 'EOF'
{
  "hookSpecificOutput": {
    "permissionDecision": "deny"
  },
  "systemMessage": "BLOCKED: Reading .env files is not allowed — it would expose API keys in the conversation. The API key is already loaded as an environment variable. Use 'xaffinity config check-key --json' to verify."
}
EOF
    exit 2
    ;;
esac

exit 0
