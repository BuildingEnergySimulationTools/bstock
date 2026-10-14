import json
import warnings

import pytest
from pydantic import ValidationError

from bstock.building import Building
from bstock.enums import BuildingUsage, Inertia
from bstock.exceptions import (
    DuplicateIdError,
    HierarchyError,
    IdentifierFormatWarning,
    IncompleteDataError,
    UnknownIdError,
    UnknownKindError,
)
from bstock.site import Site
from bstock.systems import HeatingGenerator, SolarPV

RNB_A, RNB_B, RNB_C = "A1B2C3D4E5F6", "B1B2C3D4E5F6", "C1B2C3D4E5F6"
GROUP = "bdnb-bg-1111-1HRT-8SVH"


@pytest.fixture
def tree():
    """client site > BDNB group site > buildings A and B ; building C directly in client site."""
    client = Site(id="site-ecole-A", name="Groupe scolaire A", usage="tertiary", source="client")
    group = client.add_site(Site(id=GROUP, bdnb_batiment_groupe_id=GROUP))
    a = group.add_building(Building(rnb_id=RNB_A))
    b = group.add_building(Building(rnb_id=RNB_B))
    c = client.add_building(Building(rnb_id=RNB_C))
    return client, group, a, b, c


# --- building identity ------------------------------------------------------------------------


def test_building_id_defaults_to_rnb_id():
    assert Building(rnb_id=RNB_A).id == RNB_A


@pytest.mark.parametrize("raw", ["a1b2c3d4e5f6", "A1B2-C3D4-E5F6", " a1b2 c3d4 e5f6 "])
def test_rnb_id_is_normalized(raw):
    building = Building(rnb_id=raw)
    assert building.rnb_id == RNB_A
    assert building.id == RNB_A
    building.rnb_id = raw.replace("a1", "b1").replace("A1", "B1")
    assert building.rnb_id == RNB_B


def test_building_without_rnb_gets_generated_id():
    building = Building()
    assert building.id.startswith("bldg-")
    assert building.rnb_id is None


def test_building_id_is_stable_when_rnb_changes():
    building = Building(rnb_id=RNB_A)
    building.rnb_id = RNB_B
    assert building.id == RNB_A


def test_explicit_id_is_kept():
    assert Building(id="BAY-ecole-neuve").id == "BAY-ecole-neuve"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rnb_id": "not-an-rnb"},
        {"bdnb_batiment_groupe_id": "bg-1111"},
        {"bdnb_batiment_construction_id": "bdnb-bg-1111-1HRT-8SVH"},
        {"dpe_ids": ["123"]},
    ],
)
def test_unexpected_external_id_format_warns(kwargs):
    with pytest.warns(IdentifierFormatWarning):
        Building(**kwargs)


def test_valid_external_ids_do_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Building(
            rnb_id=RNB_A,
            bdnb_batiment_groupe_id=GROUP,
            bdnb_batiment_construction_id="bdnb-bc-KSB3-T85W-GVST",
            dpe_ids=["2611E0031228S"],
        )


def test_id_with_tide_separator_is_rejected():
    with pytest.raises(ValidationError):
        Site(id="a__b")


# --- descriptive attributes -------------------------------------------------------------------


def test_common_attributes_on_site_and_building():
    assert set(Site.sourced_fields()) == set(Building.sourced_fields())
    assert Building.heated_area.unit == "m²"
    assert Building.construction_year.inherit is True
    assert Building.dwelling_count.inherit is False


@pytest.mark.parametrize(
    ("name", "inherited"),
    [
        ("address", True),
        ("usage", True),
        ("construction_year", True),
        ("level_count", True),
        ("height", True),
        ("lat", True),
        ("name", False),
        ("dwelling_count", False),
        ("footprint", False),
        ("footprint_area", False),
        ("floor_area", False),
        ("heated_area", False),
    ],
)
def test_inheritance_flags_follow_spec(name, inherited):
    assert Building.sourced_fields()[name].inherit is inherited


@pytest.mark.parametrize(
    ("name", "value"),
    [("lat", 91.0), ("lon", -181.0), ("level_count", 0), ("construction_year", 50)],
)
def test_out_of_range_values_are_rejected(name, value):
    with pytest.raises(ValidationError):
        Building().set(name, value, source="bdnb")


# --- footprint --------------------------------------------------------------------------------

FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [
        [[-1.475, 43.49], [-1.4745, 43.49], [-1.4745, 43.4905], [-1.475, 43.4905], [-1.475, 43.49]]
    ],
}


@pytest.mark.parametrize("cls", [Site, Building])
def test_footprint_is_a_sourced_geojson_geometry(cls):
    entity = cls(id="e", footprint=FOOTPRINT, source="rnb")
    assert entity.footprint == FOOTPRINT
    assert entity.effective("footprint").source == "rnb"
    assert cls.footprint.unit is None
    assert "WGS84 degrees" in cls.footprint.description


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [-1.475, 43.49]},
        {"type": "Polygon", "coordinates": [FOOTPRINT["coordinates"][0][:-1]]},
        {"type": "Polygon", "coordinates": [[[600, 43], [601, 43], [601, 44], [600, 43]]]},
    ],
)
def test_invalid_footprint_raises_validation_error(geometry):
    with pytest.raises(ValidationError):
        Building().set("footprint", geometry, source="bdnb")


def test_footprint_is_not_inherited(tree):
    client, _group, a, _b, _c = tree
    client.footprint = FOOTPRINT
    assert a.footprint is None


@pytest.mark.parametrize("cls", [Site, Building])
def test_compute_footprint_area_adds_computed_candidate(cls):
    entity = cls(id="e", footprint=FOOTPRINT)
    candidate = entity.compute_footprint_area()
    assert candidate.source == "computed"
    # 0.0005° x 0.0005° at 43.49° N: about 40.4 m x 55.6 m
    assert candidate.value == pytest.approx(2245.6, rel=1e-3)
    assert entity.footprint_area == candidate.value


def test_compute_footprint_area_without_footprint_raises():
    with pytest.raises(IncompleteDataError):
        Building().compute_footprint_area()


def test_computed_footprint_area_wins_over_bdnb_by_default():
    building = Building(footprint=FOOTPRINT)
    building.set("footprint_area", 2000.0, source="bdnb")
    computed = building.compute_footprint_area()
    assert building.footprint_area == computed.value
    assert {c.source for c in building.candidates("footprint_area")} == {"bdnb", "computed"}


# --- hierarchy and inheritance ----------------------------------------------------------------


def test_navigation(tree):
    client, group, a, _b, c = tree
    assert a.parent is group
    assert group.parent is client
    assert client.parent is None
    assert c.parent is client
    assert [s.id for s in client.iter_sites()] == [GROUP]
    assert [x.id for x in client.iter_buildings()] == [RNB_C, RNB_A, RNB_B]
    assert [x.id for x in client.iter_buildings(recursive=False)] == [RNB_C]


def test_building_inherits_through_nested_sites(tree):
    _client, group, a, _, c = tree
    group.set("construction_year", 1972, source="bdnb", ref=GROUP)
    assert a.usage is BuildingUsage.TERTIARY
    assert a.provider_id("usage") == "site-ecole-A"
    assert a.construction_year == 1972
    assert a.provider_id("construction_year") == GROUP
    assert c.construction_year is None  # not under the BDNB group


def test_extensive_values_stay_on_site(tree):
    _, group, a, _, _ = tree
    group.dwelling_count = 48
    group.heated_area = 3200.0
    assert a.dwelling_count is None
    assert a.heated_area is None


def test_names_are_not_inherited(tree):
    _, _, a, _, _ = tree
    assert a.name is None


def test_building_envelope_inherits_from_site_envelope(tree):
    _, group, a, _, _ = tree
    group.envelope.set("u_bat", 1.1, source="dpe", ref="2611E0031228S")
    group.envelope.inertia = Inertia.HEAVY
    assert a.envelope.u_bat == 1.1
    assert a.envelope.provider_id("u_bat") == GROUP
    assert a.envelope.inertia is Inertia.HEAVY


def test_detached_building_stops_inheriting(tree):
    _client, group, a, _, _ = tree
    removed = group.detach(RNB_A)
    assert removed is a
    assert a.parent is None
    assert a.usage is None
    with pytest.raises(UnknownIdError):
        group.detach(RNB_A)


def test_duplicate_child_id_is_rejected(tree):
    _, group, _, _, _ = tree
    with pytest.raises(DuplicateIdError):
        group.add_building(Building(rnb_id=RNB_A))


def test_site_cannot_contain_itself(tree):
    client, group, _, _, _ = tree
    with pytest.raises(HierarchyError, match="inside itself"):
        group.add_site(client)


def test_park_is_none_outside_a_park(tree):
    assert tree[2].park is None


# --- systems ----------------------------------------------------------------------------------


def test_add_and_remove_systems():
    building = Building(rnb_id=RNB_A)
    boiler = building.add_system(HeatingGenerator(id="heating-1"))
    panels = building.add_system(SolarPV(id="pv-1"))
    assert building.heating == [boiler]
    assert building.pv == [panels]
    assert boiler._container is building
    assert [s.id for s in building.iter_systems()] == ["heating-1", "pv-1"]
    with pytest.raises(DuplicateIdError):
        building.add_system(HeatingGenerator(id="heating-1"))
    assert building.remove_system("heating-1") is boiler
    assert building.heating == []
    with pytest.raises(UnknownIdError):
        building.remove_system("heating-1")


def test_shared_systems_serve_buildings(tree):
    client, group, a, b, c = tree
    plant = client.add_system(HeatingGenerator(id="heating-chaufferie"))  # serves everything
    group_boiler = group.add_system(HeatingGenerator(id="heating-group", serves=[RNB_A]))
    own = b.add_system(HeatingGenerator(id="heating-b"))
    assert a.serving_systems("heating") == [group_boiler, plant]
    assert b.serving_systems("heating") == [own, plant]
    assert c.serving_systems("heating") == [plant]
    assert b.serving_systems("heating", include_shared=False) == [own]
    assert a.serving_systems("pv") == []


def test_unknown_system_kind_is_rejected():
    with pytest.raises(UnknownKindError, match="unknown system kind"):
        Building().serving_systems("geothermal")


def test_duplicate_system_ids_rejected_at_construction():
    with pytest.raises(DuplicateIdError):
        Building(heating=[HeatingGenerator(id="h"), HeatingGenerator(id="h")])


# --- serialisation ----------------------------------------------------------------------------


def test_json_round_trip_rebuilds_tree(tree):
    client, group, _a, _, _ = tree
    group.set("construction_year", 1972, source="bdnb")
    group.envelope.set("u_bat", 1.1, source="dpe")
    group.add_system(HeatingGenerator(id="heating-group", serves=[RNB_A], technology="chiller"))
    data = json.loads(client.model_dump_json(exclude_none=True))
    loaded = Site.model_validate(data)
    assert loaded == client
    loaded_a = next(x for x in loaded.iter_buildings() if x.id == RNB_A)
    assert loaded_a.construction_year == 1972
    assert loaded_a.envelope.u_bat == 1.1
    assert [s.id for s in loaded_a.serving_systems("heating")] == ["heating-group"]
    assert loaded_a.usage is BuildingUsage.TERTIARY


def test_footprint_json_round_trip():
    building = Building(rnb_id=RNB_A, footprint=FOOTPRINT, source="rnb")
    building.compute_footprint_area()
    loaded = Building.model_validate_json(building.model_dump_json())
    assert loaded.footprint == FOOTPRINT
    assert loaded.candidates("footprint_area") == building.candidates("footprint_area")
