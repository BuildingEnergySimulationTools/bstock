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
    BstockWarning,
    DuplicateIdError,
    EntityInUseError,
    InvalidIdError,
    UnknownAttributeWarning,
    UnknownFieldError,
    UnknownIdError,
)
from bstock.provenance import (
    DEFAULT_SOURCE_PRIORITY,
    SourcedField,
    SourcedModel,
    SourcedValue,
    SourcePriority,
    sourced,
)

try:
    __version__ = version("bstock")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"

__all__ = [
    "DEFAULT_SOURCE_PRIORITY",
    "Adjacency",
    "BstockError",
    "BstockWarning",
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
    "SourcePriority",
    "SourcedField",
    "SourcedModel",
    "SourcedValue",
    "UnknownAttributeWarning",
    "UnknownFieldError",
    "UnknownIdError",
    "__version__",
    "sourced",
]
