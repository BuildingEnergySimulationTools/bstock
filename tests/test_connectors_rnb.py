import pytest

from bstock.building import Building
from bstock.connectors import rnb
from bstock.exceptions import InvalidArgumentError, UnknownIdError
from bstock.park import BuildingPark
from bstock.provenance import sourced
from fakes import (
    DEACTIVATED,
    DEMOLISHED,
    GABARRE,
    INACTIVE_FILES,
    MERGED,
    NEIGHBOUR,
    SPLIT,
    FakeRnbClient,
)


class ProjectBuilding(Building):
    code_patrimoine = sourced(str)


@pytest.fixture
def client(recorded):
    return FakeRnbClient(recorded)


@pytest.fixture
def park():
    park = BuildingPark(name="Bayonne", building_cls=ProjectBuilding)
    park.new_site("ecole")
    return park


# --- correspondences --------------------------------------------------------------------------


def test_building_candidates_from_recorded_response(recorded):
    candidates = rnb.building_candidates(recorded(f"rnb/building_{GABARRE}.json"))
    assert candidates["lat"].value == pytest.approx(43.48829, abs=1e-5)
    assert candidates["lon"].value == pytest.approx(-1.45472, abs=1e-5)
    assert candidates["footprint"].value["type"] == "MultiPolygon"
    assert candidates["address"].value == "4 Rue de la Gabarre, 64100 Bayonne"
    assert candidates["address"].comment == "BAN 64102_0885_00004"
    assert candidates["municipality"].value == "Bayonne"
    assert candidates["insee_code"].value == "64102"
    assert {c.source for c in candidates.values()} == {"rnb"}
    assert {c.ref for c in candidates.values()} == {GABARRE}
    assert {c.date for c in candidates.values()} == {None}


def test_point_shape_is_not_a_footprint(recorded):
    record = recorded(f"rnb/building_{GABARRE}.json")
    record["shape"] = record["point"]
    assert "footprint" not in rnb.building_candidates(record)


def test_building_without_address_gives_no_address(recorded):
    record = {**recorded(f"rnb/building_{GABARRE}.json"), "addresses": []}
    candidates = rnb.building_candidates(record)
    assert not {"address", "municipality", "insee_code"} & set(candidates)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("6x3k8hhy9ffc", GABARRE), ("6X3K-8HHY-9FFC", GABARRE), (GABARRE, GABARRE)],
)
def test_rnb_ids_are_normalised(raw, expected):
    assert rnb.normalize_rnb_id(raw) == expected


@pytest.mark.parametrize(
    ("name", "event", "ids"),
    [
        ("merged", "merge", ["WS1R5QPPVT54"]),
        ("split", "split", ["C8PBQHEVE3HX", "Q7E81Q1CR8GM"]),
        ("deactivated", "deactivation", []),
    ],
)
def test_successors_are_read_in_history(recorded, name, event, ids):
    rnb_id = next(k for k, v in INACTIVE_FILES.items() if v == name)
    assert rnb.successors(recorded(f"rnb/history_{name}_{rnb_id}.json")) == (event, ids)


# --- add_buildings and enrich -----------------------------------------------------------------


def test_add_buildings_creates_project_buildings_under_parent(park, client):
    report = rnb.add_buildings(park, ["6x3k-8hhy-9ffc"], parent="ecole", client=client)
    building = park[GABARRE]
    assert isinstance(building, ProjectBuilding)
    assert building.parent is park["ecole"]
    assert building.municipality == "Bayonne"
    assert building.footprint["type"] == "MultiPolygon"
    assert building.bdnb_batiment_construction_id == "bdnb-bc-3WE8-8JQV-DFE3"
    assert report.created == [GABARRE]


def test_add_buildings_enriches_existing_building(park, client):
    existing = park.new_building(id="BAY-001", rnb_id=GABARRE)
    report = rnb.add_buildings(park, [GABARRE], client=client)
    assert report.enriched == ["BAY-001"]
    assert report.created == []
    assert existing.insee_code == "64102"


def test_bdnb_construction_id_already_set_is_kept(park, client):
    building = park.new_building(
        rnb_id=GABARRE, bdnb_batiment_construction_id="bdnb-bc-AAAA-BBBB-CCCC"
    )
    rnb.enrich(park, client=client)
    assert building.bdnb_batiment_construction_id == "bdnb-bc-AAAA-BBBB-CCCC"


def test_running_twice_is_idempotent(park, client):
    rnb.add_buildings(park, [GABARRE], client=client)
    first = park[GABARRE].model_dump()
    report = rnb.add_buildings(park, [GABARRE], client=client)
    assert park[GABARRE].model_dump() == first
    assert report.enriched == [GABARRE]
    assert len(list(park.iter_buildings())) == 1


def test_values_no_longer_given_are_removed(park, client, recorded):
    rnb.add_buildings(park, [GABARRE], client=client)
    record = {**recorded(f"rnb/building_{GABARRE}.json"), "addresses": []}
    client.building = lambda rnb_id: record
    rnb.enrich(park, client=client)
    assert park[GABARRE].address is None
    assert park[GABARRE].lat is not None


def test_other_sources_are_kept(park, client):
    building = park.new_building(rnb_id=GABARRE)
    building.address = "4 rue de la Gabarre"
    rnb.enrich(park, client=client)
    assert building.address == "4 rue de la Gabarre"  # manual wins
    assert {c.source for c in building.candidates("address")} == {"manual", "rnb"}


def test_unknown_id_is_reported_not_created(park, client):
    report = rnb.add_buildings(park, ["ZZZZZZZZZZZZ"], client=client)
    assert report.not_found == ["ZZZZZZZZZZZZ"]
    assert list(park.iter_buildings()) == []


def test_unknown_id_of_existing_building_is_reported_under_its_id(park, client):
    park.new_building(id="BAY-001", rnb_id="ZZZZZZZZZZZZ")
    report = rnb.add_buildings(park, ["ZZZZZZZZZZZZ"], client=client)
    assert report.not_found == ["BAY-001"]
    assert "ZZZZZZZZZZZZ" in report.messages["BAY-001"][0]


def test_demolished_building_is_reported_and_left_aside(park, client):
    report = rnb.add_buildings(park, [DEMOLISHED], client=client)
    assert DEMOLISHED not in park
    assert "demolished" in report.messages[DEMOLISHED][0]


@pytest.mark.parametrize(
    ("rnb_id", "expected"),
    [
        (MERGED, "after a merge; successors: WS1R5QPPVT54"),
        (SPLIT, "after a split; successors: C8PBQHEVE3HX, Q7E81Q1CR8GM"),
        (DEACTIVATED, "after a deactivation; no successor"),
    ],
)
def test_inactive_id_is_reported_with_successors(park, client, rnb_id, expected):
    building = park.new_building(rnb_id=rnb_id)
    report = rnb.enrich(park, client=client)
    assert expected in report.messages[building.id][0]
    assert report.enriched == []
    assert building.values == {}


def test_enrich_skips_buildings_without_rnb_id(park, client):
    park.new_building(id="neuf")
    park.new_building(rnb_id=GABARRE)
    report = rnb.enrich(park, ["neuf", GABARRE], client=client)
    assert report.enriched == [GABARRE]
    assert "suggest_matches" in report.messages["neuf"][0]
    assert client.calls == [("building", GABARRE)]


def test_enrich_rejects_unknown_building(park, client):
    with pytest.raises(UnknownIdError):
        rnb.enrich(park, ["absent"], client=client)


def test_unknown_parent_fails_before_any_request(park, client):
    with pytest.raises(UnknownIdError):
        rnb.add_buildings(park, [GABARRE], parent="absent", client=client)
    assert client.calls == []


def test_report_to_dataframe(park, client):
    report = rnb.add_buildings(park, [GABARRE, "ZZZZZZZZZZZZ", DEMOLISHED], client=client)
    frame = report.to_dataframe()
    assert list(frame.index) == [GABARRE, "ZZZZZZZZZZZZ", DEMOLISHED]
    assert frame.loc[GABARRE, "created"]


# --- suggest_matches --------------------------------------------------------------------------


@pytest.fixture
def unmatched(park):
    return park.new_building(
        id="BAY-001", lat=43.48829, lon=-1.45472, address="4 rue de la Gabarre 64100 Bayonne"
    )


def test_point_suggestions_list_every_candidate(park, client, unmatched):
    frame = rnb.suggest_matches(park, client=client, radius=50)
    assert list(frame.columns) == rnb.SUGGESTION_COLUMNS
    assert set(frame["building_id"]) == {"BAY-001"}
    assert frame.iloc[0]["rnb_id"] == GABARRE
    assert frame.iloc[0]["distance"] == 0
    assert frame.iloc[0]["address"] == "4 Rue de la Gabarre, 64100 Bayonne"
    assert len(frame) > 1
    assert unmatched.rnb_id is None  # nothing assigned by default
    assert client.calls[0] == ("closest", 43.48829, -1.45472, 50)


def test_assign_unique_sets_the_only_candidate(park, client, unmatched, recorded):
    client.closest_response = recorded("rnb/closest_bayonne.json")["results"][:1]
    frame = rnb.suggest_matches(park, client=client, assign="unique")
    assert unmatched.rnb_id == GABARRE
    assert park.get_by_rnb(GABARRE) is unmatched
    assert frame["assigned"].tolist() == [True]


def test_assign_unique_ignores_several_candidates(park, client, unmatched):
    frame = rnb.suggest_matches(park, client=client, assign="unique")
    assert unmatched.rnb_id is None
    assert not frame["assigned"].any()


def test_no_candidate_gives_no_row(park, client, unmatched):
    client.closest_response = []
    frame = rnb.suggest_matches(park, client=client, assign="unique")
    assert frame.empty
    assert list(frame.columns) == rnb.SUGGESTION_COLUMNS


def test_rnb_id_already_used_is_reported_never_assigned(park, client, unmatched, recorded):
    park.new_building(id="autre", rnb_id=GABARRE)
    client.closest_response = recorded("rnb/closest_bayonne.json")["results"][:1]
    frame = rnb.suggest_matches(park, ["BAY-001"], client=client, assign="unique")
    assert frame.iloc[0]["used_by"] == "autre"
    assert not frame.iloc[0]["assigned"]
    assert unmatched.rnb_id is None


def test_by_default_only_buildings_without_rnb_id_are_matched(park, client, unmatched):
    park.new_building(rnb_id=NEIGHBOUR, lat=43.5, lon=-1.4)
    rnb.suggest_matches(park, client=client)
    assert [c[0] for c in client.calls] == ["closest"]


def test_building_without_point_is_skipped(park, client):
    park.new_building(id="sans-point")
    assert rnb.suggest_matches(park, client=client).empty
    assert client.calls == []


def test_point_can_be_inherited_from_site(park, client):
    site = park["ecole"]
    site.lat, site.lon = 43.48829, -1.45472
    park.new_building(id="dans-le-site", parent="ecole")
    frame = rnb.suggest_matches(park, client=client)
    assert set(frame["building_id"]) == {"dans-le-site"}


def test_address_suggestions_carry_the_ban_score(park, client, unmatched):
    frame = rnb.suggest_matches(park, client=client, by="address", assign="unique")
    assert frame.iloc[0]["rnb_id"] == GABARRE
    assert frame.iloc[0]["score"] == pytest.approx(0.967, abs=1e-3)
    assert unmatched.rnb_id == GABARRE
    assert client.calls == [("address", "4 rue de la Gabarre 64100 Bayonne")]


def test_low_geocoding_score_gives_no_candidate(park, recorded, unmatched):
    client = FakeRnbClient(recorded, address=recorded("rnb/address_search_low_score.json"))
    assert rnb.suggest_matches(park, client=client, by="address").empty


@pytest.mark.parametrize(
    "kwargs",
    [{"radius": 0}, {"radius": 1500}, {"by": "cadastre"}, {"assign": "all"}],
)
def test_invalid_suggestion_options_are_rejected(park, client, kwargs):
    with pytest.raises(InvalidArgumentError, match=r"^(by|radius|assign) must"):
        rnb.suggest_matches(park, client=client, **kwargs)


# --- real API ---------------------------------------------------------------------------------


@pytest.mark.network
def test_real_rnb_building_is_added():
    park = BuildingPark(name="réseau")
    report = rnb.add_buildings(park, [GABARRE])
    assert report.created == [GABARRE]
    assert park[GABARRE].insee_code == "64102"
