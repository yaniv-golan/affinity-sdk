"""Utilities for field name resolution and field metadata management.

This module provides shared helpers for resolving human-readable field names
to field IDs across person/company/opportunity/list-entry commands.
"""

from __future__ import annotations

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
                hint="Use --field-id with the specific field ID instead.",
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

    def to_v1_numeric(
        self,
        client: Any,
        field_id: str,
        entity_type: EntityType,
    ) -> int:
        """Resolve any field id (regular or enriched) to its V1 numeric id.

        V1 `/field-values` writes require numeric field ids. V2 enriched fields
        (``affinity-data-*``, ``source-of-introduction``, etc.) must be mapped
        to their V1 twin by ``(name, enrichment_source)`` with ``list_id=null``.

        Args:
            client: The Affinity client (sync or async — only `.fields.list` is used).
            field_id: 'field-<n>' or any enriched literal returned by V2.
            entity_type: CLI entity-type string ("person" / "company" / "opportunity").

        Returns:
            Numeric V1 field id.

        Raises:
            EnrichedFieldNotWritableError: if the enriched field has no V1 twin.
        """
        from affinity.exceptions import EnrichedFieldNotWritableError
        from affinity.models.types import (
            EntityType as EntityTypeEnum,
        )
        from affinity.models.types import (
            FieldId as FieldIdType,
        )
        from affinity.models.types import (
            field_id_to_v1_numeric,
        )

        if field_id.startswith("field-"):
            return field_id_to_v1_numeric(FieldIdType(field_id))

        meta = self.get_field_metadata(field_id)
        if meta is None:
            raise EnrichedFieldNotWritableError(field_id=field_id, reason="unknown enriched id")

        enum_map: dict[str, EntityTypeEnum] = {
            "person": EntityTypeEnum.PERSON,
            "company": EntityTypeEnum.ORGANIZATION,
            "opportunity": EntityTypeEnum.OPPORTUNITY,
        }
        if entity_type not in enum_map:
            raise EnrichedFieldNotWritableError(
                field_id=field_id,
                reason=f"no V1 field lookup for entity_type={entity_type!r}",
            )

        v1_fields = client.fields.list(entity_type=enum_map[entity_type])
        v2_src = meta.enrichment_source  # None for RI fields

        def _norm_v1_src(s: str | None) -> str | None:
            return None if s in (None, "none", "") else s

        candidates = [
            f
            for f in v1_fields
            if f.name == meta.name
            and f.list_id is None
            and _norm_v1_src(f.enrichment_source) == v2_src
        ]
        if not candidates:
            raise EnrichedFieldNotWritableError(
                field_id=field_id,
                reason=(
                    f"no V1 global twin for (name={meta.name!r}, enrichment_source={v2_src!r})"
                ),
            )
        # Canonical write target — FieldId accepts int and normalizes to 'field-<n>'
        v1_id = candidates[0].id
        return (
            int(str(v1_id).removeprefix("field-"))
            if str(v1_id).startswith("field-")
            else int(v1_id)
        )

    def resolve_field_value(
        self, field_id: str, value: str | list[str]
    ) -> tuple[list[dict[str, int]] | dict[str, int] | str | list[str], str]:
        """Resolve a field value to the format expected by the V2 API.

        Handles type-specific wrapping:
        - Dropdown/ranked-dropdown/dropdown-multi: text/ID → ``{"dropdownOptionId": ID}``
        - Person/company: ID → ``{"id": ID}``
        - Person-multi/company-multi: ID(s) → ``[{"id": ID}, ...]``
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
        if field.allows_multiple and type_str in ("dropdown", "person", "company"):
            type_str = f"{type_str}-multi"

        # Handle dropdown, ranked-dropdown, and dropdown-multi fields
        # V2 API expects:
        #   dropdown/ranked-dropdown: {"data": {"dropdownOptionId": ID}, "type": "..."}
        #   dropdown-multi: {"data": [{"dropdownOptionId": ID}], "type": "dropdown-multi"}
        if type_str in ("dropdown", "ranked-dropdown", "dropdown-multi"):
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

        # For non-dropdown fields, return value and inferred type
        return value, type_str

    # Backward-compat alias
    resolve_dropdown_value = resolve_field_value


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
    # Reject bool before int check (isinstance(True, int) is True)
    if isinstance(value, bool):
        raise CLIError(
            f"Invalid entity ID '{value}' for {type_str} field '{field_name}': "
            "expected a numeric ID.",
            exit_code=2,
            error_type="validation_error",
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


def validate_field_option_mutual_exclusion(
    *,
    field: str | None,
    field_id: str | None,
) -> None:
    """Validate that exactly one of --field or --field-id is provided.

    Args:
        field: The --field option value.
        field_id: The --field-id option value.

    Raises:
        CLIError: If neither or both options are provided.
    """
    if field is None and field_id is None:
        raise CLIError(
            "Must specify either --field or --field-id.",
            exit_code=2,
            error_type="usage_error",
        )
    if field is not None and field_id is not None:
        raise CLIError(
            "Use only one of --field or --field-id.",
            exit_code=2,
            error_type="usage_error",
        )


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
