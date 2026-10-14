import sys

import pytest

from bstock._geometry import geometry_area, validate_footprint
from bstock.connectors import _geo
from bstock.connectors._geo import to_wgs84
from bstock.exceptions import ConnectorError, MissingDependencyError

SINGLE = "bdnb/batiment_construction_single_bdnb-bg-6SCZ-8433-AMV8.json"


@pytest.fixture
def construction(recorded):
    return recorded(SINGLE)[0]


def test_bdnb_geometry_is_reprojected_to_a_valid_footprint(construction):
    footprint = to_wgs84(construction["geom_cstr"])
    assert "crs" not in footprint
    assert footprint["type"] == "MultiPolygon"
    assert validate_footprint(footprint) == footprint
    lon, lat = footprint["coordinates"][0][0][0]
    assert lon == pytest.approx(-1.47, abs=0.05)  # Bayonne
    assert lat == pytest.approx(43.49, abs=0.05)


def test_reprojected_area_matches_bdnb_area(construction):
    # s_geom_cstr is an integer computed in Lambert-93 (scale error about 1e-3 in Bayonne)
    area = geometry_area(to_wgs84(construction["geom_cstr"]))
    assert area == pytest.approx(construction["s_geom_cstr"], rel=3e-3)


def test_point_is_reprojected_with_altitude():
    point = {"type": "Point", "coordinates": [339879.9, 6274540.7, 36.3]}
    lon, lat, altitude = to_wgs84(point, crs="EPSG:2154")["coordinates"]
    assert altitude == 36.3
    assert -2 < lon < -1
    assert 43 < lat < 44


@pytest.mark.parametrize("name", ["EPSG:2154", "urn:ogc:def:crs:EPSG::2154"])
def test_crs_member_names_are_read(name):
    point = {"type": "Point", "crs": {"type": "name", "properties": {"name": name}}}
    point["coordinates"] = [339879.9, 6274540.7]
    assert to_wgs84(point)["coordinates"][1] == pytest.approx(43.49, abs=0.05)


def test_geometry_without_crs_raises():
    with pytest.raises(ConnectorError, match="crs"):
        to_wgs84({"type": "Point", "coordinates": [0, 0]})


def test_unreadable_crs_raises():
    point = {"type": "Point", "coordinates": [0, 0], "crs": {"properties": {"name": "Lambert"}}}
    with pytest.raises(ConnectorError, match="crs"):
        to_wgs84(point)


def test_missing_pyproj_raises_missing_dependency(monkeypatch):
    _geo._transformer.cache_clear()
    monkeypatch.setitem(sys.modules, "pyproj", None)
    with pytest.raises(MissingDependencyError, match=r"bstock\[geo\]"):
        to_wgs84({"type": "Point", "coordinates": [0, 0]}, crs="EPSG:2154")
    _geo._transformer.cache_clear()
