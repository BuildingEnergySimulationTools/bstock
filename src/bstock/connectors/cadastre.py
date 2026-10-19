"""Connector to the cadastral plots of the IGN API Carto (SPEC §8.3, annex A.6).

Only a raw client: it finds the plots at a point, whose RNB buildings
:func:`bstock.connectors.rnb.suggest_site_matches` then lists. Requires the ``[connectors]`` extra.
"""

from __future__ import annotations

import json
import math
from typing import Any

from bstock.connectors._http import HttpClient
from bstock.exceptions import InvalidArgumentError

__all__ = ["CadastreClient", "plot_id"]

BASE_URL = "https://apicarto.ign.fr/api/cadastre/"
MAX_RADIUS = 1000.0
"""Largest search radius accepted by :meth:`CadastreClient.plots`, in m."""
EARTH_RADIUS = 6_371_000.0
"""Mean Earth radius, in m."""
_CIRCLE_SIDES = 16


class CadastreClient:
    """Raw access to the cadastre of the IGN API Carto: GeoJSON features out.

    The methods raise :class:`~bstock.exceptions.ConnectorError` when the API fails persistently.

    Parameters
    ----------
    base_url
        API root, ending with ``/``.
    session
        ``requests.Session`` to use, e.g. a cached one; a new session by default.
    **http_options
        Other :class:`~bstock.connectors._http.HttpClient` options (``timeout`` in s,
        ``max_retries``, ``backoff``...).

    Raises
    ------
    MissingDependencyError
        If ``requests`` is not installed.
    """

    def __init__(
        self, *, base_url: str = BASE_URL, session: Any | None = None, **http_options: Any
    ) -> None:
        self.http = HttpClient(base_url, session=session, **http_options)

    def plots(self, lat: float, lon: float, radius: float = 0.0) -> list[dict[str, Any]]:
        """Return the plots containing the point, or intersecting a circle around it.

        Parameters
        ----------
        lat, lon
            Point, in degrees (WGS84).
        radius
            Search radius, in m (at most 1000); ``0`` gives the plot containing the point. The
            circle is approximated by a 16-sided polygon.

        Returns
        -------
        list of dict
            GeoJSON features (WGS84) whose ``properties`` hold ``idu``, the 14-character plot id
            (INSEE code, prefix, section, number), ``code_insee`` and ``contenance`` (area, m²).

        Raises
        ------
        InvalidArgumentError
            If ``radius`` is not in [0, 1000].
        """
        if not 0 <= radius <= MAX_RADIUS:
            raise InvalidArgumentError(f"radius must be in [0, {MAX_RADIUS:g}] m, not {radius!r}")
        geometry = (
            {"type": "Point", "coordinates": [lon, lat]}
            if radius == 0
            else {"type": "Polygon", "coordinates": [_circle(lat, lon, radius)]}
        )
        response = self.http.get_json("parcelle", {"geom": json.dumps(geometry)}) or {}
        return list(response.get("features") or [])


def plot_id(feature: dict[str, Any]) -> str | None:
    """Return the plot id (``idu``) of a feature of :meth:`CadastreClient.plots`, if any."""
    return (feature.get("properties") or {}).get("idu")


def _circle(lat: float, lon: float, radius: float) -> list[list[float]]:
    """Return a closed ring of ``[lon, lat]`` approximating the circle of ``radius`` m."""
    dlat = math.degrees(radius / EARTH_RADIUS)
    dlon = dlat / math.cos(math.radians(lat))
    ring = [
        [
            lon + dlon * math.cos(2 * math.pi * k / _CIRCLE_SIDES),
            lat + dlat * math.sin(2 * math.pi * k / _CIRCLE_SIDES),
        ]
        for k in range(_CIRCLE_SIDES)
    ]
    return [*ring, ring[0]]
