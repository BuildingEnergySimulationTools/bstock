"""Tabular and geographic exports of a park (SPEC §9)."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock._deps import require
from bstock.description import DescribedEntity
from bstock.exceptions import InvalidArgumentError
from bstock.selection import EntityType, check_entity_type

if TYPE_CHECKING:
    from geopandas import GeoDataFrame

    from bstock.park import BuildingPark

__all__ = ["to_dataframe", "to_geodataframe", "to_geopackage", "to_geoparquet"]

GeometryKind = Literal["footprint", "point"]
CRS = "EPSG:4326"
"""Coordinate reference system of the exported geometries (WGS84)."""
LAYERS: dict[EntityType, str] = {"site": "sites", "building": "buildings"}
"""GeoPackage layer of each entity type."""

EXCLUDED_ATTRIBUTES = ("footprint",)
"""Sourced fields left out of :func:`to_dataframe`."""

_ID_COLUMNS: dict[str, tuple[str, ...]] = {
    "building": (
        "rnb_id",
        "bdnb_batiment_groupe_id",
        "bdnb_batiment_construction_id",
        "dpe_ids",
        "parent_id",
    ),
    "site": ("bdnb_batiment_groupe_id", "dpe_ids", "parent_id"),
}


def to_dataframe(
    park: BuildingPark, entity: EntityType = "building", *, provenance: bool = False
) -> pd.DataFrame:
    """Return one row per building (or site) of ``park``, indexed by ``id``.

    Parameters
    ----------
    park
        The park to export.
    entity
        ``"building"`` for every building of the park, ``"site"`` for every site, nested ones
        included.
    provenance
        Add, after each field column, ``<attr>_source`` (source of the effective value) and
        ``<attr>_provider`` (id of the entity providing it, an ancestor site when inherited).

    Returns
    -------
    pandas.DataFrame
        Columns: the external ids, ``dpe_ids`` and ``parent_id``, then the effective value of each
        sourced field, inheritance included, in the unit of the field. Fields of
        project subclasses are included and ``footprint`` is excluded. Enums are given by their
        value, lists are kept as lists, integer fields use the nullable ``Int64`` dtype.

    Raises
    ------
    InvalidArgumentError
        If ``entity`` is neither ``"building"`` nor ``"site"``.
    """
    check_entity_type(entity)
    entities = list(park.iter_buildings() if entity == "building" else park.iter_sites())
    return _entities_frame(park, entity, entities, provenance=provenance)


def _entities_frame(
    park: BuildingPark,
    entity_type: EntityType,
    entities: list[DescribedEntity],
    *,
    provenance: bool = False,
) -> pd.DataFrame:
    """Return the :func:`to_dataframe` rows of ``entities``, buildings or sites of ``park``."""
    base: type[DescribedEntity] = park.building_cls if entity_type == "building" else park.site_cls
    fields = _exported_fields(base, entities)
    columns = list(_ID_COLUMNS[entity_type])
    for name in fields:
        columns.append(name)
        if provenance:
            columns += [f"{name}_source", f"{name}_provider"]

    rows = [_row(entity, entity_type, fields, provenance) for entity in entities]
    frame = pd.DataFrame(rows, columns=columns, index=pd.Index([e.id for e in entities], name="id"))
    for name, field in fields.items():
        if field.type_ is int:
            frame[name] = frame[name].astype("Int64")
    return frame


def _exported_fields(
    base: type[DescribedEntity], entities: list[DescribedEntity]
) -> dict[str, Any]:
    """Return the sourced fields of ``base``, then those added by subclasses of some entities."""
    fields = dict(base.sourced_fields())
    for entity in entities:
        for name, field in type(entity).sourced_fields().items():
            fields.setdefault(name, field)
    return {name: f for name, f in fields.items() if name not in EXCLUDED_ATTRIBUTES}


def _row(
    entity: DescribedEntity, entity_type: str, fields: dict[str, Any], provenance: bool
) -> dict[str, Any]:
    parent = entity.parent
    row: dict[str, Any] = {
        column: getattr(entity, column)
        for column in _ID_COLUMNS[entity_type]
        if column not in ("dpe_ids", "parent_id")
    }
    row["dpe_ids"] = list(entity.dpe_ids)
    row["parent_id"] = None if parent is None else parent.id
    for name in fields:
        if name not in type(entity).__sourced_fields__:
            continue  # field of another subclass
        candidate = entity.effective(name)
        row[name] = None if candidate is None else _cell(candidate.value)
        if provenance:
            row[f"{name}_source"] = None if candidate is None else candidate.source
            row[f"{name}_provider"] = entity.provider_id(name)
    return row


def _cell(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_cell(v) for v in value]
    return value


# --- geographic exports (extra [geo]) ---------------------------------------------------------


def to_geodataframe(
    park: BuildingPark,
    entity: EntityType = "building",
    *,
    geometry: GeometryKind = "footprint",
    provenance: bool = False,
) -> GeoDataFrame:
    """Return :func:`to_dataframe` as a GeoDataFrame in WGS84 (EPSG:4326).

    Parameters
    ----------
    park, entity, provenance
        As in :func:`to_dataframe`.
    geometry
        ``"footprint"``: the footprint polygons; ``"point"``: the effective ``lat`` / ``lon``
        point, inheritance included. An entity without geometry is kept with a missing geometry
        (``None``).

    Returns
    -------
    geopandas.GeoDataFrame
        The columns of :func:`to_dataframe` and a ``geometry`` column, in degrees (WGS84).

    Raises
    ------
    MissingDependencyError
        If the ``[geo]`` extra is not installed.
    InvalidArgumentError
        If ``entity`` is invalid.
    InvalidArgumentError
        If ``geometry`` is neither ``"footprint"`` nor ``"point"``.
    """
    if geometry not in ("footprint", "point"):
        raise InvalidArgumentError(f"geometry must be 'footprint' or 'point', not {geometry!r}")
    geopandas = require("geopandas", "geo")
    shapely_geometry = require("shapely.geometry", "geo")
    frame = to_dataframe(park, entity, provenance=provenance)
    entities = {
        e.id: e for e in (park.iter_buildings() if entity == "building" else park.iter_sites())
    }
    shapes: list[Any] = []
    for entity_id in frame.index:
        item = entities[entity_id]
        if geometry == "footprint":
            footprint = item.footprint
            shapes.append(None if footprint is None else shapely_geometry.shape(footprint))
        elif item.lat is None or item.lon is None:
            shapes.append(None)
        else:
            shapes.append(shapely_geometry.Point(item.lon, item.lat))
    return geopandas.GeoDataFrame(frame, geometry=shapes, crs=CRS)


def to_geopackage(
    park: BuildingPark,
    path: str | Path,
    *,
    geometry: GeometryKind = "footprint",
    provenance: bool = False,
) -> Path:
    """Write the sites and the buildings to the ``sites`` and ``buildings`` layers of a GeoPackage.

    The GeoPackage format has no list column: lists (``dpe_ids``, ``sub_usages``...) are written as
    JSON strings. The ``id`` index is written as a column. Existing layers are replaced.

    Parameters
    ----------
    park
        The park to export.
    path
        File to write (``.gpkg``).
    geometry, provenance
        As in :func:`to_geodataframe`.

    Returns
    -------
    pathlib.Path
        The written file.

    Raises
    ------
    MissingDependencyError
        If the ``[geo]`` extra is not installed.
    """
    path = Path(path)
    for entity_type, layer in LAYERS.items():
        frame = to_geodataframe(park, entity_type, geometry=geometry, provenance=provenance)
        _lists_to_json(frame).to_file(path, layer=layer, driver="GPKG", index=True)
    return path


def to_geoparquet(
    park: BuildingPark,
    path: str | Path,
    entity: EntityType = "building",
    *,
    geometry: GeometryKind = "footprint",
    provenance: bool = False,
) -> Path:
    """Write the buildings (or the sites) to a GeoParquet file; lists are kept as lists.

    Parameters
    ----------
    park
        The park to export.
    path
        File to write (``.parquet``).
    entity, geometry, provenance
        As in :func:`to_geodataframe`.

    Returns
    -------
    pathlib.Path
        The written file.

    Raises
    ------
    MissingDependencyError
        If the ``[geo]`` extra is not installed.
    """
    path = Path(path)
    to_geodataframe(park, entity, geometry=geometry, provenance=provenance).to_parquet(path)
    return path


def _lists_to_json(frame: GeoDataFrame) -> GeoDataFrame:
    """Return ``frame`` with list and dict values written as JSON strings."""
    frame = frame.copy()
    for column in frame.columns:
        if column == frame.geometry.name or frame[column].dtype != object:
            continue
        if frame[column].map(lambda v: isinstance(v, (list, dict))).any():
            frame[column] = frame[column].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
    return frame
