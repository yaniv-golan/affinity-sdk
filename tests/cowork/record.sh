#!/usr/bin/env bash
# Record (or re-record) the xaffinity-cli plugin's cowork-harness cassettes.
#
#   tests/cowork/record.sh                 # record every scenario
#   tests/cowork/record.sh <name>...       # record these scenarios (file stems)
#   tests/cowork/record.sh --stale         # re-record only cassettes whose plugin/scenario drifted
#
# Needs: Docker, Claude Desktop installed (its staged agent binary), cowork-harness (pinned
# below), and a Claude token in a .env OUTSIDE this repo:
#   claude setup-token   →   echo "CLAUDE_CODE_OAUTH_TOKEN=..." > ~/.config/cowork-harness/.env
# then: COWORK_TOKEN_ENV=~/.config/cowork-harness/.env tests/cowork/record.sh
# Each scenario is a paid model run.
set -euo pipefail

HARNESS_VERSION="4.7.1"
MAX_BUDGET_USD="${MAX_BUDGET_USD:-5}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
token_env="${COWORK_TOKEN_ENV:?set COWORK_TOKEN_ENV to the .env file holding CLAUDE_CODE_OAUTH_TOKEN (outside the repo)}"

harness() { npx --yes "cowork-harness@$HARNESS_VERSION" --dotenv "$token_env" "$@"; }

# The harness stages git-tracked files only, and a cassette fingerprints the committed plugin.
if [[ -n "$(git -C "$repo" status --porcelain -- plugins/xaffinity-cli tests/cowork/scenarios tests/cowork/sessions tests/cowork/fixtures)" ]]; then
    echo "Commit plugins/xaffinity-cli and tests/cowork first (the harness stages tracked files only)." >&2
    exit 1
fi

# Test image: this checkout's CLI + the fake Affinity API (synthetic data).
rm -rf "$here/image/dist"
python3 -m pip wheel --no-deps -q -w "$here/image/dist" "$repo"
tag="xaffinity-cowork-test:$(git -C "$repo" rev-parse --short HEAD)"
docker build --platform linux/arm64 -q -t "$tag" "$here/image" >/dev/null
export COWORK_AGENT_IMAGE="$tag"
echo "COWORK_AGENT_IMAGE=$COWORK_AGENT_IMAGE"

cd "$here"
if [[ "${1:-}" == "--stale" ]]; then
    harness record cassettes/ --rerecord-stale --max-budget-usd "$MAX_BUDGET_USD"
    exit
fi
names=("$@")
if [[ ${#names[@]} -eq 0 ]]; then
    for f in scenarios/*.yaml; do names+=("$(basename "$f" .yaml)"); done
fi
for name in "${names[@]}"; do
    harness record "scenarios/$name.yaml" --out "cassettes/$name.cassette.json" \
        --max-budget-usd "$MAX_BUDGET_USD"
done
harness verify-cassettes cassettes/ --allow-patterns-file privacy-allow.txt
