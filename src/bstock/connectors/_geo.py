"""Reprojection of source geometries to WGS84 (extra ``[geo]``)."""

from __future__ import annotations

import functools
import re
from typing import Any

from bstock._deps import require
from bstock.exceptions import ConnectorError

__all__ = ["LAMBERT_93", "to_wgs84"]

LAMBERT_93 = "EPSG:2154"
_EPSG_CODE = re.compile(r"EPSG:+(\d+)$", re.IGNORECASE)


def to_wgs84(geometry: dict[str, Any], crs: str | None = None) -> dict[str, Any]:
    """Return ``geometry`` reprojected to WGS84 (EPSG:4326), ``[lon, lat]`` positions.

    Parameters
    ----------
    geometry
        GeoJSON geometry. Its ``crs`` member (as in the BDNB responses), if any, gives the source
        system; it is dropped from the result.
    crs
        Source system when the geometry has no ``crs`` member, e.g. ``"EPSG:2154"``.

    Returns
    -------
    dict
        The geometry with reprojected coordinates (altitudes kept) and without ``crs``.

    Raises
    ------
    ConnectorError
        If the source system is unknown or unreadable.
    MissingDependencyError
        If pyproj is not installed.
    """
    source = _crs_name(geometry.get("crs")) or crs
    if source is None:
        raise ConnectorError("the geometry has no 'crs' member and no crs was given")
    transform = _transformer(source).transform
    result = {k: v for k, v in geometry.items() if k != "crs"}
    result["coordinates"] = _map_positions(geometry["coordinates"], transform)
    return result


def _crs_name(crs: Any) -> str | None:
    """Return ``EPSG:<code>`` from a GeoJSON 2008 ``crs`` member, or ``None``."""
    if crs is None:
        return None
    name = crs.get("properties", {}).get("name") if isinstance(crs, dict) else None
    match = _EPSG_CODE.search(name) if isinstance(name, str) else None
    if match is None:
        raise ConnectorError(f"unreadable geometry crs {crs!r}")
    return f"EPSG:{match.group(1)}"


@functools.cache
def _transformer(source: str) -> Any:
    pyproj = require("pyproj", "geo")
    try:
        return pyproj.Transformer.from_crs(source, "EPSG:4326", always_xy=True)
    except pyproj.exceptions.CRSError as error:
        raise ConnectorError(f"unknown coordinate reference system {source!r}") from error


def _map_positions(coordinates: Any, transform: Any) -> Any:
    if coordinates and isinstance(coordinates[0], (int, float)):
        lon, lat = transform(coordinates[0], coordinates[1])
        return [lon, lat, *coordinates[2:]]
    return [_map_positions(c, transform) for c in coordinates]
