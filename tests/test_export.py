import sys

import pandas as pd
import pytest

from bstock.building import Building
from bstock.exceptions import InvalidArgumentError, MissingDependencyError
from bstock.park import BuildingPark
from bstock.provenance import sourced
from bstock.site import Site

RNB_A, RNB_B = "A1B2C3D4E5F6", "B1B2C3D4E5F6"
GROUP = "bdnb-bg-1111-1HRT-8SVH"
FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [[[-1.475, 43.49], [-1.474, 43.49], [-1.474, 43.491], [-1.475, 43.49]]],
}


class BayonneSite(Site):
    secteur = sourced(str)


class BayonneBuilding(Building):
    code_patrimoine = sourced(str)


@pytest.fixture
def park():
    """client site > BDNB group site > building A ; building B at the top level."""
    park = BuildingPark(name="Bayonne", site_cls=BayonneSite, building_cls=BayonneBuilding)
    park.new_site("ecole", usage="tertiary", construction_year=1972, secteur="nord", source="bdnb")
    park.new_site(GROUP, parent="ecole", bdnb_batiment_groupe_id=GROUP, dwelling_count=4)
    park.new_building(
        rnb_id=RNB_A,
        parent=GROUP,
        dpe_ids=["2611E0031228S"],
        sub_usages=["school", "canteen"],
        footprint=FOOTPRINT,
        code_patrimoine="BAY-001",
    )
    park.new_building(rnb_id=RNB_B, construction_year=2001, heated_area=850.0, source="dpe")
    return park


def test_one_row_per_building_indexed_by_id(park):
    frame = park.to_dataframe()
    assert frame.index.name == "id"
    assert list(frame.index) == [RNB_B, RNB_A]
    assert list(frame.columns[:5]) == [
        "rnb_id",
        "bdnb_batiment_groupe_id",
        "bdnb_batiment_construction_id",
        "dpe_ids",
        "parent_id",
    ]
    assert frame.loc[RNB_A, "parent_id"] == GROUP
    assert pd.isna(frame.loc[RNB_B, "parent_id"])
    assert frame.loc[RNB_A, "dpe_ids"] == ["2611E0031228S"]


def test_site_rows_include_nested_sites(park):
    frame = park.to_dataframe("site")
    assert list(frame.index) == ["ecole", GROUP]
    assert list(frame.columns[:3]) == ["bdnb_batiment_groupe_id", "dpe_ids", "parent_id"]
    assert frame.loc[GROUP, "parent_id"] == "ecole"
    assert frame.loc[GROUP, "dwelling_count"] == 4
    assert frame.loc["ecole", "secteur"] == "nord"
    assert pd.isna(frame.loc[GROUP, "secteur"])


def test_effective_values_include_inheritance(park):
    frame = park.to_dataframe()
    assert frame.loc[RNB_A, "construction_year"] == 1972
    assert frame.loc[RNB_A, "usage"] == "tertiary"
    assert frame.loc[RNB_B, "construction_year"] == 2001
    assert pd.isna(frame.loc[RNB_A, "dwelling_count"])  # extensive: not inherited


def test_project_subclass_attributes_are_columns(park):
    assert park.to_dataframe().loc[RNB_A, "code_patrimoine"] == "BAY-001"


def test_enums_become_strings_and_lists_are_kept(park):
    frame = park.to_dataframe()
    assert type(frame.loc[RNB_A, "usage"]) is str
    assert frame.loc[RNB_A, "sub_usages"] == ["school", "canteen"]


def test_footprint_is_excluded(park):
    columns = park.to_dataframe(provenance=True).columns
    assert "footprint" not in columns
    assert "footprint_source" not in columns
    assert "footprint_area" in columns


def test_integer_attributes_use_nullable_dtype(park):
    frame = park.to_dataframe()
    assert frame["construction_year"].dtype == "Int64"
    assert frame["heated_area"].dtype == "float64"


def test_provenance_columns_follow_each_attribute(park):
    frame = park.to_dataframe(provenance=True)
    position = list(frame.columns).index("construction_year")
    assert list(frame.columns[position : position + 3]) == [
        "construction_year",
        "construction_year_source",
        "construction_year_provider",
    ]
    assert frame.loc[RNB_A, "construction_year_source"] == "bdnb"
    assert frame.loc[RNB_A, "construction_year_provider"] == "ecole"
    assert frame.loc[RNB_B, "construction_year_source"] == "dpe"
    assert frame.loc[RNB_B, "construction_year_provider"] == RNB_B
    assert pd.isna(frame.loc[RNB_B, "address_source"])


def test_no_provenance_columns_by_default(park):
    assert not any(c.endswith(("_source", "_provider")) for c in park.to_dataframe().columns)


@pytest.mark.parametrize("kind", ["building", "site"])
def test_empty_park_gives_empty_frame_with_columns(kind):
    frame = BuildingPark(name="vide").to_dataframe(kind, provenance=True)
    assert frame.empty
    assert frame.index.name == "id"
    assert "parent_id" in frame.columns
    assert "construction_year_source" in frame.columns


def test_unknown_entity_type_is_rejected(park):
    with pytest.raises(InvalidArgumentError, match="entity must be"):
        park.to_dataframe("meter")


@pytest.mark.parametrize("method", ["to_geodataframe", "to_geopackage", "to_geoparquet"])
def test_geographic_exports_need_the_geo_extra(park, tmp_path, monkeypatch, method):
    monkeypatch.setitem(sys.modules, "geopandas", None)
    args = [] if method == "to_geodataframe" else [tmp_path / "out"]
    with pytest.raises(MissingDependencyError, match=r"bstock\[geo\]"):
        getattr(park, method)(*args)
