# Authentication

The SDK authenticates using an Affinity API key.

```python
from affinity import Affinity

with Affinity(api_key="your-api-key") as client:
    me = client.whoami()
    print(me.user.email)
```

## Resolution chain

The SDK resolves the API key through the following chain (first non-empty value wins):

1. **Explicit constructor arg** — `Affinity(api_key="…")` or `--api-key` CLI flag
2. **`AFFINITY_API_KEY`** — standard environment variable
3. **`AFFINITY_API_KEY_FILE`** — path to a file containing the key, or a `.env` file / named pipe with `AFFINITY_API_KEY=` (12-factor / Docker secrets / 1Password)
4. **`AFFINITY_API_KEY_COMMAND`** — shell command whose stdout is the key (credential-helper)
5. **`--api-key-file <path>`** or **`--api-key-stdin`** — CLI flags
6. **`xaffinity config setup-key`** — saved to the system keychain

Empty string is treated as unset at every step (safe against stale `export AFFINITY_API_KEY=` lines).

## Environment variables

If you prefer reading from the environment:

```python
from affinity import Affinity

# Reads AFFINITY_API_KEY by default
client = Affinity.from_env()

# Use a custom environment variable name
client = Affinity.from_env(env_var="MY_AFFINITY_KEY")
```

For local development, you can load a `.env` file (requires `python-dotenv`):

```python
from affinity import Affinity

# Load .env from current directory
client = Affinity.from_env(load_dotenv=True)

# Load from a specific path
client = Affinity.from_env(load_dotenv=True, dotenv_path=".env.local")
```

### `AFFINITY_API_KEY_FILE` — file-based secrets

Set this env var to the path of a file containing your API key. Used by Docker secrets,
Kubernetes mounted Secrets, and Hashicorp Vault agent sidecars.

```bash
# Docker
docker run -e AFFINITY_API_KEY_FILE=/run/secrets/affinity_api_key …

# Kubernetes — mount a Secret as a file and set the env var
env:
  - name: AFFINITY_API_KEY_FILE
    value: /etc/secrets/affinity-api-key
```

The file can hold just the key, or be a `.env` file: the `AFFINITY_API_KEY=` line is used
(`export` and quotes are allowed). The same applies to `--api-key-file`.

**1Password Environments.** A 1Password locally mounted `.env` is a named pipe that 1Password
fills when it is read, after you approve the read. Point the variable at it:

```bash
export AFFINITY_API_KEY_FILE=~/projects/crm/.env   # the path you chose when mounting it
xaffinity whoami
```

The CLI waits up to 30 seconds for 1Password (time to approve). If 1Password isn't running or
the read isn't approved, it stops with a message saying so. The key is never written to disk.
`config check-key` reports a named pipe as configured without reading it.

On Posix systems, a `UserWarning` is emitted if the file is group- or world-readable
(mode `0644` or looser). Use `chmod 600` to silence it. The check is a no-op on Windows.
An empty file or missing path raises `ConfigError`.

### `AFFINITY_API_KEY_COMMAND` — command-based secrets

Set this env var to a shell command. The SDK runs it at startup and uses its stdout as the key.
Follows the same convention as `git credential.helper`, `gpg --passphrase-program`, and similar tools.

```bash
# 1Password CLI
export AFFINITY_API_KEY_COMMAND="op read op://Personal/Affinity/credential"

# macOS Keychain
export AFFINITY_API_KEY_COMMAND="security find-generic-password -a affinity -w"

# pass (Unix password manager)
export AFFINITY_API_KEY_COMMAND="pass show affinity/api-key"

# HashiCorp Vault
export AFFINITY_API_KEY_COMMAND="vault kv get -field=api_key secret/affinity"
```

The default timeout is 30 seconds; override with `AFFINITY_API_KEY_COMMAND_TIMEOUT=<seconds>`.
A non-zero exit code, empty stdout, or timeout raises `ConfigError` (stderr is included in the
error message, capped at 500 chars).

For defensive "no writes" usage (scripts, audits), disable writes via policy:

```python
from affinity import Affinity
from affinity.policies import Policies, WritePolicy

client = Affinity.from_env(policies=Policies(write=WritePolicy.DENY))
```

## CLI Authentication

For the CLI, use the built-in setup commands:

```bash
# Check if a key is configured
xaffinity config check-key

# Set up a new key securely (hidden input)
xaffinity config setup-key
```

See [CLI Authentication](../cli/index.md#authentication) for details.

## Caveats

### `.env` files can silently shadow `AFFINITY_API_KEY_FILE` / `_COMMAND`

`load_dotenv=True` (SDK) and `--dotenv` (CLI) load `.env` files **before** the
resolver reads any environment variable. By default, dotenv values do not override
already-set shell variables (`dotenv_override=False`).

The resolver checks `AFFINITY_API_KEY` *first*, then `AFFINITY_API_KEY_FILE`, then
`AFFINITY_API_KEY_COMMAND`. So if your `.env` file contains
`AFFINITY_API_KEY=…` and you also have `AFFINITY_API_KEY_FILE` set in your shell:

- After dotenv load, the shell-set `AFFINITY_API_KEY_FILE` is unchanged.
- But `AFFINITY_API_KEY` is now also set (from `.env`).
- The resolver returns the `.env` value at step 2; the file at the `_FILE` path
  is **never read**.

This is rarely intended. If you mix dotenv and `_FILE`/`_COMMAND`, either:

- Keep one mechanism per environment, or
- Set `dotenv_override=True` deliberately and own the precedence in the `.env` file.

### File-on-disk caveat for `AFFINITY_API_KEY_FILE`

The SDK opens the file in three syscalls (existence check, permission stat, read).
A co-tenant with write access to the *parent directory* could in principle swap
the file between checks. This is the standard Unix file-handle race; the
mitigation is to keep the key file in a directory you control (your home dir,
a locked-down `/etc` subtree, or a Kubernetes-mounted Secret volume), not in a
world-writable temp dir.

## Next steps

- [Getting started](../getting-started.md)
- [Configuration](configuration.md)
- [Examples](../examples.md)
- [Errors & retries](errors-and-retries.md)
- [API reference](../reference/client.md)
