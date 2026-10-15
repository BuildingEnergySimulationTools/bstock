import datetime
import sys

import pytest

from bstock.building import Building
from bstock.connectors import _geo, bdnb
from bstock.connectors.bdnb import BdnbClient
from bstock.enums import BuildingUsage, EnergyType, GeneratorTechnology, SystemKind
from bstock.exceptions import BstockWarning, ConnectorError
from bstock.park import BuildingPark
from bstock.provenance import sourced
from bstock.site import Site
from fakes import (
    MULTI_GROUP,
    MULTI_RNB,
    OTHER_CONSTRUCTION,
    OTHER_RNB,
    SINGLE_GROUP,
    SINGLE_RNB,
    FakeBdnbClient,
)

T, E = GeneratorTechnology, EnergyType


class ProjectSite(Site):
    secteur = sourced(str)


@pytest.fixture
def client(recorded):
    return FakeBdnbClient(recorded)


@pytest.fixture
def park():
    park = BuildingPark(name="Bayonne", site_cls=ProjectSite)
    park.new_site("ecole")
    park.new_site("mairie")
    return park


@pytest.fixture
def recorded_row(recorded):
    def row(kind, group):
        name = "single" if group == SINGLE_GROUP else "multi"
        return recorded(f"bdnb/{kind}_{name}_{group}.json")[0]

    return row


# --- correspondences --------------------------------------------------------------------------


def test_construction_candidates(recorded_row):
    candidates = bdnb.construction_candidates(recorded_row("batiment_construction", SINGLE_GROUP))
    assert candidates["height"].value == 19.2
    assert candidates["footprint_area"].value == 2831
    footprint = candidates["footprint"].value
    assert footprint["type"] == "MultiPolygon"
    assert "crs" not in footprint
    assert footprint["coordinates"][0][0][0][1] == pytest.approx(43.49, abs=0.05)
    assert {c.ref for c in candidates.values()} == {"bdnb-bc-FWPN-JGGA-BTKB"}
    assert {c.source for c in candidates.values()} == {"bdnb"}


def test_group_candidates(recorded_row):
    candidates = bdnb.group_candidates(recorded_row("batiment_groupe_complet", SINGLE_GROUP))
    assert candidates["usage"].value == BuildingUsage.RESIDENTIAL_COLLECTIVE
    assert candidates["usage"].raw == "Résidentiel collectif"
    assert candidates["construction_year"].value == 2016
    assert candidates["dwelling_count"].value == 192
    assert candidates["level_count"].value == 7
    assert candidates["height"].value == 19
    assert candidates["footprint_area"].value == 2831
    assert candidates["footprint"].value["type"] == "MultiPolygon"
    assert {c.ref for c in candidates.values()} == {SINGLE_GROUP}
    assert {c.date for c in candidates.values()} == {None}


def test_invalid_values_are_ignored(recorded_row):
    row = {**recorded_row("batiment_groupe_complet", SINGLE_GROUP), "nb_niveau": 0}
    assert "level_count" not in bdnb.group_candidates(row)


def test_generators_use_energy_columns_and_dpe_date(recorded_row, recorded):
    group = recorded_row("batiment_groupe_complet", MULTI_GROUP)
    dpe = recorded(f"bdnb/batiment_groupe_dpe_representatif_logement_{MULTI_GROUP}.json")[0]
    produced = bdnb.generator_candidates(group, dpe)
    [heating] = produced[SystemKind.HEATING]
    assert heating["technology"].value == T.ELECTRIC_RESISTANCE
    assert heating["technology"].raw == "generateurs a effet joule"
    assert heating["energy_type"].value == E.ELECTRICITY
    assert heating["energy_type"].raw == "electricite"
    assert heating["name"].value == "main"
    assert heating["technology"].date == datetime.date(2026, 3, 10)
    [dhw] = produced[SystemKind.DHW]
    assert dhw["technology"].value == T.ELECTRIC_WATER_TANK
    assert dhw["energy_type"].raw == "electricite"  # from the representative DPE
    assert produced[SystemKind.COOLING] == []


def test_generator_energy_falls_back_on_the_label(recorded_row):
    group = recorded_row("batiment_groupe_complet", SINGLE_GROUP)
    [dhw] = bdnb.generator_candidates(group)[SystemKind.DHW]  # no representative DPE row
    assert dhw["technology"].value == T.BOILER_CONDENSING
    assert dhw["energy_type"].value == E.NATURAL_GAS
    assert dhw["energy_type"].raw == "chaudiere gaz condensation"
    assert dhw["technology"].date is None


def test_backup_generator_is_second():
    group = {
        "batiment_groupe_id": SINGLE_GROUP,
        "type_generateur_chauffage": "pac air/eau",
        "type_generateur_chauffage_appoint": "poele ou insert bois",
    }
    heating = bdnb.generator_candidates(group)[SystemKind.HEATING]
    assert [g["name"].value for g in heating] == ["main", "backup"]
    assert heating[1]["energy_type"].value == E.WOOD


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-03-09T23:00:00", datetime.date(2026, 3, 10)),  # winter: UTC+1
        ("2025-07-14T22:00:00", datetime.date(2025, 7, 15)),  # summer: UTC+2
        ("2024-04-15T15:50:47.17497", datetime.date(2024, 4, 15)),
        ("2024-04-15T23:30:00+02:00", datetime.date(2024, 4, 15)),
        (None, None),
        ("hier", None),
    ],
)
def test_dpe_date(value, expected):
    assert bdnb.dpe_date(value) == expected


# --- groups of one construction ---------------------------------------------------------------


def test_single_construction_group_values_go_on_the_building(park, client):
    building = park.new_building(rnb_id=SINGLE_RNB, parent="ecole")
    report = bdnb.enrich(park, client=client)
    assert report.enriched == [SINGLE_RNB]
    assert building.parent.id == "ecole"
    assert building.bdnb_batiment_groupe_id == SINGLE_GROUP
    assert building.bdnb_batiment_construction_id == "bdnb-bc-FWPN-JGGA-BTKB"
    assert building.dwelling_count == 192
    assert building.usage == BuildingUsage.RESIDENTIAL_COLLECTIVE
    assert building.height == 19.2  # the construction wins over the group mean
    assert building.effective("height").ref == "bdnb-bc-FWPN-JGGA-BTKB"
    assert "2464E1353074X" in building.dpe_ids
    [heating] = building.heating
    assert heating.id == f"heating-{SINGLE_RNB}-bdnb-1"
    assert heating.technology == T.BOILER_CONDENSING
    assert heating.energy_type == E.NATURAL_GAS
    assert [s.id for s in building.dhw] == [f"dhw-{SINGLE_RNB}-bdnb-1"]


# --- groups of several constructions ----------------------------------------------------------


def test_multi_construction_group_becomes_a_site(park, client):
    building = park.new_building(rnb_id=MULTI_RNB, parent="ecole")
    report = bdnb.enrich(park, client=client)
    site = park[MULTI_GROUP]
    assert isinstance(site, ProjectSite)
    assert site.parent.id == "ecole"
    assert site.bdnb_batiment_groupe_id == MULTI_GROUP
    assert building.parent is site
    assert report.created == [MULTI_GROUP]
    assert report.enriched == [MULTI_GROUP, MULTI_RNB]
    # group values on the site: intensive ones inherited, extensive ones not
    assert site.dwelling_count == 83
    assert site.footprint_area == 3114
    assert building.dwelling_count is None
    assert building.construction_year == 2007
    assert building.provider_id("construction_year") == MULTI_GROUP
    # construction values on the building
    assert building.height == 14.9
    assert building.footprint_area == 456
    assert building.bdnb_batiment_groupe_id is None
    # systems and DPE on the site, served to the building
    assert [s.id for s in site.heating] == [f"heating-{MULTI_GROUP}-bdnb-1"]
    assert building.heating == []
    served = building.find_serving_systems("heating")
    assert [s.id for s in served] == [f"heating-{MULTI_GROUP}-bdnb-1"]
    assert site.dpe_ids == ["2664E0684239O"]


def test_group_site_at_top_level(client, recorded):
    park = BuildingPark(name="p")
    building = park.new_building(rnb_id=MULTI_RNB)
    bdnb.enrich(park, client=client)
    assert building.parent.id == MULTI_GROUP
    assert park[MULTI_GROUP].parent is None


def test_two_buildings_of_a_group_share_the_site(park, recorded):
    client = FakeBdnbClient(recorded, rnb_of={OTHER_CONSTRUCTION: OTHER_RNB})
    a = park.new_building(rnb_id=MULTI_RNB, parent="ecole")
    b = park.new_building(rnb_id=OTHER_RNB, parent="ecole")
    bdnb.enrich(park, client=client)
    assert a.parent is b.parent is park[MULTI_GROUP]
    assert client.calls.count(("count", MULTI_GROUP)) == 1


def test_existing_group_site_is_reused(park, client):
    site = park.new_site(MULTI_GROUP, parent="mairie", secteur="nord")
    building = park.new_building(rnb_id=MULTI_RNB, parent="mairie")
    report = bdnb.enrich(park, client=client)
    assert park[MULTI_GROUP] is site
    assert building.parent is site
    assert site.secteur == "nord"
    assert report.created == []


def test_different_parents_spread_intensive_values(park, recorded):
    client = FakeBdnbClient(recorded, rnb_of={OTHER_CONSTRUCTION: OTHER_RNB})
    a = park.new_building(rnb_id=MULTI_RNB, parent="ecole")
    b = park.new_building(rnb_id=OTHER_RNB, parent="mairie")
    report = bdnb.enrich(park, client=client)
    assert MULTI_GROUP not in park
    assert a.parent.id == "ecole"
    assert b.parent.id == "mairie"
    for building in (a, b):
        assert building.construction_year == 2007
        assert building.dwelling_count is None
        assert building.heating[0].technology == T.ELECTRIC_RESISTANCE
        assert "different parents" in report.messages[building.id][0]
    assert a.footprint_area == 456  # the construction value, not the group one


def test_group_sites_can_be_disabled(park, client):
    building = park.new_building(rnb_id=MULTI_RNB, parent="ecole")
    report = bdnb.enrich(park, group_sites=False, client=client)
    assert MULTI_GROUP not in park
    assert building.usage == BuildingUsage.RESIDENTIAL_COLLECTIVE
    assert building.dwelling_count is None
    assert "group_sites=False" in report.messages[MULTI_RNB][0]


@pytest.mark.parametrize("rnb_id", [SINGLE_RNB, MULTI_RNB])
def test_running_twice_is_idempotent(park, client, rnb_id):
    park.new_building(rnb_id=rnb_id, parent="ecole")
    bdnb.enrich(park, client=client)
    first = park.to_json()
    report = bdnb.enrich(park, client=client)
    assert park.to_json() == first
    assert report.created == []


# --- reported cases ---------------------------------------------------------------------------


def test_building_without_construction_is_not_found(park, client):
    park.new_building(rnb_id="ZZZZZZZZZZZZ")
    report = bdnb.enrich(park, client=client)
    assert report.not_found == ["ZZZZZZZZZZZZ"]


def test_several_constructions_for_one_rnb_id_are_ambiguous(park, recorded):
    client = FakeBdnbClient(recorded, rnb_of={OTHER_CONSTRUCTION: MULTI_RNB})
    building = park.new_building(rnb_id=MULTI_RNB)
    report = bdnb.enrich(park, client=client)
    assert report.ambiguous == [MULTI_RNB]
    assert building.values == {}
    assert MULTI_GROUP not in park


def test_building_without_rnb_id_is_noted(park, client):
    park.new_building(id="neuf")
    report = bdnb.enrich(park, ["neuf"], client=client)
    assert "suggest_matches" in report.messages["neuf"][0]
    assert client.calls == []


def test_without_geo_extra_geometries_are_ignored_with_a_warning(park, client, monkeypatch):
    _geo._transformer.cache_clear()
    monkeypatch.setitem(sys.modules, "pyproj", None)
    building = park.new_building(rnb_id=SINGLE_RNB)
    with pytest.warns(BstockWarning, match=r"bstock\[geo\]"):
        bdnb.enrich(park, client=client)
    _geo._transformer.cache_clear()
    assert building.footprint is None
    assert building.footprint_area == 2831
    assert building.dwelling_count == 192


def test_project_building_class_is_kept(recorded):
    class ProjectBuilding(Building):
        code = sourced(str)

    park = BuildingPark(name="p", building_cls=ProjectBuilding)
    building = park.new_building(rnb_id=MULTI_RNB)
    bdnb.enrich(park, client=FakeBdnbClient(recorded))
    assert isinstance(park[MULTI_RNB], ProjectBuilding)
    assert building.parent.id == MULTI_GROUP


# --- client -----------------------------------------------------------------------------------


def test_client_batches_ids_and_follows_offsets(fake_session):
    rows = [{"rnb_id": f"R{i}"} for i in range(10)]
    session = fake_session([rows, rows[:2], []])
    client = BdnbClient(session=session, sleep=lambda s: None)
    found = client.constructions_by_rnb([f"R{i}" for i in range(12)])
    assert len(found) == 12
    first, second, third = (c["params"] for c in session.calls)
    assert first["rnb_id"] == f"in.({','.join(f'R{i}' for i in range(10))})"
    assert first["limit"] == 10
    assert (first["offset"], second["offset"]) == (0, 10)
    assert third["rnb_id"] == "in.(R10,R11)"
    assert session.calls[0]["url"].endswith("/donnees/batiment_construction")


def test_api_key_is_sent_and_raises_page_size(fake_session):
    session = fake_session([[]])
    client = BdnbClient("secret", session=session)
    client.groups(["g"])
    assert session.calls[0]["headers"]["X-Gravitee-Api-Key"] == "secret"
    assert session.calls[0]["params"]["limit"] == 1000
    assert "usage_principal_bdnb_open" in session.calls[0]["params"]["select"]


def test_constructions_are_counted_from_content_range(fake_session, fake_response):
    session = fake_session([fake_response(200, [{}], headers={"Content-Range": "0-0/7"})])
    assert BdnbClient(session=session).count_constructions(MULTI_GROUP) == 7
    call = session.calls[0]
    assert call["headers"]["Prefer"] == "count=exact"
    assert call["params"]["batiment_groupe_id"] == f"eq.{MULTI_GROUP}"


def test_missing_content_range_raises(fake_session):
    session = fake_session([[{}]])
    with pytest.raises(ConnectorError, match="Content-Range"):
        BdnbClient(session=session).count_constructions(MULTI_GROUP)


def test_unexpected_response_raises(fake_session):
    session = fake_session([{"message": "boom"}])
    with pytest.raises(ConnectorError, match="unexpected"):
        BdnbClient(session=session).groups(["g"])


# --- real API ---------------------------------------------------------------------------------


@pytest.mark.network
def test_real_bdnb_enrichment():
    park = BuildingPark(name="réseau")
    building = park.new_building(rnb_id=SINGLE_RNB)
    report = bdnb.enrich(park)
    assert report.enriched == [SINGLE_RNB]
    assert building.bdnb_batiment_groupe_id == SINGLE_GROUP
    assert building.footprint["type"] == "MultiPolygon"
