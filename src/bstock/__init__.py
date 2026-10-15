"""bstock: common data structure for building stocks.

See ``SPEC.md`` for the design reference.
"""

from importlib.metadata import PackageNotFoundError, version

from bstock.building import Building
from bstock.description import DescribedEntity
from bstock.display import TreeView
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
    SystemKind,
)
from bstock.envelope import Envelope, EnvelopeComponent, Window
from bstock.exceptions import (
    BstockError,
    BstockWarning,
    ClassMismatchError,
    ClassMismatchWarning,
    ConnectorError,
    DataReadError,
    DuplicateIdError,
    EntityInUseError,
    HierarchyError,
    IdentifierFormatWarning,
    IncompleteDataError,
    InvalidArgumentError,
    InvalidIdError,
    InvalidTagError,
    MissingDependencyError,
    NoDataWarning,
    PerformanceRatioWarning,
    TimeSeriesError,
    UnknownFieldError,
    UnknownFieldWarning,
    UnknownIdError,
    UnknownKindError,
    UnmappedValueWarning,
    UnsupportedSchemaError,
)
from bstock.meters import Channel, Meter, tide_column
from bstock.park import BuildingPark
from bstock.provenance import (
    COMPUTED_SOURCE,
    DEFAULT_SOURCE_PRIORITY,
    MANUAL_SOURCE,
    WILDCARD_SOURCE,
    SourcedField,
    SourcedModel,
    SourcedValue,
    SourcePriority,
    sourced,
)
from bstock.readers import CsvReader, DataReader, ParquetReader, UnregisteredReader
from bstock.selection import Selection
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
    "MANUAL_SOURCE",
    "WILDCARD_SOURCE",
    "Adjacency",
    "BstockError",
    "BstockWarning",
    "Building",
    "BuildingPark",
    "BuildingUsage",
    "Channel",
    "ClassMismatchError",
    "ClassMismatchWarning",
    "ComponentKind",
    "ConnectorError",
    "CoolingGenerator",
    "CsvReader",
    "DHWGenerator",
    "DataReadError",
    "DataReader",
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
    "HierarchyError",
    "IdentifierFormatWarning",
    "IncompleteDataError",
    "Inertia",
    "InsulationLevel",
    "InvalidArgumentError",
    "InvalidIdError",
    "InvalidTagError",
    "Meter",
    "MissingDependencyError",
    "NoDataWarning",
    "Orientation",
    "ParquetReader",
    "PerformanceRatioWarning",
    "Selection",
    "Site",
    "SolarPV",
    "SourcePriority",
    "SourcedField",
    "SourcedModel",
    "SourcedValue",
    "System",
    "SystemKind",
    "TimeSeriesError",
    "TreeView",
    "UnknownFieldError",
    "UnknownFieldWarning",
    "UnknownIdError",
    "UnknownKindError",
    "UnmappedValueWarning",
    "UnregisteredReader",
    "UnsupportedSchemaError",
    "Ventilation",
    "Window",
    "__version__",
    "sourced",
    "tide_column",
]
