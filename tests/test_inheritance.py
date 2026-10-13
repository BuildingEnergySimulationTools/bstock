"""Value inheritance along a hierarchy (SPEC §4.4), tested on stand-in classes.

``Node`` plays the role of sites and buildings, ``Part`` the role of envelopes: a part inherits from
the part of its owner's parent.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from bstock.exceptions import HierarchyError, UnknownFieldError
from bstock.provenance import SourcedModel, SourcedValue, SourcePriority, sourced


class Part(SourcedModel):
    u_value = sourced(float, inherit=True)
    area = sourced(float)

    def _inheritance_parent(self):
        owner_parent = self._container._inheritance_parent() if self._container else None
        return owner_parent.part if owner_parent is not None else None


class Node(SourcedModel):
    id: str
    year = sourced(int, inherit=True)
    usage = sourced(str, inherit=True)
    dwellings = sourced(int)  # extensive: never inherited
    part: Part = Part()

    def model_post_init(self, context):
        self.part._container = self

    def _inheritance_parent(self):
        return self._container if isinstance(self._container, Node) else None


class LeafNode(Node):
    """A subclass with an extra inherited field the parents do not declare."""

    code = sourced(str, inherit=True)


class Park(BaseModel):
    source_priority: SourcePriority = SourcePriority()


def attach(child, parent):
    child._container = parent
    return child


@pytest.fixture
def tree():
    """park > client site > BDNB group site > building."""
    park = Park()
    client = attach(Node(id="client-site"), park)
    group = attach(Node(id="bdnb-bg-1111"), client)
    building = attach(LeafNode(id="A1B2C3D4E5F6"), group)
    return client, group, building


def test_value_inherited_from_parent(tree):
    _, group, building = tree
    group.set("year", 1972, source="bdnb")
    assert building.year == 1972
    assert building.provider_id("year") == "bdnb-bg-1111"


def test_value_inherited_across_several_levels(tree):
    client, _, building = tree
    client.usage = "school"
    assert building.usage == "school"
    assert building.provider_id("usage") == "client-site"


def test_nearest_level_wins_whatever_the_source(tree):
    _, group, building = tree
    group.set("year", 1972, source="audit")
    building.set("year", 1970, source="assumption")
    assert building.year == 1970
    assert building.provider_id("year") == "A1B2C3D4E5F6"


def test_nearest_ancestor_with_value_wins(tree):
    client, group, building = tree
    client.set("year", 1950, source="manual")
    group.set("year", 1972, source="bdnb")
    assert building.year == 1972


def test_own_value_masks_inheritance_and_unset_restores_it(tree):
    _, group, building = tree
    group.set("year", 1972, source="bdnb")
    building.set("year", 1985, source="audit")
    assert building.year == 1985
    building.unset("year")
    assert building.year == 1972


def test_extensive_value_is_not_inherited(tree):
    _, group, building = tree
    group.dwellings = 48
    assert building.dwellings is None
    assert building.provider_id("dwellings") is None


def test_own_sources_exclude_inherited_ones(tree):
    _, group, building = tree
    group.set("year", 1972, source="bdnb")
    assert building.candidates("year") == []


def test_inherited_sources_list_every_level_nearest_first(tree):
    client, group, building = tree
    building.set("year", 1985, source="audit")
    group.set("year", 1972, source="bdnb")
    client.set("year", 1950, source="manual")
    assert building.candidates("year", inherited=True) == [
        ("A1B2C3D4E5F6", SourcedValue(value=1985, source="audit")),
        ("bdnb-bg-1111", SourcedValue(value=1972, source="bdnb")),
        ("client-site", SourcedValue(value=1950, source="manual")),
    ]


def test_inherited_sources_of_extensive_field_are_own_only(tree):
    _, group, building = tree
    group.dwellings = 48
    assert building.candidates("dwellings", inherited=True) == []


def test_field_missing_on_parent_stops_inheritance(tree):
    _, group, building = tree
    assert "code" not in group.sourced_fields()
    assert building.code is None
    assert building.provider_id("code") is None


def test_entity_without_parent_inherits_nothing():
    orphan = Node(id="orphan")
    assert orphan.year is None
    assert orphan.provider_id("year") is None


def test_inheritance_uses_each_level_priority(tree):
    client, group, building = tree
    group.set("year", 1970, source="bdnb")
    group.set("year", 1972, source="dpe")
    assert building.year == 1972  # dpe before bdnb by default
    client._container.source_priority = SourcePriority(default=["bdnb", "dpe"])
    assert building.year == 1970


def test_part_inherits_from_part_of_parent_owner(tree):
    _, group, building = tree
    group.part.set("u_value", 1.2, source="dpe")
    assert building.part.u_value == 1.2
    assert building.part.provider_id("u_value") == "bdnb-bg-1111"
    group.part.area = 300.0
    assert building.part.area is None


def test_origin_of_unknown_field_is_rejected(tree):
    with pytest.raises(UnknownFieldError):
        tree[2].provider_id("nope")


def test_inheritance_cycle_is_detected():
    first, second = Node(id="a"), Node(id="b")
    attach(first, second)
    attach(second, first)
    with pytest.raises(HierarchyError, match="cycle"):
        _ = first.year
