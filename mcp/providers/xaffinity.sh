#!/usr/bin/env bash
# providers/xaffinity.sh - Custom provider for xaffinity:// URI scheme
# Executes resource scripts under resources/ to generate dynamic content.
#
# Environment variables passed by mcp-bash framework (v0.8.4+):
#   MCPBASH_HOME          - Framework installation directory
#   MCPBASH_PROJECT_ROOT  - Project root directory
#   MCPBASH_PROVIDERS_DIR - Providers directory (project or framework)
#   MCP_RESOURCES_ROOTS   - Allowed resource roots (colon-separated)

set -euo pipefail

# Decode %HH escapes only (everything else, including a lone "%" or backslashes, is kept as is).
# Bash 3.2 compatible. Fails (returns 1) on %00 or a control character, which no list name has.
percent_decode() {
    # No `local LC_ALL=C` here: under bash 5.3 it makes this function fail at random when run
    # in a command substitution. "%" and hex digits are ASCII in any locale.
    local s="$1" out="" i=0 n c hex byte
    n=${#s}
    while [ "${i}" -lt "${n}" ]; do
        c="${s:i:1}"
        hex="${s:i+1:2}"
        if [ "${c}" = "%" ] && [[ "${hex}" =~ ^[0-9A-Fa-f]{2}$ ]]; then
            [ "${hex}" = "00" ] && return 1
            printf -v byte '%b' "\\x${hex}"
            out="${out}${byte}"
            i=$((i + 3))
        else
            out="${out}${c}"
            i=$((i + 1))
        fi
    done
    case "${out}" in
    *[[:cntrl:]]*) return 1 ;;
    esac
    printf '%s' "${out}"
}

# A templated resource's argument (a list id or name): decoded, and never empty or option-like
# (it is passed to the CLI as a positional argument).
template_argument() {
    local decoded trimmed
    decoded="$(percent_decode "$1")" || return 1
    trimmed="${decoded#"${decoded%%[![:space:]]*}"}"
    case "${trimmed}" in
    "" | -*) return 1 ;;
    esac
    printf '%s' "${decoded}"
}

uri="${1:-}"
if [ -z "${uri}" ]; then
    printf '%s\n' "xaffinity provider requires xaffinity://<path>" >&2
    exit 4
fi

case "${uri}" in
xaffinity://*)
    # Extract path from URI (e.g., "me" from "xaffinity://me")
    resource_path="${uri#xaffinity://}"
    ;;
*)
    printf '%s\n' "Unsupported URI scheme for xaffinity provider" >&2
    exit 4
    ;;
esac

# Map URI path to script
# xaffinity://me -> resources/me/me.sh
# xaffinity://me/person-id -> resources/me-person-id/me-person-id.sh
# xaffinity://interaction-enums -> resources/interaction-enums/interaction-enums.json
# xaffinity://saved-views/{listId} -> resources/saved-views/saved-views.sh {listId}
# xaffinity://workflow-config/{listId} -> resources/workflow-config/workflow-config.sh {listId}
# xaffinity://field-catalogs/{entityType} -> resources/field-catalogs/field-catalogs.sh {entityType}

# Use project resources directory
resources_dir="${MCPBASH_PROJECT_ROOT}/resources"

# Handle parameterized URIs (e.g., saved-views/{listId})
# Check for known parameterized patterns before normalizing
script_path=""
script_args=()

case "${resource_path}" in
    saved-views/*)
        # Extract listId from saved-views/{listId}
        if ! param="$(template_argument "${resource_path#saved-views/}")"; then
            printf '%s\n' "Invalid argument in ${uri}" >&2
            exit 4
        fi
        if [[ -f "${resources_dir}/saved-views/saved-views.sh" ]]; then
            script_path="${resources_dir}/saved-views/saved-views.sh"
            script_args=("${param}")
        fi
        ;;
    workflow-config/*)
        # Extract listId from workflow-config/{listId}
        if ! param="$(template_argument "${resource_path#workflow-config/}")"; then
            printf '%s\n' "Invalid argument in ${uri}" >&2
            exit 4
        fi
        if [[ -f "${resources_dir}/workflow-config/workflow-config.sh" ]]; then
            script_path="${resources_dir}/workflow-config/workflow-config.sh"
            script_args=("${param}")
        fi
        ;;
    field-catalogs/*)
        # Extract entityType from field-catalogs/{entityType}
        if ! param="$(template_argument "${resource_path#field-catalogs/}")"; then
            printf '%s\n' "Invalid argument in ${uri}" >&2
            exit 4
        fi
        if [[ -f "${resources_dir}/field-catalogs/field-catalogs.sh" ]]; then
            script_path="${resources_dir}/field-catalogs/field-catalogs.sh"
            script_args=("${param}")
        fi
        ;;
esac

# If not a parameterized path, use standard resolution
if [ -z "${script_path}" ]; then
    # Static resources: plain path segments only (no dots, so no "..", no "%" escapes), checked
    # on the raw path before it touches the filesystem.
    if ! LC_ALL=C bash -c '[[ "$1" =~ ^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$ ]]' _ "${resource_path}"; then
        printf '%s\n' "Resource not found: ${resource_path}" >&2
        exit 3
    fi
    # Normalize path: replace / with - for directory lookup
    normalized_path="${resource_path//\//-}"

    # Try to find the resource script or static file
    if [ -f "${resources_dir}/${normalized_path}/${normalized_path}.sh" ]; then
        script_path="${resources_dir}/${normalized_path}/${normalized_path}.sh"
    elif [ -f "${resources_dir}/${normalized_path}/${normalized_path}.json" ]; then
        # Static JSON file - just output it
        cat "${resources_dir}/${normalized_path}/${normalized_path}.json"
        exit 0
    elif [ -f "${resources_dir}/${normalized_path}/${normalized_path}.md" ]; then
        # Static markdown file - just output it
        cat "${resources_dir}/${normalized_path}/${normalized_path}.md"
        exit 0
    elif [ -f "${resources_dir}/${resource_path}/${resource_path##*/}.sh" ]; then
        # Try with original path structure
        script_path="${resources_dir}/${resource_path}/${resource_path##*/}.sh"
    fi
fi

if [ -z "${script_path}" ] || [ ! -f "${script_path}" ]; then
    printf '%s\n' "Resource not found: ${resource_path}" >&2
    exit 3
fi

# Execute the resource script
# The script should output JSON content
# Note: Use ${script_args[@]+"${script_args[@]}"} for Bash 3.2 compatibility
# (empty array expansion fails with set -u in older Bash versions)
if [ -x "${script_path}" ]; then
    exec "${script_path}" ${script_args[@]+"${script_args[@]}"}
else
    # Fall back to bash execution if not marked executable
    exec bash "${script_path}" ${script_args[@]+"${script_args[@]}"}
fi
