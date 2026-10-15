"""Site: optional, nestable grouping of buildings (SPEC §3.5)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

from pydantic import Field, ValidationInfo, field_validator, model_validator

from bstock._children import ChildList, append_child, find_child, remove_child, wrap_children
from bstock._ids import check_external_id
from bstock.building import Building
from bstock.description import DescribedEntity
from bstock.display import REPR_MAX_LINES, TreeView, build_tree
from bstock.exceptions import DuplicateIdError, HierarchyError, UnknownIdError

__all__ = ["Site"]


class Site(DescribedEntity):
    """A client site (school group, campus...) or a BDNB building group.

    Sites nest freely. The descriptive values of a site are inherited by its buildings and
    sub-sites for intensive fields, and its systems may serve several buildings.

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
    sites: list[Site] = Field(default_factory=list, validate_default=True)
    buildings: list[Building] = Field(default_factory=list, validate_default=True)

    _child_hints: ClassVar[dict[str, str]] = {
        **DescribedEntity._child_hints,
        "sites": "site.add_site, site.remove_site, park.move or park.remove",
        "buildings": "site.add_building, site.remove_building, park.move or park.remove",
    }

    @model_validator(mode="before")
    @classmethod
    def _build_children_with_context_classes(cls, data: Any, info: ValidationInfo) -> Any:
        return build_children(data, info)

    @field_validator("sites", "buildings")
    @classmethod
    def _protect_children(cls, value: list[Any], info: ValidationInfo) -> ChildList[Any]:
        return wrap_children(cls, info.field_name or "", value)

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

    # --- display ------------------------------------------------------------------------------

    def tree(
        self,
        *,
        systems: bool = False,
        meters: bool = False,
        max_depth: int | None = None,
        fields: str | Sequence[str] | None = None,
    ) -> TreeView:
        """Return the tree of the sub-sites and buildings of the site.

        Parameters
        ----------
        systems
            Also show the systems owned by each entity.
        meters
            Also show, under each entity, the meters whose scope contains it or one of its systems
            (empty outside a park).
        max_depth
            Number of levels shown below the site (``None``: all); deeper content is counted on
            one line.
        fields
            Sourced fields whose effective value is shown on each line, with its unit.

        Returns
        -------
        TreeView
            The tree; ``str()`` gives the text and Jupyter shows it as HTML.

        Raises
        ------
        UnknownFieldError
            If a name of ``fields`` is not a sourced field of the sites or buildings.
        InvalidArgumentError
            If ``max_depth`` is negative.
        """
        return build_tree(self, systems=systems, meters=meters, max_depth=max_depth, fields=fields)

    def __repr__(self) -> str:
        return self.tree().truncated(REPR_MAX_LINES)

    def _repr_html_(self) -> str:
        """Return the full tree as foldable HTML, for Jupyter."""
        return self.tree()._repr_html_()

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
        append_child(self.sites, site)
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
        append_child(self.buildings, building)
        return building

    def remove_site(self, site: Site | str) -> Site:
        """Remove the direct sub-site ``site`` (object or id), with its content, and return it.

        In a park, the sub-site and its content leave the park, as with
        :meth:`BuildingPark.remove`.

        Raises
        ------
        UnknownIdError
            If ``site`` is not a direct sub-site.
        EntityInUseError
            If, in a park, an entity outside the sub-site references it or its content (``scope``
            of a meter, ``serves`` of a system); use ``park.remove(site, force=True)`` to drop the
            references.
        """
        child = find_child(self.sites, site, self.id, "sub-site")
        self._take_out(self.sites, child)
        return child

    def remove_building(self, building: Building | str) -> Building:
        """Remove the building ``building`` (object or id) directly attached to the site.

        In a park, the building and its systems leave the park, as with
        :meth:`BuildingPark.remove`.

        Raises
        ------
        UnknownIdError
            If ``building`` is not directly attached to the site.
        EntityInUseError
            If, in a park, an entity outside the building references it or its systems (``scope``
            of a meter, ``serves`` of a system); use ``park.remove(building, force=True)`` to drop
            the references.
        """
        child = find_child(self.buildings, building, self.id, "building")
        self._take_out(self.buildings, child)
        return child

    def _take_out(self, collection: list[Any], child: Site | Building) -> None:
        self._notify_park("_release", child)
        self._notify_park("_unregister", child)
        remove_child(collection, child)
        child._container = None

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
