"""``--filter`` text as a WHERE clause (moved to :mod:`affinity._filtering.filter_text`)."""

from ..._filtering.filter_text import ROW_KEYS, where_from_filter

__all__ = ["ROW_KEYS", "where_from_filter"]
