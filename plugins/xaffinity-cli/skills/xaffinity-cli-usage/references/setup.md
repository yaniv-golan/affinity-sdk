# API key setup and session cache

Read this when `xaffinity config check-key --json` reports `"configured": false`.

## Key resolution order

The CLI resolves the API key in this order:

1. `AFFINITY_API_KEY` env var
2. `AFFINITY_API_KEY_FILE` env var (path to a file containing the key — Docker secrets / k8s convention)
3. `AFFINITY_API_KEY_COMMAND` env var (shell command whose stdout is the key — git-credential-helper style; works with `op` / `pass` / `vault` / macOS `security`)
4. `--api-key-file <path>` or `--api-key-stdin` CLI flags
5. `xaffinity config setup-key` config file (saved to system keychain on supported platforms)

## Helping the user set up

For most users:

- Tell them: "You need to configure an Affinity API key first."
- Direct them: Affinity -> Settings -> API -> Generate New Key
- Tell them to run `xaffinity config setup-key` themselves. Do NOT run it for them — it is interactive.

For users with an existing secret manager (1Password, vault, pass, Keychain), suggest
`AFFINITY_API_KEY_COMMAND` as a credential-helper-style integration instead.

## Claude Cowork edge case

If you are running inside a Claude Cowork session and the host CLI is configured but `check-key`
returns `configured: false`, the key likely lives in a host-only location (env var, `~/.config/`,
keychain, host-only credential helper) that the microVM does not mount. Two host-portable options:

- **Project `.env` + `--dotenv`** — create a project-scope `.env` file with `AFFINITY_API_KEY=…`.
  The project workdir IS mounted into the VM. This is the most common Cowork path.
- **`AFFINITY_API_KEY_FILE`** — write the key to a file in the project workdir (e.g.,
  `.xaffinity-key`, gitignored, `chmod 600`) and `export AFFINITY_API_KEY_FILE=/path/to/.xaffinity-key`.
  Same reach as `.env`, but works without `--dotenv` on the command line.

`AFFINITY_API_KEY_COMMAND` is generally NOT useful in Cowork because the helper binaries (`op`,
`pass`, `vault`, `security`) typically aren't installed in the VM.

## Session cache

Started automatically on first xaffinity use. If `AFFINITY_SESSION_CACHE` is not set, it is
initialized when you run your first xaffinity command — this shares metadata across commands and
avoids redundant API calls.
