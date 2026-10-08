"""Body of ``company field`` and ``person field``.

Reads and writes go through V2: ``GET /v2/{companies,persons}/{id}/fields`` and one
``PATCH .../fields`` per command, which Affinity applies completely or not at all. Global,
enriched and Source of Introduction fields are all written the same way.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from affinity.services._field_updates import FIELD_WRITES_MIN_API_VERSION

from ..context import CLIContext
from ..errors import CLIError
from ..results import CommandContext
from ..runner import CommandOutput

EntityKind = Literal["company", "person"]


def _usage(message: str) -> CLIError:
    return CLIError(message, exit_code=2, error_type="usage_error")


def run_entity_field(
    ctx: CLIContext,
    warnings: list[str],
    *,
    entity: EntityKind,
    entity_id: int,
    set_values: tuple[tuple[str, str], ...],
    unset_fields: tuple[str, ...],
    json_input: str | None,
    get_fields: tuple[str, ...],
) -> CommandOutput:
    from ..field_utils import (
        FieldResolver,
        apply_field_updates,
        fetch_field_metadata,
        pre_validate_set_operations,
        rows_from_v2_field_values,
        truncation_warnings,
    )

    has_set = bool(set_values) or bool(json_input)
    has_unset = bool(unset_fields)
    has_get = bool(get_fields)
    if not (has_set or has_unset or has_get):
        raise _usage("Provide at least one of --set, --unset, --set-json, or --get.")
    if has_get and (has_set or has_unset):
        raise _usage("--get cannot be combined with --set, --unset, or --set-json.")

    json_ops: list[tuple[str, Any]] = []
    if json_input:
        try:
            json_data = json.loads(json_input)
        except json.JSONDecodeError as e:
            raise _usage(f"Invalid JSON in --set-json: {e}") from e
        if not isinstance(json_data, dict):
            raise _usage("--set-json must be a JSON object.")
        json_ops = list(json_data.items())

    client = ctx.get_client(warnings=warnings)
    if has_set or has_unset:
        # Before any request: a pin older than the write endpoint needs fails the same way
        # whatever the data.
        client.require_api_version(FIELD_WRITES_MIN_API_VERSION, operation=f"{entity} field")
    service = client.companies if entity == "company" else client.persons

    resolver = FieldResolver(fetch_field_metadata(client=client, entity_type=entity))

    def resolve(spec: str) -> str:
        return resolver.resolve_field_name_or_id(spec, context="field")

    modifiers: dict[str, object] = {}
    if set_values:
        modifiers["set"] = [list(sv) for sv in set_values]
    if unset_fields:
        modifiers["unset"] = list(unset_fields)
    if json_input:
        modifiers["json"] = json_input
    if get_fields:
        modifiers["get"] = list(get_fields)
    id_key = "companyId" if entity == "company" else "personId"
    cmd_context = CommandContext(
        name=f"{entity} field", inputs={id_key: entity_id}, modifiers=modifiers
    )

    if has_get:
        ids = [resolve(spec) for spec in get_fields]
        fields = service.get_field_values(entity_id, ids=ids)  # type: ignore[arg-type]
        results: dict[str, Any] = {}
        for spec, field_id in zip(get_fields, ids, strict=True):
            field_obj = fields.data.get(field_id) or {}
            value = field_obj.get("value") if isinstance(field_obj, dict) else None
            name = resolver.get_field_name(field_id) or spec
            results[name] = value.get("data") if isinstance(value, dict) else value
        warnings.extend(truncation_warnings([(f"{entity} {entity_id}", fields)]))
        return CommandOutput(data={"fields": results}, context=cmd_context, api_called=True)

    # Resolve every field before any write; conflicts are checked on the resolved ids (a name,
    # another casing and the field id are the same field, and Affinity silently applies the
    # last of two updates to one field).
    set_ops = [(resolve(spec), value) for spec, value in [*set_values, *json_ops]]
    unsets = [(spec, resolve(spec)) for spec in unset_fields]

    def names(ids: set[str]) -> str:
        return ", ".join(sorted(resolver.get_field_name(i) or i for i in ids))

    set_ids = [fid for fid, _ in set_ops]
    duplicates = {fid for fid in set_ids if set_ids.count(fid) > 1}
    if duplicates:
        raise _usage(f"Field(s) set more than once: {names(duplicates)}")
    both = set(set_ids) & {fid for _, fid in unsets}
    if both:
        raise _usage(f"Field(s) in both --set and --unset: {names(both)}")

    # Dropdown options read fresh (V2 field metadata has none); then every value is checked
    # before anything is written.
    resolver.load_dropdown_options(client, set_ids, entity_type=entity)
    pre_resolved_set = pre_validate_set_operations(resolver, set_ops)

    target_ids = [*set_ids, *(fid for _, fid in unsets)]
    existing = service.get_field_values(entity_id, ids=target_ids)  # type: ignore[arg-type]
    created, cleared, _written = apply_field_updates(
        resolver=resolver,
        pre_resolved_set=pre_resolved_set,
        unsets=unsets,
        existing_values_serialized=rows_from_v2_field_values(existing),
        write=lambda updates, value_types: service.batch_update_fields(
            entity_id,  # type: ignore[arg-type]
            updates,
            value_types=value_types,
        ),
    )
    data: dict[str, Any] = {}
    if created:
        data["created"] = created
    if cleared:
        data["cleared"] = cleared
    return CommandOutput(data=data, context=cmd_context, api_called=True)
