import json

import pytest

gpd = pytest.importorskip("geopandas")  # the [geo] extra

from bstock.exceptions import InvalidArgumentError  # noqa: E402
from bstock.park import BuildingPark  # noqa: E402

FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [[[-1.475, 43.49], [-1.474, 43.49], [-1.474, 43.491], [-1.475, 43.49]]],
}
MULTI = {"type": "MultiPolygon", "coordinates": [FOOTPRINT["coordinates"]]}


@pytest.fixture
def park():
    """Site with a point and a footprint; a building inheriting the point; one with nothing."""
    park = BuildingPark(name="Bayonne")
    park.new_site("ecole", lat=43.49, lon=-1.47, footprint=MULTI, dpe_ids=["2664E2519948T"])
    park.new_building(
        "A1B2C3D4E5F6",
        parent="ecole",
        footprint=FOOTPRINT,
        sub_usages=["school", "canteen"],
        usage="tertiary",
        construction_year=1972,
    )
    park.new_building(id="vide")
    return park


def test_footprint_geometries_in_wgs84(park):
    frame = park.to_geodataframe()
    assert frame.crs.to_epsg() == 4326
    assert frame.index.name == "id"
    assert frame.loc["A1B2C3D4E5F6", "geometry"].geom_type == "Polygon"
    assert frame.loc["A1B2C3D4E5F6", "geometry"].bounds == pytest.approx(
        (-1.475, 43.49, -1.474, 43.491)
    )
    assert frame.loc["vide", "geometry"] is None
    assert "footprint" not in frame.columns
    assert frame.loc["A1B2C3D4E5F6", "usage"] == "tertiary"


def test_point_geometry_is_inherited(park):
    frame = park.to_geodataframe(geometry="point")
    point = frame.loc["A1B2C3D4E5F6", "geometry"]
    assert (point.x, point.y) == (-1.47, 43.49)
    assert frame.loc["vide", "geometry"] is None


def test_site_geodataframe(park):
    frame = park.to_geodataframe("site", provenance=True)
    assert frame.loc["ecole", "geometry"].geom_type == "MultiPolygon"
    assert "lat_source" in frame.columns


def test_invalid_geometry_kind_is_rejected(park):
    with pytest.raises(InvalidArgumentError, match="geometry"):
        park.to_geodataframe(geometry="centroid")


def test_geopackage_round_trip(park, tmp_path):
    path = park.to_geopackage(tmp_path / "parc.gpkg")
    buildings = gpd.read_file(path, layer="buildings").set_index("id")
    sites = gpd.read_file(path, layer="sites").set_index("id")
    assert set(buildings.index) == {"A1B2C3D4E5F6", "vide"}
    assert json.loads(buildings.loc["A1B2C3D4E5F6", "sub_usages"]) == ["school", "canteen"]
    assert json.loads(sites.loc["ecole", "dpe_ids"]) == ["2664E2519948T"]
    assert buildings.loc["A1B2C3D4E5F6", "construction_year"] == 1972
    assert buildings.loc["A1B2C3D4E5F6", "geometry"].geom_type == "Polygon"
    assert buildings.loc["vide", "geometry"] is None
    assert buildings.crs.to_epsg() == 4326


def test_geopackage_is_overwritten(park, tmp_path):
    path = tmp_path / "parc.gpkg"
    park.to_geopackage(path)
    park.remove("vide")
    park.to_geopackage(path)
    assert list(gpd.read_file(path, layer="buildings")["id"]) == ["A1B2C3D4E5F6"]


def test_empty_park_writes_empty_layers(tmp_path):
    path = BuildingPark(name="vide").to_geopackage(tmp_path / "vide.gpkg")
    assert gpd.read_file(path, layer="sites").empty
    assert gpd.read_file(path, layer="buildings").empty


def test_geoparquet_round_trip_keeps_lists(park, tmp_path):
    path = park.to_geoparquet(tmp_path / "batiments.parquet", geometry="point")
    frame = gpd.read_parquet(path)
    assert frame.index.name == "id"
    assert list(frame.loc["A1B2C3D4E5F6", "sub_usages"]) == ["school", "canteen"]
    assert frame.loc["A1B2C3D4E5F6", "geometry"].geom_type == "Point"
    assert frame.crs.to_epsg() == 4326
    assert frame["construction_year"].dtype == "Int64"
