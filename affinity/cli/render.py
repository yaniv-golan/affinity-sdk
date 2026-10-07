from __future__ import annotations

import html
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from affinity.models.rate_limit_snapshot import RateLimitSnapshot

from .results import CommandResult, ResultSummary

# Columns likely to contain long content - used for column priority (drop first when limiting)
_LONG_COLUMNS = frozenset(
    {
        "dropdownoptions",
        "dropdown_options",
        "domains",
        "emails",
        "description",
        "notes",
        "content",
        "fields",
        "body",
        "subject",
        "snippet",
    }
)

# Essential columns that should always be kept when limiting columns
_ESSENTIAL_COLUMNS = frozenset({"id", "listentryid"})

# Minimum readable column width for calculating max columns
_MIN_COL_WIDTH = 8


def get_terminal_width() -> int:
    """Get terminal width, with fallbacks."""
    try:
        return shutil.get_terminal_size().columns
    except (ValueError, OSError):
        return 80  # Standard fallback


def get_max_columns(terminal_width: int | None = None) -> int:
    """Calculate max columns based on terminal width."""
    width = terminal_width or get_terminal_width()
    # Account for borders (~3 chars per column: | + padding)
    usable = width - 2  # outer borders
    return max(4, usable // (_MIN_COL_WIDTH + 3))  # ~12 for 80 chars, ~20 for 160 chars


def limit_columns(
    columns: list[str],
    max_cols: int | None = None,
    essential: frozenset[str] | None = None,
    drop_first: frozenset[str] | None = None,
) -> tuple[list[str], int]:
    """Limit columns to fit terminal, return (limited_cols, omitted_count).

    Args:
        columns: All columns to consider
        max_cols: Maximum columns to show (default: auto from terminal width)
        essential: Columns to always keep (default: _ESSENTIAL_COLUMNS)
        drop_first: Columns to drop first when over limit (default: _LONG_COLUMNS)

    Returns:
        (selected_columns, omitted_count) - preserves original column order
    """
    max_cols = max_cols if max_cols is not None else get_max_columns()
    essential = essential if essential is not None else _ESSENTIAL_COLUMNS
    drop_first = drop_first if drop_first is not None else _LONG_COLUMNS

    if len(columns) <= max_cols:
        return columns, 0

    # Use LISTS (not sets) to preserve deterministic order
    # Sets have undefined iteration order - would cause non-deterministic column selection
    essential_cols = [c for c in columns if c.lower() in essential]
    droppable_cols = [c for c in columns if c.lower() in drop_first and c.lower() not in essential]
    regular_cols = [c for c in columns if c not in essential_cols and c not in droppable_cols]

    # Calculate how many we can keep beyond essential
    # Handle edge case where essential columns alone exceed max_cols
    available = max(0, max_cols - len(essential_cols))
    keep_regular = regular_cols[:available]
    remaining = available - len(keep_regular)
    keep_droppable = droppable_cols[:remaining] if remaining > 0 else []

    # Build kept set for O(1) lookup
    kept = set(essential_cols + keep_regular + keep_droppable)

    # Return in ORIGINAL ORDER (critical!)
    selected = [c for c in columns if c in kept]
    omitted = len(columns) - len(selected)
    return selected, omitted


def format_duration(seconds: float) -> str:
    """Format duration as '2:15' or '0:45' or '3:02:05'."""
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


@dataclass(frozen=True, slots=True)
class RenderSettings:
    output: str  # "table" | "json"
    quiet: bool
    verbosity: int
    pager: bool | None  # None=auto
    all_columns: bool = False  # If True, show all columns regardless of terminal width
    max_columns: int | None = None  # Override auto-calculated max columns


def _error_title(error_type: str) -> str:
    normalized = (error_type or "").strip()
    mapping = {
        "usage_error": "Usage error",
        "ambiguous_resolution": "Ambiguous",
        "not_found": "Not found",
        "validation_error": "Validation error",
        "file_exists": "File exists",
        "permission_denied": "Permission denied",
        "disk_full": "Disk full",
        "io_error": "I/O error",
        "config_error": "Configuration error",
        "auth_error": "Authentication error",
        "forbidden": "Permission denied",
        "rate_limited": "Rate limited",
        "server_error": "Server error",
        "network_error": "Network error",
        "timeout": "Timeout",
        "write_not_allowed": "Write blocked",
        "api_error": "API error",
        "internal_error": "Internal error",
        "AuthenticationError": "Authentication error",
        "AuthorizationError": "Permission denied",
        "NotFoundError": "Not found",
        "RateLimitError": "Rate limited",
        "ServerError": "Server error",
    }
    return mapping.get(normalized, "Error")


def _render_error_details(
    *,
    stderr: Console,
    command: str,
    error_type: str,
    message: str,
    hint: str | None,
    docs_url: str | None,
    details: dict[str, Any] | None,
    settings: RenderSettings,
) -> None:
    if settings.quiet:
        return

    if hint:
        stderr.print(f"Hint: {hint}")
    if docs_url:
        stderr.print(f"Docs: {docs_url}")

    if not details:
        if error_type == "ambiguous_resolution":
            if not hint:
                stderr.print(f"Hint: run `affinity {command} --help`")
        elif error_type == "usage_error":
            # Avoid noisy hints for credential/config errors where --help doesn't help.
            lowered = message.lower()
            if "api key" not in lowered and "python-dotenv" not in lowered and not hint:
                stderr.print(f"Hint: run `affinity {command} --help`")
        return

    if error_type == "ambiguous_resolution":
        matches = details.get("matches")
        if isinstance(matches, list) and matches and all(isinstance(m, dict) for m in matches):
            matches = cast(list[dict[str, Any]], matches)
            preferred = ["listId", "savedViewId", "fieldId", "id", "name", "type", "isDefault"]
            columns: list[str] = []
            for key in preferred:
                if any(key in m for m in matches):
                    columns.append(key)
            if not columns:
                columns = list(matches[0].keys())[:4]

            table = Table(show_header=True, header_style="bold")
            for col in columns:
                table.add_column(col)
            for m in matches[:20]:
                table.add_row(*[str(m.get(col, "")) for col in columns])
            stderr.print(table)
        elif "fieldIds" in details and isinstance(details.get("fieldIds"), list):
            field_ids = details.get("fieldIds")
            stderr.print("Matches: " + ", ".join(str(x) for x in cast(list[Any], field_ids)[:20]))

        if not hint:
            stderr.print(f"Hint: run `affinity {command} --help`")
        return

    if error_type == "usage_error":
        if not hint:
            stderr.print(f"Hint: run `affinity {command} --help`")
        if settings.verbosity >= 1:
            stderr.print(Panel.fit(Text(json.dumps(details, ensure_ascii=False, indent=2))))
        return

    if settings.verbosity >= 2:
        stderr.print(Panel.fit(Text(json.dumps(details, ensure_ascii=False, indent=2))))


def _rate_limit_footer(snapshot: RateLimitSnapshot) -> str:
    def _fmt_int(value: int | None) -> str | None:
        if value is None:
            return None
        return f"{value:,}"

    def _fmt_reset_seconds(seconds: int | None) -> str | None:
        if seconds is None:
            return None
        total_seconds = max(0, int(seconds))
        days = total_seconds // 86400
        if days:
            return f"{days:,}d"
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours}:{minutes:02}:{secs:02}"

    parts: list[str] = []
    user = snapshot.api_key_per_minute
    if user.limit is not None or user.remaining is not None:
        user_bits: list[str] = []
        user_remaining = _fmt_int(user.remaining)
        user_limit = _fmt_int(user.limit)
        if user_remaining is not None and user_limit is not None:
            user_bits.append(f"user {user_remaining}/{user_limit}")
        elif user_remaining is not None:
            user_bits.append(f"user remaining {user_remaining}")
        user_reset = _fmt_reset_seconds(user.reset_seconds)
        if user_reset is not None:
            user_bits.append(f"reset {user_reset}")
        parts.append(" ".join(user_bits))

    org = snapshot.org_monthly
    if org.limit is not None or org.remaining is not None:
        org_bits: list[str] = []
        org_remaining = _fmt_int(org.remaining)
        org_limit = _fmt_int(org.limit)
        if org_remaining is not None and org_limit is not None:
            org_bits.append(f"org {org_remaining}/{org_limit}")
        elif org_remaining is not None:
            org_bits.append(f"org remaining {org_remaining}")
        org_reset = _fmt_reset_seconds(org.reset_seconds)
        if org_reset is not None:
            org_bits.append(f"reset {org_reset}")
        parts.append(" ".join(org_bits))

    if not parts:
        return ""
    return "# Rate limit: " + " | ".join(parts)


def _build_row_segment(summary: ResultSummary, verbosity: int) -> str | None:
    """Build the row count segment, optionally with type breakdown.

    Returns strings like:
        "150 rows"
        "150 rows: 30 call, 120 email"
        "35 rows from 9,340 scanned"

    Note: type_breakdown and scanned_rows are mutually exclusive in display
    (scanned_rows implies a filter was applied, type_breakdown implies multi-type query)
    """
    if summary.total_rows is None:
        return None

    row_str = f"{summary.total_rows:,} row{'s' if summary.total_rows != 1 else ''}"

    # Type breakdown OR scanned context (not both - would read awkwardly)
    if summary.type_breakdown:
        show_breakdown = len(summary.type_breakdown) > 1 or verbosity >= 1
        if show_breakdown:
            # Sort keys for consistent output
            type_parts = [
                f"{count:,} {type_}" for type_, count in sorted(summary.type_breakdown.items())
            ]
            row_str = f"{row_str}: {', '.join(type_parts)}"
    elif summary.scanned_rows and summary.scanned_rows > summary.total_rows:
        row_str = f"{row_str} from {summary.scanned_rows:,} scanned"

    return row_str


def _render_summary_footer(summary: ResultSummary | None, verbosity: int = 0) -> Text | None:
    """Render ResultSummary as compact footer text.

    Examples:
        (0 rows)
        (150 rows)
        (150 rows: 30 call, 120 email)
        (150 rows: 30 call, 120 email | 2023-07-26 → 2026-01-11)
        (35 rows from 9,340 scanned)
        (50 rows | included: 5 companies, 10 opportunities)
    """
    if not summary:
        return None

    segments: list[str] = []  # Joined with " | "

    # Build row count segment (may include type breakdown)
    row_segment = _build_row_segment(summary, verbosity)
    if row_segment:
        segments.append(row_segment)

    # Date range segment
    if summary.date_range:
        segments.append(summary.date_range.format_display())

    # Included counts segment (for query command)
    if summary.included_counts:
        # Sort keys for consistent output
        inc_parts = [
            f"{count:,} {entity}" for entity, count in sorted(summary.included_counts.items())
        ]
        segments.append(f"included: {', '.join(inc_parts)}")

    # Chunks processed (verbose only)
    if verbosity >= 1 and summary.chunks_processed:
        segments.append(f"{summary.chunks_processed} chunks")

    # Custom text (escape hatch for one-off messages)
    if summary.custom_text:
        segments.append(summary.custom_text)

    if not segments:
        return None

    text = " | ".join(segments)
    return Text(f"({text})", style="dim")


def _table_from_rows(
    rows: list[dict[str, Any]],
    *,
    max_columns: int | None = None,
    all_columns: bool = False,
) -> tuple[Table, int]:
    """Build a Rich table from rows.

    Args:
        rows: List of row dicts to render
        max_columns: Maximum columns to show (None = auto from terminal width)
        all_columns: If True, show all columns regardless of limit

    Returns:
        (table, omitted_count) - the table and number of columns omitted
    """
    table = Table(show_header=True, header_style="bold", expand=True)
    if not rows:
        table.add_column("result")
        table.add_row("No results")
        return table, 0

    raw_columns = list(rows[0].keys())
    # Partition into regular and long columns, preserving relative order
    # Long columns (defined in module-level _LONG_COLUMNS) go to end
    regular_cols = [c for c in raw_columns if c.lower() not in _LONG_COLUMNS]
    long_cols = [c for c in raw_columns if c.lower() in _LONG_COLUMNS]
    reordered_columns = regular_cols + long_cols

    # Apply column limiting unless all_columns is True
    if all_columns:
        columns = reordered_columns
        omitted = 0
    else:
        columns, omitted = limit_columns(reordered_columns, max_cols=max_columns)

    def maybe_urlify_domain(value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if "://" in value:
            return value
        return f"https://{value}"

    def localize_datetime(value: datetime) -> tuple[datetime, int | None]:
        # dt is guaranteed UTC-aware from ISODatetime validator
        local = value.astimezone()
        offset = local.utcoffset()
        if offset is None:
            return local, None
        return local, int(offset.total_seconds() // 60)

    def format_utc_offset(minutes: int) -> str:
        sign = "+" if minutes >= 0 else "-"
        minutes_abs = abs(minutes)
        hours = minutes_abs // 60
        mins = minutes_abs % 60
        if mins == 0:
            return f"UTC{sign}{hours}"
        return f"UTC{sign}{hours}:{mins:02d}"

    def format_local_datetime(value: datetime, *, show_seconds: bool) -> tuple[str, int | None]:
        local, offset_minutes = localize_datetime(value)
        base = (
            local.strftime("%Y-%m-%d %H:%M:%S")
            if show_seconds
            else local.strftime("%Y-%m-%d %H:%M")
        )
        return base, offset_minutes

    datetime_columns: set[str] = set()
    datetime_offsets: dict[str, set[int]] = {}
    datetime_show_seconds: dict[str, bool] = {}
    for col in columns:
        offsets: set[int] = set()
        any_dt = False
        show_seconds = False
        for row in rows:
            value = row.get(col)
            if isinstance(value, datetime):
                any_dt = True
                if value.second or value.microsecond:
                    show_seconds = True
                _local, offset_minutes = localize_datetime(value)
                if offset_minutes is not None:
                    offsets.add(offset_minutes)
        if any_dt:
            datetime_columns.add(col)
            datetime_offsets[col] = offsets
            datetime_show_seconds[col] = show_seconds

    for col in columns:
        is_long = col.lower() in _LONG_COLUMNS
        # Give long columns more space (ratio=3 means 3x the space of ratio=1)
        col_ratio = 3 if is_long else 1
        if col in datetime_columns:
            offsets = datetime_offsets.get(col, set())
            if len(offsets) == 1:
                offset_str = format_utc_offset(next(iter(offsets)))
                table.add_column(f"{col} (local, {offset_str})", ratio=col_ratio)
            else:
                table.add_column(f"{col} (local)", ratio=col_ratio)
        else:
            table.add_column(col, ratio=col_ratio)

    def format_cell(*, row: dict[str, Any], column: str, value: Any) -> str:
        if value is None:
            return ""
        column_lower = column.lower()

        def is_id_column(name: str) -> bool:
            lowered = name.lower()
            return lowered == "id" or lowered.endswith("id")

        def format_number(value: int | float, *, allow_commas: bool) -> str:
            if isinstance(value, bool):
                return str(value)
            if isinstance(value, int):
                return f"{value:,}" if allow_commas else str(value)
            if value.is_integer():
                as_int = int(value)
                return f"{as_int:,}" if allow_commas else str(as_int)
            # Keep fractional values readable (e.g. percents) while still comma-grouping.
            return f"{value:,.10f}".rstrip("0").rstrip(".")

        def infer_currency_code(row: dict[str, Any]) -> str | None:
            for key in ("currency", "currencyCode", "currency_code"):
                v = row.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip().upper()
            name = row.get("name")
            if isinstance(name, str) and name.strip():
                upper = name.upper()
                for code in ("USD", "EUR", "GBP", "CAD", "AUD", "ILS"):
                    if f"({code})" in upper or upper.endswith(f" {code}"):
                        return code
            return None

        def should_render_money(row: dict[str, Any]) -> bool:
            name = row.get("name")
            if isinstance(name, str):
                lowered = name.lower()
                keys = (" amount", "amount ", "funding", "revenue", "price")
                return any(k in lowered for k in keys)
            return "amount" in column_lower

        def should_render_year(row: dict[str, Any]) -> bool:
            name = row.get("name")
            if isinstance(name, str) and name.strip():
                lowered = name.lower()
                return "year" in lowered or "founded" in lowered
            return "year" in column_lower

        def maybe_format_year(value: int | float, *, row: dict[str, Any]) -> str | None:
            if not should_render_year(row):
                return None
            if isinstance(value, int):
                return str(value) if 1000 <= value <= 2999 else None
            if value.is_integer():
                as_int = int(value)
                return str(as_int) if 1000 <= as_int <= 2999 else None
            return None

        def format_money(value: int | float, *, code: str) -> str:
            amount = format_number(value, allow_commas=True)
            symbols = {"USD": "$", "EUR": "€", "GBP": "£", "ILS": "₪"}
            symbol = symbols.get(code)
            if symbol:
                return f"{symbol}{amount}"
            return f"{code} {amount}"

        def truncate(text: str, *, max_len: int = 240) -> str:
            text = " ".join(text.split())
            if len(text) <= max_len:
                return text
            return text[: max_len - 1].rstrip() + "…"

        def format_iso_datetime(value: Any) -> str | None:
            if not isinstance(value, str):
                return None
            text = value.strip()
            if not text:
                return None
            # Best-effort: keep this purely presentational.
            if "T" in text:
                text = text.replace("T", " ").replace("Z", "")
            # Trim fractional seconds if present.
            if "." in text:
                head, _dot, tail = text.partition(".")
                if any(c.isdigit() for c in tail):
                    text = head
            return truncate(text, max_len=32)

        def format_location(data: Any) -> str | None:
            if not isinstance(data, dict):
                return None
            parts: list[str] = []
            for key in ("streetAddress", "city", "state", "country", "continent"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            return ", ".join(parts) if parts else None

        def format_typed_value(obj: dict[str, Any], *, row: dict[str, Any]) -> str | None:
            if set(obj.keys()) != {"type", "data"} and not (
                "type" in obj and "data" in obj and len(obj) <= 4
            ):
                return None
            data = obj.get("data")
            t = obj.get("type")

            if data is None:
                return ""
            if isinstance(data, float) and data.is_integer():
                data = int(data)
            if isinstance(data, int):
                year = maybe_format_year(data, row=row)
                if year is not None:
                    return year
                code = infer_currency_code(row) if column_lower == "value" else None
                if code is not None and should_render_money(row):
                    return format_money(data, code=code)
                return format_number(data, allow_commas=not is_id_column(column))
            if isinstance(data, float):
                year = maybe_format_year(data, row=row)
                if year is not None:
                    return year
                code = infer_currency_code(row) if column_lower == "value" else None
                if code is not None and should_render_money(row):
                    return format_money(data, code=code)
                return format_number(data, allow_commas=not is_id_column(column))
            if isinstance(data, str):
                return truncate(data)
            if isinstance(data, list) and all(isinstance(x, dict) for x in data):
                texts: list[str] = []
                for item in cast(list[dict[str, Any]], data):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
                        continue
                    name = item.get("name")
                    if isinstance(name, str) and name.strip():
                        texts.append(name.strip())
                        continue
                    first = item.get("firstName")
                    last = item.get("lastName")
                    if isinstance(first, str) or isinstance(last, str):
                        display = " ".join(
                            p.strip() for p in [first, last] if isinstance(p, str) and p.strip()
                        ).strip()
                        if display:
                            texts.append(display)
                            continue
                if texts:
                    return truncate(", ".join(texts))
                return f"list ({len(data):,} items)"
            if isinstance(data, list) and all(isinstance(x, str) for x in data):
                return truncate(", ".join(x.strip() for x in data if x.strip()))
            # Handle lists of integers (e.g., personIds, companyIds)
            if isinstance(data, list) and all(isinstance(x, int) for x in data):
                return truncate(", ".join(str(x) for x in data))
            if isinstance(data, dict):
                text = data.get("text")
                if isinstance(text, str) and text.strip():
                    return truncate(text)
                name = data.get("name")
                if isinstance(name, str) and name.strip():
                    return truncate(name)
            if t == "formula-number" and isinstance(data, dict):
                calculated = data.get("calculatedValue")
                if calculated is None:
                    return ""
                if isinstance(calculated, float) and calculated.is_integer():
                    calculated = int(calculated)
                if isinstance(calculated, (int, float)) and not isinstance(calculated, bool):
                    return format_number(calculated, allow_commas=True)
                return truncate(str(calculated))
            if t == "note" and isinstance(data, dict):
                content = data.get("content")
                html_text = content.get("html") if isinstance(content, dict) else None
                note_parts: list[str] = []
                if isinstance(html_text, str):
                    plain = html.unescape(re.sub(r"<[^>]+>", "", html_text))
                    plain = " ".join(plain.split())
                    if plain:
                        note_parts.append(plain)
                note_when = format_iso_datetime(data.get("createdAt"))
                if note_when:
                    note_parts.append(f"[{note_when}]")
                if data.get("id") is not None:
                    note_parts.append(f"(id={data.get('id')})")
                if note_parts:
                    return truncate(" ".join(note_parts), max_len=140)
                return None
            if t == "reminder" and isinstance(data, dict):
                reminder_parts: list[str] = []
                reminder_content = data.get("content")
                if isinstance(reminder_content, str) and reminder_content.strip():
                    reminder_parts.append(reminder_content.strip())
                due = format_iso_datetime(data.get("dueDate"))
                if due:
                    reminder_parts.append(f"(due {due})")
                if data.get("id") is not None:
                    reminder_parts.append(f"(id={data.get('id')})")
                if reminder_parts:
                    return truncate(" ".join(reminder_parts), max_len=140)
                return None
            if isinstance(t, str) and t == "person" and isinstance(data, dict):
                first = data.get("firstName")
                last = data.get("lastName")
                email = data.get("primaryEmailAddress")
                person_id = data.get("id")
                name = " ".join(
                    p.strip() for p in [first, last] if isinstance(p, str) and p.strip()
                ).strip()
                bits: list[str] = []
                if name:
                    bits.append(name)
                if isinstance(email, str) and email.strip():
                    bits.append(f"<{email.strip()}>")
                if person_id is not None:
                    bits.append(f"(id={person_id})")
                if bits:
                    return truncate(" ".join(bits), max_len=120)
                return None
            if isinstance(t, str) and t == "interaction" and isinstance(data, dict):
                subtype = data.get("type")
                interaction_id = data.get("id")
                when = format_iso_datetime(data.get("sentAt")) or format_iso_datetime(
                    data.get("startTime")
                )
                title = None
                subject = data.get("subject")
                event_title = data.get("title")
                if isinstance(subject, str) and subject.strip():
                    title = subject
                elif isinstance(event_title, str) and event_title.strip():
                    title = event_title
                parts: list[str] = []
                if isinstance(subtype, str) and subtype.strip():
                    parts.append(subtype.strip())
                if when:
                    parts.append(when)
                if title:
                    parts.append("— " + title)
                if interaction_id is not None:
                    parts.append(f"(id={interaction_id})")
                if parts:
                    return truncate(" ".join(parts), max_len=140)
                return None
            if isinstance(t, str) and t == "location":
                loc = format_location(data)
                if loc is not None:
                    return truncate(loc)
            if isinstance(data, dict) and set(data.keys()) >= {"id", "text"}:
                text = data.get("text")
                ident = data.get("id")
                if isinstance(text, str) and text.strip():
                    if ident is None:
                        return truncate(text)
                    return truncate(f"{text} (id={ident})")
            return None

        def format_dict(obj: dict[str, Any], *, row: dict[str, Any]) -> str:
            typed = format_typed_value(obj, row=row)
            if typed is not None:
                return typed

            # Person entity (has firstName/lastName, or type is person/external/internal)
            obj_type = obj.get("type")
            is_person = ("firstName" in obj or "lastName" in obj) or obj_type in (
                "person",
                "external",
                "internal",
            )
            if is_person:
                first = obj.get("firstName")
                last = obj.get("lastName")
                person_id = obj.get("id")
                name = " ".join(
                    p.strip() for p in [first, last] if isinstance(p, str) and p.strip()
                ).strip()
                if name and person_id is not None:
                    return truncate(f"{name} (id={person_id})")
                elif name:
                    return truncate(name)
                elif person_id is not None:
                    return f"person (id={person_id})"

            # Common compact shape: {"id": ..., "text": ...}
            if set(obj.keys()) >= {"id", "text"}:
                text = obj.get("text")
                ident = obj.get("id")
                if isinstance(text, str) and text.strip():
                    return truncate(f"{text} (id={ident})" if ident is not None else text)

            # Location-like shape without wrapper.
            loc = format_location(obj)
            if loc is not None:
                return truncate(loc)

            if all(isinstance(v, (str, int, float, bool)) or v is None for v in obj.values()):
                parts: list[str] = []
                for k, v in obj.items():
                    if v is None:
                        continue
                    if isinstance(v, float) and v.is_integer():
                        v = int(v)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        parts.append(f"{k}={format_number(v, allow_commas=True)}")
                    else:
                        parts.append(f"{k}={v}")
                if parts:
                    return truncate(", ".join(parts))
            return f"object ({len(obj):,} keys)"

        if isinstance(value, datetime):
            show_seconds = datetime_show_seconds.get(column, False)
            base, _offset_minutes = format_local_datetime(value, show_seconds=show_seconds)
            return base
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            year = maybe_format_year(value, row=row)
            if year is not None:
                return year
            return format_number(value, allow_commas=not is_id_column(column))
        if isinstance(value, list):
            if not value:
                return ""
            if all(isinstance(v, str) for v in value):
                parts = [
                    maybe_urlify_domain(v) if column_lower in {"domain", "domains"} else v
                    for v in value
                ]
                return ", ".join(parts)

            if all(isinstance(v, dict) for v in value):
                dict_items = cast(list[dict[str, Any]], value)

                def summarize_field_items(items: list[dict[str, Any]]) -> str:
                    parts: list[str] = []
                    for item in items:
                        name = item.get("name") or item.get("id") or "field"
                        if not isinstance(name, str) or not name.strip():
                            name = "field"
                        raw_value = item.get("value")
                        value_text: str | None = None
                        if isinstance(raw_value, dict):
                            value_text = format_dict(raw_value, row=item)
                        elif raw_value is None:
                            value_text = None
                        elif isinstance(raw_value, (int, float)) and not isinstance(
                            raw_value, bool
                        ):
                            value_text = format_number(raw_value, allow_commas=True)
                        else:
                            value_text = str(raw_value)

                        if value_text is None or not value_text.strip():
                            continue
                        parts.append(f"{name}={value_text}")
                        if len(parts) >= 3:
                            break

                    if not parts:
                        return f"fields ({len(items):,} items)"

                    remaining = len(items) - len(parts)
                    suffix = f" … (+{remaining} more)" if remaining > 0 else ""
                    return truncate("; ".join(parts) + suffix, max_len=180)

                if column_lower == "fields":
                    return summarize_field_items(dict_items)

                # Try to extract text/name values from list of dicts (e.g. dropdownOptions)
                # Color dots for dropdown options (maps to DropdownOptionColor enum)
                _COLOR_DOTS = {
                    0: "⚪",  # DEFAULT
                    1: "🔵",  # BLUE
                    2: "🟢",  # GREEN
                    3: "🟡",  # YELLOW
                    4: "🟠",  # ORANGE
                    5: "🔴",  # RED
                    6: "🟣",  # PURPLE
                    7: "⚫",  # GRAY
                }
                texts: list[str] = []
                for item in dict_items:
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        color = item.get("color")
                        dot = _COLOR_DOTS.get(color, "") if isinstance(color, int) else ""
                        texts.append(f"{dot}{text.strip()}" if dot else text.strip())
                        continue
                    name = item.get("name")
                    if isinstance(name, str) and name.strip():
                        texts.append(name.strip())
                        continue
                    first = item.get("firstName")
                    last = item.get("lastName")
                    if isinstance(first, str) or isinstance(last, str):
                        display = " ".join(
                            p.strip() for p in [first, last] if isinstance(p, str) and p.strip()
                        ).strip()
                        if display:
                            texts.append(display)
                            continue
                if texts:
                    return truncate(", ".join(texts))

                return f"list ({len(dict_items):,} items)"

            # Handle lists of simple scalars (integers, strings)
            if all(isinstance(x, int) for x in value):
                return truncate(", ".join(str(x) for x in value))
            if all(isinstance(x, str) for x in value):
                return truncate(", ".join(x.strip() for x in value if x.strip()))

            return f"list ({len(value):,} items)"
        if isinstance(value, dict):
            return format_dict(value, row=row)
        if isinstance(value, str) and column_lower in {"domain", "domains"}:
            return maybe_urlify_domain(value)
        return str(value)

    for row in rows:
        table.add_row(*[format_cell(row=row, column=c, value=row.get(c, "")) for c in columns])
    return table, omitted


_CAMEL_BREAK_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _humanize_title(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if any(ch.isspace() for ch in raw):
        raw = raw.replace("_", " ").replace("-", " ").strip()
        return " ".join(raw.split())
    raw = raw.replace("_", " ").replace("-", " ").strip()
    raw = _CAMEL_BREAK_RE.sub(" ", raw)
    raw = " ".join(raw.split())
    return raw[:1].upper() + raw[1:]


def _is_collection_envelope(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if "data" not in obj or "pagination" not in obj:
        return False
    data = obj.get("data")
    pagination = obj.get("pagination")
    if not isinstance(data, list):
        return False
    if not isinstance(pagination, dict):
        return False
    # Collection envelopes include pagination URLs.
    return "nextUrl" in pagination or "prevUrl" in pagination


def _is_collection_with_hint(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if set(obj.keys()) != {"_rows", "_hint"}:
        return False
    if not isinstance(obj.get("_rows"), list):
        return False
    if not isinstance(obj.get("_hint"), str):
        return False
    return bool(obj.get("_hint"))


def _is_text_marker(obj: Any) -> bool:
    """
    Allow commands to embed a simple human-only text section in dict-shaped data.

    This is intentionally an internal convention (not part of the JSON contract).
    """

    return (
        isinstance(obj, dict)
        and set(obj.keys()) == {"_text"}
        and isinstance(obj.get("_text"), str)
        and bool(obj.get("_text"))
    )


def _pagination_has_more(pagination: dict[str, Any] | None) -> bool:
    if not pagination:
        return False
    for key in ("nextCursor", "nextUrl", "nextPageToken"):
        value = pagination.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _format_scalar_value(*, key: str | None, value: Any) -> str:
    if value is None:
        return ""
    key_lower = (key or "").lower()
    if isinstance(value, datetime):
        # value is guaranteed UTC-aware from ISODatetime validator
        local = value.astimezone()
        show_seconds = bool(local.second or local.microsecond)
        return local.strftime("%Y-%m-%d %H:%M:%S" if show_seconds else "%Y-%m-%d %H:%M")
    if isinstance(value, list):
        if all(isinstance(v, str) for v in value):
            parts = [str(v) for v in value]
            if key_lower in {"domain", "domains"}:
                parts = [v if "://" in v else f"https://{v.strip()}" for v in parts if v.strip()]
            return ", ".join(parts)
        return f"list ({len(value):,} items)"
    if isinstance(value, dict):
        # Special handling for date range objects (e.g., metadata.dateRange)
        if set(value.keys()) == {"start", "end"}:
            start_str = str(value.get("start", ""))
            end_str = str(value.get("end", ""))
            # Extract just the date portion if it's an ISO datetime
            if "T" in start_str:
                start_str = start_str.split("T", maxsplit=1)[0]
            if "T" in end_str:
                end_str = end_str.split("T", maxsplit=1)[0]
            return f"{start_str} → {end_str}"
        return f"object ({len(value):,} keys)"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int) and key_lower != "id" and not key_lower.endswith("id"):
        return f"{value:,}"
    if isinstance(value, float) and key_lower != "id" and not key_lower.endswith("id"):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.10f}".rstrip("0").rstrip(".")
    if isinstance(value, str) and key_lower in {"domain", "domains"}:
        text = value.strip()
        if not text:
            return ""
        return text if "://" in text else f"https://{text}"
    return str(value)


def _is_simple_scalar_dict(obj: dict[str, Any]) -> bool:
    """Check if a dict contains only simple scalar values (no nested structures).

    Date range dicts (with only 'start' and 'end' keys) are treated as simple
    since they format to inline strings like "2024-01-01 → 2024-06-01".
    """
    for v in obj.values():
        if isinstance(v, list):
            return False
        # Allow date range dicts (they format inline)
        if isinstance(v, dict) and set(v.keys()) != {"start", "end"}:
            return False
    return True


def _simple_kv_text(obj: dict[str, Any]) -> Text:
    """Render a simple scalar dict as plain text lines (not a table)."""
    lines: list[str] = []
    for k, v in obj.items():
        formatted = _format_scalar_value(key=str(k), value=v)
        lines.append(f"{_humanize_title(k)}: {formatted}")
    return Text("\n".join(lines))


def _kv_table(obj: dict[str, Any]) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("field")
    table.add_column("value")
    for k, v in obj.items():
        table.add_row(str(k), _format_scalar_value(key=str(k), value=v))
    return table


def _render_fields_section(
    *,
    title: str,
    fields: list[dict[str, Any]],
    field_metadata: dict[str, str] | None,
    verbose: bool = False,
) -> Any:
    """Render field values as a human-readable table with field names.

    Groups multi-value fields (same field_id with multiple values) and shows
    field name only on the first row. Shows Field Name, Field ID, Value columns.
    If verbose=True, also shows Value ID column.

    Args:
        title: Section title (e.g., "Fields (29)")
        fields: List of field value dicts from the API
        field_metadata: Mapping of field_id -> field_name (can be None)
        verbose: If True, include Value ID column
    """
    if not fields:
        return None

    # Group fields by field_id to handle multi-value fields
    from collections import OrderedDict

    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for field in fields:
        field_id = field.get("fieldId") or field.get("id") or ""
        if isinstance(field_id, str):
            grouped.setdefault(field_id, []).append(field)

    # Build table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Field ID")
    table.add_column("Value")
    if verbose:
        table.add_column("Value ID")

    def format_field_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value.is_integer():
                return f"{int(value):,}"
            if isinstance(value, int):
                return f"{value:,}"
            return str(value)
        if isinstance(value, str):
            # Truncate long values
            if len(value) > 120:
                return value[:117] + "..."
            return value
        if isinstance(value, dict):
            # Handle typed values like {type: "...", data: ...}
            data = value.get("data")
            if data is not None:
                return format_field_value(data)
            # Handle name/text objects
            name = value.get("name") or value.get("text")
            if isinstance(name, str):
                return name
            return f"object ({len(value)} keys)"
        if isinstance(value, list):
            if all(isinstance(v, str) for v in value):
                return ", ".join(value)
            if all(isinstance(v, dict) for v in value):
                # Extract names/texts from list of objects
                texts = []
                for item in value:
                    text = item.get("text") or item.get("name")
                    if isinstance(text, str):
                        texts.append(text)
                if texts:
                    return ", ".join(texts)
            return f"list ({len(value)} items)"
        return str(value)

    field_metadata = field_metadata or {}

    for field_id, field_values in grouped.items():
        field_name = field_metadata.get(field_id, "")
        for idx, fv in enumerate(field_values):
            # Show field name and ID only on first row for multi-value fields
            display_name = field_name if idx == 0 else ""
            display_id = field_id if idx == 0 else ""

            value = fv.get("value")
            value_str = format_field_value(value)

            if verbose:
                value_id = fv.get("id", "")
                value_id_str = str(value_id) if value_id else ""
                table.add_row(display_name, display_id, value_str, value_id_str)
            else:
                table.add_row(display_name, display_id, value_str)

    renderables: list[Any] = []
    renderables.append(Text(title, style="bold"))
    renderables.append(table)
    return Group(*renderables)


def _render_collection_section(
    *,
    title: str | None,
    rows: list[Any],
    pagination: dict[str, Any] | None,
    all_columns: bool = False,
    max_columns: int | None = None,
) -> Any:
    dict_rows = [r for r in rows if isinstance(r, dict)]
    renderables: list[Any] = []
    if title:
        renderables.append(Text(_humanize_title(title), style="bold"))
    table, omitted = _table_from_rows(
        cast(list[dict[str, Any]], dict_rows),
        all_columns=all_columns,
        max_columns=max_columns,
    )
    renderables.append(table)
    if omitted > 0:
        renderables.append(
            Text(f"({omitted} columns hidden — use --all-columns or --json)", style="dim")
        )
    if _pagination_has_more(pagination):
        renderables.append(
            Text(f"({len(dict_rows):,} shown, more available — use --max-results/--all or --json)")
        )
    return Group(*renderables) if len(renderables) > 1 else renderables[0]


def _render_collection_with_hint(
    *,
    title: str | None,
    rows: list[Any],
    hint: str,
    all_columns: bool = False,
    max_columns: int | None = None,
) -> Any:
    dict_rows = [r for r in rows if isinstance(r, dict)]
    renderables: list[Any] = []
    if title:
        renderables.append(Text(_humanize_title(title), style="bold"))
    table, omitted = _table_from_rows(
        cast(list[dict[str, Any]], dict_rows),
        all_columns=all_columns,
        max_columns=max_columns,
    )
    renderables.append(table)
    if omitted > 0:
        renderables.append(
            Text(f"({omitted} columns hidden — use --all-columns or --json)", style="dim")
        )
    renderables.append(Text(hint))
    return Group(*renderables) if len(renderables) > 1 else renderables[0]


def _render_object_section(
    *,
    title: str | None,
    obj: dict[str, Any],
    verbosity: int,
    pagination: dict[str, Any] | None,
    force_nested_keys: set[str] | None = None,
    all_columns: bool = False,
    max_columns: int | None = None,
) -> Any:
    scalar_summary: dict[str, Any] = {}
    nested: list[tuple[str, Any]] = []
    for k, v in obj.items():
        # Render nested collections/objects only at higher verbosity to avoid noise.
        if isinstance(v, (list, dict)):
            scalar_summary[str(k)] = v
            nested.append((str(k), v))
        else:
            scalar_summary[str(k)] = v

    renderables: list[Any] = []
    if title:
        renderables.append(Text(_humanize_title(title), style="bold"))

    # Use simple text output for dicts with only scalar values (no nested structures)
    # This is cleaner for commands like `files dump` that output simple summaries
    if _is_simple_scalar_dict(obj):
        renderables.append(_simple_kv_text(scalar_summary))
        return Group(*renderables) if len(renderables) > 1 else renderables[0]

    renderables.append(_kv_table(scalar_summary))
    kv_index = len(renderables) - 1

    show_nested_keys = set(force_nested_keys or set())
    if verbosity >= 1:
        show_nested_keys.update(k for k, _v in nested)

    if show_nested_keys:
        # Avoid duplicating "fields: list (N items)" style rows when we render a section.
        for key in show_nested_keys:
            if key in scalar_summary:
                scalar_summary[key] = "see below"
        renderables[kv_index] = _kv_table(scalar_summary)

        for k, v in nested:
            if k not in show_nested_keys:
                continue
            if isinstance(v, dict) and _is_collection_envelope(v):
                envelope = cast(dict[str, Any], v)
                renderables.append(
                    _render_collection_section(
                        title=k,
                        rows=cast(list[Any], envelope.get("data", [])),
                        pagination=cast(dict[str, Any] | None, envelope.get("pagination")),
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            elif isinstance(v, dict) and _is_collection_with_hint(v):
                renderables.append(
                    _render_collection_with_hint(
                        title=k,
                        rows=cast(list[Any], v.get("_rows", [])),
                        hint=cast(str, v.get("_hint")),
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            elif isinstance(v, list) and all(isinstance(x, dict) for x in v):
                renderables.append(
                    _render_collection_section(
                        title=k,
                        rows=v,
                        pagination=None,
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            elif isinstance(v, dict):
                renderables.append(
                    _render_object_section(
                        title=k,
                        obj=v,
                        verbosity=verbosity,
                        pagination=None,
                        force_nested_keys=None,
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            else:
                # Non-tabular nested values (e.g., list[str]) are already in the kv table.
                continue

    if _pagination_has_more(pagination):
        # Object sections can also be paginated (rare); keep message consistent.
        renderables.append(Text("(more available — use --max-results/--all or --json)"))

    return Group(*renderables) if len(renderables) > 1 else renderables[0]


def _extract_section_pagination(
    *,
    meta_pagination: dict[str, Any] | None,
    section: str,
) -> dict[str, Any] | None:
    if not meta_pagination:
        return None
    # Preferred contract: always keyed by section name.
    maybe = meta_pagination.get(section)
    if isinstance(maybe, dict):
        return cast(dict[str, Any], maybe)
    # Legacy: unkeyed single-section pagination dict.
    if any(
        k in meta_pagination
        for k in (
            "nextCursor",
            "prevCursor",
            "nextPageToken",
            "nextUrl",
            "prevUrl",
            "prevPageToken",
        )
    ):
        return meta_pagination
    return None


def _render_human_data(
    *,
    data: Any,
    meta_pagination: dict[str, Any] | None,
    meta_resolved: dict[str, Any] | None,
    verbosity: int,
    all_columns: bool = False,
    max_columns: int | None = None,
) -> Any:
    if isinstance(data, list) and all(isinstance(x, dict) for x in data):
        table, omitted = _table_from_rows(
            cast(list[dict[str, Any]], data),
            all_columns=all_columns,
            max_columns=max_columns,
        )
        pagination = (
            meta_pagination if meta_pagination and _pagination_has_more(meta_pagination) else None
        )
        renderables: list[Any] = [table]
        if omitted > 0:
            renderables.append(
                Text(f"({omitted} columns hidden — use --all-columns or --json)", style="dim")
            )
        if pagination:
            renderables.append(
                Text(f"({len(data):,} shown, more available — use --max-results/--all or --json)")
            )
        return Group(*renderables) if len(renderables) > 1 else table

    if isinstance(data, dict):
        if _is_collection_envelope(data):
            envelope = cast(dict[str, Any], data)
            return _render_collection_section(
                title=None,
                rows=cast(list[Any], envelope.get("data", [])),
                pagination=cast(dict[str, Any] | None, envelope.get("pagination")),
                all_columns=all_columns,
                max_columns=max_columns,
            )

        # Sectioned dict rendering: render collections as tables; objects as kv.
        keys = list(data.keys())
        if len(keys) == 1:
            only_key = str(keys[0])
            v = data[only_key]
            section_pagination = _extract_section_pagination(
                meta_pagination=meta_pagination, section=only_key
            )
            if _is_text_marker(v):
                return Group(
                    Text(_humanize_title(only_key), style="bold"),
                    Text(cast(dict[str, Any], v)["_text"]),
                )
            if isinstance(v, dict) and _is_collection_envelope(v):
                envelope = cast(dict[str, Any], v)
                return _render_collection_section(
                    title=only_key,
                    rows=cast(list[Any], envelope.get("data", [])),
                    pagination=cast(dict[str, Any] | None, envelope.get("pagination")),
                    all_columns=all_columns,
                    max_columns=max_columns,
                )
            if isinstance(v, dict) and _is_collection_with_hint(v):
                return _render_collection_with_hint(
                    title=only_key,
                    rows=cast(list[Any], v.get("_rows", [])),
                    hint=cast(str, v.get("_hint")),
                    all_columns=all_columns,
                    max_columns=max_columns,
                )
            if isinstance(v, list) and all(isinstance(x, dict) for x in v):
                # Check if this is a fields section with metadata available
                if (
                    only_key == "fields"
                    and isinstance(meta_resolved, dict)
                    and "fieldMetadata" in meta_resolved
                ):
                    field_metadata = meta_resolved.get("fieldMetadata")
                    if isinstance(field_metadata, dict):
                        fields_section = _render_fields_section(
                            title=f"Fields ({len(v)})" if v else "Fields",
                            fields=cast(list[dict[str, Any]], v),
                            field_metadata=cast(dict[str, str], field_metadata),
                            verbose=verbosity >= 1,
                        )
                        if fields_section is not None:
                            return fields_section
                return _render_collection_section(
                    title=only_key,
                    rows=v,
                    pagination=section_pagination,
                    all_columns=all_columns,
                    max_columns=max_columns,
                )
            if isinstance(v, dict):
                company_force_nested: set[str] | None = None
                if (
                    only_key == "company"
                    and isinstance(meta_resolved, dict)
                    and "fieldSelection" in meta_resolved
                ):
                    company_force_nested = {"fields"}
                return _render_object_section(
                    title=only_key,
                    obj=v,
                    verbosity=verbosity,
                    pagination=section_pagination,
                    force_nested_keys=company_force_nested,
                    all_columns=all_columns,
                    max_columns=max_columns,
                )

        sections: list[Any] = []
        for k in keys:
            key = str(k)
            v = data[key]
            section_pagination = _extract_section_pagination(
                meta_pagination=meta_pagination, section=key
            )

            if _is_text_marker(v):
                sections.append(
                    Group(
                        Text(_humanize_title(key), style="bold"),
                        Text(cast(dict[str, Any], v)["_text"]),
                    )
                )
                continue

            if isinstance(v, dict) and _is_collection_envelope(v):
                envelope = cast(dict[str, Any], v)
                sections.append(
                    _render_collection_section(
                        title=key,
                        rows=cast(list[Any], envelope.get("data", [])),
                        pagination=cast(dict[str, Any] | None, envelope.get("pagination")),
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            elif isinstance(v, dict) and _is_collection_with_hint(v):
                sections.append(
                    _render_collection_with_hint(
                        title=key,
                        rows=cast(list[Any], v.get("_rows", [])),
                        hint=cast(str, v.get("_hint")),
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            elif isinstance(v, list) and all(isinstance(x, dict) for x in v):
                # Check if this is a fields section with metadata available
                if (
                    key == "fields"
                    and isinstance(meta_resolved, dict)
                    and "fieldMetadata" in meta_resolved
                ):
                    field_metadata = meta_resolved.get("fieldMetadata")
                    if isinstance(field_metadata, dict):
                        fields_section = _render_fields_section(
                            title=f"Fields ({len(v)})" if v else "Fields",
                            fields=cast(list[dict[str, Any]], v),
                            field_metadata=cast(dict[str, str], field_metadata),
                            verbose=verbosity >= 1,
                        )
                        if fields_section is not None:
                            sections.append(fields_section)
                        continue
                sections.append(
                    _render_collection_section(
                        title=key,
                        rows=v,
                        pagination=section_pagination,
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            elif isinstance(v, dict):
                force_nested: set[str] | None = None
                if (
                    key == "company"
                    and isinstance(meta_resolved, dict)
                    and "fieldSelection" in meta_resolved
                ):
                    force_nested = {"fields"}
                sections.append(
                    _render_object_section(
                        title=key,
                        obj=v,
                        verbosity=verbosity,
                        pagination=section_pagination,
                        force_nested_keys=force_nested,
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
            else:
                sections.append(
                    _render_object_section(
                        title=key,
                        obj={"value": v},
                        verbosity=verbosity,
                        pagination=section_pagination,
                        force_nested_keys=None,
                        all_columns=all_columns,
                        max_columns=max_columns,
                    )
                )
        return Group(*sections) if sections else Panel.fit(Text("OK"))

    return Panel.fit(Text(str(data) if data is not None else "OK"))


def _should_use_pager(*, settings: RenderSettings, stdout: Console, renderable: Any) -> bool:
    if settings.pager is False:
        return False
    if settings.pager is True:
        return True

    # Auto mode: only page when interactive and the output is likely to scroll.
    if not sys.stdout.isatty():
        return False

    try:
        height = stdout.size.height
        if not height:
            return False
        lines = stdout.render_lines(renderable, options=stdout.options, pad=False)
        return len(lines) > height
    except Exception:
        return False


def render_result(result: CommandResult, *, settings: RenderSettings) -> int:
    stdout = Console(file=sys.stdout, force_terminal=False)
    stderr = Console(file=sys.stderr, force_terminal=False)

    if settings.output == "json":
        payload = result.model_dump(by_alias=True, mode="json")
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0

    # table/human output
    if not result.ok:
        if result.error is not None:
            title = _error_title(result.error.type)
            stderr.print(f"{title}: {result.error.message}")
            _render_error_details(
                stderr=stderr,
                command=result.command.name,
                error_type=result.error.type,
                message=result.error.message,
                hint=result.error.hint,
                docs_url=result.error.docs_url,
                details=result.error.details,
                settings=settings,
            )
        else:
            stderr.print("Error")
        return 0

    # Display context header if available
    context_header = result.command.format_header()
    if context_header and not settings.quiet:
        stdout.print(Text(context_header, style="bold"))

    renderable: Any
    if result.command.name == "version" and isinstance(result.data, dict):
        renderable = Text(result.data.get("version", ""), style="bold")
    elif result.command.name == "config path" and isinstance(result.data, dict):
        renderable = Text(str(result.data.get("path", "")))
    elif result.command.name == "config init" and isinstance(result.data, dict):
        renderable = Panel.fit(Text(f"Initialized config at {result.data.get('path', '')}"))
    elif result.command.name == "resolve-url" and isinstance(result.data, dict):
        renderable = Text(
            f"{result.data.get('type')} {result.data.get('canonicalUrl', '')}".strip()
        )
    elif result.command.name == "whoami" and isinstance(result.data, dict):
        tenant = (
            result.data.get("tenant", {}) if isinstance(result.data.get("tenant"), dict) else {}
        )
        user = result.data.get("user", {}) if isinstance(result.data.get("user"), dict) else {}
        tenant_name = tenant.get("name") or ""
        tenant_id = tenant.get("id")
        first = user.get("firstName", "") or ""
        last = user.get("lastName", "") or ""
        email = user.get("emailAddress", "") or ""
        user_id = user.get("id")
        name = f"{first} {last}".strip()
        # Simple text output: "Name <email>" with optional tenant on separate line
        lines = []
        if tenant_name:
            lines.append(tenant_name)
        if name and email:
            lines.append(f"{name} <{email}>")
        elif name:
            lines.append(name)
        elif email:
            lines.append(email)
        # With -v, show IDs (useful for scripting)
        if settings.verbosity >= 1:
            if user_id is not None:
                lines.append(f"User ID: {user_id}")
            if tenant_id is not None:
                lines.append(f"Tenant ID: {tenant_id}")
        renderable = Text("\n".join(lines)) if lines else Text("OK")
    else:
        renderable = _render_human_data(
            data=result.data,
            meta_pagination=result.meta.pagination,
            meta_resolved=result.meta.resolved,
            verbosity=settings.verbosity,
            all_columns=settings.all_columns,
            max_columns=settings.max_columns,
        )

    if renderable is not None:
        if _should_use_pager(settings=settings, stdout=stdout, renderable=renderable):
            with stdout.pager(styles=True):
                stdout.print(renderable)
        else:
            stdout.print(renderable)

    # Render summary footer (e.g., row counts, date ranges, type breakdowns)
    if result.meta.summary is not None and not settings.quiet:
        summary_footer = _render_summary_footer(result.meta.summary, settings.verbosity)
        if summary_footer:
            stdout.print(summary_footer)

    if result.meta.rate_limit is not None and not settings.quiet:
        footer = _rate_limit_footer(result.meta.rate_limit)
        if footer:
            stdout.print(footer)

    return 0
