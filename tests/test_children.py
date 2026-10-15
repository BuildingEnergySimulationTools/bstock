import copy
import pickle

import pytest

from bstock import (
    Building,
    BuildingPark,
    Channel,
    EnvelopeComponent,
    HeatingGenerator,
    HierarchyError,
    Meter,
    Site,
)


@pytest.fixture
def park():
    park = BuildingPark(name="Park")
    site = park.new_site("s1", usage="tertiary")
    building = park.new_building(id="b1", parent=site)
    building.add_system(HeatingGenerator(id="h1"))
    building.envelope.add_component(EnvelopeComponent(id="w1", kind="wall"))
    park.add_meter(Meter(id="m1", scope=["b1"], channels=[Channel(name="energy", unit="kWh")]))
    return park


OWNERS = {
    "park": lambda park: park,
    "site": lambda park: park["s1"],
    "building": lambda park: park["b1"],
    "envelope": lambda park: park["b1"].envelope,
}
CHILD_LISTS = [
    ("park", "sites"),
    ("park", "buildings"),
    ("park", "meters"),
    ("site", "sites"),
    ("site", "buildings"),
    ("site", "heating"),
    ("building", "heating"),
    ("building", "pv"),
    ("envelope", "components"),
]
MUTATIONS = {
    "append": lambda items: items.append(None),
    "extend": lambda items: items.extend([None]),
    "insert": lambda items: items.insert(0, None),
    "remove": lambda items: items.remove(None),
    "pop": lambda items: items.pop(),
    "clear": lambda items: items.clear(),
    "sort": lambda items: items.sort(),
    "reverse": lambda items: items.reverse(),
    "setitem": lambda items: items.__setitem__(0, None),
    "delitem": lambda items: items.__delitem__(0),
    "iadd": lambda items: items.__iadd__([None]),
    "imul": lambda items: items.__imul__(2),
}


@pytest.mark.parametrize(("owner", "field"), CHILD_LISTS)
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_child_list_refuses_in_place_change(park, owner, field, mutation):
    items = getattr(OWNERS[owner](park), field)
    before = list(items)
    with pytest.raises(HierarchyError, match=f"'{field}' cannot be modified in place; use "):
        MUTATIONS[mutation](items)
    assert items == before


@pytest.mark.parametrize(("owner", "field"), CHILD_LISTS)
def test_child_list_refuses_assignment(park, owner, field):
    entity = OWNERS[owner](park)
    with pytest.raises(HierarchyError, match=f"'{field}' cannot be replaced; use "):
        setattr(entity, field, [])


def test_appended_building_does_not_escape_the_index(park):
    # the bug: a building appended to site.buildings had no parent and was missing from the index,
    # but park.select() found it
    with pytest.raises(HierarchyError, match=r"site\.add_building"):
        park["s1"].buildings.append(Building(id="b2"))
    assert park.get("b2") is None
    assert park.select().ids == ("b1",)


def test_child_list_reads_as_a_list(park):
    site = park["s1"]
    assert isinstance(site.buildings, list)
    assert site.buildings == [park["b1"]]
    assert site.buildings[0].id == "b1"
    assert len(site.buildings) == 1


def test_methods_still_change_child_lists(park):
    site = park.new_site("s2")
    park.move("b1", site)
    park.remove("m1")
    park["b1"].remove_system("h1")
    park["b1"].envelope.remove_component("w1")
    park["s1"].add_building(Building(id="b2"))
    assert [b.id for b in site.buildings] == ["b1"]
    assert [b.id for b in park["s1"].buildings] == ["b2"]
    assert park.meters == []
    assert park["b1"].heating == []
    assert park["b1"].envelope.components == []


@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(copy.deepcopy, id="deepcopy"),
        pytest.param(lambda park: park.model_copy(deep=True), id="model_copy"),
        pytest.param(lambda park: pickle.loads(pickle.dumps(park)), id="pickle"),
    ],
)
def test_copied_child_list_stays_protected(park, duplicate):
    buildings = duplicate(park)["s1"].buildings
    assert [b.id for b in buildings] == ["b1"]
    with pytest.raises(HierarchyError):
        buildings.append(Building(id="b2"))


def test_loaded_park_child_lists_are_protected(park):
    loaded = BuildingPark.from_json(park.to_json())
    with pytest.raises(HierarchyError):
        loaded["s1"].buildings.append(Building(id="b2"))


def test_entity_outside_park_child_lists_are_protected():
    site = Site(id="s1", buildings=[Building(id="b1")])
    with pytest.raises(HierarchyError):
        site.buildings.append(Building(id="b2"))
