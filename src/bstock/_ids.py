"""Identifier helpers (SPEC §3.2)."""

import uuid

from bstock.exceptions import InvalidIdError

TIDE_SEPARATOR = "__"


def new_id(prefix: str) -> str:
    """Return a new identifier made of ``prefix`` and 8 hexadecimal characters."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def check_id(value: str) -> str:
    """Return ``value`` if it is a valid bstock identifier.

    Raises
    ------
    InvalidIdError
        If the id is empty or contains the tide separator ``__``.
    """
    if not value:
        raise InvalidIdError("an id cannot be empty")
    if TIDE_SEPARATOR in value:
        raise InvalidIdError(f"id {value!r} must not contain {TIDE_SEPARATOR!r}")
    return value
