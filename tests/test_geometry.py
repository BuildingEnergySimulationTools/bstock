import math

import pytest

from bstock._geometry import geometry_area, validate_footprint

# Bayonne, about 43.49° N
LON0, LAT0 = -1.475, 43.49


def box(lon1, lat1, lon2, lat2):
    """Closed, counter-clockwise ring of a lon/lat box."""
    return [[lon1, lat1], [lon2, lat1], [lon2, lat2], [lon1, lat2], [lon1, lat1]]


def polygon(*rings):
    return {"type": "Polygon", "coordinates": list(rings)}


def ellipsoid_box_area(lon1, lat1, lon2, lat2):
    """Exact area of a lon/lat box on the WGS84 ellipsoid, in m²."""
    a, f = 6_378_137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    e = math.sqrt(e2)
    b2 = a**2 * (1 - e2)

    def q(lat):
        s = math.sin(math.radians(lat))
        return s / (1 - e2 * s**2) + math.log((1 + e * s) / (1 - e * s)) / (2 * e)

    return b2 / 2 * math.radians(lon2 - lon1) * (q(lat2) - q(lat1))


# --- validation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [LON0, LAT0]},
        {"coordinates": [box(0, 0, 1, 1)]},
        {"type": "Polygon"},
        {"type": "Polygon", "coordinates": []},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
        {"type": "Polygon", "coordinates": [box(0, 0, 1, 91)]},
        {"type": "Polygon", "coordinates": [box(179, 0, 181, 1)]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, "1"], [0, 0]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1, 2, 3], [0, 0]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, math.nan], [0, 0]]]},
        {"type": "Polygon", "coordinates": "nope"},
        {"type": "MultiPolygon", "coordinates": []},
        {"type": "MultiPolygon", "coordinates": [box(0, 0, 1, 1)]},
    ],
)
def test_invalid_footprint_is_rejected(geometry):
    with pytest.raises(ValueError, match=r"footprint|polygon|MultiPolygon|ring|position"):
        validate_footprint(geometry)


def test_valid_footprint_is_returned_with_list_coordinates():
    ring = tuple(tuple(p) for p in box(0, 0, 1, 1))
    checked = validate_footprint({"type": "Polygon", "coordinates": (ring,), "bbox": [0, 0, 1, 1]})
    assert checked == {"type": "Polygon", "coordinates": [box(0, 0, 1, 1)], "bbox": [0, 0, 1, 1]}


def test_positions_with_altitude_are_accepted():
    ring = [[*p, 12.5] for p in box(0, 0, 1, 1)]
    assert validate_footprint(polygon(ring))["coordinates"] == [ring]


# --- area -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "size"), [(LAT0, 0.0005), (LAT0, 0.003), (0.0, 0.001), (65.0, 0.001)]
)
def test_area_matches_ellipsoid_reference(lat, size):
    lon1, lat1, lon2, lat2 = LON0, lat, LON0 + size, lat + size
    reference = ellipsoid_box_area(lon1, lat1, lon2, lat2)
    assert geometry_area(polygon(box(lon1, lat1, lon2, lat2))) == pytest.approx(reference, rel=1e-3)


def test_ring_orientation_does_not_change_area():
    ring = box(LON0, LAT0, LON0 + 0.001, LAT0 + 0.001)
    assert geometry_area(polygon(ring[::-1])) == pytest.approx(geometry_area(polygon(ring)))


def test_holes_are_subtracted():
    outer = box(LON0, LAT0, LON0 + 0.002, LAT0 + 0.002)
    hole = box(LON0 + 0.0005, LAT0 + 0.0005, LON0 + 0.0015, LAT0 + 0.0015)
    reference = ellipsoid_box_area(*outer[0], *outer[2]) - ellipsoid_box_area(*hole[0], *hole[2])
    assert geometry_area(polygon(outer, hole)) == pytest.approx(reference, rel=1e-3)


def test_multipolygon_areas_are_added():
    first = box(LON0, LAT0, LON0 + 0.001, LAT0 + 0.001)
    second = box(LON0 + 0.002, LAT0, LON0 + 0.003, LAT0 + 0.0005)
    geometry = {"type": "MultiPolygon", "coordinates": [[first], [second]]}
    reference = ellipsoid_box_area(*first[0], *first[2]) + ellipsoid_box_area(
        *second[0], *second[2]
    )
    assert geometry_area(geometry) == pytest.approx(reference, rel=1e-3)
