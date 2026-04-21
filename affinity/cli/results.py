from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from affinity.models.entities import AffinityModel
from affinity.models.rate_limit_snapshot import RateLimitSnapshot


class CommandContext(AffinityModel):
    """Structured command context for reproducibility and debugging.

    Attributes:
        name: Command name (e.g., "person get", "list entry ls")
        inputs: Required positional/named inputs that identify what was queried
        modifiers: Optional flags/options that modify behavior
        resolved: Human-readable names for IDs in inputs (optional)
    """

    name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    modifiers: dict[str, Any] = Field(default_factory=dict)
    resolved: dict[str, str] | None = None

    def format_header(self) -> str | None:
        """Generate human-readable context header for CLI output.

        Returns None for commands that don't need a header (e.g., whoami).
        """
        parts = self.name.split()
        if len(parts) < 2:
            return None

        entity_type = parts[0]  # person, company, list, etc.
        action = parts[1]  # get, ls, create, etc.

        # Special case: no header for simple info commands
        if self.name in ("whoami",):
            return None

        # Map entity types to display names
        entity_names = {
            "person": "Person",
            "company": "Company",
            "opportunity": "Opportunity",
            "list": "List",
            "field": "Field",
            "field-value": "Field Value",
            "field-value-changes": "Field Value Changes",
            "note": "Note",
            "interaction": "Interaction",
            "reminder": "Reminder",
            "relationship-strength": "Relationship Strength",
            "entity-file": "Entity File",
            "webhook": "Webhook",
        }

        display_name = entity_names.get(entity_type, entity_type.title())

        # Handle "list entry" as a special case
        if entity_type == "list" and len(parts) >= 3 and parts[1] == "entry":
            display_name = "List Entry"
            action = parts[2]

        # Entity get commands
        if action == "get":
            return self._format_get_header(display_name)

        # List commands
        if action == "ls":
            return self._format_ls_header(display_name)

        # Create commands
        if action == "create":
            return self._format_create_header(display_name)

        # Update/delete commands
        if action in ("update", "delete"):
            return self._format_mutation_header(display_name, action)

        # Merge commands
        if action == "merge":
            return self._format_merge_header(display_name)

        return None

    def _format_get_header(self, display_name: str) -> str:
        """Format header for get commands."""
        # Check for selector or entity ID
        if "selector" in self.inputs:
            selector = self.inputs["selector"]
            if self.resolved and "selector" in self.resolved:
                return f'{display_name} "{self.resolved["selector"]}" ({selector})'
            return f"{display_name} {selector}"

        # Find the primary ID input
        id_key = self._find_primary_id_key()
        if id_key and id_key in self.inputs:
            entity_id = self.inputs[id_key]
            if self.resolved and id_key in self.resolved:
                return f'{display_name} "{self.resolved[id_key]}" (ID {entity_id})'
            return f"{display_name} ID {entity_id}"

        return display_name

    def _format_ls_header(self, display_name: str) -> str:
        """Format header for ls commands."""
        # Special case: relationship-strength with composite keys
        if self.name == "relationship-strength ls":
            internal = self.inputs.get("internalId")
            external = self.inputs.get("externalId")
            if internal and external:
                return f"Relationship Strength: Person ID {internal} ↔ Person ID {external}"

        # Check for primary scope input (e.g., listId for list entry ls)
        scope_input = None
        for key in ("listId", "entryId"):
            if key in self.inputs:
                scope_input = (key, self.inputs[key])
                break

        if scope_input:
            key, value = scope_input
            entity = key.replace("Id", "").title()
            if self.resolved and key in self.resolved:
                return f'{display_name}s: {entity} "{self.resolved[key]}"'
            return f"{display_name}s: {entity} ID {value}"

        # Check for modifier filters
        filters = self._get_display_filters()
        if not filters:
            return f"{display_name}s"

        # Check for primary entity filter (personId, companyId, etc.)
        entity_filter_keys = ["personId", "companyId", "opportunityId"]
        present_entity_filters = [ef for ef in entity_filter_keys if ef in filters]

        # Single entity filter uses "for Entity ID X" pattern
        if len(present_entity_filters) == 1:
            ef = present_entity_filters[0]
            entity = ef.replace("Id", "").title()
            remaining = {k: v for k, v in filters.items() if k != ef}
            base = f"{display_name}s for {entity} ID {self.modifiers[ef]}"
            if remaining:
                extra = self._format_filter_suffix(remaining)
                return f"{base} ({extra})"
            return base

        # Multiple filters: use parenthetical format with shortened keys
        filter_str = self._format_filter_suffix(filters)
        return f"{display_name}s ({filter_str})"

    def _format_create_header(self, display_name: str) -> str:
        """Format header for create commands."""
        # Check for scope input
        if "listId" in self.inputs:
            list_id = self.inputs["listId"]
            if self.resolved and "listId" in self.resolved:
                return f'{display_name} Create: List "{self.resolved["listId"]}"'
            return f"{display_name} Create: List ID {list_id}"

        if "type" in self.inputs:
            return f"{display_name} Create (type: {self.inputs['type']})"

        return f"{display_name} Create"

    def _format_mutation_header(self, display_name: str, action: str) -> str:
        """Format header for update/delete commands."""
        id_key = self._find_primary_id_key()
        if id_key and id_key in self.inputs:
            entity_id = self.inputs[id_key]
            if self.resolved and id_key in self.resolved:
                resolved_name = self.resolved[id_key]
                return f'{display_name} {action.title()}: "{resolved_name}" (ID {entity_id})'
            return f"{display_name} {action.title()}: ID {entity_id}"
        return f"{display_name} {action.title()}"

    def _format_merge_header(self, display_name: str) -> str:
        """Format header for merge commands."""
        primary = self.inputs.get("primaryId")
        duplicate = self.inputs.get("duplicateId")
        if primary and duplicate:
            return f"{display_name} Merge: ID {primary} ← ID {duplicate}"
        return f"{display_name} Merge"

    def _find_primary_id_key(self) -> str | None:
        """Find the primary ID key in inputs."""
        id_keys = [
            "personId",
            "companyId",
            "opportunityId",
            "listId",
            "entryId",
            "fieldId",
            "fieldValueId",
            "noteId",
            "interactionId",
            "reminderId",
            "entityFileId",
            "webhookId",
        ]
        for key in id_keys:
            if key in self.inputs:
                return key
        return None

    def _get_display_filters(self) -> dict[str, Any]:
        """Get modifiers that should be displayed in headers (exclude pagination)."""
        exclude = {"pageSize", "cursor", "maxResults", "allPages"}
        return {k: v for k, v in self.modifiers.items() if k not in exclude and v is not None}

    def _shorten_key(self, key: str) -> str:
        """Shorten key names for display (personId → person)."""
        if key.endswith("Id") and len(key) > 2:
            return key[:-2]
        return key

    def _format_filter_suffix(self, filters: dict[str, Any], max_display: int = 2) -> str:
        """Format filter dict as suffix string, truncating if needed."""
        items = [(self._shorten_key(k), v) for k, v in filters.items()]
        if len(items) <= max_display:
            return ", ".join(f"{k}: {v}" for k, v in items)

        displayed = items[:max_display]
        remaining = len(items) - max_display
        parts = [f"{k}: {v}" for k, v in displayed]
        parts.append(f"+{remaining} more")
        return ", ".join(parts)


class DateRange(AffinityModel):
    """Date range for time-bounded queries.

    Display format: YYYY-MM-DD → YYYY-MM-DD (compact, for footer)
    JSON format: Full ISO-8601 with timezone (via Pydantic serialization)
    """

    start: datetime
    end: datetime

    def format_display(self) -> str:
        """Format as 'YYYY-MM-DD → YYYY-MM-DD' for human display."""
        return f"{self.start.strftime('%Y-%m-%d')} → {self.end.strftime('%Y-%m-%d')}"


class ResultSummary(AffinityModel):
    """Metadata about query results - rendered as footer in table mode.

    This is the standardized way to communicate summary information
    about results. All commands should use this instead of ad-hoc
    metadata dictionaries.

    Attributes:
        total_rows: Total number of rows in the result
        date_range: For time-bounded queries (e.g., interaction ls)
        type_breakdown: Count per type (e.g., {"email": 120, "call": 30})
        included_counts: For query --include (e.g., {"companies": 10})
        chunks_processed: For chunked fetches (interaction ls)
        scanned_rows: For filtered queries (rows examined before filter)
        custom_text: Escape hatch for one-off messages
    """

    total_rows: int | None = Field(None, alias="totalRows")
    date_range: DateRange | None = Field(None, alias="dateRange")
    type_breakdown: dict[str, int] | None = Field(None, alias="typeBreakdown")
    included_counts: dict[str, int] | None = Field(None, alias="includedCounts")
    chunks_processed: int | None = Field(None, alias="chunksProcessed")
    scanned_rows: int | None = Field(None, alias="scannedRows")
    custom_text: str | None = Field(None, alias="customText")


class Artifact(AffinityModel):
    type: str
    path: str
    path_is_relative: bool = Field(..., alias="pathIsRelative")
    rows_written: int | None = Field(None, alias="rowsWritten")
    bytes_written: int | None = Field(None, alias="bytesWritten")
    partial: bool = False


class ErrorInfo(AffinityModel):
    type: str
    message: str
    hint: str | None = None
    docs_url: str | None = Field(None, alias="docsUrl")
    details: dict[str, Any] | None = None


class CommandMeta(AffinityModel):
    duration_ms: int = Field(..., alias="durationMs")
    profile: str | None = None
    resolved: dict[str, Any] | None = None
    pagination: dict[str, Any] | None = None
    columns: list[dict[str, Any]] | None = None
    rate_limit: RateLimitSnapshot | None = Field(None, alias="rateLimit")
    summary: ResultSummary | None = None
    truncated: bool | None = None
    truncation_reason: str | None = Field(None, alias="truncationReason")


class CommandResult(AffinityModel):
    ok: bool
    command: CommandContext
    data: Any | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    meta: CommandMeta
    error: ErrorInfo | None = None
