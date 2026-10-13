"""BuildingPark: root of the data model (SPEC §3.4, §5)."""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationInfo, model_validator
from pydantic.json_schema import SkipJsonSchema

from bstock.building import Building
from bstock.description import DescribedEntity
from bstock.enums import EndUse, EnergyType, SystemKind
from bstock.exceptions import (
    DuplicateIdError,
    EntityInUseError,
    NoDataWarning,
    UnknownIdError,
)
from bstock.meters import Meter
from bstock.provenance import SourcePriority
from bstock.site import Site, build_children
from bstock.systems import System

__all__ = ["BuildingPark"]

Entity = Site | Building | System | Meter


class BuildingPark(BaseModel):
    """A building stock: top-level sites and buildings, and the source priority.

    Collections are modified through the park methods (or the entity methods, which keep the park
    index up to date). Ids are unique in the whole park, and so are RNB ids.

    Parameters
    ----------
    name
        Name of the park, e.g. ``"Ville de Bayonne"``.
    site_cls, building_cls
        Classes instantiated by :meth:`new_site` and :meth:`new_building` and required by
        :meth:`add_site` and :meth:`add_building`; project subclasses of :class:`Site` and
        :class:`Building` (SPEC §5). Not serialised.

    Notes
    -----
    Use :meth:`to_json` / :meth:`from_json` (or :meth:`save` / :meth:`load`) rather than the
    Pydantic ``model_dump_json`` / ``model_validate_json``: these skip the file metadata (format
    version, class paths), so neither the format version nor the classes are checked on loading.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str
    description: str | None = None
    source_priority: SourcePriority = Field(default_factory=SourcePriority)
    sites: list[Site] = Field(default_factory=list)
    buildings: list[Building] = Field(default_factory=list)
    meters: list[Meter] = Field(default_factory=list)
    site_cls: SkipJsonSchema[type[Site]] = Field(default=Site, exclude=True)
    building_cls: SkipJsonSchema[type[Building]] = Field(default=Building, exclude=True)

    _index: dict[str, Entity] = PrivateAttr(default_factory=dict)
    _rnb_index: dict[str, Building] = PrivateAttr(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _build_children_with_context_classes(cls, data: Any, info: ValidationInfo) -> Any:
        default = None
        if isinstance(data, dict):
            default = (
                _class_or(data.get("site_cls"), Site),
                _class_or(data.get("building_cls"), Building),
            )
        return build_children(data, info, default)

    def model_post_init(self, context: Any) -> None:
        """Check the entity classes, attach the top-level entities and build the id index."""
        for entity in [*self.sites, *self.buildings]:
            self._check_classes(entity)
        for entity in [*self.sites, *self.buildings]:
            self._register(entity)
            entity._container = self
        for meter in self.meters:
            self._check_scope(meter.id, meter.scope)
            self._register(meter)
            meter._container = self

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BaseModel):
            return NotImplemented
        return type(self) is type(other) and self.__dict__ == other.__dict__

    __hash__ = None  # type: ignore[assignment]

    # --- persistence (implemented in bstock.io) ------------------------------------------------

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialise the park to JSON (SPEC §7.2); metered data is not included."""
        from bstock import io  # circular import

        return io.to_json(self, indent=indent)

    @classmethod
    def from_json(
        cls,
        text: str | bytes,
        *,
        site_cls: type[Site] = Site,
        building_cls: type[Building] = Building,
    ) -> BuildingPark:
        """Load a park serialised by :meth:`to_json`, building project classes for its entities."""
        from bstock import io  # circular import

        return io.from_json(text, site_cls=site_cls, building_cls=building_cls)

    def save(self, directory: str | Path, *, include_data: bool = False) -> Path:
        """Save the park in ``directory`` (``park.json``, plus Parquet files with ``include_data``).

        The park itself is modified: relative paths of data sources become absolute and, with
        ``include_data``, the meters point to the saved Parquet files. See :func:`bstock.io.save`.
        """
        from bstock import io  # circular import

        return io.save(self, directory, include_data=include_data)

    @classmethod
    def load(
        cls,
        directory: str | Path,
        *,
        site_cls: type[Site] = Site,
        building_cls: type[Building] = Building,
    ) -> BuildingPark:
        """Load a park saved by :meth:`save`. See :func:`bstock.io.load`."""
        from bstock import io  # circular import

        return io.load(directory, site_cls=site_cls, building_cls=building_cls)

    @classmethod
    def json_schema(
        cls, *, site_cls: type[Site] = Site, building_cls: type[Building] = Building
    ) -> dict[str, Any]:
        """Return the JSON Schema of the park file. See :func:`bstock.io.json_schema`."""
        from bstock import io  # circular import

        return io.json_schema(site_cls=site_cls, building_cls=building_cls)

    # --- lookup -------------------------------------------------------------------------------

    def get(self, entity_id: str) -> Entity | None:
        """Return the site, building or system with id ``entity_id``, or ``None``."""
        return self._index.get(entity_id)

    def __getitem__(self, entity_id: str) -> Entity:
        """Return the entity ``entity_id``; raise :class:`UnknownIdError` if there is none."""
        try:
            return self._index[entity_id]
        except KeyError:
            raise UnknownIdError(entity_id) from None

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self._index

    def get_by_rnb(self, rnb_id: str) -> Building | None:
        """Return the building whose RNB id is ``rnb_id``, or ``None``."""
        return self._rnb_index.get(rnb_id)

    def iter_sites(self) -> Iterator[Site]:
        """Iterate over every site of the park, depth first."""
        for site in self.sites:
            yield site
            yield from site.iter_sites(recursive=True)

    def iter_buildings(self) -> Iterator[Building]:
        """Iterate over every building of the park."""
        yield from self.buildings
        for site in self.sites:
            yield from site.iter_buildings(recursive=True)

    def iter_systems(self, kind: SystemKind | str | None = None) -> Iterator[System]:
        """Iterate over every system of the park, optionally of one ``kind``."""
        for entity in [*self.iter_sites(), *self.iter_buildings()]:
            yield from entity.iter_systems(kind)

    def meters_for(self, entity: Site | Building | str) -> list[Meter]:
        """Return the meters whose scope contains ``entity`` or one of its own systems."""
        target = self._resolve(entity, (Site, Building))
        ids = {target.id, *(s.id for s in target.iter_systems())}
        return [m for m in self.meters if ids & set(m.scope)]

    # --- creation -----------------------------------------------------------------------------

    def new_site(
        self,
        id: str,
        parent: Site | str | None = None,
        *,
        source: str | None = None,
        **attrs: Any,
    ) -> Site:
        """Create a site of class :attr:`site_cls`, attach it and return it.

        Parameters
        ----------
        id
            Site id.
        parent
            Parent site (object or id); ``None`` for a top-level site.
        source
            Source of the sourced attributes given in ``attrs`` (``"manual"`` by default).
        **attrs
            Site fields and sourced attributes.
        """
        site = self.site_cls(id=id, source=source, **attrs)
        return self.add_site(site, parent)

    def new_building(
        self,
        rnb_id: str | None = None,
        id: str | None = None,
        site: Site | str | None = None,
        *,
        source: str | None = None,
        **attrs: Any,
    ) -> Building:
        """Create a building of class :attr:`building_cls`, attach it and return it.

        Parameters
        ----------
        rnb_id
            RNB id; also the building id when ``id`` is omitted.
        id
            Explicit building id.
        site
            Site (object or id) receiving the building; ``None`` attaches it to the park.
        source
            Source of the sourced attributes given in ``attrs`` (``"manual"`` by default).
        **attrs
            Building fields and sourced attributes.
        """
        building = self.building_cls(rnb_id=rnb_id, id=id, source=source, **attrs)
        return self.add_building(building, site)

    def add_site(self, site: Site, parent: Site | str | None = None) -> Site:
        """Attach an existing site at the top level or under ``parent`` and return it.

        Raises
        ------
        TypeError
            If a site of the subtree is not an instance of :attr:`site_cls`, or a building of
            :attr:`building_cls`.
        DuplicateIdError
            If an id of the site subtree is already used in the park.
        UnknownIdError
            If ``parent`` is an unknown id.
        """
        self._check_classes(site)
        if parent is None:
            self._register(site)
            site._container = self
            self.sites.append(site)
            return site
        return self._resolve_site(parent).add_site(site)

    def add_building(self, building: Building, site: Site | str | None = None) -> Building:
        """Attach an existing building to the park or to ``site`` and return it.

        Raises
        ------
        TypeError
            If ``building`` is not an instance of :attr:`building_cls`.
        DuplicateIdError
            If its id, RNB id or one of its system ids is already used in the park.
        UnknownIdError
            If ``site`` is an unknown id.
        """
        self._check_classes(building)
        if site is None:
            self._register(building)
            building._container = self
            self.buildings.append(building)
            return building
        return self._resolve_site(site).add_building(building)

    def add_system(self, system: System, owner: Site | Building | str) -> System:
        """Add ``system`` to ``owner`` (a site or a building, object or id) and return it.

        Raises
        ------
        UnknownIdError
            If ``owner`` is unknown, or ``serves`` names a building outside the owner site.
        ValueError
            If ``serves`` is set on a system owned by a building.
        DuplicateIdError
            If the system id is already used in the park.
        """
        entity: DescribedEntity = self._resolve(owner, (Site, Building))
        return entity.add_system(system)

    def add_meter(self, meter: Meter) -> Meter:
        """Add ``meter`` to the park and return it.

        Raises
        ------
        UnknownIdError
            If an id of ``meter.scope`` is not a site, building or system of the park.
        DuplicateIdError
            If the meter id is already used in the park.
        """
        self._check_scope(meter.id, meter.scope)
        self._register(meter)
        meter._container = self
        self.meters.append(meter)
        return meter

    def _check_scope(self, meter_id: str, scope: Iterable[str]) -> None:
        """Check that every id of ``scope`` is a site, building or system of the park."""
        unknown = [i for i in scope if not isinstance(self._index.get(i), Site | Building | System)]
        if unknown:
            raise UnknownIdError(f"meter {meter_id!r}: unknown scope ids {unknown}")

    # --- time series --------------------------------------------------------------------------

    def timeseries(
        self,
        *,
        end_use: EndUse | str | Iterable[EndUse | str] | None = None,
        energy_type: EnergyType | str | Iterable[EnergyType | str] | None = None,
        meters: Iterable[Meter | str] | None = None,
        buildings: Iterable[Building | str] | None = None,
        sites: Iterable[Site | str] | None = None,
        load_data: bool = False,
        tz: str = "UTC",
    ) -> pd.DataFrame:
        """Assemble the data of several meters in one DataFrame, ready for ``tide.Plumber``.

        Parameters
        ----------
        end_use, energy_type
            Keep the meters with one of these end uses / energy types.
        meters
            Keep these meters (objects or ids).
        buildings, sites
            Keep the meters whose scope contains one of these entities or one of their own
            systems.
        load_data
            Call :meth:`Meter.load_data` on the selected meters that have no data in memory. Without
            it, such meters are skipped with a :class:`NoDataWarning`.
        tz
            Time zone of the result.

        Returns
        -------
        pandas.DataFrame
            Outer join of the meter data, in tide column format, without resampling.
        """
        selected = self._select_meters(end_use, energy_type, meters, buildings, sites)
        frames: list[pd.DataFrame] = []
        for meter in selected:
            if meter.data is None and load_data:
                meter.load_data()
            if meter.data is None:
                warnings.warn(f"meter {meter.id!r} has no data", NoDataWarning, stacklevel=2)
                continue
            frames.append(meter.data.tz_convert(tz))
        if not frames:
            return pd.DataFrame(index=pd.DatetimeIndex([], tz=tz))
        # column names start with the meter id, so they are unique across meters
        return pd.concat(frames, axis=1, join="outer").sort_index()

    def _select_meters(
        self,
        end_use: Any,
        energy_type: Any,
        meters: Iterable[Meter | str] | None,
        buildings: Iterable[Building | str] | None,
        sites: Iterable[Site | str] | None,
    ) -> list[Meter]:
        selected = list(self.meters)
        if meters is not None:
            wanted = {m if isinstance(m, str) else m.id for m in meters}
            selected = [m for m in selected if m.id in wanted]
        if end_use is not None:
            end_uses = _as_set(end_use, EndUse)
            selected = [m for m in selected if m.end_use in end_uses]
        if energy_type is not None:
            energies = _as_set(energy_type, EnergyType)
            selected = [m for m in selected if m.energy_type in energies]
        if buildings is not None or sites is not None:
            entities = [*(buildings or []), *(sites or [])]
            keep = {m.id for entity in entities for m in self.meters_for(entity)}
            selected = [m for m in selected if m.id in keep]
        return selected

    # --- move / remove ------------------------------------------------------------------------

    def move(
        self, entity_id: str, new_parent: Site | str | None, *, force: bool = False
    ) -> Site | Building:
        """Move a site or a building under ``new_parent`` (``None``: top level) and return it.

        Ids and provenance are kept; inherited values now come from the new parent.

        Parameters
        ----------
        entity_id
            Id of the site or building to move.
        new_parent
            New parent site (object or id), or ``None`` for the top level.
        force
            Move even if systems of the sites left behind serve moved buildings, removing those
            buildings from their ``serves``.

        Raises
        ------
        UnknownIdError
            If ``entity_id`` or ``new_parent`` is unknown.
        ValueError
            If a site would be moved inside itself.
        EntityInUseError
            If a system of a site left behind serves a moved building and ``force`` is False.
        """
        entity = self._resolve(entity_id, (Site, Building))
        target = None if new_parent is None else self._resolve_site(new_parent)
        if (
            target is not None
            and isinstance(entity, Site)
            and (target is entity or any(target is s for s in entity.iter_sites()))
        ):
            raise ValueError(f"cannot move {entity.id!r} inside itself")
        moved = {e.id for e in _walk(entity) if isinstance(e, Building)}
        stale = _systems_left_serving(entity, target, moved)
        if stale and not force:
            raise EntityInUseError(
                f"{entity.id!r} is served by {sorted(s.id for s in stale)} from a site it leaves; "
                "use force=True to drop these references"
            )
        for system in stale:
            system.serves = [i for i in system.serves or [] if i not in moved]
        self._take_out(entity)
        if target is None:
            entity._container = self
            (self.sites if isinstance(entity, Site) else self.buildings).append(entity)
        else:
            entity._container = target
            (target.sites if isinstance(entity, Site) else target.buildings).append(entity)
        return entity

    def remove(self, entity_id: str, *, force: bool = False) -> Entity:
        """Remove a site (with its content), a building or a system and return it.

        Parameters
        ----------
        entity_id
            Id of the entity to remove.
        force
            Remove even if other entities reference it, deleting those references.

        Raises
        ------
        UnknownIdError
            If ``entity_id`` is unknown.
        EntityInUseError
            If the entity is referenced (e.g. in the ``serves`` of a system) and ``force`` is False.
        """
        entity = self[entity_id]
        removed_ids = set(self._subtree_ids(entity))
        references = self.references_to(removed_ids)
        outside = [ref for ref in references if ref[0] not in removed_ids]
        if outside and not force:
            raise EntityInUseError(
                f"{entity_id!r} is referenced by {sorted({r[0] for r in outside})}"
            )
        for _, drop in outside:
            drop()
        if isinstance(entity, System):
            owner = entity._container
            assert isinstance(owner, DescribedEntity)
            return owner.remove_system(entity.id)
        if isinstance(entity, Meter):
            self.meters.remove(entity)
            self._unregister(entity)
            entity._container = None
            return entity
        self._take_out(entity)
        self._unregister(entity)
        entity._container = None
        return entity

    def references_to(self, ids: set[str]) -> list[tuple[str, Any]]:
        """Return the references to ``ids``, as ``(referencing_id, drop_callback)`` pairs."""
        references: list[tuple[str, Any]] = []
        for system in self.iter_systems():
            if system.serves and ids & set(system.serves):
                references.append((system.id, _dropper(system, "serves", ids)))
        for meter in self.meters:
            if ids & set(meter.scope):
                references.append((meter.id, _dropper(meter, "scope", ids)))
        return references

    # --- index maintenance (also called by the entities) --------------------------------------

    def _register(self, entity: Entity) -> None:
        """Index ``entity`` and its content; nothing is indexed if an id is already taken."""
        new = list(_walk(entity))
        ids = [e.id for e in new]
        taken = [i for i in ids if i in self._index] + [i for i in set(ids) if ids.count(i) > 1]
        if taken:
            raise DuplicateIdError(f"ids already used in the park: {sorted(set(taken))}")
        new_rnb = [e.rnb_id for e in new if isinstance(e, Building) and e.rnb_id]
        clashes = [r for r in new_rnb if r in self._rnb_index or new_rnb.count(r) > 1]
        if clashes:
            raise DuplicateIdError(f"RNB ids already used in the park: {sorted(set(clashes))}")
        for item in new:
            self._index[item.id] = item
            if isinstance(item, Building) and item.rnb_id:
                self._rnb_index[item.rnb_id] = item

    def _unregister(self, entity: Entity) -> None:
        for item in _walk(entity):
            self._index.pop(item.id, None)
            if isinstance(item, Building) and item.rnb_id:
                self._rnb_index.pop(item.rnb_id, None)

    def _change_rnb(self, building: Building, rnb_id: str | None) -> None:
        """Update the RNB index before ``building.rnb_id`` becomes ``rnb_id``."""
        other = None if rnb_id is None else self._rnb_index.get(rnb_id)
        if other is not None and other is not building:
            raise DuplicateIdError(f"RNB id {rnb_id!r} is already used by {other.id!r}")
        if building.rnb_id:
            self._rnb_index.pop(building.rnb_id, None)
        if rnb_id:
            self._rnb_index[rnb_id] = building

    # --- helpers ------------------------------------------------------------------------------

    def _take_out(self, entity: Site | Building) -> None:
        """Remove ``entity`` from its current collection without touching the index."""
        container = entity._container
        holder: Any = self if container is self else container
        collection = holder.sites if isinstance(entity, Site) else holder.buildings
        collection.remove(entity)

    def _subtree_ids(self, entity: Entity) -> Iterator[str]:
        return (e.id for e in _walk(entity))

    def _resolve(self, ref: Any, kinds: tuple[type, ...]) -> Any:
        entity = self[ref] if isinstance(ref, str) else ref
        if not isinstance(entity, kinds) or self._index.get(entity.id) is not entity:
            raise UnknownIdError(f"{getattr(entity, 'id', ref)!r} is not a {kinds} of this park")
        return entity

    def _resolve_site(self, ref: Site | str) -> Site:
        site: Site = self._resolve(ref, (Site,))
        return site

    def _check_classes(self, entity: Site | Building) -> None:
        """Check that the sites and buildings of the subtree are instances of the park classes."""
        for item in _walk(entity):
            if not isinstance(item, Site | Building):
                continue
            expected = self.site_cls if isinstance(item, Site) else self.building_cls
            if not isinstance(item, expected):
                raise TypeError(
                    f"{item.id!r}: expected an instance of {expected.__name__}, "
                    f"got {type(item).__name__}"
                )


def _walk(entity: Entity) -> Iterator[Entity]:
    """Yield ``entity`` and every indexed entity it contains."""
    yield entity
    if isinstance(entity, DescribedEntity):
        yield from entity.iter_systems()
    if isinstance(entity, Site):
        for child in [*entity.sites, *entity.buildings]:
            yield from _walk(child)


def _dropper(entity: System | Meter, field: str, ids: set[str]) -> Any:
    def drop() -> None:
        setattr(entity, field, [i for i in getattr(entity, field) or [] if i not in ids])

    return drop


def _systems_left_serving(
    entity: Site | Building, target: Site | None, moved: set[str]
) -> list[System]:
    """Return the systems of the sites ``entity`` leaves that serve a ``moved`` building."""
    kept = [] if target is None else [target, *target._ancestor_sites()]
    stale: list[System] = []
    for site in entity._ancestor_sites():
        if any(site is k for k in kept):
            continue  # the moved buildings stay inside this site
        stale.extend(s for s in site.iter_systems() if s.serves and moved & set(s.serves))
    return stale


def _class_or(value: Any, base: type) -> type:
    """Return ``value`` if it is a subclass of ``base``, else ``base`` (the field validates it)."""
    return value if isinstance(value, type) and issubclass(value, base) else base


def _as_set(value: Any, enum: type) -> set[Any]:
    values = [value] if isinstance(value, str) else list(value)
    return {enum(v) for v in values}
