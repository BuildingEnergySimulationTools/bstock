import importlib.util
import math
import sys

import pandas as pd
import pytest

from bstock.exceptions import InvalidArgumentError, MissingDependencyError, UnknownFieldError
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.plot import CATEGORICAL, OTHER, OTHER_COLOR

requires_plotly = pytest.mark.skipif(
    importlib.util.find_spec("plotly") is None, reason="needs the [plot] extra"
)
YEAR = {"start": "2024-01-01", "end": "2025-01-01"}
USAGES = ["tertiary", "tertiary", "tertiary", "residential_collective", "primary", "secondary"]


def square(lat, lon, size=0.001):
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


@pytest.fixture
def park():
    """Sites s0 (b0, b1, b2) and s1 (b3, b4); b5 at the top level, without location nor area."""
    park = BuildingPark(name="P")
    for s in range(2):
        park.new_site(f"s{s}", lat=43.5 + s / 100, lon=-1.5)
    for i, usage in enumerate(USAGES):
        parent = "s0" if i < 3 else "s1" if i < 5 else None
        located = {} if i == 5 else {"footprint": square(43.5 + i / 1000, -1.5)}
        park.new_building(
            id=f"b{i}",
            parent=parent,
            usage=usage,
            construction_year=1950 + 10 * i,
            heated_area=None if i == 5 else 100.0 * (i + 1),
            source="dpe" if i % 2 else "bdnb",
            **located,
        )
    days = pd.date_range("2024-01-01", "2024-12-31", freq="D", tz="Europe/Paris")
    for s, energy in (("s0", "natural_gas"), ("s0", "electricity"), ("s1", "natural_gas")):
        meter = park.add_meter(
            Meter(
                id=f"{s}-{energy}",
                scope=[s],
                energy_type=energy,
                end_use="heating" if energy == "natural_gas" else None,
                channels=[Channel(name="energy", unit="kWh")],
            )
        )
        meter.data = pd.DataFrame({"energy": 1.0}, index=days)
    return park


def trace_names(fig):
    return [trace.name for trace in fig.data]


def subtitle(fig):
    return fig.layout.title.text.split("<sup>")[1].removesuffix("</sup>")


@requires_plotly
@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("completeness", {}),
        ("completeness", {"per": "entity"}),
        ("provenance", {}),
        ("aggregate", {"field": "heated_area"}),
        ("distribution", {"field": "usage"}),
        ("map", {}),
        ("consumption", YEAR),
        ("intensity", YEAR),
    ],
)
def test_every_method_returns_a_figure(park, method, kwargs):
    from plotly.graph_objects import Figure

    assert isinstance(getattr(park.select().plot, method)(**kwargs), Figure)


def test_missing_plotly_gives_a_clear_error(park, monkeypatch):
    monkeypatch.setitem(sys.modules, "plotly", None)
    with pytest.raises(MissingDependencyError, match=r"bstock\[plot\]"):
        park.select().plot.completeness()


@requires_plotly
def test_completeness_stacks_own_and_inherited_shares(park):
    fig = park.select().plot.completeness(fields=["usage", "heated_area"])
    assert trace_names(fig) == ["Own value", "Inherited from a site"]
    assert list(fig.data[0].x) == [100, pytest.approx(500 / 6)]
    assert [trace.marker.color for trace in fig.data] == list(CATEGORICAL[:2])
    assert fig.layout.barmode == "stack"


@requires_plotly
def test_completeness_by_entity_has_one_bar_per_entity(park):
    fig = park.select().plot.completeness("entity", fields=["heated_area"])
    assert list(fig.data[0].y) == [f"b{i}" for i in (5, 0, 1, 2, 3, 4)]


@requires_plotly
def test_provenance_has_one_colour_per_source_and_grey_missing(park):
    fig = park.select().plot.provenance(["heated_area"])
    assert trace_names(fig) == ["bdnb", "dpe", "missing"]
    assert fig.data[-1].marker.color == OTHER_COLOR


@requires_plotly
def test_provenance_folds_sources_past_the_palette(park):
    # nine buildings, each with its own source: one more source than colours
    for i, building in enumerate(park.iter_buildings()):
        building.set("level_count", 2, source=f"extra-{i}")
    park.new_building(id="b6", level_count=1, source="extra-6")
    park.new_building(id="b7", level_count=1, source="extra-7")
    park.new_building(id="b8", level_count=1, source="extra-8")
    fig = park.select().plot.provenance(["level_count"])
    assert len(fig.data) == len(CATEGORICAL) + 1
    assert trace_names(fig)[-2:] == [OTHER, "missing"]


@requires_plotly
def test_aggregate_bars_per_site_with_park_total(park):
    fig = park.select().plot.aggregate("heated_area")
    assert list(fig.data[0].x) == ["s0", "s1"]
    assert list(fig.data[0].y) == [600, 900]
    assert subtitle(fig) == "total: 1,500 m² · 2 / 2 sites drawn"
    assert fig.layout.yaxis.title.text == "heated_area (m²)"


@requires_plotly
def test_aggregate_by_attribute(park):
    fig = park.select().plot.aggregate("heated_area", by="usage")
    assert list(fig.data[0].x) == ["tertiary", "residential_collective", "primary"]
    assert list(fig.data[0].customdata) == [100, 100, 100]
    assert subtitle(fig) == "3 / 4 groups drawn"  # secondary (b5) has no area


@requires_plotly
def test_distribution_of_a_number_is_a_histogram(park):
    fig = park.select().plot.distribution("heated_area", bins=5)
    assert fig.data[0].type == "histogram"
    assert sorted(fig.data[0].x) == [100, 200, 300, 400, 500]
    assert subtitle(fig) == "5 / 6 buildings with a value"
    assert fig.layout.xaxis.title.text == "heated_area (m²)"


@requires_plotly
def test_distribution_of_categories_is_sorted_by_frequency(park):
    fig = park.select().plot.distribution("usage")
    assert fig.data[0].type == "bar"
    assert (fig.data[0].x[0], fig.data[0].y[0]) == ("tertiary", 3)


@requires_plotly
@pytest.mark.parametrize(
    ("field", "error"), [("sub_usages", InvalidArgumentError), ("colour", UnknownFieldError)]
)
def test_distribution_refuses_list_and_unknown_attributes(park, field, error):
    park["b0"].sub_usages = ["school"]
    with pytest.raises(error):
        park.select().plot.distribution(field)


@requires_plotly
def test_map_of_points_keeps_three_colours_then_other(park):
    park["b5"].lat, park["b5"].lon = 43.6, -1.4  # b5 has no site to inherit a location from
    fig = park.select().plot.map(color="usage")
    # the most frequent first, ties in the order of the selection (b5 comes first)
    assert trace_names(fig) == ["tertiary", "secondary", "residential_collective", OTHER]
    assert list(fig.data[-1].text) == ["b4"]
    assert fig.data[-1].marker.color == OTHER_COLOR
    assert fig.layout.map.style == "open-street-map"


@requires_plotly
def test_map_points_use_inherited_locations_and_skip_unlocated_entities(park):
    fig = park.select().plot.map()
    assert len(fig.data[0].lat) == 5
    assert subtitle(fig) == "5 / 6 buildings located"


@requires_plotly
def test_map_colours_numbers_on_a_ramp(park):
    fig = park.select().plot.map(color="heated_area")
    assert len(fig.data) == 1
    assert fig.data[0].marker.colorbar.title.text == "heated_area (m²)"


@requires_plotly
def test_map_of_footprints_draws_located_entities_only(park):
    fig = park.select().plot.map(geometry="footprint", color="heated_area")
    assert fig.data[0].type == "choroplethmap"
    assert list(fig.data[0].locations) == ["b0", "b1", "b2", "b3", "b4"]
    assert subtitle(fig) == "5 / 6 buildings located"
    assert math.isclose(fig.layout.map.center.lon, -1.4995, abs_tol=1e-6)


@requires_plotly
def test_map_refuses_an_unknown_geometry(park):
    with pytest.raises(InvalidArgumentError):
        park.select().plot.map(geometry="polygon")


@requires_plotly
def test_consumption_stacks_energies_per_entity(park):
    fig = park.select(entity="site").plot.consumption(**YEAR)
    assert trace_names(fig) == ["natural_gas", "electricity"]
    assert list(fig.data[0].x) == ["s0", "s1"]
    assert list(fig.data[0].y) == [366, 366]
    assert subtitle(fig) == "lowest data coverage: 100 %"


@requires_plotly
def test_consumption_by_end_use_and_per_month(park):
    fig = park.select(entity="site").plot.consumption(**YEAR, by="end_use", freq="MS")
    assert trace_names(fig) == ["heating", "other"]
    assert len(fig.data[0].x) == 12
    assert fig.data[0].y[0] == 62  # two gas meters, 31 days


@requires_plotly
def test_consumption_refuses_another_stack(park):
    with pytest.raises(InvalidArgumentError):
        park.select().plot.consumption(**YEAR, by="entity")


@requires_plotly
def test_intensity_per_entity(park):
    fig = park.select(entity="site").plot.intensity(**YEAR)
    assert list(fig.data[0].x) == ["s0", "s1"]
    assert list(fig.data[0].y) == [pytest.approx(366 / 600), pytest.approx(366 / 900)]
    assert fig.layout.yaxis.title.text == "Intensity (kWh/m²)"


@requires_plotly
def test_intensity_per_period_divides_by_the_total_area(park):
    fig = park.select(entity="site").plot.intensity(**YEAR, freq="YS")
    gas = dict(zip(trace_names(fig), fig.data, strict=True))["natural_gas"]
    assert list(gas.y) == [pytest.approx(2 * 366 / 1500)]
