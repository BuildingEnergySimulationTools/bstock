"""Aggregations of extensive quantities over a selection (SPEC §14.3)."""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock._fields import field_names
from bstock.enums import SystemKind
from bstock.exceptions import InvalidArgumentError, NoDataWarning, UnknownFieldError

if TYPE_CHECKING:
    from bstock.building import Building
    from bstock.park import BuildingPark
    from bstock.provenance import SourcedField
    from bstock.selection import Selection
    from bstock.site import Site
    from bstock.systems import System

__all__: list[str] = []

PARK_ROW = "__park__"
"""Index of the park total row; ids cannot contain ``__``, so it never clashes with a site id."""
FIELD_MEASURES = ("buildings", "site", "delta", "coverage", "total")
"""Measures of an extensive field in the hierarchical aggregation."""
POWERS: dict[str, tuple[SystemKind, str]] = {
    "heating_power": (SystemKind.HEATING, "nominal_power"),
    "cooling_power": (SystemKind.COOLING, "nominal_power"),
    "dhw_power": (SystemKind.DHW, "nominal_power"),
    "pv_peak_power": (SystemKind.PV, "peak_power"),
}
"""Power columns: system kind and summed field (kW, or kWp for PV)."""

Prefer = Literal["buildings", "site"]
Container = Any  # BuildingPark or Site


def aggregate(
    selection: Selection,
    fields: str | Sequence[str] | None = None,
    *,
    by: str | None = None,
    prefer: Prefer = "buildings",
) -> pd.DataFrame:
    """Aggregate the extensive fields of a selection; see :meth:`Selection.aggregate`."""
    if prefer not in ("buildings", "site"):
        raise InvalidArgumentError(f"prefer must be 'buildings' or 'site', not {prefer!r}")
    names = _extensive_fields(selection.park, fields)
    if by is not None:
        if selection.entity != "building":
            raise InvalidArgumentError("by= only applies to a selection of buildings")
        return _aggregate_by(selection, names, by)
    scope = _Scope(selection)
    rows = {site.id: _site_row(scope, site, names, prefer) for site in scope.row_sites()}
    rows[PARK_ROW] = _park_row(scope, names, prefer)
    frame = pd.DataFrame.from_dict(rows, orient="index")
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["quantity", "measure"])
    frame.index.name = "id"
    return frame


def _extensive_fields(park: BuildingPark, fields: str | Sequence[str] | None) -> list[str]:
    available = park.building_cls.sourced_fields()
    fields = field_names(fields)
    if fields is None:
        return [name for name, field in available.items() if field.extensive]
    for name in fields:
        if name not in available:
            raise UnknownFieldError(f"{name!r} is not a field of the buildings of this park")
        if not available[name].extensive:
            raise InvalidArgumentError(f"{name!r} is not extensive: it cannot be summed")
    return list(fields)


class _Scope:
    """Which sites and buildings count for a selection.

    For a selection of buildings: the selected buildings, and the sites containing them. For a
    selection of sites: every site and building of their subtrees.
    """

    def __init__(self, selection: Selection) -> None:
        self.park = selection.park
        self.entity_type = selection.entity
        entities = selection._entities()
        if self.entity_type == "building":
            self.roots: list[Any] = []
            self.buildings = {b.id for b in entities}
            self.sites = {s.id for b in entities for s in b._ancestor_sites()}
        else:
            selected = {s.id for s in entities}
            self.roots = [
                s for s in entities if not any(a.id in selected for a in s._ancestor_sites())
            ]
            subtree_sites = [x for s in entities for x in s.iter_sites()]
            self.sites = selected | {s.id for s in subtree_sites}
            self.buildings = {b.id for s in entities for b in s.iter_buildings()}
        self._selected_sites = entities if self.entity_type == "site" else []

    def row_sites(self) -> list[Site]:
        """Return the sites that get a row."""
        if self.entity_type == "site":
            return list(self._selected_sites)
        return [s for s in self.park.iter_sites() if s.id in self.sites]

    def top_items(self) -> list[Any]:
        """Return the counted sites and buildings that have no counted ancestor."""
        if self.entity_type == "site":
            return list(self.roots)
        return [*self.child_sites(self.park), *self.child_buildings(self.park)]

    def child_sites(self, container: Container) -> list[Site]:
        return [s for s in container.sites if s.id in self.sites]

    def child_buildings(self, container: Container) -> list[Building]:
        return [b for b in container.buildings if b.id in self.buildings]

    def walk(self, items: Iterable[Any]) -> tuple[list[Site], list[Building]]:
        """Return the counted sites and buildings in and below ``items``."""
        from bstock.site import Site

        sites: list[Site] = []
        buildings: list[Building] = []
        stack = list(items)
        while stack:
            item = stack.pop(0)
            if isinstance(item, Site):
                sites.append(item)
                stack.extend([*self.child_sites(item), *self.child_buildings(item)])
            else:
                buildings.append(item)
        return sites, buildings

    def site_value_counts(self, site: Site) -> bool:
        """Whether the own value of ``site`` may enter a total: it covers counted buildings only."""
        return self.entity_type == "site" or all(
            b.id in self.buildings for b in site.iter_buildings()
        )

    def systems(self, sites: list[Site], buildings: list[Building]) -> list[System]:
        """Return the systems owned by ``sites`` and ``buildings`` that serve counted buildings."""
        found: list[System] = [s for b in buildings for s in b.iter_systems()]
        for site in sites:
            found.extend(s for s in site.iter_systems() if self._serves_counted(site, s))
        return found

    def _serves_counted(self, site: Site, system: System) -> bool:
        if self.entity_type == "site":
            return True
        if system.serves is None:
            return any(b.id in self.buildings for b in site.iter_buildings())
        return any(building_id in self.buildings for building_id in system.serves)


# --- hierarchical rows ---------------------------------------------------------------------------


def _site_row(scope: _Scope, site: Site, names: list[str], prefer: Prefer) -> dict[Any, Any]:
    sites, buildings = scope.walk([site])
    row: dict[Any, Any] = {}
    for name in names:
        summed, coverage = _sum(_value(b, name) for b in buildings)
        own = _value(site, name)
        row.update(
            {
                (name, "buildings"): summed,
                (name, "site"): own,
                (name, "delta"): own - summed,
                (name, "coverage"): coverage,
                (name, "total"): _total(scope, site, name, prefer),
            }
        )
    row.update(_system_columns(scope, sites, buildings))
    return row


def _park_row(scope: _Scope, names: list[str], prefer: Prefer) -> dict[Any, Any]:
    top = scope.top_items()
    sites, buildings = scope.walk(top)
    row: dict[Any, Any] = {}
    for name in names:
        summed, coverage = _sum(_value(b, name) for b in buildings)
        total, _ = _sum(_total(scope, item, name, prefer) for item in top)
        row.update(
            {
                (name, "buildings"): summed,
                (name, "site"): math.nan,
                (name, "delta"): math.nan,
                (name, "coverage"): coverage,
                (name, "total"): total,
            }
        )
    row.update(_system_columns(scope, sites, buildings))
    return row


def _total(scope: _Scope, item: Any, name: str, prefer: Prefer) -> float:
    """Return the value retained for ``item``, computed bottom-up (SPEC §14.3, ``prefer``)."""
    from bstock.site import Site

    if not isinstance(item, Site):
        return _value(item, name)
    own = _value(item, name) if scope.site_value_counts(item) else math.nan
    children = [*scope.child_sites(item), *scope.child_buildings(item)]
    summed, _ = _sum(_total(scope, child, name, prefer) for child in children)
    if prefer == "site":
        return summed if math.isnan(own) else own
    return own if math.isnan(summed) else summed


def _system_columns(scope: _Scope, sites: list[Site], buildings: list[Building]) -> dict[Any, Any]:
    systems = scope.systems(sites, buildings)
    columns: dict[Any, Any] = {}
    for column, (kind, field_name) in POWERS.items():
        values = [getattr(s, field_name) for s in systems if s.kind == kind]
        summed, coverage = _sum(math.nan if v is None else float(v) for v in values)
        columns[(column, "value")] = summed
        columns[(column, "coverage")] = coverage
    counted_ids = {*(s.id for s in sites), *(b.id for b in buildings), *(s.id for s in systems)}
    columns[("building_count", "value")] = len(buildings)
    for kind in SystemKind:
        columns[(f"{kind}_count", "value")] = sum(1 for s in systems if s.kind == kind)
    columns[("meter_count", "value")] = sum(
        1 for m in scope.park.meters if counted_ids & set(m.scope)
    )
    return columns


def _value(entity: Any, name: str) -> float:
    """Return the effective value of ``name`` as a float, NaN when missing or not declared."""
    if name not in entity.sourced_fields():
        return math.nan
    value = getattr(entity, name)
    return math.nan if value is None else float(value)


def _sum(values: Iterable[float]) -> tuple[float, float]:
    """Return the sum of the known values (NaN if none) and the share of known values."""
    values = list(values)
    known = [v for v in values if not math.isnan(v)]
    total = math.fsum(known) if known else math.nan
    coverage = len(known) / len(values) if values else math.nan
    return total, coverage


# --- grouping by field -----------------------------------------------------------------------


def _aggregate_by(selection: Selection, names: list[str], by: str) -> pd.DataFrame:
    park = selection.park
    field: SourcedField[Any] | None = park.building_cls.sourced_fields().get(by)
    if field is None:
        raise UnknownFieldError(f"{by!r} is not a field of the buildings of this park")
    buildings = selection._entities()
    groups: dict[Any, list[Building]] = {}
    for building in buildings:
        groups.setdefault(_group_key(building, by), []).append(building)
    _warn_ignored_site_values(buildings, names)
    keys = [k for k in groups if k is not pd.NA]
    if pd.NA in groups:
        keys.append(pd.NA)
    rows = {}
    for key in keys:
        row: dict[Any, Any] = {}
        for name in names:
            summed, coverage = _sum(_value(b, name) for b in groups[key])
            row[(name, "buildings")] = summed
            row[(name, "coverage")] = coverage
        row[("building_count", "value")] = len(groups[key])
        rows[key] = row
    frame = pd.DataFrame.from_dict(rows, orient="index")
    if not rows:
        frame = pd.DataFrame(columns=_by_columns(names))
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["quantity", "measure"])
    frame.index = pd.Index(keys, name=by, dtype=object)
    return frame


def _by_columns(names: list[str]) -> list[tuple[str, str]]:
    columns = [(name, measure) for name in names for measure in ("buildings", "coverage")]
    return [*columns, ("building_count", "value")]


def _group_key(building: Building, by: str) -> Any:
    value = getattr(building, by)
    if value is None:
        return pd.NA
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list | dict):
        raise InvalidArgumentError(f"cannot group by {by!r}: its values are not single values")
    return value


def _warn_ignored_site_values(buildings: list[Building], names: list[str]) -> None:
    sites = {s.id: s for b in buildings for s in b._ancestor_sites()}
    ignored = sorted(
        site_id
        for site_id, site in sites.items()
        if any(not math.isnan(_value(site, name)) for name in names)
    )
    if ignored:
        warnings.warn(
            f"own values of sites {ignored} are ignored when grouping buildings by field",
            NoDataWarning,
            stacklevel=5,
        )


# --- completeness and provenance (SPEC §14.4) ----------------------------------------------------

CONFLICT_EXCLUDED = ("footprint",)
"""Fields left out of :func:`conflicts` by default: two geometries never match exactly."""


def completeness(
    selection: Selection,
    *,
    per: Literal["field", "entity"] = "field",
    fields: str | Sequence[str] | None = None,
) -> pd.DataFrame:
    """Return the share of filled fields; see :meth:`Selection.completeness`."""
    if per not in ("field", "entity"):
        raise InvalidArgumentError(f"per must be 'field' or 'entity', not {per!r}")
    entities = selection._entities()
    names = _sourced_names(selection, entities, fields)
    if per == "entity":
        rows = []
        for entity in entities:
            own = [name for name in names if name in entity.sourced_fields()]
            missing = [name for name in own if getattr(entity, name) is None]
            rows.append(
                {
                    "filled": len(own) - len(missing),
                    "total": len(own),
                    "rate": _rate(len(own) - len(missing), len(own)),
                    "missing": missing,
                }
            )
        index = pd.Index([e.id for e in entities], name="id")
        return pd.DataFrame(rows, index=index, columns=["filled", "total", "rate", "missing"])
    rows = []
    for name in names:
        concerned = [e for e in entities if name in e.sourced_fields()]
        providers = [e._provider(name) for e in concerned]
        filled = sum(1 for p in providers if p is not None)
        inherited = sum(1 for e, p in zip(concerned, providers, strict=True) if p not in (None, e))
        rows.append(
            {
                "filled": filled,
                "inherited": inherited,
                "total": len(concerned),
                "rate": _rate(filled, len(concerned)),
            }
        )
    index = pd.Index(names, name="field")
    return pd.DataFrame(rows, index=index, columns=["filled", "inherited", "total", "rate"])


def provenance(
    selection: Selection, *, fields: str | Sequence[str] | None = None, normalize: bool = False
) -> pd.DataFrame:
    """Return the sources of the effective values; see :meth:`Selection.provenance`."""
    entities = selection._entities()
    names = _sourced_names(selection, entities, fields)
    counts: dict[str, dict[str, int]] = {}
    extra: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for name in names:
        concerned = [e for e in entities if name in e.sourced_fields()]
        totals[name] = len(concerned)
        by_source: dict[str, int] = {}
        inherited = missing = 0
        for entity in concerned:
            candidate = entity.effective(name)
            if candidate is None:
                missing += 1
                continue
            by_source[candidate.source] = by_source.get(candidate.source, 0) + 1
            if entity._provider(name) is not entity:
                inherited += 1
        counts[name] = by_source
        extra[name] = {"inherited": inherited, "missing": missing}
    overall: dict[str, int] = {}
    for by_source in counts.values():
        for source, count in by_source.items():
            overall[source] = overall.get(source, 0) + count
    sources = sorted(overall, key=lambda source: (-overall[source], source))
    rows = [{**{s: counts[name].get(s, 0) for s in sources}, **extra[name]} for name in names]
    frame = pd.DataFrame(
        rows, index=pd.Index(names, name="field"), columns=[*sources, "inherited", "missing"]
    )
    if normalize:
        frame = frame.div(pd.Series(totals).replace(0, math.nan), axis=0)
    return frame


def conflicts(
    selection: Selection, *, fields: str | Sequence[str] | None = None, rtol: float = 0.05
) -> pd.DataFrame:
    """Return the fields whose own candidates disagree; see :meth:`Selection.conflicts`."""
    if rtol < 0:
        raise InvalidArgumentError(f"rtol must be positive, not {rtol!r}")
    entities = selection._entities()
    names = _sourced_names(selection, entities, fields)
    if fields is None:
        names = [name for name in names if name not in CONFLICT_EXCLUDED]
    rows = []
    for entity in entities:
        for name in names:
            if name not in entity.sourced_fields():
                continue
            candidates = entity.candidates(name)
            values = [c.value for c in candidates]
            if len(candidates) < 2 or _agree(values, rtol):
                continue
            retained = entity.effective(name)
            rows.append(
                {
                    "entity": entity.id,
                    "field": name,
                    "values": {c.source: _plain(c.value) for c in candidates},
                    "retained": None if retained is None else retained.source,
                }
            )
    return pd.DataFrame(rows, columns=["entity", "field", "values", "retained"])


def _sourced_names(
    selection: Selection, entities: list[Any], fields: str | Sequence[str] | None
) -> list[str]:
    """Return ``fields``, checked, or every sourced field of the selected entities."""
    fields = field_names(fields)
    park = selection.park
    base = park.building_cls if selection.entity == "building" else park.site_cls
    available = list(base.sourced_fields())
    for entity in entities:
        available += [n for n in type(entity).sourced_fields() if n not in available]
    if fields is None:
        return available
    unknown = ", ".join(repr(name) for name in fields if name not in available)
    if unknown:
        raise UnknownFieldError(f"unknown field(s) of the {selection.entity}s: {unknown}")
    return list(fields)


def _rate(count: int, total: int) -> float:
    return count / total if total else math.nan


def _agree(values: list[Any], rtol: float) -> bool:
    """Whether the candidate values agree: equal, or floats within ``rtol`` of the largest."""
    if all(isinstance(v, float) for v in values):
        largest = max(abs(v) for v in values)
        return max(values) - min(values) <= rtol * largest
    first = values[0]
    return all(v == first and type(v) is type(first) for v in values[1:])


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value
