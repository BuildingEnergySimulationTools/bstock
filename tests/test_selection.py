import pandas as pd
import pytest

from bstock.building import Building
from bstock.enums import BuildingUsage, GeneratorTechnology
from bstock.exceptions import (
    InvalidArgumentError,
    NoDataWarning,
    UnknownFieldError,
    UnknownIdError,
    UnknownKindError,
)
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.provenance import sourced
from bstock.selection import Selection
from bstock.systems import HeatingGenerator, SolarPV

RNB = "A1B2C3D4E5F6"


@pytest.fixture
def park():
    """gs (tertiary, 1960) > group > b1, b2 (1990) ; gs > b3 ; other (residential) > b4 ; b5."""
    park = BuildingPark(name="P")
    gs = park.new_site("gs", usage="tertiary", construction_year=1960)
    group = park.new_site("group", parent=gs)
    park.new_building(rnb_id=RNB, id="b1", parent=group, heated_area=1000.0, sub_usages=["school"])
    park.new_building(id="b2", parent=group, construction_year=1990)
    park.new_building(id="b3", parent=gs, name="Gym", heated_area=500.0)
    other = park.new_site("other", usage="residential_collective")
    park.new_building(id="b4", parent=other)
    park.new_building(id="b5")
    park.add_system(HeatingGenerator(id="heating-gs"), owner=gs)
    park.add_system(SolarPV(id="pv-b5"), owner="b5")
    park.add_meter(Meter(id="m", scope=["b3"], channels=[Channel(name="e", unit="kWh")]))
    return park


def test_select_without_criteria_returns_every_building_in_tree_order(park):
    selection = park.select()
    assert isinstance(selection, Selection)
    assert selection.entity == "building"
    assert selection.ids == tuple(b.id for b in park.iter_buildings())
    assert list(selection) == list(park.iter_buildings())


def test_select_sites(park):
    assert park.select(entity="site").ids == ("gs", "group", "other")


def test_unknown_entity_type_is_refused(park):
    with pytest.raises(InvalidArgumentError, match="entity must be"):
        park.select(entity="meter")


def test_inherited_value_matches(park):
    assert park.select(usage="tertiary").ids == ("b3", "b1", "b2")


def test_enum_criterion_accepts_member_and_value(park):
    assert park.select(usage=BuildingUsage.TERTIARY).ids == park.select(usage="tertiary").ids


def test_invalid_enum_value_is_refused(park):
    with pytest.raises(InvalidArgumentError, match="education"):
        park.select(usage="education")


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        ({"construction_year": 1990}, ("b2",)),
        ({"construction_year__in": [1960, 1990]}, ("b3", "b1", "b2")),
        ({"construction_year__lt": 1990}, ("b3", "b1")),
        ({"construction_year__le": 1960}, ("b3", "b1")),
        ({"construction_year__gt": 1960}, ("b2",)),
        ({"construction_year__ge": 1990}, ("b2",)),
        ({"heated_area__isnull": True}, ("b5", "b2", "b4")),
        ({"heated_area__isnull": False}, ("b3", "b1")),
        ({"sub_usages__contains": "school"}, ("b1",)),
        ({"rnb_id": RNB}, ("b1",)),
        ({"usage__in": ["tertiary", "residential_collective"]}, ("b3", "b1", "b2", "b4")),
    ],
)
def test_value_operators(park, criteria, expected):
    assert park.select(**criteria).ids == expected


def test_missing_value_matches_no_comparison(park):
    assert "b5" not in park.select(construction_year__lt=3000)
    assert "b5" not in park.select(usage__in=list(BuildingUsage))


def test_criteria_combine_with_and(park):
    assert park.select(usage="tertiary", heated_area__gt=600).ids == ("b1",)


def test_predicate_combines_with_criteria(park):
    selection = park.select(lambda b: b.name is not None, usage="tertiary")
    assert selection.ids == ("b3",)


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        ({"within": "gs"}, ("b3", "b1", "b2")),
        ({"within": "group"}, ("b1", "b2")),
        ({"parent": "group"}, ("b1", "b2")),
        ({"parent": None}, ("b5",)),
        ({"has_rnb_id": True}, ("b1",)),
        ({"has_rnb_id": False}, ("b5", "b3", "b2", "b4")),
        ({"has_meter": True}, ("b3",)),
        ({"has_system": "heating"}, ("b3", "b1", "b2")),
        ({"has_system": "pv"}, ("b5",)),
        ({"has_system": False}, ("b4",)),
    ],
)
def test_structure_criteria_on_buildings(park, criteria, expected):
    assert park.select(**criteria).ids == expected


def test_within_accepts_a_site_object(park):
    assert park.select(within=park["group"]).ids == ("b1", "b2")


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        ({"within": "gs"}, ("group",)),
        ({"parent": None}, ("gs", "other")),
        ({"has_system": True}, ("gs",)),
        ({"usage": "tertiary"}, ("gs", "group")),
    ],
)
def test_criteria_on_sites(park, criteria, expected):
    assert park.select(entity="site", **criteria).ids == expected


def test_has_rnb_id_does_not_apply_to_sites(park):
    with pytest.raises(InvalidArgumentError, match="has_rnb_id"):
        park.select(entity="site", has_rnb_id=True)


def test_unknown_system_kind_is_refused(park):
    with pytest.raises(UnknownKindError):
        park.select(has_system="lighting")


def test_within_an_unknown_site_is_refused(park):
    with pytest.raises(UnknownIdError):
        park.select(within="b1")


def test_unknown_attribute_is_refused(park):
    with pytest.raises(UnknownFieldError, match="colour"):
        park.select(colour="red")


def test_project_attribute_is_a_criterion():
    class ProjectBuilding(Building):
        code = sourced(str)

    park = BuildingPark(name="P", building_cls=ProjectBuilding)
    park.new_building(id="a", code="X1")
    park.new_building(id="b", code="X2")
    assert park.select(code="X2").ids == ("b",)


@pytest.mark.parametrize(
    ("criteria", "match"),
    [
        ({"heated_area__contains": 1}, "list"),
        ({"name__lt": 5}, "compare"),
        ({"heated_area__isnull": "yes"}, "True or False"),
        ({"construction_year__in": 1960}, "list"),
        ({"has_meter": 1}, "True or False"),
        ({"heated_area__between": (1, 2)}, "unknown operator"),
    ],
)
def test_misused_operators_are_refused(park, criteria, match):
    with pytest.raises(InvalidArgumentError, match=match):
        park.select(**criteria)


# --- selection by ids ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        ({"id": "b2"}, ("b2",)),
        ({"id__in": ["b4", "b1", "b3"]}, ("b3", "b1", "b4")),
        ({"id__in": ["b1", "b2", "b5"], "construction_year": 1990}, ("b2",)),
        ({"id__in": ["b1", "b4"], "within": "gs"}, ("b1",)),
        ({"id__in": []}, ()),
    ],
)
def test_id_criteria(park, criteria, expected):
    assert park.select(**criteria).ids == expected


def test_id_criteria_on_sites(park):
    assert park.select(id__in=("other", "gs"), entity="site").ids == ("gs", "other")


def test_id_criterion_filters_a_selection(park):
    assert park.select(within="gs").filter(id__in={"b1", "b4"}).ids == ("b1",)


@pytest.mark.parametrize(
    ("criteria", "entity"),
    [
        ({"id": "nope"}, "building"),
        ({"id__in": ["b1", "nope", "gs", "m"]}, "building"),
        ({"id__in": ["gs", "b1"]}, "site"),
    ],
)
def test_id_criteria_refuse_ids_of_another_type_or_absent(park, criteria, entity):
    with pytest.raises(UnknownIdError):
        park.select(entity=entity, **criteria)


def test_id_criteria_list_every_faulty_id(park):
    with pytest.raises(UnknownIdError, match=r"\['nope', 'gs', 'm'\]"):
        park.select(id__in=["b1", "nope", "gs", "m", "nope"])


@pytest.mark.parametrize(
    ("criteria", "match"),
    [
        ({"id__lt": "b2"}, "id only accepts"),
        ({"id__isnull": False}, "id only accepts"),
        ({"id__in": "b1"}, "list of ids"),
        ({"id": ["b1"]}, "string"),
        ({"id__in": ["b1", 3]}, "strings"),
    ],
)
def test_misused_id_criteria_are_refused(park, criteria, match):
    with pytest.raises(InvalidArgumentError, match=match):
        park.select(**criteria)


@pytest.mark.parametrize(
    "ids",
    [["b4", "b1"], ("b4", "b1"), {"b4", "b1"}, pd.Index(["b4", "b1"]), iter(["b4", "b1"])],
    ids=["list", "tuple", "set", "index", "iterator"],
)
def test_getitem_with_ids_returns_a_selection_of_buildings(park, ids):
    selection = park[ids]
    assert isinstance(selection, Selection)
    assert (selection.entity, selection.ids) == ("building", ("b1", "b4"))


def test_getitem_with_ids_of_sites(park):
    selection = park[["other", "group"]]
    assert (selection.entity, selection.ids) == ("site", ("group", "other"))


def test_getitem_ignores_duplicates_and_follows_the_tree_order(park):
    assert park[["b5", "b2", "b5", "b1"]].ids == ("b5", "b1", "b2")


def test_getitem_with_one_id_still_returns_the_entity(park):
    assert park["b1"] is park.get("b1")


def test_getitem_refuses_a_mix_of_sites_and_buildings(park):
    with pytest.raises(InvalidArgumentError, match="mix"):
        park[["gs", "b1"]]


def test_getitem_refuses_an_empty_list(park):
    with pytest.raises(InvalidArgumentError, match="empty"):
        park[[]]


@pytest.mark.parametrize("faulty", [["nope"], ["m"], ["heating-gs"]])
def test_getitem_refuses_ids_that_are_not_sites_or_buildings(park, faulty):
    with pytest.raises(UnknownIdError, match=faulty[0]):
        park[["b1", *faulty]]


def test_getitem_lists_every_faulty_id(park):
    with pytest.raises(UnknownIdError, match=r"\['nope', 'm'\]"):
        park[["nope", "b1", "m", "nope"]]


def test_getitem_refuses_non_string_ids(park):
    with pytest.raises(InvalidArgumentError, match="strings"):
        park[["b1", 2]]


def test_filter_narrows_a_selection(park):
    narrowed = park.select(usage="tertiary").filter(heated_area__isnull=False)
    assert (narrowed.entity, narrowed.ids) == ("building", ("b3", "b1"))


def test_len_and_membership(park):
    selection = park.select(within="group")
    assert len(selection) == 2
    assert "b1" in selection
    assert park["b2"] in selection
    assert "b3" not in selection


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (lambda a, b: a | b, ("b5", "b1", "b2")),
        (lambda a, b: a & b, ("b1",)),
        (lambda a, b: a - b, ("b2",)),
    ],
)
def test_set_operations_keep_the_tree_order(park, operation, expected):
    group, small = park.select(within="group"), park.select(lambda b: b.id in ("b5", "b1"))
    assert operation(group, small).ids == expected


def test_set_operations_need_same_kind_and_park(park):
    with pytest.raises(InvalidArgumentError):
        park.select() | park.select(entity="site")
    other = BuildingPark(name="Q")
    with pytest.raises(InvalidArgumentError):
        park.select() & other.select()


def test_entity_removed_after_the_selection_raises(park):
    selection = park.select(within="group")
    park.remove("b2")
    with pytest.raises(UnknownIdError, match="select again"):
        list(selection)


def test_to_dataframe_is_restricted_to_the_selection(park):
    frame = park.select(within="group").to_dataframe(provenance=True)
    assert list(frame.index) == ["b1", "b2"]
    assert frame.loc["b2", "usage_provider"] == "gs"


def test_site_selection_to_dataframe(park):
    frame = park.select(entity="site", parent=None).to_dataframe()
    assert list(frame.index) == ["gs", "other"]


def test_tree_shows_selection_and_ancestors(park):
    assert park.select(heated_area__gt=0).tree().lines() == [
        "Selection(entity='building', 2 entities) of BuildingPark 'P'",
        "└── [site] gs  (2 buildings)",
        "    ├── [site] group  (1 building)",
        "    │   └── [building] b1",
        "    └── [building] b3  Gym",
    ]


def test_site_selection_tree_counts_every_building(park):
    assert park.select(entity="site", within="gs").tree().lines() == [
        "Selection(entity='site', 1 entity) of BuildingPark 'P'",
        "└── [site] gs  (3 buildings)",
        "    └── [site] group  (2 buildings)",
    ]


def test_repr_is_one_line_and_html_is_the_tree(park):
    selection = park.select(has_rnb_id=True)
    assert repr(selection) == "Selection(entity='building', 1 entity)"
    assert "[building] b1" in selection._repr_html_()


# --- family criterion ---------------------------------------------------------------------------


class _HeatedBuilding(Building):
    heating_technology = sourced(GeneratorTechnology)


@pytest.fixture
def heated_park():
    park = BuildingPark(name="P", building_cls=_HeatedBuilding)
    park.new_building(id="pac", heating_technology="heat_pump_air_water")
    park.new_building(id="pac-inconnue", heating_technology="heat_pump")
    park.new_building(id="chaudiere", heating_technology="boiler_condensing")
    park.new_building(id="vide")
    return park


def test_family_criterion_matches_the_generic_value_and_its_variants(heated_park):
    assert heated_park.select(heating_technology__family="heat_pump").ids == ("pac", "pac-inconnue")


def test_equality_stays_strict(heated_park):
    assert heated_park.select(heating_technology="heat_pump").ids == ("pac-inconnue",)


def test_family_criterion_needs_an_enum_field(heated_park):
    with pytest.raises(InvalidArgumentError, match="enum fields"):
        heated_park.select(construction_year__family=1970)


# --- measures ---------------------------------------------------------------------------------


def add_sensor(park, meter_id, scope, temperatures=(19.0, 20.0)):
    meter = park.add_meter(
        Meter(
            id=meter_id,
            scope=scope,
            sub_bloc="ambiance",
            channels=[Channel(name="temperature", unit="°C")],
        )
    )
    index = pd.date_range("2024-01-01", periods=len(temperatures), freq="h", tz="UTC")
    meter.data = pd.DataFrame({"temperature": list(temperatures)}, index=index)
    return meter


@pytest.fixture
def metered(park):
    """The park, with meters on gs, group, b1, b4, the boiler of gs, gs + other, and b5."""
    for meter_id, scope in [
        ("m-gs", ["gs"]),
        ("m-group", ["group"]),
        ("m-boiler", ["heating-gs"]),
        ("m-shared", ["gs", "other"]),
        ("m-b4", ["b4"]),
        ("m-b5", ["pv-b5"]),
    ]:
        park.add_meter(
            Meter(
                id=meter_id,
                scope=scope,
                energy_type="electricity",
                channels=[Channel(name="energy", unit="kWh")],
            )
        )
    park.add_meter(
        Meter(
            id="m-sub",
            scope=["b1"],
            parent_meter="m-group",
            channels=[Channel(name="energy", unit="kWh")],
        )
    )
    add_sensor(park, "t-b1", ["b1"])
    park.add_meter(Meter(id="spare", channels=[Channel(name="e", unit="kWh")]))
    return park


@pytest.mark.parametrize(
    ("entity", "ids", "expected"),
    [
        ("site", ["group"], ["m-group", "m-sub", "t-b1"]),
        ("site", None, ["m", "m-gs", "m-group", "m-boiler", "m-shared", "m-b4", "m-sub", "t-b1"]),
        ("site", ["other"], ["m-shared", "m-b4"]),
        # buildings: their meters and those of their ancestor sites
        (
            "building",
            ["b1", "b3"],
            ["m", "m-gs", "m-group", "m-boiler", "m-shared", "m-sub", "t-b1"],
        ),
        ("building", ["b5"], ["m-b5"]),  # a meter of a system counts for its owner
    ],
)
def test_meters_follow_the_attachment_rule(metered, entity, ids, expected):
    selection = metered.select(lambda e: ids is None or e.id in ids, entity=entity)
    assert [m.id for m in selection.meters] == expected


def test_meters_of_a_site_group_gather_its_sub_sites(metered):
    sel = metered.select(lambda s: s.id == "gs", entity="site")
    assert [m.id for m in metered["gs"].find_meters()] == ["m-gs", "m-boiler", "m-shared"]
    assert {"m-group", "m-sub", "t-b1", "m"} <= {m.id for m in sel.meters}


def test_a_meter_of_several_sites_appears_once(metered):
    ids = [m.id for m in metered.select(entity="site").meters]
    assert ids.count("m-shared") == 1


def test_selection_timeseries_is_restricted_to_its_meters(metered):
    add_sensor(metered, "t-b4", ["b4"])
    sel = metered.select(lambda b: b.id == "b1")
    frame = sel.timeseries(quantity="temperature")
    assert list(frame.columns) == ["t-b1_temperature__°C__b1__ambiance"]


def test_selection_timeseries_mixes_energy_and_temperatures(metered):
    metered["m-sub"].data = pd.DataFrame(
        {"energy": [1.0]}, index=pd.DatetimeIndex(["2024-01-01"], tz="UTC")
    )
    sel = metered.select(lambda b: b.id == "b1")
    with pytest.warns(NoDataWarning) as record:
        frame = sel.timeseries(quantity=["energy", "temperature"], sub_bloc=["other", "ambiance"])
    assert list(frame.columns) == [
        "m-sub_energy__kWh__b1__other",
        "t-b1_temperature__°C__b1__ambiance",
    ]
    # the electricity meters of gs, group and the boiler have no data
    assert {str(w.message).split("'")[1] for w in record} == {
        "m-gs",
        "m-group",
        "m-boiler",
        "m-shared",
    }


def test_selection_without_meters_gives_an_empty_frame(park):
    assert park.select(lambda b: b.id == "b2").timeseries().empty
