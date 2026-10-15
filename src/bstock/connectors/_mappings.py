"""Correspondence tables between source labels and bstock enums (SPEC annex A).

Pure functions: a source label in, an enum member (or ``None``) out. Labels are compared exactly,
without normalisation: the sources mix straight and typographic apostrophes, and the DPE wood labels
use an en dash (U+2013).

An unknown label gives ``OTHER`` when the enum has it, else ``None``, and emits an
:class:`~bstock.exceptions.UnmappedValueWarning`. The connectors keep the label in ``raw``.
"""

from __future__ import annotations

import csv
import functools
import io
import warnings
from collections.abc import Mapping
from enum import Enum
from importlib.resources import files
from typing import NamedTuple, TypeVar

from bstock.enums import (
    BuildingUsage,
    EnergyType,
    FrameType,
    GeneratorTechnology,
    GlazingType,
    Inertia,
    InsulationLevel,
)
from bstock.exceptions import UnmappedValueWarning

__all__ = [
    "EMPTY_LABELS",
    "GeneratorMapping",
    "bdnb_energy_type",
    "bdnb_generator",
    "bdnb_usage",
    "dpe_energy_type",
    "dpe_frame",
    "dpe_generator",
    "dpe_glazing",
    "dpe_inertia",
    "dpe_insulation",
]

E = TypeVar("E", bound=Enum)
_T = GeneratorTechnology
_E = EnergyType

EMPTY_LABELS = frozenset({"", "Non affecté"})
"""Labels meaning "no value" (the DPE open data uses ``Non affecté``)."""

DPE_GENERATORS_FILE = "dpe_generators.csv"
"""Data file of the DPE generator labels, in ``bstock/connectors/data`` (SPEC A.5)."""


class GeneratorMapping(NamedTuple):
    """Translation of a generator label."""

    technology: GeneratorTechnology | None
    energy_type: EnergyType | None


def _translate(label: str | None, table: Mapping[str, E], enum: type[E], what: str) -> E | None:
    if label is None or label in EMPTY_LABELS:
        return None
    if label in table:
        return table[label]
    fallback: E | None = enum.__members__.get("OTHER")  # type: ignore[assignment]
    outcome = "translated to OTHER" if fallback is not None else "value not set"
    warnings.warn(f"unknown {what} label {label!r}: {outcome}", UnmappedValueWarning, stacklevel=3)
    return fallback


# --- A.2 usage --------------------------------------------------------------------------------

BDNB_USAGES: dict[str, BuildingUsage] = {
    "Résidentiel individuel": BuildingUsage.RESIDENTIAL_INDIVIDUAL,
    "Résidentiel collectif": BuildingUsage.RESIDENTIAL_COLLECTIVE,
    "Tertiaire": BuildingUsage.TERTIARY,
    "Secondaire": BuildingUsage.INDUSTRY,
    "Primaire": BuildingUsage.AGRICULTURE,
    "Dépendance": BuildingUsage.OUTBUILDING,
    "Indifférencié": BuildingUsage.UNDETERMINED,
}


def bdnb_usage(label: str | None) -> BuildingUsage | None:
    """Translate a BDNB ``usage_principal_bdnb_open`` label (SPEC A.2)."""
    return _translate(label, BDNB_USAGES, BuildingUsage, "BDNB usage")


# --- A.3 energy -------------------------------------------------------------------------------

BDNB_ENERGIES: dict[str, EnergyType] = {
    "electricite": _E.ELECTRICITY,
    "gaz": _E.NATURAL_GAS,
    "fioul": _E.FUEL_OIL,
    "gpl/butane/propane": _E.LPG,
    "bois": _E.WOOD,
    "charbon": _E.COAL,
    "reseau de chaleur": _E.DISTRICT_HEATING,
    "reseau de froid": _E.DISTRICT_COOLING,
    "solaire": _E.SOLAR,
    "autre combustible fossile": _E.OTHER_FOSSIL,
}

DPE_ENERGIES: dict[str, EnergyType] = {
    "Électricité": _E.ELECTRICITY,
    "Électricité d'origine renouvelable utilisée dans le bâtiment": _E.ELECTRICITY,
    "Gaz naturel": _E.NATURAL_GAS,
    "Fioul domestique": _E.FUEL_OIL,
    "GPL": _E.LPG,
    "Propane": _E.LPG,
    "Butane": _E.LPG,
    "Bois – Bûches": _E.WOOD_LOGS,
    "Bois – Granulés (pellets) ou briquettes": _E.WOOD_PELLETS,
    "Bois – Plaquettes forestières": _E.WOOD_CHIPS,
    "Bois – Plaquettes d’industrie": _E.WOOD_CHIPS,
    "Charbon": _E.COAL,
    "Réseau de Chauffage urbain": _E.DISTRICT_HEATING,
    "Réseau de Froid Urbain": _E.DISTRICT_COOLING,
    "autre combustible fossile": _E.OTHER_FOSSIL,
}


def bdnb_energy_type(label: str | None) -> EnergyType | None:
    """Translate a BDNB ``type_energie_*`` label (SPEC A.3)."""
    return _translate(label, BDNB_ENERGIES, EnergyType, "BDNB energy")


def dpe_energy_type(label: str | None) -> EnergyType | None:
    """Translate a DPE ``type_energie_*`` label (SPEC A.3)."""
    return _translate(label, DPE_ENERGIES, EnergyType, "DPE energy")


# --- A.4 envelope -----------------------------------------------------------------------------

DPE_INSULATION: dict[str, InsulationLevel] = {
    "très bonne": InsulationLevel.VERY_GOOD,
    "bonne": InsulationLevel.GOOD,
    "moyenne": InsulationLevel.AVERAGE,
    "insuffisante": InsulationLevel.INSUFFICIENT,
}

DPE_INERTIA: dict[str, Inertia] = {
    "Très lourde": Inertia.VERY_HEAVY,
    "Lourde": Inertia.HEAVY,
    "Moyenne": Inertia.MEDIUM,
    "Légère": Inertia.LIGHT,
}

DPE_GLAZING: dict[str, GlazingType] = {
    "Simple vitrage": GlazingType.SINGLE,
    "Double vitrage": GlazingType.DOUBLE,
    "Triple vitrage": GlazingType.TRIPLE,
    "Survitrage": GlazingType.SECONDARY_GLAZING,
    "Brique de Verre": GlazingType.GLASS_BRICK,
    "Polycarbonate": GlazingType.POLYCARBONATE,
}

DPE_FRAMES: dict[str, FrameType] = {
    "Bois": FrameType.WOOD,
    "Bois/métal": FrameType.WOOD_METAL,
    "PVC": FrameType.PVC,
    "Métal avec rupture de pont thermique": FrameType.METAL_THERMAL_BREAK,
    "Métal sans rupture de pont thermique": FrameType.METAL_NO_THERMAL_BREAK,
    "Brique de Verre": FrameType.OTHER,
    "Polycarbonate": FrameType.OTHER,
}


def dpe_insulation(label: str | None) -> InsulationLevel | None:
    """Translate a DPE ``qualite_isolation_*`` label (SPEC A.4)."""
    return _translate(label, DPE_INSULATION, InsulationLevel, "DPE insulation")


def dpe_inertia(label: str | None) -> Inertia | None:
    """Translate a DPE ``classe_inertie_batiment`` label (SPEC A.4)."""
    return _translate(label, DPE_INERTIA, Inertia, "DPE inertia")


def dpe_glazing(label: str | None) -> GlazingType | None:
    """Translate a DPE ``type_vitrage`` label (SPEC A.4)."""
    return _translate(label, DPE_GLAZING, GlazingType, "DPE glazing")


def dpe_frame(label: str | None) -> FrameType | None:
    """Translate a DPE ``type_materiaux_menuiserie`` label (SPEC A.4)."""
    return _translate(label, DPE_FRAMES, FrameType, "DPE frame")


# --- A.5 generators ---------------------------------------------------------------------------

_BDNB_LABEL_ENERGIES: dict[str, EnergyType | None] = {
    "gaz": _E.NATURAL_GAS,
    "fioul": _E.FUEL_OIL,
    "gpl/butane/propane": _E.LPG,
    "bois": _E.WOOD,
    "charbon": _E.COAL,
    "energie indetermine": None,
    "indetermine": None,
}

_BDNB_BOILER_KINDS = {
    "standard": _T.BOILER_STANDARD,
    "basse temperature": _T.BOILER_LOW_TEMPERATURE,
    "condensation": _T.BOILER_CONDENSING,
    "indetermine": _T.BOILER,
}


def _bdnb_generators() -> dict[str, GeneratorMapping]:
    g = GeneratorMapping
    table = {
        f"chaudiere {name} {kind}": g(technology, energy)
        for name, energy in _BDNB_LABEL_ENERGIES.items()
        if name not in ("bois", "indetermine")
        for kind, technology in _BDNB_BOILER_KINDS.items()
    }
    table |= {
        f"poele ou insert {name}": g(_T.STOVE_INSERT, energy)
        for name, energy in _BDNB_LABEL_ENERGIES.items()
        if name != "energie indetermine"
    }
    table |= {
        f"chauffe-eau {name} independant": g(_T.WATER_HEATER, energy)
        for name, energy in _BDNB_LABEL_ENERGIES.items()
        if name in ("gaz", "fioul", "gpl/butane/propane")
    }
    table |= {
        "chaudiere bois": g(_T.BOILER, _E.WOOD),
        "chaudiere electrique": g(_T.ELECTRIC_BOILER, _E.ELECTRICITY),
        "pac air/air": g(_T.HEAT_PUMP_AIR_AIR, _E.ELECTRICITY),
        "pac air/eau": g(_T.HEAT_PUMP_AIR_WATER, _E.ELECTRICITY),
        "pac eau/eau": g(_T.HEAT_PUMP_WATER_WATER, _E.ELECTRICITY),
        "pac geothermique": g(_T.HEAT_PUMP_GROUND, _E.ELECTRICITY),
        "pac gaz": g(_T.HEAT_PUMP_GAS, _E.NATURAL_GAS),
        "pac indetermine": g(_T.HEAT_PUMP, _E.ELECTRICITY),
        "poele bouilleur bois": g(_T.STOVE_INSERT, _E.WOOD),
        "radiateurs gaz": g(_T.GAS_RADIATOR, _E.NATURAL_GAS),
        "generateur air chaud combustion": g(_T.WARM_AIR_GENERATOR, None),
        "generateurs a effet joule": g(_T.ELECTRIC_RESISTANCE, _E.ELECTRICITY),
        "reseau de chaleur": g(_T.DISTRICT_SUBSTATION, _E.DISTRICT_HEATING),
        "reseau de froid": g(_T.DISTRICT_SUBSTATION, _E.DISTRICT_COOLING),
        "chauffage solaire": g(_T.SOLAR_THERMAL, _E.SOLAR),
        "ecs solaire": g(_T.SOLAR_THERMAL, _E.SOLAR),
        "ballon accumulation electrique": g(_T.ELECTRIC_WATER_TANK, _E.ELECTRICITY),
        "chauffe eau electrique instantane": g(_T.INSTANT_WATER_HEATER, _E.ELECTRICITY),
        "ecs thermodynamique electrique(pac ou ballon)": g(
            _T.THERMODYNAMIC_WATER_HEATER, _E.ELECTRICITY
        ),
        "chauffe-eau independant indetermine": g(_T.WATER_HEATER, None),
        "chauffage bois indetermine": g(_T.OTHER, _E.WOOD),
        "ecs bois indetermine": g(_T.OTHER, _E.WOOD),
        "chauffage autre indetermine": g(_T.OTHER, None),
        "ecs autre indetermine": g(_T.OTHER, None),
        "production mixte indetermine": g(_T.OTHER, None),
    }
    return table


BDNB_GENERATORS: dict[str, GeneratorMapping] = _bdnb_generators()
"""BDNB generator labels (``type_generateur_*``) → technology and energy (SPEC A.5)."""


def _generator(
    label: str | None, table: Mapping[str, GeneratorMapping], what: str
) -> GeneratorMapping:
    if label is None or label in EMPTY_LABELS:
        return GeneratorMapping(None, None)
    if label in table:
        return table[label]
    warnings.warn(
        f"unknown {what} generator label {label!r}: translated to OTHER",
        UnmappedValueWarning,
        stacklevel=3,
    )
    return GeneratorMapping(_T.OTHER, None)


def bdnb_generator(label: str | None) -> GeneratorMapping:
    """Translate a BDNB ``type_generateur_*`` label into a technology and an energy (SPEC A.5).

    The energy read in the label is ``None`` when the label does not give it; the matching
    ``type_energie_*`` column, when there is one, takes precedence.
    """
    return _generator(label, BDNB_GENERATORS, "BDNB")


@functools.cache
def dpe_generators() -> dict[str, GeneratorMapping]:
    """Return the DPE generator table read from ``connectors/data/dpe_generators.csv``."""
    text = files("bstock.connectors").joinpath("data", DPE_GENERATORS_FILE).read_text("utf-8")
    lines = (line for line in io.StringIO(text) if not line.startswith("#"))
    return {
        row["label"]: GeneratorMapping(
            GeneratorTechnology(row["technology"]),
            EnergyType(row["energy_type"]) if row["energy_type"] else None,
        )
        for row in csv.DictReader(lines)
    }


def dpe_generator(label: str | None) -> GeneratorMapping:
    """Translate a DPE ``type_generateur_*`` label into a technology and an energy (SPEC A.5).

    The energy is the one implied by the generator, if any; the ``type_energie_generateur_*``
    column takes precedence.
    """
    return _generator(label, dpe_generators(), "DPE")
