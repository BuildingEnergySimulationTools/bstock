"""Attributes and behaviour shared by sites and buildings (SPEC §3.5, §3.6)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import Field, field_validator

from bstock._ids import check_external_id, check_id
from bstock.enums import BuildingUsage
from bstock.envelope import Envelope
from bstock.exceptions import DuplicateIdError, UnknownIdError
from bstock.provenance import SourcedModel, sourced
from bstock.systems import (
    SYSTEM_CLASSES,
    CoolingGenerator,
    DHWGenerator,
    HeatingGenerator,
    SolarPV,
    System,
    Ventilation,
)

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
    heating: list[HeatingGenerator] = Field(default_factory=list)
    cooling: list[CoolingGenerator] = Field(default_factory=list)
    dhw: list[DHWGenerator] = Field(default_factory=list)
    ventilation: list[Ventilation] = Field(default_factory=list)
    pv: list[SolarPV] = Field(default_factory=list)

    name = sourced(str)
    address = sourced(str, inherit=True)
    municipality = sourced(str, inherit=True)
    insee_code = sourced(str, inherit=True, description="INSEE code of the municipality")
    lat = sourced(float, unit="°", ge=-90, le=90, inherit=True, description="Latitude (WGS84)")
    lon = sourced(float, unit="°", ge=-180, le=180, inherit=True, description="Longitude (WGS84)")
    usage = sourced(BuildingUsage, inherit=True, description="Main usage")
    sub_usages = sourced(list[str], inherit=True)
    year_construction = sourced(int, ge=1000, le=2100, inherit=True)
    nb_levels = sourced(int, ge=1, inherit=True, description="Number of levels, ground floor = 1")
    height = sourced(float, unit="m", ge=0, inherit=True)
    nb_dwellings = sourced(int, ge=0, description="Number of dwellings")
    footprint_area = sourced(float, unit="m²", ge=0, description="Footprint area")
    floor_area = sourced(float, unit="m²", ge=0, description="Gross floor area")
    heated_area = sourced(float, unit="m²", ge=0, description="Heated area")

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return check_id(value)

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

    # --- hierarchy ----------------------------------------------------------------------------

    @property
    def parent_site(self) -> Any:
        """The site containing this entity, or ``None``."""
        from bstock.site import Site  # circular import

        return self._container if isinstance(self._container, Site) else None

    @property
    def park(self) -> Any:
        """The park this entity belongs to, or ``None``."""
        node = self._container
        while isinstance(node, SourcedModel):
            node = node._container
        return node

    def inheritance_parent(self) -> SourcedModel | None:
        """Inherited values come from the parent site."""
        return self.parent_site

    def _ancestor_sites(self) -> Iterator[Any]:
        site = self.parent_site
        while site is not None:
            yield site
            site = site.parent_site

    # --- systems ------------------------------------------------------------------------------

    def iter_systems(self, kind: str | None = None) -> Iterator[System]:
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
        """
        _check_kind(system.kind)
        if any(s.id == system.id for s in self.iter_systems()):
            raise DuplicateIdError(f"system id {system.id!r} is already used in {self.id!r}")
        self._notify_park("_register", system)
        system._container = self
        getattr(self, system.kind).append(system)
        return system

    def remove_system(self, system_id: str) -> System:
        """Remove the owned system ``system_id`` and return it.

        Raises
        ------
        UnknownIdError
            If this entity owns no system with this id.
        """
        for system in self.iter_systems():
            if system.id == system_id:
                self._notify_park("_unregister", system)
                getattr(self, system.kind).remove(system)
                system._container = None
                return system
        raise UnknownIdError(system_id)

    def _notify_park(self, method: str, entity: SourcedModel) -> None:
        """Let the park maintain its id index (no-op for an entity outside a park)."""
        park = self.park
        if park is not None and hasattr(park, method):
            getattr(park, method)(entity)


def _check_kind(kind: str) -> str:
    if kind not in SYSTEM_CLASSES:
        raise ValueError(f"unknown system kind {kind!r}; expected one of {list(SYSTEM_CLASSES)}")
    return kind
