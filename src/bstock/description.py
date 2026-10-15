"""Attributes and behaviour shared by sites and buildings (SPEC §3.5, §3.6)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field, ValidationInfo, field_validator

from bstock._children import (
    ChildList,
    append_child,
    guard_assignment,
    remove_child,
    wrap_children,
)
from bstock._geometry import Footprint, geometry_area
from bstock._ids import check_external_id, check_id
from bstock.enums import BuildingUsage, SystemKind
from bstock.envelope import Envelope
from bstock.exceptions import (
    DuplicateIdError,
    HierarchyError,
    IncompleteDataError,
    UnknownIdError,
    UnknownKindError,
)
from bstock.provenance import COMPUTED_SOURCE, SourcedModel, SourcedValue, sourced
from bstock.systems import (
    SYSTEM_CLASSES,
    CoolingGenerator,
    DHWGenerator,
    HeatingGenerator,
    SolarPV,
    System,
    Ventilation,
)

if TYPE_CHECKING:
    from bstock.site import Site

__all__ = ["DescribedEntity"]


class DescribedEntity(SourcedModel):
    """Base class of :class:`~bstock.site.Site` and :class:`~bstock.building.Building`.

    It carries the descriptive attributes common to both, an envelope and the systems owned by
    the entity. Intensive attributes (``inherit=True``) fall back on the parent site value;
    extensive ones (counts, areas) never do.
    """

    id: str = Field(frozen=True)
    dpe_ids: list[str] = Field(default_factory=list)
    envelope: Envelope = Field(default_factory=Envelope)
    heating: list[HeatingGenerator] = Field(default_factory=list, validate_default=True)
    cooling: list[CoolingGenerator] = Field(default_factory=list, validate_default=True)
    dhw: list[DHWGenerator] = Field(default_factory=list, validate_default=True)
    ventilation: list[Ventilation] = Field(default_factory=list, validate_default=True)
    pv: list[SolarPV] = Field(default_factory=list, validate_default=True)

    name = sourced(str)
    address = sourced(str, inherit=True)
    municipality = sourced(str, inherit=True)
    insee_code = sourced(str, inherit=True, description="INSEE code of the municipality")
    lat = sourced(float, unit="°", ge=-90, le=90, inherit=True, description="Latitude (WGS84)")
    lon = sourced(float, unit="°", ge=-180, le=180, inherit=True, description="Longitude (WGS84)")
    usage = sourced(BuildingUsage, inherit=True, description="Main usage")
    sub_usages = sourced(list[str], inherit=True)
    construction_year = sourced(int, ge=1000, le=2100, inherit=True)
    level_count = sourced(int, ge=1, inherit=True, description="Number of levels, ground floor = 1")
    height = sourced(float, unit="m", ge=0, inherit=True)
    dwelling_count = sourced(int, ge=0, extensive=True, description="Number of dwellings")
    footprint = sourced(
        Footprint,
        description="Footprint: GeoJSON Polygon or MultiPolygon, coordinates in WGS84 degrees",
    )
    footprint_area = sourced(float, unit="m²", ge=0, extensive=True, description="Footprint area")
    floor_area = sourced(float, unit="m²", ge=0, extensive=True, description="Gross floor area")
    heated_area = sourced(float, unit="m²", ge=0, extensive=True, description="Heated area")

    _SYSTEMS_HINT: ClassVar[str] = "add_system and remove_system (or park.add_system, park.remove)"
    _child_hints: ClassVar[dict[str, str]] = dict.fromkeys(
        ("heating", "cooling", "dhw", "ventilation", "pv"), _SYSTEMS_HINT
    )
    """Child lists changed only through methods, with the methods cited in the error message."""

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return check_id(value)

    @field_validator("heating", "cooling", "dhw", "ventilation", "pv")
    @classmethod
    def _protect_systems(cls, value: list[Any], info: ValidationInfo) -> ChildList[Any]:
        return wrap_children(cls, info.field_name or "", value)

    @field_validator("dpe_ids")
    @classmethod
    def _check_dpe_ids(cls, value: list[str]) -> list[str]:
        return [check_external_id("dpe", v) or v for v in value]

    def model_post_init(self, context: Any) -> None:
        """Attach the envelope and the owned systems to this entity."""
        self.envelope._container = self
        seen: set[str] = set()
        for system in self.iter_systems():
            if system.id in seen:
                raise DuplicateIdError(f"system id {system.id!r} is used twice in {self.id!r}")
            seen.add(system.id)
            system._container = self

    def __setattr__(self, name: str, value: Any) -> None:
        guard_assignment(self, name)
        if name != "envelope":
            super().__setattr__(name, value)
            return
        # attach the new envelope, so that its values inherit from the parent site
        owner = value._container if isinstance(value, Envelope) else None
        if owner is not None and owner is not self:
            raise HierarchyError(
                f"this envelope already belongs to {owner._entity_id()!r}; "
                "assign a copy (envelope.model_copy(deep=True))"
            )
        old = self.envelope
        super().__setattr__(name, value)
        if self.envelope is not old:
            old._container = None
            self.envelope._container = self

    # --- hierarchy ----------------------------------------------------------------------------

    @property
    def parent(self) -> Site | None:
        """The site directly containing this entity, or ``None`` (top level or outside a park)."""
        from bstock.site import Site  # circular import

        return self._container if isinstance(self._container, Site) else None

    @property
    def park(self) -> Any:
        """The park this entity belongs to, or ``None``."""
        node = self._container
        while isinstance(node, SourcedModel):
            node = node._container
        return node

    def _inheritance_parent(self) -> SourcedModel | None:
        """Inherited values come from the parent site."""
        return self.parent

    def _ancestor_sites(self) -> Iterator[Any]:
        site = self.parent
        while site is not None:
            yield site
            site = site.parent

    # --- footprint ----------------------------------------------------------------------------

    def compute_footprint_area(self) -> SourcedValue:
        """Add a ``"computed"`` candidate to ``footprint_area`` from the effective ``footprint``.

        The area, in m², is computed without dependency on a local equirectangular projection
        (SPEC §3.6): holes are subtracted and the polygons of a ``MultiPolygon`` added up. Like any
        computed value it is a snapshot: compute again after changing the footprint.

        Returns
        -------
        SourcedValue
            The ``"computed"`` candidate added, in m².

        Raises
        ------
        IncompleteDataError
            If the entity has no footprint.
        """
        footprint = self.footprint
        if footprint is None:
            raise IncompleteDataError(f"{self.id!r} has no footprint")
        candidate = self.set(
            "footprint_area",
            geometry_area(footprint),
            source=COMPUTED_SOURCE,
            comment="from footprint",
        )
        assert candidate is not None
        return candidate

    # --- systems ------------------------------------------------------------------------------

    def iter_systems(self, kind: SystemKind | str | None = None) -> Iterator[System]:
        """Iterate over the systems owned by this entity, optionally of one ``kind``."""
        kinds = SYSTEM_CLASSES if kind is None else [_check_kind(kind)]
        for name in kinds:
            yield from getattr(self, name)

    def add_system(self, system: System) -> System:
        """Add ``system`` to the list matching its kind and return it.

        Raises
        ------
        DuplicateIdError
            If the entity, or the park it belongs to, already uses the id.
        HierarchyError
            If ``serves`` is set on a system added to a building.
        UnknownIdError
            If ``serves`` names a building outside the site.
        """
        _check_kind(system.kind)
        self._check_served(system.serves)
        if any(s.id == system.id for s in self.iter_systems()):
            raise DuplicateIdError(f"system id {system.id!r} is already used in {self.id!r}")
        self._notify_park("_register", system)
        system._container = self
        append_child(getattr(self, system.kind), system)
        return system

    def remove_system(self, system_id: str) -> System:
        """Remove the owned system ``system_id`` and return it.

        Raises
        ------
        UnknownIdError
            If this entity owns no system with this id.
        EntityInUseError
            If the entity belongs to a park where a meter measures the system; use
            ``park.remove(system_id, force=True)`` to drop the reference.
        """
        for system in self.iter_systems():
            if system.id == system_id:
                self._notify_park("_release", system)
                self._notify_park("_unregister", system)
                remove_child(getattr(self, system.kind), system)
                system._container = None
                return system
        raise UnknownIdError(system_id)

    def _check_served(self, serves: list[str] | None) -> None:
        """Check the ``serves`` of a system owned by this entity; only sites accept one."""
        if serves is not None:
            raise HierarchyError(
                f"'serves' only applies to systems owned by a site, not {self.id!r}"
            )

    def find_meters(self) -> list[Any]:
        """Return the park meters whose scope contains this entity or one of its own systems.

        Same as ``park.meters_for(entity)``; ``park.meters`` is the list of every meter of the park.
        Outside a park, the list is empty.
        """
        park = self.park
        return [] if park is None else park.meters_for(self)

    def _notify_park(self, method: str, entity: SourcedModel) -> None:
        """Let the park check references and maintain its index (no-op outside a park)."""
        park = self.park
        if park is not None and hasattr(park, method):
            getattr(park, method)(entity)


def _check_kind(kind: SystemKind | str) -> SystemKind:
    try:
        return SystemKind(kind)
    except ValueError:
        expected = [str(k) for k in SystemKind]
        raise UnknownKindError(
            f"unknown system kind {kind!r}; expected one of {expected}"
        ) from None
