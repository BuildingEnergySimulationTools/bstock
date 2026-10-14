"""Tabular and geographic exports of a park (SPEC §9)."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock.description import DescribedEntity
from bstock.exceptions import UnknownKindError

if TYPE_CHECKING:
    from bstock.park import BuildingPark

__all__ = ["to_dataframe"]

ExportKind = Literal["building", "site"]

EXCLUDED_ATTRIBUTES = ("footprint",)
"""Sourced attributes left out of :func:`to_dataframe`."""

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
    park: BuildingPark, kind: ExportKind = "building", *, provenance: bool = False
) -> pd.DataFrame:
    """Return one row per building (or site) of ``park``, indexed by ``id``.

    Parameters
    ----------
    park
        The park to export.
    kind
        ``"building"`` for every building of the park, ``"site"`` for every site, nested ones
        included.
    provenance
        Add, after each attribute column, ``<attr>_source`` (source of the effective value) and
        ``<attr>_provider`` (id of the entity providing it, an ancestor site when inherited).

    Returns
    -------
    pandas.DataFrame
        Columns: the external ids, ``dpe_ids`` and ``parent_id``, then the effective value of each
        sourced attribute, inheritance included, in the unit of the attribute. Attributes of
        project subclasses are included and ``footprint`` is excluded. Enums are given by their
        value, lists are kept as lists, integer attributes use the nullable ``Int64`` dtype.

    Raises
    ------
    UnknownKindError
        If ``kind`` is neither ``"building"`` nor ``"site"``.
    """
    if kind not in _ID_COLUMNS:
        raise UnknownKindError(f"unknown export kind {kind!r}; expected 'building' or 'site'")
    entities: list[DescribedEntity]
    if kind == "building":
        entities = list(park.iter_buildings())
        base: type[DescribedEntity] = park.building_cls
    else:
        entities = list(park.iter_sites())
        base = park.site_cls

    fields = _exported_fields(base, entities)
    columns = list(_ID_COLUMNS[kind])
    for name in fields:
        columns.append(name)
        if provenance:
            columns += [f"{name}_source", f"{name}_provider"]

    rows = [_row(entity, kind, fields, provenance) for entity in entities]
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
    entity: DescribedEntity, kind: str, fields: dict[str, Any], provenance: bool
) -> dict[str, Any]:
    parent = entity.parent
    row: dict[str, Any] = {
        column: getattr(entity, column)
        for column in _ID_COLUMNS[kind]
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
