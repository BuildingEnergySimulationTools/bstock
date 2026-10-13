import json
import warnings

import pytest
from pydantic import ValidationError

from bstock.building import Building
from bstock.enums import EnergyType, GeneratorTechnology, SystemKind
from bstock.exceptions import PerformanceRatioWarning
from bstock.systems import (
    SYSTEM_CLASSES,
    CoolingGenerator,
    DHWGenerator,
    HeatingGenerator,
    SolarPV,
    Ventilation,
)


@pytest.fixture
def no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        yield


# --- identity ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cls", list(SYSTEM_CLASSES.values()))
def test_generated_id_uses_kind_prefix(cls):
    system = cls()
    assert system.id.startswith(f"{cls.kind}-")
    assert cls().id != system.id


@pytest.mark.parametrize(
    ("cls", "kind"),
    [
        (HeatingGenerator, "heating"),
        (CoolingGenerator, "cooling"),
        (DHWGenerator, "dhw"),
        (Ventilation, "ventilation"),
        (SolarPV, "pv"),
    ],
)
def test_kind_is_a_system_kind(cls, kind):
    assert cls.kind is SystemKind(kind)
    assert cls().id.startswith(f"{kind}-")


@pytest.mark.parametrize("kind", [SystemKind.HEATING, "heating"])
def test_systems_filter_accepts_member_or_string(kind):
    building = Building()
    boiler = building.add_system(HeatingGenerator())
    building.add_system(SolarPV())
    assert building.systems(kind) == [boiler]
    assert list(building.iter_systems(kind)) == [boiler]


def test_given_id_is_kept():
    assert HeatingGenerator(id="heating-chaufferie-A").id == "heating-chaufferie-A"


@pytest.mark.parametrize("bad", ["a__b"])
def test_ids_cannot_contain_tide_separator(bad):
    with pytest.raises(ValidationError):
        HeatingGenerator(id=bad)
    with pytest.raises(ValidationError):
        HeatingGenerator(serves=[bad])


def test_system_classes_by_kind():
    assert set(SYSTEM_CLASSES) == {"heating", "cooling", "dhw", "ventilation", "pv"}


# --- attributes -------------------------------------------------------------------------------


def test_generator_attributes_and_provenance(no_warning):
    generator = HeatingGenerator(
        id="heating-chaufferie-A",
        technology="boiler_condensing",
        energy_type="natural_gas",
        nominal_power=450.0,
        seasonal_performance_ratio=0.95,
        source="audit",
    )
    assert generator.technology is GeneratorTechnology.BOILER_CONDENSING
    assert generator.energy_type is EnergyType.NATURAL_GAS
    assert generator.nominal_power == 450.0
    assert generator.effective("nominal_power").source == "audit"


def test_units_are_declared():
    assert HeatingGenerator.nominal_power.unit == "kW"
    assert DHWGenerator.storage_volume.unit == "L"
    assert Ventilation.air_change_rate.unit == "1/h"
    assert SolarPV.peak_power.unit == "kWp"


@pytest.mark.parametrize(
    ("cls", "name", "value"),
    [
        (HeatingGenerator, "nominal_power", -1.0),
        (HeatingGenerator, "seasonal_performance_ratio", 0.0),
        (Ventilation, "heat_recovery_efficiency", 1.2),
        (SolarPV, "tilt", 95.0),
        (SolarPV, "azimuth", 400.0),
        (DHWGenerator, "storage_volume", -5.0),
    ],
)
def test_out_of_range_values_are_rejected(cls, name, value):
    with pytest.raises(ValidationError):
        cls().set(name, value, source="audit")


def test_only_dhw_has_storage_volume():
    assert "storage_volume" in DHWGenerator.sourced_fields()
    assert "storage_volume" not in HeatingGenerator.sourced_fields()


# --- performance ratio checks -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("technology", "ratio"),
    [
        ("boiler_condensing", 1.3),
        ("electric_resistance", 2.0),
        ("district_substation", 1.5),
        ("heat_pump_air_water", 0.9),
        ("chiller", 0.5),
        ("thermodynamic_water_heater", 0.8),
    ],
)
def test_inconsistent_ratio_warns(technology, ratio):
    with pytest.warns(PerformanceRatioWarning):
        HeatingGenerator(technology=technology, seasonal_performance_ratio=ratio)


@pytest.mark.parametrize(
    ("technology", "ratio"),
    [
        ("boiler_condensing", 1.05),
        ("boiler_standard", 0.85),
        ("heat_pump_air_water", 3.2),
        ("heat_pump_hybrid", 0.9),  # hybrids are not checked
        ("solar_thermal", 5.0),  # not checked either
    ],
)
def test_consistent_ratio_does_not_warn(technology, ratio, no_warning):
    HeatingGenerator(technology=technology, nominal_performance_ratio=ratio)


def test_check_runs_when_values_change_later():
    generator = CoolingGenerator(seasonal_performance_ratio=0.8)  # no technology yet: no check
    with pytest.warns(PerformanceRatioWarning, match="below 1"):
        generator.technology = "chiller"
    with pytest.warns(PerformanceRatioWarning):
        generator.set("nominal_performance_ratio", 0.7, source="audit")


def test_check_runs_when_a_candidate_is_removed():
    generator = HeatingGenerator(seasonal_performance_ratio=0.9)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        generator.set("technology", "heat_pump_air_water", source="dpe")
        generator.set("technology", "boiler_condensing", source="manual")
    with pytest.warns(PerformanceRatioWarning, match="below 1"):
        generator.unset("technology", source="manual")  # the heat pump becomes effective


def test_check_returns_messages():
    with pytest.warns(PerformanceRatioWarning):
        generator = HeatingGenerator(technology="boiler_standard", nominal_performance_ratio=2.0)
    with pytest.warns(PerformanceRatioWarning):
        messages = generator.check_performance_ratios()
    assert len(messages) == 1
    assert "nominal_performance_ratio" in messages[0]


# --- serialisation ----------------------------------------------------------------------------


def test_json_round_trip(no_warning):
    generator = DHWGenerator(
        id="dhw-1",
        technology="electric_water_tank",
        energy_type="electricity",
        storage_volume=200.0,
        serves=["A1B2C3D4E5F6"],
        source="dpe",
    )
    data = json.loads(generator.model_dump_json(exclude_none=True))
    assert data["serves"] == ["A1B2C3D4E5F6"]
    assert DHWGenerator.model_validate(data) == generator
