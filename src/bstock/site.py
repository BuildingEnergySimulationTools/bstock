"""Site: optional, nestable grouping of buildings (SPEC §3.5)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from bstock._ids import check_external_id
from bstock.building import Building
from bstock.description import DescribedEntity
from bstock.exceptions import DuplicateIdError, HierarchyError, UnknownIdError

__all__ = ["Site"]


class Site(DescribedEntity):
    """A client site (school group, campus...) or a BDNB building group.

    Sites nest freely. The descriptive values of a site are inherited by its buildings and
    sub-sites for intensive attributes, and its systems may serve several buildings.

    Parameters
    ----------
    id
        Unique id: a client code, or the ``batiment_groupe_id`` for a BDNB group.
    bdnb_batiment_groupe_id
        Set when the site stands for a BDNB group.
    sites
        Sub-sites.
    buildings
        Buildings directly attached to the site.
    """

    bdnb_batiment_groupe_id: str | None = None
    sites: list[Site] = Field(default_factory=list)
    buildings: list[Building] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _build_children_with_context_classes(cls, data: Any, info: ValidationInfo) -> Any:
        return build_children(data, info)

    @field_validator("bdnb_batiment_groupe_id")
    @classmethod
    def _check_bdnb_group_id(cls, value: str | None) -> str | None:
        return check_external_id("bdnb_batiment_groupe", value)

    def model_post_init(self, context: Any) -> None:
        """Attach envelope, systems, sub-sites and buildings to this site."""
        super().model_post_init(context)
        seen: set[str] = set()
        for child in [*self.sites, *self.buildings]:
            if child.id in seen:
                raise DuplicateIdError(f"id {child.id!r} is used twice in site {self.id!r}")
            seen.add(child.id)
            child._container = self

    # --- navigation ---------------------------------------------------------------------------

    def iter_sites(self, *, recursive: bool = True) -> Iterator[Site]:
        """Iterate over the sub-sites, depth first when ``recursive``."""
        for site in self.sites:
            yield site
            if recursive:
                yield from site.iter_sites(recursive=True)

    def iter_buildings(self, *, recursive: bool = True) -> Iterator[Building]:
        """Iterate over the buildings of the site, and of its sub-sites when ``recursive``."""
        yield from self.buildings
        if recursive:
            for site in self.sites:
                yield from site.iter_buildings(recursive=True)

    # --- children -----------------------------------------------------------------------------

    def add_site(self, site: Site) -> Site:
        """Attach ``site`` as a sub-site and return it.

        Raises
        ------
        DuplicateIdError
            If the id is already used by a direct child or in the park.
        ProjectClassError
            If the site belongs to a park and a site or building of the added subtree is not an
            instance of the park ``site_cls`` or ``building_cls``.
        """
        self._check_new_child(site)
        self._notify_park("_register", site)
        site._container = self
        self.sites.append(site)
        return site

    def add_building(self, building: Building) -> Building:
        """Attach ``building`` to the site and return it.

        Raises
        ------
        DuplicateIdError
            If the id is already used by a direct child or in the park.
        ProjectClassError
            If the site belongs to a park and a site or building of the added subtree is not an
            instance of the park ``site_cls`` or ``building_cls``.
        """
        self._check_new_child(building)
        self._notify_park("_register", building)
        building._container = self
        self.buildings.append(building)
        return building

    def detach(self, child_id: str) -> Site | Building:
        """Detach the direct child (sub-site or building) ``child_id`` and return it.

        Raises
        ------
        UnknownIdError
            If no direct child has this id.
        """
        for collection in (self.sites, self.buildings):
            for child in collection:
                if child.id == child_id:
                    self._notify_park("_unregister", child)
                    collection.remove(child)
                    child._container = None
                    return child
        raise UnknownIdError(child_id)

    def _check_new_child(self, child: Site | Building) -> None:
        if child is self or any(child is s for s in self._ancestor_sites()):
            raise HierarchyError(f"cannot attach {child.id!r} inside itself")
        if any(c.id == child.id for c in [*self.sites, *self.buildings]):
            raise DuplicateIdError(f"id {child.id!r} is already used in site {self.id!r}")
        park = self.park
        if park is not None:
            park._check_classes(child)

    def _check_served(self, serves: list[str] | None) -> None:
        """Check that ``serves`` names buildings of the site or of its sub-sites."""
        if serves is None:
            return
        served = {b.id for b in self.iter_buildings()}
        unknown = [i for i in serves if i not in served]
        if unknown:
            raise UnknownIdError(f"buildings {unknown} are not in site {self.id!r}")


CLASSES_CONTEXT_KEY = "bstock_classes"
"""Validation context key holding the ``(site_cls, building_cls)`` used for nested entities."""


def build_children(
    data: Any,
    info: ValidationInfo,
    default: tuple[type[Site], type[Building]] | None = None,
) -> Any:
    """Instantiate the ``sites`` and ``buildings`` of ``data`` with the context classes, if any.

    Used when loading a park, so that nested entities are project subclasses (SPEC §5). Without
    classes in the validation context, ``default`` is used.
    """
    classes = (info.context or {}).get(CLASSES_CONTEXT_KEY) or default
    if classes is None or not isinstance(data, dict):
        return data
    site_cls, building_cls = classes
    context = {**(info.context or {}), CLASSES_CONTEXT_KEY: classes}
    data = dict(data)
    for key, cls in (("sites", site_cls), ("buildings", building_cls)):
        if key in data:
            data[key] = [
                cls.model_validate(item, context=context) if isinstance(item, dict) else item
                for item in data[key]
            ]
    return data
