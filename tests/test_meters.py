import json

import pandas as pd
import pytest
from pydantic import ValidationError

from bstock.building import Building
from bstock.enums import EndUse
from bstock.exceptions import (
    DataSourceError,
    EntityInUseError,
    NoDataWarning,
    TimeSeriesError,
    UnknownIdError,
)
from bstock.meters import Channel, Meter, tide_column
from bstock.park import BuildingPark
from bstock.sources import CsvSource, DataSource, ParquetSource, UnknownSource
from bstock.systems import HeatingGenerator

RNB_A, RNB_B = "A1B2C3D4E5F6", "B1B2C3D4E5F6"


def hourly(start="2024-01-01", periods=3, tz="UTC", **columns):
    index = pd.date_range(start, periods=periods, freq="h", tz=tz)
    return pd.DataFrame(columns or {"value": range(periods)}, index=index)


@pytest.fixture
def park():
    park = BuildingPark(name="p")
    site = park.new_site("site-A")
    park.new_building(RNB_A, parent=site)
    park.new_building(RNB_B, parent=site)
    park.add_system(HeatingGenerator(id="boiler"), owner=site)
    return park


# --- tide naming ------------------------------------------------------------------------------


def test_tide_column():
    assert tide_column("PDL_power", "W", "site-A", "total") == "PDL_power__W__site-A__total"
    with pytest.raises(ValueError, match="tide tag"):
        tide_column("a__b", "W", "x", "y")


def test_column_names_follow_convention():
    meter = Meter(
        id="PDL_123", scope=["site-A"], end_use="total", channels=[Channel(name="power", unit="W")]
    )
    assert meter.column_names() == {"power": "PDL_123_power__W__site-A__total"}


def test_bloc_is_meter_id_for_several_entities_and_sub_bloc_defaults_to_other():
    meter = Meter(id="m", scope=[RNB_A, RNB_B], channels=[Channel(name="energy", unit="kWh")])
    assert meter.column_names()["energy"] == "m_energy__kWh__m__other"


def test_explicit_bloc_and_sub_bloc_override_derived_tags():
    meter = Meter(
        id="m",
        scope=[RNB_A],
        end_use="heating",
        bloc="chaufferie",
        sub_bloc="gas",
        channels=[Channel(name="energy", unit="kWh")],
    )
    assert meter.column_names()["energy"] == "m_energy__kWh__chaufferie__gas"
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
        data_source=CsvSource(path=str(csv), index_col="horodate", sep=";", decimal=","),
    )
    data = meter.load_data()
    assert data.iloc[:, 0].tolist() == [1.5, 2.5]
    assert data.index.tz is not None


def test_csv_source_naive_index_needs_tz(tmp_path):
    csv = write_csv(tmp_path / "m.csv", "t,v\n2024-01-01 00:00,1\n2024-01-01 01:00,2\n")
    with pytest.raises(TimeSeriesError, match="time zone"):
        CsvSource(path=str(csv)).load()
    loaded = CsvSource(path=str(csv), tz="Europe/Paris").load()
    assert str(loaded.index.tz) == "Europe/Paris"


def test_csv_source_handles_daylight_saving_time_change(tmp_path):
    # 2024-10-27 in Paris: 02:00-03:00 happens twice, offsets change from +02:00 to +01:00
    csv = write_csv(
        tmp_path / "m.csv",
        "t,v\n2024-10-27T02:30:00+02:00,1\n2024-10-27T02:30:00+01:00,2\n",
    )
    loaded = CsvSource(path=str(csv)).load()
    assert len(loaded.index.unique()) == 2
    assert str(loaded.index.tz) == "UTC"


def test_naive_ambiguous_hour_with_infer(tmp_path):
    csv = write_csv(
        tmp_path / "m.csv",
        "t,v\n2024-10-27 01:30,0\n2024-10-27 02:30,1\n2024-10-27 02:30,2\n2024-10-27 03:30,3\n",
    )
    with pytest.raises(TimeSeriesError, match="localise"):
        CsvSource(path=str(csv), tz="Europe/Paris").load()
    loaded = CsvSource(path=str(csv), tz="Europe/Paris", tz_ambiguous="infer").load()
    assert loaded.index.tz_convert("UTC").is_unique


def test_parquet_source(tmp_path):
    path = tmp_path / "m.parquet"
    hourly(power=[1.0, 2.0, 3.0]).to_parquet(path)
    meter = Meter(
        channels=[Channel(name="power", unit="W")], data_source=ParquetSource(path=str(path))
    )
    assert meter.load_data().shape == (3, 1)


def test_single_column_feeds_single_channel(tmp_path):
    path = tmp_path / "m.parquet"
    hourly(whatever=[1.0, 2.0, 3.0]).to_parquet(path)
    meter = Meter(
        channels=[Channel(name="power", unit="W")], data_source=ParquetSource(path=str(path))
    )
    assert meter.load_data().iloc[:, 0].tolist() == [1.0, 2.0, 3.0]


def test_missing_source_column(tmp_path):
    path = tmp_path / "m.parquet"
    hourly(a=[1, 2, 3], b=[1, 2, 3]).to_parquet(path)
    meter = Meter(
        channels=[Channel(name="power", unit="W", column="c")],
        data_source=ParquetSource(path=str(path)),
    )
    with pytest.raises(DataSourceError, match="not found"):
        meter.load_data()


def test_missing_file_raises_data_source_error(tmp_path):
    with pytest.raises(DataSourceError):
        CsvSource(path=str(tmp_path / "nope.csv")).load()


def test_load_without_source():
    with pytest.raises(DataSourceError, match="no data source"):
        Meter(channels=[Channel(name="power", unit="W")]).load_data()


def test_relative_path_resolved_against_base_dir(tmp_path):
    hourly(power=[1, 2, 3]).to_parquet(tmp_path / "m.parquet")
    source = ParquetSource(path="m.parquet")
    source._base_dir = tmp_path
    assert source.load().shape == (3, 1)


class ApiSource(DataSource):
    """A project specific source."""

    type: str = "test_api"
    endpoint: str

    def read(self):
        return hourly(tz="Europe/Paris", power=[5.0, 6.0, 7.0])


def test_custom_source_is_registered_and_round_trips():
    meter = Meter(
        id="m",
        channels=[Channel(name="power", unit="W")],
        data_source=ApiSource(endpoint="https://example.org"),
    )
    data = json.loads(meter.model_dump_json())
    assert data["data_source"] == {
        "type": "test_api",
        "tz": None,
        "tz_ambiguous": "raise",
        "tz_nonexistent": "raise",
        "endpoint": "https://example.org",
    }
    loaded = Meter.model_validate(data)
    assert isinstance(loaded.data_source, ApiSource)
    assert loaded.load_data().iloc[:, 0].tolist() == [5.0, 6.0, 7.0]


def test_unknown_source_type_is_kept():
    data = {
        "id": "m",
        "channels": [{"name": "power", "unit": "W"}],
        "data_source": {"type": "client_sftp", "host": "sftp.example.org"},
    }
    meter = Meter.model_validate(data)
    assert isinstance(meter.data_source, UnknownSource)
    assert json.loads(meter.model_dump_json(exclude_none=True))["data_source"]["host"] == (
        "sftp.example.org"
    )
    with pytest.raises(DataSourceError, match="client_sftp"):
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
    assert park["site-A"].meters() == [site_meter, boiler_meter]  # boiler is a site system
    assert park[RNB_A].meters() == [a_meter]
    assert Building().meters() == []


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
        make_meter("m", ["site-A"], data_source=ParquetSource(path=str(tmp_path / "m.parquet")))
    )
    assert park.timeseries(load_data=True).shape == (3, 1)


def test_meters_with_same_tags_get_distinct_columns(park):
    first = park.add_meter(make_meter("m1", ["site-A"]))
    second = park.add_meter(make_meter("m2", ["site-A"]))
    for meter in (first, second):
        meter.data = hourly(energy=[1, 2, 3])
    assert park.timeseries().shape[1] == 2


def test_source_equality_ignores_base_dir(tmp_path):
    first, second = ParquetSource(path="m.parquet"), ParquetSource(path="m.parquet")
    first._base_dir = tmp_path
    assert first == second
    assert first != ParquetSource(path="other.parquet")
