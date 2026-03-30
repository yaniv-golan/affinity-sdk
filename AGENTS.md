# AGENTS.md

## Project Overview

`affinity-sdk` is a mixed Python monorepo centered on a strongly typed Affinity CRM SDK and CLI, with adjacent documentation, MCP server assets, and Claude Code plugin artifacts.

Primary components in this repository:

- **SDK + CLI (Python package):** `affinity/` (published from `pyproject.toml`)
- **Test suite:** `tests/` (pytest, strict markers, async coverage)
- **Documentation source:** `docs/public/` (MkDocs Material)
- **MCP server distribution and tooling:** `mcp/` (versioned independently)
- **Claude Code plugin assets:** `plugins/` and `.claude-plugin/`

The project is configured for Python **3.10+** and emphasizes strict static analysis, clear API modeling, and automation-friendly CLI behavior.

---

## Repository Structure

Top-level layout (validated paths):

- `affinity/` — core SDK and CLI source code
- `tests/` — unit, integration, contract, and CLI-focused tests
- `docs/` — docs sources and contributor guides
  - `docs/public/` — MkDocs site content (`mkdocs.yml` uses this as `docs_dir`)
  - `docs/cli-development-guide.md` — CLI contribution guide
- `mcp/` — MCP server runtime, scripts, docs, and versioning files
- `plugins/` — plugin-related artifacts (SDK/CLI plugins)
- `.claude-plugin/` — repository marketplace/plugin metadata
- `.github/workflows/` — CI and release automation workflows
- `tools/` — maintenance/release helper scripts

Important root files:

- `pyproject.toml` — package metadata, dependencies, lint/type/test config
- `mkdocs.yml` — docs site navigation and build config
- `.pre-commit-config.yaml` — pre-commit hooks
- `codecov.yml` — coverage reporting config
- `README.md` — user-facing project overview
- `CONTRIBUTING.md` — contributor workflow
- `VERSIONING.md` — release/version governance across SDK/MCP/plugins
- `CHANGELOG.md` — SDK/CLI changelog

MCP-specific release/version files (validated):

- `mcp/VERSION`
- `mcp/CHANGELOG.md`
- `mcp/COMPATIBILITY`

---

## Development Guidelines

### Environment and Installation

Use a virtual environment, then install editable with development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### Core Local Checks

Run these before submitting changes:

```bash
ruff format .
ruff check .
mypy affinity
pytest
```

Repository conventions are defined in `pyproject.toml`:

- **Ruff**: formatting/linting, target version `py310`
- **Mypy**: `strict = true`
- **Pytest**: strict config/markers, default excludes integration tests via marker expression

### Pre-commit

Recommended for all contributors:

```bash
pre-commit install
```

---

## Code Patterns

### Architecture and Organization

- Domain/service-oriented SDK under `affinity/`
- CLI entrypoint exposed as `xaffinity` (`[project.scripts]` in `pyproject.toml`)
- Typed models and API-facing interfaces with strong validation semantics
- Separate but co-located MCP tooling in `mcp/`

### Testing Patterns

From `CONTRIBUTING.md`, test files follow clear naming conventions:

- `test_cli_<topic>.py` — CLI command behavior
- `test_services_<service>.py` — service-layer coverage
- `test_integration_*.py` — integration/smoke coverage
- coverage-gap suffixes like `_additional_coverage` and `_remaining_coverage`

Common pytest markers configured in `pyproject.toml` include:

- `integration`
- `contract`
- `pitfall`
- `req`
- `slow`

### CLI and MCP Relationship

- MCP tooling shells out to `xaffinity` CLI and has independent versioning in `mcp/VERSION`
- Breaking CLI output/shape changes may require MCP compatibility updates (`mcp/COMPATIBILITY`)

---

## Quality Standards

Quality gates are strict and should be treated as mandatory:

1. **Formatting/linting passes** (`ruff format`, `ruff check`)
2. **Type checking passes** (`mypy affinity` with strict mode)
3. **Tests pass** (`pytest`)
4. **Docs stay aligned** when public behavior changes (`docs/public/`, `README.md`, changelogs)

Additional expectations:

- Keep public interfaces typed and stable where possible
- Favor clear error handling and user-readable CLI output
- Preserve marker discipline and test naming conventions

---

## Critical Rules

1. **Do not bypass configured quality gates.**
2. **Maintain version-source correctness:**
   - SDK/CLI version from `pyproject.toml`
   - MCP version from `mcp/VERSION`
   - Plugin versions from their respective `plugin.json` files (see `VERSIONING.md`)
3. **When CLI output contract changes, evaluate MCP impact** and update `mcp/COMPATIBILITY` if needed.
4. **Keep changelogs updated** (`CHANGELOG.md`, `mcp/CHANGELOG.md`) per release policy.
5. **Respect docs structure and nav** in `mkdocs.yml` when adding/removing docs pages.

---

## Common Tasks

### Run the test suite

```bash
pytest
```

### Run fast local quality checks

```bash
ruff format .
ruff check .
mypy affinity
```

### Build docs locally (if docs deps are installed)

```bash
mkdocs serve
```

### Work on MCP assets

```bash
cd mcp
./xaffinity-mcp.sh validate
```

### Build MCP Claude plugin structure

```bash
cd mcp
make plugin
```

---

## Reference Examples

Useful orientation files for contributors/agents:

- **Simple usage/examples**
  - `examples/basic_usage.py`
  - `tests/test_models.py`
- **Complex CLI/query behaviors**
  - `tests/test_cli_query_executor.py`
  - `affinity/cli/`
- **Service layer patterns**
  - `affinity/services/`
- **Docs architecture**
  - `mkdocs.yml`
  - `docs/public/`
- **Automation/release helpers**
  - `tools/`
  - `.github/workflows/`

---

## Additional Resources

- `README.md` — high-level usage and installation
- `CONTRIBUTING.md` — contributor workflows and quality checklist
- `VERSIONING.md` — canonical versioning/release policy
- `docs/cli-development-guide.md` — CLI-specific implementation guidance
- `mcp/README.md` — MCP installation, operation, and troubleshooting
- Published docs site: https://yaniv-golan.github.io/affinity-sdk/latest/
