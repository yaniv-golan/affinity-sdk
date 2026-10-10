# cowork-harness tests for the xaffinity-cli plugin

These scenarios run the CLI plugin (skill `xaffinity-cli-usage` and its hooks) the way Claude
Cowork runs it, using [cowork-harness](https://github.com/yaniv-golan/cowork-harness) (an
unofficial, independent project). They check that an agent picks the right commands, reads the
right JSON, stays read-only, and stops with setup guidance when no key is configured.

No Affinity account is involved. A test image (`image/`) holds this repo's CLI, pointed at a
small fake Affinity API that serves invented data (`image/fakeaff/build_fixtures.py`): Acme
Robotics and Bolt Analytics, lists "Dealflow" and "Portfolio Watch", a Status field and its
history. A connected folder containing `xaffinity-test-key` makes the CLI count as configured.

| Scenario | Checks |
|---|---|
| `company-lists-status` | lists and Status come from `.data.listEntries` (`company get --expand list-entries` / `list export`) |
| `status-history` | "who changed it" routes to `field history` |
| `no-key-skill-path` | no key: `config check-key`, then setup guidance, no data calls |
| `hook-blocks-without-key` | no key, direct command: the PreToolUse hook denies it |

## CI

`replay` re-checks the recorded runs (no token, Docker or model), and `verify-cassettes` fails
when the plugin, a scenario or a session changed since recording (stale) or a cassette holds
data the privacy scan flags. **Any edit to `plugins/xaffinity-cli` makes CI fail until the
cassettes are re-recorded**, which takes a maintainer's machine (below): a replay alone would keep
passing while the skill it is meant to cover drifts.

## Recording

Needs Docker, Claude Desktop (the harness uses its staged agent binary), and a Claude token in a
`.env` outside the repo. Commit your plugin changes first (the harness stages tracked files):

```bash
claude setup-token                                    # then put the token in that file:
echo "CLAUDE_CODE_OAUTH_TOKEN=<token>" > ~/.config/cowork-harness/.env
COWORK_TOKEN_ENV=~/.config/cowork-harness/.env tests/cowork/record.sh --stale   # stale ones
COWORK_TOKEN_ENV=~/.config/cowork-harness/.env tests/cowork/record.sh            # all
```

Each scenario is one paid model run (`MAX_BUDGET_USD`, default 5, caps a run from history).

## Scope

- **Container tier:** the agent loop runs in the sandbox, so hooks and the CLI run there.
  Cowork's default host-loop tier (hooks on the host, shell `mcp__workspace__bash`) isn't covered.
- **Frozen CLI:** replay never re-runs the CLI and the cassette fingerprint doesn't cover
  `affinity/cli`; CLI behaviour is covered by the pytest suite.
- **Key path:** the key comes from the test folder, not from the plugin's credential settings
  (the harness doesn't model those).
- Fake API gaps show up as `[404] fake: no fixture` in a tool result; every scenario asserts
  there are none. Add routes in `build_fixtures.py`, run it, rebuild.
