#!/usr/bin/env bash
# lib/cli-gateway.sh - Shared validation functions for CLI Gateway tools
#
# This library provides validation functions for the CLI Gateway pattern:
# - discover-commands
# - execute-read-command
# - execute-write-command
#
# All functions use the pre-generated registry (REGISTRY_FILE from common.sh).

# ==============================================================================
# Registry Validation
# ==============================================================================

# Validate registry file exists and has expected structure
# Returns: 0 if valid, 1 if invalid (with mcp_result_error emitted)
validate_registry() {
    if [[ ! -f "$REGISTRY_FILE" ]]; then
        mcp_error "configuration_error" "Command registry not found" \
            --hint "Run: python tools/generate_mcp_command_registry.py"
        return 1
    fi

    if ! jq_tool -e '.commands | type == "array"' "$REGISTRY_FILE" >/dev/null 2>&1; then
        mcp_error "configuration_error" "Command registry is invalid or corrupted" \
            --hint "Regenerate registry: python tools/generate_mcp_command_registry.py"
        return 1
    fi

    # Validate command object shape (prevents subtle runtime failures)
    local validation_result
    validation_result=$(jq_tool -r '
        .commands | to_entries | map(
            .value as $cmd | .key as $idx |
            if ($cmd.name | type) != "string" or ($cmd.name | length) == 0 then
                "Entry \($idx): missing or empty name"
            elif ($cmd.category | . != "read" and . != "write" and . != "local") then
                "Entry \($idx) (\($cmd.name)): category must be read, write, or local"
            elif ($cmd.destructive | type) != "boolean" and ($cmd.destructive | type) != "null" then
                "Entry \($idx) (\($cmd.name)): destructive must be boolean"
            elif ($cmd.parameters | type) != "object" and ($cmd.parameters | type) != "null" then
                "Entry \($idx) (\($cmd.name)): parameters must be object or null"
            elif ($cmd.positionals | type) != "array" and ($cmd.positionals | type) != "null" then
                "Entry \($idx) (\($cmd.name)): positionals must be array or null"
            else empty end
        ) | first // empty
    ' "$REGISTRY_FILE" 2>/dev/null)

    if [[ -n "$validation_result" ]]; then
        mcp_error "configuration_error" "Registry validation failed: $validation_result" \
            --hint "Regenerate registry: python tools/generate_mcp_command_registry.py"
        return 1
    fi

    return 0
}

# ==============================================================================
# Fuzzy Matching Helpers
# ==============================================================================

# Find a similar command for "Did you mean" suggestions
# Uses simple substring and prefix matching (no external dependencies)
# Args: input_cmd all_commands (newline-separated)
# Returns: best matching command or empty string
find_similar_command() {
    local input="$1"
    local all_commands="$2"

    # Normalize input (lowercase, remove extra spaces)
    local normalized_input
    normalized_input=$(echo "$input" | tr '[:upper:]' '[:lower:]' | tr -s ' ')

    # Strategy 1: Check for prefix match (e.g., "pers" -> "person get")
    local prefix_match
    prefix_match=$(echo "$all_commands" | grep -i "^${normalized_input}" | head -1)
    if [[ -n "$prefix_match" ]]; then
        echo "$prefix_match"
        return 0
    fi

    # Strategy 2: Check if input words appear in command (e.g., "persn create" -> "person create")
    # Split input into words and find commands containing most of them
    local input_words
    read -ra input_words <<< "$normalized_input"

    local best_match=""
    local best_score=0

    while IFS= read -r cmd; do
        [[ -z "$cmd" ]] && continue
        local cmd_lower
        cmd_lower=$(echo "$cmd" | tr '[:upper:]' '[:lower:]')
        local score=0

        for word in "${input_words[@]}"; do
            # Skip very short words
            [[ ${#word} -lt 2 ]] && continue
            # Check if word or similar appears in command
            if [[ "$cmd_lower" == *"$word"* ]]; then
                ((score++))
            # Check for off-by-one typo (e.g., "persn" in "person")
            elif [[ ${#word} -ge 3 ]]; then
                # Check if removing one char matches
                for ((i=0; i<${#word}; i++)); do
                    local partial="${word:0:i}${word:i+1}"
                    if [[ "$cmd_lower" == *"$partial"* ]]; then
                        ((score++))
                        break
                    fi
                done
            fi
        done

        if [[ $score -gt $best_score ]]; then
            best_score=$score
            best_match="$cmd"
        fi
    done <<< "$all_commands"

    # Only return if we found a reasonable match (at least one word matched)
    if [[ $best_score -gt 0 ]]; then
        echo "$best_match"
    fi
}

# ==============================================================================
# Command Validation
# ==============================================================================

# Validate command exists in registry with correct category
# Args: command tool_type ("read" or "write")
# Returns: 0 if valid, 1 if invalid (with mcp_result_error emitted)
#
# Category routing:
#   - execute-read-command accepts: "read" and "local" (safe, no Affinity modifications)
#   - execute-write-command accepts: "write" only (modifies Affinity data)
validate_command() {
    local cmd="$1"
    local tool_type="$2"

    # Reject any token starting with - (flags belong in argv, not command)
    if [[ "$cmd" =~ (^|[[:space:]])-. ]]; then
        mcp_error "validation_error" "Flags not allowed in command path" \
            --hint "Put flags like --limit in argv, not in command string"
        return 1
    fi

    # Load valid commands from pre-generated registry
    # For "read" tool_type, also accept "local" category commands
    local valid_commands
    if [[ "$tool_type" == "read" ]]; then
        valid_commands=$(jq_tool -r \
            '.commands[] | select(.category == "read" or .category == "local") | .name' \
            "$REGISTRY_FILE")
    else
        valid_commands=$(jq_tool -r --arg cat "$tool_type" \
            '.commands[] | select(.category == $cat) | .name' \
            "$REGISTRY_FILE")
    fi

    # Exact match required
    if ! echo "$valid_commands" | grep -qxF "$cmd"; then
        # Check if command exists but wrong category
        local all_commands
        all_commands=$(jq_tool -r '.commands[].name' "$REGISTRY_FILE")
        if echo "$all_commands" | grep -qxF "$cmd"; then
            local actual_cat
            actual_cat=$(jq_tool -r --arg cmd "$cmd" '.commands[] | select(.name == $cmd) | .category' "$REGISTRY_FILE")
            # Map "local" to "read" in error message for user clarity
            local suggest_tool="execute-${actual_cat}-command"
            if [[ "$actual_cat" == "local" ]]; then
                suggest_tool="execute-read-command"
            fi
            mcp_error "validation_error" "Command \"$cmd\" is category \"$actual_cat\", use $suggest_tool instead" \
                --hint "Use $suggest_tool for $actual_cat commands"
        else
            # Try to find similar commands for "Did you mean" suggestion
            local suggestion
            suggestion=$(find_similar_command "$cmd" "$all_commands")
            if [[ -n "$suggestion" ]]; then
                mcp_error "command_not_found" "Unknown command: $cmd" \
                    --hint "Did you mean \"$suggestion\"?"
            else
                mcp_error "command_not_found" "Unknown command: $cmd" \
                    --hint "Use discover-commands to find available commands"
            fi
        fi
        return 1
    fi

    return 0
}

# ==============================================================================
# Argument Validation
# ==============================================================================

# Validate argv against per-command schema from registry
# Args: command argv...
# Returns: 0 if valid, 1 if invalid (with mcp_result_error emitted)
validate_argv() {
    local cmd="$1"
    shift
    local argv=("$@")
    local argc=${#argv[@]}

    # Get allowed parameters for this command from registry
    local cmd_schema
    cmd_schema=$(jq_tool --arg cmd "$cmd" '.commands[] | select(.name == $cmd)' "$REGISTRY_FILE")

    if [[ -z "$cmd_schema" ]]; then
        mcp_error "internal_error" "Command schema not found" \
            --hint "Registry may be out of sync; regenerate with: python tools/generate_mcp_command_registry.py"
        return 1
    fi

    # Extract parameter metadata from registry (including aliases)
    local allowed_flags
    # Get primary flags and their aliases
    allowed_flags=$(printf '%s' "$cmd_schema" | jq_tool -r '
        .parameters // {} | to_entries[] | select(.key | startswith("-")) |
        .key, (.value.aliases // [] | .[])
    ' 2>/dev/null || printf '')

    # Build alias-to-primary map (JSON object for lookup)
    local alias_map
    alias_map=$(printf '%s' "$cmd_schema" | jq_tool -c '
        .parameters // {} | to_entries | map(select(.key | startswith("-"))) |
        map({primary: .key, aliases: (.value.aliases // [])}) |
        map([{key: .primary, value: .primary}] + [.aliases[] as $a | {key: $a, value: .primary}]) |
        flatten | from_entries
    ' 2>/dev/null || printf '{}')

    local positional_defs
    positional_defs=$(printf '%s' "$cmd_schema" | jq_tool -c '.positionals // []' 2>/dev/null || printf '[]')
    local required_positional_count
    required_positional_count=$(printf '%s' "$positional_defs" | jq_tool -r '[.[] | select(.required == true)] | length' 2>/dev/null || printf '0')
    local total_positional_count
    total_positional_count=$(printf '%s' "$positional_defs" | jq_tool -r 'length' 2>/dev/null || printf '0')

    # Track provided params (space-delimited string for Bash 3.2 compatibility)
    local provided_flags=""
    local positional_count=0
    local i=0

    while [[ $i -lt $argc ]]; do
        local arg="${argv[$i]}"

        if [[ "$arg" == -* ]]; then
            # --- Flag argument ---
            local flag_name="$arg"
            local flag_value=""
            local has_equals=false

            # Handle --flag=value syntax
            if [[ "$flag_name" == *=* ]]; then
                has_equals=true
                flag_value="${flag_name#*=}"
                flag_name="${flag_name%%=*}"
            fi

            # Check if flag is in allowed list (use -- to prevent grep treating flags as options)
            if ! printf '%s' "$allowed_flags" | grep -qxF -- "$flag_name"; then
                mcp_error "validation_error" "Unknown flag: $flag_name" \
                    --hint "Use discover-commands to see valid parameters for this command"
                return 1
            fi

            # Resolve alias to primary flag name for type lookup
            local primary_flag
            primary_flag=$(printf '%s' "$alias_map" | jq_tool -r --arg f "$flag_name" '.[$f] // $f')

            # Get flag type and nargs from schema (using primary flag name)
            local flag_type flag_nargs
            flag_type=$(printf '%s' "$cmd_schema" | jq_tool -r --arg f "$primary_flag" '.parameters[$f].type // "string"')
            flag_nargs=$(printf '%s' "$cmd_schema" | jq_tool -r --arg f "$primary_flag" '.parameters[$f].nargs // 1')

            if [[ "$flag_type" == "flag" ]]; then
                # Boolean flag (no value expected) - reject ANY use of = syntax
                if [[ "$has_equals" == "true" ]]; then
                    mcp_error "validation_error" "Flag $flag_name does not accept a value (use without =)" \
                        --hint "Use just $flag_name without =value"
                    return 1
                fi
            else
                # Value-taking flag - consume nargs values
                if [[ "$has_equals" == "false" ]]; then
                    # Check we have enough remaining arguments
                    if [[ $((i + flag_nargs)) -gt $argc ]]; then
                        if [[ "$flag_nargs" -gt 1 ]]; then
                            mcp_error "validation_error" "Flag $flag_name requires $flag_nargs values" \
                                --hint "Provide $flag_nargs values after $flag_name"
                        else
                            mcp_error "validation_error" "Flag $flag_name requires a value" \
                                --hint "Add a value after $flag_name or use $flag_name=value"
                        fi
                        return 1
                    fi

                    # Consume nargs values (skip over them)
                    local val_idx
                    for ((val_idx = 1; val_idx <= flag_nargs; val_idx++)); do
                        local next_arg="${argv[$((i + val_idx))]}"
                        # Only reject dash-leading values if they match a known flag
                        if [[ "$next_arg" == -* ]]; then
                            local next_flag_name="${next_arg%%=*}"
                            if printf '%s' "$allowed_flags" | grep -qxF -- "$next_flag_name"; then
                                if [[ "$flag_nargs" -gt 1 ]]; then
                                    mcp_error "validation_error" "Flag $flag_name requires $flag_nargs values, got $((val_idx - 1)) before another flag" \
                                        --hint "Provide $flag_nargs values after $flag_name"
                                else
                                    mcp_error "validation_error" "Flag $flag_name requires a value, got another flag" \
                                        --hint "Add a value between $flag_name and $next_arg"
                                fi
                                return 1
                            fi
                        fi
                        # For single-value flags, capture for type validation
                        if [[ "$flag_nargs" -eq 1 ]]; then
                            flag_value="$next_arg"
                        fi
                    done
                    # Skip past all consumed values
                    ((i += flag_nargs))
                fi

                # Type validation (only for single-value flags)
                if [[ "$flag_nargs" -eq 1 ]]; then
                    case "$flag_type" in
                        int|integer)
                            if ! [[ "$flag_value" =~ ^-?[0-9]+$ ]]; then
                                mcp_error "validation_error" "Flag $flag_name requires integer, got: $flag_value" \
                                    --hint "Provide a numeric value like $flag_name=10"
                                return 1
                            fi
                            ;;
                        bool|boolean)
                            if ! [[ "$flag_value" =~ ^(true|false)$ ]]; then
                                mcp_error "validation_error" "Flag $flag_name requires true/false, got: $flag_value" \
                                    --hint "Use $flag_name=true or $flag_name=false"
                                return 1
                            fi
                            ;;
                    esac
                fi
            fi

            # Check for duplicate flags (using string contains for Bash 3.2 compatibility)
            if [[ " $provided_flags " == *" $flag_name "* ]]; then
                local allows_multiple
                allows_multiple=$(printf '%s' "$cmd_schema" | jq_tool -r --arg f "$flag_name" '.parameters[$f].multiple // false')
                if [[ "$allows_multiple" != "true" ]]; then
                    mcp_error "validation_error" "Flag $flag_name cannot be specified multiple times" \
                        --hint "Remove duplicate $flag_name from argv"
                    return 1
                fi
            fi
            provided_flags="$provided_flags $flag_name"

        else
            # --- Positional argument ---
            if [[ $positional_count -lt $total_positional_count ]]; then
                local pos_type
                pos_type=$(printf '%s' "$positional_defs" | jq_tool -r --argjson idx "$positional_count" '.[$idx].type // "string"')
                case "$pos_type" in
                    int|integer)
                        if ! [[ "$arg" =~ ^-?[0-9]+$ ]]; then
                            local pos_name
                            pos_name=$(printf '%s' "$positional_defs" | jq_tool -r --argjson idx "$positional_count" '.[$idx].name // "argument"')
                            mcp_error "validation_error" "$pos_name requires integer, got: $arg" \
                                --hint "Provide a numeric value for $pos_name"
                            return 1
                        fi
                        ;;
                esac
            fi
            ((positional_count++))
        fi
        ((i++))
    done

    # Check required positional arguments
    if [[ $positional_count -lt $required_positional_count ]]; then
        mcp_error "validation_error" "Missing required arguments: need $required_positional_count, got $positional_count" \
            --hint "Use discover-commands to see required arguments for this command"
        return 1
    fi

    # Check for too many positional arguments
    if [[ $positional_count -gt $total_positional_count ]]; then
        mcp_error "validation_error" "Too many arguments: expected at most $total_positional_count, got $positional_count" \
            --hint "Remove extra arguments from argv"
        return 1
    fi

    # Check required flags (using string contains for Bash 3.2 compatibility)
    local required_flags
    required_flags=$(printf '%s' "$cmd_schema" | jq_tool -r '.parameters // {} | to_entries[] | select(.key | startswith("-")) | select(.value.required == true) | .key' 2>/dev/null || printf '')
    for req_flag in $required_flags; do
        if [[ " $provided_flags " != *" $req_flag "* ]]; then
            mcp_error "validation_error" "Missing required flag: $req_flag" \
                --hint "Add $req_flag to your argv"
            return 1
        fi
    done

    return 0
}

# ==============================================================================
# Destructive Command Detection
# ==============================================================================

# Check if a command is destructive (from registry metadata)
# Args: command
# Returns: 0 if destructive, 1 if not
is_destructive() {
    local cmd="$1"
    local destructive
    destructive=$(jq_tool -r --arg cmd "$cmd" \
        '.commands[] | select(.name == $cmd) | .destructive // false' \
        "$REGISTRY_FILE")
    [[ "$destructive" == "true" ]]
}

# ==============================================================================
# Proactive Output Limiting
# ==============================================================================

# Default and maximum limits for pagination (aligned with query tool)
CLI_GATEWAY_DEFAULT_LIMIT="${CLI_GATEWAY_DEFAULT_LIMIT:-1000}"
CLI_GATEWAY_MAX_LIMIT="${CLI_GATEWAY_MAX_LIMIT:-10000}"

# Apply limit cap to argv - block unbounded flags and set env vars for Python enforcement
# This prevents large outputs before they happen (more efficient than post-hoc truncation)
# Args: command argv...
# Outputs: Modified argv (NUL-delimited) to stdout
# Usage (bash 3.x compatible):
#   new_argv=()
#   while IFS= read -r -d '' item; do new_argv+=("$item"); done < <(apply_limit_cap "$command" "${argv[@]}")
#   argv=("${new_argv[@]}")
apply_limit_cap() {
    local cmd="$1"
    shift
    local argv=("$@")

    # Get limit config from registry
    local limit_config
    limit_config=$(jq_tool -c --arg cmd "$cmd" \
        '.commands[] | select(.name == $cmd) | .limitConfig // empty' \
        "$REGISTRY_FILE")

    # No limit config = no changes needed
    if [[ -z "$limit_config" ]]; then
        [[ ${#argv[@]} -gt 0 ]] && printf '%s\0' "${argv[@]}"
        return 0
    fi

    local unbounded_aliases
    # Get all aliases for the unbounded flag (e.g., ["--all", "-A"])
    unbounded_aliases=$(echo "$limit_config" | jq_tool -r '.unboundedFlagAliases // [] | .[]')

    # --all is acceptable only with the option + value in allowUnboundedWith (e.g.
    # `--strategy field` for field history-bulk: a bounded field-wide read)
    local allow_opt allow_val allowed=false i
    allow_opt=$(echo "$limit_config" | jq_tool -r '.allowUnboundedWith[0] // empty')
    allow_val=$(echo "$limit_config" | jq_tool -r '.allowUnboundedWith[1] // empty')
    if [[ -n "$allow_opt" && ${#argv[@]} -gt 0 ]]; then
        for ((i = 0; i < ${#argv[@]}; i++)); do
            if [[ "${argv[i]}" == "$allow_opt=$allow_val" ||
                ("${argv[i]}" == "$allow_opt" && "${argv[i + 1]:-}" == "$allow_val") ]]; then
                allowed=true
            fi
        done
    fi

    # Block unbounded flag (--all and aliases like -A) with clear error
    # Note: Guard for Bash 3.2 compatibility - empty arrays fail with set -u
    if [[ -n "$unbounded_aliases" && "$allowed" != "true" && ${#argv[@]} -gt 0 ]]; then
        for arg in "${argv[@]}"; do
            for alias in $unbounded_aliases; do
                if [[ "$arg" == "$alias" ]]; then
                    if [[ -n "$allow_opt" ]]; then
                        mcp_error "validation_error" \
                            "$alias is allowed via MCP only with $allow_opt $allow_val" \
                            --hint "Add $allow_opt $allow_val (dry run first), or use --max-results N"
                    else
                        mcp_error "validation_error" \
                            "$alias is not allowed via MCP (prevents unbounded scans)" \
                            --hint "Use --max-results N (max: $CLI_GATEWAY_MAX_LIMIT), or use --cursor for paginated iteration"
                    fi
                    return 1
                fi
            done
        done
    fi

    # enforceMax: refuse a limit above the command's max (its CLI doesn't cap it)
    local enforce_max cmd_max limit_aliases value
    enforce_max=$(echo "$limit_config" | jq_tool -r '.enforceMax // false')
    if [[ "$enforce_max" == "true" && ${#argv[@]} -gt 0 ]]; then
        cmd_max=$(echo "$limit_config" | jq_tool -r '.max')
        limit_aliases=$(echo "$limit_config" | jq_tool -r '.flagAliases // [] | .[]')
        for ((i = 0; i < ${#argv[@]}; i++)); do
            value=""
            for alias in $limit_aliases; do
                if [[ "${argv[i]}" == "$alias" ]]; then
                    value="${argv[i + 1]:-}"
                elif [[ "${argv[i]}" == "$alias="* ]]; then
                    value="${argv[i]#"$alias="}"
                fi
            done
            if [[ "$value" =~ ^[0-9]+$ ]] && ((10#$value > cmd_max)); then
                mcp_error "validation_error" \
                    "$cmd: --max-results is at most $cmd_max via MCP" \
                    --hint "Use $cmd_max or fewer; discover-commands describes the command's limits"
                return 1
            fi
        done
    fi

    # Set env vars for Python-side enforcement
    export AFFINITY_MCP_MAX_LIMIT="$CLI_GATEWAY_MAX_LIMIT"
    export AFFINITY_MCP_DEFAULT_LIMIT="$CLI_GATEWAY_DEFAULT_LIMIT"

    # Output NUL-delimited for safe consumption with mapfile
    # Only output if array is non-empty (empty printf '%s\0' would create one empty element)
    [[ ${#argv[@]} -gt 0 ]] && printf '%s\0' "${argv[@]}"
    return 0
}
