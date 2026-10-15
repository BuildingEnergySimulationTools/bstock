"""GeoJSON footprints without geographic dependency (SPEC §3.6).

A footprint is a GeoJSON geometry object (RFC 7946), ``Polygon`` or ``MultiPolygon``, with
``[lon, lat]`` positions in WGS84 (EPSG:4326). This module validates it lightly and computes its
area.
"""

from __future__ import annotations

import itertools
import math
from typing import Annotated, Any

from pydantic import AfterValidator, WithJsonSchema

__all__ = ["Footprint", "geometry_area", "validate_footprint"]

FOOTPRINT_TYPES = ("Polygon", "MultiPolygon")

# WGS84 ellipsoid
_SEMI_MAJOR_AXIS = 6_378_137.0  # m
_FLATTENING = 1 / 298.257223563
_ECCENTRICITY_SQUARED = _FLATTENING * (2 - _FLATTENING)


def validate_footprint(geometry: dict[str, Any]) -> dict[str, Any]:
    """Check a GeoJSON ``Polygon`` or ``MultiPolygon`` and return it with list coordinates.

    Parameters
    ----------
    geometry
        GeoJSON geometry object, positions ``[lon, lat]`` (or ``[lon, lat, altitude]``) in degrees
        (WGS84). Members other than ``type`` and ``coordinates`` (``bbox``...) are kept as is.

    Returns
    -------
    dict
        The geometry, with tuples in ``coordinates`` turned into lists.

    Raises
    ------
    ValueError
        If the type is not ``Polygon`` or ``MultiPolygon``, a ring is not closed or has fewer than
        4 positions, or a coordinate is outside the WGS84 bounds.
    """
    kind = geometry.get("type")
    if kind not in FOOTPRINT_TYPES:
        raise ValueError(f"footprint type must be one of {list(FOOTPRINT_TYPES)}, not {kind!r}")
    if "coordinates" not in geometry:
        raise ValueError("footprint has no 'coordinates'")
    coordinates = geometry["coordinates"]
    if kind == "Polygon":
        coordinates = _check_polygon(coordinates)
    else:
        polygons = _check_sequence(coordinates, "MultiPolygon coordinates")
        if not polygons:
            raise ValueError("a MultiPolygon needs at least one polygon")
        coordinates = [_check_polygon(p) for p in polygons]
    return {**geometry, "coordinates": coordinates}


def _check_sequence(value: Any, what: str) -> list[Any]:
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{what} must be an array, not {value!r}")
    return list(value)


def _check_polygon(value: Any) -> list[list[list[float]]]:
    rings = _check_sequence(value, "polygon coordinates")
    if not rings:
        raise ValueError("a polygon needs at least one ring")
    return [_check_ring(ring) for ring in rings]


def _check_ring(value: Any) -> list[list[float]]:
    ring = [_check_position(p) for p in _check_sequence(value, "ring")]
    if len(ring) < 4:
        raise ValueError(f"a ring needs at least 4 positions, got {len(ring)}")
    if ring[0] != ring[-1]:
        raise ValueError("a ring must be closed (first position == last position)")
    return ring


def _check_position(value: Any) -> list[float]:
    position = _check_sequence(value, "position")
    if len(position) not in (2, 3) or not all(_is_number(c) for c in position):
        raise ValueError(f"a position must be [lon, lat] or [lon, lat, altitude], not {value!r}")
    lon, lat = position[0], position[1]
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"position {position} is outside the WGS84 bounds [lon, lat]")
    return position


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


Footprint = Annotated[
    dict[str, Any],
    AfterValidator(validate_footprint),
    WithJsonSchema(
        {
            "type": "object",
            "description": "GeoJSON Polygon or MultiPolygon, [lon, lat] positions in WGS84",
            "properties": {
                "type": {"enum": list(FOOTPRINT_TYPES)},
                "coordinates": {"type": "array"},
            },
            "required": ["type", "coordinates"],
        }
    ),
]
"""Type of the ``footprint`` sourced field."""


def geometry_area(geometry: dict[str, Any]) -> float:
    """Return the area of a valid footprint, in m².

    The positions are projected on a local equirectangular plane centred on the middle of the
    geometry bounding box, scaled by the WGS84 meridian and prime vertical radii of curvature at
    that latitude; the shoelace formula then gives the area. Holes are subtracted and the polygons
    of a ``MultiPolygon`` added up. The error is below 0.1 % at the scale of a building.

    Parameters
    ----------
    geometry
        Footprint already checked by :func:`validate_footprint`.

    Returns
    -------
    float
        Area, in m².
    """
    polygons = (
        [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    )
    positions = [p for polygon in polygons for ring in polygon for p in ring]
    lon0 = (min(p[0] for p in positions) + max(p[0] for p in positions)) / 2
    lat0 = (min(p[1] for p in positions) + max(p[1] for p in positions)) / 2
    phi0 = math.radians(lat0)
    w = math.sqrt(1 - _ECCENTRICITY_SQUARED * math.sin(phi0) ** 2)
    meridian_radius = _SEMI_MAJOR_AXIS * (1 - _ECCENTRICITY_SQUARED) / w**3
    prime_vertical_radius = _SEMI_MAJOR_AXIS / w
    x_scale = prime_vertical_radius * math.cos(phi0) * math.pi / 180  # m per degree of longitude
    y_scale = meridian_radius * math.pi / 180  # m per degree of latitude

    def ring_area(ring: list[list[float]]) -> float:
        xy = [((p[0] - lon0) * x_scale, (p[1] - lat0) * y_scale) for p in ring]
        doubled = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in itertools.pairwise(xy))
        return abs(doubled) / 2

    return sum(
        ring_area(polygon[0]) - sum(ring_area(hole) for hole in polygon[1:]) for polygon in polygons
    )
