import json
from enum import StrEnum

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from bstock import enums

ALL_ENUMS = [getattr(enums, name) for name in enums.__all__]


@pytest.mark.parametrize("enum_cls", ALL_ENUMS)
def test_values_are_lower_case_member_names(enum_cls):
    assert issubclass(enum_cls, StrEnum)
    for member in enum_cls:
        assert member.value == member.name.lower()


@pytest.mark.parametrize("enum_cls", ALL_ENUMS)
def test_member_and_string_are_interchangeable(enum_cls):
    adapter = TypeAdapter(enum_cls)
    for member in enum_cls:
        assert enum_cls(member.value) is member
        assert adapter.validate_python(member.value) is member
        assert adapter.validate_python(member) is member


@pytest.mark.parametrize("enum_cls", ALL_ENUMS)
def test_lookup_ignores_case(enum_cls):
    adapter = TypeAdapter(enum_cls)
    for member in enum_cls:
        assert enum_cls(member.name) is member
        assert adapter.validate_python(member.name) is member
        assert adapter.validate_json(json.dumps(member.name)) is member


def test_unknown_value_is_still_rejected():
    with pytest.raises(ValueError, match="north"):
        enums.Orientation("north")


def test_serialises_to_string_value():
    class Model(BaseModel):
        energy: enums.EnergyType

    model = Model(energy="natural_gas")
    assert model.energy is enums.EnergyType.NATURAL_GAS
    assert json.loads(model.model_dump_json()) == {"energy": "natural_gas"}


def test_unknown_value_is_rejected():
    with pytest.raises(ValidationError):
        TypeAdapter(enums.EnergyType).validate_python("plutonium")


@pytest.mark.parametrize(
    ("enum_cls", "expected"),
    [
        (enums.BuildingUsage, 7),  # BDNB usage_principal_bdnb_open
        (enums.Inertia, 4),  # DPE classe_inertie
        (enums.InsulationLevel, 4),  # DPE qualite_isolation_*
    ],
)
def test_enums_aligned_on_sources_have_expected_size(enum_cls, expected):
    assert len(enum_cls) == expected


# --- families -----------------------------------------------------------------------------------

E, T = enums.EnergyType, enums.GeneratorTechnology


@pytest.mark.parametrize(
    ("member", "family"),
    [
        (T.HEAT_PUMP_AIR_WATER, T.HEAT_PUMP),
        (T.HEAT_PUMP, T.HEAT_PUMP),
        (T.BOILER_CONDENSING, T.BOILER),
        (T.ELECTRIC_WATER_TANK, T.WATER_HEATER),
        (T.ELECTRIC_BOILER, T.ELECTRIC_BOILER),  # not a combustion boiler
        (E.WOOD_PELLETS, E.WOOD),
        (E.NATURAL_GAS, E.NATURAL_GAS),
    ],
)
def test_family_is_the_generic_member(member, family):
    assert member.family is family


def test_variants_of_a_generic_member():
    assert E.WOOD.variants() == {E.WOOD, E.WOOD_LOGS, E.WOOD_PELLETS, E.WOOD_CHIPS}
    assert T.BOILER.variants() == {
        T.BOILER,
        T.BOILER_STANDARD,
        T.BOILER_LOW_TEMPERATURE,
        T.BOILER_CONDENSING,
    }


def test_variants_of_a_precise_member_are_itself():
    assert E.WOOD_LOGS.variants() == {E.WOOD_LOGS}


def test_family_of_a_member_sharing_a_value_with_another_enum():
    # FrameType.WOOD == EnergyType.WOOD as strings: families must not mix enums
    assert enums.FrameType.WOOD.family is enums.FrameType.WOOD
    assert enums.FrameType.WOOD.variants() == {enums.FrameType.WOOD}


def test_economic_sectors_have_explicit_names():
    assert enums.BuildingUsage("industry") is enums.BuildingUsage.INDUSTRY
    with pytest.raises(ValueError, match="secondary"):
        enums.BuildingUsage("secondary")
