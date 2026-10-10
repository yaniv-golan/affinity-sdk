"""Utilities for field name resolution and field metadata management.

This module provides shared helpers for resolving human-readable field names
to field IDs across person/company/opportunity/list-entry commands.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, Literal, cast

from .errors import CLIError

if TYPE_CHECKING:
    from affinity.models.entities import FieldMetadata


EntityType = Literal["person", "company", "opportunity", "list-entry"]


def fetch_field_metadata(
    *,
    client: Any,
    entity_type: EntityType,
    list_id: int | None = None,
) -> list[FieldMetadata]:
    """Fetch field metadata for an entity type.

    Args:
        client: The Affinity client instance.
        entity_type: Type of entity ("person", "company", "opportunity", "list-entry").
        list_id: Required for opportunity and list-entry entity types.

    Returns:
        List of FieldMetadata objects.

    Raises:
        CLIError: If list_id is required but not provided.
    """
    from affinity.models.entities import FieldMetadata as FM

    if entity_type == "person":
        return cast(list[FM], client.persons.get_fields())
    elif entity_type == "company":
        return cast(list[FM], client.companies.get_fields())
    elif entity_type in ("opportunity", "list-entry"):
        if list_id is None:
            raise CLIError(
                f"list_id is required for {entity_type} field metadata.",
                exit_code=2,
                error_type="internal_error",
            )
        from affinity.types import ListId

        return cast(list[FM], client.lists.get_fields(ListId(list_id)))
    else:
        raise CLIError(
            f"Unknown entity type: {entity_type}",
            exit_code=2,
            error_type="internal_error",
        )


# Dropdown kinds whose V1 write value is the option id (V1 rejects text for them); plain
# dropdowns take the option text.
_DROPDOWN_BY_ID = ("ranked-dropdown", "status-dropdown")
_DROPDOWN_KINDS = ("dropdown", "dropdown-multi", *_DROPDOWN_BY_ID)


def is_dropdown_type(type_str: str) -> bool:
    return "dropdown" in type_str


def _type_str(field: FieldMetadata) -> str:
    from ..models.types import FieldValueType

    vt = field.value_type
    return vt.value if isinstance(vt, FieldValueType) else str(vt)


def fetch_dropdown_options(
    client: Any, *, entity_type: EntityType, field_id: str, list_id: int | None = None
) -> list[Any]:
    """All options of a dropdown field from the V2 API, uncached (every page)."""
    if entity_type == "company":
        return list(client.companies.get_field_dropdown_options(field_id))
    if entity_type == "person":
        return list(client.persons.get_field_dropdown_options(field_id))
    from affinity.types import ListId

    if list_id is None:
        raise CLIError(
            f"list_id is required to read the options of {field_id}.",
            exit_code=2,
            error_type="internal_error",
        )
    return list(client.lists.get_field_dropdown_options(ListId(list_id), field_id))


def with_v2_value_types(
    v1_fields: list[FieldMetadata], v2_fields: list[FieldMetadata]
) -> list[FieldMetadata]:
    """List field metadata with each field's type taken from V2.

    V1 list metadata carries dropdown options (V2 doesn't) but its value types are ambiguous
    (V1 type 2 means "text or dropdown"); V2's ``valueType`` is authoritative. List fields that
    only V2 knows yet (V1's field listing lags creation) are added without options.
    """
    by_id = {str(f.id): f for f in v2_fields}
    out: list[FieldMetadata] = []
    for field in v1_fields:
        v2 = by_id.get(str(field.id))
        if v2 is None:
            out.append(field)
            continue
        out.append(
            field.model_copy(
                update={
                    "value_type": v2.value_type,
                    "type": v2.type or field.type,
                    "allows_multiple": _type_str(v2).endswith("-multi") or v2.allows_multiple,
                }
            )
        )
    known = {str(f.id) for f in v1_fields}
    out.extend(f for f in v2_fields if str(f.id) not in known and f.type in ("list", "hidden"))
    return out


# V2 value types that can't be written through the field-value endpoints.
UNWRITABLE_TYPES = frozenset(
    {"interaction", "formula-number", "list-multi", "note", "reminder", "hidden"}
)


def build_field_id_to_name_map(fields: list[FieldMetadata]) -> dict[str, str]:
    """Build a mapping from field ID to field name.

    Args:
        fields: List of FieldMetadata objects.

    Returns:
        Dictionary mapping field_id -> field_name.
    """
    result: dict[str, str] = {}
    for field in fields:
        field_id = str(field.id)
        field_name = str(field.name) if field.name else ""
        result[field_id] = field_name
    return result


def build_field_name_to_id_map(fields: list[FieldMetadata]) -> dict[str, list[str]]:
    """Build a mapping from lowercase field name to field IDs.

    Multiple fields can have the same name (case-insensitive), so this returns
    a list of field IDs for each name.

    Args:
        fields: List of FieldMetadata objects.

    Returns:
        Dictionary mapping lowercase_name -> [field_id, ...].
    """
    result: dict[str, list[str]] = {}
    for field in fields:
        field_id = str(field.id)
        field_name = str(field.name) if field.name else ""
        if field_name:
            result.setdefault(field_name.lower(), []).append(field_id)
    return result


class FieldResolver:
    """Helper class for resolving field names to field IDs.

    Provides case-insensitive field name resolution with proper error handling
    for ambiguous or missing field names.
    """

    def __init__(self, fields: list[FieldMetadata]) -> None:
        """Initialize the resolver with field metadata.

        Args:
            fields: List of FieldMetadata objects.
        """
        self._fields = fields
        self._by_id = build_field_id_to_name_map(fields)
        self._by_name = build_field_name_to_id_map(fields)

    @property
    def available_names(self) -> list[str]:
        """Get list of available field names for error messages."""
        names: list[str] = []
        seen: set[str] = set()
        for field in self._fields:
            name = str(field.name) if field.name else ""
            if name and name.lower() not in seen:
                names.append(name)
                seen.add(name.lower())
        return sorted(names, key=str.lower)

    def resolve_field_name_or_id(
        self,
        value: str,
        *,
        context: str = "field",
    ) -> str:
        """Resolve a field name or ID to a field ID.

        If the value starts with "field-", it's treated as a field ID and validated.
        Otherwise, it's treated as a field name and resolved case-insensitively.

        Args:
            value: Field name or field ID (e.g., "Phone" or "field-260415").
            context: Context for error messages (e.g., "field" or "list-entry field").

        Returns:
            The resolved field ID.

        Raises:
            CLIError: If the field is not found or the name is ambiguous.
        """
        value = value.strip()
        if not value:
            raise CLIError(
                f"Empty {context} name.",
                exit_code=2,
                error_type="usage_error",
            )

        # ID-branch: accept 'field-<n>' or any known enriched literal present in _by_id
        # (e.g. 'affinity-data-phone-number', 'source-of-introduction', 'dealroom-industry').
        if value.startswith("field-") or value in self._by_id:
            if value not in self._by_id:
                # Only reached for unknown 'field-<n>' values
                available = ", ".join(self.available_names[:10])
                suffix = "..." if len(self.available_names) > 10 else ""
                raise CLIError(
                    f"Field ID '{value}' not found.",
                    exit_code=2,
                    error_type="not_found",
                    hint=f"Available fields: {available}{suffix}",
                )
            return value

        # Otherwise, resolve by name (case-insensitive)
        matches = self._by_name.get(value.lower(), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Ambiguous - multiple fields with same name
            details: list[dict[str, Any]] = []
            for fid in matches[:10]:
                details.append(
                    {
                        "fieldId": fid,
                        "name": self._by_id.get(fid, ""),
                    }
                )
            raise CLIError(
                f"Ambiguous {context} name '{value}' matches {len(matches)} fields.",
                exit_code=2,
                error_type="ambiguous_resolution",
                details={"name": value, "matches": details},
                hint="Pass the specific field ID instead of the name.",
            )

        # Not found
        available = ", ".join(self.available_names[:10])
        suffix = "..." if len(self.available_names) > 10 else ""
        raise CLIError(
            f"Field '{value}' not found.",
            exit_code=2,
            error_type="not_found",
            hint=f"Available fields: {available}{suffix}",
        )

    def resolve_all_field_names_or_ids(
        self,
        updates: dict[str, Any],
        *,
        context: str = "field",
    ) -> tuple[dict[str, Any], list[str]]:
        """Resolve all field names/IDs in an updates dict to field IDs.

        Validates ALL field names first and reports ALL errors at once.

        Args:
            updates: Dictionary of field_name_or_id -> value.
            context: Context for error messages.

        Returns:
            Tuple of (resolved_updates, errors) where resolved_updates maps
            field_id -> value and errors is a list of invalid field names.

        Raises:
            CLIError: If any field names are invalid (lists all invalid names).
        """
        resolved: dict[str, Any] = {}
        invalid: list[str] = []

        for key, value in updates.items():
            key = key.strip()
            if not key:
                continue

            # If starts with "field-", treat as field ID
            if key.startswith("field-"):
                if key not in self._by_id:
                    invalid.append(key)
                else:
                    resolved[key] = value
                continue

            # Otherwise, resolve by name (case-insensitive)
            matches = self._by_name.get(key.lower(), [])
            if len(matches) == 1:
                resolved[matches[0]] = value
            elif len(matches) > 1:
                # For batch updates, treat ambiguous as invalid
                invalid.append(f"{key} (ambiguous: {', '.join(matches[:3])})")
            else:
                invalid.append(key)

        if invalid:
            available = ", ".join(self.available_names[:10])
            suffix = "..." if len(self.available_names) > 10 else ""
            raise CLIError(
                f"Invalid {context}s: {', '.join(repr(n) for n in invalid)}.",
                exit_code=2,
                error_type="not_found",
                hint=f"Available fields: {available}{suffix}",
            )

        return resolved, []

    def get_field_name(self, field_id: str) -> str:
        """Get the field name for a field ID.

        Args:
            field_id: The field ID.

        Returns:
            The field name, or empty string if not found.
        """
        return self._by_id.get(field_id, "")

    def load_dropdown_options(
        self,
        client: Any,
        field_ids: Iterable[str],
        *,
        entity_type: EntityType,
        list_id: int | None = None,
    ) -> None:
        """Attach dropdown options, read fresh from the V2 API, to the given dropdown fields.

        V2 field metadata carries no options, and cached options can be stale: V1 writes
        dropdown values by text and create a new option for text that no longer matches one.
        Only global and list fields (``field-<n>``) are loaded; enriched and
        relationship-intelligence fields have no options endpoint and are left as they are.
        """
        for field_id in dict.fromkeys(field_ids):
            field = self.get_field_metadata(field_id)
            if field is None or not is_dropdown_type(_type_str(field)):
                continue
            if not str(field.id).startswith("field-") or field.type in (
                "enriched",
                "relationship-intelligence",
            ):
                continue
            options = fetch_dropdown_options(
                client, entity_type=entity_type, field_id=field_id, list_id=list_id
            )
            fresh = field.model_copy(update={"dropdown_options": options})
            self._fields = [fresh if f is field else f for f in self._fields]

    def write_type(self, field_id: str) -> str | None:
        """The V2 value type to write this field with (``<type>-multi`` for multi-value
        fields), or ``None`` when the field's metadata is unknown."""
        field = self.get_field_metadata(field_id)
        if field is None:
            return None
        type_str = _type_str(field)
        if field.allows_multiple and type_str in _PROMOTABLE_TO_MULTI:
            type_str = f"{type_str}-multi"
        return type_str

    def get_field_metadata(self, field_id: str) -> FieldMetadata | None:
        """Get field metadata by field ID.

        Args:
            field_id: The field ID (e.g., "field-260419").

        Returns:
            FieldMetadata if found, None otherwise.
        """
        for field in self._fields:
            if str(field.id) == field_id:
                return field
        return None

    def resolve_field_value(self, field_id: str, value: Any) -> tuple[Any, str]:
        """Resolve a field value to the format expected by the V2 API.

        Handles type-specific wrapping:
        - Dropdown/ranked-dropdown/dropdown-multi: text/ID → ``{"dropdownOptionId": ID}``
        - Person/company: ID → ``{"id": ID}``
        - Person-multi/company-multi: ID(s) → ``[{"id": ID}, ...]``
        - Number(-multi): numeric string(s) → int/float
        - Location(-multi): JSON object(s) → the five-key V2 location shape
        - Other types: returns value unchanged with inferred type

        Args:
            field_id: The field ID.
            value: The value to resolve (text, ID, or list for multi fields).

        Returns:
            Tuple of (resolved_value, value_type_string).

        Raises:
            CLIError: If dropdown option text not found or entity ID is invalid.
        """
        from ..models.types import FieldValueType

        field = self.get_field_metadata(field_id)
        if field is None:
            # Field not found, return value as-is with text type
            return value, "text"

        value_type = field.value_type
        type_str = value_type.value if isinstance(value_type, FieldValueType) else str(value_type)

        # V1 API returns the base type (e.g., "dropdown", "person", "company") for
        # both single and multi fields, relying on allows_multiple to distinguish.
        # Promote to "-multi" so the correct API payload format is used downstream.
        if field.allows_multiple and type_str in _PROMOTABLE_TO_MULTI:
            type_str = f"{type_str}-multi"

        # Handle dropdown, ranked-dropdown, and dropdown-multi fields
        # V2 API expects:
        #   dropdown/ranked-dropdown: {"data": {"dropdownOptionId": ID}, "type": "..."}
        #   dropdown-multi: {"data": [{"dropdownOptionId": ID}], "type": "dropdown-multi"}
        if is_dropdown_type(type_str) and type_str not in _DROPDOWN_KINDS:
            raise CLIError(
                f"Field '{field.name}' has dropdown type '{type_str}', which the CLI cannot "
                "write yet. Nothing was changed.",
                exit_code=2,
                error_type="validation_error",
            )
        if type_str in _DROPDOWN_KINDS:
            # For dropdown-multi, accept list values (e.g., from --set-json ["AN", "YG"])
            if isinstance(value, list):
                if type_str != "dropdown-multi":
                    raise CLIError(
                        f"List values are only supported for dropdown-multi fields, "
                        f"but '{field.name}' is '{type_str}'.",
                        exit_code=2,
                        error_type="validation_error",
                    )
                all_resolved: list[dict[str, int]] = []
                for item in value:
                    item_result, _ = self.resolve_dropdown_value(field_id, str(item))
                    # Single-element resolve for dropdown-multi returns [{"dropdownOptionId": ID}]
                    if isinstance(item_result, dict):
                        all_resolved.append(item_result)
                    elif isinstance(item_result, list):
                        for entry in item_result:
                            if isinstance(entry, dict):
                                all_resolved.append(entry)
                return all_resolved, type_str

            options = field.dropdown_options

            # First, try to match by option text (case-insensitive)
            value_lower = str(value).strip().lower()
            for opt in options:
                if opt.text.lower() == value_lower:
                    resolved: dict[str, int] = {"dropdownOptionId": int(opt.id)}
                    # dropdown-multi expects array of option objects
                    if type_str == "dropdown-multi":
                        return [resolved], type_str
                    return resolved, type_str

            # Then, try to parse as option ID
            try:
                option_id = int(value)
                # Validate the ID exists
                for opt in options:
                    if int(opt.id) == option_id:
                        resolved = {"dropdownOptionId": option_id}
                        if type_str == "dropdown-multi":
                            return [resolved], type_str
                        return resolved, type_str
                # ID not found in options
                available = [f"'{opt.text}'" for opt in options[:5]]
                suffix = "..." if len(options) > 5 else ""
                raise CLIError(
                    f"Dropdown option ID {option_id} not found for field '{field.name}'.",
                    exit_code=2,
                    error_type="validation_error",
                    hint=f"Available options: {', '.join(available)}{suffix}",
                )
            except ValueError:
                # Not a valid integer, treat as text that wasn't found
                available = [f"'{opt.text}'" for opt in options[:5]]
                suffix = "..." if len(options) > 5 else ""
                raise CLIError(
                    f"Dropdown option '{value}' not found for field '{field.name}'.",
                    exit_code=2,
                    error_type="validation_error",
                    hint=f"Available options: {', '.join(available)}{suffix}",
                ) from None

        # Handle entity-reference fields (person, company and their -multi variants)
        if type_str in ("person", "person-multi", "company", "company-multi"):
            is_multi = type_str.endswith("-multi")

            if isinstance(value, list):
                if not is_multi:
                    raise CLIError(
                        f"List values not supported for '{type_str}' field '{field.name}'.",
                        exit_code=2,
                        error_type="validation_error",
                    )
                return [
                    {"id": _coerce_entity_id(item, field.name, type_str)} for item in value
                ], type_str

            entity_id = _coerce_entity_id(value, field.name, type_str)
            wrapped: dict[str, int] = {"id": entity_id}
            return ([wrapped], type_str) if is_multi else (wrapped, type_str)

        # Date fields: the V2 API requires a full date-time ("2024-04-01" is rejected with
        # 400 "does not match format: date-time") and keeps its UTC calendar date.
        if type_str == "datetime" and isinstance(value, str):
            return _normalize_datetime_input(value, field.name), type_str

        # Numbers: the V2 API rejects numeric strings ("5" -> 400).
        if type_str == "number":
            if isinstance(value, list):
                raise CLIError(
                    f"List values not supported for number field '{field.name}'.",
                    exit_code=2,
                    error_type="validation_error",
                )
            return _coerce_number(value, field.name), type_str
        if type_str == "number-multi":
            items = value if isinstance(value, list) else [value]
            return [_coerce_number(item, field.name) for item in items], type_str

        # Locations: the V2 API requires an object with all five address keys.
        if type_str == "location":
            parsed = _parse_location_input(value, field.name)
            if isinstance(parsed, list):
                raise CLIError(
                    f"List values not supported for location field '{field.name}'.",
                    exit_code=2,
                    error_type="validation_error",
                )
            return parsed, type_str
        if type_str == "location-multi":
            parsed = _parse_location_input(value, field.name)
            return (parsed if isinstance(parsed, list) else [parsed]), type_str

        # Text: the V2 API rejects non-string data (a number from --set-json -> 400).
        if type_str in ("text", "filterable-text") and isinstance(value, (int, float, bool)):
            return (str(value).lower() if isinstance(value, bool) else str(value)), type_str
        if type_str == "filterable-text-multi":
            items = value if isinstance(value, list) else [value]
            return [
                str(item).lower()
                if isinstance(item, bool)
                else str(item)
                if isinstance(item, (int, float))
                else item
                for item in items
            ], type_str

        # Any other multi-value type takes a list: wrap a single value (V2 rejects a scalar).
        if type_str.endswith("-multi") and not isinstance(value, list):
            return [value], type_str

        # For non-dropdown fields, return value and inferred type
        return value, type_str

    # Backward-compat alias
    resolve_dropdown_value = resolve_field_value


# V1 metadata reports the base type plus ``allows_multiple``; these become ``<type>-multi``.
_PROMOTABLE_TO_MULTI = ("dropdown", "person", "company", "number", "location")

LOCATION_KEYS = ("streetAddress", "city", "state", "country", "continent")
_LOCATION_KEY_ALIASES = {"street_address": "streetAddress"}


def _coerce_number(value: Any, field_name: str) -> int | float:
    """A JSON number or numeric string as int/float; anything else is a validation error."""
    import math

    if isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        return value
    elif isinstance(value, str):
        s = value.strip()
        try:
            return int(s)
        except ValueError:
            try:
                f = float(s)
            except ValueError:
                pass
            else:
                if math.isfinite(f):
                    return f
    raise CLIError(
        f"Invalid number '{value}' for field '{field_name}'.",
        exit_code=2,
        error_type="validation_error",
    )


def normalize_location(value: Any, field_name: str) -> dict[str, Any]:
    """A location object in the V2 shape: camelCase keys, all five present (missing -> None)."""
    if not isinstance(value, dict):
        raise CLIError(
            f"Invalid location for field '{field_name}': expected a JSON object such as "
            '{"city": "Paris", "country": "France"}.',
            exit_code=2,
            error_type="validation_error",
        )
    out: dict[str, Any] = dict.fromkeys(LOCATION_KEYS)
    unknown: list[str] = []
    for k, v in value.items():
        key = _LOCATION_KEY_ALIASES.get(k, k)
        if key not in out:
            unknown.append(str(k))
            continue
        if v is not None and not isinstance(v, str):
            raise CLIError(
                f"Invalid location for field '{field_name}': '{k}' must be a string.",
                exit_code=2,
                error_type="validation_error",
            )
        out[key] = v
    if unknown:
        raise CLIError(
            f"Invalid location for field '{field_name}': unknown key(s) {', '.join(unknown)} "
            f"(allowed: {', '.join(LOCATION_KEYS)}).",
            exit_code=2,
            error_type="validation_error",
        )
    if all(v is None for v in out.values()):
        raise CLIError(
            f"Invalid location for field '{field_name}': no address part given.",
            exit_code=2,
            error_type="validation_error",
        )
    return out


def _parse_location_input(value: Any, field_name: str) -> dict[str, Any] | list[dict[str, Any]]:
    """Parse a location from ``--set`` (JSON text) or ``--set-json`` (object or list)."""
    import json

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise CLIError(
                f"Invalid location for field '{field_name}': expected a JSON object such as "
                '{"city": "Paris", "country": "France"}.',
                exit_code=2,
                error_type="validation_error",
            ) from None
    if isinstance(value, list):
        return [normalize_location(item, field_name) for item in value]
    return normalize_location(value, field_name)


def _coerce_entity_id(value: Any, field_name: str, type_str: str) -> int:
    """Coerce a value to an integer entity ID with strict validation.

    Args:
        value: The value to coerce (string or int).
        field_name: Field name for error messages.
        type_str: Field type string for error messages.

    Returns:
        Integer entity ID.

    Raises:
        CLIError: If value is not a valid entity ID.
    """

    def _hint() -> str | None:
        # Type-aware hint pointing at the right lookup command.
        if "person" in type_str:
            return (
                "Resolve names to IDs with "
                "'xaffinity person ls --json --query \"<name>\"', then pass the numeric id."
            )
        if "company" in type_str:
            return (
                "Resolve names to IDs with "
                "'xaffinity company ls --json --query \"<name>\"', then pass the numeric id."
            )
        return None

    # Reject bool before int check (isinstance(True, int) is True)
    if isinstance(value, bool):
        raise CLIError(
            f"Invalid entity ID '{value}' for {type_str} field '{field_name}': "
            "expected a numeric ID.",
            exit_code=2,
            error_type="validation_error",
            hint=_hint(),
        )
    if isinstance(value, int):
        return value
    # String: try to parse as integer
    s = str(value).strip()
    try:
        return int(s)
    except ValueError:
        raise CLIError(
            f"Invalid entity ID '{value}' for {type_str} field '{field_name}': "
            "expected a numeric ID.",
            exit_code=2,
            error_type="validation_error",
            hint=_hint(),
        ) from None


def _extract_entity_id(fv_value: Any) -> int | None:
    """Extract an integer entity ID from an existing field value.

    Handles the various formats returned by the field values API:
    - Dict with "id" key: ``{"id": 123}`` or ``{"id": "123"}``
    - Scalar int or numeric string: ``123`` or ``"123"``

    Args:
        fv_value: The field value from the API.

    Returns:
        Integer entity ID, or None if unparseable.
    """
    if fv_value is None or isinstance(fv_value, bool):
        return None
    if isinstance(fv_value, dict):
        raw_id = fv_value.get("id")
        if raw_id is None or isinstance(raw_id, bool):
            return None
        try:
            return int(raw_id)
        except (ValueError, TypeError):
            return None
    try:
        return int(fv_value)
    except (ValueError, TypeError):
        return None


def _norm_field_id(fid: Any) -> str:
    """Normalize a field id for equality comparison.

    V1 returns ``fieldId`` as a bare int (e.g. 260415). V2 and the CLI
    resolver use ``'field-<n>'``. Enriched literals (``affinity-data-*``,
    ``source-of-introduction``) pass through as-is. This canonicalizes
    both sides so ``str(260415)`` and ``'field-260415'`` compare equal.
    """
    from affinity.models.types import FieldId

    try:
        return str(FieldId(fid))
    except (ValueError, TypeError):
        return str(fid)


def find_field_values_for_field(
    *,
    field_values: list[dict[str, Any]],
    field_id: str | int,
) -> list[dict[str, Any]]:
    """Find all field values matching a specific field ID.

    Args:
        field_values: List of field value dicts from the API.
        field_id: The field ID to match. Accepts 'field-<n>', '<n>',
            numeric int, or an enriched literal.

    Returns:
        List of matching field value dicts.
    """
    target = _norm_field_id(field_id)
    matches: list[dict[str, Any]] = []
    for fv in field_values:
        fv_field_id = fv.get("fieldId") if fv.get("fieldId") is not None else fv.get("field_id")
        if _norm_field_id(fv_field_id) == target:
            matches.append(fv)
    return matches


def format_value_for_comparison(value: Any) -> str:
    """Format a field value for string comparison.

    Non-string values are serialized to their string representation.

    Args:
        value: The field value.

    Returns:
        String representation for comparison.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        # Handle typed values like {type: "...", data: ...}
        data = value.get("data")
        if data is not None:
            return format_value_for_comparison(data)
        text = value.get("text") or value.get("name")
        if text is not None:
            return str(text)
    if isinstance(value, list):
        # For lists, join with comma
        return ", ".join(format_value_for_comparison(v) for v in value)
    return str(value)


# =============================================================================
# No-op short-circuit comparator and pre-validation aggregator
# =============================================================================


def _extract_dropdown_option_id(value: Any) -> int | None:
    """Pull a dropdown-option ID from any of the shapes we encounter.

    Resolved-new shape: ``{"dropdownOptionId": N}``.
    V1 existing shape: ``{"id": N, "text": "..."}`` (dropdownOption embedded as ``id``),
    or sometimes a bare option-id int.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        raw = value.get("dropdownOptionId")
        if raw is None:
            raw = value.get("id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def canonical_item(type_str: str, value: Any) -> Any:
    """A comparable key for one value of a field, from either a resolved (V2) value or an
    existing V1 row value; ``None`` when it can't be determined."""
    if isinstance(value, dict) and "data" in value and "type" in value:
        value = value["data"]
    if is_dropdown_type(type_str):
        return _extract_dropdown_option_id(value)
    if type_str.startswith(("person", "company")):
        return _extract_entity_id(value)
    if type_str.startswith("number"):
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    if type_str.startswith("location"):
        if not isinstance(value, dict):
            return None
        norm = {_LOCATION_KEY_ALIASES.get(k, k): v for k, v in value.items()}
        return tuple((k, (norm.get(k) or None)) for k in LOCATION_KEYS)
    return None if value is None else format_value_for_comparison(value).strip()


def _existing_dropdown_option_id(field_meta: FieldMetadata | None, value: Any) -> int | None:
    """Option id of an existing dropdown row: V1 stores plain dropdown values as the option
    text, ranked ones as an option object. A text row is matched against the field's options
    by text (never parsed as an id: "2024" can be an option's text)."""
    if isinstance(value, dict):
        return _extract_dropdown_option_id(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and field_meta is not None:
        wanted = value.strip().lower()
        for opt in field_meta.dropdown_options:
            if opt.text.strip().lower() == wanted:
                return int(opt.id)
    return None


def _is_empty_new_value(value: Any) -> bool:
    """Is the new value semantically empty? (matches empty existing for no-op)."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return bool(isinstance(value, (list, dict)) and len(value) == 0)


def _normalize_datetime_input(value: str, field_name: str) -> str:
    """Turn CLI date input into the ISO date-time the V2 API accepts (UTC, ``Z``).

    A date-only value (``YYYY-MM-DD``) becomes noon UTC: Affinity keeps the UTC calendar date of
    what it is given, and noon UTC is that date in Pacific time too.
    A date-time keeps its instant; a naive one is local time (see ``parse_iso_datetime``).
    Unparseable input raises ``CLIError`` before any write.
    """
    from datetime import date, timezone

    from .commands._v1_parsing import parse_iso_datetime

    text = value.strip()
    if len(text) == 10:
        try:
            return f"{date.fromisoformat(text).isoformat()}T12:00:00Z"
        except ValueError:
            pass
    dt = parse_iso_datetime(text, label=f"value for '{field_name}'")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _same_affinity_date(new_raw: str, new_dt: Any, old_dt: Any) -> bool:
    """True if a date field stored at midnight Pacific already holds the date being set.

    Affinity drops the time of day from date fields: it keeps the **UTC** calendar date of the
    value written and stores midnight Pacific of that date (2024-04-01 comes back as
    2024-04-01T07:00:00Z; 2024-01-02T03:00:00Z is stored as January 2 - verified live for
    list and enriched fields, 2026-10-08). A date-only input is a calendar date as typed; an
    input with a time maps to its UTC date. Only an existing value at exactly midnight Pacific
    is treated this way; anything else, or a missing tz database, keeps the exact comparison
    (safe default: write).
    """
    from datetime import date, time, timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        pacific = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:
        return False
    old_pacific = old_dt.astimezone(pacific)
    if old_pacific.time() != time(0, 0):
        return False
    text = new_raw.strip()
    try:
        new_date = date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        new_date = None
    if new_date is None:
        new_date = new_dt.astimezone(timezone.utc).date()
    return bool(new_date == old_pacific.date())


def value_equals_existing(
    field_meta: FieldMetadata | None,
    resolved_new: Any,
    existing_for_field: list[dict[str, Any]],
) -> bool:
    """Return True if applying ``resolved_new`` is a true no-op vs ``existing_for_field``.

    Per-type rules (see SKILL/plan):

    - ``dropdown`` / ``ranked-dropdown``: existing must be a single value; compare option IDs.
    - ``dropdown-multi``: set equality on option IDs. Subset is NOT a no-op
      (REPLACE drops the rest).
    - ``person`` / ``company``: single existing; compare entity IDs.
    - ``person-multi`` / ``company-multi``: set equality on entity IDs.
    - ``number``: int/float-coerce both sides.
    - ``datetime``: parse both sides via :func:`parse_iso_datetime` and compare.
    - ``text`` / fallback: exact-string equality after ``.strip()`` on both sides.
    - Empty existing + empty-new → True.

    Returns False on any uncertainty (safe default: write).
    """
    from ..models.types import FieldValueType
    from .commands._v1_parsing import parse_iso_datetime

    if _is_empty_new_value(resolved_new) and not existing_for_field:
        return True

    if field_meta is None:
        existing_strs = [format_value_for_comparison(fv.get("value")) for fv in existing_for_field]
        if len(existing_strs) != 1:
            return False
        return existing_strs[0].strip() == format_value_for_comparison(resolved_new).strip()

    value_type = field_meta.value_type
    type_str = value_type.value if isinstance(value_type, FieldValueType) else str(value_type)
    if field_meta.allows_multiple and type_str in _PROMOTABLE_TO_MULTI:
        type_str = f"{type_str}-multi"

    if type_str in ("number-multi", "location", "location-multi"):
        new_items = resolved_new if isinstance(resolved_new, list) else [resolved_new]
        new_keys = [canonical_item(type_str, v) for v in new_items]
        old_keys = [canonical_item(type_str, fv.get("value")) for fv in existing_for_field]
        if None in new_keys or None in old_keys:
            return False
        return sorted(map(repr, new_keys)) == sorted(map(repr, old_keys))

    if type_str in ("dropdown", *_DROPDOWN_BY_ID):
        if len(existing_for_field) != 1:
            return False
        new_id = _extract_dropdown_option_id(resolved_new)
        old_id = _existing_dropdown_option_id(field_meta, existing_for_field[0].get("value"))
        return new_id is not None and new_id == old_id

    if type_str == "dropdown-multi":
        new_ids: set[int] = set()
        candidates = resolved_new if isinstance(resolved_new, list) else [resolved_new]
        for item in candidates:
            oid = _extract_dropdown_option_id(item)
            if oid is None:
                return False
            new_ids.add(oid)
        old_ids: set[int] = set()
        for fv in existing_for_field:
            oid = _existing_dropdown_option_id(field_meta, fv.get("value"))
            if oid is None:
                return False
            old_ids.add(oid)
        return new_ids == old_ids

    if type_str in ("person", "company"):
        if len(existing_for_field) != 1:
            return False
        new_id = _extract_entity_id(resolved_new)
        old_id = _extract_entity_id(existing_for_field[0].get("value"))
        return new_id is not None and new_id == old_id

    if type_str in ("person-multi", "company-multi"):
        new_eids: set[int] = set()
        candidates = resolved_new if isinstance(resolved_new, list) else [resolved_new]
        for item in candidates:
            eid = _extract_entity_id(item)
            if eid is None:
                return False
            new_eids.add(eid)
        old_eids: set[int] = set()
        for fv in existing_for_field:
            eid = _extract_entity_id(fv.get("value"))
            if eid is None:
                return False
            old_eids.add(eid)
        return new_eids == old_eids

    if type_str in ("number", "number-multi"):
        if len(existing_for_field) != 1:
            return False
        try:
            new_n = float(str(resolved_new).strip())
            old_raw = existing_for_field[0].get("value")
            if isinstance(old_raw, dict):
                old_raw = old_raw.get("data", old_raw)
            if old_raw is None:
                return False
            old_n = float(old_raw)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return False
        return new_n == old_n

    if type_str == "datetime":
        if len(existing_for_field) != 1:
            return False
        try:
            new_dt = parse_iso_datetime(str(resolved_new), label="set value")
            old_raw = existing_for_field[0].get("value")
            if isinstance(old_raw, dict):
                old_raw = old_raw.get("data", old_raw)
            old_dt = parse_iso_datetime(str(old_raw), label="existing value")
        except CLIError:
            return False
        return new_dt == old_dt or _same_affinity_date(str(resolved_new), new_dt, old_dt)

    # text / filterable-text / fallback
    if len(existing_for_field) != 1:
        return False
    new_s = format_value_for_comparison(resolved_new).strip()
    old_s = format_value_for_comparison(existing_for_field[0].get("value")).strip()
    return new_s == old_s


def pre_validate_set_operations(
    resolver: FieldResolver,
    set_operations: list[tuple[str, Any]],
) -> dict[str, tuple[Any, Any, str]]:
    """Resolve every (field_id, value) up front, aggregating errors.

    Returns a dict ``{field_id: (raw_value, resolved_value, value_type_str)}``.
    Raises a single :class:`CLIError` with structured ``details`` listing all
    invalid values; this is a deliberate departure from
    :meth:`FieldResolver.resolve_field_value`'s raise-on-first contract.

    Why a 3-tuple? V2 callers (list_entry) send the **resolved** payload
    (e.g. ``{"dropdownOptionId": N}``) on the wire. V1 callers
    (company/person/opportunity) send the **raw** user value and let the server
    resolve. Both need the resolved form for the no-op short-circuit
    (:func:`value_equals_existing`).

    Args:
        resolver: A :class:`FieldResolver` built from the relevant list/entity
            metadata. Callers must have already mapped field names to IDs (use
            :meth:`FieldResolver.resolve_field_name_or_id` first).
        set_operations: List of ``(field_id, value)`` pairs to validate.
            ``field_id`` must already be resolved to a canonical field-id
            string (``'field-<n>'`` or an enriched literal).
    """
    resolved: dict[str, tuple[Any, Any, str]] = {}
    errors: list[dict[str, Any]] = []

    for field_id, value in set_operations:
        try:
            res_val, type_str = resolver.resolve_field_value(field_id, value)
            resolved[field_id] = (value, res_val, type_str)
        except CLIError as exc:
            field_name = resolver.get_field_name(field_id) or field_id
            errors.append(
                {
                    "field": field_name,
                    "fieldId": field_id,
                    "value": value,
                    "reason": exc.message,
                    "hint": exc.hint,
                }
            )

    if not errors:
        return resolved

    lines = [f"Cannot apply --set: {len(errors)} value(s) failed validation:"]
    hints: list[str] = []
    for err in errors:
        lines.append(f"  - {err['field']}={err['value']!r}: {err['reason']}")
        if err["hint"] and err["hint"] not in hints:
            hints.append(err["hint"])
    raise CLIError(
        "\n".join(lines),
        exit_code=2,
        error_type="validation_error",
        details={"failures": errors},
        hint=" / ".join(hints) if hints else None,
    )


# =============================================================================
# Write helpers (V1 and V2 set phases, V2 append phase)
# =============================================================================


def _refresh_existing_after_change(
    existing_values_serialized: list[dict[str, Any]],
    field_id: str,
    deleted_fv_ids: list[int],
    new_fv_serialized: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return a new existing-values list reflecting a single field's write.

    Existing rows whose ``id`` is in ``deleted_fv_ids`` (or whose
    ``fieldId`` matches ``field_id`` when ``new_fv_serialized`` is provided)
    are removed; ``new_fv_serialized`` (if any) is appended.
    """
    canonical_target = _norm_field_id(field_id)
    next_list: list[dict[str, Any]] = []
    for fv in existing_values_serialized:
        if fv.get("id") in deleted_fv_ids:
            continue
        if new_fv_serialized is not None:
            fv_field_id = fv.get("fieldId") if fv.get("fieldId") is not None else fv.get("field_id")
            if _norm_field_id(fv_field_id) == canonical_target:
                # Drop any leftover rows for this field (defensive)
                continue
        next_list.append(fv)
    if new_fv_serialized is not None:
        next_list.append(new_fv_serialized)
    return next_list


def _serialize(obj: Any) -> dict[str, Any]:
    """Best-effort serializer for SDK models or already-serialized dicts."""
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, dict):
            return result
    return {"value": obj}


def _truncated_in(fields: Any) -> list[tuple[str, int, int]]:
    """``(field name or id, returned, total)`` for each truncated multi-value field.

    Accepts raw V2 field objects (``[{"id", "name", "value": {"data", "totalCount"}}]``), a
    dict keyed by field ID, or a :class:`FieldValues` container.
    """
    data = getattr(fields, "data", fields)
    if isinstance(data, dict):
        items: list[Any] = list(data.values())
    elif isinstance(data, list):
        items = data
    else:
        return []
    out: list[tuple[str, int, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        total, values = value.get("totalCount"), value.get("data")
        if isinstance(total, int) and isinstance(values, list) and total > len(values):
            out.append((str(item.get("name") or item.get("id")), len(values), total))
    return out


def _field_items(fields: Any) -> list[Any]:
    data = getattr(fields, "data", fields)
    if isinstance(data, dict):
        return list(data.values())
    if isinstance(data, list):
        return data
    return []


def _hidden_in(fields: Any) -> list[str]:
    """Names (or ids) of fields Affinity returned as ``type: "hidden"``.

    Since API version 2026-07-15, a field on a restricted opportunity that the API key can't
    manage comes back with ``type: "hidden"`` and an empty value: it is masked, not empty.
    """
    return [
        str(item.get("name") or item.get("id"))
        for item in _field_items(fields)
        if isinstance(item, dict) and str(item.get("type") or "").lower() == "hidden"
    ]


def truncation_warnings(
    records: Iterable[tuple[str, Any]], *, filtered: bool = False, examples: int = 3
) -> list[str]:
    """Warnings for field values Affinity didn't return in full.

    - multi-value fields cut off at 100 values (``totalCount``);
    - fields hidden by Affinity (``type: "hidden"``: masked, not empty).

    ``records`` yields ``(record label, raw fields)``. Returns at most one warning per kind: a
    precise one for a single field, otherwise a summary with a few examples. ``filtered`` adds
    that a client-side filter was evaluated on the returned values only.
    """
    records = list(records)
    out = _truncation_warning(records, filtered=filtered, examples=examples)
    hidden = [(label, name) for label, fields in records for name in _hidden_in(fields)]
    if hidden:
        shown = "; ".join(f"'{n}' on {lbl}" for lbl, n in hidden[:examples])
        more = f"; and {len(hidden) - examples} more" if len(hidden) > examples else ""
        msg = (
            f"{len(hidden)} field value(s) hidden by Affinity: {shown}{more}. They belong to "
            "restricted opportunities your API key can't manage and are masked, not empty; "
            "don't treat them as empty or overwrite them."
        )
        if filtered:
            msg += " Filters treated them as empty."
        out.append(msg)
    return out


def _truncation_warning(
    records: list[tuple[str, Any]], *, filtered: bool, examples: int
) -> list[str]:
    hits = [
        (label, name, got, total)
        for label, fields in records
        for name, got, total in _truncated_in(fields)
    ]
    if not hits:
        return []
    if len(hits) == 1:
        label, name, got, total = hits[0]
        msg = (
            f"Field '{name}' on {label} shows {got} of {total} values "
            "(Affinity returns at most 100 values per field)."
        )
    else:
        shown = "; ".join(f"'{n}' on {lbl} ({g} of {t})" for lbl, n, g, t in hits[:examples])
        more = f"; and {len(hits) - examples} more" if len(hits) > examples else ""
        records_n = len({h[0] for h in hits})
        msg = (
            f"{len(hits)} multi-value field(s) on {records_n} record(s) were cut off by Affinity "
            f"at 100 values: {shown}{more}."
        )
    if filtered:
        msg += " Filters on those fields were evaluated on the returned values only."
    return [msg]


def check_multi_value_limits(
    *,
    resolver: FieldResolver,
    pre_resolved_set: dict[str, tuple[Any, Any, str]],
    append_ops: list[tuple[str, Any]],
    existing_values_serialized: list[dict[str, Any]],
) -> None:
    """Fail before any write if a multi-value field would exceed Affinity's 100-value cap.

    Runs before any write, so an over-cap request changes nothing (the server would reject
    the write anyway, and a multi-field command must not stop half-way).
    ``--append`` counts the existing values plus the new ones not already present, the same
    way :func:`execute_append_phase` merges them.
    """
    from affinity.services._field_updates import CAPPED_MULTI_VALUE_TYPES, MAX_MULTI_VALUES

    final_counts: dict[str, tuple[str, int]] = {}
    for field_id, (_raw, resolved_value, type_str) in pre_resolved_set.items():
        if type_str in CAPPED_MULTI_VALUE_TYPES and isinstance(resolved_value, list):
            final_counts[field_id] = (type_str, len(resolved_value))

    new_by_field: dict[str, list[Any]] = {}
    type_by_field: dict[str, str] = {}
    for field_id, value in append_ops:
        res_val, type_str = resolver.resolve_field_value(field_id, value)
        type_by_field[field_id] = type_str
        new_by_field.setdefault(field_id, []).extend(
            res_val if isinstance(res_val, list) else [res_val]
        )
    for field_id, new_items in new_by_field.items():
        type_str = type_by_field[field_id]
        if type_str not in CAPPED_MULTI_VALUE_TYPES:
            continue
        if field_id in final_counts:
            # --set and --append on the same field: the set replaces, then append adds.
            base_count = final_counts[field_id][1]
            existing_ids: set[Any] = set()
        else:
            existing = find_field_values_for_field(
                field_values=existing_values_serialized, field_id=field_id
            )
            existing_ids = {canonical_item(type_str, fv.get("value")) for fv in existing}
            base_count = len(existing)
        added = {repr(canonical_item(type_str, item)) for item in new_items} - {
            repr(k) for k in existing_ids
        }
        final_counts[field_id] = (type_str, base_count + len(added))

    over = {fid: n for fid, (_t, n) in final_counts.items() if n > MAX_MULTI_VALUES}
    if over:
        parts = [
            f"'{resolver.get_field_name(fid) or fid}' would hold {n}" for fid, n in over.items()
        ]
        raise CLIError(
            f"Affinity accepts at most {MAX_MULTI_VALUES} values per multi-value field: "
            + "; ".join(parts)
            + ". Nothing was changed.",
            exit_code=2,
            error_type="usage_error",
            details={"maxValues": MAX_MULTI_VALUES, "fields": over},
        )


def _v2_item_from_v1(type_str: str, value: Any) -> Any:
    """One existing V1 row value in the V2 write shape of ``type_str``."""
    if type_str.startswith(("person", "company")):
        eid = _extract_entity_id(value)
        return None if eid is None else {"id": eid}
    if type_str.startswith("location") and isinstance(value, dict):
        norm = {_LOCATION_KEY_ALIASES.get(k, k): v for k, v in value.items()}
        return {k: norm.get(k) for k in LOCATION_KEYS}
    if type_str.startswith("number"):
        return canonical_item(type_str, value)
    return value


def check_append_targets(*, resolver: FieldResolver, append_ops: list[tuple[str, Any]]) -> None:
    """Fail before any write if ``--append`` targets a field that holds a single value."""
    unknown = sorted(
        {field_id for field_id, _ in append_ops if resolver.get_field_metadata(field_id) is None}
    )
    if unknown:
        raise CLIError(
            f"--append: can't tell whether {', '.join(repr(f) for f in unknown)} hold(s) "
            "several values (no field information on this list, e.g. an enriched field). "
            "Use --set with the full value. Nothing was changed.",
            exit_code=2,
            error_type="usage_error",
        )
    single = sorted(
        {
            resolver.get_field_name(field_id) or field_id
            for field_id, value in append_ops
            if not resolver.resolve_field_value(field_id, value)[1].endswith("-multi")
        }
    )
    if single:
        raise CLIError(
            f"--append adds to multi-value fields; {', '.join(repr(n) for n in single)} "
            "hold(s) one value. Use --set to replace it. Nothing was changed.",
            exit_code=2,
            error_type="usage_error",
        )


def execute_append_phase(
    *,
    entries: Any,
    list_entry_id: int,
    append_ops: list[tuple[str, Any]],
    existing_values_serialized: list[dict[str, Any]],
    resolver: FieldResolver,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """V2-only --append phase. Merges with existing values for multi-fields.

    Group multiple ``--append`` flags by field, resolve, then merge with the
    existing values of the multi-value field (dropdown, person, company,
    number, location) so the
    write is a true append (the V2 endpoint replaces the whole array, so we
    have to include the existing IDs).

    Single-value fields are refused (:func:`check_append_targets` runs before any
    write). For each grouped field, short-circuits when all new values are
    already present.

    ``append_ops`` MUST be the *resolved* field-id list (callers should run
    pre-validation first to catch invalid values before any side-effect).
    """
    from collections import OrderedDict

    from affinity.models.types import FieldId
    from affinity.types import EnrichedFieldId, ListEntryId

    if not append_ops:
        return [], list(existing_values_serialized)

    append_groups: OrderedDict[str, list[Any]] = OrderedDict()
    for field_id, value in append_ops:
        append_groups.setdefault(field_id, []).append(value)

    created: list[dict[str, Any]] = []
    refreshed = list(existing_values_serialized)

    for field_id, values_for_field in append_groups.items():
        try:
            parsed_field_id: Any = FieldId(field_id)
        except (ValueError, TypeError):
            parsed_field_id = EnrichedFieldId(field_id)

        all_new_resolved: list[Any] = []
        value_type_str = "text"
        for val in values_for_field:
            res_val, value_type_str = resolver.resolve_field_value(field_id, val)
            if isinstance(res_val, list):
                all_new_resolved.extend(res_val)
            else:
                all_new_resolved.append(res_val)

        existing_for_field = find_field_values_for_field(field_values=refreshed, field_id=field_id)

        if value_type_str == "dropdown-multi" and all_new_resolved:
            # Build pre-existing option-id set by extracting IDs from each row.
            # Existing rows store dropdown values as ``{"id": N, "text": "..."}``
            # (V1 returns the option object directly under .value) or as plain
            # text. ``_extract_dropdown_option_id`` handles both shapes.
            pre_existing_ids: set[int] = set()
            pre_existing_opts: list[dict[str, int]] = []
            for fv in existing_for_field:
                fv_value = fv.get("value")
                opt_id = _extract_dropdown_option_id(fv_value)
                if opt_id is None and isinstance(fv_value, str):
                    # Fallback: text label — resolve via the field-meta options.
                    field_meta = resolver.get_field_metadata(field_id)
                    if field_meta is not None:
                        for opt in field_meta.dropdown_options:
                            if opt.text.strip().lower() == fv_value.strip().lower():
                                opt_id = int(opt.id)
                                break
                if opt_id is not None and opt_id not in pre_existing_ids:
                    pre_existing_ids.add(opt_id)
                    pre_existing_opts.append({"dropdownOptionId": opt_id})

            new_ids: set[int] = set()
            new_opts_to_add: list[dict[str, int]] = []
            for opt in all_new_resolved:
                if isinstance(opt, dict):
                    opt_id = opt.get("dropdownOptionId")
                    if opt_id is not None:
                        new_ids.add(int(opt_id))
                        if int(opt_id) not in pre_existing_ids:
                            new_opts_to_add.append(opt)

            # No-op: every new id is already in existing.
            if new_ids and new_ids.issubset(pre_existing_ids):
                continue
            final_value: Any = pre_existing_opts + new_opts_to_add

        elif value_type_str.endswith("-multi") and all_new_resolved:
            # person/company/number/location multi: keep existing values, add the new ones.
            existing_items = [
                _v2_item_from_v1(value_type_str, fv.get("value")) for fv in existing_for_field
            ]
            existing_keys = [canonical_item(value_type_str, v) for v in existing_items]
            if None in existing_keys:
                raise CLIError(
                    f"Cannot append to field '{resolver.get_field_name(field_id) or field_id}': "
                    "an existing value could not be read back, so the merged list could drop "
                    "it. Nothing was changed; use --set-json with the full list instead.",
                    exit_code=2,
                    error_type="usage_error",
                )
            to_add: list[Any] = []
            for item in all_new_resolved:
                key = canonical_item(value_type_str, item)
                if key not in existing_keys:
                    existing_keys.append(key)
                    to_add.append(item)
            if not to_add:
                continue
            final_value = existing_items + to_add

        else:
            # Single-value fields are refused before any write (check_append_targets).
            raise CLIError(
                f"Field '{resolver.get_field_name(field_id) or field_id}' holds one value; "
                "use --set to replace it.",
                exit_code=2,
                error_type="usage_error",
            )

        result = entries.update_field_value(
            ListEntryId(list_entry_id),
            parsed_field_id,
            final_value,
            value_type=value_type_str,
        )
        new_serialized = _serialize(result)
        created.append(new_serialized)
        # For multi-value fields the V2 POST replaces the entire row set; for the
        # refresh, we drop all old rows for this field and append the single new
        # FieldValues row (best-effort — caller can refetch if they need exact state).
        refreshed = _refresh_existing_after_change(
            refreshed,
            field_id,
            [int(fv["id"]) for fv in existing_for_field if fv.get("id") is not None],
            new_serialized,
        )

    return created, refreshed


def rows_from_v2_field_values(fields: Any) -> list[dict[str, Any]]:
    """Existing values read from V2 (``FieldValues``) as rows for :func:`value_equals_existing`:
    one ``{"fieldId", "value"}`` row per value, keyed by the V2 field id.

    A multi-value field read at the 100-value page limit may hold more values than were
    returned; it gets an extra ``None`` row so it never compares equal (safe default: write).
    """
    from affinity.services._field_updates import MAX_MULTI_VALUES

    rows: list[dict[str, Any]] = []
    for field_id, field_obj in (getattr(fields, "data", None) or {}).items():
        value = field_obj.get("value") if isinstance(field_obj, dict) else None
        data = value.get("data") if isinstance(value, dict) else None
        if data is None:
            continue
        if isinstance(data, list):
            rows.extend({"fieldId": field_id, "value": item} for item in data)
            if len(data) >= MAX_MULTI_VALUES:
                rows.append({"fieldId": field_id, "value": None})
        else:
            rows.append({"fieldId": field_id, "value": data})
    return rows


def apply_field_updates(
    *,
    resolver: FieldResolver,
    pre_resolved_set: dict[str, tuple[Any, Any, str]],
    unsets: Sequence[tuple[str, str]],
    existing_values_serialized: list[dict[str, Any]],
    write: Callable[[dict[str, Any], dict[str, str]], Any],
    append_ops: Sequence[tuple[str, Any]] = (),
    permission_hint: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Write every ``--set`` / ``--set-json`` / ``--unset`` of a command in ONE request.

    Affinity applies the whole batch or none of it, and each update replaces the field's value
    (a typed ``None`` clears it), so nothing is deleted first. Fields whose new value equals the
    existing one are skipped. ``unsets`` are ``(spec as typed, field id)`` pairs; they are always
    sent (clearing is idempotent, and reads can lag a write). ``append_ops`` only feed the
    multi-value size check, which runs before anything is written.

    ``write(updates, value_types)`` performs the request. Returns ``(created, cleared,
    written_field_ids)`` with items ``{fieldId, name, value}`` / ``{fieldId, name}``.
    """
    import click

    from affinity.exceptions import (
        AffinityError,
        AuthorizationError,
        NetworkError,
        TimeoutError,
        WriteNotAllowedError,
    )
    from affinity.services._field_updates import MAX_BATCH_UPDATES

    # Keep the "Replaced N existing values" warning for fields that will actually change.
    for target_field_id, (_raw, new_value, _t) in pre_resolved_set.items():
        existing_for_field = find_field_values_for_field(
            field_values=existing_values_serialized, field_id=target_field_id
        )
        if len(existing_for_field) > 1 and not value_equals_existing(
            resolver.get_field_metadata(target_field_id), new_value, existing_for_field
        ):
            resolved_name = resolver.get_field_name(target_field_id) or target_field_id
            old_vals = [fv.get("value") for fv in existing_for_field]
            display_vals = (
                [*old_vals[:3], f"...{len(old_vals) - 3} more..."]
                if len(old_vals) > 5
                else old_vals
            )
            click.echo(
                f"Warning: Replaced {len(existing_for_field)} existing values "
                f"for field '{resolved_name}': {display_vals}",
                err=True,
            )

    # Size check BEFORE any write, so an over-cap request changes nothing.
    check_multi_value_limits(
        resolver=resolver,
        pre_resolved_set=pre_resolved_set,
        append_ops=list(append_ops),
        existing_values_serialized=existing_values_serialized,
    )

    updates: dict[str, Any] = {}
    value_types: dict[str, str] = {}
    for target_field_id, (_raw, new_value, type_str) in pre_resolved_set.items():
        existing_for_field = find_field_values_for_field(
            field_values=existing_values_serialized, field_id=target_field_id
        )
        if value_equals_existing(
            resolver.get_field_metadata(target_field_id), new_value, existing_for_field
        ):
            continue
        updates[target_field_id] = new_value
        value_types[target_field_id] = type_str
    for spec, target_field_id in unsets:
        write_type = resolver.write_type(target_field_id)
        if write_type is None:
            raise CLIError(
                f"Cannot clear '{spec}': its value type is unknown. Nothing was changed.",
                exit_code=2,
                error_type="usage_error",
            )
        updates[target_field_id] = None
        value_types[target_field_id] = write_type

    unwritable = {fid: t for fid, t in value_types.items() if t in UNWRITABLE_TYPES}
    if unwritable:
        names = ", ".join(
            f"'{resolver.get_field_name(f) or f}' ({t})" for f, t in unwritable.items()
        )
        raise CLIError(
            f"Affinity doesn't accept writes to: {names}. Nothing was changed.",
            exit_code=2,
            error_type="usage_error",
        )
    if len(updates) > MAX_BATCH_UPDATES:
        raise CLIError(
            f"{len(updates)} fields in one command; Affinity accepts at most "
            f"{MAX_BATCH_UPDATES} per request. Nothing was changed; split the command.",
            exit_code=2,
            error_type="usage_error",
        )
    if not updates:
        return [], [], []

    names = ", ".join(resolver.get_field_name(f) or f for f in updates)
    try:
        write(updates, value_types)
    except (TimeoutError, NetworkError) as exc:
        raise CLIError(
            f"The update of {names} may or may not have been applied (no response "
            f"from Affinity: {exc}). Re-running the command is safe.",
            exit_code=1,
            error_type="network_error",
        ) from exc
    except WriteNotAllowedError:
        raise
    except AuthorizationError as exc:
        raise CLIError(
            f"Affinity refused the update of {names}; nothing was changed. {permission_hint}{exc}",
            exit_code=1,
            error_type="permission_denied",
        ) from exc
    except AffinityError as exc:
        from affinity.exceptions import UnsupportedApiVersionError

        if isinstance(exc, UnsupportedApiVersionError):
            raise
        if getattr(exc, "status_code", None) and int(exc.status_code or 0) < 500:
            raise CLIError(
                f"Affinity rejected the update of {names}; nothing was changed. {exc}",
                exit_code=1,
                error_type="api_error",
            ) from exc
        raise

    created: list[dict[str, Any]] = []
    cleared: list[dict[str, Any]] = []
    for fid, value in updates.items():
        item = {"fieldId": fid, "name": resolver.get_field_name(fid) or fid}
        if value is None:
            cleared.append(item)
        else:
            created.append({**item, "value": value})
    return created, cleared, list(updates)
