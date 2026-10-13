import json
import warnings

import pandas as pd
import pytest

from bstock.building import Building
from bstock.enums import Inertia
from bstock.envelope import EnvelopeComponent, Window
from bstock.exceptions import ClassMismatchWarning, UnknownAttributeWarning, UnsupportedSchemaError
from bstock.io import class_path
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.provenance import SourcePriority, sourced
from bstock.site import Site
from bstock.sources import CsvSource, ParquetSource
from bstock.systems import HeatingGenerator

RNB_A, RNB_B = "A1B2C3D4E5F6", "B1B2C3D4E5F6"
GROUP = "bdnb-bg-1111-1HRT-8SVH"


class BayonneSite(Site):
    secteur = sourced(str)


class BayonneBuilding(Building):
    code_patrimoine = sourced(str)
    surface_utile_client = sourced(float, unit="m²", ge=0)


def hourly(**columns):
    index = pd.date_range("2024-01-01", periods=3, freq="h", tz="Europe/Paris")
    return pd.DataFrame(columns, index=index)


@pytest.fixture
def park():
    """A Bayonne park exercising every part of the model."""
    park = BuildingPark(
        name="Ville de Bayonne",
        site_cls=BayonneSite,
        building_cls=BayonneBuilding,
        source_priority=SourcePriority(per_attribute={"heated_area": ["bayonne_xlsx", "dpe"]}),
    )
    client = park.new_site("site-ecole-A", secteur="Nord", usage="tertiary", source="bayonne_xlsx")
    group = park.new_site(GROUP, parent=client, bdnb_batiment_groupe_id=GROUP)
    group.set("construction_year", 1972, source="bdnb", ref=GROUP, raw="1972")
    group.envelope.set("u_bat", 1.1, source="dpe", ref="2611E0031228S")
    a = park.new_building(RNB_A, site=group, code_patrimoine="BAT-0042", source="bayonne_xlsx")
    a.set("heated_area", 1250.0, source="audit", comment="plans")
    a.envelope.add_component(EnvelopeComponent(id="walls", kind="wall", heat_loss_coefficient=120))
    a.envelope.add_component(Window(id="windows", area=40.0, shgc=0.6, glazing="double"))
    park.new_building(RNB_B, site=group)
    park.new_building(id="bldg-isolated", dpe_ids=["2611E0031228S"])
    park.add_system(
        HeatingGenerator(
            id="plant", serves=[RNB_A], technology="boiler_condensing", source="audit"
        ),
        owner=client,
    )
    meter = park.add_meter(
        Meter(
            id="PDL_123",
            scope=["site-ecole-A"],
            end_use="total",
            channels=[Channel(name="power", unit="W")],
        )
    )
    meter.data = hourly(power=[1.0, 2.0, 3.0])
    return park


# --- JSON -------------------------------------------------------------------------------------


def test_json_contains_metadata_and_candidates_only(park):
    data = json.loads(park.to_json())
    assert data["schema_version"] == 1
    assert data["site_cls"] == class_path(BayonneSite)
    assert data["building_cls"] == class_path(BayonneBuilding)
    group = data["sites"][0]["sites"][0]
    assert group["values"]["construction_year"] == [
        {"value": 1972, "source": "bdnb", "ref": GROUP, "raw": "1972"}
    ]
    assert "construction_year" not in group["buildings"][0]["values"]  # inherited, not copied
    assert "data" not in data["meters"][0]


def test_json_round_trip_with_project_classes(park):
    loaded = BuildingPark.from_json(
        park.to_json(), site_cls=BayonneSite, building_cls=BayonneBuilding
    )
    assert loaded == park
    a = loaded[RNB_A]
    assert type(a) is BayonneBuilding
    assert type(loaded[GROUP]) is BayonneSite
    assert type(loaded["site-ecole-A"]) is BayonneSite
    assert loaded.building_cls is BayonneBuilding
    assert a.code_patrimoine == "BAT-0042"
    assert a.construction_year == 1972
    assert a.provider_id("construction_year") == GROUP
    assert a.usage == "tertiary"
    assert a.envelope.u_bat == 1.1
    assert type(a.envelope.components[1]) is Window
    assert [s.id for s in a.systems("heating")] == ["plant"]
    assert loaded.get_by_rnb(RNB_B) is loaded[RNB_B]
    assert a.meters() == []
    assert loaded["site-ecole-A"].meters()[0].id == "PDL_123"


def test_round_trip_keeps_source_priority(park):
    loaded = BuildingPark.from_json(
        park.to_json(), site_cls=BayonneSite, building_cls=BayonneBuilding
    )
    assert loaded.source_priority == park.source_priority
    a = loaded[RNB_A]
    a.set("heated_area", 1000.0, source="bayonne_xlsx")
    assert a.heated_area == 1000.0  # per-attribute priority survived


def test_loading_with_base_classes_keeps_project_attributes(park):
    text = park.to_json()
    with pytest.warns(UnknownAttributeWarning), pytest.warns(ClassMismatchWarning):
        plain = BuildingPark.from_json(text)
    assert type(plain[RNB_A]) is Building
    assert plain[RNB_A].candidates("code_patrimoine")[0].value == "BAT-0042"
    # saving again writes them back unchanged, and project classes read them normally
    with pytest.warns(ClassMismatchWarning):
        reloaded = BuildingPark.from_json(
            plain.to_json(), site_cls=BayonneSite, building_cls=BayonneBuilding
        )
    assert reloaded[RNB_A].code_patrimoine == "BAT-0042"


def test_matching_classes_do_not_warn(park):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        BuildingPark.from_json(park.to_json(), site_cls=BayonneSite, building_cls=BayonneBuilding)


def test_unsupported_schema_version(park):
    data = json.loads(park.to_json())
    data["schema_version"] = 99
    with pytest.raises(UnsupportedSchemaError):
        BuildingPark.from_json(json.dumps(data))


def test_empty_park_round_trip():
    park = BuildingPark(name="empty")
    assert BuildingPark.from_json(park.to_json()) == park


# --- directories ------------------------------------------------------------------------------


def test_save_and_load_without_data(park, tmp_path):
    path = park.save(tmp_path / "bayonne")
    assert path == tmp_path / "bayonne" / "park.json"
    loaded = BuildingPark.load(
        tmp_path / "bayonne", site_cls=BayonneSite, building_cls=BayonneBuilding
    )
    assert loaded == park
    assert loaded["PDL_123"].data is None
    assert not (tmp_path / "bayonne" / "timeseries").exists()


def test_save_with_data_makes_park_autonomous(park, tmp_path):
    csv = tmp_path / "enedis.csv"
    csv.write_text("t,v\n", encoding="utf-8")
    meter = park["PDL_123"]
    meter.data_source = CsvSource(path=str(csv), index_col="t")
    expected = meter.data.copy()

    park.save(tmp_path / "bayonne", include_data=True)
    assert (tmp_path / "bayonne" / "timeseries" / "PDL_123.parquet").exists()
    assert isinstance(meter.data_source, ParquetSource)
    assert meter.data_source.path == "timeseries/PDL_123.parquet"
    assert isinstance(meter.original_data_source, CsvSource)

    csv.unlink()  # the original data is gone
    loaded = BuildingPark.load(
        tmp_path / "bayonne", site_cls=BayonneSite, building_cls=BayonneBuilding
    )
    df = loaded.timeseries(load_data=True, tz="Europe/Paris")
    pd.testing.assert_frame_equal(df, expected, check_freq=False)
    assert isinstance(loaded["PDL_123"].original_data_source, CsvSource)


def test_saving_twice_keeps_first_original_source(park, tmp_path):
    park["PDL_123"].data_source = CsvSource(path=str(tmp_path / "x.csv"))
    park.save(tmp_path / "p", include_data=True)
    park.save(tmp_path / "p", include_data=True)
    assert isinstance(park["PDL_123"].original_data_source, CsvSource)


def test_relative_source_paths_are_made_absolute(park, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    park["PDL_123"].data_source = CsvSource(path="data/m.csv")
    park.save(tmp_path / "p")
    saved = json.loads((tmp_path / "p" / "park.json").read_text(encoding="utf-8"))
    assert saved["meters"][0]["data_source"]["path"] == str(tmp_path / "data" / "m.csv")


def test_moved_park_directory_still_loads_data(park, tmp_path):
    park.save(tmp_path / "first", include_data=True)
    (tmp_path / "first").rename(tmp_path / "moved")
    loaded = BuildingPark.load(
        tmp_path / "moved", site_cls=BayonneSite, building_cls=BayonneBuilding
    )
    assert loaded["PDL_123"].load_data().shape == (3, 1)


def test_multi_channel_meter_round_trip(tmp_path):
    park = BuildingPark(name="p")
    park.new_site("s")
    meter = park.add_meter(
        Meter(
            id="m",
            scope=["s"],
            channels=[Channel(name="power", unit="W"), Channel(name="energy", unit="kWh")],
        )
    )
    meter.data = hourly(power=[1, 2, 3], energy=[4, 5, 6])
    park.save(tmp_path, include_data=True)
    loaded = BuildingPark.load(tmp_path)
    assert list(loaded["m"].load_data().columns) == list(meter.data.columns)


# --- schema -----------------------------------------------------------------------------------


def test_json_schema_lists_sourced_attributes_with_units():
    schema = BuildingPark.json_schema(site_cls=BayonneSite, building_cls=BayonneBuilding)
    sourced_attributes = schema["x-bstock-sourced-attributes"]
    assert sourced_attributes["BayonneBuilding"]["surface_utile_client"]["unit"] == "m²"
    assert sourced_attributes["BayonneBuilding"]["construction_year"]["inherit"] is True
    assert sourced_attributes["Envelope"]["inertia"]["schema"]["enum"] == [i.value for i in Inertia]
    assert sourced_attributes["HeatingGenerator"]["nominal_power"]["unit"] == "kW"
    assert "sites" in schema["properties"]
    json.dumps(schema)  # serialisable
