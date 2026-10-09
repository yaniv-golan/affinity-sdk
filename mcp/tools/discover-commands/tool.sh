#!/usr/bin/env bash
# tools/discover-commands/tool.sh - Search CLI commands by keyword or capability
set -euo pipefail

source "${MCP_SDK:?}/tool-sdk.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/common.sh"
source "${MCPBASH_PROJECT_ROOT}/lib/cli-gateway.sh"

# Validate registry (required for CLI Gateway tools)
if ! validate_registry; then
    # validate_registry already emitted mcp_result_error with details
    exit 0
fi

# Parse arguments using mcp-bash SDK
query="$(mcp_args_require '.query' 'Query string is required')"
detail="$(mcp_args_get '.detail // "summary"')"
category="$(mcp_args_get '.category // "all"')"
format="$(mcp_args_get '.format // "text"')"
limit="$(mcp_args_int '.limit' --default 10 --min 1 --max 50)"

# Log tool invocation
xaffinity_log_debug "discover-commands" "query='$query' category=$category format=$format limit=$limit"

# Read-only mode: force category to "read" regardless of request
if xaffinity_flag_enabled "${AFFINITY_MCP_READ_ONLY:-}"; then
    category="read"
fi

# Validate category
case "$category" in
    all|read|write|local) ;;
    *)
        mcp_error "validation_error" "Unknown category: $category" \
            --hint "Valid values: all, read, write, local"
        exit 0
        ;;
esac

# Filter commands from pre-generated registry using token-based matching
# Search over: name + description + parameter names
# Scoring (prioritizes command name matches):
#   - Exact name match: +1000
#   - Name starts with query: +500
#   - Each token in name: +100
#   - Each token in description/params: +1
# NOTE: Filter tokens < 3 chars for better relevance, but fall back to
#       original query if ALL tokens would be filtered (e.g., "AI", "VC")
if [[ "$category" == "all" ]]; then
    matches=$(jq_tool -c --arg q "$query" --argjson lim "$limit" '
        # Split query into tokens (lowercase), filter short tokens
        ($q | ascii_downcase) as $query_lower |
        ($query_lower | split(" ")) as $all_tokens |
        ($all_tokens | map(select(length >= 3))) as $filtered |
        (if ($filtered | length) > 0 then $filtered else $all_tokens end) as $tokens |
        .commands
        | map(
            . as $cmd |
            (.name | ascii_downcase) as $name_lower |
            (
                [(.description // "")]
                + [(.parameters // {}) | keys | .[]]
                + [.positionals // [] | .[].name]
            ) | map(ascii_downcase) | join(" ") as $other_text |
            # Scoring: prioritize name matches
            (if $name_lower == $query_lower then 1000 else 0 end) as $exact_match |
            (if ($name_lower | startswith($query_lower)) then 500 else 0 end) as $prefix_match |
            ($tokens | map(select($name_lower | contains(.))) | length * 100) as $name_token_score |
            ($tokens | map(select($other_text | contains(.))) | length) as $other_token_score |
            ($exact_match + $prefix_match + $name_token_score + $other_token_score) as $score |
            select($score > 0) |
            {cmd: $cmd, score: $score}
        )
        | sort_by(-.score)
        | .[:$lim]
        | map(.cmd)
    ' "$REGISTRY_FILE")
else
    # For "read" category, also include "local" commands (safe to execute via execute-read-command)
    matches=$(jq_tool -c --arg q "$query" --arg cat "$category" --argjson lim "$limit" '
        ($q | ascii_downcase) as $query_lower |
        ($query_lower | split(" ")) as $all_tokens |
        ($all_tokens | map(select(length >= 3))) as $filtered |
        (if ($filtered | length) > 0 then $filtered else $all_tokens end) as $tokens |
        .commands
        | map(select(if $cat == "read" then (.category == "read" or .category == "local") else .category == $cat end))
        | map(
            . as $cmd |
            (.name | ascii_downcase) as $name_lower |
            (
                [(.description // "")]
                + [(.parameters // {}) | keys | .[]]
                + [.positionals // [] | .[].name]
            ) | map(ascii_downcase) | join(" ") as $other_text |
            # Scoring: prioritize name matches
            (if $name_lower == $query_lower then 1000 else 0 end) as $exact_match |
            (if ($name_lower | startswith($query_lower)) then 500 else 0 end) as $prefix_match |
            ($tokens | map(select($name_lower | contains(.))) | length * 100) as $name_token_score |
            ($tokens | map(select($other_text | contains(.))) | length) as $other_token_score |
            ($exact_match + $prefix_match + $name_token_score + $other_token_score) as $score |
            select($score > 0) |
            {cmd: $cmd, score: $score}
        )
        | sort_by(-.score)
        | .[:$lim]
        | map(.cmd)
    ' "$REGISTRY_FILE")
fi

# Use temp file to avoid shell heredoc issues with complex JSON
matches_file=$(mktemp)
printf '%s' "$matches" > "$matches_file"
trap 'rm -f "$matches_file"' EXIT

match_count=$(jq_tool 'length' "$matches_file")
xaffinity_log_debug "discover-commands" "found $match_count matches"

if [[ "$format" == "text" ]]; then
    # Compact pipe-delimited format
    # Legend: r=read, l=local, w=write, d=destructive (so "wd" = write+destructive)
    # Params: s=str i=int b=bool f=flag !=req *=multi, UPPERCASE=positional
    # One-of required groups shown as: (--opt1|--opt2):type!
    text_lines=$(jq_tool -r '.[] |
        . as $cmd |
        # Flatten all params in requiredOneOf groups for exclusion
        (($cmd.requiredOneOf // []) | flatten) as $one_of_params |
        # Build one-of group strings: (--opt1|--opt2|...):type!
        (($cmd.requiredOneOf // []) | map(
            "(" + (. | join("|")) + "):" +
            (.[0] as $first | $cmd.parameters[$first].type // "string" | .[0:1]) + "!"
        )) as $one_of_groups |
        # Build params array: positionals + one-of groups + regular params
        (
            ([$cmd.positionals // [] | .[] | (.name | ascii_upcase) + ":" + ((.type // "string") | .[0:1]) + (if .required then "!" else "" end)])
            +
            $one_of_groups
            +
            [($cmd.parameters // {}) | to_entries | sort_by(.key) | .[] | select(.key as $k | $one_of_params | index($k) | not) | .key + ":" + ((.value.type // "string") | .[0:1]) + (if .value.required then "!" else "" end) + (if .value.multiple then "*" else "" end)]
        ) as $params |
        [
            $cmd.name,
            (if $cmd.destructive then "wd" elif $cmd.category == "write" then "w" elif $cmd.category == "local" then "l" else "r" end),
            ($params | join(" "))
        ] | join("|")' "$matches_file")
    text_output="# cmd|cat|params (r=read l=local w=write wd=destructive)"$'\n'"${text_lines}"

    # In text mode, include minimal structuredContent (names only) for programmatic access
    minimal_matches=$(jq_tool -c '[.[] | {name, category, destructive}]' "$matches_file")
    jq_tool -n --arg text "$text_output" --argjson matches "$minimal_matches" '{
        content: [{type: "text", text: $text}],
        structuredContent: {success: true, result: $matches},
        isError: false
    }'
else
    # JSON format with full details
    case "$detail" in
        full)
            matches_array="$matches"
            ;;
        list)
            matches_array=$(jq_tool 'map({name})' "$matches_file")
            ;;
        summary|*)
            matches_array=$(jq_tool 'map({name, description, category, destructive})' "$matches_file")
            ;;
    esac

    # Wrap in {matches, total} structure
    total_count=$(jq_tool 'length' "$matches_file")
    json_result=$(jq_tool -n --argjson m "$matches_array" --argjson t "$total_count" \
        '{matches: $m, total: $t}')

    mcp_result_success "$json_result"
fi
