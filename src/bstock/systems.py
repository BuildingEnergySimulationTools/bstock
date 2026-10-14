"""Technical systems: generators, ventilation and photovoltaics (SPEC §3.8)."""

from __future__ import annotations

import warnings
from typing import Any, ClassVar

from pydantic import Field, field_validator, model_validator

from bstock._ids import check_id, new_id
from bstock.enums import EnergyType, GeneratorTechnology, SystemKind
from bstock.exceptions import PerformanceRatioWarning
from bstock.provenance import SourcedModel, sourced

__all__ = [
    "CoolingGenerator",
    "DHWGenerator",
    "Generator",
    "HeatingGenerator",
    "SolarPV",
    "System",
    "Ventilation",
]

_T = GeneratorTechnology

#: Technologies whose performance ratio should not exceed ~1 (combustion, Joule effect, substation).
NON_THERMODYNAMIC = frozenset(
    {
        _T.BOILER,
        _T.BOILER_STANDARD,
        _T.BOILER_LOW_TEMPERATURE,
        _T.BOILER_CONDENSING,
        _T.ELECTRIC_BOILER,
        _T.ELECTRIC_RESISTANCE,
        _T.ELECTRIC_STORAGE_HEATER,
        _T.STOVE_INSERT,
        _T.GAS_RADIATOR,
        _T.WARM_AIR_GENERATOR,
        _T.DISTRICT_SUBSTATION,
        _T.WATER_HEATER,
        _T.ELECTRIC_WATER_TANK,
        _T.INSTANT_WATER_HEATER,
    }
)

#: Technologies whose performance ratio is expected above 1.
THERMODYNAMIC = frozenset(
    {
        _T.HEAT_PUMP,
        _T.HEAT_PUMP_AIR_AIR,
        _T.HEAT_PUMP_AIR_WATER,
        _T.HEAT_PUMP_WATER_WATER,
        _T.HEAT_PUMP_GROUND,
        _T.HEAT_PUMP_GAS,
        _T.THERMODYNAMIC_WATER_HEATER,
        _T.CHILLER,
    }
)

#: Ratio above which a non-thermodynamic generator triggers a warning (condensing boilers on LHV
#: reach about 1.05-1.09).
MAX_NON_THERMODYNAMIC_RATIO = 1.1

_RATIO_FIELDS = ("nominal_performance_ratio", "seasonal_performance_ratio")


class System(SourcedModel):
    """Base class of technical systems, owned by a building or shared by a site.

    Parameters
    ----------
    id
        Unique id in the park; generated from :attr:`kind` when omitted.
    serves
        For a system owned by a site: ids of the buildings it serves; ``None`` means every
        building of the site and of its sub-sites. Must stay ``None`` for a system owned by a
        building. Checked when the system is added to its owner and on assignment.
    """

    kind: ClassVar[str] = "system"
    """Kind of system (a :class:`SystemKind` on concrete classes), used as id prefix and by
    ``building.serving_systems(kind=...)``."""

    id: str = Field(frozen=True)
    serves: list[str] | None = None

    name = sourced(str)

    @model_validator(mode="before")
    @classmethod
    def _generate_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("id"):
            data = {**data, "id": new_id(cls.kind)}
        return data

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return check_id(value)

    @field_validator("serves")
    @classmethod
    def _check_served_ids(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [check_id(v) for v in value]

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "serves" and self._container is not None:
            self._container._check_served(value)
        super().__setattr__(name, value)


class Generator(System):
    """Base class of heating, cooling and domestic hot water generators.

    The performance ratios are *useful energy produced / final energy consumed*: COP, EER or
    efficiency depending on the technology. They may exceed 1 (heat pumps, chillers, condensing
    boilers on LHV). A warning is emitted when a ratio looks inconsistent with ``technology``.
    """

    technology = sourced(GeneratorTechnology, description="How to read the performance ratios")
    energy_type = sourced(EnergyType, description="Final energy consumed")
    nominal_power = sourced(float, unit="kW", ge=0)
    nominal_performance_ratio = sourced(
        float, gt=0, description="Rated COP, EER or efficiency (data sheet)"
    )
    seasonal_performance_ratio = sourced(
        float, gt=0, description="SCOP, SEER or seasonal efficiency (as given by the DPE)"
    )

    def model_post_init(self, context: Any) -> None:
        """Check the performance ratios given at construction."""
        self.check_performance_ratios()

    def _on_change(self, name: str) -> None:
        """Check the ratios again when the technology or a ratio changes."""
        if name == "technology" or name in _RATIO_FIELDS:
            self.check_performance_ratios()

    def check_performance_ratios(self) -> list[str]:
        """Warn about effective ratios inconsistent with the effective technology.

        Returns
        -------
        list of str
            The warning messages (empty when everything looks consistent).
        """
        technology = self.technology
        messages: list[str] = []
        for name in _RATIO_FIELDS:
            ratio = getattr(self, name)
            if technology is None or ratio is None:
                continue
            if technology in NON_THERMODYNAMIC and ratio > MAX_NON_THERMODYNAMIC_RATIO:
                messages.append(
                    f"{self.id}: {name}={ratio} is above {MAX_NON_THERMODYNAMIC_RATIO} "
                    f"for a {technology} generator"
                )
            elif technology in THERMODYNAMIC and ratio < 1:
                messages.append(f"{self.id}: {name}={ratio} is below 1 for a {technology}")
        for message in messages:
            warnings.warn(message, PerformanceRatioWarning, stacklevel=4)
        return messages


class HeatingGenerator(Generator):
    """Space heating generator."""

    kind: ClassVar[str] = SystemKind.HEATING


class CoolingGenerator(Generator):
    """Space cooling generator."""

    kind: ClassVar[str] = SystemKind.COOLING


class DHWGenerator(Generator):
    """Domestic hot water generator."""

    kind: ClassVar[str] = SystemKind.DHW

    storage_volume = sourced(
        float, unit="L", ge=0, description="Storage volume; None for instantaneous or unknown"
    )


class Ventilation(System):
    """Ventilation system."""

    kind: ClassVar[str] = SystemKind.VENTILATION

    air_change_rate = sourced(float, unit="1/h", ge=0, description="Air changes per hour")
    heat_recovery_efficiency = sourced(
        float, ge=0, le=1, description="Heat exchanger efficiency; None means no heat recovery"
    )


class SolarPV(System):
    """Photovoltaic installation."""

    kind: ClassVar[str] = SystemKind.PV

    peak_power = sourced(float, unit="kWp", ge=0)
    area = sourced(float, unit="m²", ge=0)
    azimuth = sourced(float, unit="°", ge=0, le=360, description="0 = north, 180 = south")
    tilt = sourced(float, unit="°", ge=0, le=90, description="0 = horizontal")


#: Concrete system classes by kind.
SYSTEM_CLASSES: dict[SystemKind, type[System]] = {
    SystemKind(cls.kind): cls
    for cls in (HeatingGenerator, CoolingGenerator, DHWGenerator, Ventilation, SolarPV)
}
