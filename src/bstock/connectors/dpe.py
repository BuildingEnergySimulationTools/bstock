"""Connector to the DPE open data of the ADEME (SPEC §8.5, annex A.4 to A.6).

Three layers: :class:`DpeClient` fetches raw lines of the three datasets, internal correspondence
functions translate one DPE into candidate values, and :func:`enrich` works on a park. Requires the
``[connectors]`` extra.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from bstock.building import Building
from bstock.connectors import _mappings
from bstock.connectors._http import HttpClient
from bstock.connectors._report import ImportReport
from bstock.connectors._sync import Candidates, put, select_buildings, sync_components, sync_systems
from bstock.connectors.rnb import normalize_rnb_id
from bstock.description import DescribedEntity
from bstock.enums import BuildingUsage, ComponentKind, InsulationLevel, SystemKind
from bstock.envelope import Envelope, EnvelopeComponent
from bstock.exceptions import ConnectorError
from bstock.provenance import SourcedModel, SourcedValue
from bstock.systems import CoolingGenerator, DHWGenerator, HeatingGenerator

if TYPE_CHECKING:
    from bstock.park import BuildingPark

__all__ = [
    "DATASETS",
    "SOURCE",
    "DpeClient",
    "enrich",
]

logger = logging.getLogger(__name__)

SOURCE = "dpe"
BASE_URL = "https://data.ademe.fr/data-fair/api/v1/datasets/"
DATASETS = {"E": "dpe03existant", "N": "dpe02neuf", "T": "dpe01tertiaire"}
"""Datasets by the 5th character of the DPE number (existing, new, tertiary)."""
DATASET_KEY = "_dataset"
"""Key added by :class:`DpeClient` to each line: the dataset it comes from."""

_COMMON_COLUMNS = (
    "numero_dpe",
    "numero_dpe_remplace",
    "id_rnb",
    "date_etablissement_dpe",
    "annee_construction",
)
_SLOTS_HEATING = [(g, i) for i in (1, 2) for g in (1, 2)]
_SLOTS_DHW = (1, 2)
RESIDENTIAL_COLUMNS = (
    *_COMMON_COLUMNS,
    "type_batiment",
    "numero_dpe_immeuble_associe",
    "surface_habitable_logement",
    "surface_habitable_immeuble",
    "nombre_appartement",
    "nombre_niveau_immeuble",
    "ubat_w_par_m2_k",
    "classe_inertie_batiment",
    "deperditions_murs",
    "deperditions_planchers_hauts",
    "deperditions_planchers_bas",
    "deperditions_baies_vitrees",
    "deperditions_portes",
    "deperditions_ponts_thermiques",
    "qualite_isolation_murs",
    "qualite_isolation_menuiseries",
    "qualite_isolation_plancher_bas",
    "qualite_isolation_plancher_haut_comble_perdu",
    "qualite_isolation_plancher_haut_comble_amenage",
    "qualite_isolation_plancher_haut_toit_terrasse",
    *(f"type_generateur_n{g}_installation_n{i}" for g, i in _SLOTS_HEATING),
    *(f"type_energie_generateur_n{g}_installation_n{i}" for g, i in _SLOTS_HEATING),
    *(f"type_generateur_n{g}_ecs_n1" for g in _SLOTS_DHW),
    *(f"type_energie_generateur_n{g}_ecs_n1" for g in _SLOTS_DHW),
    *(f"volume_stockage_generateur_n{g}_ecs_n1" for g in _SLOTS_DHW),
    *(f"cop_generateur_n{g}_ecs_n1" for g in _SLOTS_DHW),
    "type_generateur_froid",
    "type_energie_climatisation",
)
TERTIARY_COLUMNS = (
    *_COMMON_COLUMNS,
    "methode_dpe",
    "surface_utile",
    "type_energie_principale_chauffage",
)
_MISSING_IN_NEW = {
    "qualite_isolation_plancher_haut_comble_perdu",
    "qualite_isolation_plancher_haut_toit_terrasse",
    "type_energie_climatisation",
}
COLUMNS = {
    "dpe03existant": RESIDENTIAL_COLUMNS,
    "dpe02neuf": tuple(c for c in RESIDENTIAL_COLUMNS if c not in _MISSING_IN_NEW),
    "dpe01tertiaire": TERTIARY_COLUMNS,
}
IN_BATCH = 100
"""Values per ``_in`` filter."""
PAGE_SIZE = 1000
"""Lines per page."""

_IN_RESIDENTIAL_BUILDING = "dans un bâtiment de logement"
_BUILDING_TYPES = {"maison", "immeuble"}


class DpeClient:
    """Raw access to the DPE datasets of the ADEME data-fair API: lines as JSON dicts.

    Each line receives a ``_dataset`` key naming its dataset. Only the columns used by bstock are
    requested, without total count, to spare the processing budget of the API. The methods raise
    :class:`~bstock.exceptions.ConnectorError` when the API fails persistently.

    Parameters
    ----------
    base_url
        API root of the datasets, ending with ``/``.
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
        self, *, base_url: str = BASE_URL, session: Any | None = None, **http_options: Any
    ) -> None:
        self.http = HttpClient(base_url, session=session, **http_options)

    def dpe_by_numbers(self, numbers: Iterable[str]) -> list[dict[str, Any]]:
        """Return the DPE with the given numbers, each looked up in the dataset of its number."""
        by_dataset: dict[str, list[str]] = {}
        for number in dict.fromkeys(numbers):
            dataset = DATASETS.get(number[4:5].upper())
            if dataset is None:
                logger.info("DPE number %r: unknown dataset, skipped", number)
                continue
            by_dataset.setdefault(dataset, []).append(number)
        return [
            line
            for dataset, values in by_dataset.items()
            for line in self.query(dataset, "numero_dpe", values)
        ]

    def dpe_by_rnb(self, rnb_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return the DPE of the given RNB ids, from the three datasets."""
        values = list(dict.fromkeys(rnb_ids))
        return [line for d in DATASETS.values() for line in self.query(d, "id_rnb", values)]

    def query(self, dataset: str, column: str, values: Iterable[str]) -> list[dict[str, Any]]:
        """Return the lines of ``dataset`` whose ``column`` is in ``values``.

        Raises
        ------
        ConnectorError
            If the API fails persistently.
        """
        found: list[dict[str, Any]] = []
        values = list(values)
        for start in range(0, len(values), IN_BATCH):
            params = {
                f"{column}_in": ",".join(values[start : start + IN_BATCH]),
                "select": ",".join(COLUMNS[dataset]),
                "size": PAGE_SIZE,
                "count": "false",
            }
            for line in self.http.paginate(f"{dataset}/lines", params):
                found.append({**_check_line(line), DATASET_KEY: dataset})
        return found


# --- selection --------------------------------------------------------------------------------


def _is_building_scale(line: Mapping[str, Any]) -> bool:
    """Tell whether a DPE describes a whole building (SPEC §8.5).

    A house or a residential building DPE, or a tertiary DPE that is not for premises in a
    residential building. Apartment DPE are not.
    """
    if line.get(DATASET_KEY) == DATASETS["T"]:
        return _IN_RESIDENTIAL_BUILDING not in (line.get("methode_dpe") or "")
    return line.get("type_batiment") in _BUILDING_TYPES


def _select_dpe(lines: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the DPE whose values are used: the most recent building-scale one not replaced.

    Ties on the date are broken by the DPE number.
    """
    lines = list(lines)
    replaced = {line.get("numero_dpe_remplace") for line in lines} - {None}
    usable = [
        line for line in lines if _is_building_scale(line) and line["numero_dpe"] not in replaced
    ]
    if not usable:
        return None
    return max(
        usable, key=lambda line: (line.get("date_etablissement_dpe") or "", line["numero_dpe"])
    )


# --- correspondences --------------------------------------------------------------------------


class _Builder:
    """Build valid ``"dpe"`` candidates for the fields of one model class."""

    def __init__(self, line: Mapping[str, Any], cls: type[SourcedModel]) -> None:
        self.fields = cls.sourced_fields()
        self.ref = line.get("numero_dpe")
        self.date = _date(line.get("date_etablissement_dpe"))
        self.found: dict[str, SourcedValue] = {}

    def add(self, name: str, value: Any, *, raw: Any = None, comment: str | None = None) -> None:
        if value is None:
            return
        try:
            self.fields[name].validate(value)
        except ValueError as error:
            logger.info("DPE %s: invalid %s %r ignored (%s)", self.ref, name, value, error)
            return
        self.found[name] = SourcedValue(
            value=value, source=SOURCE, date=self.date, ref=self.ref, raw=raw, comment=comment
        )


def _description_candidates(line: Mapping[str, Any]) -> dict[str, SourcedValue]:
    """Translate the description of a building-scale DPE into candidates for its entity.

    ``annee_construction`` gives ``construction_year``; ``nombre_appartement`` (1 for a house)
    ``dwelling_count``; ``nombre_niveau_immeuble`` ``level_count``; the DPE kind ``usage``; the
    reference area (living area, or useful area for a tertiary DPE) ``heated_area``, named in
    ``comment``. ``date`` is the DPE date and ``ref`` its number.
    """
    builder = _Builder(line, DescribedEntity)
    builder.add("construction_year", line.get("annee_construction"))
    if line.get(DATASET_KEY) == DATASETS["T"]:
        builder.add("usage", BuildingUsage.TERTIARY, raw=line.get("methode_dpe"))
        builder.add(
            "heated_area", line.get("surface_utile"), comment="surface utile (DPE tertiaire)"
        )
        return builder.found
    kind = line.get("type_batiment")
    if kind == "maison":
        builder.add("usage", BuildingUsage.RESIDENTIAL_INDIVIDUAL, raw=kind)
        builder.add("dwelling_count", 1)
        area, area_name = line.get("surface_habitable_logement"), "surface habitable du logement"
        if area is None:
            area, area_name = line.get("surface_habitable_immeuble"), "surface habitable"
    else:
        builder.add("usage", BuildingUsage.RESIDENTIAL_COLLECTIVE, raw=kind)
        builder.add("dwelling_count", line.get("nombre_appartement"))
        area, area_name = line.get("surface_habitable_immeuble"), "surface habitable de l'immeuble"
    builder.add("level_count", line.get("nombre_niveau_immeuble"))
    builder.add("heated_area", area, comment=f"{area_name} (DPE)")
    return builder.found


_COMPONENTS: dict[ComponentKind, tuple[str, tuple[str, ...]]] = {
    ComponentKind.WALL: ("deperditions_murs", ("qualite_isolation_murs",)),
    ComponentKind.ROOF: (
        "deperditions_planchers_hauts",
        (
            "qualite_isolation_plancher_haut_comble_perdu",
            "qualite_isolation_plancher_haut_comble_amenage",
            "qualite_isolation_plancher_haut_toit_terrasse",
        ),
    ),
    ComponentKind.FLOOR: ("deperditions_planchers_bas", ("qualite_isolation_plancher_bas",)),
    ComponentKind.WINDOW: ("deperditions_baies_vitrees", ("qualite_isolation_menuiseries",)),
    ComponentKind.DOOR: ("deperditions_portes", ("qualite_isolation_menuiseries",)),
    ComponentKind.THERMAL_BRIDGE: ("deperditions_ponts_thermiques", ()),
}
_INSULATION_ORDER = [
    InsulationLevel.INSUFFICIENT,
    InsulationLevel.AVERAGE,
    InsulationLevel.GOOD,
    InsulationLevel.VERY_GOOD,
]


def _envelope_candidates(
    line: Mapping[str, Any],
) -> tuple[dict[str, SourcedValue], dict[ComponentKind, Candidates]]:
    """Translate the envelope of a residential DPE (SPEC A.4).

    Returns
    -------
    tuple
        The envelope candidates (``u_bat`` in W/(m².K), ``inertia``), and the candidates of one
        component per kind: ``heat_loss_coefficient`` (W/K) and ``insulation``. The roof gets the
        worst of the three upper floor qualities given; windows and doors the joinery quality.
        Both are empty for a tertiary DPE.
    """
    envelope = _Builder(line, Envelope)
    components: dict[ComponentKind, Candidates] = {}
    if line.get(DATASET_KEY) == DATASETS["T"]:
        return envelope.found, components
    envelope.add("u_bat", line.get("ubat_w_par_m2_k"))
    label = line.get("classe_inertie_batiment")
    envelope.add("inertia", _mappings.dpe_inertia(label), raw=label)
    for kind, (loss_column, quality_columns) in _COMPONENTS.items():
        component = _Builder(line, EnvelopeComponent)
        component.add("heat_loss_coefficient", line.get(loss_column))
        qualities = [
            (_mappings.dpe_insulation(line.get(c)), line.get(c))
            for c in quality_columns
            if line.get(c) not in (None, *_mappings.EMPTY_LABELS)
        ]
        known = [(q, raw) for q, raw in qualities if q is not None]
        if known:
            worst, raw = min(known, key=lambda item: _INSULATION_ORDER.index(item[0]))
            component.add("insulation", worst, raw=raw)
        if component.found:
            components[kind] = component.found
    return envelope.found, components


def _generator_candidates(line: Mapping[str, Any]) -> dict[SystemKind, list[Candidates]]:
    """Translate the generators of a DPE into candidates, by system kind (SPEC A.5).

    Residential DPE: up to four heating generators (two installations of two generators), two
    DHW generators with ``storage_volume`` (L; 0, given for combined boilers, is left out) and
    their COP as ``nominal_performance_ratio``, and one cooling generator. The energy column takes
    precedence over the energy implied by the generator. Tertiary DPE: one heating generator with
    the main heating energy only.
    """
    produced: dict[SystemKind, list[Candidates]] = {
        SystemKind.HEATING: [],
        SystemKind.DHW: [],
        SystemKind.COOLING: [],
    }
    if line.get(DATASET_KEY) == DATASETS["T"]:
        label = line.get("type_energie_principale_chauffage")
        generator = _Builder(line, HeatingGenerator)
        generator.add("energy_type", _mappings.dpe_energy_type(label), raw=label)
        if generator.found:
            produced[SystemKind.HEATING].append(generator.found)
        return produced
    for g, i in _SLOTS_HEATING:
        found = _generator(
            line,
            HeatingGenerator,
            f"type_generateur_n{g}_installation_n{i}",
            f"type_energie_generateur_n{g}_installation_n{i}",
            f"installation {i}, generator {g}",
        )
        if found:
            produced[SystemKind.HEATING].append(found)
    for g in _SLOTS_DHW:
        extra = {
            "storage_volume": line.get(f"volume_stockage_generateur_n{g}_ecs_n1") or None,
            "nominal_performance_ratio": line.get(f"cop_generateur_n{g}_ecs_n1"),
        }
        found = _generator(
            line,
            DHWGenerator,
            f"type_generateur_n{g}_ecs_n1",
            f"type_energie_generateur_n{g}_ecs_n1",
            f"generator {g}",
            extra,
        )
        if found:
            produced[SystemKind.DHW].append(found)
    found = _generator(
        line, CoolingGenerator, "type_generateur_froid", "type_energie_climatisation", None
    )
    if found:
        produced[SystemKind.COOLING].append(found)
    return produced


def _generator(
    line: Mapping[str, Any],
    cls: type[SourcedModel],
    generator_column: str,
    energy_column: str,
    name: str | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, SourcedValue]:
    label = line.get(generator_column)
    if label in (None, *_mappings.EMPTY_LABELS):
        return {}
    mapping = _mappings.dpe_generator(label)
    builder = _Builder(line, cls)
    builder.add("name", name)
    builder.add("technology", mapping.technology, raw=label)
    energy_label = line.get(energy_column)
    energy = _mappings.dpe_energy_type(energy_label)
    if energy is not None:
        builder.add("energy_type", energy, raw=energy_label)
    else:
        builder.add("energy_type", mapping.energy_type, raw=label)
    for field, value in (extra or {}).items():
        builder.add(field, value)
    return builder.found


def _date(value: str | None) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value[:10]) if value else None
    except ValueError:
        logger.info("unreadable DPE date %r", value)
        return None


# --- park operation ---------------------------------------------------------------------------


def enrich(
    park: BuildingPark,
    buildings: Iterable[Building | str] | None = None,
    *,
    client: DpeClient | None = None,
) -> ImportReport:
    """Enrich the park from the DPE open data (SPEC §8.5).

    DPE are looked up by the ``rnb_id`` of the buildings (``id_rnb`` column) and by the ``dpe_ids``
    already known on buildings and sites. An apartment DPE generated from a building DPE leads to
    that building DPE. Every DPE number found is added to ``dpe_ids``; the values of the most
    recent building-scale DPE that is not replaced go on the entity carrying it: description,
    reference area, envelope (``u_bat``, inertia, one component per kind) and generators.

    Parameters
    ----------
    park
        Park to enrich.
    buildings
        Buildings (objects or ids) to enrich. ``None``: every building with an ``rnb_id`` or
        ``dpe_ids``, and every site with ``dpe_ids``.
    client
        DPE client; a default one is created when omitted.

    Returns
    -------
    ImportReport
        ``enriched`` entities, ``not_found`` entities without any DPE, and messages (DPE used,
        apartment DPE only).

    Raises
    ------
    UnknownIdError
        If a building id is not in the park.
    ConnectorError
        If the API fails persistently.
    """
    client = client or DpeClient()
    report = ImportReport(source=SOURCE)
    entities = list(_targets(park, buildings))
    if not entities:
        return report
    rnb_owner = {
        normalize_rnb_id(e.rnb_id): e for e in entities if isinstance(e, Building) and e.rnb_id
    }
    number_owners: dict[str, list[DescribedEntity]] = {}
    for entity in entities:
        for number in entity.dpe_ids:
            number_owners.setdefault(number, []).append(entity)

    lines = [*client.dpe_by_rnb(rnb_owner), *client.dpe_by_numbers(number_owners)]
    found: dict[str, dict[str, dict[str, Any]]] = {e.id: {} for e in entities}
    by_id = {e.id: e for e in entities}
    for line in lines:
        owners = list(number_owners.get(line.get("numero_dpe", ""), []))
        if line.get("id_rnb") and normalize_rnb_id(line["id_rnb"]) in rnb_owner:
            owners.append(rnb_owner[normalize_rnb_id(line["id_rnb"])])
        for owner in owners:
            found[owner.id][line["numero_dpe"]] = line

    _follow_building_dpe(client, found)

    for entity_id, entity_lines in found.items():
        entity = by_id[entity_id]
        if not entity_lines:
            report.add("not_found", entity_id, "no DPE found")
            continue
        _add_numbers(entity, entity_lines)
        chosen = _select_dpe(entity_lines.values())
        if chosen is None:
            report.note(entity_id, f"{len(entity_lines)} DPE found, none for the whole building")
            continue
        _apply(entity, chosen, report)
        report.add("enriched", entity_id, f"DPE {chosen['numero_dpe']} used")
    return report


def _targets(
    park: BuildingPark, buildings: Iterable[Building | str] | None
) -> Iterator[DescribedEntity]:
    if buildings is not None:
        yield from select_buildings(park, buildings)
        return
    yield from (b for b in park.iter_buildings() if b.rnb_id or b.dpe_ids)
    yield from (s for s in park.iter_sites() if s.dpe_ids)


def _follow_building_dpe(client: DpeClient, found: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Add the building DPE that the apartment DPE found were generated from."""
    wanted: dict[str, list[str]] = {}
    for entity_id, lines in found.items():
        for line in list(lines.values()):
            number = line.get("numero_dpe_immeuble_associe")
            if number and number not in lines:
                wanted.setdefault(number, []).append(entity_id)
    if not wanted:
        return
    for line in client.dpe_by_numbers(wanted):
        for entity_id in wanted.get(line.get("numero_dpe", ""), []):
            found[entity_id][line["numero_dpe"]] = line


def _add_numbers(entity: DescribedEntity, lines: Mapping[str, Mapping[str, Any]]) -> None:
    replaced = {line.get("numero_dpe_remplace") for line in lines.values()}
    new = [n for n in lines if n not in replaced and n not in entity.dpe_ids]
    if new:
        entity.dpe_ids = [*entity.dpe_ids, *sorted(new)]


def _apply(entity: DescribedEntity, line: Mapping[str, Any], report: ImportReport) -> None:
    put(entity, _description_candidates(line), source=SOURCE, clear=True)
    envelope, components = _envelope_candidates(line)
    put(entity.envelope, envelope, source=SOURCE, clear=True)
    sync_components(entity.envelope, SOURCE, components)
    for kind, produced in _generator_candidates(line).items():
        sync_systems(entity, kind, SOURCE, produced, report)


def _check_line(line: Any) -> Mapping[str, Any]:
    if not isinstance(line, Mapping) or "numero_dpe" not in line:
        raise ConnectorError(f"unexpected DPE line: {line!r:.200}")
    return line
