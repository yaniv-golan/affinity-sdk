# CLI Development Guide

This guide provides best practices and patterns for developing CLI commands in the Affinity SDK.

## Table of Contents

- [Standard Command Structure](#standard-command-structure)
- [Model Serialization](#model-serialization)
- [CommandOutput Pattern](#commandoutput-pattern)
- [Pagination Patterns](#pagination-patterns)
- [Resolved Metadata](#resolved-metadata)
- [Error Handling](#error-handling)
- [Testing CLI Commands](#testing-cli-commands)
- [Common Pitfalls](#common-pitfalls)

## Standard Command Structure

All CLI commands follow a consistent structure using Click decorators and the `run_command` orchestrator:

```python
from affinity.cli.click_compat import RichCommand, click
from affinity.cli.context import CLIContext
from affinity.cli.options import output_options
from affinity.cli.runner import CommandOutput, run_command
from affinity.cli.serialization import serialize_model_for_cli

@click.command(name="get", cls=RichCommand)
@click.argument("entity_id", type=int)
@output_options
@click.pass_obj
def entity_get(ctx: CLIContext, entity_id: int) -> None:
    """Get an entity by ID."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        entity = client.entities.get(EntityId(entity_id))
        return CommandOutput(
            data=serialize_model_for_cli(entity),
            api_called=True
        )

    run_command(ctx, command="entity get", fn=fn)
```

### Key Components

1. **Click Decorators**: Use `@click.command()` with `cls=RichCommand` for enhanced formatting
2. **Output Options**: Always include `@output_options` for `--output`/`-o` (`table`, `json`, `jsonl`,
   `markdown`, `toon`, `csv`) and `--json`; use `@csv_output_options` to add the `--csv` alias.
   `--quiet` is a global option, not part of `@output_options`
3. **Context**: Use `@click.pass_obj` to receive `CLIContext`
4. **Inner Function**: Define command logic in an inner `fn()` function
5. **Orchestration**: Call `run_command()` to handle execution, error formatting, and output

## Model Serialization

### The Standard Pattern

**ALWAYS** use the centralized serialization helpers from `affinity.cli.serialization`:

```python
from affinity.cli.serialization import serialize_model_for_cli, serialize_models_for_cli

# Single model
result = serialize_model_for_cli(person)

# List of models
results = serialize_models_for_cli(persons)
```

### Why This Matters

The serialization helpers ensure:

1. **JSON-Safe Types**: Converts Python objects to JSON-serializable types
   - `datetime` → ISO 8601 string
   - `UUID` → string
   - Other complex types handled automatically

2. **Consistent Field Naming**: Uses field aliases (camelCase for API compatibility)
   - `first_name` → `firstName`
   - `email_addresses` → `emailAddresses`

3. **Null Handling**: Excludes `None` values from output (cleaner JSON)

### Example: Before and After

**Before (INCORRECT - will fail for datetime fields):**
```python
def _entity_payload(entity: Entity) -> dict[str, object]:
    return entity.model_dump(by_alias=True, exclude_none=True)
    # Missing mode="json" causes: TypeError: Object of type datetime is not JSON serializable
```

**After (CORRECT):**
```python
from affinity.cli.serialization import serialize_model_for_cli

def _entity_payload(entity: Entity) -> dict[str, object]:
    return serialize_model_for_cli(entity)
```

### Critical Entities with Datetime Fields

These entities MUST use `serialize_model_for_cli()`:

- `ListEntry` (has `created_at`)
- `Opportunity` (has `created_at`, `updated_at`)
- `Note` (has `created_at`)
- Any custom entities with datetime fields

## CommandOutput Pattern

The `CommandOutput` dataclass is the standard way to return data from CLI commands:

```python
from affinity.cli.runner import CommandOutput

CommandOutput(
    data=...,           # Required: Dict or serialized model
    api_called=True,    # Required: Whether an API call was made
    warnings=[]         # Optional: List of warning messages
)
```

### Data Field Structure

The `data` field should be a dictionary with descriptive keys:

```python
# Single entity
CommandOutput(data={"person": serialize_model_for_cli(person)}, api_called=True)

# List of entities
CommandOutput(data={"persons": serialize_models_for_cli(persons)}, api_called=True)

# Multiple fields
CommandOutput(
    data={
        "person": serialize_model_for_cli(person),
        "notes": serialize_models_for_cli(notes)
    },
    api_called=True
)
```

### Success-Only Results

For operations that return boolean success:

```python
CommandOutput(data={"success": True}, api_called=True)
```

## Pagination Patterns

### Using Page Iterators

For commands that support pagination, use the SDK's async iterators:

```python
from affinity.cli.serialization import serialize_models_for_cli

def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
    client = ctx.get_client(warnings=warnings)

    # Collect all pages
    all_items = []
    async for page in client.entities.list_iter(page_size=100):
        all_items.extend(page.data)

    return CommandOutput(
        data={"entities": serialize_models_for_cli(all_items)},
        api_called=True
    )
```

### Single Page Fetch

For simpler commands without pagination:

```python
def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
    client = ctx.get_client(warnings=warnings)
    items = client.entities.list()  # Returns list directly

    return CommandOutput(
        data={"entities": serialize_models_for_cli(items)},
        api_called=True
    )
```

## Resolved Metadata

Many entity commands support a `--with-interaction-metadata` flag that enriches the response with resolved field values.

### Standard Resolver Structure

The resolver adds a `_resolve` key to each entity with resolved field values:

```json
{
  "person": {
    "id": 12345,
    "firstName": "John",
    "lastName": "Doe",
    "_resolve": {
      "field-123": {
        "value": "Active",
        "field": {
          "id": "field-123",
          "name": "Status",
          "valueType": "dropdown"
        }
      }
    }
  }
}
```

### Implementation Pattern

```python
@click.option("--with-interaction-metadata", is_flag=True, help="Include resolved field values")
def entity_get(
    ctx: CLIContext,
    entity_id: int,
    *,
    with_interaction_metadata: bool
) -> None:
    """Get an entity with optional metadata."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)

        if with_interaction_metadata:
            result = client.entities.get_with_metadata(EntityId(entity_id))
            # result.entity is the main entity
            # result.metadata contains resolved field values
            data = serialize_model_for_cli(result.entity)
            data["_resolve"] = result.metadata  # Already serialized
        else:
            entity = client.entities.get(EntityId(entity_id))
            data = serialize_model_for_cli(entity)

        return CommandOutput(data={"entity": data}, api_called=True)

    run_command(ctx, command="entity get", fn=fn)
```

### Resolved Metadata Best Practices

1. **Always use `--with-interaction-metadata` flag** for consistency across commands
2. **Document the `_resolve` structure** in command docstrings
3. **Don't serialize metadata twice** - it's already in JSON-safe format
4. **Provide examples** in help text showing the resolved structure

### Using Standard Resolver Types

For new commands, use the standard resolver types from `affinity.cli.resolvers` to ensure consistent metadata structure:

```python
from affinity.cli.resolvers import (
    ResolvedEntity,
    ResolvedFieldSelection,
    ResolvedList,
    build_resolved_metadata
)

def _resolve_person_selector(*, client: Any, selector: str) -> tuple[PersonId, dict[str, Any]]:
    """Resolve person selector to ID and metadata."""
    # Parse selector and resolve to person ID
    if selector.startswith("email:"):
        email = selector[6:]
        person = client.persons.search(email).data[0]
        person_id = PersonId(person.id)

        # Use standard resolver type
        resolved_entity = ResolvedEntity(
            input=selector,
            entity_id=int(person_id),
            entity_type="person",
            source="email",
            canonical_url=f"https://app.affinity.co/persons/{person_id}"
        )

        return person_id, {"person": resolved_entity.to_dict()}
    # ... handle other selector types

# In the command function:
def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
    client = ctx.get_client(warnings=warnings)
    person_id, entity_resolved = _resolve_person_selector(
        client=client,
        selector=person_selector
    )

    # Build complete resolved metadata
    resolved = build_resolved_metadata(
        entity=ResolvedEntity(
            input=person_selector,
            entity_id=int(person_id),
            entity_type="person",
            source="email"
        ),
        field_selection=ResolvedFieldSelection(
            field_types=["global", "enriched"]
        ) if all_fields else None,
        expand=list(expand) if expand else None
    )

    person = client.persons.get(person_id)
    return CommandOutput(
        data={"person": serialize_model_for_cli(person)},
        resolved=resolved,
        api_called=True
    )
```

**Benefits of standard resolver types**:
- Consistent structure across all commands
- Type safety for resolver metadata
- Automatic camelCase conversion
- Clear documentation with examples

**Available resolver types**:
- `ResolvedEntity` - For person, company, opportunity resolution
- `ResolvedList` - For list ID resolution
- `ResolvedSavedView` - For saved view resolution
- `ResolvedFieldSelection` - For field selection options
- `build_resolved_metadata()` - Convenience builder

See `affinity/cli/resolvers.py` for complete API documentation.

## Error Handling

### Raising CLI Errors

Use `CLIError` for user-facing errors:

```python
from affinity.cli.errors import CLIError

# Usage error (exit code 2)
if not value and not value_json:
    raise CLIError(
        "Provide --value or --value-json.",
        error_type="usage_error",
        exit_code=2
    )

# Validation error
if len(values) == 0:
    raise CLIError(
        "At least one value is required.",
        error_type="validation_error",
        exit_code=1
    )
```

### API Errors

API errors are automatically handled by `run_command()` and formatted for CLI output. No special handling needed in most cases.

## Testing CLI Commands

### Unit Tests for Serialization

Test the serialization helpers directly:

```python
from affinity.cli.serialization import serialize_model_for_cli
from affinity.models.entities import Person

def test_serializes_person():
    person = Person(id=123, first_name="John", last_name="Doe", emails=[])
    result = serialize_model_for_cli(person)

    assert result["id"] == 123
    assert result["firstName"] == "John"  # Uses alias
    assert "first_name" not in result  # Original field name not included
```

### End-to-End CLI Tests

Test commands with `CliRunner`:

```python
from click.testing import CliRunner
from affinity.cli.commands.person_cmds import person_get

def test_person_get_json_output():
    runner = CliRunner()
    result = runner.invoke(person_get, ["12345", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "person" in data
    assert data["person"]["id"] == 12345
```

### JSON Safety Verification

Verify output is JSON-safe recursively:

```python
def verify_json_safe(data: Any, path: str = "root") -> None:
    """Recursively verify data structure contains only JSON-safe types."""
    if isinstance(data, dict):
        for key, value in data.items():
            verify_json_safe(value, f"{path}.{key}")
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            verify_json_safe(item, f"{path}[{idx}]")
    elif not isinstance(data, (str, int, float, bool, type(None))):
        raise AssertionError(
            f"Non-JSON-safe type at {path}: {type(data).__name__}"
        )

def test_list_entry_json_safe():
    list_entry = ListEntry(id=1, list_id=2, entity_id=3, created_at=datetime.now())
    result = serialize_model_for_cli(list_entry)
    verify_json_safe(result)  # Should not raise
```

## Common Pitfalls

### 1. Forgetting `mode="json"`

**Problem**: Using `model_dump()` without `mode="json"` causes datetime serialization errors.

```python
# WRONG - Will fail for datetime fields
person.model_dump(by_alias=True, exclude_none=True)

# RIGHT - Use the helper
from affinity.cli.serialization import serialize_model_for_cli
serialize_model_for_cli(person)
```

**Error Message**: `TypeError: Object of type datetime is not JSON serializable`

**Solution**: Always use `serialize_model_for_cli()` instead of calling `model_dump()` directly.

### 2. Inconsistent Field Names

**Problem**: Using snake_case field names instead of camelCase aliases.

```python
# WRONG - Uses Python field names
{"first_name": "John", "last_name": "Doe"}

# RIGHT - Uses API field aliases
{"firstName": "John", "lastName": "Doe"}
```

**Solution**: The serialization helper automatically uses aliases with `by_alias=True`.

### 3. Including None Values

**Problem**: Including fields with `None` values clutters output.

```python
# WRONG - Includes None values
{"id": 123, "firstName": "John", "middleName": None}

# RIGHT - Excludes None values
{"id": 123, "firstName": "John"}
```

**Solution**: The serialization helper uses `exclude_none=True` automatically.

### 4. Not Using CommandOutput

**Problem**: Returning raw data instead of `CommandOutput`.

```python
# WRONG - Returns raw dict
def fn(ctx: CLIContext, warnings: list[str]) -> dict:
    return {"person": serialize_model_for_cli(person)}

# RIGHT - Returns CommandOutput
def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
    return CommandOutput(
        data={"person": serialize_model_for_cli(person)},
        api_called=True
    )
```

### 5. Serializing Metadata Twice

**Problem**: Calling `serialize_model_for_cli()` on already-serialized resolved metadata.

```python
# WRONG - Metadata is already serialized
data["_resolve"] = serialize_model_for_cli(result.metadata)

# RIGHT - Use metadata as-is
data["_resolve"] = result.metadata
```

**Reason**: Resolved metadata from `*_with_metadata()` methods is already in JSON-safe dict format.

### 6. Not Reading Files Before Editing

**Problem**: Attempting to edit files without reading them first.

**Solution**: Always use the Read tool before Edit or Write tools:

```python
# 1. Read the file first
# 2. Then edit it
```

### 7. Bypassing Pre-Commit Hooks

The codebase includes a pre-commit hook (`check-cli-patterns`) that enforces CLI serialization patterns. In rare cases, you may need to bypass it.

**When to bypass**:
- Emergency hotfixes
- Files in active migration (should have TODO comments)
- Legitimate exceptions that don't fit the standard pattern

**How to bypass**:

```bash
# Skip all hooks for a single commit
git commit --no-verify -m "Emergency fix"

# Or skip just the CLI pattern check
SKIP=check-cli-patterns git commit -m "WIP: migrating commands"
```

**Best practice**: Always add a TODO comment if you're bypassing the hook:

```python
def _field_payload(field: FieldMetadata) -> dict[str, object]:
    # TODO: Migrate to use serialize_model_for_cli() from ..serialization
    return field.model_dump(by_alias=True, mode="json", exclude_none=True)
```

This documents the intent to migrate later and helps track technical debt.

## Related Documentation

- [CLI JSON Output Resolution Plan](internal/cli-json-output-resolution-plan.md) - Implementation roadmap
- [Testing Guidelines](../CONTRIBUTING.md#testing) - General testing best practices
- [Serialization Module](../affinity/cli/serialization.py) - Source code for helpers
- [Pydantic Serialization](https://docs.pydantic.dev/latest/concepts/serialization/) - Upstream documentation

## Quick Reference

### Command Template

```python
from affinity.cli.click_compat import RichCommand, click
from affinity.cli.context import CLIContext
from affinity.cli.options import output_options
from affinity.cli.runner import CommandOutput, run_command
from affinity.cli.serialization import serialize_model_for_cli

@click.command(name="my-command", cls=RichCommand)
@click.argument("entity_id", type=int)
@output_options
@click.pass_obj
def my_command(ctx: CLIContext, entity_id: int) -> None:
    """Command description."""

    def fn(ctx: CLIContext, warnings: list[str]) -> CommandOutput:
        client = ctx.get_client(warnings=warnings)
        entity = client.entities.get(EntityId(entity_id))
        return CommandOutput(
            data={"entity": serialize_model_for_cli(entity)},
            api_called=True
        )

    run_command(ctx, command="my-command", fn=fn)
```

### Serialization Checklist

- [ ] Import `serialize_model_for_cli` from `affinity.cli.serialization`
- [ ] Use `serialize_model_for_cli(model)` for single models
- [ ] Use `serialize_models_for_cli(models)` for lists
- [ ] Never call `model.model_dump()` directly in CLI code
- [ ] Wrap result in `CommandOutput` with `api_called=True`
- [ ] Test with entities that have datetime fields
- [ ] Verify JSON output with `--json` flag
