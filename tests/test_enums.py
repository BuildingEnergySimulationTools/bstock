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
