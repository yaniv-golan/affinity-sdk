# API key setup and session cache

Read this when `xaffinity config check-key --json` reports `"configured": false`.

## Key resolution order

The CLI resolves the API key in this order:

1. `--api-key-file <path>` or `--api-key-stdin` CLI flags (when one is used and an env var below
   is also set, the CLI warns that the env var is ignored)
2. `AFFINITY_API_KEY` env var
3. `AFFINITY_API_KEY_FILE` env var (path to a file containing the key, or a `.env` file with an
   `AFFINITY_API_KEY=` line — including a 1Password Environments locally mounted `.env` file, which the
   CLI waits up to 30 s for while 1Password asks the user to approve the read)
4. `AFFINITY_API_KEY_COMMAND` env var (shell command whose stdout is the key — git-credential-helper style; works with `op` / `pass` / `vault` / macOS `security`)
5. `api_key` in the user config file (`config.toml`, written by `xaffinity config setup-key --scope user`)

`xaffinity config setup-key --scope project` writes `AFFINITY_API_KEY` to a `.env` file in the current
directory instead. The CLI reads it only with `--dotenv` (it then sets the step 2 env var), so commands need
`xaffinity --dotenv …`.

## Helping the user set up

For most users:

- Tell them: "You need to configure an Affinity API key first."
- Direct them: Affinity -> Settings -> API -> Generate New Key
- Tell them to run `xaffinity config setup-key` themselves. Do NOT run it for them — it is interactive.

For users with an existing secret manager, suggest one of these instead (the key stays out of
config files):

- **1Password Environments** (Mac/Linux, 1Password app running): the user creates an Environment
  holding `AFFINITY_API_KEY` and a locally mounted `.env` file for it in the 1Password app, then sets `AFFINITY_API_KEY_FILE` to its path.
  The first read shows a 1Password approval prompt, or an unlock prompt if 1Password is locked;
  the CLI waits up to 30 s. A timeout error naming 1Password means the app isn't running or the
  prompt wasn't answered — ask the user to approve or unlock, then retry. `check-key` reports it
  configured without reading it. Works on the Mac itself, not inside Cowork's VM (a named pipe
  doesn't cross its folder mount).
- **`AFFINITY_API_KEY_COMMAND`** for vault, pass, Keychain or the 1Password CLI
  (`op read op://…`): a command whose stdout is the key.

## Claude Cowork

**Installing the CLI.** Cowork's VM may not have `xaffinity` (`command not found`). Install it
once per task: `pip install --user -q --no-warn-script-location "affinity-sdk[cli]==2.0.4"` (about
10s). It lands in `~/.local/bin`, which is not on PATH, and each bash call is a fresh shell, so start every
xaffinity call with `export PATH="$HOME/.local/bin:$PATH";` — then commands and check-key's
`pattern` work as written (or call `~/.local/bin/xaffinity` by its full path). The install lasts
for the task only; a new task starts without it, so check again there. If pip can't reach PyPI
(the organization's network policy), stop and tell the user: they can allow PyPI for Cowork, or
use the xaffinity MCP extension instead.

**Finding the key.** If the host CLI is configured but `check-key`
returns `configured: false`, the key likely lives in a host-only location (env var, `~/.config/`,
keychain, host-only credential helper) that the VM does not mount. Only connected folders reach
it, and their path matters:

- Each bash call starts in the session root (`/sessions/<id>`), not in the connected folder, and
  nothing carries over between calls (a `cd` or `export` lasts for that one command). Connected
  folders are below it: `ls -d /sessions/*/mnt/*/`. `<id>` changes every session, so find the
  path in each session; don't reuse one from an earlier session.
- **Key in a connected folder's `.env`** (`AFFINITY_API_KEY=…`): name the file on every command,
  `xaffinity --dotenv --env-file "/sessions/<id>/mnt/<folder>/.env" …`. Check it with
  `xaffinity --dotenv --env-file "/sessions/<id>/mnt/<folder>/.env" config check-key --json`; its
  `pattern` then carries the `--env-file` part to reuse. A plain `--dotenv` searches upward from
  the session root and never finds it.
- **Key file** (just the key, e.g. `<folder>/.xaffinity-key`, gitignored): prefix each command,
  `AFFINITY_API_KEY_FILE="/sessions/<id>/mnt/<folder>/.xaffinity-key" xaffinity …`. Keep the
  quotes on these paths: folder names can contain spaces.
- A 1Password locally mounted `.env` does not work inside the VM (the named pipe doesn't cross
  the folder mount). If the xaffinity MCP extension is installed, it already holds the key.

`AFFINITY_API_KEY_COMMAND` is generally NOT useful in Cowork because the helper binaries (`op`,
`pass`, `vault`, `security`) typically aren't installed in the VM.

## Session cache

Started automatically on first xaffinity use. If `AFFINITY_SESSION_CACHE` is not set, it is
initialized when you run your first xaffinity command — this shares metadata across commands and
avoids redundant API calls.
