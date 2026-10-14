"""Connector to the RNB, the national building register (SPEC §8.3, annex A.6).

Three layers: :class:`RnbClient` fetches raw responses, :func:`building_candidates` translates one
building into candidate values, and :func:`add_buildings`, :func:`enrich` and
:func:`suggest_matches` work on a park. Requires the ``[connectors]`` extra.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock._geometry import validate_footprint
from bstock._ids import normalize_rnb_id
from bstock.building import Building
from bstock.connectors._http import HttpClient
from bstock.connectors._report import ImportReport
from bstock.connectors._sync import put, select_buildings
from bstock.exceptions import ConnectorError
from bstock.provenance import SourcedValue

if TYPE_CHECKING:
    from bstock.park import BuildingPark

__all__ = [
    "SOURCE",
    "RnbClient",
    "add_buildings",
    "bdnb_construction_id",
    "building_candidates",
    "enrich",
    "format_address",
    "normalize_rnb_id",
    "successors",
    "suggest_matches",
]

logger = logging.getLogger(__name__)

SOURCE = "rnb"
BASE_URL = "https://rnb-api.beta.gouv.fr/api/alpha/"
MAX_RADIUS = 1000.0
"""Largest search radius accepted by the API, in m."""

SUGGESTION_COLUMNS = [
    "building_id",
    "rnb_id",
    "distance",
    "score",
    "address",
    "used_by",
    "assigned",
]


class RnbClient:
    """Raw access to the RNB API: one method per endpoint, JSON dicts out.

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
        self, base_url: str = BASE_URL, *, session: Any | None = None, **http_options: Any
    ) -> None:
        self.http = HttpClient(base_url, session=session, **http_options)

    def building(self, rnb_id: str) -> dict[str, Any] | None:
        """Return the building ``rnb_id``, or ``None`` if the RNB does not know it."""
        return self.http.get_json(f"buildings/{normalize_rnb_id(rnb_id)}/", not_found_ok=True)

    def history(self, rnb_id: str) -> list[dict[str, Any]]:
        """Return the versions of the building ``rnb_id``, most recent first."""
        return self.http.get_json(f"buildings/{normalize_rnb_id(rnb_id)}/history/") or []

    def closest(self, lat: float, lon: float, radius: float) -> list[dict[str, Any]]:
        """Return the buildings within ``radius`` m of the point, nearest first.

        Parameters
        ----------
        lat, lon
            Point, in degrees (WGS84).
        radius
            Search radius, in m (at most 1000).
        """
        params = {"point": f"{lat},{lon}", "radius": radius, "limit": 100}
        return list(self.http.paginate("buildings/closest/", params))

    def search_address(self, address: str) -> dict[str, Any]:
        """Return the address search response: ``status``, ``score_ban`` and ``results``."""
        return self.http.get_json("buildings/address/", {"q": address}) or {}


# --- correspondences --------------------------------------------------------------------------


def format_address(address: dict[str, Any]) -> str | None:
    """Return a one-line address (``"4 Rue de la Gabarre, 64100 Bayonne"``), or ``None``."""
    street = " ".join(
        str(part)
        for part in (address.get("street_number"), address.get("street_rep"), address.get("street"))
        if part
    )
    city = " ".join(str(p) for p in (address.get("city_zipcode"), address.get("city_name")) if p)
    text = ", ".join(part for part in (street, city) if part)
    return text or None


def building_candidates(record: dict[str, Any]) -> dict[str, SourcedValue]:
    """Translate an RNB building into ``"rnb"`` candidates, by attribute name.

    ``point`` gives ``lat`` / ``lon``, ``shape`` the ``footprint`` (only a valid ``Polygon`` or
    ``MultiPolygon``), the first address ``address``, ``municipality`` and ``insee_code``. Each
    candidate has the RNB id as ``ref``; the RNB gives no date.

    Raises
    ------
    ConnectorError
        If the record has no ``rnb_id``.
    """
    try:
        ref = record["rnb_id"]
    except (KeyError, TypeError):
        raise ConnectorError(f"unexpected RNB building: {record!r:.200}") from None

    def candidate(value: Any, **kwargs: Any) -> SourcedValue:
        return SourcedValue(value=value, source=SOURCE, ref=ref, **kwargs)

    found: dict[str, SourcedValue] = {}
    point = record.get("point") or {}
    if point.get("type") == "Point" and len(point.get("coordinates") or []) >= 2:
        lon, lat = point["coordinates"][:2]
        found["lon"], found["lat"] = candidate(lon), candidate(lat)
    shape = record.get("shape") or {}
    if shape.get("type") in ("Polygon", "MultiPolygon"):
        try:
            found["footprint"] = candidate(validate_footprint(shape))
        except ValueError as error:
            logger.info("RNB %s: shape ignored (%s)", ref, error)
    addresses = record.get("addresses") or []
    if addresses:
        first = addresses[0]
        ban = f"BAN {first['id']}" if first.get("id") else None
        text = format_address(first)
        if text:
            found["address"] = candidate(text, comment=ban)
        if first.get("city_name"):
            found["municipality"] = candidate(first["city_name"])
        if first.get("city_insee_code"):
            found["insee_code"] = candidate(first["city_insee_code"])
    return found


def bdnb_construction_id(record: dict[str, Any]) -> str | None:
    """Return the BDNB construction id among the external ids of an RNB building, if any."""
    for ext in record.get("ext_ids") or []:
        if ext.get("source") == "bdnb" and ext.get("id"):
            return str(ext["id"])
    return None


def successors(history: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """Return the event that deactivated an id and the successor ids.

    The history lists the versions of the building, most recent first. The event is ``"merge"``,
    ``"split"`` or ``"deactivation"`` (``None`` if no version is inactive).
    """
    for version in history:
        event = version.get("event") or {}
        if version.get("is_active") is False and event.get("type"):
            details = event.get("details") or {}
            if event["type"] == "merge" and details.get("merge_child"):
                return "merge", [details["merge_child"]]
            if event["type"] == "split":
                return "split", list(details.get("split_children") or [])
            return event["type"], []
    return None, []


# --- park operations --------------------------------------------------------------------------


def add_buildings(
    park: BuildingPark,
    rnb_ids: Iterable[str],
    parent: Any | None = None,
    *,
    client: RnbClient | None = None,
) -> ImportReport:
    """Create the buildings ``rnb_ids`` missing from the park and enrich the existing ones.

    New buildings are created with ``park.new_building`` (so with the park ``building_cls``) under
    ``parent`` (a site or its id, ``None`` for the top level). One request per id.

    Parameters
    ----------
    park
        Park to complete.
    rnb_ids
        RNB ids, in any case, with or without dashes.
    parent
        Site receiving the created buildings.
    client
        RNB client; a default one is created when omitted.

    Returns
    -------
    ImportReport
        ``created`` and ``enriched`` building ids, ``not_found`` RNB ids, and a message for the
        demolished and inactive ids (with their successors), which are left aside.

    Raises
    ------
    UnknownIdError
        If ``parent`` is not a site of the park.
    ConnectorError
        If the API fails persistently.
    """
    if parent is not None:
        park._resolve_site(parent)  # fail before any request
    client = client or RnbClient()
    report = ImportReport(source=SOURCE)
    for rnb_id in dict.fromkeys(normalize_rnb_id(i) for i in rnb_ids):
        record = _fetch_usable(client, rnb_id, report)
        if record is None:
            continue
        building = _find(park, record["rnb_id"])
        if building is None:
            building = park.new_building(rnb_id=record["rnb_id"], parent=parent)
            _apply(building, record)
            report.add("created", building.id)
        else:
            _apply(building, record)
            report.add("enriched", building.id)
    return report


def enrich(
    park: BuildingPark,
    buildings: Iterable[Building | str] | None = None,
    *,
    client: RnbClient | None = None,
) -> ImportReport:
    """Update the RNB values of the park buildings that have an ``rnb_id``.

    Parameters
    ----------
    park
        Park to enrich.
    buildings
        Buildings (objects or ids) to enrich; every building with an ``rnb_id`` when ``None``.
        Buildings without ``rnb_id`` are reported and skipped.
    client
        RNB client; a default one is created when omitted.

    Returns
    -------
    ImportReport
        ``enriched`` building ids; ``not_found`` for unknown RNB ids; messages for buildings
        without ``rnb_id``, demolished or inactive ones, which are left unchanged.

    Raises
    ------
    UnknownIdError
        If a building id is not in the park.
    ConnectorError
        If the API fails persistently.
    """
    client = client or RnbClient()
    report = ImportReport(source=SOURCE)
    for building in select_buildings(park, buildings):
        if not building.rnb_id:
            report.note(building.id, "no rnb_id: use rnb.suggest_matches")
            continue
        record = _fetch_usable(client, normalize_rnb_id(building.rnb_id), report, building.id)
        if record is not None:
            _apply(building, record)
            report.add("enriched", building.id)
    return report


def suggest_matches(
    park: BuildingPark,
    buildings: Iterable[Building | str] | None = None,
    *,
    by: Literal["point", "address"] = "point",
    radius: float = 30.0,
    assign: Literal["unique"] | None = None,
    client: RnbClient | None = None,
) -> pd.DataFrame:
    """Propose RNB buildings for park buildings that have no ``rnb_id`` (SPEC §8.3).

    Parameters
    ----------
    park
        Park whose buildings are matched.
    buildings
        Buildings (objects or ids) to match; every building without ``rnb_id`` when ``None``.
    by
        ``"point"``: RNB buildings within ``radius`` of the effective ``lat`` / ``lon`` (inherited
        from the site when the building has none). ``"address"``: the RNB buildings at the
        effective ``address``, geocoded by the RNB with the BAN.
    radius
        Search radius around the point, in m (at most 1000).
    assign
        ``"unique"`` sets ``rnb_id`` when a building has exactly one candidate that no other park
        building uses. ``None`` (default) changes nothing.
    client
        RNB client; a default one is created when omitted.

    Returns
    -------
    pandas.DataFrame
        One row per (building, candidate): ``building_id``, ``rnb_id``, ``distance`` (m, 0 inside
        the footprint; point search), ``score`` (BAN geocoding score; address search),
        ``address`` (RNB), ``used_by`` (park building already holding this ``rnb_id``, never
        assigned) and ``assigned``. Buildings without point (or address) or without candidate have
        no row.

    Raises
    ------
    ValueError
        If ``by``, ``radius`` or ``assign`` is invalid.
    UnknownIdError
        If a building id is not in the park.
    ConnectorError
        If the API fails persistently.
    """
    if by not in ("point", "address"):
        raise ValueError(f"by must be 'point' or 'address', not {by!r}")
    if not 0 < radius <= MAX_RADIUS:
        raise ValueError(f"radius must be in ]0, {MAX_RADIUS:g}] m, not {radius!r}")
    if assign not in (None, "unique"):
        raise ValueError(f"assign must be None or 'unique', not {assign!r}")
    client = client or RnbClient()
    targets = (
        [b for b in park.iter_buildings() if not b.rnb_id]
        if buildings is None
        else list(select_buildings(park, buildings))
    )
    rows: list[dict[str, Any]] = []
    for building in targets:
        found = list(_candidates(client, building, by, radius))
        for candidate in found:
            used = _find(park, candidate["rnb_id"])
            candidate.update(building_id=building.id, used_by=used and used.id, assigned=False)
        if assign == "unique" and len(found) == 1 and found[0]["used_by"] is None:
            building.rnb_id = found[0]["rnb_id"]
            found[0]["assigned"] = True
        rows.extend(found)
    return pd.DataFrame(rows, columns=SUGGESTION_COLUMNS)


# --- helpers ----------------------------------------------------------------------------------


def _fetch_usable(
    client: RnbClient, rnb_id: str, report: ImportReport, entity_id: str | None = None
) -> dict[str, Any] | None:
    """Return the RNB record of ``rnb_id``, or ``None`` (reported) if it cannot be used."""
    key = entity_id or rnb_id
    record = client.building(rnb_id)
    if record is None:
        report.add("not_found", key, f"RNB id {rnb_id} is unknown to the RNB")
        return None
    if record.get("status") == "demolished":
        report.note(key, f"RNB id {rnb_id} is a demolished building: left unchanged")
        return None
    if record.get("is_active") is False:
        event, ids = successors(client.history(rnb_id))
        detail = f" after a {event}" if event else ""
        following = f"; successors: {', '.join(ids)}" if ids else "; no successor"
        report.note(key, f"RNB id {rnb_id} is inactive{detail}{following}: left unchanged")
        return None
    return record


def _apply(building: Building, record: dict[str, Any]) -> None:
    put(building, building_candidates(record), source=SOURCE, clear=True)
    construction = bdnb_construction_id(record)
    if construction and not building.bdnb_batiment_construction_id:
        building.bdnb_batiment_construction_id = construction


def _find(park: BuildingPark, rnb_id: str) -> Building | None:
    """Return the park building whose normalised ``rnb_id`` is ``rnb_id``."""
    found = park.get_by_rnb(rnb_id)
    if found is not None:
        return found
    target = normalize_rnb_id(rnb_id)
    return next(
        (b for b in park.iter_buildings() if b.rnb_id and normalize_rnb_id(b.rnb_id) == target),
        None,
    )


def _candidates(
    client: RnbClient, building: Building, by: str, radius: float
) -> Iterator[dict[str, Any]]:
    if by == "point":
        if building.lat is None or building.lon is None:
            logger.info("%s: no lat/lon, no RNB suggestion", building.id)
            return
        for record in client.closest(building.lat, building.lon, radius):
            yield _suggestion(record, distance=record.get("distance"))
        return
    if not building.address:
        logger.info("%s: no address, no RNB suggestion", building.id)
        return
    response = client.search_address(building.address)
    if response.get("status") != "ok":
        logger.info("%s: RNB address search status %s", building.id, response.get("status"))
    for record in response.get("results") or []:
        yield _suggestion(record, score=response.get("score_ban"))


def _suggestion(
    record: dict[str, Any], *, distance: float | None = None, score: float | None = None
) -> dict[str, Any]:
    addresses = record.get("addresses") or []
    return {
        "rnb_id": record["rnb_id"],
        "distance": distance,
        "score": score,
        "address": format_address(addresses[0]) if addresses else None,
    }
