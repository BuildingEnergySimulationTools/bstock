import math
import warnings

import pandas as pd
import pytest

from bstock.analysis import PARK_ROW
from bstock.building import Building
from bstock.exceptions import InvalidArgumentError, NoDataWarning, UnknownFieldError
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.provenance import sourced
from bstock.systems import HeatingGenerator, SolarPV

HEATED = "heated_area"


@pytest.fixture
def park():
    """Heated areas: res-x 3000 (b1 1000, b2 1200, b3 700); campus > lab 900 (l1 500, l2 -),
    campus > c1 300; b4 400 at the top level.
    """
    park = BuildingPark(name="P")
    res = park.new_site("res-x", heated_area=3000.0)
    park.new_building(id="b1", parent=res, heated_area=1000.0, usage="residential_collective")
    park.new_building(id="b2", parent=res, heated_area=1200.0, usage="residential_collective")
    park.new_building(id="b3", parent=res, heated_area=700.0)
    campus = park.new_site("campus")
    lab = park.new_site("lab", parent=campus, heated_area=900.0)
    park.new_building(id="l1", parent=lab, heated_area=500.0, usage="tertiary")
    park.new_building(id="l2", parent=lab, usage="tertiary")
    park.new_building(id="c1", parent=campus, heated_area=300.0, usage="tertiary")
    park.new_building(id="b4", heated_area=400.0)
    park.add_system(HeatingGenerator(id="h-res", nominal_power=300.0), owner="res-x")
    park.add_system(HeatingGenerator(id="h-lab", nominal_power=100.0, serves=["l1"]), owner="lab")
    park.add_system(HeatingGenerator(id="h-b4", nominal_power=50.0), owner="b4")
    park.add_system(HeatingGenerator(id="h-b4-2"), owner="b4")
    park.add_system(SolarPV(id="pv-c1", peak_power=9.0), owner="c1")
    park.add_meter(Meter(id="m-res", scope=["res-x"], channels=[Channel(name="e", unit="kWh")]))
    park.add_meter(Meter(id="m-l1", scope=["l1"], channels=[Channel(name="e", unit="kWh")]))
    return park


def test_one_row_per_site_then_the_park_total(park):
    frame = park.select().aggregate([HEATED])
    assert list(frame.index) == ["res-x", "campus", "lab", PARK_ROW]
    assert frame.columns.names == ["quantity", "measure"]


def test_site_value_is_shown_next_to_the_sum_of_its_buildings(park):
    row = park.select().aggregate([HEATED]).loc["res-x", HEATED]
    assert (row["buildings"], row["site"], row["delta"], row["coverage"]) == (2900, 3000, 100, 1)


@pytest.mark.parametrize(
    ("prefer", "totals"),
    [
        ("buildings", {"res-x": 2900, "lab": 500, "campus": 800, PARK_ROW: 4100}),
        ("site", {"res-x": 3000, "lab": 900, "campus": 1200, PARK_ROW: 4600}),
    ],
)
def test_total_never_counts_a_building_twice(park, prefer, totals):
    frame = park.select().aggregate([HEATED], prefer=prefer)
    assert frame[(HEATED, "total")].to_dict() == totals


def test_partial_coverage_is_not_extrapolated(park):
    frame = park.select().aggregate([HEATED])
    assert frame.loc["lab", (HEATED, "buildings")] == 500
    assert frame.loc["lab", (HEATED, "coverage")] == 0.5
    assert frame.loc["campus", (HEATED, "coverage")] == pytest.approx(2 / 3)
    assert frame.loc[PARK_ROW, (HEATED, "coverage")] == pytest.approx(6 / 7)


def test_site_value_does_not_enter_the_total_when_part_of_its_buildings_is_selected(park):
    frame = park.select(within="res-x", heated_area__lt=1100).aggregate([HEATED], prefer="site")
    assert frame.loc["res-x", (HEATED, "site")] == 3000
    assert frame.loc["res-x", (HEATED, "total")] == 1700
    assert frame.loc[PARK_ROW, (HEATED, "total")] == 1700


def test_only_sites_containing_selected_buildings_get_a_row(park):
    frame = park.select(lambda b: b.id in ("b4", "c1")).aggregate([HEATED])
    assert list(frame.index) == ["campus", PARK_ROW]
    assert frame.loc[PARK_ROW, (HEATED, "total")] == 700


def test_selection_of_sites_counts_their_whole_subtree(park):
    frame = park.select(kind="site", parent=None).aggregate([HEATED], prefer="site")
    assert list(frame.index) == ["res-x", "campus", PARK_ROW]
    assert frame.loc["campus", ("building_count", "value")] == 3
    assert frame.loc[PARK_ROW, (HEATED, "total")] == 4200


def test_nested_selected_sites_are_totalled_once(park):
    frame = park.select(kind="site", within="campus").aggregate([HEATED])
    assert list(frame.index) == ["lab", PARK_ROW]
    assert frame.loc[PARK_ROW, (HEATED, "total")] == 500
    both = park.select(kind="site", has_system=True) | park.select(kind="site", within="campus")
    assert both.aggregate([HEATED]).loc[PARK_ROW, (HEATED, "total")] == 2900 + 500


def test_system_powers_count_each_system_once_on_its_owner(park):
    frame = park.select().aggregate([HEATED])
    assert frame.loc["res-x", ("heating_power", "value")] == 300
    assert frame.loc[PARK_ROW, ("heating_power", "value")] == 450
    assert frame.loc[PARK_ROW, ("heating_power", "coverage")] == 0.75
    assert frame.loc[PARK_ROW, ("heating_count", "value")] == 4
    assert frame.loc["campus", ("pv_peak_power", "value")] == 9
    assert math.isnan(frame.loc["res-x", ("cooling_power", "value")])


def test_site_system_counts_only_if_it_serves_a_selected_building(park):
    frame = park.select(lambda b: b.id == "l2").aggregate([HEATED])
    assert frame.loc["lab", ("heating_count", "value")] == 0
    assert math.isnan(frame.loc["lab", ("heating_power", "value")])


def test_counts_of_buildings_and_meters(park):
    frame = park.select().aggregate([HEATED])
    assert frame.loc["res-x", ("building_count", "value")] == 3
    assert frame.loc["res-x", ("meter_count", "value")] == 1
    assert frame.loc[PARK_ROW, ("building_count", "value")] == 7
    assert frame.loc[PARK_ROW, ("meter_count", "value")] == 2


def test_every_extensive_attribute_by_default_project_ones_included():
    class ProjectBuilding(Building):
        rooms = sourced(int, extensive=True)

    park = BuildingPark(name="P", building_cls=ProjectBuilding)
    park.new_building(id="a", rooms=3)
    park.new_building(id="b", rooms=4)
    frame = park.select().aggregate()
    quantities = list(dict.fromkeys(frame.columns.get_level_values("quantity")))
    assert quantities[:5] == ["dwelling_count", "footprint_area", "floor_area", HEATED, "rooms"]
    assert frame.loc[PARK_ROW, ("rooms", "total")] == 7


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"fields": ["construction_year"]}, InvalidArgumentError),
        ({"fields": ["colour"]}, UnknownFieldError),
        ({"prefer": "both"}, InvalidArgumentError),
        ({"by": "sub_usages"}, InvalidArgumentError),
        ({"by": "colour"}, UnknownFieldError),
    ],
)
def test_invalid_arguments_are_refused(park, kwargs, error):
    park["b1"].sub_usages = ["housing"]
    with pytest.raises(error):
        park.select().aggregate(**kwargs)


def test_by_groups_buildings_by_effective_value_missing_last(park):
    park["res-x"].unset(HEATED)
    park["lab"].unset(HEATED)
    frame = park.select().aggregate([HEATED], by="usage")
    assert frame.index.name == "usage"
    assert list(frame.index[:2]) == ["residential_collective", "tertiary"]
    assert frame.index[2] is pd.NA
    assert frame[(HEATED, "buildings")].tolist() == [2200, 800, 1100]
    assert frame[("building_count", "value")].tolist() == [2, 3, 2]
    assert frame.loc["tertiary", (HEATED, "coverage")] == pytest.approx(2 / 3)


def test_by_warns_that_site_values_are_ignored(park):
    with pytest.warns(NoDataWarning, match="'lab', 'res-x'"):
        park.select().aggregate([HEATED], by="usage")


def test_by_does_not_warn_without_site_values(park):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        park.select(within="campus").aggregate(["floor_area"], by="usage")


def test_by_applies_to_buildings_only(park):
    with pytest.raises(InvalidArgumentError, match="buildings"):
        park.select(kind="site").aggregate(by="usage")


def test_aggregation_does_not_modify_the_park(park):
    before = park.to_json()
    park.select().aggregate(prefer="site")
    assert park.to_json() == before
