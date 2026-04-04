#!/usr/bin/env bash
# Simple demo resource provider that echoes the URI payload.

set -euo pipefail

uri="${1:-}"
if [ -z "${uri}" ]; then
	printf '%s\n' "echo provider requires echo://<payload>" >&2
	exit 4
fi

case "${uri}" in
echo://*)
	# Minimal percent-decoding for spaces to keep examples readable.
	payload="${uri#echo://}"
	payload="${payload//%20/ }"
	printf '%s' "${payload}"
	;;
*)
	printf '%s\n' "Unsupported URI scheme for echo provider" >&2
	exit 4
	;;
esac
