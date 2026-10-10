#!/usr/bin/env bash
# xaffinity for the cowork-harness test image: the real CLI, pointed at the fake Affinity API
# (synthetic data, no key needed). Starts the fake API on first use.
set -euo pipefail
port=8765
if ! python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.3); sys.exit(s.connect_ex(('127.0.0.1',$port)))" 2>/dev/null; then
    setsid nohup python3 /opt/fakeaff/server.py "$port" </dev/null >/dev/null 2>&1 &
    for _ in $(seq 1 50); do
        python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.2); sys.exit(s.connect_ex(('127.0.0.1',$port)))" 2>/dev/null && break
        sleep 0.1
    done
fi
# Test key: a connected folder carrying `xaffinity-test-key` stands in for a configured key
# (the harness stages only git-tracked files, and the shell starts in the session root).
if [[ -z "${AFFINITY_API_KEY_FILE:-}" ]]; then
    for key_file in /sessions/*/mnt/*/xaffinity-test-key; do
        [[ -f "$key_file" ]] && export AFFINITY_API_KEY_FILE="$key_file" && break
    done
fi
export AFFINITY_V1_BASE_URL="http://127.0.0.1:$port"
export AFFINITY_V2_BASE_URL="http://127.0.0.1:$port/v2"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}" no_proxy="127.0.0.1,localhost${no_proxy:+,$no_proxy}"
exec /usr/local/bin/xaffinity.real "$@"
