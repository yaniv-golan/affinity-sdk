#!/usr/bin/env bash
# completions/list-name.sh - List name suggestions for a prompt's `listName` argument.
#
# Called through prompts/<name>/<name>.completion.sh (mcp-bash prompt completion). Gets
# MCP_COMPLETION_ARGS_JSON ({argument: {name, value}, query, ...}), MCP_COMPLETION_LIMIT and
# MCP_COMPLETION_OFFSET. Always prints JSON and exits 0: a failing completion script becomes a
# JSON-RPC error on every keystroke, and empty suggestions are the better answer.

empty() {
    printf '%s\n' '{"suggestions":[],"hasMore":false}'
    exit 0
}
trap empty ERR

# shellcheck source=/dev/null
source "${MCPBASH_PROJECT_ROOT}/lib/common.sh" 2>/dev/null || empty

# (Not ${VAR:-{}}: bash ends that default at the first "}" and appends a stray "}".)
args_json="${MCP_COMPLETION_ARGS_JSON:-}"
[[ -n "${args_json}" ]] || args_json='{}'
arg_name="$(jq_tool -r '.argument.name // ""' <<<"${args_json}" 2>/dev/null)" || empty
# Only listName is ours: return nothing for other arguments (better than the framework's stub,
# which echoes the typed value back as made-up suggestions).
[[ "${arg_name}" == "listName" ]] || empty

prefix="$(jq_tool -r '(.query // .prefix // "")' <<<"${args_json}" 2>/dev/null)" || empty
prefix="${prefix#"${prefix%%[![:space:]]*}"}"
prefix="${prefix%"${prefix##*[![:space:]]}"}"
limit="${MCP_COMPLETION_LIMIT:-20}"
offset="${MCP_COMPLETION_OFFSET:-0}"
[[ "${limit}" =~ ^[0-9]+$ ]] || limit=20
[[ "${offset}" =~ ^[0-9]+$ ]] || offset=0

# The framework gives prompt completion scripts no timeout: bound the CLI call ourselves.
cli_list() {
    run_xaffinity_readonly --timeout 8 list ls "$@" --output json --quiet 2>/dev/null
}

if [[ -n "${prefix}" ]]; then
    # --query=VALUE (one argument): the helper strips -q / --quiet / --session-cache tokens, which
    # a separate value could be. Affinity filters by name across all lists.
    result="$(cli_list --all "--query=${prefix}")" || true
    # CLI older than 1.21.0: no --query (usage error in its JSON) -> first page, filtered here.
    if jq_tool -e '.error.type == "usage_error" and (.error.message | test("--query"))' \
        <<<"${result}" >/dev/null 2>&1; then
        result="$(cli_list --max-results 100)" || true
    fi
else
    result="$(cli_list --max-results 100)" || true
fi

jq_tool -c --arg prefix "${prefix}" --argjson limit "${limit}" --argjson offset "${offset}" '
    ($prefix | ascii_downcase) as $p
    | [(.data.lists // [])[] | .name | select(type == "string")]
    | map(select(ascii_downcase | contains($p)))
    | (map(select(ascii_downcase | startswith($p))) + map(select(ascii_downcase | startswith($p) | not)))
    | reduce .[] as $n ([]; if index([$n]) then . else . + [$n] end)
    | .[$offset:$offset + $limit] as $page
    | {suggestions: $page, hasMore: (($offset + ($page | length)) < length)}
      + (if ($offset + ($page | length)) < length then {next: ($offset + ($page | length))} else {} end)
' <<<"${result:-"{}"}" 2>/dev/null || empty
