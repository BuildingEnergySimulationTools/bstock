import datetime

import pytest

from bstock.building import Building
from bstock.connectors import ImportReport
from bstock.connectors._sync import put, sync_components, sync_systems
from bstock.enums import ComponentKind, EnergyType
from bstock.envelope import EnvelopeComponent, Window
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.provenance import SourcedValue
from bstock.systems import HeatingGenerator

RNB_A = "A1B2C3D4E5F6"


def bdnb(value, **kwargs):
    return SourcedValue(value=value, source="bdnb", **kwargs)


@pytest.fixture
def park():
    park = BuildingPark(name="p")
    park.new_building(rnb_id=RNB_A)
    return park


@pytest.fixture
def report():
    return ImportReport(source="bdnb")


def test_put_keeps_provenance_and_replaces_source_candidate():
    building = Building(construction_year=1970, source="bdnb")
    building.construction_year = 1975
    date = datetime.date(2026, 2, 1)
    put(building, {"construction_year": bdnb(1972, date=date, ref="bdnb-bg-x")}, source="bdnb")
    [manual, from_bdnb] = building.candidates("construction_year")
    assert manual.source == "manual"
    assert from_bdnb == bdnb(1972, date=date, ref="bdnb-bg-x")


def test_put_with_clear_removes_values_no_longer_given():
    building = Building(height=12.0, level_count=4, source="bdnb")
    building.set("height", 13.0, source="audit")
    put(building, {"level_count": bdnb(5)}, source="bdnb", clear=True)
    assert building.height == 13.0
    assert [c.source for c in building.candidates("height")] == ["audit"]
    assert building.level_count == 5


def test_put_rejects_candidate_of_another_source():
    with pytest.raises(ValueError, match="dpe"):
        put(Building(), {"height": SourcedValue(value=1.0, source="dpe")}, source="bdnb")


def test_systems_get_deterministic_ids(park, report):
    building = park[RNB_A]
    produced = [{"energy_type": bdnb(EnergyType.NATURAL_GAS)}, {"energy_type": bdnb("electricity")}]
    systems = sync_systems(building, "heating", "bdnb", produced, report)
    assert [s.id for s in systems] == [f"heating-{RNB_A}-bdnb-1", f"heating-{RNB_A}-bdnb-2"]
    assert all(isinstance(s, HeatingGenerator) for s in systems)
    assert park[f"heating-{RNB_A}-bdnb-2"] is systems[1]


def test_systems_are_updated_not_duplicated(park, report):
    building = park[RNB_A]
    sync_systems(building, "heating", "bdnb", [{"energy_type": bdnb("natural_gas")}], report)
    first = building.heating[0]
    sync_systems(building, "heating", "bdnb", [{"energy_type": bdnb("electricity")}], report)
    assert building.heating == [first]
    assert first.energy_type == EnergyType.ELECTRICITY


def test_systems_no_longer_given_are_removed(park, report):
    building = park[RNB_A]
    two = [{"energy_type": bdnb("natural_gas")}, {"energy_type": bdnb("electricity")}]
    sync_systems(building, "heating", "bdnb", two, report)
    sync_systems(building, "heating", "bdnb", two[:1], report)
    assert [s.id for s in building.heating] == [f"heating-{RNB_A}-bdnb-1"]
    assert f"heating-{RNB_A}-bdnb-2" not in park


def test_referenced_system_is_kept_and_reported(park, report):
    building = park[RNB_A]
    two = [{"energy_type": bdnb("natural_gas")}, {"energy_type": bdnb("electricity")}]
    sync_systems(building, "heating", "bdnb", two, report)
    stale = f"heating-{RNB_A}-bdnb-2"
    park.add_meter(Meter(id="m", scope=[stale], channels=[Channel(name="energy", unit="kWh")]))
    sync_systems(building, "heating", "bdnb", two[:1], report)
    assert stale in park
    assert "['m']" in report.messages[RNB_A][0]


def test_other_systems_are_left_untouched(park, report):
    building = park[RNB_A]
    manual = building.add_system(HeatingGenerator(id="chaudiere"))
    other = building.add_system(HeatingGenerator(id=f"heating-{RNB_A}-dpe-1"))
    sync_systems(building, "heating", "bdnb", [], report)
    assert building.heating == [manual, other]


def test_components_get_per_kind_ids_and_window_class():
    envelope = Building().envelope
    produced = {
        ComponentKind.WALL: {"heat_loss_coefficient": SourcedValue(value=120.0, source="dpe")},
        ComponentKind.WINDOW: {"heat_loss_coefficient": SourcedValue(value=80.0, source="dpe")},
    }
    components = sync_components(envelope, "dpe", produced)
    assert [c.id for c in components] == ["dpe-wall", "dpe-window"]
    assert isinstance(components[1], Window)
    assert components[0].kind == ComponentKind.WALL


def test_components_are_updated_and_stale_ones_removed():
    envelope = Building().envelope
    manual = envelope.add_component(EnvelopeComponent(id="mur-nord", kind="wall"))
    wall = {"heat_loss_coefficient": SourcedValue(value=120.0, source="dpe")}
    door = {"heat_loss_coefficient": SourcedValue(value=5.0, source="dpe")}
    sync_components(envelope, "dpe", {ComponentKind.WALL: wall, ComponentKind.DOOR: door})
    new_wall = {"heat_loss_coefficient": SourcedValue(value=100.0, source="dpe")}
    sync_components(envelope, "dpe", {ComponentKind.WALL: new_wall})
    assert [c.id for c in envelope.components] == ["mur-nord", "dpe-wall"]
    assert envelope.components[0] is manual
    assert envelope.components[1].heat_loss_coefficient == 100.0
