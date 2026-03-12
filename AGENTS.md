# AGENTS.md

## Project Overview

`affinity-sdk` is a mixed-surface repository centered on a strongly typed Python SDK for the Affinity CRM API, with additional developer and AI-facing interfaces:

- Python SDK package (`affinity/`)
- CLI (`xaffinity` entrypoint)
- MCP server assets and scripts (`mcp/`)
- Claude Code plugin packaging (`plugins/`, `.claude-plugin/`)
- MkDocs-based documentation (`docs/public/`)
- Large automated test suite (`tests/`)

The project targets Python **3.10+** and emphasizes strict typing, predictable API behavior, and robust test/lint/type quality gates.

## Repository Structure

Top-level directories and their roles:

- `affinity/` — Core SDK package (client, services, models, types, CLI modules).
- `tests/` — Unit/integration/CLI/service tests. Integration tests are sandbox-gated and skipped by default.
- `docs/public/` — Documentation source for MkDocs (guides, CLI docs, API reference, MCP docs).
- `mcp/` — Bash-based MCP server implementation, provider scripts, prompts, tools, and plugin assembly artifacts.
- `examples/` — Runnable SDK usage examples.
- `tools/` — Repository automation and validation scripts.
- `.github/workflows/` — CI, release, docs, and validation workflows.
- `plugins/` — Claude plugin packaging directories.
- `.claude-plugin/` — Plugin marketplace metadata (repository marketplace manifest).

Important root files:

- `pyproject.toml` — Build config, dependencies, lint/type/test configuration.
- `mkdocs.yml` — Docs build/navigation configuration (`docs/public` as docs source).
- `README.md` — Product overview, quick start, feature docs.
- `CONTRIBUTING.md` — Contributor workflow, naming conventions, quality checks.
- `tests/integration/README.md` — Live sandbox integration test safety and usage.

## Development Guidelines

Environment and install:

```bash
python -m pip install -e ".[dev]"
```

Recommended local checks before PRs:

```bash
ruff format .
ruff check .
mypy affinity
pytest
```

Pre-commit hooks are supported:

```bash
pre-commit install
```

CLI notes:

- CLI script entrypoint is `xaffinity` (`[project.scripts]` in `pyproject.toml`).
- CLI dependencies are in optional `cli` extras, and included in `dev` extras for test/type consistency.

Documentation notes:

- MkDocs config is in `mkdocs.yml`.
- Docs source is `docs/public/`.
- Versioned docs use `mike` (`extra.version.provider: mike`).

## Code Patterns

Repository patterns to follow:

- **Service-based SDK design** under `affinity/services` (resource-specific service modules).
- **Strong typing everywhere** with Pydantic v2 models and typed IDs/enums.
- **Dual API surface support** (V1 + V2 routing behavior documented in README/docs).
- **Sync + async parity** for client operations where supported.
- **CLI + SDK cohesion**: CLI behavior should map cleanly to SDK capabilities.
- **MCP Bash framework integration** in `mcp/` for AI client interoperability.

Testing patterns (from `CONTRIBUTING.md`):

- `test_cli_<topic>.py`
- `test_services_<service>.py`
- `test_<feature>.py`
- `test_http_client_*.py`
- `test_v1_only_*.py`
- `test_integration_*.py`

Coverage-gap naming suffixes include `_additional_coverage` and `_remaining_coverage`.

## Quality Standards

Configured standards (from `pyproject.toml`):

- **Python target**: `py310`
- **Formatting/linting**: Ruff (`line-length = 100`, broad rule set enabled)
- **Type checking**: Mypy strict mode (`strict = true`, `disallow_untyped_defs = true`)
- **Tests**: Pytest with strict config/markers and default `-m 'not integration'`
- **Coverage**: Configured via `tool.coverage.*`

Core runtime dependencies:

- `httpx`
- `pydantic` (v2)

Core dev/tooling dependencies include:

- `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`
- `ruff`, `mypy`
- `mkdocs`, `mkdocs-material`, `mkdocstrings`, `mike`

## Critical Rules

1. **Do not run live integration tests against production tenants.** Integration tests require sandbox credentials and enforce sandbox-only checks.
2. **Respect integration safety gates** in `tests/integration/README.md`:
   - `.sandbox.env` requirement
   - sandbox tenant validation
   - cleanup expectations for write tests
3. **Assume integration tests are skipped by default** unless explicitly invoked with `-m integration`.
4. **Keep SDK/CLI/docs consistency** when changing API behavior.
5. **Preserve strict typing and lint quality**; do not weaken type/lint/test gates without strong justification.
6. **For plugin/MCP changes**, ensure the documented build/validation flow remains valid (`mcp` plugin build + CI validation).

## Common Tasks

Install for development:

```bash
python -m pip install -e ".[dev]"
```

Run default test suite (excluding integration by default config):

```bash
pytest
```

Run integration tests (sandbox only):

```bash
pytest -m integration
```

Run lint and format:

```bash
ruff format .
ruff check .
```

Run type checks:

```bash
mypy affinity
```

Build MCP plugin assets (from `mcp/`):

```bash
cd mcp
make plugin
```

## Reference Examples

Useful examples and references in-repo:

- `examples/basic_usage.py` — basic SDK usage patterns.
- `affinity/services/companies.py` — representative service-layer implementation.
- `tests/test_models.py` — model/type behavior tests.
- `tests/integration/README.md` — integration test operating model and safeguards.
- `.github/workflows/ci.yml` — authoritative CI quality gates and plugin validation.

## Additional Resources

- Main overview and usage: `README.md`
- Contributor workflow and naming conventions: `CONTRIBUTING.md`
- Packaging/tool configuration: `pyproject.toml`
- Documentation navigation/build config: `mkdocs.yml`
- Public docs site: https://yaniv-golan.github.io/affinity-sdk/latest/
