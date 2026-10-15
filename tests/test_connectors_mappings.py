import warnings

import pytest

from bstock.connectors import _mappings as m
from bstock.enums import (
    BuildingUsage,
    EnergyType,
    FrameType,
    GeneratorTechnology,
    GlazingType,
    Inertia,
    InsulationLevel,
)
from bstock.exceptions import UnmappedValueWarning

T, E = GeneratorTechnology, EnergyType
DPE_VALUES = "dpe/values_agg_generators_and_scales.json"
NOT_LABELS = {"__MISSING__", "Non affecté"}


@pytest.fixture
def no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        yield


# --- simple tables ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("function", "label", "expected"),
    [
        (m.bdnb_usage, "Résidentiel collectif", BuildingUsage.RESIDENTIAL_COLLECTIVE),
        (m.bdnb_usage, "Dépendance", BuildingUsage.OUTBUILDING),
        (m.bdnb_usage, "Secondaire", BuildingUsage.INDUSTRY),
        (m.bdnb_usage, "Primaire", BuildingUsage.AGRICULTURE),
        (m.bdnb_energy_type, "gpl/butane/propane", E.LPG),
        (m.bdnb_energy_type, "reseau de chaleur", E.DISTRICT_HEATING),
        (m.dpe_energy_type, "Bois – Plaquettes d’industrie", E.WOOD_CHIPS),
        (
            m.dpe_energy_type,
            "Électricité d'origine renouvelable utilisée dans le bâtiment",
            E.ELECTRICITY,
        ),
        (m.dpe_energy_type, "Butane", E.LPG),
        (m.dpe_insulation, "très bonne", InsulationLevel.VERY_GOOD),
        (m.dpe_inertia, "Légère", Inertia.LIGHT),
        (m.dpe_glazing, "Survitrage", GlazingType.SECONDARY_GLAZING),
        (m.dpe_frame, "Métal avec rupture de pont thermique", FrameType.METAL_THERMAL_BREAK),
        (m.dpe_frame, "Polycarbonate", FrameType.OTHER),
    ],
)
def test_known_label_is_translated(function, label, expected, no_warning):
    assert function(label) is expected


@pytest.mark.parametrize(
    ("function", "label", "expected"),
    [
        (m.dpe_energy_type, "Hydrogène", E.OTHER),
        (m.dpe_frame, "Aluminium", FrameType.OTHER),
        (m.bdnb_usage, "Agricole", None),
        (m.dpe_inertia, "Très légère", None),
        (m.dpe_insulation, "excellente", None),
        (m.dpe_glazing, "Quadruple vitrage", None),
    ],
)
def test_unknown_label_gives_other_or_nothing_and_warns(function, label, expected):
    with pytest.warns(UnmappedValueWarning, match=label):
        assert function(label) is expected


@pytest.mark.parametrize(
    "label",
    [
        "Bois - Bûches",  # hyphen instead of the en dash
        "Bois – Plaquettes d'industrie",  # straight apostrophe instead of the typographic one
        "électricité",
    ],
)
def test_labels_are_compared_exactly(label):
    with pytest.warns(UnmappedValueWarning):
        m.dpe_energy_type(label)


@pytest.mark.parametrize("label", [None, "", "Non affecté"])
def test_empty_labels_give_nothing_silently(label, no_warning):
    assert m.dpe_energy_type(label) is None
    assert m.dpe_generator(label) == (None, None)


def test_every_collected_bdnb_usage_and_energy_is_mapped(recorded, no_warning):
    values = recorded("bdnb/enum_values.json")
    for table in values.values():
        for label in table.get("usage_principal_bdnb_open") or []:
            assert m.bdnb_usage(label) is not None
        for column, labels in table.items():
            if column.startswith("type_energie"):
                assert all(m.bdnb_energy_type(label) is not None for label in labels)


def test_every_collected_dpe_energy_is_mapped(recorded, no_warning):
    values = recorded(DPE_VALUES)
    for column, aggregate in values.items():
        if "type_energie" in column:
            for label in set(aggregate["values"]) - NOT_LABELS:
                assert m.dpe_energy_type(label) is not None


def test_every_collected_dpe_envelope_label_is_mapped(recorded, no_warning):
    values = recorded(DPE_VALUES)
    for label in set(values["dpe03existant__classe_inertie_batiment"]["values"]) - NOT_LABELS:
        assert m.dpe_inertia(label) is not None


# --- BDNB generators (SPEC A.5) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "technology", "energy"),
    [
        ("chaudiere gaz condensation", T.BOILER_CONDENSING, E.NATURAL_GAS),
        ("chaudiere fioul basse temperature", T.BOILER_LOW_TEMPERATURE, E.FUEL_OIL),
        ("chaudiere gpl/butane/propane standard", T.BOILER_STANDARD, E.LPG),
        ("chaudiere gaz indetermine", T.BOILER, E.NATURAL_GAS),
        ("chaudiere energie indetermine indetermine", T.BOILER, None),
        ("chaudiere energie indetermine condensation", T.BOILER_CONDENSING, None),
        ("chaudiere bois", T.BOILER, E.WOOD),
        ("chaudiere electrique", T.ELECTRIC_BOILER, E.ELECTRICITY),
        ("pac air/eau", T.HEAT_PUMP_AIR_WATER, E.ELECTRICITY),
        ("pac gaz", T.HEAT_PUMP_GAS, E.NATURAL_GAS),
        ("pac indetermine", T.HEAT_PUMP, E.ELECTRICITY),
        ("poele ou insert charbon", T.STOVE_INSERT, E.COAL),
        ("poele ou insert indetermine", T.STOVE_INSERT, None),
        ("poele bouilleur bois", T.STOVE_INSERT, E.WOOD),
        ("radiateurs gaz", T.GAS_RADIATOR, E.NATURAL_GAS),
        ("generateur air chaud combustion", T.WARM_AIR_GENERATOR, None),
        ("generateurs a effet joule", T.ELECTRIC_RESISTANCE, E.ELECTRICITY),
        ("reseau de froid", T.DISTRICT_SUBSTATION, E.DISTRICT_COOLING),
        ("ecs solaire", T.SOLAR_THERMAL, E.SOLAR),
        (
            "ecs thermodynamique electrique(pac ou ballon)",
            T.THERMODYNAMIC_WATER_HEATER,
            E.ELECTRICITY,
        ),
        ("chauffe-eau gaz independant", T.WATER_HEATER, E.NATURAL_GAS),
        ("chauffe-eau independant indetermine", T.WATER_HEATER, None),
        ("ecs bois indetermine", T.OTHER, E.WOOD),
        ("production mixte indetermine", T.OTHER, None),
    ],
)
def test_bdnb_generator_label_is_split(label, technology, energy, no_warning):
    assert m.bdnb_generator(label) == (technology, energy)


def test_every_collected_bdnb_generator_is_mapped(recorded, no_warning):
    values = recorded("bdnb/enum_values.json")
    labels = {
        label
        for table in values.values()
        for column, column_labels in table.items()
        if column.startswith("type_generateur")
        for label in column_labels
    }
    assert len(labels) == 50
    for label in labels:
        m.bdnb_generator(label)


def test_unknown_bdnb_generator_gives_other():
    with pytest.warns(UnmappedValueWarning, match="pile a combustible"):
        assert m.bdnb_generator("pile a combustible") == (T.OTHER, None)


# --- DPE generators (data table) --------------------------------------------------------------


def test_dpe_table_is_loaded_from_package_data():
    table = m.dpe_generators()
    assert len(table) == 210
    assert all(isinstance(t.technology, GeneratorTechnology) for t in table.values())


@pytest.mark.parametrize(
    ("label", "technology", "energy"),
    [
        ("Chaudière gaz à condensation après 2015", T.BOILER_CONDENSING, E.NATURAL_GAS),
        ("Chaudière fioul classique 1981-1990", T.BOILER_STANDARD, E.FUEL_OIL),
        ("Chaudière bois granulés après 2019", T.BOILER, E.WOOD_PELLETS),
        ("PAC air/air installée à partir de 2015", T.HEAT_PUMP_AIR_AIR, E.ELECTRICITY),
        ("PAC eau glycolée/eau installée après 2017", T.HEAT_PUMP_GROUND, E.ELECTRICITY),
        ("Convecteur électrique NFC, NF** et NF***", T.ELECTRIC_RESISTANCE, E.ELECTRICITY),
        ("Radiateur électrique à accumulation", T.ELECTRIC_STORAGE_HEATER, E.ELECTRICITY),
        ("insert installé avant 1990", T.STOVE_INSERT, None),
        (
            "générateur à air chaud à combustion à condensation a partir de 2006",
            T.WARM_AIR_GENERATOR,
            None,
        ),
        ("CET sur air extrait après 2014", T.THERMODYNAMIC_WATER_HEATER, E.ELECTRICITY),
        ("Accumulateur gaz à condensation après 2000", T.WATER_HEATER, E.NATURAL_GAS),
        ("Ballon électrique à accumulation horizontal", T.ELECTRIC_WATER_TANK, E.ELECTRICITY),
        (
            "pompe à chaleur hybride : partie chaudière Chaudière gaz à condensation après 2015",
            T.BOILER_CONDENSING,
            E.NATURAL_GAS,
        ),
        (
            "pompe à chaleur hybride : partie pompe à chaleur PAC air/eau installée après 2017",
            T.HEAT_PUMP_HYBRID,
            E.ELECTRICITY,
        ),
        ("Réseau de chaleur isolé", T.DISTRICT_SUBSTATION, E.DISTRICT_HEATING),
        ("réseau de froid urbain", T.DISTRICT_SUBSTATION, E.DISTRICT_COOLING),
    ],
)
def test_dpe_generator_label_is_translated(label, technology, energy, no_warning):
    assert m.dpe_generator(label) == (technology, energy)


def test_every_collected_dpe_generator_is_in_the_table(recorded):
    values = recorded(DPE_VALUES)
    labels = {
        label
        for column, aggregate in values.items()
        if "type_generateur" in column
        for label in aggregate["values"]
    } - NOT_LABELS
    assert sorted(labels - set(m.dpe_generators())) == []


def test_unknown_dpe_generator_gives_other():
    with pytest.warns(UnmappedValueWarning):
        assert m.dpe_generator("Chaudière à hydrogène après 2030") == (T.OTHER, None)
