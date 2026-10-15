"""Connector to the BDNB, the national building database (SPEC §8.4, annex A.6).

Three layers: :class:`BdnbClient` fetches raw rows, internal correspondence functions translate them
into candidate values, and :func:`enrich` works on a park. Requires the ``[connectors]`` extra;
geometries also need the ``[geo]`` extra.
"""

from __future__ import annotations

import datetime
import logging
import re
import warnings
import zoneinfo
from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from bstock._geometry import validate_footprint
from bstock.building import Building
from bstock.connectors import _mappings
from bstock.connectors._geo import to_wgs84
from bstock.connectors._http import HttpClient
from bstock.connectors._report import ImportReport
from bstock.connectors._sync import Candidates, put, select_buildings, sync_systems
from bstock.connectors.rnb import normalize_rnb_id
from bstock.description import DescribedEntity
from bstock.enums import SystemKind
from bstock.exceptions import (
    BstockWarning,
    ConnectorError,
    EntityInUseError,
    MissingDependencyError,
)
from bstock.provenance import SourcedValue
from bstock.site import Site

if TYPE_CHECKING:
    from bstock.park import BuildingPark

__all__ = [
    "SOURCE",
    "BdnbClient",
    "enrich",
]

logger = logging.getLogger(__name__)

SOURCE = "bdnb"
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
BASE_URL = "https://api.bdnb.io/v1/bdnb/"
OPEN_PAGE_SIZE = 10
"""Rows per call without API key (open offer)."""
KEY_PAGE_SIZE = 1000
"""Rows per call assumed with an API key (Open+ offer); pass ``page_size`` for the Expert offer."""

CONSTRUCTION_COLUMNS = (
    "batiment_construction_id",
    "batiment_groupe_id",
    "rnb_id",
    "hauteur",
    "s_geom_cstr",
    "geom_cstr",
)
GROUP_COLUMNS = (
    "batiment_groupe_id",
    "usage_principal_bdnb_open",
    "annee_construction",
    "nb_log",
    "nb_niveau",
    "hauteur_mean",
    "s_geom_groupe",
    "geom_groupe",
    "type_generateur_chauffage",
    "type_energie_chauffage",
    "type_generateur_chauffage_appoint",
    "type_energie_chauffage_appoint",
    "type_generateur_ecs",
    "type_generateur_ecs_appoint",
    "type_generateur_climatisation",
    "identifiant_dpe",
)
DPE_COLUMNS = (
    "batiment_groupe_id",
    "identifiant_dpe",
    "date_etablissement_dpe",
    "type_energie_ecs",
    "type_energie_ecs_appoint",
    "type_energie_climatisation",
)

# (kind, generator column, energy column, table of the energy column, name)
_GENERATORS = (
    (SystemKind.HEATING, "type_generateur_chauffage", "type_energie_chauffage", "group", "main"),
    (
        SystemKind.HEATING,
        "type_generateur_chauffage_appoint",
        "type_energie_chauffage_appoint",
        "group",
        "backup",
    ),
    (SystemKind.DHW, "type_generateur_ecs", "type_energie_ecs", "dpe", "main"),
    (SystemKind.DHW, "type_generateur_ecs_appoint", "type_energie_ecs_appoint", "dpe", "backup"),
    (
        SystemKind.COOLING,
        "type_generateur_climatisation",
        "type_energie_climatisation",
        "dpe",
        "main",
    ),
)
GENERATOR_KINDS = (SystemKind.HEATING, SystemKind.DHW, SystemKind.COOLING)


class BdnbClient:
    """Raw access to the BDNB API (PostgREST): rows of the tables as JSON dicts.

    Requests are grouped by batches of ids (``col=in.(…)``), as large as the page size of the offer,
    and paginated with ``offset``. The methods raise :class:`~bstock.exceptions.ConnectorError`
    when the API fails persistently.

    Parameters
    ----------
    api_key
        BDNB API key (``X-Gravitee-Api-Key``); without key, the open offer serves 10 rows per call.
    base_url
        API root, ending with ``/``.
    page_size
        Rows per call: 10 without key, 1000 with a key by default.
    session
        ``requests.Session`` to use; a new session by default.
    **http_options
        Other :class:`~bstock.connectors._http.HttpClient` options.

    Raises
    ------
    MissingDependencyError
        If ``requests`` is not installed.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        page_size: int | None = None,
        session: Any | None = None,
        **http_options: Any,
    ) -> None:
        headers = {"X-Gravitee-Api-Key": api_key} if api_key else {}
        self.http = HttpClient(base_url, session=session, headers=headers, **http_options)
        self.page_size = page_size or (KEY_PAGE_SIZE if api_key else OPEN_PAGE_SIZE)

    def constructions_by_rnb(self, rnb_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return the ``batiment_construction`` rows of the given RNB ids."""
        return self.query("batiment_construction", "rnb_id", rnb_ids, CONSTRUCTION_COLUMNS)

    def groups(self, group_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return the ``batiment_groupe_complet`` rows of the given groups."""
        return self.query("batiment_groupe_complet", "batiment_groupe_id", group_ids, GROUP_COLUMNS)

    def representative_dpe(self, group_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return the ``batiment_groupe_dpe_representatif_logement`` rows of the given groups."""
        return self.query(
            "batiment_groupe_dpe_representatif_logement",
            "batiment_groupe_id",
            group_ids,
            DPE_COLUMNS,
        )

    def count_constructions(self, group_id: str) -> int:
        """Return the number of BDNB constructions of the group ``group_id`` (one call).

        Raises
        ------
        ConnectorError
            If the response has no readable ``Content-Range`` header.
        """
        response = self.http.get(
            "donnees/batiment_construction",
            {
                "batiment_groupe_id": f"eq.{group_id}",
                "select": "batiment_construction_id",
                "limit": 1,
            },
            headers={"Prefer": "count=exact"},
        )
        content_range = response.headers.get("Content-Range", "")
        match = re.search(r"/(\d+)$", content_range)
        if match is None:
            raise ConnectorError(f"no construction count in Content-Range {content_range!r}")
        return int(match.group(1))

    def query(
        self, table: str, column: str, values: Iterable[str], columns: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return the rows of ``table`` whose ``column`` is in ``values``.

        Raises
        ------
        ConnectorError
            If the API fails or returns something else than a list of rows.
        """
        rows: list[dict[str, Any]] = []
        for batch in _batches(list(dict.fromkeys(values)), self.page_size):
            params: dict[str, Any] = {column: f"in.({','.join(batch)})", "limit": self.page_size}
            if columns:
                params["select"] = ",".join(columns)
            offset = 0
            while True:
                page = self.http.get_json(f"donnees/{table}", {**params, "offset": offset})
                if not isinstance(page, list):
                    raise ConnectorError(f"BDNB {table}: unexpected response {page!r:.200}")
                rows.extend(page)
                if len(page) < self.page_size:
                    break
                offset += len(page)
        return rows


# --- correspondences --------------------------------------------------------------------------

_FIELDS = DescribedEntity.sourced_fields()


def _construction_candidates(row: dict[str, Any]) -> dict[str, SourcedValue]:
    """Translate a ``batiment_construction`` row into ``"bdnb"`` candidates for a building.

    ``hauteur`` gives ``height``, ``s_geom_cstr`` ``footprint_area`` and ``geom_cstr`` (reprojected
    to WGS84) ``footprint``; ``ref`` is the construction id.
    """
    ref = row.get("batiment_construction_id")
    found: dict[str, SourcedValue] = {}
    _add(found, "height", row.get("hauteur"), ref)
    _add(found, "footprint_area", row.get("s_geom_cstr"), ref)
    _add(found, "footprint", _footprint(row.get("geom_cstr"), ref), ref)
    return found


def _group_candidates(row: dict[str, Any]) -> dict[str, SourcedValue]:
    """Translate a ``batiment_groupe_complet`` row into ``"bdnb"`` candidates.

    ``usage_principal_bdnb_open`` gives ``usage`` (SPEC A.2), ``annee_construction``
    ``construction_year``, ``nb_log`` ``dwelling_count``, ``nb_niveau`` ``level_count``,
    ``hauteur_mean`` ``height``, ``s_geom_groupe`` ``footprint_area`` and ``geom_groupe``
    ``footprint``; ``ref`` is the group id.
    """
    ref = row.get("batiment_groupe_id")
    found: dict[str, SourcedValue] = {}
    label = row.get("usage_principal_bdnb_open")
    _add(found, "usage", _mappings.bdnb_usage(label), ref, raw=label)
    _add(found, "construction_year", row.get("annee_construction"), ref)
    _add(found, "dwelling_count", row.get("nb_log"), ref)
    _add(found, "level_count", row.get("nb_niveau"), ref)
    _add(found, "height", row.get("hauteur_mean"), ref)
    _add(found, "footprint_area", row.get("s_geom_groupe"), ref)
    _add(found, "footprint", _footprint(row.get("geom_groupe"), ref), ref)
    return found


def _generator_candidates(
    group: dict[str, Any], dpe: dict[str, Any] | None = None
) -> dict[SystemKind, list[Candidates]]:
    """Translate the generators of a group into candidates, by system kind (SPEC A.5).

    Main generator first, then the backup one. The energy column, when it is filled (heating in
    the group row, DHW and cooling in the representative DPE row), takes precedence over the energy
    read in the generator label. Every candidate has the representative DPE date.
    """
    ref = group.get("batiment_groupe_id")
    date = _dpe_date((dpe or {}).get("date_etablissement_dpe"))
    produced: dict[SystemKind, list[Candidates]] = {kind: [] for kind in GENERATOR_KINDS}

    def candidate(value: Any, raw: Any = None) -> SourcedValue:
        return SourcedValue(value=value, source=SOURCE, date=date, ref=ref, raw=raw)

    for kind, generator_column, energy_column, table, name in _GENERATORS:
        label = group.get(generator_column)
        if not label:
            continue
        mapping = _mappings.bdnb_generator(label)
        energy_label = (group if table == "group" else dpe or {}).get(energy_column)
        found = {"name": candidate(name)}
        if mapping.technology is not None:
            found["technology"] = candidate(mapping.technology, label)
        energy = _mappings.bdnb_energy_type(energy_label) if energy_label else None
        if energy is not None:
            found["energy_type"] = candidate(energy, energy_label)
        elif mapping.energy_type is not None:
            found["energy_type"] = candidate(mapping.energy_type, label)
        produced[kind].append(found)
    return produced


def _dpe_date(value: str | None) -> datetime.date | None:
    """Return the date, in Paris, of a BDNB DPE timestamp.

    The BDNB gives UTC timestamps without offset: ``2026-03-09T23:00:00`` is midnight in Paris on
    10 March 2026.
    """
    if not value:
        return None
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        logger.info("unreadable BDNB date %r", value)
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.UTC)
    return moment.astimezone(PARIS).date()


def _add(
    found: dict[str, SourcedValue], name: str, value: Any, ref: str | None, raw: Any = None
) -> None:
    """Add a candidate when ``value`` is set and valid for the field."""
    if value is None:
        return
    try:
        _FIELDS[name].validate(value)
    except ValueError as error:
        logger.info("BDNB %s: invalid %s %r ignored (%s)", ref, name, value, error)
        return
    found[name] = SourcedValue(value=value, source=SOURCE, ref=ref, raw=raw)


def _footprint(geometry: dict[str, Any] | None, ref: str | None) -> dict[str, Any] | None:
    """Return a BDNB geometry as a WGS84 footprint, or ``None``."""
    if not geometry or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    try:
        return validate_footprint(to_wgs84(geometry, crs="EPSG:2154"))
    except MissingDependencyError:
        warnings.warn(
            "pyproj is not installed: BDNB geometries are ignored; "
            "install them with pip install 'bstock[geo]'",
            BstockWarning,
            stacklevel=2,
        )
    except (ValueError, ConnectorError) as error:
        logger.info("BDNB %s: geometry ignored (%s)", ref, error)
    return None


# --- park operation ---------------------------------------------------------------------------


def enrich(
    park: BuildingPark,
    buildings: Iterable[Building | str] | None = None,
    *,
    group_sites: bool = True,
    client: BdnbClient | None = None,
) -> ImportReport:
    """Enrich the park buildings from the BDNB, starting from their ``rnb_id`` (SPEC §8.4).

    Each building receives the values of its BDNB construction. The values of its BDNB group go:

    - on the building, when the group has a single construction in the BDNB;
    - on a sub-site ``id = batiment_groupe_id``, created under the common parent of the park
      buildings of the group (or reused) and into which they are moved, when the group has
      several constructions; buildings inherit the intensive values;
    - on each building, intensive values only, when the buildings of the group have different
      parents or ``group_sites`` is False.

    Unlike the other connectors, it may thus change the structure of the park: every building moved
    into a group site is reported. Pass ``group_sites=False`` to keep the structure as is.

    Parameters
    ----------
    park
        Park to enrich.
    buildings
        Buildings (objects or ids); every building with an ``rnb_id`` when ``None``.
    group_sites
        Create the group sub-sites (default). False applies the per-building rule to every group
        of several constructions.
    client
        BDNB client; a default one (open offer, no key) is created when omitted.

    Returns
    -------
    ImportReport
        ``enriched`` buildings and group sites, ``created`` group sites, ``not_found`` buildings
        without BDNB construction, ``ambiguous`` buildings with several, and messages (among
        them, every building moved into a group site, with its former parent).

    Raises
    ------
    UnknownIdError
        If a building id is not in the park.
    ConnectorError
        If the API fails persistently.
    """
    client = client or BdnbClient()
    report = ImportReport(source=SOURCE)
    targets: dict[str, Building] = {}
    for building in select_buildings(park, buildings):
        if building.rnb_id:
            targets[normalize_rnb_id(building.rnb_id)] = building
        else:
            report.note(building.id, "no rnb_id: use rnb.suggest_matches")
    if not targets:
        return report

    rows_by_rnb: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in client.constructions_by_rnb(targets):
        if row.get("rnb_id"):
            rows_by_rnb[normalize_rnb_id(row["rnb_id"])].append(row)
    members: dict[str, list[tuple[Building, dict[str, Any]]]] = defaultdict(list)
    for rnb_id, building in targets.items():
        rows = rows_by_rnb.get(rnb_id, [])
        if not rows:
            report.add("not_found", building.id, f"no BDNB construction for RNB id {rnb_id}")
        elif len(rows) > 1:
            ids = [r.get("batiment_construction_id") for r in rows]
            report.add("ambiguous", building.id, f"several BDNB constructions: {ids}")
        else:
            members[rows[0]["batiment_groupe_id"]].append((building, rows[0]))
    if not members:
        return report

    groups = {r["batiment_groupe_id"]: r for r in client.groups(members)}
    dpes = {r["batiment_groupe_id"]: r for r in client.representative_dpe(members)}
    for group_id, group_members in members.items():
        group = groups.get(group_id)
        if group is None:
            for building, row in group_members:
                _set_building(building, row)
                report.add("enriched", building.id, f"group {group_id} not found in the BDNB")
            continue
        _apply_group(park, group, dpes.get(group_id), group_members, group_sites, client, report)
    return report


def _apply_group(
    park: BuildingPark,
    group: dict[str, Any],
    dpe: dict[str, Any] | None,
    group_members: list[tuple[Building, dict[str, Any]]],
    group_sites: bool,
    client: BdnbClient,
    report: ImportReport,
) -> None:
    group_id = group["batiment_groupe_id"]
    values = _group_candidates(group)
    systems = _generator_candidates(group, dpe)
    dpe_id = group.get("identifiant_dpe")

    if client.count_constructions(group_id) <= 1:
        for building, row in group_members:
            _set_building(building, row, values)
            building.bdnb_batiment_groupe_id = group_id
            _set_systems_and_dpe(building, systems, dpe_id, report)
            report.add("enriched", building.id)
        return

    parents = {_parent_outside(b, group_id) for b, _ in group_members}
    existing = park.get(group_id)
    reason = None
    if not group_sites:
        reason = "group_sites=False"
    elif len(parents) > 1:
        reason = "its buildings have different parents"
    elif existing is not None and not isinstance(existing, Site):
        reason = f"id {group_id!r} is already used by another entity"
    if reason is None:
        (parent,) = parents
        site = existing
        if site is None:
            site = park.new_site(group_id, parent=parent, bdnb_batiment_groupe_id=group_id)
            report.add("created", group_id)
        put(site, values, source=SOURCE, clear=True)
        _set_systems_and_dpe(site, systems, dpe_id, report)
        report.add("enriched", group_id)
        for building, row in group_members:
            moved = building.parent is site or _move(park, building, site, report)
            spread = {} if moved else _intensive(values)
            _set_building(building, row, spread)
            _set_systems_and_dpe(building, {} if moved else systems, None, report)
            report.add("enriched", building.id)
        return

    intensive = _intensive(values)
    for building, row in group_members:
        _set_building(building, row, intensive)
        _set_systems_and_dpe(building, systems, dpe_id, report)
        report.add(
            "enriched",
            building.id,
            f"BDNB group {group_id} not made a site ({reason}): its intensive values are set on "
            "the building, its extensive values are not set",
        )


def _set_building(
    building: Building,
    row: dict[str, Any],
    group_values: Candidates | None = None,
) -> None:
    """Store the construction values, which take precedence over the group ones."""
    values = {**(group_values or {}), **_construction_candidates(row)}
    put(building, values, source=SOURCE, clear=True)
    building.bdnb_batiment_construction_id = row.get("batiment_construction_id")


def _set_systems_and_dpe(
    entity: DescribedEntity,
    systems: dict[SystemKind, list[Candidates]],
    dpe_id: str | None,
    report: ImportReport,
) -> None:
    for kind in GENERATOR_KINDS:
        sync_systems(entity, kind, SOURCE, systems.get(kind, []), report)
    if dpe_id and dpe_id not in entity.dpe_ids:
        entity.dpe_ids = [*entity.dpe_ids, dpe_id]


def _intensive(values: Candidates) -> dict[str, SourcedValue]:
    return {name: c for name, c in values.items() if _FIELDS[name].inherit}


def _parent_outside(building: Building, group_id: str) -> str | None:
    """Return the id of the parent of ``building``, looking past the group site itself."""
    parent = building.parent
    if parent is not None and parent.id == group_id:
        parent = parent.parent
    return None if parent is None else parent.id


def _move(park: BuildingPark, building: Building, site: Site, report: ImportReport) -> bool:
    former = building.parent.id if building.parent is not None else "the top level"
    try:
        park.move(building, site)
    except EntityInUseError as error:
        report.note(building.id, f"not moved into {site.id}: {error}")
        return False
    report.note(building.id, f"moved from {former} into the group site {site.id}")
    return True


def _batches(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
