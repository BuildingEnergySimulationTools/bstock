"""BuildingPark: root of the data model (SPEC §3.4, §5)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from bstock.building import Building
from bstock.description import DescribedEntity
from bstock.exceptions import DuplicateIdError, EntityInUseError, UnknownIdError
from bstock.provenance import SourcedModel, SourcePriority
from bstock.site import Site
from bstock.systems import System

__all__ = ["BuildingPark"]

Entity = Site | Building | System


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
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str
    description: str | None = None
    source_priority: SourcePriority = Field(default_factory=SourcePriority)
    sites: list[Site] = Field(default_factory=list)
    buildings: list[Building] = Field(default_factory=list)

    _site_cls: type[Site] = PrivateAttr(default=Site)
    _building_cls: type[Building] = PrivateAttr(default=Building)
    _index: dict[str, Entity] = PrivateAttr(default_factory=dict)
    _rnb_index: dict[str, Building] = PrivateAttr(default_factory=dict)

    def __init__(
        self,
        name: str,
        *,
        site_cls: type[Site] = Site,
        building_cls: type[Building] = Building,
        **data: Any,
    ) -> None:
        super().__init__(name=name, **data)
        self._set_classes(site_cls, building_cls)

    def _set_classes(self, site_cls: type[Site], building_cls: type[Building]) -> None:
        if not (isinstance(site_cls, type) and issubclass(site_cls, Site)):
            raise TypeError(f"site_cls must be a subclass of Site, got {site_cls!r}")
        if not (isinstance(building_cls, type) and issubclass(building_cls, Building)):
            raise TypeError(f"building_cls must be a subclass of Building, got {building_cls!r}")
        self._site_cls, self._building_cls = site_cls, building_cls

    def model_post_init(self, context: Any) -> None:
        """Attach the top-level entities and build the id index."""
        for entity in [*self.sites, *self.buildings]:
            self._register(entity)
            entity._container = self

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BaseModel):
            return NotImplemented
        return type(self) is type(other) and self.__dict__ == other.__dict__

    __hash__ = None  # type: ignore[assignment]

    @property
    def site_cls(self) -> type[Site]:
        """Class used to create sites."""
        return self._site_cls

    @property
    def building_cls(self) -> type[Building]:
        """Class used to create buildings."""
        return self._building_cls

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

    def iter_systems(self, kind: str | None = None) -> Iterator[System]:
        """Iterate over every system of the park, optionally of one ``kind``."""
        for entity in [*self.iter_sites(), *self.iter_buildings()]:
            yield from entity.iter_systems(kind)

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
        site = self._site_cls(id=id, source=source, **attrs)
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
        building = self._building_cls(rnb_id=rnb_id, id=id, source=source, **attrs)
        return self.add_building(building, site)

    def add_site(self, site: Site, parent: Site | str | None = None) -> Site:
        """Attach an existing site at the top level or under ``parent`` and return it.

        Raises
        ------
        TypeError
            If ``site`` is not an instance of :attr:`site_cls`.
        DuplicateIdError
            If an id of the site subtree is already used in the park.
        UnknownIdError
            If ``parent`` is an unknown id.
        """
        self._check_class(site, self._site_cls)
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
        self._check_class(building, self._building_cls)
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
        entity = self._resolve(owner, (Site, Building))
        if system.serves is not None:
            if not isinstance(entity, Site):
                raise ValueError("'serves' only applies to systems owned by a site")
            served = {b.id for b in entity.iter_buildings()}
            unknown = [i for i in system.serves if i not in served]
            if unknown:
                raise UnknownIdError(f"buildings {unknown} are not in site {entity.id!r}")
        return entity.add_system(system)

    # --- move / remove ------------------------------------------------------------------------

    def move(self, entity_id: str, new_parent: Site | str | None) -> Site | Building:
        """Move a site or a building under ``new_parent`` (``None``: top level) and return it.

        Ids and provenance are kept; inherited values now come from the new parent.

        Raises
        ------
        UnknownIdError
            If ``entity_id`` or ``new_parent`` is unknown.
        ValueError
            If a site would be moved inside itself.
        """
        entity = self._resolve(entity_id, (Site, Building))
        target = None if new_parent is None else self._resolve_site(new_parent)
        if (
            target is not None
            and isinstance(entity, Site)
            and (target is entity or any(target is s for s in entity.iter_sites()))
        ):
            raise ValueError(f"cannot move {entity.id!r} inside itself")
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
        self._take_out(entity)
        self._unregister(entity)
        entity._container = None
        return entity

    def references_to(self, ids: set[str]) -> list[tuple[str, Any]]:
        """Return the references to ``ids``, as ``(referencing_id, drop_callback)`` pairs."""
        references: list[tuple[str, Any]] = []
        for system in self.iter_systems():
            if system.serves and ids & set(system.serves):
                references.append((system.id, _dropper(system, ids)))
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

    @staticmethod
    def _check_class(entity: SourcedModel, expected: type) -> None:
        if not isinstance(entity, expected):
            raise TypeError(
                f"expected an instance of {expected.__name__}, got {type(entity).__name__}"
            )


def _walk(entity: Entity) -> Iterator[Entity]:
    """Yield ``entity`` and every indexed entity it contains."""
    yield entity
    if isinstance(entity, DescribedEntity):
        yield from entity.iter_systems()
    if isinstance(entity, Site):
        for child in [*entity.sites, *entity.buildings]:
            yield from _walk(child)


def _dropper(system: System, ids: set[str]) -> Any:
    def drop() -> None:
        system.serves = [i for i in system.serves or [] if i not in ids]

    return drop
