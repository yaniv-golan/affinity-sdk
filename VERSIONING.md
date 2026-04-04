# Versioning Policy

This document defines how versions are managed across the affinity-api-x repository.

## Overview

| Component | Version Source | Versioning |
|-----------|----------------|------------|
| SDK + CLI | `pyproject.toml` | Single source of truth, SemVer |
| SDK Plugin | `plugins/affinity-sdk/.claude-plugin/plugin.json` | Independent, SemVer |
| CLI Plugin | `plugins/xaffinity-cli/.claude-plugin/plugin.json` | Independent, SemVer |
| MCP Server | `mcp/VERSION` | Independent, declares CLI compatibility |
| MCP Plugin | `mcp/.claude-plugin/plugin.json` | Independent, SemVer |

## Semantic Versioning Rules

We follow [SemVer 2.0.0](https://semver.org/). Given version `MAJOR.MINOR.PATCH`:

### When to Bump MAJOR (breaking change)
- Removing or renaming a public SDK class/method
- Changing CLI command names or required arguments
- Changing JSON output structure in incompatible ways
- Removing CLI flags or options

### When to Bump MINOR (new feature, backwards-compatible)
- Adding new SDK classes/methods
- Adding new CLI commands or optional flags
- Adding new fields to JSON output (additive)
- Performance improvements with no API changes

### When to Bump PATCH (bug fix, backwards-compatible)
- Fixing bugs without changing the API
- Documentation improvements
- Internal refactoring with no external impact

## Plugin Versioning (Independent from SDK)

Plugins (SDK, CLI, MCP) are distributed via Git marketplaces, not PyPI. Their versions
are independent from the SDK/CLI version in `pyproject.toml`. This follows
[Anthropic's plugin guidance](https://code.claude.com/docs/en/plugins-reference#version-management):
update the plugin version before distributing changes.

### Why Independent Versioning?

Plugins contain hooks, skills, and configuration that change independently from the
SDK/CLI Python code. Examples:
- A security fix to a hook script changes plugin behavior but not the SDK
- A skill rewrite improves LLM guidance but doesn't touch Python code
- Metadata changes (descriptions, author) are plugin-only

Tying plugin versions to the SDK version would either:
- Trigger unnecessary PyPI releases for plugin-only changes, or
- Leave plugin changes unversioned (confusing for users and debugging)

### When to Bump Plugin Versions

| Change | Bump |
|--------|------|
| Hook behavior change (e.g., security fix) | PATCH |
| Skill content rewrite | PATCH |
| New skill or hook added | MINOR |
| Skill or hook removed | MAJOR |
| Metadata-only changes (description, author) | No bump needed |

### Plugin Version Files

| Plugin | Version Source |
|--------|---------------|
| SDK Plugin | `plugins/affinity-sdk/.claude-plugin/plugin.json` → `version` field |
| CLI Plugin | `plugins/xaffinity-cli/.claude-plugin/plugin.json` → `version` field |
| MCP Plugin | `mcp/.claude-plugin/plugin.json` → `version` field |

Plugin versions are **not** auto-synced from `pyproject.toml`. Update them manually
when plugin content changes. The pre-commit hook validates that plugin.json files
have valid JSON but does not enforce version sync.

### Relationship to SDK Version

- Plugin versions MAY diverge from the SDK version
- When an SDK release also changes plugin content, bump both
- Plugin cache invalidation is based on git commit hash, not version number,
  so version bumps are for human traceability, not distribution mechanics

## Pre-1.0 Versioning (Current State)

**SDK is currently at 0.x.y** — per [SemVer spec item 4](https://semver.org/#spec-item-4):
> *"Major version zero (0.y.z) is for initial development. Anything MAY change at any time."*

This means:
- MINOR bumps (0.6 → 0.7) MAY include breaking changes
- PATCH bumps (0.6.5 → 0.6.6) should be backwards-compatible
- MCP must track CLI minor version for compatibility

## CLI Changes That Affect MCP

The MCP server shells out to CLI commands. These changes require MCP updates:

| CLI Change | MCP Impact | Action Required |
|------------|------------|-----------------|
| New command added | None | Optional: add MCP tool |
| New optional flag | None | Optional: use new flag |
| JSON output field added | None | Backwards-compatible |
| JSON output field removed | **Breaking** | Update MCP, bump COMPATIBILITY |
| JSON output structure changed | **Breaking** | Update MCP, bump COMPATIBILITY |
| Command renamed | **Breaking** | Update MCP, bump COMPATIBILITY |
| Required flag added | **Breaking** | Update MCP, bump COMPATIBILITY |

## How to Release

Releases are triggered automatically when version files are updated on `main`.

### How Release Detection Works

The release detection workflow compares the current version in version files against existing release tags:

1. **After CI passes** on `main`, the release detection workflow runs
2. It reads the SDK version from `pyproject.toml` and MCP version from `mcp/VERSION`
3. It checks if release tags exist:
   - SDK: checks for `v{version}` tag (e.g., `v0.9.2`)
   - MCP: checks for `mcp-v{version}` tag (e.g., `mcp-v1.7.6`)
4. If no tag exists for the current version, it triggers the release workflow via `workflow_dispatch` API

The release workflows are triggered via GitHub's workflow_dispatch API (not reusable workflows). This enables:
- **PyPI attestations**: Full provenance attestation support for all releases
- **GITHUB_TOKEN**: No PATs or GitHub Apps required
- **Tag creation post-publish**: Tags are created after successful PyPI publish (SDK) or GitHub release (MCP)

This approach is robust regardless of how many commits are pushed together, squash merges, rebases, or any other git workflow.

### SDK Release

1. Update version in `pyproject.toml`
2. If plugin content also changed: bump plugin versions in their `plugin.json` files
3. Run pre-commit
4. Update `CHANGELOG.md` with changes (see [Changelog Format](#changelog-format) below)
5. If CLI output changed: check MCP compatibility
6. Commit and push to `main` (or merge PR)
7. **Release runs automatically** — tag created post-release

SDK releases include MCPB bundles (built from the same commit) for convenience, so users don't need to find separate MCP releases.

### MCP Release

1. Update `mcp/VERSION`
2. If MCP plugin content also changed: bump version in `mcp/.claude-plugin/plugin.json`
3. Update `mcp/CHANGELOG.md` (see [Changelog Format](#changelog-format) below)
4. If CLI requirements changed: update `mcp/COMPATIBILITY`
5. Run pre-commit (syncs server.meta.json from `mcp/VERSION`)
6. Commit and push to `main` (or merge PR)
7. **Release runs automatically** — tag created post-release

### Manual Tag Release

You can also trigger releases via tags:

```bash
# SDK
git tag -a v0.9.1 -m "v0.9.1" && git push origin v0.9.1

# MCP
git tag -a mcp-v1.7.6 -m "MCP v1.7.6" && git push origin mcp-v1.7.6
```

## Tag Naming

| Component | Tag Pattern | Example |
|-----------|-------------|---------|
| SDK | `vX.Y.Z` | `v0.9.0` |
| MCP | `mcp-vX.Y.Z` | `mcp-v1.7.5` |

## Testing MCP Compatibility

Before releasing a CLI change that modifies JSON output:

```bash
# 1. Build and install the new CLI locally
pip install -e .

# 2. Run MCP tools manually to verify they still work
cd mcp
./xaffinity-mcp.sh validate

# 3. Test specific tools
source lib/common.sh
run_xaffinity_readonly person ls --query "test" --output json --quiet
```

## Version File Locations

| File | Purpose | Updated By |
|------|---------|------------|
| `pyproject.toml` | SDK/CLI version | Manual |
| `plugins/affinity-sdk/.claude-plugin/plugin.json` | SDK plugin version | Manual |
| `plugins/xaffinity-cli/.claude-plugin/plugin.json` | CLI plugin version | Manual |
| `mcp/VERSION` | MCP distribution version | Manual |
| `mcp/server.d/server.meta.json` | MCP server metadata | Pre-commit hook (from `mcp/VERSION`) |
| `mcp/.claude-plugin/plugin.json` | MCP plugin version | Manual |
| `mcp/COMPATIBILITY` | CLI version requirements | Manual |
| `mcp/mcpb.conf` | MCPB bundle config | Manual (version from VERSION) |
| `mcp/mcp-bash.lock` | MCP-bash framework version + commit hash | Manual |
| `mcp/mcp-publisher.lock` | MCP Registry publisher CLI version | Manual |
| `mcp/server.json` | MCP Registry metadata template (patched by CI) | Manual (version/hash patched at release time) |

## MCP-Bash Framework Vendoring

The MCP server vendors the mcp-bash runtime at `mcp/.mcp-bash/` (committed to git). Version and commit hash are also recorded in `mcp/mcp-bash.lock` for MCPB bundling.

### When to Update

```bash
# 1. Upgrade system mcp-bash to the new version
# 2. Re-vendor from the updated install:
cd mcp && mcp-bash vendor --upgrade

# 3. Update mcp/mcp-bash.lock with new version and commit hash:
git ls-remote https://github.com/yaniv-golan/mcp-bash-framework.git 'vX.Y.Z^{}'

# 4. Verify integrity:
mcp-bash vendor --verify

# 5. Review and commit:
git diff mcp/.mcp-bash/
git add mcp/.mcp-bash/ mcp/mcp-bash.lock
# 6. Update mcp/CHANGELOG.md
```

## Changelog Format

Both `CHANGELOG.md` (SDK/CLI) and `mcp/CHANGELOG.md` follow [Keep a Changelog](https://keepachangelog.com/)
with one addition: every version entry **must** start with a `### Highlights` section.

```markdown
## [1.6.0] - 2026-02-16

### Highlights

1-3 sentences in plain language summarizing why users should upgrade.
Write for the user, not the developer — focus on what's now possible
or what problem is fixed, not internal implementation details.

### Added
- ...

### Fixed
- ...
```

**Why Highlights matter:** The release workflow (`tools/generate_release_notes.py`) reads
the changelog and restructures it into GitHub Release notes. The Highlights section
becomes the first thing users see in release notifications and RSS feeds. Without it,
the release notes lack context.

**Guidelines for writing Highlights:**
- Use plain language ("You can now..." not "Changed `InteractionService.list()`...")
- For breaking changes, mention them with a **Breaking:** prefix and a brief migration hint
- For patch releases with only fixes, one sentence is fine
- For feature releases, summarize the 1-2 most impactful additions

## Release Checklist

### SDK Release
- [ ] Version bumped in `pyproject.toml`
- [ ] If plugin content also changed: plugin versions bumped in their `plugin.json` files
- [ ] Pre-commit ran
- [ ] `CHANGELOG.md` updated with `### Highlights` and categorized changes
- [ ] If CLI output changed: MCP tested and COMPATIBILITY checked
- [ ] Changes merged to `main`
- [ ] Verify release workflow completed successfully

### Plugin-Only Release (no SDK/CLI code changes)
- [ ] Version bumped in affected `plugin.json` file(s)
- [ ] `CHANGELOG.md` updated (under plugin section)
- [ ] Pre-commit ran
- [ ] Changes merged to `main`
- [ ] No PyPI release triggered (SDK version unchanged)

### MCP Release
- [ ] Version bumped in `mcp/VERSION`
- [ ] If MCP plugin content also changed: version bumped in `mcp/.claude-plugin/plugin.json`
- [ ] `mcp/CHANGELOG.md` updated with `### Highlights` and categorized changes
- [ ] If CLI requirements changed: `mcp/COMPATIBILITY` verified
- [ ] Pre-commit ran (syncs server.meta.json)
- [ ] Changes merged to `main`
- [ ] Verify release workflow completed successfully
- [ ] Verify MCP Registry publish step succeeded (non-blocking; check workflow annotations)
