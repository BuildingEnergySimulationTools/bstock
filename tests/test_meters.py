import json
import warnings

import pandas as pd
import pytest
from pydantic import ValidationError

from bstock.building import Building
from bstock.enums import EndUse
from bstock.exceptions import (
    DataReadError,
    EntityInUseError,
    HierarchyError,
    InvalidArgumentError,
    InvalidTagError,
    NoDataWarning,
    TimeSeriesError,
    UnknownIdError,
)
from bstock.meters import Channel, Meter, tide_column
from bstock.park import BuildingPark
from bstock.readers import CsvReader, DataReader, ParquetReader, UnregisteredReader
from bstock.systems import HeatingGenerator

RNB_A, RNB_B = "A1B2C3D4E5F6", "B1B2C3D4E5F6"


def hourly(start="2024-01-01", periods=3, tz="UTC", **columns):
    index = pd.date_range(start, periods=periods, freq="h", tz=tz)
    return pd.DataFrame(columns or {"value": range(periods)}, index=index)


@pytest.fixture
def park():
    park = BuildingPark(name="p")
    site = park.new_site("site-A")
    park.new_building(rnb_id=RNB_A, parent=site)
    park.new_building(rnb_id=RNB_B, parent=site)
    park.add_system(HeatingGenerator(id="boiler"), owner=site)
    return park


# --- tide naming ------------------------------------------------------------------------------


def test_tide_column():
    assert tide_column("PDL_power", "W", "site-A", "total") == "PDL_power__W__site-A__total"
    with pytest.raises(InvalidTagError, match="tide tag"):
        tide_column("a__b", "W", "x", "y")


def test_tide_columns_follow_convention():
    meter = Meter(
        id="PDL_123", scope=["site-A"], end_use="total", channels=[Channel(name="power", unit="W")]
    )
    assert meter.tide_columns() == {"power": "PDL_123_power__W__site-A__total"}


def test_bloc_is_meter_id_for_several_entities_and_sub_bloc_defaults_to_other():
    meter = Meter(id="m", scope=[RNB_A, RNB_B], channels=[Channel(name="energy", unit="kWh")])
    assert meter.tide_columns()["energy"] == "m_energy__kWh__m__other"


def test_explicit_bloc_and_sub_bloc_override_derived_tags():
    meter = Meter(
        id="m",
        scope=[RNB_A],
        end_use="heating",
        bloc="chaufferie",
        sub_bloc="gas",
        channels=[Channel(name="energy", unit="kWh")],
    )
    assert meter.tide_columns()["energy"] == "m_energy__kWh__chaufferie__gas"
    assert meter.tide_tags() == ("chaufferie", "gas")
    meter.bloc = None
    assert meter.tide_tags() == (RNB_A, "gas")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bloc": "a__b"},
        {"sub_bloc": ""},
        {"tags": {"bloc": "x"}},  # removed field
        {"channels": [Channel(name="e", unit="kWh"), Channel(name="e", unit="Wh")]},
        {"channels": []},
        {"id": "bad__id"},
    ],
)
def test_invalid_meters_are_rejected(kwargs):
    data = {"id": "m", "channels": [Channel(name="e", unit="kWh")], **kwargs}
    with pytest.raises(ValidationError):
        Meter(**data)


def test_channel_name_cannot_contain_separator():
    with pytest.raises(ValidationError):
        Channel(name="a__b", unit="W")


def test_timestep_is_validated_at_construction():
    with pytest.raises(ValidationError, match="timestep"):
        Meter(channels=[Channel(name="e", unit="kWh")], timestep="every now and then")


def test_timestep_is_validated():
    meter = Meter(channels=[Channel(name="e", unit="kWh")], timestep="30min")
    assert meter.timestep == "30min"
    with pytest.raises(ValueError, match="timestep"):
        meter.timestep = "every now and then"


# --- data -------------------------------------------------------------------------------------


def test_data_setter_renames_channel_columns():
    meter = Meter(id="m", scope=["s"], end_use="total", channels=[Channel(name="power", unit="W")])
    meter.data = hourly(power=[1.0, 2.0, 3.0])
    assert list(meter.data.columns) == ["m_power__W__s__total"]
    meter.data = meter.data  # already in tide format
    assert list(meter.data.columns) == ["m_power__W__s__total"]


def test_data_setter_requires_time_zone():
    meter = Meter(channels=[Channel(name="power", unit="W")])
    with pytest.raises(TimeSeriesError, match="time zone"):
        meter.data = hourly(tz=None, power=[1, 2, 3])


def test_data_setter_requires_datetime_index():
    meter = Meter(channels=[Channel(name="power", unit="W")])
    with pytest.raises(TimeSeriesError, match="DatetimeIndex"):
        meter.data = pd.DataFrame({"power": [1, 2]})


def test_data_setter_requires_every_channel():
    meter = Meter(channels=[Channel(name="power", unit="W"), Channel(name="energy", unit="kWh")])
    with pytest.raises(TimeSeriesError, match="missing"):
        meter.data = hourly(power=[1, 2, 3])


def test_data_is_sorted():
    meter = Meter(channels=[Channel(name="power", unit="W")])
    frame = hourly(power=[1, 2, 3]).iloc[::-1]
    meter.data = frame
    assert meter.data.index.is_monotonic_increasing


def test_data_is_not_serialised():
    meter = Meter(id="m", channels=[Channel(name="power", unit="W")])
    meter.data = hourly(power=[1, 2, 3])
    assert "data" not in json.loads(meter.model_dump_json())


# --- sources ----------------------------------------------------------------------------------


def write_csv(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_csv_source_with_offsets(tmp_path):
    csv = write_csv(
        tmp_path / "m.csv",
        "horodate;valeur\n2024-01-01T00:00:00+01:00;1,5\n2024-01-01T01:00:00+01:00;2,5\n",
    )
    meter = Meter(
        id="m",
        channels=[Channel(name="power", unit="W", column="valeur")],
        reader=CsvReader(path=str(csv), index_col="horodate", sep=";", decimal=","),
    )
    data = meter.load_data()
    assert data.iloc[:, 0].tolist() == [1.5, 2.5]
    assert data.index.tz is not None


def test_csv_source_naive_index_needs_tz(tmp_path):
    csv = write_csv(tmp_path / "m.csv", "t,v\n2024-01-01 00:00,1\n2024-01-01 01:00,2\n")
    with pytest.raises(TimeSeriesError, match="time zone"):
        CsvReader(path=str(csv)).read()
    loaded = CsvReader(path=str(csv), tz="Europe/Paris").read()
    assert str(loaded.index.tz) == "Europe/Paris"


def test_csv_source_handles_daylight_saving_time_change(tmp_path):
    # 2024-10-27 in Paris: 02:00-03:00 happens twice, offsets change from +02:00 to +01:00
    csv = write_csv(
        tmp_path / "m.csv",
        "t,v\n2024-10-27T02:30:00+02:00,1\n2024-10-27T02:30:00+01:00,2\n",
    )
    loaded = CsvReader(path=str(csv)).read()
    assert len(loaded.index.unique()) == 2
    assert str(loaded.index.tz) == "UTC"


def test_naive_ambiguous_hour_with_infer(tmp_path):
    csv = write_csv(
        tmp_path / "m.csv",
        "t,v\n2024-10-27 01:30,0\n2024-10-27 02:30,1\n2024-10-27 02:30,2\n2024-10-27 03:30,3\n",
    )
    with pytest.raises(TimeSeriesError, match="localise"):
        CsvReader(path=str(csv), tz="Europe/Paris").read()
    loaded = CsvReader(path=str(csv), tz="Europe/Paris", tz_ambiguous="infer").read()
    assert loaded.index.tz_convert("UTC").is_unique


def test_parquet_source(tmp_path):
    path = tmp_path / "m.parquet"
    hourly(power=[1.0, 2.0, 3.0]).to_parquet(path)
    meter = Meter(channels=[Channel(name="power", unit="W")], reader=ParquetReader(path=str(path)))
    assert meter.load_data().shape == (3, 1)


def test_single_column_feeds_single_channel(tmp_path):
    path = tmp_path / "m.parquet"
    hourly(whatever=[1.0, 2.0, 3.0]).to_parquet(path)
    meter = Meter(channels=[Channel(name="power", unit="W")], reader=ParquetReader(path=str(path)))
    assert meter.load_data().iloc[:, 0].tolist() == [1.0, 2.0, 3.0]


def test_missing_source_column(tmp_path):
    path = tmp_path / "m.parquet"
    hourly(a=[1, 2, 3], b=[1, 2, 3]).to_parquet(path)
    meter = Meter(
        channels=[Channel(name="power", unit="W", column="c")],
        reader=ParquetReader(path=str(path)),
    )
    with pytest.raises(DataReadError, match="not found"):
        meter.load_data()


def test_missing_file_raises_data_read_error(tmp_path):
    with pytest.raises(DataReadError):
        CsvReader(path=str(tmp_path / "nope.csv")).read()


def test_load_without_source():
    with pytest.raises(DataReadError, match="no reader"):
        Meter(channels=[Channel(name="power", unit="W")]).load_data()


def test_relative_path_resolved_against_base_dir(tmp_path):
    hourly(power=[1, 2, 3]).to_parquet(tmp_path / "m.parquet")
    source = ParquetReader(path="m.parquet")
    source._base_dir = tmp_path
    assert source.read().shape == (3, 1)


class ApiReader(DataReader):
    """A project specific reader."""

    type: str = "test_api"
    endpoint: str

    def read_raw(self):
        return hourly(tz="Europe/Paris", power=[5.0, 6.0, 7.0])


def test_reader_overriding_read_is_rejected():
    with pytest.raises(InvalidArgumentError, match="implement read_raw"):

        class BadReader(DataReader):
            type: str = "test_bad"

            def read(self):
                return hourly(tz="UTC", power=[1.0])


def test_reader_read_checks_the_raw_index():
    with pytest.raises(TimeSeriesError, match="no time zone"):
        NaiveReader().read()
    assert NaiveReader(tz="Europe/Paris").read().index.tz is not None


class NaiveReader(DataReader):
    type: str = "test_naive"

    def read_raw(self):
        return hourly(tz=None, power=[1.0, 2.0, 3.0])


def test_custom_source_is_registered_and_round_trips():
    meter = Meter(
        id="m",
        channels=[Channel(name="power", unit="W")],
        reader=ApiReader(endpoint="https://example.org"),
    )
    data = json.loads(meter.model_dump_json())
    assert data["reader"] == {
        "type": "test_api",
        "tz": None,
        "tz_ambiguous": "raise",
        "tz_nonexistent": "raise",
        "endpoint": "https://example.org",
    }
    loaded = Meter.model_validate(data)
    assert isinstance(loaded.reader, ApiReader)
    assert loaded.load_data().iloc[:, 0].tolist() == [5.0, 6.0, 7.0]


def test_unknown_source_type_is_kept():
    data = {
        "id": "m",
        "channels": [{"name": "power", "unit": "W"}],
        "reader": {"type": "client_sftp", "host": "sftp.example.org"},
    }
    meter = Meter.model_validate(data)
    assert isinstance(meter.reader, UnregisteredReader)
    assert json.loads(meter.model_dump_json(exclude_none=True))["reader"]["host"] == (
        "sftp.example.org"
    )
    with pytest.raises(DataReadError, match="client_sftp"):
        meter.load_data()


# --- park -------------------------------------------------------------------------------------


def make_meter(meter_id, scope, end_use="total", channel="energy", unit="kWh", **kwargs):
    return Meter(
        id=meter_id,
        scope=scope,
        end_use=end_use,
        channels=[Channel(name=channel, unit=unit)],
        **kwargs,
    )


def test_add_meter_checks_scope_and_id(park):
    meter = park.add_meter(make_meter("PDL_1", ["site-A"]))
    assert park["PDL_1"] is meter
    with pytest.raises(UnknownIdError):
        park.add_meter(make_meter("PDL_2", ["nope"]))
    with pytest.raises(Exception, match="already used"):
        park.add_meter(make_meter(RNB_A, ["site-A"]))


def test_scope_assignment_is_checked_in_park(park):
    meter = park.add_meter(make_meter("PDL_1", ["site-A"]))
    with pytest.raises(UnknownIdError, match="GHOST"):
        meter.scope = ["GHOST"]
    assert meter.scope == ["site-A"]
    meter.scope = [RNB_A, "boiler"]
    assert meter.scope == [RNB_A, "boiler"]


def test_scope_assignment_is_free_outside_park():
    meter = make_meter("PDL_1", ["site-A"])
    meter.scope = ["anything"]
    assert meter.scope == ["anything"]


def test_meter_uses_park_priority(park):
    meter = park.add_meter(make_meter("PDL_1", ["site-A"]))
    assert meter.active_source_priority() is park.source_priority


def test_entity_meters(park):
    site_meter = park.add_meter(make_meter("site-meter", ["site-A"]))
    boiler_meter = park.add_meter(make_meter("boiler-meter", ["boiler"], end_use="heating"))
    a_meter = park.add_meter(make_meter("a-meter", [RNB_A]))
    assert park["site-A"].find_meters() == [site_meter, boiler_meter]  # boiler is a site system
    assert park[RNB_A].find_meters() == [a_meter]
    assert Building().find_meters() == []


def test_remove_meter(park):
    park.add_meter(make_meter("PDL_1", ["site-A"]))
    park.remove("PDL_1")
    assert "PDL_1" not in park
    assert park.meters == []


def test_removing_measured_entity_needs_force(park):
    meter = park.add_meter(make_meter("m", [RNB_A, RNB_B]))
    with pytest.raises(EntityInUseError, match="'m'"):
        park.remove(RNB_A)
    park.remove(RNB_A, force=True)
    assert meter.scope == [RNB_B]


def test_timeseries_assembles_meters(park):
    elec = park.add_meter(make_meter("elec", ["site-A"], channel="power", unit="W"))
    gas = park.add_meter(make_meter("gas", ["boiler"], end_use="heating"))
    elec.data = hourly(power=[1.0, 2.0, 3.0])
    gas.data = hourly(start="2024-01-01 02:00", tz="Europe/Paris", energy=[10.0, 20.0, 30.0])
    df = park.timeseries()
    assert list(df.columns) == ["elec_power__W__site-A__total", "gas_energy__kWh__boiler__heating"]
    assert str(df.index.tz) == "UTC"
    assert len(df) == 4  # 00:00-02:00 UTC and 01:00-03:00 UTC: union of 4 hours
    assert df.index.is_monotonic_increasing


def test_timeseries_interleaves_meters_of_different_time_steps(park):
    # Regression: pandas warned (deprecation) when concatenating interleaved indexes.
    hourly_meter = park.add_meter(make_meter("elec", ["site-A"], channel="power", unit="W"))
    daily_meter = park.add_meter(make_meter("gas", ["boiler"], end_use="heating"))
    hourly_meter.data = hourly(start="2024-01-02 10:00", periods=3, power=[1.0, 2.0, 3.0])
    index = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    daily_meter.data = pd.DataFrame({"energy": [1.0, 2.0, 3.0, 4.0]}, index=index)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        df = park.timeseries()
    assert len(df) == 7
    assert df.index.is_monotonic_increasing


def test_timeseries_filters(park):
    for meter_id, scope, end_use in [("a", [RNB_A], "heating"), ("b", [RNB_B], "total")]:
        meter = park.add_meter(
            make_meter(meter_id, scope, end_use=end_use, energy_type="electricity")
        )
        meter.data = hourly(energy=[1, 2, 3])
    assert len(park.timeseries(end_use="heating").columns) == 1
    assert len(park.timeseries(end_use=[EndUse.HEATING, "total"]).columns) == 2
    assert len(park.timeseries(buildings=[RNB_B]).columns) == 1
    assert len(park.timeseries(meters=["a"]).columns) == 1
    assert len(park.timeseries(energy_type="natural_gas").columns) == 0
    assert str(park.timeseries(tz="Europe/Paris").index.tz) == "Europe/Paris"


def test_timeseries_skips_meters_without_data(park):
    park.add_meter(make_meter("m", ["site-A"]))
    with pytest.warns(NoDataWarning):
        df = park.timeseries()
    assert df.empty


def test_timeseries_load(park, tmp_path):
    hourly(energy=[1.0, 2.0, 3.0]).to_parquet(tmp_path / "m.parquet")
    park.add_meter(
        make_meter("m", ["site-A"], reader=ParquetReader(path=str(tmp_path / "m.parquet")))
    )
    assert park.timeseries(load_data=True).shape == (3, 1)


def test_meters_with_same_tags_get_distinct_columns(park):
    first = park.add_meter(make_meter("m1", ["site-A"]))
    second = park.add_meter(make_meter("m2", ["site-A"]))
    for meter in (first, second):
        meter.data = hourly(energy=[1, 2, 3])
    assert park.timeseries().shape[1] == 2


def test_source_equality_ignores_base_dir(tmp_path):
    first, second = ParquetReader(path="m.parquet"), ParquetReader(path="m.parquet")
    first._base_dir = tmp_path
    assert first == second
    assert first != ParquetReader(path="other.parquet")


# --- sub-meters -------------------------------------------------------------------------------


def meter(meter_id, parent=None):
    return Meter(
        id=meter_id,
        scope=["site-A"],
        parent_meter=parent,
        channels=[Channel(name="energy", unit="kWh")],
    )


@pytest.fixture
def metered(park):
    park.add_meter(meter("head"))
    park.add_meter(meter("sub", parent="head"))
    park.add_meter(meter("subsub", parent="sub"))
    return park


def test_meter_is_a_head_meter_by_default():
    assert meter("m").parent_meter is None


def test_parent_meter_must_be_a_meter_of_the_park(park):
    with pytest.raises(UnknownIdError, match="parent meter 'nope'"):
        park.add_meter(meter("m", parent="nope"))
    with pytest.raises(UnknownIdError, match="parent meter 'site-A'"):
        park.add_meter(meter("m", parent="site-A"))
    assert "m" not in park


def test_meter_cannot_be_its_own_parent(park):
    with pytest.raises(HierarchyError, match="cycle"):
        park.add_meter(meter("m", parent="m"))


def test_parent_meter_assignment_is_checked(metered):
    with pytest.raises(HierarchyError, match="cycle"):
        metered["head"].parent_meter = "subsub"
    with pytest.raises(UnknownIdError):
        metered["sub"].parent_meter = "nope"
    assert metered["head"].parent_meter is None
    metered["subsub"].parent_meter = "head"
    assert metered["subsub"].parent_meter == "head"


def test_parent_meter_is_not_checked_outside_a_park():
    assert meter("m", parent="anything").parent_meter == "anything"


def test_parent_meter_id_cannot_contain_the_tide_separator():
    with pytest.raises(ValidationError):
        meter("m", parent="a__b")


def test_removing_a_parent_meter_needs_force(metered):
    with pytest.raises(EntityInUseError, match="sub"):
        metered.remove("head")
    metered.remove("head", force=True)
    assert metered["sub"].parent_meter is None
    assert metered["subsub"].parent_meter == "sub"


def test_parent_meter_round_trips_through_json(metered):
    reloaded = BuildingPark.from_json(metered.to_json())
    assert reloaded["subsub"].parent_meter == "sub"
    assert reloaded == metered


def test_head_meter_json_has_no_parent_meter_key(metered):
    meters = json.loads(metered.to_json())["meters"]
    assert "parent_meter" not in meters[0]


def test_loading_checks_parent_meters_after_every_meter(metered):
    data = json.loads(metered.to_json())
    data["meters"].reverse()
    assert BuildingPark.from_json(json.dumps(data))["sub"].parent_meter == "head"
    data["meters"][0]["parent_meter"] = "nope"
    with pytest.raises(UnknownIdError, match="nope"):
        BuildingPark.from_json(json.dumps(data))


def test_timeseries_accepts_the_variants_of_a_family(park):
    from bstock.enums import EnergyType

    for meter_id, energy in (("logs", "wood_logs"), ("gas", "natural_gas")):
        meter = park.add_meter(make_meter(meter_id, [RNB_A], channel="energy", energy_type=energy))
        meter.data = hourly(energy=[1.0, 2.0, 3.0])
    frame = park.timeseries(energy_type=EnergyType.WOOD.variants())
    assert [c.split("__")[0] for c in frame.columns] == ["logs_energy"]


# --- coverage -----------------------------------------------------------------------------------


def daily_meter(days, timestep="1D"):
    meter = Meter(id="m", channels=[Channel(name="energy", unit="kWh")], timestep=timestep)
    index = pd.DatetimeIndex(pd.to_datetime(days)).tz_localize("Europe/Paris")
    meter.data = pd.DataFrame({"energy": 1.0}, index=index)
    return meter


def test_coverage_counts_present_and_expected_time_steps():
    meter = daily_meter(pd.date_range("2024-01-01", "2024-01-10"))
    row = meter.coverage(start="2024-01-01", end="2024-02-01").iloc[0]
    assert (row.present, row.expected) == (10, 31)
    assert row.coverage == pytest.approx(10 / 31)


def test_coverage_per_month_counts_local_days_across_dst():
    meter = daily_meter(pd.date_range("2024-03-01", "2024-04-30"))
    frame = meter.coverage(start="2024-03-01", end="2024-05-01", freq="MS")
    assert frame["expected"].tolist() == [31, 30]
    assert frame["coverage"].tolist() == [1.0, 1.0]


def test_coverage_ignores_rows_without_value():
    meter = daily_meter(pd.date_range("2024-01-01", "2024-01-04"))
    meter.data = meter.data.assign(**{meter.tide_columns()["energy"]: [1.0, None, 1.0, None]})
    assert meter.coverage(start="2024-01-01", end="2024-01-05").iloc[0].present == 2


def test_coverage_uses_median_gap_without_timestep():
    meter = Meter(id="m", channels=[Channel(name="temperature", unit="°C")])
    meter.data = hourly(periods=12, tz="UTC", temperature=[20.0] * 12)
    row = meter.coverage(start="2024-01-01", end="2024-01-02", tz="UTC").iloc[0]
    assert (row.present, row.expected) == (12, 24)


def test_coverage_requires_data():
    with pytest.raises(TimeSeriesError, match="load_data"):
        Meter(id="m", channels=[Channel(name="energy", unit="kWh")]).coverage(
            start="2024-01-01", end="2024-02-01"
        )


def test_coverage_of_a_daily_meter_without_data_in_the_window():
    meter = daily_meter(["2024-01-01"])
    row = meter.coverage(start="2025-01-01", end="2026-01-01").iloc[0]
    assert (row.present, row.expected, row.coverage) == (0, 365, 0.0)


def test_coverage_counts_real_hours_on_dst_day():
    meter = Meter(id="m", channels=[Channel(name="temperature", unit="°C")], timestep="1h")
    meter.data = hourly(start="2024-03-31", periods=23, tz="Europe/Paris", temperature=[1.0] * 23)
    row = meter.coverage(start="2024-03-31", end="2024-04-01").iloc[0]
    assert (row.present, row.expected) == (23, 23)


def test_coverage_per_day_and_week():
    meter = daily_meter(pd.date_range("2024-01-01", "2024-01-14").delete([2, 3]))
    days = meter.coverage(start="2024-01-01", end="2024-01-15", freq="1D")
    assert len(days) == 14
    assert days["coverage"].tolist()[:5] == [1.0, 1.0, 0.0, 0.0, 1.0]
    weeks = meter.coverage(start="2024-01-01", end="2024-01-15", freq="W-MON")
    # 2024-01-01 is a Monday: two full weeks
    assert weeks["present"].tolist() == [5, 7]
    assert weeks["expected"].tolist() == [7, 7]


def test_coverage_of_hourly_data_per_day():
    meter = Meter(id="m", channels=[Channel(name="temperature", unit="°C")], timestep="1h")
    meter.data = hourly(start="2024-01-01", periods=36, tz="UTC", temperature=[1.0] * 36)
    frame = meter.coverage(start="2024-01-01", end="2024-01-03", freq="1D", tz="UTC")
    assert frame["present"].tolist() == [24, 12]
    assert frame["coverage"].tolist() == [1.0, 0.5]


def test_coverage_refuses_an_invalid_freq():
    with pytest.raises(InvalidArgumentError, match="freq"):
        daily_meter(["2024-01-01"]).coverage(start="2024-01-01", end="2024-02-01", freq="bad")


# --- measures: channels, roles, tolerant load_data ------------------------------------------------

GAS_CHANNELS = [
    ("energy", "kWh"),
    ("volume", "m³"),
    ("thermal_coefficient", "kWh/m³"),
    ("duration", "d"),
]


@pytest.fixture
def measured(park):
    """Park with a gas meter of 4 channels (heating) and an ambient temperature sensor."""
    gas = park.add_meter(
        Meter(
            id="gas",
            scope=["boiler"],
            energy_type="natural_gas",
            end_use="heating",
            channels=[Channel(name=n, unit=u) for n, u in GAS_CHANNELS],
        )
    )
    gas.data = hourly(**{n: [1.0, 2.0, 3.0] for n, _ in GAS_CHANNELS})
    sensor = park.add_meter(
        Meter(
            id="t",
            scope=[RNB_A],
            sub_bloc="ambiance",
            channels=[Channel(name="temperature", unit="°C")],
        )
    )
    sensor.data = hourly(temperature=[19.0, 20.0, 21.0])
    return park


def test_timeseries_keeps_every_channel_without_quantity(measured):
    assert measured.timeseries().shape[1] == 5


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("energy", ["gas_energy__kWh__boiler__heating"]),
        ("temperature", [f"t_temperature__°C__{RNB_A}__ambiance"]),
        (
            ["energy", "temperature"],
            ["gas_energy__kWh__boiler__heating", f"t_temperature__°C__{RNB_A}__ambiance"],
        ),
        ("power", []),
    ],
)
def test_quantity_keeps_only_the_columns_of_these_channels(measured, quantity, expected):
    assert list(measured.timeseries(quantity=quantity).columns) == expected


@pytest.mark.parametrize(
    ("sub_bloc", "expected"),
    [("ambiance", ["t"]), ("heating", ["gas"]), (["ambiance", "heating"], ["gas", "t"])],
)
def test_sub_bloc_filters_on_the_effective_tag(measured, sub_bloc, expected):
    columns = measured.timeseries(sub_bloc=sub_bloc, quantity=["energy", "temperature"]).columns
    assert [c.split("_", 1)[0] for c in columns] == expected


@pytest.mark.parametrize("criterion", [{"energy_type": "natural_gas"}, {"end_use": "heating"}])
def test_energy_filters_leave_the_sensors_out(measured, criterion):
    columns = measured.timeseries(**criterion, quantity=["energy", "temperature"]).columns
    assert list(columns) == ["gas_energy__kWh__boiler__heating"]


def test_load_data_skips_a_meter_without_reader(park, tmp_path):
    hourly(energy=[1.0, 2.0, 3.0]).to_parquet(tmp_path / "m.parquet")
    park.add_meter(
        make_meter("read", ["site-A"], reader=ParquetReader(path=str(tmp_path / "m.parquet")))
    )
    park.add_meter(make_meter("unread", ["site-A"]))
    with pytest.warns(NoDataWarning, match="'unread' has no data and no reader") as record:
        df = park.timeseries(load_data=True)
    assert len(record) == 1
    assert list(df.columns) == ["read_energy__kWh__site-A__total"]


def test_load_data_still_raises_when_a_reader_fails(park, tmp_path):
    park.add_meter(
        make_meter("m", ["site-A"], reader=ParquetReader(path=str(tmp_path / "missing.parquet")))
    )
    with pytest.raises(DataReadError):
        park.timeseries(load_data=True)


# --- tags changed with data in memory (0.13.0) ------------------------------------------------


def loaded_meter(park):
    meter = park.add_meter(
        make_meter("m", ["site-A"], end_use=None, energy_type="electricity", timestep="1h")
    )
    meter.data = hourly(energy=[1.0, 2.0, 3.0])
    return meter


@pytest.mark.parametrize(
    ("change", "column"),
    [
        (lambda m: setattr(m, "bloc", "zone"), "m_energy__kWh__zone__other"),
        (lambda m: setattr(m, "sub_bloc", "lights"), "m_energy__kWh__site-A__lights"),
        (lambda m: setattr(m, "scope", [RNB_A]), f"m_energy__kWh__{RNB_A}__other"),
        (lambda m: setattr(m, "scope", [RNB_A, RNB_B]), "m_energy__kWh__m__other"),
        (lambda m: setattr(m, "end_use", "heating"), "m_energy__kWh__site-A__heating"),
        (lambda m: m.set("end_use", "heating", source="bdnb"), "m_energy__kWh__site-A__heating"),
    ],
)
def test_data_columns_follow_changed_tags(park, change, column):
    meter = loaded_meter(park)
    change(meter)
    assert list(meter.data.columns) == [column]
    assert list(park.timeseries().columns) == [column]
    assert meter.data[column].tolist() == [1.0, 2.0, 3.0]


def test_data_columns_follow_unset_end_use(park):
    meter = loaded_meter(park)
    meter.set("end_use", "heating", source="bdnb")
    meter.unset("end_use")
    assert list(meter.data.columns) == ["m_energy__kWh__site-A__other"]


def test_consumption_works_after_tags_changed(park):
    meter = loaded_meter(park)
    meter.bloc, meter.sub_bloc = "zone", "lights"
    frame = park.select().consumption(start="2024-01-01", end="2024-01-02", tz="UTC")
    assert frame.energy.sum() == pytest.approx(6.0)


def test_changing_tags_without_data_keeps_no_data(park):
    meter = park.add_meter(make_meter("m", ["site-A"]))
    meter.bloc = "zone"
    assert meter.data is None


def test_invalid_tag_leaves_data_columns_unchanged(park):
    meter = loaded_meter(park)
    with pytest.raises(ValidationError):
        meter.bloc = "a__b"
    assert list(meter.data.columns) == ["m_energy__kWh__site-A__other"]
