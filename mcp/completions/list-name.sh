#!/usr/bin/env bash
# completions/list-name.sh - List name suggestions for prompt arguments and resource templates.
#
# Usage: list-name.sh [--args NAME,NAME...] [--extra VALUE,VALUE...]
#   --args   argument / template variable names this script completes (default: listName)
#   --extra  fixed values offered before list names (e.g. company,person,opportunity)
#
# Called through prompts/<name>/<name>.completion.sh and resources/<template>.completion.sh
# (mcp-bash prompt / resource completion). Gets MCP_COMPLETION_ARGS_JSON ({argument: {name,
# value}, query, ...}), MCP_COMPLETION_LIMIT and MCP_COMPLETION_OFFSET. mcp-bash stops these
# scripts after 5 s, so the CLI gets one 3 s attempt. Always prints JSON and exits 0: a failing
# completion script becomes a JSON-RPC error on every keystroke.

empty() {
    printf '%s\n' '{"suggestions":[],"hasMore":false}'
    exit 0
}
trap empty ERR

# shellcheck source=/dev/null
source "${MCPBASH_PROJECT_ROOT}/lib/common.sh" 2>/dev/null || empty

# (Not ${VAR:-{}}: bash ends that default at the first "}" and appends a stray "}".)
completable="listName"
extra=""
while [[ $# -gt 0 ]]; do
    case "$1" in
    --args) completable="${2:-}"; shift 2 || empty ;;
    --extra) extra="${2:-}"; shift 2 || empty ;;
    *) shift ;;
    esac
done

args_json="${MCP_COMPLETION_ARGS_JSON:-}"
[[ -n "${args_json}" ]] || args_json='{}'
arg_name="$(jq_tool -r '.argument.name // ""' <<<"${args_json}" 2>/dev/null)" || empty
# Only the configured arguments are ours: return nothing for others (better than the framework's
# stub, which echoes the typed value back as made-up suggestions).
case ",${completable}," in
*",${arg_name},"*) ;;
*) empty ;;
esac

prefix="$(jq_tool -r '(.query // .prefix // "")' <<<"${args_json}" 2>/dev/null)" || empty
prefix="${prefix#"${prefix%%[![:space:]]*}"}"
prefix="${prefix%"${prefix##*[![:space:]]}"}"
limit="${MCP_COMPLETION_LIMIT:-20}"
offset="${MCP_COMPLETION_OFFSET:-0}"
[[ "${limit}" =~ ^[0-9]+$ ]] || limit=20
[[ "${offset}" =~ ^[0-9]+$ ]] || offset=0

# One bounded attempt (mcp-bash kills the script after 5 s).
cli_list() {
    run_xaffinity_readonly --timeout 3 --max-retries 0 list ls "$@" --output json --quiet 2>/dev/null
}

if [[ -n "${prefix}" ]]; then
    # --query=VALUE (one argument): the helper strips -q / --quiet / --session-cache tokens, which
    # a separate value could be. Affinity filters by name across all lists.
    result="$(cli_list --all "--query=${prefix}")" || true
else
    result="$(cli_list --max-results 100)" || true
fi

jq_tool -c --arg prefix "${prefix}" --arg extra "${extra}" --argjson limit "${limit}" --argjson offset "${offset}" '
    ($prefix | ascii_downcase) as $p
    | (($extra | split(",") | map(select(. != ""))) + [(.data.lists // [])[] | .name | select(type == "string")])
    | map(select(ascii_downcase | contains($p)))
    | (map(select(ascii_downcase | startswith($p))) + map(select(ascii_downcase | startswith($p) | not)))
    | reduce .[] as $n ([]; if index([$n]) then . else . + [$n] end)
    | .[$offset:$offset + $limit] as $page
    | {suggestions: $page, hasMore: (($offset + ($page | length)) < length)}
      + (if ($offset + ($page | length)) < length then {next: ($offset + ($page | length))} else {} end)
' <<<"${result:-"{}"}" 2>/dev/null || empty
