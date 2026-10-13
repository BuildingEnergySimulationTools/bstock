import pytest
from pydantic import ValidationError

from bstock.building import Building
from bstock.exceptions import DuplicateIdError, EntityInUseError, UnknownIdError
from bstock.park import BuildingPark
from bstock.provenance import SourcePriority, sourced
from bstock.site import Site
from bstock.systems import HeatingGenerator, SolarPV

RNB_A, RNB_B, RNB_C = "A1B2C3D4E5F6", "B1B2C3D4E5F6", "C1B2C3D4E5F6"
GROUP = "bdnb-bg-1111-1HRT-8SVH"


class BayonneSite(Site):
    secteur = sourced(str, description="Management sector")


class BayonneBuilding(Building):
    code_patrimoine = sourced(str)
    surface_utile_client = sourced(float, unit="m²", ge=0)


@pytest.fixture
def park():
    """client site > BDNB group > A, B ; client site > C ; D at park level."""
    park = BuildingPark(name="Ville de Bayonne")
    client = park.new_site("site-ecole-A", name="Groupe scolaire A", usage="tertiary")
    park.new_site(GROUP, parent=client, bdnb_batiment_groupe_id=GROUP)
    park.new_building(RNB_A, site=GROUP, year_construction=1972, source="bdnb")
    park.new_building(RNB_B, site=GROUP)
    park.new_building(RNB_C, site="site-ecole-A")
    park.new_building(id="bldg-isolated")
    return park


# --- creation and lookup ----------------------------------------------------------------------


def test_lookup(park):
    a = park.get(RNB_A)
    assert isinstance(a, Building)
    assert park[RNB_A] is a
    assert RNB_A in park
    assert park.get("nope") is None
    with pytest.raises(UnknownIdError):
        park["nope"]
    assert park.get_by_rnb(RNB_A) is a
    assert park.get_by_rnb("ZZZZZZZZZZZZ") is None


def test_iteration(park):
    assert [s.id for s in park.iter_sites()] == ["site-ecole-A", GROUP]
    assert [b.id for b in park.iter_buildings()] == ["bldg-isolated", RNB_C, RNB_A, RNB_B]


def test_new_entities_are_attached(park):
    a = park[RNB_A]
    assert a.park is park
    assert a.site is park[GROUP]
    assert park["bldg-isolated"].site is None
    assert park["bldg-isolated"].park is park


def test_new_building_source_applies_to_attributes(park):
    assert park[RNB_A].effective("year_construction").source == "bdnb"


def test_inheritance_inside_park(park):
    assert park[RNB_A].usage == "tertiary"
    assert park[RNB_A].origin("usage") == "site-ecole-A"


def test_park_priority_is_used_by_all_entities(park):
    a = park[RNB_A]
    a.set("heated_area", 100.0, source="bdnb")
    a.set("heated_area", 200.0, source="dpe")
    assert a.heated_area == 200.0
    park.source_priority = SourcePriority(default=["bdnb", "dpe"])
    assert a.heated_area == 100.0
    assert park[GROUP].envelope.active_source_priority() is park.source_priority


# --- uniqueness -------------------------------------------------------------------------------


def test_duplicate_id_anywhere_in_park_is_rejected(park):
    with pytest.raises(DuplicateIdError):
        park.new_building(id=RNB_A)  # same id, different place
    with pytest.raises(DuplicateIdError):
        park.new_site(RNB_C)  # a site cannot reuse a building id


def test_duplicate_rnb_id_is_rejected(park):
    with pytest.raises(DuplicateIdError, match="RNB"):
        park.new_building(RNB_A, id="other-id")


def test_changing_rnb_id_keeps_index_consistent(park):
    a = park[RNB_A]
    a.rnb_id = "ZZZZZZZZZZZZ"
    assert park.get_by_rnb("ZZZZZZZZZZZZ") is a
    assert park.get_by_rnb(RNB_A) is None
    assert a.id == RNB_A
    with pytest.raises(DuplicateIdError):
        a.rnb_id = RNB_B


def test_ids_are_immutable(park):
    with pytest.raises(ValidationError):
        park[RNB_A].id = "new-id"


def test_failed_registration_indexes_nothing(park):
    site = Site(id="new-site", buildings=[Building(id="fresh"), Building(id=RNB_C)])
    with pytest.raises(DuplicateIdError):
        park.add_site(site)
    assert "new-site" not in park
    assert "fresh" not in park


def test_entity_methods_update_park_index(park):
    group = park[GROUP]
    group.add_building(Building(id="added-via-site"))
    assert "added-via-site" in park
    with pytest.raises(DuplicateIdError):
        group.add_building(Building(id="bldg-isolated"))
    group.detach("added-via-site")
    assert "added-via-site" not in park


def test_systems_are_indexed(park):
    boiler = park.add_system(HeatingGenerator(id="heating-1"), owner=RNB_A)
    assert park["heating-1"] is boiler
    assert list(park.iter_systems("heating")) == [boiler]
    with pytest.raises(DuplicateIdError):
        park[RNB_B].add_system(HeatingGenerator(id="heating-1"))


# --- project classes --------------------------------------------------------------------------


def test_new_entities_use_project_classes():
    park = BuildingPark(name="Bayonne", site_cls=BayonneSite, building_cls=BayonneBuilding)
    site = park.new_site("s1", secteur="Nord", source="bayonne_xlsx")
    building = park.new_building(RNB_A, site=site, code_patrimoine="BAT-0042")
    assert type(site) is BayonneSite
    assert type(building) is BayonneBuilding
    assert building.code_patrimoine == "BAT-0042"
    assert site.effective("secteur").source == "bayonne_xlsx"
    assert park.site_cls is BayonneSite
    assert park.building_cls is BayonneBuilding


def test_add_checks_project_class():
    park = BuildingPark(name="Bayonne", building_cls=BayonneBuilding)
    with pytest.raises(TypeError, match="BayonneBuilding"):
        park.add_building(Building(rnb_id=RNB_A))


def test_invalid_class_is_rejected():
    with pytest.raises(ValidationError):
        BuildingPark(name="x", building_cls=Site)


# --- shared systems ---------------------------------------------------------------------------


def test_add_shared_system_checks_served_buildings(park):
    plant = park.add_system(HeatingGenerator(id="plant", serves=[RNB_A, RNB_C]), "site-ecole-A")
    assert park[RNB_A].systems("heating") == [plant]
    assert park["bldg-isolated"].systems("heating") == []
    with pytest.raises(UnknownIdError):
        park.add_system(HeatingGenerator(serves=["bldg-isolated"]), "site-ecole-A")
    with pytest.raises(ValueError, match="serves"):
        park.add_system(HeatingGenerator(serves=[RNB_A]), RNB_A)


def test_add_system_to_unknown_owner(park):
    with pytest.raises(UnknownIdError):
        park.add_system(SolarPV(), "nope")


# --- move -------------------------------------------------------------------------------------


def test_move_building_changes_inheritance(park):
    park[GROUP].set("year_construction", 1980, source="bdnb")
    b = park[RNB_B]
    assert b.year_construction == 1980
    park.move(RNB_B, None)
    assert b.site is None
    assert b in park.buildings
    assert b.year_construction is None
    park.move(RNB_B, GROUP)
    assert b.site is park[GROUP]
    assert b.year_construction == 1980


def test_move_site_keeps_its_content(park):
    park.move(GROUP, None)
    group = park[GROUP]
    assert group.parent is None
    assert group in park.sites
    assert park[RNB_A].site is group
    assert park[RNB_A].usage is None  # no longer under the client site


def test_site_cannot_move_inside_itself(park):
    with pytest.raises(ValueError, match="inside itself"):
        park.move("site-ecole-A", GROUP)


# --- remove -----------------------------------------------------------------------------------


def test_remove_building(park):
    removed = park.remove(RNB_C)
    assert removed.id == RNB_C
    assert RNB_C not in park
    assert park.get_by_rnb(RNB_C) is None
    assert removed.park is None


def test_remove_site_removes_its_content(park):
    park.add_system(HeatingGenerator(id="heating-a"), RNB_A)
    park.remove(GROUP)
    for entity_id in (GROUP, RNB_A, RNB_B, "heating-a"):
        assert entity_id not in park
    assert [s.id for s in park.iter_sites()] == ["site-ecole-A"]


def test_remove_system(park):
    park.add_system(HeatingGenerator(id="heating-a"), RNB_A)
    park.remove("heating-a")
    assert park[RNB_A].heating == []
    assert "heating-a" not in park


def test_remove_referenced_building_needs_force(park):
    plant = park.add_system(HeatingGenerator(id="plant", serves=[RNB_A, RNB_C]), "site-ecole-A")
    with pytest.raises(EntityInUseError, match="plant"):
        park.remove(RNB_A)
    park.remove(RNB_A, force=True)
    assert plant.serves == [RNB_C]


def test_references_inside_removed_subtree_do_not_block(park):
    park.add_system(HeatingGenerator(id="group-boiler", serves=[RNB_A]), GROUP)
    park.remove(GROUP)
    assert "group-boiler" not in park


def test_remove_unknown_id(park):
    with pytest.raises(UnknownIdError):
        park.remove("nope")


# --- construction from data -------------------------------------------------------------------


def test_park_built_from_nested_data_is_indexed():
    park = BuildingPark.model_validate(
        {
            "name": "x",
            "sites": [{"id": "s", "buildings": [{"rnb_id": RNB_A}]}],
            "buildings": [{"id": "b2"}],
        }
    )
    assert park[RNB_A].site is park["s"]
    assert park["b2"].park is park


def test_duplicate_ids_in_data_are_rejected():
    with pytest.raises(DuplicateIdError):
        BuildingPark(
            name="x",
            sites=[Site(id="s", buildings=[Building(id="b")])],
            buildings=[Building(id="b")],
        )
