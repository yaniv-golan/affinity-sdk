#!/usr/bin/env python3
"""
Validate SDK models against Affinity's official V2 OpenAPI specification.

TR-013: OpenAPI Schema Alignment (V2)

Three checks, each of which fails the run (exit 1):

1. **Models** - every property of a mapped spec schema must be read by the SDK model
   (matched on the field's alias / accepted input keys, not its Python name). Composed
   schemas are resolved fully: ``allOf`` members plus the schema's own ``properties``, and
   the union of ``oneOf`` / ``anyOf`` variants.
2. **Enums** - every spec member at every mapped location must exist in the SDK enum.
3. **Spec drift** - a normalized digest of the spec (operations, parameters, response
   refs, properties, types, enums, ``const``, ``maxItems``, ``required``, compositions,
   discriminators) must equal the committed ``tools/openapi_snapshot.json``.

Deliberate gaps are listed in ``KNOWN_GAPS`` with a reason. The list is strict: an entry
that no longer matches anything is itself an error, so it cannot rot.

Usage:
    python tools/validate_openapi_models.py [--offline PATH | --url URL | --pinned]
        [--snapshot PATH] [--update-snapshot [--source-sha SHA]] [--verbose]

Exit codes:
    0 - No errors and no drift
    1 - Validation errors or spec drift found
    2 - Schema fetch/parse error, missing snapshot, or bad arguments
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, AliasPath, BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = Path(__file__).resolve().parent / "openapi_snapshot.json"

# Default OpenAPI schema URL (upstream main)
OPENAPI_SCHEMA_URL = (
    "https://raw.githubusercontent.com/yaniv-golan/affinity-api-docs/main/docs/v2/openapi.json"
)
_PINNED_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/yaniv-golan/affinity-api-docs/{sha}/docs/v2/openapi.json"
)
_SHA_IN_URL = re.compile(r"/affinity-api-docs/([0-9a-f]{40})/")

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")
_MAX_DIFF_LINES = 200


# =============================================================================
# Tables
# =============================================================================

# SDK model -> V2 component schema(s). Several schemas are unioned (MergeTask is returned as
# either). Models not listed here and not skipped are looked up under their own name.
MODEL_MAPPINGS: dict[str, tuple[str, ...]] = {
    "AffinityList": ("ListWithType",),  # GET /v2/lists/{listId}
    "ListSummary": ("List",),
    "PersonSummary": ("PersonData",),
    "CompanySummary": ("CompanyData",),
    "ListEntryWithEntity": ("ListEntryWithEntity",),
    "NoteV2": ("notes.Note",),
    "MergeTask": ("CompanyMergeTask", "PersonMergeTask"),
}

# SDK models deliberately not checked, with the reason.
SKIPPED_MODELS: dict[str, str] = {
    "Note": "V1 note payload (V2 notes are NoteV2)",
    "Interaction": "V1 interaction payload (integer type codes); V2 interactions.* not modelled",
    "Reminder": "V1 reminder payload; V2 reminders.* not modelled",
    "WebhookSubscription": "V1 webhook payload; V2 webhooks.Webhook not modelled",
    "EntityFile": "V1 file payload; V2 files.File not modelled",
    "FieldValueChange": "V1 field-value-change payload; V2 fieldValueChanges.* not modelled",
    "FieldValue": "V1 field-value row; V2 FieldValue is the nested value union (other concept)",
    "ListPermission": "V1-only (additionalPermissions)",
    "DropdownOption": "spans V1 dropdown options and extracted V2 values; no single V2 schema",
}

# Known SDK extensions beyond OpenAPI (documented exceptions, by Python field name).
# An entry whose field is gone from the model or is now in the spec is an error.
KNOWN_EXTENSIONS: dict[str, set[str]] = {
    "Person": {
        "company_ids",
        "opportunity_ids",
        "current_company_ids",
        "fields_raw",
        "interaction_dates",
        "interactions",
        "list_entries",
    },
    "Company": {
        "person_ids",
        "opportunity_ids",
        "fields_raw",
        "list_entries",
        "interaction_dates",
        "interactions",
    },
    "Opportunity": {"person_ids", "company_ids", "fields", "fields_raw", "list_entries"},
    "ListEntry": {"entity_id", "entity_type", "entity", "fields_raw"},
    "ListEntryWithEntity": {"creator", "fields", "fields_raw"},
    "AffinityList": {"is_public", "fields", "additional_permissions", "list_size_temp"},
    "ListSummary": {"type", "is_public", "list_size"},  # V1 / relationship-endpoint keys
    "SavedView": {"list_id", "is_default", "field_ids"},  # V1 fields
    "FieldMetadata": {
        "allows_multiple",
        "value_type_raw",
        "list_id",
        "track_changes",
        "dropdown_options",
    },
}

# Strict list of deliberate gaps: {(model or enum name, spec property or member): reason}.
KNOWN_GAPS: dict[tuple[str, str], str] = {
    # --- models ---
    ("AffinityList", "isPublic"): (
        "read by a mode='before' validator that copies isPublic -> public; invisible to "
        "the alias check"
    ),
    ("ListSummary", "isPublic"): (
        "read by a mode='before' validator that copies isPublic -> public; invisible to "
        "the alias check"
    ),
    ("ListSummary", "creatorId"): "not modelled yet (small addition, P1.14 follow-up)",
    ("ListEntryWithEntity", "creatorId"): (
        "SDK model has `creator` (older shape); V2 returns creatorId - not modelled yet"
    ),
    ("NoteV2", "companiesPreview"): "opt-in `includes` preview; notes on V2 is P2.6",
    ("NoteV2", "opportunitiesPreview"): "opt-in `includes` preview; notes on V2 is P2.6",
    ("NoteV2", "personsPreview"): "opt-in `includes` preview; notes on V2 is P2.6",
    ("NoteV2", "repliesCount"): "opt-in `includes` count; notes on V2 is P2.6",
    ("NoteV2", "interaction"): "interaction / AI-notetaker note variants; P2.6",
    ("NoteV2", "transcriptId"): "AI-notetaker note variants; P2.6",
    # --- enums ---
    ("FieldValueType", "formula-number"): (
        "adding it would offer it in `xaffinity field create --value-type` (choices are "
        "built from the enum) although V1 cannot create it; needs a CLI filter first"
    ),
    ("FieldValueType", "list-multi"): "same as formula-number (CLI field create choices)",
    ("FieldValueType", "note"): "same as formula-number (CLI field create choices)",
    ("FieldValueType", "reminder"): "same as formula-number (CLI field create choices)",
    ("FieldType", "hidden"): (
        "response-only (Field.type); the CLI builds --field-type choices from FieldType and "
        "the API rejects `hidden` as a request value; needs a CLI filter first"
    ),
    ("DropdownOptionColor", "white"): (
        "SDK enum is the V1 integer palette (DEFAULT, YELLOW, ...); no verified V1 code "
        "for V2 `white`"
    ),
}


@dataclass(frozen=True)
class EnumLocation:
    """Where a set of enum members lives in the spec.

    ``where`` is a JSON pointer (``#/components/schemas/X/properties/y``) for ``enum``,
    ``const`` and ``discriminator``; for ``param`` it is ``"METHOD /path paramName"``.
    """

    where: str
    kind: str = "enum"  # "enum" | "const" | "discriminator" | "param"


@dataclass(frozen=True)
class EnumCheck:
    """An SDK enum and every spec location whose members it must cover.

    ``value_map`` translates spec strings to SDK values (int enums); a spec value missing
    from the map counts as missing from the SDK.
    """

    enum: type[Enum]
    locations: tuple[EnumLocation, ...]
    value_map: Mapping[str, Any] | None = None


def _schema_ptr(name: str, *props: str) -> str:
    ptr = "#/components/schemas/" + name.replace("~", "~0").replace("/", "~1")
    for prop in props:
        ptr += "/properties/" + prop
    return ptr


def default_enum_checks() -> tuple[EnumCheck, ...]:
    from affinity.models.types import (
        DropdownOptionColor,
        FieldType,
        FieldValueType,
        ListType,
        MergeStatus,
        PersonType,
        WebhookEvent,
    )

    list_type_map = {"person": 0, "company": 1, "opportunity": 8}
    # Name-based: V2 color names that match an SDK member name verbatim.
    color_map = {m.name.lower(): m.value for m in DropdownOptionColor}
    return (
        EnumCheck(
            FieldValueType,
            (
                EnumLocation(_schema_ptr("FieldMetadata", "valueType")),
                EnumLocation(_schema_ptr("FieldValue"), kind="discriminator"),
            ),
        ),
        EnumCheck(
            FieldType,
            (
                EnumLocation(_schema_ptr("Field", "type")),
                EnumLocation(_schema_ptr("FieldMetadata", "type")),
                EnumLocation("GET /v2/companies fieldTypes", kind="param"),
                EnumLocation("GET /v2/persons fieldTypes", kind="param"),
                EnumLocation("GET /v2/lists/{listId}/list-entries fieldTypes", kind="param"),
            ),
        ),
        EnumCheck(
            PersonType,
            (
                EnumLocation(_schema_ptr("Person", "type")),
                EnumLocation(_schema_ptr("PersonData", "type")),
                EnumLocation(_schema_ptr("fieldValueChanges.PersonEntityData", "type")),
            ),
        ),
        EnumCheck(
            WebhookEvent,
            (EnumLocation(_schema_ptr("webhooks.SubscriptionType")),),
        ),
        EnumCheck(
            ListType,
            (
                EnumLocation(_schema_ptr("ListWithType", "type")),
                EnumLocation(_schema_ptr("ListToBeCreated", "type")),
                EnumLocation(_schema_ptr("ListEntryWithEntity"), kind="discriminator"),
            ),
            value_map=list_type_map,
        ),
        EnumCheck(
            MergeStatus,
            (
                EnumLocation(_schema_ptr("CompanyMergeTask", "status")),
                EnumLocation(_schema_ptr("PersonMergeTask", "status")),
                EnumLocation(_schema_ptr("CompanyMergeState", "status")),
                EnumLocation(_schema_ptr("PersonMergeState", "status")),
            ),
        ),
        EnumCheck(
            DropdownOptionColor,
            (EnumLocation(_schema_ptr("dropdownOptions.RankedDropdownOption", "color")),),
            value_map=color_map,
        ),
    )


@dataclass
class CheckConfig:
    """Everything the checks compare against; injectable for tests."""

    models: Mapping[str, type]
    mappings: Mapping[str, tuple[str, ...]]
    skipped: Mapping[str, str]
    known_gaps: dict[tuple[str, str], str]
    known_extensions: Mapping[str, set[str]]
    enum_checks: Sequence[EnumCheck]


def default_config() -> CheckConfig:
    return CheckConfig(
        models=get_sdk_models(),
        mappings=MODEL_MAPPINGS,
        skipped=SKIPPED_MODELS,
        known_gaps=dict(KNOWN_GAPS),
        known_extensions=KNOWN_EXTENSIONS,
        enum_checks=default_enum_checks(),
    )


# =============================================================================
# Loading
# =============================================================================


def fetch_openapi_schema(url: str) -> dict[str, Any]:
    """Fetch the OpenAPI schema from URL."""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            result: dict[str, Any] = json.loads(response.read().decode("utf-8"))
            return result
    except Exception as e:
        raise RuntimeError(f"Failed to fetch OpenAPI schema: {e}") from e


def load_openapi_schema(path: str) -> dict[str, Any]:
    """Load the OpenAPI schema from a local file."""
    with Path(path).open() as f:
        result: dict[str, Any] = json.load(f)
        return result


def pinned_url(sha: str) -> str:
    return _PINNED_URL_TEMPLATE.format(sha=sha)


def sha_from_url(url: str) -> str | None:
    match = _SHA_IN_URL.search(url)
    return match.group(1) if match else None


def spec_version(spec: Mapping[str, Any]) -> str:
    info = spec.get("info", {})
    return str(info.get("x-affinity-api-version") or info.get("version") or "unknown")


# =============================================================================
# Schema resolution
# =============================================================================


class SchemaResolutionError(Exception):
    """A schema construct the resolver cannot interpret (never silently ``{}``)."""


def resolve_pointer(spec: Mapping[str, Any], pointer: str) -> Any:
    """Resolve a local JSON pointer (``#/a/b``). Raises KeyError if it does not resolve."""
    if not pointer.startswith("#"):
        raise KeyError(f"non-local reference: {pointer}")
    node: Any = spec
    for raw in pointer[1:].split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            raise KeyError(pointer)
    return node


def resolve_properties(
    spec: Mapping[str, Any],
    schema: Mapping[str, Any],
    _seen: frozenset[str] = frozenset(),
) -> dict[str, dict[str, Any]]:
    """Return every property a payload matching ``schema`` may carry.

    Follows ``$ref`` (cycle-safe), merges ``allOf`` members with the schema's own
    ``properties``, and unions ``oneOf`` / ``anyOf`` variants.
    """
    if not isinstance(schema, Mapping):
        raise SchemaResolutionError(f"not a schema object: {schema!r}")
    if "not" in schema:
        raise SchemaResolutionError("`not` schemas are not supported")

    props: dict[str, dict[str, Any]] = {}
    ref = schema.get("$ref")
    if ref is not None:
        if ref in _seen:
            return props
        try:
            target = resolve_pointer(spec, ref)
        except (KeyError, IndexError, ValueError) as e:
            raise SchemaResolutionError(f"unresolvable $ref {ref}") from e
        props.update(resolve_properties(spec, target, _seen | {ref}))

    for sub in schema.get("allOf", []):
        props.update(resolve_properties(spec, sub, _seen))
    for key in ("oneOf", "anyOf"):
        for sub in schema.get(key, []):
            for name, prop in resolve_properties(spec, sub, _seen).items():
                props.setdefault(name, prop)
    props.update(schema.get("properties", {}))
    return props


def get_schema_properties(
    schema: Mapping[str, Any],
    component_name: str,
) -> dict[str, dict[str, Any]] | None:
    """Properties of a component schema, or None if the component does not exist."""
    components = schema.get("components", {}).get("schemas", {})
    if component_name not in components:
        return None
    return resolve_properties(schema, components[component_name])


def normalize_field_name(name: str) -> str:
    """Convert camelCase to snake_case."""
    s = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s).lower()


# =============================================================================
# Model checks
# =============================================================================


def get_sdk_models() -> dict[str, type]:
    """Import and return SDK model classes."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))  # this checkout's `affinity`, not an installed one

    from affinity.models.entities import (
        AffinityList,
        Company,
        CompanySummary,
        DropdownOption,
        FieldMetadata,
        FieldValue,
        FieldValueChange,
        ListEntry,
        ListEntryWithEntity,
        ListPermission,
        ListSummary,
        Opportunity,
        Person,
        PersonSummary,
        SavedView,
    )
    from affinity.models.secondary import (
        EntityFile,
        Interaction,
        MergeTask,
        Note,
        NoteV2,
        Reminder,
        WebhookSubscription,
        WhoAmI,
    )

    return {
        # Core entities
        "Person": Person,
        "PersonSummary": PersonSummary,
        "Company": Company,
        "CompanySummary": CompanySummary,
        "Opportunity": Opportunity,
        "AffinityList": AffinityList,
        "ListSummary": ListSummary,
        "ListEntry": ListEntry,
        "ListEntryWithEntity": ListEntryWithEntity,
        "ListPermission": ListPermission,
        "SavedView": SavedView,
        # Fields
        "FieldMetadata": FieldMetadata,
        "FieldValue": FieldValue,
        "FieldValueChange": FieldValueChange,
        "DropdownOption": DropdownOption,
        # Secondary entities
        "Note": Note,
        "NoteV2": NoteV2,
        "Interaction": Interaction,
        "Reminder": Reminder,
        "WebhookSubscription": WebhookSubscription,
        "EntityFile": EntityFile,
        # Merge tasks
        "MergeTask": MergeTask,
        # Auth
        "WhoAmI": WhoAmI,
    }


def get_pydantic_fields(model: type) -> dict[str, dict[str, Any]]:
    """Field name -> alias and every input key the model accepts for it."""
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        return {}

    populate_by_name = bool(model.model_config.get("populate_by_name", False))
    fields: dict[str, dict[str, Any]] = {}
    for name, info in model.model_fields.items():
        alias = info.alias or name
        keys = {alias}
        if populate_by_name or info.alias is None:
            keys.add(name)
        va = info.validation_alias
        if isinstance(va, str):
            keys = {va} | ({name} if populate_by_name else set())
        elif isinstance(va, AliasChoices):
            keys = {c for c in va.choices if isinstance(c, str)}
            keys |= {
                c.path[0]
                for c in va.choices
                if isinstance(c, AliasPath) and len(c.path) == 1 and isinstance(c.path[0], str)
            }
            if populate_by_name:
                keys.add(name)
        fields[name] = {"alias": alias, "keys": keys, "required": info.is_required()}
    return fields


@dataclass
class ValidationResult:
    """Result of validating a single model."""

    model_name: str
    schema_name: str
    missing_in_sdk: list[str] = field(default_factory=list)
    gapped: dict[str, str] = field(default_factory=dict)
    extra_in_sdk: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    used_gaps: set[tuple[str, str]] = field(default_factory=set)
    is_skipped: bool = False
    skip_reason: str = ""

    @property
    def is_compatible(self) -> bool:
        return not self.missing_in_sdk and not self.errors

    def error_lines(self) -> list[str]:
        lines = [
            f"{self.model_name} ({self.schema_name}): spec property '{p}' is not read by the "
            "SDK model (match is on alias / accepted input keys)"
            for p in self.missing_in_sdk
        ]
        lines += [f"{self.model_name} ({self.schema_name}): {e}" for e in self.errors]
        return lines

    def __str__(self) -> str:
        lines = [f"\n=== {self.model_name} (schema: {self.schema_name}) ==="]
        if self.is_skipped:
            lines.append(f"  [SKIP] {self.skip_reason}")
            return "\n".join(lines)
        if self.is_compatible:
            lines.append("  [OK] Compatible")
        for p in self.missing_in_sdk:
            lines.append(f"  [ERROR] Missing in SDK: {p}")
        for e in self.errors:
            lines.append(f"  [ERROR] {e}")
        for p, reason in sorted(self.gapped.items()):
            lines.append(f"  [GAP] {p}: {reason}")
        for x in self.extra_in_sdk:
            lines.append(f"  [INFO] Extra in SDK: {x}")
        return "\n".join(lines)


def validate_model(
    schema: Mapping[str, Any],
    sdk_model_name: str,
    sdk_model: type,
    schema_names: Sequence[str] | str | None = None,
    *,
    known_gaps: Mapping[tuple[str, str], str] | None = None,
    known_extensions: Mapping[str, set[str]] | None = None,
) -> ValidationResult:
    """Validate a single SDK model against one or more spec component schemas."""
    if schema_names is None:
        schema_names = MODEL_MAPPINGS.get(sdk_model_name, (sdk_model_name,))
    if isinstance(schema_names, str):
        schema_names = (schema_names,)
    gaps = KNOWN_GAPS if known_gaps is None else known_gaps
    extensions = KNOWN_EXTENSIONS if known_extensions is None else known_extensions

    result = ValidationResult(model_name=sdk_model_name, schema_name=" | ".join(schema_names))

    schema_props: dict[str, dict[str, Any]] = {}
    for name in schema_names:
        try:
            props = get_schema_properties(schema, name)
        except SchemaResolutionError as e:
            result.errors.append(f"cannot resolve schema '{name}': {e}")
            continue
        if props is None:
            result.errors.append(f"mapped schema '{name}' not found in the spec")
            continue
        for prop_name, prop_schema in props.items():
            schema_props.setdefault(prop_name, prop_schema)
    if result.errors:
        return result

    sdk_fields = get_pydantic_fields(sdk_model)
    accepted = set().union(*(f["keys"] for f in sdk_fields.values())) if sdk_fields else set()

    for prop in sorted(schema_props):
        if prop in accepted:
            continue
        key = (sdk_model_name, prop)
        if key in gaps:
            result.gapped[prop] = gaps[key]
            result.used_gaps.add(key)
        else:
            result.missing_in_sdk.append(prop)

    known_ext = extensions.get(sdk_model_name, set())
    for ext in sorted(known_ext):
        if ext not in sdk_fields:
            result.errors.append(f"stale KNOWN_EXTENSIONS entry '{ext}': not a model field")
        elif sdk_fields[ext]["keys"] & set(schema_props):
            result.errors.append(
                f"stale KNOWN_EXTENSIONS entry '{ext}': the spec now has "
                f"{sorted(sdk_fields[ext]['keys'] & set(schema_props))}"
            )
    for name, info in sdk_fields.items():
        if not info["keys"] & set(schema_props) and name not in known_ext:
            result.extra_in_sdk.append(f"{name} (alias: {info['alias']})")

    return result


# =============================================================================
# Enum checks
# =============================================================================


def _enum_members_of(spec: Mapping[str, Any], node: Any, where: str) -> list[Any]:
    if isinstance(node, Mapping) and "$ref" in node and "enum" not in node:
        node = resolve_pointer(spec, node["$ref"])
    if isinstance(node, Mapping) and "enum" not in node and isinstance(node.get("items"), Mapping):
        node = node["items"]
        if "$ref" in node:
            node = resolve_pointer(spec, node["$ref"])
    if not isinstance(node, Mapping) or "enum" not in node:
        raise KeyError(f"no `enum` at {where}")
    return [v for v in node["enum"] if v is not None]


def spec_enum_members(spec: Mapping[str, Any], location: EnumLocation) -> list[Any]:
    """Members at a location. Raises KeyError when the location does not resolve."""
    if location.kind == "param":
        method, path, name = location.where.split(" ", 2)
        item = spec.get("paths", {}).get(path)
        if not isinstance(item, Mapping) or method.lower() not in item:
            raise KeyError(f"no operation {method} {path}")
        params = [*item.get("parameters", []), *item[method.lower()].get("parameters", [])]
        for param in params:
            if "$ref" in param:
                param = resolve_pointer(spec, param["$ref"])
            if param.get("name") == name:
                return _enum_members_of(spec, param.get("schema", {}), location.where)
        raise KeyError(f"no parameter {name!r} on {method} {path}")

    node = resolve_pointer(spec, location.where)
    if location.kind == "enum":
        return _enum_members_of(spec, node, location.where)
    if location.kind == "const":
        if not isinstance(node, Mapping) or "const" not in node:
            raise KeyError(f"no `const` at {location.where}")
        return [node["const"]]
    if location.kind == "discriminator":
        mapping = (
            node.get("discriminator", {}).get("mapping") if isinstance(node, Mapping) else None
        )
        if not isinstance(mapping, Mapping):
            raise KeyError(f"no discriminator mapping at {location.where}")
        return list(mapping)
    raise ValueError(f"unknown EnumLocation kind {location.kind!r}")


@dataclass
class EnumResult:
    enum_name: str
    errors: list[str] = field(default_factory=list)
    gapped: dict[str, str] = field(default_factory=dict)
    used_gaps: set[tuple[str, str]] = field(default_factory=set)


def check_enum(
    spec: Mapping[str, Any],
    check: EnumCheck,
    known_gaps: Mapping[tuple[str, str], str],
) -> EnumResult:
    enum_name = check.enum.__name__
    result = EnumResult(enum_name)
    # Real members only. Never construct the enum or read _value2member_map_: Open*Enum
    # _missing_ inserts unknown values there, which would make every check pass.
    sdk_values = {m.value for m in check.enum.__members__.values()}

    missing: dict[str, list[str]] = {}
    for location in check.locations:
        try:
            members = spec_enum_members(spec, location)
        except (KeyError, IndexError, ValueError) as e:
            result.errors.append(
                f"{enum_name}: spec location {location.where} ({location.kind}) "
                f"no longer resolves: {e}"
            )
            continue
        for value in members:
            if check.value_map is not None:
                present = value in check.value_map and check.value_map[value] in sdk_values
            else:
                present = value in sdk_values
            if not present:
                missing.setdefault(str(value), []).append(location.where)

    for value, wheres in sorted(missing.items()):
        key = (enum_name, value)
        if key in known_gaps:
            result.gapped[value] = known_gaps[key]
            result.used_gaps.add(key)
        else:
            result.errors.append(
                f"{enum_name}: spec member '{value}' is not in the SDK enum "
                f"(at {', '.join(wheres)})"
            )
    return result


# =============================================================================
# Spec-drift digest
# =============================================================================


def _sorted_set(values: Iterable[Any]) -> list[Any]:
    return sorted(values, key=lambda v: json.dumps(v, sort_keys=True))


def normalize_schema(schema: Any) -> Any:
    """Facts of a schema that matter to the SDK; descriptions/examples/titles are dropped."""
    if not isinstance(schema, Mapping):
        return schema
    out: dict[str, Any] = {}
    for key in ("$ref", "const", "maxItems", "deprecated"):
        if key in schema:
            out[key] = schema[key]
    if "type" in schema:
        t = schema["type"]
        out["type"] = sorted(t) if isinstance(t, list) else t
    if "enum" in schema:
        out["enum"] = _sorted_set(schema["enum"])
    if "required" in schema and isinstance(schema["required"], list):
        out["required"] = sorted(schema["required"])
    for key in ("allOf", "oneOf", "anyOf"):
        if key in schema:
            out[key] = _sorted_set(normalize_schema(s) for s in schema[key])
    if "discriminator" in schema:
        disc = schema["discriminator"]
        out["discriminator"] = {
            "propertyName": disc.get("propertyName"),
            "mapping": dict(disc.get("mapping", {})),
        }
    if "properties" in schema:
        out["properties"] = {k: normalize_schema(v) for k, v in schema["properties"].items()}
    for key in ("items", "additionalProperties", "not"):
        if isinstance(schema.get(key), Mapping):
            out[key] = normalize_schema(schema[key])
    return out


def _normalize_content(spec: Mapping[str, Any], obj: Mapping[str, Any]) -> dict[str, Any]:
    if "$ref" in obj:
        obj = resolve_pointer(spec, obj["$ref"])
    return {
        media: normalize_schema(body.get("schema", {}))
        for media, body in obj.get("content", {}).items()
    }


def _normalize_operation(
    spec: Mapping[str, Any], op: Mapping[str, Any], path_params: Sequence[Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "x-stability-level" in op:
        out["x-stability-level"] = op["x-stability-level"]
    if op.get("deprecated"):
        out["deprecated"] = True
    params: dict[str, Any] = {}
    for param in [*path_params, *op.get("parameters", [])]:
        if "$ref" in param:
            param = resolve_pointer(spec, param["$ref"])
        params[f"{param.get('in')}:{param.get('name')}"] = {
            "required": bool(param.get("required", False)),
            "schema": normalize_schema(param.get("schema", {})),
        }
    out["parameters"] = params
    if "requestBody" in op:
        body = op["requestBody"]
        if "$ref" in body:
            body = resolve_pointer(spec, body["$ref"])
        out["requestBody"] = {
            "required": bool(body.get("required", False)),
            "content": _normalize_content(spec, body),
        }
    out["responses"] = {
        str(code): _normalize_content(spec, resp)
        for code, resp in op.get("responses", {}).items()
        if str(code).startswith("2")
    }
    return out


def build_digest(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Normalized, order-insensitive digest of the parts of the spec the SDK depends on."""
    operations: dict[str, Any] = {}
    for path, item in spec.get("paths", {}).items():
        path_params = item.get("parameters", [])
        for method in _HTTP_METHODS:
            if method in item:
                operations[f"{method.upper()} {path}"] = _normalize_operation(
                    spec, item[method], path_params
                )
    schemas = {
        name: normalize_schema(schema)
        for name, schema in spec.get("components", {}).get("schemas", {}).items()
    }
    return {"operations": operations, "schemas": schemas}


def _flatten(node: Any, prefix: str, out: dict[str, str]) -> None:
    if isinstance(node, Mapping) and node:
        for key, value in node.items():
            _flatten(value, f"{prefix} > {key}" if prefix else str(key), out)
    else:
        out[prefix] = json.dumps(node, sort_keys=True)


def diff_digest(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """Readable line diff between two digests ([] when identical)."""
    a: dict[str, str] = {}
    b: dict[str, str] = {}
    _flatten(old, "", a)
    _flatten(new, "", b)
    lines: list[str] = []
    for key in sorted(a.keys() | b.keys()):
        if key not in b:
            lines.append(f"- {key} = {a[key]}")
        elif key not in a:
            lines.append(f"+ {key} = {b[key]}")
        elif a[key] != b[key]:
            lines.append(f"~ {key}: {a[key]} -> {b[key]}")
    return lines


def make_snapshot(spec: Mapping[str, Any], *, url: str, sha: str) -> dict[str, Any]:
    return {
        "source": {
            "url": url,
            "sha": sha,
            "x-affinity-api-version": spec_version(spec),
        },
        "digest": build_digest(spec),
    }


def write_snapshot(path: Path, snapshot: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")


# =============================================================================
# Run
# =============================================================================


@dataclass
class Report:
    model_results: list[ValidationResult] = field(default_factory=list)
    enum_results: list[EnumResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if self.errors or self.drift else 0

    @property
    def model_counts(self) -> dict[str, int]:
        counts = {"ok": 0, "error": 0, "skipped": 0}
        for r in self.model_results:
            if r.is_skipped:
                counts["skipped"] += 1
            elif r.is_compatible:
                counts["ok"] += 1
            else:
                counts["error"] += 1
        return counts


def validate_all_models(
    schema: Mapping[str, Any],
    verbose: bool = False,
    *,
    config: CheckConfig | None = None,
) -> list[ValidationResult]:
    """Validate all SDK models against the OpenAPI schema."""
    config = config or default_config()
    results: list[ValidationResult] = []
    for model_name, model in config.models.items():
        if model_name in config.skipped:
            result = ValidationResult(model_name=model_name, schema_name="-")
            result.is_skipped = True
            result.skip_reason = config.skipped[model_name]
        else:
            result = validate_model(
                schema,
                model_name,
                model,
                config.mappings.get(model_name, (model_name,)),
                known_gaps=config.known_gaps,
                known_extensions=config.known_extensions,
            )
        results.append(result)
        if verbose:
            print(result)
    return results


def run_checks(
    spec: Mapping[str, Any],
    *,
    config: CheckConfig,
    snapshot: Mapping[str, Any] | None,
    verbose: bool = False,
) -> Report:
    report = Report()
    report.model_results = validate_all_models(spec, verbose=verbose, config=config)
    report.enum_results = [check_enum(spec, c, config.known_gaps) for c in config.enum_checks]

    used: set[tuple[str, str]] = set()
    for r in report.model_results:
        report.errors.extend(r.error_lines())
        used |= r.used_gaps
    for e in report.enum_results:
        report.errors.extend(e.errors)
        used |= e.used_gaps

    for key in sorted(set(config.known_gaps) - used):
        report.errors.append(
            f"stale KNOWN_GAPS entry {key}: no longer a gap (the SDK covers it, the spec "
            "dropped it, or nothing checks it) - remove it"
        )

    if snapshot is not None:
        report.drift = diff_digest(snapshot.get("digest", {}), build_digest(spec))
    return report


def _print_report(report: Report, spec: Mapping[str, Any], source: str, verbose: bool) -> None:
    print(f"OpenAPI spec version (x-affinity-api-version): {spec_version(spec)}")
    print(f"Source: {source}")

    if not verbose:
        for r in report.model_results:
            if not r.is_skipped and (not r.is_compatible or r.gapped):
                print(r)

    gaps = [(r.model_name, p, why) for r in report.model_results for p, why in r.gapped.items()]
    gaps += [(e.enum_name, v, why) for e in report.enum_results for v, why in e.gapped.items()]

    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    counts = report.model_counts
    print(f"[OK] Models aligned with V2: {counts['ok']}")
    print(f"[SKIP] Models not checked: {counts['skipped']}")
    if counts["error"]:
        print(f"[ERROR] Models with errors: {counts['error']}")
    print(f"[OK] Enums checked: {len(report.enum_results)}")
    if gaps:
        print(f"[GAP] Known gaps (KNOWN_GAPS): {len(gaps)}")
        for subject, item, why in sorted(gaps):
            print(f"      - {subject}.{item}: {why}")

    if report.errors:
        print(f"\n[ERROR] {len(report.errors)} error(s):")
        for err in report.errors:
            print(f"  - {err}")

    if report.drift:
        print(
            f"\n[DRIFT] Spec differs from the committed snapshot ({len(report.drift)} change(s)):"
        )
        for line in report.drift[:_MAX_DIFF_LINES]:
            print(f"  {line}")
        if len(report.drift) > _MAX_DIFF_LINES:
            print(f"  ... {len(report.drift) - _MAX_DIFF_LINES} more")
        print(
            "\n  Review the change, update the SDK if needed, then refresh the snapshot:\n"
            "    python tools/validate_openapi_models.py --update-snapshot"
        )

    print("\nRESULT: " + ("FAIL" if report.exit_code else "PASS"))


def main(argv: Sequence[str] | None = None, *, config: CheckConfig | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate SDK models against OpenAPI schema")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed output")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--offline",
        metavar="PATH",
        help="Use local OpenAPI schema file instead of fetching",
    )
    source.add_argument(
        "--url",
        default=OPENAPI_SCHEMA_URL,
        help=f"OpenAPI schema URL (default: {OPENAPI_SCHEMA_URL})",
    )
    source.add_argument(
        "--pinned",
        action="store_true",
        help="Fetch the spec at the affinity-api-docs commit recorded in the snapshot",
    )
    parser.add_argument(
        "--snapshot",
        default=str(DEFAULT_SNAPSHOT_PATH),
        help="Spec-drift snapshot file (default: tools/openapi_snapshot.json)",
    )
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help="Rewrite the snapshot from the selected spec source instead of checking",
    )
    parser.add_argument(
        "--source-sha",
        help="affinity-api-docs commit sha of the spec (required with --update-snapshot "
        "unless the --url is pinned to a sha)",
    )
    args = parser.parse_args(argv)

    snapshot_path = Path(args.snapshot)
    snapshot: dict[str, Any] | None = None
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text())
    elif not args.update_snapshot:
        print(
            f"Error: snapshot {snapshot_path} not found (create it with --update-snapshot)",
            file=sys.stderr,
        )
        return 2

    url = args.url
    if args.pinned:
        assert snapshot is not None
        url = pinned_url(snapshot["source"]["sha"])

    sha = args.source_sha or (None if args.offline else sha_from_url(url))
    if args.update_snapshot and not sha:
        print(
            "Error: --update-snapshot needs the spec's affinity-api-docs commit: pass "
            "--source-sha, or a --url pinned to a sha",
            file=sys.stderr,
        )
        return 2

    try:
        if args.offline:
            print(f"Loading OpenAPI schema from {args.offline}...")
            spec = load_openapi_schema(args.offline)
            source_desc = f"{args.offline} (sha {sha or 'unknown'})"
        else:
            print(f"Fetching OpenAPI schema from {url}...")
            spec = fetch_openapi_schema(url)
            source_desc = url
        print("Schema loaded successfully.\n")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if args.update_snapshot:
        assert sha is not None
        new_snapshot = make_snapshot(spec, url=pinned_url(sha), sha=sha)
        if snapshot is not None:
            changes = diff_digest(snapshot.get("digest", {}), new_snapshot["digest"])
            print(f"Snapshot changes: {len(changes)}")
            for line in changes[:_MAX_DIFF_LINES]:
                print(f"  {line}")
        write_snapshot(snapshot_path, new_snapshot)
        print(f"Wrote {snapshot_path} (sha {sha}, spec version {spec_version(spec)})")
        return 0

    if snapshot is not None:
        print(
            f"Snapshot: sha {snapshot['source'].get('sha')} "
            f"(spec version {snapshot['source'].get('x-affinity-api-version')})"
        )

    report = run_checks(
        spec, config=config or default_config(), snapshot=snapshot, verbose=args.verbose
    )
    _print_report(report, spec, source_desc, args.verbose)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
