import json

import pytest
from pydantic import ValidationError

from bstock.enums import ComponentKind, Inertia, Orientation
from bstock.envelope import Envelope, EnvelopeComponent, Window
from bstock.exceptions import (
    DuplicateIdError,
    IncompleteDataError,
    InvalidIdError,
    UnknownIdError,
)
from bstock.provenance import SourcedModel, sourced


def wall(**kwargs):
    return EnvelopeComponent(kind="wall", **kwargs)


def window(**kwargs):
    return Window(**kwargs)


@pytest.fixture
def detailed():
    """Walls 100 m² at U=0.5, roof 50 m² at H=10 W/K, windows 20+10 m², bridge ψ·L = 5 W/K."""
    return Envelope(
        components=[
            wall(id="wall-n", area=60.0, u_value=0.5, orientation="n"),
            wall(id="wall-s", area=40.0, u_value=0.5, orientation="s"),
            EnvelopeComponent(id="roof", kind="roof", area=50.0, heat_loss_coefficient=10.0),
            window(id="win-s", area=20.0, u_value=2.0, shgc=0.6, orientation="s"),
            window(id="win-n", area=10.0, u_value=2.0, shgc=0.3, orientation="n"),
            EnvelopeComponent(id="bridges", kind="thermal_bridge", area=50.0, u_value=0.1),
        ]
    )


# --- components -------------------------------------------------------------------------------


def test_component_ids_are_generated():
    first, second = wall(), wall()
    assert first.id.startswith("component-")
    assert first.id != second.id


def test_component_id_cannot_contain_tide_separator():
    with pytest.raises((InvalidIdError, ValidationError)):
        wall(id="bad__id")


def test_window_kind_requires_window_class():
    with pytest.raises(ValidationError, match="Window"):
        EnvelopeComponent(kind="window")
    assert Window().kind is ComponentKind.WINDOW


def test_component_heat_loss_prefers_given_value():
    assert wall(area=10.0, u_value=0.5).resolved_heat_loss_coefficient() == 5.0
    assert (
        wall(area=10.0, u_value=0.5, heat_loss_coefficient=7.0).resolved_heat_loss_coefficient()
        == 7.0
    )
    assert wall(area=10.0).resolved_heat_loss_coefficient() is None


def test_units_are_declared():
    assert Envelope.u_bat.unit == "W/(m².K)"
    assert EnvelopeComponent.heat_loss_coefficient.unit == "W/K"
    assert Envelope.infiltration_q4pa.unit == "m³/(h.m²)"


def test_shgc_is_bounded():
    with pytest.raises(ValidationError):
        Window(shgc=1.5)


def test_add_and_remove_components():
    envelope = Envelope()
    added = envelope.add_component(wall(id="w1"))
    assert added._container is envelope
    with pytest.raises(DuplicateIdError):
        envelope.add_component(wall(id="w1"))
    removed = envelope.remove_component("w1")
    assert removed._container is None
    assert envelope.components == []
    with pytest.raises(UnknownIdError):
        envelope.remove_component("w1")


def test_remove_component_accepts_object():
    envelope = Envelope()
    added = envelope.add_component(wall(id="w1"))
    with pytest.raises(UnknownIdError, match="no envelope component 'w1'"):
        envelope.remove_component(wall(id="w1"))  # same id, other object
    assert envelope.remove_component(added) is added
    assert envelope.components == []


def test_duplicate_component_ids_rejected_at_construction():
    with pytest.raises(DuplicateIdError):
        Envelope(components=[wall(id="w"), wall(id="w")])


def test_find_components(detailed):
    assert [c.id for c in detailed.find_components(kind="window")] == ["win-s", "win-n"]
    assert [c.id for c in detailed.find_components(orientation=Orientation.S)] == [
        "wall-s",
        "win-s",
    ]
    assert [c.id for c in detailed.find_components(kind="wall", orientation="n")] == ["wall-n"]
    assert len(detailed.find_components()) == 6


def test_find_components_ignores_case(detailed):
    assert [c.id for c in detailed.find_components(kind="WALL", orientation="N")] == ["wall-n"]


# --- computations -----------------------------------------------------------------------------


def test_compute_heat_loss_coefficient(detailed):
    candidate = detailed.compute_heat_loss_coefficient()
    # walls 50 + roof 10 + windows 60 + bridges 5
    assert candidate.value == pytest.approx(125.0)
    assert candidate.source == "computed"
    assert detailed.heat_loss_coefficient == pytest.approx(125.0)


def test_compute_loss_area_excludes_thermal_bridges(detailed):
    assert detailed.compute_loss_area().value == pytest.approx(180.0)


def test_compute_u_bat(detailed):
    detailed.compute_heat_loss_coefficient()
    detailed.compute_loss_area()
    assert detailed.compute_u_bat().value == pytest.approx(125.0 / 180.0)


def test_compute_u_bat_uses_effective_values():
    envelope = Envelope(heat_loss_coefficient=200.0, loss_area=400.0, source="dpe")
    assert envelope.compute_u_bat().value == pytest.approx(0.5)


def test_compute_window_shgc_is_area_weighted(detailed):
    assert detailed.compute_window_shgc().value == pytest.approx((20 * 0.6 + 10 * 0.3) / 30)


@pytest.mark.parametrize(
    "method",
    [
        "compute_heat_loss_coefficient",
        "compute_loss_area",
        "compute_u_bat",
        "compute_window_shgc",
    ],
)
def test_computations_need_data(method):
    with pytest.raises(IncompleteDataError):
        getattr(Envelope(), method)()


def test_missing_component_value_is_reported():
    envelope = Envelope(components=[wall(id="w1", area=10.0), wall(id="w2", area=5.0, u_value=1)])
    with pytest.raises(IncompleteDataError, match="w1"):
        envelope.compute_heat_loss_coefficient()


def test_audit_value_beats_computed_one(detailed):
    detailed.set("u_bat", 0.9, source="audit")
    detailed.compute_heat_loss_coefficient()
    detailed.compute_loss_area()
    detailed.compute_u_bat()
    assert detailed.u_bat == 0.9


def test_computed_value_beats_dpe(detailed):
    detailed.set("u_bat", 2.0, source="dpe")
    detailed.compute_heat_loss_coefficient()
    detailed.compute_loss_area()
    detailed.compute_u_bat()
    assert detailed.effective("u_bat").source == "computed"


# --- inheritance ------------------------------------------------------------------------------


class Owner(SourcedModel):
    """Stands for a site or a building owning an envelope."""

    id: str
    name = sourced(str)
    envelope: Envelope = Envelope()

    def model_post_init(self, context):
        self.envelope._container = self

    def _inheritance_parent(self):
        return self._container if isinstance(self._container, Owner) else None


def test_envelope_inherits_from_parent_owner_envelope():
    site = Owner(id="site")
    building = Owner(id="building")
    building._container = site
    site.envelope.set("u_bat", 1.2, source="dpe")
    site.envelope.inertia = Inertia.HEAVY
    site.envelope.loss_area = 900.0
    assert building.envelope.u_bat == 1.2
    assert building.envelope.inertia is Inertia.HEAVY
    assert building.envelope.provider_id("u_bat") == "site"
    assert building.envelope.loss_area is None  # extensive


def test_envelope_without_owner_inherits_nothing():
    assert Envelope()._inheritance_parent() is None
    assert Envelope().u_bat is None


# --- serialisation ----------------------------------------------------------------------------


def test_json_round_trip_restores_component_classes(detailed):
    detailed.set("u_bat", 0.8, source="audit")
    data = json.loads(detailed.model_dump_json(exclude_none=True))
    loaded = Envelope.model_validate(data)
    assert loaded == detailed
    assert [type(c) for c in loaded.components] == [type(c) for c in detailed.components]
    assert all(c._container is loaded for c in loaded.components)
    assert loaded.find_components(kind="window")[0].shgc == 0.6
