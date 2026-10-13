#!/usr/bin/env bash
set -euo pipefail

# Block reading .env files and the xaffinity config file to prevent API key exposure in the
# conversation.
# Commands load the key from .env themselves (--dotenv / --env-file); reading it isn't needed.

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

# The xaffinity user config file (config.toml in the platform config dir: ~/.config/xaffinity
# on Linux, ~/Library/Application Support/xaffinity on macOS, ...\Affinity\xaffinity on
# Windows) holds the key stored by `config setup-key --scope user`. Compared lowercased: macOS
# and Windows file systems ignore case.
file_path_lc=$(printf '%s' "$file_path" | tr '[:upper:]' '[:lower:]')
case "$file_path_lc" in
  */xaffinity/config.toml|*\\xaffinity\\config.toml)
    cat >&2 << 'EOF'
{
  "hookSpecificOutput": {
    "permissionDecision": "deny"
  },
  "systemMessage": "BLOCKED: Reading the xaffinity config file (config.toml) is not allowed — it can hold the API key and would expose it in the conversation. Commands read the key themselves; run 'xaffinity config check-key --json' to see whether a key is configured and the command pattern to use."
}
EOF
    exit 2
    ;;
esac

# Check if the path points to a .env file
case "$file_path" in
  *.env|*.env.*|*/.env|*/.env.*)
    cat >&2 << 'EOF'
{
  "hookSpecificOutput": {
    "permissionDecision": "deny"
  },
  "systemMessage": "BLOCKED: Reading .env files is not allowed — it would expose API keys in the conversation. Commands read the key themselves; run 'xaffinity config check-key --json' to see whether a key is configured and the command pattern to use."
}
EOF
    exit 2
    ;;
esac

exit 0
