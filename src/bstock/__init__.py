"""bstock: common data structure for building stocks.

See ``SPEC.md`` for the design reference.
"""

from importlib.metadata import PackageNotFoundError, version

from bstock.exceptions import (
    BstockError,
    DuplicateIdError,
    EntityInUseError,
    InvalidIdError,
    UnknownIdError,
)

try:
    __version__ = version("bstock")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"

__all__ = [
    "BstockError",
    "DuplicateIdError",
    "EntityInUseError",
    "InvalidIdError",
    "UnknownIdError",
    "__version__",
]
