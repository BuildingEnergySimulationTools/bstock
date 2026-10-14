"""Identifier helpers (SPEC §3.2)."""

import re
import uuid
import warnings

from bstock.exceptions import IdentifierFormatWarning, InvalidIdError

TIDE_SEPARATOR = "__"

#: Expected formats of external identifiers. A mismatch only triggers a warning.
EXTERNAL_ID_PATTERNS = {
    "rnb": re.compile(r"^[0-9A-Z]{12}$"),
    "bdnb_batiment_groupe": re.compile(r"^bdnb-bg-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$"),
    "bdnb_batiment_construction": re.compile(r"^bdnb-bc-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$"),
    "dpe": re.compile(r"^[0-9A-Z]{13}$"),
}


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


def normalize_rnb_id(rnb_id: str) -> str:
    """Return ``rnb_id`` in the RNB canonical form: upper case, without dashes or spaces."""
    return rnb_id.replace("-", "").replace(" ", "").upper()


def check_external_id(kind: str, value: str | None) -> str | None:
    """Return ``value``, warning if it does not match the expected format of ``kind``.

    ``kind`` is a key of :data:`EXTERNAL_ID_PATTERNS`.
    """
    if value is not None and not EXTERNAL_ID_PATTERNS[kind].match(value):
        warnings.warn(
            f"{value!r} does not look like a {kind} identifier",
            IdentifierFormatWarning,
            stacklevel=4,
        )
    return value
