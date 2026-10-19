"""Connector to the RNB, the national building register (SPEC §8.3, annex A.6).

Three layers: :class:`RnbClient` fetches raw responses, internal correspondence functions translate
one building into candidate values, and the other public functions work on a park. Requires the
``[connectors]`` extra.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock._geometry import validate_footprint
from bstock._ids import normalize_rnb_id
from bstock.building import Building
from bstock.connectors._http import HttpClient
from bstock.connectors._report import ImportReport
from bstock.connectors._sync import put, select_buildings
from bstock.connectors.cadastre import EARTH_RADIUS, CadastreClient, plot_id
from bstock.exceptions import ConnectorError, InvalidArgumentError
from bstock.provenance import SourcedValue

if TYPE_CHECKING:
    from bstock.park import BuildingPark
    from bstock.site import Site

__all__ = [
    "SOURCE",
    "RnbClient",
    "add_buildings",
    "add_buildings_at_address",
    "assign_matches",
    "assign_site_matches",
    "enrich",
    "normalize_rnb_id",
    "suggest_matches",
    "suggest_site_matches",
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
]

SITE_MATCH_COLUMNS = [
    "site_id",
    "rnb_id",
    "status",
    "method",
    "plot_id",
    "cover_ratio",
    "distance",
    "address",
    "used_by",
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
        self, *, base_url: str = BASE_URL, session: Any | None = None, **http_options: Any
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

    def plot_buildings(self, plot_id: str) -> list[dict[str, Any]]:
        """Return the buildings on a cadastral plot, with their ``bdg_cover_ratio``.

        Parameters
        ----------
        plot_id
            Plot id of 14 characters (e.g. ``"33063000KE0078"``), as given by
            :meth:`bstock.connectors.cadastre.CadastreClient.plots`.

        Returns
        -------
        list of dict
            Buildings as :meth:`building` gives them, plus ``bdg_cover_ratio``: share of the
            building footprint on the plot (0 to 1). Empty for a plot unknown to the RNB.
        """
        first = self.http.get_json(f"buildings/plot/{plot_id}/", not_found_ok=True)
        if first is None:
            return []
        records = list(first.get("results") or [])
        if first.get("next"):
            records.extend(self.http.paginate(first["next"]))
        return records


# --- correspondences --------------------------------------------------------------------------


def _format_address(address: dict[str, Any]) -> str | None:
    """Return a one-line address (``"4 Rue de la Gabarre, 64100 Bayonne"``), or ``None``."""
    street = " ".join(
        str(part)
        for part in (address.get("street_number"), address.get("street_rep"), address.get("street"))
        if part
    )
    city = " ".join(str(p) for p in (address.get("city_zipcode"), address.get("city_name")) if p)
    text = ", ".join(part for part in (street, city) if part)
    return text or None


def _building_candidates(record: dict[str, Any]) -> dict[str, SourcedValue]:
    """Translate an RNB building into ``"rnb"`` candidates, by field name.

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
        text = _format_address(first)
        if text:
            found["address"] = candidate(text, comment=ban)
        if first.get("city_name"):
            found["municipality"] = candidate(first["city_name"])
        if first.get("city_insee_code"):
            found["insee_code"] = candidate(first["city_insee_code"])
    return found


def _bdnb_construction_id(record: dict[str, Any]) -> str | None:
    """Return the BDNB construction id among the external ids of an RNB building, if any."""
    for ext in record.get("ext_ids") or []:
        if ext.get("source") == "bdnb" and ext.get("id"):
            return str(ext["id"])
    return None


def _successors(history: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
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
    parent: Site | str | None = None,
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
        ``created`` and ``enriched`` building ids; ``not_found`` and messages (demolished and
        inactive ids, with their successors, which are left aside) under the building id when the
        building is in the park, else under the RNB id.

    Raises
    ------
    UnknownIdError
        If ``parent`` is not a site of the park.
    ConnectorError
        If the API fails persistently.
    """
    if parent is not None:
        park._resolve_site(parent)  # fail before any request
    report = ImportReport(source=SOURCE)
    _add_buildings(park, rnb_ids, parent, client or RnbClient(), report)
    return report


def add_buildings_at_address(
    park: BuildingPark,
    address: str,
    parent: Site | str | None = None,
    *,
    client: RnbClient | None = None,
) -> ImportReport:
    """Add the RNB buildings found at ``address``, as :func:`add_buildings` does.

    The RNB geocodes the address with the BAN and keeps the best match only; an address without
    number or poorly recognised gives no building. The buildings found are then fetched one by one
    (:func:`add_buildings`), so that demolished and inactive ones are left aside.

    Parameters
    ----------
    park
        Park to complete.
    address
        Postal address on one line, e.g. ``"67 rue de Mirambeau 64600 Anglet"``.
    parent
        Site receiving the created buildings.
    client
        RNB client; a default one is created when omitted.

    Returns
    -------
    ImportReport
        As :func:`add_buildings`; when the address gives no building, ``not_found`` under the
        address, with the status of the search.

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
    response = client.search_address(address)
    rnb_ids = [record["rnb_id"] for record in response.get("results") or []]
    if not rnb_ids:
        report = ImportReport(source=SOURCE)
        status = response.get("status")
        report.add("not_found", address, f"no RNB building at this address (status {status!r})")
        return report
    return add_buildings(park, rnb_ids, parent, client=client)


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
    method: Literal["point", "address"] = "point",
    radius: float = 30.0,
    client: RnbClient | None = None,
) -> pd.DataFrame:
    """Propose RNB buildings for park buildings that have no ``rnb_id`` (SPEC §8.3).

    Nothing is changed in the park. Unlike the other connector functions, which return an
    :class:`ImportReport`, this returns a table of proposals to review: filter it, then pass it to
    :func:`assign_matches`, or set ``building.rnb_id`` by hand.

    Parameters
    ----------
    park
        Park whose buildings are matched.
    buildings
        Buildings (objects or ids) to match; every building without ``rnb_id`` when ``None``.
    method
        ``"point"``: RNB buildings within ``radius`` of the effective ``lat`` / ``lon`` (inherited
        from the site when the building has none). ``"address"``: the RNB buildings at the
        effective ``address``, geocoded by the RNB with the BAN.
    radius
        Search radius around the point, in m (at most 1000).
    client
        RNB client; a default one is created when omitted.

    Returns
    -------
    pandas.DataFrame
        One row per (building, candidate): ``building_id``, ``rnb_id``, ``distance`` (m, 0 inside
        the footprint; point search), ``score`` (BAN geocoding score; address search),
        ``address`` (RNB) and ``used_by`` (park building already holding this ``rnb_id``).
        Buildings without point (or address) or without candidate have no row.

    Raises
    ------
    InvalidArgumentError
        If ``method`` or ``radius`` is invalid.
    UnknownIdError
        If a building id is not in the park.
    ConnectorError
        If the API fails persistently.
    """
    if method not in ("point", "address"):
        raise InvalidArgumentError(f"method must be 'point' or 'address', not {method!r}")
    if not 0 < radius <= MAX_RADIUS:
        raise InvalidArgumentError(f"radius must be in ]0, {MAX_RADIUS:g}] m, not {radius!r}")
    client = client or RnbClient()
    targets = (
        [b for b in park.iter_buildings() if not b.rnb_id]
        if buildings is None
        else list(select_buildings(park, buildings))
    )
    rows: list[dict[str, Any]] = []
    for building in targets:
        found = list(_candidates(client, building, method, radius))
        for candidate in found:
            used = _find(park, candidate["rnb_id"])
            candidate.update(building_id=building.id, used_by=used and used.id)
        rows.extend(found)
    return pd.DataFrame(rows, columns=SUGGESTION_COLUMNS)


def assign_matches(
    park: BuildingPark,
    matches: pd.DataFrame,
    *,
    strategy: Literal["unique"] = "unique",
) -> ImportReport:
    """Set the ``rnb_id`` of park buildings from a table of proposals (SPEC §8.3).

    ``matches`` is typically the result of :func:`suggest_matches`, filtered by the user. No
    request is made: run :func:`enrich` afterwards to fetch the RNB values of the assigned
    buildings.

    Parameters
    ----------
    park
        Park whose buildings receive an ``rnb_id``.
    matches
        Table with at least the columns ``building_id`` and ``rnb_id``, one row per candidate.
    strategy
        ``"unique"``: a building receives its candidate when it is the only one left in
        ``matches``. A string rather than a boolean, so that other strategies can be added.

    Returns
    -------
    ImportReport
        ``enriched`` for the buildings that received an ``rnb_id``; ``ambiguous`` for those with
        several candidates. Messages for a building that already has another ``rnb_id``, or a
        candidate already held by another park building: both are left unchanged.

    Raises
    ------
    InvalidArgumentError
        If ``strategy`` is invalid or ``matches`` lacks a column.
    UnknownIdError
        If a ``building_id`` is not a building of the park.
    """
    if strategy != "unique":
        raise InvalidArgumentError(f"strategy must be 'unique', not {strategy!r}")
    missing = [c for c in ("building_id", "rnb_id") if c not in matches.columns]
    if missing:
        raise InvalidArgumentError(f"matches lacks the columns {missing}")
    report = ImportReport(source=SOURCE)
    candidates: dict[str, list[str]] = {}
    for building_id, rnb_id in zip(matches["building_id"], matches["rnb_id"], strict=True):
        ids = candidates.setdefault(building_id, [])
        if normalize_rnb_id(rnb_id) not in ids:
            ids.append(normalize_rnb_id(rnb_id))
    buildings = {i: park._resolve(i, (Building,)) for i in candidates}  # fail before any change
    for building_id, ids in candidates.items():
        building = buildings[building_id]
        if len(ids) > 1:
            report.add("ambiguous", building_id, f"several candidates: {', '.join(ids)}")
            continue
        [rnb_id] = ids
        if building.rnb_id == rnb_id:
            continue
        if building.rnb_id:
            report.note(building_id, f"already has RNB id {building.rnb_id}: {rnb_id} not set")
            continue
        used = _find(park, rnb_id)
        if used is not None:
            report.note(building_id, f"RNB id {rnb_id} is already held by {used.id}: not set")
            continue
        building.rnb_id = rnb_id
        report.add("enriched", building_id, f"rnb_id set to {rnb_id}")
    return report


def suggest_site_matches(
    park: BuildingPark,
    sites: Iterable[Site | str] | None = None,
    *,
    method: Literal["plot", "point"] = "plot",
    radius: float = 0.0,
    min_cover: float = 0.5,
    fallback_radius: float | None = None,
    client: RnbClient | None = None,
    cadastre: CadastreClient | None = None,
) -> pd.DataFrame:
    """Propose the RNB buildings of park sites, from their point (SPEC §8.3).

    Nothing is changed in the park: review the table, then pass the rows kept to
    :func:`assign_site_matches`. Unlike :func:`suggest_matches`, which needs park buildings, this
    starts from the sites, which may have no building yet.

    Parameters
    ----------
    park
        Park whose sites are matched.
    sites
        Sites (objects or ids) to match; every site of the park when ``None``.
    method
        ``"plot"``: the RNB buildings on the cadastral plots at the effective ``lat`` / ``lon``
        of the site (plot containing the point, or plots within ``radius``). ``"point"``: the
        RNB buildings within ``radius`` of the point.
    radius
        Search radius around the point, in m (at most 1000): ``0`` for the plot containing the
        point (``"plot"``); more than 0 with ``"point"``.
    min_cover
        Share of a building footprint on the plot (0 to 1) below which the candidate gets the
        ``low_cover`` status (``"plot"``).
    fallback_radius
        With ``"plot"``: when no candidate reaches ``min_cover`` (e.g. a point on the road), add
        the RNB buildings within this radius of the point, in m. No fallback when ``None``.
    client
        RNB client; a default one is created when omitted.
    cadastre
        Cadastre client (``"plot"``); a default one is created when omitted.

    Returns
    -------
    pandas.DataFrame
        One row per (site, candidate): ``site_id``, ``rnb_id``, ``status``, ``method``
        (``"plot"`` or ``"point"``), ``plot_id`` and ``cover_ratio`` (largest share on one plot;
        plot search), ``distance`` (m, from the site point to the RNB point; 0 inside the
        footprint for a point search), ``address`` (RNB) and ``used_by`` (park building already
        holding this ``rnb_id``). ``status``: ``ok``; ``low_cover``; ``shared`` (a candidate
        otherwise ``ok`` proposed for several sites). A site without candidate has one row with
        ``rnb_id`` ``None`` and the status ``none``, or ``no_point`` without ``lat`` / ``lon``.

    Raises
    ------
    InvalidArgumentError
        If ``method``, ``radius``, ``min_cover`` or ``fallback_radius`` is invalid.
    UnknownIdError
        If a site id is not in the park.
    ConnectorError
        If an API fails persistently.
    """
    if method not in ("plot", "point"):
        raise InvalidArgumentError(f"method must be 'plot' or 'point', not {method!r}")
    above_minimum = radius >= 0 if method == "plot" else radius > 0
    if not above_minimum or radius > MAX_RADIUS:
        raise InvalidArgumentError(f"radius {radius!r} m is out of range for method {method!r}")
    if not 0 <= min_cover <= 1:
        raise InvalidArgumentError(f"min_cover must be in [0, 1], not {min_cover!r}")
    if fallback_radius is not None and not 0 < fallback_radius <= MAX_RADIUS:
        raise InvalidArgumentError(
            f"fallback_radius must be in ]0, {MAX_RADIUS:g}] m, not {fallback_radius!r}"
        )
    targets = list(park.iter_sites()) if sites is None else [park._resolve_site(s) for s in sites]
    client = client or RnbClient()
    if method == "plot":
        cadastre = cadastre or CadastreClient()
    rows: list[dict[str, Any]] = []
    for site in targets:
        if site.lat is None or site.lon is None:
            logger.info("%s: no lat/lon, no RNB suggestion", site.id)
            rows.append({"site_id": site.id, "rnb_id": None, "status": "no_point"})
            continue
        found: dict[str, dict[str, Any]] = {}
        if cadastre is not None and method == "plot":
            found = _plot_candidates(client, cadastre, site.lat, site.lon, radius, min_cover)
        point_radius = radius if method == "point" else fallback_radius
        if point_radius is not None and all(r["status"] != "ok" for r in found.values()):
            for record in client.closest(site.lat, site.lon, point_radius):
                rnb_id = normalize_rnb_id(record["rnb_id"])
                found[rnb_id] = {
                    **_suggestion(record, distance=record.get("distance")),
                    "rnb_id": rnb_id,
                    "method": "point",
                    "status": "ok",
                }
        if not found:
            rows.append({"site_id": site.id, "rnb_id": None, "status": "none"})
        for row in found.values():
            used = _find(park, row["rnb_id"])
            rows.append({**row, "site_id": site.id, "used_by": used and used.id})
    table = pd.DataFrame(rows, columns=SITE_MATCH_COLUMNS)
    ok = table["status"] == "ok"
    sites_per_id = table[ok].groupby("rnb_id")["site_id"].nunique()
    table.loc[ok & table["rnb_id"].isin(sites_per_id.index[sites_per_id > 1]), "status"] = "shared"
    return table


def assign_site_matches(
    park: BuildingPark,
    matches: pd.DataFrame,
    *,
    client: RnbClient | None = None,
) -> ImportReport:
    """Add the RNB buildings of a table of proposals under their sites (SPEC §8.3).

    ``matches`` is typically the result of :func:`suggest_site_matches`, reviewed by the user:
    every row with an ``rnb_id`` is applied, whatever its ``status``. Each site receives its
    buildings as with :func:`add_buildings` (one request per id).

    Parameters
    ----------
    park
        Park whose sites receive the buildings.
    matches
        Table with at least the columns ``site_id`` and ``rnb_id``, one row per building; rows
        without ``rnb_id`` are ignored.
    client
        RNB client; a default one is created when omitted.

    Returns
    -------
    ImportReport
        As :func:`add_buildings`; ``ambiguous`` under the RNB id when it is given for several
        sites (nothing done for it). A message when the building is already in the park under
        another site: it is left there (use ``park.move``).

    Raises
    ------
    InvalidArgumentError
        If ``matches`` lacks a column.
    UnknownIdError
        If a ``site_id`` is not a site of the park.
    ConnectorError
        If the API fails persistently.
    """
    missing = [c for c in ("site_id", "rnb_id") if c not in matches.columns]
    if missing:
        raise InvalidArgumentError(f"matches lacks the columns {missing}")
    kept = matches[matches["rnb_id"].notna()]
    sites_of: dict[str, list[str]] = {}
    for site_id, rnb_id in zip(kept["site_id"], kept["rnb_id"], strict=True):
        ids = sites_of.setdefault(normalize_rnb_id(rnb_id), [])
        if site_id not in ids:
            ids.append(site_id)
    sites = {s: park._resolve_site(s) for ids in sites_of.values() for s in ids}  # fail first
    client = client or RnbClient()
    report = ImportReport(source=SOURCE)
    plan: dict[str, list[str]] = {}
    for rnb_id, site_ids in sites_of.items():
        if len(site_ids) > 1:
            report.add("ambiguous", rnb_id, f"matched to several sites: {', '.join(site_ids)}")
            continue
        [site_id] = site_ids
        existing = _find(park, rnb_id)
        if existing is not None and not _is_under(existing, sites[site_id]):
            where = existing.parent.id if existing.parent is not None else "the top level"
            report.note(existing.id, f"RNB id {rnb_id} is under {where}, not {site_id}: not moved")
            continue
        plan.setdefault(site_id, []).append(rnb_id)
    for site_id, rnb_ids in plan.items():
        _add_buildings(park, rnb_ids, sites[site_id], client, report)
    return report


# --- helpers ----------------------------------------------------------------------------------


def _add_buildings(
    park: BuildingPark,
    rnb_ids: Iterable[str],
    parent: Site | str | None,
    client: RnbClient,
    report: ImportReport,
) -> None:
    for rnb_id in dict.fromkeys(normalize_rnb_id(i) for i in rnb_ids):
        existing = _find(park, rnb_id)
        record = _fetch_usable(client, rnb_id, report, None if existing is None else existing.id)
        if record is None:
            continue
        building = existing or _find(park, record["rnb_id"])
        if building is None:
            building = park.new_building(rnb_id=record["rnb_id"], parent=parent)
            _apply(building, record)
            report.add("created", building.id)
        else:
            _apply(building, record)
            report.add("enriched", building.id)


def _is_under(building: Building, site: Site) -> bool:
    """Return whether ``site`` is the parent of ``building`` or one of its ancestors."""
    parent = building.parent
    while parent is not None:
        if parent is site:
            return True
        parent = parent.parent
    return False


def _distance(lat: float, lon: float, point: dict[str, Any] | None) -> float | None:
    """Return the great-circle distance from (lat, lon) to a GeoJSON ``Point``, in m."""
    point = point or {}
    coordinates = point.get("coordinates") or []
    if point.get("type") != "Point" or len(coordinates) < 2:
        return None
    lon2, lat2 = coordinates[:2]
    phi1, phi2 = math.radians(lat), math.radians(lat2)
    a = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon) / 2) ** 2
    )
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(a))


def _plot_candidates(
    client: RnbClient,
    cadastre: CadastreClient,
    lat: float,
    lon: float,
    radius: float,
    min_cover: float,
) -> dict[str, dict[str, Any]]:
    """Return the usable RNB buildings on the plots at the point, by RNB id.

    A building on several plots keeps the plot where its ``bdg_cover_ratio`` is the largest.
    Demolished and inactive buildings are left aside.
    """
    found: dict[str, dict[str, Any]] = {}
    for feature in cadastre.plots(lat, lon, radius):
        plot = plot_id(feature)
        if not plot:
            continue
        for record in client.plot_buildings(plot):
            if record.get("status") == "demolished" or record.get("is_active") is False:
                continue
            rnb_id = normalize_rnb_id(record["rnb_id"])
            cover = float(record.get("bdg_cover_ratio") or 0.0)
            if rnb_id in found and found[rnb_id]["cover_ratio"] >= cover:
                continue
            found[rnb_id] = {
                **_suggestion(record, distance=_distance(lat, lon, record.get("point"))),
                "rnb_id": rnb_id,
                "method": "plot",
                "plot_id": plot,
                "cover_ratio": cover,
                "status": "ok" if cover >= min_cover else "low_cover",
            }
    return found


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
        event, ids = _successors(client.history(rnb_id))
        detail = f" after a {event}" if event else ""
        following = f"; successors: {', '.join(ids)}" if ids else "; no successor"
        report.note(key, f"RNB id {rnb_id} is inactive{detail}{following}: left unchanged")
        return None
    return record


def _apply(building: Building, record: dict[str, Any]) -> None:
    put(building, _building_candidates(record), source=SOURCE, clear=True)
    construction = _bdnb_construction_id(record)
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
    client: RnbClient, building: Building, method: str, radius: float
) -> Iterator[dict[str, Any]]:
    if method == "point":
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
        "address": _format_address(addresses[0]) if addresses else None,
    }
