#!/usr/bin/env bash
# Remote authentication guard for proxied deployments.

set -euo pipefail

: "${MCPBASH_REMOTE_TOKEN_EXPECTED:=}"
: "${MCPBASH_REMOTE_TOKEN_KEY:=}"
: "${MCPBASH_REMOTE_TOKEN_FALLBACK_KEY:=}"
: "${MCPBASH_REMOTE_TOKEN_ENABLED:=false}"
: "${MCPBASH_REMOTE_TOKEN_DEBUG_WARNED:=false}"

mcp_auth_rate_limit_failures() {
	if [ -z "${MCPBASH_STATE_DIR:-}" ]; then
		return 0
	fi
	if [ "${MCPBASH_REMOTE_TOKEN_ENABLED:-false}" != "true" ]; then
		return 0
	fi
	local max_fail="${MCPBASH_REMOTE_TOKEN_MAX_FAILURES_PER_MIN:-10}"
	case "${max_fail}" in
	'' | *[!0-9]*) max_fail=10 ;;
	0) return 0 ;;
	esac
	local file="${MCPBASH_STATE_DIR}/auth.remote_token.fail.log"
	local lock_name="auth.remote_token.fail"
	local now
	local preserved=""
	local count=0
	local line

	mcp_lock_acquire "${lock_name}"
	now="$(date +%s)"
	if [ -f "${file}" ]; then
		while IFS= read -r line; do
			[ -z "${line}" ] && continue
			if [ $((now - line)) -lt 60 ]; then
				preserved="${preserved}${line}"$'\n'
				count=$((count + 1))
			fi
		done <"${file}"
	fi
	if [ "${count}" -ge "${max_fail}" ]; then
		printf '%s' "${preserved}" >"${file}"
		mcp_lock_release "${lock_name}"
		return 1
	fi
	printf '%s%s\n' "${preserved}" "${now}" >"${file}"
	mcp_lock_release "${lock_name}"
	return 0
}

mcp_auth_init() {
	local token="${MCPBASH_REMOTE_TOKEN:-}"
	local key="${MCPBASH_REMOTE_TOKEN_KEY:-mcpbash/remoteToken}"
	local fallback="${MCPBASH_REMOTE_TOKEN_FALLBACK_KEY:-remoteToken}"

	if [ -z "${token}" ]; then
		MCPBASH_REMOTE_TOKEN_ENABLED=false
		return 0
	fi

	if [ "${MCPBASH_JSON_TOOL:-none}" = "none" ] || [ -z "${MCPBASH_JSON_TOOL_BIN:-}" ]; then
		printf '%s\n' "mcp-bash: MCPBASH_REMOTE_TOKEN is set but JSON tooling is unavailable; cannot enforce remote token guard." >&2
		return 1
	fi

	if [ -z "${key}" ]; then
		key="mcpbash/remoteToken"
	fi

	if [ "${#token}" -lt 32 ]; then
		printf '%s\n' "mcp-bash: MCPBASH_REMOTE_TOKEN must be at least 32 characters; refusing weak shared secret." >&2
		return 1
	fi

	MCPBASH_REMOTE_TOKEN_EXPECTED="${token}"
	MCPBASH_REMOTE_TOKEN_KEY="${key}"
	MCPBASH_REMOTE_TOKEN_FALLBACK_KEY="${fallback}"
	MCPBASH_REMOTE_TOKEN_ENABLED=true

	if [ "${MCPBASH_DEBUG_PAYLOADS:-false}" = "true" ] && [ "${MCPBASH_REMOTE_TOKEN_DEBUG_WARNED}" != "true" ]; then
		MCPBASH_REMOTE_TOKEN_DEBUG_WARNED="true"
		if mcp_runtime_log_allowed; then
			printf '%s\n' "mcp-bash: payload debug logging is enabled; remote tokens will be redacted in debug logs but disable debug in production." >&2
		fi
	fi

	return 0
}

mcp_auth_is_enabled() {
	[ "${MCPBASH_REMOTE_TOKEN_ENABLED:-false}" = "true" ]
}

mcp_auth_constant_time_equals() {
	local expected="$1"
	local provided="$2"
	local expected_len=${#expected}
	local provided_len=${#provided}
	local diff=$((expected_len ^ provided_len))
	local max_len="${expected_len}"
	local i=0

	if [ "${provided_len}" -gt "${max_len}" ]; then
		max_len="${provided_len}"
	fi

	while [ "${i}" -lt "${max_len}" ]; do
		local e=0 p=0
		if [ "${i}" -lt "${expected_len}" ]; then
			LC_ALL=C printf -v e '%d' "'${expected:i:1}"
		fi
		if [ "${i}" -lt "${provided_len}" ]; then
			LC_ALL=C printf -v p '%d' "'${provided:i:1}"
		fi
		diff=$((diff | (e ^ p)))
		i=$((i + 1))
	done

	[ "${diff}" -eq 0 ]
}

mcp_auth_extract_remote_token() {
	local json_line="$1"
	local key="${MCPBASH_REMOTE_TOKEN_KEY:-mcpbash/remoteToken}"
	local fallback="${MCPBASH_REMOTE_TOKEN_FALLBACK_KEY:-remoteToken}"
	local value=""

	value="$(
		{ printf '%s' "${json_line}" | "${MCPBASH_JSON_TOOL_BIN}" -r --arg key "${key}" --arg fallback "${fallback}" '
			def grab($k): ((.params._meta // {})[$k]? // empty | strings);
			(grab($key) // (if $fallback != "" then grab($fallback) else "" end))
		'; } 2>/dev/null
	)"

	printf '%s' "${value}"
}

mcp_auth_emit_error() {
	local id_json="$1"
	local message="$2"

	# Notifications must not receive responses; skip when id is absent.
	case "${id_json}" in
	null | '') return 0 ;;
	esac

	rpc_send_line "$(mcp_core_build_error_response "${id_json}" -32602 "${message}" "")"
}

mcp_auth_guard_request() {
	local json_line="$1"
	local method="$2"
	local id_json="$3"

	if ! mcp_auth_is_enabled; then
		return 0
	fi

	local presented
	presented="$(mcp_auth_extract_remote_token "${json_line}")"

	if [ -z "${presented}" ] || ! mcp_auth_constant_time_equals "${MCPBASH_REMOTE_TOKEN_EXPECTED}" "${presented}"; then
		if mcp_auth_rate_limit_failures; then
			if mcp_logging_is_enabled "warning"; then
				mcp_logging_warning "mcp.auth" "Remote token rejected method=${method}"
			fi
			mcp_auth_emit_error "${id_json:-null}" "Remote token missing or invalid"
			return 1
		fi
		mcp_auth_emit_error "${id_json:-null}" "Remote token missing or invalid (throttled)"
		return 1
	fi

	return 0
}
