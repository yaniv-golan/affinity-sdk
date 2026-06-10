#!/usr/bin/env bash
set -euo pipefail

# Lightweight session bootstrap for xaffinity CLI.
#
# Design: This hook runs at SessionStart and must be FAST (<1s).
# Instead of installing xaffinity immediately (~30s pip install), we:
#   1. Fix PATH so $user_bin is reachable
#   2. If xaffinity is missing, drop a self-installing wrapper script
#
# The wrapper defers the actual pip install to first use, which is
# triggered by pre-xaffinity.sh (PreToolUse hook) calling
# `xaffinity session start`. This way SessionStart is near-instant
# and the install cost is paid only when xaffinity is actually needed.
#
# Safe in all environments:
# - Claude Code (macOS): xaffinity already installed — no wrapper created
# - Claude Desktop: not applicable (MCP server, not CLI)
# - Claude Cowork (Linux VM): wrapper created, installs on first use
#
# NOTE: Does NOT export AFFINITY_API_KEY to the environment.
# The key stays in .env and is read per-command via --dotenv.
# This prevents the LLM from reading the key via `env` or `echo`.

# ---------------------------------------------------------------------------
# 1. Determine user bin directory
# ---------------------------------------------------------------------------
user_bin=$(python3 -c "import site, os; print(os.path.join(site.getuserbase(), 'bin'))" 2>/dev/null) \
  || user_bin="$HOME/.local/bin"

mkdir -p "$user_bin"

# ---------------------------------------------------------------------------
# 2. Ensure user_bin is on PATH (current process)
# ---------------------------------------------------------------------------
case ":${PATH:-}:" in
  *":$user_bin:"*) ;;
  *) export PATH="$user_bin:$PATH" ;;
esac

# ---------------------------------------------------------------------------
# 3. Persist PATH for the session via CLAUDE_ENV_FILE
# ---------------------------------------------------------------------------
if [ -n "${CLAUDE_ENV_FILE:-}" ] && ! grep -qF "$user_bin" "$CLAUDE_ENV_FILE" 2>/dev/null; then
  echo "export PATH=\"$user_bin:\$PATH\"" >> "$CLAUDE_ENV_FILE" || true
fi

# ---------------------------------------------------------------------------
# 4. If xaffinity is not available, create a self-installing wrapper
# ---------------------------------------------------------------------------
if ! command -v xaffinity &>/dev/null; then
  # Clear stale state from any previous failed/interrupted install
  rm -f "$HOME/.xaffinity-install-status"
  rmdir "$HOME/.xaffinity-installing" 2>/dev/null || true

  cat > "$user_bin/xaffinity" << 'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail

# Self-installing wrapper for xaffinity CLI.
# On first invocation, installs affinity-sdk[cli] via pip, then exec's
# the real xaffinity binary with the original arguments.

# --- Recursion guard ---
# Prevents infinite loop if pip install somehow triggers this wrapper again.
if [ -n "${_XAFFINITY_BOOTSTRAP:-}" ]; then
  echo "FATAL: xaffinity bootstrap recursion detected" >&2
  exit 99
fi
export _XAFFINITY_BOOTSTRAP=1

# Derive user_bin (same logic as session-setup.sh)
user_bin=$(python3 -c "import site, os; print(os.path.join(site.getuserbase(), 'bin'))" 2>/dev/null) \
  || user_bin="$HOME/.local/bin"

lockdir="$HOME/.xaffinity-installing"
marker="$HOME/.xaffinity-install-status"

# --- Try to acquire install lock ---
if mkdir "$lockdir" 2>/dev/null; then
  # We own the lock — clean up on exit
  trap 'rmdir "$lockdir" 2>/dev/null || true' EXIT

  echo "Installing xaffinity CLI (first use)..." >&2
  if pip install --user "affinity-sdk[cli]" >&2; then
    echo "INSTALLED" > "$marker"
    echo "xaffinity installed successfully." >&2
  else
    echo "INSTALL_FAILED" > "$marker"
    echo "ERROR: xaffinity installation failed." >&2
    exit 1
  fi

  # pip should have overwritten this wrapper with the real entry point.
  # If not, the recursion guard (_XAFFINITY_BOOTSTRAP) will catch it.
  exec "$user_bin/xaffinity" "$@"
else
  # Another process holds the lock — wait for it to finish
  echo "Another process is installing xaffinity, waiting..." >&2
  elapsed=0
  while [ $elapsed -lt 45 ]; do
    sleep 1
    elapsed=$((elapsed + 1))

    # Check if install completed
    if [ -f "$marker" ]; then
      status=$(cat "$marker")
      if [ "$status" = "INSTALLED" ]; then
        exec "$user_bin/xaffinity" "$@"
      else
        echo "ERROR: concurrent xaffinity installation failed." >&2
        exit 1
      fi
    fi
  done

  # Final check — install may have completed during the last sleep
  if [ -f "$marker" ] && grep -qF "INSTALLED" "$marker" 2>/dev/null; then
    exec "$user_bin/xaffinity" "$@"
  fi

  echo "ERROR: timed out waiting for xaffinity installation (45s)." >&2
  exit 1
fi
WRAPPER

  chmod +x "$user_bin/xaffinity"
  echo "xaffinity wrapper installed at $user_bin/xaffinity (will install on first use)" >&2
fi

exit 0
