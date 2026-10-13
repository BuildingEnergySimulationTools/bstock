"""bstock: common data structure for building stocks.

See ``SPEC.md`` for the design reference.
"""

from importlib.metadata import PackageNotFoundError, version

from bstock.building import Building
from bstock.description import DescribedEntity
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
from bstock.envelope import COMPUTED_SOURCE, Envelope, EnvelopeComponent, Window
from bstock.exceptions import (
    BstockError,
    BstockWarning,
    DuplicateIdError,
    EntityInUseError,
    IdentifierFormatWarning,
    IncompleteDataError,
    InvalidIdError,
    PerformanceRatioWarning,
    UnknownAttributeWarning,
    UnknownFieldError,
    UnknownIdError,
)
from bstock.park import BuildingPark
from bstock.provenance import (
    DEFAULT_SOURCE_PRIORITY,
    SourcedField,
    SourcedModel,
    SourcedValue,
    SourcePriority,
    sourced,
)
from bstock.site import Site
from bstock.systems import (
    CoolingGenerator,
    DHWGenerator,
    Generator,
    HeatingGenerator,
    SolarPV,
    System,
    Ventilation,
)

try:
    __version__ = version("bstock")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"

__all__ = [
    "COMPUTED_SOURCE",
    "DEFAULT_SOURCE_PRIORITY",
    "Adjacency",
    "BstockError",
    "BstockWarning",
    "Building",
    "BuildingPark",
    "BuildingUsage",
    "ComponentKind",
    "CoolingGenerator",
    "DHWGenerator",
    "DescribedEntity",
    "DuplicateIdError",
    "EndUse",
    "EnergyType",
    "EntityInUseError",
    "Envelope",
    "EnvelopeComponent",
    "FrameType",
    "Generator",
    "GeneratorTechnology",
    "GlazingType",
    "HeatingGenerator",
    "IdentifierFormatWarning",
    "IncompleteDataError",
    "Inertia",
    "InsulationLevel",
    "InvalidIdError",
    "Orientation",
    "PerformanceRatioWarning",
    "Site",
    "SolarPV",
    "SourcePriority",
    "SourcedField",
    "SourcedModel",
    "SourcedValue",
    "System",
    "UnknownAttributeWarning",
    "UnknownFieldError",
    "UnknownIdError",
    "Ventilation",
    "Window",
    "__version__",
    "sourced",
]
