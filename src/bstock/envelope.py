"""Building envelope: summary values and optional components (SPEC §3.7)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, Tag, field_validator

from bstock._ids import check_id, new_id
from bstock.enums import (
    Adjacency,
    ComponentKind,
    FrameType,
    GlazingType,
    Inertia,
    InsulationLevel,
    Orientation,
)
from bstock.exceptions import DuplicateIdError, IncompleteDataError, UnknownIdError
from bstock.provenance import SourcedModel, SourcedValue, sourced

__all__ = ["COMPUTED_SOURCE", "Envelope", "EnvelopeComponent", "Window"]

COMPUTED_SOURCE = "computed"
"""Source of the values derived by the ``compute_*`` methods."""


class EnvelopeComponent(SourcedModel):
    """One envelope component: a wall, roof, floor, door or thermal bridge, alone or grouped.

    For a ``THERMAL_BRIDGE``, ``area`` holds a length in m and ``u_value`` a linear transmittance
    ψ in W/(m.K). Windows use :class:`Window`.
    """

    id: str = Field(default_factory=lambda: new_id("component"), frozen=True)
    kind: ComponentKind

    area = sourced(float, unit="m²", ge=0, description="Area (length in m for a thermal bridge)")
    u_value = sourced(
        float, unit="W/(m².K)", ge=0, description="Thermal transmittance (ψ for a thermal bridge)"
    )
    heat_loss_coefficient = sourced(
        float, unit="W/K", ge=0, description="Transmission heat loss coefficient U·A (or ψ·L)"
    )
    orientation = sourced(Orientation, description="None means all orientations together")
    adjacency = sourced(Adjacency)
    insulation = sourced(InsulationLevel)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return check_id(value)

    @field_validator("kind")
    @classmethod
    def _windows_use_window_class(cls, kind: ComponentKind) -> ComponentKind:
        if kind is ComponentKind.WINDOW and cls is EnvelopeComponent:
            raise ValueError("use the Window class for windows")
        return kind

    def heat_loss(self) -> float | None:
        """Return the heat loss coefficient in W/K: the given one, else U·A, else ``None``."""
        if self.heat_loss_coefficient is not None:
            return self.heat_loss_coefficient
        if self.u_value is not None and self.area is not None:
            return self.u_value * self.area
        return None


class Window(EnvelopeComponent):
    """A window or a group of windows."""

    kind: Literal[ComponentKind.WINDOW] = ComponentKind.WINDOW

    shgc = sourced(float, ge=0, le=1, description="Solar heat gain coefficient")
    glazing = sourced(GlazingType)
    frame = sourced(FrameType)


def _component_tag(value: Any) -> str:
    kind = value.get("kind") if isinstance(value, dict) else getattr(value, "kind", None)
    return "window" if kind == ComponentKind.WINDOW else "component"


AnyComponent = Annotated[
    Annotated[Window, Tag("window")] | Annotated[EnvelopeComponent, Tag("component")],
    Discriminator(_component_tag),
]


class Envelope(SourcedModel):
    """Envelope of a building or of a site.

    The summary attributes are always usable; ``components`` optionally describes the envelope per
    component kind (as in the DPE open data) or in detail (as in the DPE XML or an audit). The two
    levels are not synchronised: the ``compute_*`` methods add ``"computed"`` candidates explicitly.
    """

    loss_area = sourced(float, unit="m²", ge=0, description="Heat loss area")
    heat_loss_coefficient = sourced(
        float, unit="W/K", ge=0, description="Transmission heat losses (DPE GV, no air renewal)"
    )
    u_bat = sourced(float, unit="W/(m².K)", ge=0, inherit=True, description="Mean transmittance")
    inertia = sourced(Inertia, inherit=True)
    window_shgc = sourced(
        float, ge=0, le=1, inherit=True, description="Mean solar heat gain coefficient of windows"
    )
    infiltration_q4pa = sourced(
        float, unit="m³/(h.m²)", ge=0, inherit=True, description="Air permeability under 4 Pa"
    )

    components: list[AnyComponent] = Field(default_factory=list)

    def model_post_init(self, context: Any) -> None:
        """Attach the components to this envelope and check their ids are unique."""
        seen: set[str] = set()
        for component in self.components:
            if component.id in seen:
                raise DuplicateIdError(f"component id {component.id!r} is used twice")
            seen.add(component.id)
            component._container = self

    def inheritance_parent(self) -> Envelope | None:
        """Return the envelope of the parent site of the owner, if any."""
        owner = self._container
        parent = owner.inheritance_parent() if isinstance(owner, SourcedModel) else None
        envelope = getattr(parent, "envelope", None)
        return envelope if isinstance(envelope, Envelope) else None

    # --- components -------------------------------------------------------------------------

    def add_component(self, component: EnvelopeComponent) -> EnvelopeComponent:
        """Add ``component`` to the envelope and return it.

        Raises
        ------
        DuplicateIdError
            If a component with the same id already exists.
        """
        if any(c.id == component.id for c in self.components):
            raise DuplicateIdError(f"component id {component.id!r} is already used")
        component._container = self
        self.components.append(component)
        return component

    def remove_component(self, component_id: str) -> EnvelopeComponent:
        """Remove the component ``component_id`` and return it.

        Raises
        ------
        UnknownIdError
            If no component has this id.
        """
        for index, component in enumerate(self.components):
            if component.id == component_id:
                component._container = None
                return self.components.pop(index)
        raise UnknownIdError(component_id)

    def components_by(
        self,
        kind: ComponentKind | str | None = None,
        orientation: Orientation | str | None = None,
    ) -> list[EnvelopeComponent]:
        """Return the components matching ``kind`` and ``orientation`` (``None`` matches all)."""
        return [
            c
            for c in self.components
            if (kind is None or c.kind == kind)
            and (orientation is None or c.orientation == orientation)
        ]

    def _opaque_and_windows(self) -> Iterator[EnvelopeComponent]:
        return (c for c in self.components if c.kind != ComponentKind.THERMAL_BRIDGE)

    # --- computations -----------------------------------------------------------------------

    def compute_heat_loss_coefficient(self) -> SourcedValue:
        """Set ``heat_loss_coefficient`` to the sum of the component heat losses, in W/K.

        Each component contributes its ``heat_loss_coefficient``, or U·A (ψ·L for a thermal bridge).

        Returns
        -------
        SourcedValue
            The ``"computed"`` candidate added.

        Raises
        ------
        IncompleteDataError
            If there is no component, or a component heat loss cannot be determined.
        """
        components = self._require_components()
        missing = [c.id for c in components if c.heat_loss() is None]
        if missing:
            raise IncompleteDataError(f"heat loss unknown for components {missing}")
        total = sum(c.heat_loss() or 0.0 for c in components)
        return self._set_computed("heat_loss_coefficient", total, len(components))

    def compute_loss_area(self) -> SourcedValue:
        """Set ``loss_area`` to the summed area of the components except thermal bridges, in m².

        Raises
        ------
        IncompleteDataError
            If there is no such component, or one of them has no area.
        """
        components = list(self._opaque_and_windows())
        if not components:
            raise IncompleteDataError("no component other than thermal bridges")
        missing = [c.id for c in components if c.area is None]
        if missing:
            raise IncompleteDataError(f"area unknown for components {missing}")
        total = sum(c.area or 0.0 for c in components)
        return self._set_computed("loss_area", total, len(components))

    def compute_u_bat(self) -> SourcedValue:
        """Set ``u_bat`` to the effective ``heat_loss_coefficient`` / ``loss_area``, in W/(m².K).

        Raises
        ------
        IncompleteDataError
            If either value is unknown or the loss area is zero.
        """
        heat_loss, area = self.heat_loss_coefficient, self.loss_area
        if heat_loss is None or not area:
            raise IncompleteDataError("u_bat needs heat_loss_coefficient and a non-zero loss_area")
        candidate = self.set(
            "u_bat",
            heat_loss / area,
            source=COMPUTED_SOURCE,
            comment="heat_loss_coefficient / loss_area",
        )
        assert candidate is not None
        return candidate

    def compute_window_shgc(self) -> SourcedValue:
        """Set ``window_shgc`` to the mean window SHGC weighted by window area.

        Raises
        ------
        IncompleteDataError
            If there is no window, or a window lacks its area or SHGC, or the total area is zero.
        """
        windows = [c for c in self.components if isinstance(c, Window)]
        if not windows:
            raise IncompleteDataError("no window component")
        missing = [w.id for w in windows if w.area is None or w.shgc is None]
        if missing:
            raise IncompleteDataError(f"area or shgc unknown for windows {missing}")
        total_area = sum(w.area or 0.0 for w in windows)
        if not total_area:
            raise IncompleteDataError("total window area is zero")
        mean = sum((w.area or 0.0) * (w.shgc or 0.0) for w in windows) / total_area
        return self._set_computed("window_shgc", mean, len(windows))

    def _require_components(self) -> list[EnvelopeComponent]:
        if not self.components:
            raise IncompleteDataError("the envelope has no component")
        return list(self.components)

    def _set_computed(self, name: str, value: float, count: int) -> SourcedValue:
        comment = f"from {count} components"
        candidate = self.set(name, value, source=COMPUTED_SOURCE, comment=comment)
        assert candidate is not None
        return candidate
