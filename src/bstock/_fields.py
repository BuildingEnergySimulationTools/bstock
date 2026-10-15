"""Field name arguments of the analysis and display functions."""

from __future__ import annotations

from collections.abc import Iterable


def field_names(fields: str | Iterable[str] | None) -> list[str] | None:
    """Return ``fields`` as a list of names (``None`` kept); a single string is one name."""
    if fields is None:
        return None
    return [fields] if isinstance(fields, str) else list(fields)
