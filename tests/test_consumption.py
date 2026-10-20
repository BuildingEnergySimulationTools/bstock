import math
import warnings

import pandas as pd
import pytest

from bstock.exceptions import (
    InvalidArgumentError,
    NoDataWarning,
    TimeSeriesError,
    UnknownFieldError,
)
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.readers import CsvReader
from bstock.systems import HeatingGenerator

YEAR = {"start": "2024-01-01", "end": "2025-01-01"}
DAYS_2024 = 366


def add_meter(
    park,
    meter_id,
    scope,
    value=1.0,
    *,
    unit="kWh",
    channel="energy",
    energy_type="natural_gas",
    end_use=None,
    parent=None,
    timestep="1D",
    index=None,
):
    """Add a meter with one channel and a constant value per day of 2024 (or per ``index``)."""
    meter = park.add_meter(
        Meter(
            id=meter_id,
            scope=scope,
            energy_type=energy_type,
            end_use=end_use,
            parent_meter=parent,
            timestep=timestep,
            channels=[Channel(name=channel, unit=unit)],
        )
    )
    if index is None:
        index = pd.date_range("2024-01-01", "2024-12-31", freq="D", tz="Europe/Paris")
    values = value if isinstance(value, list) else [value] * len(index)
    meter.data = pd.DataFrame({channel: values}, index=index)
    return meter


@pytest.fixture
def park():
    """Site s (b1 1000 m², b2 500 m², shared boiler) and building b3 (no area) at the top level."""
    park = BuildingPark(name="P")
    site = park.new_site("s")
    park.new_building(id="b1", parent=site, heated_area=1000.0)
    park.new_building(id="b2", parent=site, heated_area=500.0)
    park.new_building(id="b3")
    park.add_system(HeatingGenerator(id="boiler"), owner=site)
    return park


def rows(frame):
    return {(r.entity, r.end_use): r.energy for r in frame.itertuples()}


def test_energy_channel_is_summed_over_the_period(park):
    add_meter(park, "gas", ["s"], 100.0, end_use="total")
    frame = park.select().consumption(**YEAR)
    assert list(frame.columns) == [
        "entity",
        "energy_type",
        "end_use",
        "energy",
        "coverage",
        "estimated",
        "meters",
    ]
    row = frame.iloc[0]
    assert (row.entity, row.energy_type, row.end_use) == ("s", "natural_gas", "total")
    assert (row.energy, row.coverage, row.estimated, row.meters) == (36600, 1, False, ["gas"])


@pytest.mark.parametrize(("unit", "factor"), [("Wh", 1e-3), ("kWh", 1), ("MWh", 1e3), ("GWh", 1e6)])
def test_energy_units_are_converted_to_kwh(park, unit, factor):
    add_meter(park, "m", ["b1"], 2.0, unit=unit)
    assert park.select().consumption(**YEAR).energy.iloc[0] == pytest.approx(2 * DAYS_2024 * factor)


def test_other_energy_unit_is_refused(park):
    add_meter(park, "m", ["b1"], unit="W")
    with pytest.raises(TimeSeriesError, match="'W'"):
        park.select().consumption(**YEAR)


def test_power_channel_is_not_integrated(park):
    add_meter(park, "m", ["b1"], channel="power", unit="W")
    with pytest.warns(NoDataWarning, match="no 'energy' or 'index' channel"):
        assert park.select().consumption(**YEAR).empty


def test_sensor_without_energy_type_is_ignored_silently(park):
    add_meter(park, "t", ["b1"], 20.0, channel="temperature", unit="°C", energy_type=None)
    add_meter(park, "m", ["b1"], 1.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        frame = park.select().consumption(**YEAR)
    assert frame["meters"].tolist() == [["m"]]


def test_daily_coverage_uses_the_timestep_with_a_single_sample(park):
    index = pd.DatetimeIndex(["2024-06-01"]).tz_localize("Europe/Paris")
    add_meter(park, "m", ["b1"], 5.0, index=index)
    row = park.select().consumption(**YEAR).iloc[0]
    assert row.coverage == pytest.approx(1 / DAYS_2024)


def test_index_channel_gives_last_minus_first_reading(park):
    index = pd.date_range("2024-01-01", "2024-12-31", freq="MS", tz="Europe/Paris")
    add_meter(park, "m", ["b1"], [100.0 * i for i in range(12)], channel="index", index=index)
    row = park.select().consumption(**YEAR).iloc[0]
    assert row.energy == 1100
    # 1 January to 1 December: 335 days (the summer time hour is lost in March, back in October)
    assert row.coverage == pytest.approx(335 / 366)


def test_index_going_down_gives_a_missing_value(park):
    index = pd.date_range("2024-01-01", periods=3, freq="MS", tz="Europe/Paris")
    add_meter(park, "m", ["b1"], [500.0, 600.0, 10.0], channel="index", index=index)
    with pytest.warns(NoDataWarning, match="index going down"):
        row = park.select().consumption(**YEAR).iloc[0]
    assert math.isnan(row.energy)


def test_partial_coverage_is_not_extrapolated(park):
    index = pd.date_range("2024-01-01", periods=183, freq="D", tz="Europe/Paris")
    add_meter(park, "m", ["b1"], 1.0, index=index)
    row = park.select().consumption(**YEAR).iloc[0]
    assert (row.energy, row.coverage) == (183, pytest.approx(183 / 366))


def test_timestep_defaults_to_the_median_gap(park):
    add_meter(park, "m", ["b1"], 1.0, timestep=None)
    assert park.select().consumption(**YEAR).coverage.iloc[0] == pytest.approx(1)


def test_monthly_periods_follow_the_local_calendar_across_daylight_saving(park):
    index = pd.date_range("2024-03-01", "2024-04-30 23:00", freq="h", tz="Europe/Paris")
    add_meter(park, "m", ["b1"], 1.0, timestep="1h", index=index.tz_convert("UTC"))
    frame = park.select().consumption(start="2024-03-01", end="2024-05-01", freq="MS")
    assert frame.period.tolist() == [
        pd.Timestamp("2024-03-01", tz="Europe/Paris"),
        pd.Timestamp("2024-04-01", tz="Europe/Paris"),
    ]
    assert frame.energy.tolist() == [31 * 24 - 1, 30 * 24]
    assert frame.coverage.tolist() == [pytest.approx(1), pytest.approx(1)]


@pytest.mark.parametrize(("tz", "energies"), [("Europe/Paris", [1, 1]), ("UTC", [2, None])])
def test_years_are_cut_in_the_given_time_zone(park, tz, energies):
    # 23:30 UTC on 31 December is already 2024 in Paris
    index = pd.DatetimeIndex(["2023-12-31 22:30", "2023-12-31 23:30"], tz="UTC")
    add_meter(park, "m", ["b1"], 1.0, timestep="1h", index=index)
    frame = park.select().consumption(start="2023-01-01", end="2025-01-01", freq="YS", tz=tz)
    assert [None if math.isnan(e) else e for e in frame.energy] == energies


def test_sub_meters_are_not_counted_twice(park):
    add_meter(park, "gas", ["s"], 100.0, end_use="total")
    add_meter(park, "heat", ["boiler"], 60.0, end_use="heating", parent="gas")
    assert rows(park.select().consumption(**YEAR)) == {("s", "total"): 36600}


def test_breakdown_splits_a_head_meter_by_end_use_of_its_sub_meters(park):
    add_meter(park, "gas", ["s"], 100.0, end_use="total")
    add_meter(park, "heat", ["boiler"], 60.0, end_use="heating", parent="gas")
    add_meter(park, "dhw", ["s"], 10.0, end_use="dhw", parent="gas")
    add_meter(park, "deeper", ["s"], 5.0, end_use="dhw", parent="dhw")
    frame = park.select().consumption(**YEAR, breakdown=True)
    assert rows(frame) == {("s", "heating"): 21960, ("s", "dhw"): 3660, ("s", "other"): 10980}
    assert frame.set_index("end_use").loc["other", "meters"] == ["gas", "heat", "dhw"]


def test_breakdown_warns_when_sub_meters_exceed_their_head(park):
    add_meter(park, "gas", ["s"], 10.0)
    add_meter(park, "heat", ["boiler"], 60.0, end_use="heating", parent="gas")
    with pytest.warns(NoDataWarning, match="sub-meters measure more"):
        frame = park.select().consumption(**YEAR, breakdown=True)
    assert rows(frame)[("s", "other")] == -18300


def test_breakdown_refuses_a_sub_meter_of_another_energy(park):
    add_meter(park, "gas", ["s"], 10.0)
    add_meter(park, "elec", ["s"], 1.0, energy_type="electricity", parent="gas")
    with pytest.raises(TimeSeriesError, match="'elec'"):
        park.select().consumption(**YEAR, breakdown=True)


def test_meter_of_several_buildings_has_one_row(park):
    add_meter(park, "m", ["b1", "b3"], 1.0)
    assert rows(park.select().consumption(**YEAR)) == {("b1+b3", "other"): DAYS_2024}


def test_building_selection_keeps_meters_of_its_buildings_and_their_sites(park):
    add_meter(park, "m-site", ["boiler"], 1.0)
    add_meter(park, "m-b1", ["b1"], 1.0)
    add_meter(park, "m-b3", ["b3"], 1.0)
    assert set(park.select(within="s").consumption(**YEAR).entity) == {"s", "b1"}
    assert set(park.select(parent=None).consumption(**YEAR).entity) == {"b3"}


def test_site_selection_adds_up_the_head_meters_of_the_subtree(park):
    add_meter(park, "m-site", ["s"], 1.0)
    add_meter(park, "m-b1", ["b1"], 2.0)
    add_meter(park, "m-b3", ["b3"], 4.0)
    frame = park.select(entity="site").consumption(**YEAR)
    assert frame.to_dict("records") == [
        {
            "entity": "s",
            "energy_type": "natural_gas",
            "end_use": "other",
            "energy": 3 * DAYS_2024,
            "coverage": 1.0,
            "estimated": False,
            "meters": ["m-site", "m-b1"],
        }
    ]


def test_split_shares_a_site_meter_by_area_between_selected_buildings(park):
    add_meter(park, "site", ["s"], 3.0)
    frame = park.select(lambda b: b.id == "b2").consumption(**YEAR, split="heated_area")
    assert frame.to_dict("records")[0] | {"meters": None} == {
        "entity": "b2",
        "energy_type": "natural_gas",
        "end_use": "other",
        "energy": DAYS_2024,
        "coverage": 1.0,
        "estimated": True,
        "meters": None,
    }


def test_split_with_a_missing_area_keeps_the_line_and_warns(park):
    add_meter(park, "m", ["b1", "b3"], 1.0)
    with pytest.warns(NoDataWarning, match="not split"):
        frame = park.select().consumption(**YEAR, split="heated_area")
    assert frame.entity.tolist() == ["b1+b3"]
    assert not frame.estimated.iloc[0]


def test_meter_without_data_is_ignored_with_a_warning(park):
    park.add_meter(Meter(id="m", scope=["b1"], channels=[Channel(name="energy", unit="kWh")]))
    with pytest.warns(NoDataWarning, match="no data in memory"):
        assert park.select().consumption(**YEAR).empty


def test_load_data_reads_the_meter_source(park, tmp_path):
    path = tmp_path / "m.csv"
    pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10, freq="D"), "energy": 1.0}).to_csv(
        path, index=False
    )
    source = CsvReader(path=str(path), index_col="date", tz="Europe/Paris")
    park.add_meter(
        Meter(id="m", scope=["b1"], reader=source, channels=[Channel(name="energy", unit="kWh")])
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        frame = park.select().consumption(**YEAR, load_data=True)
    assert frame.energy.iloc[0] == 10


def test_load_data_skips_a_meter_without_reader(park):
    add_meter(park, "read", ["b1"])
    park.add_meter(Meter(id="unread", scope=["b2"], channels=[Channel(name="energy", unit="kWh")]))
    with pytest.warns(NoDataWarning, match="'unread' has no data and no reader") as record:
        frame = park.select().consumption(**YEAR, load_data=True)
    assert len(record) == 1
    assert frame.entity.tolist() == ["b1"]


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"start": "2025-01-01", "end": "2024-01-01"}, InvalidArgumentError),
        ({**YEAR, "freq": "W"}, InvalidArgumentError),
        ({**YEAR, "split": "construction_year"}, InvalidArgumentError),
        ({**YEAR, "split": "colour"}, UnknownFieldError),
    ],
)
def test_invalid_arguments_are_refused(park, kwargs, error):
    with pytest.raises(error):
        park.select().consumption(**kwargs)


def test_split_does_not_apply_to_sites(park):
    with pytest.raises(InvalidArgumentError, match="buildings"):
        park.select(entity="site").consumption(**YEAR, split="heated_area")


def test_intensity_of_a_building(park):
    add_meter(park, "m", ["b1"], 10.0)
    row = park.select().intensity(**YEAR).iloc[0]
    assert (row.area, row.intensity) == (1000, 3.66)


@pytest.mark.parametrize(("prefer", "area"), [("buildings", 1500), ("site", 2000)])
def test_intensity_of_a_site_uses_the_aggregated_area(park, prefer, area):
    park["s"].heated_area = 2000.0
    add_meter(park, "m", ["s"], 10.0)
    row = park.select().intensity(**YEAR, prefer=prefer).iloc[0]
    assert (row.entity, row.area) == ("s", area)
    assert row.intensity == pytest.approx(3660 / area)


def test_intensity_is_missing_when_an_area_is_missing(park):
    add_meter(park, "m", ["b1", "b3"], 1.0)
    row = park.select().intensity(**YEAR).iloc[0]
    assert math.isnan(row.area)
    assert math.isnan(row.intensity)


def test_intensity_of_split_lines_uses_the_building_area(park):
    add_meter(park, "m", ["s"], 3.0)
    frame = park.select(within="s").intensity(**YEAR, split="heated_area")
    assert frame.intensity.tolist() == [pytest.approx(3 * DAYS_2024 / 1500)] * 2


def test_intensity_area_must_be_extensive(park):
    with pytest.raises(InvalidArgumentError, match="extensive"):
        park.select().intensity(**YEAR, area="height")


def test_consumption_does_not_modify_the_park(park):
    add_meter(park, "m", ["s"], 3.0)
    before = park.to_json()
    park.select().intensity(**YEAR, split="heated_area", breakdown=True, freq="MS")
    assert park.to_json() == before


# --- PV production ------------------------------------------------------------------------------


def test_pv_production_counts_negatively(park):
    add_meter(park, "grid", ["b1"], 10.0, energy_type="electricity", end_use="total")
    add_meter(park, "pv", ["b1"], 4.0, energy_type="electricity", end_use="pv_production")
    frame = park.select().consumption(**YEAR)
    assert rows(frame) == {("b1", "total"): 3660, ("b1", "pv_production"): -1464}
    assert frame["energy"].sum() == 2196  # net balance


def test_pv_sub_meter_is_neither_skipped_nor_subtracted(park):
    add_meter(park, "grid", ["b1"], 10.0, energy_type="electricity", end_use="total")
    add_meter(
        park, "pv", ["b1"], 4.0, energy_type="electricity", end_use="pv_production", parent="grid"
    )
    frame = park.select().consumption(**YEAR, breakdown=True)
    assert rows(frame) == {("b1", "total"): 3660, ("b1", "pv_production"): -1464}


def test_intensity_is_a_net_balance(park):
    add_meter(park, "grid", ["b1"], 10.0, energy_type="electricity", end_use="total")
    add_meter(park, "pv", ["b1"], 4.0, energy_type="electricity", end_use="pv_production")
    frame = park.select(within="s").intensity(**YEAR).set_index("end_use")
    assert frame.loc["pv_production", "intensity"] == pytest.approx(-1.464)
