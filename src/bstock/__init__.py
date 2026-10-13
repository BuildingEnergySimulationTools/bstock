"""bstock: common data structure for building stocks.

See ``SPEC.md`` for the design reference.
"""

from importlib.metadata import PackageNotFoundError, version

from bstock.enums import (
    Adjacency,
    BuildingUsage,
    ComponentKind,
    EndUse,
    EnergyType,
    FrameType,
    GeneratorTechnology,
    GlazingType,
    Inertia,
    InsulationLevel,
    Orientation,
)
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
    "Adjacency",
    "BstockError",
    "BuildingUsage",
    "ComponentKind",
    "DuplicateIdError",
    "EndUse",
    "EnergyType",
    "EntityInUseError",
    "FrameType",
    "GeneratorTechnology",
    "GlazingType",
    "Inertia",
    "InsulationLevel",
    "InvalidIdError",
    "Orientation",
    "UnknownIdError",
    "__version__",
]
