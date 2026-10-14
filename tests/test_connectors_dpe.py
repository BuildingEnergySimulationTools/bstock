import datetime

import pytest

from bstock.connectors import dpe
from bstock.connectors.dpe import DATASET_KEY, DpeClient
from bstock.enums import (
    BuildingUsage,
    ComponentKind,
    EnergyType,
    GeneratorTechnology,
    Inertia,
    InsulationLevel,
    SystemKind,
)
from bstock.exceptions import ConnectorError
from bstock.park import BuildingPark

T, E = GeneratorTechnology, EnergyType
FILES = {
    "dpe03existant": [
        "lines_dpe03existant_bayonne_immeuble.json",
        "lines_dpe03existant_bayonne_maison.json",
        "lines_dpe03existant_bayonne_appartement.json",
        "lines_dpe03existant_bayonne_appartement_from_immeuble.json",
    ],
    "dpe02neuf": ["lines_dpe02neuf_bayonne_immeuble.json", "lines_dpe02neuf_bayonne_maison.json"],
    "dpe01tertiaire": [
        "lines_dpe01tertiaire_bayonne.json",
        "lines_dpe01tertiaire_bayonne_facture.json",
    ],
}
IMMEUBLE, IMMEUBLE_RNB = "2664E2519948T", "2E1X4DY5AY9T"  # replaces 2664E2519887K
REPLACED = "2664E2519887K"
MAISON_RNB = "1SZPEQZ91AE6"
APARTMENT_RNB = "2PH4SW4PVQQ9"
FROM_BUILDING_RNB, BUILDING_DPE = "PEH1TASYDBYE", "2664E0578892T"
TERTIARY_RNB = "WWZHWSD72AAJ"


@pytest.fixture
def lines(recorded):
    """Every recorded line, tagged with its dataset, plus two crafted ones."""
    found = [
        {**line, DATASET_KEY: dataset}
        for dataset, files in FILES.items()
        for name in files
        for line in recorded(f"dpe/{name}")["results"]
    ]
    immeuble = next(line for line in found if line["numero_dpe"] == IMMEUBLE)
    # the replaced DPE, more recent on purpose: it must still be set aside
    found.append(
        {
            **immeuble,
            "numero_dpe": REPLACED,
            "numero_dpe_remplace": None,
            "date_etablissement_dpe": "2026-10-10",
        }
    )
    # the building DPE the apartment DPE of FROM_BUILDING_RNB were generated from
    other = next(line for line in found if line["numero_dpe"] == "2664E2513521O")
    found.append(
        {
            **other,
            "numero_dpe": BUILDING_DPE,
            "id_rnb": None,
            "date_etablissement_dpe": "2026-02-27",
        }
    )
    return found


class FakeDpeClient:
    def __init__(self, lines):
        self.all_lines = lines
        self.calls = []

    def by_rnb(self, rnb_ids):
        rnb_ids = set(rnb_ids)
        self.calls.append(("rnb", sorted(rnb_ids)))
        return [line for line in self.all_lines if line.get("id_rnb") in rnb_ids]

    def by_numbers(self, numbers):
        numbers = set(numbers)
        self.calls.append(("numbers", sorted(numbers)))
        return [line for line in self.all_lines if line["numero_dpe"] in numbers]


@pytest.fixture
def client(lines):
    return FakeDpeClient(lines)


@pytest.fixture
def line(lines):
    def get(number):
        return next(item for item in lines if item["numero_dpe"] == number)

    return get


@pytest.fixture
def park():
    park = BuildingPark(name="Bayonne")
    park.new_site("ecole")
    return park


# --- selection --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (IMMEUBLE, True),
        ("2664E2310621S", True),  # maison
        ("2664E2539952D", False),  # appartement
        ("2664N2446358S", True),  # neuf, immeuble
        ("2664T2497722M", True),  # tertiaire vierge
        ("2664T2478490U", False),  # tertiaire facture dans un bâtiment de logement
    ],
)
def test_building_scale(line, number, expected):
    assert dpe.is_building_scale(line(number)) is expected


def test_replaced_dpe_is_set_aside_even_if_more_recent(line):
    assert dpe.select_dpe([line(REPLACED), line(IMMEUBLE)])["numero_dpe"] == IMMEUBLE


def test_most_recent_dpe_wins(line):
    older = {**line(IMMEUBLE), "numero_dpe": "2664E0000000A", "numero_dpe_remplace": None}
    older["date_etablissement_dpe"] = "2022-01-01"
    assert dpe.select_dpe([older, line(IMMEUBLE)])["numero_dpe"] == IMMEUBLE


def test_apartments_only_select_nothing(line):
    assert dpe.select_dpe([line("2664E2539952D")]) is None


# --- correspondences --------------------------------------------------------------------------


def test_residential_building_description(line):
    found = dpe.description_candidates(line(IMMEUBLE))
    assert found["usage"].value == BuildingUsage.RESIDENTIAL_COLLECTIVE
    assert found["construction_year"].value == 1838
    assert found["dwelling_count"].value == 6
    assert found["level_count"].value == 2
    assert found["heated_area"].value == 466.3
    assert found["heated_area"].comment == "surface habitable de l'immeuble (DPE)"
    assert {c.ref for c in found.values()} == {IMMEUBLE}
    assert {c.date for c in found.values()} == {datetime.date(2026, 10, 2)}
    assert "floor_area" not in found


def test_house_description(line):
    found = dpe.description_candidates(line("2664E2310621S"))
    assert found["usage"].value == BuildingUsage.RESIDENTIAL_INDIVIDUAL
    assert found["dwelling_count"].value == 1
    assert found["heated_area"].value == 132
    assert found["heated_area"].comment == "surface habitable du logement (DPE)"


def test_period_alone_gives_no_year(line):
    assert "construction_year" not in dpe.description_candidates(line("2664E2513521O"))


def test_tertiary_description(line):
    found = dpe.description_candidates(line("2664T2231016O"))
    assert found["usage"].value == BuildingUsage.TERTIARY
    assert found["heated_area"].value == 153
    assert found["heated_area"].comment == "surface utile (DPE tertiaire)"
    assert found["construction_year"].value == 1990


def test_envelope(line):
    envelope, components = dpe.envelope_candidates(line(IMMEUBLE))
    assert envelope["u_bat"].value == 0.77
    assert envelope["inertia"].value == Inertia.MEDIUM
    assert envelope["inertia"].raw == "Moyenne"
    assert components[ComponentKind.WALL]["heat_loss_coefficient"].value == 330
    assert components[ComponentKind.WALL]["insulation"].value == InsulationLevel.INSUFFICIENT
    # comble perdu "bonne", comble aménagé "bonne", toit terrasse "insuffisante": the worst
    assert components[ComponentKind.ROOF]["insulation"].value == InsulationLevel.INSUFFICIENT
    assert components[ComponentKind.ROOF]["insulation"].raw == "insuffisante"
    assert components[ComponentKind.WINDOW]["insulation"].value == InsulationLevel.AVERAGE
    assert components[ComponentKind.DOOR]["heat_loss_coefficient"].value == 26.6
    assert components[ComponentKind.THERMAL_BRIDGE] == {
        "heat_loss_coefficient": components[ComponentKind.THERMAL_BRIDGE]["heat_loss_coefficient"]
    }
    assert len(components) == 6


def test_tertiary_dpe_has_no_envelope(line):
    assert dpe.envelope_candidates(line("2664T2231016O")) == ({}, {})


def test_generators(line):
    produced = dpe.generator_candidates(line(IMMEUBLE))
    heating = produced[SystemKind.HEATING]
    assert [g["technology"].value for g in heating] == [T.ELECTRIC_RESISTANCE, T.BOILER_CONDENSING]
    assert [g["name"].value for g in heating] == [
        "installation 1, generator 1",
        "installation 2, generator 1",
    ]
    assert heating[1]["energy_type"].value == E.NATURAL_GAS
    assert heating[1]["energy_type"].raw == "Gaz naturel"
    [dhw] = produced[SystemKind.DHW]
    assert dhw["technology"].value == T.ELECTRIC_WATER_TANK
    assert dhw["storage_volume"].value == 200
    [cooling] = produced[SystemKind.COOLING]
    assert cooling["technology"].value == T.HEAT_PUMP_AIR_AIR
    assert cooling["energy_type"].value == E.ELECTRICITY


def test_combined_boiler_storage_of_zero_is_left_out(line):
    record = {
        **line(IMMEUBLE),
        "type_generateur_n1_ecs_n1": "Chaudière gaz à condensation après 2015",
        "type_energie_generateur_n1_ecs_n1": "Gaz naturel",
        "volume_stockage_generateur_n1_ecs_n1": 0,
        "cop_generateur_n1_ecs_n1": None,
    }
    [dhw] = dpe.generator_candidates(record)[SystemKind.DHW]
    assert "storage_volume" not in dhw


def test_dhw_cop_is_the_nominal_ratio(line):
    record = {
        **line(IMMEUBLE),
        "type_generateur_n1_ecs_n1": "CET sur air extrait après 2014",
        "cop_generateur_n1_ecs_n1": 2.9,
    }
    [dhw] = dpe.generator_candidates(record)[SystemKind.DHW]
    assert dhw["nominal_performance_ratio"].value == 2.9


def test_tertiary_heating_energy_only(line):
    record = {**line("2664T2231016O"), "type_energie_principale_chauffage": "Gaz naturel"}
    [heating] = dpe.generator_candidates(record)[SystemKind.HEATING]
    assert heating["energy_type"].value == E.NATURAL_GAS
    assert "technology" not in heating


# --- enrich -----------------------------------------------------------------------------------


def test_building_is_enriched_from_its_rnb_id(park, client):
    building = park.new_building(IMMEUBLE_RNB)
    report = dpe.enrich(park, client=client)
    assert report.enriched == [IMMEUBLE_RNB]
    assert building.construction_year == 1838
    assert building.effective("construction_year").ref == IMMEUBLE
    assert building.envelope.u_bat == 0.77
    assert [c.id for c in building.envelope.components] == [
        "dpe-wall",
        "dpe-roof",
        "dpe-floor",
        "dpe-window",
        "dpe-door",
        "dpe-thermal_bridge",
    ]
    assert [s.id for s in building.heating] == [
        f"heating-{IMMEUBLE_RNB}-dpe-1",
        f"heating-{IMMEUBLE_RNB}-dpe-2",
    ]
    assert building.dpe_ids == [IMMEUBLE]  # the replaced DPE is not added
    assert f"DPE {IMMEUBLE} used" in report.messages[IMMEUBLE_RNB]


def test_running_twice_is_idempotent(park, client):
    park.new_building(IMMEUBLE_RNB)
    dpe.enrich(park, client=client)
    first = park.to_json()
    dpe.enrich(park, client=client)
    assert park.to_json() == first


def test_apartment_dpe_is_only_added_to_dpe_ids(park, client):
    building = park.new_building(APARTMENT_RNB)
    report = dpe.enrich(park, client=client)
    assert building.dpe_ids == ["2664E2539952D"]
    assert building.values == {}
    assert report.enriched == []
    assert "none for the whole building" in report.messages[APARTMENT_RNB][0]


def test_apartment_dpe_leads_to_its_building_dpe(park, client):
    building = park.new_building(FROM_BUILDING_RNB)
    dpe.enrich(park, client=client)
    assert BUILDING_DPE in building.dpe_ids
    assert building.effective("dwelling_count").ref == BUILDING_DPE
    assert ("numbers", [BUILDING_DPE]) in client.calls


def test_dpe_on_a_site_goes_to_the_site(park, client):
    site = park["ecole"]
    site.dpe_ids = [IMMEUBLE]
    building = park.new_building(id="sans-rnb", parent="ecole")
    report = dpe.enrich(park, client=client)
    assert report.enriched == ["ecole"]
    assert site.dwelling_count == 6
    assert building.construction_year == 1838  # inherited from the site
    assert building.envelope.u_bat == 0.77  # envelope inherited too


def test_tertiary_dpe_enrichment(park, client):
    building = park.new_building(TERTIARY_RNB)
    dpe.enrich(park, client=client)
    assert building.usage == BuildingUsage.TERTIARY
    assert building.heated_area == 153
    assert building.envelope.components == []


def test_building_without_dpe_is_not_found(park, client):
    park.new_building("ZZZZZZZZZZZZ")
    assert dpe.enrich(park, client=client).not_found == ["ZZZZZZZZZZZZ"]


def test_new_dpe_replaces_values_and_components(park, client, line):
    building = park.new_building(IMMEUBLE_RNB)
    dpe.enrich(park, client=client)
    newer = {
        **line("2664T2231016O"),
        "numero_dpe": "2664T2999999A",
        "id_rnb": IMMEUBLE_RNB,
        "date_etablissement_dpe": "2026-10-12",
    }
    client.all_lines.append(newer)
    dpe.enrich(park, client=client)
    assert building.usage == BuildingUsage.TERTIARY
    assert building.envelope.components == []
    assert building.envelope.u_bat is None
    assert building.level_count is None
    assert building.heating == []  # no heating energy in this tertiary DPE
    assert building.dpe_ids == [IMMEUBLE, "2664T2999999A"]


def test_explicit_buildings_only(park, client):
    park.new_building(IMMEUBLE_RNB)
    park.new_building(MAISON_RNB)
    report = dpe.enrich(park, [MAISON_RNB], client=client)
    assert report.enriched == [MAISON_RNB]


# --- client -----------------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", ["dpe03existant", "dpe02neuf", "dpe01tertiaire"])
def test_selected_columns_exist_in_the_schema(recorded, dataset):
    schema = recorded(f"dpe/schema_{dataset}.json")
    keys = {column["key"] for column in schema["schema"]}
    assert set(dpe.COLUMNS[dataset]) <= keys


def test_numbers_are_looked_up_in_their_dataset(fake_session):
    session = fake_session(
        [{"results": [{"numero_dpe": "2664E1"}]}, {"results": [{"numero_dpe": "2664T1"}]}]
    )
    found = DpeClient(session=session).by_numbers(["2664E1", "2664T1", "bad"])
    assert [line[DATASET_KEY] for line in found] == ["dpe03existant", "dpe01tertiaire"]
    first = session.calls[0]
    assert first["url"].endswith("/datasets/dpe03existant/lines")
    assert first["params"]["numero_dpe_in"] == "2664E1"
    assert first["params"]["count"] == "false"
    assert "type_batiment" in first["params"]["select"]
    assert "type_batiment" not in session.calls[1]["params"]["select"]


def test_rnb_ids_are_looked_up_in_every_dataset(fake_session):
    session = fake_session([{"results": []}] * 3)
    DpeClient(session=session).by_rnb(["A", "B"])
    assert [c["params"]["id_rnb_in"] for c in session.calls] == ["A,B"] * 3


def test_lines_follow_next(fake_session):
    session = fake_session(
        [
            {"results": [{"numero_dpe": "1"}], "next": "https://fake/next"},
            {"results": [{"numero_dpe": "2"}]},
        ]
    )
    found = DpeClient(session=session).lines("dpe02neuf", "numero_dpe", ["1", "2"])
    assert [line["numero_dpe"] for line in found] == ["1", "2"]


def test_unexpected_line_raises(fake_session):
    session = fake_session([{"results": [{"oops": 1}]}])
    with pytest.raises(ConnectorError, match="unexpected DPE line"):
        DpeClient(session=session).lines("dpe02neuf", "numero_dpe", ["1"])


# --- real API ---------------------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.parametrize(
    ("dataset", "number"),
    [
        ("dpe03existant", "2664E2513521O"),
        ("dpe02neuf", "2664N2446358S"),
        ("dpe01tertiaire", "2664T2231016O"),
    ],
)
def test_real_dpe_lines(dataset, number):
    [found] = DpeClient().lines(dataset, "numero_dpe", [number])
    assert found["numero_dpe"] == number
    assert dpe.description_candidates(found)
