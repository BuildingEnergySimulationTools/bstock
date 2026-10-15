from html.parser import HTMLParser

import pandas as pd
import pytest

from bstock.building import Building
from bstock.display import REPR_MAX_LINES
from bstock.envelope import EnvelopeComponent
from bstock.exceptions import InvalidArgumentError, UnknownFieldError
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.provenance import sourced
from bstock.systems import HeatingGenerator, SolarPV

RNB = "A1B2C3D4E5F6"


@pytest.fixture
def park():
    park = BuildingPark(name="Bayonne")
    school = park.new_site("gs-a", name="Groupe scolaire A", usage="tertiary")
    group = park.new_site("group", parent=school)
    park.new_building(rnb_id=RNB, parent=group, name="École", heated_area=1250.0)
    park.new_building(id="b2", parent=group)
    park.new_building(id="gym", parent=school, name="Gymnase")
    park.new_building(id="annex", name="Mairie annexe")
    return park


def test_park_repr_is_the_tree_with_counts(park):
    assert repr(park).splitlines() == [
        "BuildingPark 'Bayonne': 2 sites, 4 buildings, 0 meters",
        "├── [site] gs-a  Groupe scolaire A  (3 buildings)",
        "│   ├── [site] group  (2 buildings)",
        f"│   │   ├── [building] {RNB}  École",
        "│   │   └── [building] b2",
        "│   └── [building] gym  Gymnase",
        "└── [building] annex  Mairie annexe",
    ]


def test_str_of_park_is_its_repr(park):
    assert str(park) == repr(park)


def test_site_repr_is_its_subtree(park):
    assert repr(park["group"]).splitlines() == [
        "[site] group  (2 buildings)",
        f"├── [building] {RNB}  École",
        "└── [building] b2",
    ]


def test_repr_truncates_a_long_tree_but_tree_does_not():
    park = BuildingPark(name="Big")
    for i in range(40):
        park.new_building(id=f"b{i}")
    lines = repr(park).splitlines()
    assert len(lines) == REPR_MAX_LINES
    assert lines[-1] == f"… {41 - (REPR_MAX_LINES - 1)} more lines: call .tree() for the full tree"
    assert len(str(park.tree()).splitlines()) == 41


def test_tree_shows_systems_and_meters_under_their_owner(park):
    park.add_system(HeatingGenerator(id="heating-ch", name="Chaufferie"), owner="gs-a")
    park.add_meter(Meter(id="pdl", scope=["heating-ch"], channels=[Channel(name="e", unit="kWh")]))
    park.add_meter(Meter(id="spare", channels=[Channel(name="e", unit="kWh")]))
    lines = park.tree(systems=True, meters=True).lines()
    assert lines[2:4] == ["│   ├── [heating] heating-ch  Chaufferie", "│   ├── [meter] pdl"]
    assert lines[-1] == "└── [meter] spare  (no scope)"


def test_tree_hides_systems_and_meters_by_default(park):
    park.add_system(SolarPV(id="pv-1"), owner="annex")
    assert "pv-1" not in str(park.tree())


@pytest.mark.parametrize(
    ("max_depth", "expected"),
    [
        (
            0,
            [
                "BuildingPark 'Bayonne': 2 sites, 4 buildings, 0 meters",
                "└── … 2 sites, 4 buildings",
            ],
        ),
        (
            1,
            [
                "BuildingPark 'Bayonne': 2 sites, 4 buildings, 0 meters",
                "├── [site] gs-a  Groupe scolaire A  (3 buildings)",
                "│   └── … 1 site, 3 buildings",
                "└── [building] annex  Mairie annexe",
            ],
        ),
    ],
)
def test_max_depth_counts_the_hidden_content(park, max_depth, expected):
    assert park.tree(max_depth=max_depth).lines() == expected


def test_negative_max_depth_is_refused(park):
    with pytest.raises(InvalidArgumentError):
        park.tree(max_depth=-1)


def test_fields_show_effective_values_with_unit(park):
    lines = park.tree(fields=["usage", "heated_area"]).lines()
    assert lines[3].endswith(f"{RNB}  École  usage='tertiary', heated_area=1250.0 m²")
    assert lines[-1].endswith("annex  Mairie annexe  usage=None, heated_area=None")


def test_tree_accepts_a_single_field_name(park):
    assert park.tree(fields="usage").lines() == park.tree(fields=["usage"]).lines()


def test_unknown_field_is_refused(park):
    with pytest.raises(UnknownFieldError, match="colour"):
        park.tree(fields=["colour"])


def test_fields_accept_project_attributes():
    class ProjectBuilding(Building):
        code = sourced(str)

    park = BuildingPark(name="P", building_cls=ProjectBuilding)
    park.new_building(id="b", code="X1")
    assert park.tree(fields=["code"]).lines()[-1] == "└── [building] b  code='X1'"


def test_building_repr_is_one_line(park):
    assert repr(park[RNB]) == f"Building(id='{RNB}', name='École', parent='group')"


def test_building_repr_shows_rnb_id_when_it_differs_from_id(park):
    building = park.new_building(id="local", rnb_id="B2C3D4E5F6G7")
    assert repr(building) == "Building(id='local', rnb_id='B2C3D4E5F6G7')"


def test_repr_uses_the_project_class_name():
    class ProjectBuilding(Building):
        pass

    assert repr(ProjectBuilding(id="b")) == "ProjectBuilding(id='b')"


def test_generator_repr_shows_key_values_with_unit_and_owner(park):
    park.add_system(
        HeatingGenerator(
            id="heating-ch", technology="boiler_condensing", nominal_power=450.0, source="audit"
        ),
        owner="gs-a",
    )
    assert repr(park["heating-ch"]) == (
        "HeatingGenerator(id='heating-ch', technology='boiler_condensing', "
        "nominal_power=450.0 kW, owner='gs-a')"
    )


def test_meter_repr_shows_scope_and_loaded_data(park):
    meter = park.add_meter(
        Meter(id="pdl", scope=["gs-a"], end_use="heating", channels=[Channel(name="e", unit="kWh")])
    )
    assert repr(meter) == "Meter(id='pdl', end_use='heating', scope=['gs-a'])"
    meter.data = pd.DataFrame({"e": [1.0]}, index=pd.DatetimeIndex(["2024-01-01"], tz="UTC"))
    assert repr(meter).endswith("scope=['gs-a'], data=loaded)")


def test_envelope_and_component_reprs(park):
    envelope = park[RNB].envelope
    envelope.add_component(EnvelopeComponent(id="roof", kind="roof", area=120.0))
    envelope.u_bat = 0.9
    assert repr(envelope) == f"Envelope(owner='{RNB}', u_bat=0.9 W/(m².K), components=1)"
    assert (
        repr(envelope.components[0]) == "EnvelopeComponent(id='roof', kind='roof', area=120.0 m²)"
    )


def test_describe_lists_every_sourced_attribute(park):
    frame = park[RNB].describe()
    assert list(frame.index) == list(Building.sourced_fields())
    assert list(frame.columns) == ["value", "unit", "source", "provider", "inherited", "candidates"]


def test_describe_shows_inherited_value_and_its_provider(park):
    row = park[RNB].describe().loc["usage"]
    assert row["value"] == "tertiary"
    assert row["provider"] == "gs-a"
    assert row["inherited"]
    assert row["candidates"] == 0


def test_describe_shows_own_value_with_unit_and_source(park):
    row = park[RNB].describe().loc["heated_area"]
    assert (row["value"], row["unit"], row["source"], row["provider"]) == (
        1250.0,
        "m²",
        "manual",
        RNB,
    )
    assert not row["inherited"]


def test_describe_keeps_empty_attributes(park):
    row = park["b2"].describe().loc["construction_year"]
    assert pd.isna(row["value"])
    assert pd.isna(row["provider"])
    assert not row["inherited"]


class _TreeParser(HTMLParser):
    """Rebuild ``(depth, label, foldable)`` lines from the HTML tree."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.lines = []
        self.header = None
        self._capture = None
        self._text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.depth += 1
        elif tag in ("summary", "strong") or (tag == "div" and "padding-left: 1.1em" in str(attrs)):
            self._capture, self._text = tag, ""

    def handle_endtag(self, tag):
        if tag == "details":
            self.depth -= 1
        elif tag == self._capture:
            if tag == "strong":
                self.header = self._text
            else:
                depth = self.depth if tag == "summary" else self.depth + 1
                self.lines.append((depth, self._text, tag == "summary"))
            self._capture = None

    def handle_data(self, data):
        if self._capture:
            self._text += data


def parse_html(html):
    parser = _TreeParser()
    parser.feed(html)
    return parser


def test_html_tree_nests_foldable_nodes(park):
    parser = parse_html(park._repr_html_())
    assert parser.header == "BuildingPark 'Bayonne': 2 sites, 4 buildings, 0 meters"
    assert parser.lines == [
        (1, "[site] gs-a  Groupe scolaire A  (3 buildings)", True),
        (2, "[site] group  (2 buildings)", True),
        (3, f"[building] {RNB}  École", False),
        (3, "[building] b2", False),
        (2, "[building] gym  Gymnase", False),
        (1, "[building] annex  Mairie annexe", False),
    ]


def test_html_tree_lists_the_first_level_with_nodes_folded(park):
    html = park._repr_html_()
    assert html.startswith('<div class="bstock-tree"')
    assert "<details open" not in html
    assert "<script" not in html


def test_html_tree_is_not_truncated():
    park = BuildingPark(name="Big")
    for i in range(40):
        park.new_building(id=f"b{i}")
    assert len(parse_html(park._repr_html_()).lines) == 40


def test_html_tree_escapes_labels(park):
    park["annex"].name = "<b>A & B</b>"
    html = park._repr_html_()
    assert "<b>A" not in html
    assert parse_html(html).lines[-1] == (1, "[building] annex  <b>A & B</b>", False)


def test_site_and_tree_view_html_follow_the_tree_options(park):
    site_lines = parse_html(park["group"]._repr_html_()).lines
    assert [label for _, label, _ in site_lines] == [f"[building] {RNB}  École", "[building] b2"]
    view = park.tree(max_depth=1)
    assert parse_html(view._repr_html_()).lines[1] == (2, "… 1 site, 3 buildings", False)
