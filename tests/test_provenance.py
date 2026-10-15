import copy
import datetime
import json
import pickle
import warnings

import pytest
from pydantic import BaseModel, ValidationError

import bstock
from bstock.enums import EnergyType
from bstock.exceptions import InvalidArgumentError, UnknownFieldError, UnknownFieldWarning
from bstock.provenance import (
    COMPUTED_SOURCE,
    DEFAULT_SOURCE_PRIORITY,
    MANUAL_SOURCE,
    WILDCARD_SOURCE,
    SourcedModel,
    SourcedValue,
    SourcePriority,
    sourced,
)


class Thing(SourcedModel):
    id: str = "thing"
    area = sourced(float, unit="m²", ge=0, description="Some area")
    year = sourced(int, inherit=True)
    energy = sourced(EnergyType)


class ProjectThing(Thing):
    code = sourced(str)


class Holder(BaseModel):
    """Stands for the park: anything with a ``source_priority`` attribute."""

    source_priority: SourcePriority = SourcePriority()


# --- declaration ------------------------------------------------------------------------------


def test_class_access_returns_field_definition():
    assert Thing.area.unit == "m²"
    assert Thing.year.inherit is True
    assert Thing.area.description == "Some area"


def test_sourced_fields_include_subclass_fields():
    assert set(Thing.sourced_fields()) == {"area", "year", "energy"}
    assert set(ProjectThing.sourced_fields()) == {"area", "year", "energy", "code"}


def test_sourced_fields_are_not_pydantic_fields():
    assert set(Thing.model_fields) == {"id", "values"}


def test_reserved_name_is_rejected():
    with pytest.raises(TypeError, match="reserved"):

        class Bad(SourcedModel):
            candidates = sourced(int)


# --- set / get / unset ------------------------------------------------------------------------


def test_unset_attribute_is_none():
    assert Thing().area is None
    assert Thing().effective("area") is None
    assert Thing().candidates("area") == []


def test_direct_assignment_uses_manual_source():
    thing = Thing()
    thing.area = 12.5
    assert thing.area == 12.5
    assert thing.effective("area").source == "manual"


def test_assigning_none_removes_manual_candidate():
    thing = Thing()
    thing.set("area", 10.0, source="bdnb")
    thing.area = 12.5
    thing.area = None
    assert thing.area == 10.0
    assert [c.source for c in thing.candidates("area")] == ["bdnb"]


def test_set_keeps_provenance():
    thing = Thing()
    stored = thing.set(
        "year",
        1972,
        source="dpe",
        date=datetime.date(2023, 4, 1),
        comment="from DPE",
        ref="2364E0123456X",
        raw="1972",
    )
    assert stored == SourcedValue(
        value=1972,
        source="dpe",
        date=datetime.date(2023, 4, 1),
        comment="from DPE",
        ref="2364E0123456X",
        raw="1972",
    )
    assert thing.effective("year") == stored


def test_one_candidate_per_source():
    thing = Thing()
    thing.set("area", 10.0, source="bdnb")
    thing.set("area", 11.0, source="bdnb")
    assert [c.value for c in thing.candidates("area")] == [11.0]


def test_set_none_unsets_that_source():
    thing = Thing()
    thing.set("area", 10.0, source="bdnb")
    assert thing.set("area", None, source="bdnb") is None
    assert thing.candidates("area") == []


def test_unset_all_sources():
    thing = Thing()
    thing.set("area", 10.0, source="bdnb")
    thing.set("area", 11.0, source="dpe")
    thing.unset("area")
    assert thing.area is None


def test_values_are_validated_and_coerced():
    thing = Thing()
    thing.set("year", "1972", source="bdnb")
    assert thing.year == 1972
    thing.energy = "natural_gas"
    assert thing.energy is EnergyType.NATURAL_GAS


@pytest.mark.parametrize(
    ("name", "value"),
    [("area", -1.0), ("year", "nineteen seventy"), ("energy", "plutonium")],
)
def test_invalid_values_are_rejected(name, value):
    with pytest.raises(ValidationError):
        Thing().set(name, value, source="bdnb")


@pytest.mark.parametrize("method", ["set", "unset", "candidates", "effective"])
def test_unknown_attribute_name_is_rejected(method):
    thing = Thing()
    args = ("nope", 1) if method == "set" else ("nope",)
    with pytest.raises(UnknownFieldError):
        getattr(thing, method)(*args)


def test_subclass_fields_behave_like_base_fields():
    thing = ProjectThing(code="BAT-0042", source="bayonne_xlsx")
    assert thing.code == "BAT-0042"
    assert thing.effective("code").source == "bayonne_xlsx"


# --- construction -----------------------------------------------------------------------------


def test_constructor_kwargs_default_to_manual_source():
    thing = Thing(area=10.0, year=1972)
    assert thing.effective("area").source == "manual"
    assert thing.year == 1972


def test_constructor_source_applies_to_all_kwargs():
    thing = Thing(area=10.0, year=1972, source="bdnb")
    assert {c.source for name in ("area", "year") for c in thing.candidates(name)} == {"bdnb"}


def test_constructor_ignores_none_values():
    thing = Thing(area=None, year=1972)
    assert thing.candidates("area") == []


def test_constructor_accepts_sourced_value_instances():
    candidate = SourcedValue(value=1972, source="dpe", ref="X")
    thing = Thing(year=candidate, area=10.0, source="bdnb")
    assert thing.effective("year") == candidate
    assert thing.effective("area").source == "bdnb"


def test_constructor_validates_values():
    with pytest.raises(ValidationError):
        Thing(area=-5.0)


def test_unknown_constructor_kwarg_is_rejected():
    with pytest.raises(ValidationError):
        Thing(not_a_field=1)


# --- serialisation ----------------------------------------------------------------------------


def test_json_round_trip_keeps_all_candidates():
    thing = Thing()
    thing.set("year", 1970, source="bdnb", date=datetime.date(2024, 10, 1))
    thing.set("year", 1972, source="dpe", ref="2364E0123456X")
    thing.energy = EnergyType.NATURAL_GAS
    data = json.loads(thing.model_dump_json(exclude_none=True))
    assert data["values"]["energy"] == [{"value": "natural_gas", "source": "manual"}]
    loaded = Thing.model_validate(data)
    assert loaded == thing
    assert loaded.energy is EnergyType.NATURAL_GAS


def test_loaded_values_are_validated():
    with pytest.raises(ValidationError):
        Thing.model_validate({"values": {"area": [{"value": -1, "source": "bdnb"}]}})


def test_unknown_loaded_attribute_is_kept_with_warning():
    data = {"values": {"code": [{"value": "BAT-0042", "source": "bayonne_xlsx"}]}}
    with pytest.warns(UnknownFieldWarning):
        thing = Thing.model_validate(data)
    assert thing.candidates("code")[0].value == "BAT-0042"
    assert json.loads(thing.model_dump_json(exclude_none=True)) == {"id": "thing", **data}


def test_known_loaded_attributes_do_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Thing.model_validate({"values": {"area": [{"value": 1, "source": "bdnb"}]}})


# --- priority ---------------------------------------------------------------------------------


def candidates(*specs):
    return [
        SourcedValue(value=i, source=source, date=date) for i, (source, date) in enumerate(specs)
    ]


def test_default_priority_order():
    chosen = DEFAULT_SOURCE_PRIORITY.select(
        "area", candidates(("bdnb", None), ("audit", None), ("dpe", None))
    )
    assert chosen.source == "audit"


def test_unlisted_source_takes_wildcard_rank():
    priority = SourcePriority()
    chosen = priority.select("area", candidates(("assumption", None), ("bayonne_xlsx", None)))
    assert chosen.source == "bayonne_xlsx"
    chosen = priority.select("area", candidates(("bayonne_xlsx", None), ("rnb", None)))
    assert chosen.source == "rnb"


def test_unlisted_source_without_wildcard_comes_last():
    priority = SourcePriority(default=["dpe", "bdnb"])
    chosen = priority.select("area", candidates(("client", None), ("bdnb", None)))
    assert chosen.source == "bdnb"


def test_per_attribute_override():
    priority = SourcePriority(per_field={"area": ["bayonne_xlsx", "audit"]})
    specs = (("audit", None), ("bayonne_xlsx", None))
    assert priority.select("area", candidates(*specs)).source == "bayonne_xlsx"
    assert priority.select("year", candidates(*specs)).source == "audit"


def test_tie_broken_by_most_recent_date_then_last_added():
    priority = SourcePriority(default=["a", "b", "*"])
    old, new = datetime.date(2020, 1, 1), datetime.date(2024, 1, 1)
    # "a" and "b" never tie, so use two unlisted sources sharing the wildcard rank
    chosen = priority.select("area", candidates(("x", new), ("y", old)))
    assert chosen.source == "x"
    chosen = priority.select("area", candidates(("x", None), ("y", old)))
    assert chosen.source == "y"
    chosen = priority.select("area", candidates(("x", None), ("y", None)))
    assert chosen.source == "y"


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda p: setattr(p, "default", ("dpe",)), ValidationError),
        (lambda p: p.default.append("dpe"), AttributeError),
        (lambda p: p.per_field.__setitem__("area", ("dpe",)), TypeError),
        (lambda p: p.per_field.update(area=("dpe",)), TypeError),
    ],
)
def test_source_priority_is_immutable(mutate, error):
    priority = SourcePriority(per_field={"year": ["bdnb"]})
    with pytest.raises(error):
        mutate(priority)
    assert priority == SourcePriority(per_field={"year": ["bdnb"]})


def test_source_priority_copies_pickles_and_serialises():
    priority = SourcePriority(per_field={"year": ["bdnb", "dpe"]})
    assert copy.deepcopy(priority) == priority
    assert pickle.loads(pickle.dumps(priority)) == priority
    assert json.loads(priority.model_dump_json())["per_field"] == {"year": ["bdnb", "dpe"]}
    assert SourcePriority.model_validate_json(priority.model_dump_json()) == priority


def test_reserved_source_names_are_used_by_default():
    assert DEFAULT_SOURCE_PRIORITY.default[0] == MANUAL_SOURCE
    assert COMPUTED_SOURCE in DEFAULT_SOURCE_PRIORITY.default
    assert WILDCARD_SOURCE in DEFAULT_SOURCE_PRIORITY.default


def test_no_candidate_selects_none():
    assert DEFAULT_SOURCE_PRIORITY.select("area", []) is None


def test_container_priority_is_used():
    holder = Holder(source_priority=SourcePriority(default=["bdnb", "audit"]))
    thing = Thing()
    thing.set("area", 10.0, source="bdnb")
    thing.set("area", 20.0, source="audit")
    assert thing.area == 20.0
    thing._container = holder
    assert thing.area == 10.0
    assert thing.active_source_priority() is holder.source_priority


def test_equality_ignores_container():
    first, second = Thing(area=1.0), Thing(area=1.0)
    first._container = Holder()
    assert first == second


def test_extensive_attribute_cannot_be_inherited():
    with pytest.raises(InvalidArgumentError, match="extensive"):
        sourced(float, extensive=True, inherit=True)


@pytest.mark.parametrize(
    ("cls", "name", "extensive"),
    [
        ("Building", "heated_area", True),
        ("Building", "dwelling_count", True),
        ("Building", "footprint_area", True),
        ("Building", "floor_area", True),
        ("Building", "footprint", False),
        ("Building", "name", False),
        ("Building", "construction_year", False),
        ("Envelope", "loss_area", True),
        ("Envelope", "heat_loss_coefficient", True),
        ("Envelope", "u_bat", False),
        ("HeatingGenerator", "nominal_power", True),
        ("SolarPV", "peak_power", True),
    ],
)
def test_extensive_flag_of_core_attributes(cls, name, extensive):
    assert getattr(bstock, cls).sourced_fields()[name].extensive is extensive


def test_deep_copy_is_detached_from_its_owner():
    from bstock import BuildingPark

    park = BuildingPark(name="Park")
    building = park.new_building(id="b1", parent=park.new_site("s1"))
    copied = building.model_copy(deep=True)
    assert copied.parent is None
    assert copied.park is None
    assert building.parent is park["s1"]


def test_deep_copy_of_park_keeps_links_inside_the_copy():
    from bstock import BuildingPark

    park = BuildingPark(name="Park")
    park.new_building(id="b1", parent=park.new_site("s1"))
    copied = park.model_copy(deep=True)
    assert copied["b1"].parent is copied["s1"]
    assert copied["s1"].park is copied


def test_deep_copy_of_park_is_independent():
    from bstock import BuildingPark

    park = BuildingPark(name="Park")
    park.new_building(id="b1", parent=park.new_site("s1", construction_year=1970))
    copied = park.model_copy(deep=True)
    copied["s1"].construction_year = 1990
    assert copied["b1"].construction_year == 1990
    assert park["b1"].construction_year == 1970
    assert copied["b1"] is copied["s1"].buildings[0]
