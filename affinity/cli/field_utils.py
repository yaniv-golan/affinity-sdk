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


def _is_empty_new_value(value: Any) -> bool:
    """Is the new value semantically empty? (matches empty existing for no-op)."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return bool(isinstance(value, (list, dict)) and len(value) == 0)


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
    if field_meta.allows_multiple and type_str in ("dropdown", "person", "company"):
        type_str = f"{type_str}-multi"

    if type_str in ("dropdown", "ranked-dropdown"):
        if len(existing_for_field) != 1:
            return False
        new_id = _extract_dropdown_option_id(resolved_new)
        old_id = _extract_dropdown_option_id(existing_for_field[0].get("value"))
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
            oid = _extract_dropdown_option_id(fv.get("value"))
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
            old_n = float(old_raw)
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
        return new_dt == old_dt

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


def execute_v2_set_phase(
    *,
    client: Any,
    entries: Any,
    list_entry_id: int,
    pre_resolved_ops: dict[str, tuple[Any, Any, str]],
    existing_values_serialized: list[dict[str, Any]],
    resolver: FieldResolver,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Apply pre-validated --set operations via V2 ``entries.update_field_value``.

    Skips delete+create when :func:`value_equals_existing` reports a no-op
    (the audit log stays clean on retries). Returns refreshed existing-values
    so a subsequent :func:`execute_append_phase` does not re-fetch and does
    not see stale data.

    Args:
        client: Sync ``Affinity`` client.
        entries: ``client.lists.entries(list_id)`` accessor.
        list_entry_id: The list entry being updated.
        pre_resolved_ops: Output of :func:`pre_validate_set_operations`.
            Maps ``field_id -> (raw, resolved, value_type_str)``.
        existing_values_serialized: Current field values (each a dict from
            ``serialize_model_for_cli``).
        resolver: For looking up :class:`FieldMetadata` per field-id.

    Returns:
        ``(created_values, deleted_count, refreshed_existing_values)``.
    """
    from affinity.models.types import FieldId
    from affinity.types import EnrichedFieldId, ListEntryId

    created: list[dict[str, Any]] = []
    deleted_count = 0
    refreshed = list(existing_values_serialized)

    for field_id, (_raw, resolved_value, value_type_str) in pre_resolved_ops.items():
        existing_for_field = find_field_values_for_field(field_values=refreshed, field_id=field_id)
        field_meta = resolver.get_field_metadata(field_id)

        if value_equals_existing(field_meta, resolved_value, existing_for_field):
            continue

        deleted_ids: list[int] = []
        for fv in existing_for_field:
            fv_id = fv.get("id")
            if fv_id is not None:
                client.field_values.delete(fv_id)
                deleted_ids.append(int(fv_id))
                deleted_count += 1

        try:
            parsed_field_id: Any = FieldId(field_id)
        except (ValueError, TypeError):
            parsed_field_id = EnrichedFieldId(field_id)

        result = entries.update_field_value(
            ListEntryId(list_entry_id),
            parsed_field_id,
            resolved_value,
            value_type=value_type_str,
        )
        new_serialized = _serialize(result)
        created.append(new_serialized)
        refreshed = _refresh_existing_after_change(refreshed, field_id, deleted_ids, new_serialized)

    return created, deleted_count, refreshed


def execute_v1_set_phase(
    *,
    client: Any,
    entity_kind: Literal["company", "person", "opportunity"],
    entity_id: int,
    pre_resolved_ops: dict[str, tuple[Any, Any, str]],
    existing_values_serialized: list[dict[str, Any]],
    resolver: FieldResolver,
) -> tuple[list[dict[str, Any]], int]:
    """Apply pre-validated --set operations via V1 ``field_values.create``.

    Sends the **raw** user value (not the resolved payload) on the wire
    because V1's ``FieldValueCreate`` schema expects scalars/strings and
    server-side resolves dropdown text and entity references. The no-op
    short-circuit still uses the resolved value for accurate comparison.

    For enriched fields, resolves the V2 enriched-field literal to its V1
    numeric id via :meth:`FieldResolver.to_v1_numeric` before writing.
    """
    from affinity.models.entities import FieldValueCreate
    from affinity.models.types import FieldId as FieldIdType

    created: list[dict[str, Any]] = []
    deleted_count = 0

    for field_id, (raw_value, resolved_value, _value_type_str) in pre_resolved_ops.items():
        numeric_field_id = resolver.to_v1_numeric(client, field_id, entity_type=entity_kind)
        # V1 field-value rows are keyed by numeric field-id, so the no-op
        # comparison must match on that canonical form.
        existing_for_field = find_field_values_for_field(
            field_values=existing_values_serialized, field_id=numeric_field_id
        )
        field_meta = resolver.get_field_metadata(field_id)

        if value_equals_existing(field_meta, resolved_value, existing_for_field):
            continue

        for fv in existing_for_field:
            fv_id = fv.get("id")
            if fv_id is not None:
                client.field_values.delete(fv_id)
                deleted_count += 1

        result = client.field_values.create(
            FieldValueCreate(
                field_id=FieldIdType(numeric_field_id),
                entity_id=entity_id,
                value=raw_value,
            )
        )
        created.append(_serialize(result))

    return created, deleted_count


def execute_append_phase(
    *,
    client: Any,  # noqa: ARG001 - kept for symmetry with set helpers + future use
    entries: Any,
    list_entry_id: int,
    append_ops: list[tuple[str, Any]],
    existing_values_serialized: list[dict[str, Any]],
    resolver: FieldResolver,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """V2-only --append phase. Merges with existing values for multi-fields.

    Group multiple ``--append`` flags by field, resolve, then merge with the
    existing dropdown-multi / person-multi / company-multi values so the
    write is a true append (the V2 endpoint replaces the whole array, so we
    have to include the existing IDs).

    For single-value fields, ``--append`` is just an overwrite — there is
    only one slot. For each grouped field, short-circuits when all new IDs
    are already in existing.

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

        elif value_type_str in ("person-multi", "company-multi") and all_new_resolved:
            pre_existing_entity_ids: set[int] = {
                eid
                for fv in existing_for_field
                if (eid := _extract_entity_id(fv.get("value"))) is not None
            }
            new_entity_ids: set[int] = set()
            entities_to_add: list[dict[str, int]] = []
            for opt in all_new_resolved:
                if isinstance(opt, dict):
                    eid = opt.get("id")
                    if eid is not None:
                        new_entity_ids.add(int(eid))
                        if int(eid) not in pre_existing_entity_ids:
                            entities_to_add.append(opt)

            if new_entity_ids and new_entity_ids.issubset(pre_existing_entity_ids):
                continue
            final_value = [{"id": eid} for eid in pre_existing_entity_ids] + entities_to_add

        elif len(values_for_field) == 1:
            final_value = all_new_resolved[0] if len(all_new_resolved) == 1 else all_new_resolved

        else:
            final_value = all_new_resolved[-1] if all_new_resolved else all_new_resolved

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
