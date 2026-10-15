"""Enumerations of the bstock data model (SPEC §3.9).

All enums are :class:`enum.StrEnum` whose value is the lower-case member name, e.g.
``EnergyType.NATURAL_GAS == "natural_gas"``. They serialise to that value, and every API
accepts either the member or its string value, in any case (``Orientation("N")`` is
``Orientation.N``).

Source labels (BDNB, DPE) are translated to these values by the connectors; the mapping
tables are in SPEC appendix A.

Some members are generic: they keep the family of a value whose detail the source does not give
(``GeneratorTechnology.HEAT_PUMP``, ``EnergyType.WOOD``). :attr:`family` and :meth:`variants`
relate them to the more precise members.
"""

from enum import StrEnum, auto
from typing import Any, Self

__all__ = [
    "Adjacency",
    "BuildingUsage",
    "ComponentKind",
    "EndUse",
    "EnergyType",
    "FrameType",
    "GeneratorTechnology",
    "GlazingType",
    "Inertia",
    "InsulationLevel",
    "Orientation",
    "SystemKind",
]


class _StrEnum(StrEnum):
    """StrEnum whose lookup by value ignores the case, with families of members."""

    @classmethod
    def _missing_(cls, value: Any) -> Self | None:
        if isinstance(value, str):
            return next((m for m in cls if m.value == value.lower()), None)
        return None

    @property
    def family(self) -> Self:
        """The generic member of the family of this member, or the member itself.

        E.g. ``GeneratorTechnology.HEAT_PUMP_AIR_WATER.family`` is
        ``GeneratorTechnology.HEAT_PUMP``, and ``EnergyType.WOOD_LOGS.family`` is
        ``EnergyType.WOOD``. A member outside any family is its own family.
        """
        family: Self = _FAMILIES.get((type(self), self.value), self)
        return family

    def variants(self) -> frozenset[Self]:
        """Return this member and, for a generic member, the more precise members of its family.

        E.g. ``EnergyType.WOOD.variants()`` is ``{WOOD, WOOD_LOGS, WOOD_PELLETS, WOOD_CHIPS}``,
        while ``EnergyType.WOOD_LOGS.variants()`` is ``{WOOD_LOGS}``. Usable wherever a list of
        values is accepted: ``park.timeseries(energy_type=EnergyType.WOOD.variants())``.
        """
        return frozenset(m for m in type(self) if m is self or m.family is self)


class BuildingUsage(_StrEnum):
    """Main usage of a building or site (BDNB ``usage_principal_bdnb_open``).

    ``AGRICULTURE``, ``INDUSTRY`` and ``TERTIARY`` are the BDNB primary, secondary and tertiary
    economic sectors; ``TERTIARY`` is services (offices, schools, shops...).
    """

    RESIDENTIAL_INDIVIDUAL = auto()
    RESIDENTIAL_COLLECTIVE = auto()
    TERTIARY = auto()  # services
    INDUSTRY = auto()  # BDNB "Secondaire"
    AGRICULTURE = auto()  # BDNB "Primaire"
    OUTBUILDING = auto()  # "Dépendance"
    UNDETERMINED = auto()


class EnergyType(_StrEnum):
    """Final energy carrier consumed by a generator or measured by a meter."""

    ELECTRICITY = auto()
    NATURAL_GAS = auto()
    FUEL_OIL = auto()
    LPG = auto()  # GPL, propane, butane
    WOOD = auto()  # wood, form unknown
    WOOD_LOGS = auto()
    WOOD_PELLETS = auto()
    WOOD_CHIPS = auto()
    COAL = auto()
    DISTRICT_HEATING = auto()
    DISTRICT_COOLING = auto()
    SOLAR = auto()
    OTHER_FOSSIL = auto()
    OTHER = auto()


class GeneratorTechnology(_StrEnum):
    """Technology of a heating, cooling or domestic hot water generator.

    It tells how to read the generator performance ratios: a heat pump ratio is expected above 1, a
    combustion or Joule-effect ratio around or below 1.
    """

    BOILER = auto()  # boiler of unknown type
    BOILER_STANDARD = auto()
    BOILER_LOW_TEMPERATURE = auto()
    BOILER_CONDENSING = auto()
    ELECTRIC_BOILER = auto()
    ELECTRIC_RESISTANCE = auto()  # Joule effect: convectors, radiant panels...
    ELECTRIC_STORAGE_HEATER = auto()
    HEAT_PUMP = auto()  # heat pump of unknown type
    HEAT_PUMP_AIR_AIR = auto()
    HEAT_PUMP_AIR_WATER = auto()
    HEAT_PUMP_WATER_WATER = auto()
    HEAT_PUMP_GROUND = auto()
    HEAT_PUMP_GAS = auto()
    HEAT_PUMP_HYBRID = auto()
    STOVE_INSERT = auto()
    GAS_RADIATOR = auto()
    WARM_AIR_GENERATOR = auto()
    DISTRICT_SUBSTATION = auto()
    SOLAR_THERMAL = auto()
    WATER_HEATER = auto()  # non-thermodynamic water heater of unknown type
    ELECTRIC_WATER_TANK = auto()
    INSTANT_WATER_HEATER = auto()
    THERMODYNAMIC_WATER_HEATER = auto()
    CHILLER = auto()
    OTHER = auto()


class Inertia(_StrEnum):
    """Thermal inertia class of a building (DPE ``classe_inertie``)."""

    LIGHT = auto()
    MEDIUM = auto()
    HEAVY = auto()
    VERY_HEAVY = auto()


class InsulationLevel(_StrEnum):
    """Insulation quality of an envelope component (DPE ``qualite_isolation_*``)."""

    INSUFFICIENT = auto()
    AVERAGE = auto()
    GOOD = auto()
    VERY_GOOD = auto()


class GlazingType(_StrEnum):
    """Glazing of a window (DPE ``type_vitrage``)."""

    SINGLE = auto()
    DOUBLE = auto()
    TRIPLE = auto()
    SECONDARY_GLAZING = auto()  # "Survitrage"
    GLASS_BRICK = auto()
    POLYCARBONATE = auto()


class FrameType(_StrEnum):
    """Window frame material (DPE ``type_materiaux_menuiserie``)."""

    WOOD = auto()
    WOOD_METAL = auto()
    PVC = auto()
    METAL_THERMAL_BREAK = auto()
    METAL_NO_THERMAL_BREAK = auto()
    OTHER = auto()


class ComponentKind(_StrEnum):
    """Kind of envelope component."""

    WALL = auto()
    ROOF = auto()
    FLOOR = auto()
    WINDOW = auto()
    DOOR = auto()
    THERMAL_BRIDGE = auto()


class Orientation(_StrEnum):
    """Orientation of an envelope component."""

    N = auto()
    NE = auto()
    E = auto()
    SE = auto()
    S = auto()
    SW = auto()
    W = auto()
    NW = auto()
    HORIZONTAL = auto()


class Adjacency(_StrEnum):
    """What lies on the other side of an envelope component."""

    EXTERIOR = auto()
    UNHEATED_SPACE = auto()
    GROUND = auto()
    ADJACENT_BUILDING = auto()


class SystemKind(_StrEnum):
    """Kind of technical system; also the name of the matching list on sites and buildings.

    ``DHW`` is domestic hot water and ``PV`` photovoltaics, the usual abbreviations of the field.
    """

    HEATING = auto()
    COOLING = auto()
    DHW = auto()
    VENTILATION = auto()
    PV = auto()


class EndUse(_StrEnum):
    """End use measured by a meter."""

    HEATING = auto()
    COOLING = auto()
    DHW = auto()
    VENTILATION = auto()
    LIGHTING = auto()
    APPLIANCES = auto()
    PV_PRODUCTION = auto()
    TOTAL = auto()
    OTHER = auto()


_T = GeneratorTechnology
_VARIANTS: dict[_StrEnum, tuple[_StrEnum, ...]] = {
    EnergyType.WOOD: (EnergyType.WOOD_LOGS, EnergyType.WOOD_PELLETS, EnergyType.WOOD_CHIPS),
    _T.BOILER: (_T.BOILER_STANDARD, _T.BOILER_LOW_TEMPERATURE, _T.BOILER_CONDENSING),
    _T.HEAT_PUMP: (
        _T.HEAT_PUMP_AIR_AIR,
        _T.HEAT_PUMP_AIR_WATER,
        _T.HEAT_PUMP_WATER_WATER,
        _T.HEAT_PUMP_GROUND,
        _T.HEAT_PUMP_GAS,
        _T.HEAT_PUMP_HYBRID,
    ),
    _T.WATER_HEATER: (_T.ELECTRIC_WATER_TANK, _T.INSTANT_WATER_HEATER),
}
"""Generic member -> its more precise variants (SPEC §3.9)."""
# keyed by (enum, value): members of different enums with the same value compare equal
_FAMILIES: dict[tuple[type, str], Any] = {
    (type(variant), variant.value): generic
    for generic, variants in _VARIANTS.items()
    for variant in variants
}
