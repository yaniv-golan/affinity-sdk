#!/usr/bin/env bash
# URI helpers shared across CLI and runtime.

set -euo pipefail

mcp_uri_url_encode() {
	local value="$1"
	local output=""
	local i char hex
	local LC_ALL=C
	for ((i = 0; i < ${#value}; i++)); do
		char="${value:i:1}"
		case "${char}" in
		[a-zA-Z0-9.~_-] | / | :)
			output+="${char}"
			;;
		*)
			printf -v hex '%02X' "'${char}"
			output+="%${hex}"
			;;
		esac
	done
	printf '%s' "${output}"
}

mcp_uri_file_uri_from_path() {
	local path="$1"
	local dir base abs_path
	if ! dir="$(cd "$(dirname "${path}")" >/dev/null 2>&1 && pwd)"; then
		printf 'Unable to resolve resource path %s\n' "${path}" >&2
		return 1
	fi
	base="$(basename "${path}")"
	abs_path="${dir}/${base}"
	mcp_uri_url_encode "file://${abs_path}"
}
